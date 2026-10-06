"""Controlled publication never switches a running task to another generation."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import Settings
from app.agent_base.core import hooks
from app.agent_base.core.hooks import AgentRuntime, HookContext, HookEvent, HookRegistry
from app.agent_base.core.lifecycle import build_plan, install_plan
from app.agent_base.core.plugins import PluginManager
from app.agent_base.core.plugin_runtime import (
    PluginRefreshService, PluginSnapshot, current_snapshot, plugin_scope, publish_snapshot,
)
from app.trace.tracing import reset_current_trace_sink, set_current_trace_sink


@pytest.fixture(autouse=True)
def restore_generation():
    previous = publish_snapshot(None)
    try:
        yield
    finally:
        publish_snapshot(previous)


def write_plugin(root, name="new_observer", **changes):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "plugin.json"
    data = {"schema_version": 1, "id": name, "version": "1.0.0", "provider": f"{name}:create",
            "interfaces": {"ping": {"stage": "prepare"}}, "defaults": {"limit": 3},
            "contributions": [{"id": f"{name}.observe", "stage": "model_after", "handler": f"{name}:observe"}]}
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def service(tmp_path, **options):
    roots = tmp_path / "plugins"
    roots.mkdir(exist_ok=True)
    settings = Settings(_env_file=None, llm_api_key="test", llm_base_url="http://test/v1", llm_model_id="test",
                        plugin_roots=[str(roots)], **options)
    manager = PluginManager()
    plan = build_plan(manager, settings)
    registry = HookRegistry()
    install_plan(plan, registry)
    frozen = manager.freeze(settings)
    snapshot = PluginSnapshot(frozen, registry, frozen._fixed_settings, plan)
    output = tmp_path / "plans"
    plan.write(output)
    publish_snapshot(snapshot)
    return PluginRefreshService(snapshot, output), roots


def module(monkeypatch, name="new_observer", observe=None):
    instance = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(ping=lambda: kwargs["settings"].plugin_configs[name]["limit"]),
                               observe=observe or (lambda ctx: None))
    monkeypatch.setitem(sys.modules, name, instance)
    return instance


def test_publication_adds_plugin_preserves_old_registry_and_archive(tmp_path, monkeypatch):
    controller, root = service(tmp_path)
    previous = controller.active
    write_plugin(root)
    module(monkeypatch)
    async def run():
        with plugin_scope():
            bound = current_snapshot()
            result = await controller.refresh()
            assert result["status"] == "published" and result["added"] == ["new_observer"]
            assert current_snapshot() is bound is previous
            assert hooks.get_hooks() is previous.registry
            assert not hooks.get_hooks().has_contribution("new_observer.observe")
            from app.agent_base.core.plugins import get_plugin_manager
            assert get_plugin_manager().load_optional("new_observer") is None
        assert current_snapshot() is controller.active
        assert hooks.get_hooks().has_contribution("new_observer.observe")
        assert controller.active.manager.load("new_observer").ping() == 3
        assert (await controller.refresh())["status"] == "unchanged"
    asyncio.run(run())
    assert previous.registry.plan_id != controller.active.registry.plan_id
    assert controller.snapshot_for_plan(previous.registry.plan_id) is previous
    assert (controller.directory / "history" / f"{previous.registry.plan_id}.json").is_file()
    assert json.loads((controller.directory / "plugin-plan.json").read_text())["plan_id"] == controller.active.registry.plan_id


@pytest.mark.parametrize("failure", ["invalid_json", "import", "contribution", "new_router", "existing_manifest", "existing_code", "removed", "configuration"])
def test_rejected_candidate_keeps_catalog_plan_and_active_files(tmp_path, monkeypatch, failure):
    # Own an existing additional plugin before freezing the first generation.
    root = tmp_path / "plugins"
    old_path = write_plugin(root, "old_observer")
    (old_path.parent / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    module(monkeypatch, "old_observer")
    override = tmp_path / "overrides.json"
    override.write_text(json.dumps({"schema_version": 1, "plugins": {}}), encoding="utf-8")
    controller, _ = service(tmp_path, plugin_config_file=str(override))
    previous = controller.active
    path = write_plugin(root)
    module(monkeypatch)
    if failure == "invalid_json":
        path.write_text("broken", encoding="utf-8")
    elif failure == "import":
        write_plugin(root, provider="missing_refresh_module:create")
    elif failure == "contribution":
        write_plugin(root, contributions=[{"id": "broken", "stage": "model_after", "handler": "new_observer:missing"}])
    elif failure == "new_router":
        write_plugin(root, router="new_observer:router")
    elif failure == "existing_manifest":
        write_plugin(root, "old_observer", version="2.0.0")
    elif failure == "existing_code":
        (old_path.parent / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
    elif failure == "removed":
        old_path.unlink()
    else:
        override.write_text(json.dumps({"schema_version": 1, "plugins": {"old_observer": {"config": {"limit": 8}}}}), encoding="utf-8")
    result = asyncio.run(controller.refresh())
    assert result["status"] == "rejected" and result["diagnostics"]
    assert controller.active is previous and current_snapshot() is previous
    assert json.loads((controller.directory / "plugin-plan.json").read_text())["plan_id"] == previous.registry.plan_id
    assert not previous.registry.has_contribution("new_observer.observe")
    if failure in {"new_router", "existing_manifest", "existing_code", "removed", "configuration"}:
        assert any(item["code"] == "restart_required" for item in result["diagnostics"])


def test_concurrent_refreshes_serialize_and_publish_once(tmp_path, monkeypatch):
    controller, root = service(tmp_path)
    write_plugin(root)
    module(monkeypatch)
    async def run():
        results = await asyncio.gather(controller.refresh(), controller.refresh())
        assert sorted(item["status"] for item in results) == ["published", "unchanged"]
    asyncio.run(run())
    assert len(controller.snapshots) == 2


def test_background_and_threads_keep_the_captured_generation(tmp_path, monkeypatch):
    from app.agent_base.core.plugins import get_plugin_manager
    controller, root = service(tmp_path)
    old = controller.active
    write_plugin(root)
    module(monkeypatch)
    async def run():
        wait = asyncio.Event()
        async def background():
            await wait.wait()
            assert hooks.get_hooks() is old.registry
            assert get_plugin_manager() is old.manager
            assert await asyncio.to_thread(lambda: hooks.get_hooks().plan_id) == old.registry.plan_id
            # A new child runtime inherits the same generation independently of run_id.
            token = hooks.set_runtime(AgentRuntime(run_id="parent/child"))
            try:
                assert hooks.get_hooks() is old.registry
            finally:
                hooks.reset_runtime(token)
        with plugin_scope():
            task = asyncio.create_task(background())
        assert (await controller.refresh())["status"] == "published"
        wait.set()
        await task
    asyncio.run(run())


def test_running_agent_retains_old_hooks_and_new_task_gets_new_hooks(tmp_path, monkeypatch):
    from app.agent_base.agents.react_agent import ReActAgent
    from app.agent_base.tools.registry import ToolRegistry
    controller, root = service(tmp_path)
    previous_id = controller.active.registry.plan_id
    calls, events = [], []
    module(monkeypatch, observe=lambda ctx: calls.append(ctx.run_id))
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        async def model(**kwargs):
            started.set()
            await release.wait()
            return {"content": "done", "tool_calls": None}
        async def task(run_id):
            token = hooks.set_runtime(AgentRuntime(run_id=run_id))
            sink_token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append({"event_type": kind, **data})))
            try:
                agent = ReActAgent("test", SimpleNamespace(ainvoke_with_tools=model), ToolRegistry())
                assert await agent.arun("hello") == "done"
            finally:
                reset_current_trace_sink(sink_token)
                hooks.reset_runtime(token)
        active_task = asyncio.create_task(task("old-run"))
        await started.wait()
        write_plugin(root)
        assert (await controller.refresh())["status"] == "published"
        release.set()
        await active_task
        await task("new-run")
    asyncio.run(run())
    assert calls == ["new-run"]
    old_events = [item for item in events if item.get("run_id") == "old-run"]
    new_events = [item for item in events if item.get("run_id") == "new-run"]
    assert old_events and new_events
    assert all(item.get("plan_id") == previous_id for item in old_events)
    assert all(item.get("plan_id") == controller.active.registry.plan_id for item in new_events)


def test_factory_reference_is_frozen_and_snapshot_catalog_cannot_be_mutated(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    write_plugin(root)
    loaded = module(monkeypatch)
    controller, _ = service(tmp_path)
    loaded.create = lambda **kwargs: pytest.fail("must not switch an existing factory")
    assert controller.active.manager.load("new_observer").ping() == 3
    with pytest.raises(RuntimeError, match="cannot be modified"):
        controller.active.manager.register(controller.active.manager.get_spec("new_observer"))


def test_refresh_api_reports_published_and_rejected_without_replacing_active_plan(tmp_path, monkeypatch):
    from app.api.plugins import router
    controller, root = service(tmp_path)
    app = FastAPI()
    app.state.plugin_refresh = controller
    app.state.plugin_plan_dir = controller.directory
    app.include_router(router)
    write_plugin(root)
    module(monkeypatch)
    with TestClient(app) as client:
        response = client.post("/api/plugins/refresh")
        assert response.status_code == 200 and response.json()["status"] == "published"
        plan_id = response.json()["plan_id"]
        assert client.get("/api/plugins/plan").json()["plan_id"] == plan_id
        assert client.get("/api/plugins/diagnostics").json()["plan_id"] == plan_id
        write_plugin(root, version="2.0.0")
        rejected = client.post("/api/plugins/refresh")
        assert rejected.status_code == 409 and rejected.json()["status"] == "rejected"
        assert client.get("/api/plugins/plan").json()["plan_id"] == plan_id


def test_persistence_failure_does_not_publish(tmp_path, monkeypatch):
    from app.agent_base.core.lifecycle import ExecutionPlan
    controller, root = service(tmp_path)
    previous = controller.active
    write_plugin(root)
    module(monkeypatch)
    monkeypatch.setattr(ExecutionPlan, "write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    result = asyncio.run(controller.refresh())
    assert result["status"] == "rejected"
    assert controller.active is previous and current_snapshot() is previous


def test_new_directory_imports_without_core_registration_and_returns_configured_service(tmp_path):
    controller, root = service(tmp_path)
    name = "real_refresh_example"
    path = write_plugin(root, name)
    (path.parent / "__init__.py").write_text(
        "from types import SimpleNamespace\n"
        "def create(*, settings):\n"
        "    return SimpleNamespace(ping=lambda: settings.plugin_configs['real_refresh_example']['limit'])\n"
        "def observe(context):\n"
        "    return None\n", encoding="utf-8")
    result = asyncio.run(controller.refresh())
    assert result["status"] == "published" and result["added"] == [name]
    assert controller.active.manager.load(name).ping() == 3


def test_code_changed_after_rejected_import_attempt_requires_restart(tmp_path, monkeypatch):
    controller, root = service(tmp_path)
    path = write_plugin(root, contributions=[{"id": "bad", "stage": "model_after", "handler": "new_observer:missing"}])
    implementation = path.parent / "__init__.py"
    implementation.write_text("VALUE = 1\n", encoding="utf-8")
    module(monkeypatch)
    assert asyncio.run(controller.refresh())["status"] == "rejected"
    implementation.write_text("VALUE = 2\n", encoding="utf-8")
    write_plugin(root)
    result = asyncio.run(controller.refresh())
    assert result["status"] == "rejected"
    assert any(item["code"] == "restart_required" for item in result["diagnostics"])
