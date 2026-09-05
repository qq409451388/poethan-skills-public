"""Version-bound capability evidence for the local Codex Runtime."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def current_cli_version() -> str:
    return subprocess.run(["codex", "--version"], text=True, capture_output=True, check=True).stdout.strip()


def load_report(review_home: Path) -> dict:
    path = review_home / "config" / "runtime-capabilities.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def capability(review_home: Path, name: str) -> bool:
    report = load_report(review_home)
    return bool(
        report.get("cli_version") == current_cli_version()
        and report.get("probe_completed_at")
        and report.get(name) is True
    )


def require_capability(review_home: Path, name: str) -> None:
    if not capability(review_home, name):
        raise RuntimeError(f"CAPABILITY_PROBE_REQUIRED:{name}")
