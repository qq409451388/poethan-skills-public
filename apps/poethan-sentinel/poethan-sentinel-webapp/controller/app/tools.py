from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import ApplicationSettings, PluginPackage, ServerScriptToolInput
from .plugins import MANAGED_TOOL_MARKER, plugin_service


class DiagnosticToolService:
    def create_local_script(
        self,
        settings: ApplicationSettings,
        name: str,
        description: str,
        runtime: str,
        filename: str,
        content: bytes,
    ) -> PluginPackage:
        if not content:
            raise ValueError("脚本文件不能为空")
        if len(content) > 10 * 1024 * 1024:
            raise ValueError("单个脚本不能超过 10 MB")
        source_name = "tool.py" if runtime == "python" else "tool.sh"
        return self._create(
            settings=settings,
            name=name,
            description=description or f"从本机脚本 {Path(filename).name} 创建",
            runtime=runtime,
            tool_type="local_script",
            source_name=source_name,
            source_content=content,
            remote_path=None,
        )

    def create_server_script(self, settings: ApplicationSettings, value: ServerScriptToolInput) -> PluginPackage:
        if not value.script_path.startswith("/"):
            raise ValueError("服务器脚本路径必须是绝对路径")
        return self._create(
            settings=settings,
            name=value.name,
            description=value.description or f"运行服务器已有脚本 {value.script_path}",
            runtime=value.runtime,
            tool_type="server_script",
            source_name=None,
            source_content=None,
            remote_path=value.script_path,
        )

    def _create(
        self,
        settings: ApplicationSettings,
        name: str,
        description: str,
        runtime: str,
        tool_type: str,
        source_name: str | None,
        source_content: bytes | None,
        remote_path: str | None,
    ) -> PluginPackage:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("工具名称不能为空")
        if runtime not in {"bash", "python"}:
            raise ValueError("运行环境只支持 bash 或 python")
        tool_id = self._tool_id(clean_name, tool_type)
        version = "1.0.0"
        root = Path(settings.plugin_directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".poethan-tool-", dir=root))
        try:
            fields: list[dict[str, object]] = [{
                "key": "POETHAN_PLUGIN_HOME", "label": "服务器工具目录", "type": "path",
                "section": "工具运行", "default": "/opt/poethan-sentinel/plugins", "required": True,
                "help": "工具按 ID 和版本缓存在服务器。",
            }]
            if tool_type == "server_script":
                fields.append({
                    "key": "REMOTE_SCRIPT_PATH", "label": "服务器脚本路径", "type": "path",
                    "section": "工具运行", "default": remote_path, "required": True,
                    "help": "必须是目标服务器上的绝对路径；可按服务器保存不同值。",
                })
            manifest = {
                "schemaVersion": 1,
                "id": tool_id,
                "name": clean_name,
                "description": description.strip(),
                "version": version,
                "toolType": tool_type,
                "entrypoint": "run.sh",
                "language": runtime,
                "outputLimit": 1_000_000,
                "permissions": {"sudo": False, "perf": False, "network": False, "readLogs": False, "databaseReadOnly": False},
                "defaultMode": "standard",
                "modes": [{"id": "standard", "label": "标准", "help": "按当前配置运行一次脚本。"}],
                "configuration": {"fields": fields},
            }
            (temporary / "plugin.yaml").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            marker = {"id": tool_id, "version": version, "toolType": tool_type, "createdAt": datetime.now(timezone.utc).isoformat()}
            (temporary / MANAGED_TOOL_MARKER).write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
            if source_name and source_content is not None:
                (temporary / source_name).write_bytes(source_content)
            wrapper = self._wrapper(runtime, source_name)
            entrypoint = temporary / "run.sh"
            entrypoint.write_text(wrapper, encoding="utf-8")
            entrypoint.chmod(0o755)
            package = plugin_service.validate(temporary, settings.developer_mode)
            destination = root / tool_id / version
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError(f"工具 {tool_id}@{version} 已存在")
            os.replace(temporary, destination)
            return plugin_service.validate(destination, settings.developer_mode)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)

    @staticmethod
    def _tool_id(name: str, tool_type: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        prefix = slug[:42] or ("local-script" if tool_type == "local_script" else "server-script")
        digest = hashlib.sha256(f"{tool_type}:{name}:{os.urandom(8).hex()}".encode("utf-8")).hexdigest()[:8]
        return f"{prefix}-{digest}"

    @staticmethod
    def _wrapper(runtime: str, source_name: str | None) -> str:
        target = f'"$SCRIPT_DIR/{source_name}"' if source_name else '"$REMOTE_SCRIPT_PATH"'
        interpreter = "python3" if runtime == "python" else "/bin/bash"
        required = ': "${REMOTE_SCRIPT_PATH:?未配置服务器脚本路径}"\n' if source_name is None else ""
        return f'''#!/usr/bin/env bash
set -uo pipefail
MODE="${{1:-standard}}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${{POETHAN_CONFIG_FILE:-$SCRIPT_DIR/config.env}}"
if [[ -f "$CONFIG_FILE" ]]; then set -a; source "$CONFIG_FILE"; set +a; fi
{required}exec {interpreter} {target} "$MODE"
'''


diagnostic_tool_service = DiagnosticToolService()
