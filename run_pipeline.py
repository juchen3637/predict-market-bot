"""
run_pipeline.py — Pipeline Orchestrator for predict-market-bot

Runs the full scan → research → predict → risk pipeline as a one-shot cycle.
Designed to be invoked by systemd timer every 15 minutes.

Usage:
    python run_pipeline.py [--dry-run]

Environment:
    PAPER_TRADING=true  (default) — simulate orders, no real API calls
    MAX_DAILY_AI_COST_USD=30      — daily AI cost cap (default from settings.yaml)

State file: data/pipeline_state.json
Logs:       logs/pipeline_YYYY-MM-DD.log
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = _PROJECT_ROOT / "data"
LOGS_DIR = _PROJECT_ROOT / "logs"
STOP_FILE = _PROJECT_ROOT / "STOP"
STATE_FILE = DATA_DIR / "pipeline_state.json"
PERFORMANCE_METRICS_FILE = DATA_DIR / "performance_metrics.json"
AI_COST_LOG = DATA_DIR / "ai_cost_log.jsonl"

MAX_CONSECUTIVE_FAILURES = 3

# ---------------------------------------------------------------------------
# Script paths
# ---------------------------------------------------------------------------

SCAN_SCRIPT = _PROJECT_ROOT / "skills" / "pm-scan" / "scripts" / "filter_markets.py"
RESEARCH_SCRIPT = _PROJECT_ROOT / "skills" / "pm-research" / "scripts" / "research_pipeline.py"
PREDICT_SCRIPT = _PROJECT_ROOT / "skills" / "pm-predict" / "scripts" / "predict_pipeline.py"
RISK_SCRIPT = _PROJECT_ROOT / "skills" / "pm-risk" / "scripts" / "risk_pipeline.py"


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"pipeline_{today}.log"

    logger = logging.getLogger("pipeline")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

        fh = logging.FileHandler(log_file)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"consecutive_failures": 0, "last_run_at": None, "last_success_at": None}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"consecutive_failures": 0, "last_run_at": None, "last_success_at": None}


def _save_state(state: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Pre-flight Checks
# ---------------------------------------------------------------------------

def _check_stop_file(logger: logging.Logger) -> bool:
    """Returns True (abort) if STOP file exists."""
    if STOP_FILE.exists():
        logger.critical("STOP file present — aborting pipeline cycle")
        return True
    return False


def _check_daily_ai_cost(logger: logging.Logger) -> bool:
    """Returns True (abort) if daily AI cost exceeds configured cap."""
    import yaml
    settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
    try:
        with open(settings_path) as f:
            settings = yaml.safe_load(f)
        max_cost = float(
            os.environ.get(
                "MAX_DAILY_AI_COST_USD",
                settings.get("cost_control", {}).get("max_daily_ai_cost_usd", 30),
            )
        )
    except Exception:
        max_cost = 30.0

    if not AI_COST_LOG.exists():
        return False

    today = datetime.now(timezone.utc).date().isoformat()
    total_cost = 0.0
    try:
        with open(AI_COST_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("timestamp", "")[:10] == today:
                        total_cost += float(record.get("cost_usd", 0))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return False

    if total_cost >= max_cost:
        logger.critical(
            "Daily AI cost $%.2f exceeds cap $%.2f — aborting cycle", total_cost, max_cost
        )
        return True
    return False


def _check_drawdown(logger: logging.Logger) -> bool:
    """Returns True (abort) if current drawdown >= 8%."""
    if not PERFORMANCE_METRICS_FILE.exists():
        return False
    try:
        with open(PERFORMANCE_METRICS_FILE) as f:
            metrics = json.load(f)
        drawdown = metrics.get("max_drawdown")
        if drawdown is not None and drawdown >= 0.08:
            logger.critical(
                "Current drawdown %.1f%% >= 8%% threshold — aborting cycle", drawdown * 100
            )
            return True
    except (json.JSONDecodeError, OSError):
        pass
    return False


def _run_preflight(logger: logging.Logger) -> bool:
    """
    Run all pre-flight checks.
    Returns True if pipeline should abort.
    """
    if _check_stop_file(logger):
        return True
    if _check_daily_ai_cost(logger):
        return True
    if _check_drawdown(logger):
        return True
    return False


# ---------------------------------------------------------------------------
# Stage Runner
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _run_stage(
    logger: logging.Logger,
    script: Path,
    extra_args: list[str] | None = None,
    stdin_data: bytes | None = None,
    dry_run: bool = False,
) -> tuple[bytes, int]:
    """
    Run a pipeline stage script as a subprocess.

    Returns:
        (stdout_bytes, returncode)
    """
    cmd = [sys.executable, str(script)] + (extra_args or [])
    logger.info("Running stage: %s", " ".join(str(c) for c in cmd))

    if dry_run:
        logger.info("[dry-run] Skipping actual execution of %s", script.name)
        return b'{"scan_id": "dry_run", "candidates": [], "signals": []}', 0

    result = subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        timeout=600,
    )

    if result.stderr:
        for line in result.stderr.decode(errors="replace").splitlines():
            logger.debug("[%s stderr] %s", script.stem, line)

    return result.stdout, result.returncode


def _validate_json_output(data: bytes, stage_name: str, logger: logging.Logger) -> bool:
    """Returns True if output is valid non-empty JSON."""
    if not data or not data.strip():
        logger.error("Stage %s produced empty output", stage_name)
        return False
    try:
        json.loads(data)
        return True
    except json.JSONDecodeError as e:
        logger.error("Stage %s produced invalid JSON: %s", stage_name, e)
        return False


# ---------------------------------------------------------------------------
# Disk Management
# ---------------------------------------------------------------------------

def _rotate_logs(logger: logging.Logger) -> None:
    """Delete log files older than 30 days."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    if not LOGS_DIR.exists():
        return
    for log_file in LOGS_DIR.glob("pipeline_*.log"):
        try:
            date_str = log_file.stem.replace("pipeline_", "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_date < cutoff:
                log_file.unlink()
                logger.debug("Rotated old log: %s", log_file.name)
        except (ValueError, OSError):
            continue


def _rotate_data_files(logger: logging.Logger) -> None:
    """Delete scan data files older than 7 days."""
    from datetime import timedelta
    cutoff_mtime = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
    if not DATA_DIR.exists():
        return
    patterns = ["candidates_*.json", "enriched_*.json", "signals_*.json"]
    for pattern in patterns:
        for data_file in DATA_DIR.glob(pattern):
            try:
                if data_file.stat().st_mtime < cutoff_mtime:
                    data_file.unlink()
                    logger.debug("Rotated old data file: %s", data_file.name)
            except OSError:
                continue


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(dry_run: bool = False) -> int:
    """
    Run one full pipeline cycle.

    Returns:
        0 on success, 1 on failure.
    """
    logger = _setup_logging()
    now = datetime.now(timezone.utc).isoformat()
    state = _load_state()
    state["last_run_at"] = now

    logger.info("=== Pipeline cycle starting (dry_run=%s) ===", dry_run)

    # Rotate old files
    _rotate_logs(logger)
    _rotate_data_files(logger)

    # Pre-flight checks
    if _run_preflight(logger):
        _save_state(state)
        return 1

    ts = _ts()

    # ---- Stage 1: Scan ----
    scan_out, rc = _run_stage(logger, SCAN_SCRIPT, dry_run=dry_run)
    if rc != 0 or not _validate_json_output(scan_out, "scan", logger):
        logger.error("Scan stage failed (rc=%d)", rc)
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        _handle_consecutive_failures(state, logger)
        _save_state(state)
        return 1

    # Save scan output
    DATA_DIR.mkdir(exist_ok=True)
    scan_file = DATA_DIR / f"candidates_{ts}.json"
    scan_file.write_bytes(scan_out)
    logger.info("Scan complete → %s", scan_file.name)

    # ---- Stage 2: Research ----
    research_out, rc = _run_stage(
        logger,
        RESEARCH_SCRIPT,
        extra_args=["--input", str(scan_file)],
        dry_run=dry_run,
    )
    if rc != 0 or not _validate_json_output(research_out, "research", logger):
        logger.error("Research stage failed (rc=%d)", rc)
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        _handle_consecutive_failures(state, logger)
        _save_state(state)
        return 1

    enriched_file = DATA_DIR / f"enriched_{ts}.json"
    enriched_file.write_bytes(research_out)
    logger.info("Research complete → %s", enriched_file.name)

    # ---- Stage 3: Predict ----
    predict_out, rc = _run_stage(
        logger,
        PREDICT_SCRIPT,
        extra_args=["--input", str(enriched_file)],
        dry_run=dry_run,
    )
    if rc != 0 or not _validate_json_output(predict_out, "predict", logger):
        logger.error("Predict stage failed (rc=%d)", rc)
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        _handle_consecutive_failures(state, logger)
        _save_state(state)
        return 1

    signals_file = DATA_DIR / f"signals_{ts}.json"
    signals_file.write_bytes(predict_out)
    logger.info("Predict complete → %s", signals_file.name)

    # ---- Stage 4: Risk / Execute ----
    _risk_out, rc = _run_stage(
        logger,
        RISK_SCRIPT,
        extra_args=["--file", str(signals_file)],
        dry_run=dry_run,
    )
    if rc != 0:
        logger.error("Risk stage failed (rc=%d)", rc)
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        _handle_consecutive_failures(state, logger)
        _save_state(state)
        return 1

    logger.info("Risk/execute stage complete")

    # Success
    state["consecutive_failures"] = 0
    state["last_success_at"] = now
    _save_state(state)
    logger.info("=== Pipeline cycle complete ===")
    return 0


def _handle_consecutive_failures(state: dict, logger: logging.Logger) -> None:
    """Check consecutive failure count; touch STOP if threshold exceeded."""
    failures = state.get("consecutive_failures", 0)
    if failures >= MAX_CONSECUTIVE_FAILURES:
        logger.critical(
            "%d consecutive failures — creating STOP file to halt trading", failures
        )
        STOP_FILE.touch()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run one full predict-market-bot pipeline cycle")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip actual subprocess calls (preflight checks still run)",
    )
    args = parser.parse_args()

    rc = run_pipeline(dry_run=args.dry_run)
    sys.exit(rc)


if __name__ == "__main__":
    main()
