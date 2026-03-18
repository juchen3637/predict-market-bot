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

from config_loader import TRADE_LOG_PATH  # type: ignore[import]
from brier_score import compute_rolling_brier  # type: ignore[import]
from log_trade import compute_pnl, update_resolved_trade
from platform_client import get_market_resolution


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
                if trade.get("outcome") is None:
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

    for trade in trades:
        trade_id = trade.get("trade_id", "")
        market_id = trade.get("market_id", "")
        platform = trade.get("platform", "")
        direction = trade.get("direction", "yes")
        size_contracts = int(trade.get("size_contracts", 0))
        entry_price = float(trade.get("entry_price", 0.5))
        fill_price = float(trade.get("fill_price") or entry_price)

        if not market_id or not platform:
            continue

        resolution = get_market_resolution(market_id, platform)
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
