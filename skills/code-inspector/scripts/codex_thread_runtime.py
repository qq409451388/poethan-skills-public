#!/usr/bin/env python3
"""Version-gated Codex App Server JSON-RPC v2 adapter."""

from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


class CodexRuntimeError(RuntimeError):
    pass


REVIEW_COMMAND_RE = re.compile(r"review-db(?:-[A-Za-z0-9_-]+)?\.py[\"']?\s+([a-z][a-z0-9-]+)")

# 固定角色输出前缀：与安装器 ROLE_OUTPUT_PREFIXES 保持一致。
# 前缀逐字固定，不允许模型改写；只作角色强化信号，不作为权限判断依据。
ROLE_OUTPUT_PREFIXES: dict[str, dict[str, str]] = {
    "inspector": {
        "name": "Inspector",
        "boundary": "审核、判断、验收；禁止修改业务代码",
        "prefix": "[Inspector｜审核·判断·验收｜禁止修改业务代码]",
    },
    "developer": {
        "name": "Developer",
        "boundary": "设计实现、编码、测试；禁止最终审核确认",
        "prefix": "[Developer｜设计实现·编码·测试｜禁止最终审核确认]",
    },
    "human": {
        "name": "Human",
        "boundary": "业务决策、风险确认；不代替技术验证",
        "prefix": "[Human｜业务决策·风险确认｜不代替技术验证]",
    },
}


def role_identity_block(role: str) -> str:
    """按 session 绑定的真实角色生成每轮注入的短角色块。"""
    policy = ROLE_OUTPUT_PREFIXES.get(role)
    if policy is None:
        raise ValueError(f"ROLE_PREFIX_UNDEFINED:{role}")
    return (
        f"ROLE: {policy['name']}\n"
        f"BOUNDARY: {policy['boundary']}。\n"
        f"OUTPUT_PREFIX: {policy['prefix']}"
    )


def review_db_command(item: dict[str, Any]) -> str | None:
    """只提取 Review DB 子命令名，不保留 shell 参数或工具返回正文。"""
    candidates: list[str] = []
    for key in ("command", "cmd", "input"):
        value = item.get(key)
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.append(" ".join(str(part) for part in value))
    arguments = item.get("arguments")
    if isinstance(arguments, dict):
        for key in ("cmd", "command"):
            if isinstance(arguments.get(key), str):
                candidates.append(arguments[key])
    elif isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("cmd", "command"):
                if isinstance(parsed.get(key), str):
                    candidates.append(parsed[key])
    for candidate in candidates:
        matched = REVIEW_COMMAND_RE.search(candidate)
        if matched:
            return matched.group(1)
    return None


