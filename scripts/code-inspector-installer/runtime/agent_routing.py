"""Agent Routing：把 Inspector 的抽象 difficulty 映射为可执行的 Developer 配置。

职责边界：
- Inspector 只输出任务困难程度 `difficulty`，不感知任何具体模型名称。
- 本模块读取本机级 YAML 配置（`<review_home>/config/agent-routing.yml`），
  按 `enabled + role + level >= difficulty` 计算候选执行配置。
- 配置文件不存在时不启用 Router；配置非法时不启用 Router 且不影响主流程。

配置更新采用「磁盘 + 不可变内存快照」：写入走临时文件加原子替换，加载成功后才
替换运行时快照；加载失败继续沿用上一份有效快照。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

# 配置版本：v1 是历史扁平 agents 列表，v2 是 Dev Execution Profile 列表。
# 只读时兼容 v1，写盘一律输出 v2。
SUPPORTED_VERSIONS = {1, 2}
CONFIG_VERSION = 2

# 执行配置只代表 Developer 执行能力；role 不再是可配置维度。
ROLE_DEVELOPER = "DEVELOPER"

# 离散等级：与 Inspector 的 difficulty 使用同一尺度，页面用滑杆选择。
LEVEL_SCALE = (1, 2, 3, 4, 5)
DEFAULT_LEVEL = 3
MAX_PROFILES = 200

# capability 未收录某模型时的兜底档位集合。页面与后端共用同一份，避免集合不一致。
FALLBACK_REASONING = ("none", "minimal", "low", "medium", "high", "xhigh")
CAPABILITY_VERSION = 1

ROUTER_DISABLED = "DISABLED"
ROUTER_INVALID = "INVALID"

# Assignment（真正选定的执行者）状态。判定只看「当前配置」，不看历史 revision。
ASSIGNMENT_NONE = "NONE"
ASSIGNMENT_VALID = "VALID"
ASSIGNMENT_STALE = "STALE"

# STALE 原因码；WebApp 与 Inspector 直接复用，不需要各自再推导。
ASSIGNMENT_REASON_ROUTER_DISABLED = "router_disabled"
ASSIGNMENT_REASON_PROFILE_MISSING = "profile_missing"
ASSIGNMENT_REASON_PROFILE_DISABLED = "profile_disabled"
ASSIGNMENT_REASON_PROFILE_CHANGED = "profile_changed"
ASSIGNMENT_REASON_LEVEL_BELOW_DIFFICULTY = "profile_level_below_difficulty"
ASSIGNMENT_REASON_LEVEL_REDUCED = "profile_level_reduced"


class AgentRoutingError(ValueError):
    """配置无效；调用方应保留上一份有效快照并记录日志。"""


def parse_difficulty(value: Any) -> int | None:
    """difficulty 是可选字段；空值表示 Inspector 未给出困难程度。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("difficulty 必须是正整数")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not text.isdigit():
            raise ValueError(f"difficulty 必须是正整数: {value}")
        value = int(text)
    if not isinstance(value, int):
        raise ValueError(f"difficulty 必须是正整数: {value}")
    if value < 1:
        raise ValueError(f"difficulty 必须大于 0: {value}")
    return value


def routing_config_path(home: Path) -> Path:
    """Agent Routing 配置文件的唯一路径解析入口。

    Runtime（review-db.py）与 WebApp 都必须调用本函数，读取、保存、展示指向同一文件；
    任何一处自行拼接路径都会造成路径分裂。
    """
    override = os.environ.get("AGENT_ROUTING_CONFIG")
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override))).resolve()
    return Path(home).resolve() / "config" / "agent-routing.yml"


def capability_path(home: Path) -> Path:
    """Skill 内置 capability 元数据路径。"""
    override = os.environ.get("AGENT_CAPABILITY_CONFIG")
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override))).resolve()
    return Path(home).resolve() / "config" / "agent-capabilities.yml"


