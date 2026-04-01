"""
tests/test_resolver.py — Unit tests for resolver.py

Tests win/loss direction logic, skip-when-unresolved, and Brier trigger.
No real API calls are made.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills" / "pm-compound" / "scripts"))

import resolver


# ---------------------------------------------------------------------------
# determine_trade_result
# ---------------------------------------------------------------------------

class TestDetermineTradeResult:
    def test_yes_direction_yes_outcome_is_win(self):
        assert resolver.determine_trade_result("yes", "yes") == "win"

    def test_yes_direction_no_outcome_is_loss(self):
        assert resolver.determine_trade_result("yes", "no") == "loss"

    def test_no_direction_no_outcome_is_win(self):
        assert resolver.determine_trade_result("no", "no") == "win"

    def test_no_direction_yes_outcome_is_loss(self):
        assert resolver.determine_trade_result("no", "yes") == "loss"

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError):
            resolver.determine_trade_result("maybe", "yes")


# ---------------------------------------------------------------------------
# load_unresolved_trades
# ---------------------------------------------------------------------------

class TestLoadUnresolvedTrades:
    def test_returns_empty_when_log_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", tmp_path / "missing.jsonl")
        result = resolver.load_unresolved_trades()
        assert result == []

    def test_returns_only_unresolved(self, tmp_path, monkeypatch):
        log = tmp_path / "trade_log.jsonl"
        trades = [
            {"trade_id": "t1", "outcome": None, "market_id": "m1", "platform": "polymarket"},
            {"trade_id": "t2", "outcome": "win", "market_id": "m2", "platform": "kalshi"},
            {"trade_id": "t3", "outcome": None, "market_id": "m3", "platform": "polymarket"},
        ]
        with open(log, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)
        result = resolver.load_unresolved_trades()
        assert len(result) == 2
        assert {r["trade_id"] for r in result} == {"t1", "t3"}

    def test_skips_malformed_lines(self, tmp_path, monkeypatch):
        log = tmp_path / "trade_log.jsonl"
        log.write_text('{"trade_id": "t1", "outcome": null}\n{bad json}\n')
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)
        result = resolver.load_unresolved_trades()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# run() — integration-style with mocked dependencies
# ---------------------------------------------------------------------------

def _make_trade(**kwargs) -> dict:
    defaults = {
        "trade_id": "trade_001",
        "market_id": "mkt_abc",
        "platform": "polymarket",
        "direction": "yes",
        "size_contracts": 10,
        "entry_price": 0.6,
        "fill_price": 0.61,
        "outcome": None,
    }
    return {**defaults, **kwargs}


class TestResolverRun:
    def test_no_unresolved_trades_prints_zero(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "trade_log.jsonl"
        log.write_text("")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        resolver.run()

        captured = capsys.readouterr()
        assert "0 unresolved trades found" in captured.out

    def test_skip_when_market_not_yet_resolved(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade()
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution") as mock_res:
            mock_res.return_value = {"resolved": False, "outcome": None, "resolved_at": None}
            with patch("resolver.update_resolved_trade") as mock_update:
                resolver.run()
                mock_update.assert_not_called()

        captured = capsys.readouterr()
        assert "0 new resolutions" in captured.out

    def test_resolves_win_trade(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade(direction="yes", size_contracts=10, fill_price=0.6)
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution") as mock_res:
            mock_res.return_value = {
                "resolved": True,
                "outcome": "yes",
                "resolved_at": "2024-12-31T23:59:00Z",
            }
            with patch("resolver.update_resolved_trade", return_value=True) as mock_update:
                with patch("resolver.compute_rolling_brier") as mock_brier:
                    mock_brier.return_value = {
                        "brier_score": 0.18,
                        "trade_count": 12,
                        "message": None,
                    }
                    resolver.run()

        mock_update.assert_called_once_with(
            "trade_001", "win", pytest.approx(4.0), "2024-12-31T23:59:00Z"
        )
        mock_brier.assert_called_once()
        captured = capsys.readouterr()
        assert "Resolved 1 trade" in captured.out

    def test_resolves_loss_trade(self, tmp_path, monkeypatch):
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade(direction="yes", fill_price=0.7)
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution") as mock_res:
            mock_res.return_value = {"resolved": True, "outcome": "no", "resolved_at": None}
            with patch("resolver.update_resolved_trade", return_value=True):
                with patch("resolver.compute_rolling_brier") as mock_brier:
                    mock_brier.return_value = {"brier_score": 0.22}
                    resolver.run()

        # direction=yes + outcome=no = loss; pnl = 10 * (-0.7) = -7.0
        resolver.update_resolved_trade  # called via patch above

    def test_no_direction_win(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade(direction="no", fill_price=0.35)
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution") as mock_res:
            mock_res.return_value = {"resolved": True, "outcome": "no", "resolved_at": None}
            with patch("resolver.update_resolved_trade", return_value=True) as mock_update:
                with patch("resolver.compute_rolling_brier", return_value={"brier_score": 0.2}):
                    resolver.run()

        # direction=no + outcome=no = win; pnl = 10 * (1.0 - 0.35) = 6.5
        args = mock_update.call_args[0]
        assert args[1] == "win"
        assert args[2] == pytest.approx(6.5)

    def test_no_direction_loss(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade(direction="no", fill_price=0.35)
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution") as mock_res:
            mock_res.return_value = {"resolved": True, "outcome": "yes", "resolved_at": None}
            with patch("resolver.update_resolved_trade", return_value=True) as mock_update:
                with patch("resolver.compute_rolling_brier", return_value={"brier_score": 0.25}):
                    resolver.run()

        args = mock_update.call_args[0]
        assert args[1] == "loss"
        assert args[2] == pytest.approx(-3.5)

    def test_brier_triggered_after_resolution(self, tmp_path, monkeypatch):
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade()
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution") as mock_res:
            mock_res.return_value = {"resolved": True, "outcome": "yes", "resolved_at": None}
            with patch("resolver.update_resolved_trade", return_value=True):
                with patch("resolver.compute_rolling_brier") as mock_brier:
                    mock_brier.return_value = {"brier_score": 0.15}
                    resolver.run()

        mock_brier.assert_called_once()

    def test_brier_not_triggered_when_no_resolutions(self, tmp_path, monkeypatch):
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade()
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution") as mock_res:
            mock_res.return_value = {"resolved": False, "outcome": None, "resolved_at": None}
            with patch("resolver.compute_rolling_brier") as mock_brier:
                resolver.run()

        mock_brier.assert_not_called()

    def test_api_error_skips_trade(self, tmp_path, monkeypatch, capsys):
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade()
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution", return_value=None):
            with patch("resolver.update_resolved_trade") as mock_update:
                resolver.run()

        mock_update.assert_not_called()

    def test_indeterminate_outcome_skips_trade(self, tmp_path, monkeypatch):
        """Resolved=True but outcome=None (e.g. void/cancelled market)."""
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade()
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution") as mock_res:
            mock_res.return_value = {"resolved": True, "outcome": None, "resolved_at": None}
            with patch("resolver.update_resolved_trade") as mock_update:
                resolver.run()

        mock_update.assert_not_called()

    def test_kalshi_resting_order_is_skipped(self, tmp_path, monkeypatch):
        """Resting Kalshi orders should not be resolved — check again next cycle."""
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade(
            platform="kalshi", status="placed", order_id="kalshi-order-001"
        )
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.kalshi_client.get_order", return_value={"status": "resting", "fill_price": None}):
            with patch("resolver.get_market_resolution") as mock_res:
                with patch("resolver.update_resolved_trade") as mock_update:
                    resolver.run()

        mock_res.assert_not_called()
        mock_update.assert_not_called()

    def test_kalshi_canceled_order_marked_expired(self, tmp_path, monkeypatch, capsys):
        """Canceled Kalshi orders (never filled) should be marked expired with pnl=0."""
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade(
            platform="kalshi", status="placed", order_id="kalshi-order-002"
        )
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.kalshi_client.get_order", return_value={"status": "canceled", "fill_price": None}):
            with patch("resolver.get_market_resolution") as mock_res:
                with patch("resolver.update_resolved_trade", return_value=True) as mock_update:
                    with patch("resolver.compute_rolling_brier", return_value={"brier_score": 0.2}):
                        resolver.run()

        mock_res.assert_not_called()
        args = mock_update.call_args[0]
        assert args[0] == "trade_001"
        assert args[1] == "expired"
        assert args[2] == pytest.approx(0.0)
        captured = capsys.readouterr()
        assert "Expired" in captured.out

    def test_kalshi_filled_order_continues_to_market_resolution(self, tmp_path, monkeypatch, capsys):
        """Filled Kalshi orders should update fill_price and resolve win/loss via market resolution."""
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade(
            platform="kalshi", status="placed", order_id="kalshi-order-003",
            direction="yes", size_contracts=10
        )
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.kalshi_client.get_order", return_value={"status": "filled", "fill_price": 0.63}):
            with patch("resolver.get_market_resolution") as mock_res:
                mock_res.return_value = {"resolved": True, "outcome": "yes", "resolved_at": "2024-12-31T23:59:00Z"}
                with patch("resolver.update_resolved_trade", return_value=True) as mock_update:
                    with patch("resolver.compute_rolling_brier", return_value={"brier_score": 0.18}):
                        resolver.run()

        mock_res.assert_called_once()
        args = mock_update.call_args[0]
        assert args[1] == "win"
        assert args[2] == pytest.approx(10 * (1.0 - 0.63))

    def test_kalshi_unknown_order_status_falls_through_to_market_resolution(self, tmp_path, monkeypatch):
        """Unknown order status should fall through to market resolution as a best-effort."""
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade(
            platform="kalshi", status="placed", order_id="kalshi-order-004"
        )
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.kalshi_client.get_order", return_value={"status": "unknown", "fill_price": None}):
            with patch("resolver.get_market_resolution") as mock_res:
                mock_res.return_value = {"resolved": False, "outcome": None, "resolved_at": None}
                with patch("resolver.update_resolved_trade") as mock_update:
                    resolver.run()

        mock_res.assert_called_once()
        mock_update.assert_not_called()

    def test_kalshi_trade_without_order_id_falls_through(self, tmp_path, monkeypatch):
        """Kalshi trades without an order_id (legacy) skip the order check."""
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade(platform="kalshi", status="placed")  # no order_id
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.kalshi_client.get_order") as mock_get:
            with patch("resolver.get_market_resolution") as mock_res:
                mock_res.return_value = {"resolved": False, "outcome": None, "resolved_at": None}
                resolver.run()

        mock_get.assert_not_called()
        mock_res.assert_called_once()

    def test_brier_score_none_message_shown(self, tmp_path, monkeypatch, capsys):
        """When Brier needs more trades, message is shown instead of score."""
        log = tmp_path / "trade_log.jsonl"
        trade = _make_trade()
        log.write_text(json.dumps(trade) + "\n")
        monkeypatch.setattr(resolver, "TRADE_LOG_PATH", log)

        with patch("resolver.get_market_resolution") as mock_res:
            mock_res.return_value = {"resolved": True, "outcome": "yes", "resolved_at": None}
            with patch("resolver.update_resolved_trade", return_value=True):
                with patch("resolver.compute_rolling_brier") as mock_brier:
                    mock_brier.return_value = {
                        "brier_score": None,
                        "message": "Need at least 10 resolved trades (have 1)",
                    }
                    resolver.run()

        captured = capsys.readouterr()
        assert "Need at least 10 resolved trades" in captured.out
