"""Derive the current actionable Issue projection without loading history bodies."""
from __future__ import annotations

import sqlite3
from typing import Any

from runtime_permissions import runtime_issue_actions


TERMINAL_OR_WAITING = {
    "ON_HOLD", "BLOCKED", "HUMAN_CONFIRMATION_REQUIRED", "CONFIRMED", "CANCELLED",
}

# 进入 REDESIGN_REQUIRED 的活动标记：通用状态变更或 stage-review 的整案失效。
REDESIGN_ENTRY_SUBQUERY = (
    "SELECT entry.id FROM issue_activity entry "
    "WHERE entry.issue_id = i.id "
    "  AND entry.result_status = 'REDESIGN_REQUIRED' "
    "  AND entry.activity_type IN ('STATUS_CHANGED','STAGE_REJECTED') "
    "ORDER BY entry.id DESC LIMIT 1"
)


def _pending_action(
    status: str, role: str, stage: dict[str, Any] | None, has_plan: bool,
    redesign_guidance_required: bool = False,
) -> str | None:
    if status in TERMINAL_OR_WAITING:
        return None
    if role == "developer":
        if status == "DESIGN_REQUIRED":
            return "submit_design"
        if status == "REDESIGN_REQUIRED":
            # Inspector 修订架构指导前，Developer 只能阅读和讨论。
            return None if redesign_guidance_required else "submit_design"
        if status == "IN_PROGRESS":
            if stage and stage["status"] == "IN_PROGRESS":
                return "implement_stage"
            if stage:
                return None
            if has_plan:
                return "submit_final_implementation"
            return "implement_issue"
        if status == "PROPOSED":
            return "triage_or_implement_issue"
        return None
    if role == "inspector":
        if status == "DESIGN_PENDING_REVIEW":
            return "review_design"
        if status == "IMPLEMENTED_PENDING_REVIEW":
            return "review_implementation"
        if status == "INSPECTOR_CONFIRMATION_REQUIRED":
            return "answer_inspector_confirmation"
        if status == "REDESIGN_REQUIRED" and redesign_guidance_required:
            return "revise_design_guidance"
        if status == "IN_PROGRESS" and stage and stage["status"] == "PENDING_REVIEW":
            return "review_stage"
        return None
    return None


def current_projection(conn: sqlite3.Connection, issue_key: str, role: str) -> dict[str, Any]:
    # 单条 SQLite 语句自身就是 read snapshot。即使调用方没有显式事务，Issue、Stage
    # 与 Plan 也不会来自不同版本。
    row = conn.execute(
        f"""SELECT i.id, i.issue_key, i.status, i.projection_revision,
                  s.id AS stage_id, s.plan_no, s.stage_no, s.title AS stage_title,
                  s.status AS stage_status, s.prepared_at AS stage_prepared_at,
                  s.governance_version AS stage_governance_version,
                  EXISTS(
                    SELECT 1 FROM issue_stage p
                    WHERE p.issue_id=i.id AND p.plan_status='ACTIVE'
                  ) AS has_active_plan,
                  NOT EXISTS(
                    SELECT 1 FROM issue_stage p
                    WHERE p.issue_id=i.id AND p.plan_status='ACTIVE' AND p.status!='APPROVED'
                  ) AS active_plan_complete,
                  ({REDESIGN_ENTRY_SUBQUERY}) AS redesign_entry_activity_id,
                  EXISTS(
                    SELECT 1 FROM issue_activity a
                    WHERE a.issue_id = i.id AND a.activity_type = 'DESIGN_REQUESTED'
                      AND a.id > COALESCE(({REDESIGN_ENTRY_SUBQUERY}), 0)
                  ) AS redesign_guidance_refreshed
           FROM review_issue i
           LEFT JOIN issue_stage s ON s.id=(
             SELECT candidate.id FROM issue_stage candidate
             WHERE candidate.issue_id=i.id AND candidate.plan_status='ACTIVE'
               AND candidate.status IN ('IN_PROGRESS','PENDING_REVIEW','PLANNED')
             ORDER BY CASE candidate.status
                        WHEN 'PENDING_REVIEW' THEN 0
                        WHEN 'IN_PROGRESS' THEN 1
                        ELSE 2
                      END, candidate.stage_no
             LIMIT 1
           )
           WHERE i.issue_key=?""",
        (issue_key,),
    ).fetchone()
    if not row:
        raise RuntimeError("ISSUE_NOT_FOUND")
    stage = None
    if row["stage_id"] is not None:
        stage = {
            "id": row["stage_id"], "plan_no": row["plan_no"], "stage_no": row["stage_no"],
            "title": row["stage_title"], "status": row["stage_status"],
            "prepared_at": row["stage_prepared_at"],
            "governance_version": row["stage_governance_version"],
        }
    has_plan = bool(row["has_active_plan"])
    # 方向失败进入重设计后、Inspector 尚未提交修订版 design-request 时，Developer 的
    # design-submit 被阻止；旧数据没有重设计起点标记时保持兼容（不设门槛）。
    redesign_guidance_required = bool(
        row["status"] == "REDESIGN_REQUIRED"
        and row["redesign_entry_activity_id"] is not None
        and not row["redesign_guidance_refreshed"]
    )
    action = _pending_action(row["status"], role, stage, has_plan, redesign_guidance_required)
    permission_state = {
        "issue_status": row["status"], "role": role,
        "current_stage_status": row["stage_status"],
        "current_stage_prepared": bool(row["stage_prepared_at"]),
        "current_stage_governance_version": row["stage_governance_version"],
        "has_active_plan": has_plan,
        "active_plan_complete": bool(row["active_plan_complete"]),
        "redesign_guidance_required": redesign_guidance_required,
    }
    permitted_actions, exception_actions = runtime_issue_actions(permission_state)
    return {
        "issue_id": row["id"],
        "issue_key": issue_key,
        "issue_status": row["status"],
        "projection_revision": int(row["projection_revision"]),
        "pending_action": action,
        "permitted_actions": permitted_actions,
        "exception_actions": exception_actions,
        "current_stage": stage,
        "has_active_plan": has_plan,
        "redesign_guidance_required": redesign_guidance_required,
    }
