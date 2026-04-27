"""
tests/test_diagnose_state.py — Unit tests for scripts/diagnose_state.py.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import diagnose_state as ds


NOW = datetime(2026, 4, 23, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def synthetic_root(tmp_path: Path) -> Path:
    """Build a synthetic project root with realistic data/docs contents."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "runs").mkdir()
    (tmp_path / "docs" / "daily_summaries").mkdir(parents=True)

    # pipeline_state.json
    state = {
        "consecutive_failures": 0,
        "last_run_at": "2026-04-22T23:59:18+00:00",
        "last_success_at": "2026-04-22T23:59:18+00:00",
    }
    (tmp_path / "data" / "pipeline_state.json").write_text(json.dumps(state))

    # run manifests (3)
    for i, status in enumerate(("completed", "failed", "completed")):
        rid = f"2026042{i}T120000"
        manifest = {
            "run_id": rid,
            "status": status,
            "trades_placed": i,
            "stages": {
                "scan": {"status": "completed"},
                "research": {"status": "completed"},
                "predict": {"status": "completed"},
                "risk": {"status": "failed" if status == "failed" else "completed",
                         "error": "boom" if status == "failed" else None},
            },
        }
        (tmp_path / "data" / "runs" / f"run_{rid}.json").write_text(json.dumps(manifest))

    # trade_log.jsonl (5 entries: 2 rejected, 1 placed, 1 paper, 1 resolved)
    trades = [
        {"status": "rejected", "rejection_reason": "insufficient_depth", "outcome": None},
        {"status": "rejected", "rejection_reason": "insufficient_depth", "outcome": None},
        {"status": "rejected", "rejection_reason": "daily_loss_exceeded", "outcome": None},
        {"status": "placed", "outcome": None},
        {"status": "paper", "outcome": "win"},
    ]
    trade_log = tmp_path / "data" / "trade_log.jsonl"
    trade_log.write_text("\n".join(json.dumps(t) for t in trades) + "\n")

    # performance_metrics.json
    metrics = {
        "computed_at": "2026-04-22T23:00:00+00:00",
        "brier_score": 0.25,
        "live": {"trade_count": 10, "win_rate": 0.6, "sharpe": 2.0,
                 "max_drawdown": 0.05, "profit_factor": 1.5},
        "paper": {"trade_count": 20, "win_rate": 0.55, "sharpe": 1.2,
                  "max_drawdown": 0.08, "profit_factor": 1.1},
    }
    (tmp_path / "data" / "performance_metrics.json").write_text(json.dumps(metrics))

    # daily summaries
    for day in ("2026-04-21", "2026-04-22"):
        (tmp_path / "docs" / "daily_summaries" / f"{day}.md").write_text("# " + day)

    # xgboost train state
    xgb = {"last_train_trade_count": 50, "last_trained_at": "2026-04-21T23:00:00+00:00"}
    (tmp_path / "data" / "xgboost_train_state.json").write_text(json.dumps(xgb))

    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestFmtDuration:
    def test_seconds(self):
        assert ds._fmt_duration(45) == "45s"

    def test_minutes(self):
        assert ds._fmt_duration(600) == "10m"

    def test_hours(self):
        assert ds._fmt_duration(7200) == "2.0h"

    def test_days(self):
        assert ds._fmt_duration(86400 * 3) == "3.0d"

    def test_future(self):
        assert ds._fmt_duration(-10) == "in the future"


class TestParseIso:
    def test_valid(self):
        assert ds._parse_iso("2026-04-22T12:00:00+00:00") is not None

    def test_z_suffix(self):
        assert ds._parse_iso("2026-04-22T12:00:00Z") is not None

    def test_none(self):
        assert ds._parse_iso(None) is None

    def test_invalid(self):
        assert ds._parse_iso("not-a-date") is None


