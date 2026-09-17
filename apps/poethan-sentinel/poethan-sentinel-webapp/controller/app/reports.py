from __future__ import annotations

import html
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator

from . import markdown
from .models import DiagnosticReport, Finding, PluginPackage, ServerProfile


TITLE_MAP = {
    "DORIS-001": "Doris 服务未运行",
    "DORIS-002": "Doris 进程未由 systemd 正常托管",
    "HOST-001": "系统负载超过配置阈值",
    "HOST-002": "可用内存低于配置阈值",
    "NETWORK-001": "网络带宽超过配置阈值",
    "DISK-001": "文件系统使用率超过阈值",
    "DISK-002": "inode 使用率超过阈值",
    "DISK-003": "发现超大文件",
}
# 插件可以在检查块里用 severity= 表达严重程度；没写时按警告处理。
FINDING_SEVERITIES = {"critical", "warning", "info", "success"}


def parse_findings(output: str, exit_code: int) -> list[Finding]:
    findings: list[Finding] = []
    blocks = re.split(r"\n\s*\n", output)
    for block in blocks:
        values: dict[str, str] = {}
        for line in block.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if key.strip() and " " not in key.strip():
                    values[key.strip()] = value.strip()
        if values.get("status") != "failed":
            continue
        check_id = values.get("check_id", "CHECK")
        severity = values.get("severity", "warning").lower()
        evidence = ", ".join(f"{key}={value}" for key, value in values.items() if key not in {"status", "check_id", "severity"})
        findings.append(Finding(
            severity=severity if severity in FINDING_SEVERITIES else "warning",
            title=TITLE_MAP.get(check_id, f"检查 {check_id} 未通过"),
            evidence=evidence or block.strip(),
            recommendation="结合原始输出确认影响范围和处理窗口。",
        ))
    lowered = output.lower()
    if "job_state=failed" in lowered or '"state": "failed"' in lowered:
        findings.append(Finding(severity="warning", title="Flink 作业处于失败状态", evidence="诊断输出检测到 FAILED 作业。", recommendation="确认下游服务稳定后，从最近可用检查点恢复作业。"))
    hot_match = re.search(r"(?:avg|cpu_avg)=([0-9.]+)", output)
    if hot_match and float(hot_match.group(1)) >= 50:
        findings.append(Finding(severity="warning", title="发现持续热线程", evidence=f"线程平均 CPU 为 {hot_match.group(1)}%。", recommendation="结合查询、线程名和 perf 栈确定负载来源。"))
    if exit_code != 0:
        findings.insert(0, Finding(severity="critical", title="诊断插件未正常结束", evidence=f"远程脚本退出码 {exit_code}。", recommendation="检查插件依赖、权限以及原始输出末尾。"))
    if not findings:
        findings.append(Finding(severity="success", title="本次检查未发现确定性异常", evidence="插件正常结束，所有确定性规则均未触发。"))
    return findings


def build_report(server: ServerProfile, plugin: PluginPackage, mode: str, output: str, exit_code: int, duration: float, audit: dict[str, Any]) -> DiagnosticReport:
    findings = parse_findings(output, exit_code)
    problems = [item for item in findings if item.severity in {"critical", "warning"}]
    summary = f"发现 {len(problems)} 项需要关注的问题。" if problems else "本次诊断未发现确定性异常。"
    return DiagnosticReport(
        server={"id": server.id, "name": server.name},
        plugin={"id": plugin.id, "name": plugin.name, "version": plugin.version, "mode": mode},
        status="completed" if exit_code == 0 else "failed", duration_seconds=round(duration, 2),
        summary=summary, findings=findings, raw_output=output, audit=audit,
        # 把当时的呈现方式固化进报告，避免插件以后加/删模板时追溯改写历史报告的显示。
        report_template=bool(plugin.report),
    )


AI_RENDER_NONE = "none"
AI_RENDER_JSON = "json"
AI_RENDER_MARKDOWN = "markdown"

# 插件在这个区段里放一行 JSON，即可自定义报告模板拿到的数据结构。
REPORT_DATA_SECTION = "REPORT_DATA"


