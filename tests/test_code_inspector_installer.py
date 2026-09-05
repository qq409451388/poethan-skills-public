import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "code-inspector-installer" / "install.py"
INSTALLER_SPEC = importlib.util.spec_from_file_location("code_inspector_installer", INSTALLER)
assert INSTALLER_SPEC and INSTALLER_SPEC.loader
INSTALLER_MODULE = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(INSTALLER_MODULE)


class CodeInspectorInstallerTest(unittest.TestCase):
    @staticmethod
    def home_env(home: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        return env

    def run_cmd(self, home: Path, *args: str, cwd: Path | None = None) -> dict:
        result = subprocess.run(
            [sys.executable, *args], cwd=cwd or ROOT, env=self.home_env(home),
            text=True, capture_output=True,
        )
        if result.returncode != 0:
            self.fail(f"命令执行失败 ({result.returncode}): {result.stderr}")
        return json.loads(result.stdout)

    def run_raw(self, home: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, *args], cwd=cwd or ROOT, env=self.home_env(home),
            text=True, capture_output=True,
        )

    def test_symlink_failure_falls_back_to_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "source.py"
            source_file.write_text("print('copied')\n", encoding="utf-8")
            source_dir = root / "source-dir"
            source_dir.mkdir()
            (source_dir / "rule.yaml").write_text("enabled: true\n", encoding="utf-8")

            with mock.patch.object(Path, "symlink_to", side_effect=OSError("symlink denied")):
                copied_file = root / "targets" / "tool.py"
                copied_dir = root / "targets" / "references"
                INSTALLER_MODULE.create_skill_link(source_file, copied_file, force=False)
                INSTALLER_MODULE.create_skill_link(source_dir, copied_dir, force=False)

            self.assertFalse(copied_file.is_symlink())
            self.assertEqual(copied_file.read_text(encoding="utf-8"), "print('copied')\n")
            self.assertFalse(copied_dir.is_symlink())
            self.assertEqual(
                (copied_dir / "rule.yaml").read_text(encoding="utf-8"),
                "enabled: true\n",
            )

    def test_home_placeholder_uses_platform_home_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            platform_home = Path(temp)
            with mock.patch.object(INSTALLER_MODULE.Path, "home", return_value=platform_home):
                expanded = INSTALLER_MODULE.expand_path("${HOME}/.codex/skills")
            self.assertEqual(expanded, (platform_home / ".codex" / "skills").resolve())

    def test_copied_skill_wrapper_can_execute_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            review_home = root / ".agent-review"
            database = review_home / "data" / "review.db"
            source = ROOT / "skills" / "code-inspector"
            skill_config = INSTALLER_MODULE.load_skill_config(source)
            tools_config = {
                "tools": {
                    "codex": {"enabled": True, "skills_dir": str(root / ".codex" / "skills")},
                    "trae-cn": {"enabled": True, "skills_dir": str(root / ".trae-cn" / "skills")},
                    "claude": {"enabled": True, "skills_dir": str(root / ".claude" / "skills")},
                }
            }
            INSTALLER_MODULE.ensure_dirs(review_home)
            INSTALLER_MODULE.migrate(database, review_home / "backups")
            runtime_config = json.loads((source / "config" / "runtime.json").read_text(encoding="utf-8"))
            runtime_config["database"] = str(database)
            (review_home / "config" / "runtime.json").write_text(
                json.dumps(runtime_config), encoding="utf-8",
            )

            with mock.patch.object(Path, "symlink_to", side_effect=OSError("symlink denied")):
                INSTALLER_MODULE.link_runtime(review_home, skill_config, force=False, skill_source=source)
                INSTALLER_MODULE.install_role_skills(
                    tools_config,
                    skill_config,
                    source,
                    review_home,
                    force=False,
                )

            copied_tool = (
                root / ".codex" / "skills" / "code-inspector"
                / "tools" / "review-db-codex-dev.py"
            )
            self.assertFalse(copied_tool.is_symlink())
            result = subprocess.run(
                [sys.executable, str(copied_tool), "task-list"],
                cwd=root,
                env={
                    **os.environ,
                    "AGENT_REVIEW_HOME": str(review_home),
                    "AGENT_REVIEW_DB": str(database),
                },
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [])
            runtime_status = subprocess.run(
                [sys.executable, str(review_home / "bin" / "code-inspector-supervisor.py"), "status"],
                cwd=root,
                env={
                    **os.environ,
                    "AGENT_REVIEW_HOME": str(review_home),
                    "AGENT_REVIEW_DB": str(database),
                },
                text=True,
                capture_output=True,
            )
            self.assertEqual(runtime_status.returncode, 0, runtime_status.stderr)
            self.assertEqual(json.loads(runtime_status.stdout), {"threads": [], "events": []})

    def test_webtool_launchers_are_platform_specific(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".agent-review"
            unix_launcher = INSTALLER_MODULE.link_webtool(home, force=False, windows=False)
            self.assertEqual(unix_launcher.name, "review-web")
            self.assertIn('exec python3 "app.py" "$@"', unix_launcher.read_text(encoding="utf-8"))

            windows_launcher = INSTALLER_MODULE.link_webtool(home, force=False, windows=True)
            self.assertEqual(windows_launcher.name, "review-web.cmd")
            windows_text = windows_launcher.read_text(encoding="utf-8")
            self.assertIn("cd /d", windows_text)
            self.assertIn('python "app.py" %*', windows_text)

    def test_install_is_idempotent_and_workflow_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            first = self.run_cmd(home, str(INSTALLER), "install")
            second = self.run_cmd(home, str(INSTALLER), "install")
            self.assertEqual(first["status"], "ok")
            self.assertEqual(second["status"], "ok")
            codex_skill = home / ".codex" / "skills" / "code-inspector"
            trae_skill = home / ".trae-cn" / "skills" / "code-inspector"
            self.assertTrue((codex_skill / ".code-inspector-generated").exists())
            self.assertTrue((trae_skill / ".code-inspector-generated").exists())
            self.assertTrue((codex_skill / "references").is_dir())
            self.assertTrue((codex_skill / "scripts" / "watch.py").is_file())
            self.assertTrue((home / ".agent-review" / "bin" / "review-db.py").is_file())
            self.assertTrue((home / ".agent-review" / "bin" / "code-inspector-supervisor.py").is_file())
            self.assertTrue((home / ".agent-review" / "bin" / "runtime_identity.py").is_file())
            self.assertTrue((home / ".agent-review" / "bin" / "session_scope.py").is_file())
            if os.name != "nt":
                self.assertTrue(os.access(home / ".agent-review" / "bin" / "code-inspector-supervisor.py", os.X_OK))
            bindings = json.loads((home / ".agent-review" / "config" / "agent-bindings.json").read_text())
            installed_runtime = json.loads((home / ".agent-review" / "config" / "runtime.json").read_text())
            self.assertEqual(installed_runtime["thread_runtime"]["isolation"]["granularity"], "issue_operator")
            self.assertFalse(installed_runtime["thread_runtime"]["multi_thread"]["enabled"])
            self.assertEqual(
                Path(installed_runtime["database"]).resolve(),
                (home / ".agent-review" / "data" / "review.db").resolve(),
            )
            self.assertEqual(bindings["codex-dev"]["role"], "developer")
            self.assertEqual(bindings["codex-insp"]["role"], "inspector")
            self.assertEqual(bindings["codex-insp"]["runtime_backend"], "codex-app-server")
            self.assertEqual(bindings["trae-inspector"]["role"], "inspector")
            self.assertEqual(bindings["trae-inspector"]["runtime_backend"], "external")
            self.assertTrue((home / ".agent-review" / "bin" / "review-db-trae-inspector.py").exists())
            wrapper_text = (
                home / ".agent-review" / "bin" / "review-db-codex-dev.py"
            ).read_text(encoding="utf-8")
            self.assertIn(str(home / ".agent-review" / "bin" / "review-db.py"), wrapper_text)
            self.assertNotIn("with_name('review-db.py')", wrapper_text)
            codex_skill_text = (codex_skill / "SKILL.md").read_text(encoding="utf-8")
            trae_skill_text = (trae_skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(str(codex_skill / "tools" / "review-db-codex-dev.py"), codex_skill_text)
            self.assertIn(str(codex_skill / "tools" / "review-db-codex-insp.py"), codex_skill_text)
            self.assertIn(str(trae_skill / "tools" / "review-db-trae-inspector.py"), trae_skill_text)
            self.assertIn("$code-inspector start dev", codex_skill_text)
            self.assertIn("$code-inspector start insp", codex_skill_text)
            self.assertIn("references/core-workflow.md", codex_skill_text)
            self.assertIn("references/role-workflows.md", codex_skill_text)
            self.assertIn("references/watch-mode.md", codex_skill_text)
            self.assertIn(str(codex_skill / "scripts" / "watch.py"), codex_skill_text)
            self.assertIn("缺少参数", codex_skill_text)
            self.assertIn("Multi-Thread 默认关闭", codex_skill_text)
            self.assertIn("SESSION_SCOPE_VIOLATION", codex_skill_text)
            self.assertIn("Watch 不授权 Multi-Thread", codex_skill_text)
            self.assertIn("禁止创建或恢复 Codex Goal", codex_skill_text)
            self.assertIn("当前平台仅配置 `inspector` 角色", trae_skill_text)
            self.assertIn("$code-inspector start` 即可启动", trae_skill_text)
            self.assertIn('固定工具：`python "', codex_skill_text)
            self.assertNotIn("modify-business-code", codex_skill_text.split("可执行命令：", 1)[1].split("。", 1)[0])
            self.assertNotIn("### 执行流程", codex_skill_text)
            self.assertNotIn("Issue 默认正文只写", codex_skill_text)
            self.assertNotIn("Watch Mode 默认关闭", codex_skill_text)
            core_text = (trae_skill / "references" / "core-workflow.md").read_text(encoding="utf-8")
            role_text = (trae_skill / "references" / "role-workflows.md").read_text(encoding="utf-8")
            watch_text = (trae_skill / "references" / "watch-mode.md").read_text(encoding="utf-8")
            self.assertIn("Issue 默认正文只写", core_text)
            self.assertIn("讨论达成一致后", core_text)
            self.assertIn("Human 只作为极低频最终兜底", core_text)
            self.assertIn("主审核者必须额外串联跨模块数据流", role_text)
            self.assertIn("先做覆盖面回查和补充扫描", role_text)
            self.assertIn("stage-prepare", role_text)
            self.assertIn("stage-plan-create", role_text)
            self.assertIn("连续两次失败必须重新判断设计是否对齐", role_text)
            self.assertIn("默认关闭", watch_text)
            self.assertIn("禁止创建或维持 Codex Goal", watch_text)
            workflow_text = (trae_skill / "references" / "workflow.yaml").read_text(encoding="utf-8")
            levels_text = (trae_skill / "references" / "review-levels.yaml").read_text(encoding="utf-8")
            self.assertIn("确认动作不能被默认视为清理动作", workflow_text)
            self.assertIn("MAXLEN", levels_text)
            self.assertIn("无界增长趋势", levels_text)

            workspace = home / "project"
            workspace.mkdir()
            self.run_cmd(home, str(INSTALLER), "init-workspace", "--workspace", str(workspace))
            self.run_cmd(home, str(INSTALLER), "init-workspace", "--workspace", str(workspace))
            workspace_config = (workspace / ".agents" / "workspace.yaml").read_text(encoding="utf-8")
            self.assertIn('command: "python \\"', workspace_config)

            db_tool = home / ".agent-review" / "bin" / "review-db.py"
            def db(*args: str) -> dict:
                role, *command = args
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                return self.run_cmd(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )

            wrapper_result = self.run_cmd(
                home, str(codex_skill / "tools" / "review-db-codex-dev.py"), "task-list", cwd=workspace
            )
            self.assertEqual(wrapper_result, [])

            forged = subprocess.run(
                [sys.executable, str(db_tool), "--agent", "inspector", "--operator-id", "codex-dev", "task-list"],
                cwd=workspace, env=self.home_env(home), text=True, capture_output=True,
            )
            self.assertNotEqual(forged.returncode, 0)
            self.assertIn("绑定角色为 developer", forged.stderr)

            db("inspector", "task-create", "--task-key", "RT-TEST", "--title", "测试", "--objective", "验证")
            db("inspector", "version-create", "--task-key", "RT-TEST", "--reason", "首次审核")
            db("inspector", "issue-create", "--task-key", "RT-TEST", "--issue-key", "RI-TEST",
               "--title", "问题", "--dimension", "functional_correctness", "--severity", "high",
               "--summary", "**现象**：重复请求会产生重复记录",
               "--expected-outcome", "重复请求只保留一条记录",
               "--local-terms", '{"影子记录":"仅用于回放的只读记录"}')
            compact_issue = db("developer", "issue-get", "--issue-key", "RI-TEST")
            self.assertEqual(compact_issue["summary"], "**现象**：重复请求会产生重复记录")
            self.assertNotIn("description", compact_issue)
            full_issue = db("developer", "issue-get", "--issue-key", "RI-TEST", "--view", "full")
            self.assertEqual(full_issue["description"], compact_issue["summary"])
            self.assertEqual(full_issue["remediation_benefit"], "medium")
            installed_database = home / ".agent-review" / "data" / "review.db"
            with sqlite3.connect(installed_database) as conn:
                audit_before = conn.execute("SELECT COUNT(*) FROM agent_audit_log").fetchone()[0]
            probe = db("developer", "watch-probe", "--kind", "issue-status", "--target", "RI-TEST")
            self.assertEqual(probe, {"target": "RI-TEST", "status": "PROPOSED"})
            with sqlite3.connect(installed_database) as conn:
                audit_after = conn.execute("SELECT COUNT(*) FROM agent_audit_log").fetchone()[0]
            self.assertEqual(audit_after, audit_before)

            blocked = subprocess.run(
                [sys.executable, str(db_tool), "--agent", "inspector", "--operator-id", "trae-inspector", "issue-update-status",
                 "--issue-key", "RI-TEST", "--status", "CONFIRMED"],
                cwd=workspace, env=self.home_env(home),
                text=True, capture_output=True,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("VERIFICATION_PASSED", blocked.stderr)

            db("developer", "issue-update-status", "--issue-key", "RI-TEST", "--status", "IN_PROGRESS")
            db("developer", "issue-update-status", "--issue-key", "RI-TEST",
               "--status", "INSPECTOR_CONFIRMATION_REQUIRED", "--content", "请确认验收边界")
            db("inspector", "activity-append", "--issue-key", "RI-TEST",
               "--activity-type", "INSPECTOR_CONFIRMATION_PROVIDED", "--content", "按当前边界继续")
            db("inspector", "issue-update-status", "--issue-key", "RI-TEST", "--status", "IN_PROGRESS")
            db("developer", "issue-update-status", "--issue-key", "RI-TEST",
               "--status", "IMPLEMENTED_PENDING_REVIEW")
            db("inspector", "activity-append", "--issue-key", "RI-TEST",
               "--activity-type", "VERIFICATION_PASSED", "--content", "通过")
            result = db("inspector", "issue-update-status", "--issue-key", "RI-TEST", "--status", "CONFIRMED")
            self.assertEqual(result["status"], "CONFIRMED")

    def test_task_resolution_and_batch_issue_deduplication(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.run_cmd(home, str(INSTALLER), "install")
            workspace = home / "project"
            workspace.mkdir()
            db_tool = home / ".agent-review" / "bin" / "review-db.py"

            def db(*args: str) -> dict:
                role, *command = args
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                return self.run_cmd(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )

            db("inspector", "task-create", "--task-key", "RT-LEGACY", "--title", "旧任务", "--objective", "旧任务目标")
            legacy = db(
                "inspector", "task-resolve", "--title", "旧任务", "--objective", "旧任务目标",
                "--review-level", "L1", "--review-scope", "legacy-module",
            )
            self.assertEqual(legacy["task_key"], "RT-LEGACY")
            self.assertFalse(legacy["created"])
            db("inspector", "task-update-status", "--task-key", "RT-LEGACY", "--status", "CLOSED")

            resolve_args = (
                "task-resolve", "--title", "订单模块检查", "--objective", "检查订单模块稳定性",
                "--review-level", "L2", "--review-scope", "order-service",
            )
            first = db("inspector", *resolve_args)
            second = db("inspector", *resolve_args)
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(first["task_key"], second["task_key"])

            issue = {
                "title": "重复提交缺少幂等保护",
                "dimension": "data_security",
                "severity": "high",
                "remediation_benefit": "high",
                "remediation_cost": "medium",
                "disposition": "current_iteration",
                "confidence": "high",
                "description": "重复请求可能重复创建订单",
                "facts": "创建接口未使用幂等键",
                "rationale": "相同根因应合并为一个问题",
                "evidence": [{"file_path": "src/order.py", "line_start": 10, "line_end": 12}],
            }
            created = db("inspector", "issue-create-batch", "--task-key", first["task_key"],
                         "--reason", "首次扫描", "--issues", json.dumps([issue, issue], ensure_ascii=False))
            self.assertEqual(len(created["created"]), 1)
            self.assertEqual(len(created["skipped"]), 1)

            repeated = db("inspector", "issue-create-batch", "--task-key", first["task_key"],
                          "--reason", "重复扫描", "--issues", json.dumps([issue], ensure_ascii=False))
            self.assertEqual(repeated["created"], [])
            self.assertEqual(len(repeated["skipped"]), 1)

            confirmed_issue = {
                **issue, "issue_key": "RI-TASK-CLOSE-CONFIRMED", "title": "已经完成验证的问题",
                "facts": "该问题已完成独立验证",
            }
            db(
                "inspector", "issue-create-batch", "--task-key", first["task_key"],
                "--reason", "补充已验证问题", "--issues", json.dumps([confirmed_issue], ensure_ascii=False),
            )
            db(
                "inspector", "activity-append", "--issue-key", "RI-TASK-CLOSE-CONFIRMED",
                "--activity-type", "VERIFICATION_PASSED", "--content", "验证通过",
            )
            db(
                "inspector", "issue-update-status", "--issue-key", "RI-TASK-CLOSE-CONFIRMED",
                "--status", "CONFIRMED", "--content", "完成技术确认",
            )

            direct_submit = db(
                "developer", "issue-update-status", "--issue-key", created["created"][0],
                "--status", "IMPLEMENTED_PENDING_REVIEW",
            )
            self.assertEqual(direct_submit["status"], "IMPLEMENTED_PENDING_REVIEW")

            db("inspector", "task-update-status", "--task-key", first["task_key"], "--status", "ON_HOLD")
            self.assertEqual(
                db("inspector", "issue-get", "--issue-key", created["created"][0])["status"],
                "IMPLEMENTED_PENDING_REVIEW",
            )
            db("inspector", "task-update-status", "--task-key", first["task_key"], "--status", "IN_PROGRESS")
            closed_result = db(
                "inspector", "task-update-status", "--task-key", first["task_key"],
                "--status", "CLOSED", "--close-reason", "本轮治理结束",
            )
            self.assertEqual(closed_result["cancelled_issue_count"], 1)
            self.assertEqual(
                db("inspector", "issue-get", "--issue-key", created["created"][0])["status"],
                "CANCELLED",
            )
            self.assertEqual(
                db("inspector", "issue-get", "--issue-key", "RI-TASK-CLOSE-CONFIRMED")["status"],
                "CONFIRMED",
            )
            close_activities = db("inspector", "activity-list", "--issue-key", created["created"][0])
            task_close_activity = close_activities[-1]
            self.assertEqual(task_close_activity["result_status"], "CANCELLED")
            self.assertEqual(task_close_activity["metadata_json"]["source"], "task-update-status")
            self.assertIn("本轮治理结束", task_close_activity["content"])
            self.assertEqual(db("inspector", "task-list"), [])
            closed = db("inspector", "task-list", "--status", "CLOSED")
            self.assertEqual(closed[0]["task_key"], first["task_key"])

    def test_review_efficiency_commands_and_direct_inspector_transitions(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.run_cmd(home, str(INSTALLER), "install")
            workspace = home / "project"
            workspace.mkdir()
            db_tool = home / ".agent-review" / "bin" / "review-db.py"

            def db(*args: str) -> dict | list:
                role, *command = args
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                return self.run_cmd(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )

            task = db(
                "inspector", "task-resolve", "--title", "批量复核", "--objective", "验证效率接口",
                "--review-level", "L2", "--review-scope", "service",
            )
            issues = [
                {
                    "issue_key": f"RI-BATCH-{index}", "title": f"问题 {index}",
                    "dimension": "code_quality", "severity": "medium",
                    "remediation_benefit": "medium", "remediation_cost": "low",
                    "disposition": "current_iteration", "confidence": "high",
                    "description": f"完整描述 {index}", "facts": f"事实 {index}", "rationale": f"依据 {index}",
                    "evidence": [{"file_path": f"src/{index}.py", "line_start": index}],
                }
                for index in range(1, 4)
            ]
            created = db(
                "inspector", "issue-create-batch", "--task-key", task["task_key"],
                "--reason", "首次扫描", "--issues", json.dumps(issues, ensure_ascii=False),
            )
            self.assertEqual(created["created"], ["RI-BATCH-1", "RI-BATCH-2", "RI-BATCH-3"])

            assessment_updates = [
                {"issue_key": "RI-BATCH-1", "severity": "low", "confidence": "medium"},
                {"issue_key": "RI-BATCH-2", "dimension": "test_observability", "severity": "high"},
            ]
            assessed = db(
                "inspector", "issue-update-assessment-batch",
                "--updates", json.dumps(assessment_updates, ensure_ascii=False),
            )
            self.assertEqual(len(assessed["updated"]), 2)

            failed_batch = subprocess.run(
                [
                    sys.executable, str(db_tool), "--agent", "inspector", "--operator-id", "trae-inspector",
                    "issue-update-assessment-batch", "--updates", json.dumps([
                        {"issue_key": "RI-BATCH-1", "severity": "critical"},
                        {"issue_key": "RI-NOT-FOUND", "severity": "low"},
                    ]),
                ],
                cwd=workspace, env=self.home_env(home), text=True, capture_output=True,
            )
            self.assertNotEqual(failed_batch.returncode, 0)
            self.assertEqual(db("inspector", "issue-get", "--issue-key", "RI-BATCH-1")["severity"], "low")

            db(
                "human", "discussion-append", "--issue-key", "RI-BATCH-1",
                "--topic", "GENERAL", "--content", "请重新评估严重度",
            )
            comments = db("inspector", "discussion-list", "--issue-key", "RI-BATCH-1")
            self.assertTrue(any(item["content"] == "请重新评估严重度" for item in comments))

            detail = db("developer", "issue-get", "--issue-key", "RI-BATCH-1")
            self.assertEqual(detail["summary"], "完整描述 1")
            self.assertEqual(detail["evidence_json"][0]["file_path"], "src/1.py")
            self.assertIsNotNone(detail["last_activity_at"])
            self.assertIsNotNone(detail["last_discussion_at"])

            compact = db(
                "developer", "issue-list", "--task-key", task["task_key"],
                "--updated-after", "2020-01-01T00:00:00Z", "--limit", "1",
                "--fields", "issue_key,title,status,updated_at",
            )
            self.assertEqual(len(compact), 1)
            self.assertEqual(set(compact[0]), {"issue_key", "title", "status", "updated_at"})
            self.assertEqual(
                db(
                    "developer", "issue-list", "--task-key", task["task_key"],
                    "--updated-after", "2099-01-01T00:00:00Z", "--limit", "10",
                    "--fields", "issue_key,status",
                ),
                [],
            )

            status_updates = [
                {"issue_key": "RI-BATCH-1", "status": "IMPLEMENTED_PENDING_REVIEW"},
                {"issue_key": "RI-BATCH-2", "status": "IMPLEMENTED_PENDING_REVIEW"},
            ]
            updated = db(
                "developer", "issue-update-status-batch", "--updates", json.dumps(status_updates),
            )
            self.assertEqual(len(updated["updated"]), 2)
            pending = db("inspector", "issue-list-pending-review", "--task-key", task["task_key"])
            self.assertEqual({item["issue_key"] for item in pending}, {"RI-BATCH-1", "RI-BATCH-2"})

            in_progress = db(
                "inspector", "issue-update-status", "--issue-key", "RI-BATCH-3", "--status", "IN_PROGRESS",
            )
            self.assertEqual(in_progress["status"], "IN_PROGRESS")
            submitted = db(
                "developer", "implementation-submit", "--issue-key", "RI-BATCH-3",
                "--content", "已修复并补充测试", "--code-reference",
                json.dumps([{"file_path": "src/3.py", "line_start": 3}]),
                "--metadata", json.dumps({"tests": ["test_three"]}),
            )
            self.assertEqual(submitted["status"], "IMPLEMENTED_PENDING_REVIEW")
            implementation_activity = [
                item for item in db("developer", "activity-list", "--issue-key", "RI-BATCH-3")
                if item["activity_type"] == "IMPLEMENTATION_SUBMITTED"
            ]
            self.assertEqual(len(implementation_activity), 1)
            self.assertEqual(implementation_activity[0]["attempt_no"], submitted["attempt_no"])
            self.assertEqual(implementation_activity[0]["metadata_json"]["tests"], ["test_three"])

            direct = {**issues[2], "issue_key": "RI-DIRECT-CONFIRM", "title": "直接确认"}
            db(
                "inspector", "issue-create-batch", "--task-key", task["task_key"],
                "--reason", "异议复核", "--issues", json.dumps([direct], ensure_ascii=False),
            )
            db(
                "inspector", "activity-append", "--issue-key", "RI-DIRECT-CONFIRM",
                "--activity-type", "VERIFICATION_PASSED", "--content", "用户降级后复核通过",
            )
            confirmed = db(
                "inspector", "issue-update-status", "--issue-key", "RI-DIRECT-CONFIRM", "--status", "CONFIRMED",
            )
            self.assertEqual(confirmed["status"], "CONFIRMED")
            activity_count = len(db("developer", "activity-list", "--issue-key", "RI-DIRECT-CONFIRM"))
            invalid_submission = subprocess.run(
                [
                    sys.executable, str(db_tool), "--agent", "developer", "--operator-id", "codex-dev",
                    "implementation-submit", "--issue-key", "RI-DIRECT-CONFIRM",
                    "--content", "不应写入的实现证据",
                ],
                cwd=workspace, env=self.home_env(home), text=True, capture_output=True,
            )
            self.assertNotEqual(invalid_submission.returncode, 0)
            self.assertEqual(
                len(db("developer", "activity-list", "--issue-key", "RI-DIRECT-CONFIRM")),
                activity_count,
            )

            candidate = db(
                "developer", "candidate-submit", "--task-key", task["task_key"],
                "--candidate-key", "RC-PLAINTEXT-CREDENTIAL", "--title", "发现明文生产凭据",
                "--description", "配置中包含疑似生产密钥", "--facts", "密钥以明文形式存在",
                "--rationale", "需要 inspector 判断是否成立", "--evidence",
                json.dumps([{"file_path": "config/prod.env", "line_start": 2}]),
                "--suggested-dimension", "data_security", "--suggested-severity", "critical",
                "--suggested-confidence", "medium",
            )
            self.assertEqual(candidate["status"], "SUBMITTED")
            queue = db("inspector", "candidate-list", "--task-key", task["task_key"])
            self.assertEqual(queue[0]["candidate_key"], "RC-PLAINTEXT-CREDENTIAL")
            self.assertEqual(queue[0]["evidence_json"][0]["file_path"], "config/prod.env")
            self.assertNotIn(
                "发现明文生产凭据",
                {item["title"] for item in db("inspector", "issue-list", "--task-key", task["task_key"])},
            )

            forbidden_review = subprocess.run(
                [
                    sys.executable, str(db_tool), "--agent", "developer", "--operator-id", "codex-dev",
                    "candidate-update-status", "--candidate-key", "RC-PLAINTEXT-CREDENTIAL",
                    "--status", "ACCEPTED", "--content", "越权接受",
                ],
                cwd=workspace, env=self.home_env(home), text=True, capture_output=True,
            )
            self.assertNotEqual(forbidden_review.returncode, 0)
            db(
                "inspector", "candidate-update-status", "--candidate-key", "RC-PLAINTEXT-CREDENTIAL",
                "--status", "UNDER_REVIEW", "--content", "开始核实",
            )
            accepted = db(
                "inspector", "candidate-update-status", "--candidate-key", "RC-PLAINTEXT-CREDENTIAL",
                "--status", "ACCEPTED", "--content", "证据成立，转正式问题处理",
            )
            self.assertEqual(accepted["status"], "ACCEPTED")
            accepted_items = db("inspector", "candidate-list", "--status", "ACCEPTED")
            self.assertEqual(accepted_items[0]["reviewed_by"], "trae-inspector")

    def test_task_types_identity_filter_and_continuous_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.run_cmd(home, str(INSTALLER), "install")
            workspace = home / "project"
            workspace.mkdir()
            db_tool = home / ".agent-review" / "bin" / "review-db.py"

            def db(*args: str) -> dict | list:
                role, *command = args
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                return self.run_cmd(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )

            created_review = db(
                "inspector", "task-create", "--task-key", "RT-REVIEW", "--title", "一次检查",
                "--objective", "检查发布变更", "--task-type", "REVIEW",
            )
            self.assertEqual(created_review["task_type"], "REVIEW")
            db("inspector", "task-update-status", "--task-key", "RT-REVIEW", "--status", "CLOSED")
            inspector_reopen = self.run_raw(
                home, str(db_tool), "--agent", "inspector", "--operator-id", "trae-inspector",
                "task-update-status", "--task-key", "RT-REVIEW", "--status", "IN_PROGRESS",
                cwd=workspace,
            )
            self.assertNotEqual(inspector_reopen.returncode, 0)
            self.assertIn("不允许任务状态流转", inspector_reopen.stderr)
            human_reopen = db(
                "human", "task-update-status", "--task-key", "RT-REVIEW", "--status", "IN_PROGRESS",
                "--remark", "Human 判定任务需要继续",
            )
            self.assertEqual(human_reopen["status"], "IN_PROGRESS")
            db("human", "task-update-status", "--task-key", "RT-REVIEW", "--status", "CANCELLED")
            self.assertEqual(
                db("human", "task-update-status", "--task-key", "RT-REVIEW", "--status", "PENDING")["status"],
                "PENDING",
            )
            continuous_args = (
                "task-resolve", "--title", "长期线上 Bug", "--objective", "持续收集线上 Bug",
                "--review-level", "L2", "--review-scope", "trade-flow", "--task-type", "CONTINUOUS",
            )
            continuous_a = db("inspector", *continuous_args, "--baseline-ref", "commit-A")
            continuous_b = db("inspector", *continuous_args, "--baseline-ref", "commit-B")
            self.assertTrue(continuous_a["created"])
            self.assertFalse(continuous_b["created"])
            self.assertEqual(continuous_a["task_key"], continuous_b["task_key"])

            review_args = (
                "task-resolve", "--title", "基线检查", "--objective", "验证 REVIEW identity",
                "--review-level", "L2", "--review-scope", "trade-flow", "--task-type", "REVIEW",
            )
            review_a = db("inspector", *review_args, "--baseline-ref", "commit-A")
            review_b = db("inspector", *review_args, "--baseline-ref", "commit-B")
            self.assertNotEqual(review_a["task_key"], review_b["task_key"])
            continuous_only = db("developer", "task-list", "--task-type", "CONTINUOUS")
            self.assertEqual([item["task_key"] for item in continuous_only], [continuous_a["task_key"]])
            self.assertTrue(all(item["task_type"] == "CONTINUOUS" for item in continuous_only))

            explicit = db(
                "inspector", *continuous_args, "--task-key", continuous_a["task_key"],
                "--baseline-ref", "commit-C",
            )
            self.assertFalse(explicit["created"])
            db("inspector", "task-update-status", "--task-key", continuous_a["task_key"], "--status", "ON_HOLD")
            held = db("inspector", *continuous_args, "--baseline-ref", "commit-D")
            self.assertEqual((held["task_key"], held["status"], held["created"]),
                             (continuous_a["task_key"], "ON_HOLD", False))
            db("human", "task-update-status", "--task-key", continuous_a["task_key"], "--status", "IN_PROGRESS")
            mismatch = self.run_raw(
                home, str(db_tool), "--agent", "inspector", "--operator-id", "trae-inspector",
                "task-resolve", "--task-key", continuous_a["task_key"], "--title", "长期线上 Bug",
                "--objective", "持续收集线上 Bug", "--review-level", "L2",
                "--review-scope", "trade-flow", "--task-type", "REVIEW", cwd=workspace,
            )
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("task_type 创建后不可修改", mismatch.stderr)

            issue = {
                "issue_key": "RI-CONTINUOUS", "title": "长期任务问题", "dimension": "code_quality",
                "severity": "low", "remediation_benefit": "medium", "remediation_cost": "low",
                "disposition": "current_iteration", "confidence": "high", "description": "描述",
                "facts": "事实", "rationale": "依据",
            }
            db(
                "inspector", "issue-create-batch", "--task-key", continuous_a["task_key"],
                "--reason", "报告线上问题", "--issues", json.dumps([issue], ensure_ascii=False),
            )
            db(
                "inspector", "activity-append", "--issue-key", "RI-CONTINUOUS",
                "--activity-type", "VERIFICATION_PASSED", "--content", "无需改码，事实已消除",
            )
            db("inspector", "issue-update-status", "--issue-key", "RI-CONTINUOUS", "--status", "CONFIRMED")
            still_open = db("inspector", "task-list", "--task-type", "CONTINUOUS")
            self.assertEqual(still_open[0]["status"], "IN_PROGRESS")

    def test_developer_and_inspector_can_only_amend_their_own_discussion_content(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.run_cmd(home, str(INSTALLER), "install")
            workspace = home / "project"
            workspace.mkdir()
            db_tool = home / ".agent-review" / "bin" / "review-db.py"

            def db(*args: str) -> dict | list:
                role, *command = args
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                return self.run_cmd(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )

            def fails(role: str, *command: str) -> subprocess.CompletedProcess:
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                result = self.run_raw(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                return result

            db("inspector", "task-create", "--task-key", "RT-AMEND", "--title", "修订测试", "--objective", "验证文案覆盖")
            db("inspector", "version-create", "--task-key", "RT-AMEND", "--reason", "首次检查")
            db(
                "inspector", "issue-create", "--task-key", "RT-AMEND", "--issue-key", "RI-AMEND",
                "--title", "文案修订", "--dimension", "code_quality", "--severity", "medium",
                "--remediation-benefit", "medium", "--remediation-cost", "low",
                "--disposition", "current_iteration", "--confidence", "high",
                "--description", "原始问题描述", "--facts", "事实", "--rationale", "依据",
            )
            developer_discussion = db(
                "developer", "discussion-append", "--issue-key", "RI-AMEND",
                "--topic", "IMPLEMENTATION", "--content", "旧开发说明",
            )
            inspector_discussion = db(
                "inspector", "discussion-append", "--issue-key", "RI-AMEND",
                "--topic", "DESIGN", "--content", "旧审核约束",
            )
            activities = db("developer", "activity-list", "--issue-key", "RI-AMEND")
            created_activity = next(item for item in activities if item["activity_type"] == "ISSUE_CREATED")
            self.assertNotIn("旧开发说明", [item["content"] for item in activities])

            amended = db(
                "developer", "discussion-amend", "--issue-key", "RI-AMEND",
                "--discussion-id", str(developer_discussion["discussion_id"]), "--content", "最新开发说明",
                "--reason", "旧说明会误导后续判断",
            )
            self.assertEqual((amended["amendment_count"], amended["unchanged"]), (1, False))
            current = db("inspector", "discussion-list", "--issue-key", "RI-AMEND")
            amended_discussion = next(item for item in current if item["id"] == developer_discussion["discussion_id"])
            self.assertEqual(amended_discussion["content"], "最新开发说明")
            self.assertEqual(amended_discussion["amendment_count"], 1)
            self.assertIsNotNone(amended_discussion["amended_at"])
            self.assertNotIn("旧开发说明", [item["content"] for item in current])

            with sqlite3.connect(home / ".agent-review" / "data" / "review.db") as conn:
                revision = conn.execute(
                    "SELECT previous_content, replacement_content, amended_by FROM issue_discussion_revision WHERE discussion_id = ?",
                    (developer_discussion["discussion_id"],),
                ).fetchone()
            self.assertEqual(revision, ("旧开发说明", "最新开发说明", "codex-dev"))

            denied = fails(
                "inspector", "discussion-amend", "--issue-key", "RI-AMEND",
                "--discussion-id", str(developer_discussion["discussion_id"]), "--content", "越权修改",
            )
            self.assertIn("自己的讨论", denied.stderr)
            denied = fails(
                "developer", "discussion-amend", "--issue-key", "RI-AMEND",
                "--discussion-id", str(inspector_discussion["discussion_id"]), "--content", "越权修改",
            )
            self.assertIn("自己的讨论", denied.stderr)
            denied = fails(
                "inspector", "activity-amend", "--issue-key", "RI-AMEND",
                "--activity-id", str(created_activity["id"]), "--content", "不得改结构活动",
            )
            self.assertIn("正式结论或结构化记录", denied.stderr)
            denied = fails(
                "human", "discussion-amend", "--issue-key", "RI-AMEND",
                "--discussion-id", str(developer_discussion["discussion_id"]), "--content", "Human 也不能改",
            )
            self.assertIn("自己的讨论", denied.stderr)

            db(
                "inspector", "discussion-amend", "--issue-key", "RI-AMEND",
                "--discussion-id", str(inspector_discussion["discussion_id"]), "--content", "最新审核约束",
            )
            recent = db("developer", "discussion-list", "--issue-key", "RI-AMEND")
            self.assertEqual(recent[-1]["content"], "最新审核约束")

            conclusion = db(
                "inspector", "decision-record", "--issue-key", "RI-AMEND",
                "--decision-type", "DISCUSSION_CONCLUSION", "--scope-key", "design-boundary",
                "--outcome", "AGREED", "--content", "采用最新审核约束",
                "--source-discussion-ids", json.dumps([
                    developer_discussion["discussion_id"], inspector_discussion["discussion_id"],
                ]),
            )
            self.assertGreater(conclusion["decision_id"], 0)
            decisions = db("developer", "decision-list", "--issue-key", "RI-AMEND")
            self.assertEqual(decisions[0]["content"], "采用最新审核约束")
            self.assertEqual(
                decisions[0]["source_discussion_ids_json"],
                [developer_discussion["discussion_id"], inspector_discussion["discussion_id"]],
            )
            replacement = db(
                "inspector", "decision-record", "--issue-key", "RI-AMEND",
                "--decision-type", "DISCUSSION_CONCLUSION", "--scope-key", "design-boundary",
                "--outcome", "AGREED", "--content", "采用修订后的最终约束",
                "--source-discussion-ids", json.dumps([inspector_discussion["discussion_id"]]),
            )
            current_decisions = db("developer", "decision-list", "--issue-key", "RI-AMEND")
            self.assertEqual([item["content"] for item in current_decisions], ["采用修订后的最终约束"])
            all_decisions = db(
                "developer", "decision-list", "--issue-key", "RI-AMEND", "--include-superseded",
            )
            self.assertEqual([item["effective"] for item in all_decisions], [0, 1])
            self.assertEqual(all_decisions[0]["superseded_by_id"], replacement["decision_id"])
            denied = fails(
                "developer", "decision-record", "--issue-key", "RI-AMEND",
                "--decision-type", "DISCUSSION_CONCLUSION", "--outcome", "AGREED",
                "--content", "Developer 不能登记最终结论",
            )
            self.assertIn("只有 inspector", denied.stderr)

    def test_v006_database_migrates_without_losing_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy_script_dir = root / "legacy-installer"
            legacy_migrations = legacy_script_dir / "migrations"
            legacy_migrations.mkdir(parents=True)
            source_migrations = ROOT / "scripts" / "code-inspector-installer" / "migrations"
            for migration in sorted(source_migrations.glob("V00[1-6]__*.sql")):
                shutil.copy2(migration, legacy_migrations / migration.name)
            database = root / "review.db"
            with mock.patch.object(INSTALLER_MODULE, "SCRIPT_DIR", legacy_script_dir):
                first = INSTALLER_MODULE.migrate(database)
            self.assertEqual(len(first["applied"]), 6)

            with sqlite3.connect(database) as conn:
                conn.execute(
                    """INSERT INTO review_task(
                        id, task_key, project_name, project_path, title, objective, current_version,
                        status, review_level, review_scope, baseline_ref, scope_fingerprint
                    ) VALUES (41, 'RT-OLD', 'project', '/project', '旧任务', '旧目标', 1,
                              'IN_PROGRESS', 'L2', 'scope', 'commit-old', 'old-fingerprint')"""
                )
                conn.execute(
                    """INSERT INTO review_task_version(id, task_id, version_no, reason, created_by)
                       VALUES (42, 41, 1, '旧扫描', 'old-inspector')"""
                )
                conn.execute(
                    """INSERT INTO review_issue(
                        id, issue_key, task_id, introduced_version, title, dimension, severity,
                        remediation_benefit, remediation_cost, disposition, confidence, status,
                        description, facts, rationale, current_attempt_no, dedupe_key
                    ) VALUES (43, 'RI-OLD', 41, 1, '旧问题', 'code_quality', 'medium',
                              'medium', 'low', 'current_iteration', 'high', 'IN_PROGRESS',
                              '旧描述', '旧事实', '旧依据', 2, 'old-dedupe')"""
                )
                conn.execute("UPDATE review_task_version SET source_issue_id = 43 WHERE id = 42")
                conn.execute(
                    """INSERT INTO review_issue(
                        id, issue_key, task_id, introduced_version, parent_issue_id, title, dimension,
                        severity, remediation_benefit, remediation_cost, disposition, confidence, status,
                        description, facts, rationale, current_attempt_no, dedupe_key
                    ) VALUES (47, 'RI-OLD-CHILD', 41, 1, 43, '旧子问题', 'code_quality', 'low',
                              'low', 'low', 'observe', 'medium', 'PROPOSED', '子描述', '子事实',
                              '子依据', 0, 'old-child-dedupe')"""
                )
                conn.execute(
                    """INSERT INTO issue_activity(
                        id, issue_id, attempt_no, activity_type, operator_type, operator_id,
                        content, result_status, code_reference_json, metadata_json
                    ) VALUES (44, 43, 2, 'COMMENT_ADDED', 'INSPECTOR_AGENT', 'old-inspector',
                              '旧活动', 'IN_PROGRESS', '[{\"file_path\":\"old.py\"}]', '{\"kept\":true}')"""
                )
                conn.execute(
                    """INSERT INTO agent_audit_log(
                        id, agent_id, action, resource_type, resource_id, success, detail
                    ) VALUES (45, 'old-inspector', 'old.action', 'review_issue', 'RI-OLD', 1, '旧审计')"""
                )
                conn.execute(
                    """INSERT INTO issue_candidate(
                        id, candidate_key, task_id, title, description, facts, rationale,
                        submitted_by, status
                    ) VALUES (46, 'RC-OLD', 41, '旧候选', '描述', '事实', '依据',
                              'old-developer', 'SUBMITTED')"""
                )
                conn.commit()

            upgraded = INSTALLER_MODULE.migrate(database, root / "backups")
            self.assertEqual(upgraded["applied"], [
                "V007__design_workflow_and_continuous_tasks.sql",
                "V008__human_confirmation_escalation.sql",
                "V009__issue_stage_plans.sql",
                "V010__stage_baselines_and_review_gates.sql",
                "V011__activity_amendments.sql",
                "V012__lean_issues_discussions_and_decisions.sql",
                "V013__issue_thread_runtime.sql",
                "V014__runtime_identity_leases_and_outbox.sql",
            ])
            self.assertIsNotNone(upgraded["backup"])
            with sqlite3.connect(database) as conn:
                conn.row_factory = sqlite3.Row
                self.assertEqual(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], 14)
                task = conn.execute("SELECT * FROM review_task WHERE id = 41").fetchone()
                self.assertEqual((task["task_key"], task["task_type"], task["scope_fingerprint"]),
                                 ("RT-OLD", "REVIEW", "old-fingerprint"))
                issue = conn.execute("SELECT * FROM review_issue WHERE id = 43").fetchone()
                self.assertEqual((issue["issue_key"], issue["current_attempt_no"], issue["dedupe_key"]),
                                 ("RI-OLD", 2, "old-dedupe"))
                self.assertEqual(issue["summary"], "旧描述")
                self.assertEqual(conn.execute("SELECT parent_issue_id FROM review_issue WHERE id = 47").fetchone()[0], 43)
                activity = conn.execute("SELECT * FROM issue_activity WHERE id = 44").fetchone()
                self.assertEqual((activity["content"], activity["created_at"] is not None), ("旧活动", True))
                discussion = conn.execute("SELECT * FROM issue_discussion WHERE source_activity_id = 44").fetchone()
                self.assertEqual((discussion["topic"], discussion["content"]), ("GENERAL", "旧活动"))
                self.assertEqual(conn.execute("SELECT source_issue_id FROM review_task_version WHERE id = 42").fetchone()[0], 43)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM agent_audit_log WHERE id = 45").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM issue_candidate WHERE id = 46").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM issue_stage").fetchone()[0], 0)
                stage_columns = {row[1] for row in conn.execute("PRAGMA table_info(issue_stage)")}
                self.assertTrue({
                    "governance_version", "planned_change_scope_json", "review_round",
                    "review_findings_json", "historical_regression_json", "baseline_json",
                }.issubset(stage_columns))
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                conn.execute("UPDATE review_issue SET status = 'DESIGN_REQUIRED' WHERE id = 43")
                conn.execute(
                    """INSERT INTO issue_activity(
                        issue_id, attempt_no, activity_type, operator_type, operator_id, content
                    ) VALUES (43, 2, 'DESIGN_REQUESTED', 'INSPECTOR_AGENT', 'new-inspector', '新设计约束')"""
                )
                conn.execute("UPDATE review_issue SET status = 'HUMAN_CONFIRMATION_REQUIRED' WHERE id = 43")
                conn.execute(
                    """INSERT INTO issue_activity(
                        issue_id, attempt_no, activity_type, operator_type, operator_id, content
                    ) VALUES (43, 2, 'HUMAN_CONFIRMATION_REQUESTED', 'INSPECTOR_AGENT',
                              'new-inspector', '需要人工决定')"""
                )

    def test_v009_stage_history_migrates_as_legacy_governance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy_script_dir = root / "v009-installer"
            legacy_migrations = legacy_script_dir / "migrations"
            legacy_migrations.mkdir(parents=True)
            source_migrations = ROOT / "scripts" / "code-inspector-installer" / "migrations"
            for migration in sorted(source_migrations.glob("V*.sql")):
                if INSTALLER_MODULE.migration_version(migration) <= 9:
                    shutil.copy2(migration, legacy_migrations / migration.name)
            database = root / "review.db"
            with mock.patch.object(INSTALLER_MODULE, "SCRIPT_DIR", legacy_script_dir):
                INSTALLER_MODULE.migrate(database)
            with sqlite3.connect(database) as conn:
                conn.execute(
                    """INSERT INTO review_task(
                        id, task_key, project_name, project_path, title, objective,
                        current_version, task_type, status, scope_fingerprint
                    ) VALUES (1, 'RT-V9', 'project', '/project', 'V9 Task', 'legacy',
                              1, 'REVIEW', 'IN_PROGRESS', 'legacy')"""
                )
                conn.execute(
                    """INSERT INTO review_issue(
                        id, issue_key, task_id, introduced_version, title, dimension, severity,
                        remediation_benefit, remediation_cost, disposition, confidence, status,
                        description, facts, rationale, current_attempt_no, dedupe_key
                    ) VALUES (2, 'RI-V9-STAGE', 1, 1, '旧 Stage', 'code_quality', 'medium',
                              'medium', 'low', 'current_iteration', 'high', 'IN_PROGRESS',
                              '描述', '事实', '依据', 0, 'legacy-stage')"""
                )
                conn.execute(
                    """INSERT INTO issue_stage(
                        id, issue_id, plan_no, stage_no, title, objective, acceptance_criteria,
                        status, submitted_commit_sha, developer_summary, test_evidence_json
                    ) VALUES (3, 2, 1, 1, '旧阶段', '旧目标', '旧标准', 'PENDING_REVIEW',
                              'abc123', '旧提交', '["legacy passed"]')"""
                )
                conn.execute(
                    """INSERT INTO issue_activity(
                        id, issue_id, attempt_no, activity_type, operator_type, operator_id, content
                    ) VALUES (4, 2, 0, 'STAGE_SUBMITTED', 'DEVELOPMENT_AGENT', 'old-dev', '旧 Stage 提交')"""
                )
                conn.commit()

            upgraded = INSTALLER_MODULE.migrate(database, root / "backups")
            self.assertEqual(upgraded["applied"], [
                "V010__stage_baselines_and_review_gates.sql",
                "V011__activity_amendments.sql",
                "V012__lean_issues_discussions_and_decisions.sql",
                "V013__issue_thread_runtime.sql",
                "V014__runtime_identity_leases_and_outbox.sql",
            ])
            with sqlite3.connect(database) as conn:
                conn.row_factory = sqlite3.Row
                stage = conn.execute("SELECT * FROM issue_stage WHERE id = 3").fetchone()
                self.assertEqual((stage["governance_version"], stage["status"], stage["submitted_commit_sha"]),
                                 (1, "PENDING_REVIEW", "abc123"))
                self.assertEqual(stage["review_round"], 0)
                self.assertEqual(stage["baseline_json"], "{}")
                activity = conn.execute("SELECT * FROM issue_activity WHERE id = 4").fetchone()
                self.assertEqual((activity["activity_type"], activity["content"]),
                                 ("STAGE_SUBMITTED", "旧 Stage 提交"))
                conn.execute(
                    """INSERT INTO issue_activity(
                        issue_id, attempt_no, activity_type, operator_type, operator_id, content
                    ) VALUES (2, 0, 'STAGE_SCOPE_DECLARED', 'DEVELOPMENT_AGENT', 'new-dev', '补充影响声明')"""
                )
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_v013_runtime_data_upgrades_to_operator_identity_and_leases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            v13_dir = root / "v13-installer"
            migrations = v13_dir / "migrations"
            migrations.mkdir(parents=True)
            source = ROOT / "scripts" / "code-inspector-installer" / "migrations"
            for migration in sorted(source.glob("V*.sql")):
                if INSTALLER_MODULE.migration_version(migration) <= 13:
                    shutil.copy2(migration, migrations / migration.name)
            database = root / "review.db"
            with mock.patch.object(INSTALLER_MODULE, "SCRIPT_DIR", v13_dir):
                INSTALLER_MODULE.migrate(database)
            with closing(sqlite3.connect(database)) as conn, conn:
                conn.execute("INSERT INTO review_task(id,task_key,project_name,project_path,title,objective,status) VALUES(1,'RT-V13','p','/p','t','o','IN_PROGRESS')")
                conn.execute(
                    """INSERT INTO review_issue(id,issue_key,task_id,introduced_version,title,dimension,severity,
                       remediation_benefit,remediation_cost,disposition,confidence,status,description,facts,rationale)
                       VALUES(1,'RI-V13',1,1,'i','code_quality','medium','medium','low','current_iteration','high','IN_PROGRESS','d','f','r')"""
                )
                conn.execute(
                    """INSERT INTO code_inspector_thread(issue_id,issue_key,role,thread_id,thread_status,cwd)
                       VALUES(1,'RI-V13','inspector','thr-v13','WAITING','/p')"""
                )
                conn.execute(
                    """INSERT INTO code_inspector_event(event_id,idempotency_key,issue_key,role,event_type,status,error_message)
                       VALUES('evt-v13','idem-v13','RI-V13','inspector','STAGE_SUBMITTED','FAILED','old error')"""
                )
            upgraded = INSTALLER_MODULE.migrate(database, root / "backups")
            self.assertEqual(upgraded["applied"], ["V014__runtime_identity_leases_and_outbox.sql"])
            with closing(sqlite3.connect(database)) as conn, conn:
                conn.row_factory = sqlite3.Row
                thread = conn.execute("SELECT * FROM code_inspector_thread WHERE thread_id='thr-v13'").fetchone()
                event = conn.execute("SELECT * FROM code_inspector_event WHERE event_id='evt-v13'").fetchone()
                self.assertEqual(
                    (thread["operator_id"], thread["agent_platform"], thread["runtime_backend"]),
                    ("codex-insp", "codex", "codex-app-server"),
                )
                self.assertEqual(
                    (event["operator_id"], event["attempt_count"], event["last_error"]),
                    ("codex-insp", 0, "old error"),
                )
                columns = {row[1] for row in conn.execute("PRAGMA table_info(code_inspector_event)")}
                self.assertTrue({"attempt_count", "claimed_at", "lease_until", "worker_id", "next_attempt_at", "failure_kind"}.issubset(columns))
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_design_commands_permissions_atomicity_and_attempt_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.run_cmd(home, str(INSTALLER), "install")
            workspace = home / "project"
            workspace.mkdir()
            db_tool = home / ".agent-review" / "bin" / "review-db.py"

            def db(*args: str) -> dict | list:
                role, *command = args
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                return self.run_cmd(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )

            def fails(role: str, *command: str) -> subprocess.CompletedProcess:
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                result = self.run_raw(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                return result

            task = db(
                "inspector", "task-resolve", "--title", "复杂设计", "--objective", "验证设计审核",
                "--review-level", "L3", "--review-scope", "all",
            )
            issue = {
                "issue_key": "RI-DESIGN", "title": "跨模块版本覆盖", "dimension": "architecture_extensibility",
                "severity": "high", "remediation_benefit": "high", "remediation_cost": "high",
                "disposition": "current_iteration", "confidence": "high", "description": "描述",
                "facts": "事实", "rationale": "依据",
            }
            db(
                "inspector", "issue-create-batch", "--task-key", task["task_key"],
                "--reason", "复杂问题", "--issues", json.dumps([issue], ensure_ascii=False),
            )
            initial_activity_count = len(db("developer", "activity-list", "--issue-key", "RI-DESIGN"))
            database = home / ".agent-review" / "data" / "review.db"
            with closing(sqlite3.connect(database)) as conn, conn:
                conn.execute(
                    """CREATE TRIGGER force_design_update_failure
                       BEFORE UPDATE OF status ON review_issue
                       WHEN NEW.status = 'DESIGN_REQUIRED'
                       BEGIN SELECT RAISE(ABORT, 'forced design rollback'); END"""
                )
            fails(
                "inspector", "design-request", "--issue-key", "RI-DESIGN",
                "--content", "该调用用于验证活动与状态同事务回滚",
            )
            self.assertEqual(db("developer", "issue-get", "--issue-key", "RI-DESIGN")["status"], "PROPOSED")
            self.assertEqual(len(db("developer", "activity-list", "--issue-key", "RI-DESIGN")), initial_activity_count)
            with sqlite3.connect(database) as conn:
                conn.execute("DROP TRIGGER force_design_update_failure")
            fails("developer", "design-request", "--issue-key", "RI-DESIGN", "--content", "越权")
            fails(
                "developer", "activity-append", "--issue-key", "RI-DESIGN",
                "--activity-type", "DESIGN_SUBMITTED", "--content", "不得用活动拼装设计提交",
            )
            self.assertEqual(len(db("developer", "activity-list", "--issue-key", "RI-DESIGN")), initial_activity_count)

            requested = db(
                "inspector", "design-request", "--issue-key", "RI-DESIGN", "--content",
                "跨模块版本语义不能复用；需说明兼容、并发和回灌边界",
            )
            self.assertEqual((requested["status"], requested["attempt_no"]), ("DESIGN_REQUIRED", 0))
            fails("developer", "implementation-submit", "--issue-key", "RI-DESIGN", "--content", "禁止实现")
            submitted = db(
                "developer", "design-submit", "--issue-key", "RI-DESIGN", "--content", "按 Domain 拆分版本并兼容历史",
                "--code-reference", json.dumps([{"file_path": "writer.py", "line_start": 20}]),
                "--metadata", json.dumps({"tests": ["backfill", "concurrency"]}),
            )
            self.assertEqual((submitted["status"], submitted["attempt_no"]), ("DESIGN_PENDING_REVIEW", 0))
            activity_count = len(db("developer", "activity-list", "--issue-key", "RI-DESIGN"))
            fails("developer", "design-review", "--issue-key", "RI-DESIGN", "--decision", "approved", "--content", "越权批准")
            fails("developer", "implementation-submit", "--issue-key", "RI-DESIGN", "--content", "尚未批准")
            self.assertEqual(len(db("developer", "activity-list", "--issue-key", "RI-DESIGN")), activity_count)

            db(
                "inspector", "discussion-append", "--issue-key", "RI-DESIGN",
                "--topic", "DESIGN", "--content", "补充：实时消费与回灌不能互相覆盖",
            )
            rejected = db(
                "inspector", "design-review", "--issue-key", "RI-DESIGN", "--decision", "rejected",
                "--content", "历史缺失版本的兼容路径未说明，请补充读取回退规则",
            )
            self.assertEqual((rejected["status"], rejected["attempt_no"]), ("DESIGN_REQUIRED", 0))
            db("developer", "design-submit", "--issue-key", "RI-DESIGN", "--content", "补充 legacy fallback")
            approved = db(
                "inspector", "design-review", "--issue-key", "RI-DESIGN", "--decision", "approved",
                "--content", "批准；不得改变历史数据读取语义",
            )
            self.assertEqual((approved["status"], approved["attempt_no"]), ("IN_PROGRESS", 0))
            fails("inspector", "implementation-submit", "--issue-key", "RI-DESIGN", "--content", "Inspector 禁止实现")

            first_impl = db("developer", "implementation-submit", "--issue-key", "RI-DESIGN", "--content", "首次实现")
            self.assertEqual(first_impl["attempt_no"], 1)
            fails(
                "inspector", "issue-update-status", "--issue-key", "RI-DESIGN", "--status", "IN_PROGRESS",
                "--content", "不能只改状态而不记录实现失败",
            )
            db(
                "inspector", "activity-append", "--issue-key", "RI-DESIGN",
                "--activity-type", "VERIFICATION_FAILED", "--content", "遗漏一个已批准分支",
            )
            returned = db(
                "inspector", "issue-update-status", "--issue-key", "RI-DESIGN", "--status", "IN_PROGRESS",
                "--content", "设计正确，补齐遗漏分支",
            )
            self.assertEqual(returned["attempt_no"], 1)
            second_impl = db("developer", "implementation-submit", "--issue-key", "RI-DESIGN", "--content", "第二次实现")
            self.assertEqual(second_impl["attempt_no"], 2)
            redesign = db(
                "inspector", "issue-update-status", "--issue-key", "RI-DESIGN", "--status", "REDESIGN_REQUIRED",
                "--content", "新证据推翻原数据流，需要重新设计",
            )
            self.assertEqual(redesign["attempt_no"], 2)
            fails("developer", "implementation-submit", "--issue-key", "RI-DESIGN", "--content", "不得继续实现")
            fails("developer", "issue-update-status", "--issue-key", "RI-DESIGN", "--status", "IN_PROGRESS")
            db("developer", "design-submit", "--issue-key", "RI-DESIGN", "--content", "重做数据流方案")
            db(
                "inspector", "design-review", "--issue-key", "RI-DESIGN", "--decision", "approved",
                "--content", "基于新证据批准",
            )
            third_impl = db("developer", "implementation-submit", "--issue-key", "RI-DESIGN", "--content", "重设计后实现")
            self.assertEqual(third_impl["attempt_no"], 3)
            db(
                "inspector", "activity-append", "--issue-key", "RI-DESIGN",
                "--activity-type", "VERIFICATION_PASSED", "--content", "验证通过",
            )
            confirmed = db("inspector", "issue-update-status", "--issue-key", "RI-DESIGN", "--status", "CONFIRMED")
            self.assertEqual(confirmed["status"], "CONFIRMED")
            activities = db("developer", "activity-list", "--issue-key", "RI-DESIGN")
            self.assertTrue({"DESIGN_REQUESTED", "DESIGN_SUBMITTED", "DESIGN_REJECTED", "DESIGN_APPROVED"}.issubset(
                {item["activity_type"] for item in activities}
            ))
            self.assertEqual(
                [item["attempt_no"] for item in activities if item["activity_type"] == "DESIGN_SUBMITTED"],
                [0, 0, 2],
            )

    def test_stage_plan_serial_execution_redesign_history_and_attempt_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.run_cmd(home, str(INSTALLER), "install")
            workspace = home / "project"
            workspace.mkdir()
            db_tool = home / ".agent-review" / "bin" / "review-db.py"

            def db(*args: str) -> dict | list:
                role, *command = args
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                return self.run_cmd(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )

            def fails(role: str, *command: str) -> subprocess.CompletedProcess:
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                result = self.run_raw(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                return result

            def prepare(stage_no: int, protected: list[str] | None = None) -> dict:
                return db(
                    "developer", "stage-prepare", "--issue-key", "RI-STAGE",
                    "--stage-no", str(stage_no),
                    "--change-scope", json.dumps({"files": [f"stage{stage_no}.py"]}),
                    "--change-reason", f"实现 Stage {stage_no} 验收目标",
                    "--protected-behaviors", json.dumps(protected or [], ensure_ascii=False),
                )

            def review_payload(
                criteria: list[str], history: dict[str, dict] | None = None,
                findings: dict[str, list[dict]] | None = None,
                failed_criteria: set[str] | None = None,
            ) -> str:
                failed_criteria = failed_criteria or set()
                return json.dumps({
                    "findings": findings or {"BLOCKER": [], "MUST": [], "SHOULD": [], "NIT": []},
                    "historical_regression": history or {},
                    "current_acceptance": [
                        {
                            "criterion": criterion,
                            "status": "FAIL" if criterion in failed_criteria else "PASS",
                            "evidence": [f"{criterion}: verified"],
                        }
                        for criterion in criteria
                    ],
                }, ensure_ascii=False)

            def stage_baseline(stage_no: int) -> str:
                return json.dumps({
                    "verified_behaviors": [f"Stage {stage_no} behavior"],
                    "input_output_contracts": [f"Stage {stage_no} I/O"],
                    "business_semantics": [f"Stage {stage_no} semantics"],
                    "tests": [f"stage{stage_no}_regression"],
                }, ensure_ascii=False)

            task = db(
                "inspector", "task-resolve", "--title", "Stage 治理", "--objective", "防止复杂实现跑偏",
                "--review-level", "L3", "--review-scope", "writer,backfill",
            )
            base_issue = {
                "title": "领域版本与回灌并发冲突", "dimension": "architecture_extensibility",
                "severity": "high", "remediation_benefit": "high", "remediation_cost": "high",
                "disposition": "current_iteration", "confidence": "high", "description": "描述",
                "facts": "事实", "rationale": "依据",
            }
            issue = {**base_issue, "issue_key": "RI-STAGE"}
            db(
                "inspector", "issue-create-batch", "--task-key", task["task_key"],
                "--reason", "复杂问题", "--issues", json.dumps([issue], ensure_ascii=False),
            )
            db("inspector", "design-request", "--issue-key", "RI-STAGE", "--content", "必须分阶段验收")
            db("developer", "design-submit", "--issue-key", "RI-STAGE", "--content", "分模型、主链路、回灌三阶段")
            stages = [
                {"stage_no": 1, "title": "领域版本模型", "objective": "建立独立版本模型",
                 "acceptance_criteria": ["旧数据可读", "版本单测通过"]},
                {"stage_no": 2, "title": "Writer 主链路", "objective": "接入实时写入",
                 "acceptance_criteria": "并发写入不能互相覆盖"},
                {"stage_no": 3, "title": "回灌兼容", "objective": "安全接入历史回灌",
                 "acceptance_criteria": "回灌不能覆盖更晚线上事实"},
            ]

            fails(
                "developer", "stage-plan-create", "--issue-key", "RI-STAGE",
                "--stages", json.dumps(stages, ensure_ascii=False),
            )
            database = home / ".agent-review" / "data" / "review.db"
            with sqlite3.connect(database) as conn:
                conn.execute(
                    """CREATE TRIGGER force_stage_plan_activity_failure
                       BEFORE INSERT ON issue_activity
                       WHEN NEW.activity_type = 'STAGE_PLAN_CREATED'
                       BEGIN SELECT RAISE(ABORT, 'forced plan rollback'); END"""
                )
            fails(
                "inspector", "stage-plan-create", "--issue-key", "RI-STAGE",
                "--stages", json.dumps(stages, ensure_ascii=False),
            )
            with sqlite3.connect(database) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM issue_stage").fetchone()[0], 0)
                conn.execute("DROP TRIGGER force_stage_plan_activity_failure")

            plan = db(
                "inspector", "stage-plan-create", "--issue-key", "RI-STAGE",
                "--stages", json.dumps(stages, ensure_ascii=False),
            )
            self.assertEqual(plan["plan_no"], 1)
            self.assertEqual([item["status"] for item in db("developer", "stage-list", "--issue-key", "RI-STAGE")],
                             ["PLANNED", "PLANNED", "PLANNED"])
            db(
                "inspector", "design-review", "--issue-key", "RI-STAGE", "--decision", "approved",
                "--content", "按三个 Stage 串行执行",
            )
            self.assertEqual(
                [item["status"] for item in db("developer", "stage-list", "--issue-key", "RI-STAGE")],
                ["IN_PROGRESS", "PLANNED", "PLANNED"],
            )
            prepare_required = fails(
                "developer", "stage-submit", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--content", "跳过准备", "--commit-sha", "bad",
                "--diff-summary", "diff", "--test-evidence", json.dumps(["unit: passed"]),
                "--code-reference", json.dumps([{"file_path": "domain.py"}]),
            )
            self.assertIn("stage-prepare", prepare_required.stderr)
            fails(
                "inspector", "stage-prepare", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--change-scope", json.dumps({"files": ["domain.py"]}),
                "--change-reason", "Inspector 不得替 Dev 声明",
            )
            prepared = prepare(1)
            self.assertEqual(prepared["historical_baselines"], [])
            fails("developer", "stage-submit", "--issue-key", "RI-STAGE", "--stage-no", "2",
                  "--content", "提前提交", "--commit-sha", "bad")
            fails("developer", "implementation-submit", "--issue-key", "RI-STAGE", "--content", "提前最终提交")
            fails("inspector", "stage-submit", "--issue-key", "RI-STAGE", "--stage-no", "1",
                  "--content", "越权", "--commit-sha", "bad")

            with sqlite3.connect(database) as conn:
                conn.execute(
                    """CREATE TRIGGER force_stage_submit_activity_failure
                       BEFORE INSERT ON issue_activity
                       WHEN NEW.activity_type = 'STAGE_SUBMITTED'
                       BEGIN SELECT RAISE(ABORT, 'forced submit rollback'); END"""
                )
            fails(
                "developer", "stage-submit", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--content", "模型完成", "--commit-sha", "abc001",
                "--diff-summary", "新增领域版本模型", "--test-evidence", json.dumps(["unit: passed"]),
                "--code-reference", json.dumps([{"file_path": "domain.py", "line_start": 10}]),
            )
            self.assertEqual(db("developer", "stage-get", "--issue-key", "RI-STAGE", "--stage-no", "1")["status"],
                             "IN_PROGRESS")
            with sqlite3.connect(database) as conn:
                conn.execute("DROP TRIGGER force_stage_submit_activity_failure")

            submitted = db(
                "developer", "stage-submit", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--content", "模型完成", "--commit-sha", "abc001",
                "--diff-summary", "新增领域版本模型",
                "--test-evidence", json.dumps(["unit: passed"]),
                "--code-reference", json.dumps([{"file_path": "domain.py", "line_start": 10}]),
            )
            self.assertEqual(submitted["status"], "PENDING_REVIEW")
            first_stage_submission = next(
                item for item in db("developer", "activity-list", "--issue-key", "RI-STAGE")
                if item["activity_type"] == "STAGE_SUBMITTED"
            )
            db(
                "developer", "activity-amend", "--issue-key", "RI-STAGE",
                "--activity-id", str(first_stage_submission["id"]),
                "--content", "模型完成并通过单测", "--reason", "补全测试结论",
            )
            self.assertEqual(
                db("developer", "stage-get", "--issue-key", "RI-STAGE", "--stage-no", "1")["developer_summary"],
                "模型完成并通过单测",
            )
            self.assertEqual(db("developer", "issue-get", "--issue-key", "RI-STAGE")["current_attempt_no"], 0)
            before_review_activities = len(db("developer", "activity-list", "--issue-key", "RI-STAGE"))
            with sqlite3.connect(database) as conn:
                conn.execute(
                    """CREATE TRIGGER force_stage_review_update_failure
                       BEFORE UPDATE OF status ON issue_stage
                       WHEN OLD.status = 'PENDING_REVIEW' AND NEW.status = 'IN_PROGRESS'
                       BEGIN SELECT RAISE(ABORT, 'forced review rollback'); END"""
                )
            fails(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--decision", "rejected", "--content", "验证验收事务回滚",
                "--review-result", review_payload(
                    ["旧数据可读", "版本单测通过"],
                    findings={
                        "BLOCKER": [],
                        "MUST": [{"id": "M-LEGACY", "summary": "缺少兼容测试", "evidence": "test missing", "risk": "旧数据不可读"}],
                        "SHOULD": [], "NIT": [],
                    },
                    failed_criteria={"旧数据可读"},
                ),
            )
            self.assertEqual(db("developer", "stage-get", "--issue-key", "RI-STAGE", "--stage-no", "1")["status"],
                             "PENDING_REVIEW")
            self.assertEqual(len(db("developer", "activity-list", "--issue-key", "RI-STAGE")),
                             before_review_activities)
            with sqlite3.connect(database) as conn:
                conn.execute("DROP TRIGGER force_stage_review_update_failure")
            rejected = db(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--decision", "rejected", "--content", "缺少 legacy fallback 测试",
                "--review-result", review_payload(
                    ["旧数据可读", "版本单测通过"],
                    findings={
                        "BLOCKER": [],
                        "MUST": [{"id": "M-LEGACY", "summary": "缺少兼容测试", "evidence": "test missing", "risk": "旧数据不可读"}],
                        "SHOULD": [{"id": "S-NAME", "summary": "测试名可更清晰"}], "NIT": [],
                    },
                    failed_criteria={"旧数据可读"},
                ),
            )
            self.assertEqual(rejected["status"], "IN_PROGRESS")
            db(
                "developer", "stage-submit", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--content", "补齐兼容测试", "--commit-sha", "abc002",
                "--diff-summary", "增加 legacy fallback 及其回归测试",
                "--test-evidence", json.dumps(["unit: passed", "legacy: passed"]),
                "--code-reference", json.dumps([{"file_path": "domain.py", "line_start": 10}]),
                "--resolved-findings", json.dumps(["M-LEGACY"]),
            )
            late_must = fails(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--decision", "rejected", "--content", "试图新增未解释的阻断项",
                "--review-result", review_payload(
                    ["旧数据可读", "版本单测通过"],
                    findings={
                        "BLOCKER": [],
                        "MUST": [{"id": "M-LATE", "summary": "新增阻断", "evidence": "new", "risk": "risk"}],
                        "SHOULD": [], "NIT": [],
                    },
                    failed_criteria={"版本单测通过"},
                ),
            )
            self.assertIn("why_not_found_earlier", late_must.stderr)
            second_round_should = fails(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--decision", "approved", "--content", "试图新增无关优化",
                "--review-result", review_payload(
                    ["旧数据可读", "版本单测通过"],
                    findings={
                        "BLOCKER": [], "MUST": [],
                        "SHOULD": [{"id": "S-NEW", "summary": "可以进一步抽象"}], "NIT": [],
                    },
                ),
                "--baseline", stage_baseline(1),
            )
            self.assertIn("不得新增无关 SHOULD", second_round_should.stderr)
            approved = db(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "1",
                "--decision", "approved", "--content", "模型与兼容测试满足标准",
                "--review-result", review_payload(
                    ["旧数据可读", "版本单测通过"],
                    findings={
                        "BLOCKER": [], "MUST": [],
                        "SHOULD": [{"id": "S-NAME", "summary": "测试名可更清晰"}], "NIT": [],
                    },
                ),
                "--baseline", stage_baseline(1),
            )
            self.assertEqual((approved["inspection_result"], approved["final_decision"]), ("PASS", "PASS"))
            self.assertEqual(approved["next_stage_no"], 2)
            approved_activity = next(
                item for item in reversed(db("inspector", "activity-list", "--issue-key", "RI-STAGE"))
                if item["activity_type"] == "STAGE_APPROVED" and item["metadata_json"]["stage_no"] == 1
            )
            locked = fails(
                "inspector", "activity-amend", "--issue-key", "RI-STAGE",
                "--activity-id", str(approved_activity["id"]),
                "--content", "模型、兼容测试与累计回归均满足标准",
            )
            self.assertIn("正式结论", locked.stderr)
            self.assertEqual(
                db("inspector", "stage-get", "--issue-key", "RI-STAGE", "--stage-no", "1")["review_comment"],
                "模型与兼容测试满足标准",
            )
            self.assertEqual(
                [item["status"] for item in db("developer", "stage-list", "--issue-key", "RI-STAGE")],
                ["APPROVED", "IN_PROGRESS", "PLANNED"],
            )
            prepare(2, ["Stage 1 behavior", "Stage 1 I/O"])
            db(
                "developer", "stage-submit", "--issue-key", "RI-STAGE", "--stage-no", "2",
                "--content", "Stage 2 完成", "--commit-sha", "abc003",
                "--diff-summary", "Writer 接入领域版本",
                "--test-evidence", json.dumps(["stage1_regression: passed", "writer: passed"]),
                "--code-reference", json.dumps([{"file_path": "writer.py", "line_start": 20}]),
            )
            should_only_reject = fails(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "2",
                "--decision", "rejected", "--content", "仅有可选抽象优化",
                "--review-result", review_payload(
                    ["并发写入不能互相覆盖"],
                    history={"1": {"status": "PASS", "evidence": ["stage1_regression: passed"]}},
                    findings={
                        "BLOCKER": [], "MUST": [],
                        "SHOULD": [{"id": "S-ABSTRACT", "summary": "可提取辅助类"}], "NIT": [],
                    },
                ),
            )
            self.assertIn("Inspector 必须 PASS", should_only_reject.stderr)
            db(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "2",
                "--decision", "approved", "--content", "Stage 2 达到交付标准；优化进入 Backlog",
                "--review-result", review_payload(
                    ["并发写入不能互相覆盖"],
                    history={"1": {"status": "PASS", "evidence": ["stage1_regression: passed"]}},
                    findings={
                        "BLOCKER": [], "MUST": [],
                        "SHOULD": [{"id": "S-ABSTRACT", "summary": "可提取辅助类"}], "NIT": [],
                    },
                ),
                "--baseline", stage_baseline(2),
            )

            prepared_stage3 = prepare(3, ["Stage 1 behavior", "Stage 1 I/O", "Stage 2 behavior"])
            self.assertEqual(
                [item["stage_no"] for item in prepared_stage3["historical_baselines"]], [1, 2],
            )
            db(
                "developer", "stage-submit", "--issue-key", "RI-STAGE", "--stage-no", "3",
                "--content", "Stage 3 完成", "--commit-sha", "abc004",
                "--diff-summary", "接入回灌兼容路径",
                "--test-evidence", json.dumps([
                    "stage1_regression: failed", "stage2_regression: passed", "backfill: passed",
                ]),
                "--code-reference", json.dumps([{"file_path": "backfill.py", "line_start": 30}]),
            )
            unclassified_regression = fails(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "3",
                "--decision", "rejected", "--content", "未按 BLOCKER 标注历史破坏",
                "--review-result", review_payload(
                    ["回灌不能覆盖更晚线上事实"],
                    history={
                        "1": {"status": "FAIL", "evidence": ["stage1_regression: failed"]},
                        "2": {"status": "PASS", "evidence": ["stage2_regression: passed"]},
                    },
                ),
            )
            self.assertIn("历史 Stage 回归失败必须至少记录一个 BLOCKER", unclassified_regression.stderr)
            stage3_blocked = db(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "3",
                "--decision", "rejected", "--content", "Stage 3 破坏 Stage 1 旧数据读取契约",
                "--review-result", review_payload(
                    ["回灌不能覆盖更晚线上事实"],
                    history={
                        "1": {"status": "FAIL", "evidence": ["stage1_regression: failed"]},
                        "2": {"status": "PASS", "evidence": ["stage2_regression: passed"]},
                    },
                    findings={
                        "BLOCKER": [{
                            "id": "B-STAGE1-REGRESSION", "summary": "破坏 Stage 1 旧数据读取",
                            "evidence": "stage1_regression failed", "risk": "历史数据无法读取",
                        }],
                        "MUST": [], "SHOULD": [], "NIT": [],
                    },
                ),
            )
            self.assertEqual(stage3_blocked["blocking_counts"]["BLOCKER"], 1)
            db(
                "developer", "stage-submit", "--issue-key", "RI-STAGE", "--stage-no", "3",
                "--content", "修复 Stage 1 回归", "--commit-sha", "abc005",
                "--diff-summary", "恢复旧数据 fallback 并保留回灌隔离",
                "--test-evidence", json.dumps([
                    "stage1_regression: passed", "stage2_regression: passed", "backfill: passed",
                ]),
                "--code-reference", json.dumps([{"file_path": "backfill.py", "line_start": 30}]),
                "--resolved-findings", json.dumps(["B-STAGE1-REGRESSION"]),
            )
            stage3_passed = db(
                "inspector", "stage-review", "--issue-key", "RI-STAGE", "--stage-no", "3",
                "--decision", "auto", "--content", "当前验收与 Stage 1/2 累计回归全部通过",
                "--review-result", review_payload(
                    ["回灌不能覆盖更晚线上事实"],
                    history={
                        "1": {"status": "PASS", "evidence": ["stage1_regression: passed"]},
                        "2": {"status": "PASS", "evidence": ["stage2_regression: passed"]},
                    },
                ),
                "--baseline", stage_baseline(3),
            )
            self.assertEqual(
                (stage3_passed["requested_decision"], stage3_passed["decision"], stage3_passed["final_decision"]),
                ("auto", "approved", "PASS"),
            )
            stage3_state = db("developer", "stage-get", "--issue-key", "RI-STAGE", "--stage-no", "3")
            self.assertEqual(stage3_state["baseline_status"], "PASSED")
            self.assertEqual(stage3_state["baseline"]["inherits_stage_nos"], [1, 2])
            final = db("developer", "implementation-submit", "--issue-key", "RI-STAGE", "--content", "全阶段完成")
            self.assertEqual(final["attempt_no"], 1)
            stage_activity_types = {
                item["activity_type"] for item in db("developer", "activity-list", "--issue-key", "RI-STAGE")
            }
            self.assertTrue({"STAGE_PLAN_CREATED", "STAGE_SUBMITTED", "STAGE_APPROVED", "STAGE_REJECTED"}.issubset(
                stage_activity_types
            ))

            redesign_issue = {**base_issue, "issue_key": "RI-STAGE-REDESIGN", "title": "阶段中发现整案错误"}
            db(
                "inspector", "issue-create-batch", "--task-key", task["task_key"], "--reason", "重设计测试",
                "--issues", json.dumps([redesign_issue], ensure_ascii=False),
            )
            db("inspector", "design-request", "--issue-key", "RI-STAGE-REDESIGN", "--content", "先设计")
            db("developer", "design-submit", "--issue-key", "RI-STAGE-REDESIGN", "--content", "初版方案")
            db("inspector", "stage-plan-create", "--issue-key", "RI-STAGE-REDESIGN",
               "--stages", json.dumps(stages[:2], ensure_ascii=False))
            db("inspector", "design-review", "--issue-key", "RI-STAGE-REDESIGN", "--decision", "approved",
               "--content", "批准初版")
            db(
                "developer", "stage-prepare", "--issue-key", "RI-STAGE-REDESIGN", "--stage-no", "1",
                "--change-scope", json.dumps({"files": ["domain.py"]}),
                "--change-reason", "验证设计失效路径", "--protected-behaviors", "[]",
            )
            db("developer", "stage-submit", "--issue-key", "RI-STAGE-REDESIGN", "--stage-no", "1",
               "--content", "第一阶段完成", "--commit-sha", "deadbeef",
               "--diff-summary", "实现初版领域模型",
               "--test-evidence", json.dumps(["model: failed"]),
               "--code-reference", json.dumps([{"file_path": "domain.py", "line_start": 1}]))
            redesigned = db(
                "inspector", "stage-review", "--issue-key", "RI-STAGE-REDESIGN", "--stage-no", "1",
                "--decision", "redesign", "--content", "新证据证明领域模型方向不成立",
                "--review-result", review_payload(
                    ["旧数据可读", "版本单测通过"],
                    findings={
                        "BLOCKER": [{
                            "id": "B-DESIGN", "summary": "领域模型方向不成立",
                            "evidence": "model test failed", "risk": "无法满足兼容边界",
                        }],
                        "MUST": [], "SHOULD": [], "NIT": [],
                    },
                    failed_criteria={"旧数据可读"},
                ),
            )
            self.assertEqual(redesigned["status"], "REDESIGN_REQUIRED")
            old_plan = db("developer", "stage-list", "--issue-key", "RI-STAGE-REDESIGN", "--plan-no", "1")
            self.assertEqual([item["status"] for item in old_plan], ["SUPERSEDED", "SUPERSEDED"])
            fails("developer", "implementation-submit", "--issue-key", "RI-STAGE-REDESIGN", "--content", "不能绕过")
            db("developer", "design-submit", "--issue-key", "RI-STAGE-REDESIGN", "--content", "新方案")
            new_plan = db(
                "inspector", "stage-plan-create", "--issue-key", "RI-STAGE-REDESIGN",
                "--stages", json.dumps(stages[:1], ensure_ascii=False),
            )
            self.assertEqual(new_plan["plan_no"], 2)
            all_history = db("developer", "stage-list", "--issue-key", "RI-STAGE-REDESIGN")
            self.assertEqual({item["plan_no"] for item in all_history}, {1, 2})
            self.assertIn(
                "STAGE_PLAN_SUPERSEDED",
                {item["activity_type"] for item in db("developer", "activity-list", "--issue-key", "RI-STAGE-REDESIGN")},
            )

    def test_human_confirmation_permissions_atomicity_and_workflow_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.run_cmd(home, str(INSTALLER), "install")
            workspace = home / "project"
            workspace.mkdir()
            db_tool = home / ".agent-review" / "bin" / "review-db.py"

            def db(*args: str) -> dict | list:
                role, *command = args
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                return self.run_cmd(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )

            def fails(role: str, *command: str) -> subprocess.CompletedProcess:
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                result = self.run_raw(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                return result

            task = db(
                "inspector", "task-resolve", "--title", "高风险历史修复",
                "--objective", "验证 Human 异常兜底", "--review-level", "L3", "--review-scope", "all",
            )
            issue = {
                "issue_key": "RI-HUMAN", "title": "历史账务事实源不明确",
                "dimension": "data_security", "severity": "critical",
                "remediation_benefit": "high", "remediation_cost": "extreme",
                "disposition": "business_confirmation", "confidence": "medium",
                "description": "两个系统都保存历史账务事实", "facts": "代码无法证明最终事实源",
                "rationale": "错误覆盖将造成不可逆历史数据破坏",
            }
            db(
                "inspector", "issue-create-batch", "--task-key", task["task_key"],
                "--reason", "发现高风险事实缺口", "--issues", json.dumps([issue], ensure_ascii=False),
            )
            initial_activities = db("developer", "activity-list", "--issue-key", "RI-HUMAN")
            database = home / ".agent-review" / "data" / "review.db"

            # 不能通过通用状态命令、Developer 或 Human 越权发起升级。
            fails(
                "developer", "issue-update-status", "--issue-key", "RI-HUMAN",
                "--status", "HUMAN_CONFIRMATION_REQUIRED",
            )
            fails(
                "human", "issue-update-status", "--issue-key", "RI-HUMAN",
                "--status", "HUMAN_CONFIRMATION_REQUIRED",
            )
            fails(
                "developer", "human-escalate", "--issue-key", "RI-HUMAN",
                "--reason", "越权", "--question", "怎么做？",
            )
            fails(
                "human", "human-escalate", "--issue-key", "RI-HUMAN",
                "--reason", "越权", "--question", "怎么做？",
            )
            fails(
                "inspector", "human-escalate", "--issue-key", "RI-HUMAN",
                "--reason", "", "--question", "谁是事实源？",
            )
            fails(
                "inspector", "human-escalate", "--issue-key", "RI-HUMAN",
                "--reason", "项目内没有依据", "--question", "",
            )

            # Activity 与状态必须在同一事务；状态更新失败时活动不能残留。
            with sqlite3.connect(database) as conn:
                conn.execute(
                    """CREATE TRIGGER force_human_escalation_update_failure
                       BEFORE UPDATE OF status ON review_issue
                       WHEN NEW.status = 'HUMAN_CONFIRMATION_REQUIRED'
                       BEGIN SELECT RAISE(ABORT, 'forced escalation rollback'); END"""
                )
            fails(
                "inspector", "human-escalate", "--issue-key", "RI-HUMAN",
                "--reason", "项目内无法确定账务事实源", "--question", "应以哪个系统为准？",
            )
            self.assertEqual(db("developer", "issue-get", "--issue-key", "RI-HUMAN")["status"], "PROPOSED")
            self.assertEqual(
                len(db("developer", "activity-list", "--issue-key", "RI-HUMAN")),
                len(initial_activities),
            )
            with sqlite3.connect(database) as conn:
                conn.execute("DROP TRIGGER force_human_escalation_update_failure")

            escalation = db(
                "inspector", "human-escalate", "--issue-key", "RI-HUMAN",
                "--reason", "代码、测试、Schema 与历史活动均不能确定两个账务系统的最终事实归属",
                "--question", "历史冲突时应以 ledger 还是 settlement 为最终事实源？",
                "--options", json.dumps([
                    {"label": "ledger", "impact": "保留总账事实"},
                    {"label": "settlement", "impact": "保留结算事实"},
                ], ensure_ascii=False),
                "--evidence", json.dumps([
                    {"file_path": "migration/reconcile.sql", "line_start": 10, "line_end": 35},
                ], ensure_ascii=False),
                "--recommended-option", "ledger（可审计且已有不可变流水）",
            )
            self.assertEqual(
                (escalation["status"], escalation["activity_type"], escalation["attempt_no"]),
                ("HUMAN_CONFIRMATION_REQUIRED", "HUMAN_CONFIRMATION_REQUESTED", 0),
            )
            activities = db("developer", "activity-list", "--issue-key", "RI-HUMAN")
            request_activity = [a for a in activities if a["activity_type"] == "HUMAN_CONFIRMATION_REQUESTED"][-1]
            self.assertEqual(request_activity["metadata_json"]["question"], "历史冲突时应以 ledger 还是 settlement 为最终事实源？")
            self.assertEqual(request_activity["metadata_json"]["options"][0]["label"], "ledger")
            self.assertEqual(request_activity["code_reference_json"][0]["file_path"], "migration/reconcile.sql")

            # 两个 Agent 在等待 Human 时没有自动继续动作，且原子活动不能伪造。
            fails("developer", "implementation-submit", "--issue-key", "RI-HUMAN", "--content", "越权继续")
            fails("inspector", "issue-update-status", "--issue-key", "RI-HUMAN", "--status", "IN_PROGRESS")
            fails(
                "inspector", "activity-append", "--issue-key", "RI-HUMAN",
                "--activity-type", "HUMAN_CONFIRMATION_PROVIDED", "--content", "伪造 Human 决策",
            )
            fails(
                "inspector", "human-confirmation-resolve", "--issue-key", "RI-HUMAN",
                "--decision", "ledger", "--content", "越权 resolve",
            )
            fails(
                "developer", "human-confirmation-resolve", "--issue-key", "RI-HUMAN",
                "--decision", "ledger", "--content", "越权 resolve",
            )
            fails(
                "human", "human-confirmation-resolve", "--issue-key", "RI-HUMAN",
                "--decision", "ledger", "--content", "试图直接确认", "--next-status", "CONFIRMED",
            )

            # Human resolve 同样必须原子；默认恢复设计阶段，implementation attempt 不增加。
            before_resolve_count = len(activities)
            with sqlite3.connect(database) as conn:
                conn.execute(
                    """CREATE TRIGGER force_human_resolve_update_failure
                       BEFORE UPDATE OF status ON review_issue
                       WHEN OLD.status = 'HUMAN_CONFIRMATION_REQUIRED' AND NEW.status = 'DESIGN_REQUIRED'
                       BEGIN SELECT RAISE(ABORT, 'forced human resolve rollback'); END"""
                )
            fails(
                "human", "human-confirmation-resolve", "--issue-key", "RI-HUMAN",
                "--decision", "ledger", "--content", "以 ledger 为最终事实源",
            )
            self.assertEqual(
                db("developer", "issue-get", "--issue-key", "RI-HUMAN")["status"],
                "HUMAN_CONFIRMATION_REQUIRED",
            )
            self.assertEqual(
                len(db("developer", "activity-list", "--issue-key", "RI-HUMAN")),
                before_resolve_count,
            )
            with sqlite3.connect(database) as conn:
                conn.execute("DROP TRIGGER force_human_resolve_update_failure")

            resolved = db(
                "human", "human-confirmation-resolve", "--issue-key", "RI-HUMAN",
                "--decision", "ledger", "--content", "冲突时以不可变总账流水为最终事实源",
            )
            self.assertEqual(
                (resolved["status"], resolved["activity_type"], resolved["attempt_no"]),
                ("DESIGN_REQUIRED", "HUMAN_CONFIRMATION_PROVIDED", 0),
            )
            db("developer", "design-submit", "--issue-key", "RI-HUMAN", "--content", "按 Human 边界重做冲突合并方案")
            db(
                "inspector", "design-review", "--issue-key", "RI-HUMAN", "--decision", "approved",
                "--content", "方案符合 ledger 最终事实源边界，允许实现",
            )
            implementation = db(
                "developer", "implementation-submit", "--issue-key", "RI-HUMAN", "--content", "实现并补充冲突回归测试",
            )
            self.assertEqual(implementation["attempt_no"], 1)
            verification_required = fails(
                "inspector", "issue-update-status", "--issue-key", "RI-HUMAN", "--status", "CONFIRMED",
            )
            self.assertIn("VERIFICATION_PASSED", verification_required.stderr)
            db(
                "inspector", "activity-append", "--issue-key", "RI-HUMAN",
                "--activity-type", "VERIFICATION_PASSED", "--content", "回归及历史样本验证通过",
            )
            confirmed = db("inspector", "issue-update-status", "--issue-key", "RI-HUMAN", "--status", "CONFIRMED")
            self.assertEqual(confirmed["status"], "CONFIRMED")

            override_issue = {**issue, "issue_key": "RI-HUMAN-OVERRIDE", "title": "人工状态纠正"}
            db(
                "inspector", "issue-create-batch", "--task-key", task["task_key"],
                "--reason", "验证 Human 最高解释权", "--issues", json.dumps([override_issue], ensure_ascii=False),
            )
            db(
                "human", "issue-update-status", "--issue-key", "RI-HUMAN-OVERRIDE",
                "--status", "REDESIGN_REQUIRED", "--content", "人工判定当前方案需要重做",
            )
            human_pending_review = db(
                "human", "issue-update-status", "--issue-key", "RI-HUMAN-OVERRIDE",
                "--status", "IMPLEMENTED_PENDING_REVIEW", "--content", "人工确认实现已经提交，纠正状态",
            )
            self.assertEqual(
                (human_pending_review["status"], human_pending_review["attempt_no"]),
                ("IMPLEMENTED_PENDING_REVIEW", 1),
            )
            human_confirmed = fails(
                "human", "issue-update-status", "--issue-key", "RI-HUMAN-OVERRIDE",
                "--status", "CONFIRMED", "--content", "Human 也不能绕过最终验证",
            )
            self.assertIn("VERIFICATION_PASSED", human_confirmed.stderr)
            db(
                "human", "activity-append", "--issue-key", "RI-HUMAN-OVERRIDE",
                "--activity-type", "VERIFICATION_PASSED", "--content", "Human 人工验证通过",
            )
            self.assertEqual(db(
                "human", "issue-update-status", "--issue-key", "RI-HUMAN-OVERRIDE",
                "--status", "CONFIRMED", "--content", "保留验证证据后确认",
            )["status"], "CONFIRMED")

    def test_webtool_human_workspace_routes_and_domain_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.run_cmd(home, str(INSTALLER), "install")
            workspace = home / "project"
            workspace.mkdir()
            db_tool = home / ".agent-review" / "bin" / "review-db.py"

            def db(*args: str) -> dict:
                role, *command = args
                alias = {"inspector": "trae-inspector", "developer": "codex-dev", "human": "human"}[role]
                return self.run_cmd(
                    home, str(db_tool), "--agent", role, "--operator-id", alias, *command, cwd=workspace
                )

            task = db(
                "inspector", "task-resolve", "--title", "订单模块检查", "--objective", "检查稳定性",
                "--review-level", "L2", "--review-scope", "order-service",
            )
            issue = {
                "title": "重复提交缺少幂等保护", "dimension": "data_security", "severity": "high",
                "remediation_benefit": "high", "remediation_cost": "medium",
                "disposition": "current_iteration", "confidence": "high", "description": "描述",
                "facts": "事实", "rationale": "依据",
                "trigger_conditions": ["同一请求重复提交"], "potential_impact": ["产生重复订单"],
                "impact_scope": ["order-service"],
                "evidence": [{"file_path": "order.py", "line_start": 5, "line_end": 8, "code_excerpt": "save(order)"}],
                "estimated_change": {
                    "file_count": 2, "change_points": 3, "affects_database": False,
                    "affects_public_api": True, "requires_data_migration": False,
                    "affects_upstream_downstream": True, "modules": ["order-service"],
                    "verification_scope": ["重复请求回归"],
                },
            }
            created = db("inspector", "issue-create-batch", "--task-key", task["task_key"], "--reason", "扫描", "--issues", json.dumps([issue]))
            issue_key = created["created"][0]
            candidate = db(
                "developer", "candidate-submit", "--task-key", task["task_key"],
                "--candidate-key", "RC-WEB-QUEUE", "--title", "候选缓存泄漏",
                "--description", "候选描述", "--facts", "候选事实", "--rationale", "候选依据",
                "--evidence", json.dumps([{"file_path": "cache.py", "line_start": 7, "code_excerpt": "cache[key] = value"}]),
                "--suggested-dimension", "stability_concurrency", "--suggested-severity", "medium",
                "--suggested-confidence", "high",
            )
            self.assertEqual(candidate["status"], "SUBMITTED")
            db(
                "developer", "candidate-submit", "--task-key", task["task_key"],
                "--candidate-key", "RC-WEB-REJECT", "--title", "证据不足的候选",
                "--description", "候选描述", "--facts", "候选事实", "--rationale", "候选依据",
            )

            previous_home = os.environ.get("AGENT_REVIEW_HOME")
            previous_db = os.environ.get("AGENT_REVIEW_DB")
            os.environ["AGENT_REVIEW_HOME"] = str(home / ".agent-review")
            os.environ["AGENT_REVIEW_DB"] = str(home / ".agent-review" / "data" / "review.db")
            try:
                webtool_dir = ROOT / "apps" / "code-inspector-webtool"
                sys.path.insert(0, str(webtool_dir))
                from app import app  # pylint: disable=import-outside-toplevel
                from app import localtime, markdown  # pylint: disable=import-outside-toplevel
                app.config.update(TESTING=True, CSRF_ENABLED=False)
                self.assertEqual(localtime("2026-07-19 06:29:31"), "2026-07-19 14:29:31")
                rendered = str(markdown("第一行\n第二行\n\n```python\nprint('<safe>')\n```"))
                self.assertIn("第一行<br>第二行", rendered)
                self.assertIn('<code class="language-python">', rendered)
                self.assertIn("&lt;safe&gt;", rendered)
                self.assertNotIn("<safe>", rendered)
                escaped_newlines = str(markdown(r"第一行\n第二行\n第三行"))
                self.assertIn("第一行<br>第二行<br>第三行", escaped_newlines)
                intentional_escape = str(markdown("说明\n`value\\n`"))
                self.assertIn("value\\n", intentional_escape)
                client = app.test_client()
                inbox = client.get("/")
                self.assertEqual(inbox.status_code, 200)
                self.assertIn("待我处理", inbox.get_data(as_text=True))
                self.assertIn("最近活跃的任务", inbox.get_data(as_text=True))
                self.assertIn(task["task_key"], inbox.get_data(as_text=True))
                self.assertIn("候选问题待审核", inbox.get_data(as_text=True))

                project_picker = client.get("/tasks")
                self.assertEqual(project_picker.status_code, 200)
                project_picker_html = project_picker.get_data(as_text=True)
                self.assertIn("选择项目", project_picker_html)
                self.assertIn("project", project_picker_html)
                self.assertNotIn('class="task-table"', project_picker_html)
                self.assertNotIn(task["task_key"], project_picker_html)
                self.assertIn("创建任务", project_picker_html)
                self.assertEqual(client.get("/tasks/").status_code, 200)
                listing = client.get("/tasks?project_name=project")
                self.assertEqual(listing.status_code, 200)
                self.assertIn(task["task_key"], listing.get_data(as_text=True))
                created_task_response = client.post(
                    "/tasks/create",
                    data={
                        "project_path": str(workspace), "task_type": "CONTINUOUS",
                        "title": "长期线上 Bug 汇报", "objective": "持续收集线上故障",
                        "review_level": "L2", "review_scope": "production-report",
                        "baseline_ref": "commit-a", "remark": "长期维护",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(created_task_response.status_code, 302)
                continuous_web_task = next(
                    item for item in db("human", "task-list", "--task-type", "CONTINUOUS")
                    if item["title"] == "长期线上 Bug 汇报"
                )
                self.assertEqual(continuous_web_task["project_path"], str(workspace.resolve()))
                ordered_listing = client.get("/tasks?project_name=project").get_data(as_text=True)
                self.assertLess(ordered_listing.index(continuous_web_task["task_key"]), ordered_listing.index(task["task_key"]))
                self.assertIn("badge-task-type-CONTINUOUS", ordered_listing)
                self.assertIn("badge-task-type-REVIEW", ordered_listing)
                continuous_detail = client.get(f"/tasks/{continuous_web_task['task_key']}").get_data(as_text=True)
                self.assertIn("持续治理", continuous_detail)
                self.assertIn("创建后不可修改", continuous_detail)
                self.assertEqual(client.get("/issues", follow_redirects=False).status_code, 302)
                detail = client.get(f"/tasks/{task['task_key']}")
                self.assertEqual(detail.status_code, 200)
                self.assertIn("重复提交缺少幂等保护", detail.get_data(as_text=True))
                self.assertIn("编辑当前任务", detail.get_data(as_text=True))
                self.assertIn("快捷变更状态", detail.get_data(as_text=True))
                self.assertIn("关闭任务会同步取消所有尚未结束的 Issue", detail.get_data(as_text=True))
                self.assertEqual(
                    client.get(f"/tasks/{task['task_key']}?dimension=data_security").status_code, 200,
                )

                task_update = client.post(
                    f"/tasks/{task['task_key']}/edit",
                    data={"title": "订单模块持续检查", "objective": "检查稳定性与幂等", "remark": "Human 补充", "close_reason": "预留关闭说明"},
                    follow_redirects=False,
                )
                self.assertEqual(task_update.status_code, 302)
                updated_task = next(
                    item for item in db("inspector", "task-list", "--project-name", "project")
                    if item["task_key"] == task["task_key"]
                )
                self.assertEqual(updated_task["title"], "订单模块持续检查")
                self.assertEqual(updated_task["objective"], "检查稳定性与幂等")
                self.assertEqual(updated_task["remark"], "Human 补充")
                self.assertEqual(updated_task["close_reason"], "预留关闭说明")

                update = client.post(
                    f"/issues/{issue_key}/body",
                    data={
                        "title": "已由人工补充", "summary": "**现象**：重复提交会创建两笔订单",
                        "expected_outcome": "重复提交只创建一笔订单", "technical_note": "检查 `idempotency_key`",
                        "local_terms": '{"影子单":"回放测试生成的只读订单"}',
                    },
                    follow_redirects=False,
                )
                self.assertEqual(update.status_code, 302)
                self.assertEqual(
                    db("inspector", "issue-list", "--task-key", task["task_key"])[0]["title"],
                    "已由人工补充",
                )

                assessment = client.post(
                    f"/issues/{issue_key}/assessment",
                    data={"dimension": "functional_correctness", "severity": "critical"},
                    follow_redirects=False,
                )
                self.assertEqual(assessment.status_code, 302)
                assessed_issue = db("inspector", "issue-list", "--task-key", task["task_key"])[0]
                self.assertEqual(
                    (assessed_issue["dimension"], assessed_issue["severity"]),
                    ("functional_correctness", "critical"),
                )
                activity = client.post(
                    f"/issues/{issue_key}/activities",
                    data={"topic": "VERIFICATION", "content": "回归项\n\n```python\nassert safe\n```"},
                    follow_redirects=False,
                )
                self.assertEqual(activity.status_code, 302)
                self.assertEqual(
                    client.post(
                        f"/issues/{issue_key}/status", data={"status": "ON_HOLD", "content": "等待测试环境"},
                        follow_redirects=False,
                    ).status_code,
                    302,
                )
                self.assertEqual(
                    client.post(
                        f"/issues/{issue_key}/status", data={"status": "IN_PROGRESS", "content": "环境就绪"},
                        follow_redirects=False,
                    ).status_code,
                    302,
                )

                issue_page = client.get(f"/issues/{issue_key}")
                issue_html = issue_page.get_data(as_text=True)
                self.assertEqual(issue_page.status_code, 200)
                self.assertIn("编辑当前 Issue", issue_html)
                self.assertIn("关键证据", issue_html)
                self.assertIn('class="tab active" data-tab-target="all"', issue_html)
                self.assertIn('class="tab-pane active" data-tab-pane="all"', issue_html)
                self.assertIn("讨论 1", issue_html)
                self.assertIn("处理历史", issue_html)
                self.assertIn("高级状态", issue_html)
                self.assertIn("order.py", issue_html)
                self.assertIn("重复提交会创建两笔订单", issue_html)
                self.assertIn("回归项", issue_html)
                self.assertIn('<code class="language-python">', issue_html)
                self.assertIn('提交人 <b class="mono">human</b> · Human', issue_html)
                self.assertIn('提交人 <b class="mono">trae-inspector</b> · Inspector', issue_html)

                candidates = client.get("/candidates")
                candidate_html = candidates.get_data(as_text=True)
                self.assertEqual(candidates.status_code, 200)
                self.assertIn("RC-WEB-QUEUE", candidate_html)
                self.assertIn("cache.py", candidate_html)
                missing_comment = client.post(
                    "/candidates/RC-WEB-QUEUE/status", data={"status": "ACCEPTED", "content": ""},
                    follow_redirects=False,
                )
                self.assertIn("err=", missing_comment.location)
                accepted = client.post(
                    "/candidates/RC-WEB-QUEUE/status",
                    data={"status": "ACCEPTED", "content": "证据成立，但不自动创建 Issue"},
                    follow_redirects=False,
                )
                self.assertEqual(accepted.status_code, 302)
                self.assertEqual(db("inspector", "candidate-list", "--status", "ACCEPTED")[0]["status"], "ACCEPTED")
                rejected = client.post(
                    "/candidates/RC-WEB-REJECT/status",
                    data={"status": "REJECTED", "content": "当前证据不能支持结论"}, follow_redirects=False,
                )
                self.assertEqual(rejected.status_code, 302)
                self.assertEqual(db("inspector", "candidate-list", "--status", "REJECTED")[0]["status"], "REJECTED")

                submitted = db(
                    "developer", "implementation-submit", "--issue-key", issue_key,
                    "--content", "已增加幂等键校验\n\n- 单测通过\n- 集成测试通过",
                    "--code-reference", json.dumps([{"file_path": "order.py", "line_start": 10, "line_end": 14, "code_excerpt": "if key in seen:\n    return"}]),
                    "--metadata", json.dumps({"tests": "passed"}),
                )
                self.assertEqual(submitted["status"], "IMPLEMENTED_PENDING_REVIEW")
                inbox_after_submit = client.get("/").get_data(as_text=True)
                self.assertIn(issue_key, inbox_after_submit)
                review_page = client.get(f"/issues/{issue_key}").get_data(as_text=True)
                self.assertIn("本次实现", review_page)
                self.assertIn("Human 操作台", review_page)
                self.assertIn("order.py", review_page)
                self.assertIn('提交人 <b class="mono">codex-dev</b> · Developer', review_page)
                implementation_discussion = review_page.split(
                    'data-tab-pane="discussion"', 1,
                )[1].split('data-tab-pane="history"', 1)[0]
                self.assertIn('data-activity-type="IMPLEMENTATION_SUBMITTED"', implementation_discussion)

                confirmed = client.post(
                    f"/issues/{issue_key}/human-action",
                    data={"action": "confirm", "content": "Human 已验证测试与实现"},
                    follow_redirects=False,
                )
                self.assertEqual(confirmed.status_code, 302)
                final_issue = db("inspector", "issue-list", "--task-key", task["task_key"])[0]
                self.assertEqual(final_issue["status"], "CONFIRMED")
                activities = db("inspector", "activity-list", "--issue-key", issue_key)
                self.assertIn("VERIFICATION_PASSED", {item["activity_type"] for item in activities})
                rejected_transition = client.post(
                    f"/issues/{issue_key}/status",
                    data={"status": "CANCELLED", "content": "终态后再次操作"}, follow_redirects=True,
                )
                self.assertEqual(rejected_transition.status_code, 200)
                self.assertIn("问题状态已更新", rejected_transition.get_data(as_text=True))
                self.assertEqual(db("human", "issue-get", "--issue-key", issue_key)["status"], "CANCELLED")
                design_issue = {**issue, "issue_key": "RI-WEB-DESIGN", "title": "Web 设计审核"}
                db(
                    "inspector", "issue-create-batch", "--task-key", task["task_key"],
                    "--reason", "Web 设计链路", "--issues", json.dumps([design_issue], ensure_ascii=False),
                )
                self.assertEqual(
                    client.post(
                        "/issues/RI-WEB-DESIGN/design-request", data={"content": "先明确兼容和并发边界"},
                        follow_redirects=False,
                    ).status_code,
                    302,
                )
                self.assertEqual(db("inspector", "issue-get", "--issue-key", "RI-WEB-DESIGN")["status"], "DESIGN_REQUIRED")
                client.post(
                    "/issues/RI-WEB-DESIGN/design-submit", data={"content": "Human 代为补充具体方案"},
                    follow_redirects=False,
                )
                design_page = client.get("/issues/RI-WEB-DESIGN").get_data(as_text=True)
                self.assertIn("审核设计方案", design_page)
                design_discussion = design_page.split(
                    'data-tab-pane="discussion"', 1,
                )[1].split('data-tab-pane="history"', 1)[0]
                self.assertIn('data-activity-type="DESIGN_SUBMITTED"', design_discussion)
                self.assertIn("Human 代为补充具体方案", design_discussion)
                self.assertEqual(
                    client.post(
                        "/issues/RI-WEB-DESIGN/stage-plan",
                        data={"stages": json.dumps([{
                            "stage_no": 1, "title": "兼容边界", "objective": "先完成兼容层",
                            "acceptance_criteria": ["旧数据可读", "回归通过"],
                        }], ensure_ascii=False)}, follow_redirects=False,
                    ).status_code,
                    302,
                )
                client.post(
                    "/issues/RI-WEB-DESIGN/design-review",
                    data={"decision": "approved", "content": "方案边界明确，可以实现"},
                    follow_redirects=False,
                )
                self.assertEqual(db("inspector", "issue-get", "--issue-key", "RI-WEB-DESIGN")["status"], "IN_PROGRESS")
                db(
                    "developer", "stage-prepare", "--issue-key", "RI-WEB-DESIGN", "--stage-no", "1",
                    "--change-scope", json.dumps({"files": ["compat.py"]}),
                    "--change-reason", "实现兼容层", "--protected-behaviors", "[]",
                )
                db(
                    "developer", "stage-submit", "--issue-key", "RI-WEB-DESIGN", "--stage-no", "1",
                    "--content", "兼容层完成", "--commit-sha", "web001",
                    "--diff-summary", "新增兼容读取层",
                    "--test-evidence", json.dumps(["compat: passed"]),
                    "--code-reference", json.dumps([{"file_path": "compat.py", "line_start": 1}]),
                )
                stage_page = client.get("/issues/RI-WEB-DESIGN").get_data(as_text=True)
                self.assertIn("执行计划", stage_page)
                self.assertIn("兼容边界", stage_page)
                self.assertIn("阶段验收通过", stage_page)
                stage_review = client.post(
                    "/issues/RI-WEB-DESIGN/stages/1/review",
                    data={
                        "plan_no": "1", "decision": "approved", "content": "兼容回归符合标准",
                        "review_result": json.dumps({
                            "findings": {"BLOCKER": [], "MUST": [], "SHOULD": [], "NIT": []},
                            "historical_regression": {},
                            "current_acceptance": [
                                {"criterion": "旧数据可读", "status": "PASS", "evidence": ["compat: passed"]},
                                {"criterion": "回归通过", "status": "PASS", "evidence": ["regression: passed"]},
                            ],
                        }, ensure_ascii=False),
                        "baseline": json.dumps({
                            "verified_behaviors": ["旧数据兼容读取"],
                            "input_output_contracts": [], "business_semantics": ["旧语义不变"],
                            "tests": ["compat", "regression"],
                        }, ensure_ascii=False),
                    },
                    follow_redirects=False,
                )
                self.assertEqual(stage_review.status_code, 302)
                self.assertEqual(
                    db("developer", "stage-get", "--issue-key", "RI-WEB-DESIGN", "--stage-no", "1")["status"],
                    "APPROVED",
                )
                client.post(
                    "/issues/RI-WEB-DESIGN/status",
                    data={"status": "REDESIGN_REQUIRED", "content": "Human 人工纠正为重设计"},
                    follow_redirects=False,
                )
                superseded_approved_stage = db(
                    "developer", "stage-get", "--issue-key", "RI-WEB-DESIGN", "--stage-no", "1",
                )
                self.assertEqual(
                    (superseded_approved_stage["plan_status"], superseded_approved_stage["status"]),
                    ("SUPERSEDED", "APPROVED"),
                )
                human_override = client.post(
                    "/issues/RI-WEB-DESIGN/status",
                    data={"status": "IMPLEMENTED_PENDING_REVIEW", "content": "Human 确认实现已提交"},
                    follow_redirects=False,
                )
                self.assertEqual(human_override.status_code, 302)
                self.assertEqual(
                    db("inspector", "issue-get", "--issue-key", "RI-WEB-DESIGN")["status"],
                    "IMPLEMENTED_PENDING_REVIEW",
                )

                human_issue = {**issue, "issue_key": "RI-WEB-HUMAN", "title": "需要业务事实源决策"}
                db(
                    "inspector", "issue-create-batch", "--task-key", task["task_key"],
                    "--reason", "Web Human 兜底链路", "--issues", json.dumps([human_issue], ensure_ascii=False),
                )
                db(
                    "inspector", "human-escalate", "--issue-key", "RI-WEB-HUMAN",
                    "--reason", "项目内无法确定冲突数据的最终业务事实源",
                    "--question", "冲突时应保留订单系统还是账务系统结果？",
                    "--options", json.dumps(["订单系统", "账务系统"], ensure_ascii=False),
                    "--evidence", json.dumps([{"file_path": "reconcile.py", "line_start": 41}], ensure_ascii=False),
                    "--recommended-option", "账务系统",
                )
                human_inbox = client.get("/").get_data(as_text=True)
                self.assertIn("需要人工确认", human_inbox)
                self.assertIn("冲突时应保留订单系统还是账务系统结果", human_inbox)
                self.assertIn("账务系统", human_inbox)
                human_page = client.get("/issues/RI-WEB-HUMAN").get_data(as_text=True)
                self.assertIn("自动工作流已暂停", human_page)
                self.assertIn("reconcile.py", human_page)
                human_resolved = client.post(
                    "/issues/RI-WEB-HUMAN/human-confirmation-resolve",
                    data={
                        "decision": "账务系统为事实源", "content": "以不可变账务流水为准",
                        "next_status": "DESIGN_REQUIRED",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(human_resolved.status_code, 302)
                self.assertEqual(
                    db("inspector", "issue-get", "--issue-key", "RI-WEB-HUMAN")["status"],
                    "DESIGN_REQUIRED",
                )
                task_status = client.post(
                    f"/tasks/{task['task_key']}/status",
                    data={"status": "ON_HOLD", "remark": "等待下一轮", "close_reason": "预留关闭说明"},
                    follow_redirects=False,
                )
                self.assertEqual(task_status.status_code, 302)
                self.assertEqual(db("inspector", "task-list", "--status", "ON_HOLD")[0]["status"], "ON_HOLD")
                closed_task = client.post(
                    f"/tasks/{task['task_key']}/status",
                    data={"status": "CLOSED", "close_reason": "阶段完成"}, follow_redirects=False,
                )
                self.assertEqual(closed_task.status_code, 302)
                self.assertEqual(db("human", "issue-get", "--issue-key", "RI-WEB-DESIGN")["status"], "CANCELLED")
                self.assertEqual(db("human", "issue-get", "--issue-key", "RI-WEB-HUMAN")["status"], "CANCELLED")
                reopened = client.post(
                    f"/tasks/{task['task_key']}/status",
                    data={"status": "IN_PROGRESS", "remark": "Human 重新开启"}, follow_redirects=False,
                )
                self.assertEqual(reopened.status_code, 302)
                reopened_tasks = db("human", "task-list", "--status", "IN_PROGRESS")
                self.assertEqual(
                    next(item for item in reopened_tasks if item["task_key"] == task["task_key"])["status"],
                    "IN_PROGRESS",
                )
            finally:
                app.config["CSRF_ENABLED"] = True
                sys.path.pop(0)
                if previous_home is None:
                    os.environ.pop("AGENT_REVIEW_HOME", None)
                else:
                    os.environ["AGENT_REVIEW_HOME"] = previous_home
                if previous_db is None:
                    os.environ.pop("AGENT_REVIEW_DB", None)
                else:
                    os.environ["AGENT_REVIEW_DB"] = previous_db

    def test_webtool_runtime_observability_csrf_and_debug(self):
        try:
            import flask  # noqa: F401  # pylint: disable=unused-import,import-outside-toplevel
        except ModuleNotFoundError:
            self.skipTest("Flask dependency is not installed")
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.run_cmd(home, str(INSTALLER), "install")
            review_home = home / ".agent-review"
            database = review_home / "data" / "review.db"
            with closing(sqlite3.connect(database)) as conn, conn:
                conn.execute(
                    "INSERT INTO review_task(task_key,project_name,project_path,title,objective,status) VALUES('RT-RUNTIME','p',?,'t','o','IN_PROGRESS')",
                    (str(home),),
                )
                conn.execute(
                    """INSERT INTO review_issue(issue_key,task_id,introduced_version,title,dimension,severity,
                       remediation_benefit,remediation_cost,disposition,confidence,status,description,facts,rationale)
                       VALUES('RI-RUNTIME',1,1,'Runtime','code_quality','medium','medium','low',
                              'current_iteration','high','IN_PROGRESS','d','f','r')"""
                )
                conn.execute(
                    """INSERT INTO code_inspector_thread(
                       issue_id,issue_key,role,operator_id,agent_platform,runtime_backend,thread_id,
                       thread_status,issue_status,next_action,last_event,cwd,context_tokens,context_window)
                       VALUES(1,'RI-RUNTIME','inspector','codex-insp','codex','codex-app-server',
                              'thr-visible','WAITING','IN_PROGRESS','await_event','STAGE_SUBMITTED',?,630,1000)""",
                    (str(home),),
                )
                conn.execute(
                    """INSERT INTO code_inspector_event(
                       event_id,idempotency_key,issue_key,role,operator_id,agent_platform,runtime_backend,event_type,status,failure_kind)
                       VALUES('evt-visible','idem-visible','RI-RUNTIME','inspector','codex-insp','codex',
                              'codex-app-server','STAGE_SUBMITTED','FAILED','RETRYABLE')"""
                )
            old_home, old_db = os.environ.get("AGENT_REVIEW_HOME"), os.environ.get("AGENT_REVIEW_DB")
            os.environ["AGENT_REVIEW_HOME"] = str(review_home)
            os.environ["AGENT_REVIEW_DB"] = str(database)
            webtool_dir = ROOT / "apps" / "code-inspector-webtool"
            sys.path.insert(0, str(webtool_dir))
            try:
                import importlib
                module = importlib.import_module("app")
                module.app.config.update(TESTING=True, CSRF_ENABLED=True)
                client = module.app.test_client()
                runtime_page = client.get("/runtime")
                self.assertEqual(runtime_page.status_code, 200)
                runtime_html = runtime_page.get_data(as_text=True)
                self.assertIn("thr-visible", runtime_html)
                self.assertIn("evt-visible", runtime_html)
                self.assertIn("63.0%", runtime_html)
                self.assertIn("Agent 运行状态", runtime_html)
                self.assertIn("等待事件", runtime_html)
                self.assertIn("调度事件队列", runtime_html)
                self.assertIn("重新入队", runtime_html)
                self.assertNotIn(">Reconcile<", runtime_html)
                self.assertNotIn(">Retry<", runtime_html)
                issue_html = client.get("/issues/RI-RUNTIME").get_data(as_text=True)
                self.assertIn("Agent 运行状态", issue_html)
                self.assertIn("检查者", issue_html)
                self.assertIn("codex-insp", issue_html)

                denied = client.post("/runtime/events/evt-visible/retry", follow_redirects=False)
                self.assertEqual(denied.status_code, 403)
                with client.session_transaction() as state:
                    token = state.get("csrf_token")
                self.assertTrue(token)
                with mock.patch.object(module, "run_runtime_command", return_value={"status": "PENDING"}) as command:
                    accepted = client.post(
                        "/runtime/events/evt-visible/retry", data={"csrf_token": token},
                        follow_redirects=False,
                    )
                self.assertEqual(accepted.status_code, 302)
                command.assert_called_once_with("retry-event", "--event-id", "evt-visible", "--confirm")
                with mock.patch.dict(os.environ, {"WEBTOOL_DEBUG": "false"}):
                    self.assertFalse(module.env_bool("WEBTOOL_DEBUG"))
                with mock.patch.dict(os.environ, {"WEBTOOL_DEBUG": "yes"}):
                    self.assertTrue(module.env_bool("WEBTOOL_DEBUG"))
            finally:
                sys.path.pop(0)
                if old_home is None:
                    os.environ.pop("AGENT_REVIEW_HOME", None)
                else:
                    os.environ["AGENT_REVIEW_HOME"] = old_home
                if old_db is None:
                    os.environ.pop("AGENT_REVIEW_DB", None)
                else:
                    os.environ["AGENT_REVIEW_DB"] = old_db


if __name__ == "__main__":
    unittest.main()
