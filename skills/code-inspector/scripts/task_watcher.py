#!/usr/bin/env python3
"""One silent shell watcher for multiple explicitly supplied Issue specs."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


def probe(spec: dict) -> dict | None:
    tool = Path(spec["tool"])
    command = [sys.executable, str(tool)] if tool.suffix == ".py" else [str(tool)]
    action = {"issue-status": "issue", "stage-status": "stage", "activity": "activity"}[spec["kind"]]
    command.extend(["watch", action, spec["issue"]])
    if spec["kind"] == "stage-status":
        command.append(str(spec["stage"]))
        if spec.get("plan") is not None:
            command.extend(["--plan-no", str(spec["plan"])])
    if spec["kind"] == "activity":
        command.extend(["--after-activity-id", str(spec["after_activity_id"])])
        for value in spec["expect"]:
            command.extend(["--type", value])
    result = subprocess.run(command, text=True, capture_output=True, timeout=90)
    if result.returncode != 0:
        raise RuntimeError("WATCH_QUERY_FAILED")
    item = json.loads(result.stdout)
    if spec["kind"] == "activity":
        return item if item.get("matched") else None
    return item if item.get("status") in spec["expect"] else None


def validate(specs: list[dict]) -> None:
    if not specs:
        raise ValueError("至少需要一个显式 Watch Specification")
    seen = set()
    for spec in specs:
        required = {"issue", "role", "kind", "expect", "tool"}
        if not required.issubset(spec) or not spec["expect"]:
            raise ValueError("Watch Specification 缺少 issue/role/kind/expect/tool")
        key = (spec["issue"], spec["role"])
        if key in seen:
            raise ValueError("同一 issue+role 只能有一个 Watch Specification")
        seen.add(key)
        tool = Path(spec["tool"])
        if not tool.is_file():
            resolved = shutil.which(str(tool))
            if resolved is None:
                raise ValueError(f"tool 不存在: {spec['tool']}")
            spec["tool"] = resolved
        if spec["kind"] not in {"issue-status", "stage-status", "activity"}:
            raise ValueError(f"不支持的观察类型: {spec['kind']}")
        if spec["kind"] == "stage-status" and not spec.get("stage"):
            raise ValueError("stage-status 缺少 stage")
        if spec["kind"] == "activity" and "after_activity_id" not in spec:
            raise ValueError("activity 缺少 after_activity_id")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-file", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=120)
    args = parser.parse_args()
    specs = json.loads(args.spec_file.read_text(encoding="utf-8"))
    try:
        validate(specs)
    except ValueError as exc:
        print(f"watch: {exc}", file=sys.stderr)
        return 2
    failures = {(spec["issue"], spec["role"]): 0 for spec in specs}
    while True:
        for spec in specs:
            try:
                matched = probe(spec)
            except Exception:
                key = (spec["issue"], spec["role"])
                failures[key] += 1
                if failures[key] >= 3:
                    print("ACTION_REQUIRED")
                    print(f"issue={spec['issue']}")
                    print(f"role={spec['role']}")
                    print("reason=WATCH_QUERY_FAILED")
                    return 1
                continue
            failures[(spec["issue"], spec["role"])] = 0
            if not matched:
                continue
            reason = matched.get("activity_type") or matched.get("status")
            print("ACTION_REQUIRED")
            print(f"issue={spec['issue']}")
            print(f"role={spec['role']}")
            print(f"reason={reason}")
            stage = matched.get("stage_no") or spec.get("stage")
            if stage is not None:
                print(f"stage={stage}")
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
