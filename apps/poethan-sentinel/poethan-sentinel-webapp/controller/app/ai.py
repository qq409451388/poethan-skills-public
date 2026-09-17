from __future__ import annotations

import ipaddress
import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from jsonschema import Draft202012Validator

from . import config, markdown
from .models import AIProfile, ApplicationSettings, DiagnosticReport, PluginPackage
from .secrets import secrets


LEGACY_AI_KEY_ACCOUNT = "ai:api-key"

# AI 分析有两套输出格式，取决于插件是否自带 HTML 报告模板：
#   json     —— 模板页面用 __REPORT_AI__ 占位符接收结构化数据并赋值渲染；
#   markdown —— 没有模板时由应用内置报告页自己解析 Markdown。
AI_FORMAT_JSON = "json"
AI_FORMAT_MARKDOWN = "markdown"
MAX_AI_OUTPUT_CHARS = 120_000
MAX_AI_FINDINGS = 20
MAX_AI_ACTIONS = 20
SEVERITIES = {"critical", "warning", "info", "success"}
CONFIDENCES = {"high", "medium", "low"}
RISKS = {"low", "medium", "high"}
SEVERITY_LABELS = {"critical": "严重", "warning": "警告", "info": "信息", "success": "正常"}
CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}

_AI_REPORT_SCHEMA = json.loads((config.CONTRACTS_ROOT / "ai-report.schema.json").read_text(encoding="utf-8"))
_AI_REPORT_VALIDATOR = Draft202012Validator(_AI_REPORT_SCHEMA)


def ai_key_account(profile_id: str) -> str:
    return f"ai:{profile_id}:api-key"


def saved_key(profile_id: str) -> str | None:
    """读取配置已保存的 Key；首次访问时把旧版单一 Key 迁移到 default 配置名下。"""
    key = secrets.get(ai_key_account(profile_id))
    if key:
        return key
    legacy = secrets.get(LEGACY_AI_KEY_ACCOUNT)
    if legacy and profile_id == "default":
        secrets.set(ai_key_account(profile_id), legacy)
        return legacy
    return None


def active_ai_profile(settings: ApplicationSettings) -> AIProfile | None:
    return next((profile for profile in settings.ai_profiles if profile.id == settings.active_ai_id), None)


def ai_configured_map(settings: ApplicationSettings) -> dict[str, bool]:
    return {profile.id: saved_key(profile.id) is not None for profile in settings.ai_profiles}

# 国内服务商域名与内网地址绕过系统代理直连，避免本机代理软件（含 SOCKS）干扰；
# 境外端点（如 OpenAI）仍走系统代理，SOCKS 场景依赖已安装的 socksio。
DIRECT_PROXY_HOSTS = (
    "api.deepseek.com",
    "api.moonshot.cn",
    "dashscope.aliyuncs.com",
    "open.bigmodel.cn",
    "ark.cn-beijing.volces.com",
    "api.siliconflow.cn",
    "qianfan.baidubce.com",
    "api.hunyuan.cloud.tencent.com",
    "api.minimax.chat",
)


def should_bypass_proxy(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return True
    if any(host == domain or host.endswith(f".{domain}") for domain in DIRECT_PROXY_HOSTS):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def endpoint_url(endpoint: str) -> str:
    base = endpoint.strip().rstrip("/")
    if base.endswith("/chat/completions") or base.endswith("/responses"):
        return base
    return base + "/chat/completions"


def extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str):
            return content
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for output in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        for content in output.get("content", []) if isinstance(output, dict) else []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                return content["text"]
    raise ValueError("响应中没有可识别的文本内容")


async def request_ai(profile: AIProfile, messages: list[dict[str, str]], key: str, response_format: dict[str, Any] | None = None) -> tuple[str, str]:
    if not key:
        raise ValueError("尚未保存 AI API Key")
    url = endpoint_url(profile.endpoint)
    if url.endswith("/responses"):
        body: dict[str, Any] = {"model": profile.model, "input": messages, "temperature": 0.1}
    else:
        body = {"model": profile.model, "messages": messages, "temperature": 0.1}
        # 只有 Chat Completions 支持 response_format；模板模式借它约束模型直接输出 JSON。
        if response_format:
            body["response_format"] = response_format
    client_options: dict[str, Any] = {"timeout": 60}
    if should_bypass_proxy(url):
        client_options["trust_env"] = False
    try:
        async with httpx.AsyncClient(**client_options) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body)
    except httpx.HTTPError as exc:
        raise ValueError(f"网络请求失败：{exc}") from exc
    raw = response.text
    if not response.is_success:
        raise ValueError(f"HTTP {response.status_code}：{raw[:1200]}")
    try:
        payload = response.json()
    except Exception as exc:
        raise ValueError(f"服务返回的不是 JSON：{raw[:1200]}") from exc
    return extract_text(payload), json.dumps(payload, ensure_ascii=False, indent=2)


