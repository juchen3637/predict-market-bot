"""
consolidate.py — Nightly Consolidation for pm-compound skill

Sequence:
  1. resolver.py      → resolve any open trades settled today
  2. postmortem.py    → classify failures for losses without prior postmortem
  3. metrics.py       → compute performance snapshot
  4. Threshold checks → create STOP file if drawdown exceeded
  5. docs/daily_summaries/YYYY-MM-DD.md → human-readable daily summary

Exits with code 0 on success, 1 on any step failure (so systemd can alert).

Usage:
    python skills/pm-compound/scripts/consolidate.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_COMPOUND_SCRIPTS = Path(__file__).resolve().parent
_RISK_SCRIPTS = _PROJECT_ROOT / "skills" / "pm-risk" / "scripts"

for _p in (str(_COMPOUND_SCRIPTS), str(_RISK_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config_loader import DATA_DIR, STOP_FILE_PATH  # noqa: E402
from postmortem import classify_failure, format_failure_entry, append_failure_log, update_patterns  # noqa: E402
from resolver import run as run_resolver  # noqa: E402
from metrics import compute_metrics  # noqa: E402
from take_profit import run_take_profit_checks  # noqa: E402
from retrain_xgboost import run_retrain  # noqa: E402


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TRADE_LOG_PATH = DATA_DIR / "trade_log.jsonl"
POSTMORTEM_PROCESSED_PATH = DATA_DIR / "postmortem_processed.json"
DAILY_SUMMARIES_DIR = _PROJECT_ROOT / "docs" / "daily_summaries"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_closed_losses() -> list[dict]:
    """Return all resolved loss trades from trade_log.jsonl."""
    if not TRADE_LOG_PATH.exists():
        return []
    losses = []
    with open(TRADE_LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                if trade.get("outcome") == "loss":
                    losses.append(trade)
            except json.JSONDecodeError:
                continue
    return losses


def _load_processed_ids() -> set[str]:
    """Return trade IDs already processed by postmortem."""
    if not POSTMORTEM_PROCESSED_PATH.exists():
        return set()
    try:
        with open(POSTMORTEM_PROCESSED_PATH) as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def _mark_as_processed(trade_id: str) -> None:
    """Append trade_id to the postmortem processed set."""
    processed = _load_processed_ids()
    processed.add(trade_id)
    DATA_DIR.mkdir(exist_ok=True)
    with open(POSTMORTEM_PROCESSED_PATH, "w") as f:
        json.dump(sorted(processed), f)


def run_postmortem_for_losses() -> int:
    """Classify and log all losses that haven't been through postmortem yet."""
    losses = _load_closed_losses()
    processed = _load_processed_ids()
    count = 0

    for trade in losses:
        trade_id = trade.get("trade_id", "")
        if not trade_id or trade_id in processed:
            continue
        category = classify_failure(trade)
        entry = format_failure_entry(trade, category)
        append_failure_log(entry)
        update_patterns(trade, category)
        _mark_as_processed(trade_id)
        count += 1

    if count > 0:
        print(f"[consolidate] Postmortem: classified {count} new loss(es)", file=sys.stderr)
    else:
        print("[consolidate] Postmortem: no new losses to classify", file=sys.stderr)
    return count


def write_daily_summary(metrics: dict, new_lessons: int) -> Path:
    """Write human-readable daily summary to docs/daily_summaries/YYYY-MM-DD.md."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    DAILY_SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = DAILY_SUMMARIES_DIR / f"{today}.md"

    stop_active = STOP_FILE_PATH.exists()

    def _fmt(val: float | None, fmt: str = ".1%") -> str:
        return format(val, fmt) if val is not None else "N/A"

    # Support both new nested schema { paper: {...}, live: {...} } and legacy flat schema
    live = metrics.get("live", metrics)
    win_rate_str = _fmt(live.get("win_rate"))
    sharpe_str = _fmt(live.get("sharpe"), ".2f")
    drawdown_str = _fmt(live.get("max_drawdown"))
    profit_factor_str = _fmt(live.get("profit_factor"), ".2f")
    brier_str = _fmt(metrics.get("brier_score"), ".3f")
    trades_resolved = live.get("trade_count", 0)

    content = (
        f"# Daily Summary — {today}\n"
        f"Trades resolved: {trades_resolved}"
        f"  |  Win rate (30d): {win_rate_str}"
        f"  |  Sharpe: {sharpe_str}\n"
        f"Drawdown: {drawdown_str}"
        f"  |  Profit factor: {profit_factor_str}"
        f"  |  Brier score: {brier_str}\n"
        f"New lessons: {new_lessons}  |  STOP file: {'yes' if stop_active else 'no'}\n"
    )

    with open(summary_path, "w") as f:
        f.write(content)

    print(f"[consolidate] Wrote daily summary to {summary_path}", file=sys.stderr)
    return summary_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    """Run full nightly consolidation. Exits 1 on any step failure."""
    print(
        f"[consolidate] Starting nightly consolidation at "
        f"{datetime.now(timezone.utc).isoformat()}",
        file=sys.stderr,
    )

    # Step 1: Resolve open trades
    try:
        run_resolver()
    except Exception as e:
        print(f"[consolidate] FAIL: resolver step failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 2: Postmortem for new losses
    try:
        new_lessons = run_postmortem_for_losses()
    except Exception as e:
        print(f"[consolidate] FAIL: postmortem step failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 3: Compute metrics (also triggers STOP file if drawdown exceeded)
    try:
        metrics = compute_metrics()
    except Exception as e:
        print(f"[consolidate] FAIL: metrics step failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 4: Write daily summary
    try:
        write_daily_summary(metrics, new_lessons)
    except Exception as e:
        print(f"[consolidate] FAIL: daily summary step failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 5: Take-profit checks on open positions
    try:
        import yaml  # noqa: PLC0415
        settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
        with open(settings_path) as _sf:
            _settings = yaml.safe_load(_sf)
        exited = run_take_profit_checks(_settings)
        if exited:
            print(f"[consolidate] Take-profit: exited {len(exited)} position(s)", file=sys.stderr)
    except Exception as e:
        print(f"[consolidate] WARN: take-profit step failed: {e}", file=sys.stderr)
        # Non-fatal — continue consolidation

    # Step 6: XGBoost auto-retrain (if enough new resolved trades)
    try:
        run_retrain()
    except Exception as e:
        print(f"[consolidate] WARN: XGBoost retrain step failed: {e}", file=sys.stderr)
        # Non-fatal — continue consolidation

    stop_active = STOP_FILE_PATH.exists()
    print(
        f"[consolidate] Done. stop_file={'active' if stop_active else 'inactive'}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    run()
