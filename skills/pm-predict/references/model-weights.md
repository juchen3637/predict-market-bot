# LLM Weight Rationale & Rebalancing

## Current Weights

| Model | Weight | Rationale |
|-------|--------|-----------|
| Grok | 30% | Strong on real-time events and current news; primary forecaster |
| Claude Sonnet | 20% | Excellent text comprehension; strong news analyst |
| GPT-4o | 20% | Bull case advocate; argues for Yes outcome |
| Gemini Flash | 15% | Bear case advocate; argues for No outcome |
| DeepSeek | 15% | Risk manager; flags uncertainty and tail risks |

Adapted from: `github.com/ryanfrigo/kalshi-ai-trading-bot`

## When to Rebalance

Trigger rebalancing when:
1. Brier Score exceeds 0.30 for 7+ consecutive days
2. One model's per-model Brier Score is >30% worse than the ensemble average
3. A model's API becomes unreliable (>20% failure rate over 7 days)

## Rebalancing Process

1. Compute per-model Brier Score for last 30 resolved trades
2. Reduce weight of worst-performing model by 5 percentage points
3. Redistribute to best-performing model
4. Run 10 paper trades to validate improvement before applying to live
5. Document change in this file with date and rationale

## Rebalancing History

| Date | Change | Reason |
|------|--------|--------|
| (none yet) | Initial weights | Adapted from ryanfrigo/kalshi-ai-trading-bot |
