"""
postmortem.py — Loss Classifier for pm-compound skill

Analyzes losing trades, classifies the failure mode, and appends a lesson
to the failure knowledge base (failure_log.md).

The pm-scan skill reads this log at the start of every cycle to avoid
repeating known mistakes.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


FAILURE_LOG = Path(__file__).parent.parent / "references" / "failure_log.md"

_PATTERNS_RE = re.compile(r"<!-- PATTERNS_JSON: (\{.*?\}) -->", re.DOTALL)


# ---------------------------------------------------------------------------
# Failure Classification
# ---------------------------------------------------------------------------

FAILURE_CATEGORIES = {
    "bad_calibration": "Model probability was significantly wrong (>15% off actual outcome)",
    "liquidity_trap": "Market lacked depth; couldn't enter/exit at expected prices",
    "stale_data": "Research data was outdated by time of trade",
    "model_disagreement": "LLMs disagreed but trade proceeded; high variance prediction",
    "black_swan": "Unpredictable external shock (breaking news, regulatory change)",
    "execution_error": "Technical failure in order placement or fill",
    "spread_cost": "Edge existed but bid-ask spread consumed the profit",
    "unknown": "Failure cause not yet determined — manual review needed",
}


def classify_failure(trade: dict) -> str:
    """
    Auto-classify failure based on trade metadata.
    Falls back to 'unknown' — manual reclassification is encouraged.
    """
    edge = abs(float(trade.get("edge", 0)))
    models_responded = int(trade.get("models_responded", 5))
    rejection_reason = trade.get("rejection_reason", "")

    if rejection_reason and "execution" in rejection_reason.lower():
        return "execution_error"
    if rejection_reason and "slippage" in rejection_reason.lower():
        return "liquidity_trap"
    if models_responded < 3:
        return "model_disagreement"
    # If edge was tiny, likely spread ate it
    if edge < 0.06:
        return "spread_cost"
    # Otherwise assume calibration issue
    return "bad_calibration"


# ---------------------------------------------------------------------------
# PATTERNS_JSON — machine-readable feedback for pm-scan
# ---------------------------------------------------------------------------

def _read_patterns() -> dict:
    """Read the current PATTERNS_JSON block from failure_log.md."""
    try:
        match = _PATTERNS_RE.search(FAILURE_LOG.read_text())
        if match:
            return json.loads(match.group(1))
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "market_ids_to_avoid": [],
        "failure_patterns_by_category": {k: [] for k in FAILURE_CATEGORIES},
    }


def _write_patterns(patterns: dict) -> None:
    """Rewrite the PATTERNS_JSON block in-place within failure_log.md."""
    try:
        content = FAILURE_LOG.read_text()
        new_block = f"<!-- PATTERNS_JSON: {json.dumps(patterns)} -->"
        updated = _PATTERNS_RE.sub(new_block, content)
        FAILURE_LOG.write_text(updated)
    except OSError:
        pass


def update_patterns(trade: dict, category: str) -> None:
    """Update machine-readable PATTERNS_JSON after a failure is classified.

    Adds the market_id to market_ids_to_avoid (so the scanner deprioritizes
    it on future cycles) and tracks it under its failure category.
    """
    patterns = _read_patterns()

    market_id = trade.get("market_id", "")
    if market_id and market_id not in patterns["market_ids_to_avoid"]:
        patterns["market_ids_to_avoid"].append(market_id)

    by_cat = patterns.setdefault(
        "failure_patterns_by_category", {k: [] for k in FAILURE_CATEGORIES}
    )
    cat_list = by_cat.setdefault(category, [])
    if market_id and market_id not in cat_list:
        cat_list.append(market_id)

    _write_patterns(patterns)


# ---------------------------------------------------------------------------
# Failure Log
# ---------------------------------------------------------------------------

def format_failure_entry(trade: dict, category: str) -> str:
    """Format a failure log entry in Markdown."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"\n## [{now}] {trade.get('market_id', 'unknown')} — {category}\n\n"
        f"**Platform**: {trade.get('platform', 'unknown')}\n"
        f"**Direction**: {trade.get('direction', 'unknown')}\n"
        f"**Entry Price**: {trade.get('entry_price', 'N/A')}\n"
        f"**p_model**: {trade.get('p_model', 'N/A')}\n"
        f"**Edge**: {trade.get('edge', 'N/A')}\n"
        f"**P&L**: {trade.get('pnl', 'N/A')}\n"
        f"**Failure Category**: `{category}` — {FAILURE_CATEGORIES.get(category, '')}\n\n"
        f"**Lesson**: [TODO: add specific lesson learned]\n\n"
        f"**Avoid in future**: [TODO: add specific condition to filter out]\n\n"
        f"---\n"
    )


def append_failure_log(entry: str) -> None:
    """Append an entry to the failure log Markdown file."""
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FAILURE_LOG, "a") as f:
        f.write(entry)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    trade = json.load(sys.stdin)

    if trade.get("outcome") != "loss":
        print(json.dumps({"skipped": True, "reason": "not_a_loss"}))
        return

    category = classify_failure(trade)
    entry = format_failure_entry(trade, category)
    append_failure_log(entry)
    update_patterns(trade, category)

    print(json.dumps({
        "trade_id": trade.get("trade_id"),
        "failure_category": category,
        "description": FAILURE_CATEGORIES.get(category, ""),
        "logged": True,
    }, indent=2))


if __name__ == "__main__":
    main()
