"""
test_backtest.py — Unit tests for scripts/backtest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import backtest as _module
from backtest import (
    load_training_data,
    load_existing_backtest_ids,
    inject_backtest_entries,
    _brier_score,
    _infer_platform,
    _build_backtest_entry,
    run_backtest,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_record(market_id: str, outcome: int, volume: float = 1000.0) -> dict:
    return {
        "market_id": market_id,
        "title": f"Will {market_id} resolve yes?",
        "category": "other",
        "current_yes_price": 0.5,
        "days_to_expiry": 0,
        "volume_24h": volume,
        "open_interest": 0.0,
        "anomaly_flags": [],
        "sentiment": {"score": 0.0, "confidence": 0.0},
        "outcome": outcome,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# load_training_data
# ---------------------------------------------------------------------------

class TestLoadTrainingData:

    def test_returns_all_records(self, tmp_path):
        records = [_make_record(f"m{i}", i % 2) for i in range(5)]
        path = tmp_path / "training.jsonl"
        _write_jsonl(path, records)

        loaded = load_training_data(path)
        assert len(loaded) == 5

    def test_preserves_order(self, tmp_path):
        records = [_make_record(f"m{i}", 0) for i in range(3)]
        path = tmp_path / "training.jsonl"
        _write_jsonl(path, records)

        loaded = load_training_data(path)
        assert [r["market_id"] for r in loaded] == ["m0", "m1", "m2"]

    def test_skips_blank_lines(self, tmp_path):
        path = tmp_path / "training.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps(_make_record("m0", 1)) + "\n")
            f.write("\n")
            f.write(json.dumps(_make_record("m1", 0)) + "\n")

        loaded = load_training_data(path)
        assert len(loaded) == 2

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "training.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps(_make_record("m0", 1)) + "\n")
            f.write("not-json\n")
            f.write(json.dumps(_make_record("m1", 0)) + "\n")

        loaded = load_training_data(path)
        assert len(loaded) == 2


# ---------------------------------------------------------------------------
# _brier_score
# ---------------------------------------------------------------------------

class TestBrierScore:

    def test_perfect_predictions(self):
        predictions = [1.0, 0.0, 1.0]
        outcomes = [1, 0, 1]
        assert _brier_score(predictions, outcomes) == pytest.approx(0.0)

    def test_worst_predictions(self):
        predictions = [0.0, 1.0]
        outcomes = [1, 0]
        assert _brier_score(predictions, outcomes) == pytest.approx(1.0)

    def test_uniform_half_predictions(self):
        # All p=0.5 on binary outcomes → BS = 0.25
        predictions = [0.5, 0.5, 0.5, 0.5]
        outcomes = [1, 0, 1, 0]
        assert _brier_score(predictions, outcomes) == pytest.approx(0.25)

    def test_single_prediction(self):
        assert _brier_score([0.7], [1]) == pytest.approx(0.09)


# ---------------------------------------------------------------------------
# _infer_platform
# ---------------------------------------------------------------------------

class TestInferPlatform:

    def test_hex_market_id_is_polymarket(self):
        assert _infer_platform("0xea9974c6b981fcd6096db41fb077006fd7ed80053c9f") == "polymarket"

    def test_short_hex_is_kalshi(self):
        # Too short to be a real Polymarket condition ID
        assert _infer_platform("0x12") == "kalshi"

    def test_alpha_ticker_is_kalshi(self):
        assert _infer_platform("KXBTC-24DEC01-55000") == "kalshi"

    def test_non_hex_is_kalshi(self):
        assert _infer_platform("some-kalshi-ticker") == "kalshi"


# ---------------------------------------------------------------------------
# _build_backtest_entry
# ---------------------------------------------------------------------------

class TestBuildBacktestEntry:

    def test_win_outcome(self):
        record = _make_record("0x" + "a" * 40, outcome=1)
        entry = _build_backtest_entry(record, p_model=0.75, now_iso="2026-01-01T00:00:00+00:00")

        assert entry["trade_id"] == f"backtest-{record['market_id']}"
        assert entry["status"] == "backtest"
        assert entry["outcome"] == "win"
        assert entry["pnl"] == 1.0
        assert entry["p_model"] == pytest.approx(0.75, abs=1e-6)
        assert entry["platform"] == "polymarket"

    def test_loss_outcome(self):
        record = _make_record("KXBTC-24DEC01", outcome=0)
        entry = _build_backtest_entry(record, p_model=0.3, now_iso="2026-01-01T00:00:00+00:00")

        assert entry["outcome"] == "loss"
        assert entry["pnl"] == -1.0
        assert entry["platform"] == "kalshi"

    def test_resolved_at_set(self):
        record = _make_record("m0", outcome=1)
        entry = _build_backtest_entry(record, p_model=0.5, now_iso="2026-03-21T10:00:00+00:00")
        assert entry["resolved_at"] == "2026-03-21T10:00:00+00:00"

    def test_direction_is_yes(self):
        record = _make_record("m0", outcome=1)
        entry = _build_backtest_entry(record, p_model=0.5, now_iso="2026-01-01T00:00:00+00:00")
        assert entry["direction"] == "yes"

    def test_size_usd_is_one(self):
        record = _make_record("m0", outcome=0)
        entry = _build_backtest_entry(record, p_model=0.5, now_iso="2026-01-01T00:00:00+00:00")
        assert entry["size_usd"] == 1.0


# ---------------------------------------------------------------------------
# load_existing_backtest_ids
# ---------------------------------------------------------------------------

class TestLoadExistingBacktestIds:

    def test_returns_empty_when_no_file(self, tmp_path):
        ids = load_existing_backtest_ids(tmp_path / "nonexistent.jsonl")
        assert ids == set()

    def test_returns_only_backtest_ids(self, tmp_path):
        log = tmp_path / "trade_log.jsonl"
        entries = [
            {"trade_id": "backtest-m1", "status": "backtest"},
            {"trade_id": "backtest-m2", "status": "backtest"},
            {"trade_id": "paper-xyz", "status": "paper"},
        ]
        _write_jsonl(log, entries)

        ids = load_existing_backtest_ids(log)
        assert ids == {"backtest-m1", "backtest-m2"}

    def test_skips_malformed_lines(self, tmp_path):
        log = tmp_path / "trade_log.jsonl"
        with open(log, "w") as f:
            f.write(json.dumps({"trade_id": "backtest-ok", "status": "backtest"}) + "\n")
            f.write("bad-json\n")

        ids = load_existing_backtest_ids(log)
        assert "backtest-ok" in ids


# ---------------------------------------------------------------------------
# inject_backtest_entries
# ---------------------------------------------------------------------------

class TestInjectBacktestEntries:

    def test_writes_new_entries(self, tmp_path):
        log = tmp_path / "trade_log.jsonl"
        entries = [
            {"trade_id": "backtest-m1", "status": "backtest"},
            {"trade_id": "backtest-m2", "status": "backtest"},
        ]
        count = inject_backtest_entries(entries, log, existing_ids=set())

        assert count == 2
        with open(log) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) == 2

    def test_deduplicates_existing_ids(self, tmp_path):
        log = tmp_path / "trade_log.jsonl"
        existing = {"backtest-m1"}
        entries = [
            {"trade_id": "backtest-m1", "status": "backtest"},
            {"trade_id": "backtest-m2", "status": "backtest"},
        ]
        count = inject_backtest_entries(entries, log, existing_ids=existing)

        assert count == 1
        with open(log) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert lines[0]["trade_id"] == "backtest-m2"

    def test_returns_zero_when_all_duplicate(self, tmp_path):
        log = tmp_path / "trade_log.jsonl"
        existing = {"backtest-m1", "backtest-m2"}
        entries = [
            {"trade_id": "backtest-m1", "status": "backtest"},
            {"trade_id": "backtest-m2", "status": "backtest"},
        ]
        count = inject_backtest_entries(entries, log, existing_ids=existing)

        assert count == 0
        assert not log.exists()

    def test_appends_to_existing_log(self, tmp_path):
        log = tmp_path / "trade_log.jsonl"
        _write_jsonl(log, [{"trade_id": "paper-1", "status": "paper"}])

        entries = [{"trade_id": "backtest-m1", "status": "backtest"}]
        inject_backtest_entries(entries, log, existing_ids=set())

        with open(log) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) == 2
        assert lines[1]["trade_id"] == "backtest-m1"


# ---------------------------------------------------------------------------
# run_backtest (integration — mocks xgboost_features)
# ---------------------------------------------------------------------------

class TestRunBacktest:

    def _make_training_file(self, tmp_path: Path, n: int = 20) -> Path:
        records = [_make_record(f"m{i}", i % 2) for i in range(n)]
        path = tmp_path / "training_data.jsonl"
        _write_jsonl(path, records)
        return path

    def test_raises_on_missing_training_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            run_backtest(
                training_data_path=tmp_path / "nonexistent.jsonl",
                trade_log_path=tmp_path / "trade_log.jsonl",
            )

    def test_raises_on_too_few_records(self, tmp_path):
        path = tmp_path / "training_data.jsonl"
        _write_jsonl(path, [_make_record("m0", 1)])

        with pytest.raises(ValueError, match="Need at least 10"):
            run_backtest(
                training_data_path=path,
                trade_log_path=tmp_path / "trade_log.jsonl",
            )

    def test_returns_summary_with_correct_counts(self, tmp_path):
        training_path = self._make_training_file(tmp_path, n=20)
        log_path = tmp_path / "trade_log.jsonl"

        mock_xgb = MagicMock()
        mock_xgb.train = MagicMock()
        mock_xgb.predict = MagicMock(return_value=0.6)

        with patch.dict("sys.modules", {"xgboost_features": mock_xgb}):
            with patch.object(_module, "xgboost_features", mock_xgb):
                result = run_backtest(
                    training_data_path=training_path,
                    trade_log_path=log_path,
                )

        assert result["train_count"] == 16  # 80% of 20
        assert result["test_count"] == 4    # 20% of 20
        assert "brier_score" in result
        assert result["injected_count"] == 4

    def test_brier_score_in_result(self, tmp_path):
        training_path = self._make_training_file(tmp_path, n=20)
        log_path = tmp_path / "trade_log.jsonl"

        mock_xgb = MagicMock()
        mock_xgb.train = MagicMock()
        mock_xgb.predict = MagicMock(return_value=0.5)

        with patch.object(_module, "xgboost_features", mock_xgb):
            result = run_backtest(
                training_data_path=training_path,
                trade_log_path=log_path,
            )

        assert isinstance(result["brier_score"], float)
        assert 0.0 <= result["brier_score"] <= 1.0

    def test_dedup_on_rerun(self, tmp_path):
        training_path = self._make_training_file(tmp_path, n=20)
        log_path = tmp_path / "trade_log.jsonl"

        mock_xgb = MagicMock()
        mock_xgb.train = MagicMock()
        mock_xgb.predict = MagicMock(return_value=0.6)

        with patch.object(_module, "xgboost_features", mock_xgb):
            result1 = run_backtest(training_data_path=training_path, trade_log_path=log_path)
            result2 = run_backtest(training_data_path=training_path, trade_log_path=log_path)

        # Second run should inject 0 (all already present)
        assert result1["injected_count"] == 4
        assert result2["injected_count"] == 0

    def test_backtest_entries_written_to_log(self, tmp_path):
        training_path = self._make_training_file(tmp_path, n=20)
        log_path = tmp_path / "trade_log.jsonl"

        mock_xgb = MagicMock()
        mock_xgb.train = MagicMock()
        mock_xgb.predict = MagicMock(return_value=0.7)

        with patch.object(_module, "xgboost_features", mock_xgb):
            run_backtest(training_data_path=training_path, trade_log_path=log_path)

        with open(log_path) as f:
            entries = [json.loads(l) for l in f if l.strip()]

        assert all(e["status"] == "backtest" for e in entries)
        assert all(e["trade_id"].startswith("backtest-") for e in entries)
        assert all("p_model" in e for e in entries)
        assert all("outcome" in e for e in entries)
