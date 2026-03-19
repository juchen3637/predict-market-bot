"""
validate_risk.py — Risk Gate Validator for pm-risk skill

Deterministic risk checks. ALL gates must pass before any trade is executed.
This is code, not language instructions — results are unambiguous.

Gates:
  1. Edge check        (edge >= min_edge)
  2. Ensemble check    (enough models agreed)
  3. Position size     (kelly size <= max_position_pct)
  4. Max positions     (open positions < max_concurrent)
  5. VaR check         (portfolio VaR within daily limit)
  6. Drawdown check    (current drawdown < kill switch threshold)
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from config_loader import load_settings, TRADE_LOG_PATH, STOP_FILE_PATH  # noqa: E402


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class RiskDecision:
    approved: bool
    rejection_reason: str | None
    gates_checked: list[str]
    gates_failed: list[str]


# ---------------------------------------------------------------------------
# Portfolio State Loader
# ---------------------------------------------------------------------------

def load_portfolio_state() -> dict[str, Any]:
    """Compute current portfolio state from trade log."""
    if not TRADE_LOG_PATH.exists():
        return {"open_positions": 0, "current_drawdown": 0.0, "daily_pnl": 0.0, "peak_value": 1.0}

    open_positions = 0
    total_pnl = 0.0
    daily_pnl = 0.0
    peak_value = 1.0
    current_value = 1.0

    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc).date()

    with open(TRADE_LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                if trade.get("status") not in ("placed", "paper"):
                    continue
                if trade.get("outcome") is None:
                    open_positions += 1
                elif trade.get("pnl") is not None:
                    pnl = float(trade["pnl"])
                    total_pnl += pnl
                    current_value += pnl / float(os.environ.get("BANKROLL_USD", 100))
                    peak_value = max(peak_value, current_value)
                    placed_at = trade.get("placed_at", "")
                    if placed_at:
                        trade_date = datetime.fromisoformat(
                            placed_at.replace("Z", "+00:00")
                        ).date()
                        if trade_date == today:
                            daily_pnl += pnl
            except (json.JSONDecodeError, ValueError, KeyError):
                continue

    drawdown = max(0.0, (peak_value - current_value) / peak_value) if peak_value > 0 else 0.0
    bankroll = float(os.environ.get("BANKROLL_USD", 100))
    daily_loss_pct = abs(min(0.0, daily_pnl)) / bankroll if bankroll > 0 else 0.0

    return {
        "open_positions": open_positions,
        "current_drawdown": drawdown,
        "daily_pnl": daily_pnl,
        "daily_loss_pct": daily_loss_pct,
        "peak_value": peak_value,
        "current_value": current_value,
    }


# ---------------------------------------------------------------------------
# Market Family Extraction
# ---------------------------------------------------------------------------

_CPI_RE = re.compile(r"will cpi rise more than .+ in (\w+)\s+(\d{4})", re.IGNORECASE)
_SP500_RE = re.compile(r"will the s&p 500 be between .+ on (\w+)\s+(\d+),?\s*(\d{4})", re.IGNORECASE)
_BTC_RE = re.compile(r"will (?:btc|bitcoin).+on (\w+)\s+(\d+),?\s*(\d{4})", re.IGNORECASE)


def _extract_market_family(title: str, market_id: str) -> str:
    """Extract a normalized family key from known title patterns. Falls back to market_id."""
    m = _CPI_RE.search(title)
    if m:
        return f"cpi_{m.group(1).lower()}_{m.group(2)}"
    m = _SP500_RE.search(title)
    if m:
        return f"sp500_{m.group(1).lower()}_{m.group(2)}_{m.group(3)}"
    m = _BTC_RE.search(title)
    if m:
        return f"btc_{m.group(1).lower()}_{m.group(2)}_{m.group(3)}"
    return market_id  # fallback — unique family, no grouping


# ---------------------------------------------------------------------------
# Open Market IDs Loader
# ---------------------------------------------------------------------------

def load_open_market_ids() -> set[str]:
    """Return set of market_ids that currently have an open position."""
    open_markets: set[str] = set()
    if not TRADE_LOG_PATH.exists():
        return open_markets
    try:
        with open(TRADE_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if trade.get("status") in ("placed", "paper") and trade.get("outcome") is None:
                    mid = trade.get("market_id")
                    if mid:
                        open_markets.add(mid)
    except OSError:
        pass
    return open_markets


def load_open_market_families() -> dict[str, int]:
    """Return mapping of family_key → count of open positions in that family."""
    counts: dict[str, int] = {}
    if not TRADE_LOG_PATH.exists():
        return counts
    try:
        with open(TRADE_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if trade.get("status") in ("placed", "paper") and trade.get("outcome") is None:
                    family = _extract_market_family(
                        trade.get("title", ""), trade.get("market_id", "")
                    )
                    counts[family] = counts.get(family, 0) + 1
    except OSError:
        pass
    return counts


# ---------------------------------------------------------------------------
# Individual Gate Checks
# ---------------------------------------------------------------------------

def check_kill_switch() -> bool:
    """Returns True (blocked) if STOP file exists."""
    return STOP_FILE_PATH.exists()


def check_edge(edge: float, min_edge: float) -> bool:
    return abs(edge) >= min_edge


def check_ensemble(models_responded: int, min_required: int) -> bool:
    return models_responded >= min_required


def check_position_size(kelly_size_usd: float, bankroll: float, max_pct: float) -> bool:
    if bankroll <= 0:
        return False
    return (kelly_size_usd / bankroll) <= max_pct


def check_max_positions(open_positions: int, max_concurrent: int) -> bool:
    return open_positions < max_concurrent


def check_var(daily_pnl: float, bankroll: float, max_daily_loss_pct: float) -> bool:
    """Blocks if today's losses already exceed daily limit."""
    if bankroll <= 0:
        return False
    daily_loss_pct = abs(min(0.0, daily_pnl)) / bankroll
    return daily_loss_pct < max_daily_loss_pct


