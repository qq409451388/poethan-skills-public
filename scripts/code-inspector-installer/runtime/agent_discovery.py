"""从本机已安装的 Agent 发现可用模型与默认配置。

只读本机已有配置，不猜测、不联网：
- Codex      → `~/.codex/config.toml` 的 `model` / `model_reasoning_effort`
- Claude     → `~/.claude/settings.json` 的 `model` 别名与 `ANTHROPIC_DEFAULT_*_MODEL`
- DeepSeek   → `~/.dsh/settings.yaml` 的 `agent-default-model`

discovery 的职责只有两件事：
1. 发现本机安装了哪些 Agent（按绑定关系）；
2. 读取其当前默认模型/推理档位，作为初始化时的推荐值。

它不负责判断某个模型支持哪些 reasoning —— 那属于 Skill 内置 capability 元数据。
同一个 Agent 可以返回多个 Model，不会再把 Agent 压成单模型。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DEFAULT_LEVEL = 3

# 平台 → 人类可读名称，用于初始化时的 profile id 前缀。
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
    if not model:
        return {}
    reasoning = _toml_string(text, "model_reasoning_effort")
    return {
        "model": model,
        "reasoning": reasoning,
        "models": [{"model": model, "reasoning": reasoning}],
    }


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

    # 别名（opus/sonnet/haiku/…）优先映射到 env 中的完整模型名。
    models: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(model: str | None) -> None:
        name = str(model or "").strip()
        if name and name not in seen:
            seen.add(name)
            models.append({"model": name, "reasoning": None})

    default_model: str | None = None
    if alias:
        # Claude 允许 `opus[1m]` 这类带上下文后缀的别名，映射时先取基础别名。
        base_alias = alias.split("[", 1)[0].strip()
        suffix = (base_alias or alias).upper().replace("-", "_")
        default_model = str(env.get(f"ANTHROPIC_DEFAULT_{suffix}_MODEL") or "").strip() or alias
        add(default_model)
    # settings.json 里声明的其它默认模型同样是本机可用模型。
    for key in sorted(env):
        if key.startswith("ANTHROPIC_DEFAULT_") and key.endswith("_MODEL"):
            add(env.get(key))
    if not models:
        return {}
    if not default_model:
        default_model = models[0]["model"]
    return {"model": default_model, "reasoning": None, "models": models}


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
    if not value:
        return {}
    return {"model": value, "reasoning": None, "models": [{"model": value, "reasoning": None}]}


DISCOVERERS = {
    "codex": discover_codex,
    "claude": discover_claude,
    "dsh": discover_dsh,
}


def discover_platform(platform: str, home: Path | None = None) -> dict[str, Any]:
    """返回 {model, reasoning, models[]}；无法确认时返回空对象。"""
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


def _binding_platform(binding: Any) -> str:
    if not isinstance(binding, dict):
        return ""
    return str(binding.get("agent_platform") or binding.get("agent") or "").strip()


def discover_installed_agents(home: Path | None = None) -> list[str]:
    """本机绑定过的 Agent 名称（不区分角色）。"""
    return sorted({
        platform
        for binding in load_bindings(home).values()
        if (platform := _binding_platform(binding))
    })


def discover_agents(home: Path | None = None, role: str = "developer") -> list[dict[str, Any]]:
    """把本机 Developer 执行身份展开成候选条目。

    每个 (agent, model) 一条，同一个 Agent 的多个 Model 都会保留；
    只在该模型能从本机配置确认时才给出，避免写入编造的模型名。
    """
    bindings = load_bindings(home)
    discovered: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for alias, binding in sorted(bindings.items()):
        if not isinstance(binding, dict) or binding.get("role") != role:
            continue
        platform = _binding_platform(binding)
        if not platform:
            continue
        found = discover_platform(platform, home)
        models = found.get("models") or (
            [{"model": found["model"], "reasoning": found.get("reasoning")}]
            if found.get("model") else []
        )
        label = PLATFORM_LABELS.get(platform, platform)
        for item in models:
            model = str(item.get("model") or "").strip()
            if not model or (platform, model) in seen:
                continue
            seen.add((platform, model))
            # 模型名已带平台前缀时不再重复（claude-opus-5[1M] 不写成 claude-claude-…）。
            base = model if model.lower().startswith(label.lower()) else f"{label}-{model}"
            discovered.append({
                "id": base,
                "agent": platform,
                "model": model,
                # 只有「当前默认模型」才是确定的推荐档位，其余留空交由 capability 决定。
                "reasoning": (
                    found.get("reasoning")
                    if model == found.get("model") else None
                ),
                "level": DEFAULT_LEVEL,
                "enabled": True,
                "alias": alias,
                "isDefaultModel": model == found.get("model"),
            })
    return discovered
