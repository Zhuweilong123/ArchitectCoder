"""Developer commands exercise real plugin contracts in a private scheduler."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from backend.plugin_dev import main, scaffold
from app.agent_base.core.hooks import get_hooks, get_runtime
from app.agent_base.core.plugin_runtime import current_snapshot
from app.agent_base.core.observability import current_trace_sink


def command(capsys, *args):
    code = main(list(map(str, args)))
    report = json.loads(capsys.readouterr().out)
    assert code == int(report["status"] != "passed")
    return report


def plugin(tmp_path, monkeypatch, provider, **changes):
    name = "dev_test_" + uuid.uuid4().hex
    directory = tmp_path / name
    directory.mkdir()
    manifest = {"schema_version": 1, "id": name, "version": "0.1.0", "provider": f"{name}:create",
                "interfaces": {"ping": {"stage": "prepare"}}}
    manifest.update(changes)
    (directory / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    module = SimpleNamespace(create=lambda *, settings, **kwargs: provider)
    monkeypatch.setitem(sys.modules, name, module)
    return directory, module


def test_generated_plugin_passes_check_and_real_sync_async_and_observer_runs(tmp_path, capsys):
    name = "starter_" + uuid.uuid4().hex
    created = command(capsys, "new", name, "--root", tmp_path)
    directory = Path(created["directory"])
    assert set(created["files"]) == {"plugin.json", "__init__.py", "README.md", "smoke.json"}
    before = get_hooks(), get_runtime(), current_snapshot(), current_trace_sink()
    checked = command(capsys, "check", directory)
    assert checked["status"] == "passed" and not checked["provider_instantiated"]
    checked = command(capsys, "check", directory / "plugin.json", "--instantiate")
    assert all(row["implemented"] for row in checked["interfaces"])
    described = command(capsys, "run", directory, "--method", "describe")
    assert described["result"] == {"label": name}
    echoed = command(capsys, "run", directory, "--method", "echo", "--input", directory / "smoke.json")
    assert echoed["result"] == {"label": name, "text": "hello"}
    service = next(event for event in echoed["events"] if event.get("mode") == "service")
    assert service["status"] == "executed" and service["plan_id"] == echoed["plan_id"]
    assert service["duration_ms"] >= 0 and service["plugin_version"] == "0.1.0"
    output = tmp_path / "reports" / "stage.json"
    observed = command(capsys, "run", directory, "--stage", "prepare", "--output", output)
    assert json.loads(output.read_text(encoding="utf-8")) == observed
    assert not observed["provider_instantiated"]
    assert [event["plugin"] for event in observed["events"]] == [name, name]
    assert before == (get_hooks(), get_runtime(), current_snapshot(), current_trace_sink())


def test_scaffold_does_not_overwrite_existing_directory(tmp_path):
    target = tmp_path / "kept"
    target.mkdir()
    (target / "important.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        scaffold("kept", tmp_path)
    assert [item.name for item in target.iterdir()] == ["important.txt"]


@pytest.mark.parametrize("name", ["../escaped", "a-b", "1name", "class", "core", "json"])
def test_scaffold_rejects_invalid_or_reserved_package_names(tmp_path, name):
    with pytest.raises(ValueError):
        scaffold(name, tmp_path)
    assert not list(tmp_path.iterdir())


def test_declaration_check_does_not_instantiate_and_target_disabled_default_is_checked(tmp_path, monkeypatch, capsys):
    directory, module = plugin(tmp_path, monkeypatch, None, enabled_by_default=False)

    def create(*, settings):
        pytest.fail("static check must not construct resources")
    module.create = create
    assert command(capsys, "check", directory)["status"] == "passed"


@pytest.mark.parametrize("failure", ["missing", "non_callable", "undeclared_async", "factory", "async_factory", "signature"])
def test_contract_errors_fail_with_diagnostics_and_close_rejected_instances(tmp_path, monkeypatch, capsys, failure):
    closed = []
    async def ping():
        return "pong"
    provider = SimpleNamespace(ping=lambda: "pong", close=lambda: closed.append(True))
    if failure == "missing":
        del provider.ping
    elif failure == "non_callable":
        provider.ping = 3
    elif failure == "undeclared_async":
        provider.ping = ping
    directory, module = plugin(tmp_path, monkeypatch, provider)
    if failure == "factory":
        def create(*, settings):
            raise RuntimeError("construction failed")
        module.create = create
    elif failure == "async_factory":
        async def create(*, settings):
            return provider
        module.create = create
    elif failure == "signature":
        module.create = lambda: provider
    result = command(capsys, "check", directory, "--instantiate")
    assert result["status"] == "failed" and result["diagnostics"]
    assert closed == ([True] if failure in {"missing", "non_callable", "undeclared_async"} else [])


def test_optional_missing_interface_is_reported_without_failing(tmp_path, monkeypatch, capsys):
    directory, _ = plugin(tmp_path, monkeypatch, SimpleNamespace(ping=lambda: "pong"),
                          interfaces={"ping": {"stage": "prepare"}, "extra": {"stage": "prepare", "required": False}})
    result = command(capsys, "check", directory, "--instantiate")
    assert result["status"] == "passed" and result["interfaces"][1]["implemented"] is False
    assert command(capsys, "run", directory, "--method", "extra")["status"] == "failed"


def test_factory_input_config_and_async_cleanup_use_private_snapshot(tmp_path, monkeypatch, capsys):
    cleanups = []
    directory, module = plugin(tmp_path, monkeypatch, None, defaults={"limit": 3})
    def create(*, settings, factor):
        value = settings.plugin_configs[directory.name]["limit"] * factor
        async def aclose():
            cleanups.append(get_hooks().plan_id)
        return SimpleNamespace(ping=lambda: value, aclose=aclose)
    module.create = create
    fixture = tmp_path / "input.json"
    fixture.write_text(json.dumps({"config": {"limit": 4}, "factory_kwargs": {"factor": 5}}), encoding="utf-8")
    result = command(capsys, "run", directory, "--method", "ping", "--input", fixture)
    assert result["result"] == 20 and cleanups == [result["plan_id"]]


def test_invalid_method_and_arguments_do_not_call_service(tmp_path, monkeypatch, capsys):
    calls = []
    directory, module = plugin(tmp_path, monkeypatch, SimpleNamespace(ping=lambda required: calls.append(required)))
    old_factory = module.create
    def create(**kwargs):
        calls.append("create")
        return old_factory(**kwargs)
    module.create = create
    assert command(capsys, "run", directory, "--method", "close")["status"] == "failed"
    assert calls == []
    invalid = command(capsys, "run", directory, "--method", "ping")
    assert invalid["status"] == "failed" and "required" in invalid["diagnostics"][0]["message"]
    assert calls == ["create"]


@pytest.mark.parametrize("fixture", [{"kwargs": []}, {"config": []}, {"context": {"runtime": {}}},
                                      {"context": {"messages": "wrong"}}, {"context": {"payload": None}},
                                      {"unknown": 1}])
def test_invalid_fixtures_fail_before_execution(tmp_path, monkeypatch, capsys, fixture):
    directory, module = plugin(tmp_path, monkeypatch, None)
    module.create = lambda **kwargs: pytest.fail("invalid input must not instantiate")
    path = tmp_path / "input.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    assert command(capsys, "run", directory, "--method", "ping", "--input", path)["status"] == "failed"


def test_service_exception_is_traced_and_resources_closed(tmp_path, monkeypatch, capsys):
    closed = []
    def ping():
        raise RuntimeError("service failed")
    directory, _ = plugin(tmp_path, monkeypatch, SimpleNamespace(ping=ping, close=lambda: closed.append(True)))
    result = command(capsys, "run", directory, "--method", "ping")
    assert result["status"] == "failed" and closed == [True]
    assert any(event.get("mode") == "service" and event["status"] == "error" for event in result["events"])


def test_non_fatal_observer_failure_still_fails_development_run(tmp_path, monkeypatch, capsys):
    directory, module = plugin(tmp_path, monkeypatch, None)
    manifest = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
    manifest["contributions"] = [{"id": "observer", "stage": "prepare", "mode": "observer",
                                  "handler": f"{directory.name}:observe"}]
    (directory / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    async def observe(context):
        assert context.agent_name == "Tester" and context.payload["sample"] is True
        assert context.payload["plugin_plan_id"] == get_hooks().plan_id
        raise RuntimeError("observer failed")
    module.observe = observe
    fixture = tmp_path / "input.json"
    fixture.write_text(json.dumps({"context": {"agent_name": "Tester", "payload": {"sample": True}}}), encoding="utf-8")
    result = command(capsys, "run", directory, "--stage", "prepare", "--input", fixture)
    assert result["status"] == "failed" and not result["provider_instantiated"]
    assert result["events"][0]["error_message"] == "observer failed"
    assert command(capsys, "run", directory, "--stage", "run_start")["status"] == "failed"


def test_dependencies_checked_but_their_observers_are_not_executed(tmp_path, monkeypatch, capsys):
    dependencies = tmp_path / "dependencies"
    dependencies.mkdir()
    dep, dep_module = plugin(dependencies, monkeypatch, None)
    target, module = plugin(tmp_path, monkeypatch, SimpleNamespace(ping=lambda: "pong"), dependencies=[dep.name])
    for directory, implementation in ((dep, dep_module), (target, module)):
        data = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
        data["contributions"] = [{"id": f"{directory.name}.observe", "stage": "prepare",
                                  "mode": "observer", "handler": f"{directory.name}:observe"}]
        (directory / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
        implementation.observe = lambda ctx: None
    dep_module.observe = lambda ctx: pytest.fail("dependency observer must not run")
    # An unrelated broken plugin in the dependency root must not be imported.
    unrelated, broken = plugin(dependencies, monkeypatch, None)
    broken.create = 1
    assert command(capsys, "check", target)["status"] == "failed"
    checked = command(capsys, "check", target, "--root", dependencies)
    assert checked["status"] == "passed" and len(checked["declarations"]) == 2
    ran = command(capsys, "run", target, "--stage", "prepare", "--root", dependencies)
    assert ran["status"] == "passed" and {event["plugin"] for event in ran["events"]} == {target.name}


def test_cli_works_without_model_credentials_or_server(tmp_path):
    name = "cli_" + uuid.uuid4().hex
    scaffold(name, tmp_path)
    root = Path(__file__).resolve().parents[2]
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "AGENT_", "PLUGIN_"))}
    process = subprocess.run([sys.executable, str(root / "plugin_dev.py"), "run", str(tmp_path / name),
                              "--method", "echo", "--input", str(tmp_path / name / "smoke.json")],
                             env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=45)
    assert process.returncode == 0, process.stderr + process.stdout
    assert json.loads(process.stdout)["result"] == {"label": name, "text": "hello"}


def test_dependency_service_can_be_called_explicitly_without_broadcasting_hooks(tmp_path, monkeypatch, capsys):
    from app.agent_base.core.plugins import get_plugin_manager
    dependencies = tmp_path / "dependencies"
    dependencies.mkdir()
    dep, _ = plugin(dependencies, monkeypatch, SimpleNamespace(ping=lambda: "dependency result"))
    def ping():
        return get_plugin_manager().load(dep.name, settings=current_snapshot().settings).ping()
    target, _ = plugin(tmp_path, monkeypatch, SimpleNamespace(ping=ping), dependencies=[dep.name])
    ran = command(capsys, "run", target, "--method", "ping", "--root", dependencies)
    assert ran["status"] == "passed" and ran["result"] == "dependency result"
    assert {event["plugin"] for event in ran["events"] if event.get("mode") == "service"} == {target.name, dep.name}


def test_cleanup_failure_fails_report_and_restores_context(tmp_path, monkeypatch, capsys):
    before = current_snapshot(), get_runtime(), current_trace_sink()
    def close():
        raise RuntimeError("cleanup failed")
    directory, _ = plugin(tmp_path, monkeypatch, SimpleNamespace(ping=lambda: "pong", close=close))
    ran = command(capsys, "run", directory, "--method", "ping")
    assert ran["status"] == "failed" and ran["result"] == "pong"
    assert ran["diagnostics"][0]["code"] == "cleanup_failed"
    assert before == (current_snapshot(), get_runtime(), current_trace_sink())
