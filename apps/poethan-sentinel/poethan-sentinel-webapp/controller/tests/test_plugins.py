from __future__ import annotations

import json
import shutil
from pathlib import Path

from app import config
from app.models import ApplicationSettings
from app.plugins import plugin_service


def settings_for(path: Path, developer: bool = False) -> ApplicationSettings:
    return ApplicationSettings(plugin_directory=str(path), developer_mode=developer, demo_mode=True)


def test_official_plugins_are_signed_and_valid() -> None:
    result = plugin_service.scan(settings_for(config.PROJECT_PLUGIN_ROOT))
    assert result.valid_count == 3
    assert result.invalid_count == 0
    assert all(item.plugin and item.plugin.trust.status == "trusted" for item in result.items)


def test_tampered_signed_plugin_is_rejected(tmp_path: Path) -> None:
    source = config.PROJECT_PLUGIN_ROOT / "host-performance"
    target = tmp_path / "host-performance"
    shutil.copytree(source, target)
    (target / "main.py").write_text((target / "main.py").read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    result = plugin_service.scan(settings_for(tmp_path))
    assert result.valid_count == 0
    assert "摘要不匹配" in result.items[0].errors[0]


def test_unsigned_plugin_only_loads_in_developer_mode(tmp_path: Path) -> None:
    source = config.PROJECT_PLUGIN_ROOT / "network-diagnostic"
    target = tmp_path / "network-diagnostic"
    shutil.copytree(source, target)
    (target / "plugin.lock.json").unlink()
    (target / "plugin.sig").unlink()
    strict = plugin_service.scan(settings_for(tmp_path, False))
    developer = plugin_service.scan(settings_for(tmp_path, True))
    assert strict.valid_count == 0
    assert developer.valid_count == 1
    assert developer.items[0].plugin
    assert developer.items[0].plugin.trust.status == "unsigned"


def test_lock_covers_every_package_file() -> None:
    for root in config.PROJECT_PLUGIN_ROOT.iterdir():
        if not (root / "plugin.yaml").exists():
            continue
        lock = json.loads((root / "plugin.lock.json").read_text(encoding="utf-8"))
        expected = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path.name not in {"plugin.lock.json", "plugin.sig"}}
        assert {item["path"] for item in lock["files"]} == expected
