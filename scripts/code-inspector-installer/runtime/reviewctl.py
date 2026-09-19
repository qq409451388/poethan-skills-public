#!/usr/bin/env python3
"""reviewctl — Code Inspector 统一 CLI 入口。

固定格式：reviewctl <操作域> <动作> [位置参数] [动态参数]。

设计约束：
- 全局只暴露本命令；角色和 operator 由安装器私有适配器通过进程内受信入口传入，
  再用 agent-bindings.json 解析，不提供公开的 --agent 参数，环境变量也不能切换身份。
- 全部业务逻辑（handler、事务、权限、状态机、审计、事件）复用内部兼容层
  review_db.py：校验通过后把输入编译成 review-db 调用并在本进程执行，
  因此新旧入口对同一合法输入产生完全相同的数据库状态、Activity 和事件。
- 校验失败时一次性返回全部可安全判断的错误（ok: false + errors 数组），
  且不产生任何数据库写入。

--help、reviewctl schema 与运行时校验共用同一份命令注册表和 Schema 声明。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_review_db():
    """加载内部兼容层 review-db（仓库内为 review_db.py，安装后为 review-db.py）。"""
    import importlib.util

    here = Path(__file__).resolve().parent
    for candidate in (here / "review_db.py", here / "review-db.py"):
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("reviewctl_internal_review_db", candidate)
            module = importlib.util.module_from_spec(spec)
            sys.modules["reviewctl_internal_review_db"] = module
            assert spec.loader
            spec.loader.exec_module(module)
            return module
    raise ModuleNotFoundError("内部兼容层 review-db.py 不存在；请重新执行 Code Inspector 安装")


review_db = _load_review_db()  # noqa: E402

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


# ---------------------------------------------------------------------------
# 统一错误收集器
# ---------------------------------------------------------------------------

@dataclass
class CliError:
    path: str
    code: str
    message: str

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "code": self.code, "message": self.message}


class Errors:
    """按稳定顺序收集错误：参数解析 → Schema → 跨字段 → 权限/数据库状态。"""

    def __init__(self) -> None:
        self.items: list[CliError] = []

    def add(self, path: str, code: str, message: str) -> None:
        self.items.append(CliError(path, code, message))

    def extend(self, other: "Errors") -> None:
        self.items.extend(other.items)

    @property
    def ok(self) -> bool:
        return not self.items

    def report(self) -> dict[str, Any]:
        return {"ok": False, "errors": [item.to_json() for item in self.items]}


# ---------------------------------------------------------------------------
# JSON Schema 子集校验器：遍历全部错误，不在第一个错误处停止
# ---------------------------------------------------------------------------

_TYPE_NAMES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "null": type(None),
}


def _matches_type(value: Any, type_name: str) -> bool:
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    python = _TYPE_NAMES.get(type_name)
    if python is None:
        return True
    if python is bool:
        return isinstance(value, bool)
    if python is str:
        return isinstance(value, str)
    return isinstance(value, python) and not (
        python in (dict, list) and isinstance(value, bool)
    )


def _child_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _index_path(path: str, index: int) -> str:
    return f"{path}[{index}]"


def validate_schema(instance: Any, schema: dict[str, Any], path: str, errors: Errors) -> None:
    """支持 2020-12 子集：type/enum/const/required/properties/additionalProperties/
    items/minItems/uniqueItems/minLength/pattern/minimum/maximum/minProperties/
    anyOf/oneOf。对象和数组会遍历全部子项后继续，保证一次返回所有错误。"""
    if not isinstance(schema, dict):
        return

    if "anyOf" in schema or "oneOf" in schema:
        keyword = "anyOf" if "anyOf" in schema else "oneOf"
        matched = 0
        for candidate in schema[keyword]:
            probe = Errors()
            validate_schema(instance, candidate, path, probe)
            if probe.ok:
                matched += 1
        valid = matched >= 1 if keyword == "anyOf" else matched == 1
        if not valid:
            errors.add(path, "SCHEMA_ANY_OF" if keyword == "anyOf" else "SCHEMA_ONE_OF",
                       f"{path or '输入'} 不满足 {keyword} 中的任意一个分支" if keyword == "anyOf"
                       else f"{path or '输入'} 必须恰好匹配 oneOf 中的一个分支")
        return

    type_names = schema.get("type")
    if type_names is not None:
        names = [type_names] if isinstance(type_names, str) else list(type_names)
        if not any(_matches_type(instance, name) for name in names):
            expected = " 或 ".join(names)
            actual = type(instance).__name__
            errors.add(path, "SCHEMA_TYPE", f"{path or '输入'} 必须是 {expected}，实际是 {actual}")
            # 类型不符时无法继续做结构校验；独立字段不受影响。
            return

    if "const" in schema and instance != schema["const"]:
        errors.add(path, "SCHEMA_CONST", f"{path or '输入'} 必须等于 {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.add(path, "SCHEMA_ENUM",
                   f"{path or '输入'} 的值 {instance!r} 不在允许范围: {schema['enum']}")

    if isinstance(instance, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(instance.strip()) < min_length:
            errors.add(path, "SCHEMA_MIN_LENGTH", f"{path} 去除首尾空白后不能为空" if min_length == 1
                       else f"{path} 长度不足，至少 {min_length} 个字符")
        pattern = schema.get("pattern")
        if pattern is not None and not re.search(pattern, instance):
            errors.add(path, "SCHEMA_PATTERN", f"{path} 必须匹配模式 {pattern}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.add(path, "SCHEMA_MINIMUM", f"{path} 不能小于 {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.add(path, "SCHEMA_MAXIMUM", f"{path} 不能大于 {schema['maximum']}")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.add(_child_path(path, key), "SCHEMA_REQUIRED", f"缺少必填字段 {key}")
        additional = schema.get("additionalProperties")
        for key in sorted(instance):
            if key in properties:
                validate_schema(instance[key], properties[key], _child_path(path, key), errors)
            elif additional is False:
                errors.add(_child_path(path, key), "SCHEMA_ADDITIONAL_PROPERTY",
                           f"不允许未知字段 {key}")
            elif isinstance(additional, dict):
                validate_schema(instance[key], additional, _child_path(path, key), errors)
        min_properties = schema.get("minProperties")
        if min_properties is not None and len(instance) < min_properties:
            errors.add(path, "SCHEMA_MIN_PROPERTIES", f"{path} 至少需要 {min_properties} 个字段")

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(instance) < min_items:
            errors.add(path, "SCHEMA_MIN_ITEMS",
                       f"{path} 不能为空数组" if min_items == 1 else f"{path} 至少需要 {min_items} 项")
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for item in instance:
                if item in seen:
                    errors.add(path, "SCHEMA_UNIQUE_ITEMS", f"{path} 存在重复项: {item!r}")
                else:
                    seen.append(item)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                validate_schema(item, item_schema, _index_path(path, index), errors)


# ---------------------------------------------------------------------------
# 命令注册表：--help、schema 与运行时校验的唯一事实来源
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Positional:
    name: str
    kind: str = "str"            # str | int | @json
    legacy: str = ""             # 对应 review-db 的旗标名（不含 --）
    required: bool = True
    choices: tuple[str, ...] | None = None
    help: str = ""
    legacy_positional: bool = False  # review-db 侧也是位置参数（如 activity-get）


@dataclass(frozen=True)
class Option:
    name: str                    # 不含 -- 的新旗标名
    kind: str = "str"            # str | int | json | bool
    legacy: str = ""             # review-db 旗标名；默认同 name
    choices: tuple[str, ...] | None = None
    multiple: bool = False
    required: bool = False
    help: str = ""


@dataclass
class Command:
    domain: str
    action: str
    legacy: str                  # review-db 子命令名，同时是角色权限判断单位
    summary: str
    positionals: list[Positional] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)
    schema: dict[str, Any] | None = None   # 动态参数（@file / -）的 JSON Schema
    transform: Callable[[dict[str, Any], "Context"], dict[str, Any]] | None = None
    derive: Callable[..., dict[str, Any]] | None = None  # 需要读库的派生（如 stage review）

    @property
    def key(self) -> str:
        return f"{self.domain}-{self.action}"


class Context:
    """一次 reviewctl 调用的解析结果与身份上下文。"""

    def __init__(self, command: Command, values: dict[str, Any], payload: Any,
                 agent: str, operator_id: str | None) -> None:
        self.command = command
        self.values = values
        self.payload = payload
        self.agent = agent
        self.operator_id = operator_id


def _kebab(name: str) -> str:
    return name.replace("_", "-")


def default_transform(payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
    """默认动态参数映射：payload 字段名转 kebab-case 后直接变成 review-db 旗标。"""
    return {_kebab(key): value for key, value in payload.items()}


# ---------------------------------------------------------------------------
# 动态参数 Schema 声明
# ---------------------------------------------------------------------------

DIMENSIONS = sorted(review_db.ALLOWED_DIMENSIONS)
SEVERITIES = sorted(review_db.ALLOWED_SEVERITIES)
BENEFITS = sorted(review_db.ALLOWED_BENEFITS)
COSTS = sorted(review_db.ALLOWED_COSTS)
DISPOSITIONS = sorted(review_db.ALLOWED_DISPOSITIONS)
CONFIDENCES = sorted(review_db.ALLOWED_CONFIDENCE)
FINDING_LEVELS = list(review_db.FINDING_LEVELS)
TASK_STATUS_VALUES = sorted(review_db.TASK_STATUSES)
ISSUE_STATUS_VALUES = sorted(review_db.ALLOWED_TRANSITIONS)
ACTIVITY_TYPES = sorted(review_db.ALLOWED_ACTIVITY_TYPES)
DISCUSSION_TOPICS = sorted(review_db.DISCUSSION_TOPICS)
CANDIDATE_STATUSES = ["UNDER_REVIEW", "ACCEPTED", "REJECTED"]

_CODE_REFERENCE_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "required": ["file_path"],
    "properties": {
        "file_path": {"type": "string", "minLength": 1},
        "class_name": {"type": "string"},
        "method_name": {"type": "string"},
        "symbol": {"type": "string"},
        "line_start": {"type": "integer", "minimum": 1},
        "line_end": {"type": "integer", "minimum": 1},
        "code_excerpt": {"type": "string"},
    },
}

_ISSUE_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "dimension", "severity", "summary"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "dimension": {"type": "string", "enum": DIMENSIONS},
        "severity": {"type": "string", "enum": SEVERITIES},
        "summary": {"type": "string", "minLength": 1},
        "issue_key": {"type": "string", "pattern": "^RI-[0-9A-Za-z-]+$"},
        "parent_issue_id": {"type": "integer", "minimum": 1},
        "expected_outcome": {"type": "string"},
        "technical_note": {"type": "string"},
        "local_terms": {"type": "object"},
        "evidence": {"type": "array", "items": _CODE_REFERENCE_ITEM},
        "dedupe_key": {"type": "string", "minLength": 1},
        "difficulty": {"type": "integer", "minimum": 1},
        "difficulty_reason": {"type": "array", "items": {"type": "string"}},
        "remediation_benefit": {"type": "string", "enum": BENEFITS},
        "remediation_cost": {"type": "string", "enum": COSTS},
        "disposition": {"type": "string", "enum": DISPOSITIONS},
        "confidence": {"type": "string", "enum": CONFIDENCES},
        "description": {"type": "string"},
        "facts": {"type": "string"},
        "rationale": {"type": "string"},
        "trigger_conditions": {"type": "array", "items": {"type": "string"}},
        "potential_impact": {"type": "array", "items": {"type": "string"}},
        "impact_scope": {"type": "array", "items": {"type": "string"}},
        "estimated_change": {"type": "object"},
    },
}

ISSUE_CREATE_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl issue create 动态参数",
    "description": "创建单个 Issue；task_key 使用位置参数传递。",
    **_ISSUE_ITEM_SCHEMA,
    "examples": [{
        "title": "重复请求产生重复记录",
        "dimension": "functional_correctness",
        "severity": "high",
        "summary": "现象：并发重复提交订单会生成两条记录；影响：超卖风险",
        "expected_outcome": "重复请求只保留一条记录",
        "evidence": [{"file_path": "OrderService.java", "symbol": "place"}],
        "local_terms": {"下单": "OrderService.place"},
        "difficulty": 2,
    }],
}

ISSUE_CREATE_MANY_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl issue create-many 动态参数",
    "description": "批量创建 Issue 的 JSON 数组；每项字段与 issue create 相同，summary 必填。任务版本由命令自动创建。",
    "type": "array",
    "minItems": 1,
    "items": _ISSUE_ITEM_SCHEMA,
    "examples": [[
        {"title": "库存为 0 仍可提交订单", "dimension": "data_security", "severity": "critical",
         "summary": "库存校验缺失导致超卖"},
        {"title": "订单查询无索引", "dimension": "performance", "severity": "medium",
         "summary": "全表扫描导致查询超时"},
    ]],
}

ISSUE_EDIT_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl issue edit 动态参数",
    "description": "更新 Issue 正文；只传需要修改的字段。",
    "type": "object",
    "additionalProperties": False,
    "properties": {key: value for key, value in _ISSUE_ITEM_SCHEMA["properties"].items()
                   if key in {"title", "summary", "expected_outcome", "technical_note",
                              "local_terms", "description", "facts", "rationale"}},
    "examples": [{"summary": "更新后的现象与影响说明", "expected_outcome": "重复请求只保留一条记录"}],
}

ISSUE_RATE_MANY_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl issue rate-many 动态参数",
    "description": "批量更新评级；同一批次不得重复 issue_key。",
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "minProperties": 2,
        "required": ["issue_key"],
        "properties": {
            "issue_key": {"type": "string", "pattern": "^RI-[0-9A-Za-z-]+$"},
            "dimension": {"type": "string", "enum": DIMENSIONS},
            "severity": {"type": "string", "enum": SEVERITIES},
            "remediation_benefit": {"type": "string", "enum": BENEFITS},
            "remediation_cost": {"type": "string", "enum": COSTS},
            "disposition": {"type": "string", "enum": DISPOSITIONS},
            "confidence": {"type": "string", "enum": CONFIDENCES},
        },
    },
    "examples": [[{"issue_key": "RI-1", "severity": "high"}, {"issue_key": "RI-2", "dimension": "performance"}]],
}

ISSUE_STATUS_MANY_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl issue status-many 动态参数",
    "description": "批量更新 Issue 状态；每项必含 issue_key 和 status，可选 content 说明原因。",
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["issue_key", "status"],
        "properties": {
            "issue_key": {"type": "string", "pattern": "^RI-[0-9A-Za-z-]+$"},
            "status": {"type": "string", "enum": ISSUE_STATUS_VALUES},
            "content": {"type": "string"},
        },
    },
    "examples": [[{"issue_key": "RI-1", "status": "ON_HOLD", "content": "等待外部确认"}]],
}

DESIGN_SUBMIT_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl design submit 动态参数",
    "description": "提交设计方案；summary/content 使用具名参数，动态参数承载结构化声明。",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scope_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "summary", "impacts"],
                "properties": {
                    "id": {"type": "string", "pattern": "^SC-[A-Z0-9][A-Z0-9_-]{0,27}$"},
                    "summary": {"type": "string", "minLength": 1},
                    "impacts": {
                        "type": "array", "minItems": 1, "uniqueItems": True,
                        "items": {"type": "string", "enum": sorted(review_db.MATERIAL_DESIGN_IMPACTS)},
                    },
                },
            },
        },
        "code_reference": {"type": "array", "items": _CODE_REFERENCE_ITEM},
    },
    "examples": [{
        "scope_changes": [{"id": "SC-1", "summary": "新增影子表双写", "impacts": ["new_persistence"]}],
        "code_reference": [{"file_path": "OrderService.java", "symbol": "place"}],
    }],
}

DESIGN_CONFIRM_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl design confirm 动态参数",
    "description": "记录 Inspector 对范围外变化的确认回答。",
    "type": "object",
    "additionalProperties": False,
    "required": ["question", "answer", "summary", "impacts", "change_ids"],
    "properties": {
        "question": {"type": "string", "minLength": 1},
        "answer": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "impacts": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "change_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
    },
    "examples": [{
        "question": "是否接受新增影子表双写？", "answer": "接受，先双写再迁移",
        "summary": "接受影子表方案", "impacts": ["new_persistence", "data_migration"],
        "change_ids": ["SC-1"],
    }],
}

_STAGE_PLAN_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["stage_no", "title", "objective", "acceptance_criteria"],
    "properties": {
        "stage_no": {"type": "integer", "minimum": 1},
        "title": {"type": "string", "minLength": 1},
        "objective": {"type": "string", "minLength": 1},
        "acceptance_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "scope_change_ids": {"type": "array", "items": {"type": "string"}},
    },
}

DESIGN_APPROVE_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl design approve 动态参数",
    "description": "批准设计；execution_mode=staged 时必须提供 stages。content 为审批结论。",
    "type": "object",
    "additionalProperties": False,
    "required": ["content", "execution_mode", "confirmation"],
    "properties": {
        "content": {"type": "string", "minLength": 1},
        "execution_mode": {"type": "string", "enum": ["direct", "staged"]},
        "confirmation": {"type": "string", "enum": ["not-needed", "recorded"]},
        "confirmation_id": {"type": "integer", "minimum": 1},
        "confirmation_ids": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 1}},
        "stages": {"type": "array", "minItems": 1, "items": _STAGE_PLAN_ITEM},
    },
    "examples": [{
        "content": "方案满足边界和验收，批准实施",
        "execution_mode": "staged",
        "confirmation": "not-needed",
        "stages": [{"stage_no": 1, "title": "库存校验", "objective": "提交前校验库存",
                    "acceptance_criteria": ["库存为 0 时禁止提交订单"]}],
    }],
}

DESIGN_REJECT_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl design reject 动态参数",
    "description": "驳回设计；content 说明驳回原因和必改点。",
    "type": "object",
    "additionalProperties": False,
    "required": ["content"],
    "properties": {
        "content": {"type": "string", "minLength": 1},
        "confirmation": {"type": "string", "enum": ["not-needed", "recorded"]},
        "confirmation_id": {"type": "integer", "minimum": 1},
    },
    "examples": [{"content": "方案未回答幂等问题，驳回并要求补充重试边界设计"}],
}

STAGE_PREPARE_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl stage prepare 动态参数",
    "description": "声明 Stage 开发范围与历史保护项；存在历史 Stage 时 protected_behaviors 必填。",
    "type": "object",
    "additionalProperties": False,
    "required": ["change_scope", "change_reason"],
    "properties": {
        "change_scope": {
            "anyOf": [
                {"type": "array", "minItems": 1, "items": {"type": "string"}},
                {"type": "object", "minProperties": 1},
            ],
        },
        "change_reason": {"type": "string", "minLength": 1},
        "protected_behaviors": {"type": "array", "items": {"type": "string"}},
    },
    "examples": [{
        "change_scope": ["order-service", "OrderService.java"],
        "change_reason": "在提交入口增加库存校验",
        "protected_behaviors": ["库存为 0 时禁止提交订单"],
    }],
}

STAGE_SUBMIT_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl stage submit 动态参数",
    "description": "提交 Stage 实现；上一轮 BLOCKER/MUST finding 自动关联为已处理，测试证据在此提供。",
    "type": "object",
    "additionalProperties": False,
    "required": ["test_evidence"],
    "properties": {
        "code_reference": {"type": "array", "minItems": 1, "items": _CODE_REFERENCE_ITEM},
        "test_evidence": {
            "anyOf": [
                {"type": "array", "minItems": 1},
                {"type": "object", "minProperties": 1},
            ],
        },
        "resolved_findings": {"type": "array", "items": {"type": "string"}},
    },
    "examples": [{
        "code_reference": [{"file_path": "OrderService.java", "symbol": "place"}],
        "test_evidence": [{"type": "unit", "name": "testPlaceRejectZeroStock", "result": "PASS"}],
    }],
}

_STAGE_REVIEW_FINDING = {
    "type": "object",
    "additionalProperties": False,
    "required": ["level", "id", "summary"],
    "properties": {
        "level": {"type": "string", "enum": FINDING_LEVELS},
        "id": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "evidence": {},
        "risk": {"type": "string", "minLength": 1},
        "why_not_found_earlier": {"type": "string", "minLength": 1},
        "introduced_by_fix": {"type": "boolean"},
    },
}

_STAGE_REVIEW_CHECK = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "evidence"],
    "properties": {
        "status": {"type": "string", "enum": ["PASS", "FAIL"]},
        "evidence": {},
    },
}

STAGE_REVIEW_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl stage review 动态参数",
    "description": "结构化审核结果。decision、content、summary、plan_no 由命令从本数据和数据库状态自动推导；"
                   "验收项使用 stage review-template 生成的稳定 ID（AC-1、AC-2…），不需要复制验收标准原文。"
                   "findings 是单一数组，每项携带 level；无 findings、无历史 Stage 时可省略对应字段。",
    "type": "object",
    "additionalProperties": False,
    "required": ["current_acceptance"],
    "properties": {
        "current_acceptance": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "status", "evidence"],
                "properties": {
                    "id": {"type": "string", "pattern": "^AC-[1-9][0-9]*$"},
                    "criterion": {"type": "string", "minLength": 1},
                    "status": {"type": "string", "enum": ["PASS", "FAIL"]},
                    "evidence": {},
                },
            },
        },
        "findings": {"type": "array", "items": _STAGE_REVIEW_FINDING},
        "historical_regression": {
            "type": "object",
            "additionalProperties": _STAGE_REVIEW_CHECK,
        },
        "baseline": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verified_behaviors", "input_output_contracts", "business_semantics", "tests"],
            "properties": {
                "verified_behaviors": {"type": "array", "items": {"type": "string"}},
                "input_output_contracts": {"type": "array", "items": {"type": "string"}},
                "business_semantics": {"type": "array", "items": {"type": "string"}},
                "tests": {"type": "array", "items": {"type": "string"}},
            },
        },
        "_template": {
            "type": "object",
            "description": "review-template 生成的只读提示信息；提交时由命令忽略。",
        },
        "comment": {"type": "string", "description": "附加说明，追加到自动生成的验收结论之后"},
    },
    "examples": [{
        "current_acceptance": [
            {"id": "AC-1", "status": "PASS", "evidence": "testPlaceRejectZeroStock 通过"},
        ],
        "findings": [
            {"level": "MUST", "id": "F-1", "summary": "未处理并发重复提交",
             "evidence": "OrderService.place 无幂等键", "risk": "重复订单"},
        ],
        "baseline": {
            "verified_behaviors": ["库存为 0 时禁止提交订单"],
            "input_output_contracts": ["place(order) -> OrderResult"],
            "business_semantics": ["订单创建幂等"],
            "tests": ["testPlaceRejectZeroStock"],
        },
    }],
}

ACTIVITY_ADD_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl activity add 动态参数",
    "description": "追加 Activity 时的结构化代码定位。",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "code_reference": {"type": "array", "items": _CODE_REFERENCE_ITEM},
    },
    "examples": [{"code_reference": [{"file_path": "OrderService.java", "symbol": "place"}]}],
}

DECISION_ADD_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl decision add 动态参数",
    "description": "登记有效结论时的范围键和来源讨论。",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scope_key": {"type": "string"},
        "source_discussion_ids": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 1}},
    },
    "examples": [{"scope_key": "design:cache", "source_discussion_ids": [12, 15]}],
}

CANDIDATE_SUBMIT_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl candidate submit 动态参数",
    "description": "提交问题候选的结构化证据与建议评级。",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_key": {"type": "string"},
        "evidence": {"type": "array", "items": _CODE_REFERENCE_ITEM},
        "suggested_dimension": {"type": "string", "enum": DIMENSIONS},
        "suggested_severity": {"type": "string", "enum": SEVERITIES},
        "suggested_confidence": {"type": "string", "enum": CONFIDENCES},
    },
    "examples": [{"evidence": [{"file_path": "OrderService.java", "symbol": "place"}],
                  "suggested_dimension": "code_quality", "suggested_severity": "low"}],
}

HUMAN_ASK_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl human ask 动态参数",
    "description": "人工升级的选项、证据与推荐。",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "options": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "recommended_option": {"type": "string"},
    },
    "examples": [{
        "options": ["方案A：保留现库表并双写", "方案B：直接迁移"],
        "evidence": ["订单表存在无校验写入路径"],
        "recommended_option": "方案A：保留现库表并双写",
    }],
}

IMPL_SUBMIT_SCHEMA = {
    "$schema": DRAFT_2020_12,
    "title": "reviewctl impl submit 动态参数",
    "description": "最终实现提交的代码定位。",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "code_reference": {"type": "array", "items": _CODE_REFERENCE_ITEM},
    },
    "examples": [{"code_reference": [{"file_path": "OrderService.java", "symbol": "place"}]}],
}


# ---------------------------------------------------------------------------
# stage review / review-template 的派生逻辑
# ---------------------------------------------------------------------------

def _acceptance_ids(criterions: list[str]) -> dict[str, str]:
    """稳定验收项 ID：按 Stage 验收标准顺序编号 AC-1..AC-n。"""
    return {f"AC-{index}": text for index, text in enumerate(criterions, 1)}


def _stage_row(conn, issue_id: int, plan_no: int, stage_no: int):
    return conn.execute(
        "SELECT * FROM issue_stage WHERE issue_id = ? AND plan_no = ? AND stage_no = ?",
        (issue_id, plan_no, stage_no),
    ).fetchone()


def _approved_stage_nos(conn, issue_id: int, plan_no: int, stage_no: int) -> list[int]:
    rows = conn.execute(
        """SELECT stage_no FROM issue_stage
           WHERE issue_id = ? AND plan_no = ? AND stage_no < ? AND status = 'APPROVED'
           ORDER BY stage_no""",
        (issue_id, plan_no, stage_no),
    ).fetchall()
    return [int(row["stage_no"]) for row in rows]


def build_review_template(issue_key: str, stage_no: int) -> dict[str, Any]:
    """生成 stage review 动态模板：active plan、稳定验收 ID、历史 Stage、轮次和 baseline 骨架。"""
    with review_db.connect() as conn:
        issue = review_db.issue_row(conn, issue_key)
        plan_no = review_db.active_stage_plan_no(conn, issue["id"])
        if plan_no is None:
            raise RuntimeError("当前没有可验收的 Stage Plan")
        stage = _stage_row(conn, issue["id"], plan_no, stage_no)
        if not stage:
            raise KeyError(f"Stage 不存在: plan={plan_no}, stage={stage_no}")
        criterions = review_db.acceptance_items(stage["acceptance_criteria"])
        accepted_ids = _acceptance_ids(criterions)
        history_nos = _approved_stage_nos(conn, issue["id"], plan_no, stage_no)
        history_rows = conn.execute(
            """SELECT stage_no, title FROM issue_stage
               WHERE issue_id = ? AND plan_no = ? AND stage_no < ? AND status = 'APPROVED'
               ORDER BY stage_no""",
            (issue["id"], plan_no, stage_no),
        ).fetchall()
        previous_findings = review_db.loads(stage["review_findings_json"], {})
        known_ids = sorted({
            str(item.get("id"))
            for level in review_db.FINDING_LEVELS
            for item in previous_findings.get(level, [])
            if isinstance(item, dict) and item.get("id")
        })
    document: dict[str, Any] = {
        "_template": {
            "issue_key": issue_key,
            "plan_no": plan_no,
            "stage_no": stage_no,
            "review_round": int(stage["review_round"]) + 1,
            "historical_stages": [
                {"stage_no": int(row["stage_no"]), "title": row["title"]} for row in history_rows
            ],
            "open_blocking_findings": known_ids,
            "usage": "补全当前文件后直接提交；整案失效改用 reviewctl stage redesign。",
        },
        "findings": [],
        "current_acceptance": [
            {
                "id": accepted_id,
                "criterion": accepted_ids[accepted_id],
                "status": "PASS",
                "evidence": "",
            }
            for accepted_id in accepted_ids
        ],
        "baseline": {
            "verified_behaviors": [], "input_output_contracts": [],
            "business_semantics": [], "tests": [],
        },
    }
    if history_nos:
        document["historical_regression"] = {
            str(no): {"status": "PASS", "evidence": ""} for no in history_nos
        }
    return document


def generate_stage_review_content(
    issue_key: str, plan_no: int, stage_no: int, review_round: int, decision: str,
    findings_by_level: dict[str, list[dict[str, Any]]],
    historical: dict[str, dict[str, Any]],
    acceptance_pairs: list[tuple[str, str, str]],
    comment: str | None,
) -> str:
    """从结构化审核结果确定性生成验收结论 content。"""
    final = {"approved": "PASS", "rejected": "REJECT", "redesign": "REDESIGN"}[decision]
    lines = [f"Stage {stage_no}（plan {plan_no}）第 {review_round} 轮验收：{final}"]
    for level in review_db.FINDING_LEVELS:
        items = findings_by_level.get(level, [])
        if not items:
            lines.append(f"{level}：无")
            continue
        entries = []
        for item in items:
            entry = f"{item.get('id')}: {item.get('summary')}"
            if item.get("risk"):
                entry += f"（风险：{item['risk']}）"
            entries.append(entry)
        lines.append(f"{level}：" + "；".join(entries))
    if historical:
        checks = ", ".join(
            f"Stage {no} {check.get('status')}" for no, check in sorted(historical.items(), key=lambda kv: int(kv[0]))
        )
        lines.append(f"历史回归：{checks}")
    else:
        lines.append("历史回归：无历史 Stage")
    acceptance_notes = ", ".join(
        f"{accepted_id} {status}" for accepted_id, _, status in acceptance_pairs
    )
    lines.append(f"当前验收：{acceptance_notes}")
    if comment and comment.strip():
        lines.append(comment.strip())
    return "\n".join(lines)


def derive_stage_review(ctx: Context, errors: Errors) -> dict[str, Any]:
    """把结构化审核输入编译为 review-db stage-review 调用参数。

    decision、content、summary、plan_no 全部推导；验收项用稳定 ID 匹配，
    不要求逐字复制验收标准。所有检查只读数据库，失败时不产生写入。
    """
    issue_key: str = ctx.values.get("issue", "")
    stage_no: int = ctx.values.get("stage", 0)
    payload: dict[str, Any] = ctx.payload or {}
    redesign = ctx.command.action == "redesign"

    if not issue_key or not stage_no:
        return {}

    state_errors = Errors()
    with review_db.connect() as conn:
        issue = conn.execute(
            "SELECT id, issue_key, status, current_attempt_no FROM review_issue WHERE issue_key = ?",
            (issue_key,),
        ).fetchone()
        if not issue:
            errors.add("issue", "STATE_ISSUE_MISSING", f"问题不存在: {issue_key}")
            return {}
        if issue["status"] != "IN_PROGRESS":
            state_errors.add("issue", "STATE_ISSUE_STATUS",
                             f"Issue 状态 {issue['status']} 不允许验收 Stage")
        plan_no = review_db.active_stage_plan_no(conn, issue["id"])
        criterions: list[str] = []
        history_nos: list[int] = []
        review_round = 0
        known_finding_ids: set[str] = set()
        if plan_no is None:
            state_errors.add("plan", "STATE_PLAN_MISSING", "当前没有可验收的 Stage Plan")
            stage = None
        else:
            stage = _stage_row(conn, issue["id"], plan_no, stage_no)
            if not stage:
                state_errors.add("stage", "STATE_STAGE_MISSING",
                                 f"Stage 不存在: plan={plan_no}, stage={stage_no}")
            else:
                if stage["status"] != "PENDING_REVIEW":
                    state_errors.add("stage", "STATE_STAGE_STATUS",
                                     f"Stage {stage_no} 当前为 {stage['status']}，不能验收")
                criterions = review_db.acceptance_items(stage["acceptance_criteria"])
                review_round = int(stage["review_round"]) + 1
                activity_rows = conn.execute(
                    """SELECT metadata_json FROM issue_activity
                       WHERE issue_id = ? AND activity_type IN ('STAGE_APPROVED', 'STAGE_REJECTED')
                       ORDER BY id""",
                    (issue["id"],),
                ).fetchall()
                for activity in activity_rows:
                    metadata = review_db.loads(activity["metadata_json"], {})
                    if metadata.get("plan_no") != plan_no or metadata.get("stage_no") != stage_no:
                        continue
                    old_findings = metadata.get("review_result", {}).get("findings", {})
                    for level in review_db.FINDING_LEVELS:
                        for finding in old_findings.get(level, []):
                            if isinstance(finding, dict) and finding.get("id"):
                                known_finding_ids.add(str(finding["id"]))
            history_nos = _approved_stage_nos(conn, issue["id"], plan_no, stage_no)
        accepted_ids = _acceptance_ids(criterions)

    # --- 跨字段规则（全部收集，不提前退出；畸形条目只跳过自身深层校验） ---
    provided = [item for item in payload.get("current_acceptance", []) if isinstance(item, dict)]
    provided_ids = [str(item.get("id")) for item in provided]
    seen: set[str] = set()
    for index, accepted_id in enumerate(provided_ids):
        if accepted_id in seen:
            errors.add(f"current_acceptance[{index}].id", "CROSS_DUPLICATE_ID",
                       f"验收项 ID 重复: {accepted_id}")
        seen.add(accepted_id)
        item = provided[index]
        if not review_db.nonempty_evidence(item.get("evidence")):
            errors.add(f"current_acceptance[{index}].evidence", "CROSS_EVIDENCE_REQUIRED",
                       "验收项必须提供非空 evidence")
        criterion = item.get("criterion")
        if criterion is not None and accepted_id in accepted_ids and criterion != accepted_ids[accepted_id]:
            errors.add(f"current_acceptance[{index}].criterion", "CROSS_CRITERION_MISMATCH",
                       f"验收项 {accepted_id} 的说明已变化，请重新生成 review-template")
    unknown = [accepted_id for accepted_id in provided_ids if accepted_id not in accepted_ids]
    for accepted_id in unknown:
        errors.add("current_acceptance", "CROSS_UNKNOWN_ACCEPTANCE_ID",
                   f"未知验收项 ID: {accepted_id}；有效 ID: {', '.join(accepted_ids) or '无'}"
                   "（使用 stage review-template 获取稳定 ID）")
    missing = [accepted_id for accepted_id in accepted_ids if accepted_id not in seen]
    if missing:
        errors.add("current_acceptance", "CROSS_MISSING_ACCEPTANCE_ID",
                   f"缺少验收项: {', '.join(missing)}")

    findings = [item for item in payload.get("findings", []) if isinstance(item, dict)]
    findings_by_level: dict[str, list[dict[str, Any]]] = {level: [] for level in review_db.FINDING_LEVELS}
    finding_ids: set[str] = set()
    for index, finding in enumerate(findings):
        finding_id = str(finding.get("id"))
        if finding_id in finding_ids:
            errors.add(f"findings[{index}].id", "CROSS_DUPLICATE_ID", f"finding id 重复: {finding_id}")
        finding_ids.add(finding_id)
        level = finding.get("level")
        if level in findings_by_level:
            findings_by_level[level].append(finding)
            is_new = finding_id not in known_finding_ids
            if level in {"BLOCKER", "MUST"}:
                if not review_db.nonempty_evidence(finding.get("evidence")):
                    errors.add(f"findings[{index}].evidence", "CROSS_EVIDENCE_REQUIRED",
                               f"{level} {finding_id} 必须提供 evidence")
                if not str(finding.get("risk") or "").strip():
                    errors.add(f"findings[{index}].risk", "CROSS_RISK_REQUIRED",
                               f"{level} {finding_id} 必须说明实际 risk")
                if review_round >= 2 and is_new and not str(finding.get("why_not_found_earlier") or "").strip():
                    errors.add(f"findings[{index}].why_not_found_earlier", "CROSS_NEW_FINDING_REASON_REQUIRED",
                               f"第 {review_round} 轮新增 {level} 必须说明此前为何未发现")
            elif review_round >= 2 and is_new:
                if finding.get("introduced_by_fix") is not True:
                    errors.add(f"findings[{index}].introduced_by_fix", "CROSS_FIX_INTRODUCTION_REQUIRED",
                               f"第 {review_round} 轮新增 {level} 必须由本轮修复引入")
                if not review_db.nonempty_evidence(finding.get("evidence")):
                    errors.add(f"findings[{index}].evidence", "CROSS_EVIDENCE_REQUIRED",
                               f"第 {review_round} 轮新增 {level} 必须提供 evidence")

    historical_raw = payload.get("historical_regression", {})
    historical = {key: check for key, check in historical_raw.items() if isinstance(check, dict)} \
        if isinstance(historical_raw, dict) else {}
    for stage_id, check in historical.items():
        if not review_db.nonempty_evidence(check.get("evidence")):
            errors.add(f"historical_regression.{stage_id}.evidence", "CROSS_EVIDENCE_REQUIRED",
                       f"历史 Stage {stage_id} 必须提供非空 evidence")
    expected_history = {str(no) for no in history_nos}
    if set(historical) != expected_history:
        if not historical and expected_history:
            errors.add("historical_regression", "CROSS_MISSING_HISTORY",
                       "存在已批准历史 Stage，必须逐项提供 historical_regression: "
                       + ", ".join(sorted(expected_history, key=int)))
        else:
            errors.add("historical_regression", "CROSS_HISTORY_MISMATCH",
                       f"historical_regression 必须逐项覆盖已批准 Stage "
                       f"{sorted(expected_history, key=int) or '{}'}，实际为 {sorted(historical, key=int)}")

    historical_failed = any(check.get("status") == "FAIL" for check in historical.values())
    current_failed = any(item.get("status") == "FAIL" for item in provided)
    blocking = len(findings_by_level["BLOCKER"]) + len(findings_by_level["MUST"])
    if historical_failed and not findings_by_level["BLOCKER"]:
        errors.add("findings", "CROSS_HISTORY_FAIL_BLOCKER", "历史 Stage 回归失败必须至少记录一个 BLOCKER")
    if current_failed and blocking == 0:
        errors.add("findings", "CROSS_ACCEPTANCE_FAIL_BLOCKER",
                   "当前 Stage 验收失败必须记录对应 BLOCKER 或 MUST")

    passed = blocking == 0 and not historical_failed and not current_failed
    baseline = payload.get("baseline")
    if redesign:
        if not findings_by_level["BLOCKER"]:
            errors.add("findings", "CROSS_REDESIGN_BLOCKER",
                       "要求重新设计必须记录至少一个说明整案失效的 BLOCKER")
        decision = "redesign"
    elif passed:
        decision = "approved"
        if baseline is None:
            errors.add("baseline", "CROSS_BASELINE_REQUIRED",
                       "审核通过必须建立 baseline（verified_behaviors 与 tests 不能为空）")
        elif not baseline.get("verified_behaviors") or not baseline.get("tests"):
            errors.add("baseline", "CROSS_BASELINE_NONEMPTY",
                       "审核通过时 baseline.verified_behaviors 和 baseline.tests 不能为空")
    else:
        decision = "rejected"

    # 错误顺序稳定：跨字段规则先于数据库状态错误合并。
    errors.extend(state_errors)
    if errors.items:
        return {}

    review_result = {
        "findings": findings_by_level,
        "historical_regression": historical,
        # 按 Stage 验收标准顺序重排：调用方只需提供稳定 ID，顺序由命令恢复。
        "current_acceptance": [
            {"criterion": accepted_ids[str(item["id"])], "status": item["status"], "evidence": item["evidence"]}
            for accepted_id in accepted_ids
            for item in provided
            if str(item.get("id")) == accepted_id
        ],
    }
    id_by_criterion = {text: accepted_id for accepted_id, text in accepted_ids.items()}
    acceptance_pairs = [
        (id_by_criterion[accepted_ids[str(item["id"])]], accepted_ids[str(item["id"])], str(item["status"]))
        for item in provided
    ]
    content = generate_stage_review_content(
        issue_key, plan_no, stage_no, review_round, decision,
        findings_by_level, historical, acceptance_pairs, payload.get("comment"),
    )
    governance_version = int(stage["governance_version"])
    if redesign:
        legacy_decision = "redesign"
    else:
        legacy_decision = "auto" if governance_version >= 2 else decision
    flags: dict[str, Any] = {
        "decision": legacy_decision,
        "content": content,
        "review-result": review_db.dumps(review_result),
        "baseline": review_db.dumps(baseline or {}),
    }
    return flags


def derive_design_submit(ctx: Context, errors: Errors) -> dict[str, Any]:
    """没有范围外变化时不需要显式传空数组：默认声明 scope_changes=[]。"""
    payload = ctx.payload or {}
    return {
        "scope-changes": payload.get("scope_changes", []),
        "code-reference": payload.get("code_reference", []),
    }


def derive_design_approve(ctx: Context, errors: Errors) -> dict[str, Any]:
    """聚合设计审批中可在写库前判断的互斥参数错误。"""
    payload: dict[str, Any] = ctx.payload or {}
    mode = payload.get("execution_mode")
    confirmation = payload.get("confirmation")
    stages = payload.get("stages")
    confirmation_ids = list(payload.get("confirmation_ids") or [])
    if payload.get("confirmation_id") is not None:
        confirmation_ids.append(payload["confirmation_id"])
    if mode == "direct" and stages is not None:
        errors.add("decision.stages", "CROSS_DIRECT_STAGES_FORBIDDEN",
                   "execution_mode=direct 时不得提供 stages")
    issue_key = str(ctx.values.get("issue") or "")
    if issue_key and mode in {"direct", "staged"}:
        with review_db.connect() as conn:
            issue = review_db.issue_row(conn, issue_key)
            active_plan = review_db.active_stage_plan_no(conn, issue["id"])
        if mode == "direct" and active_plan is not None:
            errors.add("decision.execution_mode", "STATE_ACTIVE_PLAN_CONFLICT",
                       "当前已有 Stage Plan，不能按 direct 批准")
        if mode == "staged" and active_plan is None and not stages:
            errors.add("decision.stages", "CROSS_STAGES_REQUIRED",
                       "首次按 staged 批准时必须提供非空 stages")
        if mode == "staged" and active_plan is not None and stages is not None:
            errors.add("decision.stages", "STATE_ACTIVE_PLAN_DUPLICATE",
                       "当前已有 Stage Plan，不得重复提供 stages")
    if confirmation == "not-needed" and confirmation_ids:
        errors.add("decision.confirmation", "CROSS_CONFIRMATION_ID_FORBIDDEN",
                   "confirmation=not-needed 时不得提供 confirmation_id/confirmation_ids")
    if confirmation == "recorded" and not confirmation_ids:
        errors.add("decision.confirmation", "CROSS_CONFIRMATION_ID_REQUIRED",
                   "confirmation=recorded 时必须提供 confirmation_id 或 confirmation_ids")
    return {"decision": "approved", **_flag_map(payload, ctx)}


def derive_stage_prepare(ctx: Context, errors: Errors) -> dict[str, Any]:
    """默认从已批准 Stage 的 PASSED baseline 继承历史保护行为。"""
    payload: dict[str, Any] = ctx.payload or {}
    flags = {
        "change-scope": payload.get("change_scope"),
        "change-reason": payload.get("change_reason"),
    }
    if "protected_behaviors" in payload:
        flags["protected-behaviors"] = payload["protected_behaviors"]
        return flags

    issue_key = str(ctx.values.get("issue") or "")
    stage_no = int(ctx.values.get("stage") or 0)
    inherited: list[str] = []
    if issue_key and stage_no:
        with review_db.connect() as conn:
            issue = review_db.issue_row(conn, issue_key)
            plan_no = review_db.active_stage_plan_no(conn, issue["id"])
            if plan_no is not None:
                rows = conn.execute(
                    """SELECT baseline_json FROM issue_stage
                       WHERE issue_id = ? AND plan_no = ? AND stage_no < ?
                         AND status = 'APPROVED' AND baseline_status = 'PASSED'
                       ORDER BY stage_no""",
                    (issue["id"], plan_no, stage_no),
                ).fetchall()
                for row in rows:
                    baseline = review_db.loads(row["baseline_json"], {})
                    for behavior in baseline.get("verified_behaviors", []):
                        text = str(behavior).strip()
                        if text and text not in inherited:
                            inherited.append(text)
    flags["protected-behaviors"] = inherited
    return flags


def _issue_project_path(issue_key: str) -> Path | None:
    with review_db.connect() as conn:
        row = conn.execute(
            """SELECT task.project_path
               FROM review_issue issue
               JOIN review_task task ON task.id = issue.task_id
               WHERE issue.issue_key = ?""",
            (issue_key,),
        ).fetchone()
    if not row or not row["project_path"]:
        return None
    return Path(row["project_path"])


def _git_output(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=project, text=True, capture_output=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Git 命令失败")
    return result.stdout.strip()


def derive_stage_submit(ctx: Context, errors: Errors) -> dict[str, Any]:
    """自动关联 Git Diff 与上一轮 BLOCKER/MUST findings。"""
    issue_key: str = ctx.values.get("issue", "")
    stage_no: int = ctx.values.get("stage", 0)
    payload: dict[str, Any] = ctx.payload or {}
    flags: dict[str, Any] = {}
    if "code_reference" in payload:
        flags["code-reference"] = payload["code_reference"]
    if "test_evidence" in payload:
        flags["test-evidence"] = payload["test_evidence"]
    if "code_reference" not in payload and issue_key:
        project = _issue_project_path(issue_key)
        commit_sha = str(ctx.values.get("commit-sha") or "")
        if project is None or not project.is_dir():
            errors.add("issue", "STATE_PROJECT_MISSING", "Issue 所属项目目录不存在，无法自动关联 Git Diff")
        elif commit_sha:
            try:
                names = _git_output(project, "show", "--format=", "--name-only", "--no-renames", commit_sha)
                references = [{"file_path": name} for name in names.splitlines() if name.strip()]
                if not references:
                    errors.add("commit-sha", "STATE_DIFF_EMPTY", "该提交没有可关联的文件变更")
                else:
                    flags["code-reference"] = references
                if not ctx.values.get("diff-summary"):
                    flags["diff-summary"] = _git_output(
                        project, "show", "--stat", "--oneline", "--no-renames", commit_sha,
                    )
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                errors.add("commit-sha", "STATE_GIT_DIFF_FAILED", f"无法从提交自动关联 Git Diff: {exc}")
    resolved = payload.get("resolved_findings") or []
    if not issue_key or not stage_no or resolved:
        flags["resolved-findings"] = resolved
        return flags
    with review_db.connect() as conn:
        issue = review_db.issue_row(conn, issue_key)
        plan_no = review_db.active_stage_plan_no(conn, issue["id"])
        if plan_no is None:
            return flags
        stage = _stage_row(conn, issue["id"], plan_no, stage_no)
        if not stage:
            return flags
        previous = review_db.loads(stage["review_findings_json"], {})
        blocking_ids = sorted({
            str(item.get("id"))
            for level in ("BLOCKER", "MUST")
            for item in previous.get(level, [])
            if isinstance(item, dict) and item.get("id")
        })
    flags["resolved-findings"] = blocking_ids
    return flags


# ---------------------------------------------------------------------------
# 命令注册表
# ---------------------------------------------------------------------------

def _flag_map(payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
    return {_kebab(key): value for key, value in payload.items()}


def _p(name: str, kind: str = "str", legacy: str = "", required: bool = True,
       choices: tuple[str, ...] | None = None, help: str = "") -> Positional:
    return Positional(name, kind, legacy or _kebab(name), required, choices, help)


def _o(name: str, kind: str = "str", legacy: str = "", choices=None, multiple=False,
       required=False, help: str = "") -> Option:
    return Option(name, kind, legacy or _kebab(name), choices, multiple, required, help)


COMMANDS: list[Command] = [
    # ---- task ----
    Command("task", "list", "task-list", "列出治理任务",
            options=[_o("status", choices=tuple(TASK_STATUS_VALUES)), _o("project-name"),
                     _o("task-type", choices=("REVIEW", "CONTINUOUS")), _o("include-closed", kind="bool")]),
    Command("task", "show", "task-get", "查看单个任务",
            positionals=[_p("task", legacy="task-key")]),
    Command("task", "ensure", "task-resolve", "按稳定 identity 复用任务；不存在时创建",
            positionals=[_p("task", legacy="task-key", required=False)],
            options=[_o("title", required=True), _o("objective", required=True),
                     _o("review-level", choices=("L1", "L2", "L3"), required=True),
                     _o("review-scope", required=True), _o("task-type", choices=("REVIEW", "CONTINUOUS")),
                     _o("baseline-ref"), _o("remark")]),
    Command("task", "edit", "task-update", "修改任务标题、目标或备注",
            positionals=[_p("task", legacy="task-key")],
            options=[_o("title"), _o("objective"), _o("remark"), _o("close-reason")]),
    Command("task", "status", "task-update-status", "更新任务状态",
            positionals=[_p("task", legacy="task-key"), _p("status", choices=tuple(TASK_STATUS_VALUES))],
            options=[_o("started-at"), _o("finished-at"), _o("close-reason"), _o("remark")]),
    # ---- issue ----
    Command("issue", "list", "issue-list", "列出问题",
            options=[_o("task-key"), _o("status"), _o("severity"), _o("dimension"),
                     _o("updated-after"), _o("limit", kind="int"), _o("fields")]),
    Command("issue", "pending", "issue-list-pending-review", "列出待最终审核的实现",
            options=[_o("task-key"), _o("updated-after"), _o("limit", kind="int"), _o("fields")]),
    Command("issue", "show", "issue-get", "查看问题详情",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("view", choices=("compact", "full"))]),
    Command("issue", "context", "issue-context-get", "读取当前角色的有界 Working Set",
            positionals=[_p("issue", legacy="issue-key")]),
    Command("issue", "create", "issue-create", "创建单个问题；动态参数见 reviewctl schema issue-create",
            positionals=[_p("task", legacy="task-key"),
                         _p("data", kind="@json", help="Issue 数据 @文件 或 -")],
            schema=ISSUE_CREATE_SCHEMA),
    Command("issue", "create-many", "issue-create-batch", "批量创建问题并自动创建任务版本",
            positionals=[_p("task", legacy="task-key"),
                         _p("issues", kind="@json", help="Issue 数组 @文件 或 -")],
            options=[_o("reason", required=True)],
            schema=ISSUE_CREATE_MANY_SCHEMA),
    Command("issue", "edit", "issue-update-body", "更新问题正文",
            positionals=[_p("issue", legacy="issue-key"),
                         _p("body", kind="@json", required=False, help="正文更新 @文件 或 -")],
            schema=ISSUE_EDIT_SCHEMA),
    Command("issue", "rate", "issue-update-assessment", "更新单个问题评级",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("dimension", choices=tuple(DIMENSIONS)), _o("severity", choices=tuple(SEVERITIES)),
                     _o("remediation-benefit", choices=tuple(BENEFITS)),
                     _o("remediation-cost", choices=tuple(COSTS)),
                     _o("disposition", choices=tuple(DISPOSITIONS)),
                     _o("confidence", choices=tuple(CONFIDENCES))]),
    Command("issue", "rate-many", "issue-update-assessment-batch", "批量更新评级",
            positionals=[_p("updates", kind="@json", help="评级更新数组 @文件 或 -")],
            schema=ISSUE_RATE_MANY_SCHEMA),
    Command("issue", "status", "issue-update-status", "更新问题状态",
            positionals=[_p("issue", legacy="issue-key"), _p("status", choices=tuple(ISSUE_STATUS_VALUES))],
            options=[_o("content")]),
    Command("issue", "status-many", "issue-update-status-batch", "批量更新问题状态",
            positionals=[_p("updates", kind="@json", help="状态更新数组 @文件 或 -")],
            schema=ISSUE_STATUS_MANY_SCHEMA),
    Command("issue", "difficulty", "issue-set-difficulty", "设置问题困难程度",
            positionals=[_p("issue", legacy="issue-key"), _p("difficulty", kind="int")],
            options=[_o("reason", kind="json", legacy="difficulty-reason", help="解释要点 @文件 或 -")]),
    Command("issue", "assign", "issue-set-assignment", "选定或清除 Dev 执行配置",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("profile-id")]),
    # ---- design ----
    Command("design", "request", "design-request", "发起设计请求",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("content", required=True)]),
    Command("design", "submit", "design-submit", "提交设计方案",
            positionals=[_p("issue", legacy="issue-key"),
                         _p("design", kind="@json", required=False, help="范围变化与代码定位 @文件 或 -")],
            options=[_o("summary", required=True), _o("content", required=True)],
            schema=DESIGN_SUBMIT_SCHEMA, derive=derive_design_submit),
    Command("design", "preview", "design-preview", "预览设计范围差异",
            positionals=[_p("issue", legacy="issue-key"), _p("activity", kind="int", legacy="design-activity-id")]),
    Command("design", "confirm", "design-choice-record", "确认范围外变化",
            positionals=[_p("issue", legacy="issue-key"),
                         _p("activity", kind="int", legacy="design-activity-id"),
                         _p("choice", kind="@json", help="确认回答 @文件 或 -")],
            schema=DESIGN_CONFIRM_SCHEMA),
    Command("design", "approve", "design-review", "批准设计",
            positionals=[_p("issue", legacy="issue-key"),
                         _p("activity", kind="int", legacy="design-activity-id"),
                         _p("decision", kind="@json", help="审批动态参数 @文件 或 -")],
            schema=DESIGN_APPROVE_SCHEMA, derive=derive_design_approve),
    Command("design", "reject", "design-review", "驳回设计",
            positionals=[_p("issue", legacy="issue-key"),
                         _p("activity", kind="int", legacy="design-activity-id"),
                         _p("decision", kind="@json", help="驳回动态参数 @文件 或 -")],
            schema=DESIGN_REJECT_SCHEMA,
            transform=lambda payload, ctx: {"decision": "rejected", **_flag_map(payload, ctx)}),
    # ---- stage ----
    Command("stage", "list", "stage-list", "列出 Stage（默认 active plan）",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("plan-no", kind="int")]),
    Command("stage", "show", "stage-get", "查看 Stage 详情",
            positionals=[_p("issue", legacy="issue-key"), _p("stage", kind="int", legacy="stage-no")],
            options=[_o("plan-no", kind="int")]),
    Command("stage", "history", "stage-history-get", "查看 Stage 审核历史",
            positionals=[_p("issue", legacy="issue-key"), _p("stage", kind="int", legacy="stage-no")],
            options=[_o("plan-no", kind="int")]),
    Command("stage", "prepare", "stage-prepare", "声明开发范围与历史保护项",
            positionals=[_p("issue", legacy="issue-key"), _p("stage", kind="int", legacy="stage-no"),
                         _p("scope", kind="@json", help="范围声明 @文件 或 -")],
            schema=STAGE_PREPARE_SCHEMA, derive=derive_stage_prepare),
    Command("stage", "submit", "stage-submit", "提交 Stage 实现；自动关联 Diff 与上一轮 findings",
            positionals=[_p("issue", legacy="issue-key"), _p("stage", kind="int", legacy="stage-no"),
                         _p("evidence", kind="@json", help="测试证据 @文件 或 -")],
            options=[_o("content", required=True), _o("commit-sha", required=True), _o("diff-summary")],
            schema=STAGE_SUBMIT_SCHEMA, derive=derive_stage_submit),
    Command("stage", "review-template", "stage-review", "生成动态审核模板（稳定验收 ID）",
            positionals=[_p("issue", legacy="issue-key"), _p("stage", kind="int", legacy="stage-no"),
                         _p("output", help="模板输出文件；- 写入 stdout")]),
    Command("stage", "review", "stage-review", "提交结构化审核结果；decision/content/summary/plan_no 自动推导",
            positionals=[_p("issue", legacy="issue-key"), _p("stage", kind="int", legacy="stage-no"),
                         _p("review", kind="@json", help="审核结果 @文件 或 -")],
            schema=STAGE_REVIEW_SCHEMA, derive=derive_stage_review),
    Command("stage", "redesign", "stage-review", "整案失效：驳回并进入重设计",
            positionals=[_p("issue", legacy="issue-key"), _p("stage", kind="int", legacy="stage-no"),
                         _p("review", kind="@json", help="审核结果 @文件 或 -")],
            schema=STAGE_REVIEW_SCHEMA, derive=derive_stage_review),
    # ---- discussion ----
    Command("discussion", "list", "discussion-list", "列出讨论",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("topic", choices=tuple(DISCUSSION_TOPICS)), _o("since"),
                     _o("cursor", kind="int"), _o("limit", kind="int")]),
    Command("discussion", "show", "discussion-get", "查看单条讨论",
            positionals=[_p("issue", legacy="issue-key"), _p("discussion", kind="int", legacy="discussion-id")]),
    Command("discussion", "add", "discussion-append", "追加讨论",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("content", required=True),
                     _o("topic", choices=tuple(DISCUSSION_TOPICS))]),
    Command("discussion", "edit", "discussion-amend", "修正自己的讨论",
            positionals=[_p("issue", legacy="issue-key"), _p("discussion", kind="int", legacy="discussion-id")],
            options=[_o("content", required=True), _o("reason")]),
    # ---- activity ----
    Command("activity", "list", "activity-list", "列出 Issue 活动",
            positionals=[_p("issue", legacy="issue-key")]),
    Command("activity", "recent", "activity-list-recent", "最近活动（跨 Issue）",
            options=[_o("task-key"), _o("activity-type", choices=tuple(ACTIVITY_TYPES)),
                     _o("since"), _o("limit", kind="int")]),
    Command("activity", "show", "activity-get", "查看单条活动",
            positionals=[Positional("activity", kind="int", legacy="activity_id",
                                    legacy_positional=True)]),
    Command("activity", "revisions", "activity-revision-list", "查看活动修订历史",
            positionals=[_p("issue", legacy="issue-key"), _p("activity", kind="int", legacy="activity-id")]),
    Command("activity", "add", "activity-append", "追加活动",
            positionals=[_p("issue", legacy="issue-key"),
                         _p("refs", kind="@json", required=False, help="代码定位 @文件 或 -")],
            options=[_o("activity-type", required=True, choices=tuple(ACTIVITY_TYPES)),
                     _o("content", required=True), _o("summary"), _o("result-status"),
                     _o("attempt-no", kind="int")],
            schema=ACTIVITY_ADD_SCHEMA),
    Command("activity", "edit", "activity-amend", "修正尚未被消费的提交文案",
            positionals=[_p("issue", legacy="issue-key"), _p("activity", kind="int", legacy="activity-id")],
            options=[_o("content", required=True), _o("reason")]),
    # ---- decision ----
    Command("decision", "list", "decision-list", "列出有效结论",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("include-superseded", kind="bool")]),
    Command("decision", "add", "decision-record", "登记有效结论",
            positionals=[_p("issue", legacy="issue-key"),
                         _p("decision", kind="@json", required=False, help="范围键与来源讨论 @文件 或 -")],
            options=[_o("decision-type", required=True), _o("outcome", required=True),
                     _o("content", required=True)],
            schema=DECISION_ADD_SCHEMA),
    # ---- candidate ----
    Command("candidate", "list", "candidate-list", "列出问题候选",
            options=[_o("task-key"), _o("status", choices=tuple(CANDIDATE_STATUSES)),
                     _o("updated-after"), _o("limit", kind="int")]),
    Command("candidate", "submit", "candidate-submit", "提交问题候选",
            positionals=[_p("task", legacy="task-key"),
                         _p("candidate", kind="@json", required=False, help="证据与建议评级 @文件 或 -")],
            options=[_o("title", required=True), _o("description", required=True),
                     _o("facts", required=True), _o("rationale", required=True)],
            schema=CANDIDATE_SUBMIT_SCHEMA),
    Command("candidate", "accept", "candidate-update-status", "接受候选",
            positionals=[_p("candidate", legacy="candidate-key")], options=[_o("content")],
            transform=lambda payload, ctx: {"status": "ACCEPTED"}),
    Command("candidate", "reject", "candidate-update-status", "拒绝候选",
            positionals=[_p("candidate", legacy="candidate-key")], options=[_o("content")],
            transform=lambda payload, ctx: {"status": "REJECTED"}),
    # ---- human ----
    Command("human", "ask", "human-escalate", "升级人工决策",
            positionals=[_p("issue", legacy="issue-key"),
                         _p("ask", kind="@json", required=False, help="选项与证据 @文件 或 -")],
            options=[_o("reason", required=True), _o("question", required=True)],
            schema=HUMAN_ASK_SCHEMA),
    Command("human", "resolve", "human-confirmation-resolve", "提交最终人工决定",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("decision", required=True), _o("content", required=True),
                     _o("next-status", choices=("DESIGN_REQUIRED", "IN_PROGRESS", "ON_HOLD",
                                                "BLOCKED", "CANCELLED"))]),
    # ---- fast ----
    Command("fast", "pass", "fast-review-record", "FastMode 记录通过",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("content", required=True), _o("evidence")],
            transform=lambda payload, ctx: {"decision": "pass"}),
    Command("fast", "fail", "fast-review-record", "FastMode 记录失败",
            positionals=[_p("issue", legacy="issue-key")],
            options=[_o("content", required=True), _o("evidence")],
            transform=lambda payload, ctx: {"decision": "fail"}),
    # ---- impl ----
    Command("impl", "submit", "implementation-submit", "提交最终实现证据",
            positionals=[_p("issue", legacy="issue-key"),
                         _p("impl", kind="@json", required=False, help="代码定位 @文件 或 -")],
            options=[_o("content", required=True), _o("status-content")],
            schema=IMPL_SUBMIT_SCHEMA),
    # ---- watch ----
    Command("watch", "issue", "watch-probe", "观察 Issue 状态",
            positionals=[_p("target")], options=[_o("plan-no", kind="int")],
            transform=lambda payload, ctx: {"kind": "issue-status"}),
    Command("watch", "stage", "watch-probe", "观察 Stage 状态",
            positionals=[_p("target"), _p("stage", kind="int", legacy="stage-no")],
            options=[_o("plan-no", kind="int")],
            transform=lambda payload, ctx: {"kind": "stage-status"}),
    Command("watch", "activity", "watch-probe", "观察 Issue 新活动",
            positionals=[_p("target")],
            options=[_o("after-activity-id", kind="int"), _o("type", choices=tuple(ACTIVITY_TYPES),
                                                             legacy="activity-type", multiple=True)],
            transform=lambda payload, ctx: {"kind": "activity"}),
    Command("watch", "task", "watch-probe", "观察任务状态",
            positionals=[_p("target")],
            transform=lambda payload, ctx: {"kind": "task-status"}),
]

REGISTRY: dict[tuple[str, str], Command] = {(cmd.domain, cmd.action): cmd for cmd in COMMANDS}
DOMAIN_ORDER: list[str] = []
for _cmd in COMMANDS:
    if _cmd.domain not in DOMAIN_ORDER:
        DOMAIN_ORDER.append(_cmd.domain)

DOMAIN_SUMMARIES = {
    "task": "治理任务", "issue": "问题", "design": "设计", "stage": "阶段",
    "discussion": "讨论", "activity": "活动", "decision": "结论", "candidate": "问题候选",
    "human": "人工决策", "fast": "快速人工审核", "impl": "最终实现", "watch": "状态观察",
}


# ---------------------------------------------------------------------------
# 身份解析与权限
# ---------------------------------------------------------------------------

def resolve_identity(trusted_operator_id: str | None) -> tuple[str, str | None]:
    """从激活上下文解析角色与 operator；禁止公开切换身份的参数。

    developer/inspector 只能由安装器私有适配器调用 main 的受信参数传入，
    并在 agent-bindings.json 中校验角色绑定；公开 CLI 和环境变量不能切换身份。
    无激活上下文时拒绝执行运行时命令；help/schema 不需要身份。
    """
    alias = str(trusted_operator_id or "").strip()
    if not alias:
        raise PermissionError("缺少 Code Inspector 激活身份；请通过已安装 Skill 的固定工具入口执行")
    bindings_path = review_db.configured_home() / "config" / "agent-bindings.json"
    if not bindings_path.exists():
        raise PermissionError(f"角色绑定配置不存在: {bindings_path}，请重新执行安装")
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    binding = bindings.get(alias)
    if not binding:
        raise PermissionError(f"未注册的逻辑身份: {alias}")
    role = binding.get("role")
    if role not in {"inspector", "developer", "human"}:
        raise PermissionError(f"逻辑身份 {alias} 绑定了未知角色: {role}")
    return role, alias


def allowed_legacy_commands(agent: str) -> set[str] | None:
    """读取角色可执行命令集合；配置缺失时 developer/inspector 拒绝执行。"""
    config_path = review_db.configured_home() / "config" / "code-inspector.json"
    if not config_path.exists():
        return None
    config = json.loads(config_path.read_text(encoding="utf-8"))
    commands = config.get("roles", {}).get(agent, {}).get("commands")
    return set(commands) if isinstance(commands, list) else None


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def load_payload_argument(token: str, root_name: str, errors: Errors) -> Any:
    """动态参数只接受 @文件路径 或 -（stdin）。"""
    if token == "-":
        raw = sys.stdin.read()
        source = "stdin"
    elif token.startswith("@"):
        path = Path(token[1:]).expanduser()
        try:
            raw = path.read_text(encoding="utf-8")
            source = str(path)
        except OSError as exc:
            errors.add(root_name, "FILE_UNREADABLE", f"无法读取动态参数文件 {path}: {exc}")
            return None
    else:
        errors.add(root_name, "PARSE_PAYLOAD_SOURCE",
                   f"动态参数 {root_name} 必须使用 @文件路径 或 -（stdin），而不是内联值")
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        errors.add(root_name, "JSON_INVALID", f"{source} 不是合法 JSON: {exc}")
        return None


def parse_arguments(command: Command, tokens: list[str], errors: Errors) -> tuple[dict[str, Any], Any]:
    values: dict[str, Any] = {}
    payload: Any = None
    payload_token_seen = False
    positionals = [item for item in command.positionals]
    options_by_name = {f"--{opt.name}": opt for opt in command.options}
    index = 0
    seen_options: set[str] = set()

    def take_value(name: str) -> str | None:
        nonlocal index
        if index >= len(tokens):
            errors.add(name, "PARSE_MISSING_VALUE", f"{name} 需要一个值")
            return None
        value = tokens[index]
        index += 1
        return value

    def store(opt: Option, value: Any) -> None:
        if opt.multiple:
            values.setdefault(opt.name, []).append(value)
        else:
            values[opt.name] = value

    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            name, _, inline = token.partition("=")
            opt = options_by_name.get(name)
            index += 1
            if opt is None:
                errors.add(name, "PARSE_UNKNOWN_OPTION", f"未知参数 {name}")
                continue
            if name in seen_options and not opt.multiple:
                errors.add(name, "PARSE_DUPLICATE_OPTION", f"参数 {name} 重复")
            seen_options.add(name)
            if opt.kind == "bool":
                store(opt, True)
                continue
            raw = inline if "=" in token else take_value(name)
            if raw is None:
                continue
            if opt.kind == "int":
                try:
                    store(opt, int(raw))
                except ValueError:
                    errors.add(name, "PARSE_BAD_VALUE", f"{name} 必须是整数，实际是 {raw!r}")
            elif opt.kind == "json":
                loaded = load_json_option(opt, raw, errors)
                if loaded is not None:
                    store(opt, loaded)
            else:
                if opt.choices is not None and raw not in opt.choices:
                    errors.add(name, "PARSE_BAD_VALUE",
                               f"{name} 的值 {raw!r} 不在允许范围: {list(opt.choices)}")
                store(opt, raw)
            continue
        # 位置参数
        slot = next((item for item in positionals if item.name not in values), None)
        if slot is None:
            errors.add(f"[{index}]", "PARSE_EXTRA_POSITIONAL", f"多余的位置参数: {token}")
            index += 1
            continue
        index += 1
        if slot.kind == "@json":
            if not (token.startswith("@") or token == "-"):
                errors.add(slot.name, "PARSE_PAYLOAD_SOURCE",
                           f"动态参数 {slot.name} 必须使用 @文件路径 或 -（stdin）")
                continue
            if payload is not None:
                errors.add(slot.name, "PARSE_DUPLICATE_PAYLOAD", "动态参数只能提供一次")
                continue
            payload_token_seen = True
            payload = load_payload_argument(token, slot.name, errors)
            values[slot.name] = token
            continue
        if slot.kind == "int":
            try:
                values[slot.name] = int(token)
            except ValueError:
                errors.add(slot.name, "PARSE_BAD_VALUE", f"{slot.name} 必须是整数，实际是 {token!r}")
            continue
        if slot.choices is not None and token not in slot.choices:
            errors.add(slot.name, "PARSE_BAD_VALUE",
                       f"{slot.name} 的值 {token!r} 不在允许范围: {list(slot.choices)}")
            continue
        values[slot.name] = token

    for slot in positionals:
        if slot.required and slot.name not in values:
            errors.add(slot.name, "PARSE_MISSING_POSITIONAL", f"缺少位置参数 {slot.name}")
    for opt in command.options:
        if opt.required and opt.name not in values:
            errors.add(f"--{opt.name}", "PARSE_MISSING_OPTION", f"缺少必填参数 --{opt.name}")
    if command.schema is not None:
        payload_slots = [item for item in positionals if item.kind == "@json" and item.required]
        if payload_slots and payload is None and not payload_token_seen:
            errors.add(payload_slots[0].name, "PARSE_MISSING_PAYLOAD",
                       f"缺少动态参数 {payload_slots[0].name}（@文件路径 或 -）")
    return values, payload


def load_json_option(opt: Option, raw: str, errors: Errors) -> Any:
    token = raw.strip()
    if token.startswith("@"):
        try:
            return json.loads(Path(token[1:]).expanduser().read_text(encoding="utf-8"))
        except OSError as exc:
            errors.add(f"--{opt.name}", "FILE_UNREADABLE", f"无法读取文件 {token[1:]}: {exc}")
        except ValueError as exc:
            errors.add(f"--{opt.name}", "JSON_INVALID", f"{token[1:]} 不是合法 JSON: {exc}")
        return None
    if token == "-":
        try:
            return json.loads(sys.stdin.read())
        except ValueError as exc:
            errors.add(f"--{opt.name}", "JSON_INVALID", f"stdin 不是合法 JSON: {exc}")
        return None
    errors.add(f"--{opt.name}", "PARSE_PAYLOAD_SOURCE",
               f"--{opt.name} 是结构化数据，必须使用 @文件路径 或 -（stdin）")
    return None


# ---------------------------------------------------------------------------
# help 与 schema 输出（与运行时校验共用同一份注册表）
# ---------------------------------------------------------------------------

def print_global_help() -> None:
    print("reviewctl — Code Inspector 统一 CLI")
    print()
    print("用法: reviewctl <操作域> <动作> [位置参数] [动态参数]")
    print("      reviewctl schema <domain-action>   # 查看动态参数 JSON Schema")
    print("      reviewctl <域> <动作> --help       # 查看单个命令用法")
    print()
    print("操作域：")
    for domain in DOMAIN_ORDER:
        actions = ", ".join(
            cmd.action for cmd in COMMANDS if cmd.domain == domain
        )
        print(f"  {domain:<12} {DOMAIN_SUMMARIES.get(domain, '')}  动作: {actions}")
    print()
    print("复杂结构化数据统一使用 @文件路径 或 -（stdin）提供；")
    print("角色与逻辑身份来自当前激活上下文，不存在公开的身份切换参数。")


def print_domain_help(domain: str) -> int:
    commands = [cmd for cmd in COMMANDS if cmd.domain == domain]
    if not commands:
        print(json.dumps({"ok": False, "errors": [
            {"path": "domain", "code": "PARSE_UNKNOWN_DOMAIN", "message": f"未知操作域: {domain}"}
        ]}, ensure_ascii=False))
        return 2
    print(f"reviewctl {domain} — {DOMAIN_SUMMARIES.get(domain, '')}")
    print()
    for cmd in commands:
        usage = " ".join([f"<{p.name}>" if p.required else f"[<{p.name}>]" for p in cmd.positionals])
        print(f"  reviewctl {domain} {cmd.action} {usage}".rstrip())
        print(f"    {cmd.summary}")
        if cmd.schema is not None:
            print(f"    动态参数 Schema: reviewctl schema {cmd.key}")
    return 0


def command_usage(command: Command) -> str:
    parts = ["reviewctl", command.domain, command.action]
    for p in command.positionals:
        parts.append(f"<{p.name}>" if p.required else f"[<{p.name}>]")
    for opt in command.options:
        if opt.required:
            parts.append(f"--{opt.name} <{opt.kind if opt.kind != 'bool' else ''}>".replace(" <>", ""))
    return " ".join(parts)


def print_command_help(command: Command) -> None:
    print(f"{command_usage(command)}")
    print(f"  {command.summary}")
    print(f"  对应内部命令: {command.legacy}")
    print()
    if command.positionals:
        print("位置参数：")
        for p in command.positionals:
            note = {"@json": "动态参数：@文件路径 或 -", "int": "整数"}.get(p.kind, "")
            required = "" if p.required else "（可省略）"
            print(f"  {p.name:<14} {note} {p.help} {required}".rstrip())
    if command.options:
        print("具名参数：")
        for opt in command.options:
            kind = "" if opt.kind in ("str", "bool") else f"（{opt.kind}）"
            choices = f" 可选值: {list(opt.choices)}" if opt.choices else ""
            required = " 必填" if opt.required else ""
            print(f"  --{opt.name:<18}{kind}{required}{choices}")
    if command.schema is not None:
        required_fields = command.schema.get("required", [])
        if required_fields:
            print(f"动态参数必填字段: {', '.join(required_fields)}")
        print(f"完整 Schema: reviewctl schema {command.key}")
        examples = command.schema.get("examples") or []
        if examples:
            print("动态参数示例：")
            print(json.dumps(examples[0], ensure_ascii=False, indent=2))


def command_input_schema(command: Command) -> dict[str, Any]:
    """命令完整输入 Schema。

    带动态参数的命令直接返回动态参数 Schema（与运行时校验同一份声明）；
    简单命令则由位置参数和具名参数生成一份等价 Schema。
    """
    if command.schema is not None:
        return command.schema
    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in command.positionals:
        if p.kind == "@json":
            continue
        prop: dict[str, Any] = {"type": "integer"} if p.kind == "int" else {"type": "string"}
        if p.choices is not None:
            prop["enum"] = list(p.choices)
        properties[p.name] = prop
        if p.required:
            required.append(p.name)
    for opt in command.options:
        if opt.kind == "bool":
            properties[opt.name] = {"type": "boolean"}
        elif opt.kind == "int":
            properties[opt.name] = {"type": "integer"}
        elif opt.kind == "json":
            properties[opt.name] = {"description": "结构化数据，@文件路径 或 -"}
        else:
            prop = {"type": "string"}
            if opt.choices is not None:
                prop["enum"] = list(opt.choices)
            properties[opt.name] = prop
        if opt.required:
            required.append(opt.name)
    schema: dict[str, Any] = {
        "$schema": DRAFT_2020_12,
        "title": f"reviewctl {command.key} 输入",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def handle_schema_request(argument: str | None) -> int:
    if not argument:
        print("可用 Schema：")
        for cmd in COMMANDS:
            if cmd.schema is not None:
                print(f"  reviewctl schema {cmd.key:<24} {cmd.summary}")
        print("查看任意命令完整输入 Schema: reviewctl schema <domain-action>")
        return 0
    matched = [cmd for cmd in COMMANDS if cmd.key == argument]
    if not matched:
        print(json.dumps({"ok": False, "errors": [{
            "path": "schema", "code": "PARSE_UNKNOWN_COMMAND",
            "message": f"未知命令: {argument}；用 reviewctl schema 查看列表",
        }]}, ensure_ascii=False))
        return 2
    print(json.dumps(command_input_schema(matched[0]), ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# 编译为内部 review-db 调用并复用其全部业务逻辑
# ---------------------------------------------------------------------------

def compile_legacy_argv(ctx: Context, derived: dict[str, Any] | None) -> list[str]:
    command = ctx.command
    argv: list[str] = ["--agent", ctx.agent]
    if ctx.operator_id:
        argv += ["--operator-id", ctx.operator_id]
    argv.append(command.legacy)
    flags: dict[str, Any] = {}
    bare_positionals: list[str] = []

    for p in command.positionals:
        if p.kind == "@json" or p.name not in ctx.values:
            continue
        value = ctx.values[p.name]
        if value is None or value == "":
            continue
        if p.legacy_positional:
            bare_positionals.append(str(value))
        elif p.legacy:
            flags[p.legacy] = value

    for opt in command.options:
        if opt.name not in ctx.values:
            continue
        value = ctx.values[opt.name]
        if opt.kind == "bool":
            if value:
                flags[opt.legacy] = True
            continue
        if value is None or value == "":
            continue
        if opt.multiple and isinstance(value, list):
            for item in value:
                flags.setdefault(opt.legacy, []).append(item)
        else:
            flags[opt.legacy] = value

    if command.transform is not None:
        for key, value in command.transform(ctx.payload, ctx).items():
            flags[key] = value
    elif command.derive is not None:
        # 派生命令的 payload 已由 derive 编译为 legacy 参数，不再走默认映射。
        pass
    elif command.schema is not None and ctx.payload is not None:
        if isinstance(ctx.payload, list):
            slot = next((p for p in command.positionals if p.kind == "@json"), None)
            key = (slot.legacy or _kebab(slot.name)) if slot else "payload"
            argv += [f"--{key}", review_db.dumps(ctx.payload)]
        else:
            for key, value in default_transform(ctx.payload, ctx).items():
                flags[key] = value
    if derived:
        for key, value in derived.items():
            flags[key] = value

    repeated_flags = {opt.legacy for opt in command.options if opt.multiple}
    for key, value in flags.items():
        if value is True:
            argv.append(f"--{key}")
            continue
        if isinstance(value, list) and key in repeated_flags:
            for item in value:
                argv += [f"--{key}", item if isinstance(item, str) else review_db.dumps(item)]
            continue
        serialized = value if isinstance(value, str) else review_db.dumps(value)
        argv += [f"--{key}", serialized]
    argv.extend(bare_positionals)
    return argv


def run_compiled(ctx: Context, derived: dict[str, Any] | None) -> int:
    argv = compile_legacy_argv(ctx, derived)
    old_argv = sys.argv
    try:
        sys.argv = ["review-db.py", *argv]
        try:
            return review_db.main(propagate_errors=True)
        except SystemExit as exc:
            errors = Errors()
            errors.add("command", "RUNTIME_ARGUMENT_ERROR", f"内部命令参数编译失败: {exc}")
            return fail(errors)
        except Exception as exc:
            errors = Errors()
            errors.add("command", "RUNTIME_VALIDATION", str(exc))
            return fail(errors)
    finally:
        sys.argv = old_argv


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def fail(errors: Errors) -> int:
    print(json.dumps(errors.report(), ensure_ascii=False, indent=2))
    return 2


def main(argv: list[str] | None = None, trusted_operator_id: str | None = None) -> int:
    tokens = [token for token in (sys.argv[1:] if argv is None else argv)]
    wants_help = any(token in ("--help", "-h") for token in tokens)
    tokens = [token for token in tokens if token not in ("--help", "-h")]

    if not tokens:
        print_global_help()
        return 0
    if tokens[0] == "schema":
        return handle_schema_request(tokens[1] if len(tokens) > 1 else None)
    if len(tokens) == 1:
        return print_domain_help(tokens[0]) if not wants_help else print_domain_help(tokens[0])
    if wants_help and len(tokens) == 2:
        command = REGISTRY.get((tokens[0], tokens[1]))
        if not command:
            errors = Errors()
            errors.add("action", "PARSE_UNKNOWN_ACTION",
                       f"未知命令: {tokens[0]} {tokens[1]}；用 reviewctl {tokens[0]} --help 查看动作")
            return fail(errors)
        print_command_help(command)
        return 0

    domain, action, *rest = tokens
    command = REGISTRY.get((domain, action))
    errors = Errors()
    if not command:
        errors.add("action", "PARSE_UNKNOWN_ACTION",
                   f"未知命令: {domain} {action}；用 reviewctl --help 查看操作域")
        return fail(errors)

    # 阶段 1：参数解析（全部收集）
    values, payload = parse_arguments(command, rest, errors)

    # 阶段 2：Schema 校验（遍历全部错误）
    if command.schema is not None and payload is not None:
        root = next((p.name for p in command.positionals if p.kind == "@json"), "payload")
        if command.schema.get("type") == "array":
            if not isinstance(payload, list):
                errors.add(root, "SCHEMA_TYPE", f"{root} 必须是 JSON 数组")
            else:
                validate_schema(payload, command.schema, root, errors)
        else:
            if not isinstance(payload, dict):
                errors.add(root, "SCHEMA_TYPE", f"{root} 必须是 JSON 对象")
            else:
                validate_schema(payload, command.schema, root, errors)

    # 阶段 3+4：身份、权限与数据库状态
    agent: str | None = None
    operator_id: str | None = None
    auth_errors = Errors()
    try:
        agent, operator_id = resolve_identity(trusted_operator_id)
    except (PermissionError, RuntimeError, ValueError, OSError) as exc:
        auth_errors.add("identity", "PERM_IDENTITY", str(exc))
        agent = None
    if agent is not None:
        allowed = allowed_legacy_commands(agent)
        if allowed is None:
            if agent != "human":
                auth_errors.add("identity", "PERM_CONFIG_MISSING",
                                "角色权限配置不存在，请重新执行 Code Inspector 安装")
        elif command.legacy not in allowed:
            auth_errors.add("command", "PERM_COMMAND_FORBIDDEN",
                            f"{agent} 身份不允许执行 {command.legacy}（{command.domain} {command.action}）")

    if agent is None or not auth_errors.ok:
        errors.extend(auth_errors)
        return fail(errors)

    ctx = Context(command, values, payload, agent, operator_id)

    # stage review-template：只读生成动态模板
    if command.domain == "stage" and command.action == "review-template":
        if not errors.ok:
            return fail(errors)
        try:
            document = build_review_template(values["issue"], values["stage"])
        except Exception as exc:  # 只读命令，统一错误出口
            print(json.dumps({"ok": False, "errors": [
                {"path": "template", "code": "STATE_TEMPLATE_FAILED", "message": str(exc)}
            ]}, ensure_ascii=False))
            return 2
        output = values.get("output")
        text = json.dumps(document, ensure_ascii=False, indent=2)
        if output == "-":
            print(text)
        else:
            Path(output).expanduser().write_text(text + "\n", encoding="utf-8")
            print(json.dumps({"ok": True, "output": str(output),
                              "acceptance_ids": [item["id"] for item in document["current_acceptance"]],
                              "review_round": document["_template"]["review_round"],
                              "plan_no": document["_template"]["plan_no"]}, ensure_ascii=False))
        return 0

    derived: dict[str, Any] | None = None
    if command.derive is not None and (payload is None or isinstance(payload, dict)):
        # 跨字段与状态校验和 Schema 错误并行收集：畸形字段只跳过自身深层校验。
        try:
            derived = command.derive(ctx, errors)
        except Exception as exc:
            errors.add("command", "STATE_DERIVATION_FAILED", str(exc))
    if not errors.ok:
        return fail(errors)

    return run_compiled(ctx, derived)


if __name__ == "__main__":
    raise SystemExit(main())
