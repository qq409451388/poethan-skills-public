from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEBAPP_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_ROOT = WEBAPP_ROOT / "contracts"
FRONTEND_DIST = WEBAPP_ROOT / "frontend" / "dist"
PROJECT_PLUGIN_ROOT = PROJECT_ROOT / "poethan-sentinel-plugins"


def default_data_root() -> Path:
    configured = os.getenv("POETHAN_SENTINEL_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / "Library" / "Application Support" / "Poethan Sentinel Web"


DATA_ROOT = default_data_root()
SETTINGS_FILE = DATA_ROOT / "settings.json"
SERVERS_FILE = DATA_ROOT / "servers.json"
RUN_CONFIGS_FILE = DATA_ROOT / "run-configs.json"
REPORTS_ROOT = DATA_ROOT / "reports"
KNOWN_HOSTS_FILE = DATA_ROOT / "known_hosts"
USER_PUBLISHERS_FILE = DATA_ROOT / "trusted-publishers.json"


def ensure_directories() -> None:
    for path in (DATA_ROOT, REPORTS_ROOT):
        path.mkdir(parents=True, exist_ok=True)
