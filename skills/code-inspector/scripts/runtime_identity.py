"""Installed operator bindings are the Runtime identity authority."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeIdentity:
    operator_id: str
    role: str
    agent_platform: str
    runtime_backend: str
    fixed_tool_path: str


def load_bindings(review_home: Path) -> dict:
    path = review_home / "config" / "agent-bindings.json"
    if not path.is_file():
        raise RuntimeError(f"AGENT_BINDINGS_MISSING:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"AGENT_BINDINGS_INVALID:{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("AGENT_BINDINGS_INVALID")
    return value


def resolve_identity(review_home: Path, operator_id: str, expected_role: str | None = None) -> RuntimeIdentity:
    binding = load_bindings(review_home).get(operator_id)
    if not binding:
        raise PermissionError(f"OPERATOR_NOT_BOUND:{operator_id}")
    role = binding.get("role")
    if expected_role and role != expected_role:
        raise PermissionError(f"ROLE_MISMATCH:{operator_id}:{role}!={expected_role}")
    identity = RuntimeIdentity(
        operator_id=operator_id,
        role=role,
        agent_platform=binding.get("agent_platform") or binding.get("agent") or "unknown",
        runtime_backend=binding.get("runtime_backend") or ("codex-app-server" if binding.get("agent") == "codex" else "external"),
        fixed_tool_path=binding.get("fixed_tool_path") or str(Path(binding["skill_path"]) / "tools" / f"review-db-{operator_id}.py"),
    )
    if identity.role not in {"inspector", "developer"}:
        raise PermissionError(f"UNSUPPORTED_RUNTIME_ROLE:{identity.role}")
    if not Path(identity.fixed_tool_path).is_file():
        raise RuntimeError(f"FIXED_TOOL_MISSING:{identity.fixed_tool_path}")
    return identity
