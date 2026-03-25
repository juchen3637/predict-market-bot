"""
predict_pipeline.py — Prediction Orchestrator for pm-predict skill

Reads enriched research output from stdin, runs LLM ensemble (with optional
XGBoost when model is trained), computes edge vs market price, applies
min-edge gate, and writes trade signals for pm-risk to consume.

Usage:
    python skills/pm-scan/scripts/filter_markets.py \
      | python skills/pm-research/scripts/research_pipeline.py \
      | python skills/pm-predict/scripts/predict_pipeline.py

Input (stdin): pm-research enriched output JSON
    {
        "scan_id": "scan_20260316T120000",
        "candidates": [
            {
                "market_id": "...", "title": "...", "current_yes_price": 0.45,
                "days_to_expiry": 14, "volume_24h": 500, "open_interest": 200,
                "category": "finance", "anomaly_flags": [],
                "sentiment": {"score": 0.42, "label": "bullish", ...},
                "gap_analysis": {"direction": "long", "signal_strength": 0.18},
                "low_confidence": false,
                "research_skipped": false,
                "skip_reason": null
            }
        ]
    }

Output (stdout + data/signals_{scan_id}.json):
    {
        "scan_id": "...",
        "signaled_at": "ISO 8601",
        "brier_status": {...},
        "signals": [...]
    }
"""

from __future__ import annotations

import json
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
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]  # skills/pm-predict/scripts → project root

sys.path.insert(0, str(_SCRIPT_DIR))

