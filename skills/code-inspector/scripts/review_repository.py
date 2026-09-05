"""Read-only Review Domain queries used by the execution runtime."""
from __future__ import annotations

import sqlite3


class ReviewRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def issue(self, issue_key: str) -> sqlite3.Row:
        row = self.conn.execute(
            """SELECT i.id, i.issue_key, i.status, t.project_path
               FROM review_issue i JOIN review_task t ON t.id=i.task_id
               WHERE i.issue_key=?""",
            (issue_key,),
        ).fetchone()
        if not row:
            raise RuntimeError("ISSUE_NOT_FOUND")
        return row

    def completed_stage_boundary(self, issue_id: int, last_compact_stage_no: int | None) -> int | None:
        row = self.conn.execute(
            """SELECT MAX(stage_no) AS stage_no FROM issue_stage
               WHERE issue_id=? AND plan_status='ACTIVE' AND status='APPROVED'""",
            (issue_id,),
        ).fetchone()
        stage_no = row["stage_no"] if row else None
        if stage_no is None or last_compact_stage_no == stage_no:
            return None
        next_stage = self.conn.execute(
            """SELECT 1 FROM issue_stage WHERE issue_id=? AND plan_status='ACTIVE'
               AND stage_no>? AND status IN ('PLANNED','IN_PROGRESS','PENDING_REVIEW') LIMIT 1""",
            (issue_id, stage_no),
        ).fetchone()
        return int(stage_no) if next_stage else None
