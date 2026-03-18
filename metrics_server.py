"""
metrics_server.py — Prometheus Metrics Server for predict-market-bot

Exposes trading bot metrics at :8001/metrics for Prometheus scraping.
Reads data/performance_metrics.json (refreshed nightly) and
data/trade_log.jsonl (real-time counts).

Port 8001 — port 8000 is reserved for Serena MCP on morningside-vps.

Usage:
    python metrics_server.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from prometheus_client import Counter, Gauge, start_http_server

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = _PROJECT_ROOT / "data"
METRICS_PATH = DATA_DIR / "performance_metrics.json"
TRADE_LOG_PATH = DATA_DIR / "trade_log.jsonl"
COST_LOG_PATH = DATA_DIR / "ai_cost_log.jsonl"

METRICS_PORT = 8001
REFRESH_INTERVAL_SECONDS = 60


# ---------------------------------------------------------------------------
# Prometheus Gauges
# ---------------------------------------------------------------------------

_win_rate = Gauge("trading_bot_win_rate", "Rolling win rate (fraction)")
_sharpe = Gauge("trading_bot_sharpe_ratio", "Annualised Sharpe ratio")
_max_drawdown = Gauge("trading_bot_max_drawdown", "Max drawdown (fraction)")
_profit_factor = Gauge("trading_bot_profit_factor", "Gross profit / gross loss")
_brier_score = Gauge("trading_bot_brier_score", "Rolling Brier score")
_open_positions = Gauge("trading_bot_open_positions", "Current open position count")
_daily_pnl = Gauge("trading_bot_daily_pnl_usd", "Today's realised P&L in USD")
_total_trades = Counter("trading_bot_total_trades_total", "Cumulative resolved trade count")
_ai_cost = Gauge("trading_bot_ai_cost_daily_usd", "Today's AI API spend in USD")

# Track last seen trade count to correctly increment the counter
_last_trade_count = 0


# ---------------------------------------------------------------------------
# Data Readers
# ---------------------------------------------------------------------------

def _read_metrics_snapshot() -> dict:
    """Load latest performance_metrics.json, return empty dict on error."""
    if not METRICS_PATH.exists():
        return {}
    try:
        with open(METRICS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _read_open_positions_and_daily_pnl() -> tuple[int, float]:
    """
    Count open positions and sum today's resolved P&L from trade_log.jsonl.
    Returns (open_count, daily_pnl_usd).
    """
    if not TRADE_LOG_PATH.exists():
        return 0, 0.0

    today = datetime.now(timezone.utc).date().isoformat()
    open_count = 0
    daily_pnl = 0.0

    try:
        with open(TRADE_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if trade.get("outcome") is None and trade.get("status") in ("placed", "paper"):
                    open_count += 1

                resolved_at = trade.get("resolved_at", "")
                if resolved_at and resolved_at[:10] == today and trade.get("pnl") is not None:
                    daily_pnl += float(trade["pnl"])
    except OSError:
        pass

    return open_count, daily_pnl


def _read_daily_ai_cost() -> float:
    """Sum AI costs logged today from ai_cost_log.jsonl."""
    if not COST_LOG_PATH.exists():
        return 0.0

    today = datetime.now(timezone.utc).date().isoformat()
    total = 0.0

    try:
        with open(COST_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = record.get("timestamp", "")
                if ts[:10] == today:
                    total += float(record.get("cost_usd", 0))
    except OSError:
        pass

    return total


# ---------------------------------------------------------------------------
# Update Loop
# ---------------------------------------------------------------------------

def _update_gauges() -> None:
    """Read latest data and update all Prometheus gauges."""
    global _last_trade_count

    snapshot = _read_metrics_snapshot()
    open_count, daily_pnl_usd = _read_open_positions_and_daily_pnl()
    ai_cost_usd = _read_daily_ai_cost()

    if snapshot.get("win_rate") is not None:
        _win_rate.set(snapshot["win_rate"])
    if snapshot.get("sharpe") is not None:
        _sharpe.set(snapshot["sharpe"])
    if snapshot.get("max_drawdown") is not None:
        _max_drawdown.set(snapshot["max_drawdown"])
    if snapshot.get("profit_factor") is not None:
        _profit_factor.set(snapshot["profit_factor"])
    if snapshot.get("brier_score") is not None:
        _brier_score.set(snapshot["brier_score"])

    _open_positions.set(open_count)
    _daily_pnl.set(daily_pnl_usd)
    _ai_cost.set(ai_cost_usd)

    trade_count = int(snapshot.get("trade_count", 0))
    if trade_count > _last_trade_count:
        _total_trades.inc(trade_count - _last_trade_count)
        _last_trade_count = trade_count


def main() -> None:
    print(f"[metrics-server] Starting Prometheus metrics server on port {METRICS_PORT}", file=sys.stderr)
    start_http_server(METRICS_PORT)
    print(f"[metrics-server] Serving metrics at http://0.0.0.0:{METRICS_PORT}/metrics", file=sys.stderr)

    while True:
        try:
            _update_gauges()
        except Exception as e:
            print(f"[metrics-server] Error updating gauges: {e}", file=sys.stderr)
        time.sleep(REFRESH_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
