"""A newly scanned plugin participates in real agent assembly and execution."""
import asyncio
import ast
import json
from pathlib import Path

import pytest

from backend.config import Settings
from app.agent_base import assembly
from app.agent_base.core import hooks, plugins, plugin_runtime
from app.agent_base.core.plugins import PluginManager
from app.agent_base.core.hooks import HookRegistry
from app.agent_base.core.extension_context import extension_scope
from app.agent_base.adapters.execution import dispatch_execution
from app.agent_base.host_api.execution import ExecutionRequest, ExecutionSlots
from app.agent_base.host_api.services import host_services_scope
from app.agent_base.adapters.host_services import ApplicationHostServices


def test_new_directory_binds_tools_and_policy_without_host_registration(tmp_path, monkeypatch):
    directory = tmp_path / "auto_policy_demo"
    directory.mkdir()
    (directory / "__init__.py").write_text('''
from app.agent_base.host_api.services import get_host_services
from app.agent_base.tools.base import Tool
class Probe(Tool):
    def __init__(self):
        super().__init__("auto_probe", "Plugin-owned read-only probe")
        self.read_only = True
    def get_parameters(self): return []
    def run(self, parameters): return "probe ready"
class Provider:
    def ping(self): return "bound"
def create(**kwargs): return Provider()
def bind(ctx):
    provider = get_host_services().resolve_provider("auto_policy_demo")
    ctx.invocation.bind("auto_policy_demo", provider)
    ctx.invocation.tools.append(Probe())
def check(ctx):
    ctx.invocation.allowed = False
    ctx.invocation.message = "new plugin policy"
''', encoding="utf-8")
    (directory / "plugin.json").write_text(json.dumps({
        "schema_version": 1, "id": "auto_policy_demo", "version": "1.0.0",
        "provider": "auto_policy_demo:create", "interfaces": {"ping": {"stage": "prepare"}},
        "contributions": [
            {"id": "auto_policy_demo.bind", "stage": "initialize", "handler": "auto_policy_demo:bind",
             "mode": "service", "scope": "invocation", "interface_id": "assembly.bind"},
            {"id": "auto_policy_demo.check", "stage": "finalize", "handler": "auto_policy_demo:check",
             "mode": "service", "scope": "invocation", "interface_id": "execution.check"},
        ]}), encoding="utf-8")
    settings = Settings(_env_file=None, llm_api_key="test", llm_base_url="http://test/v1", llm_model_id="test",
        plugin_roots=[str(tmp_path)], agent_memory_enabled=False, agent_skills_enabled=False,
        agent_orchestration_enabled=False, agent_design_contract_enabled=False,
        agent_main_subagent_enabled=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(plugins, "_default_manager", PluginManager())
    monkeypatch.setattr(plugin_runtime, "_active", None)
    monkeypatch.setattr(hooks, "_registry", HookRegistry())
    monkeypatch.setattr(assembly, "get_settings", lambda: settings)
    agent, _, _ = asyncio.run(assembly.create_dev_agent(object(), workspace_root=str(tmp_path)))
    assert agent.tool_registry.get_tool("auto_probe").run({}) == "probe ready"
    session = agent.extension_context.fork()
    with extension_scope(session):
        assert session.providers["auto_policy_demo"].ping() == "bound"
        request = asyncio.run(dispatch_execution(ExecutionRequest(ExecutionSlots.CHECK)))
    assert not request.allowed and request.message == "new plugin policy"
    assert session.state("auto_policy_demo") is not agent.extension_context.state("auto_policy_demo")


def test_assembly_has_no_builtin_provider_loading_or_binding():
    root = Path(__file__).resolve().parents[3]
    source = (root / "backend/app/agent_base/assembly.py").read_text(encoding="utf-8")
    assert all(name not in source for name in ("load_memory", "load_skills", "load_contracts", "load_orchestrator",
        'bind("memory"', 'bind("design_contract"', 'bind("orchestration"'))
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("extensions.")
                   for node in ast.walk(tree))


def test_project_paths_and_configuration_can_be_replaced_without_backend_imports(tmp_path):
    from types import SimpleNamespace
    from app.agent_base.host_api import environment
    host = SimpleNamespace(configuration=lambda: "injected", project_id=lambda *a, **k: "project",
        project_storage=lambda *a, **k: SimpleNamespace(state_dir=tmp_path))
    with host_services_scope(host):
        assert environment.configuration() == "injected"
        assert environment.project_id("demo.umlproj") == "project"
        assert environment.project_storage().state_dir == tmp_path
    with pytest.raises(ValueError, match="relative path"):
        ApplicationHostServices().runtime_path("../outside")


def test_missing_assembly_binding_in_active_plan_cannot_silently_skip_policy(monkeypatch):
    from types import SimpleNamespace
    from app.agent_base.adapters.assembly import assemble_extensions
    from app.agent_base.core.extension_context import ExtensionContext
    from app.agent_base.core.plugins import PluginSpec
    spec = PluginSpec("required_policy", "enabled", "provider", "test:create", (), contributions=({
        "id": "required_policy.bind", "interface_id": "assembly.bind"},))
    monkeypatch.setattr(plugins, "_default_manager", PluginManager((spec,)))
    monkeypatch.setattr(plugin_runtime, "_active", None)
    registry = HookRegistry()
    registry.plan_id = "active-incomplete-plan"
    monkeypatch.setattr(hooks, "_registry", registry)
    with pytest.raises(RuntimeError, match="assembly binding is unavailable"):
        asyncio.run(assemble_extensions(ExtensionContext(), settings=SimpleNamespace(enabled=True), inputs={}))
