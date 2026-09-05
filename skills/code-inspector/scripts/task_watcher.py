#!/usr/bin/env python3
"""One silent shell watcher for multiple explicitly supplied Issue specs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def probe(spec: dict) -> dict | None:
    command = [sys.executable, spec["tool"], "watch-probe", "--kind", spec["kind"], "--target", spec["issue"]]
    if spec["kind"] == "stage-status":
        command.extend(["--stage-no", str(spec["stage"])])
    if spec["kind"] == "activity":
        command.extend(["--after-activity-id", str(spec["after_activity_id"])])
        for value in spec["expect"]:
            command.extend(["--activity-type", value])
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
        if not Path(spec["tool"]).is_file():
            raise ValueError(f"tool 不存在: {spec['tool']}")


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
