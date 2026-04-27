"""
execute_order.py — Order Execution for pm-risk skill

Places limit orders on Polymarket or Kalshi after all risk gates pass.
In paper mode, simulates the order without hitting any real API.

Kill switch is checked one final time immediately before placing the order.

Reference: github.com/suislanchez/polymarket-kalshi-weather-bot (execution patterns)
Reference: github.com/terauss/Polymarket-Kalshi-Arbitrage-bot (order management)
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from check_depth import has_adequate_depth

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
STOP_FILE = _PROJECT_ROOT / "STOP"
TRADE_LOG = _PROJECT_ROOT / "data" / "trade_log.jsonl"
MAX_SLIPPAGE = 0.02   # 2% — abort if fill deviates more than this
HEDGE_TRIGGER = 0.05  # 5% — flag for hedge review after fill


def _scan_liquidity_floor_tag() -> str | None:
    """Tag trades placed while the scan-time liquidity floor is enabled.

    Returns "v1" when scan.liquidity_check_enabled is True; otherwise None.
    Lets nightly metrics segment "post-floor" trades from historical ones.
    """
    try:
        with open(_PROJECT_ROOT / "config" / "settings.yaml") as f:
            settings = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None
    if settings.get("scan", {}).get("liquidity_check_enabled"):
        return "v1"
    return None


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    trade_id: str
    market_id: str
    platform: str
    direction: str           # "yes" | "no"
    size_contracts: int
    size_usd: float
    entry_price: float
    fill_price: float | None
    p_model: float
    edge: float
    kelly_fraction: float
    status: str              # "placed" | "rejected" | "paper" | "failed"
    rejection_reason: str | None
    placed_at: str
    resolved_at: str | None
    outcome: str | None      # "win" | "loss" | None (pending)
    pnl: float | None
    title: str = ""
    hedge_needed: bool = False  # set if market moved >5% from entry after fill
    hedge_order_id: str | None = None  # order_id of the hedge order, if placed
    order_id: str | None = None  # platform order_id for later status checks
    scan_liquidity_floor: str | None = None  # "v1" if placed under the scan-time floor


# ---------------------------------------------------------------------------
# Platform Order Placers (stubs — implement in Phase 3)
# ---------------------------------------------------------------------------

def place_polymarket_order(
    market_id: str,
    direction: str,
    contracts: int,
    limit_price: float,
    clob_token_ids: list[str] | None = None,
    use_demo: bool = False,
) -> dict[str, Any]:
    """
    Place a limit order on Polymarket CLOB.
    Returns: {"order_id": str, "fill_price": float | None, "status": str}

    market_id is the condition ID (used for resolution/tracking).
    clob_token_ids[0] is the YES token and [1] is the NO token — the CLOB
    requires a token ID for OrderArgs, not the condition ID. Pass
    clob_token_ids from the scanned MarketCandidate to enable live orders.

    Reference: https://docs.polymarket.com/#place-order
    Reference: github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot
    """
    from polymarket_client import place_order as _pm_place

    # Select the correct CLOB token ID for the order direction.
    # YES token is at index 0, NO token is at index 1.
    token_id: str | None = None
    if clob_token_ids:
        token_id = clob_token_ids[0] if direction == "yes" else clob_token_ids[1] if len(clob_token_ids) > 1 else clob_token_ids[0]

    result = _pm_place(market_id, direction, contracts, limit_price, token_id=token_id, use_demo=use_demo)
    return result if result is not None else {
        "order_id": None,
        "fill_price": None,
        "status": "declined",
        "reason": "live_mode_not_enabled",
    }


def place_kalshi_order(
    market_ticker: str,
    direction: str,
    contracts: int,
    limit_price: float,
    use_demo: bool = True,
) -> dict[str, Any]:
    """
    Place a limit order on Kalshi.
    Returns: {"order_id": str, "fill_price": float | None, "status": str}

    Reference: https://trading-api.readme.io/#create-order
    Reference: github.com/suislanchez/polymarket-kalshi-weather-bot
    """
    from kalshi_client import place_order as _k_place
    result = _k_place(market_ticker, direction, contracts, limit_price, use_demo)
    return result if result is not None else {
        "order_id": None,
        "fill_price": None,
        "status": "declined",
        "reason": "live_mode_not_enabled",
    }


# ---------------------------------------------------------------------------
# Paper Trading Simulator
# ---------------------------------------------------------------------------

def simulate_paper_order(
    market_id: str,
    direction: str,
    contracts: int,
    limit_price: float,
    platform: str,
) -> dict[str, Any]:
    """Simulate an order fill for paper trading. Always 'fills' at limit price."""
    return {
        "order_id": f"paper_{uuid.uuid4().hex[:8]}",
        "fill_price": limit_price,
        "status": "filled",
        "simulated": True,
    }


# ---------------------------------------------------------------------------
# Trade Logger
# ---------------------------------------------------------------------------

def append_trade_log(record: TradeRecord) -> None:
    """Append a trade record to the append-only trade log."""
    TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_LOG, "a") as f:
        f.write(json.dumps(asdict(record)) + "\n")


# ---------------------------------------------------------------------------
# Main Execution
# ---------------------------------------------------------------------------

def execute(
    signal: dict[str, Any],
    position: dict[str, Any],
) -> TradeRecord:
    """
    Execute a trade or simulate it in paper mode.
    Final kill switch check happens here.
    """
    paper_mode = os.environ.get("PAPER_TRADING", "true").lower() == "true"
    polymarket_paper = os.environ.get("POLYMARKET_PAPER", "true").lower() == "true"
    now = datetime.now(timezone.utc).isoformat()

    market_id = signal["market_id"]
    title = signal.get("title", "")
    platform = signal["platform"]
    direction = signal["direction"]
    entry_price = float(signal.get("entry_price", signal.get("current_yes_price", 0.5)))
    if direction == "no":
        entry_price = 1.0 - entry_price  # No contract price

    contracts = int(position.get("contracts", 0))
    size_usd = float(position.get("size_usd_capped", 0))
    kelly_fraction = float(position.get("kelly_fraction_used", 0.25))

    base_record = TradeRecord(
        trade_id=str(uuid.uuid4()),
        market_id=market_id,
        title=title,
        platform=platform,
        direction=direction,
        size_contracts=contracts,
        size_usd=size_usd,
        entry_price=entry_price,
        fill_price=None,
        p_model=float(signal.get("p_model", 0)),
        edge=float(signal.get("edge", 0)),
        kelly_fraction=kelly_fraction,
        status="pending",
        rejection_reason=None,
        placed_at=now,
        resolved_at=None,
        outcome=None,
        pnl=None,
        scan_liquidity_floor=_scan_liquidity_floor_tag(),
    )

    # Final kill switch check
    if STOP_FILE.exists():
        record = TradeRecord(**{**asdict(base_record), "status": "rejected", "rejection_reason": "kill_switch_active"})
        append_trade_log(record)
        return record

    if contracts <= 0:
        record = TradeRecord(**{**asdict(base_record), "status": "rejected", "rejection_reason": "zero_contracts"})
        append_trade_log(record)
        return record

    # Resolve effective paper mode for this platform
    effective_paper = paper_mode or (platform == "polymarket" and polymarket_paper)
    pm_use_demo = os.environ.get("POLYMARKET_USE_DEMO", "false").lower() == "true"

    # Pre-trade orderbook depth check (skipped in paper mode — no real book to query)
    if not effective_paper and not has_adequate_depth(platform, market_id, direction, entry_price, contracts, use_demo=pm_use_demo):
        print(
            f"[pm-risk] Insufficient orderbook depth for {market_id} ({platform}). Aborting.",
            file=sys.stderr,
        )
        record = TradeRecord(**{**asdict(base_record), "status": "rejected", "rejection_reason": "insufficient_depth"})
        append_trade_log(record)
        return record

    # Execute or simulate
    try:
        if effective_paper:
            result = simulate_paper_order(market_id, direction, contracts, entry_price, platform)
            status = "paper"
        elif platform == "kalshi":
            use_demo = os.environ.get("KALSHI_USE_DEMO", "true").lower() == "true"
            result = place_kalshi_order(market_id, direction, contracts, entry_price, use_demo)
            status = "placed"
        elif platform == "polymarket":
            clob_token_ids = signal.get("clob_token_ids") or []
            result = place_polymarket_order(market_id, direction, contracts, entry_price, clob_token_ids=clob_token_ids, use_demo=pm_use_demo)
            status = "placed"
        else:
            raise ValueError(f"Unknown platform: {platform}")

        fill_price = result.get("fill_price")
        result_status = result.get("status", "filled")
        order_id = result.get("order_id")

        # Resting limit orders are accepted as placed — do not block the pipeline polling for fills.
        # The order stays live in the Kalshi book; nightly consolidation reconciles open positions.

        # Platform declined the order (live stub or real rejection)
        if result_status == "declined":
            print(
                f"[pm-risk] Order declined by platform: {result.get('reason', 'unknown')}",
                file=sys.stderr,
            )
            record = TradeRecord(**{
                **asdict(base_record),
                "status": "rejected",
                "rejection_reason": result.get("reason", "platform_declined"),
            })
            append_trade_log(record)
            return record

        # Slippage check
        if fill_price is not None:
            slippage = abs(fill_price - entry_price) / max(entry_price, 0.01)
            if slippage > MAX_SLIPPAGE:
                print(
                    f"[pm-risk] Slippage {slippage:.1%} exceeds {MAX_SLIPPAGE:.0%} limit. Aborting.",
                    file=sys.stderr,
                )
                record = TradeRecord(**{
                    **asdict(base_record),
                    "fill_price": fill_price,
                    "status": "rejected",
                    "rejection_reason": f"slippage_exceeded_{slippage:.3f}",
                })
                append_trade_log(record)
                return record

        # Auto-hedge hook: flag if market has moved significantly from our entry
        raw_yes_price = float(signal.get("current_yes_price", entry_price))
        current_directional_price = raw_yes_price if direction == "yes" else (1.0 - raw_yes_price)
        hedge_needed = (
            fill_price is not None
            and abs(current_directional_price - fill_price) / max(fill_price, 0.01) > HEDGE_TRIGGER
        )
        if hedge_needed:
            print(
                f"[pm-risk] hedge_needed: market at {current_directional_price:.3f} "
                f"vs fill at {fill_price:.3f} "
                f"({abs(current_directional_price - fill_price) / max(fill_price, 0.01):.1%} move)",
                file=sys.stderr,
            )

        record = TradeRecord(**{
            **asdict(base_record),
            "fill_price": fill_price,
            "status": status,
            "hedge_needed": hedge_needed,
            "order_id": order_id,
        })

        # Auto-hedge: place offsetting order if hedge_needed
        if hedge_needed and not paper_mode:
            try:
                from hedge_executor import execute_hedge  # noqa: PLC0415
                use_demo = os.environ.get("KALSHI_USE_DEMO", "true").lower() == "true"
                hedge_result = execute_hedge(record, use_demo=use_demo)
                hedge_order_id = hedge_result.get("order_id")
                print(
                    f"[pm-risk] hedge executed: order_id={hedge_order_id} "
                    f"status={hedge_result.get('status')}",
                    file=sys.stderr,
                )
                record = TradeRecord(**{**asdict(record), "hedge_order_id": hedge_order_id})
            except Exception as e:
                print(f"[pm-risk] Hedge execution error: {e}", file=sys.stderr)

        append_trade_log(record)
        return record

    except NotImplementedError as e:
        print(f"[pm-risk] Execution not implemented: {e}", file=sys.stderr)
        record = TradeRecord(**{**asdict(base_record), "status": "failed", "rejection_reason": "not_implemented"})
        append_trade_log(record)
        return record

    except Exception as e:
        print(f"[pm-risk] Execution error: {e}", file=sys.stderr)
        record = TradeRecord(**{**asdict(base_record), "status": "failed", "rejection_reason": str(e)[:100]})
        append_trade_log(record)
        return record


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    data = json.load(sys.stdin)
    signal = data.get("signal", {})
    position = data.get("position", {})
    result = execute(signal, position)
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
