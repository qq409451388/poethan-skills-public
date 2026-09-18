"""WebApp 侧的 Agent Model Routing 配置适配层。

唯一的配置真相仍在 `<review_home>/config/agent-routing.yml`。本模块只负责：
- 解析安装后的 `~/.agent-review/bin/agent_routing.py`，复用同一份校验规则，
  不在 Web 层复制第二套 schema 校验；
- 把 YAML 文本或结构化 agents 列表提交给该模块做「校验 → 临时文件 → 原子替换 → reload」。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from commands import review_home


def routing_config_path() -> Path:
    return review_home() / "config" / "agent-routing.yml"


def routing_example_path() -> Path:
    """示例文件随 Skill 安装，这里只用作展示时的后备。"""
    return review_home() / "config" / "agent-routing.example.yml"


def load_routing_module():
    """加载安装后的 agent_routing 模块；未安装时抛出可读错误。"""
    cached = sys.modules.get("code_inspector_agent_routing")
    if cached is not None:
        return cached
    module_path = review_home() / "bin" / "agent_routing.py"
    if not module_path.exists():
        raise RuntimeError(f"未找到 Agent Routing 模块：{module_path}。请重新安装 Code Inspector。")
    spec = importlib.util.spec_from_file_location("code_inspector_agent_routing", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 Agent Routing 模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["code_inspector_agent_routing"] = module
    spec.loader.exec_module(module)
    return module


def load_discovery_module():
    """加载安装后的 agent_discovery 模块，用于从本机 Agent 初始化配置。"""
    cached = sys.modules.get("code_inspector_agent_discovery")
    if cached is not None:
        return cached
    module_path = review_home() / "bin" / "agent_discovery.py"
    if not module_path.exists():
        raise RuntimeError(
            f"未找到本机 Agent 发现模块：{module_path}。请重新安装 Code Inspector。"
        )
    spec = importlib.util.spec_from_file_location("code_inspector_agent_discovery", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载本机 Agent 发现模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["code_inspector_agent_discovery"] = module
    spec.loader.exec_module(module)
    return module


def discover_candidates() -> list[dict[str, Any]]:
    """返回本机已绑定为 Developer、且能从其自身配置确认模型的候选条目。"""
    return load_discovery_module().discover_agents()


def seed_document() -> dict[str, Any]:
    """把发现结果转换为候选配置文档（仍需通过完整校验才能保存）。"""
    return load_routing_module().seed_document(discover_candidates())


def status_view() -> dict[str, Any]:
    """页面读取入口：始终返回结构，不抛异常，非法配置用 INVALID 表达。"""
    module = load_routing_module()
    path = routing_config_path()
    snapshot = module.get_snapshot(path)
    view = snapshot.as_dict()
    view["configPath"] = str(path)
    # 环境变量覆盖只在排查时展示，避免页面误导实际生效路径。
    view["pathOverride"] = os.environ.get("AGENT_ROUTING_CONFIG") or None
    if not path.exists() and view["status"] == module.ROUTER_DISABLED:
        view["message"] = "尚未配置：复制示例文件为 agent-routing.yml 后即可启用模型路由。"
    elif view["status"] == module.ROUTER_INVALID:
        view["message"] = "配置无效：Model Router 未启用，代码检查主流程不受影响。"
    else:
        view["message"] = "模型路由已启用。"
    view["enabledCount"] = sum(1 for entry in view["agents"] if entry["enabled"])
    return view


def example_text() -> str:
    path = routing_example_path()
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "# 尚未安装示例文件。可复制为 " + str(routing_config_path()) + "\n"
        "version: 1\nagents: []\n"
    )


def validate_document(document: Any) -> tuple[bool, str | None]:
    module = load_routing_module()
    try:
        module.parse_routing_document(document)
    except module.AgentRoutingError as exc:
        return False, str(exc)
    return True, None


def save_document(document: Any) -> dict[str, Any]:
    """完整校验 → 原子写入 → runtime reload；校验失败时现有文件保持不变。"""
    module = load_routing_module()
    normalized, snapshot = module.save_routing_document(document, routing_config_path())
    return {"saved": normalized, "snapshot": snapshot.as_dict()}


def save_yaml_text(text: str) -> dict[str, Any]:
    module = load_routing_module()
    try:
        document = module.parse_routing_text(text)
    except module.AgentRoutingError as exc:
        raise ValueError(str(exc)) from exc
    normalized, snapshot = module.save_routing_document(document, routing_config_path())
    return {"saved": normalized, "snapshot": snapshot.as_dict()}


def dump_yaml(document: Any) -> str:
    module = load_routing_module()
    return module.dump_routing_document(document)


def agents_from_form(form) -> list[dict[str, Any]]:
    """把页面表格的重复字段折叠回 agents 列表。

    只有 id / agent / model / level 由用户输入，role 和 reasoning 是下拉框，总会带上
    一个值。因此「整行空」判断只看这四个输入列：都不填且未勾选 enabled 才忽略该行。
    """
    ids = form.getlist("agent_id")
    agent_names = form.getlist("agent_agent")
    roles = form.getlist("agent_role")
    models = form.getlist("agent_model")
    reasonings = form.getlist("agent_reasoning")
    levels = form.getlist("agent_level")
    enabled_indexes = _enabled_indexes(form.getlist("agent_enabled_row"))

    def value(items: list[str], index: int) -> str:
        return items[index].strip() if index < len(items) else ""

    agents: list[dict[str, Any]] = []
    for index in range(len(ids)):
        agent_id = value(ids, index)
        agent_name = value(agent_names, index)
        model = value(models, index)
        level_text = value(levels, index)
        if not any((agent_id, agent_name, model, level_text)) and index not in enabled_indexes:
            continue  # 完全空行直接忽略，避免保存时被当成非法条目。
        if not level_text.isdigit() or int(level_text) < 1:
            raise ValueError(f"第 {index + 1} 行 level 必须是正整数")
        agents.append({
            "id": agent_id,
            "agent": agent_name,
            "role": value(roles, index).upper() or "DEVELOPER",
            "model": model,
            "reasoning": value(reasonings, index).lower() or "high",
            "level": int(level_text),
            "enabled": index in enabled_indexes,
        })
    return agents


def _enabled_indexes(flags: list[str]) -> set[int]:
    indexes: set[int] = set()
    for value in flags:
        text = value.strip()
        if text.startswith("row") and text[3:].isdigit():
            indexes.add(int(text[3:]))
    return indexes
