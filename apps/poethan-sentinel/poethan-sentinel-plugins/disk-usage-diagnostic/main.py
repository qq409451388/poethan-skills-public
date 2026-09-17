#!/usr/bin/env python3
"""磁盘占用诊断：文件系统使用率、inode、大目录、大文件与被删除但仍占用的空间。

输出遵循 Poethan Sentinel 插件约定：

- 每个采集区段用 ``===== SECTION: NAME =====`` 分隔；
- 区段内是 ``key=value`` 事实行；
- ``CHECKS`` 区段里每个检查项是一个空行分隔的块，含 ``check_id`` 与 ``status``，
  Controller 会把 ``status=failed`` 的块转成报告里的确定性发现。

脚本只读取事实，不删除、不修改任何文件；扫描范围按挂载点收敛，并且每个外部
命令都有超时，避免在生产机上失控。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time

# 内核伪文件系统没有"磁盘占用"语义，参与统计只会污染报告。
PSEUDO_FILESYSTEMS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "pstore",
    "securityfs", "debugfs", "tracefs", "configfs", "fusectl", "mqueue", "hugetlbfs",
    "binfmt_misc", "autofs", "nsfs", "ramfs", "rpc_pipefs", "selinuxfs", "bpf",
    "squashfs", "efivarfs", "fuse.gvfsd-fuse", "fuse.portal", "overlay",
}
DEFAULT_EXCLUDES = ("/proc", "/sys", "/dev", "/run", "/snap")
SEVERITIES = {"critical", "warning", "info", "success"}


# ---------------------------------------------------------------- 环境与工具

def env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    try:
        return max(minimum, int(raw)) if raw else default
    except ValueError:
        return default


def env_text(name: str, default: str = "") -> str:
    value = str(os.getenv(name, "") or "").strip()
    return value or default


def env_list(name: str, default: tuple[str, ...] | list[str]) -> list[str]:
    raw = env_text(name, "")
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def clean(value: object) -> str:
    """事实行必须是一行一个 key=value，路径里的换行和制表符会破坏报告结构。"""
    return str(value).replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()


def to_int(value: str, default: int = 0) -> int:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else default


def gb(kilobytes: int) -> float:
    return round(kilobytes / 1024 / 1024, 2)


def has_command(name: str) -> bool:
    return shutil.which(name) is not None


def run(command: str, timeout: int) -> tuple[str, str]:
    """执行命令并返回 (stdout, 错误说明)。

    服务器上有 ``timeout(1)`` 时优先用它，能把 du/find 派生的子进程一起收掉；
    没有时退回 Python 自己的超时保护。
    """
    if has_command("timeout"):
        command = f"timeout {int(timeout)} {command}"
    try:
        completed = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return "", f"扫描超过 {timeout} 秒已中止"
    except Exception as exc:  # pragma: no cover - 取决于远端环境
        return "", f"collector_error={type(exc).__name__}"
    return completed.stdout, completed.stderr.strip()


def section(name: str, body: str) -> None:
    print(f"===== SECTION: {name} =====\n{body}\n")


# ------------------------------------------------------------------- 采集器

def parse_size_df() -> dict[str, dict]:
    output, _ = run("df -PT -k 2>/dev/null", 30)
    rows: dict[str, dict] = {}
    for line in output.splitlines()[1:]:
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        device, fstype, size, used, avail, percent, mount = parts
        rows[mount] = {
            "device": device, "fstype": fstype,
            "size_kb": to_int(size), "used_kb": to_int(used), "avail_kb": to_int(avail),
            "used_percent": to_int(percent),
        }
    return rows


def parse_inode_df() -> dict[str, dict]:
    output, _ = run("df -Pi -k 2>/dev/null", 30)
    rows: dict[str, dict] = {}
    for line in output.splitlines()[1:]:
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        _, inodes, used, free, percent, mount = parts
        rows[mount] = {
            "inodes": to_int(inodes), "inodes_used": to_int(used), "inodes_free": to_int(free),
            "inodes_used_percent": to_int(percent),
        }
    return rows


def select_mounts(filters: list[str], limit: int) -> list[dict]:
    sizes, inodes = parse_size_df(), parse_inode_df()
    mounts: list[dict] = []
    for mount, facts in sizes.items():
        if facts["fstype"] in PSEUDO_FILESYSTEMS or facts["size_kb"] <= 0:
            continue
        if filters and mount not in filters:
            continue
        merged = {**facts, "mount": mount, **inodes.get(mount, {})}
        mounts.append(merged)
    # 占用最大的先分析，挂载点很多时也不会把超时预算浪费在空的挂载上。
    mounts.sort(key=lambda item: item["used_kb"], reverse=True)
    return mounts[:limit]


def collect_large_directories(mount: str, depth: int, top_n: int, excludes: list[str], timeout: int) -> list[dict]:
    exclude_args = " ".join(f"--exclude={shlex.quote(path)}" for path in excludes)
    command = f"du -x -k -d {depth} {exclude_args} {shlex.quote(mount)} 2>/dev/null | sort -rn | head -n {top_n}"
    output, error = run(command, timeout)
    if not output.strip() and exclude_args:
        # 个别 du 实现不认 --exclude，去掉排除项再试一次（-x 已经挡住跨文件系统）。
        output, error = run(f"du -x -k -d {depth} {shlex.quote(mount)} 2>/dev/null | sort -rn | head -n {top_n}", timeout)
    rows: list[dict] = []
    for line in output.splitlines():
        size, _, path = line.partition("\t")
        if not path:
            size, _, path = line.partition(" ")
        size_kb = to_int(size)
        if not path.strip() or path.strip() == mount:
            continue
        rows.append({"path": clean(path), "size_kb": size_kb, "size_gb": gb(size_kb)})
    if error:
        rows.append({"error": clean(error), "mount": mount})
    return rows


def has_gnu_find() -> bool:
    output, _ = run("find --version 2>/dev/null | head -n 1", 10)
    return "GNU" in output


def collect_large_files(mount: str, min_mb: int, top_n: int, timeout: int, gnu_find: bool) -> list[dict]:
    if gnu_find:
        command = (
            f"find {shlex.quote(mount)} -xdev -type f -size +{min_mb}M -printf '%s\\t%p\\n' 2>/dev/null "
            f"| sort -rn | head -n {top_n}"
        )
    else:
        # BSD find 没有 -printf，退回 stat 拿字节数。
        command = (
            f"find {shlex.quote(mount)} -xdev -type f -size +{min_mb}M -print0 2>/dev/null "
            f"| xargs -0 -r stat -c '%s\\t%n' 2>/dev/null | sort -rn | head -n {top_n}"
        )
    output, _ = run(command, timeout)
    rows: list[dict] = []
    for line in output.splitlines():
        raw_size, _, path = line.partition("\t")
        if not path.strip():
            continue
        size_bytes = to_int(raw_size)
        rows.append({
            "path": clean(path), "size_bytes": size_bytes,
            "size_mb": round(size_bytes / 1024 / 1024, 1), "size_gb": round(size_bytes / 1024 / 1024 / 1024, 2),
        })
    return rows


def collect_deleted_open_files(top_n: int) -> list[dict]:
    """df 显示已满但 du 对不上时，通常就是这些"已删除但被进程占着"的文件。

    不依赖 lsof：直接读 /proc/<pid>/fd 的链接目标。非 root 只能看到自己的进程，
    因此结果会标注权限范围。
    """
    rows: list[dict] = []
    if not os.path.isdir("/proc"):
        return rows
    for entry in sorted(os.listdir("/proc")):
        if not entry.isdigit():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            handles = os.listdir(fd_dir)
        except OSError:
            continue
        for handle in handles:
            link = f"{fd_dir}/{handle}"
            try:
                target = os.readlink(link)
                if " (deleted)" not in target:
                    continue
                size_bytes = os.stat(link).st_size
            except OSError:
                continue
            name = ""
            try:
                with open(f"/proc/{entry}/comm", encoding="utf-8") as handle_file:
                    name = handle_file.read().strip()
            except OSError:
                pass
            rows.append({
                "pid": entry, "process": clean(name) or "-",
                "size_mb": round(size_bytes / 1024 / 1024, 1),
                "path": clean(target.replace(" (deleted)", "")),
                "_bytes": size_bytes,
            })
    rows.sort(key=lambda item: item["_bytes"], reverse=True)
    return rows[:top_n]


# --------------------------------------------------------------------- 输出

def render_filesystems(mounts: list[dict]) -> str:
    blocks = []
    for item in mounts:
        lines = [
            f"mount={clean(item['mount'])}",
            f"device={clean(item['device'])}",
            f"fstype={clean(item['fstype'])}",
            f"size_gb={gb(item['size_kb'])}",
            f"used_gb={gb(item['used_kb'])}",
            f"avail_gb={gb(item['avail_kb'])}",
            f"used_percent={item['used_percent']}",
        ]
        if "inodes" in item:
            lines.append(f"inodes_used_percent={item['inodes_used_percent']}")
            lines.append(f"inodes_free={item['inodes_free']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "status=no_data"


def render_top(rows: list[dict], label: str) -> str:
    if not rows:
        return "status=no_data"
    blocks = []
    for item in rows:
        if "error" in item:
            blocks.append(f"error={item['error']}")
            continue
        head = f"path={item['path']}\nmount={clean(item.get('mount', '-'))}"
        if label == "directory":
            blocks.append(f"{head}\nsize_gb={item['size_gb']}\nsize_kb={item['size_kb']}")
        else:
            blocks.append(f"{head}\nsize_gb={item['size_gb']}\nsize_mb={item['size_mb']}")
    return "\n\n".join(blocks) or "status=no_data"


def render_deleted(rows: list[dict]) -> str:
    if not rows:
        return "deleted_file_count=0\ndeleted_total_gb=0.0"
    total = sum(item["_bytes"] for item in rows) / 1024 / 1024 / 1024
    blocks = [f"deleted_file_count={len(rows)}\ndeleted_total_gb={round(total, 2)}"]
    for item in rows:
        blocks.append(f"pid={item['pid']}\nprocess={item['process']}\nsize_mb={item['size_mb']}\npath={item['path']}")
    return "\n\n".join(blocks)


def build_checks(mounts: list[dict], large_files: list[dict], warn: int, critical: int, inode_warn: int, file_warn_mb: int) -> str:
    blocks: list[str] = []
    for item in mounts:
        severity = "critical" if item["used_percent"] >= critical else "warning"
        blocks.append(
            f"check_id=DISK-001\nmount={clean(item['mount'])}\nseverity={severity}\n"
            f"status={'failed' if item['used_percent'] >= warn else 'passed'}\n"
            f"value={item['used_percent']}\nthreshold={warn}\ncritical_threshold={critical}\n"
            f"avail_gb={gb(item['avail_kb'])}"
        )
    for item in mounts:
        if "inodes_used_percent" not in item:
            continue
        blocks.append(
            f"check_id=DISK-002\nmount={clean(item['mount'])}\nseverity=warning\n"
            f"status={'failed' if item['inodes_used_percent'] >= inode_warn else 'passed'}\n"
            f"value={item['inodes_used_percent']}\nthreshold={inode_warn}"
        )
    for item in large_files:
        if item["size_mb"] < file_warn_mb:
            continue
        blocks.append(
            f"check_id=DISK-003\nseverity=warning\nstatus=failed\n"
            f"path={item['path']}\nsize_gb={item['size_gb']}\nthreshold_mb={file_warn_mb}"
        )
    return "\n\n".join(blocks) or "status=no_data"


def build_report_data(mounts: list[dict], directories: list[dict], files: list[dict], hostname: str) -> dict:
    """报告模板消费的最小 JSON。

    字节数在这里统一算好，模板只负责按各自 Top 列表归一化画条形图，
    百分比、可用空间、状态全部由页面自己推导，避免两侧算法不一致。
    """
    return {
        "h": hostname,
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "d": [
            [item["mount"], item["size_kb"] * 1024, item["used_kb"] * 1024, item["fstype"]]
            for item in mounts
        ],
        "dirs": [
            [row["path"], row["size_kb"] * 1024]
            for row in directories if "size_kb" in row
        ],
        "files": [
            [row["path"], row["size_bytes"]]
            for row in files if "size_bytes" in row
        ],
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "standard"
    if mode not in {"quick", "standard", "deep"}:
        mode = "standard"

    warn = env_int("DISK_USAGE_WARNING_PERCENT", 85)
    critical = max(warn, env_int("DISK_USAGE_CRITICAL_PERCENT", 95))
    inode_warn = env_int("DISK_INODE_WARNING_PERCENT", 85)
    min_file_mb = env_int("DISK_LARGE_FILE_MB", 200)
    file_warn_mb = max(min_file_mb, env_int("DISK_LARGE_FILE_WARNING_MB", 10240))
    top_n = env_int("DISK_TOP_N", 15, minimum=1)
    timeout = env_int("DISK_SCAN_TIMEOUT_SECONDS", 120, minimum=5)
    max_mounts = env_int("DISK_MAX_MOUNTS", 8, minimum=1)
    filters = env_list("DISK_MOUNT_FILTER", [])
    excludes = env_list("DISK_EXCLUDE_PATHS", DEFAULT_EXCLUDES)

    depth = 2 if mode == "deep" else 1
    if mode == "deep":
        top_n *= 2

    mounts = select_mounts(filters, max_mounts)
    section("HOST", f"hostname={clean(run('hostname', 10)[0])}\nkernel={clean(run('uname -sr', 10)[0])}\nscan_mode={mode}")
    section("FILESYSTEMS", render_filesystems(mounts))

    directory_rows: list[dict] = []
    seen_directories: set[str] = set()
    for item in mounts:
        for row in collect_large_directories(item["mount"], depth, top_n, excludes, timeout):
            if "error" in row:
                directory_rows.append({"mount": item["mount"], **row})
                continue
            # 同一路径可能在多个挂载点下重复出现（例如 bind mount），只保留一次。
            if row["path"] in seen_directories:
                continue
            seen_directories.add(row["path"])
            directory_rows.append({"mount": item["mount"], **row})
    directory_rows.sort(key=lambda row: row.get("size_kb", 0), reverse=True)
    section("LARGE_DIRECTORIES", render_top(directory_rows[:top_n], "directory"))

    large_files: list[dict] = []
    if mode != "quick":
        gnu_find = has_gnu_find()
        seen_files: set[str] = set()
        for item in mounts:
            for row in collect_large_files(item["mount"], min_file_mb, top_n, timeout, gnu_find):
                if row["path"] in seen_files:
                    continue
                seen_files.add(row["path"])
                large_files.append({"mount": item["mount"], **row})
        large_files.sort(key=lambda row: row["size_mb"], reverse=True)
        large_files = large_files[:top_n]
        section("LARGE_FILES", render_top(large_files, "file"))

        deleted = collect_deleted_open_files(top_n)
        note = "" if os.geteuid() == 0 else "\npermission_scope=非 root，只能看到当前用户的进程"
        section("DELETED_OPEN_FILES", render_deleted(deleted) + note)
        section("MOUNT_LAYOUT", run("lsblk -o NAME,SIZE,FSTYPE,TYPE,MOUNTPOINT 2>/dev/null || true", 20)[0].strip() or "status=no_data")

    section("CHECKS", build_checks(mounts, large_files, warn, critical, inode_warn, file_warn_mb))
    # 给报告模板用的结构化数据，一行 JSON；Controller 会校验后注入 __REPORT_JSON__。
    section("REPORT_DATA", json.dumps(
        build_report_data(mounts, directory_rows[:top_n], large_files, clean(run("hostname", 10)[0])),
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