async def test_ai(profile: AIProfile, key: str) -> dict[str, Any]:
    content, raw = await request_ai(profile, [{"role": "user", "content": "只回复 OK"}], key)
    return {"ok": True, "message": f"模型回复：{content[:100]}", "rawResponse": raw}


def ai_format_for(plugin: PluginPackage | None) -> str:
    """有 HTML 报告模板的插件走 JSON 赋值，没有模板的走 Markdown 渲染。"""
    if plugin is not None and getattr(plugin, "report", None):
        return AI_FORMAT_JSON
    return AI_FORMAT_MARKDOWN


# 没有模板时，分析结论其实只有三种。控制器先用确定性 findings 判定属于哪一种，
# 再只把对应那一套输出骨架塞进提示词，模型没有"想写多长就写多长"的自由。
AI_CASE_NORMAL = "normal"
AI_CASE_ABNORMAL = "abnormal"
AI_CASE_INSUFFICIENT = "insufficient"

# 字数预算按去掉空白后的可见字符计算；日志和中文混排下这个口径最接近"读起来多长"。
AI_CASE_BUDGET = {
    AI_CASE_NORMAL: 200,
    AI_CASE_ABNORMAL: 600,
    AI_CASE_INSUFFICIENT: 250,
}
# 插件明确不要问题分析时，异常档只剩结论和摘要，预算跟着收紧。
AI_SUMMARY_ONLY_BUDGET = 250


def ai_case(report: DiagnosticReport) -> str:
    """判定这是哪一类分析：正常、有异常、还是证据不足。"""
    if any(item.severity in {"critical", "warning"} for item in report.findings):
        return AI_CASE_ABNORMAL
    if report.status != "completed" or not report.raw_output.strip():
        return AI_CASE_INSUFFICIENT
    return AI_CASE_NORMAL


def plugin_problem_analysis(plugin: PluginPackage | None) -> bool:
    """插件是否要求在有异常时输出「问题分析」。

    插件清单可用 ai.problemAnalysis: false 关掉；未声明时默认要求，
    这样没有重签的存量插件也能直接用到问题分析。
    """
    options = getattr(plugin, "ai", None)
    declared = options.get("problemAnalysis") if isinstance(options, dict) else None
    return True if declared is None else bool(declared)


def ai_budget(case: str, problem_analysis: bool = True) -> int:
    if case == AI_CASE_ABNORMAL and not problem_analysis:
        return AI_SUMMARY_ONLY_BUDGET
    return AI_CASE_BUDGET.get(case, AI_CASE_BUDGET[AI_CASE_ABNORMAL])


def markdown_skeleton(case: str, problem_analysis: bool, budget: int) -> str:
    """三种情形各自该输出什么、不该输出什么。"""
    if case == AI_CASE_ABNORMAL and problem_analysis:
        body = """## 结论
一句话说明是否存在异常、共几项、最需要关注哪一项。

## 摘要
2-4 句说明影响范围和紧急程度，引用 1-3 个具体指标。

## 问题分析
每项异常一条，最多 3 条，每条写成：**现象** → **依据**（引用报告事实）→ **建议**（写清动作和影响范围）。"""
    elif case == AI_CASE_ABNORMAL:
        body = """## 结论
一句话说明是否存在异常、共几项、最需要关注哪一项。

## 摘要
2-4 句说明影响范围和紧急程度，引用 1-3 个具体指标。
该插件未要求问题分析：不要输出根因推测、处理建议或风险章节。"""
    elif case == AI_CASE_INSUFFICIENT:
        body = """## 结论
一句话说明当前证据不足以判定是否异常。

## 摘要
最多 3 条，写清缺哪项关键证据、需要补采什么、补采后能判断什么。不要猜测根因。"""
    else:
        body = """## 结论
一句话说明未发现异常。

## 摘要
1-2 句，引用 1-2 个关键指标作为依据。不要写根因、建议、风险，也不要写"可排除的根因"或"最终判断"之类的章节。"""
    return f"""{body}

写作规则：
- 只使用上面给出的二级标题，不要增加、删除或改写标题，不要用一级标题。
- 只引用下方报告中出现的事实；推断必须在句子里显式标注为「推断」。
- 不要开场白、免责声明、"如需进一步分析"之类的收尾，不要复述原始输出，不要用表格。
- 全文控制在 {budget} 字以内（不含 Markdown 符号），宁短勿长。"""


