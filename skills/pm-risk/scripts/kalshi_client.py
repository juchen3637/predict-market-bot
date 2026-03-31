"""
kalshi_client.py — Kalshi REST order placement and depth check for pm-risk skill.

RSA-SHA256 auth copied from filter_markets.py:_kalshi_headers to avoid cross-skill imports.
All credentials are read from environment variables.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KALSHI_BASE_URL = os.environ.get(
    "KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"
)
KALSHI_DEMO_URL = os.environ.get(
    "KALSHI_DEMO_URL", "https://demo-api.kalshi.co/trade-api/v2"
)
KALSHI_DEMO_API_KEY = os.environ.get("KALSHI_DEMO_API_KEY", "")
KALSHI_DEMO_API_SECRET = os.environ.get("KALSHI_DEMO_API_SECRET", "")

_DECLINED = {
    "order_id": None,
    "fill_price": None,
    "status": "declined",
    "reason": "live_mode_not_enabled",
}


# ---------------------------------------------------------------------------
# Auth (copied verbatim from filter_markets.py:_kalshi_headers, lines 115-140)
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


def _get_headers(method: str, path: str) -> dict[str, str] | None:
    """Return signed production headers, or None if credentials are missing."""
    api_key = os.environ.get("KALSHI_API_KEY", "")
    api_secret = os.environ.get("KALSHI_API_SECRET", "")
    if not api_key or not api_secret:
        return None
    return _kalshi_headers(method, path)


def _get_demo_headers(method: str, path: str) -> dict[str, str] | None:
    """Return signed demo headers using KALSHI_DEMO_API_KEY/SECRET, or None if missing.

    Demo API requires RSA-PSS (SHA256) — different from production PKCS1v15.
    """
    api_key = KALSHI_DEMO_API_KEY
    api_secret = KALSHI_DEMO_API_SECRET
    if not api_key or not api_secret:
        return None
    ts = str(int(time.time() * 1000))
    msg = (ts + method.upper() + path).encode()
    pem = api_secret.replace("\\n", "\n").encode()
    private_key = serialization.load_pem_private_key(pem, password=None)
    signature = private_key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()
    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Order Placement
# ---------------------------------------------------------------------------

_ORDERS_PATH = "/trade-api/v2/portfolio/orders"


def place_order(
    market_ticker: str,
    direction: str,
    contracts: int,
    limit_price: float,
    use_demo: bool = True,
) -> dict[str, Any]:
    """
    Place a limit order on Kalshi.

    Args:
        market_ticker: Kalshi market ticker (e.g. "KXBTC-23-T45000")
        direction:     "yes" or "no"
        contracts:     Number of contracts to buy
        limit_price:   Limit price in [0, 1]
        use_demo:      Use demo environment if True

    Returns:
        {"order_id": str | None, "fill_price": float | None, "status": str}
        status is one of: "filled", "open", "declined"
    """
    headers = _get_demo_headers("POST", _ORDERS_PATH) if use_demo else _get_headers("POST", _ORDERS_PATH)
    if headers is None:
        return dict(_DECLINED)

    base_url = KALSHI_DEMO_URL if use_demo else KALSHI_BASE_URL

    # Convert price to cents, clamped to [1, 99]
    price_cents = int(round(limit_price * 100))
    price_cents = max(1, min(99, price_cents))

    body: dict[str, Any] = {
        "ticker": market_ticker,
        "action": "buy",
        "side": direction,
        "type": "limit",
        "count": contracts,
    }
    if direction == "yes":
        body["yes_price"] = price_cents
    else:
        body["no_price"] = price_cents

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{base_url}/portfolio/orders",
                headers=headers,
                json=body,
            )

        if resp.status_code >= 400:
            reason = resp.text[:100]
            print(
                f"[pm-risk] Kalshi order HTTP {resp.status_code} for {market_ticker}: {reason}",
                file=sys.stderr,
            )
            return {
                "order_id": None,
                "fill_price": None,
                "status": "declined",
                "reason": f"http_{resp.status_code}: {reason}",
            }

        data = resp.json()
        order = data.get("order", {})
        order_id = order.get("order_id") or order.get("id")
        status_str = order.get("status", "")

        if status_str == "executed":
            raw_fill = order.get("avg_price") or order.get("avg_fill_price", 0)
            fill_price = float(raw_fill) / 100.0
            return {
                "order_id": order_id,
                "fill_price": fill_price,
                "status": "filled",
            }
        elif status_str == "resting":
            return {
                "order_id": order_id,
                "fill_price": None,
                "status": "open",
            }
        else:
            reason = order.get("error") or f"unexpected_status:{status_str}"
            print(
                f"[pm-risk] Kalshi unexpected order status '{status_str}' for {market_ticker}",
                file=sys.stderr,
            )
            return {
                "order_id": order_id,
                "fill_price": None,
                "status": "declined",
                "reason": str(reason)[:100],
            }

    except Exception as e:
        print(f"[pm-risk] Kalshi order error for {market_ticker}: {e}", file=sys.stderr)
        return {
            "order_id": None,
            "fill_price": None,
            "status": "declined",
            "reason": str(e)[:100],
        }


# ---------------------------------------------------------------------------
# Depth Check
# ---------------------------------------------------------------------------

_ORDERBOOK_PATH_TPL = "/trade-api/v2/markets/{ticker}/orderbook"


def get_depth(
    market_ticker: str,
    direction: str,
    limit_price: float,
    contracts: int,
    use_demo: bool = True,
) -> bool:
    """
    Check if the Kalshi orderbook has sufficient liquidity.

    Sums contracts from orderbook.yes (or .no) where price >= our limit cents.
    Returns True if total >= requested contracts.
    """
    ob_path = _ORDERBOOK_PATH_TPL.format(ticker=market_ticker)
    headers = _get_demo_headers("GET", ob_path) if use_demo else _get_headers("GET", ob_path)
    if headers is None:
        return True  # credential-missing fallback: pass through

    base_url = KALSHI_DEMO_URL if use_demo else KALSHI_BASE_URL
    limit_cents = int(round(limit_price * 100))

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{base_url}/markets/{market_ticker}/orderbook",
                headers=headers,
                params={"depth": 10},
            )
        resp.raise_for_status()

        data = resp.json()

        # New API format: orderbook_fp with decimal prices and dollar amounts
        ob_fp = data.get("orderbook_fp", {})
        if ob_fp:
            key = f"{direction}_dollars"
            levels = ob_fp.get(key, [])
            total_dollars = sum(float(lvl[1]) for lvl in (levels or []) if float(lvl[0]) >= limit_price)
            needed_dollars = contracts * limit_price
            return total_dollars >= needed_dollars

        # Fallback: legacy orderbook format with integer cents and contract counts
        orderbook = data.get("orderbook", {})
        levels = orderbook.get(direction, [])
        total = sum(lvl[1] for lvl in (levels or []) if lvl[0] >= limit_cents)
        return total >= contracts

    except Exception as e:
        print(
            f"[pm-risk] Kalshi depth check error for {market_ticker}: {e}. Passing through.",
            file=sys.stderr,
        )
        return True  # fail open
