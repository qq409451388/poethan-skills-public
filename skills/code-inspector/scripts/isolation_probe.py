#!/usr/bin/env python3
"""Verify independent persisted threads with interleaved resumes."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from codex_thread_runtime import CodexThreadRuntime, load_config


def create(runtime: CodexThreadRuntime, label: str, marker: str) -> tuple[str, dict]:
    started = runtime.request("thread/start", {
        "cwd": str(Path.cwd()), "sandbox": "read-only", "approvalPolicy": "never",
        "developerInstructions": f"Probe only. This is Issue {label}. Its private marker is {marker}. Do not call tools.",
        "threadSource": "appServer",
    })
    thread_id = started["thread"]["id"]
    turn = runtime.run_turn(thread_id, f"Remember {marker}. Reply exactly INITIALIZED_{label}")
    return thread_id, turn


def main() -> int:
    config = load_config(Path(__file__).resolve().parents[1] / "config" / "runtime.json")
    threads: dict[str, str] = {}
    result: dict[str, object] = {}
    try:
        with CodexThreadRuntime(config) as runtime:
            threads["A"], _ = create(runtime, "A", "MARKER_ALPHA_7F3")
            threads["B"], _ = create(runtime, "B", "MARKER_BRAVO_9K2")
        with CodexThreadRuntime(config) as runtime:
            a1 = runtime.resume_and_run(threads["A"], str(Path.cwd()), "Reply with only your private marker.")
            b1 = runtime.resume_and_run(threads["B"], str(Path.cwd()), "Reply with only your private marker.")
            a2 = runtime.resume_and_run(threads["A"], str(Path.cwd()), "Reply with only your Issue label: A or B.")
        def parallel_turn(label: str) -> dict:
            with CodexThreadRuntime(config) as runtime:
                return runtime.resume_and_run(threads[label], str(Path.cwd()), f"Reply exactly PARALLEL_{label}")
        with ThreadPoolExecutor(max_workers=2) as pool:
            parallel = {label: future.result() for label, future in {
                label: pool.submit(parallel_turn, label) for label in ("A", "B")
            }.items()}
        result.update({
            "thread_a": threads["A"], "thread_b": threads["B"],
            "distinct_threads": threads["A"] != threads["B"],
            "a_isolated": "MARKER_ALPHA_7F3" in a1["message"] and "MARKER_BRAVO_9K2" not in a1["message"],
            "b_isolated": "MARKER_BRAVO_9K2" in b1["message"] and "MARKER_ALPHA_7F3" not in b1["message"],
            "interleaved_resume": a2["status"] == "completed" and "A" in a2["message"],
            "concurrent_distinct_threads": all(f"PARALLEL_{label}" in parallel[label]["message"] for label in ("A", "B")),
            "cross_process_resume": True,
        })
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        for thread_id in threads.values():
            try:
                with CodexThreadRuntime(config) as runtime:
                    runtime.archive(thread_id)
            except Exception as exc:
                result.setdefault("cleanup_errors", []).append(str(exc))
    passed = all(result.get(key) for key in ("distinct_threads", "a_isolated", "b_isolated", "interleaved_resume", "concurrent_distinct_threads"))
    result["issue_isolation_verified"] = passed
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
