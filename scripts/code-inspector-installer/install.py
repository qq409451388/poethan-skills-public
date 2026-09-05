#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    with sqlite3.connect(db_path) as conn:
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
            with sqlite3.connect(backup_path) as backup_conn:
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

def link_runtime(home: Path, skill_config: dict[str, Any], force: bool) -> None:
    src = SCRIPT_DIR / "runtime" / "review_db.py"
    dst = home / "bin" / "review-db.py"
    create_skill_link(src, dst, force)
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
        if role == "developer":
            workflow = f"""### 执行流程

1. 用 `{tool} task-list`、默认精简的 `issue-list` / `issue-get`、`discussion-list` 和 `activity-list-recent` 增量定位待处理 issue、讨论与里程碑；不要创建 task、版本或正式 issue，不要修改评级或最终确认。
2. `DESIGN_REQUIRED` 或 `REDESIGN_REQUIRED` 时只阅读、分析和讨论，用 `design-submit` 提交具体方案；`DESIGN_PENDING_REVIEW` 时等待审核。上述三种状态禁止修改业务代码和 `implementation-submit`，不得自行批准设计。
3. 只有简单问题或 `design-review approved` 后的 `IN_PROGRESS` 才能编码。若存在 Stage Plan，先用 `stage-get` 读取当前 Stage 及全部历史 PASSED baseline；修改前用 `stage-prepare` 声明预计影响的模块/文件/类、修改原因和不得改变的历史行为。只修改当前 Stage 范围，不得提前实施后续 Stage。
4. 完成后用 `stage-submit` 提交 commit、Diff 摘要、代码引用、当前验收测试和历史 Stage 累计回归；上轮有 BLOCKER/MUST 时在 `resolved-findings` 逐项回应 id。SHOULD/NIT 只进 Backlog，不要求为当前 Stage 修改。提交后立即等待验收，不得自行宣布 PASS。
5. 无 Stage Plan 时可直接实现；有计划时必须等全部 Stage `APPROVED` 后，才能用 `implementation-submit` 原子提交整个 Issue 的最终实现证据。Stage 不增加 implementation attempt。
6. 方案应按复杂度说明修改模块、类或方法、数据流、状态/幂等/并发、DB/历史/API 兼容、测试和风险；不要机械套模板。遇到验收口径或成立性疑问时，只能带明确说明转 `INSPECTOR_CONFIRMATION_REQUIRED`，不得直接进入 `HUMAN_CONFIRMATION_REQUIRED`、调用 `human-escalate` 或绕过 Inspector 请求 Human 做技术决策。
7. `HUMAN_CONFIRMATION_REQUIRED` 时没有可执行动作，必须等待 Human 决策后恢复流程。发现不属于现有 issue 的新问题时用 `candidate-submit` 提交候选，绝不调用 `issue-create`。默认聊天仅说明处理的 issue、方案或改动、测试和下一步。
"""
        else:
            workflow = f"""### 执行流程

1. 先读取 `references/workflow.yaml` 和 `references/review-levels.yaml`；创建或继续检查时用 `{tool} task-resolve`。`REVIEW` 沿用基线 identity；`CONTINUOUS` 跨基线复用且所有 issue 终结也不自动关闭。
2. 区分 `scan` 与 `report`：扫描期间先收集候选，主审核者必须额外串联跨模块数据流；向 `CONTINUOUS` 报告单个线上问题只核实证据、判定成立和去重，不触发全项目扫描。
3. 初步合并后先做覆盖面回查和补充扫描，再按根因、修复边界和风险链路去重、评级；不能用候选数量或并行扫描完成代替覆盖判断。最后用 `issue-create-batch` 创建正式 issue。
4. 对跨模块、Schema/迁移、回灌、状态机、幂等并发、ACK/retry/recovery、公共 API、大重构或方向不确定的问题优先 `design-request`，明确根因、不可破坏语义、约束、风险、推荐方向和必须回答的问题。用 `discussion-append --topic DESIGN` 补充讨论；达成一致后用 `decision-record` 登记结论，再用 `design-review` 批准或明确驳回。
5. 对一次性实施容易跑偏的复杂方案，在批准前用 `stage-plan-create` 定义少量串行 Stage，每个阶段必须有目标和可验证的验收标准。验收时检查 Dev 的影响声明、Diff、当前验收和全部历史 PASSED baseline；历史行为/测试/契约回归失败一律是 BLOCKER。finding 只分 BLOCKER/MUST/SHOULD/NIT，只有前两级阻断；SHOULD/NIT 只进 Backlog。
6. `stage-review` 必须结构化输出 Inspection Result、四级 findings、Historical Regression、Current Stage Acceptance 和 Final Decision，governance v2 优先用 `--decision auto` 让 Runtime 计算 Gate。第一轮可完整提出问题；第二轮起不得新增无关 SHOULD/NIT，新 BLOCKER/MUST 必须说明此前遗漏原因、证据与实际风险。BLOCKER/MUST 清零且当前与历史验收全 PASS 时必须结束审核并建立 PASSED baseline，不得用“还可优化”继续循环。整案失效时用 `--decision redesign` 保留旧计划并重走设计。
7. 用 `issue-list-pending-review` 汇总最终实现。审核失败必须区分：实现细节错则追加 `VERIFICATION_FAILED` 并回 `IN_PROGRESS`；方向错则转 `REDESIGN_REQUIRED`，强制重走设计。连续两次失败必须重新判断设计是否对齐，并给出必改点与验证标准。
8. Human 是异常兜底，不是普通 Reviewer。准备 `human-escalate` 前必须确认继续读代码、补测试、查数据库/日志/运行数据、追加方案讨论或自行作出合理技术判断都不能安全解决，并且 Human 确实掌握关键业务事实，或选错方案会造成无法靠正常重试恢复的重大数据破坏。意见不一致、普通架构取舍、方案差、实现或测试失败都不得升级。
9. 确需升级时，用 `human-escalate` 把原因、已验证/未知事实、选项与影响、推荐和 Human 唯一要回答的问题整理清楚；不得倾倒长日志、完整代码或 Agent 对话。`HUMAN_CONFIRMATION_REQUIRED` 时停止自动工作；Inspector 不得代替 Human resolve。Human 恢复后仍由 Inspector 负责设计、审核、验证和技术闭环。
10. 只有当前 implementation attempt 已有 `VERIFICATION_PASSED` 才能转 `CONFIRMED`，Human 也不能跳过。不修改业务代码；Inspector 定义必须解决和不可破坏的边界，Developer 决定具体代码实现。默认聊天仅输出简短任务摘要。
"""
        default_label = "（默认身份）" if item.get("default") else ""
        rows.append(
            f"## {item['alias']} · {role} · {policy['session_selector']}{default_label}\n\n"
            f"固定工具：`{tool}`。该入口已经固化角色和逻辑身份，不要另传 `--agent` 或 `--operator-id`。\n\n"
            f"可执行命令：{', '.join(policy['commands'])}。\n\n"
            f"职责：{'；'.join(policy['responsibilities'])}。\n\n"
            f"禁止：{', '.join(policy['prohibited'])}。\n\n{workflow}"
        )
    watch_path = target / "scripts" / "watch.py"
    watch = (
        "## 手动持续观察 / Watch Mode\n\n"
        "Watch Mode 默认关闭，等待状态绝不自动开启。只有用户明确要求持续观察并指定任务时，"
        "才读取 `references/watch-mode.md`，解析并校验唯一目标、查询方式、唤醒条件和当前角色。"
        "任一项无法可靠确定或角色冲突时不得启动，也不得猜测。校验通过后使用 "
        f"`python \"{watch_path}\"` 启动 Shell watcher；默认每 120 秒静默检测，"
        "整个等待期只维持这一个长生命周期 Shell command，未命中不得输出、退出或触发模型查询。"
        "禁止为 Watch 创建或维持 Codex Goal，也不得使用 Goal automatic continuation。"
        "命中后只输出一次最小 `ACTION_REQUIRED` 事件并结束 command，使当前 turn 恢复，再重新获取最新上下文。"
        "一次 Watch 默认只处理一次事件，不扩大目标、不自动续订；仅当用户一开始明确要求持续到整个任务结束时才可逐次重启。"
        "用户要求停止时立即中断保存的 Shell execution session。\n"
    )
    return "---\nname: code-inspector\ndescription: 已安装的 Code Inspector 角色 Skill。仅在用户明确开启代码检查模式后，按启动时选定的逻辑身份执行。\n---\n\n# Code Inspector\n\n当前平台：`" + platform + "`。仅在用户明确开启后使用本流程；完整规则见 `references/activation.yaml`。\n\n## 会话角色选择\n\n" + activation + "\n\n只使用启动时选定的下列逻辑身份，不得越权。所有状态流转先以 `references/workflow.yaml` 为准；工具参数以 `references/tool-contracts.yaml` 为准；不得直接操作 SQLite。\n\n" + watch + "\n" + "\n\n".join(rows) + "\n\n需要审核等级时读取 `references/review-levels.yaml`；仅在用户要求导出时读取 `references/report-schema.yaml`。\n\nIssue 默认只写标题、短摘要、维度和严重度；按需补完成标准、技术说明、本项目术语和证据。摘要可以使用轻量 Markdown，但不要写推理过程、长日志或完整代码；代码位置优先写文件与符号，行号不能作为唯一定位。通用技术词不解释，只定义项目内或临时创造的词。讨论使用 `discussion-*` 并与处理历史分开；Developer/Inspector 修正自己的讨论时 amend 原消息。讨论达成一致后由 Inspector 用 `decision-record` 录入有效结论。`activity-amend` 仅修订尚未被审核的提交文案，最终结论不得覆盖。\n"

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
    for name in ("references", "scripts"):
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
                "default": item["default"], "role_policy": item["role_policy"],
                "skill_path": str(target),
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
            (home / "config" / "runtime.json").write_text(
                json.dumps({"database": str(db)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            migration_result = migrate(db, home / "backups")
            link_runtime(home, skill_config, args.force)
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
            if db.exists():
                try:
                    with sqlite3.connect(db) as conn:
                        required = {"schema_version", "review_task", "review_task_version", "review_issue", "issue_stage", "issue_activity", "agent_audit_log"}
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
            checks = {
                "database": db.exists(),
                "schema": schema_ok,
                "migrations_current": migration_ok,
                "runtime": (home / "bin" / "review-db.py").exists(),
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
