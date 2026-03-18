"""
brier_score.py — Calibration Tracking for pm-predict skill

Computes rolling Brier Score across resolved prediction markets.
Writes results to data/brier_history.csv.

Brier Score: BS = (1/n) * Σ(p_model - outcome)²
Range: 0 (perfect) to 1 (worst). Target: < 0.25.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


BRIER_HISTORY_PATH = Path(__file__).parent.parent.parent.parent / "data" / "brier_history.csv"
TRADE_LOG_PATH = Path(__file__).parent.parent.parent.parent / "data" / "trade_log.jsonl"
ALERT_THRESHOLD = 0.30
ROLLING_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# Core Calculation
# ---------------------------------------------------------------------------

def brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """
    Compute Brier Score.

    predictions: list of p_model values (0.0–1.0)
    outcomes: list of 1 (Yes resolved) or 0 (No resolved)
    """
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have same length")
    if not predictions:
        return 0.0
    n = len(predictions)
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / n


# ---------------------------------------------------------------------------
# Load Resolved Trades from Trade Log
# ---------------------------------------------------------------------------

def load_resolved_trades(window_days: int = ROLLING_WINDOW_DAYS) -> list[dict]:
    """Load resolved trades within the rolling window from trade_log.jsonl."""
    if not TRADE_LOG_PATH.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    trades = []

    with open(TRADE_LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                if trade.get("outcome") is None:
                    continue  # Not yet resolved
                resolved_at = trade.get("resolved_at", "")
                if resolved_at:
                    resolved_dt = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
                    if resolved_dt >= cutoff:
                        trades.append(trade)
            except (json.JSONDecodeError, ValueError):
                continue

    return trades


# ---------------------------------------------------------------------------
# Compute and Record
# ---------------------------------------------------------------------------

def compute_rolling_brier() -> dict:
    """Compute rolling Brier Score and write to brier_history.csv."""
    trades = load_resolved_trades(ROLLING_WINDOW_DAYS)

    if len(trades) < 10:
        return {
            "brier_score": None,
            "trade_count": len(trades),
            "message": f"Need at least 10 resolved trades (have {len(trades)})",
        }

    predictions = [float(t["p_model"]) for t in trades]
    outcomes = [1 if t["outcome"] == "win" else 0 for t in trades]
    bs = brier_score(predictions, outcomes)

    # Append to history CSV
    BRIER_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not BRIER_HISTORY_PATH.exists()
    with open(BRIER_HISTORY_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp", "brier_score", "trade_count", "window_days"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            round(bs, 6),
            len(trades),
            ROLLING_WINDOW_DAYS,
        ])

    alert = bs > ALERT_THRESHOLD
    if alert:
        print(
            f"[pm-predict] ALERT: Brier Score {bs:.4f} exceeds threshold {ALERT_THRESHOLD}. "
            "Review model weights or retrain XGBoost.",
            file=sys.stderr,
        )

    return {
        "brier_score": round(bs, 6),
        "trade_count": len(trades),
        "window_days": ROLLING_WINDOW_DAYS,
        "alert": alert,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    result = compute_rolling_brier()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
