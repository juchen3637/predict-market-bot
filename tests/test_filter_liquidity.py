"""
test_filter_liquidity.py — Tests for the scan-time liquidity floor.

The floor probes per-market orderbook depth and drops candidates whose
cross-side bid stack can't fill a typical Kelly-sized order.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/pm-scan/scripts"))

import filter_markets as fm
from filter_markets import MarketCandidate


def _candidate(
    *,
    market_id: str = "M1",
    platform: str = "kalshi",
    title: str = "test market",
    yes_bid: float = 0.55,
    yes_ask: float = 0.65,
    current_yes_price: float = 0.60,
    volume_24h: int = 1000,
    open_interest: int = 500,
) -> MarketCandidate:
    return MarketCandidate(
        market_id=market_id,
        platform=platform,
        title=title,
        category="test",
        current_yes_price=current_yes_price,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        volume_24h=volume_24h,
        open_interest=open_interest,
        days_to_expiry=7,
        anomaly_flags=[],
        scanned_at="2026-04-27T00:00:00+00:00",
        clob_token_ids=[],
    )


# ---------------------------------------------------------------------------
# passes_liquidity_floor — Kalshi snapshot semantics
# ---------------------------------------------------------------------------

def test_passes_liquidity_floor_kalshi_both_sides_ample():
    """Both cross sides exceed floor at cross threshold → pass."""
    cand = _candidate(yes_bid=0.55, yes_ask=0.65)  # mid=0.60
    snapshot = {
        "yes_bids": [(0.40, 250.0)],   # supports BUY NO; 0.40 >= 1-0.60-0.05 = 0.35
        "no_bids":  [(0.40, 250.0)],   # supports BUY YES; 0.40 >= 0.35
    }
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.05) is True


def test_passes_liquidity_floor_kalshi_one_side_thin_fails():
    """If either side < floor, candidate fails (worst-side wins)."""
    cand = _candidate(yes_bid=0.55, yes_ask=0.65)
    snapshot = {
        "yes_bids": [(0.40, 250.0)],   # ample
        "no_bids":  [(0.40, 50.0)],    # thin — $50 < $200 floor
    }
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.05) is False


def test_passes_liquidity_floor_kalshi_below_threshold_excluded():
    """Bids below cross_threshold are not summed."""
    cand = _candidate(yes_bid=0.55, yes_ask=0.65)  # cross_threshold = 0.35 with band=0.05
    snapshot = {
        "yes_bids": [(0.30, 9999.0)],  # 0.30 < 0.35 → excluded
        "no_bids":  [(0.30, 9999.0)],
    }
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=100.0, band=0.05) is False


def test_passes_liquidity_floor_band_widens_inclusion():
    """Band parameter widens the inclusion window."""
    cand = _candidate(yes_bid=0.55, yes_ask=0.65)
    # band=0 → cross_threshold=0.40 → 0.38 excluded; band=0.05 → 0.35 → 0.38 included
    snapshot = {
        "yes_bids": [(0.38, 250.0)],
        "no_bids":  [(0.38, 250.0)],
    }
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.0) is False
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.05) is True


def test_passes_liquidity_floor_uses_mid_from_bid_ask():
    """mid = (yes_bid + yes_ask) / 2 when both > 0; current_yes_price ignored."""
    cand = _candidate(yes_bid=0.30, yes_ask=0.50, current_yes_price=0.99)
    # mid=0.40 → cross_threshold=0.60 (band=0)
    snapshot = {
        "yes_bids": [(0.60, 250.0)],
        "no_bids":  [(0.60, 250.0)],
    }
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.0) is True


def test_passes_liquidity_floor_falls_back_to_current_yes_price():
    """If yes_bid or yes_ask is 0, fall back to current_yes_price."""
    cand = _candidate(yes_bid=0.0, yes_ask=0.0, current_yes_price=0.40)
    # mid=0.40 → cross_threshold=0.60 (band=0)
    snapshot = {
        "yes_bids": [(0.60, 250.0)],
        "no_bids":  [(0.60, 250.0)],
    }
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.0) is True


def test_passes_liquidity_floor_kalshi_empty_snapshot_fails():
    cand = _candidate()
    snapshot = {"yes_bids": [], "no_bids": []}
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.05) is False


# ---------------------------------------------------------------------------
# passes_liquidity_floor — Polymarket snapshot semantics
# ---------------------------------------------------------------------------

def test_passes_liquidity_floor_polymarket_both_sides_ample():
    """asks <= mid+band give YES dollars; bids >= 1-mid-band give NO dollars."""
    cand = _candidate(platform="polymarket", yes_bid=0.55, yes_ask=0.65)
    # mid=0.60. YES dollars = sum(p*size for asks where p<=0.65). NO dollars = sum((1-p)*size for bids where p>=0.35).
    snapshot = {
        "asks": [(0.62, 500.0)],   # YES dollars = 0.62 * 500 = $310
        "bids": [(0.40, 500.0)],   # NO  dollars = 0.60 * 500 = $300
    }
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.05) is True


def test_passes_liquidity_floor_polymarket_no_side_thin_fails():
    cand = _candidate(platform="polymarket", yes_bid=0.55, yes_ask=0.65)
    snapshot = {
        "asks": [(0.62, 500.0)],   # YES $310
        "bids": [(0.40, 50.0)],    # NO  $30 — below floor
    }
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.05) is False


def test_passes_liquidity_floor_polymarket_above_band_excluded():
    cand = _candidate(platform="polymarket", yes_bid=0.55, yes_ask=0.65)
    # mid=0.60, band=0.05 → asks must be <= 0.65; ask at 0.70 excluded
    snapshot = {
        "asks": [(0.70, 9999.0)],
        "bids": [(0.40, 500.0)],
    }
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.05) is False


def test_passes_liquidity_floor_polymarket_empty_snapshot_fails():
    cand = _candidate(platform="polymarket")
    snapshot = {"asks": [], "bids": []}
    assert fm.passes_liquidity_floor(cand, snapshot, min_dollars=200.0, band=0.05) is False


# ---------------------------------------------------------------------------
# _apply_liquidity_floor — top-N + drop semantics + stats
# ---------------------------------------------------------------------------

def test_apply_liquidity_floor_drops_thin_keeps_ample(monkeypatch):
    cands = [
        _candidate(market_id="thin",  platform="kalshi"),
        _candidate(market_id="ample", platform="kalshi"),
    ]
    snapshots = {
        "thin":  {"yes_bids": [], "no_bids": []},
        "ample": {"yes_bids": [(0.40, 500.0)], "no_bids": [(0.40, 500.0)]},
    }
    monkeypatch.setattr(fm, "_fetch_kalshi_snapshot", lambda mid, use_demo=True: snapshots[mid])
    monkeypatch.setattr(fm.time, "sleep", lambda _x: None)

    kept, stats = fm._apply_liquidity_floor(
        cands, min_dollars=200.0, band=0.05, top_n=10,
    )
    assert [c.market_id for c in kept] == ["ample"]
    assert stats == {
        "probed": 2,
        "kept": 1,
        "dropped_thin": 1,
        "dropped_fetch_error": 0,
        "skipped_below_rank": 0,
    }


def test_apply_liquidity_floor_top_n_caps_probe_count(monkeypatch):
    """Below top-N candidates are dropped without probing."""
    cands = [
        _candidate(market_id=f"M{i}", open_interest=1000 - i, platform="kalshi")
        for i in range(5)
    ]
    monkeypatch.setattr(
        fm, "_fetch_kalshi_snapshot",
        lambda mid, use_demo=True: {"yes_bids": [(0.40, 500.0)], "no_bids": [(0.40, 500.0)]},
    )
    monkeypatch.setattr(fm.time, "sleep", lambda _x: None)

    kept, stats = fm._apply_liquidity_floor(cands, min_dollars=100.0, band=0.05, top_n=3)
    assert len(kept) == 3
    assert [c.market_id for c in kept] == ["M0", "M1", "M2"]
    assert stats["probed"] == 3
    assert stats["kept"] == 3
    assert stats["skipped_below_rank"] == 2


def test_apply_liquidity_floor_fetch_error_drops_candidate(monkeypatch):
    """Snapshot fetch raising drops the candidate, others survive."""
    good = _candidate(market_id="good", platform="kalshi")
    bad  = _candidate(market_id="bad",  platform="kalshi")
    def fake_kalshi_snapshot(mid, *, use_demo=True):
        if mid == "bad":
            raise RuntimeError("connect failed")
        return {"yes_bids": [(0.40, 500.0)], "no_bids": [(0.40, 500.0)]}
    monkeypatch.setattr(fm, "_fetch_kalshi_snapshot", fake_kalshi_snapshot)
    monkeypatch.setattr(fm.time, "sleep", lambda _x: None)

    kept, stats = fm._apply_liquidity_floor([good, bad], min_dollars=100.0, band=0.05, top_n=10)
    assert [c.market_id for c in kept] == ["good"]
    assert stats["probed"] == 2
    assert stats["dropped_fetch_error"] == 1
    assert stats["dropped_thin"] == 0


def test_apply_liquidity_floor_routes_polymarket_to_polymarket_snapshot(monkeypatch):
    """Polymarket candidates use the Polymarket snapshot fn."""
    poly_cand = _candidate(market_id="poly1", platform="polymarket", yes_bid=0.55, yes_ask=0.65)
    monkeypatch.setattr(
        fm, "_fetch_polymarket_snapshot",
        lambda mid, use_demo=False: {
            "asks": [(0.62, 500.0)],
            "bids": [(0.40, 500.0)],
        },
    )
    def kalshi_should_not_run(*a, **kw):
        raise AssertionError("Kalshi snapshot called for Polymarket candidate")
    monkeypatch.setattr(fm, "_fetch_kalshi_snapshot", kalshi_should_not_run)
    monkeypatch.setattr(fm.time, "sleep", lambda _x: None)

    kept, stats = fm._apply_liquidity_floor([poly_cand], min_dollars=200.0, band=0.05, top_n=10)
    assert [c.market_id for c in kept] == ["poly1"]


def test_apply_liquidity_floor_empty_input_short_circuits():
    kept, stats = fm._apply_liquidity_floor([], min_dollars=200.0, band=0.05, top_n=10)
    assert kept == []
    assert stats["probed"] == 0
    assert stats["kept"] == 0
    assert stats["dropped_thin"] == 0
    assert stats["dropped_fetch_error"] == 0
    assert stats["skipped_below_rank"] == 0
