"""
tests/test_historical_fetcher.py — Unit tests for historical_fetcher.py

Tests field mapping, deduplication, dry-run, and outcome mapping.
No real API calls are made.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills" / "pm-compound" / "scripts"))

import historical_fetcher as hf


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _polymarket_resolved_yes() -> dict:
    return {
        "conditionId": "poly_abc123",
        "question": "Will BTC exceed $100k by end of 2024?",
        "outcomePrices": ["1", "0"],   # CLOB-era: YES=1, NO=0
        "volume": 50000.0,
        "liquidity": 12000.0,
        "category": "crypto",
        "resolved": True,
        "resolutionTime": "2024-12-31T23:59:00Z",
    }


def _polymarket_resolved_no() -> dict:
    return {
        "conditionId": "poly_def456",
        "question": "Will ETH flip BTC in 2024?",
        "outcomePrices": ["0", "1"],   # CLOB-era: YES=0, NO=1
        "volume": 30000.0,
        "liquidity": 8000.0,
        "category": "crypto",
        "resolved": True,
    }


def _polymarket_unresolved() -> dict:
    return {
        "conditionId": "poly_open",
        "question": "Will X happen?",
        "outcomePrices": ["0.65", "0.35"],  # mid-range = not yet resolved
        "volume": 5000.0,
        "liquidity": 1000.0,
        "resolved": False,
    }


def _kalshi_settled_yes() -> dict:
    return {
        "ticker": "KXBTC-24DEC31-T100K",
        "title": "Will BTC exceed $100k by Dec 31?",
        "status": "settled",
        "result": "yes",
        "volume_fp": 25000.0,
        "open_interest_fp": 7500.0,
        "yes_bid_dollars": 0.0,
        "yes_ask_dollars": 0.0,
        "event_ticker": "KXBTC",
    }


def _kalshi_settled_no() -> dict:
    return {
        "ticker": "KXFED-24DEC-HIKE",
        "title": "Will Fed hike rates in December?",
        "status": "settled",
        "result": "no",
        "volume_fp": 18000.0,
        "open_interest_fp": 5000.0,
        "yes_bid_dollars": 0.0,
        "yes_ask_dollars": 0.0,
        "event_ticker": "KXFED",
    }


def _kalshi_open() -> dict:
    return {
        "ticker": "KXCPI-25JAN",
        "title": "Will CPI be below 3% in Jan?",
        "status": "open",
        "result": "",
        "volume_fp": 4000.0,
        "open_interest_fp": 1200.0,
        "event_ticker": "KXCPI",
    }


# ---------------------------------------------------------------------------
# transform_polymarket
# ---------------------------------------------------------------------------

class TestTransformPolymarket:
    def test_yes_outcome(self):
        record = hf.transform_polymarket(_polymarket_resolved_yes())
        assert record is not None
        assert record["market_id"] == "poly_abc123"
        assert record["outcome"] == 1
        assert record["current_yes_price"] == 1.0
        assert record["title"] == "Will BTC exceed $100k by end of 2024?"
        assert record["category"] == "crypto"
        assert record["volume_24h"] == 50000.0
        assert record["open_interest"] == 12000.0
        assert record["days_to_expiry"] == 0
        assert record["anomaly_flags"] == []

    def test_no_outcome(self):
        record = hf.transform_polymarket(_polymarket_resolved_no())
        assert record is not None
        assert record["outcome"] == 0
        assert record["current_yes_price"] == 0.0

    def test_unresolved_returns_none(self):
        record = hf.transform_polymarket(_polymarket_unresolved())
        assert record is None

    def test_missing_condition_id_returns_none(self):
        raw = _polymarket_resolved_yes()
        del raw["conditionId"]
        record = hf.transform_polymarket(raw)
        assert record is None

    def test_amm_era_both_zero_returns_none(self):
        """Old AMM-era markets with outcomePrices=["0","0"] are skipped."""
        raw = _polymarket_resolved_yes()
        raw["outcomePrices"] = ["0", "0"]
        record = hf.transform_polymarket(raw)
        assert record is None

    def test_json_string_outcome_prices(self):
        """outcomePrices may arrive as a JSON string."""
        raw = _polymarket_resolved_yes()
        raw["outcomePrices"] = '["1", "0"]'
        record = hf.transform_polymarket(raw)
        assert record is not None
        assert record["outcome"] == 1

    def test_sentiment_approximation_yes(self):
        record = hf.transform_polymarket(_polymarket_resolved_yes())
        assert record is not None
        # current_yes_price = 1.0 → score = (1.0 - 0.5) * 2.0 = 1.0, confidence = 1.0
        assert record["sentiment"]["score"] == pytest.approx(1.0)
        assert record["sentiment"]["confidence"] == pytest.approx(1.0)

    def test_sentiment_approximation_no(self):
        record = hf.transform_polymarket(_polymarket_resolved_no())
        assert record is not None
        # current_yes_price = 0.0 → score = (0.0 - 0.5) * 2.0 = -1.0, confidence = 1.0
        assert record["sentiment"]["score"] == pytest.approx(-1.0)
        assert record["sentiment"]["confidence"] == pytest.approx(1.0)

    def test_group_item_title_fallback_category(self):
        raw = _polymarket_resolved_yes()
        del raw["category"]
        raw["groupItemTitle"] = "Finance"
        record = hf.transform_polymarket(raw)
        assert record is not None
        assert record["category"] == "finance"


# ---------------------------------------------------------------------------
# transform_kalshi
# ---------------------------------------------------------------------------

class TestTransformKalshi:
    def test_yes_outcome(self):
        record = hf.transform_kalshi(_kalshi_settled_yes())
        assert record is not None
        assert record["market_id"] == "KXBTC-24DEC31-T100K"
        assert record["outcome"] == 1
        assert record["category"] == "crypto"
        assert record["volume_24h"] == 25000.0
        assert record["open_interest"] == 7500.0
        assert record["days_to_expiry"] == 0
        assert record["anomaly_flags"] == []

    def test_no_outcome(self):
        record = hf.transform_kalshi(_kalshi_settled_no())
        assert record is not None
        assert record["outcome"] == 0
        assert record["category"] == "economics"

    def test_open_market_returns_none(self):
        record = hf.transform_kalshi(_kalshi_open())
        assert record is None

    def test_missing_ticker_returns_none(self):
        raw = _kalshi_settled_yes()
        del raw["ticker"]
        record = hf.transform_kalshi(raw)
        assert record is None

    def test_bid_ask_price_used_when_present(self):
        raw = _kalshi_settled_yes()
        raw["yes_bid_dollars"] = 0.95
        raw["yes_ask_dollars"] = 0.97
        record = hf.transform_kalshi(raw)
        assert record is not None
        assert record["current_yes_price"] == pytest.approx(0.96)

    def test_category_mapping_sports(self):
        raw = _kalshi_settled_yes()
        raw["event_ticker"] = "KXNBA"
        record = hf.transform_kalshi(raw)
        assert record is not None
        assert record["category"] == "sports"

    def test_category_mapping_unknown(self):
        raw = _kalshi_settled_yes()
        raw["event_ticker"] = "KXWEIRD"
        record = hf.transform_kalshi(raw)
        assert record is not None
        assert record["category"] == "other"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_load_existing_ids_empty_file(self, tmp_path):
        output = tmp_path / "training_data.jsonl"
        ids = hf.load_existing_ids(output)
        assert ids == set()

    def test_load_existing_ids_populates_set(self, tmp_path):
        output = tmp_path / "training_data.jsonl"
        records = [
            {"market_id": "mkt_1", "outcome": 1},
            {"market_id": "mkt_2", "outcome": 0},
        ]
        with open(output, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        ids = hf.load_existing_ids(output)
        assert ids == {"mkt_1", "mkt_2"}

    def test_load_existing_ids_skips_malformed(self, tmp_path):
        output = tmp_path / "training_data.jsonl"
        output.write_text('{"market_id": "good"}\n{bad json}\n{"market_id": "also_good"}\n')
        ids = hf.load_existing_ids(output)
        assert ids == {"good", "also_good"}

    def test_fetch_skips_duplicate_ids(self, tmp_path):
        output = tmp_path / "training_data.jsonl"
        # Write existing record with same market_id
        existing = {"market_id": "poly_abc123", "outcome": 1}
        with open(output, "w") as f:
            f.write(json.dumps(existing) + "\n")

        with patch("historical_fetcher.fetch_resolved_markets") as mock_fetch:
            mock_fetch.return_value = [_polymarket_resolved_yes()]
            n = hf.fetch_and_transform("polymarket", limit=10, output_path=output)

        assert n == 0

    def test_fetch_writes_new_ids(self, tmp_path):
        output = tmp_path / "training_data.jsonl"
        with patch("historical_fetcher.fetch_resolved_markets") as mock_fetch:
            mock_fetch.return_value = [_polymarket_resolved_yes(), _polymarket_resolved_no()]
            n = hf.fetch_and_transform("polymarket", limit=10, output_path=output)

        assert n == 2
        ids = hf.load_existing_ids(output)
        assert "poly_abc123" in ids
        assert "poly_def456" in ids


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_write(self, tmp_path):
        output = tmp_path / "training_data.jsonl"
        with patch("historical_fetcher.fetch_resolved_markets") as mock_fetch:
            mock_fetch.return_value = [_polymarket_resolved_yes()]
            n = hf.fetch_and_transform("polymarket", limit=10, output_path=output, dry_run=True)

        assert n == 1
        assert not output.exists()

    def test_dry_run_returns_correct_count(self, tmp_path):
        output = tmp_path / "training_data.jsonl"
        with patch("historical_fetcher.fetch_resolved_markets") as mock_fetch:
            mock_fetch.return_value = [
                _polymarket_resolved_yes(),
                _polymarket_resolved_no(),
            ]
            n = hf.fetch_and_transform("polymarket", limit=10, output_path=output, dry_run=True)

        assert n == 2


# ---------------------------------------------------------------------------
# Outcome Mapping
# ---------------------------------------------------------------------------

class TestOutcomeMapping:
    def test_polymarket_yes_maps_to_1(self):
        record = hf.transform_polymarket(_polymarket_resolved_yes())
        assert record["outcome"] == 1

    def test_polymarket_no_maps_to_0(self):
        record = hf.transform_polymarket(_polymarket_resolved_no())
        assert record["outcome"] == 0

    def test_kalshi_yes_maps_to_1(self):
        record = hf.transform_kalshi(_kalshi_settled_yes())
        assert record["outcome"] == 1

    def test_kalshi_no_maps_to_0(self):
        record = hf.transform_kalshi(_kalshi_settled_no())
        assert record["outcome"] == 0
