"""Web 工具的领域命令适配层。

页面只读查询可直接使用 SQLite；所有写操作统一调用安装后的 review-db.py，
避免在 Web 层复制状态流转、审计和权限规则。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def review_home() -> Path:
    return Path(os.path.expandvars(os.path.expanduser(
        os.environ.get("AGENT_REVIEW_HOME", "~/.agent-review")
    )))


def run_human_command(command: str, *args: str) -> dict:
    tool = review_home() / "bin" / "review-db.py"
    if not tool.exists():
        raise RuntimeError(f"未找到数据库工具：{tool}。请先安装 Code Inspector。")
    result = subprocess.run(
        [sys.executable, str(tool), "--agent", "human", command, *args],
        text=True,
        capture_output=True,
        env={**os.environ, "AGENT_REVIEW_HOME": str(review_home())},
    )
    if result.returncode != 0:
        try:
            message = json.loads(result.stderr).get("error", result.stderr)
        except json.JSONDecodeError:
            message = result.stderr.strip()
        raise RuntimeError(message or "领域命令执行失败")
    return json.loads(result.stdout)
