"""
test_retrain_xgboost.py — Tests for retrain_xgboost.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_COMPOUND_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pm-compound" / "scripts"
if str(_COMPOUND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_COMPOUND_SCRIPTS))

import retrain_xgboost as _module
from retrain_xgboost import should_retrain, run_retrain


# ---------------------------------------------------------------------------
# should_retrain tests
# ---------------------------------------------------------------------------

class TestShouldRetrain:

    def test_not_enough_new_trades_returns_false(self, tmp_path):
        """Fewer than 30 new trades since last train → should_retrain returns False."""
        state_file = tmp_path / "xgboost_train_state.json"
        training_file = tmp_path / "training_data.jsonl"

        state_file.write_text(json.dumps({"last_train_trade_count": 100}))
        with open(training_file, "w") as f:
            for i in range(110):  # only 10 new
                f.write(json.dumps({"market_id": f"m{i}", "outcome": 1}) + "\n")

        with (
            patch.object(_module, "TRAIN_STATE_PATH", state_file),
            patch.object(_module, "TRAINING_DATA_PATH", training_file),
        ):
            result = should_retrain(min_new_trades=30)

        assert result is False

    def test_enough_new_trades_returns_true(self, tmp_path):
        """30+ new trades since last train → should_retrain returns True."""
        state_file = tmp_path / "xgboost_train_state.json"
        training_file = tmp_path / "training_data.jsonl"

        state_file.write_text(json.dumps({"last_train_trade_count": 5}))
        with open(training_file, "w") as f:
            for i in range(40):  # 35 new
                f.write(json.dumps({"market_id": f"m{i}", "outcome": 1}) + "\n")

        with (
            patch.object(_module, "TRAIN_STATE_PATH", state_file),
            patch.object(_module, "TRAINING_DATA_PATH", training_file),
        ):
            result = should_retrain(min_new_trades=30)

        assert result is True

    def test_no_state_file_treats_as_zero_last_count(self, tmp_path):
        """No existing state file → last_train_trade_count treated as 0."""
        state_file = tmp_path / "nonexistent_state.json"
        training_file = tmp_path / "training_data.jsonl"

        with open(training_file, "w") as f:
            for i in range(35):
                f.write(json.dumps({"market_id": f"m{i}", "outcome": 1}) + "\n")

        with (
            patch.object(_module, "TRAIN_STATE_PATH", state_file),
            patch.object(_module, "TRAINING_DATA_PATH", training_file),
        ):
            result = should_retrain(min_new_trades=30)

        assert result is True

    def test_no_training_data_returns_false(self, tmp_path):
        """No training data file → 0 records → should_retrain is False."""
        state_file = tmp_path / "xgboost_train_state.json"
        training_file = tmp_path / "nonexistent_training.jsonl"

        state_file.write_text(json.dumps({"last_train_trade_count": 0}))

        with (
            patch.object(_module, "TRAIN_STATE_PATH", state_file),
            patch.object(_module, "TRAINING_DATA_PATH", training_file),
        ):
            result = should_retrain(min_new_trades=30)

        assert result is False

    def test_exactly_at_threshold_returns_true(self, tmp_path):
        """Exactly min_new_trades new records → returns True (>= threshold)."""
        state_file = tmp_path / "xgboost_train_state.json"
        training_file = tmp_path / "training_data.jsonl"

        state_file.write_text(json.dumps({"last_train_trade_count": 10}))
        with open(training_file, "w") as f:
            for i in range(40):  # 30 new, exactly at threshold
                f.write(json.dumps({"market_id": f"m{i}", "outcome": 1}) + "\n")

        with (
            patch.object(_module, "TRAIN_STATE_PATH", state_file),
            patch.object(_module, "TRAINING_DATA_PATH", training_file),
        ):
            result = should_retrain(min_new_trades=30)

        assert result is True


# ---------------------------------------------------------------------------
# run_retrain tests
# ---------------------------------------------------------------------------

class TestRunRetrain:

    # Inject mock modules for local imports inside run_retrain
    _MOCK_MODULES = {
        "historical_fetcher": MagicMock(main=MagicMock()),
        "xgboost_features": MagicMock(train=MagicMock()),
    }

    def test_not_enough_trades_skips_and_returns_false(self, tmp_path):
        """Not enough new trades → run_retrain returns False, state unchanged."""
        state_file = tmp_path / "xgboost_train_state.json"
        training_file = tmp_path / "training_data.jsonl"

        state_file.write_text(json.dumps({"last_train_trade_count": 100}))
        with open(training_file, "w") as f:
            for i in range(100):  # no new records
                f.write(json.dumps({"market_id": f"m{i}", "outcome": 1}) + "\n")

        with (
            patch.object(_module, "TRAIN_STATE_PATH", state_file),
            patch.object(_module, "TRAINING_DATA_PATH", training_file),
            patch.object(_module, "DATA_DIR", tmp_path),
            patch.dict("sys.modules", self._MOCK_MODULES),
        ):
            result = run_retrain(min_new_trades=30)

        assert result is False
        saved = json.loads(state_file.read_text())
        assert saved["last_train_trade_count"] == 100  # unchanged

    def test_enough_trades_saves_state_and_returns_true(self, tmp_path):
        """Enough new trades → run_retrain saves updated state, returns True."""
        state_file = tmp_path / "xgboost_train_state.json"
        training_file = tmp_path / "training_data.jsonl"

        state_file.write_text(json.dumps({"last_train_trade_count": 5}))
        with open(training_file, "w") as f:
            for i in range(40):
                f.write(json.dumps({"market_id": f"m{i}", "outcome": 1}) + "\n")

        with (
            patch.object(_module, "TRAIN_STATE_PATH", state_file),
            patch.object(_module, "TRAINING_DATA_PATH", training_file),
            patch.object(_module, "DATA_DIR", tmp_path),
            patch.dict("sys.modules", self._MOCK_MODULES),
        ):
            result = run_retrain(min_new_trades=30)

        assert result is True
        saved_state = json.loads(state_file.read_text())
        assert saved_state["last_train_trade_count"] == 40
        assert saved_state["last_trained_at"] is not None

    def test_state_file_updated_after_retrain(self, tmp_path):
        """State file contains new trade count and timestamp after retrain."""
        state_file = tmp_path / "xgboost_train_state.json"
        training_file = tmp_path / "training_data.jsonl"

        state_file.write_text(json.dumps({"last_train_trade_count": 0}))
        with open(training_file, "w") as f:
            for i in range(35):
                f.write(json.dumps({"market_id": f"m{i}", "outcome": 1}) + "\n")

        with (
            patch.object(_module, "TRAIN_STATE_PATH", state_file),
            patch.object(_module, "TRAINING_DATA_PATH", training_file),
            patch.object(_module, "DATA_DIR", tmp_path),
            patch.dict("sys.modules", self._MOCK_MODULES),
        ):
            run_retrain(min_new_trades=30)

        saved = json.loads(state_file.read_text())
        assert saved["last_train_trade_count"] == 35
        assert "last_trained_at" in saved
