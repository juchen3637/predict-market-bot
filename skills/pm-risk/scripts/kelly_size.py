"""
kelly_size.py — Fractional Kelly Position Sizer for pm-risk skill

Computes the optimal position size using the Kelly Criterion with a
fractional multiplier to reduce variance.

Formula: f* = (p * b - q) / b
Applied:  f  = kelly_fraction * f*  (default: 0.25 = quarter-Kelly)
Capped:   size <= max_position_pct * bankroll

Reference: github.com/suislanchez/polymarket-kalshi-weather-bot
  - Proven implementation with $1,325 profit (Kelly sizing + execution)
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from config_loader import load_settings  # noqa: E402


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------

@dataclass
class PositionSize:
    kelly_fraction_used: float    # The fractional multiplier applied
    full_kelly_pct: float         # Full Kelly as fraction of bankroll
    fractional_kelly_pct: float   # After multiplier
    size_usd: float               # Dollar amount to bet
    size_usd_capped: float        # After max_position_pct cap
    contracts: int                # Number of contracts (size_usd_capped / entry_price)
    capped: bool                  # Was the cap applied?
    rationale: str


# ---------------------------------------------------------------------------
# Kelly Calculation
# ---------------------------------------------------------------------------

def kelly_criterion(p_win: float, entry_price: float) -> float:
    """
    Compute full Kelly fraction.

    p_win:       probability of winning (p_model for Yes, 1-p_model for No)
    entry_price: cost per contract (0.0–1.0), payout is $1.00

    Net odds b = (payout - cost) / cost = (1 - entry_price) / entry_price
    Kelly f* = (p * b - q) / b
    """
    if entry_price <= 0 or entry_price >= 1:
        return 0.0
    p = max(0.01, min(0.99, p_win))
    q = 1.0 - p
    b = (1.0 - entry_price) / entry_price  # net odds
    if b <= 0:
        return 0.0
    f_star = (p * b - q) / b
    return max(0.0, f_star)  # Negative Kelly = don't bet


def compute_position_size(
    p_model: float,
    direction: str,           # "yes" | "no"
    entry_price: float,
    bankroll: float,
    kelly_fraction: float,
    max_position_pct: float,
) -> PositionSize:
    """
    Compute fractional Kelly position size.

    p_model:          ensemble probability for Yes outcome
    direction:        "yes" or "no" — which side we're trading
    entry_price:      cost per contract
    bankroll:         current total bankroll in USD
    kelly_fraction:   multiplier (0.25 = quarter-Kelly)
    max_position_pct: hard cap as fraction of bankroll
    """
    p_win = p_model if direction == "yes" else (1.0 - p_model)
    full_kelly = kelly_criterion(p_win, entry_price)
    fractional_kelly = full_kelly * kelly_fraction

    size_usd = fractional_kelly * bankroll
    max_size_usd = max_position_pct * bankroll
    capped = size_usd > max_size_usd
    size_usd_capped = min(size_usd, max_size_usd)

    contracts = max(1, math.floor(size_usd_capped / entry_price)) if entry_price > 0 else 0

    rationale = (
        f"p_win={p_win:.3f}, b={((1-entry_price)/entry_price):.2f}, "
        f"full_kelly={full_kelly:.3f}, "
        f"fractional({kelly_fraction}x)={fractional_kelly:.3f}, "
        f"size=${size_usd:.2f}"
        + (f" → capped at ${max_size_usd:.2f} ({max_position_pct:.0%} limit)" if capped else "")
    )

    return PositionSize(
        kelly_fraction_used=kelly_fraction,
        full_kelly_pct=round(full_kelly, 6),
        fractional_kelly_pct=round(fractional_kelly, 6),
        size_usd=round(size_usd, 2),
        size_usd_capped=round(size_usd_capped, 2),
        contracts=contracts,
        capped=capped,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    settings = load_settings()
    risk_cfg = settings["risk"]

    data = json.load(sys.stdin)
    p_model = float(data["p_model"])
    direction = data.get("direction", "yes")
    entry_price = float(data["entry_price"])
    bankroll = float(os.environ.get("BANKROLL_USD", 100))
    kelly_fraction = risk_cfg.get("kelly_fraction", 0.25)
    max_position_pct = risk_cfg.get("max_position_pct_bankroll", 0.05)

    result = compute_position_size(
        p_model, direction, entry_price, bankroll, kelly_fraction, max_position_pct
    )

    from dataclasses import asdict
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
