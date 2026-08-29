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

    assert "if [ ! -x /opt/poethan-sentinel/plugins/network-diagnostic/1.0.0/digest/run.sh ]" in command
    assert "chmod +x /opt/poethan-sentinel/plugins/network-diagnostic/1.0.0/digest/run.sh 2>/dev/null || sudo -n chmod +x" in command
