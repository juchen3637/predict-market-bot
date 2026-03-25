"""
predict_cache.py — Signal-level cache for pm-predict skill.

Prevents redundant LLM calls for market candidates whose price has not
moved significantly since the last prediction cycle.

Cache file format (JSON):
    {
        "<market_id>": {
            "cached_at": "<ISO 8601 UTC>",
            "cached_price": 0.45,
            "signal": { ...full signal dict... }
        }
    }

Invalidation rules (any one → miss):
    - market_id absent
    - entry older than ttl_hours
    - abs(current_price - cached_price) > price_threshold

Public interface:
    load_cache(cache_path)                          -> dict
    save_cache(cache_path, cache, ttl_hours)        -> None  (atomic)
    lookup(cache, market_id, current_yes_price,
           ttl_hours, price_threshold)              -> dict | None
    store(cache, market_id, current_yes_price,
          signal)                                   -> dict  (new dict, no mutation)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# load_cache
# ---------------------------------------------------------------------------

def load_cache(cache_path: Path) -> dict:
    """Load the JSON cache from disk.

    Returns an empty dict if the file is missing or contains invalid JSON.
    Never raises.
    """
    try:
        with open(cache_path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[predict_cache] Warning: could not read cache at {cache_path}: {exc}",
            file=sys.stderr,
        )
        return {}


# ---------------------------------------------------------------------------
# save_cache
# ---------------------------------------------------------------------------

def save_cache(
    cache_path: Path,
    cache: dict,
    ttl_hours: float = 2.0,
) -> None:
    """Write the cache to disk atomically, pruning entries older than 2x TTL.

    Uses a temp file + os.replace() so the destination is never partially
    written. Never raises — logs a warning on failure.
    """
    try:
        pruned = _prune(cache, ttl_hours=ttl_hours)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file, then atomically replace the target.
        fd, tmp_path = tempfile.mkstemp(
            dir=cache_path.parent,
            prefix=".predict_cache_tmp_",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(pruned, f, indent=2)
            os.replace(tmp_path, cache_path)
        except Exception:
            # Clean up temp file if replace failed.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        print(
            f"[predict_cache] Warning: could not save cache to {cache_path}: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

def lookup(
    cache: dict,
    market_id: str,
    current_yes_price: float,
    ttl_hours: float,
    price_threshold: float,
) -> dict | None:
    """Return the cached signal for market_id if valid, else None.

    A cached entry is valid when ALL of:
        - market_id is present in cache
        - entry age <= ttl_hours
        - abs(current_yes_price - cached_price) <= price_threshold
    """
    entry = cache.get(market_id)
    if entry is None:
        return None

    # Age check
    try:
        cached_at = datetime.fromisoformat(entry["cached_at"])
    except (KeyError, ValueError):
        return None

    age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
    if age_hours > ttl_hours:
        return None

    # Price drift check (inclusive boundary: delta == threshold is a hit).
    # Round to 6 decimal places before comparison to avoid IEEE-754 drift
    # (e.g. 0.53 - 0.50 in float is 0.030000000000000027, not exactly 0.03).
    cached_price = entry.get("cached_price", 0.0)
    price_delta = round(abs(current_yes_price - cached_price), 6)
    if price_delta > price_threshold:
        return None

    return entry.get("signal")


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

def store(
    cache: dict,
    market_id: str,
    current_yes_price: float,
    signal: dict,
) -> dict:
    """Return a NEW cache dict with market_id updated. Never mutates the original.

    The new entry records:
        - cached_at: current UTC timestamp (ISO 8601)
        - cached_price: current_yes_price
        - signal: the full signal dict
    """
    entry = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "cached_price": current_yes_price,
        "signal": signal,
    }
    return {**cache, market_id: entry}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _prune(cache: dict, ttl_hours: float) -> dict:
    """Return a new dict with entries older than 2x ttl_hours removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours * 2)
    pruned: dict[str, Any] = {}
    for market_id, entry in cache.items():
        try:
            cached_at = datetime.fromisoformat(entry["cached_at"])
            if cached_at >= cutoff:
                pruned[market_id] = entry
        except (KeyError, ValueError):
            # Skip malformed entries rather than propagating them.
            pass
    return pruned
