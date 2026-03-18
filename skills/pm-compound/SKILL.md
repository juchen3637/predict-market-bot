---
name: pm-compound
description: >
  Post-trade analysis, performance metrics, and knowledge compounding for prediction markets.
  Runs nightly consolidation (resolver → postmortem → metrics → daily summary).
  Use when "nightly consolidation", "compute metrics", "run postmortem", "daily summary",
  "log trade", "analyze losses", "update failure log", "what went wrong", "nightly review",
  or after markets resolve.
metadata:
  version: 0.2.0
  pattern: iterative
  tags: [compound, learning, postmortem, predict-market, calibration, metrics]
---

# Compound & Learn Agent

## Purpose

A prediction market bot that doesn't learn is just gambling with extra steps.
This skill closes the feedback loop: every resolved trade — especially every loss —
is analyzed, classified, and added to the knowledge base so the next cycle starts smarter.

## Primary Entry Point

`consolidate.py` is the nightly consolidation orchestrator. Run it instead of the
individual scripts for the full pipeline:

```bash
# Full nightly run (resolver → postmortem → metrics → daily summary)
python skills/pm-compound/scripts/consolidate.py
# Exit code 0 = success, 1 = step failure (systemd will alert)

# Compute metrics only
python skills/pm-compound/scripts/metrics.py
```

## Step 0: Nightly Auto-Resolution (run first)

`consolidate.py` calls `resolver.py` automatically. To run manually:

```bash
python skills/pm-compound/scripts/resolver.py
```

Checks all unresolved trades against the Polymarket and Kalshi APIs. For each resolved market:
1. Determines win/loss from `direction` + market outcome
2. Computes P&L via `compute_pnl()`
3. Updates `data/trade_log.jsonl` with outcome + P&L
4. Triggers `brier_score.py` if any trades resolved

If you need to manually resolve a single trade, proceed to Step 1.

## Step 1: Manually Log a Resolved Trade

When a market resolves and you need to log it manually, run `python scripts/log_trade.py`
to update the trade record in `data/trade_log.jsonl` with the outcome and P&L.

Fields to add on resolution:
- `resolved_at`: ISO8601 timestamp
- `outcome`: "win" or "loss"
- `pnl`: profit or loss in USD

## Step 2: Post-Mortem on Losses

`consolidate.py` runs postmortem automatically for all new losses. To run manually:

```bash
echo '{"trade_id":"...", "outcome":"loss", ...}' | python skills/pm-compound/scripts/postmortem.py
```

Classify the failure into one of these 8 categories:

| Category | Description | Auto-classify trigger |
|----------|-------------|----------------------|
| `bad_calibration` | Model probability was wrong — event resolved against strong prediction | edge ≥ 0.06, models_responded ≥ 3 |
| `liquidity_trap` | Couldn't exit position cleanly; slippage was too high | rejection_reason contains "slippage" |
| `stale_data` | Research data was outdated by the time of trade | (manual classification) |
| `model_disagreement` | LLMs disagreed but trade was taken anyway | models_responded < 3 |
| `black_swan` | Unpredictable external shock (news event, regulatory change) | (manual classification) |
| `execution_error` | Technical failure in order placement | rejection_reason contains "execution" |
| `spread_cost` | Edge was real but spread ate the profit | edge < 0.06 |
| `unknown` | Failure cause not yet determined — manual review needed | fallback |

## Step 3: Update Knowledge Base

After classification, append the lesson to `references/failure_log.md`:
- Market ID and title
- Platform
- Date of trade
- Failure category
- What happened
- What to avoid in future scans

The pm-scan skill reads this file at the start of every cycle.

## Step 4: Trigger Calibration Review (if needed)

After every 10 resolved trades, run `python ../../pm-predict/scripts/brier_score.py`.

If Brier Score > 0.30:
- Flag in failure log
- Recommend rebalancing LLM weights (see `pm-predict/references/model-weights.md`)

## Performance Metrics (metrics.py)

`consolidate.py` calls `metrics.py` automatically. Computed from all resolved trades:

| Metric | Formula | Alert threshold |
|--------|---------|----------------|
| Win rate | wins / total_closed | < 60% → WARN |
| Sharpe ratio | mean(daily_returns) / std(daily_returns) × √365 | < 2.0 → WARN |
| Max drawdown | rolling peak-to-trough on cumulative P&L | > 8% → CRITICAL + STOP file |
| Profit factor | gross_profit / gross_loss | — |
| Brier score | delegates to brier_score.py | > 0.30 → ALERT |

Outputs: `data/performance_metrics.json` (latest), `data/metrics_history.jsonl` (history).

## Nightly Consolidation (23:00 UTC)

`consolidate.py` runs the full pipeline:
1. `resolver.py` — resolve open trades
2. `postmortem.py` — classify new losses (tracks processed IDs in `data/postmortem_processed.json`)
3. `metrics.py` — compute performance snapshot (creates STOP file if drawdown > 8%)
4. `docs/daily_summaries/YYYY-MM-DD.md` — human-readable daily summary

## Output

- Updated `data/trade_log.jsonl` (outcomes + P&L)
- Updated `references/failure_log.md`
- Updated `data/brier_history.csv`
- `data/performance_metrics.json` (latest metrics snapshot)
- `data/metrics_history.jsonl` (append-only history)
- `docs/daily_summaries/YYYY-MM-DD.md` (daily summary)
- `data/postmortem_processed.json` (tracks which losses have been classified)

## Scripts

| Script | Purpose | When to run |
|--------|---------|-------------|
| `consolidate.py` | Full nightly pipeline (primary entry point) | Nightly (23:00 UTC) |
| `metrics.py` | Compute performance metrics snapshot | After consolidation or on demand |
| `resolver.py` | Auto-resolve all open trades via API | Called by consolidate.py |
| `log_trade.py` | Manually log a single resolved trade | Ad-hoc |
| `postmortem.py` | Classify failure and update knowledge base | Called by consolidate.py |
| `historical_fetcher.py` | Fetch resolved markets for XGBoost training data | On demand |
| `platform_client.py` | Shared API client for Polymarket + Kalshi | (library, not run directly) |

## References

- See `references/failure_log.md` for full history of past mistakes
