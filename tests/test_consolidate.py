"""
test_consolidate.py — Tests for skills/pm-compound/scripts/consolidate.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import consolidate as cons


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_loss(trade_id: str = "loss-1", market_id: str = "test-market") -> dict:
    return {
        "trade_id": trade_id,
        "market_id": market_id,
        "platform": "kalshi",
        "direction": "yes",
        "size_contracts": 10,
        "size_usd": 6.0,
        "entry_price": 0.60,
        "fill_price": 0.60,
        "p_model": 0.55,
        "edge": 0.05,
        "kelly_fraction": 0.25,
        "status": "paper",
        "rejection_reason": None,
        "placed_at": "2026-03-17T10:00:00+00:00",
        "resolved_at": "2026-03-17T23:00:00+00:00",
        "outcome": "loss",
        "pnl": -6.0,
        "hedge_needed": False,
    }


@pytest.fixture()
def loss_trade_log(tmp_path) -> Path:
    log = tmp_path / "trade_log.jsonl"
    log.write_text(json.dumps(_make_loss()) + "\n")
    return log


@pytest.fixture()
def empty_trade_log(tmp_path) -> Path:
    log = tmp_path / "trade_log.jsonl"
    log.touch()
    return log


# ---------------------------------------------------------------------------
# Helper: run_postmortem_for_losses
# ---------------------------------------------------------------------------

def test_postmortem_classifies_new_losses(tmp_path, monkeypatch):
    loss = _make_loss("loss-1")
    log = tmp_path / "trade_log.jsonl"
    log.write_text(json.dumps(loss) + "\n")

    processed_path = tmp_path / "postmortem_processed.json"
    failure_log_path = tmp_path / "failure_log.md"
    failure_log_path.write_text("# Failure Knowledge Base\n")

    monkeypatch.setattr(cons, "TRADE_LOG_PATH", log)
    monkeypatch.setattr(cons, "POSTMORTEM_PROCESSED_PATH", processed_path)
    from postmortem import FAILURE_LOG
    monkeypatch.setattr("postmortem.FAILURE_LOG", failure_log_path)

    from postmortem import append_failure_log
    with patch("consolidate.append_failure_log") as mock_append:
        count = cons.run_postmortem_for_losses()

    assert count == 1
    assert processed_path.exists()
    ids = json.loads(processed_path.read_text())
    assert "loss-1" in ids


def test_postmortem_skips_already_processed(tmp_path, monkeypatch):
    loss = _make_loss("loss-already")
    log = tmp_path / "trade_log.jsonl"
    log.write_text(json.dumps(loss) + "\n")

    # Mark as already processed
    processed_path = tmp_path / "postmortem_processed.json"
    processed_path.write_text(json.dumps(["loss-already"]))

    monkeypatch.setattr(cons, "TRADE_LOG_PATH", log)
    monkeypatch.setattr(cons, "POSTMORTEM_PROCESSED_PATH", processed_path)

    with patch("consolidate.append_failure_log") as mock_append:
        count = cons.run_postmortem_for_losses()

    assert count == 0
    mock_append.assert_not_called()


# ---------------------------------------------------------------------------
# Helper: write_daily_summary
# ---------------------------------------------------------------------------

def test_write_daily_summary_creates_file(tmp_path, monkeypatch):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary_dir = tmp_path / "daily_summaries"
    monkeypatch.setattr(cons, "DAILY_SUMMARIES_DIR", summary_dir)
    monkeypatch.setattr(cons, "STOP_FILE_PATH", tmp_path / "STOP")

    metrics = {
        "trade_count": 5,
        "win_rate": 0.6,
        "sharpe": 2.1,
        "max_drawdown": 0.03,
        "profit_factor": 1.5,
        "brier_score": 0.22,
    }

    path = cons.write_daily_summary(metrics, new_lessons=2)

    assert path.exists()
    assert path.name == f"{today}.md"
    content = path.read_text()
    assert "Daily Summary" in content
    assert "Trades resolved: 5" in content
    assert "New lessons: 2" in content
    assert "STOP file: no" in content


def test_write_daily_summary_shows_stop_active(tmp_path, monkeypatch):
    stop_file = tmp_path / "STOP"
    stop_file.touch()
    monkeypatch.setattr(cons, "STOP_FILE_PATH", stop_file)
    monkeypatch.setattr(cons, "DAILY_SUMMARIES_DIR", tmp_path / "summaries")

    metrics = {"trade_count": 0, "win_rate": None, "sharpe": None,
               "max_drawdown": None, "profit_factor": None, "brier_score": None}

    path = cons.write_daily_summary(metrics, new_lessons=0)
    assert "STOP file: yes" in path.read_text()


def test_write_daily_summary_na_for_missing_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(cons, "STOP_FILE_PATH", tmp_path / "STOP")
    monkeypatch.setattr(cons, "DAILY_SUMMARIES_DIR", tmp_path / "summaries")

    metrics = {"trade_count": 0, "win_rate": None, "sharpe": None,
               "max_drawdown": None, "profit_factor": None, "brier_score": None}

    path = cons.write_daily_summary(metrics, new_lessons=0)
    content = path.read_text()
    assert "N/A" in content


# ---------------------------------------------------------------------------
# Integration: run()
# ---------------------------------------------------------------------------

def test_full_nightly_run_creates_daily_summary(tmp_path, monkeypatch):
    """Mock all sub-steps; verify daily summary is created."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary_dir = tmp_path / "daily_summaries"
    stop_file = tmp_path / "STOP"

    monkeypatch.setattr(cons, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    monkeypatch.setattr(cons, "POSTMORTEM_PROCESSED_PATH", tmp_path / "postmortem_processed.json")
    monkeypatch.setattr(cons, "DAILY_SUMMARIES_DIR", summary_dir)
    monkeypatch.setattr(cons, "STOP_FILE_PATH", stop_file)

    dummy_metrics = {
        "trade_count": 3, "win_rate": 0.67, "sharpe": 2.5,
        "max_drawdown": 0.02, "profit_factor": 2.0, "brier_score": 0.18,
    }

    with patch("consolidate.run_resolver"), \
         patch("consolidate.run_postmortem_for_losses", return_value=1), \
         patch("consolidate.compute_metrics", return_value=dummy_metrics):
        cons.run()

    summary_path = summary_dir / f"{today}.md"
    assert summary_path.exists()
    content = summary_path.read_text()
    assert "Trades resolved: 3" in content
    assert "New lessons: 1" in content


def test_run_exits_1_on_resolver_failure(tmp_path, monkeypatch):
    """Resolver step failure → sys.exit(1)."""
    monkeypatch.setattr(cons, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    monkeypatch.setattr(cons, "POSTMORTEM_PROCESSED_PATH", tmp_path / "processed.json")
    monkeypatch.setattr(cons, "DAILY_SUMMARIES_DIR", tmp_path / "summaries")
    monkeypatch.setattr(cons, "STOP_FILE_PATH", tmp_path / "STOP")

    with patch("consolidate.run_resolver", side_effect=RuntimeError("API down")):
        with pytest.raises(SystemExit) as exc_info:
            cons.run()

    assert exc_info.value.code == 1


def test_run_exits_1_on_metrics_failure(tmp_path, monkeypatch):
    """Metrics step failure → sys.exit(1)."""
    monkeypatch.setattr(cons, "TRADE_LOG_PATH", tmp_path / "trade_log.jsonl")
    monkeypatch.setattr(cons, "POSTMORTEM_PROCESSED_PATH", tmp_path / "processed.json")
    monkeypatch.setattr(cons, "DAILY_SUMMARIES_DIR", tmp_path / "summaries")
    monkeypatch.setattr(cons, "STOP_FILE_PATH", tmp_path / "STOP")

    with patch("consolidate.run_resolver"), \
         patch("consolidate.run_postmortem_for_losses", return_value=0), \
         patch("consolidate.compute_metrics", side_effect=RuntimeError("disk full")):
        with pytest.raises(SystemExit) as exc_info:
            cons.run()

    assert exc_info.value.code == 1
