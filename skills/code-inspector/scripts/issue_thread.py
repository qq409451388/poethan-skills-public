#!/usr/bin/env python3
"""Persistent Issue+Operator thread registry and lifecycle CLI."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
import threading
from typing import Any, Iterator

from codex_thread_runtime import CodexRuntimeError, CodexThreadRuntime, load_config
from review_repository import ReviewRepository
from runtime_identity import resolve_identity
from runtime_capabilities import capability, require_capability
from session_scope import SessionScope, assert_session_target, create_session_scope, require_config_allowed


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
    lock_dir = review_home() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / ("".join(c if c.isalnum() or c in "._-" else "_" for c in key) + ".lock")
    with path.open("a+", encoding="utf-8") as stream:
        try:
            lock_stream(stream)
        except (BlockingIOError, OSError) as exc:
            raise RuntimeError("BUSY_RETRYABLE") from exc
        try:
            yield
        finally:
            unlock_stream(stream)


def lock_stream(stream) -> None:
    if os.name == "nt":
        import msvcrt
        stream.seek(0)
        if not stream.read(1):
            stream.write("0"); stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_stream(stream) -> None:
    if os.name == "nt":
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def active_slot(limit: int) -> Iterator[None]:
    lock_dir = review_home() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    streams = []
    try:
        for index in range(limit):
            stream = (lock_dir / f"active-slot-{index}.lock").open("a+", encoding="utf-8")
            try:
                lock_stream(stream)
            except (BlockingIOError, OSError):
                stream.close()
                continue
            streams.append(stream)
            yield
            return
        raise RuntimeError("CONCURRENCY_LIMIT")
    finally:
        for stream in streams:
            unlock_stream(stream)
            stream.close()


@contextmanager
def thread_lease_heartbeat(
    issue_key: str, operator_id: str, worker_id: str, lease_seconds: int, interval_seconds: int,
) -> Iterator[None]:
    """Keep a live turn from being mistaken for a crashed ACTIVE mapping."""
    stopped = threading.Event()

    def beat() -> None:
        while not stopped.wait(interval_seconds):
            try:
                with connect() as conn:
                    conn.execute(
                        """UPDATE code_inspector_thread
                           SET heartbeat_at=CURRENT_TIMESTAMP,lease_until=datetime('now',?),
                               updated_at=CURRENT_TIMESTAMP
                           WHERE issue_key=? AND operator_id=? AND thread_status='ACTIVE' AND worker_id=?""",
                        (f"+{lease_seconds} seconds", issue_key, operator_id, worker_id),
                    )
            except sqlite3.Error:
                # The bounded lease still fails closed if heartbeats cannot be persisted.
                continue

    worker = threading.Thread(target=beat, name=f"thread-lease-{operator_id}", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=max(1, interval_seconds + 1))


def mapping(conn: sqlite3.Connection, issue_key: str, operator_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM code_inspector_thread WHERE issue_key=? AND operator_id=?", (issue_key, operator_id),
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


def start(
    issue_key: str, operator_id: str, expected_role: str | None = None,
    model: str | None = None, *, session_scope: SessionScope | None = None,
    acquire_dispatch_lock: bool = True,
) -> dict[str, Any]:
    config = load_config(config_path())
    if session_scope is None:
        raise PermissionError("SESSION_SCOPE_REQUIRED")
    require_config_allowed(session_scope, config)
    flags = config["thread_runtime"]
    if not flags["enabled"] or not flags["isolation"]["enabled"]:
        raise RuntimeError("THREAD_ISOLATION_DISABLED")
    require_capability(review_home(), flags["isolation"]["required_capability"])
    identity = resolve_identity(review_home(), operator_id, expected_role)
    assert_session_target(
        session_scope, identity.operator_id, identity.role,
        identity.agent_platform, identity.runtime_backend,
    )
    if identity.runtime_backend != "codex-app-server":
        raise RuntimeError(f"RUNTIME_BACKEND_UNSUPPORTED:{identity.runtime_backend}")
    role = identity.role
    lock = execution_lock(f"dispatch-{issue_key}-{operator_id}") if acquire_dispatch_lock else nullcontext()
    with lock:
        with connect() as conn:
            issue = ReviewRepository(conn).issue(issue_key)
            existing = mapping(conn, issue_key, operator_id)
            if existing:
                raise RuntimeError(f"MAPPING_ALREADY_EXISTS:{existing['thread_id']}")
            active = conn.execute(
                "SELECT COUNT(*) FROM code_inspector_thread WHERE thread_status='ACTIVE'"
            ).fetchone()[0]
            if active >= int(flags["concurrency"]["max_active_issue_threads"]):
                raise RuntimeError("CONCURRENCY_LIMIT")
            cwd = issue["project_path"] or os.getcwd()
        lock_key = f"workspace-{Path(cwd).resolve()}" if role == "developer" and flags["workspace"]["enforce_safe_write"] else f"thread-new-{issue_key}-{operator_id}"
        with active_slot(int(flags["concurrency"]["max_active_issue_threads"])):
            with execution_lock(lock_key):
                # Never replay this transaction: a timeout after thread/start may have
                # created an unbound thread, and a retry would create a duplicate.
                result = runtime_call(config, lambda runtime: runtime.start(
                    cwd, role, issue_key, identity.operator_id, identity.agent_platform,
                    identity.fixed_tool_path, model,
                ))
        thread_id = result["thread_id"]
        tokens, window = usage_values(result["turn"])
        try:
            with connect() as conn:
                issue = ReviewRepository(conn).issue(issue_key)
                conn.execute(
                    """INSERT INTO code_inspector_thread(
                         issue_id,issue_key,role,operator_id,agent_platform,runtime_backend,
                         thread_id,thread_status,issue_status,next_action,last_event,cwd,
                         context_tokens,context_window,last_active_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                    (issue["id"], issue_key, role, operator_id, identity.agent_platform,
                     identity.runtime_backend, thread_id, "WAITING", issue["status"],
                     "await_event", "INITIALIZED", cwd, tokens, window),
                )
        except Exception as exc:
            try:
                runtime_call(config, lambda runtime: runtime.archive(thread_id))
            finally:
                raise RuntimeError(f"MAPPING_WRITE_FAILED:{exc}") from exc
        return {"issue_key": issue_key, "role": role, "operator_id": operator_id, "thread_id": thread_id, "thread_status": "WAITING"}