import predict_cache  # noqa: E402
from brier_score import compute_rolling_brier  # noqa: E402
from llm_consensus import ConsensusResult, run_consensus  # noqa: E402
from xgboost_features import ModelNotTrainedError, predict as xgboost_predict  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_settings() -> dict[str, Any]:
    settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
    with open(settings_path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Research Summary Builder
# ---------------------------------------------------------------------------

def build_research_summary(candidate: dict[str, Any]) -> str:
    """Build a concise research summary string from sentiment fields for LLM context."""
    sentiment = candidate.get("sentiment") or {}
    gap = candidate.get("gap_analysis") or {}

    score = sentiment.get("score", 0.0)
    label = sentiment.get("label", "unknown")
    confidence = sentiment.get("confidence", 0.0)
    sources = sentiment.get("sources", [])
    sources_str = ", ".join(sources) if sources else "none"

    gap_direction = gap.get("direction", "none")
    gap_strength = gap.get("signal_strength", 0.0)

    return (
        f"Sentiment: {label} (score={score:.2f}, confidence={confidence:.2f}, sources: {sources_str})\n"
        f"Gap signal: {gap_direction} (strength={gap_strength:.2f})"
    )


# ---------------------------------------------------------------------------
# Per-candidate prediction
# ---------------------------------------------------------------------------

def process_candidate(
    candidate: dict[str, Any],
    min_edge_to_signal: float,
    cache: dict | None = None,
    ttl_hours: float = 2.0,
    price_threshold: float = 0.03,
) -> dict[str, Any]:
    """
    Run LLM consensus + optional XGBoost for one candidate.
    Returns a signal dict matching the output schema.

    When cache, ttl_hours, and price_threshold are provided the function
    checks the cache before calling the LLM ensemble.  Cache is bypassed
    entirely for research_skipped candidates.
    """
    market_id = candidate.get("market_id", "unknown")
    title = candidate.get("title", "")
    current_yes_price = float(candidate.get("current_yes_price", 0.5))
    sentiment = candidate.get("sentiment") or {}

    base = {
        "market_id": market_id,
        "title": title,
        "current_yes_price": current_yes_price,
        "sentiment_label": sentiment.get("label"),
        "low_confidence": candidate.get("low_confidence", False),
    }

    # --- Skip candidates that research already flagged (bypass cache) ---
    if candidate.get("research_skipped"):
        return {
            **base,
            "p_model": None,
            "edge": None,
            "direction": None,
            "llm_consensus": None,
            "xgboost_prob": None,
            "predict_skipped": True,
            "skip_reason": candidate.get("skip_reason") or "research_skipped",
            "cache_hit": False,
        }

    # --- Cache lookup (only when a cache dict is supplied) ---
    if cache is not None:
        try:
            cached_signal = predict_cache.lookup(
                cache, market_id, current_yes_price, ttl_hours, price_threshold
            )
        except Exception as exc:
            print(
                f"[pm-predict] Cache lookup error for {market_id}: {exc}",
                file=sys.stderr,
            )
            cached_signal = None

        if cached_signal is not None:
            # Recompute edge + direction with the live price to stay accurate.
            # If p_model is None the cached entry is corrupt — treat as a miss
            # and fall through to the LLM block below.
            p_model = cached_signal.get("p_model")
            if p_model is not None:
                edge = round(float(p_model) - current_yes_price, 4)
                direction = "long" if edge > 0 else "short"
                # Re-apply min-edge gate with the refreshed edge.
                if abs(edge) < min_edge_to_signal:
                    return {
                        **base,
                        **cached_signal,
                        "edge": edge,
                        "direction": direction,
                        "predict_skipped": True,
                        "skip_reason": (
                            f"edge {edge:.4f} below min_edge_to_signal {min_edge_to_signal}"
                        ),
                        "cache_hit": True,
                    }
                return {
                    **base,
                    **cached_signal,
                    "edge": edge,
                    "direction": direction,
                    "predict_skipped": False,
                    "skip_reason": None,
                    "cache_hit": True,
                }

    # --- Build research summary for LLM context ---
    research_summary = build_research_summary(candidate)

    # --- LLM Consensus ---
    llm_prob: float
    llm_consensus_dict: dict[str, Any]
    try:
        result: ConsensusResult = run_consensus(title, current_yes_price, research_summary)
        llm_prob = result.consensus_prob
        llm_consensus_dict = {
            "consensus_prob": result.consensus_prob,
            "models_responded": result.models_responded,
            "weighted_agreement": result.weighted_agreement,
        }
    except Exception as exc:
        print(
            f"[pm-predict] LLM consensus failed for {market_id}: {exc}",
            file=sys.stderr,
        )
        return {
            **base,
            "p_model": None,
            "edge": None,
            "direction": None,
            "llm_consensus": None,
            "xgboost_prob": None,
            "predict_skipped": True,
            "skip_reason": f"llm_consensus error: {exc}",
            "cache_hit": False,
        }

    # --- XGBoost (optional — falls back gracefully if not trained) ---
    xgboost_prob: float | None = None
    try:
        xgboost_prob = xgboost_predict(candidate)
    except ModelNotTrainedError:
        pass  # Expected until model is trained in Phase 2B
    except Exception as exc:
        print(
            f"[pm-predict] XGBoost inference failed for {market_id}: {exc}",
            file=sys.stderr,
        )

    # --- Combine probabilities ---
    if xgboost_prob is not None:
        p_model = 0.4 * xgboost_prob + 0.6 * llm_prob
    else:
        p_model = llm_prob

    p_model = round(p_model, 4)

    # --- Compute edge ---
    edge = round(p_model - current_yes_price, 4)

    # --- Direction ---
    direction = "long" if edge > 0 else "short"

    # --- Apply min-edge gate ---
    if abs(edge) < min_edge_to_signal:
        return {
            **base,
            "p_model": p_model,
            "edge": edge,
            "direction": direction,
            "llm_consensus": llm_consensus_dict,
            "xgboost_prob": xgboost_prob,
            "predict_skipped": True,
            "skip_reason": f"edge {edge:.4f} below min_edge_to_signal {min_edge_to_signal}",
            "cache_hit": False,
        }

    signal = {
        **base,
        "p_model": p_model,
        "edge": edge,
        "direction": direction,
        "llm_consensus": llm_consensus_dict,
        "xgboost_prob": xgboost_prob,
        "predict_skipped": False,
        "skip_reason": None,
        "cache_hit": False,
    }

    return signal


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse  # noqa: PLC0415
    parser = argparse.ArgumentParser(description="Predict pipeline — generates trade signals")
    parser.add_argument(
        "--input",
        default=None,
        metavar="FILE",
        help="Path to enriched candidates JSON file (default: read from stdin)",
    )
    args = parser.parse_args()

    settings = load_settings()
    predict_cfg = settings["predict"]
    min_edge_to_signal: float = predict_cfg["min_edge_to_signal"]
    ttl_hours: float = float(predict_cfg.get("signal_cache_ttl_hours", 2.0))
    price_threshold: float = float(predict_cfg.get("signal_cache_price_move_threshold", 0.03))

    # Read enriched research output from --input file or stdin
    try:
        if args.input:
            with open(args.input) as _f:
                enriched_output = json.load(_f)
        else:
            enriched_output = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[pm-predict] Failed to parse input JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    scan_id: str = enriched_output.get("scan_id", "scan_unknown")
    candidates: list[dict[str, Any]] = enriched_output.get("candidates", [])

    if not candidates:
        print("[pm-predict] No candidates received — nothing to do.", file=sys.stderr)
        sys.exit(0)

    print(
        f"[pm-predict] Processing {len(candidates)} candidates (scan_id={scan_id})",
        file=sys.stderr,
    )

    # --- Load signal cache ---
    cache_path = _PROJECT_ROOT / "data" / "predict_cache.json"
    try:
        cache: dict = predict_cache.load_cache(cache_path)
    except Exception as exc:
        print(f"[pm-predict] Warning: cache load failed: {exc}", file=sys.stderr)
        cache = {}

    cache_hits = 0
    cache_misses = 0

    signals = []
    for i, candidate in enumerate(candidates, 1):
        market_id = candidate.get("market_id", f"unknown_{i}")
        print(f"[pm-predict] [{i}/{len(candidates)}] {market_id}", file=sys.stderr)

        signal = process_candidate(
            candidate,
            min_edge_to_signal,
            cache=cache,
            ttl_hours=ttl_hours,
            price_threshold=price_threshold,
        )

        if signal.get("cache_hit"):
            cache_hits += 1
        elif not candidate.get("research_skipped"):
            cache_misses += 1
            # Update cache with the new signal result when p_model is valid.
            if signal.get("p_model") is not None:
                try:
                    cache = predict_cache.store(
                        cache, market_id, float(candidate.get("current_yes_price", 0.5)), signal
                    )
                except Exception as exc:
                    print(
                        f"[pm-predict] Warning: cache store failed for {market_id}: {exc}",
                        file=sys.stderr,
                    )

        signals.append(signal)

    print(
        f"[pm-predict] Signal cache: {cache_hits} hits, {cache_misses} misses",
        file=sys.stderr,
    )

    # --- Save signal cache ---
    try:
        predict_cache.save_cache(cache_path, cache, ttl_hours=ttl_hours)
    except Exception as exc:
        print(f"[pm-predict] Warning: cache save failed: {exc}", file=sys.stderr)

    # --- Brier Score ---
    print("[pm-predict] Computing rolling Brier Score...", file=sys.stderr)
    brier_status = compute_rolling_brier()

    output = {
        "scan_id": scan_id,
        "signaled_at": datetime.now(timezone.utc).isoformat(),
        "brier_status": brier_status,
        "signals": signals,
    }

    # Write to data/signals_{scan_id}.json
    data_dir = _PROJECT_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    output_path = data_dir / f"signals_{scan_id}.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[pm-predict] Wrote signals to {output_path}", file=sys.stderr)

    # Print to stdout for pipeline chaining
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