def _report_facts(report: DiagnosticReport) -> str:
    findings = "\n".join(
        f"- [{item.severity}] {item.title}｜证据：{item.evidence}｜建议：{item.recommendation or '无'}"
        for item in report.findings
    ) or "- 无"
    return f"""服务器：{report.server['name']}
插件：{report.plugin['name']} {report.plugin['version']}
运行模式：{report.plugin.get('mode', 'standard')}
确定性结论：{report.summary}
确定性发现：
{findings}
原始输出：
{report.raw_output[:MAX_AI_OUTPUT_CHARS]}"""


def build_ai_prompt(report: DiagnosticReport, ai_format: str, case: str | None = None, problem_analysis: bool = True) -> str:
    """按输出格式生成提示词；两种格式的字段规则和硬性约束分开维护。"""
    if ai_format == AI_FORMAT_JSON:
        return f"""你是只读服务器诊断助手。请只输出一个 JSON 对象，不要输出 Markdown 代码块、前后缀说明或任何解释文字。

字段规则：
- summary：字符串，一句话总体结论，不超过 80 字。
- rootCause：字符串，最可能的根因；证据不足时写“无法确定”，不要编造。
- confidence：字符串，只能是 high、medium、low 之一。
- findings：数组，每项含 severity（critical｜warning｜info｜success）、title、evidence、recommendation 四个字符串字段。
- actions：数组，每项含 priority（从 1 开始的整数）、action（字符串）、risk（low｜medium｜high）。

硬性约束：
- evidence 只能引用下方报告中已经出现的事实；推断必须写在 rootCause 或 recommendation 里。
- 不建议未经验证的破坏性操作，涉及重启、删除、数据变更的建议必须写明影响范围。
- 即使没有异常，findings 也要给出结论，severity 使用 success 或 info。
- 字段名必须使用上面的英文名称，不要新增其他字段。

{_report_facts(report)}"""

    resolved_case = case or ai_case(report)
    budget = ai_budget(resolved_case, problem_analysis)
    return f"""你是只读服务器诊断助手。请用 Markdown 输出，严格使用下面给定的二级标题。

{markdown_skeleton(resolved_case, problem_analysis, budget)}

{_report_facts(report)}"""


def visible_length(text: str) -> int:
    """按去掉空白后的可见字符计数，避免 Markdown 换行和缩进虚增篇幅。"""
    return len(re.sub(r"\s+", "", text or ""))


def enforce_budget(content: str, budget: int) -> tuple[str, bool]:
    """超预算时按整行截断并标注，保证报告页不会被模型的长篇淹没。"""
    if visible_length(content) <= budget:
        return content, False
    kept: list[str] = []
    used = 0
    for line in (content or "").splitlines():
        cost = visible_length(line)
        if kept and used + cost > budget:
            break
        kept.append(line)
        used += cost
    trimmed = "\n".join(kept).rstrip()
    return f"{trimmed}\n\n*（内容超出 {budget} 字预算，已截断）*", True


def _strip_code_fence(value: str) -> str:
    trimmed = (value or "").strip()
    if not trimmed.startswith("```"):
        return trimmed
    lines = trimmed.split("\n")
    lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _project_ai_json(payload: dict[str, Any], fallback_summary: str = "") -> dict[str, Any]:
    """把模型返回的宽松 JSON 收敛到契约字段，多余字段直接丢弃。"""
    data: dict[str, Any] = {"summary": _text(payload.get("summary")) or fallback_summary, "findings": []}
    root_cause = _text(payload.get("rootCause")) or _text(payload.get("root_cause"))
    if root_cause:
        data["rootCause"] = root_cause
    confidence = _text(payload.get("confidence")).lower()
    if confidence in CONFIDENCES:
        data["confidence"] = confidence
    raw_findings = payload.get("findings")
    if isinstance(raw_findings, list):
        for item in raw_findings[:MAX_AI_FINDINGS]:
            if not isinstance(item, dict):
                continue
            severity = _text(item.get("severity")).lower()
            data["findings"].append({
                "severity": severity if severity in SEVERITIES else "info",
                "title": _text(item.get("title")) or "AI 分析",
                "evidence": _text(item.get("evidence")),
                "recommendation": _text(item.get("recommendation")),
            })
    actions: list[dict[str, Any]] = []
    raw_actions = payload.get("actions")
    if isinstance(raw_actions, list):
        for order, item in enumerate(raw_actions[:MAX_AI_ACTIONS], start=1):
            if not isinstance(item, dict):
                continue
            action = _text(item.get("action"))
            if not action:
                continue
            try:
                priority = max(1, int(item.get("priority")))
            except (TypeError, ValueError):
                priority = order
            entry: dict[str, Any] = {"priority": priority, "action": action}
            risk = _text(item.get("risk")).lower()
            if risk in RISKS:
                entry["risk"] = risk
            actions.append(entry)
    if actions:
        data["actions"] = actions
    return data


