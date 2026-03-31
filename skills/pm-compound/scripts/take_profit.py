"""
take_profit.py — Take Profit Checker for pm-compound skill

Monitors open positions and places exit orders when a take-profit threshold
is reached.

Usage:
    python skills/pm-compound/scripts/take_profit.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_RISK_SCRIPTS = _PROJECT_ROOT / "skills" / "pm-risk" / "scripts"

for _p in (str(_SCRIPT_DIR), str(_RISK_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config_loader import DATA_DIR  # noqa: E402

TRADE_LOG_PATH = DATA_DIR / "trade_log.jsonl"
DEFAULT_TAKE_PROFIT_PCT = 0.15


# ---------------------------------------------------------------------------
# Price Fetching
# ---------------------------------------------------------------------------

def _get_current_yes_price(platform: str, market_id: str) -> float | None:
    """Fetch current YES price from platform orderbook. Returns None on error."""
    try:
        if platform == "polymarket":
            import httpx as _httpx  # noqa: PLC0415
            gamma_base = os.environ.get(
                "POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com"
            )
            with _httpx.Client(timeout=10.0) as _client:
                resp = _client.get(
                    f"{gamma_base}/markets",
                    params={"condition_id": market_id},
                )
            resp.raise_for_status()
            results = resp.json()
            data = results[0] if isinstance(results, list) and results else results
            raw = data.get("outcomePrices") or ["0.5", "0.5"]
            if isinstance(raw, str):
                import json as _json
                raw = _json.loads(raw)
            return float(raw[0])
        elif platform == "kalshi":
            import httpx  # noqa: PLC0415
            from kalshi_client import (  # noqa: PLC0415
                _get_headers, _ORDERBOOK_PATH_TPL,
            )
            ob_path = _ORDERBOOK_PATH_TPL.format(ticker=market_id)
            headers = _get_headers("GET", ob_path)
            if headers is None:
                return None
            base_url = os.environ.get(
                "KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"
            )
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    f"{base_url}/markets/{market_id}/orderbook",
                    headers=headers,
                )
            resp.raise_for_status()
            orderbook = resp.json().get("orderbook", {})
            yes_levels = orderbook.get("yes", [])
            if yes_levels:
                best_cents = max(level[0] for level in yes_levels)
                return best_cents / 100.0
    except Exception as e:
        print(f"[compound] Price fetch error for {market_id}: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Take Profit Logic
# ---------------------------------------------------------------------------

def check_take_profit(
    trade: dict[str, Any],
    platform_state: dict[str, float],
    take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT,
) -> bool:
    """
    Returns True if position should be exited.

    - YES position: current_yes_price >= fill_price + take_profit_pct
    - NO position:  current_no_price >= fill_no_price + take_profit_pct
                    (equivalent: current_yes_price <= fill_yes_price - take_profit_pct)

    Args:
        trade:           Trade record dict
        platform_state:  Mapping of market_id → current YES price
        take_profit_pct: Profit threshold to trigger exit (default 0.15)
    """
    market_id = trade.get("market_id", "")
    direction = trade.get("direction", "yes")
    fill_price = trade.get("fill_price")

    if fill_price is None:
        return False

    current_yes = platform_state.get(market_id)
    if current_yes is None:
        return False

    if direction == "yes":
        return current_yes >= fill_price + take_profit_pct
    else:
        # NO position: fill_price is the NO fill price = 1 - fill_yes
        fill_yes = 1.0 - fill_price
        current_no = 1.0 - current_yes
        fill_no = fill_price
        return current_no >= fill_no + take_profit_pct


def _place_exit_order(trade: dict[str, Any]) -> dict[str, Any] | None:
    """Place a SELL order to exit the position at current bid."""
    platform = trade.get("platform", "")
    market_id = trade.get("market_id", "")
    direction = trade.get("direction", "yes")
    size_contracts = trade.get("size_contracts", 0)

    if size_contracts <= 0:
        return None

    import os
    use_demo = os.environ.get("KALSHI_USE_DEMO", "true").lower() == "true"

    try:
        if platform == "polymarket":
            from polymarket_client import place_order  # noqa: PLC0415
            # SELL the held position: if YES, sell YES token; if NO, sell NO token
            return place_order(
                market_id=market_id,
                direction=direction,
                contracts=size_contracts,
                limit_price=trade.get("fill_price", 0.5),
                side="SELL",
            )
        elif platform == "kalshi":
            from kalshi_client import place_order  # noqa: PLC0415
            return place_order(market_id, direction, size_contracts, trade.get("fill_price", 0.5), use_demo)
    except Exception as e:
        print(f"[compound] Exit order error for {market_id}: {e}", file=sys.stderr)
    return None


def run_take_profit_checks(settings: dict) -> list[str]:
    """
    Load open trades, fetch current prices, place exit orders for qualifying trades.

    Args:
        settings: Full settings dict (uses settings["execution"]["take_profit_pct"])

    Returns:
        List of trade_ids that were exited.
    """
    take_profit_pct = (
        settings.get("execution", {}).get("take_profit_pct", DEFAULT_TAKE_PROFIT_PCT)
    )

    if not TRADE_LOG_PATH.exists():
        print("[compound] No trade log found — skipping take-profit checks", file=sys.stderr)
        return []

    # Load open (placed/paper) trades
    open_trades = []
    with open(TRADE_LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                if trade.get("outcome") is None and trade.get("status") in ("placed", "paper"):
                    open_trades.append(trade)
            except json.JSONDecodeError:
                continue

    if not open_trades:
        print("[compound] No open trades for take-profit checks", file=sys.stderr)
        return []

    print(f"[compound] Checking take-profit on {len(open_trades)} open trade(s)", file=sys.stderr)

    # Fetch current prices for unique markets
    unique_markets: dict[str, str] = {}  # market_id → platform
    for trade in open_trades:
        unique_markets[trade["market_id"]] = trade["platform"]

    platform_state: dict[str, float] = {}
    for market_id, platform in unique_markets.items():
        price = _get_current_yes_price(platform, market_id)
        if price is not None:
            platform_state[market_id] = price

    # Check each trade
    exited: list[str] = []
    for trade in open_trades:
        trade_id = trade.get("trade_id", "")
        if not check_take_profit(trade, platform_state, take_profit_pct):
            continue

        market_id = trade["market_id"]
        current_yes = platform_state.get(market_id, "?")
        print(
            f"[compound] Take-profit triggered: {trade_id} {market_id} "
            f"(fill={trade.get('fill_price')}, current={current_yes})",
            file=sys.stderr,
        )
        result = _place_exit_order(trade)
        if result:
            print(
                f"[compound] Exit order placed: {result.get('order_id')} status={result.get('status')}",
                file=sys.stderr,
            )
            exited.append(trade_id)
        else:
            print(f"[compound] Exit order failed for {trade_id}", file=sys.stderr)

    print(f"[compound] Take-profit: exited {len(exited)} position(s)", file=sys.stderr)
    return exited


# ---------------------------------------------------------------------------
# Main (standalone invocation)
# ---------------------------------------------------------------------------

def main() -> None:
    import yaml
    settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
    with open(settings_path) as f:
        settings = yaml.safe_load(f)
    exited = run_take_profit_checks(settings)
    print(json.dumps({"exited_count": len(exited), "trade_ids": exited}, indent=2))


if __name__ == "__main__":
    main()
