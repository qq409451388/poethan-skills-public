import json
from pathlib import Path

from app import config
from app.models import DiagnosticReport, Finding, PluginPackage, PluginTrust, ServerProfile
from app.reports import (
    AI_RENDER_JSON,
    AI_RENDER_MARKDOWN,
    AI_RENDER_NONE,
    ai_render_mode,
    build_report,
    extract_report_data,
    hydrate_ai_html,
    parse_findings,
    plugin_report_html,
    report_html,
    report_uses_template,
)
from app.storage import store


def sample_report(ai: dict | None = None) -> DiagnosticReport:
    return DiagnosticReport(
        server={"id": "s-1", "name": "demo-doris"},
        plugin={"id": "doris-diagnostic", "name": "Doris 综合诊断", "version": "0.3.0", "mode": "standard"},
        status="completed",
        summary="发现 1 项需要关注的问题。",
        findings=[Finding(severity="warning", title="发现持续热线程", evidence="线程平均 CPU 为 98.4%。", recommendation="结合 perf 栈定位。")],
        raw_output="===== SECTION: HOT_THREADS =====\navg=98.4",
        ai=ai,
    )


def sample_plugin(directory: Path, template: str, schema: dict | None = None) -> PluginPackage:
    (directory / "report").mkdir(parents=True, exist_ok=True)
    (directory / "report" / "report-schema.json").write_text(json.dumps(schema or {
        "type": "object",
        "required": ["server", "summary"],
        "properties": {"server": {"type": "string"}, "summary": {"type": "string"}},
    }), encoding="utf-8")
    (directory / "report" / "report-template.html").write_text(template, encoding="utf-8")
    return PluginPackage(
        id="doris-diagnostic", name="Doris 综合诊断", version="0.3.0", entrypoint="run.sh",
        default_mode="standard", modes=[{"id": "standard"}], fields=[], directory=str(directory),
        report={"schema": "report/report-schema.json", "template": "report/report-template.html"},
        trust=PluginTrust(status="trusted", message="签名有效"),
    )


def test_markdown_ai_result_is_rendered_into_the_default_page() -> None:
    html = report_html(sample_report({
        "status": "completed", "format": "markdown", "content": "## 结论\n\n- 正常",
        "html": "<h2>结论</h2><ul><li>正常</li></ul>", "data": None, "degraded": False,
    }))
    assert "<h2>结论</h2>" in html
    assert "<li>正常</li>" in html
    assert "<pre>## 结论" not in html


def test_legacy_ai_result_is_markdown_rendered_on_read() -> None:
    """改动前保存的报告只有 content；读取时按 Markdown 补渲染，不必重跑诊断。"""
    report = sample_report({"status": "completed", "content": "## 结论\n\n- 正常", "rawResponse": "{}"})
    html = report_html(report)
    assert "<h2>结论</h2>" in html
    assert "<li>正常</li>" in html
    assert "<pre>## 结论" not in html
    # 兼容只是补展示字段，不改写原始 content。
    assert report.ai["content"] == "## 结论\n\n- 正常"
    assert report.ai["format"] == "markdown"


def test_hydrate_ai_html_is_idempotent_and_skips_unusable_results() -> None:
    report = sample_report({"status": "completed", "content": "**粗体**"})
    hydrate_ai_html(report)
    first = report.ai["html"]
    hydrate_ai_html(report)
    assert report.ai["html"] == first == "<p><strong>粗体</strong></p>"
    assert hydrate_ai_html(sample_report({"status": "failed", "error": "超时"})).ai.get("html") is None
    assert hydrate_ai_html(sample_report(None)).ai is None


def test_hydrate_ai_html_re_renders_so_renderer_fixes_reach_history() -> None:
    """历史报告里存的是旧渲染器的 html；读取时要按当前渲染器重算。"""
    report = sample_report({
        "status": "completed", "format": "markdown", "content": "扩容 /mnt/storage_archive",
        "html": "<p>扩容 /mnt/storage<em>archive</em></p>",   # 旧渲染器把下划线当成了斜体
    })
    hydrate_ai_html(report)
    assert report.ai["html"] == "<p>扩容 /mnt/storage_archive</p>"


