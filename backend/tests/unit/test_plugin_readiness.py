"""Readiness diagnostics and version artifacts survive runtime failures and restarts."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config.plugin_catalog import read_manifest, resolved_settings
from app.agent_base.core import hooks
from app.agent_base.core.hooks import HookRegistry
from app.agent_base.core.lifecycle import discover_plan, install_plan
from app.agent_base.core.plugins import PluginManager
from app.agent_base.core.observability import reset_current_trace_sink, set_current_trace_sink


def plugin(tmp_path, **changes):
    directory = tmp_path / "readiness_example"
    directory.mkdir(exist_ok=True)
    path = directory / "plugin.json"
    data = {"schema_version": 1, "id": "readiness_example", "version": "1.2.3",
            "provider": "readiness_example:create", "interfaces": {"ping": {"stage": "prepare"}}}
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def manager(tmp_path):
    result = PluginManager(())
    result.discover_directories((tmp_path,))
    return result


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"interfaces": {"_internal": {}}},
    {"interfaces": {"close": {}}},
    {"contributions": [{"id": "demo", "stage": "prepare", "handler": "demo:observe", "before": "bad"}]},
    {"contributions": [{"id": "demo", "stage": "prepare", "handler": "demo:observe", "typo": True}]},
])
def test_invalid_metadata_fails_before_import(tmp_path, change):
    path = plugin(tmp_path, **change)
    with pytest.raises(ValueError, match="Invalid"):
        read_manifest(path)


def test_settings_alias_collisions_cannot_silently_overwrite_another_plugin():
    declarations = [{"id": name, "provider": "demo:create", "settings": {"enabled": "shared_enabled"}}
                    for name in ("a", "b")]
    with pytest.raises(ValueError, match="Conflicting plugin setting"):
        resolved_settings(SimpleNamespace(), declarations, {})
    with pytest.raises(ValueError, match="Conflicting plugin setting"):
        resolved_settings(SimpleNamespace(), [{"id": "a", "provider": "demo:create", "defaults": {"enabled": True}}], {})


def test_fingerprint_is_stable_and_detects_code_changes_without_a_version_bump(tmp_path):
    path = plugin(tmp_path)
    implementation = path.parent / "__init__.py"
    implementation.write_text("VALUE = 1\n", encoding="utf-8")
    first = read_manifest(path)
    path.write_text(json.dumps(json.loads(path.read_text()), indent=4), encoding="utf-8")
    assert read_manifest(path)["revision"] == first["revision"]
    implementation.write_text("VALUE = 2\n", encoding="utf-8")
    changed = read_manifest(path)
    assert changed["version"] == first["version"]
    assert changed["manifest_digest"] == first["manifest_digest"]
    assert changed["implementation_digest"] != first["implementation_digest"]
    assert changed["revision"] != first["revision"]


@pytest.mark.parametrize("accepts_settings", [True, False])
def test_discovery_checks_factory_signature_without_constructing_it(tmp_path, monkeypatch, accepts_settings):
    plugin(tmp_path)
    def create(**kwargs):
        pytest.fail("preflight must not construct providers")
    def incompatible():
        pytest.fail("preflight must not construct providers")
    factory = create if accepts_settings else incompatible
    monkeypatch.setitem(sys.modules, "readiness_example", SimpleNamespace(create=factory))
    row = discover_plan(manager(tmp_path), SimpleNamespace()).plugins[0]
    if not accepts_settings:
        assert row["diagnostics"][0]["phase"] == "factory_signature"
        assert row["status"] == "unavailable"
    else:
        assert row["status"] == "discovered"  # No interface check until instantiated.


@pytest.mark.parametrize("failure,phase", [("factory", "factory"), ("missing", "interfaces"), ("async", "interfaces"), ("optional", "interfaces")])
def test_runtime_failure_is_diagnosed_and_cleared_after_success(tmp_path, monkeypatch, failure, phase):
    plugin(tmp_path, interfaces={"ping": {"stage": "prepare"}, "extra": {"required": False}})
    async def async_ping():
        return "pong"
    def create(**kwargs):
        if failure == "factory":
            raise RuntimeError("database unavailable")
        return SimpleNamespace() if failure == "missing" else SimpleNamespace(ping=async_ping) if failure == "async" else SimpleNamespace(ping=lambda: "pong", extra=3)
    module = SimpleNamespace(create=create)
    monkeypatch.setitem(sys.modules, "readiness_example", module)
    catalog = manager(tmp_path)
    plan = discover_plan(catalog, SimpleNamespace())
    original = plan.as_dict()
    assert catalog.load("readiness_example", settings=SimpleNamespace()) is None
    report = catalog.diagnostics()[0]
    assert report["status"] == "unavailable"
    diagnostic = report["diagnostics"][0]
    assert diagnostic["phase"] == phase and diagnostic["code"] == f"{phase}_failed"
    assert diagnostic["version"] == "1.2.3" and len(diagnostic["revision"]) == 64
    assert "defaults" not in report
    assert plan.as_dict() == original  # Runtime failures never mutate plan identity.
    module.create = lambda **kwargs: SimpleNamespace(ping=lambda: "pong")
    assert catalog.load("readiness_example", settings=SimpleNamespace()) is not None
    assert catalog.diagnostics()[0]["diagnostics"] == []


def test_router_failure_is_independent_of_provider_loading(tmp_path, monkeypatch):
    plugin(tmp_path, router="readiness_example:router")
    module = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(ping=lambda: "pong"))
    monkeypatch.setitem(sys.modules, "readiness_example", module)
    catalog = manager(tmp_path)
    assert catalog.load_router("readiness_example", settings=SimpleNamespace()) is None
    assert catalog.load("readiness_example", settings=SimpleNamespace()) is not None
    assert catalog.diagnostics()[0]["diagnostics"][0]["component"] == "router"
    module.router = object()
    assert catalog.load_router("readiness_example", settings=SimpleNamespace()) is module.router
    assert catalog.diagnostics()[0]["diagnostics"] == []


def test_new_plan_preserves_old_archive_and_api_verifies_integrity(tmp_path, monkeypatch):
    from app.api.plugins import router
    path = plugin(tmp_path)
    monkeypatch.setitem(sys.modules, "readiness_example", SimpleNamespace(create=lambda **kwargs: SimpleNamespace(ping=lambda: "pong")))
    catalog = manager(tmp_path)
    old = discover_plan(catalog, SimpleNamespace())
    output = tmp_path / "plans"
    old.write(output)
    data = json.loads(path.read_text())
    data["version"] = "1.2.4"
    path.write_text(json.dumps(data), encoding="utf-8")
    current = discover_plan(manager(tmp_path), SimpleNamespace())
    current.write(output)
    assert old.as_dict()["plan_id"] != current.as_dict()["plan_id"]
    app = FastAPI()
    app.state.plugin_plan = current
    app.state.plugin_manager = catalog
    app.state.plugin_plan_dir = output
    app.include_router(router)
    with TestClient(app) as client:
        identifier = old.as_dict()["plan_id"]
        response = client.get(f"/api/plugins/plans/{identifier}")
        assert response.status_code == 200 and response.json()["plugins"][0]["version"] == "1.2.3"
        assert client.get("/api/plugins/plans/invalid").status_code == 422
        assert client.get(f"/api/plugins/plans/{'0' * 64}").status_code == 404
        report = client.get("/api/plugins/diagnostics").json()
        assert report["plan_id"] == current.as_dict()["plan_id"]
        assert report["plugins"][0]["status"] == "not_loaded"
        (output / "history" / f"{identifier}.json").write_text('{"plan_id":"tampered"}', encoding="utf-8")
        assert client.get(f"/api/plugins/plans/{identifier}").status_code == 503


def test_trace_records_the_installed_plugin_version_and_revision(tmp_path, monkeypatch):
    plugin(tmp_path, interfaces={"ping": {"stage": "prepare", "async": True}})
    async def ping():
        return "pong"
    monkeypatch.setitem(sys.modules, "readiness_example", SimpleNamespace(create=lambda **kwargs: SimpleNamespace(ping=ping)))
    catalog = manager(tmp_path)
    plan = discover_plan(catalog, SimpleNamespace())
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    install_plan(plan, registry)
    events = []
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append({"event_type": kind, **data})))
    try:
        assert asyncio.run(catalog.load("readiness_example", settings=SimpleNamespace()).ping()) == "pong"
    finally:
        reset_current_trace_sink(token)
    contribution = next(event for event in events if event["event_type"] == "plugin_contribution")
    assert contribution["plugin_version"] == "1.2.3"
    assert contribution["plugin_revision"] == plan.plugins[0]["revision"]
    assert contribution["plan_id"] == plan.as_dict()["plan_id"]


def test_async_factory_is_rejected_without_creating_a_coroutine(tmp_path, monkeypatch):
    plugin(tmp_path)
    async def create(**kwargs):
        pytest.fail("must not invoke an async factory")
    monkeypatch.setitem(sys.modules, "readiness_example", SimpleNamespace(create=create))
    catalog = manager(tmp_path)
    row = discover_plan(catalog, SimpleNamespace()).plugins[0]
    assert row["status"] == "unavailable"
    assert row["diagnostics"][0]["phase"] == "factory_signature"


def test_check_command_returns_failure_without_instantiating_plugins(tmp_path, monkeypatch, capsys):
    from backend import export_plugin_plan
    plugin(tmp_path, provider="missing_readiness_module:create")
    catalog = manager(tmp_path)
    monkeypatch.setattr(export_plugin_plan, "Settings", lambda **kwargs: SimpleNamespace(plugin_plan_dir=str(tmp_path / "check")))
    monkeypatch.setattr(export_plugin_plan, "get_plugin_manager", lambda: catalog)
    assert export_plugin_plan.main(["--check"]) == 1
    output = capsys.readouterr().out
    assert "import/import_failed" in output and "unavailable" in output
