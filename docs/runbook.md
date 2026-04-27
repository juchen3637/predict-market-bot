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

---

## Verifying the scan liquidity floor (post-deploy)

Run after deploying any change to `filter_markets.py:passes_liquidity_floor`,
`config/settings.yaml:scan.*`, or the `kalshi_client` / `polymarket_client`
`get_orderbook_snapshot` helpers. Each block is one shell command;
assumes you are in the project directory in the venv on the VPS.

**Per-cycle probe counters** (validates the floor is filtering, not a no-op):

```bash
python3 scripts/diagnose_state.py | grep -E 'probe:|liquidity'
```

Look for `(probe: K/P kept)` on each recent run. Healthy: K/P >= 0.10
(at least 10% kept) and P > 0 (probe is running). If K/P is 0 every
cycle, the floor is too strict — drop `min_cross_side_dollars` in
`config/settings.yaml`. If P is 0, `liquidity_check_enabled` is false.

**Post-floor placements** (validates the bot is actually trading):

```bash
python3 -c "
import json
rows = [json.loads(l) for l in open('data/trade_log.jsonl') if l.strip()]
post = [r for r in rows if r.get('scan_liquidity_floor') == 'v1']
placed = [r for r in post if r['status'] in ('placed', 'paper', 'filled')]
rejected = [r for r in post if r['status'] == 'rejected']
print(f'post-floor: {len(post)} entries, {len(placed)} placed/paper, {len(rejected)} rejected')
"
```

Expect at least 1 placed/paper entry within 24h of deploy. Zero is a
sign the floor is starving the pipeline OR that the predict stage is
not generating signals (separate problem).

**Per-cycle insufficient_depth rate** (validates we didn't bypass the live check):

```bash
python3 -c "
import json, collections, datetime
rows = [json.loads(l) for l in open('data/trade_log.jsonl') if l.strip()]
today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
recent = [r for r in rows if r.get('placed_at','').startswith(today) and r.get('rejection_reason') == 'insufficient_depth']
by_hour = collections.Counter(r['placed_at'][:13] for r in recent)
print('insufficient_depth rejections per hour today:', dict(by_hour))
"
```

Healthy: <= 2 per cycle on average. Rising counts mean predict-stage
signals are still landing on markets with thin cross-side bids — tighten
`min_cross_side_dollars` or investigate why the floor isn't catching them.

### Acceptance checklist (24h post-deploy)

- [ ] `liquidity_probe.probed > 0` every cycle (probe is running)
- [ ] `dropped_thin / probed >= 0.20` (floor is filtering meaningfully)
- [ ] At least 1 trade with `scan_liquidity_floor == "v1"` AND `status in ("placed", "paper")`
- [ ] `insufficient_depth` rejections per cycle stay <= 2 on average
- [ ] `consecutive_failures: 0`, no STOP, scan stage `duration_s < 60`

### Emergency rollback

In-band: set `scan.liquidity_check_enabled: false` in `config/settings.yaml`,
commit, push, `git pull` on VPS — the next cycle skips the probe entirely.
Alternative: `git revert <sha>` of the `feat(scan): add scan-time orderbook
liquidity floor` commit.
