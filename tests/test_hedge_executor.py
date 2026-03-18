"""
test_hedge_executor.py — Tests for hedge_executor.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

_RISK_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pm-risk" / "scripts"
if str(_RISK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RISK_SCRIPTS))

from hedge_executor import execute_hedge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeTrade:
    market_id: str
    platform: str
    direction: str
    size_contracts: int
    fill_price: float
    trade_id: str = "trade_abc"
    entry_price: float = 0.55


_FILLED_RESULT = {"order_id": "hedge_001", "fill_price": 0.45, "status": "filled"}
_DECLINED_RESULT = {"order_id": None, "fill_price": None, "status": "declined", "reason": "no_creds"}


# ---------------------------------------------------------------------------
# Direction Tests
# ---------------------------------------------------------------------------

class TestHedgeDirection:

    def test_yes_trade_places_buy_no(self):
        """YES position → hedge should BUY NO (opposite direction)."""
        trade = _FakeTrade(
            market_id="market_yes",
            platform="polymarket",
            direction="yes",
            size_contracts=10,
            fill_price=0.60,
        )

        with (
            patch("hedge_executor._get_hedge_price", return_value=0.40),
            patch("polymarket_client.place_order", return_value=_FILLED_RESULT) as mock_place,
        ):
            result = execute_hedge(trade)

        # Verify direction is "no" and size is half
        call_args = mock_place.call_args
        assert call_args[0][1] == "no"   # direction
        assert call_args[0][2] == 5      # half of 10
        assert result["status"] == "filled"

    def test_no_trade_places_buy_yes(self):
        """NO position → hedge should BUY YES (opposite direction)."""
        trade = _FakeTrade(
            market_id="market_no",
            platform="polymarket",
            direction="no",
            size_contracts=8,
            fill_price=0.40,
        )

        with (
            patch("hedge_executor._get_hedge_price", return_value=0.60),
            patch("polymarket_client.place_order", return_value=_FILLED_RESULT) as mock_place,
        ):
            result = execute_hedge(trade)

        call_args = mock_place.call_args
        assert call_args[0][1] == "yes"  # direction
        assert call_args[0][2] == 4      # half of 8

    def test_hedge_size_rounds_down_to_minimum_one(self):
        """Single-contract position → hedge is 1 contract (max(1, 1//2) = 1)."""
        trade = _FakeTrade(
            market_id="market_small",
            platform="polymarket",
            direction="yes",
            size_contracts=1,
            fill_price=0.60,
        )

        with (
            patch("hedge_executor._get_hedge_price", return_value=0.40),
            patch("polymarket_client.place_order", return_value=_FILLED_RESULT) as mock_place,
        ):
            execute_hedge(trade)

        assert mock_place.call_args[0][2] == 1  # max(1, 1//2) = 1

    def test_hedge_size_is_half_rounded_down(self):
        """Odd position size → hedge rounds down."""
        trade = _FakeTrade(
            market_id="market_odd",
            platform="polymarket",
            direction="yes",
            size_contracts=7,
            fill_price=0.55,
        )

        with (
            patch("hedge_executor._get_hedge_price", return_value=0.45),
            patch("polymarket_client.place_order", return_value=_FILLED_RESULT) as mock_place,
        ):
            execute_hedge(trade)

        assert mock_place.call_args[0][2] == 3  # 7 // 2 = 3


# ---------------------------------------------------------------------------
# Platform Routing Tests
# ---------------------------------------------------------------------------

class TestHedgePlatformRouting:

    def test_polymarket_routes_to_polymarket_client(self):
        """Polymarket trade routes to polymarket_client.place_order."""
        trade = _FakeTrade(
            market_id="poly_market",
            platform="polymarket",
            direction="yes",
            size_contracts=6,
            fill_price=0.60,
        )

        with (
            patch("hedge_executor._get_hedge_price", return_value=0.40),
            patch("polymarket_client.place_order", return_value=_FILLED_RESULT) as mock_place,
        ):
            result = execute_hedge(trade, use_demo=True)

        mock_place.assert_called_once()
        assert result["status"] == "filled"

    def test_kalshi_routes_to_kalshi_client(self):
        """Kalshi trade routes to kalshi_client.place_order."""
        trade = _FakeTrade(
            market_id="KXBTC-25-T50000",
            platform="kalshi",
            direction="no",
            size_contracts=4,
            fill_price=0.35,
        )

        with (
            patch("hedge_executor._get_hedge_price", return_value=0.65),
            patch("kalshi_client.place_order", return_value=_FILLED_RESULT) as mock_place,
        ):
            result = execute_hedge(trade, use_demo=True)

        mock_place.assert_called_once()

    def test_unknown_platform_returns_declined(self):
        trade = _FakeTrade(
            market_id="unknown_market",
            platform="unknown",
            direction="yes",
            size_contracts=5,
            fill_price=0.55,
        )

        with patch("hedge_executor._get_hedge_price", return_value=0.45):
            result = execute_hedge(trade)

        assert result["status"] == "declined"
        assert "unknown_platform" in result.get("reason", "")


# ---------------------------------------------------------------------------
# Declined / Error Cases
# ---------------------------------------------------------------------------

class TestHedgeDeclined:

    def test_platform_declined_captured(self):
        """If platform declines hedge, result reflects declined status."""
        trade = _FakeTrade(
            market_id="market_dec",
            platform="polymarket",
            direction="yes",
            size_contracts=10,
            fill_price=0.60,
        )

        with (
            patch("hedge_executor._get_hedge_price", return_value=0.40),
            patch("polymarket_client.place_order", return_value=_DECLINED_RESULT),
        ):
            result = execute_hedge(trade)

        assert result["status"] == "declined"

    def test_accepts_dict_trade(self):
        """execute_hedge should accept plain dict trade records too."""
        trade_dict = {
            "market_id": "market_dict",
            "platform": "polymarket",
            "direction": "yes",
            "size_contracts": 4,
            "fill_price": 0.60,
            "trade_id": "trade_dict_01",
        }

        with (
            patch("hedge_executor._get_hedge_price", return_value=0.40),
            patch("polymarket_client.place_order", return_value=_FILLED_RESULT),
        ):
            result = execute_hedge(trade_dict)

        assert result["status"] == "filled"
