#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import filecmp
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
DEFAULT_CONFIG = SCRIPTS_DIR / "config" / "tools.json"

def expand_path(value: str, base_dir: Path | None = None) -> Path:
    value = value.replace("${HOME}", str(Path.home()))
    expanded = os.path.expandvars(os.path.expanduser(value))
    path = Path(expanded)
    if not path.is_absolute():
        path = (base_dir or REPO_ROOT) / path
    return path.resolve()

def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("tools", "skills"):
        if key not in config:
            raise ValueError(f"配置缺少字段: {key}")
    return config

def load_skill_config(source: Path) -> dict[str, Any]:
    path = source / "agents" / "agent-roles.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("skill_name", "runtime", "agents", "bindings", "roles"):
        if key not in config:
            raise ValueError(f"Skill 配置缺少字段: {key}")
    if not isinstance(config["agents"], list):
        raise ValueError("Skill 配置 agents 必须为通用工具名称数组")
    aliases: set[str] = set()
    selectors: set[str] = set()
    grouped_bindings: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for role, assignments in config["bindings"].items():
        if role not in config["roles"]:
            raise ValueError(f"角色 {role} 缺少权限配置")
        policy = config["roles"][role]
        selector = policy.get("session_selector")
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError(f"角色 {role} 缺少有效的 session_selector")
        if selector in selectors:
            raise ValueError(f"角色会话 selector 必须唯一: {selector}")
        selectors.add(selector)
        for key in ("commands", "responsibilities", "prohibited"):
            if not isinstance(policy.get(key), list):
                raise ValueError(f"角色 {role} 的 {key} 必须为数组")
        if not isinstance(assignments, list) or not assignments:
            raise ValueError(f"角色 {role} 至少需要一个绑定")
        for assignment in assignments:
            agent, alias = assignment.get("agent"), assignment.get("alias")
            if agent not in config["agents"] or not alias:
                raise ValueError(f"角色 {role} 的绑定无效: {assignment}")
            if alias in aliases:
                raise ValueError(f"逻辑身份 alias 必须全局唯一: {alias}")
            aliases.add(alias)
            grouped_bindings.setdefault((agent, role), []).append(assignment)
    for (agent, role), assignments in grouped_bindings.items():
        defaults = [item for item in assignments if item.get("default") is True]
        if len(assignments) > 1 and len(defaults) != 1:
            raise ValueError(
                f"{agent} 的 {role} 角色存在多个逻辑身份时，必须且只能设置一个 default=true"
            )
        if len(defaults) > 1:
            raise ValueError(f"{agent} 的 {role} 角色只能有一个默认逻辑身份")
    return config

def print_result(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False))

def ensure_dirs(home: Path) -> None:
    for name in ("data", "bin", "backups", "exports", "logs", "config"):
        (home / name).mkdir(parents=True, exist_ok=True)

def migration_version(path: Path) -> int:
    return int(path.name.split("__", 1)[0][1:])

def migrate(db_path: Path, backup_dir: Path | None = None) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    backup_path: Path | None = None
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                migration_name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        rows = conn.execute("SELECT version, migration_name FROM schema_version ORDER BY version").fetchall()
        current = rows[-1][0] if rows else 0
        migrations = sorted((SCRIPT_DIR / "migrations").glob("V*.sql"), key=migration_version)
        expected = 1
        for row in rows:
            if row[0] != expected:
                raise RuntimeError(f"迁移版本不连续，缺少 V{expected:03d}")
            expected += 1
        pending = [path for path in migrations if migration_version(path) > current]
        if pending and rows and backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"review-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
            with closing(sqlite3.connect(backup_path)) as backup_conn:
                conn.backup(backup_conn)
        for path in migrations:
            version = migration_version(path)
            if version <= current:
                continue
            if version != current + 1:
                raise RuntimeError(f"迁移版本不连续，期望 V{current + 1:03d}，发现 V{version:03d}")
            script = path.read_text(encoding="utf-8")
            requires_foreign_keys_off = "-- migration: foreign_keys_off" in script
            if requires_foreign_keys_off:
                conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            try:
                statement = ""
                for line in script.splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        conn.execute(statement)
                        statement = ""
                if statement.strip():
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_version(version, migration_name) VALUES (?, ?)",
                    (version, path.name),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                if requires_foreign_keys_off:
                    conn.execute("PRAGMA foreign_keys = ON")
            current = version
            applied.append(path.name)
    return {"applied": applied, "backup": str(backup_path) if backup_path else None}

