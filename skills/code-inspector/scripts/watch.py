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
    result = subprocess.run(
        [sys.executable, str(tool), *arguments],
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
        item = run_tool(args.tool, ["watch-probe", "--kind", args.kind, "--target", args.target])
        status = str(item.get("status", ""))
        return status in expected, status or None, None

    if args.kind == "stage-status":
        command = ["watch-probe", "--kind", args.kind, "--target", args.target,
                   "--stage-no", str(args.stage)]
        if args.plan is not None:
            command.extend(["--plan-no", str(args.plan)])
        item = run_tool(args.tool, command)
        status = str(item.get("status", ""))
        stage = int(item.get("stage_no", args.stage))
        return status in expected, status or None, stage

    if args.kind == "activity":
        command = ["watch-probe", "--kind", args.kind, "--target", args.target,
                   "--after-activity-id", str(args.after_activity_id)]
        for activity_type in args.expect:
            command.extend(["--activity-type", activity_type])
        item = run_tool(args.tool, command)
        if not item.get("matched"):
            return False, None, None
        activity_type = str(item.get("activity_type", ""))
        stage_value = item.get("stage_no")
        stage = int(stage_value) if stage_value is not None else None
        return True, activity_type, stage

    if args.kind == "task-status":
        item = run_tool(args.tool, ["watch-probe", "--kind", args.kind, "--target", args.target])
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
    result = argparse.ArgumentParser(description="Silently watch one Code Inspector target")
    result.add_argument(
        "--kind",
        required=True,
        choices=("issue-status", "stage-status", "activity", "task-status", "process"),
    )
    result.add_argument("--target", required=True)
    result.add_argument("--role")
    result.add_argument("--tool", type=Path)
    result.add_argument("--expect", action="append", default=[])
    result.add_argument("--stage", type=int)
    result.add_argument("--plan", type=int)
    result.add_argument("--after-activity-id", type=int, default=0)
    result.add_argument("--pid", type=int)
    result.add_argument("--interval", type=float, default=120.0)
    result.add_argument("--max-errors", type=int, default=3)
    result.add_argument("--log-file", type=Path)
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

    if args.tool is None or not args.tool.is_file():
        raise ValueError("state watch requires an existing --tool path")
    if not args.expect:
        raise ValueError("state watch requires at least one --expect value")
    args.expect = [safe_token(value, "expect") for value in args.expect]
    if args.kind == "stage-status" and (args.stage is None or args.stage < 1):
        raise ValueError("stage-status watch requires a positive --stage")
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
