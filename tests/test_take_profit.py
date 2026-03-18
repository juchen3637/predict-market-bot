"""
test_take_profit.py — Tests for take_profit.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_COMPOUND_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pm-compound" / "scripts"
_RISK_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pm-risk" / "scripts"
for _p in (str(_COMPOUND_SCRIPTS), str(_RISK_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from take_profit import check_take_profit, run_take_profit_checks


# ---------------------------------------------------------------------------
# check_take_profit unit tests
# ---------------------------------------------------------------------------

class TestCheckTakeProfit:

    def test_yes_position_no_exit_price_not_reached(self):
        """YES position: current price hasn't reached fill + take_profit_pct."""
        trade = {"direction": "yes", "fill_price": 0.50, "market_id": "mkt_1"}
        platform_state = {"mkt_1": 0.60}  # 0.60 < 0.50 + 0.15 = 0.65
        assert check_take_profit(trade, platform_state, take_profit_pct=0.15) is False

    def test_yes_position_exit_threshold_hit(self):
        """YES position: current price >= fill + take_profit_pct → exit."""
        trade = {"direction": "yes", "fill_price": 0.50, "market_id": "mkt_2"}
        platform_state = {"mkt_2": 0.65}  # exactly at threshold
        assert check_take_profit(trade, platform_state, take_profit_pct=0.15) is True

    def test_yes_position_exit_threshold_exceeded(self):
        """YES position: current price well above threshold → exit."""
        trade = {"direction": "yes", "fill_price": 0.40, "market_id": "mkt_3"}
        platform_state = {"mkt_3": 0.80}
        assert check_take_profit(trade, platform_state, take_profit_pct=0.15) is True

    def test_no_position_no_exit_price_not_reached(self):
        """NO position: NO price hasn't appreciated enough."""
        # Fill price for NO was 0.40 (YES was at 0.60)
        # Current YES is 0.55 → NO is 0.45. Need 0.40 + 0.15 = 0.55 for exit.
        trade = {"direction": "no", "fill_price": 0.40, "market_id": "mkt_4"}
        platform_state = {"mkt_4": 0.55}  # NO = 0.45 < 0.55
        assert check_take_profit(trade, platform_state, take_profit_pct=0.15) is False

    def test_no_position_exit_threshold_hit(self):
        """NO position: NO price >= fill_no + take_profit_pct → exit."""
        # fill_no = 0.40, need current_no >= 0.55
        # current_yes = 0.45 → current_no = 0.55 → exactly at threshold
        trade = {"direction": "no", "fill_price": 0.40, "market_id": "mkt_5"}
        platform_state = {"mkt_5": 0.45}
        assert check_take_profit(trade, platform_state, take_profit_pct=0.15) is True

    def test_missing_fill_price_returns_false(self):
        """Missing fill_price → no exit."""
        trade = {"direction": "yes", "fill_price": None, "market_id": "mkt_6"}
        platform_state = {"mkt_6": 0.80}
        assert check_take_profit(trade, platform_state) is False

    def test_missing_current_price_returns_false(self):
        """Market not in platform_state → no exit."""
        trade = {"direction": "yes", "fill_price": 0.50, "market_id": "mkt_7"}
        platform_state = {}
        assert check_take_profit(trade, platform_state) is False

    def test_already_resolved_trade_excluded_by_caller(self):
        """Verify check_take_profit ignores outcome field (filtering is caller's job)."""
        # check_take_profit itself doesn't look at outcome — caller filters resolved trades
        trade = {"direction": "yes", "fill_price": 0.50, "market_id": "mkt_8", "outcome": "win"}
        platform_state = {"mkt_8": 0.70}
        # Price qualifies, function returns True — caller must filter resolved trades
        assert check_take_profit(trade, platform_state, take_profit_pct=0.15) is True


# ---------------------------------------------------------------------------
# run_take_profit_checks integration tests
# ---------------------------------------------------------------------------

class TestRunTakeProfitChecks:

    _SETTINGS = {"execution": {"take_profit_pct": 0.15}}

    def _write_trades(self, tmp_path: Path, trades: list[dict]) -> Path:
        log = tmp_path / "trade_log.jsonl"
        with open(log, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")
        return log

    def test_no_open_trades_returns_empty(self, tmp_path):
        with patch("take_profit.TRADE_LOG_PATH", tmp_path / "trade_log.jsonl"):
            result = run_take_profit_checks(self._SETTINGS)
        assert result == []

    def test_resolved_trade_skipped(self, tmp_path):
        """Resolved trade (outcome set) is not included in open trades."""
        trades = [
            {
                "trade_id": "t1",
                "market_id": "mkt_r",
                "platform": "polymarket",
                "direction": "yes",
                "fill_price": 0.40,
                "status": "placed",
                "outcome": "win",  # already resolved
                "size_contracts": 5,
            }
        ]
        log = self._write_trades(tmp_path, trades)

        with patch("take_profit.TRADE_LOG_PATH", log):
            result = run_take_profit_checks(self._SETTINGS)

        assert result == []

    def test_price_not_reached_no_exit(self, tmp_path):
        """Trade below take-profit threshold → no exit placed."""
        trades = [
            {
                "trade_id": "t2",
                "market_id": "mkt_below",
                "platform": "polymarket",
                "direction": "yes",
                "fill_price": 0.50,
                "status": "placed",
                "outcome": None,
                "size_contracts": 5,
            }
        ]
        log = self._write_trades(tmp_path, trades)

        with (
            patch("take_profit.TRADE_LOG_PATH", log),
            patch("take_profit._get_current_yes_price", return_value=0.60),  # below 0.65
        ):
            result = run_take_profit_checks(self._SETTINGS)

        assert result == []

    def test_price_hit_threshold_exit_placed(self, tmp_path):
        """Trade at take-profit threshold → exit order placed, trade_id returned."""
        trades = [
            {
                "trade_id": "t3",
                "market_id": "mkt_hit",
                "platform": "polymarket",
                "direction": "yes",
                "fill_price": 0.50,
                "status": "placed",
                "outcome": None,
                "size_contracts": 5,
            }
        ]
        log = self._write_trades(tmp_path, trades)

        mock_exit_result = {"order_id": "exit_001", "status": "filled"}

        with (
            patch("take_profit.TRADE_LOG_PATH", log),
            patch("take_profit._get_current_yes_price", return_value=0.65),  # exactly 0.50+0.15
            patch("take_profit._place_exit_order", return_value=mock_exit_result),
        ):
            result = run_take_profit_checks(self._SETTINGS)

        assert "t3" in result
