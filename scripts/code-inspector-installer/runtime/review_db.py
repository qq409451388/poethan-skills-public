#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(os.path.expandvars(os.path.expanduser(
    os.environ.get("AGENT_REVIEW_DB", "~/.agent-review/data/review.db")
)))

SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
BENEFIT_WEIGHT = {"high": 3, "medium": 2, "low": 1}
DIMENSION_WEIGHT = {
    "functional_correctness": 7,
    "data_security": 6,
    "stability_concurrency": 5,
    "performance": 4,
    "architecture_extensibility": 3,
    "code_quality": 2,
    "test_observability": 1,
}
COST_WEIGHT = {"low": 1, "medium": 2, "high": 3, "extreme": 4}
CONFIDENCE_WEIGHT = {"high": 3, "medium": 2, "low": 1}

ALLOWED_STATUS_BY_AGENT = {
    "inspector": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "REDESIGN_REQUIRED", "CONFIRMED", "CANCELLED"},
    "developer": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW"},
    # Human 对普通 Issue 状态具有最高管理解释权。HUMAN_CONFIRMATION_REQUIRED 仍只能
    # 由 inspector 的 human-escalate 进入，并由 human-confirmation-resolve 离开。
    "human": {
        "PROPOSED", "DESIGN_REQUIRED", "DESIGN_PENDING_REVIEW", "IN_PROGRESS", "ON_HOLD",
        "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW",
        "REDESIGN_REQUIRED", "CONFIRMED", "CANCELLED",
    },
}

# 核心设计与 Human 确认流转只能由专用原子命令执行，不能通过通用状态命令绕过活动记录。
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

TASK_STATUSES = {"PENDING", "IN_PROGRESS", "ON_HOLD", "BLOCKED", "CLOSED", "CANCELLED"}
TASK_TYPES = {"REVIEW", "CONTINUOUS"}
STAGE_STATUSES = {"PLANNED", "IN_PROGRESS", "PENDING_REVIEW", "APPROVED", "SUPERSEDED"}
TASK_TRANSITIONS = {
    "PENDING": {"IN_PROGRESS", "ON_HOLD", "BLOCKED", "CLOSED", "CANCELLED"},
    "IN_PROGRESS": {"ON_HOLD", "BLOCKED", "CLOSED", "CANCELLED"},
    "ON_HOLD": {"PENDING", "IN_PROGRESS", "BLOCKED", "CLOSED", "CANCELLED"},
    "BLOCKED": {"PENDING", "IN_PROGRESS", "ON_HOLD", "CLOSED", "CANCELLED"},
    "CLOSED": set(),
    "CANCELLED": set(),
}

ALLOWED_DIMENSIONS = set(DIMENSION_WEIGHT)
ALLOWED_SEVERITIES = set(SEVERITY_WEIGHT)
ALLOWED_BENEFITS = set(BENEFIT_WEIGHT)
ALLOWED_COSTS = set(COST_WEIGHT)
ALLOWED_CONFIDENCE = set(CONFIDENCE_WEIGHT)
ALLOWED_DISPOSITIONS = {
    "immediate_fix", "current_iteration", "near_term_iteration", "special_governance",
    "opportunistic_fix", "observe", "defer", "business_confirmation",
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
        "STATUS_CHANGED", "COMMENT_ADDED"
    },
}
ATOMIC_ACTIVITY_TYPES = {
    "DESIGN_REQUESTED", "DESIGN_SUBMITTED", "DESIGN_APPROVED", "DESIGN_REJECTED",
    "STAGE_PLAN_CREATED", "STAGE_SCOPE_DECLARED", "STAGE_SUBMITTED", "STAGE_APPROVED", "STAGE_REJECTED",
    "STAGE_PLAN_SUPERSEDED",
    "HUMAN_CONFIRMATION_REQUESTED", "HUMAN_CONFIRMATION_PROVIDED",
}
ALLOWED_ACTIVITY_TYPES = set().union(*ALLOWED_ACTIVITY_BY_AGENT.values(), ATOMIC_ACTIVITY_TYPES)

def configured_db_path() -> Path:
    if os.environ.get("AGENT_REVIEW_DB"):
        return DEFAULT_DB_PATH
    config_dir = Path(os.path.expandvars(os.path.expanduser(
        os.environ.get("AGENT_REVIEW_HOME", "~/.agent-review")
    ))) / "config"
    runtime_config = config_dir / "runtime.json"
    if runtime_config.exists():
        try:
            config = json.loads(runtime_config.read_text(encoding="utf-8"))
            return Path(os.path.expandvars(os.path.expanduser(config["database"]))).resolve()
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise RuntimeError(f"配置文件无效: {runtime_config}: {exc}") from exc
    return DEFAULT_DB_PATH

def configured_home() -> Path:
    return Path(os.path.expandvars(os.path.expanduser(
        os.environ.get("AGENT_REVIEW_HOME", "~/.agent-review")
    ))).resolve()

def validate_actor_binding(args: argparse.Namespace) -> None:
    """阻止直接调用底层工具时伪造逻辑身份或角色。"""
    if args.agent == "human":
        return
    if not args.operator_id:
        raise PermissionError("developer/inspector 必须使用安装器生成的角色工具入口")
    bindings_path = configured_home() / "config" / "agent-bindings.json"
    if not bindings_path.exists():
        raise RuntimeError(f"角色绑定配置不存在: {bindings_path}，请重新执行安装")
    try:
        bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"角色绑定配置无效: {bindings_path}: {exc}") from exc
    binding = bindings.get(args.operator_id)
    if not binding:
        raise PermissionError(f"未注册的逻辑身份: {args.operator_id}")
    if binding.get("role") != args.agent:
        raise PermissionError(
            f"逻辑身份 {args.operator_id} 绑定角色为 {binding.get('role')}，不能作为 {args.agent} 使用"
        )

def connect() -> sqlite3.Connection:
    db_path = configured_db_path()
    if not db_path.exists():
        raise RuntimeError(f"数据库不存在: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn

def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)

def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)

def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))

