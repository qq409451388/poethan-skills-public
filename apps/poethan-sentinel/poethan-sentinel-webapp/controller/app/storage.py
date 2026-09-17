from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter
import yaml

from . import config
from .models import ApplicationSettings, AuthenticationKind, DiagnosticReport, ServerProfile
from .reports import hydrate_ai_html


T = TypeVar("T")


class JSONStore:
    # 本机运行插件会留下编译缓存和 macOS 元数据，它们不属于插件内容。
    PACKAGE_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")

    def __init__(self) -> None:
        config.ensure_directories()
        self._lock = RLock()
        self._install_official_plugins()

    @classmethod
    def _copy_package(cls, source: Path, destination: Path) -> None:
        shutil.copytree(source, destination, ignore=cls.PACKAGE_IGNORE)

    def _install_official_plugins(self) -> None:
        """Seed signed project plugins into the user-managed plugin directory by version."""
        if not config.PROJECT_PLUGIN_ROOT.exists():
            return
        destination_root = config.DATA_ROOT / "plugins"
        for source in config.PROJECT_PLUGIN_ROOT.iterdir():
            if not source.is_dir() or not (source / "plugin.yaml").is_file():
                continue
            try:
                manifest = yaml.safe_load((source / "plugin.yaml").read_text(encoding="utf-8"))
                destination = destination_root / str(manifest["id"]) / str(manifest["version"])
            except Exception:
                continue
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._copy_package(source, destination)

    def read(self, path: Path, default: T) -> T:
        with self._lock:
            if not path.exists():
                return default
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return default

    def write(self, path: Path, value: Any) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = value.model_dump(mode="json", by_alias=True) if isinstance(value, BaseModel) else value
            if isinstance(payload, list):
                payload = [item.model_dump(mode="json", by_alias=True) if isinstance(item, BaseModel) else item for item in payload]
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)

    def settings(self) -> ApplicationSettings:
        default_plugins = str(config.DATA_ROOT / "plugins")
        raw = self.read(config.SETTINGS_FILE, {})
        return ApplicationSettings.model_validate({"pluginDirectory": default_plugins, **self._migrate_ai_profiles(raw)})

    @staticmethod
    def _migrate_ai_profiles(raw: dict[str, Any]) -> dict[str, Any]:
        """旧版只有单一 ai 配置，读取时迁移成 aiProfiles 列表。"""
        legacy = raw.pop("ai", None)
        if not isinstance(legacy, dict) or "aiProfiles" in raw:
            return raw
        return {
            **raw,
            "aiProfiles": [{
                "id": "default",
                "name": "默认配置",
                "endpoint": legacy.get("endpoint") or "https://api.deepseek.com",
                "model": legacy.get("model") or "deepseek-flash",
            }],
            "activeAiId": "default",
        }

    def save_settings(self, settings: ApplicationSettings) -> None:
        self.write(config.SETTINGS_FILE, settings)

    def servers(self) -> list[ServerProfile]:
        raw = self.read(config.SERVERS_FILE, [])
        if not raw:
            demo = self._demo_server()
            self.save_servers([demo])
            return [demo]
        return TypeAdapter(list[ServerProfile]).validate_python(raw)

    def visible_servers(self, demo_mode: bool) -> list[ServerProfile]:
        servers = self.servers()
        demo = next((item for item in servers if item.authentication == AuthenticationKind.demo), None)
        if demo_mode:
            if demo is None:
                demo = self._demo_server()
                servers.insert(0, demo)
                self.save_servers(servers)
            return servers
        return [item for item in servers if item.authentication != AuthenticationKind.demo]

    @staticmethod
    def _demo_server() -> ServerProfile:
        return ServerProfile(
            id="demo-server",
            name="演示服务器",
            authentication=AuthenticationKind.demo,
            alias="demo",
            host="demo.local",
            user="demo",
        )

    def save_servers(self, servers: list[ServerProfile]) -> None:
        self.write(config.SERVERS_FILE, servers)

    def run_configs(self) -> dict[str, Any]:
        return self.read(config.RUN_CONFIGS_FILE, {})

    def save_run_configs(self, value: dict[str, Any]) -> None:
        self.write(config.RUN_CONFIGS_FILE, value)

    def save_report(self, report: DiagnosticReport) -> None:
        self.write(config.REPORTS_ROOT / f"{report.id}.json", report)

    def reports(self) -> list[DiagnosticReport]:
        reports: list[DiagnosticReport] = []
        for path in config.REPORTS_ROOT.glob("*.json"):
            try:
                reports.append(hydrate_ai_html(DiagnosticReport.model_validate_json(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return sorted(reports, key=lambda item: item.created_at, reverse=True)

    def report(self, report_id: str) -> DiagnosticReport | None:
        path = config.REPORTS_ROOT / f"{report_id}.json"
        if not path.exists():
            return None
        try:
            return hydrate_ai_html(DiagnosticReport.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:
            return None


store = JSONStore()
