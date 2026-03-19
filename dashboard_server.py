"""
dashboard_server.py — Human-readable trading dashboard

Serves a live HTML dashboard at http://<host>:8002/
Reads the same data files as metrics_server.py — no extra dependencies.

Port 8002 (8000 = Serena MCP, 8001 = Prometheus metrics)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = _PROJECT_ROOT / "data"
METRICS_PATH = DATA_DIR / "performance_metrics.json"
TRADE_LOG_PATH = DATA_DIR / "trade_log.jsonl"
COST_LOG_PATH = DATA_DIR / "ai_cost_log.jsonl"

PORT = 8002


# ---------------------------------------------------------------------------
# Data readers (same logic as metrics_server.py)
# ---------------------------------------------------------------------------

def _read_metrics() -> dict:
    if not METRICS_PATH.exists():
        return {}
    try:
        with open(METRICS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _read_trades() -> list[dict]:
    if not TRADE_LOG_PATH.exists():
        return []
    trades = []
    try:
        with open(TRADE_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return trades


def _read_daily_ai_cost() -> float:
    if not COST_LOG_PATH.exists():
        return 0.0
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0.0
    try:
        with open(COST_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("timestamp", "")[:10] == today:
                    total += float(r.get("cost_usd", 0))
    except OSError:
        pass
    return total


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _fmt(value, fmt=".3f", fallback="—"):
    if value is None:
        return fallback
    try:
        return format(float(value), fmt)
    except (TypeError, ValueError):
        return fallback


def _pnl_class(value):
    if value is None:
        return ""
    return "pos" if float(value) >= 0 else "neg"


def _render_dashboard() -> str:
    metrics = _read_metrics()
    trades = _read_trades()
    ai_cost = _read_daily_ai_cost()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    open_trades = [t for t in trades if t.get("outcome") is None and t.get("status") in ("placed", "paper")]
    resolved_trades = sorted(
        [t for t in trades if t.get("outcome") is not None],
        key=lambda t: t.get("resolved_at", ""),
        reverse=True,
    )[:20]

    today = datetime.now(timezone.utc).date().isoformat()
    daily_pnl = sum(
        float(t["pnl"])
        for t in trades
        if t.get("pnl") is not None and (t.get("resolved_at", "") or "")[:10] == today
    )

    computed_at = metrics.get("computed_at", "")
    if computed_at:
        computed_at = computed_at[:16].replace("T", " ") + " UTC"

    msg = metrics.get("message", "")

    # ---- stat cards ----
    win_rate = metrics.get("win_rate")
    sharpe = metrics.get("sharpe")
    drawdown = metrics.get("max_drawdown")
    profit_factor = metrics.get("profit_factor")
    brier = metrics.get("brier_score")
    trade_count = metrics.get("trade_count", 0)

    def card(label, value, suffix="", cls=""):
        return f'<div class="card {cls}"><div class="label">{label}</div><div class="value">{value}{suffix}</div></div>'

    cards = "".join([
        card("Win Rate", _fmt(win_rate, ".1%") if win_rate is not None else "—"),
        card("Sharpe", _fmt(sharpe)),
        card("Max Drawdown", _fmt(drawdown, ".1%") if drawdown is not None else "—"),
        card("Profit Factor", _fmt(profit_factor)),
        card("Brier Score", _fmt(brier)),
        card("Resolved Trades", str(trade_count)),
        card("Open Positions", str(len(open_trades))),
        card("Daily P&L", f"${daily_pnl:+.2f}", cls=_pnl_class(daily_pnl)),
        card("AI Cost Today", f"${ai_cost:.4f}"),
    ])

    # ---- open positions table ----
    def open_row(t):
        placed = (t.get("placed_at") or "")[:16].replace("T", " ")
        raw_edge = t.get("edge", 0) or 0
        display_edge = raw_edge if t.get("direction", "yes").lower() == "yes" else -raw_edge
        edge_str = f"{display_edge:+.1%}"
        market_id = t.get("market_id", "")
        title = t.get("title") or market_id
        display_title = title[:60] + ("…" if len(title) > 60 else "")
        return (
            f"<tr>"
            f"<td title='{market_id}'>{display_title}</td>"
            f"<td>{t.get('platform','')}</td>"
            f"<td>{t.get('direction','').upper()}</td>"
            f"<td>${float(t.get('size_usd',0)):.2f}</td>"
            f"<td>{_fmt(t.get('entry_price'),'.3f')}</td>"
            f"<td>{_fmt(t.get('p_model'),'.3f')}</td>"
            f"<td>{edge_str}</td>"
            f"<td><span class='badge {t.get('status','')}'>{t.get('status','')}</span></td>"
            f"<td>{placed}</td>"
            f"</tr>"
        )

    open_rows = "".join(open_row(t) for t in open_trades) or "<tr><td colspan='9' class='empty'>No open positions</td></tr>"

    # ---- resolved trades table ----
    def resolved_row(t):
        resolved = (t.get("resolved_at") or "")[:16].replace("T", " ")
        pnl = t.get("pnl")
        pnl_str = f"${float(pnl):+.2f}" if pnl is not None else "—"
        market_id = t.get("market_id", "")
        title = t.get("title") or market_id
        display_title = title[:60] + ("…" if len(title) > 60 else "")
        return (
            f"<tr>"
            f"<td title='{market_id}'>{display_title}</td>"
            f"<td>{t.get('platform','')}</td>"
            f"<td>{t.get('direction','').upper()}</td>"
            f"<td>${float(t.get('size_usd',0)):.2f}</td>"
            f"<td>{t.get('outcome','')}</td>"
            f"<td class='{_pnl_class(pnl)}'>{pnl_str}</td>"
            f"<td>{resolved}</td>"
            f"</tr>"
        )

    resolved_rows = "".join(resolved_row(t) for t in resolved_trades) or "<tr><td colspan='7' class='empty'>No resolved trades yet</td></tr>"

    subtitle = f'<p class="subtitle">Last metrics snapshot: {computed_at or "never"}{" · " + msg if msg else ""}</p>' if msg or computed_at else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>predict-market-bot dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0d1117; color: #e6edf3; padding: 24px; }}
  h1 {{ font-size: 1.4rem; font-weight: 600; margin-bottom: 4px; }}
  .subtitle {{ font-size: 0.8rem; color: #8b949e; margin-bottom: 24px; }}
  .timestamp {{ font-size: 0.75rem; color: #8b949e; float: right; margin-top: 2px; }}

  .cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 32px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 16px 20px; min-width: 140px; flex: 1; }}
  .card .label {{ font-size: 0.72rem; color: #8b949e; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 6px; }}
  .card .value {{ font-size: 1.5rem; font-weight: 600; }}
  .pos .value {{ color: #3fb950; }}
  .neg .value {{ color: #f85149; }}

  h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: 12px; color: #c9d1d9; }}
  section {{ margin-bottom: 32px; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  th {{ text-align: left; padding: 8px 12px; background: #161b22;
        color: #8b949e; font-weight: 500; border-bottom: 1px solid #30363d; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; }}
  tr:hover td {{ background: #161b22; }}
  .empty {{ color: #8b949e; text-align: center; padding: 20px; }}
  .pos {{ color: #3fb950; }}
  .neg {{ color: #f85149; }}

  .badge {{ font-size: 0.7rem; padding: 2px 8px; border-radius: 12px;
            font-weight: 500; text-transform: uppercase; }}
  .badge.paper {{ background: #1f3a5f; color: #58a6ff; }}
  .badge.placed {{ background: #1a3a2a; color: #3fb950; }}
  .badge.filled {{ background: #1a3a2a; color: #3fb950; }}
</style>
</head>
<body>
<h1>predict-market-bot <span class="timestamp">Updated {now} · auto-refresh 60s</span></h1>
{subtitle}

<div class="cards">{cards}</div>

<section>
  <h2>Open Positions ({len(open_trades)})</h2>
  <table>
    <thead><tr>
      <th>Title</th><th>Platform</th><th>Dir</th><th>Size</th>
      <th>Entry</th><th>p_model</th><th>Edge</th><th>Status</th><th>Placed</th>
    </tr></thead>
    <tbody>{open_rows}</tbody>
  </table>
</section>

<section>
  <h2>Recent Resolved Trades</h2>
  <table>
    <thead><tr>
      <th>Title</th><th>Platform</th><th>Dir</th><th>Size</th>
      <th>Outcome</th><th>P&amp;L</th><th>Resolved</th>
    </tr></thead>
    <tbody>{resolved_rows}</tbody>
  </table>
</section>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/dashboard"):
            self.send_response(404)
            self.end_headers()
            return
        body = _render_dashboard().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # suppress per-request logs
        pass


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"[dashboard] Serving at http://0.0.0.0:{PORT}/", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
