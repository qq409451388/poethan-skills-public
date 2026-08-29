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


def test_spa_is_served() -> None:
    with TestClient(app) as web:
        response = web.get("/")
        assert response.status_code == 200
        assert "Poethan Sentinel" in response.text
