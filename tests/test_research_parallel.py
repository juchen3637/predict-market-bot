"""
tests/test_research_parallel.py — Fix B tests for
research_pipeline._process_candidates_parallel.

Covers:
  - order preservation under out-of-order completion
  - bounded parallelism (no more than max_workers concurrent)
  - worker exception captured into a research_skipped entry
  - serial fallback when max_workers <= 1 or single candidate
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "skills" / "pm-research" / "scripts"),
)

import research_pipeline  # noqa: E402


def _cand(i: int) -> dict:
    return {
        "market_id": f"m{i}",
        "title": f"title {i}",
        "current_yes_price": 0.5,
    }


class TestProcessCandidatesParallel:
    def test_preserves_order_despite_out_of_order_completion(self):
        """
        Last candidate finishes first (shortest sleep); first finishes last.
        Output list must still be ordered by input index.
        """
        candidates = [_cand(i) for i in range(5)]
        # Reverse sleep so later candidates finish first
        delays = {f"m{i}": (5 - i) * 0.01 for i in range(5)}

        def fake_process(cand, *_args, **_kw):
            time.sleep(delays[cand["market_id"]])
            return {**cand, "_processed": True}

        with patch.object(research_pipeline, "process_candidate", side_effect=fake_process):
            out = research_pipeline._process_candidates_parallel(
                candidates,
                min_sources_required=1,
                confidence_threshold=0.5,
                ttl_hours=4.0,
                max_workers=5,
            )

        assert [r["market_id"] for r in out] == [f"m{i}" for i in range(5)]
        assert all(r["_processed"] for r in out)

    def test_respects_max_workers_bound(self):
        """Concurrent count must never exceed max_workers."""
        candidates = [_cand(i) for i in range(12)]
        concurrent = {"current": 0, "peak": 0}
        lock = threading.Lock()

        def fake_process(cand, *_args, **_kw):
            with lock:
                concurrent["current"] += 1
                concurrent["peak"] = max(concurrent["peak"], concurrent["current"])
            time.sleep(0.02)
            with lock:
                concurrent["current"] -= 1
            return {**cand, "_ok": True}

        with patch.object(research_pipeline, "process_candidate", side_effect=fake_process):
            research_pipeline._process_candidates_parallel(
                candidates,
                min_sources_required=1,
                confidence_threshold=0.5,
                ttl_hours=4.0,
                max_workers=3,
            )

        assert concurrent["peak"] <= 3
        assert concurrent["peak"] >= 2  # sanity: we actually parallelized

    def test_worker_exception_captured_as_skipped(self):
        candidates = [_cand(0), _cand(1), _cand(2)]

        def fake_process(cand, *_args, **_kw):
            if cand["market_id"] == "m1":
                raise RuntimeError("boom")
            return {**cand, "sentiment": {"score": 0.1}, "research_skipped": False}

        with patch.object(research_pipeline, "process_candidate", side_effect=fake_process):
            out = research_pipeline._process_candidates_parallel(
                candidates,
                min_sources_required=1,
                confidence_threshold=0.5,
                ttl_hours=4.0,
                max_workers=3,
            )

        # Order preserved
        assert [r["market_id"] for r in out] == ["m0", "m1", "m2"]
        # Failed one marked skipped with reason mentioning the exception
        assert out[1]["research_skipped"] is True
        assert "RuntimeError" in out[1]["skip_reason"]
        assert "boom" in out[1]["skip_reason"]
        # Others passed through
        assert out[0]["research_skipped"] is False
        assert out[2]["research_skipped"] is False

    def test_serial_fallback_when_max_workers_one(self):
        """max_workers=1 should NOT open a ThreadPoolExecutor."""
        candidates = [_cand(i) for i in range(3)]

        def fake_process(cand, *_args, **_kw):
            return {**cand, "_ok": True}

        # If _process_candidates_parallel tried to use ThreadPoolExecutor,
        # patching it to raise would explode the test.
        with patch.object(research_pipeline, "ThreadPoolExecutor", side_effect=AssertionError("should not be called")):
            with patch.object(research_pipeline, "process_candidate", side_effect=fake_process):
                out = research_pipeline._process_candidates_parallel(
                    candidates,
                    min_sources_required=1,
                    confidence_threshold=0.5,
                    ttl_hours=4.0,
                    max_workers=1,
                )

        assert [r["market_id"] for r in out] == ["m0", "m1", "m2"]

    def test_single_candidate_uses_serial_path(self):
        candidates = [_cand(0)]

        def fake_process(cand, *_args, **_kw):
            return {**cand, "_ok": True}

        with patch.object(research_pipeline, "ThreadPoolExecutor", side_effect=AssertionError("should not be called")):
            with patch.object(research_pipeline, "process_candidate", side_effect=fake_process):
                out = research_pipeline._process_candidates_parallel(
                    candidates,
                    min_sources_required=1,
                    confidence_threshold=0.5,
                    ttl_hours=4.0,
                    max_workers=5,
                )

        assert len(out) == 1
        assert out[0]["market_id"] == "m0"

    def test_empty_candidates_returns_empty(self):
        out = research_pipeline._process_candidates_parallel(
            [],
            min_sources_required=1,
            confidence_threshold=0.5,
            ttl_hours=4.0,
            max_workers=5,
        )
        assert out == []

    def test_passes_config_args_through(self):
        """Make sure min_sources_required / confidence_threshold / ttl_hours
        reach process_candidate unchanged."""
        candidates = [_cand(0)]
        captured = {}

        def fake_process(cand, min_sources, conf_thresh, ttl, *a, **kw):
            captured["min_sources"] = min_sources
            captured["conf"] = conf_thresh
            captured["ttl"] = ttl
            return {**cand, "_ok": True}

        with patch.object(research_pipeline, "process_candidate", side_effect=fake_process):
            research_pipeline._process_candidates_parallel(
                candidates,
                min_sources_required=3,
                confidence_threshold=0.77,
                ttl_hours=2.5,
                max_workers=5,
            )

        assert captured == {"min_sources": 3, "conf": 0.77, "ttl": 2.5}
