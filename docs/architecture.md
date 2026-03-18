# Architecture & Data Flow
## predict-market-bot

---

## Pipeline Overview

The bot runs as a sequential pipeline driven by `pm-orchestrator`. Each stage is a standalone Claude Skill with its own `SKILL.md`, Python scripts, and reference docs. Stages communicate via structured JSON files written to `data/`.

```
pm-orchestrator (scheduler + coordinator)
        │
        ▼
    pm-scan
    [filter_markets.py + detect_anomalies.py]
    Output: data/candidates_{timestamp}.json
        │
        ▼
    pm-research  (parallel agents per candidate)
    [scrape_sources.py + classify_sentiment.py]
    Output: data/enriched_{timestamp}.json
        │
        ▼
    pm-predict
    [xgboost_features.py + llm_consensus.py]
    Output: data/signals_{timestamp}.json
        │
        ▼
    pm-risk
    [validate_risk.py + kelly_size.py + execute_order.py]
    Output: data/trade_log.jsonl (append)
        │
        ▼
    pm-compound
    [log_trade.py + postmortem.py]
    Output: skills/pm-compound/references/failure_log.md (updated)
        │
        └──────────────────────► pm-scan (next cycle, informed by failure log)
```

---

## Skill Inventory

| Skill | Pattern | Key Scripts |
|-------|---------|-------------|
| `pm-orchestrator` | Sequential pipeline | None (instructions only) |
| `pm-scan` | Sequential | `filter_markets.py`, `detect_anomalies.py` |
| `pm-research` | Parallel agents | `scrape_sources.py`, `classify_sentiment.py` |
| `pm-predict` | Ensemble | `xgboost_features.py`, `llm_consensus.py`, `brier_score.py` |
| `pm-risk` | Context-aware | `validate_risk.py`, `kelly_size.py`, `execute_order.py` |
| `pm-compound` | Iterative | `log_trade.py`, `postmortem.py` |

---

## Data Contracts

### Scan Output → Research Input
```json
{
  "candidates": [
    {
      "market_id": "string",
      "platform": "polymarket|kalshi",
      "title": "string",
      "category": "string",
      "current_yes_price": 0.0,
      "volume_24h": 0,
      "open_interest": 0,
      "days_to_expiry": 0,
      "anomaly_flags": ["price_spike", "volume_spike", "wide_spread"],
      "scanned_at": "ISO8601"
    }
  ],
  "scan_id": "string",
  "scanned_at": "ISO8601"
}
```

### Research Output → Predict Input
Extends scan output with:
```json
{
  "sentiment": {
    "score": -1.0,
    "label": "bullish|bearish|neutral",
    "confidence": 0.0,
    "sources": ["twitter", "reddit", "rss"],
    "source_count": 0
  }
}
```

### Predict Output → Risk Input
```json
{
  "signal": {
    "market_id": "string",
    "platform": "string",
    "direction": "yes|no",
    "p_model": 0.0,
    "p_market": 0.0,
    "edge": 0.0,
    "confidence_interval": [0.0, 0.0],
    "ensemble_agreement": 0,
    "xgboost_prob": 0.0,
    "llm_consensus_prob": 0.0
  }
}
```

### Risk Output → Trade Log
```json
{
  "trade_id": "uuid",
  "market_id": "string",
  "platform": "string",
  "direction": "yes|no",
  "size_contracts": 0,
  "size_usd": 0.0,
  "entry_price": 0.0,
  "p_model": 0.0,
  "edge": 0.0,
  "kelly_fraction": 0.0,
  "status": "placed|rejected|paper",
  "rejection_reason": "string|null",
  "placed_at": "ISO8601",
  "resolved_at": "ISO8601|null",
  "outcome": "win|loss|null",
  "pnl": 0.0
}
```

---

## Reference Repositories

| Repo | What We Adapted |
|------|----------------|
| `ryanfrigo/kalshi-ai-trading-bot` | Multi-LLM consensus prompt structure |
| `suislanchez/polymarket-kalshi-weather-bot` | Kelly sizing, dual-platform execution |
| `CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot` | Real-time anomaly detection patterns |
| `terauss/Polymarket-Kalshi-Arbitrage-bot` | Documentation structure |
| `pmxt` | Unified API wrapper (evaluate in Phase 0) |

---

## Key Design Decisions

1. **Scripts over instructions for risk**: All risk checks live in `validate_risk.py`, not SKILL.md prose. Code is deterministic; language interpretation is not.
2. **Fractional Kelly (0.25x)**: Full Kelly is mathematically optimal but catastrophically volatile. Quarter-Kelly trades growth for survivability.
3. **Prompt injection defense**: All scraped content length-capped at 2000 chars, wrapped as data, never inserted into system prompts.
4. **Failure-first scanning**: `pm-scan` reads `failure_log.md` before each cycle to avoid repeating known mistake patterns.
5. **Kill switch**: Creating a `STOP` file in project root halts all order placement within one check cycle.