def ai_render_mode(report: DiagnosticReport) -> str:
    """AI 内容的渲染方式只由报告里存的结果类型决定，与插件当前配置无关。

    - 存了结构化 JSON（format=json 且有 data）→ JSON，赋值给模板页面；
    - 其余（format=markdown、或改动前只有 content 的老报告）→ Markdown，由页面自己解析；
    - 没有可用的 AI 结果 → 不渲染。
    """
    ai = report.ai
    if not isinstance(ai, dict) or ai.get("status") != "completed":
        return AI_RENDER_NONE
    if ai.get("format") == "json" and isinstance(ai.get("data"), dict):
        return AI_RENDER_JSON
    return AI_RENDER_MARKDOWN


def report_uses_template(report: DiagnosticReport, plugin: PluginPackage | None) -> bool:
    """报告该不该用插件模板页：以报告记录为准，插件当前配置只提供"模板还在不在"。"""
    available = bool(plugin and plugin.report)
    if report.report_template is None:
        # 改动前的老报告没有记录，只能按当前插件尽力推断。
        return available
    return report.report_template and available


def hydrate_ai_html(report: DiagnosticReport) -> DiagnosticReport:
    """按当前渲染器补齐 AI 的展示 HTML。

    Markdown 渲染属于应用的展示逻辑，不是报告数据：每次读取都从 content 重新渲染，
    这样渲染器的修复（例如下划线被误判成斜体）也能作用到历史报告，
    不必让用户重跑诊断。content 缺失时才退回已存的 html。
    """
    ai = report.ai
    if not isinstance(ai, dict) or ai.get("status") != "completed":
        return report
    content = str(ai.get("content") or "")
    if content.strip():
        ai["html"] = markdown.render(content)
        ai.setdefault("format", "markdown")
    return report


def ai_section_html(report: DiagnosticReport, heading: str = "AI 分析") -> str:
    """AI 结果的 HTML 片段；没有可展示的 AI 结果时返回空字符串。"""
    hydrate_ai_html(report)
    if ai_render_mode(report) == AI_RENDER_NONE:
        return ""
    ai = report.ai or {}
    # AI 内容在写入报告时已经渲染成安全 HTML（Markdown 由应用自己解析）；
    # 旧报告缺 html 字段时退回转义后的纯文本。
    rendered = ai.get("html")
    body = rendered if isinstance(rendered, str) and rendered else f"<pre>{html.escape(str(ai.get('content', '')))}</pre>"
    note = "<p class='note'>模型未按 JSON 契约返回，已按 Markdown 展示。</p>" if ai.get("degraded") else ""
    return f"<h2>{html.escape(heading)}</h2>{note}<div class='markdown'>{body}</div>"


AI_OVERLAY_STYLE = (
    ".poethan-ai-overlay{max-width:1060px;box-sizing:border-box;overflow-x:auto;overflow-wrap:anywhere;"
    "margin:18px auto;padding:20px 24px;border:1px solid #d9e2e7;border-radius:14px;"
    "background:#fff;color:#17212b;font:14px -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;line-height:1.7}"
    ".poethan-ai-overlay h2{margin:0 0 10px;font-size:16px;color:#147d91}"
    ".poethan-ai-overlay .markdown h2{color:inherit;font-size:15px}"
    ".poethan-ai-overlay .note{color:#b76d00}"
    ".poethan-ai-overlay code{background:#eef2f6;border-radius:4px;padding:1px 5px;font:12px SFMono-Regular,Menlo,monospace}"
    ".poethan-ai-overlay pre{margin:12px 0;padding:14px;overflow:auto;border-radius:10px;background:#0f1720;color:#dce7ec;white-space:pre-wrap}"
    ".poethan-ai-overlay pre code{background:none;color:inherit;padding:0}"
    ".poethan-ai-overlay ul,.poethan-ai-overlay ol{margin:10px 0;padding-left:22px}"
    ".poethan-ai-overlay table{border-collapse:collapse;width:100%;margin:12px 0;table-layout:fixed}"
    ".poethan-ai-overlay th,.poethan-ai-overlay td{border:1px solid #d9e2e7;padding:7px 10px;text-align:left;overflow-wrap:anywhere}"
    ".poethan-ai-overlay blockquote{margin:12px 0;padding:6px 14px;border-left:4px solid #c8d4de;color:#4b5b6b}"
    "@media(prefers-color-scheme:dark){.poethan-ai-overlay{background:#17212b;color:#e6edf2;border-color:#2b3b47}"
    ".poethan-ai-overlay th,.poethan-ai-overlay td{border-color:#2b3b47}.poethan-ai-overlay code{background:#22303c}}"
)


