"""Derive the current actionable Issue projection without loading history bodies."""
from __future__ import annotations

import sqlite3
from typing import Any


TERMINAL_OR_WAITING = {
    "ON_HOLD", "BLOCKED", "HUMAN_CONFIRMATION_REQUIRED", "CONFIRMED", "CANCELLED",
}


def _current_stage(conn: sqlite3.Connection, issue_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT id, plan_no, stage_no, title, status
           FROM issue_stage
           WHERE issue_id=? AND plan_status='ACTIVE'
             AND status IN ('IN_PROGRESS','PENDING_REVIEW','PLANNED')
           ORDER BY CASE status
                      WHEN 'PENDING_REVIEW' THEN 0
                      WHEN 'IN_PROGRESS' THEN 1
                      ELSE 2
                    END, stage_no
           LIMIT 1""",
        (issue_id,),
    ).fetchone()


def _has_active_plan(conn: sqlite3.Connection, issue_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM issue_stage WHERE issue_id=? AND plan_status='ACTIVE' LIMIT 1",
        (issue_id,),
    ).fetchone() is not None


def _action(status: str, role: str, stage: sqlite3.Row | None, has_plan: bool) -> tuple[str | None, list[str]]:
    if status in TERMINAL_OR_WAITING:
        return None, []
    if role == "developer":
        if status in {"DESIGN_REQUIRED", "REDESIGN_REQUIRED"}:
            return "submit_design", ["design-submit", "discussion-append", "issue-update-status"]
        if status == "IN_PROGRESS":
            if stage and stage["status"] == "IN_PROGRESS":
                return "implement_stage", ["stage-prepare", "stage-submit", "discussion-append", "candidate-submit"]
            if stage:
                return None, []
            if has_plan:
                return "submit_final_implementation", ["implementation-submit", "discussion-append", "candidate-submit"]
            return "implement_issue", ["implementation-submit", "discussion-append", "candidate-submit", "issue-update-status"]
        if status == "PROPOSED":
            return "triage_or_implement_issue", ["implementation-submit", "discussion-append", "candidate-submit", "issue-update-status"]
        return None, []
    if role == "inspector":
        if status == "DESIGN_PENDING_REVIEW":
            return "review_design", ["design-preview", "design-review", "discussion-append", "design-choice-record"]
        if status == "IMPLEMENTED_PENDING_REVIEW":
            return "review_implementation", ["activity-get", "activity-append", "discussion-append", "issue-update-status"]
        if status == "INSPECTOR_CONFIRMATION_REQUIRED":
            return "answer_inspector_confirmation", ["activity-append", "discussion-append", "issue-update-status"]
        if status == "IN_PROGRESS" and stage and stage["status"] == "PENDING_REVIEW":
            return "review_stage", ["stage-review", "discussion-append", "activity-get", "stage-history-get"]
        return None, []
    return None, []


def current_projection(conn: sqlite3.Connection, issue_key: str, role: str) -> dict[str, Any]:
    issue = conn.execute(
        "SELECT id, issue_key, status, projection_revision FROM review_issue WHERE issue_key=?",
        (issue_key,),
    ).fetchone()
    if not issue:
        raise RuntimeError("ISSUE_NOT_FOUND")
    stage = _current_stage(conn, issue["id"])
    has_plan = _has_active_plan(conn, issue["id"])
    action, allowed_actions = _action(issue["status"], role, stage, has_plan)
    return {
        "issue_id": issue["id"],
        "issue_key": issue_key,
        "issue_status": issue["status"],
        "projection_revision": int(issue["projection_revision"]),
        "pending_action": action,
        "allowed_actions": allowed_actions,
        "current_stage": dict(stage) if stage else None,
        "has_active_plan": has_plan,
    }
