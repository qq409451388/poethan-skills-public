#!/usr/bin/env python3
"""Small-context multi-Issue event registry and dispatcher."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import issue_thread


def enqueue(issue: str, role: str, event_type: str, activity_id: int | None, stage: int | None, event_id: str | None) -> dict:
    supplied_event_id = event_id
    event_id = event_id or str(uuid.uuid4())
    identity = activity_id if activity_id is not None else supplied_event_id or f"stage={stage}"
    key = f"{issue}:{role}:{identity}:{event_type}"
    with issue_thread.connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO code_inspector_event(event_id,idempotency_key,issue_key,role,event_type,activity_id,stage_no)
               VALUES(?,?,?,?,?,?,?)""",
            (event_id, key, issue, role, event_type, activity_id, stage),
        )
        row = conn.execute("SELECT * FROM code_inspector_event WHERE idempotency_key=?", (key,)).fetchone()
    return dict(row)


def claim(limit: int) -> list[dict]:
    with issue_thread.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT e.* FROM code_inspector_event e
               WHERE e.status='PENDING' AND e.id=(
                 SELECT MIN(x.id) FROM code_inspector_event x
                 WHERE x.status='PENDING' AND x.issue_key=e.issue_key AND x.role=e.role
               ) ORDER BY e.created_at,e.id LIMIT ?""", (limit,),
        ).fetchall()
        ids = [row["id"] for row in rows]
        if ids:
            conn.execute(
                f"UPDATE code_inspector_event SET status='PROCESSING',updated_at=CURRENT_TIMESTAMP WHERE id IN ({','.join('?' for _ in ids)})",
                ids,
            )
        conn.commit()
    return [dict(row) for row in rows]


def process_event(event: dict) -> dict:
    try:
        result = issue_thread.dispatch(event["issue_key"], event["role"], event["event_type"])
        status, error = "DONE", None
    except Exception as exc:
        result, status, error = None, "FAILED", str(exc)[:1000]
    with issue_thread.connect() as conn:
        conn.execute(
            "UPDATE code_inspector_event SET status=?,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, error, event["id"]),
        )
    return {"event_id": event["event_id"], "status": status, "result": result, "error": error}


def dispatch_pending() -> list[dict]:
    config = issue_thread.load_config(issue_thread.config_path())
    limit = int(config["thread_runtime"]["concurrency"]["max_active_issue_threads"])
    events = claim(limit)
    if not events:
        return []
    with ThreadPoolExecutor(max_workers=limit) as pool:
        futures = [pool.submit(process_event, event) for event in events]
        return [future.result() for future in as_completed(futures)]


def status() -> dict:
    with issue_thread.connect() as conn:
        events = [dict(row) for row in conn.execute(
            "SELECT event_id,issue_key,role,event_type,status,created_at,error_message FROM code_inspector_event ORDER BY id DESC LIMIT 50"
        )]
    return {"threads": issue_thread.status(), "events": events}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("enqueue"); p.add_argument("--issue", required=True); p.add_argument("--role", required=True, choices=["inspector", "developer"]); p.add_argument("--event", required=True); p.add_argument("--event-id"); p.add_argument("--activity-id", type=int); p.add_argument("--stage", type=int)
    sub.add_parser("dispatch-pending")
    sub.add_parser("status")
    args = parser.parse_args()
    try:
        if args.command == "enqueue": result = enqueue(args.issue, args.role, args.event, args.activity_id, args.stage, args.event_id)
        elif args.command == "dispatch-pending": result = dispatch_pending()
        else: result = status()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
