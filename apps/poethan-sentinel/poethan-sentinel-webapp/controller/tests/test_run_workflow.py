from __future__ import annotations

import pytest

from app import config
from app.models import AuthenticationKind, RunRequest, ServerProfile
from app.plugins import plugin_service
from app.reports import plugin_report_html
from app.runs import RunManager
from app.storage import store


@pytest.mark.asyncio
async def test_demo_run_emits_events_and_builds_plugin_report() -> None:
    manager = RunManager()
    settings = store.settings()
    server = ServerProfile(id="workflow-demo", name="工作流演示机", authentication=AuthenticationKind.demo, alias="demo")
    request = RunRequest(
        server_id=server.id,
        plugin_id="doris-diagnostic",
        plugin_version="0.3.0",
        mode="standard",
        values={"POETHAN_PLUGIN_HOME": "/opt/poethan-sentinel/plugins"},
        ai_enabled=False,
    )
    state = manager.start(request, server, settings)
    streamed = [event async for event in manager.subscribe(state.id)]
    await manager.tasks[state.id]
    assert state.status == "completed"
    assert state.report_id
    assert streamed[-1].type == "complete"
    assert any(event.stage == "download" for event in streamed)
    report = store.report(state.report_id)
    assert report and "HOT_THREADS" in report.raw_output
    plugin = plugin_service.find(settings, "doris-diagnostic", "0.3.0")
    rendered, scripted = plugin_report_html(report, plugin)
    assert scripted is True
    assert "Doris 诊断报告" in rendered
    assert "trade-event-storage-to-doris" in rendered
