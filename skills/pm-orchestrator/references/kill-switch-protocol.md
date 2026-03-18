# Kill Switch Protocol

## Triggering

The kill switch fires when ANY of the following occur:
- `STOP` file exists in project root (manual)
- Drawdown ≥ 8% (automatic)
- Daily loss ≥ 15% of bankroll (automatic)
- Three consecutive pipeline failures (automatic)

## Automatic Actions on Kill Switch

1. Cancel all pending limit orders via platform API
2. Log: `[KILL SWITCH] Triggered by: {reason} at {timestamp}`
3. Write current portfolio state to `data/kill_switch_{timestamp}.json`
4. Halt all new order placement

## Manual Recovery Steps

1. Review `data/kill_switch_{timestamp}.json` for state snapshot
2. Check open positions on Kalshi and Polymarket dashboards manually
3. Decide for each position: hold to resolution or manually exit
4. Identify and document root cause in `docs/incidents/YYYY-MM-DD.md`
5. Fix the underlying issue (bad calibration, API bug, etc.)
6. Run paper trading for 5 cycles to validate fix
7. Remove STOP file: `rm STOP`
8. Resume with reduced kelly_fraction (halve it) for first 10 trades after resumption
