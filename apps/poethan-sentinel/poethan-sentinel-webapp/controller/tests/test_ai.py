import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ai import (
    AI_CASE_ABNORMAL,
    AI_CASE_INSUFFICIENT,
    AI_CASE_NORMAL,
    AI_FORMAT_JSON,
    AI_FORMAT_MARKDOWN,
    ai_budget,
    ai_case,
    ai_format_for,
    ai_json_to_markdown,
    build_ai_prompt,
    build_ai_result,
    enforce_budget,
    markdown_skeleton,
    normalize_ai_json,
    plugin_problem_analysis,
    should_bypass_proxy,
    visible_length,
)
from app.main import app
from app.models import DiagnosticReport, Finding


def sample_report() -> DiagnosticReport:
    return DiagnosticReport(
        server={"id": "s-1", "name": "demo-doris"},
        plugin={"id": "doris-diagnostic", "name": "Doris 综合诊断", "version": "0.3.0", "mode": "standard"},
        status="completed",
        summary="发现 1 项需要关注的问题。",
        findings=[Finding(severity="warning", title="发现持续热线程", evidence="线程平均 CPU 为 98.4%。", recommendation="结合 perf 栈定位。")],
        raw_output="===== SECTION: HOT_THREADS =====\navg=98.4",
    )


def test_format_depends_on_plugin_report_template() -> None:
    assert ai_format_for(None) == AI_FORMAT_MARKDOWN
    assert ai_format_for(SimpleNamespace(report=None)) == AI_FORMAT_MARKDOWN
    assert ai_format_for(SimpleNamespace(report={"schema": "report/report-schema.json", "template": "report/report-template.html"})) == AI_FORMAT_JSON


def test_json_prompt_demands_the_contract_fields() -> None:
    prompt = build_ai_prompt(sample_report(), AI_FORMAT_JSON)
    for field in ("summary", "rootCause", "confidence", "findings", "actions"):
        assert field in prompt
    assert "只输出一个 JSON 对象" in prompt
    assert "不要输出 Markdown 代码块" in prompt
    assert "demo-doris" in prompt


def test_markdown_prompt_fixes_the_section_structure() -> None:
    """异常档的骨架固定为 结论 / 摘要 / 问题分析，不再有五个泛化章节。"""
    prompt = build_ai_prompt(sample_report(), AI_FORMAT_MARKDOWN)
    assert [line for line in prompt.splitlines() if line.startswith("## ")] == ["## 结论", "## 摘要", "## 问题分析"]
    assert "JSON" not in prompt


def test_normalize_ai_json_accepts_fenced_output_and_drops_extra_fields() -> None:
    content = "```json\n" + json.dumps({
        "summary": "BE 节点存在持续热线程",
        "confidence": "High",
        "unknownField": "忽略我",
        "findings": [
            {"severity": "CRITICAL", "title": "热线程", "evidence": "avg=98.4", "recommendation": "看 perf 栈", "extra": 1},
            {"severity": "乱填", "title": "", "evidence": "x"},
        ],
        "actions": [{"priority": "2", "action": "扩容 BE", "risk": "medium"}, {"action": "复查日志"}],
    }) + "\n```"
    data = normalize_ai_json(content)
    assert data["summary"] == "BE 节点存在持续热线程"
    assert data["confidence"] == "high"
    assert "unknownField" not in data
    assert [item["severity"] for item in data["findings"]] == ["critical", "info"]
    assert data["findings"][1]["title"] == "AI 分析"
    assert "extra" not in data["findings"][0]
    assert data["actions"][0] == {"priority": 2, "action": "扩容 BE", "risk": "medium"}
    assert data["actions"][1] == {"priority": 2, "action": "复查日志"}


def test_normalize_ai_json_falls_back_to_deterministic_summary() -> None:
    data = normalize_ai_json('{"findings": []}', fallback_summary="插件返回失败。")
    assert data["summary"] == "插件返回失败。"


def test_normalize_ai_json_rejects_non_json_output() -> None:
    with pytest.raises(ValueError):
        normalize_ai_json("服务器一切正常，无需处理。")


def test_json_result_keeps_data_for_the_template_page() -> None:
    result = build_ai_result(AI_FORMAT_JSON, '{"summary": "正常", "findings": []}', "{}", "兜底结论")
    assert result["format"] == AI_FORMAT_JSON
    assert result["data"] == {"summary": "正常", "findings": []}
    assert result["degraded"] is False
    assert "<h2>" in result["html"] and "正常" in result["html"]


def test_json_result_degrades_to_markdown_when_model_ignores_the_contract() -> None:
    result = build_ai_result(AI_FORMAT_JSON, "## 结论\n\n一切正常。", "{}", "兜底结论")
    assert result["format"] == AI_FORMAT_MARKDOWN
    assert result["data"] is None
    assert result["degraded"] is True
    assert result["content"] == "## 结论\n\n一切正常。"
    assert "<h2>结论</h2>" in result["html"]


