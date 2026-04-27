"""
test_polymarket_client.py — Tests for skills/pm-risk/scripts/polymarket_client.py
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import polymarket_client as pc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREDS = {
    "POLYMARKET_API_KEY": "test-api-key",
    "POLYMARKET_API_SECRET": "test-api-secret",
    "POLYMARKET_API_PASSPHRASE": "test-passphrase",
    "POLYMARKET_WALLET_PRIVATE_KEY": "0xdeadbeef",
}


def _make_book(asks=None, bids=None):
    """Build a mock OrderBookSummary."""
    book = MagicMock()
    book.asks = asks or []
    book.bids = bids or []
    return book


def _make_level(price: str, size: str):
    level = MagicMock()
    level.price = price
    level.size = size
    return level


# ---------------------------------------------------------------------------
# Credential gate
# ---------------------------------------------------------------------------

def test_missing_credentials_returns_declined(monkeypatch):
    for key in _CREDS:
        monkeypatch.delenv(key, raising=False)

    result = pc.place_order("token-123", "yes", 10, 0.60)

    assert result["status"] == "declined"
    assert result["order_id"] is None
    assert result["fill_price"] is None


def test_partial_credentials_returns_declined(monkeypatch):
    monkeypatch.setenv("POLYMARKET_API_KEY", "key")
    # Others missing
    for k in ["POLYMARKET_API_SECRET", "POLYMARKET_API_PASSPHRASE", "POLYMARKET_WALLET_PRIVATE_KEY"]:
        monkeypatch.delenv(k, raising=False)

    result = pc.place_order("token-123", "yes", 10, 0.60)

    assert result["status"] == "declined"


# ---------------------------------------------------------------------------
# Successful MATCHED response → filled
# ---------------------------------------------------------------------------

def test_matched_response_maps_to_filled(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_client = MagicMock()
    mock_client.create_and_post_order.return_value = {
        "orderID": "order-abc",
        "status": "MATCHED",
    }

    with patch.object(pc, "_get_client", return_value=mock_client):
        result = pc.place_order("token-123", "yes", 10, 0.60)

    assert result["status"] == "filled"
    assert result["order_id"] == "order-abc"
    assert result["fill_price"] == pytest.approx(0.60)


# ---------------------------------------------------------------------------
# LIVE response → open
# ---------------------------------------------------------------------------

def test_live_response_maps_to_open(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_client = MagicMock()
    mock_client.create_and_post_order.return_value = {
        "orderID": "order-xyz",
        "status": "LIVE",
    }

    with patch.object(pc, "_get_client", return_value=mock_client):
        result = pc.place_order("token-123", "yes", 5, 0.45)

    assert result["status"] == "open"
    assert result["order_id"] == "order-xyz"
    assert result["fill_price"] is None


# ---------------------------------------------------------------------------
# API exception → declined with reason
# ---------------------------------------------------------------------------

def test_api_exception_returns_declined(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_client = MagicMock()
    mock_client.create_and_post_order.side_effect = RuntimeError("connection timeout")

    with patch.object(pc, "_get_client", return_value=mock_client):
        result = pc.place_order("token-123", "yes", 10, 0.60)

    assert result["status"] == "declined"
    assert "connection timeout" in result["reason"]


# ---------------------------------------------------------------------------
# get_depth — sufficient liquidity → True
# ---------------------------------------------------------------------------

def test_get_depth_sufficient_returns_true(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_client = MagicMock()
    # 3 ask levels at prices <= 0.65, each with size 5 → total 15 >= 10 requested
    mock_client.get_order_book.return_value = _make_book(
        asks=[
            _make_level("0.60", "5"),
            _make_level("0.63", "5"),
            _make_level("0.65", "5"),
            _make_level("0.70", "100"),  # above limit, not counted
        ]
    )

    with patch.object(pc, "_get_client", return_value=mock_client):
        result = pc.get_depth("token-123", "yes", 0.65, 10)

    assert result is True


# ---------------------------------------------------------------------------
# get_depth — insufficient liquidity → False
# ---------------------------------------------------------------------------

def test_get_depth_insufficient_returns_false(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_client = MagicMock()
    # Only 3 contracts available at limit, need 10
    mock_client.get_order_book.return_value = _make_book(
        asks=[_make_level("0.60", "3")]
    )

    with patch.object(pc, "_get_client", return_value=mock_client):
        result = pc.get_depth("token-123", "yes", 0.65, 10)

    assert result is False


# ---------------------------------------------------------------------------
# get_depth — direction "no" uses bids side
# ---------------------------------------------------------------------------

def test_get_depth_no_direction_uses_bids(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_client = MagicMock()
    # limit_price=0.40 → threshold = 1 - 0.40 = 0.60
    # bids at 0.60 and 0.65 (>= 0.60) → total 15
    mock_client.get_order_book.return_value = _make_book(
        bids=[
            _make_level("0.65", "8"),
            _make_level("0.60", "7"),
            _make_level("0.55", "100"),  # below threshold, not counted
        ]
    )

    with patch.object(pc, "_get_client", return_value=mock_client):
        result = pc.get_depth("token-123", "no", 0.40, 10)

    assert result is True


# ---------------------------------------------------------------------------
# get_depth — missing credentials → True (pass-through)
# ---------------------------------------------------------------------------

def test_get_depth_missing_credentials_returns_true(monkeypatch):
    for key in _CREDS:
        monkeypatch.delenv(key, raising=False)

    result = pc.get_depth("token-123", "yes", 0.60, 10)

    assert result is True


# ---------------------------------------------------------------------------
# get_orderbook_snapshot — normalized {asks, bids} as (price, size)
# ---------------------------------------------------------------------------

def test_get_orderbook_snapshot_returns_normalized_levels(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_client = MagicMock()
    mock_client.get_order_book.return_value = _make_book(
        asks=[_make_level("0.60", "5"), _make_level("0.65", "8")],
        bids=[_make_level("0.35", "10")],
    )

    with patch.object(pc, "_get_client", return_value=mock_client):
        snap = pc.get_orderbook_snapshot("token-123")

    assert snap == {
        "asks": [(0.60, 5.0), (0.65, 8.0)],
        "bids": [(0.35, 10.0)],
    }


def test_get_orderbook_snapshot_missing_credentials_returns_empty(monkeypatch):
    for key in _CREDS:
        monkeypatch.delenv(key, raising=False)

    snap = pc.get_orderbook_snapshot("token-123")

    assert snap == {"asks": [], "bids": []}


def test_get_orderbook_snapshot_clob_error_raises(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_client = MagicMock()
    mock_client.get_order_book.side_effect = Exception("clob unavailable")

    with patch.object(pc, "_get_client", return_value=mock_client):
        with pytest.raises(Exception, match="clob unavailable"):
            pc.get_orderbook_snapshot("token-123")


def test_get_orderbook_snapshot_empty_book_returns_empty_lists(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_client = MagicMock()
    mock_client.get_order_book.return_value = _make_book()  # no asks, no bids

    with patch.object(pc, "_get_client", return_value=mock_client):
        snap = pc.get_orderbook_snapshot("token-123")

    assert snap == {"asks": [], "bids": []}