def parse_capabilities_document(document: Any) -> dict[str, list[dict[str, Any]]]:
    """校验 capability 元数据，返回 {agent: [{model, supportedReasonings}]}。"""
    if not isinstance(document, dict):
        raise AgentRoutingError("capability 根节点必须是对象")
    version = document.get("version")
    if isinstance(version, bool) or version != CAPABILITY_VERSION:
        raise AgentRoutingError(f"不支持的 capability version: {version}")
    raw_agents = document.get("agents") or []
    if not isinstance(raw_agents, list):
        raise AgentRoutingError("capability.agents 必须是数组")
    catalog: dict[str, list[dict[str, Any]]] = {}
    for agent_index, raw_agent in enumerate(raw_agents):
        if not isinstance(raw_agent, dict):
            raise AgentRoutingError(f"capability.agents[{agent_index}] 必须是对象")
        agent = str(raw_agent.get("agent") or "").strip()
        if not agent:
            raise AgentRoutingError(f"capability.agents[{agent_index}].agent 必须是非空字符串")
        if agent in catalog:
            raise AgentRoutingError(f"capability.agents 中 agent 必须唯一，重复: {agent}")
        raw_models = raw_agent.get("models") or []
        if not isinstance(raw_models, list):
            raise AgentRoutingError(f"capability.agents[{agent_index}].models 必须是数组")
        models: list[dict[str, Any]] = []
        seen_models: set[str] = set()
        for model_index, raw_model in enumerate(raw_models):
            if not isinstance(raw_model, dict):
                raise AgentRoutingError(
                    f"capability.agents[{agent_index}].models[{model_index}] 必须是对象"
                )
            model = str(raw_model.get("model") or "").strip()
            if not model:
                raise AgentRoutingError(
                    f"capability.agents[{agent_index}].models[{model_index}].model 必须是非空字符串"
                )
            if model in seen_models:
                raise AgentRoutingError(f"capability 中 {agent} 的 model 必须唯一，重复: {model}")
            seen_models.add(model)
            raw_reasonings = raw_model.get("supportedReasonings")
            if not isinstance(raw_reasonings, list) or not raw_reasonings:
                raise AgentRoutingError(
                    f"capability 中 {agent}/{model} 必须声明非空 supportedReasonings"
                )
            reasonings = [
                str(item).strip().lower() for item in raw_reasonings if str(item).strip()
            ]
            if not reasonings:
                raise AgentRoutingError(
                    f"capability 中 {agent}/{model} 的 supportedReasonings 不能为空"
                )
            models.append({"model": model, "supportedReasonings": reasonings})
        catalog[agent] = models
    return catalog


_capability_cache: dict[str, Any] = {}


