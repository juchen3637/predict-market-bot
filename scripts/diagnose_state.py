"""
diagnose_state.py — Read-only health report for the predict-market-bot.

Prints a one-page summary of pipeline state so a human (or a future Claude
session) can understand at a glance whether the bot is running, what it has
been doing, and why trades are being rejected.

Sections:
  1. STOP file presence + how long since it was touched
  2. pipeline_state.json: consecutive_failures, last_run_at, last_success_at
  3. Last 5 run manifests: per-stage status, trades_placed, error strings
  4. trade_log.jsonl: totals by status / outcome / rejection_reason
  5. performance_metrics.json: live + paper trade_count, win_rate, drawdown,
     sharpe, brier_score
  6. docs/daily_summaries/ most recent date + gap warning
  7. xgboost_train_state.json last retrain timestamp + trade count

Usage:
    python scripts/diagnose_state.py
    python scripts/diagnose_state.py --root /path/to/bot
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# File readers (defensive: always return a sane default on missing/corrupt)
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _fmt_duration(seconds: float) -> str:
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


# ---------------------------------------------------------------------------
# Report builders (each returns a list[str] of lines)
# ---------------------------------------------------------------------------

def report_stop_file(root: Path, now: datetime) -> list[str]:
    path = root / "STOP"
    if not path.exists():
        return ["STOP file: absent (pipeline is NOT halted)"]
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        age = (now - mtime).total_seconds()
        return [
            "STOP file: PRESENT — pipeline is halted",
            f"  touched at: {mtime.isoformat()} ({_fmt_duration(age)} ago)",
        ]
    except OSError:
        return ["STOP file: PRESENT (could not read mtime)"]


def report_pipeline_state(root: Path, now: datetime) -> list[str]:
    state = _read_json(root / "data" / "pipeline_state.json")
    if not state:
        return ["pipeline_state.json: missing or unreadable"]

    lines = ["pipeline_state.json:"]
    lines.append(f"  consecutive_failures: {state.get('consecutive_failures', '?')}")

    for field in ("last_run_at", "last_success_at"):
        val = state.get(field)
        parsed = _parse_iso(val)
        if parsed:
            age = (now - parsed).total_seconds()
            lines.append(f"  {field}: {val}  ({_fmt_duration(age)} ago)")
        else:
            lines.append(f"  {field}: {val or 'never'}")

    # Loud warning if success is stale
    last_success = _parse_iso(state.get("last_success_at"))
    if last_success:
        gap = (now - last_success).total_seconds()
        if gap > 3600:
            lines.append(f"  WARNING: no successful run in {_fmt_duration(gap)}")
    return lines


def report_recent_runs(root: Path, limit: int = 5) -> list[str]:
    runs_dir = root / "data" / "runs"
    if not runs_dir.exists():
        return ["data/runs/: missing"]

    files = sorted(runs_dir.glob("run_*.json"), key=lambda p: p.name, reverse=True)
    if not files:
        return ["data/runs/: no manifests found"]

    lines = [f"Last {min(limit, len(files))} run manifests:"]
    for f in files[:limit]:
        manifest = _read_json(f)
        if not manifest:
            lines.append(f"  {f.name}: unreadable")
            continue
        status = manifest.get("status", "?")
        trades = manifest.get("trades_placed", 0)
        stages = manifest.get("stages", {})
        stage_summary = " ".join(
            f"{name}={stages.get(name, {}).get('status', '?')}"
            for name in ("scan", "research", "predict", "risk")
        )
        probe = stages.get("scan", {}).get("liquidity_probe") if isinstance(stages.get("scan"), dict) else None
        probe_clause = ""
        if probe and probe.get("probed", 0) > 0:
            probe_clause = f" (probe: {probe.get('kept', 0)}/{probe.get('probed', 0)} kept)"
        lines.append(
            f"  {manifest.get('run_id', f.stem)}: "
            f"status={status}  trades={trades}  [{stage_summary}]{probe_clause}"
        )
        # Surface any stage errors
        for name, info in stages.items():
            err = info.get("error") if isinstance(info, dict) else None
            if err:
                err_short = err.replace("\n", " ")
                if len(err_short) > 140:
                    err_short = err_short[:137] + "..."
                lines.append(f"    {name} error: {err_short}")
    return lines


def report_trade_log(root: Path) -> list[str]:
    trades = _read_jsonl(root / "data" / "trade_log.jsonl")
    if not trades:
        return ["trade_log.jsonl: empty or missing"]

    total = len(trades)
    status_counts = Counter(t.get("status", "unknown") for t in trades)
    outcome_counts = Counter(t.get("outcome") or "unresolved" for t in trades)
    reject_counts = Counter(
        t.get("rejection_reason") or "(none)"
        for t in trades
        if t.get("status") == "rejected"
    )

    lines = [f"trade_log.jsonl: {total} entries"]
    lines.append("  by status:")
    for s, c in sorted(status_counts.items(), key=lambda kv: -kv[1]):
        pct = 100.0 * c / total
        lines.append(f"    {s}: {c} ({pct:.1f}%)")
    lines.append("  by outcome:")
    for o, c in sorted(outcome_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {o}: {c}")
    if reject_counts:
        lines.append("  top rejection reasons:")
        for r, c in reject_counts.most_common(10):
            lines.append(f"    {r}: {c}")
    return lines


def report_metrics(root: Path) -> list[str]:
    m = _read_json(root / "data" / "performance_metrics.json")
    if not m:
        return ["performance_metrics.json: missing or unreadable"]

    lines = [f"performance_metrics.json (computed_at: {m.get('computed_at', '?')})"]
    lines.append(f"  brier_score: {m.get('brier_score', '?')}")
    for mode in ("live", "paper"):
        section = m.get(mode)
        if not isinstance(section, dict):
            lines.append(f"  {mode}: not present")
            continue
        lines.append(
            f"  {mode}: "
            f"trades={section.get('trade_count', '?')}  "
            f"win_rate={section.get('win_rate', '?')}  "
            f"sharpe={section.get('sharpe', '?')}  "
            f"max_drawdown={section.get('max_drawdown', '?')}  "
            f"profit_factor={section.get('profit_factor', '?')}"
        )
    return lines


def report_daily_summaries(root: Path, now: datetime) -> list[str]:
    d = root / "docs" / "daily_summaries"
    if not d.exists():
        return ["docs/daily_summaries/: missing"]
    files = sorted(d.glob("*.md"))
    if not files:
        return ["docs/daily_summaries/: empty"]
    latest = files[-1].stem  # YYYY-MM-DD
    lines = [f"docs/daily_summaries/: {len(files)} files, most recent: {latest}"]
    try:
        latest_dt = datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        gap_days = (now.date() - latest_dt.date()).days
        if gap_days > 1:
            lines.append(f"  WARNING: latest summary is {gap_days} days old")
    except ValueError:
        pass
    return lines


def report_xgboost(root: Path, now: datetime) -> list[str]:
    state = _read_json(root / "data" / "xgboost_train_state.json")
    if not state:
        return ["xgboost_train_state.json: missing (model may never have trained)"]
    last_at = state.get("last_trained_at")
    parsed = _parse_iso(last_at)
    line = (
        f"xgboost: last_trained_at={last_at}  "
        f"last_train_trade_count={state.get('last_train_trade_count', '?')}"
    )
    if parsed:
        line += f"  ({_fmt_duration((now - parsed).total_seconds())} ago)"
    return [line]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_report(root: Path, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    sections: list[list[str]] = [
        report_stop_file(root, now),
        report_pipeline_state(root, now),
        report_recent_runs(root),
        report_trade_log(root),
        report_metrics(root),
        report_daily_summaries(root, now),
        report_xgboost(root, now),
    ]
    header = [
        "=" * 72,
        f"predict-market-bot diagnostic report  ({now.isoformat()})",
        f"root: {root}",
        "=" * 72,
    ]
    out_lines = header[:]
    for section in sections:
        out_lines.append("")
        out_lines.extend(section)
    out_lines.append("")
    return "\n".join(out_lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Project root (defaults to the repo containing this script)",
    )
    args = parser.parse_args()
    print(build_report(Path(args.root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