class CodexThreadRuntime:
    def __init__(self, config: dict[str, Any]):
        app = config["thread_runtime"]["app_server"]
        self.command = app["command"]
        self.request_timeout = int(app["request_timeout_seconds"])
        self.turn_timeout = int(app["turn_timeout_seconds"])
        self.process: subprocess.Popen[str] | None = None
        self.request_id = 0
        self.events: list[dict[str, Any]] = []
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.stderr_lines: deque[str] = deque(maxlen=40)

    def __enter__(self) -> "CodexThreadRuntime":
        self.process = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.request("initialize", {
            "clientInfo": {"name": "code-inspector-runtime", "title": "Code Inspector Runtime", "version": "1"},
            "capabilities": {"experimentalApi": True},
        })
        self._send({"method": "initialized", "params": {}})
        return self

    def __exit__(self, *_: object) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except json.JSONDecodeError:
                    self.stderr_lines.append(f"invalid-json:{line[-500:]}")
        finally:
            self.messages.put(None)

    def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process and self.process.stdin
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def _receive(self, timeout: float) -> dict[str, Any]:
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise CodexRuntimeError("APP_SERVER_TIMEOUT") from exc
        if message is None:
            detail = "\n".join(self.stderr_lines)[-1000:]
            raise CodexRuntimeError(f"APP_SERVER_CLOSED: {detail}")
        return message

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        request_id = self.request_id
        self._send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + self.request_timeout
        while True:
            if time.monotonic() >= deadline:
                raise CodexRuntimeError(f"APP_SERVER_TIMEOUT:{method}")
            message = self._receive(max(0.1, deadline - time.monotonic()))
            if message.get("id") == request_id:
                if "error" in message:
                    raise CodexRuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            if "id" in message and "method" in message:
                self._send({"id": message["id"], "error": {"code": -32000, "message": "unattended request denied"}})
            else:
                self.events.append(message)

    def _wait_turn(self, thread_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.turn_timeout
        last_message = ""
        usage: dict[str, Any] | None = None
        review_db_calls: dict[str, int] = {}
        while True:
            if time.monotonic() >= deadline:
                raise CodexRuntimeError(f"TURN_TIMEOUT:{thread_id}")
            message = self.events.pop(0) if self.events else self._receive(max(0.1, deadline - time.monotonic()))
            method, params = message.get("method"), message.get("params", {})
            if method == "item/completed" and params.get("threadId") == thread_id:
                item = params.get("item", {})
                command = review_db_command(item)
                if command:
                    review_db_calls[command] = review_db_calls.get(command, 0) + 1
                if item.get("type") == "agentMessage":
                    last_message = item.get("text", "")
            elif method == "thread/tokenUsage/updated" and params.get("threadId") == thread_id:
                usage = params.get("tokenUsage")
            elif method == "turn/completed" and params.get("threadId") == thread_id:
                turn = params.get("turn", {})
                if turn.get("status") != "completed":
                    raise CodexRuntimeError(f"TURN_{str(turn.get('status')).upper()}: {turn.get('error')}")
                return {
                    "status": "completed", "message": last_message, "usage": usage,
                    "turn_id": turn.get("id"), "review_db_calls": review_db_calls,
                }
            elif "id" in message and "method" in message:
                self._send({"id": message["id"], "error": {"code": -32000, "message": "unattended request denied"}})

    def start(
        self, cwd: str, role: str, issue_key: str, operator_id: str,
        agent_platform: str, fixed_tool_path: str, model: str | None = None,
    ) -> dict[str, Any]:
        prompt = (
            f"Code Inspector 固定身份：operator={operator_id}, platform={agent_platform}, role={role}, issue={issue_key}。\n"
            f"{role_identity_block(role)}\n"
            "每次 CLI 回复（包括 Action Turn、等待状态、审核结论、设计反馈、Stage 验收和实现提交结果）"
            "必须逐字以 OUTPUT_PREFIX 开头，不允许改写；角色和前缀来自本固定身份，不得自行切换。"
            "角色权限、状态机和工具权限以 Runtime/Session binding 为准。\n"
            f"固定 Review 工具：{fixed_tool_path}\n"
            "只处理该 Issue 且不得切换身份。Review DB 是状态真相；每个 ACTION Turn 先调用一次 "
            "issue-context-get，并以其 pending_action/permitted_actions/exception_actions 为当前流程依据。普通 ACTION 不读取完整 "
            "workflow.yaml 或 tool-contracts.yaml；仅在专项审计时按需查阅。"
        )
        params: dict[str, Any] = {
            "cwd": str(Path(cwd).resolve()),
            "sandbox": "read-only" if role == "inspector" else "workspace-write",
            "approvalPolicy": "never", "developerInstructions": prompt,
            "threadSource": "appServer",
        }
        if model:
            params["model"] = model
        started = self.request("thread/start", params)
        return {"thread_id": started["thread"]["id"], "thread": started["thread"]}

    def resume(self, thread_id: str, cwd: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"threadId": thread_id, "excludeTurns": True}
        if cwd:
            params["cwd"] = str(Path(cwd).resolve())
        return self.request("thread/resume", params)

    def run_turn(self, thread_id: str, prompt: str) -> dict[str, Any]:
        self.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]})
        return self._wait_turn(thread_id)

    # Public adapter spelling used by the runtime design; Python callers may
    # use run_turn without depending on JSON-RPC method names.
    def runTurn(self, thread_id: str, prompt: str) -> dict[str, Any]:  # noqa: N802
        return self.run_turn(thread_id, prompt)

    def resume_and_run(self, thread_id: str, cwd: str, prompt: str) -> dict[str, Any]:
        self.resume(thread_id, cwd)
        return self.run_turn(thread_id, prompt)

    def compact(self, thread_id: str, resume: bool = True) -> dict[str, Any]:
        if resume:
            self.resume(thread_id)
        self.request("thread/compact/start", {"threadId": thread_id})
        return self._wait_turn(thread_id)

    def archive(self, thread_id: str) -> dict[str, Any]:
        return self.request("thread/archive", {"threadId": thread_id})

    def unarchive(self, thread_id: str) -> dict[str, Any]:
        return self.request("thread/unarchive", {"threadId": thread_id})

    def list(self, archived: bool = False) -> dict[str, Any]:
        return self.request("thread/list", {"archived": archived, "sourceKinds": ["appServer"]})

    def read(self, thread_id: str) -> dict[str, Any]:
        return self.request("thread/read", {"threadId": thread_id, "includeTurns": False})

    def status(self, thread_id: str) -> str:
        value = self.read(thread_id)["thread"].get("status")
        return value.get("type", "unknown") if isinstance(value, dict) else str(value or "unknown")


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
