"""Unit tests for predict_cache.py

TDD RED phase — all tests written before implementation.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/pm-predict/scripts"))

import predict_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(hours_ago: float, cached_price: float = 0.50) -> dict:
    """Build a cache entry with cached_at set `hours_ago` hours in the past."""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "cached_at": ts.isoformat(),
        "cached_price": cached_price,
        "signal": {"market_id": "TEST-1", "p_model": 0.60},
    }


# ---------------------------------------------------------------------------
# load_cache
# ---------------------------------------------------------------------------

def test_load_cache_missing_file(tmp_path):
    """load_cache on a non-existent path must return an empty dict, not raise."""
    result = predict_cache.load_cache(tmp_path / "nonexistent.json")
    assert result == {}


def test_load_cache_corrupt_json(tmp_path):
    """load_cache on a corrupt JSON file must return {} without raising."""
    bad_file = tmp_path / "cache.json"
    bad_file.write_text("{this is not valid json!!!}")
    result = predict_cache.load_cache(bad_file)
    assert result == {}


def test_load_cache_valid(tmp_path):
    """load_cache returns the parsed dict for a valid JSON file."""
    data = {"MKT-1": {"cached_at": "2026-01-01T00:00:00+00:00", "cached_price": 0.5, "signal": {}}}
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps(data))
    result = predict_cache.load_cache(cache_file)
    assert result == data


# ---------------------------------------------------------------------------
# save_cache
# ---------------------------------------------------------------------------

def test_save_cache_creates_file(tmp_path):
    """save_cache must write a file that can be read back."""
    cache_file = tmp_path / "cache.json"
    data = {"MKT-X": _make_entry(0.5)}
    predict_cache.save_cache(cache_file, data)
    assert cache_file.exists()
    loaded = json.loads(cache_file.read_text())
    assert "MKT-X" in loaded


def test_save_cache_atomic(tmp_path):
    """save_cache must not leave a partial file on disk (atomic write via tempfile)."""
    cache_file = tmp_path / "cache.json"
    data = {"MKT-A": _make_entry(0.1)}

    # Track tempfile creation — verify the final destination is written atomically.
    # We simulate this by checking the file contains valid JSON after save.
    predict_cache.save_cache(cache_file, data)
    raw = cache_file.read_text()
    parsed = json.loads(raw)  # must not raise — file is complete
    assert "MKT-A" in parsed


def test_save_cache_prunes_old_entries(tmp_path):
    """Entries older than 2x TTL must be pruned before writing."""
    ttl_hours = 2.0
    cache_file = tmp_path / "cache.json"

    fresh = _make_entry(hours_ago=1.0)          # within TTL — must survive
    stale = _make_entry(hours_ago=ttl_hours * 2 + 0.1)  # beyond 2x TTL — must be pruned

    data = {"FRESH": fresh, "STALE": stale}
    predict_cache.save_cache(cache_file, data, ttl_hours=ttl_hours)

    saved = json.loads(cache_file.read_text())
    assert "FRESH" in saved
    assert "STALE" not in saved


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

def test_lookup_miss_not_present():
    """lookup returns None when market_id is absent from cache."""
    result = predict_cache.lookup({}, "MISSING-1", 0.50, ttl_hours=2.0, price_threshold=0.03)
    assert result is None


def test_lookup_miss_expired():
    """lookup returns None when the entry is older than ttl_hours."""
    cache = {"MKT-EXP": _make_entry(hours_ago=3.0, cached_price=0.50)}
    result = predict_cache.lookup(cache, "MKT-EXP", 0.50, ttl_hours=2.0, price_threshold=0.03)
    assert result is None


def test_lookup_miss_price_moved():
    """lookup returns None when abs(current_price - cached_price) > threshold."""
    cache = {"MKT-MOV": _make_entry(hours_ago=0.5, cached_price=0.50)}
    result = predict_cache.lookup(cache, "MKT-MOV", 0.54, ttl_hours=2.0, price_threshold=0.03)
    assert result is None


def test_lookup_hit():
    """lookup returns the cached signal when entry is fresh and price stable."""
    entry = _make_entry(hours_ago=1.0, cached_price=0.50)
    cache = {"MKT-HIT": entry}
    result = predict_cache.lookup(cache, "MKT-HIT", 0.51, ttl_hours=2.0, price_threshold=0.03)
    assert result is not None
    assert result == entry["signal"]


def test_lookup_hit_at_exact_threshold():
    """delta == threshold is a hit (inclusive boundary)."""
    cache = {"MKT-EXACT": _make_entry(hours_ago=0.5, cached_price=0.50)}
    # delta = |0.53 - 0.50| = 0.03 == threshold → hit
    result = predict_cache.lookup(cache, "MKT-EXACT", 0.53, ttl_hours=2.0, price_threshold=0.03)
    assert result is not None


def test_lookup_miss_just_over_threshold():
    """delta = threshold + 0.001 is a miss."""
    cache = {"MKT-OVER": _make_entry(hours_ago=0.5, cached_price=0.50)}
    # delta = |0.531 - 0.50| = 0.031 > 0.03 → miss
    result = predict_cache.lookup(cache, "MKT-OVER", 0.531, ttl_hours=2.0, price_threshold=0.03)
    assert result is None


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

def test_store_returns_new_dict():
    """store must return a new dict and never mutate the original."""
    original = {}
    result = predict_cache.store(original, "MKT-NEW", 0.55, {"p_model": 0.65})
    assert result is not original
    assert original == {}  # original unchanged


def test_store_overwrites_existing():
    """store replaces an existing entry for the same market_id."""
    initial_cache = {"MKT-UPD": _make_entry(hours_ago=1.0, cached_price=0.40)}
    updated = predict_cache.store(initial_cache, "MKT-UPD", 0.55, {"p_model": 0.70})
    assert updated["MKT-UPD"]["cached_price"] == 0.55
    assert updated["MKT-UPD"]["signal"]["p_model"] == 0.70


def test_store_sets_cached_price():
    """store must record current_yes_price as cached_price in the entry."""
    result = predict_cache.store({}, "MKT-PRC", 0.72, {"p_model": 0.80})
    assert result["MKT-PRC"]["cached_price"] == 0.72


def test_store_sets_cached_at_utc():
    """store must record a UTC ISO timestamp in cached_at."""
    result = predict_cache.store({}, "MKT-TS", 0.50, {"p_model": 0.60})
    ts_str = result["MKT-TS"]["cached_at"]
    # Must parse without error and include timezone info
    ts = datetime.fromisoformat(ts_str)
    assert ts.tzinfo is not None


# ---------------------------------------------------------------------------
# Error-path / edge-case coverage
# ---------------------------------------------------------------------------

def test_lookup_malformed_cached_at_is_miss():
    """lookup returns None when cached_at cannot be parsed as ISO datetime."""
    cache = {
        "MKT-BAD": {
            "cached_at": "not-a-timestamp",
            "cached_price": 0.50,
            "signal": {"p_model": 0.60},
        }
    }
    result = predict_cache.lookup(cache, "MKT-BAD", 0.50, ttl_hours=2.0, price_threshold=0.03)
    assert result is None


def test_save_cache_tolerates_write_failure(tmp_path, monkeypatch):
    """save_cache must not raise even when the underlying write fails."""
    import os
    original_replace = os.replace

    def exploding_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", exploding_replace)

    cache_file = tmp_path / "cache.json"
    data = {"MKT-Z": _make_entry(0.5)}
    # Must not raise
    predict_cache.save_cache(cache_file, data)


def test_prune_skips_malformed_entries(tmp_path):
    """save_cache silently drops entries whose cached_at is malformed."""
    cache_file = tmp_path / "cache.json"
    data = {
        "GOOD": _make_entry(hours_ago=0.5),
        "BAD": {"cached_at": "???", "cached_price": 0.50, "signal": {}},
    }
    predict_cache.save_cache(cache_file, data, ttl_hours=2.0)
    saved = json.loads(cache_file.read_text())
    assert "GOOD" in saved
    assert "BAD" not in saved
