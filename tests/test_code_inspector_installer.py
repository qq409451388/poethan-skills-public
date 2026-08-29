import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
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
            (review_home / "config" / "runtime.json").write_text(
                json.dumps({"database": str(database)}),
                encoding="utf-8",
            )

            with mock.patch.object(Path, "symlink_to", side_effect=OSError("symlink denied")):
                INSTALLER_MODULE.link_runtime(review_home, skill_config, force=False)
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
            self.assertTrue((home / ".agent-review" / "bin" / "review-db.py").is_file())
            bindings = json.loads((home / ".agent-review" / "config" / "agent-bindings.json").read_text())
            self.assertEqual(bindings["codex-dev"]["role"], "developer")
            self.assertEqual(bindings["codex-insp"]["role"], "inspector")
            self.assertEqual(bindings["trae-inspector"]["role"], "inspector")
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
            self.assertIn("缺少参数", codex_skill_text)
            self.assertIn("当前平台仅配置 `inspector` 角色", trae_skill_text)
            self.assertIn("$code-inspector start` 即可启动", trae_skill_text)
            self.assertIn('固定工具：`python "', codex_skill_text)
            self.assertNotIn("modify-business-code", codex_skill_text.split("可执行命令：", 1)[1].split("。", 1)[0])
            self.assertIn("短结论使用单行纯文本", codex_skill_text)
            self.assertIn("只有代码、命令", trae_skill_text)
            self.assertIn("主审核者必须额外串联跨模块数据流", trae_skill_text)
            self.assertIn("先做覆盖面回查和补充扫描", trae_skill_text)
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
               "--remediation-benefit", "high", "--remediation-cost", "low",
               "--disposition", "current_iteration", "--confidence", "high",
               "--description", "描述", "--facts", "事实", "--rationale", "依据")

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

            direct_submit = db(
                "developer", "issue-update-status", "--issue-key", created["created"][0],
                "--status", "IMPLEMENTED_PENDING_REVIEW",
            )
            self.assertEqual(direct_submit["status"], "IMPLEMENTED_PENDING_REVIEW")

            db("inspector", "task-update-status", "--task-key", first["task_key"], "--status", "CLOSED")
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
                "human", "activity-append", "--issue-key", "RI-BATCH-1",
                "--activity-type", "COMMENT_ADDED", "--content", "请重新评估严重度",
            )
            comments = db(
                "inspector", "activity-list-recent", "--task-key", task["task_key"],
                "--activity-type", "COMMENT_ADDED", "--limit", "20",
            )
            self.assertEqual(comments[0]["issue_key"], "RI-BATCH-1")
            self.assertTrue(any(item["content"] == "请重新评估严重度" for item in comments))

            detail = db("developer", "issue-get", "--issue-key", "RI-BATCH-1")
            self.assertEqual(detail["description"], "完整描述 1")
            self.assertEqual(detail["evidence_json"][0]["file_path"], "src/1.py")
            self.assertIsNotNone(detail["last_activity_at"])
            self.assertIsNotNone(detail["last_comment_at"])

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

    def test_webtool_uses_task_as_the_only_top_level_list(self):
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
            }
            created = db("inspector", "issue-create-batch", "--task-key", task["task_key"], "--reason", "扫描", "--issues", json.dumps([issue]))
            issue_key = created["created"][0]

            previous_home = os.environ.get("AGENT_REVIEW_HOME")
            previous_db = os.environ.get("AGENT_REVIEW_DB")
            os.environ["AGENT_REVIEW_HOME"] = str(home / ".agent-review")
            os.environ["AGENT_REVIEW_DB"] = str(home / ".agent-review" / "data" / "review.db")
            try:
                webtool_dir = ROOT / "apps" / "code-inspector-webtool"
                sys.path.insert(0, str(webtool_dir))
                from app import app  # pylint: disable=import-outside-toplevel
                from app import localtime, markdown  # pylint: disable=import-outside-toplevel
                self.assertEqual(localtime("2026-07-19 06:29:31"), "2026-07-19 14:29:31")
                rendered = str(markdown("第一行\n第二行\n\n```python\nprint('<safe>')\n```"))
                self.assertIn("第一行<br>第二行", rendered)
                self.assertIn('<code class="language-python">', rendered)
                self.assertIn("&lt;safe&gt;", rendered)
                self.assertNotIn("<safe>", rendered)
                client = app.test_client()
                listing = client.get("/")
                self.assertEqual(listing.status_code, 200)
                self.assertIn("检查任务", listing.get_data(as_text=True))
                self.assertIn(task["task_key"], listing.get_data(as_text=True))
                self.assertEqual(client.get("/issues", follow_redirects=False).status_code, 302)
                detail = client.get(f"/tasks/{task['task_key']}")
                self.assertEqual(detail.status_code, 200)
                self.assertIn("重复提交缺少幂等保护", detail.get_data(as_text=True))
                update = client.post(
                    f"/issues/{issue_key}/body",
                    data={"title": "已由人工补充", "description": "描述", "facts": "事实", "rationale": "依据"},
                    follow_redirects=False,
                )
                self.assertEqual(update.status_code, 302)
                self.assertEqual(
                    db("inspector", "issue-list", "--task-key", task["task_key"])[0]["title"],
                    "已由人工补充",
                )
            finally:
                sys.path.pop(0)
                if previous_home is None:
                    os.environ.pop("AGENT_REVIEW_HOME", None)
                else:
                    os.environ["AGENT_REVIEW_HOME"] = previous_home
                if previous_db is None:
                    os.environ.pop("AGENT_REVIEW_DB", None)
                else:
                    os.environ["AGENT_REVIEW_DB"] = previous_db


if __name__ == "__main__":
    unittest.main()
