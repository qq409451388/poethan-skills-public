from __future__ import annotations

import json
from typing import Any

import httpx

from .models import AISettings, DiagnosticReport
from .secrets import secrets


AI_KEY_ACCOUNT = "ai:api-key"


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


async def request_ai(settings: AISettings, messages: list[dict[str, str]]) -> tuple[str, str]:
    key = secrets.get(AI_KEY_ACCOUNT)
    if not key:
        raise ValueError("尚未保存 AI API Key")
    url = endpoint_url(settings.endpoint)
    body = {"model": settings.model, "input": messages, "temperature": 0.1} if url.endswith("/responses") else {"model": settings.model, "messages": messages, "temperature": 0.1}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body)
    raw = response.text
    if not response.is_success:
        raise ValueError(f"HTTP {response.status_code}：{raw[:1200]}")
    try:
        payload = response.json()
    except Exception as exc:
        raise ValueError(f"服务返回的不是 JSON：{raw[:1200]}") from exc
    return extract_text(payload), json.dumps(payload, ensure_ascii=False, indent=2)


async def test_ai(settings: AISettings) -> dict[str, Any]:
    content, raw = await request_ai(settings, [{"role": "user", "content": "只回复 OK"}])
    return {"ok": True, "message": f"模型回复：{content[:100]}", "rawResponse": raw}


async def analyze_report(report: DiagnosticReport, settings: AISettings) -> dict[str, Any]:
    prompt = f"""你是只读服务器诊断助手。根据以下报告给出根因推断、证据和按优先级排列的处理建议。明确区分事实与推断，不建议未经验证的破坏性操作。使用 Markdown。

服务器：{report.server['name']}
插件：{report.plugin['name']} {report.plugin['version']}
确定性结论：{report.summary}
原始输出：
{report.raw_output[:120000]}
"""
    content, raw = await request_ai(settings, [{"role": "user", "content": prompt}])
    return {"status": "completed", "content": content, "rawResponse": raw}
