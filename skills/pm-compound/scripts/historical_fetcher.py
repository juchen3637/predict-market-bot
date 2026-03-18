"""
historical_fetcher.py — Historical Training Data Fetcher for pm-compound skill

Fetches resolved markets from Polymarket and Kalshi, transforms them into
XGBoost training records, and writes to data/training_data.jsonl.

Usage:
    python historical_fetcher.py [--platform polymarket|kalshi|both] [--limit 200]
                                 [--output data/training_data.jsonl] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from platform_client import fetch_resolved_markets

# Project root: skills/pm-compound/scripts → project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_OUTPUT = _PROJECT_ROOT / "data" / "training_data.jsonl"

_TICKER_CATEGORY_MAP = {
    "KXBTC": "crypto", "KXETH": "crypto", "KXSOL": "crypto",
    "KXINX": "finance", "KXNDX": "finance", "KXSPX": "finance",
    "KXFED": "economics", "KXCPI": "economics", "KXGDP": "economics",
    "KXELECT": "politics", "KXPRES": "politics", "KXCONG": "politics",
    "KXNHL": "sports", "KXNBA": "sports", "KXNFL": "sports",
}


# ---------------------------------------------------------------------------
# Field Mapping
# ---------------------------------------------------------------------------

def _kalshi_category(event_ticker: str) -> str:
    for prefix, cat in _TICKER_CATEGORY_MAP.items():
        if event_ticker.upper().startswith(prefix):
            return cat
    return "other"


def _sentiment_from_price(yes_price: float) -> dict[str, float]:
    """Approximate sentiment from final market price (no scraper on historical data)."""
    score = (yes_price - 0.5) * 2.0
    confidence = abs(yes_price - 0.5) * 2.0
    return {"score": round(score, 4), "confidence": round(confidence, 4)}


def transform_polymarket(raw: dict[str, Any]) -> dict[str, Any] | None:
    """
    Transform a raw Polymarket resolved market dict into a training record.
    Returns None if market cannot be mapped (missing key fields or not resolved).
    """
    market_id = raw.get("conditionId") or raw.get("condition_id", "")
    if not market_id:
        return None

    # outcomePrices is reliable on CLOB-era markets (post-2022).
    # Old AMM-era markets show ["0","0"] — those are filtered out here.
    raw_prices = raw.get("outcomePrices") or ["0.5", "0.5"]
    if isinstance(raw_prices, str):
        raw_prices = json.loads(raw_prices)

    yes_price_str = str(raw_prices[0]) if raw_prices else "0.5"
    no_price_str = str(raw_prices[1]) if len(raw_prices) > 1 else "0.5"

    if yes_price_str == "1":
        outcome = 1
        current_yes_price = 1.0
    elif no_price_str == "1":
        outcome = 0
        current_yes_price = 0.0
    else:
        # Pre-CLOB or unresolved market — skip
        return None

    volume = float(raw.get("volume") or 0)
    open_interest = float(raw.get("liquidity") or 0)
    title = raw.get("question", "")
    category = (raw.get("category") or raw.get("groupItemTitle") or "other").lower()

    sentiment = _sentiment_from_price(current_yes_price)

    return {
        "market_id": market_id,
        "title": title,
        "category": category,
        # Use 0.5 (neutral) for training — we only have the final resolution price
        # (1.0 or 0.0), which would leak the outcome directly into the features.
        # In production the model receives the live pre-resolution market price.
        "current_yes_price": 0.5,
        "days_to_expiry": 0,
        "volume_24h": volume,
        "open_interest": open_interest,
        "anomaly_flags": [],
        "sentiment": sentiment,
        "outcome": outcome,
    }


def transform_kalshi(raw: dict[str, Any]) -> dict[str, Any] | None:
    """
    Transform a raw Kalshi settled market dict into a training record.
    Returns None if market cannot be mapped.
    """
    market_id = raw.get("ticker", "")
    if not market_id:
        return None

    result = raw.get("result", "")
    status = raw.get("status", "")

    if status != "settled":
        return None

    if result == "yes":
        outcome = 1
        current_yes_price = 1.0
    elif result == "no":
        outcome = 0
        current_yes_price = 0.0
    else:
        return None

    volume = float(raw.get("volume_fp") or raw.get("volume", 0))
    open_interest = float(raw.get("open_interest_fp") or raw.get("open_interest", 0))

    yes_bid = float(raw.get("yes_bid_dollars") or 0)
    yes_ask = float(raw.get("yes_ask_dollars") or 0)
    if yes_bid > 0 and yes_ask > 0:
        current_yes_price = (yes_bid + yes_ask) / 2.0

    title = raw.get("title", "")
    event_ticker = raw.get("event_ticker", "")
    category = _kalshi_category(event_ticker)

    sentiment = _sentiment_from_price(current_yes_price)

    return {
        "market_id": market_id,
        "title": title,
        "category": category,
        # Use 0.5 (neutral) for training — final settlement prices leak the outcome.
        "current_yes_price": 0.5,
        "days_to_expiry": 0,
        "volume_24h": volume,
        "open_interest": open_interest,
        "anomaly_flags": [],
        "sentiment": sentiment,
        "outcome": outcome,
    }


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def load_existing_ids(output_path: Path) -> set[str]:
    """Load market_ids already present in training_data.jsonl."""
    if not output_path.exists():
        return set()
    ids: set[str] = set()
    with open(output_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                mid = record.get("market_id")
                if mid:
                    ids.add(mid)
            except json.JSONDecodeError:
                continue
    return ids


def count_existing_records(output_path: Path) -> int:
    if not output_path.exists():
        return 0
    count = 0
    with open(output_path) as f:
        for line in f:
            if line.strip():
                count += 1
    return count


# ---------------------------------------------------------------------------
# Main Fetch Logic
# ---------------------------------------------------------------------------

def fetch_and_transform(
    platform: str,
    limit: int,
    output_path: Path,
    dry_run: bool = False,
) -> int:
    """
    Fetch resolved markets for a single platform, transform, deduplicate, and write.
    Returns count of new records written.
    """
    existing_ids = load_existing_ids(output_path)
    raw_markets = fetch_resolved_markets(platform, limit=limit)

    new_records: list[dict[str, Any]] = []
    for raw in raw_markets:
        if platform == "polymarket":
            record = transform_polymarket(raw)
        else:
            record = transform_kalshi(raw)

        if record is None:
            continue
        if record["market_id"] in existing_ids:
            continue

        new_records.append(record)
        existing_ids.add(record["market_id"])

    if dry_run:
        print(f"[historical_fetcher] DRY-RUN: would write {len(new_records)} new records from {platform}")
        return len(new_records)

    if new_records:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "a") as f:
            for record in new_records:
                f.write(json.dumps(record) + "\n")

    return len(new_records)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch resolved markets and build XGBoost training data."
    )
    parser.add_argument(
        "--platform",
        choices=["polymarket", "kalshi", "both"],
        default="both",
        help="Platform to fetch from (default: both)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max markets to fetch per platform (default: 200)",
    )
    parser.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        help=f"Output JSONL path (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts without writing to disk",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    initial_count = count_existing_records(output_path)

    platforms = (
        ["polymarket", "kalshi"] if args.platform == "both" else [args.platform]
    )

    total_new = 0
    for platform in platforms:
        try:
            n = fetch_and_transform(
                platform=platform,
                limit=args.limit,
                output_path=output_path,
                dry_run=args.dry_run,
            )
            total_new += n
        except Exception as e:
            print(
                f"[historical_fetcher] ERROR fetching {platform}: {e}",
                file=sys.stderr,
            )

    final_count = initial_count + total_new if not args.dry_run else initial_count
    print(
        f"[historical_fetcher] Wrote {total_new} new training records to {output_path} "
        f"(total: {final_count})"
    )


if __name__ == "__main__":
    main()