class TestReadJson:
    def test_missing_returns_empty(self, tmp_path):
        assert ds._read_json(tmp_path / "absent.json") == {}

    def test_corrupt_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{{{not json")
        assert ds._read_json(p) == {}

    def test_valid_returns_content(self, tmp_path):
        p = tmp_path / "ok.json"
        p.write_text('{"a": 1}')
        assert ds._read_json(p) == {"a": 1}


class TestReadJsonl:
    def test_missing_returns_empty(self, tmp_path):
        assert ds._read_jsonl(tmp_path / "absent.jsonl") == []

    def test_skips_blank_and_corrupt_lines(self, tmp_path):
        p = tmp_path / "mixed.jsonl"
        p.write_text('{"a":1}\n\nbroken\n{"b":2}\n')
        rows = ds._read_jsonl(p)
        assert rows == [{"a": 1}, {"b": 2}]


# ---------------------------------------------------------------------------
# Section reports
# ---------------------------------------------------------------------------

class TestReportStopFile:
    def test_absent(self, synthetic_root):
        lines = ds.report_stop_file(synthetic_root, NOW)
        assert any("absent" in l for l in lines)

    def test_present(self, synthetic_root):
        (synthetic_root / "STOP").write_text("")
        lines = ds.report_stop_file(synthetic_root, NOW)
        assert any("PRESENT" in l for l in lines)
        assert any("touched at" in l for l in lines)


class TestReportPipelineState:
    def test_all_fields_present(self, synthetic_root):
        lines = ds.report_pipeline_state(synthetic_root, NOW)
        joined = "\n".join(lines)
        assert "consecutive_failures: 0" in joined
        assert "last_run_at" in joined
        assert "last_success_at" in joined

    def test_stale_success_triggers_warning(self, synthetic_root):
        state = {
            "consecutive_failures": 3,
            "last_run_at": "2026-04-22T23:59:18+00:00",
            "last_success_at": "2026-04-19T06:45:00+00:00",  # ~4 days stale
        }
        (synthetic_root / "data" / "pipeline_state.json").write_text(json.dumps(state))
        lines = ds.report_pipeline_state(synthetic_root, NOW)
        assert any("WARNING" in l for l in lines)

    def test_missing_file(self, tmp_path):
        lines = ds.report_pipeline_state(tmp_path, NOW)
        assert any("missing or unreadable" in l for l in lines)


class TestReportRecentRuns:
    def test_shows_stage_summary(self, synthetic_root):
        lines = ds.report_recent_runs(synthetic_root)
        joined = "\n".join(lines)
        assert "scan=" in joined
        assert "research=" in joined
        assert "predict=" in joined
        assert "risk=" in joined

    def test_surfaces_stage_errors(self, synthetic_root):
        lines = ds.report_recent_runs(synthetic_root)
        joined = "\n".join(lines)
        assert "boom" in joined

    def test_no_runs_dir(self, tmp_path):
        lines = ds.report_recent_runs(tmp_path)
        assert any("missing" in l for l in lines)

    def test_empty_runs_dir(self, tmp_path):
        (tmp_path / "data" / "runs").mkdir(parents=True)
        lines = ds.report_recent_runs(tmp_path)
        assert any("no manifests" in l for l in lines)

    def test_displays_liquidity_probe_when_present(self, tmp_path):
        (tmp_path / "data" / "runs").mkdir(parents=True)
        manifest = {
            "run_id": "20260427T120000",
            "status": "completed",
            "trades_placed": 0,
            "stages": {
                "scan": {
                    "status": "completed",
                    "liquidity_probe": {
                        "probed": 50, "kept": 12, "dropped_thin": 36,
                        "dropped_fetch_error": 2, "skipped_below_rank": 0,
                    },
                },
                "research": {"status": "completed"},
                "predict": {"status": "completed"},
                "risk": {"status": "completed"},
            },
        }
        (tmp_path / "data" / "runs" / "run_20260427T120000.json").write_text(json.dumps(manifest))
        joined = "\n".join(ds.report_recent_runs(tmp_path))
        assert "probe: 12/50 kept" in joined

    def test_omits_liquidity_probe_when_absent(self, synthetic_root):
        """Manifest without probe block should not produce a probe: line."""
        joined = "\n".join(ds.report_recent_runs(synthetic_root))
        assert "probe:" not in joined


class TestReportTradeLog:
    def test_counts_by_status_and_rejection(self, synthetic_root):
        lines = ds.report_trade_log(synthetic_root)
        joined = "\n".join(lines)
        assert "5 entries" in joined
        assert "rejected: 3" in joined
        assert "insufficient_depth: 2" in joined
        assert "daily_loss_exceeded: 1" in joined

    def test_empty(self, tmp_path):
        (tmp_path / "data").mkdir()
        lines = ds.report_trade_log(tmp_path)
        assert any("empty or missing" in l for l in lines)


class TestReportMetrics:
    def test_includes_both_modes(self, synthetic_root):
        lines = ds.report_metrics(synthetic_root)
        joined = "\n".join(lines)
        assert "live:" in joined
        assert "paper:" in joined
        assert "brier_score" in joined

    def test_missing(self, tmp_path):
        lines = ds.report_metrics(tmp_path)
        assert any("missing" in l for l in lines)


class TestReportDailySummaries:
    def test_lists_latest(self, synthetic_root):
        lines = ds.report_daily_summaries(synthetic_root, NOW)
        joined = "\n".join(lines)
        assert "2026-04-22" in joined
        assert "2 files" in joined

    def test_gap_warning_when_stale(self, synthetic_root):
        # Only 2026-04-15 present → >1 day stale vs NOW (2026-04-23)
        for f in (synthetic_root / "docs" / "daily_summaries").glob("*.md"):
            f.unlink()
        (synthetic_root / "docs" / "daily_summaries" / "2026-04-15.md").write_text("old")
        lines = ds.report_daily_summaries(synthetic_root, NOW)
        assert any("WARNING" in l for l in lines)

    def test_missing_dir(self, tmp_path):
        lines = ds.report_daily_summaries(tmp_path, NOW)
        assert any("missing" in l for l in lines)

    def test_empty_dir(self, tmp_path):
        (tmp_path / "docs" / "daily_summaries").mkdir(parents=True)
        lines = ds.report_daily_summaries(tmp_path, NOW)
        assert any("empty" in l for l in lines)


class TestReportXgboost:
    def test_shows_timestamp_and_trade_count(self, synthetic_root):
        lines = ds.report_xgboost(synthetic_root, NOW)
        joined = "\n".join(lines)
        assert "last_train_trade_count=50" in joined
        assert "2026-04-21" in joined

    def test_missing(self, tmp_path):
        lines = ds.report_xgboost(tmp_path, NOW)
        assert any("missing" in l for l in lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

class TestBuildReport:
    def test_all_sections_present(self, synthetic_root):
        report = ds.build_report(synthetic_root, now=NOW)
        # Each section's distinguishing phrase should appear
        for phrase in (
            "STOP file",
            "pipeline_state.json",
            "Last",  # "Last N run manifests"
            "trade_log.jsonl",
            "performance_metrics.json",
            "docs/daily_summaries",
            "xgboost",
        ):
            assert phrase in report, f"missing section: {phrase}"

    def test_does_not_raise_on_empty_root(self, tmp_path):
        report = ds.build_report(tmp_path, now=NOW)
        # Missing-data path should still produce a report
        assert "predict-market-bot diagnostic report" in report

    def test_defaults_to_now_when_not_given(self, synthetic_root):
        # Should not raise; just ensure the path works
        report = ds.build_report(synthetic_root)
        assert report


class TestMain:
    def test_prints_report(self, synthetic_root, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["diagnose_state.py", "--root", str(synthetic_root)])
        rc = ds.main()
        captured = capsys.readouterr()
        assert rc == 0
        assert "predict-market-bot diagnostic report" in captured.out
