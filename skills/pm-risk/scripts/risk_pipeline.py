"""
risk_pipeline.py — Risk Orchestrator for pm-risk skill

Reads signals from pm-predict (stdin or --file), runs Kelly position sizing
and risk gate validation on each actionable signal, and writes trade orders
for pm-execution to consume.

Usage:
    # Pipeline chain:
    python skills/pm-predict/scripts/predict_pipeline.py \
      | python skills/pm-risk/scripts/risk_pipeline.py

    # Standalone with file input:
    python skills/pm-risk/scripts/risk_pipeline.py \
      --file data/signals_scan_20260317T151545.json

Input (stdin or --file): pm-predict signals JSON
Output (stdout + data/orders_{scan_id}.json): trade orders JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Resolve project root so imports work regardless of CWD
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from config_loader import DATA_DIR, STOP_FILE_PATH, load_settings  # noqa: E402
from kelly_size import compute_position_size  # noqa: E402
from validate_risk import load_open_market_ids, load_portfolio_state, validate  # noqa: E402
import execute_order as _executor  # noqa: E402


# ---------------------------------------------------------------------------
# Pipeline Result
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Full result from running the risk pipeline for a single signal."""
    signal: dict
    risk_result: dict
    size: dict | None
    order_result: dict | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def detect_platform(market_id: str) -> str:
    """Infer platform from market_id prefix."""
    if market_id.startswith("0x"):
        return "polymarket"
    if market_id.upper().startswith("KX"):
        return "kalshi"
    raise ValueError(f"Cannot detect platform for market_id: {market_id!r}")


def direction_to_kelly(direction: str) -> str:
    """Map pm-predict direction to Kelly direction vocabulary."""
    return "yes" if direction in ("yes", "long") else "no"


def compute_entry_price(signal: dict[str, Any]) -> float:
    """
    Entry price per contract.
    Long (buy Yes): use current_yes_price.
    Short (buy No): use 1 - current_yes_price (No contract price).
    """
    yes_price = float(signal["current_yes_price"])
    if signal.get("direction") == "short":
        return round(1.0 - yes_price, 6)
    return yes_price


# ---------------------------------------------------------------------------
# Per-signal processing
# ---------------------------------------------------------------------------

def process_signal(
    signal: dict[str, Any],
    settings: dict[str, Any],
    bankroll: float,
    portfolio_state: dict[str, Any],
) -> dict[str, Any]:
    """
    Run Kelly sizing + risk validation for one signal.
    Returns an order dict matching the output schema.
    """
    market_id = signal.get("market_id", "unknown")
    title = signal.get("title", "")
    direction = signal.get("direction", "long")
    current_yes_price = float(signal.get("current_yes_price", 0.5))
    p_model = signal.get("p_model")
    edge = signal.get("edge")

    base = {
        "market_id": market_id,
        "title": title,
        "platform": None,
        "direction": direction,
        "current_yes_price": current_yes_price,
        "p_model": p_model,
        "edge": edge,
    }

    # Detect platform
    try:
        base["platform"] = detect_platform(market_id)
    except ValueError as exc:
        return {
            **base,
            "kelly_fraction": None,
            "position_size_usd": None,
            "contracts": None,
            "risk_approved": False,
            "risk_flags": [],
            "order_skipped": True,
            "skip_reason": str(exc),
        }

    # Defensive: p_model / edge must not be null
    if p_model is None or edge is None:
        return {
            **base,
            "kelly_fraction": None,
            "position_size_usd": None,
            "contracts": None,
            "risk_approved": False,
            "risk_flags": [],
            "order_skipped": True,
            "skip_reason": "missing_p_model",
        }

    # Boundary entry price guard (Kelly divides by entry_price and 1-entry_price)
    entry_price = compute_entry_price(signal)
    if entry_price <= 0.0 or entry_price >= 1.0:
        return {
            **base,
            "kelly_fraction": None,
            "position_size_usd": None,
            "contracts": None,
            "risk_approved": False,
            "risk_flags": ["entry_price_boundary"],
            "order_skipped": True,
            "skip_reason": f"entry_price_at_boundary: {entry_price}",
        }

    risk_cfg = settings["risk"]
    kelly_fraction = risk_cfg["kelly_fraction"]
    max_position_pct = risk_cfg["max_position_pct_bankroll"]

    # Kelly position sizing
    kelly_direction = direction_to_kelly(direction)
    position = compute_position_size(
        p_model=float(p_model),
        direction=kelly_direction,
        entry_price=entry_price,
        bankroll=bankroll,
        kelly_fraction=kelly_fraction,
        max_position_pct=max_position_pct,
    )

    # Skip if contracts would cost more than position size (tiny bankroll edge case)
    if position.contracts > 0 and (position.contracts * entry_price > position.size_usd_capped * 1.05):
        return {
            **base,
            "kelly_fraction": round(position.fractional_kelly_pct, 6),
            "position_size_usd": position.size_usd_capped,
            "contracts": 0,
            "risk_approved": False,
            "risk_flags": ["insufficient_bankroll"],
            "order_skipped": True,
            "skip_reason": "insufficient_bankroll_for_minimum_contract",
        }

    # Attach models_responded for ensemble gate (from llm_consensus nested field)
    llm = signal.get("llm_consensus") or {}
    signal_for_validate = {
        **signal,
        "models_responded": llm.get("models_responded", 0),
    }

    # Risk validation
    decision = validate(
        signal=signal_for_validate,
        kelly_size_usd=position.size_usd_capped,
        settings=settings,
        portfolio_state=portfolio_state,
    )

    return {
        **base,
        "kelly_fraction": round(position.fractional_kelly_pct, 6),
        "position_size_usd": position.size_usd_capped,
        "contracts": position.contracts,
        "risk_approved": decision.approved,
        "risk_flags": decision.gates_failed,
        "order_skipped": not decision.approved,
        "skip_reason": decision.rejection_reason if not decision.approved else None,
    }


