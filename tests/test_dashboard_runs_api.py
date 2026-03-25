"""
tests/test_dashboard_runs_api.py — Unit and integration tests for run manifest
reader functions and HTTP routes in dashboard_server.py.

Covers:
  - _runs_dir_mtime
  - _read_runs
  - _read_run_manifest
  - GET /api/runs   (HTTP)
  - GET /api/runs/{run_id}  (HTTP)
"""

import http.client
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dashboard_server
from dashboard_server import (
    _read_runs,
    _read_run_manifest,
    _runs_dir_mtime,
    _Handler,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def runs_dir(tmp_path, monkeypatch):
    """Monkeypatch RUNS_DIR in dashboard_server to a temporary directory."""
    d = tmp_path / "runs"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dashboard_server, "RUNS_DIR", d)
    return d


def _write_manifest(runs_dir: Path, run_id: str, data: dict | None = None) -> Path:
    """Helper: write a valid manifest JSON file to runs_dir."""
    if data is None:
        data = {
            "run_id": run_id,
            "started_at": "2026-03-25T12:00:00+00:00",
            "status": "completed",
            "stages": {},
            "trades_placed": 0,
        }
    p = runs_dir / f"run_{run_id}.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# _runs_dir_mtime
# ---------------------------------------------------------------------------

class TestRunsDirMtime:
    def test_returns_zero_when_directory_missing(self, tmp_path, monkeypatch):
        missing = tmp_path / "no_such_dir"
        monkeypatch.setattr(dashboard_server, "RUNS_DIR", missing)
        assert _runs_dir_mtime() == 0.0

    def test_returns_zero_when_no_run_files(self, runs_dir):
        # Directory exists but has no run_*.json files
        assert _runs_dir_mtime() == 0.0

    def test_returns_newest_mtime(self, runs_dir):
        f1 = _write_manifest(runs_dir, "run001")
        time.sleep(0.01)
        f2 = _write_manifest(runs_dir, "run002")

        mtime = _runs_dir_mtime()
        # Should be >= mtime of newer file
        assert mtime >= f2.stat().st_mtime

    def test_ignores_non_run_json_files(self, runs_dir):
        # A file that doesn't match run_*.json should not count
        (runs_dir / "other_file.json").write_text("{}")
        assert _runs_dir_mtime() == 0.0

    def test_does_not_raise_when_stat_fails(self, runs_dir, monkeypatch):
        """Exercise the OSError: continue branch when stat() raises mid-glob."""
        _write_manifest(runs_dir, "run_ghost")

        original_stat = Path.stat

        def flaky_stat(self, **kwargs):
            if self.name.startswith("run_"):
                raise OSError("simulated")
            return original_stat(self, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky_stat)
        # Should return 0.0 (all stat calls failed) rather than raise
        result = _runs_dir_mtime()
        assert result == 0.0


# ---------------------------------------------------------------------------
# _read_runs
# ---------------------------------------------------------------------------

class TestReadRuns:
    def test_returns_empty_list_when_directory_missing(self, tmp_path, monkeypatch):
        missing = tmp_path / "no_such_dir"
        monkeypatch.setattr(dashboard_server, "RUNS_DIR", missing)
        assert _read_runs() == []

    def test_returns_empty_list_when_no_files(self, runs_dir):
        assert _read_runs() == []

    def test_returns_manifests_sorted_newest_first(self, runs_dir):
        f1 = _write_manifest(runs_dir, "aaa", {"run_id": "aaa", "started_at": "2026-01-01"})
        time.sleep(0.02)
        f2 = _write_manifest(runs_dir, "bbb", {"run_id": "bbb", "started_at": "2026-01-02"})

        # Touch f2 to ensure its mtime is newer
        os.utime(f2, None)

        result = _read_runs()
        assert len(result) == 2
        assert result[0]["run_id"] == "bbb"  # newest first
        assert result[1]["run_id"] == "aaa"

    def test_skips_corrupt_json_silently(self, runs_dir):
        (runs_dir / "run_corrupt.json").write_text("NOT VALID JSON {{{{")
        _write_manifest(runs_dir, "valid01")
        result = _read_runs()
        assert len(result) == 1
        assert result[0]["run_id"] == "valid01"

    def test_respects_max_runs_limit(self, runs_dir):
        for i in range(10):
            _write_manifest(runs_dir, f"run{i:03d}")
        result = _read_runs(max_runs=3)
        assert len(result) == 3

    def test_returns_list_of_dicts(self, runs_dir):
        _write_manifest(runs_dir, "run001")
        result = _read_runs()
        assert isinstance(result, list)
        assert isinstance(result[0], dict)


