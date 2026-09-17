"""磁盘占用插件的回归测试。

插件是独立发布物，但它的输出格式和 Controller 的 findings 解析是同一份契约，
所以放在这里一起守住：改任何一边破坏约定都会立刻失败。
"""

from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from app.reports import parse_findings


PLUGIN_MAIN = Path(__file__).resolve().parents[3] / "poethan-sentinel-plugins" / "disk-usage-diagnostic" / "main.py"

DF_SIZE = """Filesystem     Type     1024-blocks      Used Available Capacity Mounted on
/dev/vda1      ext4        99678032  62000000  33000000      66% /
tmpfs          tmpfs        8123456         0   8123456       0% /dev/shm
/dev/vdb1      xfs        500000000 470000000  30000000      95% /data
"""
DF_INODE = """Filesystem      Inodes  IUsed   IFree IUse% Mounted on
/dev/vda1      6553600 800000 5753600   13% /
/dev/vdb1     32000000 29000000 3000000   91% /data
"""
DU = "62000000\t/\n40000000\t/var\n15000000\t/usr\n12000000\t/var/lib\n"
FIND = "12884901888\t/data/dump.sql\n2147483648\t/var/log/app.log\n"


@pytest.fixture()
def plugin(monkeypatch: pytest.MonkeyPatch):
    if not PLUGIN_MAIN.is_file():
        pytest.skip("插件目录不存在")
    # 加载插件不能往插件目录写 __pycache__，否则会污染仓库里的签名包。
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("disk_usage_plugin", PLUGIN_MAIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    # 默认喂 Linux 的 df/du/find 输出，测试机是 Linux 还是 macOS 都不影响结果。
    monkeypatch.setattr(module, "run", fake_run)
    return module


def fake_run(command: str, timeout: int) -> tuple[str, str]:
    if "df -PT" in command:
        return DF_SIZE, ""
    if "df -Pi" in command:
        return DF_INODE, ""
    if command.startswith("du "):
        return DU, ""
    if "find --version" in command:
        return "find (GNU findutils) 4.9.0\n", ""
    if command.startswith("find "):
        return FIND, ""
    if command.startswith("hostname"):
        return "prod-1\n", ""
    if command.startswith("uname"):
        return "Linux 5.15.0\n", ""
    return "", ""


def run_plugin(plugin, monkeypatch: pytest.MonkeyPatch, mode: str = "standard") -> str:
    monkeypatch.setattr(sys, "argv", ["main.py", mode])
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert plugin.main() == 0
    return buffer.getvalue()


def test_skips_pseudo_filesystems_and_sorts_by_usage(plugin) -> None:
    mounts = plugin.select_mounts([], 8)
    assert [item["mount"] for item in mounts] == ["/data", "/"]
    assert all(item["fstype"] != "tmpfs" for item in mounts)


def test_mount_filter_keeps_only_requested_mounts(plugin) -> None:
    assert [item["mount"] for item in plugin.select_mounts(["/"], 8)] == ["/"]


def test_critical_threshold_raises_finding_severity(plugin) -> None:
    mounts = plugin.select_mounts([], 8)
    checks = plugin.build_checks(mounts, [], warn=85, critical=95, inode_warn=85, file_warn_mb=10240)
    data_mount = checks.split("\n\n")[0]
    assert "mount=/data" in data_mount
    assert "severity=critical" in data_mount
    assert "status=failed" in data_mount
    root_mount = next(block for block in checks.split("\n\n") if block.startswith("check_id=DISK-001") and "mount=/\n" in block)
    assert "status=passed" in root_mount


def section_of(output: str, name: str) -> str:
    marker = f"===== SECTION: {name} ====="
    assert marker in output, f"缺少区段 {name}"
    return output.split(marker, 1)[1].split("===== SECTION:", 1)[0]


def test_large_files_and_directories_are_deduplicated(plugin, monkeypatch) -> None:
    output = run_plugin(plugin, monkeypatch)
    directories = section_of(output, "LARGE_DIRECTORIES")
    files = section_of(output, "LARGE_FILES")
    assert directories.count("path=") == 4
    assert files.count("path=") == 2
    # 每个条目都要带上是哪个挂载点，否则报告里看不出大文件属于哪块盘。
    assert "mount=/data" in files


def test_plugin_output_becomes_readable_findings(plugin, monkeypatch) -> None:
    findings = parse_findings(run_plugin(plugin, monkeypatch), 0)
    by_title = {item.title: item for item in findings}
    assert by_title["文件系统使用率超过阈值"].severity == "critical"
    assert by_title["inode 使用率超过阈值"].severity == "warning"
    assert by_title["发现超大文件"].severity == "warning"
    # severity= 是给解析器看的，不该混进证据文本。
    assert "severity=" not in by_title["文件系统使用率超过阈值"].evidence


def test_quick_mode_skips_large_file_scan(plugin, monkeypatch) -> None:
    output = run_plugin(plugin, monkeypatch, mode="quick")
    assert "SECTION: LARGE_FILES" not in output
    assert "SECTION: LARGE_DIRECTORIES" in output
    assert "SECTION: CHECKS" in output


def test_survives_missing_df_support(plugin, monkeypatch) -> None:
    """目标机 df 不支持 -T 时也不能崩，只输出 no_data。"""
    monkeypatch.setattr(plugin, "run", lambda command, timeout: ("", "df: illegal option"))
    monkeypatch.setattr(sys, "argv", ["main.py", "quick"])
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert plugin.main() == 0
    assert "status=no_data" in buffer.getvalue()
