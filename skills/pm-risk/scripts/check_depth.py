"""
check_depth.py — Pre-Trade Orderbook Depth Check for pm-risk skill

Queries the Polymarket CLOB or Kalshi orderbook before placing an order
to verify there is enough liquidity at the limit price to fill the order.
Aborts early if depth is insufficient, avoiding execution attempts on
illiquid markets that would fail or incur excessive slippage.

Returns True if depth is adequate, False otherwise.
"""

from __future__ import annotations

import os
import sys
from typing import Any


# Minimum contracts that must be available in the book at or better than limit_price
_MIN_AVAILABLE_CONTRACTS = 10


# ---------------------------------------------------------------------------
# Platform-specific depth checkers
# ---------------------------------------------------------------------------

def _polymarket_depth(
    market_id: str,
    direction: str,
    limit_price: float,
    contracts: int,
) -> bool:
    """
    Check Polymarket CLOB orderbook depth.
    Returns True if enough liquidity exists at limit_price or better.

    Reference: https://docs.polymarket.com/#get-order-book
    """
    api_key = os.environ.get("POLYMARKET_API_KEY", "")
    if not api_key:
        print(
            f"[pm-risk] check_depth: POLYMARKET_API_KEY not set for {market_id}. "
            "Depth check skipped.",
            file=sys.stderr,
        )
        return True

    from polymarket_client import get_depth as _pm_depth
    return _pm_depth(market_id, direction, limit_price, contracts)


def _kalshi_depth(
    market_ticker: str,
    direction: str,
    limit_price: float,
    contracts: int,
) -> bool:
    """
    Check Kalshi orderbook depth.
    Returns True if enough liquidity exists at limit_price or better.

    Reference: https://trading-api.readme.io/#get-market-order-book
    """
    api_key = os.environ.get("KALSHI_API_KEY", "")
    if not api_key:
        print(
            f"[pm-risk] check_depth: KALSHI_API_KEY not set for {market_ticker}. "
            "Depth check skipped.",
            file=sys.stderr,
        )
        return True

    from kalshi_client import get_depth as _k_depth
    return _k_depth(market_ticker, direction, limit_price, contracts, use_demo=False)


# ---------------------------------------------------------------------------
# Public Interface
# ---------------------------------------------------------------------------

def has_adequate_depth(
    platform: str,
    market_id: str,
    direction: str,
    limit_price: float,
    contracts: int,
) -> bool:
    """
    Return True if the orderbook has sufficient liquidity to fill the order.

    Args:
        platform:    "polymarket" or "kalshi"
        market_id:   Polymarket token ID or Kalshi market ticker
        direction:   "yes" or "no"
        limit_price: The limit price we intend to submit
        contracts:   Number of contracts we want to buy

    Returns:
        True → proceed with order placement
        False → reject order; log insufficient_depth
    """
    if platform == "polymarket":
        return _polymarket_depth(market_id, direction, limit_price, contracts)
    if platform == "kalshi":
        return _kalshi_depth(market_id, direction, limit_price, contracts)

    # Unknown platform — fail safe by allowing the order to attempt
    print(
        f"[pm-risk] check_depth: Unknown platform '{platform}'. Depth check skipped.",
        file=sys.stderr,
    )
    return True
