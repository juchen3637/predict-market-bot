"""
test_kalshi_client.py — Tests for skills/pm-risk/scripts/kalshi_client.py
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import kalshi_client as kc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_test_pem() -> str:
    """Generate a fresh RSA-2048 key in PEM format with literal \\n separators."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem_bytes.decode().replace("\n", "\\n")


_TEST_PEM = _generate_test_pem()

_CREDS = {
    "KALSHI_API_KEY": "test-key-id",
    "KALSHI_API_SECRET": _TEST_PEM,
}


def _mock_response(status_code: int, json_body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Credential gate
# ---------------------------------------------------------------------------

def test_missing_credentials_returns_declined(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)
    monkeypatch.delenv("KALSHI_API_SECRET", raising=False)

    result = kc.place_order("KXBTC-23-T45000", "yes", 10, 0.60)

    assert result["status"] == "declined"
    assert result["order_id"] is None
    assert result["fill_price"] is None


def test_empty_secret_returns_declined(monkeypatch):
    monkeypatch.setenv("KALSHI_API_KEY", "some-key")
    monkeypatch.setenv("KALSHI_API_SECRET", "")

    result = kc.place_order("KXBTC-23-T45000", "yes", 10, 0.60)

    assert result["status"] == "declined"


# ---------------------------------------------------------------------------
# status=="executed" → filled with avg_fill_price/100
# ---------------------------------------------------------------------------

def test_executed_status_maps_to_filled(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {
        "order": {
            "order_id": "kalshi-order-001",
            "status": "executed",
            "avg_price": 6500,  # cents
        }
    })

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.post.return_value = mock_resp
        result = kc.place_order("KXBTC-23-T45000", "yes", 10, 0.65, use_demo=True)

    assert result["status"] == "filled"
    assert result["order_id"] == "kalshi-order-001"
    assert result["fill_price"] == pytest.approx(65.0)


# ---------------------------------------------------------------------------
# status=="resting" → open
# ---------------------------------------------------------------------------

def test_resting_status_maps_to_open(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {
        "order": {
            "order_id": "kalshi-order-002",
            "status": "resting",
        }
    })

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.post.return_value = mock_resp
        result = kc.place_order("KXBTC-23-T45000", "yes", 5, 0.60, use_demo=True)

    assert result["status"] == "open"
    assert result["order_id"] == "kalshi-order-002"
    assert result["fill_price"] is None


# ---------------------------------------------------------------------------
# HTTP 4xx → declined with reason
# ---------------------------------------------------------------------------

def test_http_400_returns_declined(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(400, {"error": "invalid ticker"})
    mock_resp.text = "invalid ticker"

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.post.return_value = mock_resp
        result = kc.place_order("KXBTC-23-T45000", "yes", 10, 0.60, use_demo=True)

    assert result["status"] == "declined"
    assert "400" in result["reason"]


def test_http_500_returns_declined(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(500, {})
    mock_resp.text = "internal server error"

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.post.return_value = mock_resp
        result = kc.place_order("KXBTC-23-T45000", "yes", 10, 0.60, use_demo=True)

    assert result["status"] == "declined"
    assert "500" in result["reason"]


# ---------------------------------------------------------------------------
# Price cents conversion and clamping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("price,expected_cents", [
    (0.65, 65),
    (0.0, 1),    # clamped to min
    (1.0, 99),   # clamped to max
    (0.01, 1),   # clamped to min
    (0.99, 99),  # clamped to max
    (0.50, 50),
])
def test_price_cents_conversion(monkeypatch, price, expected_cents):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    captured = {}

    def fake_post(url, headers=None, json=None):
        captured["body"] = json
        return _mock_response(200, {"order": {"order_id": "x", "status": "resting"}})

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.post.side_effect = fake_post
        kc.place_order("TICKER", "yes", 1, price, use_demo=True)

    assert captured["body"]["yes_price"] == expected_cents


# ---------------------------------------------------------------------------
# Demo vs live URL selection
# ---------------------------------------------------------------------------

def test_demo_url_used_when_use_demo_true(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    captured_urls = []

    def fake_post(url, headers=None, json=None):
        captured_urls.append(url)
        return _mock_response(200, {"order": {"order_id": "x", "status": "resting"}})

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.post.side_effect = fake_post
        kc.place_order("TICKER", "yes", 1, 0.60, use_demo=True)

    assert captured_urls[0].startswith(kc.KALSHI_DEMO_URL)


def test_live_url_used_when_use_demo_false(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    captured_urls = []

    def fake_post(url, headers=None, json=None):
        captured_urls.append(url)
        return _mock_response(200, {"order": {"order_id": "x", "status": "resting"}})

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.post.side_effect = fake_post
        kc.place_order("TICKER", "yes", 1, 0.60, use_demo=False)

    assert captured_urls[0].startswith(kc.KALSHI_BASE_URL)


# ---------------------------------------------------------------------------
# get_depth — corrected direction semantics
#
# Kalshi's orderbook stores YES and NO BIDS (no offers). To BUY `direction` at
# limit L, the matching counterparty must be a bidder on the OPPOSITE side at
# price >= (1 - L), because BUY YES @ L = SELL NO @ (1 - L) and vice versa.
# ---------------------------------------------------------------------------

def test_get_depth_legacy_buy_yes_reads_no_bids_sufficient(monkeypatch):
    """BUY YES at 0.60 → cross_cents=40; sums NO bids at >= 40."""
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {
        "orderbook": {
            "no": [[45, 8], [40, 7], [35, 100]],  # 35 < cross 40, excluded
            "yes": [[65, 999]],                    # same-side bids must be ignored
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC-23-T45000", "yes", 0.60, 10, use_demo=True)

    assert result is True


def test_get_depth_legacy_buy_yes_reads_no_bids_insufficient(monkeypatch):
    """BUY YES at 0.60 with only 3 NO bids at >= 40 → False."""
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {
        "orderbook": {
            "no": [[45, 3]],
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC-23-T45000", "yes", 0.60, 10, use_demo=True)

    assert result is False


def test_get_depth_legacy_buy_no_reads_yes_bids(monkeypatch):
    """BUY NO at 0.30 → cross_cents=70; sums YES bids at >= 70."""
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {
        "orderbook": {
            "yes": [[75, 8], [70, 7], [65, 100]],  # 65 < cross 70, excluded
            "no": [[35, 999]],                      # same-side bids must be ignored
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC-23-T45000", "no", 0.30, 10, use_demo=True)

    assert result is True


def test_get_depth_legacy_ignores_same_side_bids(monkeypatch):
    """BUY NO at 0.10: a wall of NO bids must NOT be treated as fillable depth."""
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {
        "orderbook": {
            "no": [[5, 9999], [10, 9999], [50, 9999]],  # same side — irrelevant
            "yes": [],                                    # no actual cross-side liquidity
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXGDP-T2.5", "no", 0.10, 10, use_demo=True)

    assert result is False


def test_get_depth_fp_buy_no_reads_yes_dollars(monkeypatch):
    """orderbook_fp: BUY NO at 0.10 → cross_price=0.90; sum yes_dollars at >= 0.90."""
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    # yes_dollars qualifying: $4.75 + $9.00 = $13.75 at cross 0.90 → 15.27 contracts
    mock_resp = _mock_response(200, {
        "orderbook_fp": {
            "yes_dollars": [[0.95, 4.75], [0.90, 9.00], [0.85, 100.00]],  # 0.85 excluded
            "no_dollars":  [[0.50, 9999.0]],                                # ignored
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXGDP-T2.5", "no", 0.10, 10, use_demo=True)

    assert result is True


def test_get_depth_fp_buy_yes_reads_no_dollars(monkeypatch):
    """orderbook_fp: BUY YES at 0.30 → cross=0.70; sum no_dollars at >= 0.70."""
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    # no_dollars at 0.70 = $7.00 → 10 contracts at cross 0.70 → exactly enough
    mock_resp = _mock_response(200, {
        "orderbook_fp": {
            "no_dollars":  [[0.70, 7.00]],
            "yes_dollars": [[0.30, 9999.0]],  # ignored
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC", "yes", 0.30, 10, use_demo=True)

    assert result is True


def test_get_depth_fp_below_cross_price_excluded(monkeypatch):
    """orderbook_fp: levels with price < cross_price are not fillable at our limit."""
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    # BUY NO at 0.10 → cross=0.90; only 0.50 level → not >= 0.90 → 0 contracts
    mock_resp = _mock_response(200, {
        "orderbook_fp": {
            "yes_dollars": [[0.50, 100.0]],
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC", "no", 0.10, 10, use_demo=True)

    assert result is False


def test_get_depth_fp_empty_returns_false(monkeypatch):
    """Both sides empty → no liquidity → False."""
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC", "no", 0.10, 1, use_demo=True)

    assert result is False


def test_get_depth_limit_price_one_returns_false(monkeypatch):
    """Defensive: limit_price=1.0 → cross_price=0; refuse rather than divide by zero."""
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {"orderbook_fp": {"yes_dollars": [[0.0, 9999.0]]}})
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC", "no", 1.0, 1, use_demo=True)

    assert result is False


def test_get_depth_replay_kxgdp_thin_market_correctly_rejected(monkeypatch):
    """
    Replay of real KXGDP-26APR30-T2.5 snapshot from VPS rejection sample.

    yes_dollars=[], no_dollars=[(0.01,$1),(0.02,$1),(0.50,$20)]. Old buggy code
    summed no_dollars (wrong side) and either approved or rejected based on
    where limit fell against stale penny bids. New code reads yes_dollars at
    cross=0.90 → empty → reject. This is the *correct* answer: the market
    has zero cross-side liquidity at any reasonable BUY-NO limit.
    """
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {
        "orderbook_fp": {
            "yes_dollars": [],
            "no_dollars":  [[0.01, 1.0], [0.02, 1.0], [0.50, 20.0]],
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXGDP-26APR30-T2.5", "no", 0.10, 11, use_demo=True)

    assert result is False


def test_get_depth_replay_kxbtc_cross_side_has_liquidity(monkeypatch):
    """
    Inverse case — a BTC market with real cross-side liquidity should now pass.
    BUY NO at 0.10 → cross=0.90. yes_dollars at 0.95 carries enough to fill.
    """
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    # $20 at 0.95 → 21.05 contracts available; need 11 → True
    mock_resp = _mock_response(200, {
        "orderbook_fp": {
            "yes_dollars": [[0.95, 20.0]],
            "no_dollars":  [[0.86, 50.0], [0.98, 30.0]],  # ignored — same side
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC-26APR2317-T87249.99", "no", 0.10, 11, use_demo=True)

    assert result is True


# ---------------------------------------------------------------------------
# RSA signing — headers contain required keys and are non-empty
# ---------------------------------------------------------------------------

def test_rsa_signing_headers_present(monkeypatch):
    monkeypatch.setenv("KALSHI_API_KEY", "test-key-id")
    monkeypatch.setenv("KALSHI_API_SECRET", _TEST_PEM)

    headers = kc._kalshi_headers("POST", "/trade-api/v2/portfolio/orders")

    assert "KALSHI-ACCESS-KEY" in headers
    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"
    assert "KALSHI-ACCESS-TIMESTAMP" in headers
    assert "KALSHI-ACCESS-SIGNATURE" in headers
    assert len(headers["KALSHI-ACCESS-SIGNATURE"]) > 0


def test_rsa_signing_missing_key_raises(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)
    monkeypatch.delenv("KALSHI_API_SECRET", raising=False)

    with pytest.raises(EnvironmentError):
        kc._kalshi_headers("POST", "/trade-api/v2/portfolio/orders")


# ---------------------------------------------------------------------------
# direction "no" uses no_price field
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# get_order — all status paths
# ---------------------------------------------------------------------------

def test_get_order_missing_credentials_returns_unknown(monkeypatch):
    monkeypatch.delenv("KALSHI_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("KALSHI_DEMO_API_SECRET", raising=False)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", "")

    result = kc.get_order("order-123", use_demo=True)

    assert result["status"] == "unknown"
    assert result["fill_price"] is None


def test_get_order_resting_returns_resting(monkeypatch):
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {"order": {"order_id": "order-123", "status": "resting"}})

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_order("order-123", use_demo=True)

    assert result["status"] == "resting"
    assert result["fill_price"] is None


def test_get_order_executed_returns_filled_with_price(monkeypatch):
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {
        "order": {"order_id": "order-123", "status": "executed", "avg_price": 6500}
    })

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_order("order-123", use_demo=True)

    assert result["status"] == "filled"
    assert result["fill_price"] == pytest.approx(65.0)


def test_get_order_canceled_returns_canceled(monkeypatch):
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {"order": {"order_id": "order-123", "status": "canceled"}})

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_order("order-123", use_demo=True)

    assert result["status"] == "canceled"
    assert result["fill_price"] is None


def test_get_order_unknown_status_returns_unknown(monkeypatch):
    """Unexpected status values must NOT map to canceled — avoid wiping real positions."""
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    mock_resp = _mock_response(200, {"order": {"order_id": "order-123", "status": "partially_filled"}})

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_order("order-123", use_demo=True)

    assert result["status"] == "unknown"
    assert result["fill_price"] is None


def test_get_order_http_error_returns_unknown(monkeypatch):
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.side_effect = Exception("timeout")
        result = kc.get_order("order-123", use_demo=True)

    assert result["status"] == "unknown"
    assert result["fill_price"] is None


# ---------------------------------------------------------------------------
# direction "no" uses no_price field
# ---------------------------------------------------------------------------

def test_no_direction_uses_no_price_field(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_KEY", "test-key")
    monkeypatch.setattr(kc, "KALSHI_DEMO_API_SECRET", _TEST_PEM)

    captured = {}

    def fake_post(url, headers=None, json=None):
        captured["body"] = json
        return _mock_response(200, {"order": {"order_id": "x", "status": "resting"}})

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.post.side_effect = fake_post
        kc.place_order("TICKER", "no", 1, 0.40, use_demo=True)

    assert "no_price" in captured["body"]
    assert "yes_price" not in captured["body"]
    assert captured["body"]["no_price"] == 40
    assert captured["body"]["side"] == "no"
