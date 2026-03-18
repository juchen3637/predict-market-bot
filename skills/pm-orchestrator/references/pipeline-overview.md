# Pipeline Overview

## Data Flow

```
pm-orchestrator (coordinator)
        │  reads: failure_log.md, settings.yaml
        │
        ▼
    pm-scan
    scripts: filter_markets.py, detect_anomalies.py
    writes:  data/candidates_{timestamp}.json
        │
        ▼ (parallel per candidate)
    pm-research
    scripts: scrape_sources.py, classify_sentiment.py
    writes:  data/enriched_{timestamp}.json
        │
        ▼
    pm-predict
    scripts: xgboost_features.py, llm_consensus.py, brier_score.py
    writes:  data/signals_{timestamp}.json
        │
        ▼ (per signal)
    pm-risk
    scripts: validate_risk.py, kelly_size.py, execute_order.py
    appends: data/trade_log.jsonl
        │
        ▼ (on resolution)
    pm-compound
    scripts: log_trade.py, postmortem.py
    updates: failure_log.md, brier_history.csv
        │
        └──────────── feeds back into pm-scan (next cycle)
```

## Stage Input/Output Contracts

See `docs/architecture.md` for full JSON schemas.

## Timing

| Stage | Typical Duration | Parallelism |
|-------|-----------------|-------------|
| Scan | 10-30 seconds | Sequential |
| Research | 30-120 seconds | Up to 5 parallel |
| Predict | 20-60 seconds | Sequential (ensemble) |
| Risk/Execute | 2-10 seconds | Sequential |
| Compound | 5-30 seconds | Sequential |
| **Total cycle** | **~2-4 minutes** | |
