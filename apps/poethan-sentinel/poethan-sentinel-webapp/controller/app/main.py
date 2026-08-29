from __future__ import annotations

import asyncio
import json
import os
import secrets as random_secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .ai import AI_KEY_ACCOUNT, test_ai
from .models import (
    AIConnectionInput, AISettings, ApplicationSettings, ConnectionTestInput, RunRequest,
    ServerInput, ServerProfile, SettingsInput,
)
from .plugins import plugin_service
from .reports import plugin_report_html, report_html
from .runs import run_manager
from .secrets import secrets, server_password_account
from .ssh import ssh_service
from .storage import store


SESSION_TOKEN = random_secrets.token_urlsafe(32)
app = FastAPI(title="Poethan Sentinel Controller", version="0.1.0-beta.1", docs_url="/api/docs", openapi_url="/api/openapi.json")


def require_session(poethan_session: Annotated[str | None, Cookie(alias="poethan_session")] = None) -> None:
    if poethan_session != SESSION_TOKEN:
        raise HTTPException(status_code=401, detail="本地会话无效，请刷新页面")


async def require_mutation(
    _: Annotated[None, Depends(require_session)],
    marker: Annotated[str | None, Header(alias="X-Poethan-Request")] = None,
) -> None:
    if marker != "1":
        raise HTTPException(status_code=403, detail="缺少本地请求标记")


@app.middleware("http")
async def local_only(request: Request, call_next):
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient"}:
        return JSONResponse(status_code=403, content={"detail": "Controller 只接受本机连接"})
    origin = request.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:4173", "http://127.0.0.1:8765", "http://localhost:4173", "http://localhost:8765"}:
        return JSONResponse(status_code=403, content={"detail": "请求来源不受信"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    return response


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": app.version, "dataRoot": str(config.DATA_ROOT)}


@app.get("/api/v1/bootstrap")
def bootstrap(response: Response) -> dict[str, Any]:
    response.set_cookie("poethan_session", SESSION_TOKEN, httponly=True, samesite="strict", secure=False, path="/")
    return {"ok": True, "version": app.version}


@app.get("/api/v1/settings", dependencies=[Depends(require_session)], response_model=ApplicationSettings)
def get_settings() -> ApplicationSettings:
    settings = store.settings()
    settings.ai.configured = secrets.get(AI_KEY_ACCOUNT) is not None
    return settings


@app.put("/api/v1/settings", dependencies=[Depends(require_mutation)], response_model=ApplicationSettings)
def save_settings(payload: SettingsInput) -> ApplicationSettings:
    plugin_directory = str(Path(payload.plugin_directory).expanduser().resolve())
    Path(plugin_directory).mkdir(parents=True, exist_ok=True)
    settings = ApplicationSettings(plugin_directory=plugin_directory, developer_mode=payload.developer_mode, demo_mode=payload.demo_mode, ai=payload.ai)
    if payload.ai_api_key:
        secrets.set(AI_KEY_ACCOUNT, payload.ai_api_key)
    settings.ai.configured = secrets.get(AI_KEY_ACCOUNT) is not None
    store.save_settings(settings)
    return settings


@app.post("/api/v1/ai/test", dependencies=[Depends(require_mutation)])
async def test_ai_connection(payload: AIConnectionInput) -> dict[str, Any]:
    if payload.api_key:
        secrets.set(AI_KEY_ACCOUNT, payload.api_key)
    return await test_ai(AISettings(endpoint=payload.endpoint, model=payload.model, configured=True))


@app.get("/api/v1/servers", dependencies=[Depends(require_session)], response_model=list[ServerProfile])
def list_servers() -> list[ServerProfile]:
    return store.servers()


@app.post("/api/v1/servers", dependencies=[Depends(require_mutation)], response_model=ServerProfile)
def create_server(payload: ServerInput) -> ServerProfile:
    server = ServerProfile.model_validate(payload.model_dump(exclude={"password"}))
    servers = store.servers()
    if any(item.id == server.id for item in servers):
        raise HTTPException(status_code=409, detail="服务器 ID 已存在")
    servers.append(server); store.save_servers(servers)
    if payload.password:
        secrets.set(server_password_account(server.id), payload.password)
    return server


@app.put("/api/v1/servers/{server_id}", dependencies=[Depends(require_mutation)], response_model=ServerProfile)
def update_server(server_id: str, payload: ServerInput) -> ServerProfile:
    servers = store.servers()
    index = next((index for index, item in enumerate(servers) if item.id == server_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="服务器不存在")
    server = ServerProfile.model_validate({**payload.model_dump(exclude={"password"}), "id": server_id, "updated_at": datetime.now(timezone.utc)})
    servers[index] = server; store.save_servers(servers)
    if payload.password:
        secrets.set(server_password_account(server.id), payload.password)
    return server


@app.delete("/api/v1/servers/{server_id}", dependencies=[Depends(require_mutation)], status_code=204)
def delete_server(server_id: str) -> Response:
    servers = store.servers()
    if not any(item.id == server_id for item in servers):
        raise HTTPException(status_code=404, detail="服务器不存在")
    store.save_servers([item for item in servers if item.id != server_id])
    secrets.delete(server_password_account(server_id))
    return Response(status_code=204)


@app.post("/api/v1/servers/test", dependencies=[Depends(require_mutation)])
async def test_server(payload: ConnectionTestInput):
    return await asyncio.to_thread(ssh_service.test, payload.server, payload.accept_host_key, payload.server.password)


@app.get("/api/v1/plugins", dependencies=[Depends(require_session)])
def list_plugins():
    return plugin_service.scan(store.settings())


@app.post("/api/v1/plugins/rescan", dependencies=[Depends(require_mutation)])
def rescan_plugins():
    return plugin_service.scan(store.settings())


@app.post("/api/v1/plugins/import", dependencies=[Depends(require_mutation)])
async def import_plugin(
    files: Annotated[list[UploadFile], File()],
    paths: Annotated[str, Form()],
):
    try:
        relative_paths = json.loads(paths)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="上传路径列表不是有效 JSON") from exc
    if len(files) != len(relative_paths):
        raise HTTPException(status_code=400, detail="文件和路径数量不一致")
    temporary = Path(tempfile.mkdtemp(prefix="poethan-plugin-import-"))
    try:
        total = 0
        normalized: list[Path] = []
        for upload, raw_path in zip(files, relative_paths):
            parts = Path(str(raw_path).replace("\\", "/")).parts
            if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
                raise HTTPException(status_code=400, detail=f"不安全的上传路径：{raw_path}")
            relative = Path(*parts[1:])
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            content = await upload.read()
            total += len(content)
            if total > 100 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="插件包超过 100 MB")
            target.write_bytes(content); normalized.append(relative)
        package = plugin_service.import_directory(temporary, store.settings())
        return package
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


@app.get("/api/v1/run-configs/{server_id}/{plugin_id}", dependencies=[Depends(require_session)])
def get_run_config(server_id: str, plugin_id: str):
    return store.run_configs().get(f"{server_id}:{plugin_id}", {"mode": "standard", "values": {}})


@app.post("/api/v1/runs", dependencies=[Depends(require_mutation)])
async def start_run(payload: RunRequest):
    server = next((item for item in store.servers() if item.id == payload.server_id), None)
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    try:
        return run_manager.start(payload, server, store.settings())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/runs/{run_id}", dependencies=[Depends(require_session)])
def get_run(run_id: str):
    state = run_manager.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return state


@app.post("/api/v1/runs/{run_id}/cancel", dependencies=[Depends(require_mutation)])
def cancel_run(run_id: str):
    if not run_manager.cancel(run_id):
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"ok": True}


