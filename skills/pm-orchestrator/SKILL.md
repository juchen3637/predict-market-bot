---
name: pm-orchestrator
description: >
  Orchestrates the full prediction market trading pipeline across Polymarket and Kalshi.
  Use when "run pipeline", "start trading cycle", "check pipeline status",
  "emergency stop", "daily summary", "how is the bot doing", or "run one cycle".
metadata:
  version: 0.1.0
  pattern: sequential-pipeline
  tags: [orchestrator, predict-market, pipeline, trading]
---

# Prediction Market Bot — Orchestrator

## Overview

This skill coordinates the 5-stage pipeline: **Scan → Research → Predict → Risk/Execute → Compound**. Each stage is a separate skill. This orchestrator defines the sequence, error handling, and inter-stage data passing.

## Pre-Flight Checks (Run Before Every Cycle)

CRITICAL — check these before starting any pipeline cycle:

1. Check for `STOP` file in project root. If present, halt immediately. Log: `[KILL SWITCH] STOP file detected. Halting.`
2. Check daily AI API cost against `MAX_DAILY_AI_COST_USD`. If exceeded, halt and log.
3. Check current drawdown from `data/trade_log.jsonl`. If drawdown ≥ 8%, halt and log.
4. Verify platform API connectivity (Polymarket + Kalshi ping). Log any failures but continue with available platforms.

## Pipeline Sequence

### Stage 1 — Scan
- Invoke `pm-scan` skill
- Input: `config/settings.yaml` scan parameters + `skills/pm-compound/references/failure_log.md` (read past failures to avoid known bad markets)
- Output: `data/candidates_{timestamp}.json`
- If zero candidates found: skip remaining stages, log, wait for next cycle
- If scan fails: log error, skip cycle entirely

### Stage 2 — Research
- Invoke `pm-research` skill for each candidate (parallel, max 5 workers)
- Input: candidates from Stage 1
- Output: `data/enriched_{timestamp}.json`
- If a candidate's research fails: drop that candidate, log, continue with others
- If all candidates fail research: skip to next cycle

### Stage 3 — Predict
- Invoke `pm-predict` skill
- Input: enriched candidates from Stage 2
- Output: `data/signals_{timestamp}.json`
- Only forward signals where `edge >= 0.04` (configured in `settings.yaml`)
- If ensemble agreement < 3 models: discard signal, log reason

### Stage 4 — Risk & Execute
- Invoke `pm-risk` skill for each signal
- Input: signals from Stage 3
- Output: appended rows to `data/trade_log.jsonl`
- Each trade must pass ALL risk gates in `validate_risk.py` before execution
- If any risk gate fails: log rejection reason, do not execute

### Stage 5 — Compound
- Invoke `pm-compound` skill after trades resolve
- Input: resolved entries from `data/trade_log.jsonl`
- Output: updated `skills/pm-compound/references/failure_log.md`
- Runs both after each cycle (to log) and nightly (for full postmortem)

## Error Handling Rules

- Any unhandled exception in a stage: log with full traceback, halt that cycle, continue next cycle
- Three consecutive stage failures: halt pipeline and send alert
- API rate limit hit: back off exponentially (2s, 4s, 8s), max 3 retries

## Nightly Resolution (Run at 23:00 UTC — before daily summary)

Run the resolver before the daily summary so outcomes are current:

```bash
python skills/pm-compound/scripts/resolver.py
```

This checks all open trades against Polymarket/Kalshi APIs, updates `data/trade_log.jsonl`
with win/loss + P&L, and triggers Brier score recomputation automatically.

## Daily Summary (Run at 23:00 UTC — after resolver)

Compile and output:
- Trades placed today (count, win/loss, P&L)
- Running Brier Score (30-day rolling)
- Current drawdown from peak
- AI API cost for the day
- Any anomalies or errors in the log

## References

- See `references/pipeline-overview.md` for data flow diagrams and input/output contracts
- See `references/kill-switch-protocol.md` for emergency procedures
- See `assets/run-config.yaml` for current runtime parameters
