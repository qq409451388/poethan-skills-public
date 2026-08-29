"""Code Inspector 本机工作台：任务主列表 → 任务内问题。"""
from __future__ import annotations

import os
import json
import re
import traceback
from datetime import datetime, timezone
from html import escape
from pathlib import Path
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
ISSUE_STATUSES = ["PROPOSED", "IN_PROGRESS", "ON_HOLD", "BLOCKED", "INSPECTOR_CONFIRMATION_REQUIRED", "IMPLEMENTED_PENDING_REVIEW", "REDESIGN_REQUIRED", "CONFIRMED", "CANCELLED"]
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
    ("INSPECTOR_CONFIRMATION_PROVIDED", "审核确认结论"),
    ("VERIFICATION_PASSED", "验证通过"), ("VERIFICATION_FAILED", "验证失败"),
    ("VERIFICATION_EVIDENCE_ADDED", "补充验证证据"),
]
LABELS = {
    **dict(DIMENSIONS), **dict(SEVERITIES), **dict(BENEFITS), **dict(COSTS), **dict(CONFIDENCE), **dict(DISPOSITIONS),
    "PENDING": "待开始", "IN_PROGRESS": "进行中", "ON_HOLD": "已搁置", "BLOCKED": "受阻", "CLOSED": "已关闭", "CANCELLED": "已取消",
    "PROPOSED": "待处理", "INSPECTOR_CONFIRMATION_REQUIRED": "待审核确认", "IMPLEMENTED_PENDING_REVIEW": "待审核", "REDESIGN_REQUIRED": "需要重新设计",
    "CONFIRMED": "已确认", "CANCELLED": "已取消",
    "COMMENT_ADDED": "补充说明", "EVIDENCE_ADDED": "补充证据", "DESIGN_SUBMITTED": "提交设计",
    "IMPLEMENTATION_SUBMITTED": "提交实现", "REVIEW_APPROVED": "审核通过", "REVIEW_REJECTED": "审核驳回",
    "REDESIGN_SUBMITTED": "重新提交设计", "INSPECTOR_CONFIRMATION_PROVIDED": "审核确认结论", "VERIFICATION_PASSED": "验证通过",
    "VERIFICATION_FAILED": "验证失败", "VERIFICATION_EVIDENCE_ADDED": "补充验证证据", "STATUS_CHANGED": "状态变更",
}


@app.template_filter("label")
def label(value: str | None) -> str:
    return LABELS.get(value or "", value or "—")


@app.template_filter("json_pretty")
def json_pretty(value: object) -> str:
    """保留中文的 JSON 仅用于原始数据兜底展示。"""
    return json.dumps(value, ensure_ascii=False, indent=2)


@app.template_filter("yesno")
def yesno(value: object) -> str:
    return "是" if value else "否"


@app.template_filter("localtime")
def localtime(value: str | None) -> str:
    """将 SQLite CURRENT_TIMESTAMP 产生的 UTC 时间转换为页面时区。"""
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
    escaped = escape(value)
    return re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)


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
                code_lines = []
                code_language = ""
                in_code = False
            else:
                flush_text()
                code_language = fence.group(1)
                in_code = True
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
        ("evidence", []), ("estimated_change", {}),
    ):
        row[field] = parse_json_field(row.get(f"{field}_json"), default)
    return row


def activity_with_json(row: dict) -> dict:
    row["code_reference"] = parse_json_field(row.get("code_reference_json"), [])
    row["metadata"] = parse_json_field(row.get("metadata_json"), {})
    return row


def redirect_back(endpoint: str, *, msg: str | None = None, err: str | None = None, **kwargs):
    target = url_for(endpoint, **kwargs)
    if msg or err:
        target += "?" + urlencode({"msg" if msg else "err": msg or err})
    return redirect(target)


@app.route("/")
@app.route("/tasks")
def task_list():
    status = request.args.get("status", "")
    project = request.args.get("project_name", "")
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
    tasks = query_all(
        f"""SELECT t.*, COUNT(i.id) AS issue_total,
                   SUM(CASE WHEN i.status NOT IN ('CONFIRMED', 'CANCELLED') THEN 1 ELSE 0 END) AS open_issue_total,
                   SUM(CASE WHEN i.severity = 'critical' AND i.status NOT IN ('CONFIRMED', 'CANCELLED') THEN 1 ELSE 0 END) AS critical_total,
                   SUM(CASE WHEN i.severity = 'high' AND i.status NOT IN ('CONFIRMED', 'CANCELLED') THEN 1 ELSE 0 END) AS high_total
            FROM review_task t LEFT JOIN review_issue i ON i.task_id = t.id
            {' '.join(filters)}
            GROUP BY t.id
            ORDER BY CASE t.status WHEN 'IN_PROGRESS' THEN 1 WHEN 'PENDING' THEN 2 ELSE 3 END,
                     t.updated_at DESC, t.id DESC""",
        params,
    )
    return render_template("tasks_list.html", tasks=tasks, statuses=TASK_STATUSES,
                           filters={"status": status, "project_name": project, "include_closed": include_closed})


@app.get("/healthz")
def healthcheck():
    return {"status": "ok"}


