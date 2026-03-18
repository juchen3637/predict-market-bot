<!-- PATTERNS_JSON: {"market_ids_to_avoid": [], "failure_patterns_by_category": {"bad_calibration": [], "liquidity_trap": [], "stale_data": [], "model_disagreement": [], "black_swan": [], "execution_error": [], "spread_cost": [], "unknown": []}} -->

# Failure Knowledge Base

This file is read by pm-scan at the start of every cycle.
Markets or categories listed here are flagged for extra scrutiny or exclusion.
The machine-readable `PATTERNS_JSON` block above is updated by postmortem.py and
parsed by filter_markets.py to deprioritize candidates matching known failure patterns.

## Failure Categories

| Category | Description |
|----------|-------------|
| `bad_calibration` | Model probability was significantly wrong |
| `liquidity_trap` | Market lacked depth; entry/exit at bad prices |
| `stale_data` | Research data was outdated at time of trade |
| `model_disagreement` | LLMs disagreed; high variance prediction |
| `black_swan` | Unpredictable external shock |
| `execution_error` | Technical failure in order placement |
| `spread_cost` | Edge consumed by bid-ask spread |

## Markets to Avoid (updated by postmortem.py)

| Market ID | Platform | Reason | Added |
|-----------|----------|--------|-------|
| (none yet) | | | |

## Categories Showing Systematic Issues

| Category | Win Rate | Notes |
|----------|----------|-------|
| (none yet — populate after 20+ trades) | | |

---

<!-- New failure entries are appended below by postmortem.py -->
