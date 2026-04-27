"""
tests/test_run_manifest.py — Unit tests for run manifest helpers in run_pipeline.py.

Covers:
  - _write_run_manifest
  - _update_stage
  - _extract_scan_counts
  - _extract_research_counts
  - _extract_predict_counts
  - _extract_risk_counts
  - _rotate_run_manifests
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_pipeline
from run_pipeline import (
    _write_run_manifest,
    _update_stage,
    _extract_scan_counts,
    _extract_research_counts,
    _extract_predict_counts,
    _extract_risk_counts,
    _rotate_run_manifests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(run_id: str = "20260325T120000") -> dict:
    return {
        "run_id": run_id,
        "started_at": "2026-03-25T12:00:00+00:00",
        "completed_at": None,
        "status": "running",
        "stages": {
            "scan": {"status": "pending"},
            "research": {"status": "pending"},
            "predict": {"status": "pending"},
            "risk": {"status": "pending"},
        },
        "trades_placed": 0,
    }


@pytest.fixture()
def runs_dir(tmp_path, monkeypatch):
    """Monkeypatch RUNS_DIR to a temporary directory."""
    d = tmp_path / "runs"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run_pipeline, "RUNS_DIR", d)
    return d


# ---------------------------------------------------------------------------
# _write_run_manifest
# ---------------------------------------------------------------------------

class TestWriteRunManifest:
    def test_creates_file_at_correct_path(self, runs_dir):
        manifest = _make_manifest("run001")
        _write_run_manifest(manifest)
        expected = runs_dir / "run_run001.json"
        assert expected.exists(), "Manifest file should be created"

    def test_file_content_matches_manifest(self, runs_dir):
        manifest = _make_manifest("run002")
        _write_run_manifest(manifest)
        written = json.loads((runs_dir / "run_run002.json").read_text())
        assert written == manifest

    def test_no_raise_on_missing_run_id(self, runs_dir):
        # manifest with no run_id key — should not raise
        manifest = {"status": "running", "stages": {}}
        # This should silently swallow the KeyError
        _write_run_manifest(manifest)  # must not raise

    def test_overwrite_existing_file(self, runs_dir):
        manifest = _make_manifest("run003")
        _write_run_manifest(manifest)
        manifest_v2 = {**manifest, "status": "completed"}
        _write_run_manifest(manifest_v2)
        written = json.loads((runs_dir / "run_run003.json").read_text())
        assert written["status"] == "completed"

    def test_creates_runs_dir_if_missing(self, tmp_path, monkeypatch):
        missing_dir = tmp_path / "nonexistent" / "runs"
        monkeypatch.setattr(run_pipeline, "RUNS_DIR", missing_dir)
        manifest = _make_manifest("run004")
        _write_run_manifest(manifest)  # should not raise
        assert (missing_dir / "run_run004.json").exists()

    def test_no_raise_when_os_replace_fails(self, runs_dir, monkeypatch):
        """Simulate os.replace() failure to exercise inner exception cleanup branch."""
        def failing_replace(src, dst):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", failing_replace)
        manifest = _make_manifest("run_fail")
        # Must not raise — inner exception is swallowed
        _write_run_manifest(manifest)


# ---------------------------------------------------------------------------
# _update_stage
# ---------------------------------------------------------------------------

class TestUpdateStage:
    def test_returns_new_dict_not_same_object(self):
        manifest = _make_manifest()
        updated = _update_stage(manifest, "scan", status="running")
        assert updated is not manifest

    def test_does_not_mutate_original(self):
        manifest = _make_manifest()
        original_scan_status = manifest["stages"]["scan"]["status"]
        _update_stage(manifest, "scan", status="running")
        assert manifest["stages"]["scan"]["status"] == original_scan_status

    def test_updates_correct_stage_field(self):
        manifest = _make_manifest()
        updated = _update_stage(manifest, "scan", status="running", started_at="2026-01-01")
        assert updated["stages"]["scan"]["status"] == "running"
        assert updated["stages"]["scan"]["started_at"] == "2026-01-01"

    def test_preserves_other_stages_unchanged(self):
        manifest = _make_manifest()
        updated = _update_stage(manifest, "scan", status="running")
        for stage in ("research", "predict", "risk"):
            assert updated["stages"][stage] == manifest["stages"][stage]

    def test_preserves_top_level_fields(self):
        manifest = _make_manifest()
        updated = _update_stage(manifest, "scan", status="completed")
        assert updated["run_id"] == manifest["run_id"]
        assert updated["started_at"] == manifest["started_at"]
        assert updated["trades_placed"] == manifest["trades_placed"]

    def test_multiple_update_stage_calls_produce_correct_state(self):
        manifest = _make_manifest()
        m1 = _update_stage(manifest, "scan", status="completed", candidates=5)
        m2 = _update_stage(m1, "research", status="running")
        m3 = _update_stage(m2, "research", status="completed", cache_hits=2)

        assert m3["stages"]["scan"]["status"] == "completed"
        assert m3["stages"]["scan"]["candidates"] == 5
        assert m3["stages"]["research"]["status"] == "completed"
        assert m3["stages"]["research"]["cache_hits"] == 2
        assert m3["stages"]["predict"]["status"] == "pending"

    def test_stages_dict_is_new_object(self):
        manifest = _make_manifest()
        updated = _update_stage(manifest, "scan", status="running")
        assert updated["stages"] is not manifest["stages"]


# ---------------------------------------------------------------------------
# _extract_scan_counts
# ---------------------------------------------------------------------------

class TestExtractScanCounts:
    def test_valid_bytes_returns_correct_count(self):
        data = json.dumps({"candidates": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}).encode()
        result = _extract_scan_counts(data)
        assert result == {"candidates": 3}

    def test_empty_candidates_returns_zero(self):
        data = json.dumps({"candidates": []}).encode()
        result = _extract_scan_counts(data)
        assert result == {"candidates": 0}

    def test_missing_candidates_key_returns_zero(self):
        data = json.dumps({"scan_id": "abc"}).encode()
        result = _extract_scan_counts(data)
        assert result == {"candidates": 0}

    def test_corrupt_bytes_returns_zero_no_raise(self):
        result = _extract_scan_counts(b"not valid json {{{{")
        assert result == {"candidates": 0}

    def test_empty_bytes_returns_zero_no_raise(self):
        result = _extract_scan_counts(b"")
        assert result == {"candidates": 0}

    def test_liquidity_probe_block_passes_through(self):
        """When the scan stage reports a liquidity_probe block, it lands in the manifest."""
        probe = {
            "probed": 50,
            "kept": 12,
            "dropped_thin": 36,
            "dropped_fetch_error": 2,
            "skipped_below_rank": 0,
        }
        data = json.dumps({"candidates": [], "liquidity_probe": probe}).encode()
        result = _extract_scan_counts(data)
        assert result == {"candidates": 0, "liquidity_probe": probe}

    def test_no_liquidity_probe_key_omitted(self):
        """Older scan output without the probe block doesn't surface a stale key."""
        data = json.dumps({"candidates": [{"id": "a"}]}).encode()
        result = _extract_scan_counts(data)
        assert result == {"candidates": 1}
        assert "liquidity_probe" not in result


