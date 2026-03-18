# Mathematical Formulas Reference

## Edge Detection

```
edge = p_model - p_market

where:
  p_model  = ensemble probability estimate (0.0–1.0)
  p_market = current market Yes price (0.0–1.0)

Trade when: |edge| >= 0.04
Direction:  edge > 0  → buy Yes
            edge < 0  → buy No
```

## Expected Value

```
EV = p * b - (1 - p)

where:
  p = p_model (your estimated probability)
  b = decimal odds - 1
      = (1 / p_market) - 1   (for Yes contracts at price p_market)

Positive EV = trade has mathematical edge
```

## Mispricing Score (Z-Score)

```
delta = (p_model - p_market) / σ

where:
  σ = rolling standard deviation of (p_model - p_market) across recent trades

Higher |delta| = stronger signal relative to historical mispricing magnitude
```

## Brier Score (Calibration)

```
BS = (1/n) * Σ(p_model_i - outcome_i)²

where:
  p_model_i = predicted probability for trade i
  outcome_i = 1 (Yes resolved) or 0 (No resolved)
  n = number of resolved trades in window

Range: 0.0 (perfect) to 1.0 (worst)
Target: BS < 0.25
Alert:  BS > 0.30  → review model weights
```

## Kelly Criterion (in pm-risk skill)

```
f* = (p * b - q) / b

where:
  p = win probability (p_model for Yes, 1 - p_model for No)
  q = 1 - p
  b = net odds (payout per $1 risked)
      = (1 - entry_price) / entry_price

Use Fractional Kelly: f = kelly_fraction * f*
Default: kelly_fraction = 0.25 (quarter-Kelly)
```

## Value at Risk (95% confidence)

```
VaR = μ - 1.645 * σ

where:
  μ = mean daily P&L
  σ = standard deviation of daily P&L

If |VaR| > daily_loss_limit: block new trades for the day
```

## Ensemble Combination

```
p_model = Σ(w_i * p_i) / Σ(w_i)

where:
  w_i = weight for model i (from settings.yaml predict.llm_weights)
  p_i = probability estimate from model i

Only responding models contribute (weights renormalized to sum to 1.0)
```
