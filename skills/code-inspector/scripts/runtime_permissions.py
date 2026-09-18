"""Runtime 唯一持有的 Issue 动作与状态权限。

Projection 只消费本模块，不再维护第二套动作表；命令处理器也用同一份规则校验写操作。

REDESIGN_REQUIRED 门槛：方向失败进入重设计后，state 中的 redesign_guidance_required 为真时，
Developer 的 design-submit 被拒绝，直到 Inspector 用 design-request 提交修订版架构指导。
"""
from __future__ import annotations

from typing import Any


ALLOWED_STATUS_BY_AGENT = {
    "inspector": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "REDESIGN_REQUIRED", "CONFIRMED", "CANCELLED"},
    "developer": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW"},
    # Human 可以纠正普通状态；Human 确认状态仍只能通过专用原子命令进入和离开。
    "human": {
        "PROPOSED", "DESIGN_REQUIRED", "DESIGN_PENDING_REVIEW", "IN_PROGRESS", "ON_HOLD",
        "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW",
        "REDESIGN_REQUIRED", "CONFIRMED", "CANCELLED",
    },
}


ALLOWED_TRANSITIONS = {
    "PROPOSED": {"inspector": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "CONFIRMED", "CANCELLED"}, "developer": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW"}, "human": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW", "CANCELLED"}},
    "IN_PROGRESS": {"inspector": {"ON_HOLD", "BLOCKED", "CANCELLED"}, "developer": {"ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW"}, "human": {"ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW", "CANCELLED"}},
    "ON_HOLD": {"inspector": {"IN_PROGRESS", "BLOCKED", "CANCELLED"}, "developer": {"IN_PROGRESS", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED"}, "human": {"IN_PROGRESS", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "CANCELLED"}},
    "BLOCKED": {"inspector": {"IN_PROGRESS", "ON_HOLD", "CANCELLED"}, "developer": {"IN_PROGRESS", "ON_HOLD", "INSPECTOR_CONFIRMATION_REQUIRED"}, "human": {"IN_PROGRESS", "ON_HOLD", "INSPECTOR_CONFIRMATION_REQUIRED", "CANCELLED"}},
    "INSPECTOR_CONFIRMATION_REQUIRED": {"inspector": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "CANCELLED"}, "developer": set(), "human": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "CANCELLED"}},
    "IMPLEMENTED_PENDING_REVIEW": {"inspector": {"IN_PROGRESS", "CONFIRMED", "REDESIGN_REQUIRED", "ON_HOLD", "BLOCKED", "CANCELLED"}, "developer": set(), "human": {"IN_PROGRESS", "CONFIRMED", "REDESIGN_REQUIRED", "ON_HOLD", "BLOCKED", "CANCELLED"}},
    "HUMAN_CONFIRMATION_REQUIRED": {"inspector": set(), "developer": set(), "human": set()},
    "DESIGN_REQUIRED": {"inspector": {"CANCELLED"}, "developer": set(), "human": {"CANCELLED"}},
    "DESIGN_PENDING_REVIEW": {"inspector": {"CANCELLED"}, "developer": set(), "human": {"CANCELLED"}},
    "REDESIGN_REQUIRED": {"inspector": {"ON_HOLD", "BLOCKED", "CANCELLED"}, "developer": {"ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED"}, "human": {"ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "CANCELLED"}},
    "CONFIRMED": {"inspector": set(), "developer": set(), "human": set()},
    "CANCELLED": {"inspector": set(), "developer": set(), "human": set()},
}


HUMAN_ESCALATION_SOURCES = {
    "PROPOSED", "DESIGN_REQUIRED", "DESIGN_PENDING_REVIEW", "IN_PROGRESS",
    "ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED",
    "IMPLEMENTED_PENDING_REVIEW", "REDESIGN_REQUIRED",
}


ALLOWED_ACTIVITY_BY_AGENT = {
    "inspector": {
        "ISSUE_CREATED", "EVIDENCE_ADDED", "REVIEW_APPROVED", "REVIEW_REJECTED",
        "DESIGN_GUIDANCE", "INSPECTOR_CONFIRMATION_PROVIDED",
        "VERIFICATION_PASSED", "VERIFICATION_FAILED", "VERIFICATION_EVIDENCE_ADDED",
        "STATUS_CHANGED", "COMMENT_ADDED",
    },
    "developer": {"IMPLEMENTATION_SUBMITTED", "REDESIGN_SUBMITTED", "STATUS_CHANGED", "COMMENT_ADDED"},
    "human": {
        "ISSUE_CREATED", "EVIDENCE_ADDED", "DESIGN_GUIDANCE", "IMPLEMENTATION_SUBMITTED",
        "REVIEW_APPROVED", "REVIEW_REJECTED", "REDESIGN_SUBMITTED", "INSPECTOR_CONFIRMATION_PROVIDED",
        "VERIFICATION_PASSED", "VERIFICATION_FAILED", "VERIFICATION_EVIDENCE_ADDED",
        "STATUS_CHANGED", "COMMENT_ADDED",
    },
}


ACTION_ROLES = {
    "discussion-append": {"inspector", "developer", "human"},
    "activity-append": set(ALLOWED_ACTIVITY_BY_AGENT),
    "design-request": {"inspector", "human"},
    "design-submit": {"developer", "human"},
    "design-choice-record": {"inspector"},
    "design-review": {"inspector", "human"},
    "stage-prepare": {"developer", "human"},
    "stage-submit": {"developer", "human"},
    "stage-review": {"inspector", "human"},
    "implementation-submit": {"developer", "human"},
    "issue-set-difficulty": {"inspector", "human"},
    "issue-update-status": {"inspector", "developer", "human"},
    "human-escalate": {"inspector"},
}


def status_targets(status: str, role: str) -> set[str]:
    """返回 Runtime 通用状态命令当前真正接受的目标状态。"""
    if role == "human" and status != "HUMAN_CONFIRMATION_REQUIRED":
        return ALLOWED_STATUS_BY_AGENT[role] - {"HUMAN_CONFIRMATION_REQUIRED"}
    return set(ALLOWED_TRANSITIONS.get(status, {}).get(role, set()))


def runtime_issue_actions(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    """返回当前 Issue 状态下 Runtime 接受的普通与异常写动作。"""
    status = str(state["issue_status"])
    role = str(state["role"])
    stage_status = state.get("current_stage_status")
    stage_prepared = bool(state.get("current_stage_prepared"))
    stage_governance_version = int(state.get("current_stage_governance_version") or 1)
    has_active_plan = bool(state.get("has_active_plan"))
    active_plan_complete = bool(state.get("active_plan_complete"))

    actions: list[str] = []
    if role in ACTION_ROLES["discussion-append"]:
        actions.append("discussion-append")
    if role in ACTION_ROLES["activity-append"]:
        actions.append("activity-append")

    if role in ACTION_ROLES["design-request"] and status in {"PROPOSED", "IN_PROGRESS", "REDESIGN_REQUIRED"}:
        actions.append("design-request")
    if (
        role in ACTION_ROLES["design-submit"]
        and status in {"DESIGN_REQUIRED", "REDESIGN_REQUIRED"}
        and not (status == "REDESIGN_REQUIRED" and state.get("redesign_guidance_required"))
    ):
        actions.append("design-submit")
    if status == "DESIGN_PENDING_REVIEW":
        if role in ACTION_ROLES["design-choice-record"]:
            actions.append("design-choice-record")
        if role in ACTION_ROLES["design-review"]:
            actions.append("design-review")

    if status == "IN_PROGRESS" and stage_status == "IN_PROGRESS":
        if role in ACTION_ROLES["stage-prepare"]:
            actions.append("stage-prepare")
        if role in ACTION_ROLES["stage-submit"] and (stage_prepared or stage_governance_version < 2):
            actions.append("stage-submit")
    if role in ACTION_ROLES["stage-review"] and status == "IN_PROGRESS" and stage_status == "PENDING_REVIEW":
        actions.append("stage-review")

    implementation_ready = status in {"PROPOSED", "IN_PROGRESS"} and (
        not has_active_plan or active_plan_complete
    )
    if role in ACTION_ROLES["implementation-submit"] and implementation_ready:
        actions.append("implementation-submit")

    if status_targets(status, role):
        actions.append("issue-update-status")

    exception_actions = []
    if role in ACTION_ROLES["human-escalate"] and status in HUMAN_ESCALATION_SOURCES:
        exception_actions.append("human-escalate")
    return actions, exception_actions


def issue_action_permitted(action: str, state: dict[str, Any]) -> bool:
    actions, exception_actions = runtime_issue_actions(state)
    return action in actions or action in exception_actions
