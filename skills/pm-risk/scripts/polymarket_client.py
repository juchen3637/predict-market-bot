"""
polymarket_client.py — Polymarket CLOB order placement and depth check for pm-risk skill.

Uses py_clob_client library for EIP-712 signing and HMAC header auth.
All credentials are read from environment variables.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from py_clob_client.constants import POLYGON
from py_clob_client.order_builder.constants import BUY, SELL


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CLOB_HOST = os.environ.get("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")
_CHAIN_ID = int(os.environ.get("POLYMARKET_CHAIN_ID", str(POLYGON)))

_DEMO_CLOB_HOST = os.environ.get("POLYMARKET_DEMO_CLOB_URL", "https://clob.polymarket.com")
try:
    _DEMO_CHAIN_ID = int(os.environ.get("POLYMARKET_DEMO_CHAIN_ID", "80002"))
except ValueError:
    raise ValueError(
        f"POLYMARKET_DEMO_CHAIN_ID must be an integer "
        f"(got: {os.environ.get('POLYMARKET_DEMO_CHAIN_ID')!r})"
    )

_DECLINED = {
    "order_id": None,
    "fill_price": None,
    "status": "declined",
    "reason": "live_mode_not_enabled",
}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_client(use_demo: bool = False) -> ClobClient | None:
    """Return an authenticated ClobClient, or None if any credential is missing."""
    if use_demo:
        api_key = os.environ.get("POLYMARKET_DEMO_API_KEY", "")
        api_secret = os.environ.get("POLYMARKET_DEMO_API_SECRET", "")
        api_passphrase = os.environ.get("POLYMARKET_DEMO_API_PASSPHRASE", "")
        private_key = os.environ.get("POLYMARKET_DEMO_WALLET_PRIVATE_KEY", "")
        host = _DEMO_CLOB_HOST
        chain_id = _DEMO_CHAIN_ID
    else:
        api_key = os.environ.get("POLYMARKET_API_KEY", "")
        api_secret = os.environ.get("POLYMARKET_API_SECRET", "")
        api_passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE", "")
        private_key = os.environ.get("POLYMARKET_WALLET_PRIVATE_KEY", "")
        host = _CLOB_HOST
        chain_id = _CHAIN_ID

    if not all([api_key, api_secret, api_passphrase, private_key]):
        return None

    creds = ApiCreds(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
    )
    return ClobClient(
        host=host,
        chain_id=chain_id,
        key=private_key,
        creds=creds,
    )


# ---------------------------------------------------------------------------
# Order Placement
# ---------------------------------------------------------------------------

def place_order(
    market_id: str,
    direction: str,
    contracts: int,
    limit_price: float,
    side: str = "BUY",
    token_id: str | None = None,
    use_demo: bool = False,
) -> dict[str, Any]:
    """
    Place a limit order on Polymarket CLOB.

    Args:
        market_id:   Polymarket condition ID (used for tracking/resolution)
        direction:   "yes" or "no"
        contracts:   Number of contracts to buy/sell
        limit_price: Limit price in [0, 1]
        side:        "BUY" (default) or "SELL" (for exit / hedge)
        token_id:    CLOB token ID for the order direction (YES or NO token).
                     The CLOB requires a token ID, not a condition ID.
                     Sourced from MarketCandidate.clob_token_ids. Falls back
                     to market_id if not provided (legacy behaviour).

    Returns:
        {"order_id": str | None, "fill_price": float | None, "status": str}
        status is one of: "filled", "open", "declined"
    """
    client = _get_client(use_demo=use_demo)
    if client is None:
        return dict(_DECLINED)

    order_side = SELL if side.upper() == "SELL" else BUY
    # Use explicit token_id if provided; fall back to market_id for backwards
    # compatibility with any callers that don't pass clob_token_ids yet.
    clob_token_id = token_id or market_id

    try:
        order_args = OrderArgs(
            token_id=clob_token_id,
            price=limit_price,
            size=float(contracts),
            side=order_side,
        )
        response = client.create_and_post_order(order_args)

        order_id = response.get("orderID") or response.get("order_id")
        status_str = response.get("status", "")

        if status_str == "MATCHED":
            return {
                "order_id": order_id,
                "fill_price": limit_price,
                "status": "filled",
            }
        elif status_str == "LIVE":
            return {
                "order_id": order_id,
                "fill_price": None,
                "status": "open",
            }
        else:
            # Unexpected status — treat as declined
            reason = response.get("errorMsg") or response.get("error") or f"unexpected_status:{status_str}"
            print(
                f"[pm-risk] Polymarket unexpected order status '{status_str}' for {market_id}: {reason}",
                file=sys.stderr,
            )
            return {
                "order_id": order_id,
                "fill_price": None,
                "status": "declined",
                "reason": str(reason)[:100],
            }

    except Exception as e:
        print(f"[pm-risk] Polymarket order error for {market_id}: {e}", file=sys.stderr)
        return {
            "order_id": None,
            "fill_price": None,
            "status": "declined",
            "reason": str(e)[:100],
        }


# ---------------------------------------------------------------------------
# Depth Check
# ---------------------------------------------------------------------------

def get_depth(
    market_id: str,
    direction: str,
    limit_price: float,
    contracts: int,
    use_demo: bool = False,
) -> bool:
    """
    Check if the Polymarket CLOB orderbook has sufficient liquidity.

    For "yes" BUY: sum asks where price <= limit_price.
    For "no" BUY: sum bids where price >= (1 - limit_price).

    Returns True if available size >= requested contracts.
    """
    client = _get_client(use_demo=use_demo)
    if client is None:
        return True  # credential-missing fallback: pass through

    try:
        book = client.get_order_book(market_id)
        total = 0.0

        if direction == "yes":
            # Buying yes: look at asks at or below our limit price
            for ask in (book.asks or []):
                if float(ask.price) <= limit_price:
                    total += float(ask.size)
        else:
            # Buying no: equivalent to selling yes; look at bids at or above (1 - limit_price)
            threshold = 1.0 - limit_price
            for bid in (book.bids or []):
                if float(bid.price) >= threshold:
                    total += float(bid.size)

        return total >= contracts

    except Exception as e:
        print(
            f"[pm-risk] Polymarket depth check error for {market_id}: {e}. Passing through.",
            file=sys.stderr,
        )
        return True  # fail open so depth errors don't block trading