def audit(conn: sqlite3.Connection, agent: str, action: str, resource_type: str,
          resource_id: str | None, success: bool, detail: str = "") -> None:
    conn.execute(
        """INSERT INTO agent_audit_log(agent_id, action, resource_type, resource_id, success, detail)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (agent, action, resource_type, resource_id, 1 if success else 0, detail),
    )

def require_agent(agent: str) -> None:
    if agent not in {"inspector", "developer", "human"}:
        raise PermissionError(f"未知 agent: {agent}")

def require_choice(value: str, allowed: set[str], field: str) -> None:
    if value not in allowed:
        raise ValueError(f"{field} 无效: {value}")

def operator_type(agent: str) -> str:
    return {"inspector": "INSPECTOR_AGENT", "developer": "DEVELOPMENT_AGENT", "human": "HUMAN"}[agent]

def actor_id(args: argparse.Namespace) -> str:
    return args.operator_id or args.agent

def issue_row(conn: sqlite3.Connection, issue_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, issue_key, status, current_attempt_no FROM review_issue WHERE issue_key = ?",
        (issue_key,),
    ).fetchone()
    if not row:
        raise KeyError(f"问题不存在: {issue_key}")
    return row

def active_stage_plan_no(conn: sqlite3.Connection, issue_id: int) -> int | None:
    """返回最新未被废弃的计划；全阶段 APPROVED 的计划仍然是 active plan。"""
    row = conn.execute(
        """SELECT plan_no
           FROM issue_stage
           WHERE issue_id = ?
             AND plan_status = 'ACTIVE'
           GROUP BY plan_no
           ORDER BY plan_no DESC LIMIT 1""",
        (issue_id,),
    ).fetchone()
    return int(row["plan_no"]) if row else None

def supersede_active_stage_plan(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    row: sqlite3.Row,
    reason: str,
) -> int | None:
    """废弃 active plan；已批准时间、意见和活动仍完整保留。"""
    plan_no = active_stage_plan_no(conn, row["id"])
    if plan_no is None:
        return None
    affected = conn.execute(
        """SELECT stage_no FROM issue_stage
           WHERE issue_id = ? AND plan_no = ?
             AND plan_status = 'ACTIVE'
           ORDER BY stage_no""",
        (row["id"], plan_no),
    ).fetchall()
    if not affected:
        return None
    stage_nos = [item["stage_no"] for item in affected]
    conn.execute(
        """UPDATE issue_stage
           SET plan_status = 'SUPERSEDED',
               status = CASE WHEN status = 'APPROVED' THEN status ELSE 'SUPERSEDED' END,
               review_comment = COALESCE(review_comment, ?),
               updated_at = CURRENT_TIMESTAMP
           WHERE issue_id = ? AND plan_no = ?
             AND plan_status = 'ACTIVE'""",
        (reason, row["id"], plan_no),
    )
    conn.execute(
        """INSERT INTO issue_activity(
            issue_id, attempt_no, activity_type, operator_type, operator_id,
            content, result_status, metadata_json
        ) VALUES (?, ?, 'STAGE_PLAN_SUPERSEDED', ?, ?, ?, 'SUPERSEDED', ?)""",
        (
            row["id"], row["current_attempt_no"], operator_type(args.agent), actor_id(args),
            reason, dumps({"plan_no": plan_no, "stage_nos": stage_nos}),
        ),
    )
    return plan_no

def task_fingerprint(project_path: str, task_type: str, review_level: str, objective: str,
                     review_scope: str, baseline_ref: str | None) -> str:
    if task_type == "CONTINUOUS":
        payload = {
            "project_path": project_path,
            "task_type": task_type,
            "objective": objective.strip(),
            "review_scope": review_scope.strip(),
        }
    else:
        # REVIEW 必须保留升级前的精确算法，才能继续命中历史 scope_fingerprint。
        payload = {
            "project_path": project_path,
            "review_level": review_level,
            "objective": objective.strip(),
            "review_scope": review_scope.strip(),
            "baseline_ref": (baseline_ref or "").strip(),
        }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

def issue_dedupe_key(payload: dict[str, Any]) -> str:
    if payload.get("dedupe_key"):
        return str(payload["dedupe_key"])
    evidence = payload.get("evidence", [])
    paths = sorted(item.get("file_path", "") for item in evidence if isinstance(item, dict))
    basis = {
        "dimension": payload["dimension"],
        "title": " ".join(payload["title"].lower().split()),
        "paths": paths,
    }
    return hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

def validate_issue_payload(payload: dict[str, Any]) -> None:
    required = {
        "title", "dimension", "severity", "remediation_benefit", "remediation_cost",
        "disposition", "confidence", "description", "facts", "rationale",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"问题缺少字段: {', '.join(missing)}")
    require_choice(payload["dimension"], ALLOWED_DIMENSIONS, "dimension")
    require_choice(payload["severity"], ALLOWED_SEVERITIES, "severity")
    require_choice(payload["remediation_benefit"], ALLOWED_BENEFITS, "remediation_benefit")
    require_choice(payload["remediation_cost"], ALLOWED_COSTS, "remediation_cost")
    require_choice(payload["disposition"], ALLOWED_DISPOSITIONS, "disposition")
    require_choice(payload["confidence"], ALLOWED_CONFIDENCE, "confidence")

def task_create(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以创建任务")

    cwd = Path.cwd().resolve()
    project_name = cwd.name
    task_key = args.task_key or f"RT-{uuid.uuid4().hex[:8].upper()}"
    review_level = args.review_level
    review_scope = args.review_scope
    require_choice(args.task_type, TASK_TYPES, "task_type")
    fingerprint = task_fingerprint(
        str(cwd), args.task_type, review_level, args.objective, review_scope, args.baseline_ref
    ) if review_level and review_scope else None

    with connect() as conn:
        conn.execute(
            """INSERT INTO review_task(
                task_key, project_name, project_path, title, objective, review_level, review_scope,
                baseline_ref, scope_fingerprint, task_type, status, started_at, remark
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)""",
            (task_key, project_name, str(cwd), args.title, args.objective, review_level, review_scope,
             args.baseline_ref, fingerprint, args.task_type, args.started_at, args.remark),
        )
        audit(conn, actor_id(args), "task.create", "review_task", task_key, True)
    print_json({"task_key": task_key, "task_type": args.task_type, "project_name": project_name, "project_path": str(cwd)})

def task_resolve(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以创建或复用任务")
    cwd = Path.cwd().resolve()
    require_choice(args.task_type, TASK_TYPES, "task_type")
    fingerprint = task_fingerprint(
        str(cwd), args.task_type, args.review_level, args.objective, args.review_scope, args.baseline_ref
    )
    with connect() as conn:
        if args.task_key:
            explicit = conn.execute(
                """SELECT id, task_key, project_path, task_type, status, current_version
                   FROM review_task WHERE task_key = ?""",
                (args.task_key,),
            ).fetchone()
            if explicit:
                if explicit["project_path"] != str(cwd):
                    raise RuntimeError(f"task_key {args.task_key} 属于其他项目路径")
                if explicit["task_type"] != args.task_type:
                    raise RuntimeError(
                        f"task_type 创建后不可修改: {explicit['task_type']} != {args.task_type}"
                    )
                if explicit["status"] in {"CLOSED", "CANCELLED"}:
                    raise RuntimeError(f"task_key {args.task_key} 已处于 {explicit['status']}，不能复用")
                if args.task_type == "CONTINUOUS" and args.baseline_ref is not None:
                    conn.execute(
                        """UPDATE review_task SET baseline_ref = ?, updated_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (args.baseline_ref, explicit["id"]),
                    )
                audit(conn, actor_id(args), "task.resolve", "review_task", explicit["task_key"], True, "reused_explicit")
                print_json({"task_key": explicit["task_key"], "task_type": explicit["task_type"], "created": False,
                            "status": explicit["status"], "current_version": explicit["current_version"]})
                return
        row = conn.execute(
            """SELECT id, task_key, task_type, status, current_version
               FROM review_task
               WHERE project_path = ? AND task_type = ? AND scope_fingerprint = ?
                 AND (status IN ('PENDING', 'IN_PROGRESS')
                      OR (? = 'CONTINUOUS' AND status IN ('ON_HOLD', 'BLOCKED')))
               ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (str(cwd), args.task_type, fingerprint, args.task_type),
        ).fetchone()
        if row:
            if args.task_type == "CONTINUOUS" and args.baseline_ref is not None:
                conn.execute(
                    "UPDATE review_task SET baseline_ref = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (args.baseline_ref, row["id"]),
                )
            audit(conn, actor_id(args), "task.resolve", "review_task", row["task_key"], True, "reused")
            print_json({"task_key": row["task_key"], "task_type": row["task_type"], "created": False,
                        "status": row["status"], "current_version": row["current_version"]})
            return
        legacy = conn.execute(
            """SELECT id, task_key, status, current_version
               FROM review_task
               WHERE project_path = ? AND title = ? AND objective = ?
                 AND task_type = 'REVIEW' AND scope_fingerprint IS NULL
                 AND status IN ('PENDING', 'IN_PROGRESS')
               ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (str(cwd), args.title, args.objective),
        ).fetchone() if args.task_type == "REVIEW" else None
        if legacy:
            conn.execute(
                """UPDATE review_task
                   SET review_level = ?, review_scope = ?, baseline_ref = ?, scope_fingerprint = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (args.review_level, args.review_scope, args.baseline_ref, fingerprint, legacy["id"]),
            )
            audit(conn, actor_id(args), "task.resolve", "review_task", legacy["task_key"], True, "reused_legacy")
            print_json({"task_key": legacy["task_key"], "task_type": "REVIEW", "created": False,
                        "status": legacy["status"], "current_version": legacy["current_version"]})
            return
        task_key = args.task_key or f"RT-{uuid.uuid4().hex[:8].upper()}"
        conn.execute(
            """INSERT INTO review_task(
                task_key, project_name, project_path, title, objective, review_level, review_scope,
                baseline_ref, scope_fingerprint, task_type, status, remark
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)""",
            (task_key, cwd.name, str(cwd), args.title, args.objective, args.review_level,
             args.review_scope, args.baseline_ref, fingerprint, args.task_type, args.remark),
        )
        audit(conn, actor_id(args), "task.resolve", "review_task", task_key, True, "created")
    print_json({"task_key": task_key, "task_type": args.task_type, "created": True, "status": "PENDING", "current_version": 0})

def task_list(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    sql = "SELECT * FROM review_task WHERE 1=1"
    params = []
    if args.status:
        sql += " AND status = ?"
        params.append(args.status)
    if args.project_name:
        sql += " AND project_name = ?"
        params.append(args.project_name)
    if args.task_type:
        sql += " AND task_type = ?"
        params.append(args.task_type)
    if not args.status and not args.include_closed:
        sql += " AND status IN ('PENDING', 'IN_PROGRESS', 'ON_HOLD', 'BLOCKED')"
    sql += " ORDER BY updated_at DESC, id DESC"

    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        audit(conn, actor_id(args), "task.list", "review_task", None, True, f"count={len(rows)}")
    print_json(rows)

def cancel_open_issues_for_closed_task(
    conn: sqlite3.Connection, args: argparse.Namespace, task_id: int, task_key: str
) -> int:
    """任务显式关闭时，同事务取消尚未终结的 Issue；不伪造技术验证结论。"""
    issues = conn.execute(
        """SELECT id, issue_key, status, current_attempt_no
           FROM review_issue
           WHERE task_id = ? AND status NOT IN ('CONFIRMED', 'CANCELLED')
           ORDER BY id""",
        (task_id,),
    ).fetchall()
    reason = (args.close_reason or args.remark or "").strip()
    for issue in issues:
        content = f"所属任务 {task_key} 已关闭，Issue 同步取消"
        if reason:
            content += f"：{reason}"
        supersede_active_stage_plan(conn, args, issue, content)
        conn.execute(
            """UPDATE review_issue
               SET status = 'CANCELLED', cancelled_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (issue["id"],),
        )
        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id,
                content, result_status, metadata_json
            ) VALUES (?, ?, 'STATUS_CHANGED', ?, ?, ?, 'CANCELLED', ?)""",
            (
                issue["id"], issue["current_attempt_no"], operator_type(args.agent), actor_id(args),
                content, dumps({
                    "source": "task-update-status", "task_key": task_key,
                    "previous_status": issue["status"],
                }),
            ),
        )
    return len(issues)

def task_update_status(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以修改任务状态")

    with connect() as conn:
        cur = conn.execute("SELECT id, status FROM review_task WHERE task_key = ?", (args.task_key,))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"任务不存在: {args.task_key}")
        require_choice(args.status, TASK_STATUSES, "任务状态")
        if (
            args.agent != "human"
            and args.status != row["status"]
            and args.status not in TASK_TRANSITIONS[row["status"]]
        ):
            raise RuntimeError(f"不允许任务状态流转: {row['status']} -> {args.status}")
        conn.execute(
            """UPDATE review_task
               SET status = ?, started_at = COALESCE(?, started_at),
                   finished_at = COALESCE(?, finished_at),
                   close_reason = COALESCE(?, close_reason),
                   remark = COALESCE(?, remark),
                   updated_at = CURRENT_TIMESTAMP
               WHERE task_key = ?""",
            (args.status, args.started_at, args.finished_at, args.close_reason, args.remark, args.task_key),
        )
        cancelled_issue_count = 0
        if args.status == "CLOSED":
            cancelled_issue_count = cancel_open_issues_for_closed_task(
                conn, args, row["id"], args.task_key,
            )
        audit(
            conn, actor_id(args), "task.update-status", "review_task", args.task_key, True,
            f"status={args.status};cancelled_issues={cancelled_issue_count}",
        )
    print_json({
        "task_key": args.task_key, "status": args.status,
        "cancelled_issue_count": cancelled_issue_count,
    })

def task_update(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以更新任务")
    fields = {name: getattr(args, name) for name in ("title", "objective", "remark", "close_reason")}
    updates = {name: value for name, value in fields.items() if value is not None}
    if not updates:
        raise ValueError("至少提供一个可更新字段")
    with connect() as conn:
        row = conn.execute("SELECT id FROM review_task WHERE task_key = ?", (args.task_key,)).fetchone()
        if not row:
            raise KeyError(f"任务不存在: {args.task_key}")
        set_clause = ", ".join(f"{name} = ?" for name in updates)
        conn.execute(
            f"UPDATE review_task SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE task_key = ?",
            [*updates.values(), args.task_key],
        )
        audit(conn, actor_id(args), "task.update", "review_task", args.task_key, True, json.dumps(updates, ensure_ascii=False))
    print_json({"task_key": args.task_key, "updated": updates})

def version_create(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以创建版本")

    with connect() as conn:
        row = conn.execute(
            "SELECT id, current_version, status FROM review_task WHERE task_key = ?",
            (args.task_key,),
        ).fetchone()
        if not row:
            raise KeyError(f"任务不存在: {args.task_key}")
        if row["status"] in {"CLOSED", "CANCELLED"}:
            raise RuntimeError("已关闭或取消的任务不能创建版本")

        next_version = row["current_version"] + 1
        conn.execute(
            """INSERT INTO review_task_version(task_id, version_no, reason, created_by)
               VALUES (?, ?, ?, ?)""",
            (row["id"], next_version, args.reason, actor_id(args)),
        )
        conn.execute(
            """UPDATE review_task
               SET current_version = ?, status = CASE WHEN status='PENDING' THEN 'IN_PROGRESS' ELSE status END,
                   started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (next_version, row["id"]),
        )
        audit(conn, actor_id(args), "version.create", "review_task_version", f"{args.task_key}:v{next_version}", True)
    print_json({"task_key": args.task_key, "version": next_version})

