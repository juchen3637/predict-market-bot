# Risk Formulas Reference

## Kelly Criterion

```
Full Kelly:      f* = (p * b - q) / b
Fractional:      f  = kelly_fraction * f*   (default: 0.25)
Position size:   $ = f * bankroll

where:
  p = win probability (p_model for Yes, 1-p_model for No)
  q = 1 - p
  b = (1 - entry_price) / entry_price   (net odds per contract)

Example (from guide):
  Bankroll:    $10,000
  p_win:       0.70
  entry_price: 0.40  → b = (0.60/0.40) = 1.5
  Full Kelly:  f* = (0.70*1.5 - 0.30) / 1.5 = 0.50 → bet $5,000
  Quarter-K:   f  = 0.25 * 0.50 = 0.125 → bet $1,250 (much safer)
```

## Risk Gates Summary

| Gate | Formula | Threshold |
|------|---------|-----------|
| Edge | `p_model - p_market` | ≥ 0.04 |
| Position size | `kelly_size / bankroll` | ≤ 5% |
| Concurrent positions | count of open trades | < 15 |
| Daily loss | `abs(daily_pnl) / bankroll` | < 15% |
| Drawdown | `(peak - current) / peak` | < 8% |

## Value at Risk (95%)

```
VaR = μ - 1.645 * σ

where:
  μ = mean daily P&L (rolling 30-day)
  σ = std deviation of daily P&L

If portfolio VaR indicates potential loss > daily_loss_limit:
  block new trades for the day
```

## Drawdown

```
drawdown = (peak_portfolio_value - current_portfolio_value) / peak_portfolio_value

Kill switch fires at: drawdown >= 0.08 (8%)
```

## Slippage Check

```
slippage = |fill_price - signal_price| / signal_price
Abort if: slippage > 0.02 (2%)
```
