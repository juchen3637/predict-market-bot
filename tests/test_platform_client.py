"""
tests/test_platform_client.py — Unit tests for platform_client.py

Tests resolution queries and bulk market fetching using mocked httpx calls.
No real API calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# platform_client is in skills/pm-compound/scripts — add to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "skills" / "pm-compound" / "scripts"))

import platform_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, body: dict | list) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status.return_value = None
    return resp


def _mock_response_error(status_code: int = 500) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


# ---------------------------------------------------------------------------
# get_market_resolution — Polymarket
# ---------------------------------------------------------------------------

class TestPolymarketResolution:
    def test_resolved_yes(self):
        body = {
            "resolved": True,
            "outcomePrices": ["1", "0"],
            "resolutionTime": "2024-01-15T12:00:00Z",
        }
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = _mock_response(200, body)

            result = platform_client.get_market_resolution("mkt_abc", "polymarket")

        assert result is not None
        assert result["resolved"] is True
        assert result["outcome"] == "yes"
        assert result["resolved_at"] == "2024-01-15T12:00:00Z"

    def test_resolved_no(self):
        body = {
            "resolved": True,
            "outcomePrices": ["0", "1"],
            "resolutionTime": "2024-02-01T00:00:00Z",
        }
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = _mock_response(200, body)

            result = platform_client.get_market_resolution("mkt_xyz", "polymarket")

        assert result["resolved"] is True
        assert result["outcome"] == "no"

    def test_unresolved(self):
        body = {
            "resolved": False,
            "outcomePrices": ["0.6", "0.4"],
            "resolutionTime": None,
        }
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = _mock_response(200, body)

            result = platform_client.get_market_resolution("mkt_open", "polymarket")

        assert result["resolved"] is False
        assert result["outcome"] is None

    def test_resolved_no_definitive_outcome(self):
        """Resolved=True but prices not yet finalized to 0 or 1."""
        body = {
            "resolved": True,
            "outcomePrices": ["0.7", "0.3"],
            "resolutionTime": "2024-03-01T00:00:00Z",
        }
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = _mock_response(200, body)

            result = platform_client.get_market_resolution("mkt_odd", "polymarket")

        assert result["resolved"] is True
        assert result["outcome"] is None

    def test_http_error_returns_none(self):
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.side_effect = Exception("Connection refused")

            result = platform_client.get_market_resolution("mkt_err", "polymarket")

        assert result is None

    def test_json_string_outcome_prices(self):
        """outcomePrices may arrive as a JSON string."""
        body = {
            "resolved": True,
            "outcomePrices": '["1", "0"]',
            "resolutionTime": "2024-01-01T00:00:00Z",
        }
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = _mock_response(200, body)

            result = platform_client.get_market_resolution("mkt_str", "polymarket")

        assert result["outcome"] == "yes"


# ---------------------------------------------------------------------------
# get_market_resolution — Kalshi
# ---------------------------------------------------------------------------

class TestKalshiResolution:
    @patch.dict("os.environ", {
        "KALSHI_API_KEY": "test-key",
        "KALSHI_API_SECRET": "",
    })
    def test_settled_yes(self):
        body = {"market": {"status": "settled", "result": "yes", "close_time": "2024-01-20T18:00:00Z"}}
        with patch("platform_client._kalshi_headers", return_value={}):
            with patch("platform_client.httpx.Client") as mock_client_cls:
                mock_client = mock_client_cls.return_value.__enter__.return_value
                mock_client.get.return_value = _mock_response(200, body)

                result = platform_client.get_market_resolution("KXBTC-24JAN20-T50K", "kalshi")

        assert result is not None
        assert result["resolved"] is True
        assert result["outcome"] == "yes"
        assert result["resolved_at"] == "2024-01-20T18:00:00Z"

    @patch.dict("os.environ", {"KALSHI_API_KEY": "k", "KALSHI_API_SECRET": ""})
    def test_settled_no(self):
        body = {"market": {"status": "settled", "result": "no", "close_time": "2024-02-15T00:00:00Z"}}
        with patch("platform_client._kalshi_headers", return_value={}):
            with patch("platform_client.httpx.Client") as mock_client_cls:
                mock_client = mock_client_cls.return_value.__enter__.return_value
                mock_client.get.return_value = _mock_response(200, body)

                result = platform_client.get_market_resolution("KXFED-24FEB15", "kalshi")

        assert result["resolved"] is True
        assert result["outcome"] == "no"

    @patch.dict("os.environ", {"KALSHI_API_KEY": "k", "KALSHI_API_SECRET": ""})
    def test_open_market_not_resolved(self):
        body = {"market": {"status": "open", "result": "", "close_time": None}}
        with patch("platform_client._kalshi_headers", return_value={}):
            with patch("platform_client.httpx.Client") as mock_client_cls:
                mock_client = mock_client_cls.return_value.__enter__.return_value
                mock_client.get.return_value = _mock_response(200, body)

                result = platform_client.get_market_resolution("KXCPI-24MAR", "kalshi")

        assert result["resolved"] is False
        assert result["outcome"] is None

    def test_kalshi_error_returns_none(self):
        with patch("platform_client._kalshi_headers", side_effect=EnvironmentError("no creds")):
            result = platform_client.get_market_resolution("some-market", "kalshi")

        assert result is None

    def test_unknown_platform_returns_none(self):
        result = platform_client.get_market_resolution("some-id", "unknown_platform")
        assert result is None


# ---------------------------------------------------------------------------
# fetch_resolved_markets
# ---------------------------------------------------------------------------

class TestFetchResolvedMarkets:
    def test_polymarket_returns_list(self):
        markets = [{"conditionId": f"cid_{i}", "resolved": True} for i in range(5)]
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            # First page returns 5 items (< page_size=100 → stop)
            mock_client.get.return_value = _mock_response(200, markets)

            result = platform_client.fetch_resolved_markets("polymarket", limit=200)

        assert isinstance(result, list)
        assert len(result) == 5

    def test_polymarket_paginates(self):
        page = [{"conditionId": f"cid_{i}"} for i in range(100)]
        last_page = [{"conditionId": f"last_{i}"} for i in range(50)]
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.side_effect = [
                _mock_response(200, page),
                _mock_response(200, last_page),
            ]

            result = platform_client.fetch_resolved_markets("polymarket", limit=200)

        assert len(result) == 150

    def test_polymarket_respects_limit(self):
        page = [{"conditionId": f"cid_{i}"} for i in range(100)]
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = _mock_response(200, page)

            result = platform_client.fetch_resolved_markets("polymarket", limit=50)

        assert len(result) == 50

    @patch.dict("os.environ", {"KALSHI_API_KEY": "k", "KALSHI_API_SECRET": ""})
    def test_kalshi_returns_list(self):
        markets_body = {"markets": [{"ticker": "KXBTC-t1", "status": "settled"}]}
        with patch("platform_client._kalshi_headers", return_value={}):
            with patch("platform_client.httpx.Client") as mock_client_cls:
                mock_client = mock_client_cls.return_value.__enter__.return_value
                mock_client.get.return_value = _mock_response(200, markets_body)

                result = platform_client.fetch_resolved_markets("kalshi", limit=20)

        assert isinstance(result, list)

    def test_unknown_platform_returns_empty(self):
        result = platform_client.fetch_resolved_markets("fakebook", limit=100)
        assert result == []

    def test_polymarket_http_error_returns_partial(self):
        """Error on first page returns empty list gracefully."""
        with patch("platform_client.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.side_effect = Exception("timeout")

            result = platform_client.fetch_resolved_markets("polymarket", limit=100)

        assert result == []