def issue_create(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以创建问题")
    payload = vars(args)
    validate_issue_payload(payload)

    with connect() as conn:
        task = conn.execute(
            "SELECT id, current_version, status FROM review_task WHERE task_key = ?",
            (args.task_key,),
        ).fetchone()
        if not task:
            raise KeyError(f"任务不存在: {args.task_key}")
        if task["status"] in {"CLOSED", "CANCELLED"}:
            raise RuntimeError("已关闭或取消的任务不能创建新问题")
        if task["current_version"] <= 0:
            raise RuntimeError("请先创建任务版本")

        issue_key = args.issue_key or f"RI-{uuid.uuid4().hex[:8].upper()}"
        conn.execute(
            """INSERT INTO review_issue(
                issue_key, task_id, introduced_version, parent_issue_id, title, dimension,
                severity, remediation_benefit, remediation_cost, disposition, confidence,
                status, description, facts, trigger_conditions_json, potential_impact_json,
                impact_scope_json, rationale, evidence_json, estimated_change_json, dedupe_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROPOSED', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                issue_key, task["id"], task["current_version"], args.parent_issue_id,
                args.title, args.dimension, args.severity, args.remediation_benefit,
                args.remediation_cost, args.disposition, args.confidence,
                args.description, args.facts,
                dumps(json.loads(args.trigger_conditions)),
                dumps(json.loads(args.potential_impact)),
                dumps(json.loads(args.impact_scope)),
                args.rationale,
                dumps(json.loads(args.evidence)),
                dumps(json.loads(args.estimated_change)),
                args.dedupe_key or issue_dedupe_key({
                    "dimension": args.dimension, "title": args.title, "evidence": json.loads(args.evidence),
                }),
            ),
        )
        issue_id = conn.execute("SELECT id FROM review_issue WHERE issue_key = ?", (issue_key,)).fetchone()["id"]
        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id, content
            ) VALUES (?, 0, 'ISSUE_CREATED', ?, ?, ?)""",
            (issue_id, {"inspector": "INSPECTOR_AGENT", "human": "HUMAN"}[args.agent], actor_id(args), args.description),
        )
        audit(conn, actor_id(args), "issue.create", "review_issue", issue_key, True)
    print_json({"issue_key": issue_key, "version": task["current_version"]})

def issue_create_batch(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以批量创建问题")
    payloads = json.loads(args.issues)
    if not isinstance(payloads, list) or not payloads:
        raise ValueError("issues 必须是非空 JSON 数组")
    for payload in payloads:
        if not isinstance(payload, dict):
            raise ValueError("issues 中每项必须是对象")
        validate_issue_payload(payload)

    with connect() as conn:
        task = conn.execute("SELECT id, current_version, status FROM review_task WHERE task_key = ?", (args.task_key,)).fetchone()
        if not task:
            raise KeyError(f"任务不存在: {args.task_key}")
        if task["status"] in {"CLOSED", "CANCELLED"}:
            raise RuntimeError("已关闭或取消的任务不能创建新问题")

        new_payloads: list[tuple[dict[str, Any], str]] = []
        skipped: list[dict[str, str]] = []
        seen_keys: set[str] = set()
        for payload in payloads:
            dedupe_key = issue_dedupe_key(payload)
            if dedupe_key in seen_keys:
                skipped.append({"title": payload["title"], "reason": "duplicate_in_batch"})
                continue
            seen_keys.add(dedupe_key)
            existing = conn.execute(
                """SELECT issue_key FROM review_issue
                   WHERE task_id = ? AND dedupe_key = ?
                     AND status NOT IN ('CONFIRMED', 'CANCELLED')
                   ORDER BY id DESC LIMIT 1""",
                (task["id"], dedupe_key),
            ).fetchone()
            if existing:
                skipped.append({"title": payload["title"], "reason": f"duplicate_of:{existing['issue_key']}"})
                continue
            new_payloads.append((payload, dedupe_key))

        if not new_payloads:
            audit(conn, actor_id(args), "issue.create-batch", "review_task", args.task_key, True, "no_new_issues")
            print_json({"task_key": args.task_key, "created": [], "skipped": skipped, "version": task["current_version"]})
            return

        version = task["current_version"] + 1
        conn.execute(
            "INSERT INTO review_task_version(task_id, version_no, reason, created_by) VALUES (?, ?, ?, ?)",
            (task["id"], version, args.reason, actor_id(args)),
        )
        conn.execute(
            """UPDATE review_task
               SET current_version = ?, status = CASE WHEN status='PENDING' THEN 'IN_PROGRESS' ELSE status END,
                   started_at = COALESCE(started_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (version, task["id"]),
        )
        created: list[str] = []
        for payload, dedupe_key in new_payloads:
            issue_key = payload.get("issue_key") or f"RI-{uuid.uuid4().hex[:8].upper()}"
            conn.execute(
                """INSERT INTO review_issue(
                    issue_key, task_id, introduced_version, parent_issue_id, title, dimension,
                    severity, remediation_benefit, remediation_cost, disposition, confidence,
                    status, description, facts, trigger_conditions_json, potential_impact_json,
                    impact_scope_json, rationale, evidence_json, estimated_change_json, dedupe_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROPOSED', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    issue_key, task["id"], version, payload.get("parent_issue_id"), payload["title"], payload["dimension"],
                    payload["severity"], payload["remediation_benefit"], payload["remediation_cost"],
                    payload["disposition"], payload["confidence"], payload["description"], payload["facts"],
                    dumps(payload.get("trigger_conditions", [])), dumps(payload.get("potential_impact", [])),
                    dumps(payload.get("impact_scope", [])), payload["rationale"], dumps(payload.get("evidence", [])),
                    dumps(payload.get("estimated_change", {})), dedupe_key,
                ),
            )
            issue_id = conn.execute("SELECT id FROM review_issue WHERE issue_key = ?", (issue_key,)).fetchone()["id"]
            conn.execute(
                """INSERT INTO issue_activity(issue_id, attempt_no, activity_type, operator_type, operator_id, content)
                   VALUES (?, 0, 'ISSUE_CREATED', ?, ?, ?)""",
                (issue_id, operator_type(args.agent), actor_id(args), payload["description"]),
            )
            created.append(issue_key)
        audit(conn, actor_id(args), "issue.create-batch", "review_task", args.task_key, True, f"created={len(created)}")
    print_json({"task_key": args.task_key, "created": created, "skipped": skipped, "version": version})

ISSUE_OUTPUT_FIELDS = {
    "id", "issue_key", "task_id", "introduced_version", "parent_issue_id", "title",
    "dimension", "severity", "remediation_benefit", "remediation_cost", "disposition",
    "confidence", "status", "description", "facts", "trigger_conditions_json",
    "potential_impact_json", "impact_scope_json", "rationale", "evidence_json",
    "estimated_change_json", "current_attempt_no", "confirmed_at", "cancelled_at",
    "created_at", "updated_at", "dedupe_key", "task_key", "project_name",
    "last_activity_at", "last_comment_at",
}

def selected_issue_fields(value: str | None) -> list[str] | None:
    if value is None:
        return None
    fields = [field.strip() for field in value.split(",") if field.strip()]
    if not fields:
        raise ValueError("fields 至少包含一个字段")
    unknown = sorted(set(fields) - ISSUE_OUTPUT_FIELDS)
    if unknown:
        raise ValueError(f"未知 issue 输出字段: {', '.join(unknown)}")
    if len(fields) != len(set(fields)):
        raise ValueError("fields 不能包含重复字段")
    return fields

def issue_query(conn: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    sql = """
        SELECT i.*, t.task_key, t.project_name,
               (SELECT MAX(a.created_at) FROM issue_activity a WHERE a.issue_id = i.id) AS last_activity_at,
               (SELECT MAX(a.created_at) FROM issue_activity a
                WHERE a.issue_id = i.id AND a.activity_type = 'COMMENT_ADDED') AS last_comment_at
        FROM review_issue i
        JOIN review_task t ON t.id = i.task_id
        WHERE 1=1
    """
    params = []
    for field in ("task_key", "status", "severity", "dimension"):
        value = getattr(args, field, None)
        if value:
            column = f"t.{field}" if field == "task_key" else f"i.{field}"
            sql += f" AND {column} = ?"
            params.append(value)
    if getattr(args, "updated_after", None):
        sql += " AND i.updated_at >= ?"
        params.append(normalize_since(args.updated_after))

    sql += """
      ORDER BY
        CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
        CASE remediation_benefit WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
        CASE dimension
          WHEN 'functional_correctness' THEN 7
          WHEN 'data_security' THEN 6
          WHEN 'stability_concurrency' THEN 5
          WHEN 'performance' THEN 4
          WHEN 'architecture_extensibility' THEN 3
          WHEN 'code_quality' THEN 2
          ELSE 1
        END DESC,
        CASE remediation_cost WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'high' THEN 3 ELSE 4 END ASC,
        CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
        issue_key ASC
    """
    limit = getattr(args, "limit", None)
    if limit is not None:
        if not 1 <= limit <= 1000:
            raise ValueError("limit 必须在 1 到 1000 之间")
        sql += " LIMIT ?"
        params.append(limit)
    fields = selected_issue_fields(getattr(args, "fields", None))
    rows = []
    for r in conn.execute(sql, params).fetchall():
        item = dict(r)
        for key in (
            "trigger_conditions_json", "potential_impact_json", "impact_scope_json",
            "evidence_json", "estimated_change_json"
        ):
            item[key] = loads(item[key], [] if key != "estimated_change_json" else {})
        rows.append({field: item[field] for field in fields} if fields else item)
    return rows

def issue_list(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    with connect() as conn:
        rows = issue_query(conn, args)
        audit(conn, actor_id(args), "issue.list", "review_issue", getattr(args, "task_key", None), True, f"count={len(rows)}")
    print_json(rows)

def issue_list_pending_review(args: argparse.Namespace) -> None:
    args.status = "IMPLEMENTED_PENDING_REVIEW"
    args.severity = None
    args.dimension = None
    issue_list(args)

def issue_get(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    with connect() as conn:
        row = conn.execute(
            """SELECT i.*, t.task_key, t.project_name,
                      (SELECT MAX(a.created_at) FROM issue_activity a WHERE a.issue_id = i.id) AS last_activity_at,
                      (SELECT MAX(a.created_at) FROM issue_activity a
                       WHERE a.issue_id = i.id AND a.activity_type = 'COMMENT_ADDED') AS last_comment_at
               FROM review_issue i
               JOIN review_task t ON t.id = i.task_id
               WHERE i.issue_key = ?""",
            (args.issue_key,),
        ).fetchone()
        if not row:
            raise KeyError(f"问题不存在: {args.issue_key}")
        item = dict(row)
        for key in (
            "trigger_conditions_json", "potential_impact_json", "impact_scope_json",
            "evidence_json", "estimated_change_json"
        ):
            item[key] = loads(item[key], [] if key != "estimated_change_json" else {})
        audit(conn, actor_id(args), "issue.get", "review_issue", args.issue_key, True)
    print_json(item)

ASSESSMENT_FIELDS = {
    "dimension": ALLOWED_DIMENSIONS,
    "severity": ALLOWED_SEVERITIES,
    "remediation_benefit": ALLOWED_BENEFITS,
    "remediation_cost": ALLOWED_COSTS,
    "disposition": ALLOWED_DISPOSITIONS,
    "confidence": ALLOWED_CONFIDENCE,
}

def validate_assessment_values(values: dict[str, Any]) -> dict[str, str]:
    unknown = sorted(set(values) - set(ASSESSMENT_FIELDS))
    if unknown:
        raise ValueError(f"未知评级字段: {', '.join(unknown)}")
    changed = {field: value for field, value in values.items() if value is not None}
    if not changed:
        raise ValueError("至少提供一个评级字段")
    for field, value in changed.items():
        require_choice(value, ASSESSMENT_FIELDS[field], field)
    return changed

def apply_assessment_update(
    conn: sqlite3.Connection, args: argparse.Namespace, issue_key: str, values: dict[str, Any]
) -> dict[str, str]:
    changed = validate_assessment_values(values)
    row = conn.execute("SELECT id FROM review_issue WHERE issue_key = ?", (issue_key,)).fetchone()
    if not row:
        raise KeyError(f"问题不存在: {issue_key}")
    conn.execute(
        f"UPDATE review_issue SET {', '.join(f'{field} = ?' for field in changed)}, "
        "updated_at = CURRENT_TIMESTAMP WHERE issue_key = ?",
        [*changed.values(), issue_key],
    )
    conn.execute(
        """INSERT INTO issue_activity(issue_id, attempt_no, activity_type, operator_type, operator_id, content, metadata_json)
           SELECT id, current_attempt_no, 'COMMENT_ADDED', ?, ?, ?, ? FROM review_issue WHERE issue_key = ?""",
        (operator_type(args.agent), actor_id(args), "更新问题评级", dumps({"assessment": changed}), issue_key),
    )
    audit(conn, actor_id(args), "issue.update-assessment", "review_issue", issue_key, True)
    return changed

def issue_update_assessment(args: argparse.Namespace) -> None:
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以修改问题评级")
    require_agent(args.agent)
    values = {field: getattr(args, field) for field in ASSESSMENT_FIELDS}
    with connect() as conn:
        changed = apply_assessment_update(conn, args, args.issue_key, values)
    print_json({"issue_key": args.issue_key, "updated": changed})

def issue_update_assessment_batch(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以批量修改问题评级")
    updates = json.loads(args.updates)
    if not isinstance(updates, list) or not updates:
        raise ValueError("updates 必须是非空 JSON 数组")
    seen: set[str] = set()
    prepared = []
    for update in updates:
        if not isinstance(update, dict) or not update.get("issue_key"):
            raise ValueError("updates 中每项必须是包含 issue_key 的对象")
        issue_key = str(update["issue_key"])
        if issue_key in seen:
            raise ValueError(f"同一批次不能重复更新问题: {issue_key}")
        seen.add(issue_key)
        values = {key: value for key, value in update.items() if key != "issue_key"}
        validate_assessment_values(values)
        prepared.append((issue_key, values))
    result = []
    with connect() as conn:
        for issue_key, values in prepared:
            changed = apply_assessment_update(conn, args, issue_key, values)
            result.append({"issue_key": issue_key, "updated": changed})
        audit(conn, actor_id(args), "issue.update-assessment-batch", "review_issue", None, True, f"count={len(result)}")
    print_json({"updated": result})

def issue_update_body(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以更新问题正文")
    fields = {name: getattr(args, name) for name in ("title", "description", "facts", "rationale")}
    updates = {name: value for name, value in fields.items() if value is not None}
    if not updates:
        raise ValueError("至少提供一个可更新字段")
    with connect() as conn:
        row = conn.execute("SELECT id, current_attempt_no FROM review_issue WHERE issue_key = ?", (args.issue_key,)).fetchone()
        if not row:
            raise KeyError(f"问题不存在: {args.issue_key}")
        set_clause = ", ".join(f"{name} = ?" for name in updates)
        conn.execute(
            f"UPDATE review_issue SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE issue_key = ?",
            [*updates.values(), args.issue_key],
        )
        conn.execute(
            """INSERT INTO issue_activity(issue_id, attempt_no, activity_type, operator_type, operator_id, content, metadata_json)
               VALUES (?, ?, 'COMMENT_ADDED', ?, ?, ?, ?)""",
            (row["id"], row["current_attempt_no"], operator_type(args.agent), actor_id(args),
             "更新问题正文", dumps({"body": sorted(updates)})),
        )
        audit(conn, actor_id(args), "issue.update-body", "review_issue", args.issue_key, True, json.dumps(updates, ensure_ascii=False))
    print_json({"issue_key": args.issue_key, "updated": updates})

def apply_status_update(
    conn: sqlite3.Connection, args: argparse.Namespace, issue_key: str, status: str, content: str | None
) -> dict[str, Any]:
    if status not in ALLOWED_STATUS_BY_AGENT[args.agent]:
        raise PermissionError(f"agent {args.agent} 无权设置状态 {status}")
    if status == "INSPECTOR_CONFIRMATION_REQUIRED" and args.agent == "developer" and not (content or "").strip():
        raise ValueError("开发端请求审核确认时必须通过 --content 说明待确认边界或疑问")
    row = conn.execute(
        "SELECT id, status, current_attempt_no FROM review_issue WHERE issue_key = ?",
        (issue_key,),
    ).fetchone()
    if not row:
        raise KeyError(f"问题不存在: {issue_key}")
    human_override = (
        args.agent == "human"
        and row["status"] != "HUMAN_CONFIRMATION_REQUIRED"
        and status != "HUMAN_CONFIRMATION_REQUIRED"
    )
    if not human_override and status not in ALLOWED_TRANSITIONS[row["status"]][args.agent]:
        raise RuntimeError(f"不允许状态流转: {row['status']} -> {status} ({args.agent})")
    if (
        not human_override
        and row["status"] == "IMPLEMENTED_PENDING_REVIEW"
        and status in {"IN_PROGRESS", "REDESIGN_REQUIRED"}
    ):
        if not (content or "").strip():
            raise ValueError("实现审核失败必须通过 --content 说明失败原因、调整点和验证标准")
        if status == "IN_PROGRESS":
            failed = conn.execute(
                """SELECT 1 FROM issue_activity
                   WHERE issue_id = ? AND attempt_no = ? AND activity_type = 'VERIFICATION_FAILED'
                   LIMIT 1""",
                (row["id"], row["current_attempt_no"]),
            ).fetchone()
            if not failed:
                raise RuntimeError("按原设计退回 IN_PROGRESS 前必须为当前 attempt 追加 VERIFICATION_FAILED")
    if not human_override and row["status"] == "INSPECTOR_CONFIRMATION_REQUIRED" and args.agent in {"inspector", "human"}:
        confirmation = conn.execute(
            """SELECT 1 FROM issue_activity
               WHERE issue_id = ? AND activity_type = 'INSPECTOR_CONFIRMATION_PROVIDED'
               LIMIT 1""",
            (row["id"],),
        ).fetchone()
        if not confirmation:
            raise RuntimeError("审核端结束待审核确认前必须追加 INSPECTOR_CONFIRMATION_PROVIDED 活动")
    # Human 可以纠正普通状态，但最终技术确认仍不能绕过 VERIFICATION_PASSED。
    if status == "CONFIRMED":
        verified = conn.execute(
            """SELECT 1 FROM issue_activity
               WHERE issue_id = ? AND attempt_no = ? AND activity_type = 'VERIFICATION_PASSED'
               LIMIT 1""",
            (row["id"], row["current_attempt_no"]),
        ).fetchone()
        if not verified:
            raise RuntimeError("问题确认前必须为当前 implementation attempt 记录 VERIFICATION_PASSED")

    attempt_no = row["current_attempt_no"]
    if status == "IMPLEMENTED_PENDING_REVIEW" and row["status"] != status:
        attempt_no += 1

    if status == "REDESIGN_REQUIRED" and row["status"] != status:
        supersede_active_stage_plan(
            conn, args, row, content or "Issue 进入 REDESIGN_REQUIRED，旧 Stage Plan 已废弃",
        )

    conn.execute(
        """UPDATE review_issue
           SET status = ?, current_attempt_no = ?,
               confirmed_at = CASE WHEN ?='CONFIRMED' THEN CURRENT_TIMESTAMP ELSE confirmed_at END,
               cancelled_at = CASE WHEN ?='CANCELLED' THEN CURRENT_TIMESTAMP ELSE cancelled_at END,
               updated_at = CURRENT_TIMESTAMP
           WHERE issue_key = ?""",
        (status, attempt_no, status, status, issue_key),
    )
    conn.execute(
        """INSERT INTO issue_activity(
            issue_id, attempt_no, activity_type, operator_type, operator_id, content, result_status
        ) VALUES (?, ?, 'STATUS_CHANGED', ?, ?, ?, ?)""",
        (
            row["id"], attempt_no, operator_type(args.agent), actor_id(args),
            content or f"状态变更为 {status}", status,
        ),
    )
    audit(conn, actor_id(args), "issue.update-status", "review_issue", issue_key, True)
    return {"issue_key": issue_key, "status": status, "attempt_no": attempt_no}

def issue_update_status(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    with connect() as conn:
        result = apply_status_update(conn, args, args.issue_key, args.status, args.content)
    print_json(result)

def issue_update_status_batch(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    updates = json.loads(args.updates)
    if not isinstance(updates, list) or not updates:
        raise ValueError("updates 必须是非空 JSON 数组")
    seen: set[str] = set()
    prepared = []
    for update in updates:
        if not isinstance(update, dict) or not update.get("issue_key") or not update.get("status"):
            raise ValueError("updates 中每项必须是包含 issue_key 和 status 的对象")
        unknown = sorted(set(update) - {"issue_key", "status", "content"})
        if unknown:
            raise ValueError(f"未知状态更新字段: {', '.join(unknown)}")
        issue_key = str(update["issue_key"])
        if issue_key in seen:
            raise ValueError(f"同一批次不能重复更新问题: {issue_key}")
        seen.add(issue_key)
        status = str(update["status"])
        if status not in ALLOWED_STATUS_BY_AGENT[args.agent]:
            raise PermissionError(f"agent {args.agent} 无权设置状态 {status}")
        prepared.append((issue_key, status, update.get("content")))
    result = []
    with connect() as conn:
        for issue_key, status, content in prepared:
            result.append(apply_status_update(conn, args, issue_key, status, content))
        audit(conn, actor_id(args), "issue.update-status-batch", "review_issue", None, True, f"count={len(result)}")
    print_json({"updated": result})

def apply_design_transition(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    *,
    allowed_agents: set[str],
    allowed_sources: set[str],
    target_status: str,
    activity_type: str,
    code_reference: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if args.agent not in allowed_agents:
        raise PermissionError(f"agent {args.agent} 无权执行 {args.command}")
    content = (args.content or "").strip()
    if not content:
        raise ValueError(f"{args.command} 必须通过 --content 记录设计结论")
    row = conn.execute(
        "SELECT id, status, current_attempt_no FROM review_issue WHERE issue_key = ?",
        (args.issue_key,),
    ).fetchone()
    if not row:
        raise KeyError(f"问题不存在: {args.issue_key}")
    if row["status"] not in allowed_sources:
        raise RuntimeError(
            f"不允许设计状态流转: {row['status']} -> {target_status} ({args.command})"
        )
    conn.execute(
        """INSERT INTO issue_activity(
            issue_id, attempt_no, activity_type, operator_type, operator_id,
            content, result_status, code_reference_json, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row["id"], row["current_attempt_no"], activity_type,
            operator_type(args.agent), actor_id(args), content, target_status,
            dumps(code_reference or []), dumps(metadata or {}),
        ),
    )
    conn.execute(
        "UPDATE review_issue SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (target_status, row["id"]),
    )
    audit(conn, actor_id(args), args.command.replace("-", "."), "review_issue", args.issue_key, True)
    return {
        "issue_key": args.issue_key,
        "status": target_status,
        "attempt_no": row["current_attempt_no"],
        "activity_type": activity_type,
    }

def design_request(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    with connect() as conn:
        row = issue_row(conn, args.issue_key)
        result = apply_design_transition(
            conn, args, allowed_agents={"inspector", "human"},
            allowed_sources={"PROPOSED", "IN_PROGRESS"}, target_status="DESIGN_REQUIRED",
            activity_type="DESIGN_REQUESTED",
        )
        supersede_active_stage_plan(conn, args, row, args.content)
    print_json(result)

def design_submit(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    code_reference = json.loads(args.code_reference)
    metadata = json.loads(args.metadata)
    if not isinstance(code_reference, list):
        raise ValueError("code-reference 必须是 JSON 数组")
    if not isinstance(metadata, dict):
        raise ValueError("metadata 必须是 JSON 对象")
    with connect() as conn:
        result = apply_design_transition(
            conn, args, allowed_agents={"developer", "human"},
            allowed_sources={"DESIGN_REQUIRED", "REDESIGN_REQUIRED"},
            target_status="DESIGN_PENDING_REVIEW", activity_type="DESIGN_SUBMITTED",
            code_reference=code_reference, metadata=metadata,
        )
    print_json(result)

def design_review(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    target_status = "IN_PROGRESS" if args.decision == "approved" else "DESIGN_REQUIRED"
    activity_type = "DESIGN_APPROVED" if args.decision == "approved" else "DESIGN_REJECTED"
    with connect() as conn:
        row = issue_row(conn, args.issue_key)
        result = apply_design_transition(
            conn, args, allowed_agents={"inspector", "human"},
            allowed_sources={"DESIGN_PENDING_REVIEW"}, target_status=target_status,
            activity_type=activity_type,
        )
        plan_no = active_stage_plan_no(conn, row["id"])
        if args.decision == "approved" and plan_no is not None:
            first = conn.execute(
                """SELECT id FROM issue_stage
                   WHERE issue_id = ? AND plan_no = ? AND status = 'PLANNED'
                   ORDER BY stage_no LIMIT 1""",
                (row["id"], plan_no),
            ).fetchone()
            if first:
                conn.execute(
                    "UPDATE issue_stage SET status = 'IN_PROGRESS', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (first["id"],),
                )
        elif args.decision == "rejected":
            supersede_active_stage_plan(conn, args, row, args.content)
    print_json({**result, "decision": args.decision})

def normalized_acceptance_criteria(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(f"- {item}" for item in items)
    return str(value or "").strip()

FINDING_LEVELS = ("BLOCKER", "MUST", "SHOULD", "NIT")

def nonempty_evidence(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None

def acceptance_items(value: str) -> list[str]:
    return [re.sub(r"^\s*[-*]\s+", "", line).strip() for line in value.splitlines() if line.strip()]

def validate_stage_review_result(
    conn: sqlite3.Connection,
    issue_id: int,
    plan_no: int,
    stage: sqlite3.Row,
    raw_result: str,
) -> tuple[dict[str, Any], bool, int]:
    try:
        result = json.loads(raw_result)
    except (TypeError, ValueError) as exc:
        raise ValueError("review-result 必须是 JSON 对象") from exc
    if not isinstance(result, dict):
        raise ValueError("review-result 必须是 JSON 对象")
    required = {"findings", "historical_regression", "current_acceptance"}
    if set(result) != required:
        raise ValueError("review-result 必须且只能包含 findings、historical_regression、current_acceptance")

    findings = result["findings"]
    if not isinstance(findings, dict) or set(findings) != set(FINDING_LEVELS):
        raise ValueError("findings 必须完整包含 BLOCKER、MUST、SHOULD、NIT 四个数组")
    review_round = int(stage["review_round"]) + 1
    known_ids: set[str] = set()
    activity_rows = conn.execute(
        """SELECT metadata_json FROM issue_activity
           WHERE issue_id = ? AND activity_type IN ('STAGE_APPROVED', 'STAGE_REJECTED')
           ORDER BY id""",
        (issue_id,),
    ).fetchall()
    for activity in activity_rows:
        metadata = loads(activity["metadata_json"], {})
        if metadata.get("plan_no") != plan_no or metadata.get("stage_no") != stage["stage_no"]:
            continue
        old_findings = metadata.get("review_result", {}).get("findings", {})
        for level in FINDING_LEVELS:
            for finding in old_findings.get(level, []):
                if isinstance(finding, dict) and finding.get("id"):
                    known_ids.add(str(finding["id"]))

    current_ids: set[str] = set()
    for level in FINDING_LEVELS:
        items = findings[level]
        if not isinstance(items, list):
            raise ValueError(f"findings.{level} 必须是 JSON 数组")
        for finding in items:
            if not isinstance(finding, dict):
                raise ValueError(f"{level} finding 必须是 JSON 对象")
            finding_id = str(finding.get("id") or "").strip()
            summary = str(finding.get("summary") or "").strip()
            if not finding_id or not summary:
                raise ValueError(f"{level} finding 必须包含非空 id 和 summary")
            if finding_id in current_ids:
                raise ValueError(f"同一轮 finding id 不能重复: {finding_id}")
            current_ids.add(finding_id)
            is_new = finding_id not in known_ids
            if level in {"BLOCKER", "MUST"}:
                if not nonempty_evidence(finding.get("evidence")) or not str(finding.get("risk") or "").strip():
                    raise ValueError(f"{level} {finding_id} 必须提供 evidence 和实际 risk")
                if review_round >= 2 and is_new and not str(finding.get("why_not_found_earlier") or "").strip():
                    raise ValueError(f"第 {review_round} 轮新增 {level} {finding_id} 必须说明 why_not_found_earlier")
            elif review_round >= 2 and is_new:
                if finding.get("introduced_by_fix") is not True or not nonempty_evidence(finding.get("evidence")):
                    raise ValueError(
                        f"第 {review_round} 轮不得新增无关 {level}；{finding_id} 必须由本轮修复新引入并提供 evidence"
                    )

    previous = conn.execute(
        """SELECT stage_no FROM issue_stage
           WHERE issue_id = ? AND plan_no = ? AND stage_no < ? AND status = 'APPROVED'
           ORDER BY stage_no""",
        (issue_id, plan_no, stage["stage_no"]),
    ).fetchall()
    expected_history = {str(row["stage_no"]) for row in previous}
    historical = result["historical_regression"]
    if not isinstance(historical, dict) or set(historical) != expected_history:
        raise ValueError(
            "historical_regression 必须逐项覆盖已批准 Stage: "
            + (", ".join(sorted(expected_history, key=int)) or "无历史 Stage，应传 {}")
        )
    historical_failed = False
    for stage_no, check in historical.items():
        if not isinstance(check, dict) or check.get("status") not in {"PASS", "FAIL"}:
            raise ValueError(f"historical_regression.{stage_no} 必须包含 PASS/FAIL status")
        if not nonempty_evidence(check.get("evidence")):
            raise ValueError(f"historical_regression.{stage_no} 必须提供 evidence")
        historical_failed = historical_failed or check["status"] == "FAIL"

    current = result["current_acceptance"]
    expected_criteria = acceptance_items(stage["acceptance_criteria"])
    if not isinstance(current, list) or [item.get("criterion") if isinstance(item, dict) else None for item in current] != expected_criteria:
        raise ValueError("current_acceptance 必须按原顺序逐项覆盖 Stage acceptance_criteria")
    current_failed = False
    for check in current:
        if check.get("status") not in {"PASS", "FAIL"} or not nonempty_evidence(check.get("evidence")):
            raise ValueError("每个 current_acceptance 项必须包含 PASS/FAIL status 和 evidence")
        current_failed = current_failed or check["status"] == "FAIL"

    blocker_count = len(findings["BLOCKER"])
    must_count = len(findings["MUST"])
    if historical_failed and blocker_count == 0:
        raise ValueError("历史 Stage 回归失败必须至少记录一个 BLOCKER")
    if current_failed and blocker_count + must_count == 0:
        raise ValueError("当前 Stage 验收失败必须记录对应 BLOCKER 或 MUST")
    passed = blocker_count == 0 and must_count == 0 and not historical_failed and not current_failed
    return result, passed, review_round

def validate_stage_baseline(raw_baseline: str, inherited_stage_nos: list[int]) -> dict[str, Any]:
    try:
        baseline = json.loads(raw_baseline)
    except (TypeError, ValueError) as exc:
        raise ValueError("baseline 必须是 JSON 对象") from exc
    required = {"verified_behaviors", "input_output_contracts", "business_semantics", "tests"}
    if not isinstance(baseline, dict) or set(baseline) != required:
        raise ValueError("baseline 必须且只能包含 verified_behaviors、input_output_contracts、business_semantics、tests")
    for key in required:
        if not isinstance(baseline[key], list):
            raise ValueError(f"baseline.{key} 必须是 JSON 数组")
    if not baseline["verified_behaviors"] or not baseline["tests"]:
        raise ValueError("baseline.verified_behaviors 和 baseline.tests 不能为空")
    return {**baseline, "inherits_stage_nos": inherited_stage_nos, "status": "PASSED"}

def stage_row_json(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["test_evidence"] = loads(result.pop("test_evidence_json", None), [])
    result["code_reference"] = loads(result.pop("code_reference_json", None), [])
    result["submission_metadata"] = loads(result.pop("submission_metadata_json", None), {})
    result["planned_change_scope"] = loads(result.pop("planned_change_scope_json", None), {})
    result["protected_behaviors"] = loads(result.pop("protected_behaviors_json", None), [])
    result["resolved_findings"] = loads(result.pop("resolved_findings_json", None), [])
    result["review_findings"] = loads(result.pop("review_findings_json", None), {level: [] for level in FINDING_LEVELS})
    result["historical_regression"] = loads(result.pop("historical_regression_json", None), {})
    result["current_acceptance"] = loads(result.pop("current_acceptance_json", None), [])
    result["baseline"] = loads(result.pop("baseline_json", None), {})
    return result

def stage_plan_create(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以创建 Stage Plan")
    stages = json.loads(args.stages)
    if not isinstance(stages, list) or not stages:
        raise ValueError("stages 必须是非空 JSON 数组")
    prepared: list[dict[str, Any]] = []
    for item in stages:
        if not isinstance(item, dict):
            raise ValueError("每个 Stage 必须是 JSON 对象")
        unknown = sorted(set(item) - {"stage_no", "title", "objective", "acceptance_criteria"})
        if unknown:
            raise ValueError(f"未知 Stage 字段: {', '.join(unknown)}")
        try:
            stage_no = int(item.get("stage_no"))
        except (TypeError, ValueError) as exc:
            raise ValueError("stage_no 必须是正整数") from exc
        title = str(item.get("title") or "").strip()
        objective = str(item.get("objective") or "").strip()
        acceptance = normalized_acceptance_criteria(item.get("acceptance_criteria"))
        if stage_no < 1 or not title or not objective or not acceptance:
            raise ValueError("每个 Stage 都必须包含正整数 stage_no、title、objective、acceptance_criteria")
        prepared.append({
            "stage_no": stage_no, "title": title, "objective": objective,
            "acceptance_criteria": acceptance,
        })
    prepared.sort(key=lambda item: item["stage_no"])
    if [item["stage_no"] for item in prepared] != list(range(1, len(prepared) + 1)):
        raise ValueError("stage_no 必须从 1 开始连续递增且不能重复")

    with connect() as conn:
        issue = issue_row(conn, args.issue_key)
        if issue["status"] != "DESIGN_PENDING_REVIEW":
            raise RuntimeError("只有 DESIGN_PENDING_REVIEW 可以创建 Stage Plan")
        if active_stage_plan_no(conn, issue["id"]) is not None:
            raise RuntimeError("当前已有未废弃的 Stage Plan")
        latest = conn.execute(
            "SELECT COALESCE(MAX(plan_no), 0) AS plan_no FROM issue_stage WHERE issue_id = ?",
            (issue["id"],),
        ).fetchone()
        plan_no = int(latest["plan_no"]) + 1
        for item in prepared:
            conn.execute(
                """INSERT INTO issue_stage(
                    issue_id, plan_no, stage_no, title, objective, acceptance_criteria,
                    status, governance_version
                ) VALUES (?, ?, ?, ?, ?, ?, 'PLANNED', 2)""",
                (
                    issue["id"], plan_no, item["stage_no"], item["title"],
                    item["objective"], item["acceptance_criteria"],
                ),
            )
        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id,
                content, result_status, metadata_json
            ) VALUES (?, ?, 'STAGE_PLAN_CREATED', ?, ?, ?, 'PLANNED', ?)""",
            (
                issue["id"], issue["current_attempt_no"], operator_type(args.agent), actor_id(args),
                f"创建 Stage Plan #{plan_no}，共 {len(prepared)} 个阶段",
                dumps({"plan_no": plan_no, "governance_version": 2, "stages": prepared}),
            ),
        )
        audit(conn, actor_id(args), "stage.plan-create", "review_issue", args.issue_key, True)
    print_json({"issue_key": args.issue_key, "plan_no": plan_no, "stages": prepared})

def stage_list(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    with connect() as conn:
        issue = issue_row(conn, args.issue_key)
        params: list[Any] = [issue["id"]]
        where = "WHERE issue_id = ?"
        if args.plan_no is not None:
            where += " AND plan_no = ?"
            params.append(args.plan_no)
        rows = conn.execute(
            f"SELECT * FROM issue_stage {where} ORDER BY plan_no DESC, stage_no", params,
        ).fetchall()
    print_json([stage_row_json(row) for row in rows])

def stage_get(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    with connect() as conn:
        issue = issue_row(conn, args.issue_key)
        plan_no = args.plan_no
        if plan_no is None:
            latest = conn.execute(
                "SELECT MAX(plan_no) AS plan_no FROM issue_stage WHERE issue_id = ?",
                (issue["id"],),
            ).fetchone()
            plan_no = latest["plan_no"]
        if plan_no is None:
            raise KeyError(f"问题没有 Stage Plan: {args.issue_key}")
        row = conn.execute(
            "SELECT * FROM issue_stage WHERE issue_id = ? AND plan_no = ? AND stage_no = ?",
            (issue["id"], plan_no, args.stage_no),
        ).fetchone()
        if not row:
            raise KeyError(f"Stage 不存在: plan={plan_no}, stage={args.stage_no}")
        previous = conn.execute(
            """SELECT stage_no, title, baseline_json, baseline_status, baseline_established_at
               FROM issue_stage
               WHERE issue_id = ? AND plan_no = ? AND stage_no < ? AND status = 'APPROVED'
               ORDER BY stage_no""",
            (issue["id"], plan_no, args.stage_no),
        ).fetchall()
    result = stage_row_json(row)
    result["historical_baselines"] = [
        {
            "stage_no": item["stage_no"], "title": item["title"],
            "status": item["baseline_status"],
            "baseline": loads(item["baseline_json"], {}),
            "established_at": item["baseline_established_at"],
        }
        for item in previous
    ]
    print_json(result)

def stage_prepare(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"developer", "human"}:
        raise PermissionError("只有 developer 或 human 可以声明 Stage 开发影响范围")
    reason = (args.change_reason or "").strip()
    if not reason:
        raise ValueError("stage-prepare 的 --change-reason 不能为空")
    try:
        change_scope = json.loads(args.change_scope)
        protected_behaviors = json.loads(args.protected_behaviors)
    except (TypeError, ValueError) as exc:
        raise ValueError("change-scope 和 protected-behaviors 必须是合法 JSON") from exc
    if not isinstance(change_scope, (list, dict)) or not change_scope:
        raise ValueError("change-scope 必须是非空 JSON 数组或对象，说明模块、文件或类")
    if not isinstance(protected_behaviors, list):
        raise ValueError("protected-behaviors 必须是 JSON 数组")

    with connect() as conn:
        issue = issue_row(conn, args.issue_key)
        if issue["status"] != "IN_PROGRESS":
            raise RuntimeError(f"Issue 状态 {issue['status']} 不允许准备 Stage")
        plan_no = active_stage_plan_no(conn, issue["id"])
        if plan_no is None:
            raise RuntimeError("当前没有可执行的 Stage Plan")
        stage = conn.execute(
            "SELECT * FROM issue_stage WHERE issue_id = ? AND plan_no = ? AND stage_no = ?",
            (issue["id"], plan_no, args.stage_no),
        ).fetchone()
        if not stage:
            raise KeyError(f"Stage 不存在: plan={plan_no}, stage={args.stage_no}")
        if stage["status"] != "IN_PROGRESS":
            raise RuntimeError(f"Stage {args.stage_no} 当前为 {stage['status']}，不能声明开发范围")
        previous = conn.execute(
            """SELECT stage_no, title, baseline_json, baseline_status
               FROM issue_stage
               WHERE issue_id = ? AND plan_no = ? AND stage_no < ? AND status = 'APPROVED'
               ORDER BY stage_no""",
            (issue["id"], plan_no, args.stage_no),
        ).fetchall()
        if int(stage["governance_version"]) >= 2:
            missing_baselines = [item["stage_no"] for item in previous if item["baseline_status"] != "PASSED"]
            if missing_baselines:
                raise RuntimeError(f"历史 Stage 缺少 PASSED baseline: {missing_baselines}")
            if previous and not protected_behaviors:
                raise ValueError("存在历史 Stage 时 protected-behaviors 不能为空")
        conn.execute(
            """UPDATE issue_stage
               SET planned_change_scope_json = ?, change_reason = ?, protected_behaviors_json = ?,
                   prepared_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (dumps(change_scope), reason, dumps(protected_behaviors), stage["id"]),
        )
        previous_baselines = [
            {
                "stage_no": item["stage_no"], "title": item["title"],
                "status": item["baseline_status"], "baseline": loads(item["baseline_json"], {}),
            }
            for item in previous
        ]
        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id,
                content, result_status, metadata_json
            ) VALUES (?, ?, 'STAGE_SCOPE_DECLARED', ?, ?, ?, 'IN_PROGRESS', ?)""",
            (
                issue["id"], issue["current_attempt_no"], operator_type(args.agent), actor_id(args),
                reason, dumps({
                    "plan_no": plan_no, "stage_no": args.stage_no,
                    "change_scope": change_scope, "protected_behaviors": protected_behaviors,
                    "historical_baseline_stage_nos": [item["stage_no"] for item in previous],
                }),
            ),
        )
        audit(conn, actor_id(args), "stage.prepare", "review_issue", args.issue_key, True)
    print_json({
        "issue_key": args.issue_key, "plan_no": plan_no, "stage_no": args.stage_no,
        "status": "IN_PROGRESS", "historical_baselines": previous_baselines,
    })

def stage_submit(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"developer", "human"}:
        raise PermissionError("只有 developer 或 human 可以提交 Stage")
    content = (args.content or "").strip()
    commit_sha = (args.commit_sha or "").strip()
    diff_summary = (args.diff_summary or "").strip()
    if not content or not commit_sha:
        raise ValueError("stage-submit 的 --content 和 --commit-sha 均不能为空")
    code_reference = json.loads(args.code_reference)
    test_evidence = json.loads(args.test_evidence)
    metadata = json.loads(args.metadata)
    resolved_findings = json.loads(args.resolved_findings)
    if not isinstance(code_reference, list):
        raise ValueError("code-reference 必须是 JSON 数组")
    if not isinstance(test_evidence, (list, dict)):
        raise ValueError("test-evidence 必须是 JSON 数组或对象")
    if not isinstance(metadata, dict):
        raise ValueError("metadata 必须是 JSON 对象")
    if not isinstance(resolved_findings, list) or any(not isinstance(item, str) for item in resolved_findings):
        raise ValueError("resolved-findings 必须是 finding id 字符串组成的 JSON 数组")
    with connect() as conn:
        issue = issue_row(conn, args.issue_key)
        if issue["status"] != "IN_PROGRESS":
            raise RuntimeError(f"Issue 状态 {issue['status']} 不允许提交 Stage")
        plan_no = active_stage_plan_no(conn, issue["id"])
        if plan_no is None:
            raise RuntimeError("当前没有可执行的 Stage Plan")
        stage = conn.execute(
            "SELECT * FROM issue_stage WHERE issue_id = ? AND plan_no = ? AND stage_no = ?",
            (issue["id"], plan_no, args.stage_no),
        ).fetchone()
        if not stage:
            raise KeyError(f"Stage 不存在: plan={plan_no}, stage={args.stage_no}")
        if stage["status"] != "IN_PROGRESS":
            raise RuntimeError(f"Stage {args.stage_no} 当前为 {stage['status']}，不能提交")
        if int(stage["governance_version"]) >= 2:
            if not stage["prepared_at"]:
                raise RuntimeError("修改业务代码前必须先用 stage-prepare 声明影响范围和历史保护项")
            if not diff_summary or not code_reference or not test_evidence:
                raise ValueError("Stage v2 提交必须提供 --diff-summary、非空 --code-reference 和 --test-evidence")
            previous_findings = loads(stage["review_findings_json"], {level: [] for level in FINDING_LEVELS})
            blocking_ids = {
                str(item.get("id"))
                for level in ("BLOCKER", "MUST")
                for item in previous_findings.get(level, [])
                if isinstance(item, dict) and item.get("id")
            }
            missing_resolutions = sorted(blocking_ids - set(resolved_findings))
            if missing_resolutions:
                raise ValueError(
                    "再次提交必须在 resolved-findings 中逐项确认已处理 BLOCKER/MUST: "
                    + ", ".join(missing_resolutions)
                )
        conn.execute(
            """UPDATE issue_stage
               SET status = 'PENDING_REVIEW', submitted_commit_sha = ?, developer_summary = ?,
                   diff_summary = ?, test_evidence_json = ?, code_reference_json = ?,
                   submission_metadata_json = ?, resolved_findings_json = ?,
                   submitted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                commit_sha, content, diff_summary or None, dumps(test_evidence), dumps(code_reference),
                dumps(metadata), dumps(resolved_findings), stage["id"],
            ),
        )
        activity_metadata = {**metadata, "plan_no": plan_no, "stage_no": args.stage_no,
                             "commit_sha": commit_sha, "diff_summary": diff_summary,
                             "test_evidence": test_evidence, "resolved_findings": resolved_findings}
        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id,
                content, result_status, code_reference_json, metadata_json
            ) VALUES (?, ?, 'STAGE_SUBMITTED', ?, ?, ?, 'PENDING_REVIEW', ?, ?)""",
            (
                issue["id"], issue["current_attempt_no"], operator_type(args.agent), actor_id(args),
                content, dumps(code_reference), dumps(activity_metadata),
            ),
        )
        audit(conn, actor_id(args), "stage.submit", "review_issue", args.issue_key, True)
    print_json({"issue_key": args.issue_key, "plan_no": plan_no, "stage_no": args.stage_no,
                "status": "PENDING_REVIEW"})

def stage_review(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以验收 Stage")
    content = (args.content or "").strip()
    if not content:
        raise ValueError("stage-review 必须通过 --content 记录验收结论")
    with connect() as conn:
        issue = issue_row(conn, args.issue_key)
        if issue["status"] != "IN_PROGRESS":
            raise RuntimeError(f"Issue 状态 {issue['status']} 不允许验收 Stage")
        plan_no = active_stage_plan_no(conn, issue["id"])
        if plan_no is None:
            raise RuntimeError("当前没有可验收的 Stage Plan")
        if args.plan_no is not None and args.plan_no != plan_no:
            raise RuntimeError(f"只能验收当前 Stage Plan #{plan_no}")
        stage = conn.execute(
            "SELECT * FROM issue_stage WHERE issue_id = ? AND plan_no = ? AND stage_no = ?",
            (issue["id"], plan_no, args.stage_no),
        ).fetchone()
        if not stage:
            raise KeyError(f"Stage 不存在: plan={plan_no}, stage={args.stage_no}")
        if stage["status"] != "PENDING_REVIEW":
            raise RuntimeError(f"Stage {args.stage_no} 当前为 {stage['status']}，不能验收")

        review_result: dict[str, Any] | None = None
        baseline: dict[str, Any] | None = None
        decision = args.decision
        if int(stage["governance_version"]) >= 2:
            review_result, passed, review_round = validate_stage_review_result(
                conn, issue["id"], plan_no, stage, args.review_result,
            )
            if decision == "auto":
                decision = "approved" if passed else "rejected"
            blockers = review_result["findings"]["BLOCKER"]
            musts = review_result["findings"]["MUST"]
            if decision == "approved":
                if not passed:
                    raise RuntimeError("存在 BLOCKER/MUST 或验收失败，不能 PASS")
                inherited = [
                    row["stage_no"] for row in conn.execute(
                        """SELECT stage_no FROM issue_stage
                           WHERE issue_id = ? AND plan_no = ? AND stage_no < ? AND status = 'APPROVED'
                           ORDER BY stage_no""",
                        (issue["id"], plan_no, args.stage_no),
                    ).fetchall()
                ]
                baseline = validate_stage_baseline(args.baseline, inherited)
            else:
                if passed:
                    raise RuntimeError("BLOCKER/MUST 已清零且当前验收与历史回归均通过，Inspector 必须 PASS")
                if decision == "redesign" and not blockers:
                    raise ValueError("要求重新设计必须记录至少一个说明整案失效的 BLOCKER")
            findings_json = dumps(review_result["findings"])
            history_json = dumps(review_result["historical_regression"])
            acceptance_json = dumps(review_result["current_acceptance"])
        else:
            if decision == "auto":
                raise ValueError("governance v1 历史 Stage 不支持 auto decision")
            passed = args.decision == "approved"
            review_round = int(stage["review_round"]) + 1
            blockers = []
            musts = []
            findings_json = stage["review_findings_json"]
            history_json = stage["historical_regression_json"]
            acceptance_json = stage["current_acceptance_json"]

        activity_type = "STAGE_APPROVED" if decision == "approved" else "STAGE_REJECTED"
        if decision == "approved":
            conn.execute(
                """UPDATE issue_stage
                   SET status = 'APPROVED', review_comment = ?, review_round = ?,
                       review_findings_json = ?, historical_regression_json = ?,
                       current_acceptance_json = ?, baseline_json = ?, baseline_status = 'PASSED',
                       baseline_established_at = CURRENT_TIMESTAMP, approved_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (
                    content, review_round, findings_json, history_json, acceptance_json,
                    dumps(baseline or {}), stage["id"],
                ),
            )
            next_stage = conn.execute(
                """SELECT id, stage_no FROM issue_stage
                   WHERE issue_id = ? AND plan_no = ? AND status = 'PLANNED'
                   ORDER BY stage_no LIMIT 1""",
                (issue["id"], plan_no),
            ).fetchone()
            if next_stage:
                conn.execute(
                    "UPDATE issue_stage SET status = 'IN_PROGRESS', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (next_stage["id"],),
                )
            result_status = "APPROVED"
        elif decision == "rejected":
            conn.execute(
                """UPDATE issue_stage
                   SET status = 'IN_PROGRESS', review_comment = ?, review_round = ?,
                       review_findings_json = ?, historical_regression_json = ?,
                       current_acceptance_json = ?, approved_at = NULL,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (content, review_round, findings_json, history_json, acceptance_json, stage["id"]),
            )
            next_stage = None
            result_status = "IN_PROGRESS"
        else:
            # 阶段验收发现整个设计不成立：同一事务中驳回、废弃计划并回到重设计。
            conn.execute(
                """UPDATE issue_stage
                   SET review_comment = ?, review_round = ?, review_findings_json = ?,
                       historical_regression_json = ?, current_acceptance_json = ?,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (content, review_round, findings_json, history_json, acceptance_json, stage["id"]),
            )
            next_stage = None
            result_status = "REDESIGN_REQUIRED"

        activity_metadata = {
            "plan_no": plan_no, "stage_no": args.stage_no, "decision": decision,
            "requested_decision": args.decision,
            "review_round": review_round, "inspection_result": "PASS" if passed else "FAIL",
            "final_decision": "PASS" if decision == "approved" else "REJECT",
        }
        if review_result is not None:
            activity_metadata["review_result"] = review_result
        if baseline is not None:
            activity_metadata["baseline"] = baseline
        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id,
                content, result_status, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                issue["id"], issue["current_attempt_no"], activity_type,
                operator_type(args.agent), actor_id(args), content, result_status,
                dumps(activity_metadata),
            ),
        )
        if decision == "redesign":
            supersede_active_stage_plan(conn, args, issue, content)
            conn.execute(
                "UPDATE review_issue SET status = 'REDESIGN_REQUIRED', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (issue["id"],),
            )
        audit(conn, actor_id(args), "stage.review", "review_issue", args.issue_key, True)
    print_json({
        "issue_key": args.issue_key, "plan_no": plan_no, "stage_no": args.stage_no,
        "decision": decision, "requested_decision": args.decision, "status": result_status,
        "inspection_result": "PASS" if passed else "FAIL",
        "final_decision": "PASS" if decision == "approved" else "REJECT",
        "review_round": review_round,
        "blocking_counts": {"BLOCKER": len(blockers), "MUST": len(musts)},
        "next_stage_no": next_stage["stage_no"] if next_stage else None,
    })

def human_escalate(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent != "inspector":
        raise PermissionError("只有 inspector 可以发起 Human 最终确认")
    reason = (args.reason or "").strip()
    question = (args.question or "").strip()
    if not reason or not question:
        raise ValueError("human-escalate 的 --reason 和 --question 均不能为空")
    options = json.loads(args.options)
    evidence = json.loads(args.evidence)
    if not isinstance(options, list):
        raise ValueError("options 必须是 JSON 数组")
    if not isinstance(evidence, list):
        raise ValueError("evidence 必须是 JSON 数组")
    metadata = {
        "reason": reason,
        "question": question,
        "options": options,
        "recommended_option": args.recommended_option,
        "evidence": evidence,
    }
    allowed_sources = {
        "PROPOSED", "DESIGN_REQUIRED", "DESIGN_PENDING_REVIEW", "IN_PROGRESS",
        "ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED",
        "IMPLEMENTED_PENDING_REVIEW", "REDESIGN_REQUIRED",
    }
    with connect() as conn:
        row = conn.execute(
            "SELECT id, status, current_attempt_no FROM review_issue WHERE issue_key = ?",
            (args.issue_key,),
        ).fetchone()
        if not row:
            raise KeyError(f"问题不存在: {args.issue_key}")
        if row["status"] not in allowed_sources:
            raise RuntimeError(f"状态 {row['status']} 不允许升级 Human")
        content = f"为什么必须人工决定：{reason}\n\nHuman 只需回答：{question}"
        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id,
                content, result_status, code_reference_json, metadata_json
            ) VALUES (?, ?, 'HUMAN_CONFIRMATION_REQUESTED', ?, ?, ?,
                      'HUMAN_CONFIRMATION_REQUIRED', ?, ?)""",
            (
                row["id"], row["current_attempt_no"], operator_type(args.agent), actor_id(args),
                content, dumps(evidence), dumps(metadata),
            ),
        )
        conn.execute(
            """UPDATE review_issue
               SET status = 'HUMAN_CONFIRMATION_REQUIRED', updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (row["id"],),
        )
        audit(conn, actor_id(args), "human.escalate", "review_issue", args.issue_key, True)
    print_json({
        "issue_key": args.issue_key, "status": "HUMAN_CONFIRMATION_REQUIRED",
        "attempt_no": row["current_attempt_no"], "activity_type": "HUMAN_CONFIRMATION_REQUESTED",
    })

def human_confirmation_resolve(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent != "human":
        raise PermissionError("只有 human 可以提交最终人工决定")
    decision = (args.decision or "").strip()
    content = (args.content or "").strip()
    if not decision or not content:
        raise ValueError("human-confirmation-resolve 的 --decision 和 --content 均不能为空")
    next_status = args.next_status or "DESIGN_REQUIRED"
    allowed_next = {"DESIGN_REQUIRED", "IN_PROGRESS", "ON_HOLD", "BLOCKED", "CANCELLED"}
    require_choice(next_status, allowed_next, "next_status")
    with connect() as conn:
        row = conn.execute(
            "SELECT id, status, current_attempt_no FROM review_issue WHERE issue_key = ?",
            (args.issue_key,),
        ).fetchone()
        if not row:
            raise KeyError(f"问题不存在: {args.issue_key}")
        if row["status"] != "HUMAN_CONFIRMATION_REQUIRED":
            raise RuntimeError(
                f"只有 HUMAN_CONFIRMATION_REQUIRED 可以提交人工决定，当前为 {row['status']}"
            )
        metadata = {"decision": decision, "next_status": next_status}
        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id,
                content, result_status, metadata_json
            ) VALUES (?, ?, 'HUMAN_CONFIRMATION_PROVIDED', ?, ?, ?, ?, ?)""",
            (
                row["id"], row["current_attempt_no"], operator_type(args.agent), actor_id(args),
                content, next_status, dumps(metadata),
            ),
        )
        conn.execute(
            "UPDATE review_issue SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (next_status, row["id"]),
        )
        if next_status == "DESIGN_REQUIRED":
            supersede_active_stage_plan(conn, args, row, content)
        audit(conn, actor_id(args), "human.confirmation-resolve", "review_issue", args.issue_key, True)
    print_json({
        "issue_key": args.issue_key, "status": next_status, "decision": decision,
        "attempt_no": row["current_attempt_no"], "activity_type": "HUMAN_CONFIRMATION_PROVIDED",
    })

def implementation_submit(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"developer", "human"}:
        raise PermissionError("只有 developer 或 human 可以提交实现")
    code_reference = json.loads(args.code_reference)
    metadata = json.loads(args.metadata)
    if not isinstance(code_reference, list):
        raise ValueError("code-reference 必须是 JSON 数组")
    if not isinstance(metadata, dict):
        raise ValueError("metadata 必须是 JSON 对象")
    with connect() as conn:
        row = conn.execute(
            "SELECT id, status, current_attempt_no FROM review_issue WHERE issue_key = ?",
            (args.issue_key,),
        ).fetchone()
        if not row:
            raise KeyError(f"问题不存在: {args.issue_key}")
        if row["status"] not in {"PROPOSED", "IN_PROGRESS"}:
            raise RuntimeError(
                f"状态 {row['status']} 禁止提交实现；设计阶段必须先完成 design-review approved"
            )
        plan_no = active_stage_plan_no(conn, row["id"])
        if plan_no is not None:
            incomplete = conn.execute(
                """SELECT stage_no, status FROM issue_stage
                   WHERE issue_id = ? AND plan_no = ? AND status != 'APPROVED'
                   ORDER BY stage_no""",
                (row["id"], plan_no),
            ).fetchall()
            if incomplete:
                summary = ", ".join(f"Stage {item['stage_no']}={item['status']}" for item in incomplete)
                raise RuntimeError(f"Stage Plan #{plan_no} 尚未全部验收通过: {summary}")
        next_attempt = row["current_attempt_no"] + 1
        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id,
                content, code_reference_json, metadata_json
            ) VALUES (?, ?, 'IMPLEMENTATION_SUBMITTED', ?, ?, ?, ?, ?)""",
            (
                row["id"], next_attempt, operator_type(args.agent), actor_id(args), args.content,
                dumps(code_reference), dumps(metadata),
            ),
        )
        result = apply_status_update(
            conn, args, args.issue_key, "IMPLEMENTED_PENDING_REVIEW", args.status_content
        )
        audit(conn, actor_id(args), "implementation.submit", "review_issue", args.issue_key, True)
    print_json({**result, "activity_type": "IMPLEMENTATION_SUBMITTED"})

def activity_append(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.activity_type not in ALLOWED_ACTIVITY_BY_AGENT[args.agent]:
        raise PermissionError(f"agent {args.agent} 无权追加活动 {args.activity_type}")

    with connect() as conn:
        row = conn.execute(
            "SELECT id, current_attempt_no FROM review_issue WHERE issue_key = ?",
            (args.issue_key,),
        ).fetchone()
        if not row:
            raise KeyError(f"问题不存在: {args.issue_key}")
        if args.attempt_no is not None and args.attempt_no < 0:
            raise ValueError("attempt_no 不能为负数")
        attempt_no = args.attempt_no if args.attempt_no is not None else row["current_attempt_no"]
        if attempt_no > row["current_attempt_no"]:
            raise ValueError("attempt_no 不能大于当前尝试次数")

        conn.execute(
            """INSERT INTO issue_activity(
                issue_id, attempt_no, activity_type, operator_type, operator_id,
                content, result_status, code_reference_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"], attempt_no,
                args.activity_type,
                {"inspector":"INSPECTOR_AGENT","developer":"DEVELOPMENT_AGENT","human":"HUMAN"}[args.agent],
                actor_id(args), args.content, args.result_status,
                dumps(json.loads(args.code_reference)),
                dumps(json.loads(args.metadata)),
            ),
        )
        audit(conn, actor_id(args), "activity.append", "issue_activity", args.issue_key, True)
    print_json({"issue_key": args.issue_key, "activity_type": args.activity_type})

def activity_list(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    with connect() as conn:
        rows = conn.execute(
            """SELECT a.*
               FROM issue_activity a
               JOIN review_issue i ON i.id = a.issue_id
               WHERE i.issue_key = ?
               ORDER BY a.created_at ASC, a.id ASC""",
            (args.issue_key,),
        ).fetchall()
        audit(conn, actor_id(args), "activity.list", "issue_activity", args.issue_key, True, f"count={len(rows)}")
    result = []
    for r in rows:
        item = dict(r)
        item["code_reference_json"] = loads(item["code_reference_json"], [])
        item["metadata_json"] = loads(item["metadata_json"], {})
        result.append(item)
    print_json(result)

def normalize_since(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("since 必须是 ISO 8601 时间，例如 2026-07-22T10:30:00+08:00") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(sep=" ", timespec="seconds")

def activity_list_recent(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if not 1 <= args.limit <= 1000:
        raise ValueError("limit 必须在 1 到 1000 之间")
    sql = """
        SELECT a.*, i.issue_key, i.title AS issue_title, i.status AS issue_status, t.task_key
        FROM issue_activity a
        JOIN review_issue i ON i.id = a.issue_id
        JOIN review_task t ON t.id = i.task_id
        WHERE 1=1
    """
    params: list[Any] = []
    if args.task_key:
        sql += " AND t.task_key = ?"
        params.append(args.task_key)
    if args.activity_type:
        sql += " AND a.activity_type = ?"
        params.append(args.activity_type)
    if args.since:
        sql += " AND a.created_at >= ?"
        params.append(normalize_since(args.since))
    sql += " ORDER BY a.created_at DESC, a.id DESC LIMIT ?"
    params.append(args.limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        audit(conn, actor_id(args), "activity.list-recent", "issue_activity", args.task_key, True, f"count={len(rows)}")
    result = []
    for row in rows:
        item = dict(row)
        item["code_reference_json"] = loads(item["code_reference_json"], [])
        item["metadata_json"] = loads(item["metadata_json"], {})
        result.append(item)
    print_json(result)

CANDIDATE_STATUSES = {"SUBMITTED", "UNDER_REVIEW", "ACCEPTED", "REJECTED"}
CANDIDATE_TRANSITIONS = {
    "SUBMITTED": {"UNDER_REVIEW", "ACCEPTED", "REJECTED"},
    "UNDER_REVIEW": {"ACCEPTED", "REJECTED"},
    "ACCEPTED": set(),
    "REJECTED": set(),
}

def candidate_submit(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"developer", "human"}:
        raise PermissionError("只有 developer 或 human 可以提交候选问题")
    evidence = json.loads(args.evidence)
    if not isinstance(evidence, list):
        raise ValueError("evidence 必须是 JSON 数组")
    candidate_key = args.candidate_key or f"RC-{uuid.uuid4().hex[:8].upper()}"
    with connect() as conn:
        task = conn.execute(
            "SELECT id, status FROM review_task WHERE task_key = ?", (args.task_key,)
        ).fetchone()
        if not task:
            raise KeyError(f"任务不存在: {args.task_key}")
        if task["status"] in {"CLOSED", "CANCELLED"}:
            raise RuntimeError("已关闭或取消的任务不能提交候选问题")
        conn.execute(
            """INSERT INTO issue_candidate(
                candidate_key, task_id, title, description, facts, rationale, evidence_json,
                suggested_dimension, suggested_severity, suggested_confidence, submitted_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate_key, task["id"], args.title, args.description, args.facts, args.rationale,
                dumps(evidence), args.suggested_dimension, args.suggested_severity,
                args.suggested_confidence, actor_id(args),
            ),
        )
        audit(conn, actor_id(args), "candidate.submit", "issue_candidate", candidate_key, True)
    print_json({"candidate_key": candidate_key, "task_key": args.task_key, "status": "SUBMITTED"})

def candidate_list(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if not 1 <= args.limit <= 1000:
        raise ValueError("limit 必须在 1 到 1000 之间")
    sql = """
        SELECT c.*, t.task_key, t.project_name
        FROM issue_candidate c
        JOIN review_task t ON t.id = c.task_id
        WHERE 1=1
    """
    params: list[Any] = []
    if args.task_key:
        sql += " AND t.task_key = ?"
        params.append(args.task_key)
    if args.status:
        sql += " AND c.status = ?"
        params.append(args.status)
    else:
        sql += " AND c.status IN ('SUBMITTED', 'UNDER_REVIEW')"
    if args.updated_after:
        sql += " AND c.updated_at >= ?"
        params.append(normalize_since(args.updated_after))
    sql += " ORDER BY c.updated_at DESC, c.id DESC LIMIT ?"
    params.append(args.limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        audit(conn, actor_id(args), "candidate.list", "issue_candidate", args.task_key, True, f"count={len(rows)}")
    result = []
    for row in rows:
        item = dict(row)
        item["evidence_json"] = loads(item["evidence_json"], [])
        result.append(item)
    print_json(result)

def candidate_update_status(args: argparse.Namespace) -> None:
    require_agent(args.agent)
    if args.agent not in {"inspector", "human"}:
        raise PermissionError("只有 inspector 或 human 可以审核候选问题")
    if args.status in {"ACCEPTED", "REJECTED"} and not (args.content or "").strip():
        raise ValueError("接受或拒绝候选问题时必须通过 --content 记录审核结论")
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM issue_candidate WHERE candidate_key = ?", (args.candidate_key,)
        ).fetchone()
        if not row:
            raise KeyError(f"候选问题不存在: {args.candidate_key}")
        if args.status not in CANDIDATE_TRANSITIONS[row["status"]]:
            raise RuntimeError(f"不允许候选问题状态流转: {row['status']} -> {args.status}")
        final = args.status in {"ACCEPTED", "REJECTED"}
        conn.execute(
            """UPDATE issue_candidate
               SET status = ?, reviewed_by = ?, review_comment = ?,
                   reviewed_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE reviewed_at END,
                   updated_at = CURRENT_TIMESTAMP
               WHERE candidate_key = ?""",
            (args.status, actor_id(args), args.content, 1 if final else 0, args.candidate_key),
        )
        audit(conn, actor_id(args), "candidate.update-status", "issue_candidate", args.candidate_key, True)
    print_json({"candidate_key": args.candidate_key, "status": args.status})

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Code Inspector 本地数据工具")
    parser.add_argument("--agent", required=True, choices=["inspector", "developer", "human"])
    parser.add_argument("--operator-id", help="安装器传入的逻辑执行身份")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("task-create")
    p.add_argument("--task-key")
    p.add_argument("--title", required=True)
    p.add_argument("--objective", required=True)
    p.add_argument("--review-level", choices=["L1", "L2", "L3"])
    p.add_argument("--review-scope")
    p.add_argument("--baseline-ref")
    p.add_argument("--started-at")
    p.add_argument("--remark")
    p.add_argument("--task-type", choices=sorted(TASK_TYPES), default="REVIEW")
    p.set_defaults(func=task_create)

    p = sub.add_parser("task-resolve")
    p.add_argument("--task-key")
    p.add_argument("--title", required=True)
    p.add_argument("--objective", required=True)
    p.add_argument("--review-level", required=True, choices=["L1", "L2", "L3"])
    p.add_argument("--review-scope", required=True)
    p.add_argument("--baseline-ref")
    p.add_argument("--remark")
    p.add_argument("--task-type", choices=sorted(TASK_TYPES), default="REVIEW")
    p.set_defaults(func=task_resolve)

    p = sub.add_parser("task-list")
    p.add_argument("--status", choices=sorted(TASK_STATUSES))
    p.add_argument("--project-name")
    p.add_argument("--task-type", choices=sorted(TASK_TYPES))
    p.add_argument("--include-closed", action="store_true")
    p.set_defaults(func=task_list)

    p = sub.add_parser("task-update-status")
    p.add_argument("--task-key", required=True)
    p.add_argument("--status", required=True, choices=sorted(TASK_STATUSES))
    p.add_argument("--started-at")
    p.add_argument("--finished-at")
    p.add_argument("--close-reason")
    p.add_argument("--remark")
    p.set_defaults(func=task_update_status)

    p = sub.add_parser("task-update")
    p.add_argument("--task-key", required=True)
    p.add_argument("--title")
    p.add_argument("--objective")
    p.add_argument("--remark")
    p.add_argument("--close-reason")
    p.set_defaults(func=task_update)

    p = sub.add_parser("version-create")
    p.add_argument("--task-key", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=version_create)

    p = sub.add_parser("issue-create")
    p.add_argument("--task-key", required=True)
    p.add_argument("--issue-key")
    p.add_argument("--parent-issue-id", type=int)
    p.add_argument("--title", required=True)
    p.add_argument("--dimension", required=True)
    p.add_argument("--severity", required=True)
    p.add_argument("--remediation-benefit", required=True)
    p.add_argument("--remediation-cost", required=True)
    p.add_argument("--disposition", required=True)
    p.add_argument("--confidence", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--facts", required=True)
    p.add_argument("--trigger-conditions", default="[]")
    p.add_argument("--potential-impact", default="[]")
    p.add_argument("--impact-scope", default="[]")
    p.add_argument("--rationale", required=True)
    p.add_argument("--evidence", default="[]")
    p.add_argument("--estimated-change", default="{}")
    p.add_argument("--dedupe-key")
    p.set_defaults(func=issue_create)

    p = sub.add_parser("issue-create-batch")
    p.add_argument("--task-key", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--issues", required=True, help="JSON array of issue objects")
    p.set_defaults(func=issue_create_batch)

    p = sub.add_parser("issue-list")
    p.add_argument("--task-key")
    p.add_argument("--status")
    p.add_argument("--severity")
    p.add_argument("--dimension")
    p.add_argument("--updated-after")
    p.add_argument("--limit", type=int)
    p.add_argument("--fields", help="comma-separated output fields")
    p.set_defaults(func=issue_list)

    p = sub.add_parser("issue-list-pending-review")
    p.add_argument("--task-key")
    p.add_argument("--updated-after")
    p.add_argument("--limit", type=int)
    p.add_argument("--fields", help="comma-separated output fields")
    p.set_defaults(func=issue_list_pending_review)

    p = sub.add_parser("issue-get")
    p.add_argument("--issue-key", required=True)
    p.set_defaults(func=issue_get)

    p = sub.add_parser("issue-update-status")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--status", required=True, choices=sorted(ALLOWED_TRANSITIONS))
    p.add_argument("--content")
    p.set_defaults(func=issue_update_status)

    p = sub.add_parser("issue-update-status-batch")
    p.add_argument("--updates", required=True, help="JSON array of status updates")
    p.set_defaults(func=issue_update_status_batch)

    p = sub.add_parser("implementation-submit")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--status-content")
    p.add_argument("--code-reference", default="[]")
    p.add_argument("--metadata", default="{}")
    p.set_defaults(func=implementation_submit)

    p = sub.add_parser("design-request")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--content", required=True)
    p.set_defaults(func=design_request)

    p = sub.add_parser("design-submit")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--code-reference", default="[]")
    p.add_argument("--metadata", default="{}")
    p.set_defaults(func=design_submit)

    p = sub.add_parser("design-review")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--decision", required=True, choices=["approved", "rejected"])
    p.add_argument("--content", required=True)
    p.set_defaults(func=design_review)

    p = sub.add_parser("stage-plan-create")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--stages", required=True, help="JSON array of ordered Stage definitions")
    p.set_defaults(func=stage_plan_create)

    p = sub.add_parser("stage-list")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--plan-no", type=int)
    p.set_defaults(func=stage_list)

    p = sub.add_parser("stage-get")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--stage-no", required=True, type=int)
    p.add_argument("--plan-no", type=int)
    p.set_defaults(func=stage_get)

    p = sub.add_parser("stage-prepare")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--stage-no", required=True, type=int)
    p.add_argument("--change-scope", required=True, help="JSON object/list of modules, files or classes")
    p.add_argument("--change-reason", required=True)
    p.add_argument("--protected-behaviors", default="[]", help="JSON array of historical behavior contracts")
    p.set_defaults(func=stage_prepare)

    p = sub.add_parser("stage-submit")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--stage-no", required=True, type=int)
    p.add_argument("--content", required=True)
    p.add_argument("--commit-sha", required=True)
    p.add_argument("--diff-summary")
    p.add_argument("--code-reference", default="[]")
    p.add_argument("--test-evidence", default="[]")
    p.add_argument("--resolved-findings", default="[]", help="JSON array of resolved BLOCKER/MUST finding ids")
    p.add_argument("--metadata", default="{}")
    p.set_defaults(func=stage_submit)

    p = sub.add_parser("stage-review")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--stage-no", required=True, type=int)
    p.add_argument("--plan-no", type=int)
    p.add_argument("--decision", required=True, choices=["auto", "approved", "rejected", "redesign"])
    p.add_argument("--content", required=True)
    p.add_argument("--review-result", default="{}", help="structured findings/regression/acceptance JSON")
    p.add_argument("--baseline", default="{}", help="approved Stage baseline contract JSON")
    p.set_defaults(func=stage_review)

    p = sub.add_parser("human-escalate")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--options", default="[]")
    p.add_argument("--evidence", default="[]")
    p.add_argument("--recommended-option")
    p.set_defaults(func=human_escalate)

    p = sub.add_parser("human-confirmation-resolve")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--decision", required=True)
    p.add_argument("--content", required=True)
    p.add_argument(
        "--next-status", choices=["DESIGN_REQUIRED", "IN_PROGRESS", "ON_HOLD", "BLOCKED", "CANCELLED"],
    )
    p.set_defaults(func=human_confirmation_resolve)

    p = sub.add_parser("issue-update-assessment")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--dimension")
    p.add_argument("--severity")
    p.add_argument("--remediation-benefit")
    p.add_argument("--remediation-cost")
    p.add_argument("--disposition")
    p.add_argument("--confidence")
    p.set_defaults(func=issue_update_assessment)

    p = sub.add_parser("issue-update-assessment-batch")
    p.add_argument("--updates", required=True, help="JSON array of assessment updates")
    p.set_defaults(func=issue_update_assessment_batch)

    p = sub.add_parser("issue-update-body")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--facts")
    p.add_argument("--rationale")
    p.set_defaults(func=issue_update_body)

    p = sub.add_parser("activity-append")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--activity-type", required=True)
    p.add_argument("--attempt-no", type=int)
    p.add_argument("--content", required=True)
    p.add_argument("--result-status")
    p.add_argument("--code-reference", default="[]")
    p.add_argument("--metadata", default="{}")
    p.set_defaults(func=activity_append)

    p = sub.add_parser("activity-list")
    p.add_argument("--issue-key", required=True)
    p.set_defaults(func=activity_list)

    p = sub.add_parser("activity-list-recent")
    p.add_argument("--task-key")
    p.add_argument("--activity-type", choices=sorted(ALLOWED_ACTIVITY_TYPES))
    p.add_argument("--since")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=activity_list_recent)

    p = sub.add_parser("candidate-submit")
    p.add_argument("--task-key", required=True)
    p.add_argument("--candidate-key")
    p.add_argument("--title", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--facts", required=True)
    p.add_argument("--rationale", required=True)
    p.add_argument("--evidence", default="[]")
    p.add_argument("--suggested-dimension", choices=sorted(ALLOWED_DIMENSIONS))
    p.add_argument("--suggested-severity", choices=sorted(ALLOWED_SEVERITIES))
    p.add_argument("--suggested-confidence", choices=sorted(ALLOWED_CONFIDENCE))
    p.set_defaults(func=candidate_submit)

    p = sub.add_parser("candidate-list")
    p.add_argument("--task-key")
    p.add_argument("--status", choices=sorted(CANDIDATE_STATUSES))
    p.add_argument("--updated-after")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=candidate_list)

    p = sub.add_parser("candidate-update-status")
    p.add_argument("--candidate-key", required=True)
    p.add_argument("--status", required=True, choices=["UNDER_REVIEW", "ACCEPTED", "REJECTED"])
    p.add_argument("--content")
    p.set_defaults(func=candidate_update_status)

    return parser

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_actor_binding(args)
        args.func(args)
        return 0
    except Exception as exc:
        try:
            with connect() as conn:
                audit(
                    conn, actor_id(args), "command.failed", "command", getattr(args, "command", None),
                    False, str(exc),
                )
        except Exception:
            pass
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