def load_capabilities(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """按 mtime 缓存读取 capability；缺失或非法时返回空表（退化为通用档位集合）。"""
    resolved = path or capability_path(_review_home())
    try:
        key = f"{resolved}:{resolved.stat().st_mtime_ns}"
    except OSError:
        return {}
    cached = _capability_cache.get("entry")
    if cached and cached[0] == key:
        return cached[1]
    try:
        catalog = parse_capabilities_document(
            _yaml_load(resolved.read_text(encoding="utf-8"))
        )
    except (AgentRoutingError, OSError, UnicodeDecodeError):
        catalog = {}
    _capability_cache["entry"] = (key, catalog)
    return catalog


def reasoning_options(
    capabilities: dict[str, list[dict[str, Any]]], agent: str, model: str,
) -> list[str]:
    """返回某个 Agent/Model 支持的 reasoning 档位；未收录时退回通用集合。

    页面与后端共用本函数，避免出现「后端允许但页面无法表达」的集合不一致。
    """
    for entry in capabilities.get(str(agent).strip(), []) or []:
        if entry["model"] == str(model).strip():
            return list(entry["supportedReasonings"])
    return list(FALLBACK_REASONING)


def catalog_agents(capabilities: dict[str, list[dict[str, Any]]]) -> list[str]:
    return sorted(capabilities)


def _reasoning_default(
    capabilities: dict[str, list[dict[str, Any]]], agent: str, model: str, preferred: str | None,
) -> str:
    """在模型支持的档位内挑选一个合理默认值。"""
    options = reasoning_options(capabilities, agent, model)
    candidate = str(preferred or "").strip().lower()
    if candidate in options:
        return candidate
    for fallback in ("high", "medium", "low", "minimal", "none", "xhigh"):
        if fallback in options:
            return fallback
    return options[0]


def _require_text(entry: dict[str, Any], field: str, index: int) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AgentRoutingError(f"profiles[{index}].{field} 必须是非空字符串")
    return value.strip()


def _require_bool(entry: dict[str, Any], field: str, index: int, default: bool) -> bool:
    value = entry.get(field, default)
    if not isinstance(value, bool):
        raise AgentRoutingError(f"profiles[{index}].{field} 必须是布尔值")
    return value


def _normalize_profile(
    raw: Any, index: int, capabilities: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentRoutingError(f"profiles[{index}] 必须是对象")
    profile_id = _require_text(raw, "id", index)
    agent = _require_text(raw, "agent", index)
    model = _require_text(raw, "model", index)
    reasoning = _require_text(raw, "reasoning", index).lower()
    allowed = reasoning_options(capabilities, agent, model)
    if reasoning not in allowed:
        raise AgentRoutingError(
            f"profiles[{index}].reasoning 无效: {reasoning}；"
            f"{agent}/{model} 支持: {', '.join(allowed)}"
        )
    level = raw.get("level")
    if isinstance(level, bool) or not isinstance(level, int) or level not in LEVEL_SCALE:
        raise AgentRoutingError(
            f"profiles[{index}].level 必须是 {LEVEL_SCALE[0]}..{LEVEL_SCALE[-1]} 的整数"
        )
    return {
        "id": profile_id,
        "agent": agent,
        "model": model,
        "reasoning": reasoning,
        "level": level,
        "enabled": _require_bool(raw, "enabled", index, True),
    }


def _legacy_profiles(
    document: dict[str, Any], capabilities: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """v1 扁平 agents 列表 → v2 profiles。

    执行配置只代表 Dev Execution Profile，因此旧配置里的 INSPECTOR 条目会被丢弃，
    而带 role=DEVELOPER 或未写 role 的条目按原样保留。

    旧配置产生时还没有 capability 校验，因此其 reasoning 可能超出模型实际档位
    （例如某模型只支持 none，旧配置写了 high）。这类历史值按模型能力就近归一，
    而不是让整份配置失效——否则一次升级就会静默停掉用户的 Router。
    """
    raw_agents = document.get("agents")
    if raw_agents is None:
        raw_agents = []
    if not isinstance(raw_agents, list):
        raise AgentRoutingError("agents 必须是数组")
    profiles: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_agents):
        if not isinstance(raw, dict):
            raise AgentRoutingError(f"agents[{index}] 必须是对象")
        role = str(raw.get("role") or "").strip().upper()
        if role and role != ROLE_DEVELOPER:
            continue  # 新配置不再产生非 Developer 执行配置
        converted = {key: value for key, value in raw.items() if key != "role"}
        converted.setdefault("id", f"{raw.get('agent', 'agent')}-{raw.get('model', 'model')}")
        agent = str(converted.get("agent") or "").strip()
        model = str(converted.get("model") or "").strip()
        reasoning = str(converted.get("reasoning") or "").strip().lower()
        if agent and model and reasoning not in reasoning_options(capabilities, agent, model):
            converted["reasoning"] = _reasoning_default(capabilities, agent, model, reasoning)
        profiles.append(converted)
    return profiles


def parse_routing_document(
    document: Any, capabilities: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """校验并规范化配置，始终返回 v2 结构 {version, profiles}。"""
    if capabilities is None:
        capabilities = load_capabilities()
    if not isinstance(document, dict):
        raise AgentRoutingError("配置根节点必须是对象")
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise AgentRoutingError("version 必须是整数")
    if version not in SUPPORTED_VERSIONS:
        raise AgentRoutingError(
            f"不支持的 version: {version}；当前支持: {', '.join(str(v) for v in sorted(SUPPORTED_VERSIONS))}"
        )
    if version >= 2:
        raw_profiles = document.get("profiles")
        if raw_profiles is None:
            raw_profiles = document.get("agents") or []
    else:
        raw_profiles = _legacy_profiles(document, capabilities)
    if not isinstance(raw_profiles, list):
        raise AgentRoutingError("profiles 必须是数组")
    if len(raw_profiles) > MAX_PROFILES:
        raise AgentRoutingError(f"profiles 最多支持 {MAX_PROFILES} 项")
    profiles = [
        _normalize_profile(raw, index, capabilities)
        for index, raw in enumerate(raw_profiles)
    ]
    seen: set[str] = set()
    for entry in profiles:
        if entry["id"] in seen:
            raise AgentRoutingError(f"profiles.id 必须唯一，重复: {entry['id']}")
        seen.add(entry["id"])
    return {"version": CONFIG_VERSION, "profiles": profiles}


# ---------------------------------------------------------------------------
# 内置极简 YAML 子集：本配置只需要「标量 + 映射 + 映射列表」。
#
# 刻意不依赖 PyYAML：Agent Runtime（review-db.py）与 WebApp 可能运行在不带
# 第三方库的 Python 上，若依赖缺失会导致整个功能静默失效。内置解析器保证
# 两端在任意环境下行为一致；遇到子集之外的语法会明确报错，而不是误解析。
# ---------------------------------------------------------------------------

_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_INTEGER = re.compile(r"^-?\d+$")
_AMBIGUOUS_SCALAR = re.compile(r"^(?:true|false|null|~|-?\d+)$", re.IGNORECASE)
_QUOTE_TRIGGER = "-?:,[]{}#&*!|>'\"%@`"


def _strip_comment(line: str) -> str:
    """去掉行尾注释；引号内的 `#` 不算注释。"""
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) < 2 or text[0] != text[-1] or text[0] not in {'"', "'"}:
        return text
    inner = text[1:-1]
    if text[0] == "'":
        return inner.replace("''", "'")
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
    result: list[str] = []
    index = 0
    while index < len(inner):
        char = inner[index]
        if char == "\\" and index + 1 < len(inner):
            result.append(escapes.get(inner[index + 1], inner[index + 1]))
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _yaml_scalar(text: str) -> Any:
    """标量解析。yes/no/on/off 保持字符串，避免把 agent id 误判成布尔值。"""
    raw = text.strip()
    if raw == "" or raw in {"~", "null", "Null", "NULL"}:
        return None
    if raw[0] in {'"', "'"}:
        return _unquote(raw)
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if _INTEGER.match(raw):
        return int(raw)
    return raw


def _yaml_lines(text: str) -> list[tuple[int, str, int]]:
    lines: list[tuple[int, str, int]] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for number, raw in enumerate(normalized.split("\n"), 1):
        indent_prefix = raw[: len(raw) - len(raw.lstrip())]
        if "\t" in indent_prefix:
            raise AgentRoutingError(f"YAML 不支持 Tab 缩进（第 {number} 行）")
        content = _strip_comment(raw).rstrip()
        if not content.strip():
            continue
        lines.append((len(content) - len(content.lstrip(" ")), content.strip(), number))
    return lines


def _yaml_map(lines: list[tuple[int, str, int]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        level, content, number = lines[index]
        if level < indent:
            break
        if level > indent:
            raise AgentRoutingError(f"YAML 缩进不正确（第 {number} 行）")
        if content.startswith("-"):
            break
        key, separator, rest = content.partition(":")
        if not separator:
            raise AgentRoutingError(f"YAML 需要 `key: value` 形式（第 {number} 行）")
        name = _unquote(key.strip())
        if not name:
            raise AgentRoutingError(f"YAML 键不能为空（第 {number} 行）")
        rest = rest.strip()
        if rest:
            result[name] = _yaml_scalar(rest)
            index += 1
        else:
            index += 1
            # 块序列允许与父键同列（PyYAML 默认输出就是 `agents:` 后接同列 `- `），
            # 因此同列且以 `-` 开头的行也要作为本键的子块。
            child = (
                index < len(lines)
                and (lines[index][0] > indent
                     or (lines[index][0] == indent and lines[index][1].startswith("-")))
            )
            if child:
                result[name], index = _yaml_block(lines, index, lines[index][0])
            else:
                result[name] = None
    return result, index


def _yaml_list(lines: list[tuple[int, str, int]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        level, content, number = lines[index]
        if level < indent:
            break
        if level > indent:
            raise AgentRoutingError(f"YAML 缩进不正确（第 {number} 行）")
        if not content.startswith("-"):
            break
        rest = content[1:]
        if rest and not rest.startswith(" "):
            raise AgentRoutingError(f"YAML 列表项需要 `- ` 前缀（第 {number} 行）")
        rest = rest.strip()
        if not rest:
            index += 1
            if index < len(lines) and lines[index][0] > indent:
                value, index = _yaml_block(lines, index, lines[index][0])
                result.append(value)
            else:
                result.append(None)
            continue
        key, separator, _value = rest.partition(":")
        if not separator or not _BARE_KEY.match(key.strip()):
            result.append(_yaml_scalar(rest))
            index += 1
            continue
        # 列表项是映射：把首行与后续更深缩进的行合成子块，复用同一套映射解析。
        item_indent = indent + 2
        sub = [(item_indent, rest, number)]
        index += 1
        while index < len(lines) and lines[index][0] > indent:
            sub.append(lines[index])
            index += 1
        item, consumed = _yaml_map(sub, 0, item_indent)
        if consumed != len(sub):
            raise AgentRoutingError(f"YAML 列表项结构不正确（第 {number} 行）")
        result.append(item)
    return result, index


def _yaml_block(lines: list[tuple[int, str, int]], index: int, indent: int):
    if lines[index][1].startswith("-"):
        return _yaml_list(lines, index, indent)
    return _yaml_map(lines, index, indent)


def _yaml_load(text: str) -> Any:
    lines = _yaml_lines(text)
    if not lines:
        return None
    value, consumed = _yaml_block(lines, 0, lines[0][0])
    if consumed != len(lines):
        raise AgentRoutingError(f"YAML 结构不正确（第 {lines[consumed][2]} 行）")
    return value


def _yaml_quote(text: str) -> str:
    needs_quote = (
        text == ""
        or _AMBIGUOUS_SCALAR.match(text) is not None
        or text != text.strip()
        or text[0] in _QUOTE_TRIGGER
        or ": " in text
        or " #" in text
        or any(char in text for char in "\n\t\r")
    )
    if not needs_quote:
        return text
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _yaml_dump_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    return _yaml_quote(str(value))


def _yaml_dump(document: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in document.items():
        if key in {"agents", "profiles"} and isinstance(value, list):
            lines.append(f"{key}:")
            for entry in value:
                for position, (field, field_value) in enumerate(entry.items()):
                    lines.append(f"{'- ' if position == 0 else '  '}{field}: {_yaml_dump_scalar(field_value)}")
        else:
            lines.append(f"{key}: {_yaml_dump_scalar(value)}")
    return "\n".join(lines) + "\n"


def dump_routing_document(
    document: dict[str, Any], capabilities: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """把配置序列化为稳定、可人工编辑的 YAML。"""
    return _yaml_dump(parse_routing_document(document, capabilities))


def parse_routing_text(
    text: str, capabilities: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    return parse_routing_document(_yaml_load(text), capabilities)


def document_revision(document: dict[str, Any]) -> str:
    """配置内容的稳定指纹，用于缓存失效：配置一变，旧推荐就不再被沿用。"""
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class AgentRoutingSnapshot:
    """不可变运行时快照；每个 Issue 不重新读取磁盘。"""

    def __init__(
        self, status: str, profiles: tuple[dict[str, Any], ...] = (),
        version: int | None = None, path: Path | None = None, error: str | None = None,
        capabilities: dict[str, list[dict[str, Any]]] | None = None,
        revision: str | None = None,
    ) -> None:
        self.status = status
        self.profiles = profiles
        self.version = version
        self.path = path
        self.error = error
        self.capabilities = capabilities or {}
        self.revision = revision

    @property
    def enabled(self) -> bool:
        return self.status == "ENABLED"

    # 兼容旧调用名：执行配置现在就是 Dev Execution Profile。
    @property
    def agents(self) -> tuple[dict[str, Any], ...]:
        return self.profiles

    def profile(self, profile_id: Any) -> dict[str, Any] | None:
        """按 id 查找当前配置里的执行配置；不存在返回 None。"""
        wanted = str(profile_id or "").strip()
        if not wanted:
            return None
        return next((entry for entry in self.profiles if entry["id"] == wanted), None)

    def candidates(self, difficulty: int | None) -> list[dict[str, Any]]:
        """可被选为执行者的合法候选：enabled 且 level >= difficulty。

        assignment 下拉与 Inspector 选择都只应使用这一集合。
        """
        if not self.enabled or difficulty is None:
            return []
        return [
            dict(entry) for entry in self.profiles
            if entry["enabled"] and entry["level"] >= difficulty
        ]

    def recommend(self, difficulty: int | None, limit: int = 20) -> list[dict[str, Any]]:
        """返回 level >= difficulty 的候选执行配置；未启用或 difficulty 缺失时为空。

        推荐结果完全由当前快照推导，不依赖任何持久化快照字段，因此配置一变即失效。
        """
        if not self.enabled or difficulty is None:
            return []
        candidates = [
            entry for entry in self.profiles
            if entry["enabled"] and entry["level"] >= difficulty
        ]
        candidates.sort(key=lambda entry: (entry["level"], entry["agent"], entry["model"], entry["id"]))
        return [
            {
                "profileId": entry["id"],
                "agent": entry["agent"],
                "model": entry["model"],
                "reasoning": entry["reasoning"],
                "level": entry["level"],
            }
            for entry in candidates[:limit]
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "enabled": self.enabled,
            "version": self.version,
            "path": str(self.path) if self.path else None,
            "error": self.error,
            "revision": self.revision,
            "profiles": [dict(entry) for entry in self.profiles],
            "capabilities": {
                agent: [dict(model) for model in models]
                for agent, models in self.capabilities.items()
            },
            "fallbackReasonings": list(FALLBACK_REASONING),
            "levelScale": list(LEVEL_SCALE),
        }


def assignment_state(
    assignment: Any, difficulty: int | None, snapshot: AgentRoutingSnapshot | None = None,
) -> tuple[str, str | None]:
    """判定 assignment 当前是否仍然有效，返回 (status, reason)。

    判定完全基于「当前 Routing 配置 + 当前 difficulty」，不比较 routingRevision：
    配置整体被替换后如果 profile 内容恰好一致，assignment 仍然有效。
    本函数只判定，不修改任何数据，也不会自动改选其它执行者。
    """
    if not isinstance(assignment, dict) or not assignment.get("profileId"):
        return ASSIGNMENT_NONE, None
    current = snapshot if snapshot is not None else get_snapshot()
    if current is None or not current.enabled:
        return ASSIGNMENT_STALE, ASSIGNMENT_REASON_ROUTER_DISABLED
    profile = current.profile(assignment.get("profileId"))
    if profile is None:
        return ASSIGNMENT_STALE, ASSIGNMENT_REASON_PROFILE_MISSING
    if not profile["enabled"]:
        return ASSIGNMENT_STALE, ASSIGNMENT_REASON_PROFILE_DISABLED
    if (
        profile["agent"] != assignment.get("agent")
        or profile["model"] != assignment.get("model")
        or profile["reasoning"] != assignment.get("reasoning")
    ):
        return ASSIGNMENT_STALE, ASSIGNMENT_REASON_PROFILE_CHANGED
    if difficulty is not None and profile["level"] < difficulty:
        return ASSIGNMENT_STALE, ASSIGNMENT_REASON_LEVEL_BELOW_DIFFICULTY
    assigned_level = assignment.get("level")
    if isinstance(assigned_level, int) and profile["level"] < assigned_level:
        return ASSIGNMENT_STALE, ASSIGNMENT_REASON_LEVEL_REDUCED
    return ASSIGNMENT_VALID, None


def assignment_view(
    assignment: Any, difficulty: int | None,
    snapshot: AgentRoutingSnapshot | None = None,
) -> dict[str, Any]:
    """返回 assignment + 状态，供 Issue Context / WebApp 直接透出。"""
    status, reason = assignment_state(assignment, difficulty, snapshot)
    return {
        "assignment": assignment if isinstance(assignment, dict) else {},
        "assignmentStatus": status,
        "assignmentInvalidReason": reason,
    }


def load_routing_snapshot(path: Path, capabilities: dict[str, list[dict[str, Any]]] | None = None) -> AgentRoutingSnapshot:
    """从磁盘加载快照；文件不存在或配置非法都返回不可用快照，不抛异常。"""
    if capabilities is None:
        capabilities = load_capabilities(capability_path(_review_home()))
    if not path.exists():
        return AgentRoutingSnapshot(ROUTER_DISABLED, path=path, capabilities=capabilities)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return AgentRoutingSnapshot(ROUTER_INVALID, path=path, error=f"配置无法读取: {exc}", capabilities=capabilities)
    try:
        document = parse_routing_text(text, capabilities)
    except AgentRoutingError as exc:
        return AgentRoutingSnapshot(ROUTER_INVALID, path=path, error=str(exc), capabilities=capabilities)
    return AgentRoutingSnapshot(
        "ENABLED", tuple(document["profiles"]), version=document["version"], path=path,
        capabilities=capabilities, revision=document_revision(document),
    )


_current_snapshot: AgentRoutingSnapshot | None = None
_current_path: Path | None = None
_current_stamp: tuple[Any, ...] | None = None


def _source_stamp(path: Path) -> tuple[Any, ...]:
    """配置文件 + capability 的磁盘指纹，用于检测进程外的修改。"""
    parts: list[Any] = []
    for candidate in (path, capability_path(_review_home())):
        try:
            stat = candidate.stat()
            parts.append((str(candidate), stat.st_mtime_ns, stat.st_size))
        except OSError:
            parts.append((str(candidate), None, None))
    return tuple(parts)


def get_snapshot(path: Path | None = None) -> AgentRoutingSnapshot:
    """取得运行时快照。

    除路径变化外，还会比对磁盘指纹：配置被手工编辑或被其它进程改写时自动重载，
    避免长期运行的进程一直沿用过期配置（推荐结果因此不会错误沿用旧快照）。
    """
    global _current_snapshot, _current_path, _current_stamp
    resolved = path or routing_config_path(_review_home())
    stamp = _source_stamp(resolved)
    if _current_snapshot is None or _current_path != resolved or _current_stamp != stamp:
        snapshot = load_routing_snapshot(resolved)
        if (
            snapshot.status == ROUTER_INVALID
            and _current_snapshot is not None and _current_snapshot.enabled
            and _current_path == resolved
        ):
            # 配置在进程外被写坏时，继续沿用上一份有效配置。
            _current_stamp = stamp
            return _current_snapshot
        _current_snapshot = snapshot
        _current_path = resolved
        _current_stamp = stamp
    return _current_snapshot


def reload_snapshot(path: Path | None = None) -> AgentRoutingSnapshot:
    """保存配置成功后立即生效；失败时保留上一份有效快照。"""
    global _current_snapshot, _current_path, _current_stamp
    resolved = path or routing_config_path(_review_home())
    snapshot = load_routing_snapshot(resolved)
    if snapshot.status == ROUTER_INVALID and _current_snapshot is not None and _current_snapshot.enabled:
        # 不能让一份坏配置把现有 Router 弄挂。
        return _current_snapshot
    _current_snapshot = snapshot
    _current_path = resolved
    _current_stamp = _source_stamp(resolved)
    return snapshot


def reset_snapshot() -> None:
    """测试或配置路径切换时清理缓存。"""
    global _current_snapshot, _current_path, _current_stamp
    _current_snapshot = None
    _current_path = None
    _current_stamp = None
    _capability_cache.clear()


def routing_revision(path: Path | None = None) -> str | None:
    """当前生效配置的 revision；未启用 Router 时返回 None。"""
    return get_snapshot(path).revision


def recommend_executors(difficulty: int | None, path: Path | None = None) -> list[dict[str, Any]]:
    """运行时入口：未启用 Router 时直接返回空列表，主流程不受影响。"""
    return get_snapshot(path).recommend(difficulty)


def load_document(path: Path, capabilities: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """读取并规范化磁盘上的配置；文件不存在返回空配置。"""
    if not path.exists():
        return {"version": CONFIG_VERSION, "profiles": []}
    return parse_routing_text(path.read_text(encoding="utf-8"), capabilities)


def save_routing_document(
    document: Any, path: Path | None = None,
    capabilities: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any], AgentRoutingSnapshot]:
    """完整校验 → 写临时文件 → 原子替换 → reload 运行时快照。

    校验失败直接抛 AgentRoutingError，调用方保证现有文件不被修改。
    """
    normalized = parse_routing_document(document, capabilities)
    text = dump_routing_document(normalized, capabilities)
    resolved = path or routing_config_path(_review_home())
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.parent / f".{resolved.name}.saving-{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved)
    finally:
        if temporary.exists():
            temporary.unlink()
    return normalized, reload_snapshot(resolved)


def _unique_id(base: str, used: set[str]) -> str:
    candidate, suffix = base, 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def discovered_to_profiles(
    discovered: list[dict[str, Any]], capabilities: dict[str, list[dict[str, Any]]],
    used_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """把本机发现结果转换为候选 profile；同一个 Agent 可以产出多个 Model。"""
    used = used_ids if used_ids is not None else set()
    profiles: list[dict[str, Any]] = []
    for entry in discovered:
        if not isinstance(entry, dict):
            continue
        agent = str(entry.get("agent") or "").strip()
        model = str(entry.get("model") or "").strip()
        if not agent or not model:
            continue  # 无法确认模型的平台不写入，避免编造
        reasoning = _reasoning_default(capabilities, agent, model, entry.get("reasoning"))
        level = entry.get("level")
        if isinstance(level, bool) or level not in LEVEL_SCALE:
            level = DEFAULT_LEVEL
        # 模型名已经带平台前缀时不再重复，例如 claude-opus-5[1M] 不写成 claude-claude-…。
        label = re.sub(r"[^A-Za-z0-9._-]+", "-", agent).strip("-").lower() or "agent"
        base = model if model.lower().startswith(label) else f"{label}-{model}"
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-").lower()
        profiles.append({
            "id": _unique_id(str(entry.get("id") or slug or "profile"), used),
            "agent": agent,
            "model": model,
            "reasoning": reasoning,
            "level": level,
            "enabled": bool(entry.get("enabled", True)),
        })
    return profiles


def seed_document(
    discovered: list[dict[str, Any]],
    capabilities: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """把本机发现结果转换为完整配置文档（仍要通过完整校验）。"""
    catalog = capabilities if capabilities is not None else load_capabilities()
    return parse_routing_document(
        {"version": CONFIG_VERSION, "profiles": discovered_to_profiles(discovered, catalog)},
        catalog,
    )


def merge_seed_document(
    existing: dict[str, Any], discovered: list[dict[str, Any]],
    capabilities: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """把发现结果合并进现有配置，默认保护人工配置。

    只追加「现有配置里不存在的 Agent+Model+Reasoning 组合」；已有 profile 的
    id、level、enabled 一律保持原样，不会被 seed 覆盖。
    """
    catalog = capabilities if capabilities is not None else load_capabilities()
    current = parse_routing_document(existing, catalog)
    profiles = [dict(entry) for entry in current["profiles"]]
    used_ids = {entry["id"] for entry in profiles}
    existing_keys = {(entry["agent"], entry["model"], entry["reasoning"]) for entry in profiles}
    added: list[dict[str, Any]] = []
    for candidate in discovered_to_profiles(discovered, catalog, used_ids):
        key = (candidate["agent"], candidate["model"], candidate["reasoning"])
        if key in existing_keys:
            continue
        existing_keys.add(key)
        profiles.append(candidate)
        added.append(candidate)
    merged = parse_routing_document({"version": CONFIG_VERSION, "profiles": profiles}, catalog)
    return {"document": merged, "added": added, "kept": len(profiles) - len(added)}


def _review_home() -> Path:
    return Path(os.path.expandvars(os.path.expanduser(
        os.environ.get("AGENT_REVIEW_HOME", "~/.agent-review")
    ))).resolve()


def main(argv: list[str] | None = None) -> int:
    """命令行入口：status / seed / validate。

    只做本机配置文件读写，不触碰 Review DB，也没有角色权限概念。
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Agent Model Routing 配置工具")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="输出当前运行时快照")
    sub.add_parser("capabilities", help="输出 Skill 内置 Agent/Model 能力表")
    seed = sub.add_parser("seed", help="从本机已安装 Agent 生成候选配置")
    seed.add_argument("--write", action="store_true", help="写入 agent-routing.yml；省略时只打印")
    seed.add_argument(
        "--merge", action="store_true",
        help="与现有配置合并（只追加新条目，保留人工配置）；配置存在时的默认行为",
    )
    seed.add_argument(
        "--replace", action="store_true",
        help="用发现结果整体替换现有配置；必须显式指定，避免误覆盖人工配置",
    )
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(get_snapshot().as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "capabilities":
        print(json.dumps(load_capabilities(), ensure_ascii=False, indent=2))
        return 0

    from agent_discovery import discover_agents

    path = routing_config_path(_review_home())
    discovered = discover_agents()
    if not args.write:
        print(json.dumps(seed_document(discovered), ensure_ascii=False, indent=2))
        return 0

    if path.exists() and args.replace:
        normalized, snapshot = save_routing_document(seed_document(discovered), path)
        action = "replaced"
    elif path.exists():
        # 默认合并：绝不无提示覆盖人工配置。
        existing = load_document(path)
        merged = merge_seed_document(existing, discovered)
        normalized, snapshot = save_routing_document(merged["document"], path)
        action = "merged"
        print(json.dumps({
            "note": f"已保留 {merged['kept']} 条现有配置，追加 {len(merged['added'])} 条；"
                    "如需整体替换请显式使用 --replace",
        }, ensure_ascii=False), file=sys.stderr)
    else:
        normalized, snapshot = save_routing_document(seed_document(discovered), path)
        action = "created"
    print(json.dumps({
        "saved": str(path),
        "action": action,
        "profiles": len(normalized["profiles"]),
        "status": snapshot.status,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
