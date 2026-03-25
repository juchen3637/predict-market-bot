"""
dashboard_server.py — Human-readable trading dashboard

Serves a live HTML dashboard at http://<host>:8002/
Updates via Server-Sent Events (SSE) — no page reloads, data pushed
within 1-2 seconds of any file change.

Endpoints:
  GET /           HTML dashboard (initial render, works without JS)
  GET /events     SSE stream (push JSON updates to connected browsers)
  GET /api/state  JSON snapshot of current dashboard data

Port 8002 (8000 = Serena MCP, 8001 = Prometheus metrics)
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = _PROJECT_ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
METRICS_PATH = DATA_DIR / "performance_metrics.json"
TRADE_LOG_PATH = DATA_DIR / "trade_log.jsonl"
COST_LOG_PATH = DATA_DIR / "ai_cost_log.jsonl"
PIPELINE_STATE_PATH = DATA_DIR / "pipeline_state.json"

PORT = 8002

_WATCHED_FILES = [TRADE_LOG_PATH, METRICS_PATH, PIPELINE_STATE_PATH, COST_LOG_PATH]


# ---------------------------------------------------------------------------
# SSE client registry
# ---------------------------------------------------------------------------

class _SseClients:
    """Thread-safe registry of connected SSE client queues."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: list[queue.Queue] = []

    def add(self, q: queue.Queue) -> None:
        with self._lock:
            self._queues.append(q)

    def remove(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    def broadcast(self, payload: str) -> None:
        with self._lock:
            for q in list(self._queues):
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass


_sse_clients = _SseClients()
_stop_event = threading.Event()


# ---------------------------------------------------------------------------
# File watcher background thread
# ---------------------------------------------------------------------------

def _runs_dir_mtime() -> float:
    """Return the newest mtime among run manifest files, or 0 if none exist."""
    if not RUNS_DIR.exists():
        return 0.0
    newest = 0.0
    for f in RUNS_DIR.glob("run_*.json"):
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            continue
    return newest


def _file_watcher() -> None:
    """Poll watched files every 1s; broadcast SSE update on any mtime change."""
    mtimes: dict[Path, float | None] = {}
    for p in _WATCHED_FILES:
        try:
            mtimes[p] = os.stat(p).st_mtime
        except OSError:
            mtimes[p] = None

    runs_mtime: float = _runs_dir_mtime()
    heartbeat_tick = 0

    while not _stop_event.is_set():
        time.sleep(1)
        heartbeat_tick += 1

        changed = False
        for p in _WATCHED_FILES:
            try:
                mtime = os.stat(p).st_mtime
            except OSError:
                mtime = None
            if mtime != mtimes.get(p):
                mtimes[p] = mtime
                changed = True

        if changed:
            try:
                data = _build_dashboard_data()
                _sse_clients.broadcast(f"data: {json.dumps(data)}\n\n")
            except Exception:
                pass
            heartbeat_tick = 0

        # Check for run manifest changes → emit run_update event
        new_runs_mtime = _runs_dir_mtime()
        if new_runs_mtime != runs_mtime:
            runs_mtime = new_runs_mtime
            try:
                newest_runs = _read_runs(max_runs=1)
                if newest_runs:
                    payload = json.dumps(newest_runs[0])
                    _sse_clients.broadcast(f"event: run_update\ndata: {payload}\n\n")
            except Exception:
                pass

        if not changed and heartbeat_tick >= 15:
            _sse_clients.broadcast(": keepalive\n\n")
            heartbeat_tick = 0


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def _read_metrics() -> dict:
    if not METRICS_PATH.exists():
        return {}
    try:
        with open(METRICS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _read_pipeline_state() -> dict:
    if not PIPELINE_STATE_PATH.exists():
        return {}
    try:
        with open(PIPELINE_STATE_PATH) as f:
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


def _read_runs(max_runs: int = 50) -> list[dict]:
    """Read run manifests from data/runs/, sorted newest-first."""
    if not RUNS_DIR.exists():
        return []
    manifests = []
    candidates = sorted(
        RUNS_DIR.glob("run_*.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )[:max_runs]
    for f in candidates:
        try:
            manifests.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return manifests


def _read_run_manifest(run_id: str) -> dict | None:
    """Read a specific run manifest. Returns None if not found/invalid."""
    # Sanitize: reject traversal attempts
    if not run_id or "/" in run_id or ".." in run_id:
        return None
    manifest_path = RUNS_DIR / f"run_{run_id}.json"
    try:
        return json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


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
# Metrics computation
# ---------------------------------------------------------------------------

def _compute_live_metrics(resolved_trades: list[dict]) -> dict:
    """Compute win_rate, max_drawdown, profit_factor, total_pnl from resolved trades."""
    if not resolved_trades:
        return {"trade_count": 0, "win_rate": None, "max_drawdown": None,
                "profit_factor": None, "total_pnl": 0.0}

    trade_count = len(resolved_trades)
    wins = sum(1 for t in resolved_trades if t.get("outcome") == "win")
    win_rate = wins / trade_count

    gross_profit = sum(float(t["pnl"]) for t in resolved_trades if float(t.get("pnl", 0)) > 0)
    gross_loss = sum(abs(float(t["pnl"])) for t in resolved_trades if float(t.get("pnl", 0)) < 0)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    total_pnl = sum(float(t.get("pnl", 0)) for t in resolved_trades)

    # Rolling peak-to-trough drawdown relative to bankroll
    bankroll = float(os.environ.get("BANKROLL_USD", 100))
    sorted_trades = sorted(
        [t for t in resolved_trades if t.get("resolved_at")],
        key=lambda t: t["resolved_at"],
    )
    portfolio, peak, max_dd = bankroll, bankroll, 0.0
    for t in sorted_trades:
        portfolio += float(t.get("pnl", 0))
        if portfolio > peak:
            peak = portfolio
        if peak > 0:
            max_dd = max(max_dd, (peak - portfolio) / peak)

    return {
        "trade_count": trade_count,
        "win_rate": round(win_rate, 4),
        "max_drawdown": round(max_dd, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "total_pnl": round(total_pnl, 4),
    }


def _daily_pnl_for(trades: list[dict]) -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(
        float(t["pnl"])
        for t in trades
        if t.get("pnl") is not None and (t.get("resolved_at") or "")[:10] == today
    )


# ---------------------------------------------------------------------------
# Dashboard data builder — shared by SSE push and /api/state
# ---------------------------------------------------------------------------

def _build_dashboard_data() -> dict:
    """Return all dashboard state as a JSON-serializable dict."""
    metrics = _read_metrics()
    trades = _read_trades()
    ai_cost = _read_daily_ai_cost()
    pipeline_state = _read_pipeline_state()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    paper_trades = [t for t in trades if t.get("status") in ("paper", "backtest")]
    live_trades = [t for t in trades if t.get("status") in ("placed", "filled")]

    def _mode_data(mode_trades: list[dict]) -> dict:
        open_trades = [t for t in mode_trades if t.get("outcome") is None]
        resolved = [t for t in mode_trades if t.get("outcome") is not None and t.get("pnl") is not None]
        recent_resolved = sorted(resolved, key=lambda t: t.get("resolved_at", ""), reverse=True)[:20]
        return {
            "metrics": _compute_live_metrics(resolved),
            "open_trades": open_trades,
            "resolved_trades": recent_resolved,
            "daily_pnl": round(_daily_pnl_for(mode_trades), 4),
            "open_count": len(open_trades),
        }

    computed_at = metrics.get("computed_at", "")
    if computed_at:
        computed_at = computed_at[:16].replace("T", " ") + " UTC"

    return {
        "updated_at": now,
        "metrics_snapshot_at": computed_at or "never",
        "brier_score": metrics.get("brier_score"),
        "ai_cost_today": round(ai_cost, 4),
        "pipeline_state": pipeline_state,
        "paper": _mode_data(paper_trades),
        "live": _mode_data(live_trades),
    }


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


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _render_stat_cards(m: dict, mode: str, open_count: int,
                       daily_pnl: float, ai_cost: float, brier: float | None) -> str:
    def card(label, stat_key, value, extra_cls=""):
        return (
            f'<div class="card {extra_cls}">'
            f'<div class="label">{label}</div>'
            f'<div class="value" id="{mode}-{stat_key}">{value}</div>'
            f'</div>'
        )

    win_rate = m.get("win_rate")
    drawdown = m.get("max_drawdown")
    profit_factor = m.get("profit_factor")
    trade_count = m.get("trade_count", 0)
    total_pnl = m.get("total_pnl", 0.0)

    return "".join([
        card("Win Rate", "win-rate", _fmt(win_rate, ".1%") if win_rate is not None else "—"),
        card("Max Drawdown", "max-drawdown", _fmt(drawdown, ".1%") if drawdown is not None else "—"),
        card("Profit Factor", "profit-factor", _fmt(profit_factor) if profit_factor is not None else "—"),
        card("Brier Score", "brier", _fmt(brier) if brier is not None else "—"),
        card("Resolved Trades", "trade-count", str(trade_count)),
        card("Open Positions", "open-count", str(open_count)),
        card("Total P&L", "total-pnl", f"${total_pnl:+.2f}", extra_cls=_pnl_class(total_pnl)),
        card("Daily P&L", "daily-pnl", f"${daily_pnl:+.2f}", extra_cls=_pnl_class(daily_pnl)),
        card("AI Cost Today", "ai-cost", f"${ai_cost:.4f}"),
    ])


def _render_open_rows(trades: list[dict]) -> str:
    if not trades:
        return "<tr><td colspan='9' class='empty'>No open positions</td></tr>"
    rows = []
    for t in trades:
        placed = (t.get("placed_at") or "")[:16].replace("T", " ")
        raw_edge = t.get("edge", 0) or 0
        display_edge = raw_edge if t.get("direction", "yes").lower() in ("yes", "long") else -raw_edge
        market_id = t.get("market_id", "")
        title = t.get("title") or market_id
        display_title = title[:60] + ("…" if len(title) > 60 else "")
        rows.append(
            f"<tr>"
            f"<td title='{market_id}'>{display_title}</td>"
            f"<td>{t.get('platform','')}</td>"
            f"<td>{t.get('direction','').upper()}</td>"
            f"<td>${float(t.get('size_usd',0)):.2f}</td>"
            f"<td class='col-hide'>{_fmt(t.get('entry_price'),'.3f')}</td>"
            f"<td class='col-hide'>{_fmt(t.get('p_model'),'.3f')}</td>"
            f"<td>{display_edge:+.1%}</td>"
            f"<td><span class='badge {t.get('status','')}'>{t.get('status','')}</span></td>"
            f"<td>{placed}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _render_resolved_rows(trades: list[dict]) -> str:
    recent = sorted(
        [t for t in trades if t.get("outcome") is not None],
        key=lambda t: t.get("resolved_at", ""),
        reverse=True,
    )[:20]
    if not recent:
        return "<tr><td colspan='7' class='empty'>No resolved trades yet</td></tr>"
    rows = []
    for t in recent:
        resolved = (t.get("resolved_at") or "")[:16].replace("T", " ")
        pnl = t.get("pnl")
        pnl_str = f"${float(pnl):+.2f}" if pnl is not None else "—"
        market_id = t.get("market_id", "")
        title = t.get("title") or market_id
        display_title = title[:60] + ("…" if len(title) > 60 else "")
        rows.append(
            f"<tr>"
            f"<td title='{market_id}'>{display_title}</td>"
            f"<td class='col-hide'>{t.get('platform','')}</td>"
            f"<td class='col-hide'>{t.get('direction','').upper()}</td>"
            f"<td>${float(t.get('size_usd',0)):.2f}</td>"
            f"<td>{t.get('outcome','')}</td>"
            f"<td class='{_pnl_class(pnl)}'>{pnl_str}</td>"
            f"<td>{resolved}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _render_view(view_id: str, mode: str, mode_data: dict,
                 ai_cost: float, brier: float | None) -> str:
    m = mode_data["metrics"]
    open_trades = mode_data["open_trades"]
    resolved_trades = mode_data["resolved_trades"]
    daily_pnl = mode_data["daily_pnl"]
    open_count = mode_data["open_count"]

    cards = _render_stat_cards(m, mode, open_count, daily_pnl, ai_cost, brier)
    open_rows = _render_open_rows(open_trades)
    resolved_rows = _render_resolved_rows(resolved_trades)

    return f"""
<div id="{view_id}" style="display:none">
  <div class="cards">{cards}</div>
  <section>
    <h2>Open Positions (<span id="{mode}-open-heading">{open_count}</span>)</h2>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Title</th><th>Platform</th><th>Dir</th><th>Size</th>
        <th class="col-hide">Entry</th><th class="col-hide">p_model</th><th>Edge</th><th>Status</th><th>Placed</th>
      </tr></thead>
      <tbody id="{mode}-open-tbody">{open_rows}</tbody>
    </table></div>
  </section>
  <section>
    <h2>Recent Resolved Trades</h2>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Title</th><th class="col-hide">Platform</th><th class="col-hide">Dir</th><th>Size</th>
        <th>Outcome</th><th>P&amp;L</th><th>Resolved</th>
      </tr></thead>
      <tbody id="{mode}-resolved-tbody">{resolved_rows}</tbody>
    </table></div>
  </section>
</div>"""


# ---------------------------------------------------------------------------
# CSS and JS (kept in dashboard_assets.py to stay under 800-line limit)
# ---------------------------------------------------------------------------

from dashboard_assets import _CSS, _JS  # noqa: E402


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def _render_dashboard() -> str:
    data = _build_dashboard_data()
    now = data["updated_at"]
    brier = data["brier_score"]
    ai_cost = data["ai_cost_today"]
    computed_at = data["metrics_snapshot_at"]

    paper_view = _render_view("view-paper", "paper", data["paper"], ai_cost, brier)
    live_view = _render_view("view-live", "live", data["live"], ai_cost, brier)

    paper_open = data["paper"]["open_count"]
    live_open = data["live"]["open_count"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>predict-market-bot dashboard</title>
<style>{_CSS}</style>
</head>
<body>
<div class="header">
  <div>
    <h1>predict-market-bot</h1>
    <p class="subtitle">Last metrics snapshot: {computed_at}</p>
  </div>
  <div class="header-right">
    <span id="conn-status" class="conn-reconnecting">◌ Connecting…</span>
    <span id="last-updated" class="timestamp">Updated {now}</span>
  </div>
</div>

<div class="tab-bar">
  <button class="tab" id="tab-paper" onclick="switchTab('paper')">
    Paper <span class="count" id="count-paper">{paper_open}</span>
  </button>
  <button class="tab" id="tab-live" onclick="switchTab('live')">
    Live <span class="count" id="count-live">{live_open}</span>
  </button>
  <button class="tab" id="tab-pipeline" onclick="switchTab('pipeline')">Pipeline</button>
</div>

{paper_view}
{live_view}

<div id="view-pipeline" style="display:none">
  <div class="pipeline-layout">
    <div class="pipeline-sidebar">
      <div class="pipeline-sidebar-section">
        <div class="pipeline-sidebar-label">Live Run</div>
        <div id="pipeline-stages">
          <div class="stage-step" data-stage="scan" onclick="selectStage('scan')">
            <span class="stage-icon pending" id="stage-icon-scan"></span>
            <span class="stage-name">Scan</span>
            <span class="stage-dur" id="stage-dur-scan"></span>
          </div>
          <div class="stage-step" data-stage="research" onclick="selectStage('research')">
            <span class="stage-icon pending" id="stage-icon-research"></span>
            <span class="stage-name">Research</span>
            <span class="stage-dur" id="stage-dur-research"></span>
          </div>
          <div class="stage-step" data-stage="predict" onclick="selectStage('predict')">
            <span class="stage-icon pending" id="stage-icon-predict"></span>
            <span class="stage-name">Predict</span>
            <span class="stage-dur" id="stage-dur-predict"></span>
          </div>
          <div class="stage-step" data-stage="risk" onclick="selectStage('risk')">
            <span class="stage-icon pending" id="stage-icon-risk"></span>
            <span class="stage-name">Risk</span>
            <span class="stage-dur" id="stage-dur-risk"></span>
          </div>
        </div>
      </div>
      <div class="pipeline-sidebar-section">
        <div class="pipeline-sidebar-label">History (7 days)</div>
        <div id="pipeline-run-list" class="run-list-scroll">
          <div class="pipeline-empty" style="padding:16px 0">Loading…</div>
        </div>
      </div>
    </div>
    <div class="pipeline-main">
      <div id="pipeline-no-data" class="pipeline-empty">No pipeline runs yet</div>
      <div id="pipeline-stage-detail" style="display:none"></div>
    </div>
  </div>
</div>

<script>{_JS}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/dashboard"):
            self._serve_dashboard()
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/api/state":
            self._serve_api_state()
        elif self.path == "/api/runs":
            self._serve_api_runs()
        elif self.path.startswith("/api/runs/"):
            run_id = self.path[len("/api/runs/"):]
            self._serve_api_run(run_id)
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_dashboard(self):
        body = _render_dashboard().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_api_state(self):
        body = json.dumps(_build_dashboard_data(), indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_api_runs(self):
        body = json.dumps(_read_runs(), indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_api_run(self, run_id: str):
        manifest = _read_run_manifest(run_id)
        if manifest is None:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(manifest, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        client_queue: queue.Queue = queue.Queue(maxsize=10)
        _sse_clients.add(client_queue)

        # Send initial state immediately
        try:
            data = _build_dashboard_data()
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
        except OSError:
            _sse_clients.remove(client_queue)
            return

        try:
            while not _stop_event.is_set():
                try:
                    payload = client_queue.get(timeout=1.0)
                    if payload.startswith(":"):
                        self.wfile.write(payload.encode())
                    else:
                        self.wfile.write(payload.encode())
                    self.wfile.flush()
                except queue.Empty:
                    continue
                except OSError:
                    break
        finally:
            _sse_clients.remove(client_queue)

    def log_message(self, fmt, *args):  # suppress per-request logs
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    watcher = threading.Thread(target=_file_watcher, daemon=True, name="file-watcher")
    watcher.start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"[dashboard] Serving at http://0.0.0.0:{PORT}/  (SSE live updates enabled)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop_event.set()


if __name__ == "__main__":
    main()