def test_stored_legacy_report_is_hydrated_when_read_back() -> None:
    """JSON API 也走同一条路径，前端 AI 页签因此能直接拿到 html。"""
    report = sample_report({"status": "completed", "content": "## 结论\n\n正常"})
    store.save_report(report)
    try:
        assert "<h2>结论</h2>" in store.report(report.id).ai["html"]
        assert "<h2>结论</h2>" in next(item for item in store.reports() if item.id == report.id).ai["html"]
    finally:
        (config.REPORTS_ROOT / f"{report.id}.json").unlink(missing_ok=True)


def test_degraded_ai_result_is_flagged_on_the_page() -> None:
    html = report_html(sample_report({
        "status": "completed", "format": "markdown", "content": "x", "html": "<p>x</p>", "degraded": True,
    }))
    assert "模型未按 JSON 契约返回" in html


def test_template_receives_ai_json_through_optional_placeholder(tmp_path: Path) -> None:
    plugin = sample_plugin(tmp_path, "<html><body><script>const report=__REPORT_JSON__;const schema=__REPORT_SCHEMA__;const ai=__REPORT_AI__;</script></body></html>")
    rendered, uses_template = plugin_report_html(sample_report({
        "status": "completed", "format": "json", "content": "md",
        "html": "<h2>结论</h2>", "data": {"summary": "热线程明显", "findings": []},
    }), plugin)
    assert uses_template is True
    assert '"summary": "热线程明显"' in rendered
    assert "__REPORT_AI__" not in rendered
    # 模板自己渲染 AI 时不再追加默认区块，避免重复。
    assert "poethan-ai-overlay" not in rendered


def test_template_without_ai_placeholder_gets_a_default_ai_overlay(tmp_path: Path) -> None:
    plugin = sample_plugin(tmp_path, "<html><body><main>__REPORT_JSON__ __REPORT_SCHEMA__ 插件正文</main></body></html>")
    rendered, _ = plugin_report_html(sample_report({
        "status": "completed", "format": "markdown", "content": "## 结论", "html": "<h2>结论</h2>", "degraded": False,
    }), plugin)
    assert "poethan-ai-overlay" in rendered
    assert "<h2>结论</h2>" in rendered
    assert rendered.index("poethan-ai-overlay") < rendered.index("</body>")


def test_template_without_ai_result_is_left_untouched(tmp_path: Path) -> None:
    plugin = sample_plugin(tmp_path, "<html><body><main>__REPORT_JSON__ __REPORT_SCHEMA__ 插件正文</main></body></html>")
    rendered, _ = plugin_report_html(sample_report(), plugin)
    assert "poethan-ai-overlay" not in rendered
    assert rendered.endswith("</body></html>")


def test_template_gets_null_ai_when_analysis_is_markdown(tmp_path: Path) -> None:
    plugin = sample_plugin(tmp_path, "<script>const ai=__REPORT_AI__;</script><b>__REPORT_JSON__</b><i>__REPORT_SCHEMA__</i>")
    rendered, _ = plugin_report_html(sample_report({"status": "completed", "format": "markdown", "content": "md", "html": "<p>md</p>"}), plugin)
    assert "const ai=null;" in rendered


def test_plugin_without_template_uses_the_default_page(tmp_path: Path) -> None:
    plugin = sample_plugin(tmp_path, "<b>__REPORT_JSON__</b>")
    plugin.report = None
    rendered, uses_template = plugin_report_html(sample_report(), plugin)
    assert uses_template is False
    assert "<!doctype html>" in rendered


