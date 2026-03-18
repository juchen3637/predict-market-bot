# Operational Runbook
## predict-market-bot

---

## First-Time Setup

```bash
# 1. Clone / enter repo
cd ~/predict-market-bot

# 2. Create Python virtualenv
python3 -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure secrets
cp config/.env.example .env
# Edit .env — fill in all API keys

# 5. Verify Kalshi demo connection
python -c "from scripts.utils.api_clients import kalshi_client; print(kalshi_client.get_balance())"

# 6. Run first scan (paper mode)
python scripts/run_pipeline.py --cycles 1 --paper
```

---

## Daily Operations

| Task | Command |
|------|---------|
| Start pipeline (paper) | `python scripts/run_pipeline.py --paper` |
| Start pipeline (live) | `python scripts/run_pipeline.py` |
| Emergency stop | `touch STOP` |
| Resume after STOP | `rm STOP && python scripts/run_pipeline.py` |
| Check today's P&L | `python scripts/utils/daily_summary.py` |
| View current positions | `python scripts/utils/show_positions.py` |
| Check Brier Score | `python scripts/utils/brier_report.py` |

---

## Kill Switch Protocol

1. **Immediate halt**: `touch STOP` — bot checks for this file before every order
2. **Verify halt**: Check logs — should see `[KILL SWITCH] STOP file detected. Halting.`
3. **Review open positions** manually on Kalshi/Polymarket dashboards
4. **Decide on open positions**: either hold to resolution or manually exit
5. **Document incident** in `docs/incidents/YYYY-MM-DD.md`
6. **Resume** only after root cause identified: `rm STOP`

---

## Scaling Checkpoints

| Trades | Check | Gate to Pass |
|--------|-------|-------------|
| 14 days paper | Brier <0.30, no risk gate failures | Go live at $100 |
| 20 live trades | Win rate >55%, drawdown <5% | Continue |
| 50 live trades | All primary metrics met | Scale to $500 |
| 100 live trades | Full evaluation | Decision to scale or maintain |

---

## Monitoring Alerts

| Alert | Trigger | Action |
|-------|---------|--------|
| Brier Score high | >0.30 rolling 30-day | Review model weights, retrain XGBoost |
| Drawdown warning | >6% | Reduce kelly_fraction to 0.15 |
| Drawdown kill switch | >8% | Auto-halts, manual review required |
| AI cost warning | >80% of daily cap | Reduce scan frequency |
| API auth failure | 401/403 from any platform | Rotate keys, check token expiry |

---

## Account Setup Notes

### Polymarket
- Requires Polygon (MATIC) wallet — use MetaMask
- EIP-712 signing for order authentication
- Geo-restrictions: verify your jurisdiction is permitted
- Deposit USDC on Polygon network

### Kalshi
- US-regulated, requires identity verification
- Demo environment: `KALSHI_USE_DEMO=true` in `.env`
- Demo has mock $10,000 in funds — use for all paper trading
- API key found in: Account → API → Create Key

---

## Common Errors

| Error | Likely Cause | Fix |
|-------|-------------|-----|
| `Connection refused` (Polymarket) | CLOB server down or wrong URL | Check `POLYMARKET_CLOB_URL` in .env |
| `401 Unauthorized` (Kalshi) | Expired API key or wrong secret | Regenerate key in Kalshi dashboard |
| `Insufficient balance` | Bankroll depleted or transfer pending | Check wallet balance on platform |
| `OrderBook depth insufficient` | Market too thin at entry price | Increase min_liquidity_usd threshold in settings.yaml |
| `Brier score missing` | Fewer than 10 resolved trades | Normal — continue collecting data |
| `STOP file detected` | Kill switch active | `rm STOP` after investigation |
