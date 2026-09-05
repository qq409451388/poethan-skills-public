"""Session-scoped authorization for the opt-in Issue Thread runtime."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime_identity import RuntimeIdentity, resolve_identity


@dataclass(frozen=True)
class SessionScope:
    operator_id: str
    role: str
    agent_platform: str
    runtime_backend: str
    fixed_tool_path: str
    multi_thread_active: bool


def create_session_scope(
    review_home: Path,
    session_identity: str,
    config: dict[str, Any],
    *,
    explicit_multi_thread: bool,
) -> SessionScope:
    """Resolve immutable identity and apply the config + explicit-user gate."""
    identity: RuntimeIdentity = resolve_identity(review_home, session_identity)
    allowed = bool(
        config.get("thread_runtime", {}).get("multi_thread", {}).get("enabled", False)
    )
    if explicit_multi_thread and not allowed:
        raise PermissionError("MULTI_THREAD_DISABLED_BY_CONFIG")
    return SessionScope(
        operator_id=identity.operator_id,
        role=identity.role,
        agent_platform=identity.agent_platform,
        runtime_backend=identity.runtime_backend,
        fixed_tool_path=identity.fixed_tool_path,
        multi_thread_active=allowed and explicit_multi_thread,
    )


def require_active(scope: SessionScope) -> None:
    if not scope.multi_thread_active:
        raise PermissionError("MULTI_THREAD_NOT_ACTIVE_FOR_SESSION")


def require_config_allowed(scope: SessionScope, config: dict[str, Any]) -> None:
    require_active(scope)
    if not config.get("thread_runtime", {}).get("multi_thread", {}).get("enabled", False):
        raise PermissionError("MULTI_THREAD_DISABLED_BY_CONFIG")


def assert_session_target(
    scope: SessionScope,
    operator_id: str,
    role: str,
    agent_platform: str | None = None,
    runtime_backend: str | None = None,
) -> None:
    """Fail closed before any claim, mapping mutation, or App Server call."""
    require_active(scope)
    if (
        operator_id != scope.operator_id
        or role != scope.role
        or (agent_platform is not None and agent_platform != scope.agent_platform)
        or (runtime_backend is not None and runtime_backend != scope.runtime_backend)
    ):
        raise PermissionError(
            "SESSION_SCOPE_VIOLATION:"
            f"session={scope.operator_id}/{scope.role}:target={operator_id}/{role}"
        )
