"""Integration tests for predict_pipeline.py cache behaviour.

TDD RED phase — all tests written before cache integration is implemented.

Strategy: patch run_consensus and predict_cache to control cache hits/misses,
then assert that the pipeline calls (or skips) LLM calls accordingly.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/pm-predict/scripts"))


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SETTINGS = {
    "predict": {
        "min_edge_to_signal": 0.04,
        "min_ensemble_agreement": 2,
        "brier_window_days": 30,
        "brier_alert_threshold": 0.30,
        "llm_weights": {
            "claude_sonnet": 0.40,
            "gpt5_mini": 0.35,
            "gemini_flash": 0.25,
        },
        "signal_cache_ttl_hours": 2,
        "signal_cache_price_move_threshold": 0.03,
    }
}

CANDIDATE = {
    "market_id": "MKT-CACHE-1",
    "title": "Will BTC exceed $100k by end of March 2026?",
    "current_yes_price": 0.45,
    "days_to_expiry": 5,
    "volume_24h": 1000,
    "open_interest": 500,
    "category": "crypto",
    "anomaly_flags": [],
    "sentiment": {"score": 0.60, "label": "bullish", "confidence": 0.75, "sources": ["reddit"]},
    "gap_analysis": {"direction": "long", "signal_strength": 0.15},
    "low_confidence": False,
    "research_skipped": False,
    "skip_reason": None,
}

CACHED_SIGNAL = {
    "market_id": "MKT-CACHE-1",
    "p_model": 0.60,
    "edge": 0.15,
    "direction": "long",
    "predict_skipped": False,
    "skip_reason": None,
}


def _make_consensus_result(prob: float = 0.65):
    result = MagicMock()
    result.consensus_prob = prob
    result.models_responded = 3
    result.weighted_agreement = 0.90
    return result


# ---------------------------------------------------------------------------
# Helper: run process_candidate with cache injected
# ---------------------------------------------------------------------------

def _run_with_cache(candidate, cache_signal, monkeypatch):
    """
    Import and call process_candidate after patching predict_cache.lookup
    to return cache_signal (or None for a miss).
    """
    import predict_pipeline
    return predict_pipeline.process_candidate(
        candidate,
        min_edge_to_signal=0.04,
        cache={"MKT-CACHE-1": {}},
        ttl_hours=2.0,
        price_threshold=0.03,
        _lookup_fn=lambda c, mid, price, ttl, thresh: cache_signal,
    )


# ---------------------------------------------------------------------------
# test_cache_hit_skips_llm
# ---------------------------------------------------------------------------

def test_cache_hit_skips_llm():
    """When cache returns a hit, run_consensus must NOT be called."""
    import predict_pipeline

    with patch.object(predict_pipeline, "run_consensus") as mock_consensus, \
         patch.object(predict_pipeline, "predict_cache") as mock_pc:
        mock_pc.lookup.return_value = CACHED_SIGNAL

        predict_pipeline.process_candidate(
            CANDIDATE,
            min_edge_to_signal=0.04,
            cache={"MKT-CACHE-1": {}},
            ttl_hours=2.0,
            price_threshold=0.03,
        )

        mock_consensus.assert_not_called()


# ---------------------------------------------------------------------------
# test_cache_miss_calls_llm
# ---------------------------------------------------------------------------

def test_cache_miss_calls_llm():
    """When cache returns None (miss), run_consensus MUST be called."""
    import predict_pipeline

    with patch.object(predict_pipeline, "run_consensus", return_value=_make_consensus_result()) as mock_consensus, \
         patch.object(predict_pipeline, "predict_cache") as mock_pc, \
         patch.object(predict_pipeline, "xgboost_predict", side_effect=Exception("not trained")):
        mock_pc.lookup.return_value = None
        mock_pc.store.side_effect = lambda c, mid, price, sig: {**c, mid: {}}

        predict_pipeline.process_candidate(
            CANDIDATE,
            min_edge_to_signal=0.04,
            cache={},
            ttl_hours=2.0,
            price_threshold=0.03,
        )

        mock_consensus.assert_called_once()


# ---------------------------------------------------------------------------
# test_cache_hit_field_present_on_hit
# ---------------------------------------------------------------------------

def test_cache_hit_field_present_on_hit():
    """Signal returned from cache hit must have cache_hit=True."""
    import predict_pipeline

    with patch.object(predict_pipeline, "run_consensus") as mock_consensus, \
         patch.object(predict_pipeline, "predict_cache") as mock_pc:
        mock_pc.lookup.return_value = CACHED_SIGNAL

        signal = predict_pipeline.process_candidate(
            CANDIDATE,
            min_edge_to_signal=0.04,
            cache={"MKT-CACHE-1": {}},
            ttl_hours=2.0,
            price_threshold=0.03,
        )

        assert signal.get("cache_hit") is True


# ---------------------------------------------------------------------------
# test_cache_hit_field_present_on_miss
# ---------------------------------------------------------------------------

def test_cache_hit_field_present_on_miss():
    """Signal from a cache miss must have cache_hit=False."""
    import predict_pipeline

    with patch.object(predict_pipeline, "run_consensus", return_value=_make_consensus_result()), \
         patch.object(predict_pipeline, "predict_cache") as mock_pc, \
         patch.object(predict_pipeline, "xgboost_predict", side_effect=Exception("not trained")):
        mock_pc.lookup.return_value = None
        mock_pc.store.side_effect = lambda c, mid, price, sig: {**c, mid: {}}

        signal = predict_pipeline.process_candidate(
            CANDIDATE,
            min_edge_to_signal=0.04,
            cache={},
            ttl_hours=2.0,
            price_threshold=0.03,
        )

        assert signal.get("cache_hit") is False


# ---------------------------------------------------------------------------
# test_cache_hit_recalculates_edge
# ---------------------------------------------------------------------------

def test_cache_hit_recalculates_edge():
    """
    On a cache hit: edge must be recomputed as p_model - current_yes_price
    using the LIVE price, not the cached price.

    Setup: cached p_model=0.60, new live price=0.55 → edge must be 0.05.
    """
    import predict_pipeline

    cached = {**CACHED_SIGNAL, "p_model": 0.60, "edge": 0.15}  # old edge was 0.15
    candidate = {**CANDIDATE, "current_yes_price": 0.55}

    with patch.object(predict_pipeline, "run_consensus") as mock_consensus, \
         patch.object(predict_pipeline, "predict_cache") as mock_pc:
        mock_pc.lookup.return_value = cached

        signal = predict_pipeline.process_candidate(
            candidate,
            min_edge_to_signal=0.04,
            cache={"MKT-CACHE-1": {}},
            ttl_hours=2.0,
            price_threshold=0.03,
        )

        assert signal["edge"] == pytest.approx(0.05, abs=1e-4)
        mock_consensus.assert_not_called()


# ---------------------------------------------------------------------------
# test_llm_failure_not_cached
# ---------------------------------------------------------------------------

def test_llm_failure_not_cached():
    """When run_consensus raises, p_model is None and store must NOT be called."""
    import predict_pipeline

    with patch.object(predict_pipeline, "run_consensus", side_effect=RuntimeError("LLM down")), \
         patch.object(predict_pipeline, "predict_cache") as mock_pc:
        mock_pc.lookup.return_value = None

        signal = predict_pipeline.process_candidate(
            CANDIDATE,
            min_edge_to_signal=0.04,
            cache={},
            ttl_hours=2.0,
            price_threshold=0.03,
        )

        mock_pc.store.assert_not_called()
        assert signal["p_model"] is None


# ---------------------------------------------------------------------------
# test_research_skipped_bypasses_cache
# ---------------------------------------------------------------------------

def test_research_skipped_bypasses_cache():
    """research_skipped candidates must bypass cache entirely (no lookup, no store)."""
    import predict_pipeline

    skipped_candidate = {**CANDIDATE, "research_skipped": True, "skip_reason": "no sources"}

    with patch.object(predict_pipeline, "predict_cache") as mock_pc:
        signal = predict_pipeline.process_candidate(
            skipped_candidate,
            min_edge_to_signal=0.04,
            cache={},
            ttl_hours=2.0,
            price_threshold=0.03,
        )

        mock_pc.lookup.assert_not_called()
        mock_pc.store.assert_not_called()
        assert signal["predict_skipped"] is True
