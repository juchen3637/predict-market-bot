"""
create_incident.py — Incident Report Creator

Creates a new incident file in docs/incidents/ from the template.

Usage:
    python scripts/create_incident.py --title "..." --severity critical --trigger kill_switch
    python scripts/create_incident.py --title "..." --severity high --trigger drawdown --drawdown 0.09
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
INCIDENTS_DIR = _PROJECT_ROOT / "docs" / "incidents"
TEMPLATE_PATH = INCIDENTS_DIR / "INCIDENT_TEMPLATE.md"

VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_TRIGGERS = {"kill_switch", "drawdown", "api_failure", "manual"}


def _slugify(title: str) -> str:
    """Convert title to a filesystem-safe slug."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug).strip("-")
    return slug[:60]


def create_incident(
    title: str,
    severity: str,
    trigger: str,
    drawdown: float | None = None,
    what_happened: str | None = None,
) -> Path:
    """
    Create a new incident markdown file from the template.

    Args:
        title:         Short incident title
        severity:      "critical" | "high" | "medium" | "low"
        trigger:       "kill_switch" | "drawdown" | "api_failure" | "manual"
        drawdown:      Drawdown magnitude (for auto-fill when trigger=drawdown)
        what_happened: Pre-filled description (optional)

    Returns:
        Path to the created incident file.
    """
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"severity must be one of {VALID_SEVERITIES}, got: {severity!r}")
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"trigger must be one of {VALID_TRIGGERS}, got: {trigger!r}")

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build auto "what happened" for drawdown trigger
    if what_happened is None and trigger == "drawdown" and drawdown is not None:
        what_happened = (
            f"Drawdown of {drawdown:.1%} exceeded the maximum allowed drawdown threshold "
            f"(8%). STOP file created automatically by metrics.py to halt trading."
        )
    elif what_happened is None:
        what_happened = "(fill in details)"

    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    filename = f"{date_str}-{slug}.md"
    incident_path = INCIDENTS_DIR / filename

    # Avoid overwriting existing incidents with a counter suffix
    if incident_path.exists():
        counter = 2
        while incident_path.exists():
            incident_path = INCIDENTS_DIR / f"{date_str}-{slug}-{counter}.md"
            counter += 1

    content = f"""# Incident {date_str}: {title}
**Severity**: {severity}
**Trigger**: {trigger}
**Detected at**: {ts_str}
**Resolved at**: (pending)

## What happened
{what_happened}

## Root cause
(fill in root cause)

## Impact (trades affected, estimated P&L impact)
(fill in impact)

## Fix applied
(fill in fix)

## Prevention
(fill in prevention steps)
"""

    with open(incident_path, "w") as f:
        f.write(content)

    print(f"[incident] Created incident report: {incident_path}", file=sys.stderr)
    return incident_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new incident report")
    parser.add_argument("--title", required=True, help="Short incident title")
    parser.add_argument(
        "--severity",
        required=True,
        choices=list(VALID_SEVERITIES),
        help="Incident severity level",
    )
    parser.add_argument(
        "--trigger",
        required=True,
        choices=list(VALID_TRIGGERS),
        help="What triggered the incident",
    )
    parser.add_argument(
        "--drawdown",
        type=float,
        default=None,
        help="Drawdown magnitude (e.g. 0.09 for 9%%), for drawdown trigger",
    )
    parser.add_argument(
        "--what-happened",
        default=None,
        help="Pre-filled description of what happened",
    )
    args = parser.parse_args()

    try:
        path = create_incident(
            title=args.title,
            severity=args.severity,
            trigger=args.trigger,
            drawdown=args.drawdown,
            what_happened=args.what_happened,
        )
        print(f"Incident created: {path}")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
