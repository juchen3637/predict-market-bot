"""
filter_markets.py — Market Filter for pm-scan skill

Fetches active markets from Polymarket and Kalshi, applies volume/expiry/
liquidity filters, and returns a ranked list of candidates.

Adapted from patterns in:
  - github.com/suislanchez/polymarket-kalshi-weather-bot  (dual-platform execution)
  - github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot  (market discovery)
  - pmxt library (unified API wrapper pattern)
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import yaml
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "../../../.env"),
    override=True,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_settings() -> dict[str, Any]:
    settings_path = os.path.join(
        os.path.dirname(__file__), "../../../config/settings.yaml"
    )
    with open(settings_path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class MarketCandidate:
    market_id: str
    platform: str                  # "polymarket" | "kalshi"
    title: str
    category: str
    current_yes_price: float
    yes_bid: float
    yes_ask: float
    volume_24h: int
    open_interest: int
    days_to_expiry: int
    anomaly_flags: list[str]
    scanned_at: str
    # CLOB token IDs for Polymarket order placement (YES token at [0], NO at [1]).
    # market_id stores the condition ID (used for resolution via gamma API).
    # Live CLOB orders require the token ID, not the condition ID.
    clob_token_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Platform Clients (stubs — implement after API access confirmed in Phase 0)
# ---------------------------------------------------------------------------

def fetch_polymarket_markets(
    min_volume: int,
    max_days: int,
    min_liquidity: float,
) -> list[dict[str, Any]]:
    """
    Fetch active markets from Polymarket Gamma API (no auth required for reads).

    Endpoint: GET https://gamma-api.polymarket.com/markets
    Auth: Not required for market discovery — only needed for order placement.

    Reference: https://docs.polymarket.com
    Reference repo: github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot
    """
    base_url = os.environ.get(
        "POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com"
    )
    all_markets: list[dict[str, Any]] = []
    offset = 0
    page_size = 100
    max_pages = 10  # cap at 1000 markets

    with httpx.Client(timeout=15.0) as client:
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "active": "true",
                "closed": "false",
                "limit": page_size,
                "offset": offset,
            }
            resp = client.get(f"{base_url}/markets", params=params)
            resp.raise_for_status()
            markets = resp.json()
            if not isinstance(markets, list) or not markets:
                break
            all_markets.extend(markets)
            if len(markets) < page_size:
                break
            offset += page_size
            time.sleep(0.15)

    return all_markets


def _kalshi_headers(method: str, path: str) -> dict[str, str]:
    """Build RSA-SHA256 auth headers for Kalshi REST API (v2 live)."""
    api_key = os.environ.get("KALSHI_API_KEY", "")
    api_secret = os.environ.get("KALSHI_API_SECRET", "")
    if not api_key or not api_secret:
        raise EnvironmentError(
            "KALSHI_API_KEY and KALSHI_API_SECRET must be set in .env"
        )
    ts = str(int(time.time() * 1000))
    msg = (ts + method.upper() + path).encode()

    # Support both real newlines and literal \n in .env
    pem = api_secret.replace("\\n", "\n").encode()
    private_key = serialization.load_pem_private_key(
        pem,
        password=None,
    )
    signature = private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(signature).decode()

    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "Content-Type": "application/json",
    }


def fetch_kalshi_markets(
    min_volume: int,
    max_days: int,
    min_liquidity: float,
) -> list[dict[str, Any]]:
    """
    Fetch active markets from Kalshi REST API.

    Auth: API key + HMAC-SHA256 header signing.
    Endpoint: GET {base}/markets?status=open&limit=200

    Reference: https://trading-api.readme.io
    Reference repo: github.com/suislanchez/polymarket-kalshi-weather-bot
    """
    # Scanning always uses production — demo has far fewer markets.
    # KALSHI_USE_DEMO only controls order execution (kalshi_client.py).
    base_url = os.environ.get(
        "KALSHI_BASE_URL", "https://trading-api.kalshi.com/trade-api/v2"
    )

    # Fetch from targeted high-volume series instead of paginating all markets.
    # Full pagination returns 40k+ mostly-zero-volume markets; series filtering
    # targets the categories with real trading activity.
    target_series = [
        "KXBTC", "KXETH", "KXSOL",              # crypto
        "KXINX", "KXNDX", "KXSPX",              # equity indices
        "KXFED", "KXCPI", "KXGDP",              # monetary policy / inflation
        "KXUNEMP", "KXPCE", "KXNFP",            # labour / spending
        "KXNHL", "KXNBA", "KXNFL",              # sports
        "KXPRES", "KXCONG", "KXELECT",          # politics
    ]

    path = "/trade-api/v2/markets"
    all_markets: list[dict[str, Any]] = []

    with httpx.Client(timeout=15.0) as client:
        for series in target_series:
            params: dict[str, Any] = {
                "status": "open",
                "limit": 100,
                "series_ticker": series,
            }
            headers = _kalshi_headers("GET", path)
            resp = client.get(f"{base_url}/markets", headers=headers, params=params)
            resp.raise_for_status()
            all_markets.extend(resp.json().get("markets", []))
            time.sleep(0.15)  # stay under 10 req/s

    return all_markets


# ---------------------------------------------------------------------------
# Filtering Logic
# ---------------------------------------------------------------------------

_TICKER_CATEGORY_MAP = {
    "KXBTC": "crypto", "KXETH": "crypto", "KXSOL": "crypto",
    "KXINX": "finance", "KXNDX": "finance", "KXSPX": "finance",
    "KXFED": "economics", "KXCPI": "economics", "KXGDP": "economics",
    "KXUNEMP": "economics", "KXPCE": "economics", "KXNFP": "economics",
    "KXELECT": "politics", "KXPRES": "politics", "KXCONG": "politics",
    "KXNHL": "sports", "KXNBA": "sports", "KXNFL": "sports",
}


def _kalshi_category(event_ticker: str) -> str:
    """Infer a category from Kalshi event ticker prefix."""
    for prefix, cat in _TICKER_CATEGORY_MAP.items():
        if event_ticker.upper().startswith(prefix):
            return cat
    return "other"


# Matches intraday Kalshi crypto price-range markets: KXETH-26MAR2416-B2140
# These resolve hourly based on price movement — LLMs have no edge here.
_INTRADAY_CRYPTO_RE = re.compile(
    r"^KX(ETH|BTC)-\d{2}[A-Z]{3}\d{4}-[BT][\d.]+$", re.IGNORECASE
)


def is_intraday_crypto_range(market_id: str) -> bool:
    """Return True for short-term hourly ETH/BTC price range Kalshi markets."""
    return bool(_INTRADAY_CRYPTO_RE.match(market_id))


def apply_filters(
    raw_markets: list[dict[str, Any]],
    platform: str,
    min_volume: int,
    max_days: int,
    min_liquidity: float,
) -> list[MarketCandidate]:
    """Apply standard filters and return normalized MarketCandidate objects."""
    candidates = []
    now = datetime.now(timezone.utc)

    for market in raw_markets:
        # Each platform uses different field names — normalize here
        if platform == "polymarket":
            volume = float(market.get("volume") or 0)
            liquidity = float(market.get("liquidity") or 0)
            # Gamma API uses endDate (YYYY-MM-DD); CLOB uses end_date_iso
            expiry_str = market.get("endDate") or market.get("end_date_iso", "")
            # outcomePrices is ["yes_price", "no_price"] — may arrive as a JSON string
            raw_prices = market.get("outcomePrices") or ["0.5", "0.5"]
            if isinstance(raw_prices, str):
                import json as _json
                raw_prices = _json.loads(raw_prices)
            yes_price = float(raw_prices[0])
            # Gamma API has no live bid/ask; use mid ± half minimum tick as proxy
            yes_bid = round(yes_price - 0.01, 4)
            yes_ask = round(yes_price + 0.01, 4)
            title = market.get("question", "")
            category = market.get("category") or market.get("groupItemTitle", "other")
            market_id = market.get("conditionId") or market.get("condition_id", "")
            # clobTokenIds arrives as a JSON string or list: ["0xYES...", "0xNO..."]
            raw_token_ids = market.get("clobTokenIds") or market.get("clob_token_ids") or []
            if isinstance(raw_token_ids, str):
                try:
                    raw_token_ids = json.loads(raw_token_ids)
                except Exception:
                    raw_token_ids = []
            clob_token_ids: list[str] = list(raw_token_ids) if raw_token_ids else []
        else:  # kalshi — v2 API field names
            volume = float(market.get("volume_fp") or market.get("volume", 0))
            # liquidity_dollars is not populated by Kalshi API; use open_interest as proxy
            liquidity = float(market.get("open_interest_fp") or market.get("open_interest", 0))
            expiry_str = market.get("close_time", "")
            yes_bid = float(market.get("yes_bid_dollars") or 0)
            yes_ask = float(market.get("yes_ask_dollars") or 0)
            yes_price = yes_ask if yes_ask > 0 else 0.5
            title = market.get("title", "")
            # Kalshi v2 has no category field; derive from event_ticker prefix
            event_ticker = market.get("event_ticker", "")
            category = _kalshi_category(event_ticker)
            market_id = market.get("ticker", "")
            clob_token_ids = []

            # Skip intraday crypto price-range markets — LLMs have no edge
            if is_intraday_crypto_range(market_id):
                continue

        # Volume filter
        if volume < min_volume:
            continue

        # Liquidity filter
        if liquidity < min_liquidity:
            continue

        # Expiry filter
        try:
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            days_remaining = (expiry - now).days
            if days_remaining > max_days or days_remaining < 0:
                continue
        except (ValueError, AttributeError):
            continue

        candidates.append(MarketCandidate(
            market_id=market_id,
            platform=platform,
            title=title,
            category=category,
            current_yes_price=yes_price,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            volume_24h=volume,
            open_interest=int(liquidity),
            days_to_expiry=days_remaining,
            anomaly_flags=[],
            scanned_at=now.isoformat(),
            clob_token_ids=clob_token_ids,
        ))

    return candidates


def rank_candidates(candidates: list[MarketCandidate]) -> list[MarketCandidate]:
    """Rank by anomaly flags first, then by open interest descending."""
    return sorted(
        candidates,
        key=lambda c: (len(c.anomaly_flags) == 0, -c.open_interest),
    )


# ---------------------------------------------------------------------------
# Failure Feedback Deprioritization
# ---------------------------------------------------------------------------

def _load_failure_patterns(failure_log_path: os.PathLike | str) -> dict:
    """Parse machine-readable PATTERNS_JSON from failure_log.md."""
    import re
    try:
        content = open(failure_log_path).read()
    except OSError:
        return {}
    match = re.search(r"<!-- PATTERNS_JSON: (\{.*?\}) -->", content, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def deprioritize_known_failures(
    candidates: list[MarketCandidate],
    failure_log_path: os.PathLike | str | None = None,
) -> list[MarketCandidate]:
    """
    Move candidates matching known failure patterns to the end of the list.
    Does NOT remove them — human review is always possible via the full list.
    """
    if failure_log_path is None:
        failure_log_path = os.path.join(
            os.path.dirname(__file__),
            "../../pm-compound/references/failure_log.md",
        )

    patterns = _load_failure_patterns(failure_log_path)
    avoid_ids = set(patterns.get("market_ids_to_avoid", []))
    if not avoid_ids:
        return candidates

    priority: list[MarketCandidate] = []
    deprioritized: list[MarketCandidate] = []
    for c in candidates:
        if c.market_id in avoid_ids:
            deprioritized.append(c)
        else:
            priority.append(c)

    n = len(deprioritized)
    if n > 0:
        print(f"[scan] deprioritized {n} candidate(s) matching failure patterns", file=sys.stderr)

    return priority + deprioritized


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    settings = load_settings()
    scan_cfg = settings["scan"]

    min_volume = scan_cfg["min_volume_contracts"]
    max_days = scan_cfg["max_days_to_expiry"]
    min_liquidity = scan_cfg.get("min_open_interest", scan_cfg.get("min_liquidity_usd", 50))

    all_candidates: list[MarketCandidate] = []
    errors: list[str] = []

    # Fetch from each enabled platform
    run_cfg_path = os.path.join(
        os.path.dirname(__file__), "../../pm-orchestrator/assets/run-config.yaml"
    )
    with open(run_cfg_path) as f:
        run_cfg = yaml.safe_load(f)

    platforms = run_cfg.get("platforms", {})

    if platforms.get("polymarket", False):
        try:
            raw = fetch_polymarket_markets(min_volume, max_days, min_liquidity)
            all_candidates.extend(apply_filters(raw, "polymarket", min_volume, max_days, min_liquidity))
        except NotImplementedError as e:
            errors.append(f"polymarket: {e}")
        except Exception as e:
            errors.append(f"polymarket fetch failed: {e}")

    if platforms.get("kalshi", False):
        try:
            raw = fetch_kalshi_markets(min_volume, max_days, min_liquidity)
            all_candidates.extend(apply_filters(raw, "kalshi", min_volume, max_days, min_liquidity))
        except NotImplementedError as e:
            errors.append(f"kalshi: {e}")
        except Exception as e:
            errors.append(f"kalshi fetch failed: {e}")

    if not all_candidates and errors:
        print(f"[pm-scan] No candidates found. Errors: {errors}", file=sys.stderr)
        sys.exit(1)

    # Cap at 25 per platform so neither dominates the output
    by_platform: dict[str, list[MarketCandidate]] = {}
    for c in all_candidates:
        by_platform.setdefault(c.platform, []).append(c)
    ranked_all: list[MarketCandidate] = []
    for platform_candidates in by_platform.values():
        ranked_all.extend(rank_candidates(platform_candidates)[:25])
    ranked = rank_candidates(ranked_all)

    # Deprioritize candidates matching known failure patterns from pm-compound
    ranked = deprioritize_known_failures(ranked)

    output = {
        "candidates": [asdict(c) for c in ranked],
        "scan_id": f"scan_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