def check_drawdown(current_drawdown: float, max_drawdown: float) -> bool:
    return current_drawdown < max_drawdown


# ---------------------------------------------------------------------------
# Main Validation Runner
# ---------------------------------------------------------------------------

def validate(
    signal: dict[str, Any],
    kelly_size_usd: float,
    settings: dict[str, Any] | None = None,
    portfolio_state: dict[str, Any] | None = None,
) -> RiskDecision:
    """Run all risk gates and return a RiskDecision."""
    if settings is None:
        settings = load_settings()

    risk_cfg = settings["risk"]
    portfolio = portfolio_state if portfolio_state is not None else load_portfolio_state()
    bankroll = float(os.environ.get("BANKROLL_USD", 100))

    gates_checked = []
    gates_failed = []

    # Kill switch check
    gates_checked.append("kill_switch")
    if check_kill_switch():
        gates_failed.append("kill_switch")
        return RiskDecision(
            approved=False,
            rejection_reason="kill_switch_active",
            gates_checked=gates_checked,
            gates_failed=gates_failed,
        )

    # Gate 1: Edge
    gates_checked.append("edge")
    if not check_edge(float(signal.get("edge", 0)), risk_cfg["min_edge_to_signal"] if "min_edge_to_signal" in risk_cfg else 0.04):
        gates_failed.append("edge")

    # Gate 2: Ensemble agreement
    gates_checked.append("ensemble")
    if not check_ensemble(int(signal.get("models_responded", 0)), 3):
        gates_failed.append("ensemble")

    # Gate 3: Position size
    gates_checked.append("position_size")
    if not check_position_size(kelly_size_usd, bankroll, risk_cfg["max_position_pct_bankroll"]):
        gates_failed.append("position_size")

    # Gate 4: Max concurrent positions
    gates_checked.append("max_positions")
    if not check_max_positions(portfolio["open_positions"], risk_cfg["max_concurrent_positions"]):
        gates_failed.append("max_positions")

    # Gate 5: Daily loss (VaR proxy)
    gates_checked.append("var")
    if not check_var(portfolio["daily_pnl"], bankroll, risk_cfg["max_daily_loss_pct"]):
        gates_failed.append("var")

    # Gate 6: Drawdown kill switch
    gates_checked.append("drawdown")
    if not check_drawdown(portfolio["current_drawdown"], risk_cfg["max_drawdown_pct"]):
        gates_failed.append("drawdown")
        # Auto-create STOP file
        STOP_FILE_PATH.touch()
        print(
            f"[pm-risk] KILL SWITCH: Drawdown {portfolio['current_drawdown']:.1%} "
            f"exceeded {risk_cfg['max_drawdown_pct']:.1%}. STOP file created.",
            file=sys.stderr,
        )

    approved = len(gates_failed) == 0
    rejection_reason = gates_failed[0] if gates_failed else None

    return RiskDecision(
        approved=approved,
        rejection_reason=rejection_reason,
        gates_checked=gates_checked,
        gates_failed=gates_failed,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    data = json.load(sys.stdin)
    signal = data.get("signal", {})
    kelly_size_usd = float(data.get("kelly_size_usd", 0))

    from dataclasses import asdict
    result = validate(signal, kelly_size_usd)
    print(json.dumps(asdict(result), indent=2))
    sys.exit(0 if result.approved else 1)


if __name__ == "__main__":
    main()
