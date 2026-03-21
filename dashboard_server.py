"""
dashboard_server.py — Human-readable trading dashboard

Serves a live HTML dashboard at http://<host>:8002/
Separate Paper / Live views with a client-side toggle (localStorage persisted).

Port 8002 (8000 = Serena MCP, 8001 = Prometheus metrics)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = _PROJECT_ROOT / "data"
METRICS_PATH = DATA_DIR / "performance_metrics.json"
TRADE_LOG_PATH = DATA_DIR / "trade_log.jsonl"
COST_LOG_PATH = DATA_DIR / "ai_cost_log.jsonl"

PORT = 8002


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def _read_metrics() -> dict:
    """Read performance_metrics.json — used only for brier_score and computed_at."""
    if not METRICS_PATH.exists():
        return {}
    try:
        with open(METRICS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _compute_live_metrics(resolved_trades: list[dict]) -> dict:
    """Compute win_rate, max_drawdown, profit_factor, total_pnl directly from trades.

    Called at render time so stats are always current, not waiting for the nightly snapshot.
    """
    if not resolved_trades:
        return {"trade_count": 0, "win_rate": None, "max_drawdown": None, "profit_factor": None, "total_pnl": 0.0}

    trade_count = len(resolved_trades)
    wins = sum(1 for t in resolved_trades if t.get("outcome") == "win")
    win_rate = wins / trade_count

    gross_profit = sum(float(t["pnl"]) for t in resolved_trades if float(t.get("pnl", 0)) > 0)
    gross_loss = sum(abs(float(t["pnl"])) for t in resolved_trades if float(t.get("pnl", 0)) < 0)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    total_pnl = sum(float(t.get("pnl", 0)) for t in resolved_trades)

    # Rolling peak-to-trough drawdown
    sorted_trades = sorted(
        [t for t in resolved_trades if t.get("resolved_at")],
        key=lambda t: t["resolved_at"],
    )
    cumulative, peak, max_dd = 0.0, 0.0, 0.0
    for t in sorted_trades:
        cumulative += float(t.get("pnl", 0))
        if cumulative > peak:
            peak = cumulative
        if peak > 0:
            max_dd = max(max_dd, (peak - cumulative) / peak)

    return {
        "trade_count": trade_count,
        "win_rate": round(win_rate, 4),
        "max_drawdown": round(max_dd, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "total_pnl": round(total_pnl, 4),
    }


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
# Helpers
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


def _mode_metrics(metrics: dict, mode: str) -> dict:
    """Extract paper or live sub-dict; falls back to flat schema for backward compat."""
    if mode in metrics:
        return metrics[mode]
    return metrics


def _daily_pnl_for(trades: list[dict]) -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(
        float(t["pnl"])
        for t in trades
        if t.get("pnl") is not None and (t.get("resolved_at") or "")[:10] == today
    )


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------

def _render_stat_cards(m: dict, open_count: int, daily_pnl: float, ai_cost: float, brier: float | None) -> str:
    def card(label, value, cls=""):
        return f'<div class="card {cls}"><div class="label">{label}</div><div class="value">{value}</div></div>'

    win_rate = m.get("win_rate")
    drawdown = m.get("max_drawdown")
    profit_factor = m.get("profit_factor")
    trade_count = m.get("trade_count", 0)
    total_pnl = m.get("total_pnl", 0.0)

    return "".join([
        card("Win Rate", _fmt(win_rate, ".1%") if win_rate is not None else "—"),
        card("Max Drawdown", _fmt(drawdown, ".1%") if drawdown is not None else "—"),
        card("Profit Factor", _fmt(profit_factor) if profit_factor is not None else "—"),
        card("Brier Score", _fmt(brier) if brier is not None else "—"),
        card("Resolved Trades", str(trade_count)),
        card("Open Positions", str(open_count)),
        card("Total P&L", f"${total_pnl:+.2f}", cls=_pnl_class(total_pnl)),
        card("Daily P&L", f"${daily_pnl:+.2f}", cls=_pnl_class(daily_pnl)),
        card("AI Cost Today", f"${ai_cost:.4f}"),
    ])


def _render_open_table(trades: list[dict]) -> str:
    def row(t):
        placed = (t.get("placed_at") or "")[:16].replace("T", " ")
        raw_edge = t.get("edge", 0) or 0
        display_edge = raw_edge if t.get("direction", "yes").lower() == "yes" else -raw_edge
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
            f"<td>{display_edge:+.1%}</td>"
            f"<td><span class='badge {t.get('status','')}'>{t.get('status','')}</span></td>"
            f"<td>{placed}</td>"
            f"</tr>"
        )

    rows = "".join(row(t) for t in trades) or "<tr><td colspan='9' class='empty'>No open positions</td></tr>"
    return f"""
  <table>
    <thead><tr>
      <th>Title</th><th>Platform</th><th>Dir</th><th>Size</th>
      <th>Entry</th><th>p_model</th><th>Edge</th><th>Status</th><th>Placed</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>"""


def _render_resolved_table(trades: list[dict]) -> str:
    recent = sorted(
        [t for t in trades if t.get("outcome") is not None],
        key=lambda t: t.get("resolved_at", ""),
        reverse=True,
    )[:20]

    def row(t):
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

    rows = "".join(row(t) for t in recent) or "<tr><td colspan='7' class='empty'>No resolved trades yet</td></tr>"
    return f"""
  <table>
    <thead><tr>
      <th>Title</th><th>Platform</th><th>Dir</th><th>Size</th>
      <th>Outcome</th><th>P&amp;L</th><th>Resolved</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>"""


def _render_view(view_id: str, all_trades: list[dict], open_trades: list[dict], daily_pnl: float, ai_cost: float, brier: float | None) -> str:
    resolved = [t for t in all_trades if t.get("outcome") is not None and t.get("pnl") is not None]
    m = _compute_live_metrics(resolved)
    cards = _render_stat_cards(m, len(open_trades), daily_pnl, ai_cost, brier)
    open_table = _render_open_table(open_trades)
    resolved_table = _render_resolved_table(all_trades)
    return f"""
