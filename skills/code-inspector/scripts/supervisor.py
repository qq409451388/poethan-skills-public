#!/usr/bin/env python3
"""Small-context leased event dispatcher and safe recovery service."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed

import issue_thread
from review_repository import ReviewRepository
from runtime_identity import resolve_identity


def worker_id() -> str:
    return f"supervisor-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def audit(conn, action: str, resource_id: str, success: bool, detail: str = "") -> None:
    conn.execute(
        """INSERT INTO agent_audit_log(agent_id,action,resource_type,resource_id,success,detail)
           VALUES('runtime-supervisor',?,'runtime',?,?,?)""",
        (action, resource_id, 1 if success else 0, detail),
    )


def enqueue(issue: str, operator_id: str, event_type: str, activity_id: int | None, stage: int | None, event_id: str | None, expected_role: str | None = None) -> dict:
    identity = resolve_identity(issue_thread.review_home(), operator_id, expected_role)
    supplied_event_id = event_id
    event_id = event_id or str(uuid.uuid4())
    identity_key = activity_id if activity_id is not None else supplied_event_id or f"stage={stage}"
    key = f"{issue}:{operator_id}:{identity_key}:{event_type}"
    with issue_thread.connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO code_inspector_event(
                 event_id,idempotency_key,issue_key,role,operator_id,agent_platform,
                 runtime_backend,event_type,activity_id,stage_no)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (event_id, key, issue, identity.role, operator_id, identity.agent_platform,
             identity.runtime_backend, event_type, activity_id, stage),
        )
        row = conn.execute("SELECT * FROM code_inspector_event WHERE idempotency_key=?", (key,)).fetchone()
    return dict(row)


def recover_stale_events(
    max_attempts: int, issue: str | None = None, operator: str | None = None,
) -> dict:
    recovered = failed = ambiguous = 0
    with issue_thread.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        filters = ["status='PROCESSING'", "lease_until < CURRENT_TIMESTAMP"]
        params: list[str] = []
        if issue:
            filters.append("issue_key=?"); params.append(issue)
        if operator:
            filters.append("operator_id=?"); params.append(operator)
        rows = conn.execute(
            "SELECT * FROM code_inspector_event WHERE " + " AND ".join(filters), params,
        ).fetchall()
        for event in rows:
            thread = conn.execute(
                "SELECT thread_status,last_event FROM code_inspector_thread WHERE issue_key=? AND operator_id=?",
                (event["issue_key"], event["operator_id"]),
            ).fetchone()
            uncertain = bool(thread and thread["last_event"] == event["event_id"])
            if uncertain:
                conn.execute(
                    """UPDATE code_inspector_event SET status='FAILED',failure_kind='AMBIGUOUS',
                       last_error='lease expired after dispatch began; reconcile before manual retry',
                       worker_id=NULL,lease_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""", (event["id"],),
                )
                ambiguous += 1
            elif event["attempt_count"] >= max_attempts:
                conn.execute(
                    """UPDATE code_inspector_event SET status='FAILED',failure_kind='RETRYABLE',
                       last_error='maximum claim attempts exceeded',worker_id=NULL,lease_until=NULL,
                       updated_at=CURRENT_TIMESTAMP WHERE id=?""", (event["id"],),
                )
                failed += 1
            else:
                conn.execute(
                    """UPDATE code_inspector_event SET status='PENDING',worker_id=NULL,claimed_at=NULL,
                       lease_until=NULL,next_attempt_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (event["id"],),
                )
                recovered += 1
        conn.commit()
    return {"recovered": recovered, "failed": failed, "ambiguous": ambiguous}


