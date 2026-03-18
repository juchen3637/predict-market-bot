# Product Requirements Document
## predict-market-bot: AI-Powered Prediction Market Trading Bot

**Version**: 0.1.0
**Status**: In Development

---

## Problem Statement

Prediction markets (Polymarket, Kalshi) are not perfectly efficient. When AI models consistently estimate a probability significantly different from what the market prices, that gap represents exploitable alpha. A human trader cannot scan 300+ markets, process real-time news sentiment, run ensemble model predictions, enforce risk rules, and execute orders consistently — a bot can.

## Goal

Build a fully autonomous, Claude Skills-powered trading pipeline that:
1. Scans Polymarket and Kalshi every 15 minutes
2. Researches and estimates true event probabilities using multi-LLM consensus
3. Trades when model edge exceeds 4%
4. Enforces strict risk rules to protect capital
5. Learns from every loss to improve future performance

## Success Metrics

| Metric | Target |
|--------|--------|
| Win Rate | >60% of resolved trades |
| Sharpe Ratio | >2.0 (annualized) |
| Brier Score | <0.25 (rolling 30-day) |
| Max Drawdown | <8% (kill switch at 8%) |
| AI API Cost | <$30/day (ramp), <$50/day (scale) |

## Non-Goals

- High-frequency trading (sub-second latency)
- Trading assets other than prediction market contracts
- Building a portfolio management UI
- Supporting exchanges beyond Polymarket and Kalshi

## Constraints

- Polymarket: geo-restricted (check jurisdiction), requires Polygon wallet, EIP-712 auth
- Kalshi: US-only, regulated exchange, has demo environment for safe testing
- All external data treated as untrusted (prompt injection defense)
- Real money never used until 14+ days of paper trading validated

## Ramp Schedule

| Week | Milestone |
|------|-----------|
| 1 | Accounts set up, APIs tested, reference repos studied |
| 2 | Scan skill live, collecting market data |
| 3–4 | Research + Predict skills, backtesting begins |
| 4–5 | Risk skill + paper trading on Kalshi demo |
| 5–6 | Compound + Orchestrator, full autonomous paper cycles |
| 7–10 | Live trading: $100 → $500 after 50+ verified profitable trades |
