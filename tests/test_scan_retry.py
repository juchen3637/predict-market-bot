"""
tests/test_scan_retry.py — Fix 5 tests for filter_markets._get_with_retry.

Covers:
  - _is_transient_http_error classification (DNS / connect / timeout / 5xx)
  - retries on httpx.ConnectError until success
  - retries on 5xx status until success
  - gives up after len(_RETRY_BACKOFFS) retries and re-raises
  - does NOT retry 4xx (client errors) or non-transient exceptions
  - Kalshi header_factory is called per attempt (fresh signature)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Make skills/pm-scan/scripts importable
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills" / "pm-scan" / "scripts")
)

import filter_markets  # noqa: E402


# ---------------------------------------------------------------------------
# _is_transient_http_error
# ---------------------------------------------------------------------------

class TestIsTransientHttpError:
    def test_connect_error_is_transient(self):
        assert filter_markets._is_transient_http_error(
            httpx.ConnectError("connect failed")
        ) is True

    def test_connect_timeout_is_transient(self):
        assert filter_markets._is_transient_http_error(
            httpx.ConnectTimeout("connect timeout")
        ) is True

    def test_read_timeout_is_transient(self):
        assert filter_markets._is_transient_http_error(
            httpx.ReadTimeout("read timeout")
        ) is True

    def test_dns_message_is_transient(self):
        # Plain RuntimeError with DNS text — classifier works by message too
        exc = RuntimeError("[Errno -3] Temporary failure in name resolution")
        assert filter_markets._is_transient_http_error(exc) is True

    def test_connection_refused_message_is_transient(self):
        exc = OSError("connection refused by host")
        assert filter_markets._is_transient_http_error(exc) is True

    def test_random_runtime_error_is_not_transient(self):
        assert filter_markets._is_transient_http_error(
            RuntimeError("some parse error")
        ) is False

    def test_key_error_is_not_transient(self):
        assert filter_markets._is_transient_http_error(KeyError("foo")) is False


# ---------------------------------------------------------------------------
# _get_with_retry — exception-path retry
# ---------------------------------------------------------------------------

class TestGetWithRetryOnException:
    def test_succeeds_on_third_attempt(self):
        client = MagicMock()
        good = MagicMock(status_code=200)
        good.raise_for_status.return_value = None
        client.get.side_effect = [
            httpx.ConnectError("boom 1"),
            httpx.ConnectError("boom 2"),
            good,
        ]
        with patch.object(filter_markets.time, "sleep") as sleep:
            resp = filter_markets._get_with_retry(
                client, "http://x/y", params={"a": 1}, label="test",
            )
        assert resp is good
        assert client.get.call_count == 3
        # Two retries → two sleeps with 2s / 6s
        assert [c.args[0] for c in sleep.call_args_list] == [2.0, 6.0]

    def test_raises_after_max_retries(self):
        client = MagicMock()
        client.get.side_effect = httpx.ConnectError("dead")
        with patch.object(filter_markets.time, "sleep"):
            with pytest.raises(httpx.ConnectError):
                filter_markets._get_with_retry(
                    client, "http://x/y", label="test",
                )
        # initial + 3 retries = 4 attempts
        assert client.get.call_count == 4

    def test_does_not_retry_non_transient(self):
        client = MagicMock()
        client.get.side_effect = ValueError("bad arg — not network")
        with patch.object(filter_markets.time, "sleep") as sleep:
            with pytest.raises(ValueError):
                filter_markets._get_with_retry(
                    client, "http://x/y", label="test",
                )
        assert client.get.call_count == 1
        sleep.assert_not_called()


# ---------------------------------------------------------------------------
# _get_with_retry — 5xx status-path retry
# ---------------------------------------------------------------------------

class TestGetWithRetryOnStatus:
    def _resp(self, code: int):
        r = MagicMock(status_code=code)
        if code >= 500:
            r.raise_for_status.side_effect = httpx.HTTPStatusError(
                "5xx", request=MagicMock(), response=r
            )
        else:
            r.raise_for_status.return_value = None
        return r

    def test_retries_on_503_then_succeeds(self):
        client = MagicMock()
        client.get.side_effect = [self._resp(503), self._resp(503), self._resp(200)]
        with patch.object(filter_markets.time, "sleep"):
            resp = filter_markets._get_with_retry(
                client, "http://x/y", label="test",
            )
        assert resp.status_code == 200
        assert client.get.call_count == 3

    def test_retries_on_502_then_504_then_succeeds(self):
        client = MagicMock()
        client.get.side_effect = [self._resp(502), self._resp(504), self._resp(200)]
        with patch.object(filter_markets.time, "sleep"):
            resp = filter_markets._get_with_retry(
                client, "http://x/y", label="test",
            )
        assert resp.status_code == 200
        assert client.get.call_count == 3

    def test_does_not_retry_on_404(self):
        """Client errors should surface immediately, not be silently retried."""
        r404 = MagicMock(status_code=404)
        r404.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=r404
        )
        client = MagicMock()
        client.get.return_value = r404
        with patch.object(filter_markets.time, "sleep") as sleep:
            with pytest.raises(httpx.HTTPStatusError):
                filter_markets._get_with_retry(
                    client, "http://x/y", label="test",
                )
        assert client.get.call_count == 1
        sleep.assert_not_called()

    def test_exhausted_5xx_retries_raise(self):
        client = MagicMock()
        client.get.side_effect = [self._resp(503)] * 4
        with patch.object(filter_markets.time, "sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                filter_markets._get_with_retry(
                    client, "http://x/y", label="test",
                )
        # initial + 3 retries = 4 attempts
        assert client.get.call_count == 4


# ---------------------------------------------------------------------------
# header_factory — Kalshi needs a fresh signature per attempt
# ---------------------------------------------------------------------------

class TestHeaderFactory:
    def test_header_factory_called_each_attempt(self):
        client = MagicMock()
        good = MagicMock(status_code=200)
        good.raise_for_status.return_value = None
        client.get.side_effect = [
            httpx.ConnectError("boom"),
            httpx.ConnectError("boom"),
            good,
        ]
        call_count = {"n": 0}

        def make_headers():
            call_count["n"] += 1
            return {"SIG": f"sig-{call_count['n']}"}

        with patch.object(filter_markets.time, "sleep"):
            filter_markets._get_with_retry(
                client, "http://x/y",
                header_factory=make_headers,
                label="kalshi",
            )
        # One call per attempt (3 total)
        assert call_count["n"] == 3
        # Third attempt used sig-3
        assert client.get.call_args_list[-1].kwargs["headers"] == {"SIG": "sig-3"}

    def test_no_header_factory_passes_headers_through(self):
        client = MagicMock()
        r = MagicMock(status_code=200)
        r.raise_for_status.return_value = None
        client.get.return_value = r
        filter_markets._get_with_retry(
            client, "http://x/y",
            headers={"X-Key": "abc"},
            label="test",
        )
        client.get.assert_called_once()
        assert client.get.call_args.kwargs["headers"] == {"X-Key": "abc"}
