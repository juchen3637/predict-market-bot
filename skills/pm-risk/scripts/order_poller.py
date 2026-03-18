"""
order_poller.py — Partial Fill / Order Polling for pm-risk skill

Polls an open/resting order until it fills, times out, or is cancelled.
"""

from __future__ import annotations

import sys
import time
from typing import Any


# ---------------------------------------------------------------------------
# Polymarket polling
# ---------------------------------------------------------------------------

def _poll_polymarket(
    order_id: str,
    market_id: str,
    timeout_seconds: int,
    interval_seconds: int,
) -> dict[str, Any]:
    """Poll Polymarket CLOB until order fills, cancels, or times out."""
    from polymarket_client import _get_client  # noqa: PLC0415

    client = _get_client()
    if client is None:
        return {"order_id": order_id, "fill_price": None, "status": "declined"}

    elapsed = 0
    while elapsed < timeout_seconds:
        try:
            order = client.get_order(order_id)
            status_str = order.get("status", "")

            if status_str == "MATCHED":
                fill_price = order.get("avg_price") or order.get("price")
                return {
                    "order_id": order_id,
                    "fill_price": float(fill_price) if fill_price is not None else None,
                    "status": "filled",
                }
            elif status_str == "CANCELED":
                return {"order_id": order_id, "fill_price": None, "status": "cancelled"}
            # LIVE — still resting, keep polling

        except Exception as e:
            print(f"[pm-risk] Polymarket poll error for {order_id}: {e}", file=sys.stderr)

        time.sleep(interval_seconds)
        elapsed += interval_seconds

    # Timed out — attempt cancel
    try:
        client.cancel(order_id)
        print(f"[pm-risk] Cancelled timed-out Polymarket order {order_id}", file=sys.stderr)
    except Exception as e:
        print(f"[pm-risk] Failed to cancel Polymarket order {order_id}: {e}", file=sys.stderr)

    return {"order_id": order_id, "fill_price": None, "status": "timed_out"}


# ---------------------------------------------------------------------------
# Kalshi polling
# ---------------------------------------------------------------------------

def _poll_kalshi(
    order_id: str,
    market_id: str,
    timeout_seconds: int,
    interval_seconds: int,
    use_demo: bool = True,
) -> dict[str, Any]:
    """Poll Kalshi REST until order fills, cancels, or times out."""
    import httpx  # noqa: PLC0415
    from kalshi_client import KALSHI_BASE_URL, KALSHI_DEMO_URL, _get_headers  # noqa: PLC0415

    base_url = KALSHI_DEMO_URL if use_demo else KALSHI_BASE_URL
    order_path = f"/trade-api/v2/portfolio/orders/{order_id}"

    elapsed = 0
    while elapsed < timeout_seconds:
        try:
            headers = _get_headers("GET", order_path)
            if headers is None:
                return {"order_id": order_id, "fill_price": None, "status": "declined"}

            with httpx.Client(timeout=15.0) as client:
                resp = client.get(f"{base_url}/portfolio/orders/{order_id}", headers=headers)

            if resp.status_code >= 400:
                print(
                    f"[pm-risk] Kalshi poll HTTP {resp.status_code} for {order_id}",
                    file=sys.stderr,
                )
            else:
                order = resp.json().get("order", {})
                status_str = order.get("status", "")

                if status_str == "executed":
                    raw_fill = order.get("avg_price") or order.get("avg_fill_price", 0)
                    fill_price = float(raw_fill) / 100.0
                    return {"order_id": order_id, "fill_price": fill_price, "status": "filled"}
                elif status_str == "canceled":
                    return {"order_id": order_id, "fill_price": None, "status": "cancelled"}
                # resting — keep polling

        except Exception as e:
            print(f"[pm-risk] Kalshi poll error for {order_id}: {e}", file=sys.stderr)

        time.sleep(interval_seconds)
        elapsed += interval_seconds

    # Timed out — attempt cancel via DELETE
    try:
        cancel_path = f"/trade-api/v2/portfolio/orders/{order_id}"
        headers = _get_headers("DELETE", cancel_path)
        if headers:
            with httpx.Client(timeout=15.0) as client:
                client.delete(f"{base_url}/portfolio/orders/{order_id}", headers=headers)
        print(f"[pm-risk] Cancelled timed-out Kalshi order {order_id}", file=sys.stderr)
    except Exception as e:
        print(f"[pm-risk] Failed to cancel Kalshi order {order_id}: {e}", file=sys.stderr)

    return {"order_id": order_id, "fill_price": None, "status": "timed_out"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def poll_until_filled(
    order_id: str,
    platform: str,
    market_id: str,
    timeout_seconds: int = 300,
    interval_seconds: int = 30,
    use_demo: bool = True,
) -> dict[str, Any]:
    """
    Poll an open/resting order until it fills, times out, or is cancelled.

    Args:
        order_id:         Platform order ID
        platform:         "polymarket" or "kalshi"
        market_id:        Market identifier (for logging)
        timeout_seconds:  Give up after this many seconds (default 300)
        interval_seconds: Sleep between polls (default 30)
        use_demo:         Kalshi only — use demo env

    Returns:
        {"order_id": str, "fill_price": float | None, "status": str}
        status: "filled" | "timed_out" | "cancelled" | "declined"
    """
    print(
        f"[pm-risk] Polling {platform} order {order_id} "
        f"(timeout={timeout_seconds}s, interval={interval_seconds}s)",
        file=sys.stderr,
    )

    if platform == "polymarket":
        return _poll_polymarket(order_id, market_id, timeout_seconds, interval_seconds)
    elif platform == "kalshi":
        return _poll_kalshi(order_id, market_id, timeout_seconds, interval_seconds, use_demo)
    else:
        print(f"[pm-risk] Unknown platform for polling: {platform}", file=sys.stderr)
        return {"order_id": order_id, "fill_price": None, "status": "declined"}
