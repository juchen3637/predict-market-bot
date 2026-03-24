# Suggested Commands

## Testing
```bash
pytest tests/                          # Run all tests
pytest tests/test_kelly_size.py        # Run a single test file
pytest tests/test_kelly_size.py::test_function_name -v  # Run a specific test
```

## Running the Bot
```bash
python run_pipeline.py                 # One full pipeline cycle (scan→research→predict→risk)
python dashboard_server.py             # Start dashboard server (port 8002)
python metrics_server.py               # Start Prometheus metrics server (port 8001)
python skills/pm-compound/scripts/consolidate.py  # Run nightly consolidation manually
python scripts/backtest.py             # Run backtest
```

## Kill Switch
```bash
touch STOP     # Halt the pipeline
rm STOP        # Resume the pipeline
```

## Systemd (on EC2)
```bash
systemctl status predict-market-pipeline.timer
systemctl status predict-market-nightly.timer
systemctl status predict-market-dashboard.service
systemctl status predict-market-metrics.service
```

## Docker (optional monitoring stack)
```bash
docker compose -f docker/docker-compose.yml up  # Prometheus (9090) + Grafana (3002)
```

## Environment
```bash
PAPER_TRADING=true python run_pipeline.py   # Run in paper trading mode (no real orders)
MAX_DAILY_AI_COST_USD=30                    # Override AI cost cap
```
