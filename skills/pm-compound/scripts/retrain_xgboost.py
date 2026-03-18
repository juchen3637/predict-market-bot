"""
retrain_xgboost.py — XGBoost Auto-Retrain for pm-compound skill

Checks whether enough new resolved trades have accumulated since the last
training run, and retrains the XGBoost model if so.

Wired into consolidate.py nightly run.

Usage:
    python skills/pm-compound/scripts/retrain_xgboost.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_PREDICT_SCRIPTS = _PROJECT_ROOT / "skills" / "pm-predict" / "scripts"

for _p in (str(_SCRIPT_DIR), str(_PREDICT_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config_loader import DATA_DIR  # noqa: E402

TRAIN_STATE_PATH = DATA_DIR / "xgboost_train_state.json"
TRAINING_DATA_PATH = DATA_DIR / "training_data.jsonl"
MIN_NEW_TRADES_DEFAULT = 30


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

def _load_train_state() -> dict:
    if not TRAIN_STATE_PATH.exists():
        return {"last_train_trade_count": 0, "last_trained_at": None}
    try:
        with open(TRAIN_STATE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"last_train_trade_count": 0, "last_trained_at": None}


def _save_train_state(trade_count: int) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    state = {
        "last_train_trade_count": trade_count,
        "last_trained_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(TRAIN_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _count_resolved_trades() -> int:
    """Count resolved training records in training_data.jsonl."""
    if not TRAINING_DATA_PATH.exists():
        return 0
    count = 0
    with open(TRAINING_DATA_PATH) as f:
        for line in f:
            if line.strip():
                count += 1
    return count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def should_retrain(min_new_trades: int = MIN_NEW_TRADES_DEFAULT) -> bool:
    """
    Returns True if resolved_trades_since_last_train >= min_new_trades.

    Reads last-train trade count from data/xgboost_train_state.json.
    """
    state = _load_train_state()
    last_count = state.get("last_train_trade_count", 0)
    current_count = _count_resolved_trades()
    new_trades = current_count - last_count
    print(
        f"[compound] XGBoost retrain check: {new_trades} new trade(s) "
        f"(current={current_count}, last_train={last_count}, min={min_new_trades})",
        file=sys.stderr,
    )
    return new_trades >= min_new_trades


def run_retrain(min_new_trades: int = MIN_NEW_TRADES_DEFAULT) -> bool:
    """
    Retrain XGBoost if enough new resolved trades are available.

    Steps:
      1. historical_fetcher.main() — fetch latest resolved markets
      2. xgboost_features.train() — retrain model
      3. Update xgboost_train_state.json

    Returns:
        True if retrained, False if skipped.
    """
    if not should_retrain(min_new_trades):
        current_count = _count_resolved_trades()
        state = _load_train_state()
        new_trades = current_count - state.get("last_train_trade_count", 0)
        print(
            f"[compound] XGBoost retrain skipped ({new_trades} < {min_new_trades} new trades)",
            file=sys.stderr,
        )
        return False

    # Step 1: Fetch latest resolved markets
    try:
        from historical_fetcher import main as fetch_historical  # noqa: PLC0415
        fetch_historical()
        print("[compound] Historical data fetched for XGBoost retrain", file=sys.stderr)
    except SystemExit:
        # argparse may raise SystemExit in some environments; non-fatal
        print("[compound] historical_fetcher exited (argparse issue) — using existing data", file=sys.stderr)
    except Exception as e:
        print(f"[compound] historical_fetcher error during retrain: {e}", file=sys.stderr)
        # Continue — existing training_data.jsonl may still be usable

    # Step 2: Retrain
    current_count = _count_resolved_trades()
    if current_count == 0:
        print("[compound] XGBoost retrain skipped — no training data", file=sys.stderr)
        return False

    try:
        from xgboost_features import train as xgb_train  # noqa: PLC0415
        xgb_train(str(TRAINING_DATA_PATH))
        _save_train_state(current_count)
        print(
            f"[compound] XGBoost retrained on {current_count} records",
            file=sys.stderr,
        )
        return True
    except Exception as e:
        print(f"[compound] XGBoost retrain failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main (standalone)
# ---------------------------------------------------------------------------

def main() -> None:
    retrained = run_retrain()
    print(json.dumps({"retrained": retrained}))


if __name__ == "__main__":
    main()
