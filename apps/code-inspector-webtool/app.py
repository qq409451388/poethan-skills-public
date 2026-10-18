"""Code Inspector 本机 Human 工作台。

SQLite 只用于页面查询；所有写操作必须经过安装后的 cictl WebApp 身份入口。
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

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from markupsafe import Markup

from commands import run_human_command
from db import parse_json_field, query_all, query_one
import routing
from workflow_view import workflow_graph
from issue_timing import attach_timing

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
# 本地工具，页面会边用边改：模板始终按 mtime 重新读取，避免"改了模板但页面不变、
# 必须重启进程才生效"的假故障（非 debug 模式下 Jinja 默认会把模板缓存在进程内存里）。
app.config["TEMPLATES_AUTO_RELOAD"] = True
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
# “待我处理”排行榜只展示仍可推进的 Issue；受阻项通过页头的受阻队列单独查看。
INBOX_LEADERBOARD_EXCLUDED_STATUSES = (*NON_OPEN_ISSUE_STATUSES, "BLOCKED")
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
    ("IMPLEMENTATION", "实现问题"), ("VERIFICATION", "验证问题"),
    ("BLOCKER", "阻断项"), ("REVIEW", "审核反馈"), ("SCOPE", "范围确认"),
]
HISTORY_MILESTONE_TYPES = {
    "ISSUE_CREATED", "DESIGN_REQUESTED", "DESIGN_SUBMITTED", "REDESIGN_SUBMITTED",
    "DESIGN_APPROVED", "DESIGN_REJECTED", "STAGE_PLAN_CREATED", "STAGE_SCOPE_DECLARED", "STAGE_SUBMITTED",
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
    "CLI_DESIGN_CONFIRMATION": "开发范围授权", "INSPECTOR_CONFIRMATION": "审核边界确认",
    "DESIGN_REVIEW": "设计结论", "STAGE_REVIEW": "阶段验收结论",
    "IMPLEMENTATION_REVIEW": "实现审核结论", "SCOPE_CONFIRMATION": "边界确认",
    "HUMAN_CONFIRMATION": "人工决定", "DISCUSSION_CONCLUSION": "讨论结论",
    "VERIFICATION": "验证结论",
}
LABELS = {
    "BOUNDARY_CONFIRMED": "边界已确认",
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
    "implementation.submit", "human.escalate", "human.confirmation-resolve", "issue.set-progress",
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
    "profile_changed": "该执行配置的 Agent / Model / Reasoning 已被修改",
    "profile_level_below_difficulty": "该执行配置的等级已低于当前 difficulty",
    "profile_level_reduced": "该执行配置的等级被下调",
}


@app.template_filter("assignment_reason")
def assignment_reason(value: str | None) -> str:
    return ASSIGNMENT_REASON_LABELS.get(value or "", value or "未提供")


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
    if activity_type == "STAGE_SCOPE_DECLARED":
        return f"第 {stage_no} 阶段已声明开发影响范围。" if stage_no else "当前阶段已声明开发影响范围。"
    if activity_type == "STAGE_SUBMITTED":
        return f"第 {stage_no} 阶段已提交，等待验收。" if stage_no else "当前阶段已提交，等待验收。"
    if activity_type == "STAGE_APPROVED":
        return f"第 {stage_no} 阶段已验收通过。" if stage_no else "当前阶段已验收通过。"
    if activity_type == "STAGE_REJECTED":
        return f"第 {stage_no} 阶段未通过，需要继续修改。" if stage_no else "当前阶段未通过，需要继续修改。"
    if activity_type == "STATUS_CHANGED":
        return f"问题状态已变更为“{label(activity.get('result_status'))}”。"
    return fixed.get(activity_type, label(activity_type))



CHANGE_FIELD_LABELS = {
    "title": "标题", "summary": "问题摘要", "expected_outcome": "完成标准", "technical_note": "技术补充",
    "local_terms_json": "项目术语", "status": "状态", "current_attempt_no": "实现轮次", "severity": "严重度",
    "dimension": "检查维度", "disposition": "处理安排", "difficulty": "难度", "assignment_json": "执行者",
    "progress": "进度", "progress_reason": "进度依据", "objective": "目标", "acceptance_criteria": "验收标准",
    "plan_status": "计划状态", "planned_change_scope_json": "修改范围", "change_reason": "修改原因",
    "protected_behaviors_json": "保护项", "submitted_commit_sha": "提交版本", "diff_summary": "改动说明",
    "baseline_status": "基线状态",
}

def issue_change_summary(change: dict) -> str:
    values = change["changes"]
    if change["action"] in {"STAGE_CREATED", "STAGE_DELETED"}:
        verb = "创建" if change["action"] == "STAGE_CREATED" else "删除"
        return f"{verb}执行计划 {values.get('plan_no')} 的 Stage {values.get('stage_no')}：{values.get('title')}"
    field = values.get("field", "")
    target = (f"计划 {values.get('plan_no')} / Stage {values.get('stage_no')} 的"
              if change["entity_type"] == "stage" else "")
    def display(value):
        if value is None or value == "":
            return "未设置"
        if field == "progress":
            return f"{value}%"
        if field in {"status", "plan_status", "severity", "dimension", "disposition", "baseline_status"}:
            return label(str(value))
        return brief_text(str(value), 90)
    return f"修改{target}{CHANGE_FIELD_LABELS.get(field, field)}：{display(values.get('before'))} → {display(values.get('after'))}"

@app.template_filter("action_summary")
def action_summary(record: dict) -> str:
    if record.get("record_kind") == "change":
        return record["human_summary"]
    activity_type = record.get("activity_type")
    fixed = {"DESIGN_SUBMITTED": "提交设计方案。", "REDESIGN_SUBMITTED": "提交重新设计方案。"}
    return fixed.get(activity_type) or activity_history_summary({**record, "metadata": {k: v for k, v in (record.get("metadata") or {}).items() if k != "human_summary"}})

@app.template_filter("decision_scope_label")
def decision_scope_label(scope: str | None) -> str:
    if not scope:
        return ""
    if match := re.fullmatch(r"(\d+):(\d+)", scope):
        return f"执行计划 {match[1]} · Stage {match[2]}"
    if match := re.match(r"stage(\d+):", scope):
        return f"Stage {match[1]}"
    if match := re.fullmatch(r"attempt:(\d+)", scope):
        return f"第 {match[1]} 轮"
    return scope


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


def workqueue_data():
    if "workqueue" in g:
        return g.workqueue
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
           WHERE c.status IN ('SUBMITTED', 'UNDER_REVIEW') ORDER BY c.updated_at DESC, c.id DESC"""
    )]
    metrics = {name: len(rows) for name, rows in issue_groups.items()}
    metrics["candidates"] = len(candidates)
    g.workqueue = {"groups": issue_groups, "candidates": candidates, "metrics": metrics}
    return g.workqueue


