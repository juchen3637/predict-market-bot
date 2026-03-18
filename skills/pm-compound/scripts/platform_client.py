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
    override=False,
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
    use_demo = os.environ.get("KALSHI_USE_DEMO", "true").lower() != "false"
    if use_demo:
        return os.environ.get(
            "KALSHI_DEMO_URL", "https://demo-api.kalshi.co/trade-api/v2"
        )
    return os.environ.get(
        "KALSHI_BASE_URL", "https://trading-api.kalshi.com/trade-api/v2"
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
    base_url = os.environ.get(
        "POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com"
    )
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{base_url}/markets/{market_id}")
            resp.raise_for_status()
            data = resp.json()

        raw_prices = data.get("outcomePrices") or ["0.5", "0.5"]
        if isinstance(raw_prices, str):
            import json as _json
            raw_prices = _json.loads(raw_prices)

        resolved = bool(data.get("resolved", False))
        outcome: str | None = None
        if resolved:
            if str(raw_prices[0]) == "1":
                outcome = "yes"
            elif str(raw_prices[1]) == "1":
                outcome = "no"

        return {
            "resolved": resolved,
            "outcome": outcome,
            "resolved_at": data.get("resolutionTime"),
        }
    except Exception:
        return None


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

        if status != "settled":
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
