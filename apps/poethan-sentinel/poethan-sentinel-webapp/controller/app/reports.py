from __future__ import annotations

import html
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator

from .models import DiagnosticReport, Finding, PluginPackage, ServerProfile


TITLE_MAP = {
    "DORIS-001": "Doris 服务未运行",
    "DORIS-002": "Doris 进程未由 systemd 正常托管",
    "HOST-001": "系统负载超过配置阈值",
    "HOST-002": "可用内存低于配置阈值",
    "NETWORK-001": "网络带宽超过配置阈值",
}


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
        evidence = ", ".join(f"{key}={value}" for key, value in values.items() if key not in {"status", "check_id"})
        findings.append(Finding(severity="warning", title=TITLE_MAP.get(check_id, f"检查 {check_id} 未通过"), evidence=evidence or block.strip(), recommendation="结合原始输出确认影响范围和处理窗口。"))
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
    )


def report_html(report: DiagnosticReport) -> str:
    findings = "".join(
        f"<article class='finding {item.severity}'><h2>{html.escape(item.title)}</h2><p>{html.escape(item.evidence)}</p><aside>{html.escape(item.recommendation)}</aside></article>"
        for item in report.findings
    )
    ai = ""
    if report.ai:
        ai = f"<section><h2>AI 分析</h2><pre>{html.escape(str(report.ai.get('content', '')))}</pre></section>"
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\"><title>{html.escape(report.plugin['name'])}</title><style>body{{font:14px -apple-system;margin:0;background:#f2f4f7;color:#18202b}}main{{max-width:900px;margin:40px auto;padding:0 20px}}header,.finding,section{{background:#fff;border:1px solid #dce2e9;border-radius:12px;padding:20px;margin-bottom:14px}}h1{{margin:5px 0}}p,aside{{line-height:1.6}}pre{{white-space:pre-wrap;font:12px SFMono-Regular,monospace}}.warning{{border-left:4px solid #c87d1e}}.critical{{border-left:4px solid #d34d57}}.success{{border-left:4px solid #2e9b66}}</style></head><body><main><header><small>Poethan Sentinel · {report.created_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}</small><h1>{html.escape(report.plugin['name'])}</h1><p>{html.escape(report.server['name'])} · {html.escape(report.summary)}</p></header>{findings}{ai}<section><h2>原始输出</h2><pre>{html.escape(report.raw_output)}</pre></section></main></body></html>"""


def plugin_report_html(report: DiagnosticReport, plugin: PluginPackage | None) -> tuple[str, bool]:
    """Render the plugin template after validating the app-owned report projection."""
    if not plugin or not plugin.report:
        return report_html(report), False
    root = Path(plugin.directory)
    schema = json.loads((root / plugin.report["schema"]).read_text(encoding="utf-8"))
    payload = {
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
    if "__REPORT_JSON__" not in template or "__REPORT_SCHEMA__" not in template:
        raise ValueError("插件 HTML 模板缺少 __REPORT_JSON__ 或 __REPORT_SCHEMA__ 占位符")
    return template.replace("__REPORT_JSON__", safe_payload).replace("__REPORT_SCHEMA__", safe_schema), True


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
