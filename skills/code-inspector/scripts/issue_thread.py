#!/usr/bin/env python3
"""Persistent Issue+Role thread registry and lifecycle CLI."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from codex_thread_runtime import CodexRuntimeError, CodexThreadRuntime, load_config


TERMINAL_ISSUES = {"CONFIRMED", "CANCELLED"}


def review_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("AGENT_REVIEW_HOME", "~/.agent-review")))


def database_path() -> Path:
    override = os.environ.get("AGENT_REVIEW_DB")
    if override:
        return Path(os.path.expanduser(override))
    runtime = review_home() / "config" / "runtime.json"
    if runtime.exists():
        return Path(json.loads(runtime.read_text(encoding="utf-8"))["database"])
    return review_home() / "data" / "review.db"


def config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "runtime.json"


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        result = super().__exit__(exc_type, exc, traceback)
        self.close()
        return result


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(database_path(), timeout=5, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def execution_lock(key: str) -> Iterator[None]:
    import fcntl
    lock_dir = review_home() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / ("".join(c if c.isalnum() or c in "._-" else "_" for c in key) + ".lock")
    with path.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("BUSY_RETRYABLE") from exc
        yield


@contextmanager
def active_slot(limit: int) -> Iterator[None]:
    import fcntl
    lock_dir = review_home() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    streams = []
    try:
        for index in range(limit):
            stream = (lock_dir / f"active-slot-{index}.lock").open("a+", encoding="utf-8")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                stream.close()
                continue
            streams.append(stream)
            yield
            return
        raise RuntimeError("CONCURRENCY_LIMIT")
    finally:
        for stream in streams:
            stream.close()


def issue_row(conn: sqlite3.Connection, issue_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT i.id, i.issue_key, i.status, t.project_path FROM review_issue i JOIN review_task t ON t.id=i.task_id WHERE i.issue_key=?",
        (issue_key,),
    ).fetchone()
    if not row:
        raise RuntimeError("ISSUE_NOT_FOUND")
    return row


def mapping(conn: sqlite3.Connection, issue_key: str, role: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM code_inspector_thread WHERE issue_key=? AND role=?", (issue_key, role),
    ).fetchone()


def usage_values(turn: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = turn.get("usage") or {}
    last = usage.get("last") or {}
    return last.get("totalTokens"), usage.get("modelContextWindow")


def runtime_call(config: dict[str, Any], function, *, retry_safe: bool = False):
    app = config["thread_runtime"]["app_server"]
    last_error: Exception | None = None
    retries = int(app["max_retry"]) if retry_safe else 0
    for attempt in range(retries + 1):
        try:
            with CodexThreadRuntime(config) as runtime:
                return function(runtime)
        except (CodexRuntimeError, OSError, KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(float(app["backoff_seconds"]) * (2 ** attempt))
    raise RuntimeError(f"APP_SERVER_FAILED: {last_error}")


def completed_stage_boundary(conn: sqlite3.Connection, issue_id: int, item: sqlite3.Row) -> int | None:
    """Return a newly persisted approved stage that is safe to compact after."""
    row = conn.execute(
        """SELECT MAX(stage_no) AS stage_no FROM issue_stage
           WHERE issue_id=? AND plan_status='ACTIVE' AND status='APPROVED'""",
        (issue_id,),
    ).fetchone()
    stage_no = row["stage_no"] if row else None
    if stage_no is None or item["last_compact_stage_no"] == stage_no:
        return None
    next_stage = conn.execute(
        """SELECT 1 FROM issue_stage WHERE issue_id=? AND plan_status='ACTIVE'
           AND stage_no>? AND status IN ('PLANNED','IN_PROGRESS','PENDING_REVIEW') LIMIT 1""",
        (issue_id, stage_no),
    ).fetchone()
    return int(stage_no) if next_stage else None


def start(issue_key: str, role: str, model: str | None = None) -> dict[str, Any]:
    config = load_config(config_path())
    flags = config["thread_runtime"]
    if not flags["enabled"] or not flags["isolation"]["enabled"]:
        raise RuntimeError("THREAD_ISOLATION_DISABLED")
    with execution_lock(f"dispatch-{issue_key}-{role}"):
        with connect() as conn:
            issue = issue_row(conn, issue_key)
            existing = mapping(conn, issue_key, role)
            if existing:
                raise RuntimeError(f"MAPPING_ALREADY_EXISTS:{existing['thread_id']}")
            active = conn.execute(
                "SELECT COUNT(*) FROM code_inspector_thread WHERE thread_status='ACTIVE'"
            ).fetchone()[0]
            if active >= int(flags["concurrency"]["max_active_issue_threads"]):
                raise RuntimeError("CONCURRENCY_LIMIT")
            cwd = issue["project_path"] or os.getcwd()
        lock_key = f"workspace-{Path(cwd).resolve()}" if role == "developer" and flags["workspace"]["enforce_safe_write"] else f"thread-new-{issue_key}-{role}"
        with active_slot(int(flags["concurrency"]["max_active_issue_threads"])):
            with execution_lock(lock_key):
                # Never replay this transaction: a timeout after thread/start may have
                # created an unbound thread, and a retry would create a duplicate.
                result = runtime_call(config, lambda runtime: runtime.start(cwd, role, issue_key, model))
        thread_id = result["thread_id"]
        tokens, window = usage_values(result["turn"])
        try:
            with connect() as conn:
                issue = issue_row(conn, issue_key)
                conn.execute(
                    """INSERT INTO code_inspector_thread(issue_id,issue_key,role,thread_id,thread_status,issue_status,next_action,last_event,cwd,context_tokens,context_window,last_active_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                    (issue["id"], issue_key, role, thread_id, "WAITING", issue["status"], "await_event", "INITIALIZED", cwd, tokens, window),
                )
        except Exception as exc:
            try:
                runtime_call(config, lambda runtime: runtime.archive(thread_id))
            finally:
                raise RuntimeError(f"MAPPING_WRITE_FAILED:{exc}") from exc
        return {"issue_key": issue_key, "role": role, "thread_id": thread_id, "thread_status": "WAITING"}


