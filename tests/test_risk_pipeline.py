"""Unit tests for risk_pipeline.py"""
import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/pm-risk/scripts"))

from risk_pipeline import (
    detect_platform, direction_to_kelly, compute_entry_price, process_signal,
    run_single_signal, PipelineResult,
)
import execute_order as eo

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

SIGNAL_LONG = {
    "market_id": "0xabc123",
    "title": "Test long signal",
    "direction": "long",
    "current_yes_price": 0.40,
    "p_model": 0.65,
    "edge": 0.25,
    "llm_consensus": {"models_responded": 3, "consensus_prob": 0.65, "weighted_agreement": 0.9},
    "predict_skipped": False,
    "skip_reason": None,
}

SIGNAL_SHORT = {
    "market_id": "0xdef456",
    "title": "Test short signal",
    "direction": "short",
    "current_yes_price": 0.75,
    "p_model": 0.40,
    "edge": -0.35,
    "llm_consensus": {"models_responded": 3, "consensus_prob": 0.40, "weighted_agreement": 0.85},
    "predict_skipped": False,
    "skip_reason": None,
}


# --- detect_platform ---

def test_detect_platform_polymarket():
    assert detect_platform("0xabc123") == "polymarket"


def test_detect_platform_kalshi():
    assert detect_platform("KXBTC-26MAR") == "kalshi"
    assert detect_platform("kxcpi-26mar") == "kalshi"


def test_detect_platform_unknown():
    with pytest.raises(ValueError):
        detect_platform("UNKNOWN-123")


# --- direction_to_kelly ---

def test_direction_long_maps_to_yes():
    assert direction_to_kelly("long") == "yes"


def test_direction_short_maps_to_no():
    assert direction_to_kelly("short") == "no"


# --- compute_entry_price ---

def test_entry_price_long():
    signal = {"direction": "long", "current_yes_price": 0.40}
    assert compute_entry_price(signal) == 0.40


def test_entry_price_short():
    signal = {"direction": "short", "current_yes_price": 0.75}
    assert abs(compute_entry_price(signal) - 0.25) < 1e-6


# --- process_signal ---

def test_process_signal_happy_path_long(monkeypatch):
    monkeypatch.setenv("BANKROLL_USD", "100")
    with patch("validate_risk.check_kill_switch", return_value=False):
        order = process_signal(SIGNAL_LONG, SETTINGS, bankroll=100.0, portfolio_state=PORTFOLIO_OK)

    assert order["market_id"] == "0xabc123"
    assert order["platform"] == "polymarket"
    assert order["direction"] == "long"
    assert order["risk_approved"] is True
    assert order["order_skipped"] is False
    assert order["skip_reason"] is None
    assert order["position_size_usd"] is not None
    assert order["contracts"] is not None


def test_process_signal_short_direction():
    with patch("validate_risk.check_kill_switch", return_value=False):
        order = process_signal(SIGNAL_SHORT, SETTINGS, bankroll=1000.0, portfolio_state=PORTFOLIO_OK)

    assert order["direction"] == "short"
    # entry_price = 1 - 0.75 = 0.25
    assert order["platform"] == "polymarket"


def test_process_signal_boundary_price_skipped():
    signal = {**SIGNAL_LONG, "current_yes_price": 1.0}
    order = process_signal(signal, SETTINGS, bankroll=1000.0, portfolio_state=PORTFOLIO_OK)
    assert order["order_skipped"] is True
    assert "boundary" in order["skip_reason"]


def test_process_signal_null_p_model_skipped():
    signal = {**SIGNAL_LONG, "p_model": None}
    order = process_signal(signal, SETTINGS, bankroll=1000.0, portfolio_state=PORTFOLIO_OK)
    assert order["order_skipped"] is True
    assert order["skip_reason"] == "missing_p_model"


def test_process_signal_max_positions_blocks():
    portfolio = {**PORTFOLIO_OK, "open_positions": 15}
    with patch("validate_risk.check_kill_switch", return_value=False):
        order = process_signal(SIGNAL_LONG, SETTINGS, bankroll=1000.0, portfolio_state=portfolio)
    assert order["order_skipped"] is True
    assert "max_positions" in order["risk_flags"]


def test_process_signal_ensemble_gate_blocks():
    signal = {**SIGNAL_LONG, "llm_consensus": {"models_responded": 2}}
    with patch("validate_risk.check_kill_switch", return_value=False):
        order = process_signal(signal, SETTINGS, bankroll=1000.0, portfolio_state=PORTFOLIO_OK)
    assert order["order_skipped"] is True
    assert "ensemble" in order["risk_flags"]


