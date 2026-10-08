"""Directory scanning, self-owned config and real dispatch share one catalog."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.config.plugin_catalog import BUILTIN_PLUGIN_ROOT, scan_manifests
from app.agent_base.core import hooks
from app.agent_base.host_api.lifecycle import HookContext, HookEvent
from app.agent_base.core.hooks import HookRegistry
from app.agent_base.core.lifecycle import build_plan, discover_plan, install_plan
from app.agent_base.core.plugins import PluginManager
from app.agent_base.core.observability import set_current_trace_sink, reset_current_trace_sink


def write_plugin(root, name, **changes):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": 1, "id": name, "version": "1.0.0", "provider": f"{name}:create",
            "interfaces": {"ping": {"stage": "prepare"}}, "defaults": {"limit": 3}}
    data.update(changes)
    path = directory / "plugin.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def settings(**changes):
    return Settings(_env_file=None, llm_api_key="test-key", llm_base_url="http://test/v1", llm_model_id="test", **changes)


def test_builtin_catalog_is_owned_by_seven_directories_and_keeps_all_interfaces():
    declarations = scan_manifests((BUILTIN_PLUGIN_ROOT,))
    assert {item["id"] for item in declarations} == {"skills", "memory", "trace", "evals", "orchestration", "knowledge_graph", "design_contract"}
    assert sum(len(item["interfaces"]) for item in declarations) == 63
    trace = next(item for item in declarations if item["id"] == "trace")
    assert trace["interfaces"]["attach"]["required"] is False
    assert trace["interfaces"]["fork"]["required"] is False
    assert trace["interfaces"]["fork"]["wrap_result"] is True
    memory = next(item for item in declarations if item["id"] == "memory")
    assert memory["interfaces"]["observe"]["required"] is False
    assert all(Path(item["source"]).name == "plugin.json" for item in declarations)
    assert len(build_plan(PluginManager(), settings()).as_dict()["stages"]) == 13


def test_scan_reads_only_direct_child_manifests_without_importing_modules(tmp_path):
    write_plugin(tmp_path, "metadata_only")
    # Importing this package would fail, but metadata discovery must succeed.
    (tmp_path / "metadata_only" / "__init__.py").write_text("raise RuntimeError('must not import')", encoding="utf-8")
    write_plugin(tmp_path / "internal", "nested")
    (tmp_path / "ordinary").mkdir()
    manager = PluginManager(())
    manager.discover_directories((tmp_path, tmp_path))
    assert [spec.name for spec in manager.specs] == ["metadata_only"]
    assert "metadata_only" not in sys.modules


def test_additional_directory_config_dispatch_and_observation_without_core_registration(tmp_path, monkeypatch):
    name = "directory_dispatch_example"
    calls = []
    write_plugin(tmp_path, name, interfaces={"ping": {"stage": "prepare", "async": True}}, contributions=[
        {"id": f"{name}.observe", "stage": "prepare", "handler": f"{name}:observe", "mode": "observer"}])
    async def ping():
        await asyncio.sleep(0)
        return "pong"
    def create(**kwargs):
        calls.append(kwargs["settings"].plugin_directory_dispatch_example_limit)
        return SimpleNamespace(ping=ping)
    monkeypatch.setitem(sys.modules, name, SimpleNamespace(create=create, observe=lambda ctx: calls.append("observe")))
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"schema_version": 1, "plugins": {name: {"config": {"limit": 7}}}}), encoding="utf-8")
    configuration = settings(plugin_roots=[str(tmp_path)], plugin_config_file=str(overrides))
    manager = PluginManager()
    registry, events = HookRegistry(), []
    monkeypatch.setattr(hooks, "_registry", registry)
    plan = build_plan(manager, configuration)
    assert calls == []  # Factory remains lazy throughout discovery and installation.
    install_plan(plan, registry)
    provider = manager.load(name, settings=configuration)
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append({"event_type": kind, **data})))
    try:
        asyncio.run(registry.aemit(HookEvent.PREPARE, HookContext(HookEvent.PREPARE, "test")))
        assert asyncio.run(provider.ping()) == "pong"
    finally:
        reset_current_trace_sink(token)
    assert calls == [7, "observe"]  # Service invocation does not broadcast the phase again.
    service = next(item for item in events if item.get("mode") == "service")
    assert service["contribution_id"] == f"{name}.interface.ping" and service["plan_id"] == plan.as_dict()["plan_id"]


def test_external_module_is_importable_from_its_scanned_root(tmp_path):
    name = "importable_directory_plugin"
    write_plugin(tmp_path, name)
    (tmp_path / name / "__init__.py").write_text(
        "from types import SimpleNamespace\ndef create(*, settings):\n    return SimpleNamespace(ping=lambda: settings.plugin_importable_directory_plugin_limit)\n", encoding="utf-8")
    manager = PluginManager(())
    manager.discover_directories((tmp_path,))
    assert manager.load(name, settings=SimpleNamespace()).ping() == 3


def test_deployment_overrides_and_legacy_environment_precedence(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema_version": 1, "plugins": {"memory": {
        "enabled": False, "provider": "replacement:create", "config": {"recall_top_k": 8}}}}), encoding="utf-8")
    first = settings(plugin_config_file=str(path))
    assert first.agent_memory_enabled is False and first.agent_memory_provider == "replacement:create"
    assert first.agent_memory_recall_top_k == 8
    monkeypatch.setenv("AGENT_MEMORY_ENABLED", "true")
    monkeypatch.setenv("AGENT_MEMORY_RECALL_TOP_K", "5")
    second = settings(plugin_config_file=str(path))
    assert second.agent_memory_enabled is True and second.agent_memory_recall_top_k == 5
    assert second.plugin_configs["memory"]["recall_top_k"] == 5


@pytest.mark.parametrize("failure", ["duplicate", "invalid", "missing_root", "slot_collision"])
def test_failed_scan_is_atomic(tmp_path, failure):
    manager = PluginManager(())
    existing = tmp_path / "existing"
    write_plugin(existing, "kept")
    manager.discover_directories((existing,))
    roots = [tmp_path / "candidate"]
    path = write_plugin(roots[0], "new_plugin")
    if failure == "duplicate":
        write_plugin(roots[0], "other", id="new_plugin")
    elif failure == "invalid":
        path.write_text("not JSON", encoding="utf-8")
    elif failure == "missing_root":
        roots.append(tmp_path / "missing")
    else:
        write_plugin(roots[0], "other", slot="new_plugin")
    with pytest.raises(ValueError):
        manager.discover_directories(roots)
    assert [spec.name for spec in manager.specs] == ["kept"]


@pytest.mark.parametrize("failure", ["missing", "disabled", "cycle", "import_error"])
def test_dependencies_exclude_unavailable_plugins_without_constructing_providers(tmp_path, monkeypatch, failure):
    for name, dependencies in (("dependency_a", ["dependency_b"]), ("dependency_b", ["dependency_a"] if failure == "cycle" else [])):
        if name == "dependency_b" and failure == "missing":
            continue
        write_plugin(tmp_path, name, dependencies=dependencies, enabled_by_default=not (name == "dependency_b" and failure == "disabled"))
        if not (name == "dependency_b" and failure == "import_error"):
            monkeypatch.setitem(sys.modules, name, SimpleNamespace(create=lambda **kwargs: pytest.fail("factory must remain lazy")))
    manager = PluginManager(())
    manager.discover_directories((tmp_path,))
    plan = discover_plan(manager, SimpleNamespace()).as_dict()
    first = next(item for item in plan["plugins"] if item["name"] == "dependency_a")
    assert first["status"] == "unavailable" and first["error"]
    assert first["contributions"] == []
    assert manager.load("dependency_a", settings=SimpleNamespace()) is None


def test_unknown_parameter_override_is_rejected_before_configuration_is_published(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema_version": 1, "plugins": {"memory": {"config": {"typo": 8}}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown config keys"):
        settings(plugin_config_file=str(path))


def test_invalid_public_stage_rejects_plugin_contributions(tmp_path, monkeypatch):
    write_plugin(tmp_path, "invalid_stage", interfaces={"ping": {"stage": "invented_stage"}})
    monkeypatch.setitem(sys.modules, "invalid_stage", SimpleNamespace(create=lambda **kwargs: None))
    manager = PluginManager(())
    manager.discover_directories((tmp_path,))
    plan = discover_plan(manager, SimpleNamespace()).as_dict()
    assert plan["plugins"][0]["status"] == "unavailable"
    assert plan["plugins"][0]["contributions"] == []


def test_repository_example_runs_through_real_jsonl_and_generated_graph(tmp_path, monkeypatch):
    from app.agent_base.agents.react_agent import ReActAgent
    from app.agent_base.tools.registry import ToolRegistry
    from app.agent_base.core.operations import operation_scope
    from app.agent_base.host_api.tracing import TraceSessionRequest
    roots = [str(BUILTIN_PLUGIN_ROOT.parent / "examples" / "plugins")]
    override = tmp_path / "config.json"
    override.write_text(json.dumps({"schema_version": 1, "plugins": {"task_notes": {
        "config": {"label": "Configured external plugin"}}}}), encoding="utf-8")
    configuration = settings(plugin_roots=roots, plugin_config_file=str(override))
    manager = PluginManager()
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    plan = build_plan(manager, configuration)
    install_plan(plan, registry)
    sink = manager.load("trace", settings=configuration).create(TraceSessionRequest(session_id="directory-plugin", trace_dir=str(tmp_path)))
    token = set_current_trace_sink(sink)
    runtime_token = hooks.set_runtime(hooks.AgentRuntime(run_id="example-run"))
    async def model(**kwargs):
        return {"content": "done", "tool_calls": None}
    async def run():
        plugin = manager.load("task_notes", settings=configuration)
        with operation_scope("prepare", run_id="example-run", stage="prepare"):
            assert plugin.describe() == "Configured external plugin"
        assert await ReActAgent("test", SimpleNamespace(ainvoke_with_tools=model), ToolRegistry()).arun("hello") == "done"
    try:
        asyncio.run(run())
    finally:
        hooks.reset_runtime(runtime_token)
        reset_current_trace_sink(token)
        sink.close()
    events = [json.loads(line) for line in Path(sink.path).read_text(encoding="utf-8").splitlines()]
    contributions = [item for item in events if item["event_type"] == "plugin_contribution" and item["plugin"] == "task_notes"]
    assert {item["contribution_id"] for item in contributions} == {"task_notes.interface.describe", "task_notes.run_start"}
    assert all(item["status"] == "executed" and item["plan_id"] == plan.as_dict()["plan_id"] for item in contributions)
    assert any(item["event_type"] == "plugin_example" for item in events)
    assert "task_notes.run_start" in plan.mermaid("organization")
    assert "Configured external plugin" not in json.dumps(plan.as_dict())


def test_previous_extra_plugin_list_and_deployment_overrides_remain_compatible(tmp_path):
    manifest = tmp_path / "legacy.json"
    manifest.write_text(json.dumps({"schema_version": 1, "plugins": [{"name": "legacy_plugin",
        "provider": "missing:create", "interfaces": ["ping"]}]}), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"schema_version": 1, "plugins": {"legacy_plugin": {"enabled": False}}}), encoding="utf-8")
    configuration = settings(plugin_manifest_file=str(manifest), plugin_config_file=str(config))
    plan = build_plan(PluginManager(), configuration).as_dict()
    assert next(item for item in plan["plugins"] if item["name"] == "legacy_plugin")["status"] == "disabled"


def test_optional_dependency_and_disabled_provider_do_not_require_imports(tmp_path):
    write_plugin(tmp_path, "disabled_plugin", enabled_by_default=False, optional_dependencies=["absent"])
    manager = PluginManager(())
    manager.discover_directories((tmp_path,))
    plan = discover_plan(manager, SimpleNamespace()).as_dict()
    assert plan["plugins"][0]["status"] == "disabled"
    assert plan["plugins"][0]["optional_dependencies"] == ["absent"]
    assert "disabled_plugin" not in sys.modules


def test_deployment_configuration_failure_preserves_previous_catalog(tmp_path):
    manager = PluginManager()
    manager.configure(SimpleNamespace())
    previous = manager.specs
    write_plugin(tmp_path, "pending_plugin")
    override = tmp_path / "config.json"
    override.write_text(json.dumps({"schema_version": 1, "plugins": {"pending_plugin": {"config": {"typo": 2}}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown config keys"):
        manager.configure(SimpleNamespace(plugin_roots=[str(tmp_path)], plugin_config_file=str(override)))
    assert manager.specs == previous


def test_core_is_reserved_for_framework_contributions(tmp_path):
    write_plugin(tmp_path, "core")
    with pytest.raises(ValueError, match="Invalid plugin ID"):
        scan_manifests((tmp_path,))


def test_optional_domain_ports_degrade_when_plugin_directory_is_absent(monkeypatch):
    from app.agent_base.core import plugins
    from app.agent_base.adapters.tracing import load_trace, NoOpTraceProvider
    monkeypatch.setattr(plugins, "_default_manager", PluginManager(()))
    assert PluginManager(()).load_optional("memory", settings=SimpleNamespace()) is None
    assert isinstance(load_trace(settings=SimpleNamespace()), NoOpTraceProvider)


def test_absent_plugin_has_no_owned_defaults_or_enabled_default(monkeypatch):
    from backend.config import plugin_catalog
    monkeypatch.setattr(plugin_catalog, "packaged_manifests", lambda: ())
    assert plugin_catalog.packaged_enabled("memory") is False
    assert plugin_catalog.packaged_default("memory", "recall_top_k", 0) == 0


def test_scanned_router_loads_without_constructing_disabled_provider(tmp_path, monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    router = APIRouter()
    @router.get("/example-plugin")
    def endpoint():
        return {"source": "scanned plugin"}
    name = "extra_router_plugin"
    write_plugin(tmp_path, name, enabled_by_default=False, router=f"{name}:router")
    monkeypatch.setitem(sys.modules, name, SimpleNamespace(router=router,
        create=lambda **kwargs: pytest.fail("router loading must not construct the provider")))
    manager = PluginManager(())
    manager.discover_directories((tmp_path,))
    app = FastAPI()
    app.include_router(manager.load_router(name, settings=SimpleNamespace()))
    with TestClient(app) as client:
        assert client.get("/example-plugin").json() == {"source": "scanned plugin"}
    assert manager.load(name, settings=SimpleNamespace()) is None