def test_markdown_result_renders_the_report_page_html() -> None:
    result = build_ai_result(AI_FORMAT_MARKDOWN, "## 结论\n\n- 正常\n", "{}")
    assert result["format"] == AI_FORMAT_MARKDOWN
    assert result["data"] is None
    assert "<li>正常</li>" in result["html"]


def test_ai_json_to_markdown_summarizes_structured_result() -> None:
    source = ai_json_to_markdown({
        "summary": "热线程明显",
        "rootCause": "查询并发过高",
        "confidence": "medium",
        "findings": [{"severity": "warning", "title": "热线程", "evidence": "avg=98.4", "recommendation": "限流"}],
        "actions": [{"priority": 1, "action": "限流", "risk": "low"}],
    })
    assert "## 结论" in source
    assert "查询并发过高（置信度：中）" in source
    assert "- **热线程**（警告）：avg=98.4 → 建议：限流" in source
    assert "1. 限流 · 风险 低" in source


def report_for_case(case: str) -> DiagnosticReport:
    base = {
        "server": {"id": "s-1", "name": "demo-net"},
        "plugin": {"id": "network-diagnostic", "name": "网络占用", "version": "1.0.0", "mode": "standard"},
        "status": "completed",
        "summary": "本次诊断未发现确定性异常。",
        "raw_output": "===== SECTION: NETWORK =====\npeak_mbps=0.506\nthreshold_mbps=100.0",
    }
    healthy = Finding(severity="success", title="本次检查未发现确定性异常", evidence="NETWORK-001 passed", recommendation="")
    if case == AI_CASE_ABNORMAL:
        return DiagnosticReport(**base, findings=[Finding(severity="warning", title="网络带宽超过配置阈值", evidence="peak=120 超过阈值 100", recommendation="扩容")])
    if case == AI_CASE_INSUFFICIENT:
        return DiagnosticReport(**{**base, "raw_output": "   "}, findings=[healthy])
    return DiagnosticReport(**base, findings=[healthy])


def test_ai_case_classifies_the_three_situations() -> None:
    assert ai_case(report_for_case(AI_CASE_NORMAL)) == AI_CASE_NORMAL
    assert ai_case(report_for_case(AI_CASE_ABNORMAL)) == AI_CASE_ABNORMAL
    assert ai_case(report_for_case(AI_CASE_INSUFFICIENT)) == AI_CASE_INSUFFICIENT
    # 异常退出但没有解析出任何断言时，属于证据不足而不是"正常"。
    failed = report_for_case(AI_CASE_NORMAL)
    failed.status = "failed"
    failed.raw_output = ""
    assert ai_case(failed) == AI_CASE_INSUFFICIENT


def test_normal_case_prompt_forbids_root_cause_and_advice() -> None:
    prompt = build_ai_prompt(report_for_case(AI_CASE_NORMAL), AI_FORMAT_MARKDOWN)
    assert "## 结论" in prompt and "## 摘要" in prompt
    assert "## 问题分析" not in prompt
    assert "不要写根因、建议、风险" in prompt
    assert "200 字以内" in prompt


def test_abnormal_case_prompt_includes_problem_analysis_only_when_requested() -> None:
    report = report_for_case(AI_CASE_ABNORMAL)
    with_analysis = build_ai_prompt(report, AI_FORMAT_MARKDOWN, AI_CASE_ABNORMAL, True)
    assert "## 问题分析" in with_analysis
    assert "最多 3 条" in with_analysis
    assert "600 字以内" in with_analysis

    without = build_ai_prompt(report, AI_FORMAT_MARKDOWN, AI_CASE_ABNORMAL, False)
    assert "## 问题分析" not in without
    assert "该插件未要求问题分析" in without
    assert "250 字以内" in without


def test_insufficient_case_prompt_asks_for_the_missing_evidence() -> None:
    prompt = build_ai_prompt(report_for_case(AI_CASE_INSUFFICIENT), AI_FORMAT_MARKDOWN)
    assert "证据不足" in prompt
    assert "不要猜测根因" in prompt
    assert "250 字以内" in prompt


def test_plugin_problem_analysis_defaults_to_true_and_can_be_disabled() -> None:
    assert plugin_problem_analysis(None) is True
    assert plugin_problem_analysis(SimpleNamespace(ai={})) is True
    assert plugin_problem_analysis(SimpleNamespace(ai={"problemAnalysis": True})) is True
    assert plugin_problem_analysis(SimpleNamespace(ai={"problemAnalysis": False})) is False


def test_budget_truncates_on_line_boundaries_and_marks_it() -> None:
    over = "## 结论\n\n正常。\n\n" + "\n".join(f"- 第 {index} 条啰嗦内容" for index in range(60))
    assert visible_length(over) > 200
    trimmed, truncated = enforce_budget(over, 200)
    assert truncated is True
    assert trimmed.endswith("*（内容超出 200 字预算，已截断）*")
    assert "第 0 条啰嗦内容" in trimmed
    assert "第 59 条啰嗦内容" not in trimmed

    short = "## 结论\n\n正常。"
    assert enforce_budget(short, 200) == (short, False)