# ---------------------------------------------------------------------------
# _extract_research_counts
# ---------------------------------------------------------------------------

class TestExtractResearchCounts:
    def _make_research_bytes(self, candidates: list) -> bytes:
        return json.dumps({"candidates": candidates}).encode()

    def test_correctly_counts_all_fields(self):
        candidates = [
            {"cache_hit": True},
            {"cache_hit": True},
            {"cache_hit": False, "research_skipped": False},  # fresh fetch
            {"cache_hit": False, "research_skipped": True},   # skipped
            {"cache_hit": False, "research_skipped": False, "low_confidence": True},  # fresh + low_conf
        ]
        data = self._make_research_bytes(candidates)
        result = _extract_research_counts(data)
        assert result["candidates"] == 5
        assert result["cache_hits"] == 2
        assert result["fresh_fetches"] == 2  # indices 2 and 4 (not cache_hit and not skipped)
        assert result["skipped"] == 1
        assert result["low_confidence"] == 1

    def test_empty_candidates(self):
        data = self._make_research_bytes([])
        result = _extract_research_counts(data)
        assert result["candidates"] == 0
        assert result["cache_hits"] == 0
        assert result["fresh_fetches"] == 0
        assert result["low_confidence"] == 0
        assert result["skipped"] == 0

    def test_corrupt_bytes_returns_empty_dict_no_raise(self):
        result = _extract_research_counts(b"<<<not json>>>")
        assert result == {}

    def test_all_keys_present_in_output(self):
        data = self._make_research_bytes([{"cache_hit": True}])
        result = _extract_research_counts(data)
        assert set(result.keys()) == {"candidates", "cache_hits", "fresh_fetches", "low_confidence", "skipped"}


# ---------------------------------------------------------------------------
# _extract_predict_counts
# ---------------------------------------------------------------------------

class TestExtractPredictCounts:
    def test_correctly_counts_signaled_and_skipped(self):
        signals = [
            {"predict_skipped": False, "edge": 0.08, "cache_hit": True},
            {"predict_skipped": False, "edge": 0.12, "cache_hit": False},
            {"predict_skipped": True,  "cache_hit": False},
        ]
        data = json.dumps({"signals": signals}).encode()
        result = _extract_predict_counts(data)
        assert result["signaled"] == 2
        assert result["skipped"] == 1
        assert result["cache_hits"] == 1

    def test_avg_edge_computed_correctly(self):
        signals = [
            {"predict_skipped": False, "edge": 0.10},
            {"predict_skipped": False, "edge": 0.20},
        ]
        data = json.dumps({"signals": signals}).encode()
        result = _extract_predict_counts(data)
        assert result["avg_edge"] == round((0.10 + 0.20) / 2, 4)

    def test_no_edges_sets_avg_edge_to_zero(self):
        signals = [
            {"predict_skipped": False},  # no "edge" key
        ]
        data = json.dumps({"signals": signals}).encode()
        result = _extract_predict_counts(data)
        assert result["avg_edge"] == 0.0

    def test_empty_signals(self):
        data = json.dumps({"signals": []}).encode()
        result = _extract_predict_counts(data)
        assert result["signaled"] == 0
        assert result["skipped"] == 0
        assert result["cache_hits"] == 0
        assert result["avg_edge"] == 0.0

    def test_corrupt_bytes_returns_empty_dict_no_raise(self):
        result = _extract_predict_counts(b"CORRUPT")
        assert result == {}

    def test_negative_edge_uses_abs_value(self):
        signals = [{"predict_skipped": False, "edge": -0.10}]
        data = json.dumps({"signals": signals}).encode()
        result = _extract_predict_counts(data)
        assert result["avg_edge"] == 0.1


