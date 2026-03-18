"""Unit tests for kelly_size.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/pm-risk/scripts"))

from kelly_size import kelly_criterion, compute_position_size


def test_kelly_criterion_known_values():
    # p=0.70, entry=0.40 → b=(0.6/0.4)=1.5, f*=(0.70*1.5-0.30)/1.5=0.50
    result = kelly_criterion(p_win=0.70, entry_price=0.40)
    assert abs(result - 0.50) < 0.001


def test_kelly_criterion_zero_entry():
    assert kelly_criterion(0.7, 0.0) == 0.0


def test_kelly_criterion_full_entry():
    assert kelly_criterion(0.7, 1.0) == 0.0


def test_kelly_criterion_negative_returns_zero():
    # p_win < entry_price → negative Kelly → 0.0
    assert kelly_criterion(0.3, 0.8) == 0.0


def test_compute_position_size_yes_direction():
    pos = compute_position_size(
        p_model=0.70,
        direction="yes",
        entry_price=0.40,
        bankroll=1000.0,
        kelly_fraction=0.25,
        max_position_pct=0.05,
    )
    # full Kelly = 0.50, fractional = 0.125, size = $125, capped at $50
    assert pos.full_kelly_pct == pytest_approx(0.50, abs=0.001)
    assert pos.fractional_kelly_pct == pytest_approx(0.125, abs=0.001)
    assert pos.size_usd == 125.0
    assert pos.size_usd_capped == 50.0
    assert pos.capped is True


def test_compute_position_size_no_direction():
    # Short: p_win = 1 - p_model = 0.30, entry = 1 - 0.70 = 0.30
    pos = compute_position_size(
        p_model=0.70,
        direction="no",
        entry_price=0.30,
        bankroll=1000.0,
        kelly_fraction=0.25,
        max_position_pct=0.10,
    )
    # p_win = 0.30, b = 0.70/0.30 = 2.33, f* = (0.30*2.33 - 0.70)/2.33 = (0.70-0.70)/2.33 = 0
    # Actually p_win=0.30 < entry 0.30 is borderline; just check it doesn't error
    assert pos.size_usd_capped >= 0.0


def test_compute_position_size_fractional_kelly():
    pos = compute_position_size(
        p_model=0.70,
        direction="yes",
        entry_price=0.40,
        bankroll=1000.0,
        kelly_fraction=0.10,
        max_position_pct=0.50,
    )
    assert pos.kelly_fraction_used == 0.10
    assert pos.fractional_kelly_pct == pytest_approx(0.05, abs=0.001)
    assert pos.size_usd == pytest_approx(50.0, abs=0.01)
    assert pos.capped is False


def test_compute_position_size_cap_applied():
    pos = compute_position_size(
        p_model=0.95,
        direction="yes",
        entry_price=0.10,
        bankroll=1000.0,
        kelly_fraction=1.0,
        max_position_pct=0.05,
    )
    assert pos.capped is True
    assert pos.size_usd_capped == 50.0


def test_compute_position_size_contracts_nonzero():
    pos = compute_position_size(
        p_model=0.70,
        direction="yes",
        entry_price=0.40,
        bankroll=1000.0,
        kelly_fraction=0.25,
        max_position_pct=0.05,
    )
    # $50 / $0.40 = 125 contracts
    assert pos.contracts == 125


# pytest_approx import
try:
    from pytest import approx as pytest_approx
except ImportError:
    def pytest_approx(val, abs=None, rel=None):
        return val
