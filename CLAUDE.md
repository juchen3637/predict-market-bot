# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_kelly_size.py

# Run a specific test
pytest tests/test_kelly_size.py::test_function_name -v

# Run pipeline manually (one full scan→research→predict→risk cycle)
python run_pipeline.py

# Start dashboard server (port 8002)
python dashboard_server.py

# Start Prometheus metrics server (port 8001)
python metrics_server.py

# Run nightly consolidation manually
python skills/pm-compound/scripts/consolidate.py

# Backtest
python scripts/backtest.py
```

No linter is currently configured.

## Architecture

This is a prediction market trading bot that runs on EC2 via systemd timers. The pipeline executes every 15 minutes and follows a strict sequential flow through independent skill modules that communicate via JSON files in `data/`.

### Pipeline Flow

```
run_pipeline.py (orchestrator)
  → pm-scan/scripts/filter_markets.py       → data/candidates_{ts}.json
  → pm-research/scripts/research_pipeline.py → data/enriched_{ts}.json
  → pm-predict/scripts/predict_pipeline.py   → data/signals_{ts}.json
  → pm-risk/scripts/risk_pipeline.py         → data/trade_log.jsonl (appended)
```

Nightly (23:00): `pm-compound/scripts/consolidate.py` resolves trades, computes metrics, retrains XGBoost, runs postmortem → `docs/incidents/failure_log.md` feeds back into next cycle's scan.

### Key Modules by Skill

| Skill | Purpose |
|-------|---------|
| `pm-scan` | Fetches Polymarket + Kalshi markets, applies liquidity/expiry/volume filters, detects anomalies (price spikes, wide spreads, volume spikes) |
| `pm-research` | Scrapes Brave/Tavily + RSS feeds, classifies sentiment (4-hour cache per market_id) |
| `pm-predict` | LLM ensemble (Claude 40%, GPT-5 Mini 35%, Gemini Flash 25%) + optional XGBoost; only signals if edge >4% AND 2/3 models agree |
| `pm-risk` | Fractional Kelly sizing (0.25x), validates against position limits/drawdown/daily loss, executes via `polymarket_client.py`/`kalshi_client.py`, hedges on 5% moves |
| `pm-compound` | Nightly consolidation, metrics computation, XGBoost retraining (if 10+ resolved trades), postmortem |

### Data Flow & State

- **`data/trade_log.jsonl`**: Append-only ledger; single source of truth for all trades (paper and live)
- **`data/performance_metrics.json`**: Nightly snapshot computed by pm-compound
- **`data/pipeline_state.json`**: Tracks consecutive failures; halts after 3
- **`data/candidates_*.json`, `enriched_*.json`, `signals_*.json`**: Ephemeral intermediate files per cycle
- **`STOP`**: Kill switch — touch this file to halt the pipeline

### Configuration

- **`config/settings.yaml`**: All tunable parameters (scan interval, Kelly fraction, risk limits, cost caps). No secrets here.
- **`config/.env.example`**: Template for required API keys (Polymarket, Kalshi, Anthropic, OpenAI, Gemini, search provider, Reddit)
- **`PAPER_TRADING=true`** env var routes orders to paper mode (skips real execution)

### Testing

`tests/conftest.py` registers all `skills/*/scripts/` directories in `sys.path` so test files can import skill modules directly by name. Tests use `pytest`, `MagicMock`, and `tmp_path` for isolation. All 18 test files map 1:1 to a specific module.

### Deployment

Deployed to EC2 via `deploy/setup_ec2.sh`. Runs as systemd services:
- `predict-market-pipeline.timer`: Every 15 min
- `predict-market-nightly.timer`: 23:00 nightly
- `predict-market-dashboard.service`: Port 8002 (always-on)
- `predict-market-metrics.service`: Port 8001 (Prometheus)

Optional monitoring stack: `docker/docker-compose.yml` (Prometheus + Grafana on ports 9090/3002).

### Success Criteria (from PRD)

Win rate >60%, Sharpe >2.0, Brier score <0.25, max drawdown <8%, AI cost <$30/day.
