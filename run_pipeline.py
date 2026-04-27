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
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent

# Load .env so all subprocess children inherit credentials.
# override=True ensures .env always wins over systemd EnvironmentFile=,
# which can mangle multi-line values like RSA private keys.
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_PROJECT_ROOT / ".env", override=True)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = _PROJECT_ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
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
        # Support both new nested schema { live: {...} } and legacy flat schema
        live = metrics.get("live", metrics)
        drawdown = live.get("max_drawdown")
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


# Sentinel exit code used when a stage subprocess is killed for exceeding its
# timeout. 124 matches GNU coreutils `timeout(1)` convention.
STAGE_TIMEOUT_RC = 124

# stderr markers that indicate an environmental / transient failure rather
# than a real code bug. Matching any of these keeps consecutive_failures flat.
_TRANSIENT_STDERR_MARKERS = (
    "name resolution",
    "temporary failure in name resolution",
    "connection refused",
    "connection reset",
    "connection aborted",
    "timed out",
    "stage timed out",
    "read timeout",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
)


def _is_transient_failure(rc: int, stderr_text: str) -> bool:
    """Return True when a stage failure looks environmental, not a real bug."""
    if rc == STAGE_TIMEOUT_RC:
        return True
    lower = (stderr_text or "").lower()
    return any(marker in lower for marker in _TRANSIENT_STDERR_MARKERS)


def _run_stage(
    logger: logging.Logger,
    script: Path,
    extra_args: list[str] | None = None,
    stdin_data: bytes | None = None,
    dry_run: bool = False,
    timeout: int = 600,
) -> tuple[bytes, int, str]:
    """
    Run a pipeline stage script as a subprocess.

    Returns:
        (stdout_bytes, returncode, stderr_text)

    On subprocess.TimeoutExpired the child is killed, returncode is set to
    STAGE_TIMEOUT_RC (124), and a synthetic "stage timed out" line is
    prepended to stderr_text so downstream classifiers can detect it.
    """
    cmd = [sys.executable, str(script)] + (extra_args or [])
    logger.info("Running stage: %s", " ".join(str(c) for c in cmd))

    if dry_run:
        logger.info("[dry-run] Skipping actual execution of %s", script.name)
        return b'{"scan_id": "dry_run", "candidates": [], "signals": []}', 0, ""

    try:
        result = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error(
            "Stage %s timed out after %ds — killing subprocess",
            script.stem, timeout,
        )
        # subprocess.run() already tries to kill on TimeoutExpired, but capture
        # whatever partial output the child produced.
        partial_stdout = exc.stdout or b""
        partial_stderr_bytes = exc.stderr or b""
        partial_stderr = partial_stderr_bytes.decode(errors="replace")
        synth = f"[run_pipeline] stage timed out after {timeout}s\n"
        combined_stderr = synth + partial_stderr
        for line in combined_stderr.splitlines():
            logger.debug("[%s stderr] %s", script.stem, line)
        return partial_stdout, STAGE_TIMEOUT_RC, combined_stderr

    stderr_text = result.stderr.decode(errors="replace") if result.stderr else ""
    for line in stderr_text.splitlines():
        logger.debug("[%s stderr] %s", script.stem, line)

    return result.stdout, result.returncode, stderr_text


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


def _rotate_research_cache(logger: logging.Logger, settings: dict) -> None:
    """Delete research cache files older than 2 × cache_ttl_hours."""
    from datetime import timedelta
    ttl_hours = settings.get("research", {}).get("cache_ttl_hours", 4)
    cache_max_age = timedelta(hours=ttl_hours * 2)
    cache_dir = DATA_DIR / "research_cache"
    if not cache_dir.exists():
        return
    for cache_file in cache_dir.glob("*.json"):
        try:
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                cache_file.stat().st_mtime, tz=timezone.utc
            )
            if age > cache_max_age:
                cache_file.unlink(missing_ok=True)
                logger.debug("Rotated stale cache entry: %s", cache_file.name)
        except OSError:
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
# Run Manifest Helpers
# ---------------------------------------------------------------------------

def _rotate_run_manifests(logger: logging.Logger) -> None:
    """Delete run manifest files older than 7 days."""
    from datetime import timedelta
    cutoff_mtime = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
    if not RUNS_DIR.exists():
        return
    for manifest in RUNS_DIR.glob("run_*.json"):
        try:
            if manifest.stat().st_mtime < cutoff_mtime:
                manifest.unlink()
                logger.debug("Rotated old run manifest: %s", manifest.name)
        except OSError:
            continue


