"""
postmortem.py — Loss Classifier for pm-compound skill

Analyzes losing trades, classifies the failure mode, and appends a lesson
to the failure knowledge base (failure_log.md).

The pm-scan skill reads this log at the start of every cycle to avoid
repeating known mistakes.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


FAILURE_LOG = Path(__file__).parent.parent / "references" / "failure_log.md"


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
    p_model = float(trade.get("p_model", 0.5))
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

    print(json.dumps({
        "trade_id": trade.get("trade_id"),
        "failure_category": category,
        "description": FAILURE_CATEGORIES.get(category, ""),
        "logged": True,
    }, indent=2))


if __name__ == "__main__":
    main()
