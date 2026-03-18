"""
config_loader.py — Shared configuration utilities for pm-risk skill

Single source of truth for settings loading and path resolution.
Imported by kelly_size.py, validate_risk.py, and risk_pipeline.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parents[2]  # skills/pm-risk/scripts → project root

TRADE_LOG_PATH = PROJECT_ROOT / "data" / "trade_log.jsonl"
STOP_FILE_PATH = PROJECT_ROOT / "STOP"
DATA_DIR = PROJECT_ROOT / "data"


def load_settings() -> dict[str, Any]:
    settings_path = PROJECT_ROOT / "config" / "settings.yaml"
    with open(settings_path) as f:
        return yaml.safe_load(f)
