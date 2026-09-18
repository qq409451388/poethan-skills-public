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

import os
import re
from pathlib import Path
from typing import Any

SUPPORTED_VERSIONS = {1}
ALLOWED_ROLES = {"DEVELOPER", "INSPECTOR"}
ALLOWED_REASONING = {"minimal", "low", "medium", "high", "xhigh", "none"}
MAX_AGENTS = 200
MAX_LEVEL = 100

ROUTER_DISABLED = "DISABLED"
ROUTER_INVALID = "INVALID"


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
    override = os.environ.get("AGENT_ROUTING_CONFIG")
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override))).resolve()
    return (home / "config" / "agent-routing.yml").resolve()


def _require_text(entry: dict[str, Any], field: str, index: int) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AgentRoutingError(f"agents[{index}].{field} 必须是非空字符串")
    return value.strip()


def _require_bool(entry: dict[str, Any], field: str, index: int, default: bool) -> bool:
    value = entry.get(field, default)
    if not isinstance(value, bool):
        raise AgentRoutingError(f"agents[{index}].{field} 必须是布尔值")
    return value


def _normalize_entry(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentRoutingError(f"agents[{index}] 必须是对象")
    agent_id = _require_text(raw, "id", index)
    agent = _require_text(raw, "agent", index)
    model = _require_text(raw, "model", index)
    role = str(_require_text(raw, "role", index)).upper()
    if role not in ALLOWED_ROLES:
        raise AgentRoutingError(
            f"agents[{index}].role 无效: {role}；可选值: {', '.join(sorted(ALLOWED_ROLES))}"
        )
    reasoning = str(_require_text(raw, "reasoning", index)).lower()
    if reasoning not in ALLOWED_REASONING:
        raise AgentRoutingError(
            f"agents[{index}].reasoning 无效: {reasoning}；可选值: {', '.join(sorted(ALLOWED_REASONING))}"
        )
    level = raw.get("level")
    if isinstance(level, bool) or not isinstance(level, int):
        raise AgentRoutingError(f"agents[{index}].level 必须是整数")
    if not 1 <= level <= MAX_LEVEL:
        raise AgentRoutingError(f"agents[{index}].level 必须在 1 到 {MAX_LEVEL} 之间")
    return {
        "id": agent_id,
        "agent": agent,
        "role": role,
        "model": model,
        "reasoning": reasoning,
        "level": level,
        "enabled": _require_bool(raw, "enabled", index, True),
    }


def parse_routing_document(document: Any) -> dict[str, Any]:
    """校验并规范化 YAML 文档，返回 {version, agents}。任何问题都抛 AgentRoutingError。"""
    if not isinstance(document, dict):
        raise AgentRoutingError("配置根节点必须是对象")
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise AgentRoutingError("version 必须是整数")
    if version not in SUPPORTED_VERSIONS:
        raise AgentRoutingError(
            f"不支持的 version: {version}；当前支持: {', '.join(str(v) for v in sorted(SUPPORTED_VERSIONS))}"
        )
    raw_agents = document.get("agents")
    if raw_agents is None:
        raw_agents = []
    if not isinstance(raw_agents, list):
        raise AgentRoutingError("agents 必须是数组")
    if len(raw_agents) > MAX_AGENTS:
        raise AgentRoutingError(f"agents 最多支持 {MAX_AGENTS} 项")
    agents = [_normalize_entry(raw, index) for index, raw in enumerate(raw_agents)]
    seen: set[str] = set()
    for entry in agents:
        if entry["id"] in seen:
            raise AgentRoutingError(f"agents.id 必须唯一，重复: {entry['id']}")
        seen.add(entry["id"])
    return {"version": version, "agents": agents}


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
        if key == "agents" and isinstance(value, list):
            lines.append("agents:")
            for entry in value:
                for position, (field, field_value) in enumerate(entry.items()):
                    lines.append(f"{'- ' if position == 0 else '  '}{field}: {_yaml_dump_scalar(field_value)}")
        else:
            lines.append(f"{key}: {_yaml_dump_scalar(value)}")
    return "\n".join(lines) + "\n"


def dump_routing_document(document: dict[str, Any]) -> str:
    """把配置序列化为稳定、可人工编辑的 YAML。"""
    return _yaml_dump(parse_routing_document(document))


def parse_routing_text(text: str) -> dict[str, Any]:
    return parse_routing_document(_yaml_load(text))


class AgentRoutingSnapshot:
    """不可变运行时快照；每个 Issue 不重新读取磁盘。"""

    def __init__(
        self, status: str, agents: tuple[dict[str, Any], ...] = (), version: int | None = None,
        path: Path | None = None, error: str | None = None,
    ) -> None:
        self.status = status
        self.agents = agents
        self.version = version
        self.path = path
        self.error = error

    @property
    def enabled(self) -> bool:
        return self.status == "ENABLED"

    def recommend(self, difficulty: int | None, role: str = "DEVELOPER", limit: int = 20) -> list[dict[str, Any]]:
        """返回 level >= difficulty 的候选执行配置摘要；未启用或 difficulty 缺失时返回空列表。"""
        if not self.enabled or difficulty is None:
            return []
        wanted = str(role).upper()
        candidates = [
            entry for entry in self.agents
            if entry["enabled"] and entry["role"] == wanted and entry["level"] >= difficulty
        ]
        candidates.sort(key=lambda entry: (entry["level"], entry["agent"], entry["model"], entry["id"]))
        return [
            {
                "agent": entry["agent"],
                "model": entry["model"],
                "reasoning": entry["reasoning"],
                "level": entry["level"],
                "id": entry["id"],
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
            "agents": [dict(entry) for entry in self.agents],
        }


def load_routing_snapshot(path: Path) -> AgentRoutingSnapshot:
    """从磁盘加载快照；文件不存在或配置非法都返回不可用快照，不抛异常。"""
    if not path.exists():
        return AgentRoutingSnapshot(ROUTER_DISABLED, path=path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return AgentRoutingSnapshot(ROUTER_INVALID, path=path, error=f"配置无法读取: {exc}")
    try:
        document = parse_routing_text(text)
    except AgentRoutingError as exc:
        return AgentRoutingSnapshot(ROUTER_INVALID, path=path, error=str(exc))
    return AgentRoutingSnapshot(
        "ENABLED", tuple(document["agents"]), version=document["version"], path=path,
    )


_current_snapshot: AgentRoutingSnapshot | None = None
_current_path: Path | None = None


def get_snapshot(path: Path | None = None) -> AgentRoutingSnapshot:
    """取得运行时快照；路径变化或尚未加载时按需从磁盘加载一次。"""
    global _current_snapshot, _current_path
    resolved = path or routing_config_path(_review_home())
    if _current_snapshot is None or _current_path != resolved:
        _current_snapshot = load_routing_snapshot(resolved)
        _current_path = resolved
    return _current_snapshot


def reload_snapshot(path: Path | None = None) -> AgentRoutingSnapshot:
    """保存配置成功后立即生效；失败时保留上一份有效快照。"""
    global _current_snapshot, _current_path
    resolved = path or routing_config_path(_review_home())
    snapshot = load_routing_snapshot(resolved)
    if snapshot.status == ROUTER_INVALID and _current_snapshot is not None and _current_snapshot.enabled:
        # 不能让一份坏配置把现有 Router 弄挂。
        return _current_snapshot
    _current_snapshot = snapshot
    _current_path = resolved
    return snapshot


def reset_snapshot() -> None:
    """测试或配置路径切换时清理缓存。"""
    global _current_snapshot, _current_path
    _current_snapshot = None
    _current_path = None


def recommend_executors(difficulty: int | None, path: Path | None = None) -> list[dict[str, Any]]:
    """运行时入口：未启用 Router 时直接返回空列表，主流程不受影响。"""
    return get_snapshot(path).recommend(difficulty)


def save_routing_document(document: Any, path: Path | None = None) -> tuple[dict[str, Any], AgentRoutingSnapshot]:
    """完整校验 → 写临时文件 → 原子替换 → reload 运行时快照。

    校验失败直接抛 AgentRoutingError，调用方保证现有文件不被修改。
    """
    normalized = parse_routing_document(document)
    text = dump_routing_document(normalized)
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


def seed_document(discovered: list[dict[str, Any]]) -> dict[str, Any]:
    """把「本机 Agent 发现结果」转换为候选配置文档。

    发现结果只提供 model/reasoning 等事实；id 去重与 level 兜底在这里补齐，
    之后仍然要经过 parse_routing_document 的完整校验。
    """
    agents: list[dict[str, Any]] = []
    used: set[str] = set()
    for entry in discovered:
        if not isinstance(entry, dict):
            continue
        agent = str(entry.get("agent") or "").strip()
        model = str(entry.get("model") or "").strip()
        if not agent or not model:
            continue  # 无法确认模型的平台不写入，避免编造
        base = str(entry.get("id") or f"{agent}-developer").strip() or f"{agent}-developer"
        candidate_id, suffix = base, 2
        while candidate_id in used:
            candidate_id = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate_id)
        reasoning = str(entry.get("reasoning") or "high").strip().lower()
        if reasoning not in ALLOWED_REASONING:
            reasoning = "high"
        level = entry.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= MAX_LEVEL:
            level = 3
        agents.append({
            "id": candidate_id,
            "agent": agent,
            "role": "DEVELOPER",
            "model": model,
            "reasoning": reasoning,
            "level": level,
            "enabled": bool(entry.get("enabled", True)),
        })
    return parse_routing_document({"version": 1, "agents": agents})


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
    seed = sub.add_parser("seed", help="从本机已安装 Agent 生成候选配置")
    seed.add_argument("--write", action="store_true", help="直接写入 agent-routing.yml；省略时只打印")
    seed.add_argument("--force", action="store_true", help="已存在配置时也覆盖（默认拒绝覆盖）")
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(get_snapshot().as_dict(), ensure_ascii=False, indent=2))
        return 0

    from agent_discovery import discover_agents

    document = seed_document(discover_agents())
    path = routing_config_path(_review_home())
    if not args.write:
        print(json.dumps(document, ensure_ascii=False, indent=2))
        return 0
    if path.exists() and not args.force:
        print(json.dumps({
            "error": f"配置已存在，未覆盖: {path}；确认要替换时使用 --force",
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    normalized, snapshot = save_routing_document(document, path)
    print(json.dumps({
        "saved": str(path),
        "agents": len(normalized["agents"]),
        "status": snapshot.status,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