<div id="{view_id}" style="display:none">
  <div class="cards">{cards}</div>
  <section>
    <h2>Open Positions ({len(open_trades)})</h2>
    {open_table}
  </section>
  <section>
    <h2>Recent Resolved Trades</h2>
    {resolved_table}
  </section>
</div>"""


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def _render_dashboard() -> str:
    metrics = _read_metrics()
    trades = _read_trades()
    ai_cost = _read_daily_ai_cost()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    brier = metrics.get("brier_score")

    paper_trades = [t for t in trades if t.get("status") == "paper"]
    live_trades = [t for t in trades if t.get("status") in ("placed", "filled")]

    paper_open = [t for t in paper_trades if t.get("outcome") is None]
    live_open = [t for t in live_trades if t.get("outcome") is None]

    paper_pnl = _daily_pnl_for(paper_trades)
    live_pnl = _daily_pnl_for(live_trades)

    computed_at = metrics.get("computed_at", "")
    if computed_at:
        computed_at = computed_at[:16].replace("T", " ") + " UTC"
    subtitle = f'<p class="subtitle">Last metrics snapshot: {computed_at or "never"}</p>'

    paper_view = _render_view("view-paper", paper_trades, paper_open, paper_pnl, ai_cost, brier)
    live_view = _render_view("view-live", live_trades, live_open, live_pnl, ai_cost, brier)

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
  .subtitle {{ font-size: 0.8rem; color: #8b949e; margin-bottom: 16px; }}
  .timestamp {{ font-size: 0.75rem; color: #8b949e; float: right; margin-top: 2px; }}

  .tab-bar {{ display: flex; gap: 8px; margin-bottom: 24px; border-bottom: 1px solid #30363d; padding-bottom: 0; }}
  .tab {{ background: none; border: none; color: #8b949e; font-size: 0.9rem; font-weight: 500;
          padding: 8px 16px; cursor: pointer; border-bottom: 2px solid transparent;
          margin-bottom: -1px; transition: color 0.15s; }}
  .tab:hover {{ color: #e6edf3; }}
  .tab.active {{ color: #58a6ff; border-bottom-color: #58a6ff; }}
  .tab .count {{ font-size: 0.72rem; background: #21262d; border-radius: 10px;
                 padding: 1px 6px; margin-left: 6px; }}

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

<div class="tab-bar">
  <button class="tab" id="tab-paper" onclick="switchTab('paper')">
    Paper <span class="count">{len(paper_open)}</span>
  </button>
  <button class="tab" id="tab-live" onclick="switchTab('live')">
    Live <span class="count">{len(live_open)}</span>
  </button>
</div>

{paper_view}
{live_view}

<script>
function switchTab(mode) {{
  document.getElementById('view-paper').style.display = mode === 'paper' ? 'block' : 'none';
  document.getElementById('view-live').style.display  = mode === 'live'  ? 'block' : 'none';
  document.getElementById('tab-paper').className = 'tab' + (mode === 'paper' ? ' active' : '');
  document.getElementById('tab-live').className  = 'tab' + (mode === 'live'  ? ' active' : '');
  try {{ localStorage.setItem('dashboard_tab', mode); }} catch(e) {{}}
}}
(function() {{
  var saved = 'paper';
  try {{ saved = localStorage.getItem('dashboard_tab') || 'paper'; }} catch(e) {{}}
  switchTab(saved);
}})();
</script>
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
    server = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"[dashboard] Serving at http://0.0.0.0:{PORT}/", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
