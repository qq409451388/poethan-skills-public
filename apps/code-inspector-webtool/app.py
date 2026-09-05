"""Code Inspector 本机 Human 工作台。

SQLite 只用于页面查询；所有写操作必须经过安装后的 review-db.py human 命令。
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import traceback
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, abort, redirect, render_template, request, url_for
from markupsafe import Markup

from commands import run_human_command
from db import parse_json_field, query_all, query_one

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.config["JSON_SORT_KEYS"] = False

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
    "REDESIGN_REQUIRED": (2, "原设计需要推翻，Developer 必须重新提交方案并获批后才能编码。"),
    "CONFIRMED": (5, "实现已验证通过，问题已经确认关闭。"),
    "CANCELLED": (5, "问题已取消，不再继续处理。"),
}


@app.template_filter("label")
def label(value: str | None) -> str:
    return LABELS.get(value or "", value or "—")


@app.template_filter("topic_label")
def topic_label(value: str | None) -> str:
    return TOPIC_LABELS.get(value or "", value or "—")


@app.template_filter("decision_label")
def decision_label(value: str | None) -> str:
    return DECISION_LABELS.get(value or "", value or "—")


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
    ):
        row[field] = parse_json_field(row.get(f"{field}_json"), default)
    return row


def activity_with_json(row: dict) -> dict:
    row["code_reference"] = parse_json_field(row.get("code_reference_json"), [])
    row["metadata"] = parse_json_field(row.get("metadata_json"), {})
    return row


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


@app.route("/tasks/<task_key>")
def task_detail(task_key: str):
    task = query_one("SELECT * FROM review_task WHERE task_key = ?", (task_key,))
    if not task:
        abort(404)
    tab = request.args.get("tab", "all")
    issue_status = request.args.get("issue_status", "")
    severity = request.args.get("severity", "")
    dimension = request.args.get("dimension", "")
    filters, params = ["WHERE i.task_id = ?"], [task["id"]]
    tab_statuses = {
        "mine": ("IMPLEMENTED_PENDING_REVIEW", "HUMAN_CONFIRMATION_REQUIRED", "BLOCKED"),
        "review": ("IMPLEMENTED_PENDING_REVIEW",), "blocked": ("BLOCKED",), "confirmed": ("CONFIRMED",),
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
    issues = [issue_with_json(row) for row in query_all(
        f"""SELECT i.* FROM review_issue i {' '.join(filters)}
            ORDER BY CASE i.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     i.updated_at DESC, i.issue_key ASC""", params,
    )]
    versions = query_all("SELECT * FROM review_task_version WHERE task_id = ? ORDER BY version_no DESC", (task["id"],))
    counts = query_one(
        """SELECT COUNT(*) AS total,
           SUM(CASE WHEN status NOT IN ('CONFIRMED','CANCELLED') THEN 1 ELSE 0 END) AS open,
           SUM(CASE WHEN severity IN ('critical','high') AND status NOT IN ('CONFIRMED','CANCELLED') THEN 1 ELSE 0 END) AS priority,
           SUM(CASE WHEN status IN ('IMPLEMENTED_PENDING_REVIEW','HUMAN_CONFIRMATION_REQUIRED','BLOCKED') THEN 1 ELSE 0 END) AS mine,
           SUM(CASE WHEN status = 'IMPLEMENTED_PENDING_REVIEW' THEN 1 ELSE 0 END) AS review,
           SUM(CASE WHEN status = 'BLOCKED' THEN 1 ELSE 0 END) AS blocked,
           SUM(CASE WHEN status = 'CONFIRMED' THEN 1 ELSE 0 END) AS confirmed
           FROM review_issue WHERE task_id = ?""", (task["id"],),
    ) or {}
    summary = {key: int(counts.get(key) or 0) for key in ("total", "open", "priority", "mine", "review", "blocked", "confirmed")}
    return render_template(
        "task_detail.html", task=task, issues=issues, versions=versions, summary=summary,
        statuses=TASK_STATUSES, issue_statuses=ISSUE_STATUSES, severities=SEVERITIES, dimensions=DIMENSIONS,
        filters={"tab": tab, "issue_status": issue_status, "severity": severity, "dimension": dimension},
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
    history_activities = [
        activity for activity in activities
        if activity["activity_type"] in HISTORY_MILESTONE_TYPES
    ]
    grouped: OrderedDict[int, list[dict]] = OrderedDict()
    for activity in history_activities:
        grouped.setdefault(activity["attempt_no"], []).append(activity)
    current_activities = [a for a in activities if a["attempt_no"] == issue["current_attempt_no"]]
    implementations = [a for a in current_activities if a["activity_type"] == "IMPLEMENTATION_SUBMITTED"]
    latest_implementation = implementations[-1] if implementations else None
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
    stage, status_explanation = STATUS_PRESENTATION.get(issue["status"], (1, issue["status"]))
    return render_template(
        "issue_detail.html", issue=issue, activities=history_activities, activity_groups=grouped,
        decisions=decisions, discussions=discussions,
        latest_implementation=latest_implementation, verification_activities=verification_activities,
        latest_human_request=latest_human_request,
        stage_plans=stage_plans, active_stage_plan=active_stage_plan,
        current_execution_stage=current_execution_stage,
        current_stage=stage, status_explanation=status_explanation, dimensions=DIMENSIONS,
        severities=SEVERITIES, benefits=BENEFITS, costs=COSTS, confidence=CONFIDENCE,
        dispositions=DISPOSITIONS, activity_types=ACTIVITY_TYPES, discussion_topics=DISCUSSION_TOPICS,
        issue_statuses=ISSUE_STATUSES,
        task_statuses=TASK_STATUSES,
    )


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
            "--content", request.form.get("content", ""),
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="设计方案已提交审核")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/design-review", methods=["POST"])
def issue_design_review(issue_key: str):
    try:
        run_human_command(
            "design-review", "--issue-key", issue_key,
            "--decision", request.form.get("decision", ""),
            "--content", request.form.get("content", ""),
        )
        return redirect_back("issue_detail", issue_key=issue_key, msg="设计审核结论已记录")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/stage-plan", methods=["POST"])
def issue_stage_plan_create(issue_key: str):
    try:
        raw_stages = request.form.get("stages", "")
        json.loads(raw_stages)
        run_human_command("stage-plan-create", "--issue-key", issue_key, "--stages", raw_stages)
        return redirect_back("issue_detail", issue_key=issue_key, msg="Stage 执行计划已创建")
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
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("WEBTOOL_PORT", "5050")),
            debug=bool(os.environ.get("WEBTOOL_DEBUG")))
