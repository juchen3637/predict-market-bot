"""
test_execute_order.py — Tests for skills/pm-risk/scripts/execute_order.py
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

import execute_order as eo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def signal():
    return {
        "market_id": "test-market-1",
        "platform": "kalshi",
        "direction": "yes",
        "current_yes_price": 0.60,
        "entry_price": 0.60,
        "p_model": 0.72,
        "edge": 0.12,
    }


@pytest.fixture()
def position():
    return {
        "contracts": 10,
        "size_usd_capped": 6.00,
        "kelly_fraction_used": 0.25,
    }


@pytest.fixture()
def trade_log(tmp_path) -> Path:
    return tmp_path / "trade_log.jsonl"


# ---------------------------------------------------------------------------
# Kill Switch Tests
# ---------------------------------------------------------------------------

def test_kill_switch_blocks_execution(signal, position, tmp_path, monkeypatch):
    stop_file = tmp_path / "STOP"
    stop_file.touch()
    monkeypatch.setattr(eo, "STOP_FILE", stop_file)
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    record = eo.execute(signal, position)

    assert record.status == "rejected"
    assert record.rejection_reason == "kill_switch_active"


def test_no_stop_file_allows_paper_execution(signal, position, tmp_path, monkeypatch):
    stop_file = tmp_path / "STOP"
    assert not stop_file.exists()
    monkeypatch.setattr(eo, "STOP_FILE", stop_file)
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    record = eo.execute(signal, position)

    assert record.status == "paper"
    assert record.fill_price == pytest.approx(0.60)


# ---------------------------------------------------------------------------
# Paper Fill Tests
# ---------------------------------------------------------------------------

def test_paper_fill_at_limit_price(signal, position, tmp_path, monkeypatch):
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    record = eo.execute(signal, position)

    assert record.status == "paper"
    assert record.fill_price == pytest.approx(0.60)
    assert record.market_id == "test-market-1"
    assert record.platform == "kalshi"
    assert record.direction == "yes"


def test_paper_fill_writes_to_trade_log(signal, position, tmp_path, monkeypatch):
    log_path = tmp_path / "trade_log.jsonl"
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", log_path)
    monkeypatch.setenv("PAPER_TRADING", "true")

    eo.execute(signal, position)

    assert log_path.exists()
    rows = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["status"] == "paper"


# ---------------------------------------------------------------------------
# Slippage Abort Tests
# ---------------------------------------------------------------------------

def test_slippage_abort_when_exceeded(signal, position, tmp_path, monkeypatch):
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    # Simulate a fill with 5% slippage (exceeds 2% limit)
    bad_fill = {"order_id": "test", "fill_price": 0.60 * 1.05, "status": "filled"}
    with patch.object(eo, "simulate_paper_order", return_value=bad_fill):
        record = eo.execute(signal, position)

    assert record.status == "rejected"
    assert "slippage_exceeded" in record.rejection_reason


def test_slippage_within_limit_is_accepted(signal, position, tmp_path, monkeypatch):
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    # 1% slippage — within the 2% limit
    ok_fill = {"order_id": "test", "fill_price": 0.60 * 1.01, "status": "filled"}
    with patch.object(eo, "simulate_paper_order", return_value=ok_fill):
        record = eo.execute(signal, position)

    assert record.status == "paper"


# ---------------------------------------------------------------------------
# Live Stub Tests (No NotImplementedError)
# ---------------------------------------------------------------------------

def test_polymarket_live_stub_returns_declined(monkeypatch):
    # Clear credentials so client returns declined with live_mode_not_enabled
    for key in ["POLYMARKET_API_KEY", "POLYMARKET_API_SECRET",
                "POLYMARKET_API_PASSPHRASE", "POLYMARKET_WALLET_PRIVATE_KEY"]:
        monkeypatch.delenv(key, raising=False)

    result = eo.place_polymarket_order("test-id", "yes", 10, 0.60)

    assert result["status"] == "declined"
    assert result["reason"] == "live_mode_not_enabled"
    assert result["fill_price"] is None


def test_kalshi_live_stub_returns_declined(monkeypatch):
    # Clear credentials so client returns declined with live_mode_not_enabled
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)
    monkeypatch.delenv("KALSHI_API_SECRET", raising=False)

    result = eo.place_kalshi_order("KXBTC-23-T45000", "yes", 10, 0.60)

    assert result["status"] == "declined"
    assert result["reason"] == "live_mode_not_enabled"
    assert result["fill_price"] is None


def test_live_declined_order_logged_as_rejected(signal, position, tmp_path, monkeypatch):
    """When platform declines, trade record should show status=rejected."""
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "false")
    # Clear Kalshi credentials so order returns declined with live_mode_not_enabled
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)
    monkeypatch.delenv("KALSHI_API_SECRET", raising=False)

    with patch("check_depth.has_adequate_depth", return_value=True):
        record = eo.execute(signal, position)

    # Kalshi order returns declined → should be rejected, not failed
    assert record.status == "rejected"
    assert record.rejection_reason == "live_mode_not_enabled"


# ---------------------------------------------------------------------------
# Hedge Flag Tests
# ---------------------------------------------------------------------------

def test_hedge_needed_when_market_moved(tmp_path, monkeypatch):
    """Market has moved >5% from entry → hedge_needed flag set."""
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    # current_yes_price (market now) = 0.70, entry_price = 0.60
    # move = |0.70 - 0.60| / 0.60 = 16.7% > HEDGE_TRIGGER(5%)
    sig = {
        "market_id": "test-hedge",
        "platform": "kalshi",
        "direction": "yes",
        "current_yes_price": 0.70,  # market has moved
        "entry_price": 0.60,
        "p_model": 0.72,
        "edge": 0.12,
    }
    pos = {"contracts": 5, "size_usd_capped": 3.00, "kelly_fraction_used": 0.25}

    record = eo.execute(sig, pos)

    assert record.status == "paper"
    assert record.hedge_needed is True


def test_no_hedge_when_price_stable(signal, position, tmp_path, monkeypatch):
    """Price hasn't moved → hedge_needed stays False."""
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    record = eo.execute(signal, position)

    assert record.hedge_needed is False


