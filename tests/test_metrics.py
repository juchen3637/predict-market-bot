"""
test_metrics.py — Tests for skills/pm-compound/scripts/metrics.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from metrics import (
    compute_win_rate,
    compute_daily_returns,
    compute_sharpe,
    compute_max_drawdown,
    compute_profit_factor,
    compute_metrics,
    filter_trades_by_mode,
    WIN_RATE_TARGET,
    MAX_DRAWDOWN_LIMIT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_trade(
    *,
    outcome: str,
    pnl: float,
    resolved_at: str = "2026-03-17T23:00:00+00:00",
    p_model: float = 0.70,
    status: str = "paper",
) -> dict:
    return {
        "trade_id": f"t-{id(pnl)}",
        "market_id": "test",
        "platform": "kalshi",
        "direction": "yes",
        "size_contracts": 10,
        "size_usd": 6.0,
        "entry_price": 0.60,
        "fill_price": 0.60,
        "p_model": p_model,
        "edge": 0.10,
        "kelly_fraction": 0.25,
        "status": status,
        "rejection_reason": None,
        "placed_at": "2026-03-15T10:00:00+00:00",
        "resolved_at": resolved_at,
        "outcome": outcome,
        "pnl": pnl,
        "hedge_needed": False,
    }


@pytest.fixture()
def winning_trades():
    return [
        _make_trade(outcome="win", pnl=4.0, resolved_at="2026-03-16T23:00:00+00:00"),
        _make_trade(outcome="win", pnl=4.0, resolved_at="2026-03-16T23:00:00+00:00"),
        _make_trade(outcome="win", pnl=4.0, resolved_at="2026-03-17T23:00:00+00:00"),
    ]


@pytest.fixture()
def mixed_trades():
    return [
        _make_trade(outcome="win", pnl=4.0, resolved_at="2026-03-15T23:00:00+00:00"),
        _make_trade(outcome="loss", pnl=-6.0, resolved_at="2026-03-16T23:00:00+00:00"),
        _make_trade(outcome="win", pnl=4.0, resolved_at="2026-03-17T23:00:00+00:00"),
        _make_trade(outcome="loss", pnl=-6.0, resolved_at="2026-03-17T23:00:00+00:00"),
    ]


def _write_trade_log(path: Path, trades: list[dict]) -> None:
    with open(path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


# ---------------------------------------------------------------------------
# Unit: Win Rate
# ---------------------------------------------------------------------------

def test_win_rate_all_wins(winning_trades):
    assert compute_win_rate(winning_trades) == 1.0


def test_win_rate_mixed(mixed_trades):
    assert compute_win_rate(mixed_trades) == pytest.approx(0.5)


def test_win_rate_empty():
    assert compute_win_rate([]) == 0.0


# ---------------------------------------------------------------------------
# Unit: Daily Returns
# ---------------------------------------------------------------------------

def test_daily_returns_groups_by_date(mixed_trades):
    returns = compute_daily_returns(mixed_trades, bankroll=100.0)
    # 3 unique dates → 3 daily return values
    assert len(returns) == 3


def test_daily_returns_zero_bankroll():
    assert compute_daily_returns([_make_trade(outcome="win", pnl=4.0)], bankroll=0) == []


# ---------------------------------------------------------------------------
# Unit: Sharpe
# ---------------------------------------------------------------------------

def test_sharpe_requires_at_least_two_points():
    assert compute_sharpe([0.01]) is None
    assert compute_sharpe([]) is None


def test_sharpe_positive_for_positive_returns():
    returns = [0.01, 0.02, 0.015, 0.01, 0.02]
    sharpe = compute_sharpe(returns)
    assert sharpe is not None
    assert sharpe > 0


def test_sharpe_none_when_zero_std():
    # All same returns → std = 0 → None
    returns = [0.01, 0.01, 0.01, 0.01]
    assert compute_sharpe(returns) is None


# ---------------------------------------------------------------------------
# Unit: Max Drawdown
# ---------------------------------------------------------------------------

def test_max_drawdown_empty():
    assert compute_max_drawdown([]) == 0.0


def test_max_drawdown_only_wins():
    trades = [
        _make_trade(outcome="win", pnl=4.0, resolved_at="2026-03-15T10:00:00+00:00"),
        _make_trade(outcome="win", pnl=4.0, resolved_at="2026-03-16T10:00:00+00:00"),
    ]
    assert compute_max_drawdown(trades) == 0.0


def test_max_drawdown_computes_trough(monkeypatch):
    monkeypatch.setenv("BANKROLL_USD", "100")
    # bankroll=100, +20 → peak=120, -12 → portfolio=108, dd = 12/120 = 10%
    trades = [
        _make_trade(outcome="win", pnl=20.0, resolved_at="2026-03-15T10:00:00+00:00"),
        _make_trade(outcome="loss", pnl=-12.0, resolved_at="2026-03-16T10:00:00+00:00"),
    ]
    dd = compute_max_drawdown(trades)
    assert dd == pytest.approx(12.0 / 120.0, rel=0.01)


def test_max_drawdown_exceeds_limit_creates_stop_file(tmp_path, monkeypatch):
    """If live drawdown > 8%, compute_metrics should create the STOP file."""
    monkeypatch.setenv("BANKROLL_USD", "100")

    trade_log = tmp_path / "trade_log.jsonl"
    stop_file = tmp_path / "STOP"

    # bankroll=100, straight loss of -9 → portfolio=91, dd=9/100=9% > 8% limit
    trades = [
        _make_trade(outcome="loss", pnl=-9.0, resolved_at="2026-03-15T10:00:00+00:00", status="placed"),
    ]
    _write_trade_log(trade_log, trades)

    import metrics as _m
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_m, "METRICS_PATH", tmp_path / "performance_metrics.json")
        mp.setattr(_m, "METRICS_HISTORY_PATH", tmp_path / "metrics_history.jsonl")
        from unittest.mock import patch
        with patch("metrics.compute_rolling_brier", return_value={"brier_score": None, "trade_count": 0}):
            result = compute_metrics(trade_log_path=trade_log, stop_file_path=stop_file)

    assert result["live"]["max_drawdown"] > MAX_DRAWDOWN_LIMIT
    assert stop_file.exists(), "STOP file should be created when live drawdown > 8%"


# ---------------------------------------------------------------------------
# Unit: Profit Factor
# ---------------------------------------------------------------------------

def test_profit_factor_with_losses(mixed_trades):
    # gross_profit = 4+4 = 8, gross_loss = 6+6 = 12
    pf = compute_profit_factor(mixed_trades)
    assert pf == pytest.approx(8.0 / 12.0, rel=0.01)


def test_profit_factor_no_losses(winning_trades):
    pf = compute_profit_factor(winning_trades)
    assert pf is None  # no losses → None (avoids inf in JSON)


def test_profit_factor_all_losses():
    losses = [_make_trade(outcome="loss", pnl=-5.0) for _ in range(3)]
    pf = compute_profit_factor(losses)
    assert pf == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Integration: compute_metrics
# ---------------------------------------------------------------------------

def test_compute_metrics_no_trades(tmp_path, monkeypatch):
    monkeypatch.setenv("BANKROLL_USD", "100")
    empty_log = tmp_path / "trade_log.jsonl"
    empty_log.touch()

    import metrics as _m
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_m, "METRICS_PATH", tmp_path / "performance_metrics.json")
        mp.setattr(_m, "METRICS_HISTORY_PATH", tmp_path / "metrics_history.jsonl")
        from unittest.mock import patch
        with patch("metrics.compute_rolling_brier", return_value={"brier_score": None}):
            result = compute_metrics(trade_log_path=empty_log)

    assert result["paper"]["trade_count"] == 0
    assert result["live"]["trade_count"] == 0
    assert "message" in result


def test_compute_metrics_writes_snapshot(tmp_path, monkeypatch, winning_trades):
    """Winning trades are all paper (default status). Verify paper sub-dict is populated."""
    monkeypatch.setenv("BANKROLL_USD", "100")
    trade_log = tmp_path / "trade_log.jsonl"
    snapshot = tmp_path / "performance_metrics.json"
    history = tmp_path / "metrics_history.jsonl"
    _write_trade_log(trade_log, winning_trades)

    import metrics as _m
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_m, "METRICS_PATH", snapshot)
        mp.setattr(_m, "METRICS_HISTORY_PATH", history)
        from unittest.mock import patch
        with patch("metrics.compute_rolling_brier", return_value={"brier_score": 0.20}):
            result = compute_metrics(trade_log_path=trade_log)

    assert snapshot.exists()
    assert history.exists()
    written = json.loads(snapshot.read_text())
    assert "paper" in written
    assert "live" in written
    assert written["paper"]["win_rate"] == pytest.approx(1.0)
    assert written["paper"]["trade_count"] == 3
    assert written["live"]["trade_count"] == 0


def test_compute_metrics_warns_low_win_rate(tmp_path, monkeypatch, capsys):
    """Alerts only fire for live trades — use status='placed'."""
    monkeypatch.setenv("BANKROLL_USD", "100")
    # 1 win, 4 losses → 20% win rate — all live (placed) to trigger alert
    trades = [_make_trade(outcome="win", pnl=4.0, resolved_at="2026-03-15T23:00:00+00:00", status="placed")] + [
        _make_trade(outcome="loss", pnl=-1.0, resolved_at=f"2026-03-1{i}T23:00:00+00:00", status="placed")
        for i in range(6, 10)
    ]
    trade_log = tmp_path / "trade_log.jsonl"
    _write_trade_log(trade_log, trades)

    import metrics as _m
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_m, "METRICS_PATH", tmp_path / "m.json")
        mp.setattr(_m, "METRICS_HISTORY_PATH", tmp_path / "h.jsonl")
        from unittest.mock import patch
        with patch("metrics.compute_rolling_brier", return_value={"brier_score": None}):
            compute_metrics(trade_log_path=trade_log)

    captured = capsys.readouterr()
    assert "WARN: win_rate" in captured.err


# ---------------------------------------------------------------------------
# Unit: filter_trades_by_mode
# ---------------------------------------------------------------------------

def test_filter_paper_trades():
    trades = [
        _make_trade(outcome="win", pnl=1.0, status="paper"),
        _make_trade(outcome="win", pnl=1.0, status="placed"),
        _make_trade(outcome="win", pnl=1.0, status="filled"),
    ]
    paper = filter_trades_by_mode(trades, "paper")
    assert len(paper) == 1
    assert all(t["status"] == "paper" for t in paper)


def test_filter_live_trades():
    trades = [
        _make_trade(outcome="win", pnl=1.0, status="paper"),
        _make_trade(outcome="win", pnl=1.0, status="placed"),
        _make_trade(outcome="win", pnl=1.0, status="filled"),
    ]
    live = filter_trades_by_mode(trades, "live")
    assert len(live) == 2
    assert all(t["status"] in ("placed", "filled") for t in live)


def test_filter_empty():
    assert filter_trades_by_mode([], "paper") == []
    assert filter_trades_by_mode([], "live") == []


# ---------------------------------------------------------------------------
# Post-floor metric segments — only trades with scan_liquidity_floor == "v1"
# ---------------------------------------------------------------------------

def _v1_trade(*, status: str, outcome: str, pnl: float, resolved_day: int) -> dict:
    t = _make_trade(
        outcome=outcome, pnl=pnl,
        resolved_at=f"2026-03-{resolved_day:02d}T23:00:00+00:00",
        status=status,
    )
    t["scan_liquidity_floor"] = "v1"
    return t


def test_compute_metrics_emits_paper_post_floor_when_5plus_v1_trades(tmp_path, monkeypatch):
    """5 v1 paper trades → metrics.paper_post_floor present."""
    monkeypatch.setenv("BANKROLL_USD", "100")
    trades = [_v1_trade(status="paper", outcome="win", pnl=2.0, resolved_day=10 + i) for i in range(5)]
    trade_log = tmp_path / "trade_log.jsonl"
    _write_trade_log(trade_log, trades)

    import metrics as _m
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_m, "METRICS_PATH", tmp_path / "m.json")
        mp.setattr(_m, "METRICS_HISTORY_PATH", tmp_path / "h.jsonl")
        from unittest.mock import patch
        with patch("metrics.compute_rolling_brier", return_value={"brier_score": 0.20}):
            result = compute_metrics(trade_log_path=trade_log)

    assert "paper_post_floor" in result
    assert result["paper_post_floor"]["trade_count"] == 5
    assert result["paper_post_floor"]["win_rate"] == pytest.approx(1.0)


def test_compute_metrics_omits_post_floor_when_under_5_v1_trades(tmp_path, monkeypatch):
    """4 v1 trades → not enough signal; the segment is omitted, not zero-reported."""
    monkeypatch.setenv("BANKROLL_USD", "100")
    trades = [_v1_trade(status="paper", outcome="win", pnl=2.0, resolved_day=10 + i) for i in range(4)]
    trade_log = tmp_path / "trade_log.jsonl"
    _write_trade_log(trade_log, trades)

    import metrics as _m
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_m, "METRICS_PATH", tmp_path / "m.json")
        mp.setattr(_m, "METRICS_HISTORY_PATH", tmp_path / "h.jsonl")
        from unittest.mock import patch
        with patch("metrics.compute_rolling_brier", return_value={"brier_score": 0.20}):
            result = compute_metrics(trade_log_path=trade_log)

    assert "paper_post_floor" not in result
    assert "live_post_floor" not in result


def test_compute_metrics_post_floor_filters_by_v1_only(tmp_path, monkeypatch):
    """Pre-floor trades (no scan_liquidity_floor key) excluded from post_floor."""
    monkeypatch.setenv("BANKROLL_USD", "100")
    pre_floor = [_make_trade(outcome="loss", pnl=-1.0, resolved_at=f"2026-03-1{i}T23:00:00+00:00",
                              status="placed") for i in range(5)]
    post_floor = [_v1_trade(status="placed", outcome="win", pnl=2.0, resolved_day=20 + i) for i in range(5)]
    trade_log = tmp_path / "trade_log.jsonl"
    _write_trade_log(trade_log, pre_floor + post_floor)

    import metrics as _m
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_m, "METRICS_PATH", tmp_path / "m.json")
        mp.setattr(_m, "METRICS_HISTORY_PATH", tmp_path / "h.jsonl")
        from unittest.mock import patch
        with patch("metrics.compute_rolling_brier", return_value={"brier_score": 0.20}):
            result = compute_metrics(trade_log_path=trade_log)

    # Live includes all 10; live_post_floor only the 5 v1 wins
    assert result["live"]["trade_count"] == 10
    assert result["live_post_floor"]["trade_count"] == 5
    assert result["live_post_floor"]["win_rate"] == pytest.approx(1.0)
