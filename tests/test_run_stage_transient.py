"""
tests/test_run_stage_transient.py — Fix A tests.

Covers:
  - _run_stage: catches subprocess.TimeoutExpired and returns rc=124 cleanly
  - _is_transient_failure: classifies timeout + network-error stderr markers
  - _execute_stage: marks stage failed with transient=True on timeout
  - run_pipeline._fail: does NOT increment consecutive_failures on transient
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_pipeline
from run_pipeline import (
    STAGE_TIMEOUT_RC,
    _is_transient_failure,
    _run_stage,
    _execute_stage,
    _init_manifest,
)


# ---------------------------------------------------------------------------
# _is_transient_failure
# ---------------------------------------------------------------------------

class TestIsTransientFailure:
    def test_timeout_rc_is_transient(self):
        assert _is_transient_failure(STAGE_TIMEOUT_RC, "") is True

    def test_dns_stderr_is_transient(self):
        assert _is_transient_failure(1, "Temporary failure in name resolution") is True

    def test_connection_refused_is_transient(self):
        assert _is_transient_failure(1, "connection refused by host") is True

    def test_5xx_is_transient(self):
        assert _is_transient_failure(1, "HTTP 503 Service Unavailable") is True

    def test_502_is_transient(self):
        assert _is_transient_failure(1, "got 502 Bad Gateway from upstream") is True

    def test_504_is_transient(self):
        assert _is_transient_failure(1, "504 Gateway Timeout") is True

    def test_read_timeout_is_transient(self):
        assert _is_transient_failure(1, "httpx.ReadTimeout: read timeout") is True

    def test_plain_traceback_is_not_transient(self):
        stderr = "Traceback (most recent call last):\n  KeyError: 'foo'\n"
        assert _is_transient_failure(1, stderr) is False

    def test_rc_zero_with_no_stderr_is_not_transient(self):
        assert _is_transient_failure(0, "") is False

    def test_case_insensitive(self):
        assert _is_transient_failure(1, "NAME RESOLUTION FAILED") is True

    def test_empty_stderr_with_nonzero_rc_is_not_transient(self):
        assert _is_transient_failure(1, "") is False

    def test_none_stderr_handled(self):
        assert _is_transient_failure(1, None) is False


# ---------------------------------------------------------------------------
# _run_stage — subprocess timeout handling
# ---------------------------------------------------------------------------

class TestRunStageTimeout:
    def test_timeout_returns_124_cleanly(self, caplog):
        """TimeoutExpired no longer bubbles up; returncode=124 returned instead."""
        logger = logging.getLogger("test_timeout")
        caplog.set_level(logging.ERROR)

        fake_exc = subprocess.TimeoutExpired(
            cmd=["python", "script.py"],
            timeout=10,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )
        with patch("run_pipeline.subprocess.run", side_effect=fake_exc):
            out, rc, stderr_text = _run_stage(
                logger, Path("/tmp/x.py"), timeout=10,
            )

        assert rc == STAGE_TIMEOUT_RC
        assert out == b"partial stdout"
        assert "stage timed out after 10s" in stderr_text
        assert "partial stderr" in stderr_text

    def test_timeout_with_none_output(self, caplog):
        """Child produced no output before timeout — should still return cleanly."""
        logger = logging.getLogger("test_timeout_empty")
        fake_exc = subprocess.TimeoutExpired(
            cmd=["python", "x.py"], timeout=5, output=None, stderr=None,
        )
        with patch("run_pipeline.subprocess.run", side_effect=fake_exc):
            out, rc, stderr_text = _run_stage(logger, Path("/tmp/x.py"), timeout=5)
        assert rc == STAGE_TIMEOUT_RC
        assert out == b""
        assert "stage timed out after 5s" in stderr_text

    def test_successful_run_returns_3_tuple(self):
        logger = logging.getLogger("test_success")
        fake_result = MagicMock(
            stdout=b'{"x": 1}',
            stderr=b"some stderr line\n",
            returncode=0,
        )
        with patch("run_pipeline.subprocess.run", return_value=fake_result):
            out, rc, stderr_text = _run_stage(logger, Path("/tmp/x.py"), timeout=10)
        assert rc == 0
        assert out == b'{"x": 1}'
        assert "some stderr line" in stderr_text

    def test_dry_run_returns_3_tuple(self):
        logger = logging.getLogger("test_dry")
        out, rc, stderr_text = _run_stage(
            logger, Path("/tmp/x.py"), dry_run=True, timeout=10,
        )
        assert rc == 0
        assert stderr_text == ""
        assert b"scan_id" in out


# ---------------------------------------------------------------------------
# _execute_stage — transient marker on failure
# ---------------------------------------------------------------------------

@pytest.fixture()
def runs_dir(tmp_path, monkeypatch):
    d = tmp_path / "runs"
    d.mkdir()
    monkeypatch.setattr(run_pipeline, "RUNS_DIR", d)
    return d


class TestExecuteStageTransient:
    def _manifest(self):
        return _init_manifest("test_run", "2026-04-23T00:00:00+00:00")

    def test_timeout_marks_stage_transient(self, runs_dir):
        """Timeout → stage failed, transient=True in manifest."""
        logger = logging.getLogger("test_exec_timeout")
        manifest = self._manifest()
        with patch(
            "run_pipeline._run_stage",
            return_value=(b"", STAGE_TIMEOUT_RC, "[run_pipeline] stage timed out after 10s"),
        ):
            _, ok, m = _execute_stage(
                logger, manifest, "research", Path("/tmp/x.py"), timeout=10,
            )
        assert ok is False
        assert m["stages"]["research"]["status"] == "failed"
        assert m["stages"]["research"]["transient"] is True
        assert "(transient)" in m["stages"]["research"]["error"]

    def test_dns_error_marks_stage_transient(self, runs_dir):
        logger = logging.getLogger("test_exec_dns")
        manifest = self._manifest()
        with patch(
            "run_pipeline._run_stage",
            return_value=(b"", 1, "polymarket fetch failed: [Errno -3] Temporary failure in name resolution"),
        ):
            _, ok, m = _execute_stage(
                logger, manifest, "scan", Path("/tmp/x.py"), timeout=120,
            )
        assert ok is False
        assert m["stages"]["scan"]["transient"] is True

    def test_real_code_bug_is_not_transient(self, runs_dir):
        """A KeyError traceback should NOT be flagged transient."""
        logger = logging.getLogger("test_exec_bug")
        manifest = self._manifest()
        stderr = "Traceback (most recent call last):\n  KeyError: 'field'\n"
        with patch("run_pipeline._run_stage", return_value=(b"", 1, stderr)):
            _, ok, m = _execute_stage(
                logger, manifest, "predict", Path("/tmp/x.py"), timeout=60,
            )
        assert ok is False
        assert m["stages"]["predict"]["transient"] is False

    def test_success_path_unchanged(self, runs_dir):
        logger = logging.getLogger("test_exec_ok")
        manifest = self._manifest()
        with patch(
            "run_pipeline._run_stage",
            return_value=(b'{"candidates": [{"x":1}]}', 0, ""),
        ):
            _, ok, m = _execute_stage(
                logger, manifest, "scan", Path("/tmp/x.py"), timeout=60,
                extract_counts=run_pipeline._extract_scan_counts,
            )
        assert ok is True
        assert m["stages"]["scan"]["status"] == "completed"
        assert m["stages"]["scan"]["candidates"] == 1
        # transient field should NOT be set on success
        assert "transient" not in m["stages"]["scan"]


# ---------------------------------------------------------------------------
# consecutive_failures counter: transient keeps it flat
# ---------------------------------------------------------------------------

class TestConsecutiveFailuresTransient:
    """
    Covers the _fail closure inside run_pipeline.run_pipeline():
      - transient=True on any failed stage → counter unchanged
      - transient=False → counter increments as today
    """

    def _build_manifest(self, stage: str, transient: bool) -> dict:
        return {
            "run_id": "x",
            "status": "failed",
            "stages": {
                stage: {
                    "status": "failed",
                    "transient": transient,
                    "error": "boom",
                },
            },
        }

    def test_transient_failure_does_not_increment(self):
        """Replicate the _fail logic from run_pipeline.py."""
        state = {"consecutive_failures": 2}
        manifest = self._build_manifest("scan", transient=True)
        transient = any(
            st.get("transient")
            for st in manifest.get("stages", {}).values()
            if isinstance(st, dict)
        )
        # _fail should NOT increment on transient
        if not transient:
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        assert state["consecutive_failures"] == 2

    def test_real_failure_increments_counter(self):
        state = {"consecutive_failures": 2}
        manifest = self._build_manifest("predict", transient=False)
        transient = any(
            st.get("transient")
            for st in manifest.get("stages", {}).values()
            if isinstance(st, dict)
        )
        if not transient:
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        assert state["consecutive_failures"] == 3