def test_markdown_result_applies_the_case_budget() -> None:
    long_body = "## 结论\n\n" + "\n".join(f"- 事实 {index}" for index in range(200))
    result = build_ai_result(AI_FORMAT_MARKDOWN, long_body, "{}", case=AI_CASE_NORMAL)
    assert result["truncated"] is True
    assert visible_length(result["content"]) < 200 + 30
    assert "已截断" in result["content"]
    # 不传 case 表示调用方自带长度控制，不做截断。
    assert build_ai_result(AI_FORMAT_MARKDOWN, long_body, "{}")["truncated"] is False


def test_normal_case_budget_is_far_below_the_old_output_size() -> None:
    """真实报告里正常档能写 3,600 字符；预算必须把它压到 200 字。"""
    assert ai_budget(AI_CASE_NORMAL) == 200
    assert markdown_skeleton(AI_CASE_NORMAL, True, 200).count("##") == 2


def test_domestic_providers_and_private_addresses_bypass_proxy() -> None:
    assert should_bypass_proxy("https://api.deepseek.com/chat/completions")
    assert should_bypass_proxy("https://api.moonshot.cn/v1/chat/completions")
    assert should_bypass_proxy("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
    assert should_bypass_proxy("https://ark.cn-beijing.volces.com/api/v3/chat/completions")
    assert should_bypass_proxy("https://gateway.api.deepseek.com/v1/chat/completions")
    assert should_bypass_proxy("http://127.0.0.1:11434/v1/chat/completions")
    assert should_bypass_proxy("http://192.168.1.10:8000/v1/chat/completions")
    assert should_bypass_proxy("http://10.0.0.5:9000/v1/chat/completions")


def test_foreign_endpoints_stay_on_system_proxy() -> None:
    assert not should_bypass_proxy("https://api.openai.com/v1/chat/completions")
    assert not should_bypass_proxy("https://ai.internal.corp:8000/v1/chat/completions")


def test_ai_test_endpoint_reports_failures_without_500(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_ai(profile, key):
        raise ValueError("网络请求失败：连接被拒绝")

    monkeypatch.setattr("app.main.test_ai", failing_ai)
    with TestClient(app) as web:
        assert web.get("/api/v1/bootstrap").status_code == 200
        response = web.post(
            "/api/v1/ai/test",
            headers={"X-Poethan-Request": "1"},
            json={"endpoint": "https://api.deepseek.com", "model": "deepseek-flash", "apiKey": "sk-test"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "网络请求失败" in body["message"]


def test_ai_test_endpoint_without_key_reports_reason() -> None:
    with TestClient(app) as web:
        assert web.get("/api/v1/bootstrap").status_code == 200
        response = web.post(
            "/api/v1/ai/test",
            headers={"X-Poethan-Request": "1"},
            json={"endpoint": "https://api.deepseek.com", "model": "deepseek-flash"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "API Key" in body["message"]


def test_ai_profiles_crud_keeps_keys_in_sync() -> None:
    with TestClient(app) as web:
        assert web.get("/api/v1/bootstrap").status_code == 200
        profiles = [
            {"id": "p-deepseek", "name": "DeepSeek 生产", "endpoint": "https://api.deepseek.com", "model": "deepseek-flash"},
            {"id": "p-kimi", "name": "Kimi 备用", "endpoint": "https://api.moonshot.cn/v1", "model": "kimi-k3"},
        ]
        response = web.put(
            "/api/v1/ai/profiles",
            headers={"X-Poethan-Request": "1"},
            json={"profiles": profiles, "activeAiId": "p-kimi", "apiKeys": {"p-deepseek": "sk-deepseek", "p-kimi": "sk-kimi"}},
        )
        assert response.status_code == 200
        saved = response.json()
        assert [item["id"] for item in saved["profiles"]] == ["p-deepseek", "p-kimi"]
        assert saved["activeAiId"] == "p-kimi"
        assert saved["aiConfigured"] == {"p-deepseek": True, "p-kimi": True}

        settings = web.get("/api/v1/settings").json()
        assert settings["activeAiId"] == "p-kimi"
        assert len(settings["aiProfiles"]) == 2

        # 删除一个配置后，它的 Key 也要被清理。
        response = web.put(
            "/api/v1/ai/profiles",
            headers={"X-Poethan-Request": "1"},
            json={"profiles": [profiles[1]], "activeAiId": "p-kimi", "apiKeys": {}},
        )
        assert response.status_code == 200
        trimmed = response.json()
        assert [item["id"] for item in trimmed["profiles"]] == ["p-kimi"]
        assert trimmed["aiConfigured"] == {"p-kimi": True}


def test_ai_profiles_unknown_active_falls_back_to_first() -> None:
    with TestClient(app) as web:
        assert web.get("/api/v1/bootstrap").status_code == 200
        response = web.put(
            "/api/v1/ai/profiles",
            headers={"X-Poethan-Request": "1"},
            json={
                "profiles": [{"id": "only", "name": "唯一配置", "endpoint": "https://api.deepseek.com", "model": "deepseek-flash"}],
                "activeAiId": "missing",
            },
        )
        assert response.status_code == 200
        assert response.json()["activeAiId"] == "only"
