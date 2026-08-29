from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import AsyncIterator

from .ai import analyze_report
from .models import ApplicationSettings, RunEvent, RunRequest, RunState, ServerProfile
from .plugins import plugin_service
from .reports import build_report, demo_output
from .secrets import plugin_secret_account, secrets
from .ssh import ssh_service
from .storage import store


class RunManager:
    def __init__(self) -> None:
        self.runs: dict[str, RunState] = {}
        self.queues: dict[str, list[asyncio.Queue[RunEvent]]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, request: RunRequest, server: ServerProfile, settings: ApplicationSettings) -> RunState:
        plugin = plugin_service.find(settings, request.plugin_id, request.plugin_version)
        if request.mode not in {str(mode.get("id")) for mode in plugin.modes}:
            raise ValueError("运行模式不属于插件清单")
        state = RunState(server_id=server.id, plugin_id=plugin.id, plugin_version=plugin.version, mode=request.mode)
        self.runs[state.id] = state
        self.cancel_events[state.id] = threading.Event()
        self.tasks[state.id] = asyncio.create_task(self._run(state, request, server, settings))
        return state

    def get(self, run_id: str) -> RunState | None:
        return self.runs.get(run_id)

    def cancel(self, run_id: str) -> bool:
        if run_id not in self.runs:
            return False
        self.cancel_events[run_id].set()
        return True

    async def subscribe(self, run_id: str) -> AsyncIterator[RunEvent]:
        state = self.runs.get(run_id)
        if not state:
            return
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        for event in state.events:
            yield event
        if state.status in {"completed", "failed", "cancelled"}:
            return
        self.queues.setdefault(run_id, []).append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.type in {"complete", "error"}:
                    break
        finally:
            listeners = self.queues.get(run_id, [])
            if queue in listeners:
                listeners.remove(queue)

    async def emit(self, state: RunState, event_type: str, stage: str, message: str) -> None:
        event = RunEvent(sequence=len(state.events) + 1, type=event_type, stage=stage, message=message)
        state.events.append(event)
        state.stage = stage
        state.message = message
        for queue in self.queues.get(state.id, []):
            await queue.put(event)

    async def _run(self, state: RunState, request: RunRequest, server: ServerProfile, settings: ApplicationSettings) -> None:
        plugin = plugin_service.find(settings, request.plugin_id, request.plugin_version)
        cancel = self.cancel_events[state.id]
        started = time.monotonic()
        state.status = "running"
        try:
            secret_values = self._resolve_secrets(request, plugin.fields)
            if request.remember:
                configs = store.run_configs()
                configs[f"{server.id}:{plugin.id}"] = {"mode": request.mode, "values": request.values}
                store.save_run_configs(configs)
            await self.emit(state, "stage", "connection", "检查 SSH 连接")
            if server.authentication.value == "demo":
                output, exit_code, audit = await self._demo(state, plugin.id, cancel)
            else:
                await self.emit(state, "stage", "sync", f"验证并同步 {plugin.id}@{plugin.version}")
                loop = asyncio.get_running_loop()
                def on_output(text: str) -> None:
                    clean = self._redact(text, list(secret_values.values()))
                    loop.call_soon_threadsafe(asyncio.create_task, self.emit(state, "output", "execute", clean))
                result = await asyncio.to_thread(
                    ssh_service.execute_plugin, server, plugin, state.id, request.mode,
                    request.values, secret_values, plugin.output_limit, cancel, on_output,
                )
                output, exit_code = result.output, result.exit_code
                audit = {"pluginTrust": plugin.trust.model_dump(mode="json", by_alias=True), "archiveSha256": result.archive_sha256, "remotePlugin": result.remote_plugin}
            if cancel.is_set():
                raise InterruptedError("诊断已由用户停止")
            await self.emit(state, "stage", "report", "生成本地诊断报告")
            report = build_report(server, plugin, request.mode, self._redact(output, list(secret_values.values())), exit_code, time.monotonic() - started, audit)
            store.save_report(report)
            state.report_id = report.id
            await self.emit(state, "stage", "report_ready", f"本地报告已生成：{report.id}")
            if request.ai_enabled:
                await self.emit(state, "stage", "ai", "AI 正在分析诊断结果")
                try:
                    report.ai = await analyze_report(report, settings.ai)
                except Exception as exc:
                    report.ai = {"status": "failed", "error": str(exc)}
                store.save_report(report)
            state.status = "completed" if report.status == "completed" else "failed"
            state.completed_at = datetime.now(timezone.utc)
            await self.emit(state, "complete", "complete", "诊断完成" if state.status == "completed" else "诊断完成，但插件返回失败")
        except InterruptedError as exc:
            state.status = "cancelled"; state.completed_at = datetime.now(timezone.utc)
            await self.emit(state, "error", "cancelled", str(exc))
        except Exception as exc:
            state.status = "failed"; state.completed_at = datetime.now(timezone.utc)
            await self.emit(state, "error", "failed", str(exc))

    async def _demo(self, state: RunState, plugin_id: str, cancel: threading.Event) -> tuple[str, int, dict[str, str]]:
        phases = [
            ("connection", "演示服务器连接成功"), ("sync", "插件签名与版本缓存验证通过"),
            ("execute", "远程诊断脚本正在采集事实"), ("download", "诊断结果包下载完成"),
        ]
        for stage, message in phases:
            if cancel.is_set():
                raise InterruptedError("诊断已由用户停止")
            await self.emit(state, "stage", stage, message)
            await asyncio.sleep(0.45)
        output = demo_output(plugin_id)
        for line in output.splitlines():
            if cancel.is_set():
                raise InterruptedError("诊断已由用户停止")
            await self.emit(state, "output", "execute", line + "\n")
            await asyncio.sleep(0.018)
        return output, 0, {"demo": "true", "pluginTrust": "demo-fixture"}

    def _resolve_secrets(self, request: RunRequest, fields: list[dict]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for field in fields:
            if field.get("type") != "password":
                continue
            key = str(field["key"])
            supplied = request.secrets.get(key, "")
            account = plugin_secret_account(request.server_id, request.plugin_id, key)
            if supplied:
                secrets.set(account, supplied)
                resolved[key] = supplied
            else:
                stored = secrets.get(account)
                if stored:
                    resolved[key] = stored
            if field.get("required") and not resolved.get(key):
                raise ValueError(f"缺少必填敏感配置：{field.get('label', key)}")
        return resolved

    @staticmethod
    def _redact(text: str, values: list[str]) -> str:
        result = text
        for value in sorted((item for item in values if len(item) >= 3), key=len, reverse=True):
            result = result.replace(value, "***")
        return result


run_manager = RunManager()
