---
name: pm-predict
description: >
  Generates calibrated probability estimates for prediction market trades using
  an ensemble of XGBoost and multiple LLMs. Use when "predict outcome",
  "what probability", "model consensus", "check edge", "brier score",
  "estimate true probability", or after pm-research produces enriched candidates.
metadata:
  version: 0.1.0
  pattern: ensemble
  tags: [predict, ensemble, xgboost, llm, calibration, predict-market]
---

# Ensemble Prediction Agent

## Purpose

Given enriched market candidates from pm-research, estimate the true probability of the Yes outcome and compare it to the market price. Generate a trade signal only when the model's estimated edge is ≥ 4%.

## Ensemble Architecture

Five independent estimators vote on each market:

| Model | Weight | Role |
|-------|--------|------|
| Grok | 30% | Primary forecaster — strong on current events |
| Claude Sonnet | 20% | News analyst — strong on text comprehension |
| GPT-4o | 20% | Bull case advocate — argue for Yes outcome |
| Gemini Flash | 15% | Bear case advocate — argue for No outcome |
| DeepSeek | 15% | Risk manager — flag uncertainty and tail risks |

Each model receives the same prompt independently. Results are weighted and averaged.

Reference: `github.com/ryanfrigo/kalshi-ai-trading-bot` implements a similar multi-model pattern.

## Step 1: XGBoost Feature Prediction

Run `python scripts/xgboost_features.py` with the enriched candidate.

Features include: days_to_expiry, volume_24h, open_interest, sentiment_score, sentiment_confidence, anomaly_flag_count, current_yes_price, category_encoded.

Output: `xgboost_prob` — XGBoost's estimated Yes probability.

If model file not yet trained (Phase 2B Week 3-4): skip XGBoost, use LLM consensus only.

## Step 2: LLM Consensus

Run `python scripts/llm_consensus.py` with market title, research summary, and current price.

Each LLM returns a probability estimate (0.0–1.0) and a brief rationale.
Weighted average = `llm_consensus_prob`.

Require minimum 3 of 5 models to respond. If fewer respond, discard the signal.

## Step 3: Final Probability and Edge

Combine XGBoost and LLM consensus:
- If XGBoost trained: `p_model = 0.4 * xgboost_prob + 0.6 * llm_consensus_prob`
- If XGBoost not yet trained: `p_model = llm_consensus_prob`

Calculate edge:
```
edge = p_model - current_yes_price   (for Yes signal)
edge = (1 - p_model) - (1 - current_yes_price)  (for No signal)
```

**Only emit a signal if `|edge| >= 0.04`.**

## Step 4: Track Calibration

Run `python scripts/brier_score.py` after each market resolves.

Brier Score formula: `BS = (1/n) * Σ(p_model - outcome)²`

Target: BS < 0.25 on rolling 30-day window.

If BS exceeds 0.30: alert and consider rebalancing LLM weights or retraining XGBoost.

## Output

Write signals to `data/signals_{scan_id}.json`. Schema: see `docs/architecture.md`.

## Troubleshooting

**Fewer than 3 LLMs respond**
- Check API keys and rate limits in `.env`
- Reduce LLM calls temporarily to 3 models (Claude, GPT-4o, Grok) until resolved
- Log which models failed and why

**XGBoost prediction diverges significantly from LLM consensus (>30%)**
- Do not average blindly — flag for human review
- Log both predictions for post-analysis
- Default to LLM consensus until divergence is understood

**Brier Score rising above 0.30**
- Check if a specific event category is causing miscalibration
- Review last 20 predictions for systematic bias
- Consider adjusting LLM weights in `config/settings.yaml`

## References

- See `references/formulas.md` for all mathematical definitions
- See `references/model-weights.md` for weight rationale and rebalancing criteria