@app.get("/api/v1/runs/{run_id}/events", dependencies=[Depends(require_session)])
async def run_events(run_id: str):
    if not run_manager.get(run_id):
        raise HTTPException(status_code=404, detail="运行记录不存在")
    async def stream():
        async for event in run_manager.subscribe(run_id):
            yield f"id: {event.sequence}\nevent: {event.type}\ndata: {event.model_dump_json(by_alias=True)}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/v1/reports", dependencies=[Depends(require_session)])
def list_reports():
    return store.reports()


@app.get("/api/v1/reports/{report_id}", dependencies=[Depends(require_session)])
def get_report(report_id: str):
    report = store.report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


@app.get("/api/v1/reports/{report_id}/html", dependencies=[Depends(require_session)], response_class=HTMLResponse)
def get_report_html(report_id: str):
    report = store.report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    plugin = None
    try:
        plugin = plugin_service.find(store.settings(), str(report.plugin["id"]), str(report.plugin["version"]))
        content, scripted = plugin_report_html(report, plugin)
    except Exception:
        content, scripted = report_html(report), False
    policy = "sandbox allow-scripts; default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'" if scripted else "sandbox; default-src 'none'; style-src 'unsafe-inline'"
    return HTMLResponse(content, headers={"Content-Security-Policy": policy})


@app.post("/api/v1/plugins/open-directory", dependencies=[Depends(require_mutation)])
def open_plugin_directory():
    directory = Path(store.settings().plugin_directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    command = ["open", str(directory)] if os.name == "posix" and shutil.which("open") else ["xdg-open", str(directory)]
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法打开插件目录：{exc}") from exc
    return {"ok": True, "directory": str(directory)}


@app.get("/api/v1/cache", dependencies=[Depends(require_session)])
def cache_info():
    total = sum(path.stat().st_size for path in config.DATA_ROOT.rglob("*") if path.is_file())
    return {"bytes": total, "dataRoot": str(config.DATA_ROOT)}


@app.delete("/api/v1/cache", dependencies=[Depends(require_mutation)])
def clear_cache():
    shutil.rmtree(config.REPORTS_ROOT, ignore_errors=True); config.REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "bytes": 0}


if config.FRONTEND_DIST.exists():
    assets = config.FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        candidate = (config.FRONTEND_DIST / path).resolve()
        if config.FRONTEND_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(config.FRONTEND_DIST / "index.html")