def test_build_report_records_whether_a_template_was_used(tmp_path: Path) -> None:
    with_template = sample_plugin(tmp_path / "a", "<b>__REPORT_JSON__</b>")
    (tmp_path / "a").mkdir(parents=True, exist_ok=True)
    assert build_report(ServerProfile(name="demo"), with_template, "standard", "status=passed", 0, 1.0, {}).report_template is True
    without_template = sample_plugin(tmp_path / "b", "<b>__REPORT_JSON__</b>")
    without_template.report = None
    assert build_report(ServerProfile(name="demo"), without_template, "standard", "status=passed", 0, 1.0, {}).report_template is False


def test_ai_render_mode_follows_the_stored_result_type() -> None:
    assert ai_render_mode(sample_report(None)) == AI_RENDER_NONE
    assert ai_render_mode(sample_report({"status": "failed", "error": "超时"})) == AI_RENDER_NONE
    assert ai_render_mode(sample_report({"status": "completed", "format": "markdown", "content": "x"})) == AI_RENDER_MARKDOWN
    # 改动前的老报告：没有 format，只有 content，一样按 Markdown 处理。
    assert ai_render_mode(sample_report({"status": "completed", "content": "x"})) == AI_RENDER_MARKDOWN
    assert ai_render_mode(sample_report({"status": "completed", "format": "json", "content": "x", "data": {"summary": "s", "findings": []}})) == AI_RENDER_JSON
    # 声称是 json 却没有结构化数据，只能退回 Markdown。
    assert ai_render_mode(sample_report({"status": "completed", "format": "json", "content": "x", "data": None})) == AI_RENDER_MARKDOWN


def test_history_keeps_builtin_page_when_plugin_later_gains_a_template(tmp_path: Path) -> None:
    """报告是历史的：插件后加了模板，也不该把一个 Markdown 老报告塞进新模板。"""
    plugin = sample_plugin(tmp_path, "<html><body><b>__REPORT_JSON__</b> __REPORT_SCHEMA__</body></html>")
    report = sample_report({"status": "completed", "content": "## 结论\n\n正常"})
    report.report_template = False
    rendered, uses_template = plugin_report_html(report, plugin)
    assert uses_template is False
    assert rendered.startswith("<!doctype html>")
    assert "<h2>结论</h2>" in rendered


def test_history_keeps_template_when_report_recorded_one(tmp_path: Path) -> None:
    plugin = sample_plugin(tmp_path, "<html><body><b>__REPORT_JSON__</b> __REPORT_SCHEMA__</body></html>")
    report = sample_report({
        "status": "completed", "format": "json", "content": "md",
        "html": "<h2>结论</h2>", "data": {"summary": "热线程明显", "findings": []},
    })
    report.report_template = True
    rendered, uses_template = plugin_report_html(report, plugin)
    assert uses_template is True
    assert "poethan-ai-overlay" in rendered


def test_json_report_falls_back_to_markdown_when_template_is_gone(tmp_path: Path) -> None:
    """模板被删掉时，结构化结果仍以 Markdown 形式留在内置页面上，不丢内容。"""
    plugin = sample_plugin(tmp_path, "<b>__REPORT_JSON__</b>")
    plugin.report = None
    report = sample_report({
        "status": "completed", "format": "json", "content": "## 结论\n\n热线程明显",
        "html": "<h2>结论</h2><p>热线程明显</p>", "data": {"summary": "热线程明显", "findings": []},
    })
    report.report_template = True
    rendered, uses_template = plugin_report_html(report, plugin)
    assert uses_template is False
    assert "<h2>结论</h2>" in rendered


def test_legacy_report_without_record_defers_to_current_plugin(tmp_path: Path) -> None:
    plugin = sample_plugin(tmp_path, "<b>__REPORT_JSON__</b> __REPORT_SCHEMA__")
    legacy = sample_report({"status": "completed", "content": "x"})
    assert legacy.report_template is None
    assert report_uses_template(legacy, plugin) is True
    assert report_uses_template(legacy, None) is False


