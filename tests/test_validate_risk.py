"""Unit tests for validate_risk.py"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/pm-risk/scripts"))

from validate_risk import (
    check_drawdown,
    check_edge,
    check_ensemble,
    check_max_positions,
    check_position_size,
    check_var,
    validate,
)

SETTINGS = {
    "risk": {
        "kelly_fraction": 0.25,
        "max_position_pct_bankroll": 0.05,
        "max_concurrent_positions": 15,
        "max_drawdown_pct": 0.08,
        "max_daily_loss_pct": 0.15,
        "max_slippage_pct": 0.02,
        "min_edge_to_signal": 0.04,
    }
}

PORTFOLIO_OK = {
    "open_positions": 0,
    "current_drawdown": 0.0,
    "daily_pnl": 0.0,
    "daily_loss_pct": 0.0,
    "peak_value": 1.0,
    "current_value": 1.0,
}

SIGNAL_OK = {
    "market_id": "test-1",
    "edge": 0.18,
    "models_responded": 3,
    "llm_consensus": {"models_responded": 3},
}


def test_check_edge_passes_at_threshold():
    assert check_edge(0.04, 0.04) is True


def test_check_edge_fails_below_threshold():
    assert check_edge(0.039, 0.04) is False


def test_check_edge_negative_edge():
    assert check_edge(-0.05, 0.04) is True  # abs(edge) >= min_edge


def test_check_ensemble_passes():
    assert check_ensemble(3, 3) is True


def test_check_ensemble_fails():
    assert check_ensemble(2, 3) is False


def test_check_position_size_passes():
    assert check_position_size(5.0, 100.0, 0.05) is True


def test_check_position_size_fails():
    assert check_position_size(5.01, 100.0, 0.05) is False


def test_check_position_size_zero_bankroll():
    assert check_position_size(0.0, 0.0, 0.05) is False


def test_check_max_positions_passes():
    assert check_max_positions(14, 15) is True


def test_check_max_positions_fails_at_limit():
    assert check_max_positions(15, 15) is False


def test_check_var_passes():
    assert check_var(-10.0, 100.0, 0.15) is True  # 10% < 15%


def test_check_var_fails():
    assert check_var(-15.0, 100.0, 0.15) is False  # 15% not < 15%


def test_check_drawdown_passes():
    assert check_drawdown(0.079, 0.08) is True


def test_check_drawdown_fails():
    assert check_drawdown(0.08, 0.08) is False


def test_validate_all_gates_pass():
    with patch("validate_risk.check_kill_switch", return_value=False), \
         patch("validate_risk.STOP_FILE_PATH") as mock_stop:
        mock_stop.touch = lambda: None
        decision = validate(
            signal=SIGNAL_OK,
            kelly_size_usd=5.0,
            settings=SETTINGS,
            portfolio_state=PORTFOLIO_OK,
        )
    assert decision.approved is True
    assert decision.rejection_reason is None
    assert decision.gates_failed == []


def test_validate_kill_switch_blocks():
    with patch("validate_risk.check_kill_switch", return_value=True):
        decision = validate(
            signal=SIGNAL_OK,
            kelly_size_usd=5.0,
            settings=SETTINGS,
            portfolio_state=PORTFOLIO_OK,
        )
    assert decision.approved is False
    assert decision.rejection_reason == "kill_switch_active"


def test_validate_ensemble_gate_fails():
    signal = {**SIGNAL_OK, "models_responded": 2}
    with patch("validate_risk.check_kill_switch", return_value=False):
        decision = validate(
            signal=signal,
            kelly_size_usd=5.0,
            settings=SETTINGS,
            portfolio_state=PORTFOLIO_OK,
        )
    assert decision.approved is False
    assert "ensemble" in decision.gates_failed


def test_validate_max_positions_gate_fails():
    portfolio = {**PORTFOLIO_OK, "open_positions": 15}
    with patch("validate_risk.check_kill_switch", return_value=False):
        decision = validate(
            signal=SIGNAL_OK,
            kelly_size_usd=5.0,
            settings=SETTINGS,
            portfolio_state=portfolio,
        )
    assert decision.approved is False
    assert "max_positions" in decision.gates_failed


def test_validate_uses_provided_portfolio_state():
    """portfolio_state param prevents re-reading trade log."""
    portfolio = {**PORTFOLIO_OK, "open_positions": 16}
    with patch("validate_risk.check_kill_switch", return_value=False), \
         patch("validate_risk.load_portfolio_state") as mock_load:
        validate(
            signal=SIGNAL_OK,
            kelly_size_usd=5.0,
            settings=SETTINGS,
            portfolio_state=portfolio,
        )
        mock_load.assert_not_called()
