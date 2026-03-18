"""
conftest.py — Pytest fixtures and path setup for predict-market-bot tests.

Adds all skill script directories to sys.path so tests can import modules directly.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Register all script directories
_SCRIPT_DIRS = [
    _PROJECT_ROOT / "skills/pm-risk/scripts",
    _PROJECT_ROOT / "skills/pm-compound/scripts",
    _PROJECT_ROOT / "skills/pm-predict/scripts",
    _PROJECT_ROOT / "skills/pm-research/scripts",
    _PROJECT_ROOT / "skills/pm-scan/scripts",
]

for _d in _SCRIPT_DIRS:
    _s = str(_d)
    if _s not in sys.path:
        sys.path.insert(0, _s)
