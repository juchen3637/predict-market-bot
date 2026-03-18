"""
log_trade.py — Trade Resolution Logger for pm-compound skill

Updates trade records in trade_log.jsonl when markets resolve.
Reads resolution data from platform API and updates matching records.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


TRADE_LOG = Path(__file__).parent.parent.parent.parent / "data" / "trade_log.jsonl"


def update_resolved_trade(
    trade_id: str,
    outcome: str,       # "win" | "loss"
    pnl: float,
    resolved_at: str | None = None,
) -> bool:
    """
    Update a trade record in the log with resolution data.
    Rewrites the file — trade_log.jsonl is small enough for this.
    Returns True if the trade was found and updated.
    """
    if not TRADE_LOG.exists():
        print(f"Trade log not found: {TRADE_LOG}", file=sys.stderr)
        return False

    resolved_at = resolved_at or datetime.now(timezone.utc).isoformat()
    updated = False
    records = []

    with open(TRADE_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("trade_id") == trade_id:
                    record["outcome"] = outcome
                    record["pnl"] = round(pnl, 4)
                    record["resolved_at"] = resolved_at
                    updated = True
                records.append(record)
            except json.JSONDecodeError:
                continue

    if updated:
        with open(TRADE_LOG, "w") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")

    return updated


def compute_pnl(
    direction: str,
    size_contracts: int,
    entry_price: float,
    fill_price: float,
    outcome: str,
) -> float:
    """
    Compute P&L for a resolved prediction market trade.

    In prediction markets: winning contract pays $1.00.
    P&L = contracts * (payout - cost)
    """
    cost_per_contract = fill_price if fill_price else entry_price

    if outcome == "win":
        # Payout $1.00 per contract, minus what we paid
        pnl_per = 1.0 - cost_per_contract
    else:
        # We lose what we paid
        pnl_per = -cost_per_contract

    return size_contracts * pnl_per


def main() -> None:
    data = json.load(sys.stdin)
    trade_id = data["trade_id"]
    outcome = data["outcome"]  # "win" | "loss"

    # Fetch the original trade to compute P&L
    if not TRADE_LOG.exists():
        print("Trade log not found", file=sys.stderr)
        sys.exit(1)

    trade = None
    with open(TRADE_LOG) as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                if record.get("trade_id") == trade_id:
                    trade = record
                    break
            except json.JSONDecodeError:
                continue

    if not trade:
        print(f"Trade {trade_id} not found in log", file=sys.stderr)
        sys.exit(1)

    pnl = compute_pnl(
        direction=trade.get("direction", "yes"),
        size_contracts=trade.get("size_contracts", 0),
        entry_price=trade.get("entry_price", 0.5),
        fill_price=trade.get("fill_price") or trade.get("entry_price", 0.5),
        outcome=outcome,
    )

    success = update_resolved_trade(trade_id, outcome, pnl)

    result = {"trade_id": trade_id, "outcome": outcome, "pnl": round(pnl, 4), "updated": success}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
