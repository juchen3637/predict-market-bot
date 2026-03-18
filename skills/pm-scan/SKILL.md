---
name: pm-scan
description: >
  Scans Polymarket and Kalshi for tradeable prediction market opportunities.
  Filters by volume, liquidity, and time to expiry. Detects price anomalies
  and volume spikes. Use when "scan markets", "find opportunities",
  "check for new markets", "market anomalies", or at scheduled intervals.
metadata:
  version: 0.1.0
  pattern: sequential
  tags: [scan, polymarket, kalshi, predict-market, opportunities]
---

# Market Scanner

## Purpose

Filter 300+ active prediction markets down to a ranked shortlist of candidates worth researching. Avoid wasting research and prediction compute on illiquid or expiry-distant markets.

## Step 1: Read Failure Log

Before scanning, read `skills/pm-compound/references/failure_log.md`.
Extract any market IDs or categories flagged as "avoid" and exclude them from this cycle's candidates.

## Step 2: Fetch Active Markets

Run `python scripts/filter_markets.py` with the following parameters (from `config/settings.yaml`):
- `min_volume_contracts`: 200
- `max_days_to_expiry`: 30
- `min_liquidity_usd`: 500

Expected output: JSON array of markets passing filters, sorted by liquidity descending.

If Polymarket API is unavailable: log warning, continue with Kalshi only.
If Kalshi API is unavailable: log warning, continue with Polymarket only.
If both unavailable: abort cycle, log error.

## Step 3: Detect Anomalies

Run `python scripts/detect_anomalies.py` on the filtered market list.

Flags to add to each candidate:
- `price_spike`: yes-price moved >10% in last 24h
- `wide_spread`: bid-ask spread >5 cents ($0.05)
- `volume_spike`: 24h volume > 3× 7-day average

Anomaly-flagged markets are higher priority — they often indicate new information not yet priced in.

## Step 4: Rank and Output

Rank candidates by estimated opportunity score:
1. Anomaly-flagged markets first
2. Within each tier, sort by liquidity (highest first)
3. Cap at 20 candidates per cycle to control research cost

Write output to `data/candidates_{timestamp}.json` using the schema in `docs/architecture.md`.

## Troubleshooting

**Error: "Rate limit exceeded" from Polymarket**
- Back off 30 seconds, retry once
- If still failing, skip Polymarket for this cycle

**Error: "No markets returned"**
- Verify API credentials in `.env`
- Check that filters aren't too restrictive (lower `min_volume_contracts` to 100 temporarily)
- Confirm at least one platform is active in `assets/run-config.yaml`

## References

- See `references/platforms.md` for full API documentation, auth patterns, and rate limits