def resume(issue_key: str, role: str, reason: str) -> dict[str, Any]:
    config = load_config(config_path())
    with execution_lock(f"dispatch-{issue_key}-{role}"):
        with connect() as conn:
            issue = issue_row(conn, issue_key)
            item = mapping(conn, issue_key, role)
            if not item:
                raise RuntimeError("MAPPING_NOT_FOUND")
            if item["thread_status"] == "ACTIVE":
                raise RuntimeError("BUSY_RETRYABLE")
            if item["thread_status"] == "FAILED":
                raise RuntimeError(f"THREAD_{item['thread_status']}")
            thread_id, cwd = item["thread_id"], item["cwd"]
            was_archived = item["thread_status"] == "ARCHIVED"
            compact_flags = config["thread_runtime"]["compact"]
            boundary = completed_stage_boundary(conn, issue["id"], item)
            should_compact = bool(
                compact_flags["enabled"] and compact_flags["capability_verified"] and boundary
                and item["context_tokens"] and item["context_window"]
                and item["context_tokens"] / item["context_window"] >= float(compact_flags["threshold"])
            )
            conn.execute(
                "UPDATE code_inspector_thread SET thread_status='ACTIVE',last_event=?,updated_at=CURRENT_TIMESTAMP,last_active_at=CURRENT_TIMESTAMP,error_code=NULL,error_message=NULL WHERE id=?",
                (reason, item["id"]),
            )
        lock_key = f"workspace-{Path(cwd).resolve()}" if role == "developer" else f"thread-{thread_id}"
        try:
            with active_slot(int(config["thread_runtime"]["concurrency"]["max_active_issue_threads"])):
                with execution_lock(lock_key):
                    prompt = (
                        f"继续处理 {issue_key}。\nreason={reason}\n"
                        "重新读取 Review DB 最新状态后执行当前角色应执行动作，不依赖上次 Turn 缓存；只处理当前 Issue。"
                    )
                    def execute(runtime: CodexThreadRuntime):
                        if was_archived:
                            runtime.unarchive(thread_id)
                        runtime.resume(thread_id, cwd)
                        compact_error = None
                        if should_compact:
                            try:
                                runtime.compact(thread_id, resume=False)
                            except Exception as exc:
                                compact_error = str(exc)[:1000]
                            with connect() as compact_conn:
                                compact_conn.execute(
                                    """UPDATE code_inspector_thread
                                       SET last_compact_at=CURRENT_TIMESTAMP,last_compact_stage_no=?,
                                           error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP
                                       WHERE issue_key=? AND role=?""",
                                    (boundary, "COMPACT_FAILED" if compact_error else None, compact_error, issue_key, role),
                                )
                        result = runtime.run_turn(thread_id, prompt)
                        result["compact_error"] = compact_error
                        return result
                    # A mutation is not replayed after an ambiguous transport failure.
                    turn = runtime_call(config, execute)
            tokens, window = usage_values(turn)
            with connect() as conn:
                issue = issue_row(conn, issue_key)
                status = "COMPLETED" if issue["status"] in TERMINAL_ISSUES else "WAITING"
                conn.execute(
                    """UPDATE code_inspector_thread SET thread_status=?,issue_status=?,next_action=?,
                       context_tokens=COALESCE(?,context_tokens),context_window=COALESCE(?,context_window),
                       updated_at=CURRENT_TIMESTAMP,error_code=?,error_message=? WHERE issue_key=? AND role=?""",
                    (status, issue["status"], "archive" if status == "COMPLETED" else "await_event", tokens, window,
                     "COMPACT_FAILED" if turn.get("compact_error") else None, turn.get("compact_error"), issue_key, role),
                )
            if status == "COMPLETED":
                try:
                    runtime_call(config, lambda runtime: runtime.archive(thread_id))
                    with connect() as conn:
                        conn.execute("UPDATE code_inspector_thread SET thread_status='ARCHIVED',next_action='none',updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND role=?", (issue_key, role))
                    status = "ARCHIVED"
                except Exception as exc:
                    with connect() as conn:
                        conn.execute("UPDATE code_inspector_thread SET error_code='ARCHIVE_FAILED',error_message=? WHERE issue_key=? AND role=?", (str(exc)[:1000], issue_key, role))
            return {"issue_key": issue_key, "role": role, "thread_id": thread_id, "thread_status": status, "issue_status": issue["status"], "managed_compact": boundary if should_compact else None}
        except Exception as exc:
            with connect() as conn:
                conn.execute(
                    "UPDATE code_inspector_thread SET thread_status='PAUSED',error_code='DISPATCH_FAILED',error_message=?,updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND role=?",
                    (str(exc)[:1000], issue_key, role),
                )
            raise


