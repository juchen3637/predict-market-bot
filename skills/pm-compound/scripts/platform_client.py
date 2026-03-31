"""
platform_client.py — Shared API Client for pm-compound skill

Provides resolution queries for Polymarket and Kalshi markets.
Used by historical_fetcher.py (training data) and resolver.py (nightly resolution).
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "../../../.env"),
    override=True,
)


# ---------------------------------------------------------------------------
# Kalshi Auth
# ---------------------------------------------------------------------------

def _kalshi_headers(method: str, path: str) -> dict[str, str]:
    """Build RSA-SHA256 auth headers for Kalshi REST API (v2)."""
    api_key = os.environ.get("KALSHI_API_KEY", "")
    api_secret = os.environ.get("KALSHI_API_SECRET", "")
    if not api_key or not api_secret:
        raise EnvironmentError(
            "KALSHI_API_KEY and KALSHI_API_SECRET must be set in .env"
        )
    ts = str(int(time.time() * 1000))
    msg = (ts + method.upper() + path).encode()

    pem = api_secret.replace("\\n", "\n").encode()
    private_key = serialization.load_pem_private_key(pem, password=None)
    signature = private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(signature).decode()

    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "Content-Type": "application/json",
    }


def _kalshi_base_url() -> str:
    # Resolution and market data always use production — markets settle on
    # production regardless of whether orders were placed on demo.
    return os.environ.get(
        "KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"
    )


# ---------------------------------------------------------------------------
# Single Market Resolution
# ---------------------------------------------------------------------------

def get_market_resolution(market_id: str, platform: str) -> dict[str, Any] | None:
    """
    Query a single market for resolution status.

    Returns:
        {"resolved": bool, "outcome": "yes"|"no"|None, "resolved_at": ISO|None}
        or None on error.
    """
    if platform == "polymarket":
        return _polymarket_resolution(market_id)
    elif platform == "kalshi":
        return _kalshi_resolution(market_id)
    else:
        return None


def _polymarket_resolution(market_id: str) -> dict[str, Any] | None:
    gamma_base = os.environ.get(
        "POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com"
    )
    clob_base = os.environ.get(
        "POLYMARKET_CLOB_URL", "https://clob.polymarket.com"
    )
    try:
        with httpx.Client(timeout=15.0) as client:
            data = _polymarket_fetch_market(client, gamma_base, clob_base, market_id)
            if data is None:
                return None

        raw_prices = data.get("outcomePrices") or ["0.5", "0.5"]
        if isinstance(raw_prices, str):
            import json as _json
            raw_prices = _json.loads(raw_prices)

        resolved = bool(data.get("resolved", False))
        outcome: str | None = None
        # Also treat price at terminal values (0/1) as resolved even if flag not set yet
        if str(raw_prices[0]) == "1" or str(raw_prices[0]) == "1.0":
            outcome = "yes"
            resolved = True
        elif str(raw_prices[1]) == "1" or str(raw_prices[1]) == "1.0":
            outcome = "no"
            resolved = True

        return {
            "resolved": resolved,
            "outcome": outcome,
            "resolved_at": data.get("resolutionTime"),
        }
    except Exception:
        return None


def _polymarket_fetch_market(
    client: "httpx.Client",
    gamma_base: str,
    clob_base: str,
    market_id: str,
) -> "dict[str, Any] | None":
    """
    Look up a Polymarket market, handling both condition IDs and CLOB token IDs.

    Primary:  GET gamma-api.polymarket.com/markets?condition_id=<market_id>
    Fallback: GET clob.polymarket.com/markets/<market_id> → extract real
              condition_id → retry gamma. Used when the stored ID is a CLOB
              token ID rather than a gamma condition ID, or when the primary
              query returns a stale AMM-era market.
    """
    data = _gamma_lookup(client, gamma_base, market_id)
    if data is not None and not _is_stale_amm_market(data):
        return data

    # Fallback: ask the CLOB for the canonical condition_id
    try:
        clob_resp = client.get(f"{clob_base}/markets/{market_id}", timeout=10.0)
        if clob_resp.status_code == 200:
            real_cid = clob_resp.json().get("condition_id", "")
            if real_cid and real_cid != market_id:
                retry = _gamma_lookup(client, gamma_base, real_cid)
                if retry is not None:
                    return retry
    except Exception:
        pass

    return data  # may be None or stale; caller handles both


def _gamma_lookup(
    client: "httpx.Client",
    base_url: str,
    condition_id: str,
) -> "dict[str, Any] | None":
    """Query gamma API by condition_id. Returns market dict or None."""
    try:
        resp = client.get(
            f"{base_url}/markets", params={"condition_id": condition_id}
        )
        resp.raise_for_status()
        results = resp.json()
        if isinstance(results, list):
            return results[0] if results else None
        if isinstance(results, dict) and results:
            return results
        return None
    except Exception:
        return None


def _is_stale_amm_market(data: "dict[str, Any]") -> bool:
    """
    Detect AMM-era markets returned as false positives by the gamma API.
    These have outcomePrices=["0","0"] with resolved=None — they never
    properly resolved and can't be used for resolution checks.
    """
    raw = data.get("outcomePrices") or ["0.5", "0.5"]
    if isinstance(raw, str):
        try:
            import json as _j
            raw = _j.loads(raw)
        except Exception:
            return False
    return (
        len(raw) >= 2
        and str(raw[0]) == "0"
        and str(raw[1]) == "0"
        and data.get("resolved") is None
    )


def _kalshi_resolution(market_id: str) -> dict[str, Any] | None:
    base_url = _kalshi_base_url()
    path = f"/trade-api/v2/markets/{market_id}"
    try:
        with httpx.Client(timeout=15.0) as client:
            headers = _kalshi_headers("GET", path)
            resp = client.get(f"{base_url}/markets/{market_id}", headers=headers)
            resp.raise_for_status()
            data = resp.json().get("market", resp.json())

        status = data.get("status", "")
        result = data.get("result", "")

        if status not in ("settled", "finalized"):
            return {"resolved": False, "outcome": None, "resolved_at": None}

        if result == "yes":
            outcome: str | None = "yes"
        elif result == "no":
            outcome = "no"
        else:
            outcome = None

        return {
            "resolved": True,
            "outcome": outcome,
            "resolved_at": data.get("close_time"),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Bulk Resolved Markets
# ---------------------------------------------------------------------------

_KALSHI_TARGET_SERIES = [
    "KXBTC", "KXETH",
    "KXINX", "KXFED", "KXCPI",
    "KXNHL", "KXNBA", "KXNFL",
    "KXPRES", "KXCONG",
]


def fetch_resolved_markets(platform: str, limit: int = 200) -> list[dict[str, Any]]:
    """
    Fetch bulk resolved/closed markets from a platform.

    Returns raw API dicts for transformation by historical_fetcher.py.
    """
    if platform == "polymarket":
        return _fetch_polymarket_resolved(limit)
    elif platform == "kalshi":
        return _fetch_kalshi_resolved(limit)
    else:
        return []


def _fetch_polymarket_resolved(limit: int) -> list[dict[str, Any]]:
    base_url = os.environ.get(
        "POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com"
    )
    all_markets: list[dict[str, Any]] = []
    # Skip first ~15k markets which are old AMM-era (outcomePrices=["0","0"], unreliable).
    # CLOB-era markets with proper resolution data start around offset 15000.
    offset = 15000
    page_size = min(100, limit)

    with httpx.Client(timeout=15.0) as client:
        while len(all_markets) < limit:
            params: dict[str, Any] = {
                "closed": "true",
                "limit": page_size,
                "offset": offset,
            }
            try:
                resp = client.get(f"{base_url}/markets", params=params)
                resp.raise_for_status()
                markets = resp.json()
            except Exception:
                break

            if not isinstance(markets, list) or not markets:
                break

            all_markets.extend(markets)
            if len(markets) < page_size:
                break
            offset += page_size
            time.sleep(0.15)

    return all_markets[:limit]


def _fetch_kalshi_resolved(limit: int) -> list[dict[str, Any]]:
    base_url = _kalshi_base_url()
    path = "/trade-api/v2/markets"
    all_markets: list[dict[str, Any]] = []

    per_series = max(1, limit // len(_KALSHI_TARGET_SERIES))

    with httpx.Client(timeout=15.0) as client:
        for series in _KALSHI_TARGET_SERIES:
            if len(all_markets) >= limit:
                break
            params: dict[str, Any] = {
                "status": "settled",
                "limit": min(100, per_series),
                "series_ticker": series,
            }
            try:
                headers = _kalshi_headers("GET", path)
                resp = client.get(f"{base_url}/markets", headers=headers, params=params)
                resp.raise_for_status()
                all_markets.extend(resp.json().get("markets", []))
            except Exception:
                continue
            time.sleep(0.15)

    return all_markets[:limit]