@app.route("/tasks/<task_key>")
def task_detail(task_key: str):
    task = query_one("SELECT * FROM review_task WHERE task_key = ?", (task_key,))
    if not task:
        abort(404)
    issue_status = request.args.get("issue_status", "")
    severity = request.args.get("severity", "")
    filters, params = ["WHERE i.task_id = ?"], [task["id"]]
    if issue_status:
        filters.append("AND i.status = ?")
        params.append(issue_status)
    if severity:
        filters.append("AND i.severity = ?")
        params.append(severity)
    issues = [issue_with_json(row) for row in query_all(
        f"""SELECT i.* FROM review_issue i {' '.join(filters)}
            ORDER BY CASE i.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     i.updated_at DESC, i.issue_key ASC""", params,
    )]
    versions = query_all("SELECT * FROM review_task_version WHERE task_id = ? ORDER BY version_no DESC", (task["id"],))
    summary = {
        "total": len(issues),
        "open": sum(issue["status"] not in {"CONFIRMED", "CANCELLED"} for issue in issues),
        "critical": sum(issue["severity"] == "critical" and issue["status"] not in {"CONFIRMED", "CANCELLED"} for issue in issues),
        "high": sum(issue["severity"] == "high" and issue["status"] not in {"CONFIRMED", "CANCELLED"} for issue in issues),
    }
    return render_template("task_detail.html", task=task, issues=issues, versions=versions, summary=summary,
                           statuses=TASK_STATUSES, issue_statuses=ISSUE_STATUSES, severities=SEVERITIES,
                           filters={"issue_status": issue_status, "severity": severity})


@app.route("/tasks/<task_key>/edit", methods=["POST"])
def task_update(task_key: str):
    args = ["--task-key", task_key]
    for field in ("title", "objective", "remark", "close_reason"):
        value = request.form.get(field)
        if value is not None:
            args.extend([f"--{field.replace('_', '-')}", value])
    try:
        run_human_command("task-update", *args)
        return redirect_back("task_detail", task_key=task_key, msg="任务信息已保存")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return redirect_back("task_detail", task_key=task_key, err=str(exc))


@app.route("/tasks/<task_key>/status", methods=["POST"])
def task_update_status(task_key: str):
    args = ["--task-key", task_key, "--status", request.form.get("status", "")]
    for field in ("remark", "close_reason"):
        if value := request.form.get(field):
            args.extend([f"--{field.replace('_', '-')}", value])
    try:
        run_human_command("task-update-status", *args)
        return redirect_back("task_detail", task_key=task_key, msg="任务状态已更新")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("task_detail", task_key=task_key, err=str(exc))


@app.route("/issues/<issue_key>")
def issue_detail(issue_key: str):
    issue = query_one(
        """SELECT i.*, t.task_key, t.project_name FROM review_issue i
           JOIN review_task t ON t.id = i.task_id WHERE i.issue_key = ?""", (issue_key,)
    )
    if not issue:
        abort(404)
    issue_with_json(issue)
    activities = [activity_with_json(row) for row in query_all(
        "SELECT * FROM issue_activity WHERE issue_id = ? ORDER BY created_at DESC, id DESC", (issue["id"],)
    )]
    return render_template("issue_detail.html", issue=issue, activities=activities, dimensions=DIMENSIONS,
                           severities=SEVERITIES, benefits=BENEFITS, costs=COSTS, confidence=CONFIDENCE,
                           dispositions=DISPOSITIONS, activity_types=ACTIVITY_TYPES,
                           issue_statuses=ISSUE_STATUSES)


@app.route("/issues/<issue_key>/assessment", methods=["POST"])
def issue_update_assessment(issue_key: str):
    args = ["--issue-key", issue_key]
    for field in ("dimension", "severity", "remediation_benefit", "remediation_cost", "disposition", "confidence"):
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
    for field in ("title", "description", "facts", "rationale"):
        if value := request.form.get(field):
            args.extend([f"--{field.replace('_', '-')}", value])
    try:
        run_human_command("issue-update-body", *args)
        return redirect_back("issue_detail", issue_key=issue_key, msg="问题内容已保存")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/status", methods=["POST"])
def issue_update_status(issue_key: str):
    try:
        run_human_command("issue-update-status", "--issue-key", issue_key, "--status", request.form.get("status", ""),
                          "--content", request.form.get("content", ""))
        return redirect_back("issue_detail", issue_key=issue_key, msg="问题状态已更新")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues/<issue_key>/activities", methods=["POST"])
def issue_add_activity(issue_key: str):
    try:
        run_human_command("activity-append", "--issue-key", issue_key,
                          "--activity-type", request.form.get("activity_type", "COMMENT_ADDED"),
                          "--content", request.form.get("content", ""))
        return redirect_back("issue_detail", issue_key=issue_key, msg="活动记录已追加")
    except Exception as exc:  # noqa: BLE001
        return redirect_back("issue_detail", issue_key=issue_key, err=str(exc))


@app.route("/issues")
@app.route("/audit")
def legacy_redirect():
    return redirect(url_for("task_list"))


@app.context_processor
def inject_globals():
    return {
        "flash_msg": request.args.get("msg", "") or request.args.get("err", ""),
        "flash_kind": "err" if request.args.get("err") else ("ok" if request.args.get("msg") else ""),
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("WEBTOOL_PORT", "5050")),
            debug=bool(os.environ.get("WEBTOOL_DEBUG")))