def resume(
    issue_key: str, operator_id: str, reason: str, expected_role: str | None = None,
    event_id: str | None = None, *, session_scope: SessionScope | None = None,
    acquire_dispatch_lock: bool = True,
) -> dict[str, Any]:
    config = load_config(config_path())
    if session_scope is None:
        raise PermissionError("SESSION_SCOPE_REQUIRED")
    require_config_allowed(session_scope, config)
    identity = resolve_identity(review_home(), operator_id, expected_role)
    assert_session_target(
        session_scope, identity.operator_id, identity.role,
        identity.agent_platform, identity.runtime_backend,
    )
    if identity.runtime_backend != "codex-app-server":
        raise RuntimeError(f"RUNTIME_BACKEND_UNSUPPORTED:{identity.runtime_backend}")
    role = identity.role
    worker_id = f"dispatch-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    lease_seconds = int(config["thread_runtime"]["leases"]["thread_seconds"])
    heartbeat_seconds = int(config["thread_runtime"]["leases"].get("heartbeat_seconds", 60))
    lock = execution_lock(f"dispatch-{issue_key}-{operator_id}") if acquire_dispatch_lock else nullcontext()
    with lock:
        with connect() as conn:
            issue = ReviewRepository(conn).issue(issue_key)
            item = mapping(conn, issue_key, operator_id)
            if not item:
                raise RuntimeError("MAPPING_NOT_FOUND")
            if item["thread_status"] == "ACTIVE":
                stale = conn.execute("SELECT datetime(?) <= CURRENT_TIMESTAMP", (item["lease_until"],)).fetchone()[0] if item["lease_until"] else False
                raise RuntimeError("STALE_ACTIVE_RECONCILE_REQUIRED" if stale else "BUSY_RETRYABLE")
            if item["thread_status"] == "FAILED":
                raise RuntimeError(f"THREAD_{item['thread_status']}")
            thread_id, cwd = item["thread_id"], item["cwd"]
            was_archived = item["thread_status"] == "ARCHIVED"
            compact_flags = config["thread_runtime"]["compact"]
            boundary = ReviewRepository(conn).completed_stage_boundary(issue["id"], item["last_compact_stage_no"])
            should_compact = bool(
                compact_flags["enabled"] and capability(review_home(), compact_flags["required_capability"]) and boundary
                and item["context_tokens"] and item["context_window"]
                and item["context_tokens"] / item["context_window"] >= float(compact_flags["threshold"])
            )
            conn.execute(
                """UPDATE code_inspector_thread SET thread_status='ACTIVE',last_event=?,worker_id=?,
                   lease_until=datetime('now',?),heartbeat_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,
                   last_active_at=CURRENT_TIMESTAMP,error_code=NULL,error_message=NULL WHERE id=?""",
                (event_id or reason, worker_id, f"+{lease_seconds} seconds", item["id"]),
            )
        lock_key = f"workspace-{Path(cwd).resolve()}" if role == "developer" else f"thread-{thread_id}"
        action_started = False
        try:
            with active_slot(int(config["thread_runtime"]["concurrency"]["max_active_issue_threads"])):
                with execution_lock(lock_key):
                    prompt = (
                        f"继续处理 {issue_key}。\nreason={reason}\n"
                        f"event_id={event_id or '-'}\noperator_id={operator_id}\n"
                        f"固定 Review 工具={identity.fixed_tool_path}\n"
                        "重新读取 Review DB 最新状态后执行当前角色应执行动作，不依赖上次 Turn 缓存；"
                        "只使用固定工具，只处理当前 Issue，不得切换角色。"
                    )
                    def execute(runtime: CodexThreadRuntime):
                        nonlocal action_started
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
                                       WHERE issue_key=? AND operator_id=?""",
                                    (boundary, "COMPACT_FAILED" if compact_error else None, compact_error, issue_key, operator_id),
                                )
                        action_started = True
                        result = runtime.run_turn(thread_id, prompt)
                        result["compact_error"] = compact_error
                        return result
                    # A mutation is not replayed after an ambiguous transport failure.
                    with thread_lease_heartbeat(
                        issue_key, operator_id, worker_id, lease_seconds, heartbeat_seconds,
                    ):
                        turn = runtime_call(config, execute)
            tokens, window = usage_values(turn)
            with connect() as conn:
                issue = ReviewRepository(conn).issue(issue_key)
                status = "COMPLETED" if issue["status"] in TERMINAL_ISSUES else "WAITING"
                conn.execute(
                    """UPDATE code_inspector_thread SET thread_status=?,issue_status=?,next_action=?,
                       context_tokens=COALESCE(?,context_tokens),context_window=COALESCE(?,context_window),
                       worker_id=NULL,lease_until=NULL,heartbeat_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP,error_code=?,error_message=? WHERE issue_key=? AND operator_id=?""",
                    (status, issue["status"], "archive" if status == "COMPLETED" else "await_event", tokens, window,
                     "COMPACT_FAILED" if turn.get("compact_error") else None, turn.get("compact_error"), issue_key, operator_id),
                )
            if status == "COMPLETED":
                try:
                    runtime_call(config, lambda runtime: runtime.archive(thread_id))
                    with connect() as conn:
                        conn.execute("UPDATE code_inspector_thread SET thread_status='ARCHIVED',next_action='none',updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND operator_id=?", (issue_key, operator_id))
                    status = "ARCHIVED"
                except Exception as exc:
                    with connect() as conn:
                        conn.execute("UPDATE code_inspector_thread SET error_code='ARCHIVE_FAILED',error_message=? WHERE issue_key=? AND operator_id=?", (str(exc)[:1000], issue_key, operator_id))
            return {"issue_key": issue_key, "role": role, "operator_id": operator_id, "thread_id": thread_id, "thread_status": status, "issue_status": issue["status"], "managed_compact": boundary if should_compact else None, "action_turn_completed": True}
        except Exception as exc:
            with connect() as conn:
                if action_started:
                    conn.execute(
                        """UPDATE code_inspector_thread SET thread_status='PAUSED',worker_id=NULL,lease_until=NULL,
                           error_code='AMBIGUOUS_DISPATCH',error_message=?,updated_at=CURRENT_TIMESTAMP
                           WHERE issue_key=? AND operator_id=?""",
                        (str(exc)[:1000], issue_key, operator_id),
                    )
                else:
                    conn.execute(
                        """UPDATE code_inspector_thread SET thread_status='WAITING',worker_id=NULL,lease_until=NULL,
                           next_action='retry_event',error_code='PRE_ACTION_RETRYABLE',error_message=?,
                           updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND operator_id=?""",
                        (str(exc)[:1000], issue_key, operator_id),
                    )
            if action_started:
                raise RuntimeError(f"AMBIGUOUS_DISPATCH:{exc}") from exc
            raise RuntimeError(f"PRE_ACTION_RETRYABLE:{exc}") from exc


def dispatch(
    issue_key: str, operator_id: str, reason: str, expected_role: str | None = None,
    event_id: str | None = None, *, session_scope: SessionScope | None = None,
) -> dict[str, Any]:
    config = load_config(config_path())
    identity = resolve_identity(review_home(), operator_id, expected_role)
    if session_scope is None:
        raise PermissionError("SESSION_SCOPE_REQUIRED")
    require_config_allowed(session_scope, config)
    assert_session_target(
        session_scope, identity.operator_id, identity.role,
        identity.agent_platform, identity.runtime_backend,
    )
    # One lock covers lookup, optional initialization and the original Action Turn.
    # This prevents a second direct dispatch from overtaking the first event between
    # mapping persistence and its action.
    with execution_lock(f"dispatch-{issue_key}-{operator_id}"):
        with connect() as conn:
            issue = ReviewRepository(conn).issue(issue_key)
            item = mapping(conn, issue_key, operator_id)
        if item and item["thread_status"] in {"COMPLETED", "ARCHIVED"} and issue["status"] in TERMINAL_ISSUES:
            return {"issue_key": issue_key, "role": identity.role, "operator_id": operator_id, "thread_id": item["thread_id"], "thread_status": item["thread_status"], "status": "SKIPPED_TERMINAL", "action_turn_completed": True}
        initialized = None
        if not item:
            initialized = start(
                issue_key, operator_id, expected_role, session_scope=session_scope,
                acquire_dispatch_lock=False,
            )
        result = resume(
            issue_key, operator_id, reason, expected_role, event_id,
            session_scope=session_scope,
            acquire_dispatch_lock=False,
        )
        result["initialized"] = bool(initialized)
        return result


def compact(
    issue_key: str, operator_id: str, stage_no: int, force: bool = False,
    expected_role: str | None = None, *, session_scope: SessionScope | None = None,
) -> dict[str, Any]:
    config = load_config(config_path())
    if session_scope is None:
        raise PermissionError("SESSION_SCOPE_REQUIRED")
    require_config_allowed(session_scope, config)
    identity = resolve_identity(review_home(), operator_id, expected_role)
    assert_session_target(
        session_scope, identity.operator_id, identity.role,
        identity.agent_platform, identity.runtime_backend,
    )
    role = identity.role
    flags = config["thread_runtime"]["compact"]
    if not flags["enabled"] or not capability(review_home(), flags["required_capability"]):
        raise RuntimeError("MANAGED_COMPACT_UNAVAILABLE")
    with execution_lock(f"dispatch-{issue_key}-{operator_id}"):
        with connect() as conn:
            item = mapping(conn, issue_key, operator_id)
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
                    "UPDATE code_inspector_thread SET last_compact_at=CURRENT_TIMESTAMP,last_compact_stage_no=?,updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND operator_id=?",
                    (stage_no, issue_key, operator_id),
                )
            return {"status": "COMPACTED", "stage_no": stage_no, "thread_id": thread_id, "result": result["status"]}
        except Exception as exc:
            with connect() as conn:
                conn.execute(
                    """UPDATE code_inspector_thread
                       SET error_code='COMPACT_FAILED',error_message=?,last_compact_at=CURRENT_TIMESTAMP,
                           last_compact_stage_no=?,updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND operator_id=?""",
                    (str(exc)[:1000], stage_no, issue_key, operator_id),
                )
            raise


def archive(
    issue_key: str, operator_id: str, expected_role: str | None = None, *,
    session_scope: SessionScope | None = None,
) -> dict[str, Any]:
    config = load_config(config_path())
    if session_scope is None:
        raise PermissionError("SESSION_SCOPE_REQUIRED")
    require_config_allowed(session_scope, config)
    identity = resolve_identity(review_home(), operator_id, expected_role)
    assert_session_target(
        session_scope, identity.operator_id, identity.role,
        identity.agent_platform, identity.runtime_backend,
    )
    role = identity.role
    with execution_lock(f"dispatch-{issue_key}-{operator_id}"):
        with connect() as conn:
            item = mapping(conn, issue_key, operator_id)
            if not item:
                raise RuntimeError("MAPPING_NOT_FOUND")
            thread_id = item["thread_id"]
        runtime_call(config, lambda runtime: runtime.archive(thread_id))
        with connect() as conn:
            conn.execute("UPDATE code_inspector_thread SET thread_status='ARCHIVED',updated_at=CURRENT_TIMESTAMP WHERE issue_key=? AND operator_id=?", (issue_key, operator_id))
        return {"issue_key": issue_key, "role": role, "operator_id": operator_id, "thread_id": thread_id, "thread_status": "ARCHIVED"}


def status(issue_key: str | None = None, operator_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        sql = """SELECT issue_key,role,operator_id,agent_platform,runtime_backend,thread_id,
                 thread_status,issue_status,next_action,last_event,last_active_at,
                 last_compact_stage_no,error_code,error_message,worker_id,lease_until,
                 CASE WHEN context_tokens IS NOT NULL AND context_window > 0
                      THEN ROUND(100.0 * context_tokens / context_window, 1) END AS context_usage
                 FROM code_inspector_thread"""
        filters, params = [], []
        if issue_key:
            filters.append("issue_key=?"); params.append(issue_key)
        if operator_id:
            filters.append("operator_id=?"); params.append(operator_id)
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY updated_at DESC"
        return [dict(row) for row in conn.execute(sql, tuple(params))]


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("dispatch", "start", "resume", "archive"):
        p = sub.add_parser(name); p.add_argument("--issue", required=True); p.add_argument("--session-identity", required=True); p.add_argument("--multi-thread", action="store_true", required=True)
        if name in {"dispatch", "resume"}: p.add_argument("--reason", default="ACTION_REQUIRED")
        if name in {"dispatch", "resume"}: p.add_argument("--event-id")
        if name == "start": p.add_argument("--model")
    p = sub.add_parser("status"); p.add_argument("--issue"); p.add_argument("--operator")
    p = sub.add_parser("compact"); p.add_argument("--issue", required=True); p.add_argument("--session-identity", required=True); p.add_argument("--multi-thread", action="store_true", required=True); p.add_argument("--stage", required=True, type=int); p.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        if args.command != "status":
            config = load_config(config_path())
            scope = create_session_scope(review_home(), args.session_identity, config, explicit_multi_thread=args.multi_thread)
            operator, role = scope.operator_id, scope.role
        if args.command == "dispatch": result = dispatch(args.issue, operator, args.reason, role, args.event_id, session_scope=scope)
        elif args.command == "start": result = start(args.issue, operator, role, args.model, session_scope=scope)
        elif args.command == "resume": result = resume(args.issue, operator, args.reason, role, args.event_id, session_scope=scope)
        elif args.command == "archive": result = archive(args.issue, operator, role, session_scope=scope)
        elif args.command == "compact": result = compact(args.issue, operator, args.stage, args.force, role, session_scope=scope)
        else: result = status(args.issue, args.operator)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