# ---------------------------------------------------------------------------
# Live Delegation Tests
# ---------------------------------------------------------------------------

def test_live_polymarket_delegates(monkeypatch):
    """place_polymarket_order delegates to polymarket_client.place_order."""
    import polymarket_client
    mock_result = {"order_id": "pm-001", "fill_price": 0.60, "status": "filled"}
    with patch.object(polymarket_client, "place_order", return_value=mock_result) as mock_fn:
        result = eo.place_polymarket_order("token-123", "yes", 10, 0.60)

    mock_fn.assert_called_once_with("token-123", "yes", 10, 0.60)
    assert result["status"] == "filled"


def test_live_kalshi_delegates(monkeypatch):
    """place_kalshi_order delegates to kalshi_client.place_order."""
    import kalshi_client
    mock_result = {"order_id": "k-001", "fill_price": None, "status": "open"}
    with patch.object(kalshi_client, "place_order", return_value=mock_result) as mock_fn:
        result = eo.place_kalshi_order("KXBTC-23-T45000", "yes", 5, 0.55, use_demo=True)

    mock_fn.assert_called_once_with("KXBTC-23-T45000", "yes", 5, 0.55, True)
    assert result["status"] == "open"


# ---------------------------------------------------------------------------
# Zero Contracts
# ---------------------------------------------------------------------------

def test_zero_contracts_rejected(signal, tmp_path, monkeypatch):
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    pos = {"contracts": 0, "size_usd_capped": 0.0, "kelly_fraction_used": 0.25}
    record = eo.execute(signal, pos)

    assert record.status == "rejected"
    assert record.rejection_reason == "zero_contracts"