@app.route("/")
def inbox():
    queue = workqueue_data()
    active_issues = query_all(
        f"""SELECT i.*, t.task_key, t.project_name,
                  MAX(i.updated_at, COALESCE((SELECT MAX(COALESCE(a.amended_at, a.created_at))
                      FROM issue_activity a WHERE a.issue_id=i.id), i.updated_at),
                      COALESCE((SELECT MAX(COALESCE(d.amended_at, d.created_at)) FROM issue_discussion d
                      WHERE d.issue_id=i.id), i.updated_at)) AS active_at
           FROM review_issue i JOIN review_task t ON t.id=i.task_id
           WHERE i.status NOT IN ({','.join('?' for _ in INBOX_LEADERBOARD_EXCLUDED_STATUSES)})
           ORDER BY i.progress IS NULL, i.progress DESC, active_at DESC, i.id DESC LIMIT 20""",
        INBOX_LEADERBOARD_EXCLUDED_STATUSES,
    )
    if active_issues:
        placeholders = ",".join("?" for _ in active_issues)
        stage_rows = query_all(
            f"SELECT * FROM issue_stage WHERE issue_id IN ({placeholders}) ORDER BY plan_no DESC, stage_no",
            [item["id"] for item in active_issues],
        )
        for item in active_issues:
            history = [row for row in stage_rows if row["issue_id"] == item["id"]]
            if not history:
                continue
            active = [row for row in history if row["plan_status"] == "ACTIVE"]
            current = next((row for row in active if row["status"] in {"IN_PROGRESS", "PENDING_REVIEW"}), None)
            item["stage_summary"] = {
                "plans": len({row["plan_no"] for row in history}),
                "history_plans": sorted({row["plan_no"] for row in history if row["plan_status"] != "ACTIVE"}),
                "active_plan_no": active[0]["plan_no"] if active else None,
                "active_total": len(active),
                "active_approved": sum(row["status"] == "APPROVED" for row in active),
                "current": current,
            }
    return render_template(
        "inbox.html", metrics=queue["metrics"],
        active_issues=active_issues,
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
        result = run_human_command("task", "create", *args, cwd=project_path.resolve())
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
    include_closed = (
        # 兼容旧版持续治理列表链接；新开关参数优先。
        request.args.get("include_closed", request.args.get("show_completed")) == "1"
        or tab in {"completed", "confirmed"}
        or issue_status in NON_OPEN_ISSUE_STATUSES
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
    if not include_closed:
        filters.append(f"AND i.status NOT IN ({','.join('?' for _ in NON_OPEN_ISSUE_STATUSES)})")
        params.extend(NON_OPEN_ISSUE_STATUSES)
    issues = [issue_with_json(row) for row in query_all(
        f"""SELECT i.* FROM review_issue i {' '.join(filters)}
            ORDER BY CASE i.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     i.updated_at DESC, i.issue_key ASC""", params,
    )]
    for issue in issues:
        issue["assignment_profiles"] = routing.candidate_profiles(issue.get("difficulty"))
        issue["assignment_status"], issue["assignment_invalid_reason"] = routing.assignment_state(
            issue.get("assignment"), issue.get("difficulty"),
        )
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
        if include_closed else summary["open"]
    )
    return render_template(
        "task_detail.html", task=task, issues=issues, versions=versions, summary=summary,
        level_roles=routing.LEVEL_ROLES, rank_skin=routing.rank_skin(),
        statuses=TASK_STATUSES, issue_statuses=ISSUE_STATUSES, severities=SEVERITIES, dimensions=DIMENSIONS,
        filters={
            "tab": tab, "issue_status": issue_status, "severity": severity, "dimension": dimension,
            "include_closed": include_closed,
        },
    )


@app.route("/tasks/<task_key>/edit", methods=["POST"])
def task_update(task_key: str):
    args = [task_key]
    for field in ("title", "objective", "remark", "close_reason"):
        value = request.form.get(field)
        if value is not None:
            args.extend([f"--{field.replace('_', '-')}", value])
    target = safe_return_to(url_for("task_detail", task_key=task_key))
    try:
        run_human_command("task", "edit", *args)
        return feedback_redirect(target, msg="任务信息已保存")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return feedback_redirect(target, err=str(exc))


@app.route("/tasks/<task_key>/status", methods=["POST"])
def task_update_status(task_key: str):
    args = [task_key, request.form.get("status", "")]
    for field in ("remark", "close_reason"):
        if value := request.form.get(field):
            args.extend([f"--{field.replace('_', '-')}", value])
    target = safe_return_to(url_for("task_detail", task_key=task_key))
    try:
        run_human_command("task", "status", *args)
        return feedback_redirect(target, msg="任务状态已更新")
    except Exception as exc:  # noqa: BLE001
        return feedback_redirect(target, err=str(exc))


@app.route("/issues/<issue_key>")
def issue_detail(issue_key: str):
    issue = query_one(
        """SELECT i.*, t.task_key, t.project_name, t.project_path
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
    topics = query_all("SELECT * FROM issue_discussion_topic WHERE issue_id=? ORDER BY id DESC", (issue["id"],))
    topic_by_id = {topic["id"]: topic for topic in topics}
    discussions = query_all(
        "SELECT * FROM issue_discussion WHERE issue_id=? ORDER BY created_at ASC,id ASC", (issue["id"],)
    )
    for discussion in discussions:
        discussion["record_kind"] = "discussion"
    for record in [*discussions, *activities]:
        topic = topic_by_id.get(record.get("topic_id"))
        record["discussion_topic"] = topic
    history_activities = [activity for activity in activities if activity["activity_type"] in HISTORY_MILESTONE_TYPES]
    discussion_records = sorted(
        [*discussions, *(activity for activity in activities if activity["is_collaborative_submission"])],
        key=record_sort_key, reverse=True,
    )
    changes = query_all("SELECT * FROM issue_change WHERE issue_id=? ORDER BY id DESC", (issue["id"],))
    for change in changes:
        change["record_kind"] = "change"
        change["changes"] = parse_json_field(change["changes_json"], {})
        change["human_summary"] = issue_change_summary(change)
    # 已迁入讨论的遗留活动不再重复展示。
    migrated_ids = {record.get("source_activity_id") for record in discussions if record.get("source_activity_id")}
    all_records = sorted(
        [*discussions, *(activity for activity in activities if activity["id"] not in migrated_ids), *changes],
        key=record_sort_key, reverse=True,
    )
    # 字段变化由 journal 精确记录；有对应 journal 时不再重复列出状态通知。
    has_status_changes = any(change["changes"].get("field") == "status" and change["entity_type"] == "issue" for change in changes)
    history_records = sorted(
        [*changes, *(activity for activity in history_activities
                    if activity["activity_type"] != "STATUS_CHANGED" or not has_status_changes
                    or activity["created_at"] < min((change["created_at"] for change in changes), default=""))],
        key=record_sort_key, reverse=True,
    )
    history_keys = {(record["record_kind"], record["id"]) for record in history_records}
    for record in all_records:
        record["in_history"] = (record["record_kind"], record["id"]) in history_keys
        record["in_all"] = (record["record_kind"] == "discussion"
                            or record["record_kind"] == "activity" and (
                                record["is_collaborative_submission"]
                                or record["activity_type"] not in HISTORY_MILESTONE_TYPES))
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
    attach_timing(issue, stages, activities)
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
    issue["recommended_executors"] = routing.recommended_for(issue.get("difficulty"))
    issue["assignment_status"], issue["assignment_invalid_reason"] = routing.assignment_state(
        issue.get("assignment"), issue.get("difficulty"),
    )
    stage, status_explanation = STATUS_PRESENTATION.get(issue["status"], (1, issue["status"]))
    return render_template(
        "issue_detail.html", issue=issue, activities=history_activities, activity_groups=grouped,
        decisions=decisions, discussions=discussions, discussion_records=discussion_records,
        all_records=all_records, all_content_count=sum(record["in_all"] for record in all_records), history_records=history_records, issue_topics=topics,
        latest_implementation=latest_implementation, latest_design_submission=latest_design_submission,
        verification_activities=verification_activities,
        latest_human_request=latest_human_request,
        stage_plans=stage_plans, active_stage_plan=active_stage_plan,
        current_execution_stage=current_execution_stage,
        workflow=workflow_graph(issue, activities, stages, current_execution_stage, discussions),
        current_stage=stage, status_explanation=status_explanation, dimensions=DIMENSIONS,
        severities=SEVERITIES, benefits=BENEFITS, costs=COSTS, confidence=CONFIDENCE,
        dispositions=DISPOSITIONS, activity_types=ACTIVITY_TYPES, discussion_topics=DISCUSSION_TOPICS,
        issue_statuses=ISSUE_STATUSES,
        assignment_profiles=routing.candidate_profiles(issue.get("difficulty")),
        level_roles=routing.LEVEL_ROLES, rank_skin=routing.rank_skin(),
    )


@app.route("/issues/<issue_key>/assignment", methods=["POST"])
def issue_set_assignment(issue_key: str):
    """记录真正选定的执行配置；推荐只是候选，assignment 才是调度结果。"""
    args = [issue_key]
    profile_id = request.form.get("profile_id", "").strip()
    if profile_id:
        args.extend(["--profile-id", profile_id])
    try:
        run_human_command("issue", "assign", *args)
        return feedback_redirect(safe_return_to(url_for("issue_detail", issue_key=issue_key)), msg="执行者分配已保存")
    except Exception as exc:  # noqa: BLE001
        return feedback_redirect(safe_return_to(url_for("issue_detail", issue_key=issue_key)), err=str(exc))


@app.route("/issues/<issue_key>/assessment", methods=["POST"])
def issue_update_assessment(issue_key: str):
    args = [issue_key]
    for field in ("dimension", "severity"):
        if value := request.form.get(field):
            args.extend([f"--{field.replace('_', '-')}", value])
    try:
        run_human_command("issue", "rate", *args)
        return redirect_back("issue_detail", issue_key=issue_key, msg="问题评级已保存")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/body", methods=["POST"])
def issue_update_body(issue_key: str):
    body = {}
    for field in ("title", "summary", "expected_outcome", "technical_note"):
        if (value := request.form.get(field)) is not None:
            body[field] = value
    try:
        if (local_terms := request.form.get("local_terms")) is not None:
            body["local_terms"] = json.loads(local_terms)
        run_human_command("issue", "edit", issue_key, "-", payload=body)
        return redirect_back("issue_detail", issue_key=issue_key, msg="问题内容已保存")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/status", methods=["POST"])
def issue_update_status(issue_key: str):
    try:
        run_human_command(
            "issue", "status", issue_key, request.form.get("status", ""),
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
                "activity", "add", issue_key, "--activity-type", "VERIFICATION_PASSED",
                "--content", verification_content,
            )
            try:
                run_human_command(
                    "issue", "status", issue_key, "CONFIRMED",
                    "--content", content or "Human 验证通过并确认问题。",
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"验证通过活动已追加，但确认状态失败：{exc}") from exc
            message = "验证通过，问题已确认"
        elif action == "confirmation":
            if not content:
                raise ValueError("请填写审核确认结论")
            run_human_command(
                "activity", "add", issue_key,
                "--activity-type", "INSPECTOR_CONFIRMATION_PROVIDED", "--content", content,
            )
            target_status = request.form.get("target_status", "IN_PROGRESS")
            try:
                run_human_command(
                    "issue", "status", issue_key, target_status,
                    "--content", content,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"审核确认活动已追加，但状态更新失败：{exc}") from exc
            message = "确认结论已记录，问题状态已更新"
        elif action == "retry":
            if not content:
                raise ValueError("请填写实现失败原因、必须修改点和验证标准")
            run_human_command(
                "activity", "add", issue_key,
                "--activity-type", "VERIFICATION_FAILED", "--content", content,
            )
            try:
                run_human_command(
                    "issue", "status", issue_key, "IN_PROGRESS",
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
                "issue", "status", issue_key, target_status,
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
            "design", "request", issue_key,
            "--content", request.form.get("content", ""),
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="已要求先完成设计方案")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/design-submit", methods=["POST"])
def issue_design_submit(issue_key: str):
    try:
        run_human_command(
            "design", "submit", issue_key,
            "--summary", request.form.get("summary", ""),
            "--content", request.form.get("content", ""),
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="设计方案已提交审核")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/design-review", methods=["POST"])
def issue_design_review(issue_key: str):
    try:
        decision = request.form.get("decision", "")
        payload = {"content": request.form.get("content", "")}
        if decision == "approved":
            payload.update({
                "execution_mode": request.form.get("execution_mode", ""),
                "confirmation": "not-needed",
            })
            if raw_stages := request.form.get("stages", "").strip():
                payload["stages"] = json.loads(raw_stages)
        elif decision != "rejected":
            raise ValueError("未知的设计审核结论")
        run_human_command(
            "design", "approve" if decision == "approved" else "reject",
            issue_key, request.form.get("design_activity_id", ""), "-", payload=payload,
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="设计审核结论已记录")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/stages/<int:stage_no>/review", methods=["POST"])
def issue_stage_review(issue_key: str, stage_no: int):
    try:
        review = json.loads(request.form.get("review_result", "{}"))
        if baseline := request.form.get("baseline", "").strip():
            review["baseline"] = json.loads(baseline)
        if comment := request.form.get("content", "").strip():
            review["comment"] = comment
        decision = request.form.get("decision", "")
        if decision not in {"approved", "rejected", "redesign"}:
            raise ValueError("未知的 Stage 审核结论")
        run_human_command(
            "stage", "redesign" if decision == "redesign" else "review",
            issue_key, str(stage_no), "-", payload=review,
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="Stage 验收结论已记录")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/human-confirmation-resolve", methods=["POST"])
def issue_human_confirmation_resolve(issue_key: str):
    try:
        run_human_command(
            "human", "resolve", issue_key,
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
        if not request.form.get("content", "").strip():
            raise ValueError("讨论正文不能为空")
        topic_id = request.form.get("topic_id", "").strip()
        title = request.form.get("topic_title", "").strip()
        if not topic_id:
            if not title:
                raise ValueError("请选择已有话题，或填写新话题标题")
            result = run_human_command("discussion", "topic-create", issue_key,
                                      "--category", request.form.get("topic", "GENERAL"), "--title", title)
            topic_id = str(result["topic_id"])
        args = ["discussion", "add", issue_key, "--topic-id", topic_id,
                "--content", request.form.get("content", "")]
        if summary := request.form.get("human_summary", "").strip():
            args.extend(["--human-summary", summary])
        run_human_command(*args)
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
    if status not in {"ACCEPTED", "REJECTED"}:
        return feedback_redirect(target, err="未知的候选问题审核状态")
    try:
        args = [candidate_key]
        if content:
            args.extend(["--content", content])
        run_human_command("candidate", "accept" if status == "ACCEPTED" else "reject", *args)
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
            "error": str(exc),
            "message": "Agent Routing 模块不可用。",
            "agentOptions": [], "modelOptions": {}, "levelScale": [1, 2, 3, 4, 5],
        }
    context = _routing_page_context(status)
    return render_template("routing.html", **context)


def _routing_page_context(status: dict):
    """执行配置只读展示；YAML 原文是唯一编辑入口，非法配置也可直接修复。"""
    example = routing.example_text()
    path = routing.routing_config_path()
    try:
        yaml_text = path.read_text(encoding="utf-8") if path.exists() else example
    except OSError:
        yaml_text = example
    return {
        "status": status, "example": example, "yaml_text": yaml_text,
        "profile_groups": routing.profile_groups(status), "level_roles": routing.LEVEL_ROLES,
        "rank_skin": routing.rank_skin(status.get("style", 1)),
    }


@app.post("/routing/save")
def routing_save():
    """只接受 YAML：完整校验后才原子替换，失败时保留原配置。"""
    try:
        result = routing.save_yaml_text(request.form.get("yaml_text", ""))
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
    try:
        module = routing.load_routing_module()
        document = module.parse_routing_text(
            request.form.get("yaml_text", ""), routing.load_capabilities(),
        )
    except Exception as exc:  # noqa: BLE001
        return redirect_back("routing_config", err=f"配置校验失败：{exc}")
    count = len(document.get("profiles") or [])
    return redirect_back("routing_config", msg=f"配置校验通过，共 {count} 条 Dev 执行配置。")


@app.context_processor
def inject_globals():
    queue = {"groups": {key: [] for key in ("pending_review", "confirmation", "blocked")},
             "candidates": [], "metrics": {key: 0 for key in ("pending_review", "confirmation", "blocked", "candidates")}}
    if request.endpoint != "healthcheck":
        try:
            queue = workqueue_data()
        except RuntimeError:
            pass
    return {
        "flash_msg": request.args.get("msg", "") or request.args.get("err", ""),
        "flash_kind": "err" if request.args.get("err") else ("ok" if request.args.get("msg") else ""),
        "queue_counts": queue["metrics"],
        "workqueue": queue,
        "csrf_token": csrf_token(),
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("WEBTOOL_PORT", "5050")),
            debug=env_bool("WEBTOOL_DEBUG"))
