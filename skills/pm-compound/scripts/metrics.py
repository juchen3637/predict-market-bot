"""
metrics.py — Performance Metrics for pm-compound skill

Reads resolved trades from data/trade_log.jsonl and computes:
  - Win rate
  - Sharpe ratio (annualized, from daily returns)
  - Max drawdown (rolling peak-to-trough on cumulative P&L)
  - Profit factor (gross_profit / gross_loss)
  - Brier score (delegates to brier_score.py)

Writes:
  - data/performance_metrics.json  (latest snapshot, overwritten)
  - data/metrics_history.jsonl     (append-only)

Alert thresholds:
  - Win rate < 0.60  → [metrics] WARN
  - Sharpe < 2.0     → [metrics] WARN
  - Max drawdown > 0.08 → [metrics] CRITICAL + creates STOP file
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_RISK_SCRIPTS = _PROJECT_ROOT / "skills" / "pm-risk" / "scripts"
_PREDICT_SCRIPTS = _PROJECT_ROOT / "skills" / "pm-predict" / "scripts"
sys.path.insert(0, str(_RISK_SCRIPTS))
sys.path.insert(0, str(_PREDICT_SCRIPTS))

from config_loader import DATA_DIR, STOP_FILE_PATH  # noqa: E402
from brier_score import compute_rolling_brier  # noqa: E402


# ---------------------------------------------------------------------------
# Paths & Thresholds
# ---------------------------------------------------------------------------

METRICS_PATH = DATA_DIR / "performance_metrics.json"
METRICS_HISTORY_PATH = DATA_DIR / "metrics_history.jsonl"

WIN_RATE_TARGET = 0.60
SHARPE_TARGET = 2.0
MAX_DRAWDOWN_LIMIT = 0.08


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def load_closed_trades(trade_log_path: Path | None = None) -> list[dict]:
    """Load resolved trades (outcome not None, pnl not None) from trade_log.jsonl."""
    path = trade_log_path if trade_log_path is not None else DATA_DIR / "trade_log.jsonl"
    if not path.exists():
        return []

    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                if trade.get("outcome") is not None and trade.get("pnl") is not None:
                    trades.append(trade)
            except json.JSONDecodeError:
                continue
    return trades


# ---------------------------------------------------------------------------
# Metric Calculations
# ---------------------------------------------------------------------------

def compute_win_rate(trades: list[dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get("outcome") == "win")
    return wins / len(trades)


def compute_daily_returns(trades: list[dict], bankroll: float) -> list[float]:
    """Group resolved trades by date, return daily P&L as fraction of bankroll."""
    if bankroll <= 0:
        return []

    daily_pnl: dict[str, float] = defaultdict(float)
    for trade in trades:
        resolved_at = trade.get("resolved_at", "")
        if not resolved_at:
            continue
        try:
            date_str = (
                datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
                .date()
                .isoformat()
            )
            daily_pnl[date_str] += float(trade.get("pnl", 0))
        except (ValueError, AttributeError):
            continue

    return [pnl / bankroll for pnl in daily_pnl.values()]


def compute_sharpe(daily_returns: list[float]) -> float | None:
    """Annualized Sharpe ratio from daily returns. Returns None if < 2 data points."""
    if len(daily_returns) < 2:
        return None
    n = len(daily_returns)
    mean = sum(daily_returns) / n
    variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0:
        return None
    return mean / std * math.sqrt(365)


def compute_max_drawdown(trades: list[dict]) -> float:
    """Rolling peak-to-trough drawdown on cumulative P&L series."""
    resolved = [t for t in trades if t.get("resolved_at")]
    if not resolved:
        return 0.0

    sorted_trades = sorted(resolved, key=lambda t: t["resolved_at"])

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for trade in sorted_trades:
        cumulative += float(trade.get("pnl", 0))
        if cumulative > peak:
            peak = cumulative
        if peak > 0:
            drawdown = (peak - cumulative) / peak
            max_dd = max(max_dd, drawdown)

    return max_dd


def compute_profit_factor(trades: list[dict]) -> float | None:
    """gross_profit / gross_loss. Returns None when no losses (avoid inf in JSON)."""
    gross_profit = sum(float(t["pnl"]) for t in trades if float(t.get("pnl", 0)) > 0)
    gross_loss = sum(abs(float(t["pnl"])) for t in trades if float(t.get("pnl", 0)) < 0)
    if gross_loss == 0:
        return None  # No losses — avoid non-JSON-serialisable inf
    return gross_profit / gross_loss


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def compute_metrics(
    trade_log_path: Path | None = None,
    stop_file_path: Path | None = None,
) -> dict:
    """
    Compute all performance metrics, write snapshot and history files.

    Args:
        trade_log_path: override for testing
        stop_file_path:  override for testing
    """
    trades = load_closed_trades(trade_log_path)
    bankroll = float(os.environ.get("BANKROLL_USD", 100))
    now = datetime.now(timezone.utc).isoformat()
    _stop_file = stop_file_path if stop_file_path is not None else STOP_FILE_PATH

    if not trades:
        result: dict = {
            "computed_at": now,
            "trade_count": 0,
            "win_rate": None,
            "sharpe": None,
            "max_drawdown": None,
            "profit_factor": None,
            "brier_score": None,
            "message": "No resolved trades",
        }
        DATA_DIR.mkdir(exist_ok=True)
        with open(METRICS_PATH, "w") as f:
            json.dump(result, f, indent=2)
        return result

    win_rate = compute_win_rate(trades)
    daily_returns = compute_daily_returns(trades, bankroll)
    sharpe = compute_sharpe(daily_returns)
    max_drawdown = compute_max_drawdown(trades)
    profit_factor = compute_profit_factor(trades)

    brier_result = compute_rolling_brier()
    brier = brier_result.get("brier_score")

    metrics: dict = {
        "computed_at": now,
        "trade_count": len(trades),
        "win_rate": round(win_rate, 4),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(max_drawdown, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "brier_score": round(brier, 6) if brier is not None else None,
        "bankroll_usd": bankroll,
    }

    # Alert threshold checks
    if win_rate < WIN_RATE_TARGET:
        print(
            f"[metrics] WARN: win_rate {win_rate:.1%} below target {WIN_RATE_TARGET:.0%}",
            file=sys.stderr,
        )

    if sharpe is not None and sharpe < SHARPE_TARGET:
        print(
            f"[metrics] WARN: sharpe {sharpe:.2f} below target {SHARPE_TARGET:.1f}",
            file=sys.stderr,
        )

    if max_drawdown > MAX_DRAWDOWN_LIMIT:
        print(
            f"[metrics] CRITICAL: drawdown {max_drawdown:.1%} exceeded {MAX_DRAWDOWN_LIMIT:.0%}",
            file=sys.stderr,
        )
        _stop_file.touch()
        print(f"[metrics] STOP file created at {_stop_file}", file=sys.stderr)

        # Auto-create incident report for drawdown-triggered halt
        # Skip if stop_file_path is overridden (indicates test mode)
        if stop_file_path is None:
            try:
                _create_script = _PROJECT_ROOT / "scripts" / "create_incident.py"
                if _create_script.exists():
                    sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
                    from create_incident import create_incident  # noqa: PLC0415
                    create_incident(
                        title=f"Drawdown halt — {max_drawdown:.1%} exceeded {MAX_DRAWDOWN_LIMIT:.0%} limit",
                        severity="critical",
                        trigger="drawdown",
                        drawdown=max_drawdown,
                    )
            except Exception as _inc_err:
                print(f"[metrics] WARN: failed to create incident report: {_inc_err}", file=sys.stderr)

    # Persist snapshot and append to history
    DATA_DIR.mkdir(exist_ok=True)
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)

    history_row = {
        "timestamp": now,
        "win_rate": metrics["win_rate"],
        "sharpe": metrics["sharpe"],
        "max_drawdown": metrics["max_drawdown"],
        "profit_factor": metrics["profit_factor"],
        "brier_score": metrics["brier_score"],
        "trade_count": metrics["trade_count"],
    }
    with open(METRICS_HISTORY_PATH, "a") as f:
        f.write(json.dumps(history_row) + "\n")

    sharpe_str = f"{sharpe:.2f}" if sharpe is not None else "N/A"
    print(
        f"[metrics] Computed metrics for {len(trades)} closed trades. "
        f"Win rate: {win_rate:.1%}, Sharpe: {sharpe_str}",
        file=sys.stderr,
    )
    return metrics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    result = compute_metrics()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
