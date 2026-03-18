"""Unit tests for cost_tracker.py"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/pm-predict/scripts"))

from cost_tracker import (
    BudgetExceededError,
    _cost_usd,
    check_budget,
    get_daily_cost,
    record_cost,
)


# ---------------------------------------------------------------------------
# _cost_usd
# ---------------------------------------------------------------------------

def test_cost_usd_known_model():
    # claude-sonnet-4-6: $3.00/M input, $15.00/M output
    cost = _cost_usd("claude-sonnet-4-6", input_tokens=1000, output_tokens=100)
    expected = (1000 * 3.00 + 100 * 15.00) / 1_000_000
    assert abs(cost - expected) < 1e-10


def test_cost_usd_haiku():
    cost = _cost_usd("claude-haiku-4-5-20251001", input_tokens=500, output_tokens=10)
    expected = (500 * 0.80 + 10 * 4.00) / 1_000_000
    assert abs(cost - expected) < 1e-10


def test_cost_usd_zero_tokens():
    assert _cost_usd("claude-sonnet-4-6", 0, 0) == 0.0


def test_cost_usd_unknown_model_uses_fallback():
    # Unknown model → fallback rates ($1.00/$5.00 per M)
    cost = _cost_usd("unknown-model-xyz", input_tokens=1_000_000, output_tokens=1_000_000)
    expected = (1.00 + 5.00)  # 1M each at fallback rates
    assert abs(cost - expected) < 1e-6


def test_cost_usd_gpt5_mini():
    cost = _cost_usd("gpt-5-mini-2025-08-07", input_tokens=1000, output_tokens=200)
    expected = (1000 * 0.15 + 200 * 0.60) / 1_000_000
    assert abs(cost - expected) < 1e-10


# ---------------------------------------------------------------------------
# record_cost + get_daily_cost
# ---------------------------------------------------------------------------

def test_record_and_read_cost(tmp_path):
    log_file = tmp_path / "ai_costs.jsonl"
    with patch("cost_tracker._COST_LOG", log_file):
        cost = record_cost("claude-sonnet-4-6", 1000, 100, "test_caller")
        assert cost > 0.0

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["model"] == "claude-sonnet-4-6"
        assert entry["input_tokens"] == 1000
        assert entry["output_tokens"] == 100
        assert entry["caller"] == "test_caller"
        assert abs(entry["cost_usd"] - cost) < 1e-8


def test_get_daily_cost_empty_log(tmp_path):
    log_file = tmp_path / "ai_costs.jsonl"
    with patch("cost_tracker._COST_LOG", log_file):
        assert get_daily_cost() == 0.0


def test_get_daily_cost_no_file(tmp_path):
    log_file = tmp_path / "nonexistent.jsonl"
    with patch("cost_tracker._COST_LOG", log_file):
        assert get_daily_cost() == 0.0


def test_get_daily_cost_accumulates(tmp_path):
    log_file = tmp_path / "ai_costs.jsonl"
    with patch("cost_tracker._COST_LOG", log_file):
        record_cost("claude-sonnet-4-6", 1000, 100, "call1")
        record_cost("claude-haiku-4-5-20251001", 500, 10, "call2")
        total = get_daily_cost()
        expected = (
            _cost_usd("claude-sonnet-4-6", 1000, 100)
            + _cost_usd("claude-haiku-4-5-20251001", 500, 10)
        )
        assert abs(total - expected) < 1e-6


def test_get_daily_cost_filters_other_dates(tmp_path):
    log_file = tmp_path / "ai_costs.jsonl"
    # Write one entry for a past date, one for today
    past_entry = json.dumps({"ts": "2020-01-01T00:00:00+00:00", "cost_usd": 99.99})
    today_entry = json.dumps({
        "ts": "2099-12-31T12:00:00+00:00",
        "model": "claude-sonnet-4-6",
        "cost_usd": 0.001,
    })
    log_file.write_text(past_entry + "\n" + today_entry + "\n")
    with patch("cost_tracker._COST_LOG", log_file):
        total = get_daily_cost(date_str="2099-12-31")
        assert abs(total - 0.001) < 1e-8


def test_get_daily_cost_ignores_malformed_lines(tmp_path):
    log_file = tmp_path / "ai_costs.jsonl"
    log_file.write_text("not-valid-json\n")
    with patch("cost_tracker._COST_LOG", log_file):
        assert get_daily_cost() == 0.0


# ---------------------------------------------------------------------------
# check_budget
# ---------------------------------------------------------------------------

def test_check_budget_under_limit(tmp_path):
    log_file = tmp_path / "ai_costs.jsonl"
    with patch("cost_tracker._COST_LOG", log_file):
        settings = {"cost_control": {"max_daily_ai_cost_usd": 30.0}}
        # No spend yet → should not raise
        check_budget(settings)


def test_check_budget_exceeded(tmp_path):
    log_file = tmp_path / "ai_costs.jsonl"
    with patch("cost_tracker._COST_LOG", log_file):
        settings = {"cost_control": {"max_daily_ai_cost_usd": 0.001}}
        # Record a small cost that exceeds the tiny budget
        record_cost("claude-sonnet-4-6", 10000, 1000, "test")
        raised = False
        try:
            check_budget(settings)
        except BudgetExceededError:
            raised = True
        assert raised, "Expected BudgetExceededError"


def test_check_budget_exactly_at_limit(tmp_path):
    log_file = tmp_path / "ai_costs.jsonl"
    today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat()
    entry = json.dumps({"ts": f"{today}T12:00:00+00:00", "cost_usd": 30.0})
    log_file.write_text(entry + "\n")
    with patch("cost_tracker._COST_LOG", log_file):
        settings = {"cost_control": {"max_daily_ai_cost_usd": 30.0}}
        raised = False
        try:
            check_budget(settings)
        except BudgetExceededError:
            raised = True
        assert raised, "Expected BudgetExceededError when spend == limit"


def test_check_budget_missing_cost_control_section(tmp_path):
    log_file = tmp_path / "ai_costs.jsonl"
    with patch("cost_tracker._COST_LOG", log_file):
        # No cost_control key → defaults to $30 limit, no spend → should not raise
        check_budget({})
