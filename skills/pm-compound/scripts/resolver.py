"""
resolver.py — Nightly Trade Resolution Script for pm-compound skill

Checks open trades against platform APIs, updates trade_log.jsonl with
win/loss + P&L, and triggers Brier score recomputation.

Usage:
    python resolver.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Project root: skills/pm-compound/scripts → project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _PROJECT_ROOT / "skills" / "pm-risk" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

_PREDICT_SCRIPTS_DIR = _PROJECT_ROOT / "skills" / "pm-predict" / "scripts"
sys.path.insert(0, str(_PREDICT_SCRIPTS_DIR))

import os
from datetime import datetime, timezone

from config_loader import TRADE_LOG_PATH  # type: ignore[import]
from brier_score import compute_rolling_brier  # type: ignore[import]
from log_trade import compute_pnl, update_resolved_trade
from platform_client import get_market_resolution
import kalshi_client  # type: ignore[import]


# ---------------------------------------------------------------------------
# Outcome Determination
# ---------------------------------------------------------------------------

def determine_trade_result(direction: str, market_outcome: str) -> str:
    """
    Map direction + market_outcome to win/loss.

    direction: "yes" | "no"
    market_outcome: "yes" | "no"
    """
    if direction == "yes" and market_outcome == "yes":
        return "win"
    if direction == "yes" and market_outcome == "no":
        return "loss"
    if direction == "no" and market_outcome == "no":
        return "win"
    if direction == "no" and market_outcome == "yes":
        return "loss"
    raise ValueError(f"Unknown direction/outcome pair: {direction}/{market_outcome}")


# ---------------------------------------------------------------------------
# Load Unresolved Trades
# ---------------------------------------------------------------------------

def load_unresolved_trades() -> list[dict]:
    if not TRADE_LOG_PATH.exists():
        return []

    trades = []
    with open(TRADE_LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                if trade.get("outcome") is None and trade.get("status") in ("placed", "paper"):
                    trades.append(trade)
            except json.JSONDecodeError:
                continue
    return trades


# ---------------------------------------------------------------------------
# Main Resolution Loop
# ---------------------------------------------------------------------------

def run() -> None:
    trades = load_unresolved_trades()

    if not trades:
        print("[resolver] 0 unresolved trades found")
        return

    print(f"[resolver] Checking {len(trades)} unresolved trade(s)...")

    resolved_count = 0

    use_demo = os.environ.get("KALSHI_USE_DEMO", "true").lower() == "true"

    for trade in trades:
        trade_id = trade.get("trade_id", "")
        market_id = trade.get("market_id", "")
        platform = trade.get("platform", "")
        direction = trade.get("direction", "yes")
        size_contracts = int(trade.get("size_contracts", 0))
        entry_price = float(trade.get("entry_price", 0.5))
        fill_price = float(trade.get("fill_price") or entry_price)
        order_id = trade.get("order_id")

        if not market_id or not platform:
            continue

        # For Kalshi live orders, check whether the order actually filled before
        # querying market resolution. Unfilled (canceled) orders are "expired" — no
        # capital was deployed, so P&L is zero.
        if platform == "kalshi" and order_id and trade.get("status") == "placed":
            order_info = kalshi_client.get_order(order_id, use_demo=use_demo)
            order_status = order_info.get("status")
            if order_status == "resting":
                # Still live in the book — check again tomorrow
                continue
            elif order_status == "canceled":
                now = datetime.now(timezone.utc).isoformat()
                success = update_resolved_trade(trade_id, "expired", 0.0, now)
                if success:
                    resolved_count += 1
                    print(f"[resolver] Expired (unfilled) {trade_id}: order canceled, P&L=+0.0000")
                else:
                    print(f"[resolver] WARNING: trade_id {trade_id} not found in log", file=sys.stderr)
                continue
            elif order_status == "filled":
                # Order did fill — update fill_price then fall through to market resolution
                if order_info.get("fill_price") is not None:
                    fill_price = order_info["fill_price"]
            # "unknown" → fall through to market resolution as a best-effort

        resolution = get_market_resolution(market_id, platform, use_demo=use_demo)
        if resolution is None:
            print(
                f"[resolver] WARNING: could not fetch resolution for {trade_id} ({market_id})",
                file=sys.stderr,
            )
            continue

        if not resolution.get("resolved"):
            continue

        market_outcome = resolution.get("outcome")
        if market_outcome is None:
            # Resolved but indeterminate — skip
            continue

        try:
            trade_result = determine_trade_result(direction, market_outcome)
        except ValueError as e:
            print(f"[resolver] WARNING: {e}", file=sys.stderr)
            continue

        pnl = compute_pnl(
            direction=direction,
            size_contracts=size_contracts,
            entry_price=entry_price,
            fill_price=fill_price,
            outcome=trade_result,
        )

        resolved_at = resolution.get("resolved_at")
        success = update_resolved_trade(trade_id, trade_result, pnl, resolved_at)

        if success:
            resolved_count += 1
            print(
                f"[resolver] Resolved {trade_id}: {trade_result.upper()}, P&L={pnl:+.4f}"
            )
        else:
            print(
                f"[resolver] WARNING: trade_id {trade_id} not found in log",
                file=sys.stderr,
            )

    if resolved_count > 0:
        brier_result = compute_rolling_brier()
        brier_str = (
            f"{brier_result['brier_score']:.4f}"
            if brier_result.get("brier_score") is not None
            else brier_result.get("message", "n/a")
        )
        print(f"[resolver] Resolved {resolved_count} trade(s). Brier score: {brier_str}")
    else:
        print(f"[resolver] 0 new resolutions (checked {len(trades)} trade(s))")


if __name__ == "__main__":
    run()
