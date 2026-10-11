"""Regression checks for narrow host contracts and replaceable graph preindexing."""
import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.agent_base.adapters.host_services import ApplicationHostServices
from app.agent_base.core.extension_context import ExtensionContext, extension_scope
from app.agent_base.host_api.assembly import AssemblyRequest
from app.agent_base.host_api.services import (
    get_assembly_services, get_environment_services, get_provider_services, host_services_scope,
)
from app.models.uml import Project, UmlDiagram
from extensions.evals.graph_fixture import preindex_project_graph
from extensions.evals.models import ProjectManifest
from extensions.knowledge_graph.plugin_api import GraphPreindexResult

ROOT = Path(__file__).resolve().parents[3]


def fixture_project(workspace):
    source = workspace / "src"
    source.mkdir()
    (source / "service.py").write_text("class Service: pass\n", encoding="utf-8")
    (workspace / "design.umlproj").write_text(Project(name="fixture", diagrams=[
        UmlDiagram(name="Design")]).model_dump_json(), encoding="utf-8")
    return ProjectManifest(id="fixture", fixture="fixture", entry_file="design.umlproj", source_dir="src", test_dir="test")


def test_assembly_can_use_only_configuration_and_provider_resolution():
    from extensions.memory.assembly import bind
    provider = object()
    bindings = []
    settings = SimpleNamespace(agent_memory_recall_top_k=2, agent_memory_recall_max_tokens=120)
    host = SimpleNamespace(configuration=lambda: settings, resolve_provider=lambda *a, **k: provider)
    request = AssemblyRequest({}, lambda *a, **k: bindings.append((a, k)))
    with host_services_scope(host):
        assert get_assembly_services() is host
        bind(SimpleNamespace(invocation=request))
    assert bindings == [(("memory", provider), {"options": {"recall_top_k": 2, "recall_max_tokens": 120}})]


def test_options_are_detached_and_configuration_follows_request_scope():
    settings = SimpleNamespace()
    context = ExtensionContext(metadata={"execution_settings": settings, "project_id": "project",
        "plugin_options": {"demo": {"nested": ["original"]}}}, providers={"demo": settings})
    options = context.provider_options("demo")
    options["nested"].append("changed")
    assert context.provider_options("demo")["nested"] == ["original"]
    with pytest.raises(TypeError):
        options["new"] = 1
    assert context.project_id == "project"
    assert context.provider("demo", object) is settings
    assert context.provider("absent", object) is None
    with extension_scope(context):
        assert get_environment_services().configuration() is settings


def test_narrow_service_views_keep_concurrent_request_bindings():
    import asyncio
    async def run():
        async def request(name):
            host = SimpleNamespace(configuration=lambda: name, resolve_provider=lambda slot: name)
            with host_services_scope(host):
                await asyncio.sleep(0)
                assert get_environment_services().configuration() == name
                assert get_provider_services().resolve_provider("demo") == name
        await asyncio.gather(request("first"), request("second"))
    asyncio.run(run())


def test_preindex_uses_configured_provider_without_selecting_database(tmp_path, monkeypatch):
    from backend.config import Settings
    from app.agent_base.core import plugins, hooks, plugin_runtime
    from app.agent_base.core.plugins import PluginManager
    from app.agent_base.core.hooks import HookRegistry
    calls = []
    manager = PluginManager()
    provider = SimpleNamespace(**{name: lambda *a, **k: None
        for name in manager.get_spec("knowledge_graph").required_methods})
    def index(request):
        calls.append(request)
        return GraphPreindexResult(3, 4, 5, 6)
    provider.preindex_project = index
    monkeypatch.setitem(sys.modules, "custom_graph_fixture", SimpleNamespace(create=lambda **k: provider))
    monkeypatch.setattr(plugins, "_default_manager", manager)
    monkeypatch.setattr(hooks, "_registry", HookRegistry())
    monkeypatch.setattr(plugin_runtime, "_active", None)
    settings = Settings(_env_file=None, llm_api_key="test", llm_base_url="http://test/v1", llm_model_id="test",
        agent_knowledge_graph_provider="custom_graph_fixture:create")
    host = ApplicationHostServices()
    host.configuration = lambda: settings
    with host_services_scope(host):
        result = preindex_project_graph(tmp_path, fixture_project(tmp_path))
    assert (result["design_nodes"], result["source_nodes"], result["design_edges"], result["source_edges"]) == (3, 4, 5, 6)
    assert len(calls) == 1 and calls[0].project.name == "fixture"
    assert calls[0].workspace_root == str(tmp_path.resolve())
    assert calls[0].project_id == result["project_id"]
    assert not list(tmp_path.rglob("*.db"))


