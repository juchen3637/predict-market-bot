"""
cost_tracker.py — AI API Cost Tracking for predict-market-bot

Records per-call token costs to data/ai_costs.jsonl and enforces the
daily budget configured in settings.yaml (cost_control.max_daily_ai_cost_usd).

One builder reported heartbeat checks alone cost $50/day — this module
prevents runaway spend by raising BudgetExceededError before each LLM call.

Usage:
    from cost_tracker import record_cost, get_daily_cost, check_budget
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_COST_LOG = _PROJECT_ROOT / "data" / "ai_costs.jsonl"


# ---------------------------------------------------------------------------
# Per-token pricing (USD per 1M tokens)
# Rates as of mid-2025; update here if provider pricing changes.
# ---------------------------------------------------------------------------

_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":  {"input": 0.80,  "output": 4.00},
    # OpenAI
    "gpt-5-mini-2025-08-07":      {"input": 0.15,  "output": 0.60},
    "gpt-4o-mini":                {"input": 0.15,  "output": 0.60},
    # Google
    "gemini-2.5-flash":           {"input": 0.075, "output": 0.30},
}

_FALLBACK_PRICING: dict[str, float] = {"input": 1.00, "output": 5.00}


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class BudgetExceededError(RuntimeError):
    """Raised when today's AI spend would exceed max_daily_ai_cost_usd."""


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for one API call."""
    rates = _PRICING.get(model, _FALLBACK_PRICING)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def record_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    caller: str = "",
) -> float:
    """
    Append one API call record to ai_costs.jsonl.
    Returns the USD cost of this call.
    """
    cost = _cost_usd(model, input_tokens, output_tokens)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 8),
        "caller": caller,
    }
    _COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_COST_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return cost


def get_daily_cost(date_str: str | None = None) -> float:
    """
    Sum all costs recorded today (UTC). Pass date_str (YYYY-MM-DD) to query
    a specific date; defaults to today.
    Returns 0.0 if log does not exist.
    """
    if not _COST_LOG.exists():
        return 0.0

    today = date_str or datetime.now(timezone.utc).date().isoformat()
    total = 0.0
    try:
        with open(_COST_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("ts", "").startswith(today):
                        total += float(entry.get("cost_usd", 0.0))
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue
    except OSError:
        return 0.0
    return round(total, 6)


def check_budget(settings: dict[str, Any]) -> None:
    """
    Raise BudgetExceededError if today's spend is at or above the daily limit.

    Args:
        settings: parsed settings.yaml dict; reads cost_control.max_daily_ai_cost_usd
    """
    limit = float(
        settings.get("cost_control", {}).get("max_daily_ai_cost_usd", 30.0)
    )
    spent = get_daily_cost()
    if spent >= limit:
        raise BudgetExceededError(
            f"Daily AI budget exhausted: ${spent:.4f} spent of ${limit:.2f} limit. "
            "No further LLM calls permitted today."
        )
