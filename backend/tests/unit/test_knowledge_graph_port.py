"""Tests for the provider-neutral knowledge-graph boundary."""

from types import SimpleNamespace

from app.agent_base.core.knowledge_graph import (
    NoOpKnowledgeGraphProvider,
    load_knowledge_graph,
)
from app.models.uml import Project, UmlClass, UmlDiagram
from knowledge_graph.provider import LocalKnowledgeGraphProvider


class _Provider:
    def rebuild_project(self, project, project_id, filepath=""):
        return {"project_id": project_id, "filepath": filepath}

    def search_diagrams(self, project_id, queries, top_k=6):
        return {"overview": {queries[0]}}


def test_disabled_knowledge_graph_uses_noop_provider():
    provider = load_knowledge_graph(
        settings=SimpleNamespace(agent_knowledge_graph_enabled=False),
    )

    assert isinstance(provider, NoOpKnowledgeGraphProvider)
    assert provider.rebuild_project({}, "project-1") is None
    assert provider.search_diagrams("project-1", ["login"]) == {}


def test_knowledge_graph_provider_factory_is_pluggable(monkeypatch):
    import sys

    module_name = "test_knowledge_graph_provider_plugin"
    monkeypatch.setitem(
        sys.modules,
        module_name,
        SimpleNamespace(create=lambda **kwargs: _Provider()),
    )
    settings = SimpleNamespace(
        agent_knowledge_graph_enabled=True,
        agent_knowledge_graph_provider=f"{module_name}:create",
    )

    provider = load_knowledge_graph(settings=settings)
    assert provider.rebuild_project({}, "project-1", "project.umlproj")["project_id"] == "project-1"
    assert provider.search_diagrams("project-1", ["login"]) == {
        "overview": {"login"},
    }


def test_local_provider_adapts_build_and_diagram_search(tmp_path):
    provider = LocalKnowledgeGraphProvider(db_path=str(tmp_path / "kg.db"))
    project = Project(
        name="demo",
        diagrams=[
            UmlDiagram(
                name="Domain",
                diagram_type="class",
                classes=[UmlClass(id="user", name="User")],
            ),
        ],
    )

    stats = provider.rebuild_project(project, "demo", filepath="demo.umlproj")
    matches = provider.search_diagrams("demo", ["User"])

    assert stats.nodes_added > 0
    assert "Domain" in matches
    assert any("User" in reason for reason in matches["Domain"])


def test_uml_summary_retrieval_uses_the_provider_boundary(monkeypatch):
    import app.services.uml_common as uml_common

    class _SearchProvider:
        def search_diagrams(self, project_id, queries, top_k=6):
            assert project_id == "demo"
            assert queries[0] == "User login"
            assert top_k == 6
            return {"Domain": {"User(class)"}}

    monkeypatch.setattr(uml_common, "get_knowledge_graph", lambda: _SearchProvider())
    hits = {}

    uml_common._fetch_kg_hits("demo.umlproj", "User login", hits)

    assert hits == {"Domain": {"User(class)"}}