def normalize_ai_json(content: str, fallback_summary: str = "") -> dict[str, Any]:
    """解析并校验模板模式的 AI JSON；任何不符合契约的情况都抛 ValueError。"""
    candidate = _strip_code_fence(content)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("模型回复中没有 JSON 对象")
    try:
        payload = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型回复不是有效 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("AI JSON 顶层必须是对象")
    data = _project_ai_json(payload, fallback_summary)
    errors = sorted(_AI_REPORT_VALIDATOR.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        raise ValueError(f"AI JSON 不符合 ai-report 契约：{errors[0].message}")
    return data


def ai_json_to_markdown(data: dict[str, Any]) -> str:
    """把结构化 AI 结果回写成 Markdown，供应用内置的 AI 页签展示。"""
    parts: list[str] = ["## 结论", "", data.get("summary", "")]
    if data.get("rootCause"):
        suffix = f"（置信度：{CONFIDENCE_LABELS[data['confidence']]}）" if data.get("confidence") in CONFIDENCE_LABELS else ""
        parts += ["", "## 根因判断", "", f"{data['rootCause']}{suffix}"]
    if data.get("findings"):
        parts += ["", "## AI 发现", ""]
        for item in data["findings"]:
            line = f"- **{item['title']}**（{SEVERITY_LABELS.get(item['severity'], item['severity'])}）：{item['evidence']}"
            if item.get("recommendation"):
                line += f" → 建议：{item['recommendation']}"
            parts.append(line)
    if data.get("actions"):
        parts += ["", "## 处理建议", ""]
        for item in data["actions"]:
            risk = f" · 风险 {RISK_LABELS[item['risk']]}" if item.get("risk") in RISK_LABELS else ""
            parts.append(f"{item['priority']}. {item['action']}{risk}")
    return "\n".join(parts).strip()


def build_ai_result(ai_format: str, content: str, raw: str, fallback_summary: str = "", case: str | None = None, problem_analysis: bool = True) -> dict[str, Any]:
    """组装写入 report.ai 的结构；JSON 解析失败时降级成 Markdown，不丢弃模型输出。

    传入 case 才会套用字数预算（生产路径一定传）；不传表示调用方自带长度控制。
    """
    budget = ai_budget(case, problem_analysis) if case else None
    if ai_format == AI_FORMAT_JSON:
        try:
            data = normalize_ai_json(content, fallback_summary)
        except ValueError as exc:
            # 模型没按契约返回时保留原文，让报告页至少能展示可用内容。
            source = _strip_code_fence(content)
            if budget:
                source, _ = enforce_budget(source, budget)
            return {
                "status": "completed", "format": AI_FORMAT_MARKDOWN, "content": source,
                "html": markdown.render(source), "data": None,
                "degraded": True, "degradeReason": str(exc), "rawResponse": raw,
            }
        source = ai_json_to_markdown(data)
        return {
            "status": "completed", "format": AI_FORMAT_JSON, "content": source,
            "html": markdown.render(source), "data": data,
            "degraded": False, "rawResponse": raw,
        }
    source = _strip_code_fence(content)
    truncated = False
    if budget:
        source, truncated = enforce_budget(source, budget)
    return {
        "status": "completed", "format": AI_FORMAT_MARKDOWN, "content": source,
        "html": markdown.render(source), "data": None,
        "degraded": False, "truncated": truncated, "rawResponse": raw,
    }


async def analyze_report(report: DiagnosticReport, profile: AIProfile, plugin: PluginPackage | None = None) -> dict[str, Any]:
    key = saved_key(profile.id)
    if not key:
        raise ValueError(f"AI 配置「{profile.name}」尚未保存 API Key")
    ai_format = ai_format_for(plugin)
    case = ai_case(report)
    problem_analysis = plugin_problem_analysis(plugin)
    prompt = build_ai_prompt(report, ai_format, case, problem_analysis)
    response_format = {"type": "json_object"} if ai_format == AI_FORMAT_JSON else None
    content, raw = await request_ai(profile, [{"role": "user", "content": prompt}], key, response_format)
    return build_ai_result(ai_format, content, raw, report.summary, case=case, problem_analysis=problem_analysis)
