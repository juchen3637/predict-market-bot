"""
detect_anomalies.py — Anomaly Detection for pm-scan skill

Flags unusual price moves, wide spreads, and volume spikes on market candidates.
Reads historical snapshot data to compute rolling baselines.

Adapted from patterns in:
  - github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot  (real-time detection)
  - github.com/terauss/Polymarket-Kalshi-Arbitrage-bot  (spread detection)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PRICE_MOVE_THRESHOLD = 0.10        # 10% price move flags anomaly
SPREAD_THRESHOLD = 0.05            # $0.05 spread flags anomaly
VOLUME_SPIKE_MULTIPLIER = 3.0      # 3x 7-day average flags anomaly
SNAPSHOTS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "market_snapshots"


# ---------------------------------------------------------------------------
# Historical Data Loader
# ---------------------------------------------------------------------------

def load_historical_snapshots(
    market_id: str,
    platform: str,
    days: int = 7,
) -> list[dict[str, Any]]:
    """
    Load the last `days` days of snapshot data for a given market.
    Snapshots are JSON files written by filter_markets.py each cycle.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    snapshots = []

    if not SNAPSHOTS_DIR.exists():
        return snapshots

    for snapshot_file in sorted(SNAPSHOTS_DIR.glob("*.json")):
        try:
            mtime = datetime.fromtimestamp(snapshot_file.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue
            with open(snapshot_file) as f:
                data = json.load(f)
            for candidate in data.get("candidates", []):
                if candidate["market_id"] == market_id and candidate["platform"] == platform:
                    snapshots.append(candidate)
        except (json.JSONDecodeError, KeyError, OSError):
            continue

    return snapshots


# ---------------------------------------------------------------------------
# Anomaly Detection Functions
# ---------------------------------------------------------------------------

def detect_price_spike(
    current_price: float,
    historical: list[dict[str, Any]],
    threshold: float = PRICE_MOVE_THRESHOLD,
) -> bool:
    """Return True if price moved more than threshold% from any point in history."""
    if not historical:
        return False
    prices = [h["current_yes_price"] for h in historical if "current_yes_price" in h]
    if not prices:
        return False
    price_range = max(prices) - min(prices)
    baseline = prices[0] if prices else current_price
    return abs(current_price - baseline) / max(baseline, 0.01) > threshold


def detect_wide_spread(bid: float, ask: float, threshold: float = SPREAD_THRESHOLD) -> bool:
    """Return True if bid-ask spread exceeds threshold."""
    return (ask - bid) > threshold


def detect_volume_spike(
    volume_24h: int,
    historical: list[dict[str, Any]],
    multiplier: float = VOLUME_SPIKE_MULTIPLIER,
) -> bool:
    """Return True if 24h volume exceeds multiplier × 7-day average."""
    if not historical:
        return False
    volumes = [h["volume_24h"] for h in historical if "volume_24h" in h]
    if len(volumes) < 3:
        return False
    avg_volume = sum(volumes) / len(volumes)
    return avg_volume > 0 and volume_24h > avg_volume * multiplier


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def annotate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add anomaly_flags to each candidate based on historical data."""
    annotated = []
    for candidate in candidates:
        market_id = candidate["market_id"]
        platform = candidate["platform"]
        historical = load_historical_snapshots(market_id, platform, days=7)

        flags = list(candidate.get("anomaly_flags", []))

        if detect_price_spike(candidate["current_yes_price"], historical):
            flags.append("price_spike")

        if detect_volume_spike(candidate["volume_24h"], historical):
            flags.append("volume_spike")

        bid = candidate.get("yes_bid", 0)
        ask = candidate.get("yes_ask", 0)
        if bid > 0 and ask > 0 and detect_wide_spread(bid, ask):
            flags.append("wide_spread")

        annotated.append({**candidate, "anomaly_flags": flags})

    return annotated


def main() -> None:
    raw = json.load(sys.stdin)
    candidates = raw.get("candidates", [])
    annotated = annotate_candidates(candidates)
    output = {**raw, "candidates": annotated}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