def _write_run_manifest(manifest: dict) -> None:
    """Write run manifest atomically to data/runs/run_{run_id}.json. Never raises."""
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = RUNS_DIR / f"run_{manifest['run_id']}.json"
        fd, tmp = tempfile.mkstemp(dir=RUNS_DIR, prefix=".run_tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(manifest, f, indent=2)
            os.replace(tmp, manifest_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        pass  # Non-fatal — dashboard manifest is best-effort


def _update_stage(manifest: dict, stage: str, **updates) -> dict:
    """Return a new manifest dict with the given stage fields updated. Never mutates."""
    return {
        **manifest,
        "stages": {
            **manifest["stages"],
            stage: {**manifest["stages"][stage], **updates},
        },
    }


def _extract_scan_counts(data: bytes) -> dict:
    try:
        d = json.loads(data)
        return {"candidates": len(d.get("candidates", []))}
    except Exception:
        return {"candidates": 0}


def _extract_research_counts(data: bytes) -> dict:
    try:
        d = json.loads(data)
        candidates = d.get("candidates", [])
        return {
            "candidates": len(candidates),
            "cache_hits": sum(1 for c in candidates if c.get("cache_hit")),
            "fresh_fetches": sum(
                1 for c in candidates
                if not c.get("cache_hit") and not c.get("research_skipped")
            ),
            "low_confidence": sum(1 for c in candidates if c.get("low_confidence")),
            "skipped": sum(1 for c in candidates if c.get("research_skipped")),
        }
    except Exception:
        return {}


def _extract_predict_counts(data: bytes) -> dict:
    try:
        d = json.loads(data)
        signals = d.get("signals", [])
        signaled = [s for s in signals if not s.get("predict_skipped")]
        edges = [abs(float(s["edge"])) for s in signaled if s.get("edge") is not None]
        return {
            "signaled": len(signaled),
            "skipped": len(signals) - len(signaled),
            "cache_hits": sum(1 for s in signals if s.get("cache_hit")),
            "avg_edge": round(sum(edges) / len(edges), 4) if edges else 0.0,
        }
    except Exception:
        return {}


def _extract_risk_counts(data: bytes) -> dict:
    try:
        d = json.loads(data)
        orders = d.get("orders", [])
        return {
            "approved": sum(1 for o in orders if o.get("risk_approved")),
            "blocked": sum(1 for o in orders if o.get("order_skipped")),
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Pipeline Setup Helpers
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    """Load settings.yaml; returns empty dict on any error."""
    import yaml
    settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
    try:
        with open(settings_path) as _sf:
            return yaml.safe_load(_sf) or {}
    except Exception:
        return {}


def _init_manifest(run_id: str, started_at: str) -> dict:
    """Return a fresh run manifest dict with all stages in 'pending' state."""
    return {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": None,
        "status": "running",
        "stages": {
            "scan": {"status": "pending"},
            "research": {"status": "pending"},
            "predict": {"status": "pending"},
            "risk": {"status": "pending"},
        },
        "trades_placed": 0,
    }


# ---------------------------------------------------------------------------
# Stage Executor (shared by all four stages)
# ---------------------------------------------------------------------------

def _execute_stage(
    logger: logging.Logger,
    manifest: dict,
    stage_name: str,
    script: Path,
    extra_args: list[str] | None = None,
    dry_run: bool = False,
    timeout: int = 600,
    extract_counts=None,
    validate_json: bool = True,
) -> tuple[bytes, bool, dict]:
    """
    Run one pipeline stage subprocess, updating the run manifest before and after.

    Returns:
        (stdout_bytes, success, updated_manifest)

    On failure the manifest stage is marked 'failed' and top-level status is
    set to 'failed'; the caller is responsible for state persistence and return.
    """
    manifest = _update_stage(manifest, stage_name,
                             status="running",
                             started_at=datetime.now(timezone.utc).isoformat())
    _write_run_manifest(manifest)
    stage_start = time.monotonic()

    output, rc, stderr_text = _run_stage(
        logger, script, extra_args=extra_args,
        dry_run=dry_run, timeout=timeout,
    )

    valid = (not validate_json) or _validate_json_output(output, stage_name, logger)
    if rc != 0 or not valid:
        transient = _is_transient_failure(rc, stderr_text)
        error = f"rc={rc}" if rc != 0 else "invalid JSON output"
        if transient:
            error += " (transient)"
        logger.error(
            "Stage %s failed (rc=%d, valid=%s, transient=%s)",
            stage_name, rc, valid, transient,
        )
        manifest = _update_stage(
            manifest, stage_name,
            status="failed",
            error=error,
            transient=transient,
        )
        manifest = {**manifest, "status": "failed",
                    "completed_at": datetime.now(timezone.utc).isoformat()}
        _write_run_manifest(manifest)
        return output, False, manifest

    counts = extract_counts(output) if extract_counts else {}
    manifest = _update_stage(manifest, stage_name,
                             status="completed",
                             completed_at=datetime.now(timezone.utc).isoformat(),
                             duration_s=round(time.monotonic() - stage_start, 1),
                             **counts)
    _write_run_manifest(manifest)
    return output, True, manifest


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

    settings = _load_settings()
    stage_timeouts = settings.get("pipeline", {}).get("stage_timeouts", {})

    _rotate_logs(logger)
    _rotate_data_files(logger)
    _rotate_research_cache(logger, settings)
    _rotate_run_manifests(logger)

    if _run_preflight(logger):
        _save_state(state)
        return 1

    ts = _ts()
    manifest: dict = _init_manifest(ts, now)
    _write_run_manifest(manifest)

    def _fail(updated_manifest: dict) -> int:
        # Transient failures (DNS blips, 5xx, stage timeouts) should not count
        # toward the 3-strike STOP — a 10-minute network outage should not
        # halt the bot for days.
        transient = any(
            stage.get("transient")
            for stage in updated_manifest.get("stages", {}).values()
            if isinstance(stage, dict)
        )
        if transient:
            logger.warning(
                "Stage failure classified as transient — "
                "consecutive_failures unchanged (current=%d)",
                state.get("consecutive_failures", 0),
            )
        else:
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            _handle_consecutive_failures(state, logger)
        _save_state(state)
        return 1  # noqa: not used as return value — caller returns this

    # ---- Stage 1: Scan ----
    DATA_DIR.mkdir(exist_ok=True)
    scan_out, ok, manifest = _execute_stage(
        logger, manifest, "scan", SCAN_SCRIPT,
        dry_run=dry_run, timeout=stage_timeouts.get("scan", 120),
        extract_counts=_extract_scan_counts,
    )
    if not ok:
        return _fail(manifest)
    scan_file = DATA_DIR / f"candidates_{ts}.json"
    scan_file.write_bytes(scan_out)
    logger.info("Scan complete → %s", scan_file.name)

    # ---- Stage 2: Research ----
    research_out, ok, manifest = _execute_stage(
        logger, manifest, "research", RESEARCH_SCRIPT,
        extra_args=["--input", str(scan_file)],
        dry_run=dry_run, timeout=stage_timeouts.get("research", 600),
        extract_counts=_extract_research_counts,
    )
    if not ok:
        return _fail(manifest)
    enriched_file = DATA_DIR / f"enriched_{ts}.json"
    enriched_file.write_bytes(research_out)
    logger.info("Research complete → %s", enriched_file.name)

    # ---- Stage 3: Predict ----
    predict_out, ok, manifest = _execute_stage(
        logger, manifest, "predict", PREDICT_SCRIPT,
        extra_args=["--input", str(enriched_file)],
        dry_run=dry_run, timeout=stage_timeouts.get("predict", 1200),
        extract_counts=_extract_predict_counts,
    )
    if not ok:
        return _fail(manifest)
    signals_file = DATA_DIR / f"signals_{ts}.json"
    signals_file.write_bytes(predict_out)
    logger.info("Predict complete → %s", signals_file.name)

    # ---- Stage 4: Risk / Execute ----
    risk_out, ok, manifest = _execute_stage(
        logger, manifest, "risk", RISK_SCRIPT,
        extra_args=["--file", str(signals_file)],
        dry_run=dry_run, timeout=stage_timeouts.get("risk", 300),
        extract_counts=_extract_risk_counts,
        validate_json=False,  # Risk stage may not output valid JSON on partial fills
    )
    if not ok:
        return _fail(manifest)

    risk_counts = manifest["stages"]["risk"]
    manifest = {**manifest,
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "trades_placed": risk_counts.get("approved", 0)}
    _write_run_manifest(manifest)
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
