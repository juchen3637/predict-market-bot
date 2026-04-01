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

    captured_urls = []

    def fake_post(url, headers=None, json=None):
        captured_urls.append(url)
        return _mock_response(200, {"order": {"order_id": "x", "status": "resting"}})

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.post.side_effect = fake_post
        kc.place_order("TICKER", "yes", 1, 0.60, use_demo=False)

    assert captured_urls[0].startswith(kc.KALSHI_BASE_URL)


# ---------------------------------------------------------------------------
# get_depth — sufficient → True
# ---------------------------------------------------------------------------

def test_get_depth_sufficient_returns_true(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    # limit_price=0.60 → limit_cents=60; levels at 65 and 60 both >= 60
    mock_resp = _mock_response(200, {
        "orderbook": {
            "yes": [[65, 8], [60, 7], [55, 100]],  # 55 is below, not counted
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC-23-T45000", "yes", 0.60, 10, use_demo=True)

    assert result is True


# ---------------------------------------------------------------------------
# get_depth — insufficient → False
# ---------------------------------------------------------------------------

def test_get_depth_insufficient_returns_false(monkeypatch):
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)

    mock_resp = _mock_response(200, {
        "orderbook": {
            "yes": [[65, 3]],  # only 3, need 10
        }
    })
    mock_resp.raise_for_status = MagicMock()

    with patch("kalshi_client.httpx.Client") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.get.return_value = mock_resp
        result = kc.get_depth("KXBTC-23-T45000", "yes", 0.60, 10, use_demo=True)

    assert result is False


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
