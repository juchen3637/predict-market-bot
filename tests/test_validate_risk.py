"""Unit tests for validate_risk.py"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/pm-risk/scripts"))

from validate_risk import (
    _extract_market_family,
    check_drawdown,
    check_edge,
    check_ensemble,
    check_max_positions,
    check_position_size,
    check_var,
    load_open_market_families,
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
    portfolio = {**PORTFOLIO_OK, "open_positions": 15, "open_positions_by_platform": {"unknown": 15}}
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


# ---------------------------------------------------------------------------
# Family extraction tests
# ---------------------------------------------------------------------------

def test_extract_family_cpi():
    assert _extract_market_family("Will CPI rise more than 0.5% in March 2026?", "KX123") == "cpi_march_2026"


def test_extract_family_sp500():
    result = _extract_market_family(
        "Will the S&P 500 be between 6550 and 6574.9999 on Mar 20, 2026?", "KX456"
    )
    assert result == "sp500_mar_20_2026"


def test_extract_family_fallback():
    result = _extract_market_family("Will Scottie Scheffler win the 2026 Masters?", "KX789")
    assert result == "KX789"


def test_extract_family_btc():
    result = _extract_market_family("Will BTC be above 100k on Dec 31, 2026?", "KX000")
    assert result == "btc_dec_31_2026"


# ---------------------------------------------------------------------------
# load_open_market_families tests
# ---------------------------------------------------------------------------

def test_load_open_market_families_counts_open_cpi():
    trades = [
        {"market_id": "KX1", "title": "Will CPI rise more than 0.5% in March 2026?",
         "status": "placed", "outcome": None},
        {"market_id": "KX2", "title": "Will CPI rise more than 0.6% in March 2026?",
         "status": "paper", "outcome": None},
        # Resolved — should not count
        {"market_id": "KX3", "title": "Will CPI rise more than 0.7% in March 2026?",
         "status": "placed", "outcome": "yes"},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")
        tmp_path = Path(f.name)

    with patch("validate_risk.TRADE_LOG_PATH", tmp_path):
        families = load_open_market_families()

    tmp_path.unlink()
    assert families == {"cpi_march_2026": 2}


# ---------------------------------------------------------------------------
# Family gate integration tests
# ---------------------------------------------------------------------------

SETTINGS_WITH_FAMILY = {
    "risk": {
        **SETTINGS["risk"],
        "max_positions_per_family": 2,
    }
}


def _make_cpi_signal(market_id: str, threshold: str = "0.5") -> dict:
    return {
        "market_id": market_id,
        "title": f"Will CPI rise more than {threshold}% in March 2026?",
        "edge": 0.18,
        "models_responded": 3,
        "llm_consensus": {"models_responded": 3},
        "current_yes_price": 0.45,
        "p_model": 0.63,
        "direction": "long",
    }


def test_family_gate_blocks_third_cpi_signal():
    """2 open CPI positions in trade log → 3rd CPI signal rejected."""
    trades = [
        {"market_id": "KX1", "title": "Will CPI rise more than 0.5% in March 2026?",
         "status": "placed", "outcome": None},
        {"market_id": "KX2", "title": "Will CPI rise more than 0.6% in March 2026?",
         "status": "placed", "outcome": None},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")
        tmp_path = Path(f.name)

    with patch("validate_risk.TRADE_LOG_PATH", tmp_path):
        from validate_risk import load_open_market_families  # re-import to get patched path
        families = load_open_market_families()

    tmp_path.unlink()

    # Simulate the family gate logic in risk_pipeline
    signal = _make_cpi_signal("KX3", "0.7")
    family = _extract_market_family(signal["title"], signal["market_id"])
    max_per_family = 2
    assert family == "cpi_march_2026"
    assert families.get(family, 0) >= max_per_family, "Family gate should trigger"


def test_family_gate_in_batch_self_limits():
    """2 CPI approvals in same batch block a 3rd CPI signal in that batch."""
    families: dict[str, int] = {}
    max_per_family = 2

    def try_approve(market_id: str, threshold: str) -> bool:
        signal = _make_cpi_signal(market_id, threshold)
        family = _extract_market_family(signal["title"], signal["market_id"])
        if family != signal["market_id"] and families.get(family, 0) >= max_per_family:
            return False
        families[family] = families.get(family, 0) + 1
        return True

    assert try_approve("KX1", "0.5") is True
    assert try_approve("KX2", "0.6") is True
    assert try_approve("KX3", "0.7") is False  # blocked — family at limit


def test_family_gate_does_not_block_unrecognized_titles():
    """Fallback market_id path: gate never fires for unrecognized titles."""
    families: dict[str, int] = {"KX_other": 99}  # irrelevant family is saturated
    max_per_family = 2

    signal = {"market_id": "KX_unique", "title": "Will Scottie Scheffler win the Masters?"}
    family = _extract_market_family(signal["title"], signal["market_id"])
    # family == market_id → gate condition `family != market_id` is False → not blocked
    assert family == signal["market_id"]
    blocked = family != signal["market_id"] and families.get(family, 0) >= max_per_family
    assert blocked is False