@contextmanager
def event_lease_heartbeat(event: dict):
    config = issue_thread.load_config(issue_thread.config_path())
    leases = config["thread_runtime"]["leases"]
    lease_seconds = int(leases["event_seconds"])
    interval_seconds = int(leases.get("heartbeat_seconds", 60))
    stopped = threading.Event()

    def beat() -> None:
        while not stopped.wait(interval_seconds):
            try:
                with issue_thread.connect() as conn:
                    conn.execute(
                        """UPDATE code_inspector_event
                           SET lease_until=datetime('now',?),updated_at=CURRENT_TIMESTAMP
                           WHERE id=? AND status='PROCESSING' AND worker_id=?""",
                        (f"+{lease_seconds} seconds", event["id"], event["worker_id"]),
                    )
            except Exception:
                continue

    worker = threading.Thread(target=beat, name=f"event-lease-{event['id']}", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=max(1, interval_seconds + 1))


def claim(limit: int, worker: str | None = None) -> list[dict]:
    config = issue_thread.load_config(issue_thread.config_path())
    lease = int(config["thread_runtime"]["leases"]["event_seconds"])
    worker = worker or worker_id()
    with issue_thread.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT e.* FROM code_inspector_event e
               WHERE e.status='PENDING' AND (e.next_attempt_at IS NULL OR e.next_attempt_at <= CURRENT_TIMESTAMP)
                 AND NOT EXISTS(
                   SELECT 1 FROM code_inspector_event active
                   WHERE active.issue_key=e.issue_key AND active.operator_id=e.operator_id
                     AND active.status='PROCESSING'
                 )
                 AND e.id=(SELECT MIN(x.id) FROM code_inspector_event x
                           WHERE x.status='PENDING' AND x.issue_key=e.issue_key
                             AND x.operator_id=e.operator_id
                             AND (x.next_attempt_at IS NULL OR x.next_attempt_at <= CURRENT_TIMESTAMP))
               ORDER BY e.created_at,e.id LIMIT ?""", (limit,),
        ).fetchall()
        ids = [row["id"] for row in rows]
        if ids:
            conn.execute(
                f"""UPDATE code_inspector_event SET status='PROCESSING',attempt_count=attempt_count+1,
                    worker_id=?,claimed_at=CURRENT_TIMESTAMP,lease_until=datetime('now',?),
                    failure_kind=NULL,last_error=NULL,updated_at=CURRENT_TIMESTAMP
                    WHERE id IN ({','.join('?' for _ in ids)})""",
                (worker, f"+{lease} seconds", *ids),
            )
        conn.commit()
        return [dict(conn.execute("SELECT * FROM code_inspector_event WHERE id=?", (row_id,)).fetchone()) for row_id in ids]


def classify_error(exc: Exception) -> str:
    text = str(exc)
    if any(code in text for code in ("BUSY_RETRYABLE", "CONCURRENCY_LIMIT", "PRE_ACTION_RETRYABLE")):
        return "RETRYABLE"
    if any(code in text for code in ("APP_SERVER_FAILED", "AMBIGUOUS", "TURN_TIMEOUT", "STALE_ACTIVE")):
        return "AMBIGUOUS"
    return "NON_RETRYABLE"


def process_event(event: dict) -> dict:
    try:
        with event_lease_heartbeat(event):
            result = issue_thread.dispatch(
                event["issue_key"], event["operator_id"], event["event_type"],
                event["role"], event["event_id"],
            )
        if not result.get("action_turn_completed"):
            raise RuntimeError("ACTION_TURN_NOT_COMPLETED")
        status, failure, error = "DONE", None, None
    except Exception as exc:
        result, failure, error = None, classify_error(exc), str(exc)[:1000]
        status = "PENDING" if failure == "RETRYABLE" else "FAILED"
    with issue_thread.connect() as conn:
        if status == "PENDING":
            cursor = conn.execute(
                """UPDATE code_inspector_event SET status='PENDING',failure_kind='RETRYABLE',last_error=?,
                   worker_id=NULL,lease_until=NULL,next_attempt_at=datetime('now','+30 seconds'),
                   updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='PROCESSING' AND worker_id=?""",
                (error, event["id"], event["worker_id"]),
            )
        else:
            cursor = conn.execute(
                """UPDATE code_inspector_event SET status=?,failure_kind=?,last_error=?,worker_id=NULL,
                   lease_until=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status='PROCESSING' AND worker_id=?""",
                (status, failure, error, event["id"], event["worker_id"]),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("EVENT_LEASE_LOST_AFTER_ACTION")
    return {"event_id": event["event_id"], "status": status, "failure_kind": failure, "result": result, "error": error}


def reconcile_threads(issue: str | None = None, operator: str | None = None) -> list[dict]:
    config = issue_thread.load_config(issue_thread.config_path())
    filters = ["thread_status='ACTIVE'", "lease_until < CURRENT_TIMESTAMP"]
    params: list[str] = []
    if issue:
        filters.append("issue_key=?"); params.append(issue)
    if operator:
        filters.append("operator_id=?"); params.append(operator)
    with issue_thread.connect() as conn:
        rows = conn.execute("SELECT * FROM code_inspector_thread WHERE " + " AND ".join(filters), params).fetchall()
    results = []
    for item in rows:
        runtime_status = "unknown"
        error = None
        try:
            runtime_status = issue_thread.runtime_call(
                config, lambda runtime: runtime.status(item["thread_id"]), retry_safe=True,
            )
        except Exception as exc:
            error = str(exc)[:1000]
        with issue_thread.connect() as conn:
            domain = ReviewRepository(conn).issue(item["issue_key"])
            conn.execute(
                """UPDATE code_inspector_thread SET thread_status='PAUSED',issue_status=?,worker_id=NULL,
                   lease_until=NULL,heartbeat_at=CURRENT_TIMESTAMP,error_code='STALE_ACTIVE_AMBIGUOUS',
                   error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (domain["status"], f"runtime_status={runtime_status}; {error or 'manual reconcile required; action was not replayed'}", item["id"]),
            )
            audit(conn, "runtime.reconcile-thread", item["thread_id"], True, f"status={runtime_status}")
        results.append({"issue": item["issue_key"], "operator": item["operator_id"], "thread": item["thread_id"], "runtime_status": runtime_status, "result": "PAUSED_AMBIGUOUS"})
    return results


