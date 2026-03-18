"""
test_order_poller.py — Tests for order_poller.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add skill scripts to path
_RISK_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pm-risk" / "scripts"
if str(_RISK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RISK_SCRIPTS))

from order_poller import poll_until_filled


# ---------------------------------------------------------------------------
# Polymarket Polling Tests
# ---------------------------------------------------------------------------

class TestPollPolymarket:
    """Tests for Polymarket order polling."""

    def test_already_filled_on_first_poll(self):
        """Order is MATCHED on first poll — returns filled immediately."""
        mock_client = MagicMock()
        mock_client.get_order.return_value = {
            "status": "MATCHED",
            "avg_price": 0.65,
        }

        with patch("polymarket_client._get_client", return_value=mock_client):
            result = poll_until_filled(
                order_id="pm_order_001",
                platform="polymarket",
                market_id="market_abc",
                timeout_seconds=300,
                interval_seconds=1,
            )

        assert result["status"] == "filled"
        assert result["order_id"] == "pm_order_001"
        assert result["fill_price"] == 0.65
        assert mock_client.get_order.call_count == 1

    def test_fills_on_second_poll(self):
        """Order is LIVE on first poll, MATCHED on second — returns filled."""
        mock_client = MagicMock()
        mock_client.get_order.side_effect = [
            {"status": "LIVE"},
            {"status": "MATCHED", "avg_price": 0.55},
        ]

        with (
            patch("polymarket_client._get_client", return_value=mock_client),
            patch("order_poller.time.sleep"),
        ):
            result = poll_until_filled(
                order_id="pm_order_002",
                platform="polymarket",
                market_id="market_abc",
                timeout_seconds=300,
                interval_seconds=30,
            )

        assert result["status"] == "filled"
        assert result["fill_price"] == 0.55
        assert mock_client.get_order.call_count == 2

    def test_times_out_attempts_cancel(self):
        """Order stays LIVE until timeout — attempts cancel, returns timed_out."""
        mock_client = MagicMock()
        mock_client.get_order.return_value = {"status": "LIVE"}
        mock_client.cancel.return_value = {"success": True}

        with (
            patch("polymarket_client._get_client", return_value=mock_client),
            patch("order_poller.time.sleep"),
        ):
            result = poll_until_filled(
                order_id="pm_order_003",
                platform="polymarket",
                market_id="market_abc",
                timeout_seconds=60,
                interval_seconds=30,
            )

        assert result["status"] == "timed_out"
        assert result["fill_price"] is None
        mock_client.cancel.assert_called_once_with("pm_order_003")

    def test_cancelled_by_exchange(self):
        """Exchange cancels the order mid-poll — returns cancelled."""
        mock_client = MagicMock()
        mock_client.get_order.return_value = {"status": "CANCELED"}

        with (
            patch("polymarket_client._get_client", return_value=mock_client),
            patch("order_poller.time.sleep"),
        ):
            result = poll_until_filled(
                order_id="pm_order_004",
                platform="polymarket",
                market_id="market_abc",
                timeout_seconds=300,
                interval_seconds=30,
            )

        assert result["status"] == "cancelled"
        assert result["fill_price"] is None

    def test_no_credentials_returns_declined(self):
        """If credentials are missing, returns declined immediately."""
        with patch("polymarket_client._get_client", return_value=None):
            result = poll_until_filled(
                order_id="pm_order_005",
                platform="polymarket",
                market_id="market_abc",
            )

        assert result["status"] == "declined"


# ---------------------------------------------------------------------------
# Kalshi Polling Tests
# ---------------------------------------------------------------------------

class TestPollKalshi:
    """Tests for Kalshi order polling."""

    def _make_mock_response(self, status: str, avg_price: int | None = None) -> MagicMock:
        order: dict = {"status": status}
        if avg_price is not None:
            order["avg_price"] = avg_price
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"order": order}
        return mock_resp

    def _make_http_client(self, get_responses) -> MagicMock:
        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value=mock_http)
        mock_http.__exit__ = MagicMock(return_value=False)
        if isinstance(get_responses, list):
            mock_http.get.side_effect = get_responses
        else:
            mock_http.get.return_value = get_responses
        mock_http.delete.return_value = MagicMock(status_code=200)
        return mock_http

    def test_already_filled_on_first_poll(self):
        """Order is executed on first poll — returns filled."""
        mock_http = self._make_http_client(self._make_mock_response("executed", avg_price=65))

        with (
            patch("kalshi_client._get_headers", return_value={"KALSHI-ACCESS-KEY": "test"}),
            patch("httpx.Client", return_value=mock_http),
            patch("order_poller.time.sleep"),
        ):
            result = poll_until_filled(
                order_id="kalshi_order_001",
                platform="kalshi",
                market_id="KXBTC-25-T50000",
                timeout_seconds=300,
                interval_seconds=30,
            )

        assert result["status"] == "filled"
        assert result["fill_price"] == 0.65

    def test_fills_on_second_poll(self):
        """Order is resting on first poll, executed on second."""
        mock_http = self._make_http_client([
            self._make_mock_response("resting"),
            self._make_mock_response("executed", avg_price=55),
        ])

        with (
            patch("kalshi_client._get_headers", return_value={"KALSHI-ACCESS-KEY": "test"}),
            patch("httpx.Client", return_value=mock_http),
            patch("order_poller.time.sleep"),
        ):
            result = poll_until_filled(
                order_id="kalshi_order_002",
                platform="kalshi",
                market_id="KXBTC-25-T50000",
                timeout_seconds=300,
                interval_seconds=30,
            )

        assert result["status"] == "filled"
        assert result["fill_price"] == 0.55

    def test_times_out_attempts_cancel(self):
        """Kalshi order times out — attempts DELETE cancel, returns timed_out."""
        mock_http = self._make_http_client(self._make_mock_response("resting"))

        with (
            patch("kalshi_client._get_headers", return_value={"KALSHI-ACCESS-KEY": "test"}),
            patch("httpx.Client", return_value=mock_http),
            patch("order_poller.time.sleep"),
        ):
            result = poll_until_filled(
                order_id="kalshi_order_003",
                platform="kalshi",
                market_id="KXBTC-25-T50000",
                timeout_seconds=60,
                interval_seconds=30,
            )

        assert result["status"] == "timed_out"
        mock_http.delete.assert_called_once()

    def test_cancelled_by_exchange(self):
        """Kalshi cancels the order — returns cancelled."""
        mock_http = self._make_http_client(self._make_mock_response("canceled"))

        with (
            patch("kalshi_client._get_headers", return_value={"KALSHI-ACCESS-KEY": "test"}),
            patch("httpx.Client", return_value=mock_http),
            patch("order_poller.time.sleep"),
        ):
            result = poll_until_filled(
                order_id="kalshi_order_004",
                platform="kalshi",
                market_id="KXBTC-25-T50000",
                timeout_seconds=300,
                interval_seconds=30,
            )

        assert result["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Unknown platform
# ---------------------------------------------------------------------------

def test_unknown_platform_returns_declined():
    result = poll_until_filled(
        order_id="unknown_001",
        platform="unknown_exchange",
        market_id="market_xyz",
    )
    assert result["status"] == "declined"