def test_plugin_supplied_report_data_overrides_the_default_projection(tmp_path: Path) -> None:
    """插件用 REPORT_DATA 自带结构时，模板拿到的就是它自己的形状。"""
    plugin = sample_plugin(
        tmp_path,
        "<html><body><script>const report=__REPORT_JSON__;</script></body></html>",
        schema={
            "type": "object",
            "required": ["h", "d", "dirs", "files"],
            "properties": {
                "h": {"type": "string"},
                "d": {"type": "array"},
                "dirs": {"type": "array"},
                "files": {"type": "array"},
            },
        },
    )
    report = sample_report({
        "status": "completed", "format": "markdown", "content": "md", "html": "<p>md</p>",
    })
    report.raw_output = (
        "===== SECTION: CHECKS =====\ncheck_id=DISK-001\nstatus=passed\n\n"
        '===== SECTION: REPORT_DATA =====\n{"h": "prod-1", "d": [["/", 100, 60, "ext4"]], "dirs": [["/var", 40]], "files": []}\n'
    )
    rendered, uses_template = plugin_report_html(report, plugin)
    assert uses_template is True
    assert '"h": "prod-1"' in rendered
    assert '"/"' in rendered
    # 默认投影里的字段不该再出现。
    assert "schemaVersion" not in rendered


def test_template_without_schema_placeholder_is_accepted(tmp_path: Path) -> None:
    """只需要数据的模板不必声明 __REPORT_SCHEMA__。"""
    plugin = sample_plugin(tmp_path, "<html><body><script>const report=__REPORT_JSON__;</script></body></html>")
    report = sample_report({
        "status": "completed", "format": "markdown", "content": "md", "html": "<p>md</p>",
    })
    report.raw_output = '===== SECTION: REPORT_DATA =====\n{"server": "x", "summary": "y"}\n'
    rendered, uses_template = plugin_report_html(report, plugin)
    assert uses_template is True
    assert "__REPORT_SCHEMA__" not in rendered
    assert '"summary": "y"' in rendered


def test_invalid_report_data_falls_back_to_the_default_projection(tmp_path: Path) -> None:
    """REPORT_DATA 不是合法 JSON 时退回默认投影，而不是让报告页直接打不开。"""
    plugin = sample_plugin(tmp_path, "<html><body><script>const report=__REPORT_JSON__;</script></body></html>")
    report = sample_report({
        "status": "completed", "format": "markdown", "content": "md", "html": "<p>md</p>",
    })
    report.raw_output = "===== SECTION: REPORT_DATA =====\n{不是 JSON\n"
    rendered, _ = plugin_report_html(report, plugin)
    assert '"server": "demo-doris"' in rendered
    assert "schemaVersion" in rendered


def test_extract_report_data_handles_missing_and_multiline_payloads() -> None:
    assert extract_report_data("===== SECTION: HOST =====\nhostname=x\n") is None
    assert extract_report_data("===== SECTION: REPORT_DATA =====\n\n") is None
    assert extract_report_data('===== SECTION: REPORT_DATA =====\nnot json\n') is None
    assert extract_report_data('===== SECTION: REPORT_DATA =====\n[1, 2]\n') is None
    single = extract_report_data('===== SECTION: REPORT_DATA =====\n{"a": 1}\n\n===== SECTION: X =====\ny=1\n')
    assert single == {"a": 1}
    pretty = extract_report_data('===== SECTION: REPORT_DATA =====\n{\n  "a": 1\n}\n')
    assert pretty == {"a": 1}


def test_parses_check_and_hot_thread_findings() -> None:
    output = """check_id=HOST-001
status=failed
value=2.0
threshold=1.0

===== SECTION: HOT_THREADS =====
cpu_avg=98.4
"""
    findings = parse_findings(output, 0)
    assert any(item.title == "系统负载超过配置阈值" for item in findings)
    assert any(item.title == "发现持续热线程" for item in findings)


def test_success_when_no_rule_fires() -> None:
    findings = parse_findings("status=passed", 0)
    assert len(findings) == 1
    assert findings[0].severity == "success"
