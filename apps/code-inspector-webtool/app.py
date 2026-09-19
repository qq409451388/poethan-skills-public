"""Code Inspector 本机 Human 工作台。

SQLite 只用于页面查询；所有写操作必须经过安装后的 review-db.py human 命令。
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import secrets
import traceback
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, abort, redirect, render_template, request, session, url_for
from markupsafe import Markup

from commands import run_human_command, run_runtime_command
from db import parse_json_field, query_all, query_one
import routing

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.config["JSON_SORT_KEYS"] = False
app.secret_key = os.environ.get("WEBTOOL_SECRET_KEY") or secrets.token_hex(32)
app.config["CSRF_ENABLED"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def verify_csrf():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and app.config.get("CSRF_ENABLED", True):
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not supplied or not secrets.compare_digest(supplied, csrf_token()):
            abort(403, "CSRF token invalid")

DISPLAY_TIMEZONE_NAME = os.environ.get("CODE_INSPECTOR_TIMEZONE", "Asia/Shanghai")
try:
    DISPLAY_TIMEZONE = ZoneInfo(DISPLAY_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    DISPLAY_TIMEZONE = timezone.utc

TASK_STATUSES = ["PENDING", "IN_PROGRESS", "ON_HOLD", "BLOCKED", "CLOSED", "CANCELLED"]
TASK_TYPES = ["REVIEW", "CONTINUOUS"]
ISSUE_STATUSES = [
    "PROPOSED", "DESIGN_REQUIRED", "DESIGN_PENDING_REVIEW", "IN_PROGRESS", "ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED",
    "HUMAN_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW", "REDESIGN_REQUIRED", "CONFIRMED", "CANCELLED",
]
# “已完成”是列表展示概念，不等同于某个具体工作流状态。以后新增完成态时只需扩展这里。
COMPLETED_ISSUE_STATUSES = ("CONFIRMED",)
NON_OPEN_ISSUE_STATUSES = (*COMPLETED_ISSUE_STATUSES, "CANCELLED")
CANDIDATE_STATUSES = ["SUBMITTED", "UNDER_REVIEW", "ACCEPTED", "REJECTED"]
DIMENSIONS = [
    ("functional_correctness", "功能正确性"), ("data_security", "数据一致性与安全"),
    ("stability_concurrency", "稳定性与并发"), ("performance", "性能"),
    ("architecture_extensibility", "架构与扩展性"), ("code_quality", "代码质量"),
    ("test_observability", "测试与可观测性"),
]
SEVERITIES = [("critical", "致命"), ("high", "高"), ("medium", "中"), ("low", "低")]
BENEFITS = [("high", "高"), ("medium", "中"), ("low", "低")]
COSTS = [("low", "低"), ("medium", "中"), ("high", "高"), ("extreme", "极高")]
CONFIDENCE = [("high", "高"), ("medium", "中"), ("low", "低")]
DISPOSITIONS = [
    ("immediate_fix", "立即修复"), ("current_iteration", "本次迭代修复"),
    ("near_term_iteration", "纳入近期迭代"), ("special_governance", "专项治理"),
    ("opportunistic_fix", "随手修复"), ("observe", "持续观察"),
    ("defer", "暂不处理"), ("business_confirmation", "需要业务确认"),
]
ACTIVITY_TYPES = [
    ("COMMENT_ADDED", "补充说明"), ("EVIDENCE_ADDED", "补充证据"),
    ("DESIGN_GUIDANCE", "设计指导"),
    ("INSPECTOR_CONFIRMATION_PROVIDED", "审核确认结论"),
    ("VERIFICATION_PASSED", "验证通过"), ("VERIFICATION_FAILED", "验证失败"),
    ("VERIFICATION_EVIDENCE_ADDED", "补充验证证据"),
]
DISCUSSION_TOPICS = [
    ("GENERAL", "一般讨论"), ("DESIGN", "方案讨论"),
    ("IMPLEMENTATION", "实现讨论"), ("VERIFICATION", "验证讨论"),
]
HISTORY_MILESTONE_TYPES = {
    "ISSUE_CREATED", "DESIGN_REQUESTED", "DESIGN_SUBMITTED", "REDESIGN_SUBMITTED",
    "DESIGN_APPROVED", "DESIGN_REJECTED", "STAGE_PLAN_CREATED", "STAGE_SUBMITTED",
    "STAGE_APPROVED", "STAGE_REJECTED", "STAGE_PLAN_SUPERSEDED",
    "IMPLEMENTATION_SUBMITTED", "REVIEW_APPROVED", "REVIEW_REJECTED",
    "VERIFICATION_PASSED", "VERIFICATION_FAILED", "INSPECTOR_CONFIRMATION_PROVIDED",
    "HUMAN_CONFIRMATION_REQUESTED", "HUMAN_CONFIRMATION_PROVIDED", "STATUS_CHANGED",
}
COLLABORATIVE_SUBMISSION_TYPES = {
    "DESIGN_SUBMITTED", "REDESIGN_SUBMITTED", "STAGE_SUBMITTED", "IMPLEMENTATION_SUBMITTED",
}
TOPIC_LABELS = dict(DISCUSSION_TOPICS)
DECISION_LABELS = {
    "DESIGN_REVIEW": "设计结论", "STAGE_REVIEW": "阶段验收结论",
    "IMPLEMENTATION_REVIEW": "实现审核结论", "SCOPE_CONFIRMATION": "边界确认",
    "HUMAN_CONFIRMATION": "人工决定", "DISCUSSION_CONCLUSION": "讨论结论",
    "VERIFICATION": "验证结论",
}
LABELS = {
    **dict(DIMENSIONS), **dict(SEVERITIES), **dict(BENEFITS), **dict(COSTS),
    **dict(CONFIDENCE), **dict(DISPOSITIONS),
    "PENDING": "待开始", "IN_PROGRESS": "进行中", "ON_HOLD": "已搁置", "BLOCKED": "受阻",
    "CLOSED": "已关闭", "CANCELLED": "已取消", "PROPOSED": "待处理",
    "REVIEW": "检查任务", "CONTINUOUS": "持续治理",
    "DESIGN_REQUIRED": "需要设计", "DESIGN_PENDING_REVIEW": "设计待审核",
    "INSPECTOR_CONFIRMATION_REQUIRED": "待 Inspector 确认", "HUMAN_CONFIRMATION_REQUIRED": "需要人工确认",
    "IMPLEMENTED_PENDING_REVIEW": "待审核",
    "REDESIGN_REQUIRED": "需要重新设计", "CONFIRMED": "已确认",
    "SUBMITTED": "待审核", "UNDER_REVIEW": "审核中", "ACCEPTED": "已接受", "REJECTED": "已拒绝",
    "ISSUE_CREATED": "创建问题", "COMMENT_ADDED": "补充说明", "EVIDENCE_ADDED": "补充证据",
    "DESIGN_REQUESTED": "要求设计", "DESIGN_GUIDANCE": "设计指导",
    "DESIGN_SUBMITTED": "提交设计", "DESIGN_APPROVED": "设计批准", "DESIGN_REJECTED": "设计驳回",
    "STAGE_PLAN_CREATED": "创建执行计划", "STAGE_SCOPE_DECLARED": "声明阶段影响范围",
    "STAGE_SUBMITTED": "提交阶段实现",
    "STAGE_APPROVED": "阶段验收通过", "STAGE_REJECTED": "阶段验收驳回",
    "STAGE_PLAN_SUPERSEDED": "执行计划已废弃",
    "HUMAN_CONFIRMATION_REQUESTED": "请求人工最终确认", "HUMAN_CONFIRMATION_PROVIDED": "人工决定已提供",
    "IMPLEMENTATION_SUBMITTED": "提交实现",
    "REVIEW_APPROVED": "审核通过", "REVIEW_REJECTED": "审核驳回",
    "REDESIGN_SUBMITTED": "重新提交设计", "INSPECTOR_CONFIRMATION_PROVIDED": "审核确认结论",
    "VERIFICATION_PASSED": "验证通过", "VERIFICATION_FAILED": "验证失败",
    "VERIFICATION_EVIDENCE_ADDED": "补充验证证据", "STATUS_CHANGED": "状态变更",
    "INSPECTOR_AGENT": "Inspector", "DEVELOPMENT_AGENT": "Developer", "HUMAN": "Human",
    "SYSTEM": "System", "VERIFIER_AGENT": "Verifier",
    "PLANNED": "待开始", "PENDING_REVIEW": "待验收", "APPROVED": "已验收", "SUPERSEDED": "已废弃",
    "GENERAL": "一般讨论", "DESIGN": "方案讨论", "IMPLEMENTATION": "实现讨论", "VERIFICATION": "验证讨论",
    **TOPIC_LABELS, **DECISION_LABELS,
    "APPROVED": "通过", "REJECTED": "未通过", "PROVIDED": "已确认",
}

RUNTIME_LABELS = {
    "inspector": "检查者", "developer": "开发者",
    "INITIALIZING": "准备中", "ACTIVE": "正在执行", "WAITING": "等待新任务",
    "PAUSED": "已暂停", "COMPLETED": "已完成", "FAILED": "执行失败", "ARCHIVED": "已归档",
    "PENDING": "等待处理", "PROCESSING": "正在处理", "DONE": "处理完成",
    "SUPERSEDED": "已被最新状态覆盖",
    "RETRYABLE": "可安全重试", "NON_RETRYABLE": "不可自动重试",
    "AMBIGUOUS": "执行结果不确定",
    "INIT": "初始化", "ACTION": "业务执行", "COMPACT": "上下文整理",
    "SESSION_SCOPE_VIOLATION": "会话身份范围不匹配",
    "AMBIGUOUS_DISPATCH": "执行结果不确定",
    "PRE_ACTION_RETRYABLE": "尚未执行，可重试",
    "STALE_ACTIVE_AMBIGUOUS": "执行租约过期，结果待核对",
    "COMPACT_FAILED": "上下文整理失败", "ARCHIVE_FAILED": "线程归档失败",
}
RUNTIME_THREAD_STATUSES = ["INITIALIZING", "ACTIVE", "WAITING", "PAUSED", "COMPLETED", "FAILED", "ARCHIVED"]
RUNTIME_EVENT_STATUSES = ["PENDING", "PROCESSING", "DONE", "FAILED", "SUPERSEDED"]
RUNTIME_PROCESSING_TIMEOUT_MINUTES = 10
TOKEN_ATTENTION_MINIMUM = 10_000
WAKEUP_ATTENTION_MINIMUM = 5

# 浏览器只关心会改变任务、问题或 Stage 展示结果的写操作。
# 查询、列表等只读审计不进入变化流，避免页面收到无意义通知。
BROWSER_CHANGE_ACTIONS = (
    "task.create", "task.update", "task.update-status", "version.create",
    "issue.create", "issue.create-batch", "issue.update-assessment",
    "issue.update-assessment-batch", "issue.update-body", "issue.update-status",
    "issue.update-status-batch", "design.request", "design.submit", "design.review",
    "stage.plan-create", "stage.prepare", "stage.submit", "stage.review",
    "implementation.submit", "human.escalate", "human.confirmation-resolve",
)

STATUS_PRESENTATION = {
    "PROPOSED": (1, "问题已经记录，等待 Developer 开始处理。"),
    "DESIGN_REQUIRED": (2, "Inspector / Human 已要求先完成方案讨论，Developer 当前不得编码。"),
    "DESIGN_PENDING_REVIEW": (2, "Developer 已提交方案，等待 Inspector / Human 审核。"),
    "IN_PROGRESS": (3, "Developer 正在按已对齐的方向实现修复。"),
    "ON_HOLD": (3, "处理暂时搁置，等待恢复。"),
    "BLOCKED": (3, "当前存在阻塞，需要 Human 协调依赖或补充信息。"),
    "INSPECTOR_CONFIRMATION_REQUIRED": (3, "Developer 请求确认技术边界，等待 Inspector 决策，不需要 Human 介入。"),
    "HUMAN_CONFIRMATION_REQUIRED": (2, "Inspector 已暂停自动工作流，等待 Human 提供最终业务边界或安全决定。"),
    "IMPLEMENTED_PENDING_REVIEW": (4, "Developer 已提交实现，等待 Inspector / Human 复核。"),
    "REDESIGN_REQUIRED": (2, "原设计方向被推翻：Inspector 先修订架构级指导，Developer 再重新提交方案并获批后才能编码。"),
    "CONFIRMED": (5, "实现已验证通过，问题已经确认关闭。"),
    "CANCELLED": (5, "问题已取消，不再继续处理。"),
}


@app.template_filter("label")
def label(value: str | None) -> str:
    return LABELS.get(value or "", value or "—")


ASSIGNMENT_REASON_LABELS = {
    "router_disabled": "Model Router 未启用或配置无效，无法核对该执行配置",
    "profile_missing": "该执行配置已被删除，或 Routing 配置已被整体替换",
    "profile_disabled": "该执行配置已被禁用",
    "profile_changed": "该执行配置的 Agent / Model / Reasoning 已被修改",
    "profile_level_below_difficulty": "该执行配置的等级已低于当前 difficulty",
    "profile_level_reduced": "该执行配置的等级被下调",
}


@app.template_filter("assignment_reason")
def assignment_reason(value: str | None) -> str:
    return ASSIGNMENT_REASON_LABELS.get(value or "", value or "未提供")


@app.template_filter("runtime_label")
def runtime_label(value: str | None) -> str:
    return RUNTIME_LABELS.get(value or "", LABELS.get(value or "", value or "—"))


@app.template_filter("compact_count")
def compact_count(value: int | float | None) -> str:
    number = float(value or 0)
    if number >= 1_000_000:
        rendered = f"{number / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{rendered}M"
    if number >= 1_000:
        rendered = f"{number / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{rendered}K"
    return str(int(number))


@app.template_filter("topic_label")
def topic_label(value: str | None) -> str:
    return TOPIC_LABELS.get(value or "", value or "—")


@app.template_filter("decision_label")
def decision_label(value: str | None) -> str:
    return DECISION_LABELS.get(value or "", value or "—")


def brief_text(value: str | None, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


@app.template_filter("brief")
def brief(value: str | None, limit: int = 180) -> str:
    return brief_text(value, limit)


@app.template_filter("activity_history_summary")
def activity_history_summary(activity: dict) -> str:
    activity_type = activity.get("activity_type")
    metadata = activity.get("metadata") or {}
    stage_no = metadata.get("stage_no")
    fixed = {
        "ISSUE_CREATED": "问题已记录。",
        "DESIGN_REQUESTED": "已要求先说明修改方案，暂未开始开发。",
        "DESIGN_APPROVED": "设计已通过，Developer 可以开始修改。",
        "DESIGN_REJECTED": "设计未通过，需要调整后重新提交。",
        "STAGE_PLAN_CREATED": "分阶段执行计划已创建。",
        "STAGE_PLAN_SUPERSEDED": "原执行计划已废弃，需要按新方案处理。",
        "IMPLEMENTATION_SUBMITTED": "Developer 已提交完整实现，等待审核。",
        "REVIEW_APPROVED": "实现审核已通过。",
        "REVIEW_REJECTED": "实现审核未通过，需要继续修改。",
        "VERIFICATION_PASSED": "最终验证已通过。",
        "VERIFICATION_FAILED": "最终验证未通过，需要继续处理。",
        "INSPECTOR_CONFIRMATION_PROVIDED": "Inspector 已给出边界确认。",
        "HUMAN_CONFIRMATION_REQUESTED": "自动流程已暂停，等待人工决定。",
        "HUMAN_CONFIRMATION_PROVIDED": "人工决定已记录，流程继续。",
    }
    if activity_type in {"DESIGN_SUBMITTED", "REDESIGN_SUBMITTED"}:
        return metadata.get("human_summary") or "修改方案已提交，具体做法可在 AI 讨论中查看。"
    if activity_type == "STAGE_SUBMITTED":
        return f"第 {stage_no} 阶段已提交，等待验收。" if stage_no else "当前阶段已提交，等待验收。"
    if activity_type == "STAGE_APPROVED":
        return f"第 {stage_no} 阶段已验收通过。" if stage_no else "当前阶段已验收通过。"
    if activity_type == "STAGE_REJECTED":
        return f"第 {stage_no} 阶段未通过，需要继续修改。" if stage_no else "当前阶段未通过，需要继续修改。"
    if activity_type == "STATUS_CHANGED":
        return f"问题状态已变更为“{label(activity.get('result_status'))}”。"
    return fixed.get(activity_type, label(activity_type))


@app.template_filter("json_pretty")
def json_pretty(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


@app.template_filter("yesno")
def yesno(value: object) -> str:
    return "是" if value else "否"


@app.template_filter("localtime")
def localtime(value: str | None) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _inline_markdown(value: str) -> str:
    rendered = escape(value)
    rendered = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", rendered)
    return re.sub(r"`([^`\n]+)`", r"<code>\1</code>", rendered)


def _render_text_block(lines: list[str]) -> str:
    if not lines:
        return ""
    unordered = [re.match(r"^\s*[-*]\s+(.+)$", line) for line in lines]
    if all(unordered):
        return "<ul>" + "".join(f"<li>{_inline_markdown(item.group(1))}</li>" for item in unordered) + "</ul>"
    ordered = [re.match(r"^\s*\d+[.)]\s+(.+)$", line) for line in lines]
    if all(ordered):
        return "<ol>" + "".join(f"<li>{_inline_markdown(item.group(1))}</li>" for item in ordered) + "</ol>"
    return "<p>" + "<br>".join(_inline_markdown(line) for line in lines) + "</p>"


@app.template_filter("markdown")
def markdown(value: str | None) -> Markup:
    """安全渲染活动内容中的换行、列表、行内代码和 fenced code block。"""
    if not value:
        return Markup("")
    # 某些 CLI/Agent 会把整段多行内容作为字面量 ``\n`` 保存。只有原文完全没有真实
    # 换行时才还原，避免破坏代码说明中有意展示的转义序列。
    if "\n" not in value and "\\n" in value:
        value = value.replace("\\r\\n", "\n").replace("\\n", "\n")
    output: list[str] = []
    text_lines: list[str] = []
    code_lines: list[str] = []
    code_language = ""
    in_code = False

    def flush_text() -> None:
        nonlocal text_lines
        paragraph: list[str] = []
        for line in text_lines + [""]:
            if line.strip():
                paragraph.append(line)
            elif paragraph:
                output.append(_render_text_block(paragraph))
                paragraph = []
        text_lines = []

    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        fence = re.match(r"^\s*```\s*([A-Za-z0-9_+.-]*)\s*$", line)
        if fence:
            if in_code:
                language_class = f' class="language-{code_language}"' if code_language else ""
                output.append(f"<pre><code{language_class}>{escape(chr(10).join(code_lines))}</code></pre>")
                code_lines, code_language, in_code = [], "", False
            else:
                flush_text()
                code_language, in_code = fence.group(1), True
            continue
        (code_lines if in_code else text_lines).append(line)
    if in_code:
        language_class = f' class="language-{code_language}"' if code_language else ""
        output.append(f"<pre><code{language_class}>{escape(chr(10).join(code_lines))}</code></pre>")
    else:
        flush_text()
    return Markup("\n".join(output))


def issue_with_json(row: dict) -> dict:
    for field, default in (
        ("trigger_conditions", []), ("potential_impact", []), ("impact_scope", []),
        ("evidence", []), ("estimated_change", {}), ("local_terms", {}),
        ("difficulty_reason", []), ("recommended_executors", []), ("assignment", {}),
    ):
        row[field] = parse_json_field(row.get(f"{field}_json"), default)
    return row


def activity_with_json(row: dict) -> dict:
    row["code_reference"] = parse_json_field(row.get("code_reference_json"), [])
    row["metadata"] = parse_json_field(row.get("metadata_json"), {})
    row["record_kind"] = "activity"
    row["is_collaborative_submission"] = row.get("activity_type") in COLLABORATIVE_SUBMISSION_TYPES
    return row


def record_sort_key(row: dict) -> tuple[str, str, int]:
    return (
        row.get("amended_at") or row.get("created_at") or "",
        row["record_kind"],
        row["id"],
    )


def decision_with_json(row: dict) -> dict:
    row["source_discussion_ids"] = parse_json_field(row.get("source_discussion_ids_json"), [])
    row["metadata"] = parse_json_field(row.get("metadata_json"), {})
    return row


def candidate_with_json(row: dict) -> dict:
    row["evidence"] = parse_json_field(row.get("evidence_json"), [])
    return row


def stage_with_json(row: dict) -> dict:
    row["test_evidence"] = parse_json_field(row.get("test_evidence_json"), [])
    row["code_reference"] = parse_json_field(row.get("code_reference_json"), [])
    row["submission_metadata"] = parse_json_field(row.get("submission_metadata_json"), {})
    row["planned_change_scope"] = parse_json_field(row.get("planned_change_scope_json"), {})
    row["protected_behaviors"] = parse_json_field(row.get("protected_behaviors_json"), [])
    row["resolved_findings"] = parse_json_field(row.get("resolved_findings_json"), [])
    row["review_findings"] = parse_json_field(
        row.get("review_findings_json"), {level: [] for level in ("BLOCKER", "MUST", "SHOULD", "NIT")},
    )
    row["historical_regression"] = parse_json_field(row.get("historical_regression_json"), {})
    row["current_acceptance"] = parse_json_field(row.get("current_acceptance_json"), [])
    row["baseline"] = parse_json_field(row.get("baseline_json"), {})
    return row


def feedback_redirect(target: str, *, msg: str | None = None, err: str | None = None):
    separator = "&" if "?" in target else "?"
    return redirect(target + (separator + urlencode({"msg" if msg else "err": msg or err}) if msg or err else ""))


def redirect_back(endpoint: str, *, msg: str | None = None, err: str | None = None, **kwargs):
    return feedback_redirect(url_for(endpoint, **kwargs), msg=msg, err=err)


def safe_return_to(default: str) -> str:
    target = request.form.get("return_to", "")
    return target if target.startswith("/") and not target.startswith("//") else default


def issue_summary_where(extra: str = "", params: tuple = ()) -> dict:
    row = query_one(
        f"""SELECT
              SUM(CASE WHEN status = 'IMPLEMENTED_PENDING_REVIEW' THEN 1 ELSE 0 END) AS pending_review,
              SUM(CASE WHEN status = 'HUMAN_CONFIRMATION_REQUIRED' THEN 1 ELSE 0 END) AS confirmation,
              SUM(CASE WHEN status = 'BLOCKED' THEN 1 ELSE 0 END) AS blocked
            FROM review_issue WHERE 1=1 {extra}""", params,
    ) or {}
    return {key: int(row.get(key) or 0) for key in ("pending_review", "confirmation", "blocked")}


@app.route("/")
def inbox():
    base_select = """SELECT i.*, t.task_key, t.title AS task_title, t.project_name,
        (SELECT MAX(COALESCE(a.amended_at, a.created_at)) FROM issue_activity a
         WHERE a.issue_id = i.id AND a.operator_type = 'DEVELOPMENT_AGENT'
           AND a.activity_type = 'IMPLEMENTATION_SUBMITTED') AS developer_submitted_at,
        (SELECT a.metadata_json FROM issue_activity a
         WHERE a.issue_id = i.id AND a.activity_type = 'HUMAN_CONFIRMATION_REQUESTED'
         ORDER BY a.created_at DESC, a.id DESC LIMIT 1) AS human_request_metadata_json
        FROM review_issue i JOIN review_task t ON t.id = i.task_id"""
    issue_groups = {}
    for name, statuses in (
        ("pending_review", ("IMPLEMENTED_PENDING_REVIEW",)),
        ("confirmation", ("HUMAN_CONFIRMATION_REQUIRED",)),
        ("blocked", ("BLOCKED",)),
    ):
        placeholders = ",".join("?" for _ in statuses)
        issue_groups[name] = query_all(
            f"{base_select} WHERE i.status IN ({placeholders}) ORDER BY i.updated_at DESC, i.id DESC", statuses,
        )
    for item in issue_groups["confirmation"]:
        item["human_request"] = parse_json_field(item.get("human_request_metadata_json"), {})
    candidates = [candidate_with_json(row) for row in query_all(
        """SELECT c.*, t.task_key, t.project_name FROM issue_candidate c
           JOIN review_task t ON t.id = c.task_id
           WHERE c.status IN ('SUBMITTED', 'UNDER_REVIEW') ORDER BY c.updated_at DESC, c.id DESC LIMIT 8"""
    )]
    metrics = {name: len(rows) for name, rows in issue_groups.items()}
    metrics["candidates"] = int((query_one(
        "SELECT COUNT(*) AS total FROM issue_candidate WHERE status IN ('SUBMITTED', 'UNDER_REVIEW')"
    ) or {}).get("total") or 0)
    recent_tasks = query_all(
        """SELECT t.*,
                  COUNT(i.id) AS issue_total,
                  SUM(CASE WHEN i.status NOT IN ('CONFIRMED','CANCELLED') THEN 1 ELSE 0 END) AS open_issue_total,
                  SUM(CASE WHEN i.status = 'IMPLEMENTED_PENDING_REVIEW' THEN 1 ELSE 0 END) AS pending_review_total,
                  SUM(CASE WHEN i.status = 'HUMAN_CONFIRMATION_REQUIRED' THEN 1 ELSE 0 END) AS human_total,
                  (SELECT MAX(i2.updated_at) FROM review_issue i2 WHERE i2.task_id = t.id) AS latest_issue_at,
                  (SELECT MAX(COALESCE(a.amended_at, a.created_at)) FROM issue_activity a
                     JOIN review_issue i3 ON i3.id = a.issue_id WHERE i3.task_id = t.id) AS latest_activity_at,
                  (SELECT MAX(c.updated_at) FROM issue_candidate c WHERE c.task_id = t.id) AS latest_candidate_at
           FROM review_task t LEFT JOIN review_issue i ON i.task_id = t.id
           WHERE t.status IN ('PENDING','IN_PROGRESS','ON_HOLD','BLOCKED')
           GROUP BY t.id"""
    )
    for task in recent_tasks:
        task["active_at"] = max(
            value for value in (
                task.get("updated_at"), task.get("latest_issue_at"),
                task.get("latest_activity_at"), task.get("latest_candidate_at"),
            ) if value
        )
    recent_tasks.sort(key=lambda task: (task["active_at"], task["id"]), reverse=True)
    return render_template(
        "inbox.html", groups=issue_groups, candidates=candidates, metrics=metrics,
        recent_tasks=recent_tasks[:6],
    )


@app.route("/tasks", strict_slashes=False)
def task_list():
    status = request.args.get("status", "")
    project = request.args.get("project_name", "")
    task_type = request.args.get("task_type", "")
    include_closed = request.args.get("include_closed") == "1"
    filters, params = ["WHERE 1=1"], []
    if status:
        filters.append("AND t.status = ?")
        params.append(status)
    elif not include_closed:
        filters.append("AND t.status IN ('PENDING', 'IN_PROGRESS', 'ON_HOLD', 'BLOCKED')")
    if project:
        filters.append("AND t.project_name = ?")
        params.append(project)
    if task_type:
        filters.append("AND t.task_type = ?")
        params.append(task_type)
    tasks = query_all(
        f"""SELECT t.*, COUNT(i.id) AS issue_total,
                   SUM(CASE WHEN i.status NOT IN ('CONFIRMED', 'CANCELLED') THEN 1 ELSE 0 END) AS open_issue_total,
                   SUM(CASE WHEN i.severity = 'critical' AND i.status NOT IN ('CONFIRMED', 'CANCELLED') THEN 1 ELSE 0 END) AS critical_total,
                   SUM(CASE WHEN i.severity = 'high' AND i.status NOT IN ('CONFIRMED', 'CANCELLED') THEN 1 ELSE 0 END) AS high_total
            FROM review_task t LEFT JOIN review_issue i ON i.task_id = t.id
            {' '.join(filters)} GROUP BY t.id
            ORDER BY CASE t.task_type WHEN 'CONTINUOUS' THEN 1 ELSE 2 END,
                     t.updated_at DESC, t.id DESC""", params,
    )
    projects: list[dict] = []
    selected_project_path = ""
    if project:
        selected_project_path = tasks[0]["project_path"] if tasks else ""
    else:
        project_groups: OrderedDict[str, dict] = OrderedDict()
        for task in tasks:
            item = project_groups.setdefault(task["project_name"], {
                "project_name": task["project_name"], "project_paths": [], "task_total": 0,
                "continuous_total": 0, "review_total": 0, "open_issue_total": 0,
                "updated_at": task["updated_at"],
            })
            if task["project_path"] not in item["project_paths"]:
                item["project_paths"].append(task["project_path"])
            item["task_total"] += 1
            item["continuous_total"] += int(task["task_type"] == "CONTINUOUS")
            item["review_total"] += int(task["task_type"] == "REVIEW")
            item["open_issue_total"] += task["open_issue_total"] or 0
            item["updated_at"] = max(item["updated_at"], task["updated_at"])
        projects = list(project_groups.values())
        for item in projects:
            item["url"] = url_for(
                "task_list", project_name=item["project_name"], status=status or None,
                task_type=task_type or None, include_closed=1 if include_closed else None,
            )
    return render_template(
        "tasks_list.html", tasks=tasks if project else [], projects=projects,
        selected_project_path=selected_project_path, statuses=TASK_STATUSES, task_types=TASK_TYPES,
        filters={"status": status, "project_name": project, "task_type": task_type,
                 "include_closed": include_closed},
    )


@app.route("/tasks/create", methods=["POST"])
def task_create():
    project_path = Path(request.form.get("project_path", "")).expanduser()
    target = url_for("task_list")
    try:
        if not project_path.is_absolute() or not project_path.is_dir():
            raise ValueError("项目路径必须是已存在的绝对目录")
        args = [
            "--title", request.form.get("title", ""),
            "--objective", request.form.get("objective", ""),
            "--task-type", request.form.get("task_type", "REVIEW"),
        ]
        for field in ("review_level", "review_scope", "baseline_ref", "remark"):
            if value := request.form.get(field):
                args.extend([f"--{field.replace('_', '-')}", value])
        result = run_human_command("task-create", *args, cwd=project_path.resolve())
        return redirect_back("task_detail", task_key=result["task_key"], msg="检查任务已创建")
    except Exception as exc:  # noqa: BLE001
        return feedback_redirect(target, err=str(exc))


@app.get("/healthz")
def healthcheck():
    return {"status": "ok"}


def browser_change_payload(row: dict) -> dict:
    """把内部审计转换为浏览器能稳定消费的小事件。"""
    action = row["action"]
    resource_id = row.get("resource_id")
    event = {
        "eventId": row["id"],
        "action": action,
        "createdAt": row["created_at"],
        "resourceId": resource_id,
    }
    if action.startswith("stage."):
        event.update({"kind": "stage", "changeType": "stage", "issueKey": resource_id})
        stage = query_one(
            """SELECT s.plan_no,s.stage_no,s.title,s.status
               FROM issue_stage s JOIN review_issue i ON i.id=s.issue_id
               WHERE i.issue_key=? AND s.plan_status='ACTIVE'
               ORDER BY s.plan_no DESC,
                        CASE s.status WHEN 'IN_PROGRESS' THEN 0 WHEN 'PENDING_REVIEW' THEN 1 ELSE 2 END,
                        s.stage_no DESC LIMIT 1""",
            (resource_id,),
        ) if resource_id else None
        if stage:
            event["stage"] = {
                "planNo": stage["plan_no"], "stageNo": stage["stage_no"],
                "title": stage["title"], "status": stage["status"],
                "statusLabel": label(stage["status"]),
            }
            event["title"] = f"{resource_id} · Stage {stage['stage_no']} 已更新"
            event["message"] = f"{stage['title']}：{label(stage['status'])}"
        else:
            event["title"] = f"{resource_id or 'Issue'} 的 Stage 已更新"
            event["message"] = "执行阶段发生变化"
        return event

    if action.startswith("task.") or action == "version.create" or action == "issue.create-batch":
        event.update({
            "kind": "task", "taskKey": resource_id,
            "changeType": "status" if action == "task.update-status" else "content",
        })
        task = query_one("SELECT status,title FROM review_task WHERE task_key=?", (resource_id,)) if resource_id else None
        if task:
            event.update({"status": task["status"], "statusLabel": label(task["status"])})
            event["title"] = f"任务 {resource_id} 已更新"
            event["message"] = f"{task['title']}：{label(task['status'])}"
        else:
            event["title"] = "检查任务已更新"
            event["message"] = "任务内容或问题列表发生变化"
        return event

    issue_status_actions = {
        "issue.update-status", "issue.update-status-batch", "design.request", "design.submit",
        "design.review", "implementation.submit", "human.escalate", "human.confirmation-resolve",
    }
    event.update({
        "kind": "issue", "issueKey": resource_id,
        "changeType": "status" if action in issue_status_actions else "content",
    })
    issue = query_one("SELECT status,title FROM review_issue WHERE issue_key=?", (resource_id,)) if resource_id else None
    if issue:
        event.update({"status": issue["status"], "statusLabel": label(issue["status"])})
        event["title"] = f"问题 {resource_id} 已更新"
        event["message"] = f"{issue['title']}：{label(issue['status'])}"
    else:
        event["title"] = "问题列表已更新"
        event["message"] = "问题内容或状态发生变化"
    return event


@app.get("/api/browser-events")
def browser_events():
    """返回上次游标之后的轻量变化；首次调用只建立游标。"""
    after_text = request.args.get("after", "").strip()
    latest = query_one("SELECT COALESCE(MAX(id), 0) AS id FROM agent_audit_log") or {"id": 0}
    if not after_text:
        return {"cursor": int(latest["id"]), "events": []}
    try:
        after = int(after_text)
        if after < 0:
            raise ValueError
    except ValueError:
        abort(400, "after 必须是非负整数")

    placeholders = ",".join("?" for _ in BROWSER_CHANGE_ACTIONS)
    rows = query_all(
        f"""SELECT id,action,resource_type,resource_id,created_at
            FROM agent_audit_log
            WHERE id>? AND success=1 AND action IN ({placeholders})
            ORDER BY id ASC LIMIT 100""",
        (after, *BROWSER_CHANGE_ACTIONS),
    )
    events = [browser_change_payload(row) for row in rows]
    cursor = rows[-1]["id"] if rows else int(latest["id"])
    return {"cursor": cursor, "events": events}


@app.route("/tasks/<task_key>")
def task_detail(task_key: str):
    task = query_one("SELECT * FROM review_task WHERE task_key = ?", (task_key,))
    if not task:
        abort(404)
    tab = request.args.get("tab", "all")
    issue_status = request.args.get("issue_status", "")
    severity = request.args.get("severity", "")
    dimension = request.args.get("dimension", "")
    show_completed = (
        request.args.get("show_completed") == "1"
        or tab in {"completed", "confirmed"}
        or issue_status in COMPLETED_ISSUE_STATUSES
    )
    filters, params = ["WHERE i.task_id = ?"], [task["id"]]
    tab_statuses = {
        "mine": ("IMPLEMENTED_PENDING_REVIEW", "HUMAN_CONFIRMATION_REQUIRED", "BLOCKED"),
        "review": ("IMPLEMENTED_PENDING_REVIEW",), "blocked": ("BLOCKED",),
        "completed": COMPLETED_ISSUE_STATUSES,
        # 兼容旧的已确认页签链接。
        "confirmed": COMPLETED_ISSUE_STATUSES,
    }
    if tab in tab_statuses:
        values = tab_statuses[tab]
        filters.append(f"AND i.status IN ({','.join('?' for _ in values)})")
        params.extend(values)
    if issue_status:
        filters.append("AND i.status = ?")
        params.append(issue_status)
    if severity:
        filters.append("AND i.severity = ?")
        params.append(severity)
    if dimension:
        filters.append("AND i.dimension = ?")
        params.append(dimension)
    if task["task_type"] == "CONTINUOUS" and not show_completed:
        filters.append(f"AND i.status NOT IN ({','.join('?' for _ in COMPLETED_ISSUE_STATUSES)})")
        params.extend(COMPLETED_ISSUE_STATUSES)
    issues = [issue_with_json(row) for row in query_all(
        f"""SELECT i.* FROM review_issue i {' '.join(filters)}
            ORDER BY CASE i.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     i.updated_at DESC, i.issue_key ASC""", params,
    )]
    versions = query_all("SELECT * FROM review_task_version WHERE task_id = ? ORDER BY version_no DESC", (task["id"],))
    non_open_placeholders = ",".join("?" for _ in NON_OPEN_ISSUE_STATUSES)
    completed_placeholders = ",".join("?" for _ in COMPLETED_ISSUE_STATUSES)
    counts = query_one(
        f"""SELECT COUNT(*) AS total,
           SUM(CASE WHEN status NOT IN ({non_open_placeholders}) THEN 1 ELSE 0 END) AS open,
           SUM(CASE WHEN severity IN ('critical','high') AND status NOT IN ({non_open_placeholders}) THEN 1 ELSE 0 END) AS priority,
           SUM(CASE WHEN status IN ('IMPLEMENTED_PENDING_REVIEW','HUMAN_CONFIRMATION_REQUIRED','BLOCKED') THEN 1 ELSE 0 END) AS mine,
           SUM(CASE WHEN status = 'IMPLEMENTED_PENDING_REVIEW' THEN 1 ELSE 0 END) AS review,
           SUM(CASE WHEN status = 'BLOCKED' THEN 1 ELSE 0 END) AS blocked,
           SUM(CASE WHEN status IN ({completed_placeholders}) THEN 1 ELSE 0 END) AS completed
           FROM review_issue WHERE task_id = ?""",
        (*NON_OPEN_ISSUE_STATUSES, *NON_OPEN_ISSUE_STATUSES, *COMPLETED_ISSUE_STATUSES, task["id"]),
    ) or {}
    summary = {key: int(counts.get(key) or 0) for key in ("total", "open", "priority", "mine", "review", "blocked", "completed")}
    summary["listed_total"] = (
        summary["total"]
        if task["task_type"] != "CONTINUOUS" or show_completed
        else summary["total"] - summary["completed"]
    )
    return render_template(
        "task_detail.html", task=task, issues=issues, versions=versions, summary=summary,
        statuses=TASK_STATUSES, issue_statuses=ISSUE_STATUSES, severities=SEVERITIES, dimensions=DIMENSIONS,
        filters={
            "tab": tab, "issue_status": issue_status, "severity": severity, "dimension": dimension,
            "show_completed": show_completed,
        },
    )


@app.route("/tasks/<task_key>/edit", methods=["POST"])
def task_update(task_key: str):
    args = ["--task-key", task_key]
    for field in ("title", "objective", "remark", "close_reason"):
        value = request.form.get(field)
        if value is not None:
            args.extend([f"--{field.replace('_', '-')}", value])
    target = safe_return_to(url_for("task_detail", task_key=task_key))
    try:
        run_human_command("task-update", *args)
        return feedback_redirect(target, msg="任务信息已保存")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return feedback_redirect(target, err=str(exc))


@app.route("/tasks/<task_key>/status", methods=["POST"])
def task_update_status(task_key: str):
    args = ["--task-key", task_key, "--status", request.form.get("status", "")]
    for field in ("remark", "close_reason"):
        if value := request.form.get(field):
            args.extend([f"--{field.replace('_', '-')}", value])
    target = safe_return_to(url_for("task_detail", task_key=task_key))
    try:
        run_human_command("task-update-status", *args)
        return feedback_redirect(target, msg="任务状态已更新")
    except Exception as exc:  # noqa: BLE001
        return feedback_redirect(target, err=str(exc))


def runtime_day_bounds() -> tuple[str, str]:
    """返回展示时区“今日”对应的 UTC SQLite 时间边界。"""
    local_now = datetime.now(DISPLAY_TIMEZONE)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return tuple(
        value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for value in (local_start, local_end)
    )


def token_total(row: dict) -> int:
    """Cached Input 是 Input 的子集，因此总量只计算 Input + Output。"""
    return int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)


def review_db_tool_counts(rows: list[dict]) -> list[dict]:
    totals = {
        str(row["command"]): {
            "count": int(row.get("call_count") or 0),
            "failure_count": int(row.get("failure_count") or 0),
        }
        for row in rows
    }
    preferred = [
        "issue-context-get", "issue-get", "activity-get", "discussion-get",
        "stage-get", "stage-history-get", "design-preview", "decision-list",
        "fast-review-record",
    ]
    entries = {
        name: {
            "name": name,
            **counts,
            "failure_rate": round(100 * counts["failure_count"] / counts["count"], 1)
            if counts["count"] else 0.0,
        }
        for name, counts in totals.items()
    }
    failed_names = sorted(
        (name for name, entry in entries.items() if entry["failure_count"]),
        key=lambda name: (-entries[name]["failure_rate"], -entries[name]["failure_count"], name),
    )
    ordered = [entries.pop(name) for name in failed_names]
    ordered.extend(
        entries.pop(name, {
            "name": name, "count": 0, "failure_count": 0, "failure_rate": 0.0,
        })
        for name in preferred
        if name not in failed_names
    )
    ordered.extend(
        entry
        for name, entry in sorted(
            entries.items(), key=lambda item: (-item[1]["count"], item[0])
        )
        if entry["count"]
    )
    return ordered


def issue_ai_summary(issue_key: str, threads: list[dict], events: list[dict]) -> dict:
    usage = query_one(
        """SELECT COALESCE(SUM(input_tokens),0) AS input_tokens,
                  COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens,
                  COALESCE(SUM(output_tokens),0) AS output_tokens,
                  COUNT(*) AS turn_count,
                  SUM(CASE WHEN input_tokens IS NOT NULL OR cached_input_tokens IS NOT NULL
                                OR output_tokens IS NOT NULL THEN 1 ELSE 0 END) AS token_usage_count,
                  SUM(CASE WHEN turn_type='INIT' THEN 1 ELSE 0 END) AS init_count,
                  SUM(CASE WHEN turn_type='ACTION' THEN 1 ELSE 0 END) AS action_count,
                  SUM(CASE WHEN turn_type='COMPACT' THEN 1 ELSE 0 END) AS compact_count
           FROM code_inspector_turn_metric WHERE issue_key=?""",
        (issue_key,),
    ) or {}
    recent_turns = query_all(
        """SELECT event_id,role,operator_id,projection_revision,turn_type,turn_id,
                  input_tokens,cached_input_tokens,output_tokens,review_db_calls_json,created_at
           FROM code_inspector_turn_metric WHERE issue_key=? ORDER BY id DESC LIMIT 12""",
        (issue_key,),
    )
    for turn in recent_turns:
        turn["total_tokens"] = token_total(turn)
        turn["review_db_calls"] = parse_json_field(turn["review_db_calls_json"], {})
    tool_rows = query_all(
        """SELECT command,COUNT(*) AS call_count,
                  SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failure_count
           FROM review_tool_call_metric WHERE issue_key=? GROUP BY command""",
        (issue_key,),
    )
    context_values = [
        row["context_usage"] for row in threads if row.get("context_usage") is not None
    ]
    ambiguous = next((
        row for row in threads
        if "AMBIGUOUS" in str(row.get("error_code") or "")
    ), None)
    failed_thread = next((row for row in threads if row.get("thread_status") == "FAILED"), None)
    paused_thread = next((row for row in threads if row.get("thread_status") == "PAUSED"), None)
    latest_event = next(
        (event for event in events if event.get("status") != "SUPERSEDED"),
        events[0] if events else None,
    )
    if ambiguous or (latest_event and latest_event.get("failure_kind") == "AMBIGUOUS"):
        latest_status, latest_kind = "执行结果不确定", "attention"
    elif failed_thread or (latest_event and latest_event.get("status") == "FAILED"):
        latest_status, latest_kind = "执行失败", "danger"
    elif paused_thread:
        latest_status, latest_kind = "已暂停", "attention"
    elif any(row.get("thread_status") == "ACTIVE" for row in threads) or (
        latest_event and latest_event.get("status") == "PROCESSING"
    ):
        latest_status, latest_kind = "正在处理", "active"
    elif recent_turns:
        latest_status, latest_kind = "正常", "ok"
    elif tool_rows:
        latest_status, latest_kind = "已记录工具调用", "neutral"
    else:
        latest_status, latest_kind = "暂无执行记录", "neutral"
    return {
        **usage,
        "token_usage_available": int(usage.get("token_usage_count") or 0) > 0,
        "total_tokens": token_total(usage),
        "wakeups": int(usage.get("init_count") or 0) + int(usage.get("action_count") or 0),
        "context_usage": max(context_values) if context_values else None,
        "latest_status": latest_status,
        "latest_kind": latest_kind,
        "tool_calls": review_db_tool_counts(tool_rows),
        "recent_turns": recent_turns,
    }


@app.route("/issues/<issue_key>")
def issue_detail(issue_key: str):
    issue = query_one(
        """SELECT i.*, t.task_key, t.project_name, t.project_path, t.title AS task_title,
                  t.objective AS task_objective, t.remark AS task_remark, t.close_reason AS task_close_reason,
                  t.status AS task_status, t.task_type
           FROM review_issue i JOIN review_task t ON t.id = i.task_id WHERE i.issue_key = ?""", (issue_key,)
    )
    if not issue:
        abort(404)
    issue_with_json(issue)
    activities = [activity_with_json(row) for row in query_all(
        "SELECT * FROM issue_activity WHERE issue_id = ? ORDER BY created_at ASC, id ASC", (issue["id"],)
    )]
    decisions = [decision_with_json(row) for row in query_all(
        """SELECT * FROM issue_decision
           WHERE issue_id = ? AND effective = 1 ORDER BY created_at DESC, id DESC""", (issue["id"],)
    )]
    discussions = query_all(
        """SELECT * FROM issue_discussion
           WHERE issue_id = ? ORDER BY created_at ASC, id ASC""", (issue["id"],)
    )
    for discussion in discussions:
        discussion["record_kind"] = "discussion"
    history_activities = [
        activity for activity in activities
        if activity["activity_type"] in HISTORY_MILESTONE_TYPES
    ]
    discussion_records = sorted(
        [*discussions, *(activity for activity in history_activities
                          if activity["activity_type"] in COLLABORATIVE_SUBMISSION_TYPES)],
        key=record_sort_key, reverse=True,
    )
    all_records = sorted(
        [*discussions, *history_activities], key=record_sort_key, reverse=True,
    )
    grouped: OrderedDict[int, list[dict]] = OrderedDict()
    for activity in history_activities:
        grouped.setdefault(activity["attempt_no"], []).append(activity)
    current_activities = [a for a in activities if a["attempt_no"] == issue["current_attempt_no"]]
    implementations = [a for a in current_activities if a["activity_type"] == "IMPLEMENTATION_SUBMITTED"]
    latest_implementation = implementations[-1] if implementations else None
    design_submissions = [a for a in activities if a["activity_type"] == "DESIGN_SUBMITTED"]
    latest_design_submission = design_submissions[-1] if design_submissions else None
    verification_activities = [a for a in current_activities if a["activity_type"].startswith("VERIFICATION_")]
    human_requests = [a for a in activities if a["activity_type"] == "HUMAN_CONFIRMATION_REQUESTED"]
    latest_human_request = human_requests[-1] if human_requests else None
    stages = [stage_with_json(row) for row in query_all(
        "SELECT * FROM issue_stage WHERE issue_id = ? ORDER BY plan_no DESC, stage_no", (issue["id"],)
    )]
    stage_plans: OrderedDict[int, list[dict]] = OrderedDict()
    for execution_stage in stages:
        stage_plans.setdefault(execution_stage["plan_no"], []).append(execution_stage)
    active_stage_plan = next((
        plan for plan in stage_plans.values()
        if all(item["plan_status"] == "ACTIVE" for item in plan)
    ), None)
    current_execution_stage = next((
        item for item in (active_stage_plan or []) if item["status"] in {"IN_PROGRESS", "PENDING_REVIEW"}
    ), None)
    runtime_threads = query_all(
        """SELECT issue_key,role,operator_id,agent_platform,runtime_backend,thread_id,
                  thread_status,context_tokens,context_window,last_event,last_active_at,
                  next_action,worker_id,lease_until,heartbeat_at,error_code,error_message,
                  created_at,updated_at
           FROM code_inspector_thread WHERE issue_key=? ORDER BY role,operator_id""", (issue_key,)
    )
    for runtime_thread in runtime_threads:
        runtime_thread["context_usage"] = round(
            100 * runtime_thread["context_tokens"] / runtime_thread["context_window"], 1
        ) if runtime_thread.get("context_tokens") is not None and runtime_thread.get("context_window") else None
    runtime_events = query_all(
        """SELECT event_id,operator_id,role,event_type,status,attempt_count,failure_kind,
                  projection_revision,claimed_at,lease_until,worker_id,next_attempt_at,
                  last_error,superseded_by_event_id,created_at
           FROM code_inspector_event WHERE issue_key=? ORDER BY id DESC LIMIT 20""", (issue_key,)
    )
    ai_summary = issue_ai_summary(issue_key, runtime_threads, runtime_events)
    # 推荐按当前 Routing 配置动态计算，避免展示持久化下来的过期快照。
    issue["recommended_executors"] = routing.recommended_for(issue.get("difficulty"))
    issue["assignment_status"], issue["assignment_invalid_reason"] = routing.assignment_state(
        issue.get("assignment"), issue.get("difficulty"),
    )
    stage, status_explanation = STATUS_PRESENTATION.get(issue["status"], (1, issue["status"]))
    return render_template(
        "issue_detail.html", issue=issue, activities=history_activities, activity_groups=grouped,
        decisions=decisions, discussions=discussions, discussion_records=discussion_records,
        all_records=all_records,
        latest_implementation=latest_implementation, latest_design_submission=latest_design_submission,
        verification_activities=verification_activities,
        latest_human_request=latest_human_request,
        stage_plans=stage_plans, active_stage_plan=active_stage_plan,
        current_execution_stage=current_execution_stage,
        current_stage=stage, status_explanation=status_explanation, dimensions=DIMENSIONS,
        severities=SEVERITIES, benefits=BENEFITS, costs=COSTS, confidence=CONFIDENCE,
        dispositions=DISPOSITIONS, activity_types=ACTIVITY_TYPES, discussion_topics=DISCUSSION_TOPICS,
        issue_statuses=ISSUE_STATUSES,
        task_statuses=TASK_STATUSES,
        runtime_threads=runtime_threads, runtime_events=runtime_events,
        ai_summary=ai_summary,
        assignment_profiles=routing.candidate_profiles(issue.get("difficulty")),
    )


@app.get("/runtime")
def runtime_overview():
    today_start, today_end = runtime_day_bounds()
    issue = request.args.get("issue", "").strip()
    role = request.args.get("role", "").strip()
    operator = request.args.get("operator", "").strip()
    status_filter = request.args.get("status", "").strip()
    filters, params = [], []
    for column, value in (("t.issue_key", issue), ("t.role", role), ("t.operator_id", operator)):
        if value:
            filters.append(f"{column}=?"); params.append(value)
    thread_statuses = set(RUNTIME_THREAD_STATUSES)
    event_statuses = set(RUNTIME_EVENT_STATUSES)
    if status_filter:
        if status_filter in thread_statuses:
            filters.append("thread_status=?"); params.append(status_filter)
        else:
            filters.append("0=1")
    where = " WHERE " + " AND ".join(filters) if filters else ""
    threads = query_all(
        """SELECT t.issue_key,t.role,t.operator_id,t.agent_platform,t.runtime_backend,t.thread_id,
                  t.thread_status,t.issue_status,t.next_action,t.last_event,t.last_active_at,
                  t.worker_id,t.lease_until,t.heartbeat_at,t.error_code,t.error_message,
                  t.context_tokens,t.context_window,t.created_at,t.updated_at,
                  i.projection_revision
           FROM code_inspector_thread t JOIN review_issue i ON i.id=t.issue_id""" + where + " ORDER BY t.updated_at DESC", params,
    )
    for row in threads:
        row["context_usage"] = round(100 * row["context_tokens"] / row["context_window"], 1) if row.get("context_tokens") is not None and row.get("context_window") else None
    event_filters, event_params = [], []
    for column, value in (("issue_key", issue), ("role", role), ("operator_id", operator)):
        if value:
            event_filters.append(f"{column}=?"); event_params.append(value)
    if status_filter in event_statuses:
        event_filters.append("status=?"); event_params.append(status_filter)
    elif status_filter:
        event_filters.append("0=1")
    event_where = " WHERE " + " AND ".join(event_filters) if event_filters else ""
    events = query_all(
        """SELECT event_id,issue_key,role,operator_id,event_type,status,attempt_count,
                  projection_revision,failure_kind,claimed_at,lease_until,worker_id,
                  next_attempt_at,last_error,superseded_by_event_id,created_at,updated_at
           FROM code_inspector_event""" + event_where + " ORDER BY id DESC LIMIT 200", event_params,
    )
    turns = query_all(
        """SELECT event_id,issue_key,role,operator_id,projection_revision,turn_type,turn_id,
                  input_tokens,cached_input_tokens,output_tokens,review_db_calls_json,created_at
           FROM code_inspector_turn_metric"""
        + (" WHERE " + " AND ".join(
            [clause for clause in (
                "issue_key=?" if issue else "",
                "role=?" if role else "",
                "operator_id=?" if operator else "",
            ) if clause]
        ) if issue or role or operator else "")
        + " ORDER BY id DESC LIMIT 200",
        tuple(value for value in (issue, role, operator) if value),
    )
    for turn in turns:
        turn["total_tokens"] = token_total(turn)
        turn["review_db_calls"] = parse_json_field(turn["review_db_calls_json"], {})

    today = query_one(
        """SELECT COALESCE(SUM(input_tokens),0) AS input_tokens,
                  COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens,
                  COALESCE(SUM(output_tokens),0) AS output_tokens,
                  SUM(CASE WHEN input_tokens IS NOT NULL OR cached_input_tokens IS NOT NULL
                                OR output_tokens IS NOT NULL THEN 1 ELSE 0 END) AS token_usage_count,
                  COUNT(DISTINCT issue_key) AS issue_count,
                  SUM(CASE WHEN turn_type IN ('INIT','ACTION') THEN 1 ELSE 0 END) AS wakeups,
                  SUM(CASE WHEN turn_type='COMPACT' THEN 1 ELSE 0 END) AS compact_count
           FROM code_inspector_turn_metric WHERE created_at>=? AND created_at<?""",
        (today_start, today_end),
    ) or {}
    today["token_usage_available"] = int(today.get("token_usage_count") or 0) > 0
    today["total_tokens"] = token_total(today)
    issue_count = int(today.get("issue_count") or 0)
    today["average_tokens"] = round(today["total_tokens"] / issue_count) if issue_count else 0

    tool_filters = ["created_at>=?", "created_at<?"]
    tool_params: list[str] = [today_start, today_end]
    for column, value in (("issue_key", issue), ("role", role), ("operator_id", operator)):
        if value:
            tool_filters.append(f"{column}=?")
            tool_params.append(value)
    today_tool_calls = review_db_tool_counts(query_all(
        """SELECT command,COUNT(*) AS call_count,
                  SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failure_count
           FROM review_tool_call_metric WHERE """
        + " AND ".join(tool_filters) + " GROUP BY command",
        tool_params,
    ))

    savings = query_one(
        """SELECT COUNT(*) AS intercepted_events
           FROM code_inspector_event e
           WHERE e.status='SUPERSEDED' AND e.updated_at>=? AND e.updated_at<?
             AND NOT EXISTS(
               SELECT 1 FROM code_inspector_turn_metric m WHERE m.event_id=e.event_id
             )""",
        (today_start, today_end),
    ) or {"intercepted_events": 0}
    savings["avoided_wakeups"] = int(savings.get("intercepted_events") or 0)
    savings["compact_count"] = int(today.get("compact_count") or 0)

    issue_usage = query_all(
        """SELECT m.issue_key,i.title,
                  COALESCE(SUM(m.input_tokens),0) AS input_tokens,
                  COALESCE(SUM(m.cached_input_tokens),0) AS cached_input_tokens,
                  COALESCE(SUM(m.output_tokens),0) AS output_tokens,
                  SUM(CASE WHEN m.turn_type IN ('INIT','ACTION') THEN 1 ELSE 0 END) AS wakeups,
                  SUM(CASE WHEN m.turn_type='INIT' THEN 1 ELSE 0 END) AS init_count,
                  SUM(CASE WHEN m.turn_type='ACTION' THEN 1 ELSE 0 END) AS action_count,
                  SUM(CASE WHEN m.turn_type='COMPACT' THEN 1 ELSE 0 END) AS compact_count
           FROM code_inspector_turn_metric m
           LEFT JOIN review_issue i ON i.issue_key=m.issue_key
           WHERE m.created_at>=? AND m.created_at<?
           GROUP BY m.issue_key,i.title
           ORDER BY COALESCE(SUM(m.input_tokens),0)+COALESCE(SUM(m.output_tokens),0) DESC""",
        (today_start, today_end),
    )
    for usage in issue_usage:
        usage["total_tokens"] = token_total(usage)

    recent_start = (
        datetime.now(DISPLAY_TIMEZONE) - timedelta(days=30)
    ).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    recent_average = query_one(
        """WITH per_issue AS (
             SELECT issue_key,
                    COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0) AS tokens,
                    SUM(CASE WHEN turn_type IN ('INIT','ACTION') THEN 1 ELSE 0 END) AS wakeups
             FROM code_inspector_turn_metric WHERE created_at>=? GROUP BY issue_key
           )
           SELECT COALESCE(AVG(tokens),0) AS tokens,COALESCE(AVG(wakeups),0) AS wakeups
           FROM per_issue""",
        (recent_start,),
    ) or {"tokens": 0, "wakeups": 0}

    thread_alerts = query_all(
        """SELECT t.issue_key,i.title,t.thread_status,t.role,t.operator_id,t.thread_id,
                  t.error_code,t.error_message,t.lease_until,t.worker_id,t.updated_at
           FROM code_inspector_thread t LEFT JOIN review_issue i ON i.id=t.issue_id
           WHERE t.thread_status IN ('PAUSED','FAILED') OR t.error_code LIKE '%AMBIGUOUS%'
           ORDER BY CASE WHEN t.error_code LIKE '%AMBIGUOUS%' THEN 0
                         WHEN t.thread_status='FAILED' THEN 1 ELSE 2 END,t.updated_at DESC"""
    )
    event_alerts = query_all(
        f"""SELECT e.issue_key,i.title,e.event_id,e.event_type,e.status,e.failure_kind,
                   e.last_error,e.worker_id,e.claimed_at,e.lease_until,e.updated_at,
                   CASE WHEN e.status='PROCESSING' AND (
                     (e.lease_until IS NOT NULL AND e.lease_until<CURRENT_TIMESTAMP) OR
                     (e.lease_until IS NULL AND COALESCE(e.claimed_at,e.updated_at,e.created_at)
                       <datetime('now','-{RUNTIME_PROCESSING_TIMEOUT_MINUTES} minutes'))
                   ) THEN 1 ELSE 0 END AS processing_too_long
            FROM code_inspector_event e
            LEFT JOIN review_issue i ON i.issue_key=e.issue_key
            WHERE e.status='FAILED' OR e.failure_kind='AMBIGUOUS' OR (
              e.status='PROCESSING' AND (
                (e.lease_until IS NOT NULL AND e.lease_until<CURRENT_TIMESTAMP) OR
                (e.lease_until IS NULL AND COALESCE(e.claimed_at,e.updated_at,e.created_at)
                  <datetime('now','-{RUNTIME_PROCESSING_TIMEOUT_MINUTES} minutes'))
              )
            )
            ORDER BY CASE WHEN e.failure_kind='AMBIGUOUS' THEN 0
                          WHEN e.status='FAILED' THEN 1 ELSE 2 END,e.updated_at DESC"""
    )

    attention: list[dict] = []
    seen_issues: set[str] = set()

    def add_attention(issue_key: str, title: str | None, message: str, kind: str, technical: dict) -> None:
        if issue_key in seen_issues or len(attention) >= 10:
            return
        seen_issues.add(issue_key)
        attention.append({
            "issue_key": issue_key, "title": title or "未命名 Issue",
            "message": message, "kind": kind, "technical": technical,
        })

    for row in thread_alerts:
        ambiguous = "AMBIGUOUS" in str(row.get("error_code") or "")
        if ambiguous:
            message, kind = "任务执行状态不确定，建议人工核对", "danger"
        elif row["thread_status"] == "FAILED":
            message, kind = "自动处理失败，需要检查", "danger"
        else:
            message, kind = "AI 已暂停，需要确认是否继续", "warning"
        add_attention(row["issue_key"], row.get("title"), message, kind, row)
    for row in event_alerts:
        if row.get("failure_kind") == "AMBIGUOUS":
            message, kind = "任务执行状态不确定，建议人工核对", "danger"
        elif row.get("processing_too_long"):
            message, kind = "处理时间明显过长，可能已经卡住", "warning"
        else:
            message, kind = "自动处理失败，需要检查", "danger"
        add_attention(row["issue_key"], row.get("title"), message, kind, row)
    operational_alert_count = len(attention)

    average_tokens = float(recent_average.get("tokens") or 0)
    average_wakeups = float(recent_average.get("wakeups") or 0)
    for usage in issue_usage:
        if (
            average_tokens > 0 and usage["total_tokens"] >= TOKEN_ATTENTION_MINIMUM
            and usage["total_tokens"] >= average_tokens * 2
        ):
            ratio = usage["total_tokens"] / average_tokens
            add_attention(
                usage["issue_key"], usage.get("title"),
                f"AI 消耗偏高：{compact_count(usage['total_tokens'])} Token，约为近期平均的 {ratio:.1f} 倍",
                "cost", usage,
            )
        elif (
            average_wakeups > 0 and int(usage.get("wakeups") or 0) >= WAKEUP_ATTENTION_MINIMUM
            and float(usage["wakeups"]) >= average_wakeups * 2
        ):
            ratio = float(usage["wakeups"]) / average_wakeups
            add_attention(
                usage["issue_key"], usage.get("title"),
                f"模型唤醒偏多：{usage['wakeups']} 次，约为近期平均的 {ratio:.1f} 倍",
                "cost", usage,
            )

    if attention:
        health = {
            "ok": False,
            "title": f"有 {len(attention)} 个问题需要关注",
            "explanation": (
                "发现执行失败、暂停或长时间未完成，请优先查看下方提示。"
                if operational_alert_count else
                "自动处理没有故障，但部分 Issue 的 AI 消耗明显偏高。"
            ),
        }
    else:
        health = {
            "ok": True, "title": "运行正常",
            "explanation": "未发现执行失败、结果不确定、异常暂停或长时间未完成的任务。",
        }
    return render_template(
        "runtime.html", threads=threads, events=events, turns=turns,
        today=today, today_tool_calls=today_tool_calls,
        savings=savings, issue_usage=issue_usage[:10],
        attention=attention, health=health,
        filters={"issue": issue, "role": role, "operator": operator, "status": status_filter},
        thread_statuses=RUNTIME_THREAD_STATUSES, event_statuses=RUNTIME_EVENT_STATUSES,
    )


@app.post("/runtime/events/<event_id>/retry")
def runtime_retry_event(event_id: str):
    try:
        run_runtime_command("retry-event", "--event-id", event_id, "--confirm")
        return redirect_back("runtime_overview", msg="调度事件已重新放回待处理队列")
    except Exception as exc:
        return redirect_back("runtime_overview", err=str(exc))


@app.post("/runtime/threads/<issue_key>/<operator_id>/reconcile")
def runtime_reconcile_thread(issue_key: str, operator_id: str):
    try:
        run_runtime_command("reconcile", "--issue", issue_key, "--operator", operator_id)
        return redirect_back("runtime_overview", msg="线程核对已完成；不确定的业务动作没有自动重放")
    except Exception as exc:
        return redirect_back("runtime_overview", err=str(exc))


@app.post("/runtime/threads/<issue_key>/<operator_id>/pause")
def runtime_pause_thread(issue_key: str, operator_id: str):
    try:
        run_runtime_command("pause-thread", "--issue", issue_key, "--operator", operator_id, "--confirm")
        return redirect_back("runtime_overview", msg="线程已暂停")
    except Exception as exc:
        return redirect_back("runtime_overview", err=str(exc))


@app.route("/issues/<issue_key>/assignment", methods=["POST"])
def issue_set_assignment(issue_key: str):
    """记录真正选定的执行配置；推荐只是候选，assignment 才是调度结果。"""
    args = ["--issue-key", issue_key]
    profile_id = request.form.get("profile_id", "").strip()
    if profile_id:
        args.extend(["--profile-id", profile_id])
    try:
        run_human_command("issue-set-assignment", *args)
        return redirect_back("issue_detail", issue_key=issue_key, msg="执行者分配已保存")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/assessment", methods=["POST"])
def issue_update_assessment(issue_key: str):
    args = ["--issue-key", issue_key]
    for field in ("dimension", "severity"):
        if value := request.form.get(field):
            args.extend([f"--{field.replace('_', '-')}", value])
    try:
        run_human_command("issue-update-assessment", *args)
        return redirect_back("issue_detail", issue_key=issue_key, msg="问题评级已保存")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/body", methods=["POST"])
def issue_update_body(issue_key: str):
    args = ["--issue-key", issue_key]
    for field in ("title", "summary", "expected_outcome", "technical_note"):
        if (value := request.form.get(field)) is not None:
            args.extend([f"--{field.replace('_', '-')}", value])
    if (local_terms := request.form.get("local_terms")) is not None:
        args.extend(["--local-terms", local_terms])
    try:
        run_human_command("issue-update-body", *args)
        return redirect_back("issue_detail", issue_key=issue_key, msg="问题内容已保存")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/status", methods=["POST"])
def issue_update_status(issue_key: str):
    try:
        run_human_command(
            "issue-update-status", "--issue-key", issue_key, "--status", request.form.get("status", ""),
            "--content", request.form.get("content", ""),
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="问题状态已更新")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/human-action", methods=["POST"])
def issue_human_action(issue_key: str):
    action = request.form.get("action", "")
    content = request.form.get("content", "").strip()
    try:
        if action == "confirm":
            verification_content = content or "Human 已复核本次实现与验证结果，验证通过。"
            run_human_command(
                "activity-append", "--issue-key", issue_key, "--activity-type", "VERIFICATION_PASSED",
                "--content", verification_content,
            )
            try:
                run_human_command(
                    "issue-update-status", "--issue-key", issue_key, "--status", "CONFIRMED",
                    "--content", content or "Human 验证通过并确认问题。",
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"验证通过活动已追加，但确认状态失败：{exc}") from exc
            message = "验证通过，问题已确认"
        elif action == "confirmation":
            if not content:
                raise ValueError("请填写审核确认结论")
            run_human_command(
                "activity-append", "--issue-key", issue_key,
                "--activity-type", "INSPECTOR_CONFIRMATION_PROVIDED", "--content", content,
            )
            target_status = request.form.get("target_status", "IN_PROGRESS")
            try:
                run_human_command(
                    "issue-update-status", "--issue-key", issue_key, "--status", target_status,
                    "--content", content,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"审核确认活动已追加，但状态更新失败：{exc}") from exc
            message = "确认结论已记录，问题状态已更新"
        elif action == "retry":
            if not content:
                raise ValueError("请填写实现失败原因、必须修改点和验证标准")
            run_human_command(
                "activity-append", "--issue-key", issue_key,
                "--activity-type", "VERIFICATION_FAILED", "--content", content,
            )
            try:
                run_human_command(
                    "issue-update-status", "--issue-key", issue_key, "--status", "IN_PROGRESS",
                    "--content", content,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"验证失败活动已追加，但状态更新失败：{exc}") from exc
            message = "已按实现错误退回，继续沿用原设计"
        else:
            targets = {"retry": "IN_PROGRESS", "redesign": "REDESIGN_REQUIRED", "blocked": "BLOCKED", "hold": "ON_HOLD", "cancel": "CANCELLED", "resume": "IN_PROGRESS"}
            target_status = targets.get(action)
            if not target_status:
                raise ValueError("未知的 Human 操作")
            run_human_command(
                "issue-update-status", "--issue-key", issue_key, "--status", target_status,
                "--content", content,
            )
            message = f"问题已更新为{label(target_status)}"
        return redirect_back("issue_detail", issue_key=issue_key, msg=message)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/design-request", methods=["POST"])
def issue_design_request(issue_key: str):
    try:
        run_human_command(
            "design-request", "--issue-key", issue_key,
            "--content", request.form.get("content", ""),
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="已要求先完成设计方案")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/design-submit", methods=["POST"])
def issue_design_submit(issue_key: str):
    try:
        run_human_command(
            "design-submit", "--issue-key", issue_key,
            "--summary", request.form.get("summary", ""),
            "--content", request.form.get("content", ""),
            "--scope-changes", "[]",
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="设计方案已提交审核")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/design-review", methods=["POST"])
def issue_design_review(issue_key: str):
    try:
        args = [
            "design-review", "--issue-key", issue_key,
            "--decision", request.form.get("decision", ""),
            "--design-activity-id", request.form.get("design_activity_id", ""),
            "--content", request.form.get("content", ""),
        ]
        if request.form.get("decision") == "approved":
            args.extend([
                "--execution-mode", request.form.get("execution_mode", ""),
                "--confirmation", "not-needed",
            ])
            if raw_stages := request.form.get("stages", "").strip():
                json.loads(raw_stages)
                args.extend(["--stages", raw_stages])
        run_human_command(*args)
        return redirect_back("issue_detail", issue_key=issue_key, msg="设计审核结论已记录")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/stages/<int:stage_no>/review", methods=["POST"])
def issue_stage_review(issue_key: str, stage_no: int):
    args = [
        "--issue-key", issue_key, "--stage-no", str(stage_no),
        "--decision", request.form.get("decision", ""),
        "--content", request.form.get("content", ""),
        "--review-result", request.form.get("review_result", "{}"),
        "--baseline", request.form.get("baseline", "{}"),
    ]
    if summary := request.form.get("summary", "").strip():
        args.extend(["--summary", summary])
    if plan_no := request.form.get("plan_no"):
        args.extend(["--plan-no", plan_no])
    try:
        run_human_command("stage-review", *args)
        return redirect_back("issue_detail", issue_key=issue_key, msg="Stage 验收结论已记录")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/human-confirmation-resolve", methods=["POST"])
def issue_human_confirmation_resolve(issue_key: str):
    try:
        run_human_command(
            "human-confirmation-resolve", "--issue-key", issue_key,
            "--decision", request.form.get("decision", ""),
            "--content", request.form.get("content", ""),
            "--next-status", request.form.get("next_status", "DESIGN_REQUIRED"),
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="人工决定已记录，Agent 工作流可以恢复")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/activities", methods=["POST"])
def issue_add_activity(issue_key: str):
    try:
        run_human_command(
            "discussion-append", "--issue-key", issue_key,
            "--topic", request.form.get("topic", "GENERAL"),
            "--content", request.form.get("content", ""),
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="讨论内容已追加")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/candidates")
def candidate_list():
    task_key = request.args.get("task_key", "")
    status = request.args.get("status", "")
    filters, params = ["WHERE 1=1"], []
    if task_key:
        filters.append("AND t.task_key = ?")
        params.append(task_key)
    if status:
        filters.append("AND c.status = ?")
        params.append(status)
    else:
        filters.append("AND c.status IN ('SUBMITTED', 'UNDER_REVIEW')")
    candidates = [candidate_with_json(row) for row in query_all(
        f"""SELECT c.*, t.task_key, t.project_name FROM issue_candidate c
            JOIN review_task t ON t.id = c.task_id {' '.join(filters)}
            ORDER BY c.updated_at DESC, c.id DESC""", params,
    )]
    counts = query_all("SELECT status, COUNT(*) AS total FROM issue_candidate GROUP BY status")
    metrics = {item["status"]: int(item["total"]) for item in counts}
    tasks = query_all("SELECT task_key, title FROM review_task ORDER BY updated_at DESC, id DESC")
    return render_template(
        "candidates_list.html", candidates=candidates, tasks=tasks, statuses=CANDIDATE_STATUSES,
        metrics=metrics, filters={"task_key": task_key, "status": status}, return_to=request.full_path,
    )


@app.route("/candidates/<candidate_key>/status", methods=["POST"])
def candidate_update_status(candidate_key: str):
    content = request.form.get("content", "").strip()
    status = request.form.get("status", "")
    target = safe_return_to(url_for("candidate_list"))
    if status in {"ACCEPTED", "REJECTED"} and not content:
        return feedback_redirect(target, err="接受或拒绝候选问题时必须填写审核结论")
    try:
        args = ["--candidate-key", candidate_key, "--status", status]
        if content:
            args.extend(["--content", content])
        run_human_command("candidate-update-status", *args)
        return feedback_redirect(target, msg=f"候选问题已更新为{label(status)}")
    except Exception as exc:  # noqa: BLE001
        return feedback_redirect(target, err=str(exc))


@app.route("/issues")
@app.route("/audit")
def legacy_redirect():
    return redirect(url_for("task_list"))


@app.get("/routing")
def routing_config():
    """Agent Model Routing 配置页；配置文件不存在、无效或启用都正常渲染。"""
    try:
        status = routing.status_view()
    except Exception as exc:  # noqa: BLE001 - 配置页本身不能因配置问题打不开
        status = {
            "status": "UNAVAILABLE", "enabled": False, "version": None,
            "profiles": [], "capabilities": {}, "revision": None,
            "configPath": "<不可用>", "capabilityPath": "<不可用>",
            "pathOverride": None, "moduleSource": routing.module_source(),
            "error": str(exc), "enabledCount": 0,
            "message": "Agent Routing 模块不可用。",
            "agentOptions": [], "modelOptions": {}, "levelScale": [1, 2, 3, 4, 5],
        }
    context = _routing_page_context(status)
    return render_template("routing.html", **context)


def _routing_page_context(status: dict, seed_preview: dict | None = None, **extra):
    """页面渲染上下文；各处渲染路径共用，避免字段漏传。"""
    try:
        example = routing.example_text()
    except Exception:  # noqa: BLE001
        example = ""
    try:
        yaml_text = routing.dump_yaml(
            {"version": status.get("version") or 2, "profiles": list(status.get("profiles") or [])}
        )
    except Exception:  # noqa: BLE001 - 序列化异常时退回示例文本，页面仍可打开
        yaml_text = example
    discovered, discovery_error = [], None
    try:
        discovered = routing.discover_candidates()
    except Exception as exc:  # noqa: BLE001 - 发现不可用时页面仍要能打开
        discovery_error = str(exc)
    try:
        capabilities_payload = routing.capabilities_json()
    except Exception:  # noqa: BLE001
        capabilities_payload = '{"capabilities": {}, "fallbackReasonings": []}'
    config_path = routing.routing_config_path()
    try:
        groups = routing.profile_groups(status)
    except Exception:  # noqa: BLE001 - 分组失败时页面仍要能打开
        groups = []
    return {
        "status": status,
        "example": example,
        "yaml_text": yaml_text,
        "discovered": discovered,
        "discovery_error": discovery_error,
        "seed_preview": seed_preview,
        "capabilities_payload": capabilities_payload,
        "config_exists": config_path.exists(),
        "profile_groups": groups,
        **extra,
    }


@app.post("/routing/seed")
def routing_seed():
    """从本机已安装 Agent 初始化执行配置。

    默认只预览；`apply=1` 时按同样的校验与原子写入流程保存。
    已有配置时默认合并（保护人工配置），只有显式 `replace=1` 才整体替换。
    """
    replace = request.form.get("replace") == "1"
    try:
        result = routing.seed_result(replace=replace)
    except Exception as exc:  # noqa: BLE001
        return redirect_back("routing_config", err=f"无法从本机 Agent 初始化：{exc}")
    if not result["document"]["profiles"]:
        return redirect_back(
            "routing_config",
            err="没有发现可用候选：本机没有绑定 Developer 身份，或对应 Agent 的模型配置无法读取。",
        )
    if result["mode"] == "merge" and not result["added"]:
        return redirect_back(
            "routing_config",
            msg=f"现有配置已覆盖全部本机 Agent，无需新增；保留原有 {result['kept']} 条执行配置。",
        )
    if request.form.get("apply") != "1":
        status = routing.status_view()
        return render_template(
            "routing.html",
            **_routing_page_context(status, seed_preview=result),
        )
    try:
        saved = routing.save_document(result["document"])
    except Exception as exc:  # noqa: BLE001
        return redirect_back("routing_config", err=f"初始化配置未保存：{exc}")
    snapshot = saved["snapshot"]
    if snapshot["status"] != "ENABLED":
        return redirect_back(
            "routing_config",
            err=f"初始化配置已写入但未启用：{snapshot.get('error') or snapshot['status']}",
        )
    action = {"merge": "合并", "replace": "替换", "create": "创建"}.get(result["mode"], "写入")
    return redirect_back(
        "routing_config",
        msg=f"已{action} {len(snapshot['profiles'])} 条执行配置并立即生效。",
    )


@app.post("/routing/save")
def routing_save():
    """结构化表单保存：完整校验后才原子替换文件，失败时保留原配置。"""
    mode = request.form.get("mode", "rows")
    try:
        if mode == "yaml":
            result = routing.save_yaml_text(request.form.get("yaml_text", ""))
        else:
            result = routing.save_document({
                "version": 2,
                "profiles": routing.profiles_from_form(request.form),
            })
    except Exception as exc:  # noqa: BLE001
        return redirect_back("routing_config", err=f"配置未保存：{exc}")
    snapshot = result["snapshot"]
    if snapshot["status"] != "ENABLED":
        return redirect_back(
            "routing_config", err=f"配置已写入但未启用：{snapshot.get('error') or snapshot['status']}"
        )
    return redirect_back(
        "routing_config",
        msg=f"配置已保存并立即生效，共 {len(snapshot['profiles'])} 条 Dev 执行配置。",
    )


@app.post("/routing/validate")
def routing_validate():
    """只校验不写盘，用于页面「校验配置」。"""
    mode = request.form.get("mode", "rows")
    try:
        if mode == "yaml":
            module = routing.load_routing_module()
            document = module.parse_routing_text(
                request.form.get("yaml_text", ""), routing.load_capabilities(),
            )
        else:
            document = {
                "version": 2,
                "profiles": routing.profiles_from_form(request.form),
            }
        ok, error = routing.validate_document(document)
    except Exception as exc:  # noqa: BLE001
        return redirect_back("routing_config", err=f"配置校验失败：{exc}")
    if not ok:
        return redirect_back("routing_config", err=f"配置校验失败：{error}")
    count = len(document.get("profiles") or [])
    return redirect_back("routing_config", msg=f"配置校验通过，共 {count} 条 Dev 执行配置。")


@app.context_processor
def inject_globals():
    queue_counts = {"pending_review": 0, "confirmation": 0, "blocked": 0}
    if request.endpoint != "healthcheck":
        try:
            queue_counts = issue_summary_where()
        except RuntimeError:
            pass
    return {
        "flash_msg": request.args.get("msg", "") or request.args.get("err", ""),
        "flash_kind": "err" if request.args.get("err") else ("ok" if request.args.get("msg") else ""),
        "queue_counts": queue_counts,
        "csrf_token": csrf_token(),
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("WEBTOOL_PORT", "5050")),
            debug=env_bool("WEBTOOL_DEBUG"))