def dispatch(issue_key: str, role: str, reason: str) -> dict[str, Any]:
    with connect() as conn:
        issue = issue_row(conn, issue_key)
        item = mapping(conn, issue_key, role)
    if item and item["thread_status"] in {"COMPLETED", "ARCHIVED"} and issue["status"] in TERMINAL_ISSUES:
        return {"issue_key": issue_key, "role": role, "thread_id": item["thread_id"], "thread_status": item["thread_status"], "status": "SKIPPED_TERMINAL"}
    return resume(issue_key, role, reason) if item else start(issue_key, role)


def compact(issue_key: str, role: str, stage_no: int, force: bool = False) -> dict[str, Any]:
    config = load_config(config_path())
    flags = config["thread_runtime"]["compact"]
    if not flags["enabled"] or not flags["capability_verified"]:
        raise RuntimeError("MANAGED_COMPACT_UNAVAILABLE")
    with execution_lock(f"dispatch-{issue_key}-{role}"):
        with connect() as conn:
            item = mapping(conn, issue_key, role)
            if not item:
                raise RuntimeError("MAPPING_NOT_FOUND")
            if item["last_compact_stage_no"] == stage_no:
                return {"status": "SKIPPED_ALREADY_COMPACTED", "stage_no": stage_no}
            if not force:
                if not item["context_tokens"] or not item["context_window"]:
                    raise RuntimeError("CONTEXT_USAGE_UNAVAILABLE")
                if item["context_tokens"] / item["context_window"] < float(flags["threshold"]):
                    return {"status": "SKIPPED_BELOW_THRESHOLD", "stage_no": stage_no}
            thread_id = item["thread_id"]
        try:
            result = runtime_call(config, lambda runtime: runtime.compact(thread_id))
            with connect() as conn:
                conn.execute(
                    "UPDATE code_inspector_thread SET last_compact_at=CURRENT_TIMESTAMP,last_compact_stage_no=?,updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND role=?",
                    (stage_no, issue_key, role),
                )
            return {"status": "COMPACTED", "stage_no": stage_no, "thread_id": thread_id, "result": result["status"]}
        except Exception as exc:
            with connect() as conn:
                conn.execute(
                    """UPDATE code_inspector_thread
                       SET error_code='COMPACT_FAILED',error_message=?,last_compact_at=CURRENT_TIMESTAMP,
                           last_compact_stage_no=?,updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND role=?""",
                    (str(exc)[:1000], stage_no, issue_key, role),
                )
            raise


