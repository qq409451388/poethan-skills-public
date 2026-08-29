from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def _client() -> TestClient:
    client = TestClient(app)
    assert client.get("/api/v1/bootstrap").status_code == 200
    return client


def _remove_created_tool(payload: dict[str, object]) -> None:
    directory = Path(str(payload["directory"]))
    shutil.rmtree(directory.parent, ignore_errors=True)


def test_create_local_script_tool() -> None:
    with _client() as web:
        response = web.post(
            "/api/v1/tools/local-script",
            headers={"X-Poethan-Request": "1"},
            data={"name": "本机网络采样", "description": "测试脚本", "runtime": "bash"},
            files={"script": ("network.sh", b"#!/bin/bash\necho local-script-ok\n", "text/x-shellscript")},
        )
        assert response.status_code == 200
        tool = response.json()
        try:
            assert tool["toolType"] == "local_script"
            assert tool["trust"]["status"] == "local"
            directory = Path(tool["directory"])
            assert (directory / "tool.sh").read_text(encoding="utf-8").endswith("local-script-ok\n")
            completed = subprocess.run([str(directory / "run.sh"), "standard"], capture_output=True, text=True)
            assert completed.returncode == 0
            assert completed.stdout.strip() == "local-script-ok"
        finally:
            _remove_created_tool(tool)


def test_create_server_script_tool_with_configurable_remote_path(tmp_path: Path) -> None:
    with _client() as web:
        response = web.post(
            "/api/v1/tools/server-script",
            headers={"X-Poethan-Request": "1"},
            json={
                "name": "服务器巡检",
                "description": "运行服务器已有脚本",
                "runtime": "python",
                "scriptPath": "/opt/diagnostics/check.py",
            },
        )
        assert response.status_code == 200
        tool = response.json()
        try:
            assert tool["toolType"] == "server_script"
            remote_path = next(field for field in tool["fields"] if field["key"] == "REMOTE_SCRIPT_PATH")
            assert remote_path["default"] == "/opt/diagnostics/check.py"
            remote_script = tmp_path / "check.py"
            remote_script.write_text("import sys\nprint(f'server-script-ok:{sys.argv[1]}')\n", encoding="utf-8")
            config_file = tmp_path / "config.env"
            config_file.write_text(f"REMOTE_SCRIPT_PATH={remote_script}\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["POETHAN_CONFIG_FILE"] = str(config_file)
            completed = subprocess.run([str(Path(tool["directory"]) / "run.sh"), "standard"], env=environment, capture_output=True, text=True)
            assert completed.returncode == 0
            assert completed.stdout.strip() == "server-script-ok:standard"
            scanned = web.get("/api/v1/plugins").json()["items"]
            assert any(item.get("plugin", {}).get("id") == tool["id"] for item in scanned)
        finally:
            _remove_created_tool(tool)


def test_server_script_path_must_be_absolute() -> None:
    with _client() as web:
        response = web.post(
            "/api/v1/tools/server-script",
            headers={"X-Poethan-Request": "1"},
            json={"name": "错误路径", "runtime": "bash", "scriptPath": "scripts/check.sh"},
        )
        assert response.status_code == 400
        assert "绝对路径" in response.json()["detail"]
