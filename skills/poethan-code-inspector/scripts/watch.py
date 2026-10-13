#!/usr/bin/env python3
"""Silent, one-shot watcher for Code Inspector tasks.

The watcher owns polling and JSON parsing. It writes nothing while the wake
condition is false, and emits only a compact ACTION_REQUIRED event when the
condition becomes true or repeated queries fail.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def safe_token(value: str, label: str) -> str:
    if not SAFE_TOKEN.fullmatch(value):
        raise ValueError(f"{label} must contain only letters, numbers, '.', '_', ':' or '-'")
    return value


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{timestamp} {message.rstrip()}\n")


def run_tool(tool: Path, arguments: list[str]) -> Any:
    command = [sys.executable, str(tool)] if tool.suffix == ".py" else [str(tool)]
    result = subprocess.run(
        [*command, *arguments],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
        raise RuntimeError(detail)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("role tool returned invalid JSON") from exc


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def query(args: argparse.Namespace) -> tuple[bool, str | None, int | None]:
    expected = set(args.expect)
    if args.kind == "issue-status":
        item = run_tool(args.tool, ["watch", "issue", args.target])
        status = str(item.get("status", ""))
        return status in expected, status or None, None

    if args.kind == "stage-status":
        command = ["watch", "stage", args.target, str(args.stage)]
        if args.plan is not None:
            command.extend(["--plan-no", str(args.plan)])
        item = run_tool(args.tool, command)
        status = str(item.get("status", ""))
        stage = int(item.get("stage_no", args.stage))
        return status in expected, status or None, stage

    if args.kind == "activity":
        command = ["watch", "activity", args.target,
                   "--after-activity-id", str(args.after_activity_id)]
        for activity_type in args.expect:
            command.extend(["--type", activity_type])
        item = run_tool(args.tool, command)
        if not item.get("matched"):
            return False, None, None
        activity_type = str(item.get("activity_type", ""))
        stage_value = item.get("stage_no")
        stage = int(stage_value) if stage_value is not None else None
        return True, activity_type, stage

    if args.kind == "task-status":
        item = run_tool(args.tool, ["watch", "task", args.target])
        status = str(item.get("status", ""))
        return status in expected, status or None, None

    if args.kind == "process":
        finished = not process_exists(args.pid)
        return finished, "PROCESS_FINISHED" if finished else None, None

    raise RuntimeError(f"unsupported watch kind: {args.kind}")


def emit_action(args: argparse.Namespace, reason: str, stage: int | None = None) -> None:
    print("ACTION_REQUIRED")
    print(f"target={args.target}")
    if args.role:
        print(f"role={args.role}")
    print(f"reason={reason}")
    if stage is not None:
        print(f"stage={stage}")
    sys.stdout.flush()


def parser() -> argparse.ArgumentParser:
    invocation = f"python {Path(__file__).resolve()}"
    result = argparse.ArgumentParser(
        description="静默观察一个 Code Inspector 目标；命中条件后输出一次 ACTION_REQUIRED 并退出。",
        epilog=(
            "示例（按当前角色选择 cictl-dev 或 cictl-insp）：\n"
            f"  {invocation} --kind task-status --target RT-D8D75313 --tool cictl-dev --expect CLOSED\n"
            f"  {invocation} --kind stage-status --target RI-1 --stage 2 --tool cictl-insp --expect PENDING_REVIEW\n"
            f"  {invocation} --kind activity --target RI-1 --tool cictl-dev --after-activity-id 42 --expect STAGE_REJECTED\n"
            "先用当前角色命令查询目标和最新 Activity id；仅在用户明确要求持续观察时启动。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    result.add_argument(
        "--kind",
        required=True,
        choices=("issue-status", "stage-status", "activity", "task-status", "process"),
        help="观察类型：Issue/Stage/Task 状态、新 Activity，或 PID 退出",
    )
    result.add_argument("--target", required=True, help="精确的 Task key、Issue key，或进程标识")
    result.add_argument("--role", help="唤醒事件中的角色标签；不用于切换 CLI 身份")
    result.add_argument("--tool", type=Path, help="当前角色命令 cictl-dev/cictl-insp，或其可执行文件路径；状态观察必填")
    result.add_argument("--expect", action="append", default=[], help="期望的状态或 Activity 类型；可重复；状态观察必填")
    result.add_argument("--stage", type=int, help="Stage 编号；stage-status 必填")
    result.add_argument("--plan", type=int, help="指定 Stage Plan 编号；stage-status 可选")
    result.add_argument("--after-activity-id", type=int, help="启动前最新 Activity id；activity 必填，只匹配之后的新事件")
    result.add_argument("--pid", type=int, help="进程 PID；process 必填，结束时唤醒")
    result.add_argument("--interval", type=float, default=120.0, help="轮询间隔秒数（默认 120）")
    result.add_argument("--max-errors", type=int, default=3, help="连续查询失败多少次后唤醒（默认 3）")
    result.add_argument("--log-file", type=Path, help="查询错误日志路径（默认系统临时目录）")
    return result


def validate(args: argparse.Namespace) -> None:
    args.target = safe_token(args.target, "target")
    if args.role:
        args.role = safe_token(args.role, "role")
    if args.interval <= 0:
        raise ValueError("interval must be greater than zero")
    if args.max_errors < 1:
        raise ValueError("max-errors must be at least one")
    if args.kind == "process":
        if not args.pid or args.pid < 1:
            raise ValueError("process watch requires --pid")
        if args.expect:
            raise ValueError("process watch does not accept --expect")
        return

    if args.tool is None:
        raise ValueError("state watch requires --tool cictl-dev or cictl-insp")
    if not args.tool.is_file():
        resolved = shutil.which(str(args.tool))
        if resolved is None:
            raise ValueError(f"role command not found: {args.tool}")
        args.tool = Path(resolved)
    if not args.expect:
        raise ValueError("state watch requires at least one --expect value")
    args.expect = [safe_token(value, "expect") for value in args.expect]
    if args.kind == "stage-status" and (args.stage is None or args.stage < 1):
        raise ValueError("stage-status watch requires a positive --stage")
    if args.kind == "activity" and args.after_activity_id is None:
        raise ValueError("activity watch requires --after-activity-id from the latest Activity")
    if args.kind == "activity" and args.after_activity_id < 0:
        raise ValueError("after-activity-id cannot be negative")


def main() -> int:
    args = parser().parse_args()
    try:
        validate(args)
    except ValueError as exc:
        print(f"watch: {exc}", file=sys.stderr)
        return 2

    if args.log_file is None:
        log_name = f"code-inspector-watch-{args.target}-{os.getpid()}.log"
        args.log_file = Path(tempfile.gettempdir()) / log_name

    errors = 0
    while True:
        try:
            matched, reason, stage = query(args)
            errors = 0
        except (OSError, RuntimeError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
            errors += 1
            append_log(args.log_file, f"query_error={type(exc).__name__}: {exc}")
            if errors >= args.max_errors:
                emit_action(args, "WATCH_QUERY_FAILED", args.stage if args.kind == "stage-status" else None)
                return 1
        else:
            if matched:
                emit_action(args, reason or "CONDITION_MET", stage)
                return 0

        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