def ai_overlay_html(report: DiagnosticReport) -> str:
    """给没有消费 __REPORT_AI__ 的插件模板补一个默认 AI 区块，保证 AI 结论在报告页可见。"""
    section = ai_section_html(report)
    if not section:
        return ""
    return f"<style>{AI_OVERLAY_STYLE}</style><section class='poethan-ai-overlay'>{section}</section>"


def _insert_before_body_end(document: str, fragment: str) -> str:
    index = document.lower().rfind("</body>")
    if index == -1:
        return document + fragment
    return document[:index] + fragment + document[index:]


def report_html(report: DiagnosticReport) -> str:
    findings = "".join(
        f"<article class='finding {item.severity}'><h2>{html.escape(item.title)}</h2><p>{html.escape(item.evidence)}</p><aside>{html.escape(item.recommendation)}</aside></article>"
        for item in report.findings
    )
    section = ai_section_html(report)
    ai = f"<section class='ai'>{section}</section>" if section else ""
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\"><title>{html.escape(report.plugin['name'])}</title><style>body{{font:14px -apple-system;margin:0;background:#f2f4f7;color:#18202b}}main{{max-width:900px;margin:40px auto;padding:0 20px}}header,.finding,section{{background:#fff;border:1px solid #dce2e9;border-radius:12px;padding:20px;margin-bottom:14px}}h1{{margin:5px 0}}p,aside{{line-height:1.6}}pre{{white-space:pre-wrap;font:12px SFMono-Regular,monospace}}code{{font:12px SFMono-Regular,monospace;background:#eef2f6;border-radius:4px;padding:1px 4px}}.markdown pre{{background:#0f1720;color:#dce7ec;padding:14px;border-radius:10px;overflow:auto}}.markdown pre code{{background:none;color:inherit;padding:0}}.markdown table{{border-collapse:collapse;width:100%;margin:12px 0}}.markdown th,.markdown td{{border:1px solid #dce2e9;padding:7px 10px;text-align:left}}.markdown blockquote{{margin:12px 0;padding:6px 14px;border-left:4px solid #c8d4de;color:#4b5b6b}}.markdown h2{{font-size:17px;margin:18px 0 8px}}.note{{color:#b76d00}}.warning{{border-left:4px solid #c87d1e}}.critical{{border-left:4px solid #d34d57}}.success{{border-left:4px solid #2e9b66}}</style></head><body><main><header><small>Poethan Sentinel · {report.created_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}</small><h1>{html.escape(report.plugin['name'])}</h1><p>{html.escape(report.server['name'])} · {html.escape(report.summary)}</p></header>{findings}{ai}<section><h2>原始输出</h2><pre>{html.escape(report.raw_output)}</pre></section></main></body></html>"""


def extract_report_data(raw_output: str) -> dict[str, Any] | None:
    """读取插件自带的报告数据。

    插件在自己的事实流里用一个 ``REPORT_DATA`` 区段输出单行 JSON，就能决定
    ``__REPORT_JSON__`` 的结构（磁盘分析这类模板需要按自己的形状拿数据）。
    没有该区段时返回 None，由调用方退回应用统一的报告投影。
    """
    marker = f"===== SECTION: {REPORT_DATA_SECTION} ====="
    if marker not in raw_output:
        return None
    body = raw_output.split(marker, 1)[1].split("===== SECTION:", 1)[0].strip()
    if not body:
        return None
    for candidate in (body, body.splitlines()[0].strip()):
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, IndexError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def plugin_report_html(report: DiagnosticReport, plugin: PluginPackage | None) -> tuple[str, bool]:
    """Render the plugin template after validating the app-owned report projection."""
    # 是否走模板页由报告记录决定，插件现在加没加模板不会改写历史报告的呈现方式。
    if not report_uses_template(report, plugin):
        return report_html(report), False
    assert plugin is not None and plugin.report is not None
    root = Path(plugin.directory)
    schema = json.loads((root / plugin.report["schema"]).read_text(encoding="utf-8"))
    # 插件可以用 REPORT_DATA 区段自带报告数据结构；没有则退回应用统一的报告投影。
    payload = extract_report_data(report.raw_output) or {
        "schemaVersion": "1.0",
        "server": report.server["name"],
        "generatedAt": report.created_at.isoformat(),
        "summary": report.summary,
        "findings": [item.model_dump(mode="json") for item in report.findings],
        "outputs": [{
            "pluginID": report.plugin["id"],
            "pluginName": report.plugin["name"],
            "exitCode": 0 if report.status == "completed" else 1,
            "text": report.raw_output,
            "collectedAt": report.created_at.isoformat(),
        }],
        "enhancedByAI": bool(report.ai and report.ai.get("status") == "completed"),
    }
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        raise ValueError(f"插件报告数据不符合 Schema：{errors[0].message}")
    template = (root / plugin.report["template"]).read_text(encoding="utf-8")
    safe_payload = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    safe_schema = json.dumps(schema, ensure_ascii=False).replace("</", "<\\/")
    if "__REPORT_JSON__" not in template:
        raise ValueError("插件 HTML 模板缺少 __REPORT_JSON__ 占位符")
    rendered = template.replace("__REPORT_JSON__", safe_payload)
    # __REPORT_SCHEMA__ 是可选占位符：模板不需要 Schema 原文时不必声明。
    if "__REPORT_SCHEMA__" in rendered:
        rendered = rendered.replace("__REPORT_SCHEMA__", safe_schema)
    # __REPORT_AI__ 是可选占位符：配置了模板的插件用它接收 AI 结构化 JSON 并赋值给页面。
    if "__REPORT_AI__" in rendered:
        ai_data = report.ai.get("data") if ai_render_mode(report) == AI_RENDER_JSON else None
        rendered = rendered.replace("__REPORT_AI__", json.dumps(ai_data, ensure_ascii=False).replace("</", "<\\/"))
    else:
        # 模板没有消费 __REPORT_AI__（例如已签名的存量插件）时补一个默认 AI 区块，
        # 否则 AI 结论只出现在应用内置页面，插件专属报告页会看不到。
        rendered = _insert_before_body_end(rendered, ai_overlay_html(report))
    return rendered, True


