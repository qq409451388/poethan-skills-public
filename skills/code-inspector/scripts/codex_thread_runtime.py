#!/usr/bin/env python3
"""Version-gated Codex App Server JSON-RPC v2 adapter."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


class CodexRuntimeError(RuntimeError):
    pass


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
        while True:
            if time.monotonic() >= deadline:
                raise CodexRuntimeError(f"TURN_TIMEOUT:{thread_id}")
            message = self.events.pop(0) if self.events else self._receive(max(0.1, deadline - time.monotonic()))
            method, params = message.get("method"), message.get("params", {})
            if method == "item/completed" and params.get("threadId") == thread_id:
                item = params.get("item", {})
                if item.get("type") == "agentMessage":
                    last_message = item.get("text", "")
            elif method == "thread/tokenUsage/updated" and params.get("threadId") == thread_id:
                usage = params.get("tokenUsage")
            elif method == "turn/completed" and params.get("threadId") == thread_id:
                turn = params.get("turn", {})
                if turn.get("status") != "completed":
                    raise CodexRuntimeError(f"TURN_{str(turn.get('status')).upper()}: {turn.get('error')}")
                return {"status": "completed", "message": last_message, "usage": usage, "turn_id": turn.get("id")}
            elif "id" in message and "method" in message:
                self._send({"id": message["id"], "error": {"code": -32000, "message": "unattended request denied"}})

    def start(self, cwd: str, role: str, issue_key: str, model: str | None = None) -> dict[str, Any]:
        prompt = (
            "加载 code-inspector Skill。\n\n"
            f"当前角色：{role}\n当前 Issue：{issue_key}\n\n"
            "这是该 Issue + Role 的独立执行 Thread。只处理当前 Issue；Review DB 是业务状态真相。"
            "从 Review DB 获取最新 Issue、Plan、Current Stage、必要 Activity、Evidence 与 Review Result，"
            "初始化当前角色状态。不要继承或寻找 Supervisor 会话历史。最后只确认初始化完成，不执行跨 Issue 工作。"
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
        thread_id = started["thread"]["id"]
        try:
            turn = self.run_turn(thread_id, "执行上述初始化要求。")
        except Exception:
            # The mapping is not committed until the initialization Turn
            # completes. Best-effort archive prevents a known-id orphan.
            try:
                self.archive(thread_id)
            except Exception:
                pass
            raise
        return {"thread_id": thread_id, "turn": turn, "thread": started["thread"]}

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