def reconcile(issue: str | None = None, operator: str | None = None) -> dict:
    config = issue_thread.load_config(issue_thread.config_path())
    threads = reconcile_threads(issue, operator)
    events = recover_stale_events(
        int(config["thread_runtime"]["leases"]["max_event_attempts"]), issue, operator,
    )
    return {"threads": threads, "events": events}


def dispatch_pending(*, recover: bool = True) -> list[dict]:
    config = issue_thread.load_config(issue_thread.config_path())
    if recover:
        reconcile()
    limit = int(config["thread_runtime"]["concurrency"]["max_active_issue_threads"])
    events = claim(limit)
    if not events:
        return []
    with ThreadPoolExecutor(max_workers=limit) as pool:
        futures = [pool.submit(process_event, event) for event in events]
        return [future.result() for future in as_completed(futures)]


def run_forever(interval: float | None = None) -> None:
    """Long-lived internal queue consumer; idle cycles never invoke an LLM."""
    config = issue_thread.load_config(issue_thread.config_path())
    interval = interval if interval is not None else float(
        config["thread_runtime"].get("supervisor", {}).get("poll_interval_seconds", 1)
    )
    if interval <= 0:
        raise ValueError("interval 必须大于 0")
    max_errors = int(config["thread_runtime"].get("supervisor", {}).get("max_consecutive_errors", 3))
    recovery_interval = float(
        config["thread_runtime"].get("supervisor", {}).get("recovery_interval_seconds", 60)
    )
    consecutive_errors = 0
    last_recovery = 0.0
    while True:
        try:
            now = time.monotonic()
            should_recover = now - last_recovery >= recovery_interval
            results = dispatch_pending(recover=should_recover)
            if should_recover:
                last_recovery = now
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            print(json.dumps({
                "status": "SUPERVISOR_ERROR", "attempt": consecutive_errors,
                "error": str(exc)[:1000],
            }, ensure_ascii=False), file=sys.stderr, flush=True)
            if consecutive_errors >= max_errors:
                raise RuntimeError("SUPERVISOR_MAX_CONSECUTIVE_ERRORS") from exc
            time.sleep(min(interval * (2 ** (consecutive_errors - 1)), 30))
            continue
        for result in results:
            print(json.dumps(result, ensure_ascii=False), flush=True)
        time.sleep(interval)