def link_runtime(home: Path, skill_config: dict[str, Any], force: bool, skill_source: Path | None = None) -> None:
    src = SCRIPT_DIR / "runtime" / "review_db.py"
    dst = home / "bin" / "review-db.py"
    create_skill_link(src, dst, force)
    if skill_source:
        for name in (
            "code-inspector-supervisor.py", "supervisor.py", "issue_thread.py",
            "codex_thread_runtime.py", "runtime_identity.py", "runtime_capabilities.py",
            "review_repository.py", "session_scope.py",
        ):
            create_skill_link(skill_source / "scripts" / name, home / "bin" / name, force)
    for role, assignments in skill_config["bindings"].items():
        for assignment in assignments:
            alias = assignment["alias"]
            runtime_role = "inspector" if role == "inspector" else role
            wrapper = home / "bin" / f"review-db-{alias}.py"
            wrapper.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "from pathlib import Path\n"
                f"tool = Path({str(dst)!r})\n"
                f"os.execv(sys.executable, [sys.executable, str(tool), '--agent', {runtime_role!r}, '--operator-id', {alias!r}, *sys.argv[1:]])\n",
                encoding="utf-8",
            )
            if os.name != "nt":
                wrapper.chmod(0o755)

def create_skill_link(source: Path, target: Path, force: bool) -> None:
    """优先创建软链接；平台不允许软链接时复制源文件或目录。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        if target.resolve() == source.resolve():
            return
        target.unlink()
    elif target.exists():
        if source.is_file() and target.is_file() and filecmp.cmp(source, target, shallow=False):
            return
        if not force:
            raise FileExistsError(f"目标已存在: {target}，如需替换请使用 --force")
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    try:
        target.symlink_to(source, target_is_directory=source.is_dir())
    except OSError as exc:
        if target.exists() or target.is_symlink():
            shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink()
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        print(f"[links] 无法创建软链接，已复制 {source} -> {target}: {exc}", file=sys.stderr)

def generated_skill_text(platform: str, identities: list[dict[str, Any]], target: Path) -> str:
    roles: dict[str, dict[str, Any]] = {}
    for item in identities:
        roles.setdefault(item["role"], item)

    selector_pairs = [
        (item["role_policy"]["session_selector"], role)
        for role, item in roles.items()
    ]
    selector_summary = "、".join(f"`{selector}` = `{role}`" for selector, role in selector_pairs)
    if len(roles) == 1:
        selector, role = selector_pairs[0]
        activation = (
            f"当前平台仅配置 `{role}` 角色（selector: `{selector}`）。"
            f"使用 `$code-inspector start` 即可启动，也可显式使用 `$code-inspector start {selector}`；"
            "`/code-inspector` 形式同样兼容。"
        )
    else:
        commands = " 或 ".join(f"`$code-inspector start {selector}`" for selector, _ in selector_pairs)
        activation = (
            f"当前平台配置了多个角色：{selector_summary}。必须使用 {commands} 显式选择；"
            "`/code-inspector` 形式同样兼容。缺少参数、参数未知或角色未配置时，不得激活模式，"
            "应要求用户从上述 selector 中选择。"
        )
    activation += (
        "启动后将角色和逻辑身份锁定到当前模式，不得根据后续任务自动切换；切换前必须先退出。"
        "同一角色存在多个逻辑身份时，使用标记为“默认身份”的入口。"
    )

    rows = []
    for item in identities:
        role = item["role"]
        policy = item["role_policy"]
        tool_path = target / "tools" / f"review-db-{item['alias']}.py"
        tool = f'python "{tool_path}"'
        default_label = "（默认身份）" if item.get("default") else ""
        rows.append(
            f"## {item['alias']} · {role} · {policy['session_selector']}{default_label}\n\n"
            f"固定工具：`{tool}`。该入口已经固化角色和逻辑身份，不要另传 `--agent` 或 `--operator-id`。\n\n"
            f"可执行命令：{', '.join(policy['commands'])}。\n\n"
            f"职责：{'；'.join(policy['responsibilities'])}。\n\n"
            f"禁止：{', '.join(policy['prohibited'])}。"
        )
    watch_path = target / "scripts" / "watch.py"
    routing = (
        "激活后必须读取 `references/core-workflow.md`，并只读取 "
        "`references/role-workflows.md` 中当前锁定角色的章节。状态机与工具调用分别以 "
        "`references/workflow.yaml`、`references/tool-contracts.yaml` 为准；不得直接操作 SQLite。\n\n"
        "## 人类可读文案\n\n"
        "Issue、讨论、阶段提交、审核、验证和报告默认使用简明中文，先说明现象、影响、结论、验证结果和下一步，"
        "再补必要技术证据；不要只堆状态码、类名、调用链、异常、SQL 或 Diff。目标是让测试人员和项目负责人"
        "无需理解内部实现也能读懂。新写代码注释默认用中文解释业务目的、边界和原因。Git 提交标题和正文也"
        "默认使用中文；仓库采用 Conventional Commits 时可保留 `feat/fix/refactor(scope)` 等固定前缀，但冒号"
        "后的摘要和正文必须用中文说明业务变化，例如 `refactor(analytics): 统一淘宝数据源边界`，不得只写英文"
        "摘要。若项目已有明确的强制语言规范则遵循项目规范。原始标识符和证据保持不变；本规则不代表获得"
        "执行 `git add`、`git commit` 或 `git push` 的额外授权。\n\n"
        "多 Issue 调度或 Issue Thread 操作必须读取 `references/thread-runtime.md`，配置来自 `config/runtime.json`。\n\n"
        "## Session Scope 与 Multi-Thread\n\n"
        "Session 启动时选定的 role、operator_id、agent_platform 固定到退出。Multi-Thread 默认关闭；"
        "只有配置 `thread_runtime.multi_thread.enabled=true` 且用户在当前 Session 明确要求开启，才可激活。"
        "Supervisor 只能 claim 当前 operator+role 的 Event，Child Thread 必须继承相同身份；跨 Role/Operator Dispatch "
        "必须以 `SESSION_SCOPE_VIOLATION` 失败。Watch 不授权 Multi-Thread，Multi-Thread 也不授权 Watch；"
        "任何模式都禁止创建或恢复 Codex Goal。关闭 Multi-Thread 时保留已有 Mapping，但停止自动 Dispatch。\n\n"
        "只有用户明确要求持续观察或停止观察时才读取 `references/watch-mode.md`；"
        f"Watcher 入口为 `python \"{watch_path}\"`。审核等级和报告导出分别按需读取 "
        "`references/review-levels.yaml`、`references/report-schema.yaml`。"
    )
    return (
        "---\nname: code-inspector\ndescription: 已安装的 Code Inspector 角色 Skill。仅在用户明确开启代码检查模式后，按启动时选定的逻辑身份执行。\n---\n\n"
        f"# Code Inspector\n\n当前平台：`{platform}`。仅在用户明确开启后使用本流程；启动规则见 `references/activation.yaml`。\n\n"
        f"## 会话角色选择\n\n{activation}\n\n{routing}\n\n"
        + "\n\n".join(rows)
        + "\n"
    )

def install_generated_skill(source: Path, target: Path, platform: str, identities: list[dict[str, Any]], home: Path, force: bool) -> None:
    if target.is_symlink():
        target.unlink()
    elif target.exists() and not (target / ".code-inspector-generated").exists():
        if not force:
            raise FileExistsError(f"目标已存在且不是本安装器生成: {target}，如需替换请使用 --force")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    (target / ".code-inspector-generated").write_text("generated\n", encoding="utf-8")
    (target / "SKILL.md").write_text(generated_skill_text(platform, identities, target), encoding="utf-8")
    for name in ("references", "scripts", "config"):
        link = target / name
        if link.exists() or link.is_symlink():
            link.unlink() if link.is_symlink() else shutil.rmtree(link)
        create_skill_link(source / name, link, force=True)
    agents_dir = target / "agents"
    if agents_dir.is_symlink():
        agents_dir.unlink()
    agents_dir.mkdir(exist_ok=True)
    shutil.copy2(source / "agents" / "openai.yaml", agents_dir / "openai.yaml")
    tools_dir = target / "tools"
    tools_dir.mkdir(exist_ok=True)
    for item in identities:
        tool_link = tools_dir / f"review-db-{item['alias']}.py"
        if tool_link.exists() or tool_link.is_symlink():
            tool_link.unlink() if tool_link.is_symlink() or tool_link.is_file() else shutil.rmtree(tool_link)
        create_skill_link(home / "bin" / f"review-db-{item['alias']}.py", tool_link, force=True)

def install_role_skills(
    tools_config: dict[str, Any], skill_config: dict[str, Any], source: Path, home: Path, force: bool
) -> None:
    bindings: dict[str, Any] = {}
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for role, assignments in skill_config["bindings"].items():
        for assignment in assignments:
            platform, alias = assignment["agent"], assignment["alias"]
            by_platform.setdefault(platform, []).append({
                "alias": alias,
                "role": role,
                "default": assignment.get("default", False),
                "role_policy": skill_config["roles"][role],
            })
    for platform, identities in by_platform.items():
        tool = tools_config["tools"][platform]
        if not tool.get("enabled", False):
            raise ValueError(f"Agent 所属工具未启用: {platform}")
        target_root = expand_path(tool["skills_dir"])
        target = target_root / skill_config["skill_name"]
        install_generated_skill(source, target, platform, identities, home, force)
        for item in identities:
            bindings[item["alias"]] = {
                "alias": item["alias"], "agent": platform, "role": item["role"],
                "agent_platform": platform,
                "runtime_backend": "codex-app-server" if platform == "codex" else "external",
                "default": item["default"], "role_policy": item["role_policy"],
                "skill_path": str(target),
                "fixed_tool_path": str(target / "tools" / f"review-db-{item['alias']}.py"),
            }
        print(f"[skills] {target} ({platform}: {', '.join(i['alias'] for i in identities)})", file=sys.stderr)
    (home / "config" / "agent-bindings.json").write_text(
        json.dumps(bindings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

def init_workspace(workspace: Path, home: Path, force: bool) -> None:
    target = workspace / ".agents"
    target.mkdir(parents=True, exist_ok=True)

    templates = SCRIPT_DIR / "templates"
    mapping = {
        templates / "workspace.yaml": target / "workspace.yaml",
        templates / "agents" / "inspector.yaml": target / "inspector.yaml",
        templates / "agents" / "developer.yaml": target / "developer.yaml",
        templates / "agents" / "human.yaml": target / "human.yaml",
    }

    user_home = home.parent.as_posix()
    for src, dst in mapping.items():
        if dst.exists() and not force:
            print(f"[workspace] skip existing {dst}", file=sys.stderr)
            continue
        content = src.read_text(encoding="utf-8")
        content = content.replace("${USER_HOME}", user_home)
        dst.write_text(content, encoding="utf-8")
        print(f"[workspace] created {dst}", file=sys.stderr)

def webtool_launcher_path(home: Path, windows: bool | None = None) -> Path:
    windows = os.name == "nt" if windows is None else windows
    return home / "bin" / ("review-web.cmd" if windows else "review-web")


def link_webtool(home: Path, force: bool, windows: bool | None = None) -> Path:
    """生成人工审查控制台启动器。

    Windows 使用批处理脚本，其他平台使用 Bash 脚本。
    """
    windows = os.name == "nt" if windows is None else windows
    webtool_dir = REPO_ROOT / "apps" / "code-inspector-webtool"
    app_py = webtool_dir / "app.py"
    if not app_py.exists():
        raise FileNotFoundError(f"未找到 webtool 入口: {app_py}")

    launcher = webtool_launcher_path(home, windows)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if launcher.exists() or launcher.is_symlink():
        if not force and not launcher.is_symlink():
            raise FileExistsError(f"目标已存在: {launcher}，如需替换请使用 --force")
        launcher.unlink()
    if windows:
        launcher.write_text(
            "@echo off\n"
            "setlocal\n"
            f'cd /d "{str(webtool_dir).replace("%", "%%")}"\n'
            "if errorlevel 1 exit /b 1\n"
            'python "app.py" %*\n'
            "exit /b %errorlevel%\n",
            encoding="utf-8",
        )
    else:
        launcher.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'cd "{webtool_dir}"\n'
            'exec python3 "app.py" "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    print(f"[webtool] launcher -> {launcher}", file=sys.stderr)
    return launcher


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Code Inspector Skill 安装器")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--force", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("install")

    p = sub.add_parser("init-workspace")
    p.add_argument("--workspace", type=Path, default=Path.cwd())

    sub.add_parser("migrate")
    sub.add_parser("verify")
    sub.add_parser("install-webtool")
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    try:
        config_path = args.config.resolve()
        config = load_config(config_path)
        source = expand_path(config["skills"]["source_dir"], config_path.parent) / "code-inspector"
        skill_config = load_skill_config(source)
        home = expand_path(skill_config["runtime"]["local_home"])
        db = expand_path(skill_config["runtime"]["database"])

        if args.command == "install":
            ensure_dirs(home)
            shutil.copy2(config_path, home / "config" / "tools.json")
            (home / "config" / "code-inspector.json").write_text(
                json.dumps(skill_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            runtime_config = json.loads((source / "config" / "runtime.json").read_text(encoding="utf-8"))
            runtime_config["database"] = str(db)
            (home / "config" / "runtime.json").write_text(
                json.dumps(runtime_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            migration_result = migrate(db, home / "backups")
            link_runtime(home, skill_config, args.force, source)
            install_role_skills(config, skill_config, source, home, args.force)
            print_result({
                "command": "install", "status": "ok", "home": str(home), "database": str(db),
                "migrations": migration_result["applied"], "backup": migration_result["backup"],
            })

        elif args.command == "init-workspace":
            ensure_dirs(home)
            init_workspace(args.workspace.resolve(), home, args.force)
            print_result({"command": "init-workspace", "status": "ok", "workspace": str(args.workspace.resolve())})

        elif args.command == "migrate":
            ensure_dirs(home)
            migration_result = migrate(db, home / "backups")
            print_result({
                "command": "migrate", "status": "ok", "database": str(db),
                "migrations": migration_result["applied"], "backup": migration_result["backup"],
            })

        elif args.command == "verify":
            schema_ok = False
            migration_ok = False
            runtime_config_ok = False
            if db.exists():
                try:
                    with closing(sqlite3.connect(db)) as conn:
                        required = {"schema_version", "review_task", "review_task_version", "review_issue", "issue_stage", "issue_activity", "agent_audit_log", "code_inspector_thread", "code_inspector_event"}
                        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        schema_ok = required.issubset(tables)
                        installed_version = conn.execute(
                            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
                        ).fetchone()[0]
                        available_versions = [
                            migration_version(path) for path in (SCRIPT_DIR / "migrations").glob("V*.sql")
                        ]
                        migration_ok = bool(available_versions) and installed_version == max(available_versions)
                except sqlite3.Error:
                    schema_ok = False
            runtime_config_path = home / "config" / "runtime.json"
            if runtime_config_path.exists():
                try:
                    installed_runtime = json.loads(runtime_config_path.read_text(encoding="utf-8"))
                    runtime_config_ok = bool(
                        installed_runtime.get("database")
                        and installed_runtime.get("thread_runtime", {}).get("isolation", {}).get("granularity") == "issue_operator"
                    )
                except (OSError, ValueError):
                    runtime_config_ok = False
            checks = {
                "database": db.exists(),
                "schema": schema_ok,
                "migrations_current": migration_ok,
                "runtime_review_db": (home / "bin" / "review-db.py").exists(),
                "runtime_supervisor": (home / "bin" / "code-inspector-supervisor.py").exists(),
                "runtime_issue_thread": (home / "bin" / "issue_thread.py").exists(),
                "runtime_config": runtime_config_ok,
                "skill_config": (home / "config" / "code-inspector.json").exists(),
                "agent_bindings": (home / "config" / "agent-bindings.json").exists(),
            }
            for role, assignments in skill_config["bindings"].items():
                for assignment in assignments:
                    agent_name = assignment["agent"]
                    tool = config["tools"].get(agent_name, {})
                    target_root = expand_path(tool.get("skills_dir", "")) if tool else Path("/")
                    target = target_root / skill_config["skill_name"]
                    checks[f"skill:{assignment['alias']}:{role}"] = target.is_dir() and (target / ".code-inspector-generated").exists()
                    checks[f"runtime:{assignment['alias']}"] = (home / "bin" / f"review-db-{assignment['alias']}.py").exists()
                    checks[f"skill-tool:{assignment['alias']}"] = (
                        target / "tools" / f"review-db-{assignment['alias']}.py"
                    ).exists()
            print_result({"command": "verify", "status": "ok" if all(checks.values()) else "failed", "checks": checks})
            if not all(checks.values()):
                return 1

        elif args.command == "install-webtool":
            ensure_dirs(home)
            launcher = link_webtool(home, args.force)
            print_result({
                "command": "install-webtool",
                "status": "ok" if launcher.exists() else "failed",
                "launcher": str(launcher),
                "note": "需先 pip install -r apps/code-inspector-webtool/requirements.txt",
            })
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
