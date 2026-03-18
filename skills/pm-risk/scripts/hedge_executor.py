"""
hedge_executor.py — Hedge Execution for pm-risk skill

Places an offsetting order for 50% of the original position size.
YES position → BUY NO (opposite direction) on same market.
NO position  → BUY YES on same market.
"""

from __future__ import annotations

import sys
from typing import Any


def execute_hedge(
    trade: Any,
    use_demo: bool = True,
) -> dict[str, Any]:
    """
    Places an offsetting order for 50% of the original position size.

    Args:
        trade:    TradeRecord (or dict with same fields) of the original filled trade
        use_demo: Kalshi only — use demo environment

    Returns:
        {"order_id": str | None, "fill_price": float | None, "status": str}
    """
    # Accept both dataclass and dict
    if hasattr(trade, "__dataclass_fields__"):
        from dataclasses import asdict
        trade_dict = asdict(trade)
    else:
        trade_dict = dict(trade)

    market_id = trade_dict["market_id"]
    platform = trade_dict["platform"]
    direction = trade_dict["direction"]
    size_contracts = trade_dict["size_contracts"]
    fill_price = trade_dict.get("fill_price") or trade_dict.get("entry_price", 0.5)

    # Hedge is the opposite direction
    hedge_direction = "no" if direction == "yes" else "yes"
    hedge_contracts = max(1, size_contracts // 2)

    # Fetch current market mid for hedge price
    hedge_price = _get_hedge_price(platform, market_id, hedge_direction, fill_price, use_demo)

    print(
        f"[pm-risk] Executing hedge: {platform} {market_id} "
        f"BUY {hedge_direction.upper()} x{hedge_contracts} @ {hedge_price:.3f}",
        file=sys.stderr,
    )

    if platform == "polymarket":
        from polymarket_client import place_order as _pm_place  # noqa: PLC0415
        return _pm_place(market_id, hedge_direction, hedge_contracts, hedge_price)
    elif platform == "kalshi":
        from kalshi_client import place_order as _k_place  # noqa: PLC0415
        return _k_place(market_id, hedge_direction, hedge_contracts, hedge_price, use_demo)
    else:
        print(f"[pm-risk] Unknown platform for hedge: {platform}", file=sys.stderr)
        return {"order_id": None, "fill_price": None, "status": "declined", "reason": "unknown_platform"}


def _get_hedge_price(
    platform: str,
    market_id: str,
    hedge_direction: str,
    fallback_price: float,
    use_demo: bool,
) -> float:
    """
    Fetch current market mid price for the hedge direction.
    Falls back to 1 - fill_price on any error.
    """
    try:
        if platform == "polymarket":
            from polymarket_client import _get_client  # noqa: PLC0415
            client = _get_client()
            if client is None:
                return 1.0 - fallback_price
            book = client.get_order_book(market_id)
            if hedge_direction == "yes":
                asks = book.asks or []
                if asks:
                    return float(min(a.price for a in asks))
            else:
                bids = book.bids or []
                if bids:
                    return 1.0 - float(max(b.price for b in bids))
        elif platform == "kalshi":
            import httpx  # noqa: PLC0415
            from kalshi_client import (  # noqa: PLC0415
                KALSHI_BASE_URL, KALSHI_DEMO_URL, _get_headers,
                _ORDERBOOK_PATH_TPL,
            )
            ob_path = _ORDERBOOK_PATH_TPL.format(ticker=market_id)
            headers = _get_headers("GET", ob_path)
            if headers is None:
                return 1.0 - fallback_price
            base_url = KALSHI_DEMO_URL if use_demo else KALSHI_BASE_URL
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    f"{base_url}/markets/{market_id}/orderbook",
                    headers=headers,
                )
            resp.raise_for_status()
            orderbook = resp.json().get("orderbook", {})
            levels = orderbook.get(hedge_direction, [])
            if levels:
                # Best ask = highest price on the hedge direction side
                best_cents = max(level[0] for level in levels)
                return best_cents / 100.0
    except Exception as e:
        print(
            f"[pm-risk] Hedge price fetch error for {market_id}: {e}. Using fallback.",
            file=sys.stderr,
        )

    return 1.0 - fallback_price