# ---------------------------------------------------------------------------
# _read_run_manifest
# ---------------------------------------------------------------------------

class TestReadRunManifest:
    def test_returns_manifest_for_valid_run_id(self, runs_dir):
        _write_manifest(runs_dir, "20260325T120000")
        result = _read_run_manifest("20260325T120000")
        assert result is not None
        assert result["run_id"] == "20260325T120000"

    def test_returns_none_for_missing_run(self, runs_dir):
        result = _read_run_manifest("nonexistent")
        assert result is None

    def test_returns_none_for_run_id_containing_slash(self, runs_dir):
        result = _read_run_manifest("some/path")
        assert result is None

    def test_returns_none_for_run_id_containing_dotdot(self, runs_dir):
        result = _read_run_manifest("../../etc/passwd")
        assert result is None

    def test_returns_none_for_empty_run_id(self, runs_dir):
        result = _read_run_manifest("")
        assert result is None

    def test_returns_none_for_corrupt_json(self, runs_dir):
        (runs_dir / "run_corrupt.json").write_text("INVALID")
        result = _read_run_manifest("corrupt")
        assert result is None

    def test_manifest_content_matches_file(self, runs_dir):
        expected = {
            "run_id": "abc123",
            "status": "completed",
            "stages": {},
            "trades_placed": 2,
        }
        _write_manifest(runs_dir, "abc123", expected)
        result = _read_run_manifest("abc123")
        assert result == expected


# ---------------------------------------------------------------------------
# HTTP Routes — spin up a real (but ephemeral) server in a background thread
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def http_server(tmp_path, monkeypatch):
    """Spin up a ThreadingHTTPServer on a random port for route tests."""
    d = tmp_path / "runs"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dashboard_server, "RUNS_DIR", d)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"port": port, "runs_dir": d}
    server.shutdown()


def _get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


class TestApiRunsRoute:
    def test_get_api_runs_returns_200_with_empty_array(self, http_server):
        status, body = _get(http_server["port"], "/api/runs")
        assert status == 200
        data = json.loads(body)
        assert data == []

    def test_get_api_runs_returns_200_with_manifests(self, http_server):
        _write_manifest(http_server["runs_dir"], "ts001", {"run_id": "ts001"})
        status, body = _get(http_server["port"], "/api/runs")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["run_id"] == "ts001"

    def test_get_api_run_by_id_returns_200(self, http_server):
        _write_manifest(http_server["runs_dir"], "ts002", {"run_id": "ts002", "status": "completed"})
        status, body = _get(http_server["port"], "/api/runs/ts002")
        assert status == 200
        data = json.loads(body)
        assert data["run_id"] == "ts002"

    def test_get_api_run_by_id_returns_404_for_unknown(self, http_server):
        status, _ = _get(http_server["port"], "/api/runs/unknown_run_xyz")
        assert status == 404

    def test_get_api_run_content_type_is_json(self, http_server):
        _write_manifest(http_server["runs_dir"], "ts003", {"run_id": "ts003"})
        conn = http.client.HTTPConnection("127.0.0.1", http_server["port"], timeout=5)
        conn.request("GET", "/api/runs/ts003")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert "application/json" in resp.getheader("Content-Type", "")

    def test_get_api_runs_content_type_is_json(self, http_server):
        conn = http.client.HTTPConnection("127.0.0.1", http_server["port"], timeout=5)
        conn.request("GET", "/api/runs")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert "application/json" in resp.getheader("Content-Type", "")