# ---------------------------------------------------------------------------
# Single-Signal Pipeline (includes order execution)
# ---------------------------------------------------------------------------

def run_single_signal(
    signal: dict[str, Any],
    settings: dict[str, Any] | None = None,
    bankroll: float | None = None,
) -> PipelineResult:
    """
    Run the complete pipeline (Kelly → risk → execute) for one signal.

    Accepts direction as "yes"/"no" or "long"/"short".
    Used by --signal CLI mode and orchestrator per-signal calls.
    """
    if settings is None:
        settings = load_settings()
    if bankroll is None:
        bankroll = float(os.environ.get("BANKROLL_USD", 100))

    portfolio_state = load_portfolio_state()
    risk_cfg = settings["risk"]

    # Normalize direction to yes/no
    exec_direction = direction_to_kelly(signal.get("direction", "yes"))
    signal_exec = {**signal, "direction": exec_direction}

    # Compute entry price (direction-adjusted)
    raw_entry = float(signal_exec.get("entry_price", signal_exec.get("current_yes_price", 0.5)))
    entry_price = raw_entry if exec_direction == "yes" else (1.0 - raw_entry)

    if entry_price <= 0.0 or entry_price >= 1.0:
        rejection = {"approved": False, "rejection_reason": f"entry_price_at_boundary_{entry_price}",
                     "gates_checked": [], "gates_failed": ["entry_price_boundary"]}
        return PipelineResult(signal=signal, risk_result=rejection, size=None, order_result=None)

    # Kelly position sizing
    p_model = float(signal.get("p_model", 0.5))
    position = compute_position_size(
        p_model=p_model,
        direction=exec_direction,
        entry_price=entry_price,
        bankroll=bankroll,
        kelly_fraction=risk_cfg["kelly_fraction"],
        max_position_pct=risk_cfg["max_position_pct_bankroll"],
    )

    # Risk gate validation
    llm = signal.get("llm_consensus") or {}
    signal_for_validate = {
        **signal_exec,
        "models_responded": llm.get("models_responded", signal.get("models_responded", 3)),
    }
    decision = validate(
        signal=signal_for_validate,
        kelly_size_usd=position.size_usd_capped,
        settings=settings,
        portfolio_state=portfolio_state,
    )

    if not decision.approved:
        return PipelineResult(
            signal=signal,
            risk_result=asdict(decision),
            size=asdict(position),
            order_result=None,
        )

    # Execute order
    exec_position = {
        "contracts": position.contracts,
        "size_usd_capped": position.size_usd_capped,
        "kelly_fraction_used": position.kelly_fraction_used,
    }
    trade_record = _executor.execute(signal_exec, exec_position)

    return PipelineResult(
        signal=signal,
        risk_result=asdict(decision),
        size=asdict(position),
        order_result=asdict(trade_record),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="pm-risk: Kelly sizing + risk gate pipeline")
    parser.add_argument("--file", help="Path to signals JSON file (default: read from stdin)")
    parser.add_argument("--signal", help="JSON string of a single signal to process and execute")
    args = parser.parse_args()

    # Single-signal mode: run full pipeline including execution
    if args.signal:
        try:
            signal = json.loads(args.signal)
        except json.JSONDecodeError as exc:
            print(f"[pm-risk] Invalid --signal JSON: {exc}", file=sys.stderr)
            sys.exit(1)
        result = run_single_signal(signal)
        print(json.dumps(asdict(result), indent=2))
        return

    # Read input
    try:
        if args.file:
            with open(args.file) as f:
                signals_output = json.load(f)
        else:
            signals_output = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"[pm-risk] Failed to parse input JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    scan_id: str = signals_output.get("scan_id", "scan_unknown")
    all_signals: list[dict[str, Any]] = signals_output.get("signals", [])

    # Filter to actionable signals only
    actionable = [s for s in all_signals if not s.get("predict_skipped", True)]

    print(
        f"[pm-risk] {len(actionable)} actionable signals of {len(all_signals)} total (scan_id={scan_id})",
        file=sys.stderr,
    )

    if not actionable:
        print("[pm-risk] No actionable signals — writing empty orders file.", file=sys.stderr)

    # Sort by abs(edge) descending — best opportunities processed first
    actionable = sorted(actionable, key=lambda s: abs(s.get("edge") or 0), reverse=True)

    settings = load_settings()
    bankroll = float(os.environ.get("BANKROLL_USD", 100))

    # Load portfolio state once for the whole batch
    portfolio_state = load_portfolio_state()
    open_market_ids = load_open_market_ids()
    approved_count = 0

    orders = []
    for i, signal in enumerate(actionable, 1):
        market_id = signal.get("market_id", f"unknown_{i}")
        print(f"[pm-risk] [{i}/{len(actionable)}] {market_id}", file=sys.stderr)

        # Check kill switch before each signal (drawdown breach may have fired mid-batch)
        if STOP_FILE_PATH.exists():
            orders.append({
                "market_id": market_id,
                "title": signal.get("title", ""),
                "platform": None,
                "direction": signal.get("direction"),
                "current_yes_price": signal.get("current_yes_price"),
                "p_model": signal.get("p_model"),
                "edge": signal.get("edge"),
                "kelly_fraction": None,
                "position_size_usd": None,
                "contracts": None,
                "risk_approved": False,
                "risk_flags": ["kill_switch"],
                "order_skipped": True,
                "skip_reason": "kill_switch_active",
            })
            continue

        # Dedup gate: skip if an open position already exists for this market
        if market_id in open_market_ids:
            orders.append({
                **signal,
                "risk_approved": False,
                "risk_flags": ["duplicate_market"],
                "order_skipped": True,
                "skip_reason": "open position already exists for this market",
            })
            continue

        # Inject running open_positions count so batch self-limits
        batch_portfolio = {
            **portfolio_state,
            "open_positions": portfolio_state["open_positions"] + approved_count,
        }

        order = process_signal(signal, settings, bankroll, batch_portfolio)

        # Execute approved orders
        if order["risk_approved"]:
            approved_count += 1
            open_market_ids.add(market_id)
            try:
                exec_direction = direction_to_kelly(order["direction"])
                exec_signal = {**signal, "direction": exec_direction, "platform": order["platform"]}
                exec_position = {
                    "contracts": order["contracts"],
                    "size_usd_capped": order["position_size_usd"],
                    "kelly_fraction_used": order["kelly_fraction"],
                }
                trade_record = _executor.execute(exec_signal, exec_position)
                order["trade_record"] = asdict(trade_record)
            except Exception as e:
                print(f"[pm-risk] Execute error for {market_id}: {e}", file=sys.stderr)
                order["trade_record"] = None

        orders.append(order)

    print(
        f"[pm-risk] {approved_count} orders approved of {len(actionable)} actionable signals",
        file=sys.stderr,
    )

    output = {
        "scan_id": scan_id,
        "ordered_at": datetime.now(timezone.utc).isoformat(),
        "bankroll_usd": bankroll,
        "orders": orders,
    }

    # Write to data/orders_{scan_id}.json
    DATA_DIR.mkdir(exist_ok=True)
    output_path = DATA_DIR / f"orders_{scan_id}.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[pm-risk] Wrote orders to {output_path}", file=sys.stderr)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
