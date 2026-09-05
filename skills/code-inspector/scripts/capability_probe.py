#!/usr/bin/env python3
"""Probe the installed Codex runtime; creates and archives one test thread."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from codex_thread_runtime import CodexThreadRuntime, load_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="persist version-bound capability evidence")
    args = parser.parse_args()
    config_path = Path(__file__).resolve().parents[1] / "config" / "runtime.json"
    config = load_config(config_path)
    cli = subprocess.run(["codex", "--version"], text=True, capture_output=True, check=True).stdout.strip()
    npm = subprocess.run(["npm", "list", "-g", "@openai/codex", "--depth=0", "--json"], text=True, capture_output=True, check=True)
    package_version = json.loads(npm.stdout)["dependencies"]["@openai/codex"]["version"]
    schema_dir = Path(tempfile.mkdtemp(prefix="code-inspector-schema-"))
    thread_id = None
    zero_turn_thread_id = None
    result = {"cli_version": cli, "package": "@openai/codex", "package_version": package_version, "backend": "app-server-json-rpc-v2"}
    result.update({
        "python_sdk": "not installed",
        "typescript_sdk": "not installed",
        "sdk_cli_session_interchange": "not assumed; runtime uses App Server persisted threads only",
    })
    try:
        subprocess.run(["codex", "app-server", "generate-json-schema", "--experimental", "--out", str(schema_dir)], check=True, capture_output=True)
        v2 = schema_dir / "v2"
        for name, filename in {
            "thread_start": "ThreadStartParams.json", "thread_resume": "ThreadResumeParams.json",
            "thread_read": "ThreadReadParams.json", "thread_list": "ThreadListParams.json",
            "thread_archive": "ThreadArchiveParams.json", "thread_compact": "ThreadCompactStartParams.json",
            "thread_unarchive": "ThreadUnarchiveParams.json",
        }.items():
            result[name] = (v2 / filename).exists()
        resume_schema = json.loads((v2 / "ThreadResumeParams.json").read_text())
        result["exclude_turns"] = "excludeTurns" in resume_schema.get("properties", {})
        start_properties = json.loads((v2 / "ThreadStartParams.json").read_text()).get("properties", {})
        result["thread_start_options"] = {name: name in start_properties for name in ("cwd", "model", "sandbox", "developerInstructions")}
        result["thread_resume_cwd"] = "cwd" in resume_schema.get("properties", {})
        usage_schema = v2 / "ThreadTokenUsageUpdatedNotification.json"
        result["context_usage_schema"] = usage_schema.exists()

        # Probe this exact installed version. Production still requires an
        # initialization turn because it establishes the fixed Issue identity.
        with CodexThreadRuntime(config) as runtime:
            zero_started = runtime.request("thread/start", {
                "cwd": str(Path.cwd()), "sandbox": "read-only", "approvalPolicy": "never",
                "developerInstructions": "Zero-turn persistence probe only.",
                "threadSource": "appServer",
            })
            zero_turn_thread_id = zero_started["thread"]["id"]
        try:
            with CodexThreadRuntime(config) as runtime:
                zero_resumed = runtime.resume(zero_turn_thread_id, str(Path.cwd()))
                result["zero_turn_resume"] = zero_resumed["thread"]["id"] == zero_turn_thread_id
                runtime.archive(zero_turn_thread_id)
                zero_turn_thread_id = None
        except Exception as zero_exc:
            result["zero_turn_resume"] = False
            result["zero_turn_error"] = str(zero_exc)

        with CodexThreadRuntime(config) as runtime:
            started = runtime.request("thread/start", {
                "cwd": str(Path.cwd()), "sandbox": "read-only", "approvalPolicy": "never",
                "developerInstructions": "Probe only. Do not call tools. Reply exactly as requested.",
                "threadSource": "appServer",
            })
            thread_id = started["thread"]["id"]
            first = runtime.run_turn(thread_id, "Reply exactly PROBE_INITIALIZED")
            compacted = runtime.compact(thread_id, resume=False)
            after_compact = runtime.run_turn(thread_id, "Reply exactly PROBE_AFTER_COMPACT")
        with CodexThreadRuntime(config) as runtime:
            resumed = runtime.resume(thread_id, str(Path.cwd()))
            after_restart = runtime.run_turn(thread_id, "Reply exactly PROBE_AFTER_RESTART")
            runtime.archive(thread_id)
        result.update({
            "initial_turn": first["status"] == "completed",
            "compact_continue": compacted["status"] == "completed" and after_compact["status"] == "completed",
            "cross_process_resume": resumed["thread"]["id"] == thread_id and after_restart["status"] == "completed",
            "context_usage_observed": bool(first.get("usage") or after_compact.get("usage") or after_restart.get("usage")),
            "archived": True,
        })
    except Exception as exc:
        result["error"] = str(exc)
        result["archived"] = False
        if thread_id:
            try:
                with CodexThreadRuntime(config) as runtime:
                    runtime.archive(thread_id)
                result["archived"] = True
            except Exception as cleanup_exc:
                result["cleanup_error"] = str(cleanup_exc)
        if zero_turn_thread_id:
            try:
                with CodexThreadRuntime(config) as runtime:
                    runtime.archive(zero_turn_thread_id)
            except Exception as cleanup_exc:
                result.setdefault("cleanup_errors", []).append(str(cleanup_exc))
    finally:
        shutil.rmtree(schema_dir, ignore_errors=True)
    result["thread_compact_supported"] = bool(result.get("compact_continue") and result.get("cross_process_resume"))
    result["managed_compact_supported"] = bool(result["thread_compact_supported"] and result.get("context_usage_observed"))
    result["thread_isolation_supported"] = bool(result.get("initial_turn") and result.get("cross_process_resume") and result.get("exclude_turns"))
    result["file_lock_backend"] = "msvcrt" if os.name == "nt" else "fcntl"
    result["platform"] = platform.system()
    result["probe_completed_at"] = datetime.now(timezone.utc).isoformat()
    if args.write:
        home = Path(os.path.expandvars(os.path.expanduser(os.environ.get("AGENT_REVIEW_HOME", "~/.agent-review"))))
        target = home / "config" / "runtime-capabilities.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result["capability_file"] = str(target)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("cross_process_resume") else 1


if __name__ == "__main__":
    raise SystemExit(main())