def test_process_signal_kalshi_platform():
    signal = {**SIGNAL_LONG, "market_id": "KXBTC-26MAR-T74000"}
    with patch("validate_risk.check_kill_switch", return_value=False):
        order = process_signal(signal, SETTINGS, bankroll=1000.0, portfolio_state=PORTFOLIO_OK)
    assert order["platform"] == "kalshi"


# --- Integration: main() with mock stdin ---

# --- direction_to_kelly — extended ---

def test_direction_yes_maps_to_yes():
    """direction='yes' (single-signal mode) should normalise correctly."""
    assert direction_to_kelly("yes") == "yes"


def test_direction_no_maps_to_no():
    assert direction_to_kelly("no") == "no"


# --- run_single_signal ---

_PORTFOLIO_OK = {
    "open_positions": 0,
    "current_drawdown": 0.0,
    "daily_pnl": 0.0,
    "daily_loss_pct": 0.0,
    "peak_value": 1.0,
    "current_value": 1.0,
}

_SIGNAL_SINGLE = {
    "market_id": "test-1",
    "platform": "kalshi",
    "p_model": 0.72,
    "direction": "yes",
    "entry_price": 0.60,
    "current_yes_price": 0.60,
    "edge": 0.12,
    "models_responded": 4,
}


def test_run_single_signal_paper_approved(monkeypatch, tmp_path):
    """run_single_signal in paper mode returns an approved, filled trade."""
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("BANKROLL_USD", "1000")

    with patch("risk_pipeline.load_portfolio_state", return_value=_PORTFOLIO_OK), \
         patch("validate_risk.check_kill_switch", return_value=False):
        result = run_single_signal(_SIGNAL_SINGLE, settings=SETTINGS, bankroll=1000.0)

    assert isinstance(result, PipelineResult)
    assert result.risk_result["approved"] is True
    assert result.order_result is not None
    assert result.order_result["status"] == "paper"


def test_run_single_signal_stop_file_blocks(monkeypatch, tmp_path):
    stop_file = tmp_path / "STOP"
    stop_file.touch()
    monkeypatch.setattr(eo, "STOP_FILE", stop_file)
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    with patch("validate_risk.STOP_FILE_PATH", stop_file), \
         patch("risk_pipeline.load_portfolio_state", return_value=_PORTFOLIO_OK):
        result = run_single_signal(_SIGNAL_SINGLE, settings=SETTINGS, bankroll=1000.0)

    assert result.risk_result["approved"] is False
    assert result.order_result is None


def test_run_single_signal_low_edge_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")

    low_edge = {**_SIGNAL_SINGLE, "edge": 0.01}
    with patch("risk_pipeline.load_portfolio_state", return_value=_PORTFOLIO_OK), \
         patch("validate_risk.check_kill_switch", return_value=False):
        result = run_single_signal(low_edge, settings=SETTINGS, bankroll=1000.0)

    assert result.risk_result["approved"] is False
    assert "edge" in result.risk_result["gates_failed"]


def test_run_single_signal_result_serialisable(monkeypatch, tmp_path):
    monkeypatch.setattr(eo, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(eo, "TRADE_LOG", tmp_path / "trade_log.jsonl")
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("BANKROLL_USD", "1000")

    with patch("risk_pipeline.load_portfolio_state", return_value=_PORTFOLIO_OK), \
         patch("validate_risk.check_kill_switch", return_value=False):
        result = run_single_signal(_SIGNAL_SINGLE, settings=SETTINGS, bankroll=1000.0)

    serialised = json.dumps(asdict(result))
    parsed = json.loads(serialised)
    assert all(k in parsed for k in ("signal", "risk_result", "size", "order_result"))


# --- Integration: main() with mock stdin ---

def test_main_filters_skipped_signals(tmp_path, monkeypatch, capsys):
    signals_data = {
        "scan_id": "scan_test",
        "signals": [
            {**SIGNAL_LONG, "predict_skipped": True, "skip_reason": "insufficient sources"},
            {**SIGNAL_SHORT, "predict_skipped": True, "skip_reason": "low edge"},
        ],
    }
    signals_file = tmp_path / "signals_test.json"
    signals_file.write_text(json.dumps(signals_data))

    monkeypatch.setenv("BANKROLL_USD", "100")
    monkeypatch.chdir(tmp_path)

    # Patch DATA_DIR to write to tmp
    with patch("risk_pipeline.DATA_DIR", tmp_path), \
         patch("risk_pipeline.load_settings", return_value=SETTINGS), \
         patch("risk_pipeline.load_portfolio_state", return_value=PORTFOLIO_OK):
        sys.argv = ["risk_pipeline.py", "--file", str(signals_file)]
        from risk_pipeline import main
        main()

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["orders"] == []  # all signals were skipped
