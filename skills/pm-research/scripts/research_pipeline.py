"""
research_pipeline.py — Research Orchestrator for pm-research skill

Reads pm-scan output JSON from stdin, runs scrape→classify for each candidate,
enforces quality gates, computes gap analysis, and writes enriched output to
data/enriched_{scan_id}.json (also printed to stdout for pipeline chaining).

Usage:
    python skills/pm-scan/scripts/filter_markets.py \
      | python skills/pm-research/scripts/research_pipeline.py

Input (stdin): pm-scan output JSON
    {
        "scan_id": "scan_20260316T120000",
        "candidates": [
            {"market_id": "...", "title": "...", "current_yes_price": 0.45, ...}
        ]
    }

Output (stdout + data/enriched_{scan_id}.json):
    {
        "scan_id": "...",
        "enriched_at": "...",
        "candidates": [...]
    }
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Resolve project root so imports work regardless of CWD
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]  # skills/pm-research/scripts → project root

sys.path.insert(0, str(_SCRIPT_DIR))

from classify_sentiment import classify  # noqa: E402
from scrape_sources import scrape_all  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = _PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "research_cache"


# ---------------------------------------------------------------------------
# Research Cache
# ---------------------------------------------------------------------------

def _cache_path(market_id: str) -> Path:
    slug = market_id.replace("/", "_")[:40]
    return CACHE_DIR / f"{slug}.json"


def _load_cache(market_id: str, ttl_hours: float) -> dict | None:
    """Return cached entry if it exists and is within TTL, else None."""
    path = _cache_path(market_id)
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text())
        cached_at = datetime.fromisoformat(entry["cached_at"])
        age = datetime.now(timezone.utc) - cached_at
        if age.total_seconds() < ttl_hours * 3600:
            return entry
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    return None


def _save_cache(market_id: str, scrape_result: dict, sentiment: dict) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(market_id)
    entry = {
        "market_id": market_id,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "scrape_result": scrape_result,
        "sentiment": sentiment,
    }
    path.write_text(json.dumps(entry))

def load_settings() -> dict[str, Any]:
    settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
    with open(settings_path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Gap Analysis
# ---------------------------------------------------------------------------

def compute_gap_analysis(
    sentiment_score: float,
    current_yes_price: float,
) -> dict[str, Any]:
    """
    Compare sentiment score vs current market price to identify mispricings.

    Rules:
      - score > 0.2 and price < 0.5  → long opportunity
      - score < -0.2 and price > 0.5 → short opportunity
      - otherwise                     → no signal
    """
    if sentiment_score > 0.2 and current_yes_price < 0.5:
        return {
            "direction": "long",
            "signal_strength": round(sentiment_score - current_yes_price, 4),
        }
    if sentiment_score < -0.2 and current_yes_price > 0.5:
        return {
            "direction": "short",
            "signal_strength": round(current_yes_price + sentiment_score, 4),
        }
    return {"direction": "none", "signal_strength": 0.0}


# ---------------------------------------------------------------------------
# Per-candidate pipeline
# ---------------------------------------------------------------------------

def process_candidate(
    candidate: dict[str, Any],
    min_sources_required: int,
    confidence_threshold: float,
    ttl_hours: float = 4.0,
) -> dict[str, Any]:
    """
    Run scrape → classify → gap analysis for one candidate.
    Returns enriched candidate dict matching the SKILL.md output schema.
    Cache hit skips scrape and classify entirely.
    """
    market_id = candidate["market_id"]
    title = candidate["title"]
    current_yes_price = candidate["current_yes_price"]

    base = {**candidate}  # preserve all scan fields (days_to_expiry, volume_24h, etc.)

    # --- Cache check ---
    cached = _load_cache(market_id, ttl_hours)
    if cached:
        sources_data = cached["scrape_result"]
        sentiment_dict = cached["sentiment"]
        print(
            f"[pm-research] {market_id}: cache hit (age < {ttl_hours}h)",
            file=sys.stderr,
        )
    else:
        # --- Scrape ---
        try:
            sources_data = scrape_all(title)
        except Exception as exc:
            print(
                f"[pm-research] scrape_all failed for {market_id}: {exc}",
                file=sys.stderr,
            )
            return {
                **base,
                "sentiment": None,
                "gap_analysis": None,
                "research_skipped": True,
                "skip_reason": f"scrape error: {exc}",
            }

        source_count = sources_data.get("source_count", 0)

        # --- Quality gate: minimum sources ---
        if source_count < min_sources_required:
            print(
                f"[pm-research] {market_id}: only {source_count} sources "
                f"(need {min_sources_required}) — skipping",
                file=sys.stderr,
            )
            return {
                **base,
                "sentiment": None,
                "gap_analysis": None,
                "research_skipped": True,
                "skip_reason": f"insufficient sources: {source_count} < {min_sources_required}",
            }

        # --- Classify ---
        try:
            sources_list = sources_data.get("sources", [])
            sentiment_result = classify(sources_list, title)
        except Exception as exc:
            print(
                f"[pm-research] classify failed for {market_id}: {exc}",
                file=sys.stderr,
            )
            return {
                **base,
                "sentiment": None,
                "gap_analysis": None,
                "research_skipped": True,
                "skip_reason": f"classify error: {exc}",
            }

        sentiment_dict = asdict(sentiment_result)
        _save_cache(market_id, sources_data, sentiment_dict)

    # --- Quality gate: confidence threshold (flag, don't skip) ---
    confidence = sentiment_dict.get("confidence", 0.0)
    low_confidence = confidence < confidence_threshold
    if low_confidence:
        print(
            f"[pm-research] {market_id}: low confidence "
            f"({confidence:.2f} < {confidence_threshold}) — flagged",
            file=sys.stderr,
        )

    # --- Gap analysis ---
    gap = compute_gap_analysis(sentiment_dict.get("score", 0.0), current_yes_price)

    return {
        **base,
        "sentiment": sentiment_dict,
        "gap_analysis": gap,
        "low_confidence": low_confidence,
        "research_skipped": False,
        "skip_reason": None,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse  # noqa: PLC0415
    parser = argparse.ArgumentParser(description="Research pipeline — enriches scan candidates")
    parser.add_argument(
        "--input",
        default=None,
        metavar="FILE",
        help="Path to candidates JSON file (default: read from stdin)",
    )
    args = parser.parse_args()

    settings = load_settings()
    research_cfg = settings["research"]
    min_sources_required: int = research_cfg["min_sources_required"]
    confidence_threshold: float = research_cfg["sentiment_confidence_threshold"]
    ttl_hours: float = float(research_cfg.get("cache_ttl_hours", 4))

    # Read pm-scan output from --input file or stdin
    try:
        if args.input:
            with open(args.input) as _f:
                scan_output = json.load(_f)
        else:
            scan_output = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[pm-research] Failed to parse input JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    scan_id: str = scan_output.get("scan_id", "scan_unknown")
    candidates: list[dict[str, Any]] = scan_output.get("candidates", [])

    if not candidates:
        print("[pm-research] No candidates received from pm-scan — nothing to do.", file=sys.stderr)
        sys.exit(0)

    print(
        f"[pm-research] Processing {len(candidates)} candidates (scan_id={scan_id})",
        file=sys.stderr,
    )

    enriched_candidates = []
    for i, candidate in enumerate(candidates, 1):
        market_id = candidate.get("market_id", f"unknown_{i}")
        print(f"[pm-research] [{i}/{len(candidates)}] {market_id}", file=sys.stderr)
        enriched = process_candidate(candidate, min_sources_required, confidence_threshold, ttl_hours)
        enriched_candidates.append(enriched)

    output = {
        "scan_id": scan_id,
        "enriched_at": datetime.now(timezone.utc).isoformat(),
        "candidates": enriched_candidates,
    }

    # Write to data/enriched_{scan_id}.json
    data_dir = _PROJECT_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    output_path = data_dir / f"enriched_{scan_id}.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[pm-research] Wrote enriched output to {output_path}", file=sys.stderr)

    # Also print to stdout for pipeline chaining
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