# ---------------------------------------------------------------------------
# _extract_risk_counts
# ---------------------------------------------------------------------------

class TestExtractRiskCounts:
    def test_correctly_counts_approved_and_blocked(self):
        orders = [
            {"risk_approved": True,  "order_skipped": False},
            {"risk_approved": True,  "order_skipped": False},
            {"risk_approved": False, "order_skipped": True},
        ]
        data = json.dumps({"orders": orders}).encode()
        result = _extract_risk_counts(data)
        assert result["approved"] == 2
        assert result["blocked"] == 1

    def test_empty_orders(self):
        data = json.dumps({"orders": []}).encode()
        result = _extract_risk_counts(data)
        assert result == {"approved": 0, "blocked": 0}

    def test_corrupt_bytes_returns_empty_dict_no_raise(self):
        result = _extract_risk_counts(b"[invalid")
        assert result == {}

    def test_all_approved(self):
        orders = [{"risk_approved": True} for _ in range(5)]
        data = json.dumps({"orders": orders}).encode()
        result = _extract_risk_counts(data)
        assert result["approved"] == 5
        assert result["blocked"] == 0


# ---------------------------------------------------------------------------
# _rotate_run_manifests
# ---------------------------------------------------------------------------

class TestRotateRunManifests:
    def _make_logger(self):
        logger = logging.getLogger("test_rotate")
        logger.addHandler(logging.NullHandler())
        return logger

    def test_deletes_files_older_than_7_days(self, runs_dir):
        old_file = runs_dir / "run_old.json"
        old_file.write_text('{"run_id": "old"}')
        # Set mtime to 8 days ago
        old_mtime = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
        os.utime(old_file, (old_mtime, old_mtime))

        _rotate_run_manifests(self._make_logger())
        assert not old_file.exists(), "File older than 7 days should be deleted"

    def test_keeps_files_newer_than_7_days(self, runs_dir):
        new_file = runs_dir / "run_new.json"
        new_file.write_text('{"run_id": "new"}')
        # mtime defaults to now — well within 7 days

        _rotate_run_manifests(self._make_logger())
        assert new_file.exists(), "File newer than 7 days should be kept"

    def test_keeps_file_just_inside_7_day_window(self, runs_dir):
        recent_file = runs_dir / "run_recent.json"
        recent_file.write_text('{"run_id": "recent"}')
        # Set mtime to 6 days + 23 hours ago — safely inside the keep window
        near_cutoff = (datetime.now(timezone.utc) - timedelta(days=6, hours=23)).timestamp()
        os.utime(recent_file, (near_cutoff, near_cutoff))
        _rotate_run_manifests(self._make_logger())
        assert recent_file.exists(), "File less than 7 days old should be kept"

    def test_does_not_raise_when_runs_dir_missing(self, tmp_path, monkeypatch):
        missing_dir = tmp_path / "nonexistent_runs"
        monkeypatch.setattr(run_pipeline, "RUNS_DIR", missing_dir)
        _rotate_run_manifests(self._make_logger())  # must not raise

    def test_mixed_old_and_new_files(self, runs_dir):
        old_file = runs_dir / "run_old2.json"
        old_file.write_text('{"run_id": "old2"}')
        old_mtime = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
        os.utime(old_file, (old_mtime, old_mtime))

        new_file = runs_dir / "run_new2.json"
        new_file.write_text('{"run_id": "new2"}')

        _rotate_run_manifests(self._make_logger())
        assert not old_file.exists()
        assert new_file.exists()

    def test_does_not_raise_when_file_disappears_during_glob(self, runs_dir, monkeypatch):
        """Simulate an OSError on stat() to exercise the except OSError: continue branch."""
        old_file = runs_dir / "run_ghost.json"
        old_file.write_text('{"run_id": "ghost"}')
        old_mtime = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
        os.utime(old_file, (old_mtime, old_mtime))

        original_stat = Path.stat

        def flaky_stat(self, **kwargs):
            if self.name == "run_ghost.json":
                raise OSError("simulated disappearance")
            return original_stat(self, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky_stat)
        # Should not raise even when stat() raises
        _rotate_run_manifests(self._make_logger())
