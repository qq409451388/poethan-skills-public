"""直连 SQLite 数据库的连接与查询助手。

读取 `~/.agent-review/config/runtime.json` 中的 database 字段，与
`scripts/code-inspector-installer/runtime/review_db.py` 保持一致的配置来源。

本模块只负责读连接与简单查询封装；写操作统一通过 `commands.py`
调用 `review-db.py --agent human` 的领域命令。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB_PATH = Path(os.path.expandvars(os.path.expanduser(
    os.environ.get("AGENT_REVIEW_DB", "~/.agent-review/data/review.db")
)))


def configured_db_path() -> Path:
    """解析当前应使用的数据库路径。

    优先级：环境变量 AGENT_REVIEW_DB > runtime.json > 默认路径。
    """
    if os.environ.get("AGENT_REVIEW_DB"):
        return DEFAULT_DB_PATH
    config_dir = Path(os.path.expandvars(os.path.expanduser(
        os.environ.get("AGENT_REVIEW_HOME", "~/.agent-review")
    ))) / "config"
    runtime_config = config_dir / "runtime.json"
    if runtime_config.exists():
        try:
            config = json.loads(runtime_config.read_text(encoding="utf-8"))
            return Path(os.path.expandvars(os.path.expanduser(config["database"]))).resolve()
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise RuntimeError(f"配置文件无效: {runtime_config}: {exc}") from exc
    return DEFAULT_DB_PATH


def connect() -> sqlite3.Connection:
    """打开一个直连 SQLite 连接，启用外键与忙等待。"""
    db_path = configured_db_path()
    if not db_path.exists():
        raise RuntimeError(f"数据库不存在: {db_path}。请先执行 install.py install。")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def query_all(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None


def parse_json_field(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
