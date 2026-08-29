import time

from fastapi.testclient import TestClient

from app.main import app


def client() -> TestClient:
    result = TestClient(app)
    response = result.get("/api/v1/bootstrap")
    assert response.status_code == 200
    return result


def set_demo_mode(web: TestClient, enabled: bool) -> dict:
    settings = web.get("/api/v1/settings").json()
    settings["demoMode"] = enabled
    response = web.put("/api/v1/settings", headers={"X-Poethan-Request": "1"}, json=settings)
    assert response.status_code == 200
    return response.json()


def test_bootstrap_settings_servers_and_plugins() -> None:
    with client() as web:
        assert web.get("/api/v1/settings").status_code == 200
        set_demo_mode(web, True)
        servers = web.get("/api/v1/servers").json()
        assert servers[0]["authentication"] == "demo"
        plugins = web.get("/api/v1/plugins").json()
        assert plugins["validCount"] == 3


def test_mutations_require_local_request_marker() -> None:
    with client() as web:
        response = web.post("/api/v1/plugins/rescan")
        assert response.status_code == 403
        response = web.post("/api/v1/plugins/rescan", headers={"X-Poethan-Request": "1"})
        assert response.status_code == 200


def test_server_can_be_deleted_without_touching_other_profiles() -> None:
    with client() as web:
        set_demo_mode(web, True)
        headers = {"X-Poethan-Request": "1"}
        created = web.post(
            "/api/v1/servers",
            headers=headers,
            json={
                "id": "temporary-server",
                "name": "临时服务器",
                "authentication": "alias",
                "alias": "temporary",
                "host": "",
                "user": "",
                "port": 22,
                "identityFile": "",
            },
        )
        assert created.status_code == 200

        deleted = web.delete("/api/v1/servers/temporary-server", headers=headers)
        assert deleted.status_code == 204
        servers = web.get("/api/v1/servers").json()
        assert all(server["id"] != "temporary-server" for server in servers)
        assert any(server["id"] == "demo-server" for server in servers)


def test_start_demo_run_from_http_route() -> None:
    with client() as web:
        set_demo_mode(web, True)
        plugins = web.get("/api/v1/plugins").json()["items"]
        plugin = next(item["plugin"] for item in plugins if item["valid"])
        response = web.post(
            "/api/v1/runs",
            headers={"X-Poethan-Request": "1"},
            json={
                "serverId": "demo-server",
                "pluginId": plugin["id"],
                "pluginVersion": plugin["version"],
                "mode": plugin["defaultMode"],
                "values": {},
                "secrets": {},
                "remember": False,
                "aiEnabled": False,
            },
        )
        assert response.status_code == 200

        run_id = response.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = web.get(f"/api/v1/runs/{run_id}").json()
            if run["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)

        assert run["status"] == "completed"
        assert run["reportId"]


def test_disabled_demo_mode_hides_server_and_rejects_direct_run() -> None:
    with client() as web:
        original = web.get("/api/v1/settings").json()
        headers = {"X-Poethan-Request": "1"}
        try:
            set_demo_mode(web, True)
            assert any(item["id"] == "demo-server" for item in web.get("/api/v1/servers").json())
            plugin = next(item["plugin"] for item in web.get("/api/v1/plugins").json()["items"] if item["valid"])

            set_demo_mode(web, False)
            assert all(item["id"] != "demo-server" for item in web.get("/api/v1/servers").json())
            response = web.post(
                "/api/v1/runs",
                headers=headers,
                json={
                    "serverId": "demo-server",
                    "pluginId": plugin["id"],
                    "pluginVersion": plugin["version"],
                    "mode": plugin["defaultMode"],
                    "values": {},
                    "secrets": {},
                    "remember": False,
                    "aiEnabled": False,
                },
            )
            assert response.status_code == 403
            assert response.json()["detail"] == "演示模式已关闭，请在设置中重新开启"
        finally:
            restored = web.put("/api/v1/settings", headers=headers, json=original)
            assert restored.status_code == 200


def test_spa_is_served() -> None:
    with TestClient(app) as web:
        response = web.get("/")
        assert response.status_code == 200
        assert "Poethan Sentinel" in response.text
