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


class AISettings(APIModel):
    endpoint: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    configured: bool = False


class ApplicationSettings(APIModel):
    plugin_directory: str
    developer_mode: bool = False
    demo_mode: bool = True
    ai: AISettings = Field(default_factory=AISettings)


class SettingsInput(APIModel):
    plugin_directory: str
    developer_mode: bool = False
    demo_mode: bool = True
    ai: AISettings = Field(default_factory=AISettings)
    ai_api_key: str | None = None


class AIConnectionInput(APIModel):
    endpoint: str
    model: str
    api_key: str | None = None


class PluginTrust(APIModel):
    status: Literal["trusted", "unsigned", "untrusted", "invalid"]
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
    entrypoint: str
    language: str = "bash"
    output_limit: int = 1_000_000
    default_mode: str
    modes: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    report: dict[str, Any] | None = None
    permissions: dict[str, bool] = Field(default_factory=dict)
    directory: str
    trust: PluginTrust
    valid: bool = True
    errors: list[str] = Field(default_factory=list)


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
    ai: dict[str, Any] | None = None
    audit: dict[str, Any] = Field(default_factory=dict)
