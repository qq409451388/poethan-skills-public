"""WebApp 侧的 Agent Model Routing 配置适配层。

设计要点：
- 路径不在这里重新拼接：配置路径由 `agent_routing.routing_config_path()` 唯一决定，
  因此 WebApp 的读取、保存、展示与 Runtime 指向同一个文件（含 AGENT_ROUTING_CONFIG 覆盖）。
- 模块加载优先使用安装后的 `~/.agent-review/bin/`，缺失时回退到源码 checkout，
  避免「源码已更新但未重新安装」时报缺少 agent_routing.py。
- 不复制第二套 schema 校验：校验、序列化、合并全部委托给 agent_routing 模块。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from commands import review_home

# apps/code-inspector-webtool/routing.py → 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCE = REPO_ROOT / "scripts" / "code-inspector-installer" / "runtime"
SKILL_CONFIG_SOURCE = REPO_ROOT / "skills" / "code-inspector" / "config"

CAPABILITY_FILENAME = "agent-capabilities.yml"
EXAMPLE_FILENAME = "agent-routing.example.yml"

_PROFILE_FIELD = re.compile(r"^p(\d+)_(id|agent|model|reasoning|level|enabled)$")


def _load_module(module_name: str, filename: str):
    """先装后源：优先安装目录，其次源码 checkout。"""
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    candidates = [
        review_home() / "bin" / filename,
        RUNTIME_SOURCE / filename,
    ]
    module_path = next((path for path in candidates if path.exists()), None)
    if module_path is None:
        tried = "、".join(str(path) for path in candidates)
        raise RuntimeError(f"未找到 {filename}，已尝试：{tried}。请重新安装 Code Inspector。")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_routing_module():
    return _load_module("code_inspector_agent_routing", "agent_routing.py")


def load_discovery_module():
    return _load_module("code_inspector_agent_discovery", "agent_discovery.py")


def routing_config_path() -> Path:
    """与 Runtime 完全一致的单一路径解析。"""
    return load_routing_module().routing_config_path(review_home())


def capability_file() -> Path:
    """capability 元数据：优先本机安装副本，缺失时回退源码 checkout。"""
    module = load_routing_module()
    installed = module.capability_path(review_home())
    if installed.exists():
        return installed
    source = SKILL_CONFIG_SOURCE / CAPABILITY_FILENAME
    return source if source.exists() else installed


def load_capabilities() -> dict[str, list[dict[str, Any]]]:
    module = load_routing_module()
    return module.load_capabilities(capability_file())


def module_source() -> str:
    """当前 agent_routing 来自安装目录还是源码，用于页面诊断。"""
    installed = review_home() / "bin" / "agent_routing.py"
    return "installed" if installed.exists() else "source"


def example_text() -> str:
    for candidate in (
        review_home() / "config" / EXAMPLE_FILENAME,
        SKILL_CONFIG_SOURCE / EXAMPLE_FILENAME,
    ):
        if candidate.exists():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                continue
    return f"# 未找到示例文件。可在 {SKILL_CONFIG_SOURCE / EXAMPLE_FILENAME} 查看。\n"


def discover_candidates() -> list[dict[str, Any]]:
    return load_discovery_module().discover_agents()


def discover_installed_agents() -> list[str]:
    try:
        return load_discovery_module().discover_installed_agents()
    except Exception:  # noqa: BLE001 - 诊断信息不可用时不影响页面
        return []


def _snapshot():
    module = load_routing_module()
    return module.load_routing_snapshot(routing_config_path(), load_capabilities())


def status_view() -> dict[str, Any]:
    """页面读取入口：始终返回结构，不抛异常，非法配置用 INVALID 表达。"""
    module = load_routing_module()
    path = routing_config_path()
    snapshot = _snapshot()
    view = snapshot.as_dict()
    view["configPath"] = str(path)
    view["capabilityPath"] = str(capability_file())
    # 环境变量覆盖只在排查时展示，避免页面误导实际生效路径。
    view["pathOverride"] = os.environ.get("AGENT_ROUTING_CONFIG") or None
    view["moduleSource"] = module_source()
    if not path.exists() and view["status"] == module.ROUTER_DISABLED:
        view["message"] = "尚未配置：从本机 Agent 初始化，或手工新增 Dev 执行配置。"
    elif view["status"] == module.ROUTER_INVALID:
        view["message"] = "配置无效：Model Router 未启用，代码检查主流程不受影响。"
    else:
        view["message"] = "模型路由已启用。"
    view["enabledCount"] = sum(1 for entry in view["profiles"] if entry["enabled"])
    view["agentOptions"] = _agent_options(view)
    view["modelOptions"] = _model_options(view)
    return view


def _agent_options(view: dict[str, Any]) -> list[str]:
    """可选 Agent：capability 收录 + 本机已安装 + 现有配置里出现的。"""
    options = set(view.get("capabilities") or {})
    options.update(discover_installed_agents())
    options.update(entry["agent"] for entry in view.get("profiles") or [])
    return sorted(options)


def _model_options(view: dict[str, Any]) -> dict[str, list[str]]:
    capabilities = view.get("capabilities") or {}
    return {agent: [item["model"] for item in models] for agent, models in capabilities.items()}


def validate_document(document: Any) -> tuple[bool, str | None]:
    module = load_routing_module()
    try:
        module.parse_routing_document(document, load_capabilities())
    except module.AgentRoutingError as exc:
        return False, str(exc)
    return True, None


def save_document(document: Any) -> dict[str, Any]:
    """完整校验 → 原子写入 → runtime reload；校验失败时现有文件保持不变。"""
    module = load_routing_module()
    normalized, snapshot = module.save_routing_document(
        document, routing_config_path(), load_capabilities(),
    )
    return {"saved": normalized, "snapshot": snapshot.as_dict()}


def save_yaml_text(text: str) -> dict[str, Any]:
    module = load_routing_module()
    capabilities = load_capabilities()
    try:
        document = module.parse_routing_text(text, capabilities)
    except module.AgentRoutingError as exc:
        raise ValueError(str(exc)) from exc
    normalized, snapshot = module.save_routing_document(
        document, routing_config_path(), capabilities,
    )
    return {"saved": normalized, "snapshot": snapshot.as_dict()}


def dump_yaml(document: Any) -> str:
    module = load_routing_module()
    return module.dump_routing_document(document, load_capabilities())


def seed_result(replace: bool = False) -> dict[str, Any]:
    """生成初始化结果。

    - 无现有配置：直接生成（mode=create）。
    - 有现有配置且 replace=False：与现有配置合并，保留人工配置（mode=merge）。
    - replace=True：整体替换（mode=replace），必须由用户显式选择。
    """
    module = load_routing_module()
    capabilities = load_capabilities()
    discovered = discover_candidates()
    path = routing_config_path()
    if path.exists() and not replace:
        try:
            existing = module.load_document(path, capabilities)
        except Exception:  # noqa: BLE001 - 现有配置损坏时不静默覆盖，改为报错
            raise ValueError(
                f"现有配置无法解析，已停止初始化以免覆盖：{path}。"
                "请先修复或清空该文件，再选择「整体替换」。"
            )
        merged = module.merge_seed_document(existing, discovered, capabilities)
        merged["mode"] = "merge"
        return merged
    document = module.seed_document(discovered, capabilities)
    return {
        "document": document,
        "added": list(document["profiles"]),
        "kept": 0,
        "mode": "replace" if path.exists() else "create",
    }


def profiles_from_form(form) -> list[dict[str, Any]]:
    """把表单里的 p{i}_* 字段折叠回 profiles 列表。

    字段带显式下标，因此不依赖各字段的提交顺序，也不受「未勾选 checkbox 不提交」影响。
    """
    rows: dict[int, dict[str, str]] = {}
    for key in form.keys():
        match = _PROFILE_FIELD.match(key)
        if not match:
            continue
        rows.setdefault(int(match.group(1)), {})[match.group(2)] = form.get(key, "")

    profiles: list[dict[str, Any]] = []
    used: set[str] = set()
    for index in sorted(rows):
        row = rows[index]
        agent = str(row.get("agent") or "").strip()
        model = str(row.get("model") or "").strip()
        reasoning = str(row.get("reasoning") or "").strip()
        level_text = str(row.get("level") or "").strip()
        if not any((agent, model, reasoning, level_text)):
            continue  # 整行空：忽略
        profile_id = str(row.get("id") or "").strip()
        if not profile_id:
            slug = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{agent}-{model}-{reasoning}").strip("-").lower()
            profile_id = slug or "profile"
        candidate, suffix = profile_id, 2
        while candidate in used:
            candidate = f"{profile_id}-{suffix}"
            suffix += 1
        used.add(candidate)
        profiles.append({
            "id": candidate,
            "agent": agent,
            "model": model,
            "reasoning": reasoning,
            "level": int(level_text) if level_text.lstrip("-").isdigit() else level_text,
            "enabled": str(row.get("enabled") or "").strip().lower() in {"1", "true", "yes", "on"},
        })
    return profiles


def capabilities_json() -> str:
    """供前端按 Model 联动 Reasoning 使用的能力表。"""
    module = load_routing_module()
    return json.dumps(
        {
            "capabilities": load_capabilities(),
            "fallbackReasonings": list(module.FALLBACK_REASONING),
        },
        ensure_ascii=False,
    )


def recommended_for(difficulty: Any) -> list[dict[str, Any]]:
    """按当前配置动态计算某个 difficulty 的推荐，供 Issue 页面展示。

    不读取 Issue 里持久化的旧快照，因此 Routing 修改后页面立即反映最新结果。
    """
    try:
        value = int(difficulty) if difficulty is not None else None
    except (TypeError, ValueError):
        return []
    try:
        return _snapshot().recommend(value)
    except Exception:  # noqa: BLE001 - 展示失败不应影响 Issue 页面
        return []


def candidate_profiles(difficulty: Any) -> list[dict[str, Any]]:
    """可作为执行者的合法候选：enabled 且 level >= difficulty。

    与 Router 的推荐集合完全一致；不展示所有 enabled profile，避免选到
    当前根本完不成该任务的执行配置。
    """
    try:
        value = int(difficulty) if difficulty is not None else None
    except (TypeError, ValueError):
        return []
    try:
        return _snapshot().candidates(value)
    except Exception:  # noqa: BLE001
        return []


def assignment_state(assignment: Any, difficulty: Any) -> tuple[str, str | None]:
    """返回 (assignmentStatus, assignmentInvalidReason)；只判定不改写。"""
    try:
        value = int(difficulty) if difficulty is not None else None
    except (TypeError, ValueError):
        value = None
    try:
        module = load_routing_module()
        return module.assignment_state(assignment, value, _snapshot())
    except Exception:  # noqa: BLE001 - 判定失败不应打断 Issue 页面
        if isinstance(assignment, dict) and assignment.get("profileId"):
            return "STALE", "router_disabled"
        return "NONE", None


def profile_groups(status: dict[str, Any]) -> list[dict[str, Any]]:
    """把 profiles 按 Agent 分组，并附带每个 Model 的可选 Reasonings。"""
    module = load_routing_module()
    capabilities = status.get("capabilities") or {}
    groups: list[dict[str, Any]] = []
    by_agent: dict[str, dict[str, Any]] = {}
    for position, profile in enumerate(status.get("profiles") or []):
        group = by_agent.get(profile["agent"])
        if group is None:
            group = {"agent": profile["agent"], "rows": []}
            by_agent[profile["agent"]] = group
            groups.append(group)
        models = [item["model"] for item in capabilities.get(profile["agent"], [])]
        if profile["model"] not in models:
            models = [*models, profile["model"]]  # 保留自定义模型，避免静默丢失
        group["rows"].append({
            "index": position,
            "profile": profile,
            "modelOptions": models,
            "reasoningOptions": module.reasoning_options(
                capabilities, profile["agent"], profile["model"],
            ),
        })
    return groups