def retry_event(event_id: str, confirm: bool) -> dict:
    if not confirm:
        raise RuntimeError("CONFIRM_REQUIRED")
    with issue_thread.connect() as conn:
        row = conn.execute("SELECT * FROM code_inspector_event WHERE event_id=?", (event_id,)).fetchone()
        if not row:
            raise RuntimeError("EVENT_NOT_FOUND")
        if row["status"] != "FAILED" or row["failure_kind"] != "RETRYABLE":
            raise RuntimeError("ONLY_RETRYABLE_FAILED_EVENT_CAN_RETRY")
        conn.execute("UPDATE code_inspector_event SET status='PENDING',next_attempt_at=CURRENT_TIMESTAMP,last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        audit(conn, "runtime.retry-event", event_id, True)
    return {"event_id": event_id, "status": "PENDING"}


def pause_thread(issue: str, operator: str, confirm: bool) -> dict:
    if not confirm:
        raise RuntimeError("CONFIRM_REQUIRED")
    with issue_thread.connect() as conn:
        row = conn.execute("SELECT * FROM code_inspector_thread WHERE issue_key=? AND operator_id=?", (issue, operator)).fetchone()
        if not row:
            raise RuntimeError("MAPPING_NOT_FOUND")
        if row["thread_status"] == "ACTIVE":
            raise RuntimeError("ACTIVE_THREAD_REQUIRES_RECONCILE")
        conn.execute("UPDATE code_inspector_thread SET thread_status='PAUSED',next_action='manual',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        audit(conn, "runtime.pause-thread", row["thread_id"], True)
    return {"issue": issue, "operator": operator, "status": "PAUSED"}


def status(issue: str | None = None, operator: str | None = None, state: str | None = None) -> dict:
    threads = issue_thread.status(issue, operator)
    if state:
        threads = [row for row in threads if row["thread_status"] == state]
    filters, params = [], []
    if issue: filters.append("issue_key=?"); params.append(issue)
    if operator: filters.append("operator_id=?"); params.append(operator)
    sql = """SELECT event_id,issue_key,role,operator_id,agent_platform,runtime_backend,event_type,
             status,attempt_count,failure_kind,lease_until,created_at,last_error
             FROM code_inspector_event"""
    if filters: sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY id DESC LIMIT 100"
    with issue_thread.connect() as conn:
        events = [dict(row) for row in conn.execute(sql, params)]
    return {"threads": threads, "events": events}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("enqueue"); p.add_argument("--issue", required=True); p.add_argument("--operator", required=True); p.add_argument("--role", choices=["inspector", "developer"]); p.add_argument("--event", required=True); p.add_argument("--event-id"); p.add_argument("--activity-id", type=int); p.add_argument("--stage", type=int)
    sub.add_parser("dispatch-pending")
    p = sub.add_parser("run"); p.add_argument("--interval", type=float)
    p = sub.add_parser("status"); p.add_argument("--issue"); p.add_argument("--operator"); p.add_argument("--status")
    p = sub.add_parser("reconcile"); p.add_argument("--issue"); p.add_argument("--operator")
    p = sub.add_parser("retry-event"); p.add_argument("--event-id", required=True); p.add_argument("--confirm", action="store_true")
    p = sub.add_parser("pause-thread"); p.add_argument("--issue", required=True); p.add_argument("--operator", required=True); p.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "enqueue": result = enqueue(args.issue, args.operator, args.event, args.activity_id, args.stage, args.event_id, args.role)
        elif args.command == "dispatch-pending": result = dispatch_pending()
        elif args.command == "run":
            run_forever(args.interval)
            return 0
        elif args.command == "reconcile": result = reconcile(args.issue, args.operator)
        elif args.command == "retry-event": result = retry_event(args.event_id, args.confirm)
        elif args.command == "pause-thread": result = pause_thread(args.issue, args.operator, args.confirm)
        else: result = status(args.issue, args.operator, args.status)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
