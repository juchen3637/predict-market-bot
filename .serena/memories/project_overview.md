# predict-market-bot — Project Overview

## Purpose
An automated prediction market trading bot that runs on EC2 via systemd timers. It scans Polymarket and Kalshi markets, researches news/sentiment, generates probabilistic signals via an LLM ensemble, sizes positions with fractional Kelly, and executes orders — all in a 15-minute pipeline loop.

## Success Criteria (from PRD)
- Win rate >60%
- Sharpe >2.0
- Brier score <0.25
- Max drawdown <8%
- AI cost <$30/day

## Tech Stack
- **Language**: Python 3.x
- **LLM APIs**: Anthropic Claude (claude-sonnet-4-6, 40%), OpenAI GPT-5 Mini (35%), Google Gemini Flash (25%)
- **ML**: XGBoost, scikit-learn, numpy, pandas
- **HTTP**: httpx, pydantic v2
- **Config**: PyYAML, python-dotenv
- **Monitoring**: prometheus-client
- **Testing**: pytest
- **Data sources**: Brave/Tavily search, RSS/feedparser, Reddit (praw)
- **Deployment**: EC2 + systemd timers

## Pipeline Architecture
```
run_pipeline.py (orchestrator, every 15 min)
  → pm-scan/scripts/filter_markets.py       → data/candidates_{ts}.json
  → pm-research/scripts/research_pipeline.py → data/enriched_{ts}.json
  → pm-predict/scripts/predict_pipeline.py   → data/signals_{ts}.json
  → pm-risk/scripts/risk_pipeline.py         → data/trade_log.jsonl (appended)
```

Nightly (23:00): `pm-compound/scripts/consolidate.py` resolves trades, computes metrics, retrains XGBoost.

## Key Skills
| Skill | Purpose |
|-------|---------|
| `pm-scan` | Fetches Polymarket + Kalshi markets, filters by liquidity/expiry/volume, detects anomalies |
| `pm-research` | Scrapes Brave/Tavily + RSS, classifies sentiment (4h cache per market_id) |
| `pm-predict` | LLM ensemble + optional XGBoost; signals only if edge >4% AND 2/3 models agree |
| `pm-risk` | Fractional Kelly sizing (0.25x), validates position limits/drawdown/daily loss, executes trades, hedges on 5% moves |
| `pm-compound` | Nightly consolidation, metrics, XGBoost retraining (10+ resolved trades), postmortem |

## State & Data
- `data/trade_log.jsonl` — append-only ledger, single source of truth
- `data/performance_metrics.json` — nightly snapshot
- `data/pipeline_state.json` — consecutive failures; halts after 3
- `STOP` file — kill switch to halt pipeline
- `config/settings.yaml` — all tunable parameters (no secrets)
- `config/.env.example` — template for required API keys
- `PAPER_TRADING=true` env var routes orders to paper mode
