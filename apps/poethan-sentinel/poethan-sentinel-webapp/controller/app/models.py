from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class APIModel(BaseModel):
    model_config = ConfigDict(alias_generator=camel, populate_by_name=True, extra="ignore")


class AuthenticationKind(str, Enum):
    alias = "alias"
    key = "key"
    password = "password"
    demo = "demo"


class ServerProfile(APIModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    authentication: AuthenticationKind = AuthenticationKind.alias
    alias: str = ""
    host: str = ""
    user: str = ""
    port: int = 22
    identity_file: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ServerInput(ServerProfile):
    password: str | None = None


class ConnectionTestInput(APIModel):
    server: ServerInput
    accept_host_key: bool = False


class ConnectionTestResult(APIModel):
    ok: bool
    message: str
    target: str = ""
    latency_ms: int | None = None
    host_key_required: bool = False
    host_key_changed: bool = False
    fingerprint: str | None = None


class AIProfile(APIModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = "默认配置"
    endpoint: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"


class ApplicationSettings(APIModel):
    plugin_directory: str
    developer_mode: bool = False
    demo_mode: bool = True
    ai_profiles: list[AIProfile] = Field(default_factory=lambda: [AIProfile(id="default", name="DeepSeek 默认")])
    active_ai_id: str = "default"
    # 由后端按钥匙串实际状态回填，前端只读；持久化值仅是快照。
    ai_configured: dict[str, bool] = Field(default_factory=dict)


class SettingsInput(APIModel):
    plugin_directory: str
    developer_mode: bool = False
    demo_mode: bool = True


class AIProfilesInput(APIModel):
    profiles: list[AIProfile]
    active_ai_id: str = ""
    # 仅包含需要写入或更新的条目；不在 profiles 里的旧配置对应的 Key 会被清理。
    api_keys: dict[str, str] = Field(default_factory=dict)


class AIConnectionInput(APIModel):
    endpoint: str
    model: str
    api_key: str | None = None
    profile_id: str | None = None


class PluginTrust(APIModel):
    status: Literal["trusted", "local", "unsigned", "untrusted", "invalid"]
    publisher_id: str | None = None
    key_id: str | None = None
    fingerprint: str | None = None
    lock_digest: str | None = None
    message: str


class PluginPackage(APIModel):
    id: str
    name: str
    description: str = ""
    version: str
    tool_type: Literal["plugin", "local_script", "server_script"] = "plugin"
    entrypoint: str
    language: str = "bash"
    output_limit: int = 1_000_000
    default_mode: str
    modes: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    report: dict[str, Any] | None = None
    # 插件对 AI 分析的声明，例如 {"problemAnalysis": false} 表示只要结论和摘要。
    ai: dict[str, Any] = Field(default_factory=dict)
    permissions: dict[str, bool] = Field(default_factory=dict)
    directory: str
    trust: PluginTrust
    valid: bool = True
    errors: list[str] = Field(default_factory=list)


class ServerScriptToolInput(APIModel):
    name: str
    description: str = ""
    runtime: Literal["bash", "python"]
    script_path: str


class PluginScanItem(APIModel):
    directory: str
    valid: bool
    plugin: PluginPackage | None = None
    errors: list[str] = Field(default_factory=list)


class PluginScanResponse(APIModel):
    items: list[PluginScanItem]
    valid_count: int
    invalid_count: int


class RunRequest(APIModel):
    server_id: str
    plugin_id: str
    plugin_version: str
    mode: str
    values: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    remember: bool = True
    ai_enabled: bool = False


class RunEvent(APIModel):
    sequence: int
    type: Literal["stage", "output", "complete", "error"]
    stage: str
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RunState(APIModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    server_id: str
    plugin_id: str
    plugin_version: str
    mode: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"] = "queued"
    stage: str = "queued"
    message: str = "等待执行"
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    report_id: str | None = None
    events: list[RunEvent] = Field(default_factory=list)


class Finding(APIModel):
    severity: Literal["critical", "warning", "info", "success"]
    title: str
    evidence: str
    recommendation: str = ""


class DiagnosticReport(APIModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    server: dict[str, Any]
    plugin: dict[str, Any]
    status: Literal["completed", "failed", "cancelled"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: float = 0
    summary: str
    findings: list[Finding]
    raw_output: str
    # 生成时是否使用了插件 HTML 报告模板。报告是历史记录，插件配置随时可能改，
    # 因此呈现方式以这里记录的为准；None 表示改动前保存的老报告，只能按当前插件尽力推断。
    report_template: bool | None = None
    ai: dict[str, Any] | None = None
    audit: dict[str, Any] = Field(default_factory=dict)
