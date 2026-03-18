---
name: pm-risk
description: >
  Risk validation, position sizing, and order execution for prediction market trades.
  Uses Kelly Criterion. Enforces all risk gates before execution.
  Use when "check risk", "kelly", "size position", "max exposure",
  "validate trade", "portfolio risk", "execute trade", "place order",
  "run risk pipeline", "kill switch", or after pm-predict produces signals.
metadata:
  version: 0.2.0
  pattern: context-aware
  tags: [kelly, risk, position-sizing, predict-market, execution, kill-switch]
---

# Risk Management & Execution

## Purpose

Every trade signal from pm-predict must pass ALL risk gates before execution.
Risk validation uses Python scripts — not language instructions — because code is deterministic.

Reference: `github.com/suislanchez/polymarket-kalshi-weather-bot` (Kelly sizing, proven $1,325 profit)

## Primary Entry Point

`risk_pipeline.py` is the single orchestrator entry point. It wires together Kelly sizing →
risk gate validation → order execution for each signal.

```bash
# Single-signal smoke test (paper mode)
PAPER_TRADING=true python skills/pm-risk/scripts/risk_pipeline.py \
  --signal '{"market_id":"test-1","platform":"kalshi","p_model":0.72,"p_market":0.60,"direction":"yes","entry_price":0.60}'

# Batch mode (reads pm-predict output from file or stdin)
python skills/pm-risk/scripts/risk_pipeline.py --file data/signals_scan_20260317.json
```

## CRITICAL: Kill Switch

**To halt ALL orders immediately:** create a file named `STOP` in the project root.
```bash
touch ./STOP      # halts all order execution
rm ./STOP         # resumes normal operation
```

The kill switch is checked in two places (redundant safety gates):
1. `validate_risk.py`: at the start of risk gate validation
2. `execute_order.py`: immediately before placing any order

If current drawdown ≥ 8%, the system **auto-creates** `STOP` and logs a CRITICAL alert.
Delete the STOP file manually only after reviewing the portfolio state.

Before processing ANY signal:
1. Check for `STOP` file in project root. If present: halt, log, do not execute.
2. Check current drawdown from `data/trade_log.jsonl`. If ≥ 8%: halt, create `STOP` file, alert.

## Step 1: Risk Gate Validation

Run `python scripts/validate_risk.py` with the signal and current portfolio state.

All 6 gates must pass:

| Gate | Condition | Reject Reason |
|------|-----------|---------------|
| Edge | `edge >= 0.04` | edge_too_small |
| Ensemble | `models_responded >= 3` | insufficient_consensus |
| Position size | `kelly_size <= 5% of bankroll` | position_too_large |
| Concurrent positions | `open_positions < 15` | max_positions_reached |
| Portfolio VaR | `VaR at 95% within daily limit` | var_exceeded |
| Drawdown | `current_drawdown < 8%` | drawdown_kill_switch |

If any gate fails: log rejection reason, do not proceed to execution.

## Step 2: Kelly Position Sizing

Run `python scripts/kelly_size.py` with `p_model`, `entry_price`, and `bankroll`.

Formula: `f* = (p * b - q) / b`, then apply `kelly_fraction` multiplier (default 0.25).

Output: size in USD and contracts. Cap at 5% of bankroll regardless of Kelly output.

## Step 3: Order Execution

`risk_pipeline.py` calls `execute_order.py` automatically for approved signals.
To run execution standalone:

```bash
echo '{"signal": {...}, "position": {...}}' | python scripts/execute_order.py
```

Execution rules:
- Limit orders ONLY — never market orders
- **Paper mode** (`PAPER_TRADING=true`, default): simulate fill at limit price, log as "paper"
- **Live mode** (`PAPER_TRADING=false`): currently logs "live mode not yet enabled" and returns
  a declined order for both Polymarket and Kalshi. Activate by implementing the API calls
  in `place_polymarket_order()` / `place_kalshi_order()`.
- Kill switch check runs at the top of `execute_order.execute()` as a redundant safety gate
- Slippage: if fill deviates >2% from limit price, abort and log with reason
- Log all attempts (including failed fills) to `data/trade_log.jsonl`

## Step 4: Position Monitoring (Auto-Hedge Hook)

After each fill, `execute_order.py` checks whether the market has moved >5% from the
entry price (comparing `current_yes_price` in the signal vs actual fill price).
If exceeded, `hedge_needed: true` is set in the trade log entry.
Actual hedge execution is a future phase — use this flag to trigger manual review.

## Output

Append trade record to `data/trade_log.jsonl`. Schema: see `docs/architecture.md`.
Include: status (`placed`, `rejected`, `paper`), rejection_reason (if applicable).

## Troubleshooting

**"max_positions_reached" rejection**
- Review open positions — some may have resolved but not been logged
- Run `python scripts/utils/reconcile_positions.py` to sync with platform

**"var_exceeded" rejection**
- Daily VaR limit hit — normal if multiple losing trades today
- Trades will resume tomorrow (daily reset at midnight UTC)

**Order not filling**
- Check orderbook depth with `scripts/utils/check_depth.py --market-id {id}`
- May need to place at slightly better (more aggressive) limit price

## References

- See `references/formulas.md` for Kelly Criterion and VaR formulas
- See `references/platforms.md` (in pm-scan) for order API endpoints
