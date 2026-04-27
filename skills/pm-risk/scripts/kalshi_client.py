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
# Order Status
# ---------------------------------------------------------------------------

def get_order(order_id: str, use_demo: bool = True) -> dict[str, Any]:
    """
    Fetch a single Kalshi order by ID.

    Returns {"status": "resting"|"filled"|"canceled"|"unknown", "fill_price": float | None}
    Kalshi statuses: "resting" = still open, "executed" = filled, "canceled" = expired/cancelled
    """
    path = f"/trade-api/v2/portfolio/orders/{order_id}"
    headers = _get_demo_headers("GET", path) if use_demo else _get_headers("GET", path)
    if headers is None:
        return {"status": "unknown", "fill_price": None}
    base_url = KALSHI_DEMO_URL if use_demo else KALSHI_BASE_URL
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{base_url}/portfolio/orders/{order_id}", headers=headers)
        resp.raise_for_status()
        order = resp.json().get("order", {})
        status_str = order.get("status", "")
        if status_str == "executed":
            raw_fill = order.get("avg_price") or order.get("avg_fill_price", 0)
            return {"status": "filled", "fill_price": float(raw_fill) / 100.0}
        elif status_str == "resting":
            return {"status": "resting", "fill_price": None}
        elif status_str in ("canceled", "cancellation_requested"):
            filled = int(order.get("no_count_filled", 0)) + int(order.get("yes_count_filled", 0))
            if filled > 0:
                raw_fill = order.get("avg_price") or order.get("avg_fill_price", 0)
                return {"status": "canceled", "fill_price": float(raw_fill) / 100.0, "filled_count": filled}
            return {"status": "canceled", "fill_price": None, "filled_count": 0}
        else:
            print(f"[pm-risk] get_order({order_id}): unexpected status '{status_str}'", file=sys.stderr)
            return {"status": "unknown", "fill_price": None}
    except Exception as e:
        print(f"[pm-risk] get_order({order_id}) error: {e}", file=sys.stderr)
        return {"status": "unknown", "fill_price": None}


# ---------------------------------------------------------------------------
# Depth Check
# ---------------------------------------------------------------------------

_ORDERBOOK_PATH_TPL = "/trade-api/v2/markets/{ticker}/orderbook"


def _legacy_to_dollar_tuples(levels: list | None) -> list[tuple[float, float]]:
    """Normalize legacy [[cents, count], ...] levels to [(decimal_price, dollars), ...]."""
    if not levels:
        return []
    out = []
    for price_cents, count in levels:
        price = int(price_cents) / 100.0
        out.append((price, price * int(count)))
    return out


def get_orderbook_snapshot(
    market_ticker: str,
    *,
    use_demo: bool = True,
) -> dict[str, list[tuple[float, float]]]:
    """
    Fetch Kalshi orderbook and return a normalized snapshot.

    Returns {"yes_bids": [(price, dollars)], "no_bids": [(price, dollars)]}.
    Both YES and NO arrays are BIDS — Kalshi has no offers; a BUY at limit L
    crosses the OPPOSITE side's bids at price >= (1 - L).

    Returns empty arrays when credentials are missing. Raises on HTTP error
    so callers can apply their own fail-open / fail-closed policy.
    """
    ob_path = _ORDERBOOK_PATH_TPL.format(ticker=market_ticker)
    headers = _get_demo_headers("GET", ob_path) if use_demo else _get_headers("GET", ob_path)
    if headers is None:
        return {"yes_bids": [], "no_bids": []}

    base_url = KALSHI_DEMO_URL if use_demo else KALSHI_BASE_URL
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(
            f"{base_url}/markets/{market_ticker}/orderbook",
            headers=headers,
            params={"depth": 10},
        )
    resp.raise_for_status()
    data = resp.json()

    ob_fp = data.get("orderbook_fp") or {}
    if ob_fp:
        return {
            "yes_bids": [(float(p), float(d)) for p, d in (ob_fp.get("yes_dollars") or [])],
            "no_bids":  [(float(p), float(d)) for p, d in (ob_fp.get("no_dollars") or [])],
        }

    orderbook = data.get("orderbook") or {}
    return {
        "yes_bids": _legacy_to_dollar_tuples(orderbook.get("yes")),
        "no_bids":  _legacy_to_dollar_tuples(orderbook.get("no")),
    }


def get_depth(
    market_ticker: str,
    direction: str,
    limit_price: float,
    contracts: int,
    use_demo: bool = True,
) -> bool:
    """
    Check if the Kalshi orderbook has sufficient liquidity to fill `contracts`
    of `direction` at limit `limit_price`. Fails open on HTTP errors.
    """
    ob_path = _ORDERBOOK_PATH_TPL.format(ticker=market_ticker)
    headers = _get_demo_headers("GET", ob_path) if use_demo else _get_headers("GET", ob_path)
    if headers is None:
        return True  # credential-missing fallback: pass through

    cross_price = 1.0 - limit_price
    if cross_price <= 0:
        return False

    try:
        snapshot = get_orderbook_snapshot(market_ticker, use_demo=use_demo)
    except Exception as e:
        print(
            f"[pm-risk] Kalshi depth check error for {market_ticker}: {e}. Passing through.",
            file=sys.stderr,
        )
        return True  # fail open

    opposite = "no" if direction == "yes" else "yes"
    levels = snapshot[f"{opposite}_bids"]
    total_dollars = sum(dollars for price, dollars in levels if price >= cross_price)
    return (total_dollars / cross_price) >= contracts