def archive(issue_key: str, role: str) -> dict[str, Any]:
    config = load_config(config_path())
    with execution_lock(f"dispatch-{issue_key}-{role}"):
        with connect() as conn:
            item = mapping(conn, issue_key, role)
            if not item:
                raise RuntimeError("MAPPING_NOT_FOUND")
            thread_id = item["thread_id"]
        runtime_call(config, lambda runtime: runtime.archive(thread_id))
        with connect() as conn:
            conn.execute("UPDATE code_inspector_thread SET thread_status='ARCHIVED',updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND role=?", (issue_key, role))
        return {"issue_key": issue_key, "role": role, "thread_id": thread_id, "thread_status": "ARCHIVED"}


def status(issue_key: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        sql = "SELECT issue_key,role,thread_id,thread_status,issue_status,next_action,last_event,last_active_at,last_compact_stage_no,error_code FROM code_inspector_thread"
        params: tuple[Any, ...] = ()
        if issue_key:
            sql += " WHERE issue_key=?"
            params = (issue_key,)
        sql += " ORDER BY updated_at DESC"
        return [dict(row) for row in conn.execute(sql, params)]


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("dispatch", "start", "resume", "archive"):
        p = sub.add_parser(name); p.add_argument("--issue", required=True); p.add_argument("--role", required=True, choices=["inspector", "developer"])
        if name in {"dispatch", "resume"}: p.add_argument("--reason", default="ACTION_REQUIRED")
        if name == "start": p.add_argument("--model")
    p = sub.add_parser("status"); p.add_argument("--issue")
    p = sub.add_parser("compact"); p.add_argument("--issue", required=True); p.add_argument("--role", required=True, choices=["inspector", "developer"]); p.add_argument("--stage", required=True, type=int); p.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "dispatch": result = dispatch(args.issue, args.role, args.reason)
        elif args.command == "start": result = start(args.issue, args.role, args.model)
        elif args.command == "resume": result = resume(args.issue, args.role, args.reason)
        elif args.command == "archive": result = archive(args.issue, args.role)
        elif args.command == "compact": result = compact(args.issue, args.role, args.stage, args.force)
        else: result = status(args.issue)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
