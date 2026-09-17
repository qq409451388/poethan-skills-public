from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.ssh import SSHService


def test_cached_executable_runs_without_attempting_chmod(tmp_path: Path) -> None:
    plugin = tmp_path / "root-owned-style cache"
    plugin.mkdir()
    entrypoint = plugin / "run.sh"
    entrypoint.write_text("#!/bin/bash\nprintf 'mode=%s' \"$1\"\n", encoding="utf-8")
    entrypoint.chmod(0o555)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_chmod = fake_bin / "chmod"
    fake_chmod.write_text("#!/bin/bash\nexit 97\n", encoding="utf-8")
    fake_chmod.chmod(0o755)

    result = tmp_path / "result.txt"
    command = SSHService._plugin_command(
        str(plugin), "run.sh", str(tmp_path / "config.env"), "standard", str(result),
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    completed = subprocess.run(["/bin/bash", "-c", command], env=environment, capture_output=True, text=True)

    assert completed.returncode == 0
    assert result.read_text(encoding="utf-8") == "mode=standard"


def test_non_executable_entrypoint_has_sudo_chmod_fallback() -> None:
    command = SSHService._plugin_command(
        "/opt/poethan-sentinel/plugins/network-diagnostic/1.0.0/digest",
        "run.sh",
        "/tmp/run/config.env",
        "standard",
        "/tmp/run/result.txt",
    )
    entrypoint = "/opt/poethan-sentinel/plugins/network-diagnostic/1.0.0/digest/run.sh"
    directory = "/opt/poethan-sentinel/plugins/network-diagnostic/1.0.0/digest"
    # 整目录补读位（入口依赖的 main.py 也可能是 root 属主且不可读）。
    assert f"chmod -R u+rwX,go+rX {directory} 2>/dev/null || sudo -n chmod -R u+rwX,go+rX {directory}" in command
    assert f"if [ ! -r {entrypoint} ] || [ ! -x {entrypoint} ]" in command
    assert "exit 126" in command


def test_unreadable_entrypoint_is_repaired_before_running(tmp_path: Path) -> None:
    """root 属主且只有执行位的脚本：修复后必须能正常跑起来。"""
    plugin = tmp_path / "cache"
    plugin.mkdir()
    entrypoint = plugin / "run.sh"
    entrypoint.write_text("#!/bin/bash\nprintf 'mode=%s' \"$1\"\n", encoding="utf-8")
    entrypoint.chmod(0o111)

    result = tmp_path / "result.txt"
    command = SSHService._plugin_command(str(plugin), "run.sh", str(tmp_path / "config.env"), "deep", str(result))
    completed = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    assert result.read_text(encoding="utf-8") == "mode=deep"


def test_unreadable_dependency_is_also_repaired(tmp_path: Path) -> None:
    """入口可读但 main.py 不可读的情况同样要修好，这是最容易漏的一种。"""
    plugin = tmp_path / "cache"
    plugin.mkdir()
    (plugin / "run.sh").write_text('#!/bin/bash\nexec python3 "$(dirname "$0")/main.py"\n', encoding="utf-8")
    (plugin / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (plugin / "run.sh").chmod(0o755)
    (plugin / "main.py").chmod(0o600)

    result = tmp_path / "result.txt"
    command = SSHService._plugin_command(str(plugin), "run.sh", str(tmp_path / "config.env"), "quick", str(result))
    completed = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    assert result.read_text(encoding="utf-8").strip() == "ok"


def test_unrepairable_entrypoint_reports_actionable_error(tmp_path: Path) -> None:
    """权限修不了时要给出看得懂的提示，而不是那句难以定位的 bash: Permission denied。"""
    plugin = tmp_path / "cache"
    plugin.mkdir()
    entrypoint = plugin / "run.sh"
    entrypoint.write_text("#!/bin/bash\necho nope\n", encoding="utf-8")
    entrypoint.chmod(0o111)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("chmod", "sudo"):
        tool = fake_bin / name
        tool.write_text("#!/bin/bash\nexit 97\n", encoding="utf-8")
        tool.chmod(0o755)

    command = SSHService._plugin_command(str(plugin), "run.sh", str(tmp_path / "config.env"), "quick", str(tmp_path / "result.txt"))
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    completed = subprocess.run(["/bin/bash", "-c", command], env=environment, capture_output=True, text=True)

    assert completed.returncode == 126
    assert "插件入口不可读或不可执行" in completed.stderr


def test_packaged_entrypoint_is_readable_and_executable(tmp_path: Path) -> None:
    """本地权限写坏了也不能带病上传：打包时必须规范化。"""
    import tarfile

    plugin = tmp_path / "disk-usage-diagnostic"
    (plugin / "report").mkdir(parents=True)
    (plugin / "run.sh").write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    (plugin / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (plugin / "plugin.yaml").write_text("{}", encoding="utf-8")
    (plugin / "report" / "tpl.html").write_text("<b>x</b>", encoding="utf-8")
    # 复现磁盘插件当时的坏权限：脚本只有执行位，yaml 只有属主可读。
    (plugin / "run.sh").chmod(0o711)
    (plugin / "main.py").chmod(0o711)
    (plugin / "plugin.yaml").chmod(0o600)

    archive_path, _ = SSHService()._archive(plugin)
    try:
        with tarfile.open(archive_path) as archive:
            modes = {item.name: item.mode for item in archive.getmembers()}
    finally:
        archive_path.unlink(missing_ok=True)

    assert modes["run.sh"] == 0o755
    assert modes["main.py"] == 0o755  # 原本带执行位，保留可执行但补上读位
    assert modes["plugin.yaml"] == 0o644
    assert modes["report"] == 0o755
    assert modes["report/tpl.html"] == 0o644
