"""
dashboard_server.py — Human-readable trading dashboard

Serves a live HTML dashboard at http://<host>:8002/
Updates via Server-Sent Events (SSE) — no page reloads, data pushed
within 1-2 seconds of any file change.

Endpoints:
  GET /               HTML dashboard (sidebar + 5 views)
  GET /events         SSE stream (push JSON updates to connected browsers)
  GET /api/state      JSON snapshot of current dashboard data
  GET /api/runs       List of run manifests (newest-first)
  GET /api/runs/<id>  Single run manifest
  GET /api/candidates Latest candidates_{scan_id}.json
  GET /api/enriched   Latest enriched_{scan_id}.json
  GET /api/signals    Latest signals_{scan_id}.json

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
BANKROLL_USD = float(os.environ.get("BANKROLL_USD", 100))

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
# File watcher
# ---------------------------------------------------------------------------

def _runs_dir_mtime() -> float:
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
    if not RUNS_DIR.exists():
        return []
    candidates = sorted(
        RUNS_DIR.glob("run_*.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )[:max_runs]
    manifests = []
    for f in candidates:
        try:
            manifests.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return manifests


def _read_run_manifest(run_id: str) -> dict | None:
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


def _read_latest_ephemeral(prefix: str) -> dict:
    """Read the most recent data/{prefix}_*.json file."""
    files = sorted(
        DATA_DIR.glob(f"{prefix}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return {}
    try:
        with open(files[0]) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Computed helpers for Post Mortem
# ---------------------------------------------------------------------------

def _compute_equity_curve(resolved_trades: list[dict]) -> list[dict]:
    valid = sorted(
        [t for t in resolved_trades if t.get("resolved_at") and t.get("pnl") is not None],
        key=lambda t: t["resolved_at"],
    )
    equity = BANKROLL_USD
    result = []
    for t in valid:
        equity += float(t["pnl"])
        result.append({"date": t["resolved_at"][:10], "equity": round(equity, 2)})
    return result


def _compute_category_stats(resolved_trades: list[dict]) -> list[dict]:
    cat_map: dict[str, str] = {}
    for prefix in ("signals", "enriched", "candidates"):
        data = _read_latest_ephemeral(prefix)
        items = data.get("signals") or data.get("candidates") or []
        for item in items:
            mid = item.get("market_id", "")
            cat = item.get("category")
            if mid and cat:
                cat_map[mid] = cat

    stats: dict[str, dict] = {}
    for t in resolved_trades:
        if t.get("outcome") is None:
            continue
        cat = cat_map.get(t.get("market_id", ""), "Other")
        if cat not in stats:
            stats[cat] = {"wins": 0, "total": 0}
        stats[cat]["total"] += 1
        if t.get("outcome") == "win":
            stats[cat]["wins"] += 1

    return [
        {"category": cat, "wins": v["wins"], "total": v["total"]}
        for cat, v in sorted(stats.items(), key=lambda x: x[1]["total"], reverse=True)
    ]


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def _compute_live_metrics(resolved_trades: list[dict]) -> dict:
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

    sorted_trades = sorted(
        [t for t in resolved_trades if t.get("resolved_at")],
        key=lambda t: t["resolved_at"],
    )
    portfolio, peak, max_dd = BANKROLL_USD, BANKROLL_USD, 0.0
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
# Dashboard data builder
# ---------------------------------------------------------------------------

def _build_dashboard_data() -> dict:
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

    all_resolved = [t for t in trades if t.get("outcome") is not None and t.get("pnl") is not None]

    return {
        "updated_at": now,
        "metrics_snapshot_at": computed_at or "never",
        "brier_score": metrics.get("brier_score"),
        "ai_cost_today": round(ai_cost, 4),
        "pipeline_state": pipeline_state,
        "paper": _mode_data(paper_trades),
        "live": _mode_data(live_trades),
        "equity_curve": _compute_equity_curve(all_resolved),
        "category_stats": _compute_category_stats(all_resolved),
    }


# ---------------------------------------------------------------------------
# HTML rendering (template in dashboard_html.py)
# ---------------------------------------------------------------------------

from dashboard_html import render_dashboard as _render_html  # noqa: E402


def _render_dashboard() -> str:
    return _render_html(_build_dashboard_data())


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
            self._serve_json(_build_dashboard_data())
        elif self.path == "/api/runs":
            self._serve_json(_read_runs())
        elif self.path.startswith("/api/runs/"):
            run_id = self.path[len("/api/runs/"):]
            manifest = _read_run_manifest(run_id)
            if manifest is None:
                self.send_response(404)
                self.end_headers()
            else:
                self._serve_json(manifest)
        elif self.path == "/api/candidates":
            self._serve_json(_read_latest_ephemeral("candidates"))
        elif self.path == "/api/enriched":
            self._serve_json(_read_latest_ephemeral("enriched"))
        elif self.path == "/api/signals":
            self._serve_json(_read_latest_ephemeral("signals"))
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

    def _serve_json(self, data: dict | list):
        body = json.dumps(data, indent=2).encode()
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
