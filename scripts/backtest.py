"""
backtest.py — Historical Backtest Runner for predict-market-bot

Evaluates XGBoost model calibration using a time-ordered 80/20 holdout split
of the resolved markets in data/training_data.jsonl.

No LLM calls — XGBoost inference only uses structural features available at
market creation time (no look-ahead bias).

Usage:
    python scripts/backtest.py
    python scripts/backtest.py --training-data data/training_data.jsonl
    python scripts/backtest.py --trade-log data/trade_log.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PREDICT_SCRIPTS = _PROJECT_ROOT / "skills" / "pm-predict" / "scripts"
if str(_PREDICT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PREDICT_SCRIPTS))

import xgboost_features

TRAINING_DATA_PATH = _PROJECT_ROOT / "data" / "training_data.jsonl"
TRADE_LOG_PATH = _PROJECT_ROOT / "data" / "trade_log.jsonl"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_training_data(path: Path) -> list[dict]:
    """Load all records from training_data.jsonl; preserve file order (proxy for time)."""
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# ---------------------------------------------------------------------------
# Platform inference
# ---------------------------------------------------------------------------

def _infer_platform(market_id: str) -> str:
    """Infer platform from market_id format (hex 0x... = polymarket, else kalshi)."""
    if market_id.startswith("0x") and len(market_id) > 10:
        return "polymarket"
    return "kalshi"


# ---------------------------------------------------------------------------
# Brier score
# ---------------------------------------------------------------------------

def _brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """Compute Brier score: mean((p - o)²)."""
    n = len(predictions)
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / n


# ---------------------------------------------------------------------------
# Trade log injection
# ---------------------------------------------------------------------------

def load_existing_backtest_ids(trade_log_path: Path) -> set[str]:
    """Return trade_ids already present in trade_log.jsonl to support dedup."""
    if not trade_log_path.exists():
        return set()
    ids: set[str] = set()
    with open(trade_log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                if trade.get("status") == "backtest":
                    ids.add(trade.get("trade_id", ""))
            except json.JSONDecodeError:
                continue
    return ids


def _build_backtest_entry(record: dict, p_model: float, now_iso: str) -> dict:
    """Build a synthetic trade_log entry for a backtest record."""
    outcome_int = record["outcome"]
    outcome_str = "win" if outcome_int == 1 else "loss"
    pnl = 1.0 if outcome_int == 1 else -1.0
    market_id = record["market_id"]
    platform = record.get("platform") or _infer_platform(market_id)

    return {
        "trade_id": f"backtest-{market_id}",
        "market_id": market_id,
        "title": record.get("title", ""),
        "platform": platform,
        "status": "backtest",
        "direction": "yes",
        "size_usd": 1.0,
        "p_model": round(p_model, 6),
        "outcome": outcome_str,
        "pnl": pnl,
        "resolved_at": now_iso,
    }


def inject_backtest_entries(
    entries: list[dict],
    trade_log_path: Path,
    existing_ids: set[str],
) -> int:
    """Append new backtest entries (deduped) to trade_log.jsonl. Returns count written."""
    new_entries = [e for e in entries if e["trade_id"] not in existing_ids]
    if not new_entries:
        return 0

    trade_log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(trade_log_path, "a") as f:
        for entry in new_entries:
            f.write(json.dumps(entry) + "\n")

    return len(new_entries)


# ---------------------------------------------------------------------------
# Main backtest logic
# ---------------------------------------------------------------------------

def run_backtest(
    training_data_path: Path = TRAINING_DATA_PATH,
    trade_log_path: Path = TRADE_LOG_PATH,
) -> dict:
    """
    Run the backtest pipeline:
    1. Load resolved market records
    2. Time-ordered 80/20 train/test split
    3. Retrain XGBoost on train split
    4. Predict on test split
    5. Compute Brier score
    6. Inject test records into trade_log.jsonl as status=backtest

    Returns summary dict with train_count, test_count, brier_score, injected_count.
    """
    if not training_data_path.exists():
        raise FileNotFoundError(f"Training data not found: {training_data_path}")

    records = load_training_data(training_data_path)
    total = len(records)
    print(f"[backtest] Loaded {total} records from {training_data_path.name}")

    if total < 10:
        raise ValueError(f"Need at least 10 records for backtest (have {total})")

    # Time-ordered 80/20 split: first 80% = train, last 20% = test
    split_idx = int(total * 0.8)
    train_records = records[:split_idx]
    test_records = records[split_idx:]

    print(f"[backtest] Train: {len(train_records)}, Test: {len(test_records)}")

    # Write train split to a temp file and retrain
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as tmp:
        for r in train_records:
            tmp.write(json.dumps(r) + "\n")
        tmp_path = tmp.name

    try:
        xgboost_features.train(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    print("[backtest] XGBoost retrained on train split")

    # Run inference on test split
    predictions: list[float] = []
    outcomes: list[int] = []
    for record in test_records:
        p = xgboost_features.predict(record)
        predictions.append(p)
        outcomes.append(int(record["outcome"]))

    bs = _brier_score(predictions, outcomes)
    print(f"[backtest] Brier Score (test set): {bs:.4f}")

    # Inject into trade_log.jsonl
    now_iso = datetime.now(timezone.utc).isoformat()
    existing_ids = load_existing_backtest_ids(trade_log_path)

    entries = [
        _build_backtest_entry(record, p, now_iso)
        for record, p in zip(test_records, predictions)
    ]
    injected = inject_backtest_entries(entries, trade_log_path, existing_ids)
    print(f"[backtest] Injected {injected} backtest entries into {trade_log_path.name}")

    return {
        "train_count": len(train_records),
        "test_count": len(test_records),
        "brier_score": round(bs, 6),
        "injected_count": injected,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run XGBoost backtest on historical resolved markets."
    )
    parser.add_argument(
        "--training-data",
        default=str(TRAINING_DATA_PATH),
        help=f"Path to training_data.jsonl (default: {TRAINING_DATA_PATH})",
    )
    parser.add_argument(
        "--trade-log",
        default=str(TRADE_LOG_PATH),
        help=f"Path to trade_log.jsonl (default: {TRADE_LOG_PATH})",
    )
    args = parser.parse_args()

    result = run_backtest(
        training_data_path=Path(args.training_data),
        trade_log_path=Path(args.trade_log),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