def demo_output(plugin_id: str) -> str:
    if plugin_id == "doris-diagnostic":
        return """===== SECTION: HOST =====
hostname=demo-doris
cpu_cores=8
load1=3.22

===== SECTION: DORIS_PROCESS =====
fe_main_pid=1111845
fe_actual_pids=1111845
fe_managed_by_systemd=true
be_main_pid=1112895
be_actual_pids=1112895
be_managed_by_systemd=true

===== SECTION: HOT_THREADS =====
tid=17797
name=rs_normal
cpu_samples=98.2,99.1,97.8
avg=98.4
persistent_hot=true

===== SECTION: FLINK =====
job_name=trade-event-storage-to-doris
job_state=FAILED
latest_completed_checkpoint=chk-1890"""
    if plugin_id == "disk-usage-diagnostic":
        return """===== SECTION: FILESYSTEMS =====
mount=/data
device=/dev/vdb1
fstype=xfs
size_gb=476.84
used_gb=448.23
avail_gb=28.61
used_percent=94
inodes_used_percent=91

===== SECTION: LARGE_DIRECTORIES =====
path=/data/warehouse
mount=/data
size_gb=318.4

path=/data/logs
mount=/data
size_gb=74.2

===== SECTION: LARGE_FILES =====
path=/data/logs/be.INFO
mount=/data
size_gb=18.6

===== SECTION: DELETED_OPEN_FILES =====
deleted_file_count=1
deleted_total_gb=41.2

pid=24188
process=java
size_mb=42188.0
path=/data/logs/be.out

===== SECTION: CHECKS =====
check_id=DISK-001
mount=/data
severity=critical
status=failed
value=94
threshold=85
critical_threshold=95
avail_gb=28.61

check_id=DISK-002
mount=/data
severity=warning
status=failed
value=91
threshold=85

check_id=DISK-003
severity=warning
status=failed
path=/data/logs/be.INFO
size_gb=18.6
threshold_mb=10240

===== SECTION: REPORT_DATA =====
{"h": "demo-doris", "ts": "2026-09-17 13:10", "d": [["/data", 500277790720, 481036337152, "xfs"], ["/", 99614720000, 66322432000, "ext4"]], "dirs": [["/data/warehouse", 341912666112], ["/data/logs", 79691776000]], "files": [["/data/logs/be.INFO", 19971597926]]}"""
    if plugin_id == "host-performance":
        return """===== SECTION: RESOURCE_FACTS =====
load1=11.36
load1_per_core=1.42
memory_available_percent=42.0

check_id=HOST-001
status=failed
value=1.42
threshold=1.0"""
    return """===== SECTION: NETWORK =====
interface=eth0
peak_mbps=86.8
threshold_mbps=80

check_id=NETWORK-001
status=failed
interface=eth0
value=86.8
threshold=80"""
