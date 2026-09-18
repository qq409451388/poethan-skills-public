"""从本机已安装的 Agent 发现可用于路由的模型与推理档位。

只读本机已有配置，不猜测、不联网：
- Codex      → `~/.codex/config.toml` 的 `model` / `model_reasoning_effort`
- Claude     → `~/.claude/settings.json` 的 `model` 别名与 `ANTHROPIC_DEFAULT_*_MODEL`
- DeepSeek   → `~/.dsh/settings.yaml` 的 `agent-default-model`
- Trae-CN    → 无稳定可读的模型配置，返回空结果

发现结果只用于「初始化」时填充候选值，最终 level 由人类在页面上确认。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DEFAULT_REASONING = "high"
DEFAULT_LEVEL = 3

# 平台 → 人类可读名称，用于初始化时的 id 前缀与展示。
PLATFORM_LABELS = {
    "codex": "codex",
    "claude": "claude",
    "dsh": "dsh",
    "trae-cn": "trae",
}


def _home() -> Path:
    return Path(os.path.expandvars(os.path.expanduser("~"))).resolve()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _toml_string(text: str, key: str) -> str | None:
    """只取顶层 `key = "value"`，避免引入 TOML 依赖。"""
    match = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return match.group(1).strip() or None if match else None


def discover_codex(home: Path) -> dict[str, Any]:
    text = _read_text(home / ".codex" / "config.toml")
    if not text:
        return {}
    model = _toml_string(text, "model")
    reasoning = _toml_string(text, "model_reasoning_effort")
    found: dict[str, Any] = {}
    if model:
        found["model"] = model
    if reasoning:
        found["reasoning"] = reasoning
    return found


def discover_claude(home: Path) -> dict[str, Any]:
    text = _read_text(home / ".claude" / "settings.json")
    if not text:
        return {}
    try:
        settings = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(settings, dict):
        return {}
    env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    alias = str(settings.get("model") or "").strip()
    if not alias and not env:
        return {}
    # 别名（opus/sonnet/haiku）优先映射到 env 中的完整模型名。
    model = None
    if alias:
        suffix = alias.upper().replace("-", "_")
        model = str(env.get(f"ANTHROPIC_DEFAULT_{suffix}_MODEL") or "").strip() or alias
    if not model:
        model = str(env.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or "").strip() or None
    return {"model": model} if model else {}


def discover_dsh(home: Path) -> dict[str, Any]:
    text = _read_text(home / ".dsh" / "settings.yaml")
    if not text:
        return {}
    # 避免为可选功能强依赖 PyYAML：只解析 agent-default-model 段。
    match = re.search(
        r"^agent-default-model:\s*\n((?:[ \t]+.*\n?)*)", text, re.MULTILINE,
    )
    if not match:
        return {}
    block = match.group(1)
    model = re.search(r"^\s*model:\s*(\S+)\s*$", block, re.MULTILINE)
    if not model:
        return {}
    value = model.group(1).strip().strip("'\"")
    return {"model": value} if value else {}


DISCOVERERS = {
    "codex": discover_codex,
    "claude": discover_claude,
    "dsh": discover_dsh,
}


def discover_platform(platform: str, home: Path | None = None) -> dict[str, Any]:
    discoverer = DISCOVERERS.get(platform)
    if discoverer is None:
        return {}
    try:
        return discoverer(home or _home())
    except Exception:  # noqa: BLE001 - 发现失败不能影响配置页
        return {}


def bindings_path(home: Path | None = None) -> Path:
    override = os.environ.get("AGENT_REVIEW_HOME")
    review_home = Path(os.path.expandvars(os.path.expanduser(
        override or "~/.agent-review"
    ))).resolve()
    return review_home / "config" / "agent-bindings.json"


def load_bindings(home: Path | None = None) -> dict[str, Any]:
    text = _read_text(bindings_path(home))
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def discover_agents(home: Path | None = None) -> list[dict[str, Any]]:
    """把本机已绑定的 Developer 执行身份转成候选路由条目。

    只在能被本机配置确认模型时给出条目；无法确认的平台（如 Trae-CN）跳过，
    避免写入编造的模型名。
    """
    bindings = load_bindings(home)
    discovered: list[dict[str, Any]] = []
    seen_platform: set[str] = set()
    for alias, binding in sorted(bindings.items()):
        if not isinstance(binding, dict) or binding.get("role") != "developer":
            continue
        platform = str(binding.get("agent_platform") or binding.get("agent") or "").strip()
        if not platform or platform in seen_platform:
            continue
        found = discover_platform(platform, home)
        model = found.get("model")
        if not model:
            continue
        seen_platform.add(platform)
        discovered.append({
            "id": f"{PLATFORM_LABELS.get(platform, platform)}-developer",
            "agent": platform,
            "role": "DEVELOPER",
            "model": str(model),
            "reasoning": str(found.get("reasoning") or DEFAULT_REASONING),
            "level": DEFAULT_LEVEL,
            "enabled": True,
            "alias": alias,
        })
    return discovered
