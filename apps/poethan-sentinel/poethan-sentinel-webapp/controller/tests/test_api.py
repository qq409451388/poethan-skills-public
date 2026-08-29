import time

from fastapi.testclient import TestClient

from app.main import app


def client() -> TestClient:
    result = TestClient(app)
    response = result.get("/api/v1/bootstrap")
    assert response.status_code == 200
    return result


def test_bootstrap_settings_servers_and_plugins() -> None:
    with client() as web:
        assert web.get("/api/v1/settings").status_code == 200
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


def test_spa_is_served() -> None:
    with TestClient(app) as web:
        response = web.get("/")
        assert response.status_code == 200
        assert "Poethan Sentinel" in response.text