@pytest.mark.parametrize("provider", [None, SimpleNamespace(), SimpleNamespace(preindex_project=lambda request: {})])
def test_preindex_unavailable_or_invalid_capability_fails_explicitly(tmp_path, provider):
    host = SimpleNamespace(project_id=lambda *a, **k: "fixture", resolve_provider=lambda *a, **k: provider)
    with host_services_scope(host), pytest.raises((ValueError, TypeError), match="preindex"):
        preindex_project_graph(tmp_path, fixture_project(tmp_path))
    assert not list(tmp_path.rglob("*.db"))


def test_local_preindex_stays_in_fixture_even_with_deployment_database(tmp_path):
    from extensions.knowledge_graph.provider import LocalKnowledgeGraphProvider
    manifest = fixture_project(tmp_path)
    outside = tmp_path / "deployment.db"
    host = ApplicationHostServices()
    host.configuration = lambda: SimpleNamespace()
    host.resolve_provider = lambda *a, **k: LocalKnowledgeGraphProvider(db_path=str(outside))
    with host_services_scope(host):
        result = preindex_project_graph(tmp_path, manifest)
    assert result["design_nodes"] > 0 and result["source_nodes"] > 0
    assert not outside.exists()
    assert list((tmp_path / ".architectcoder").rglob("knowledge_graph.db"))


@pytest.mark.parametrize("field", ["entry_file", "source_dir"])
def test_preindex_rejects_paths_outside_fixture_before_loading_provider(tmp_path, field):
    manifest = fixture_project(tmp_path).model_copy(update={field: "../outside"})
    with pytest.raises(ValueError, match="escapes workspace"):
        preindex_project_graph(tmp_path, manifest)


def test_graph_and_shared_tokenizer_can_load_without_memory_plugin():
    code = '''
import importlib.abc, sys
class NoMemory(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "extensions.memory" or fullname.startswith("extensions.memory."):
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, NoMemory())
from shared.tokenization import tokenize_for_fts
from extensions.knowledge_graph.database import _build_fts_query
from extensions.knowledge_graph.builder import GraphBuilder
assert tokenize_for_fts("用户 Service")
assert _build_fts_query("用户 Service")
assert not any(name.startswith("extensions.memory") for name in sys.modules)
'''
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "backend"), str(ROOT)))}
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_plugins_do_not_reintroduce_host_aggregate_or_cross_plugin_implementations():
    for path in (ROOT / "extensions").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module == "app.agent_base.host_api.services" and
                    any(alias.name == "get_host_services" for alias in node.names)), path
                if path.parent.name == "knowledge_graph":
                    assert not (node.module or "").startswith("extensions.memory"), path
                if path.name == "graph_fixture.py":
                    assert not (node.module or "").startswith("extensions.knowledge_graph") or node.module.endswith("plugin_api"), path


def test_legacy_tokenizer_exports_share_one_implementation():
    shared = importlib.import_module("shared.tokenization")
    legacy = importlib.import_module("extensions.memory.tokenizer")
    for name in legacy.__all__:
        assert getattr(legacy, name) is getattr(shared, name)
