"""Stable provider boundary for optional knowledge-graph capabilities."""

from __future__ import annotations

from typing import Any, Protocol


class KnowledgeGraphProvider(Protocol):
    """Build and query a project knowledge graph.

    The structural operations are intentionally part of the same port as
    indexing and diagram search.  This keeps graph tools independent from a
    particular storage engine such as SQLite.
    """

    def rebuild_project(
        self, project: Any, project_id: str, filepath: str = "",
    ) -> Any: ...

    def search_diagrams(
        self, project_id: str, queries: list[str], top_k: int = 6,
    ) -> dict[str, set[str]]: ...

    def map_project(self, project_id: str, top_classes: int = 15) -> dict: ...

    def locate(
        self,
        project_id: str,
        pattern: str,
        node_types: list[str] | None = None,
        source: str | None = None,
        top_k: int = 10,
    ) -> dict: ...

    def expand(
        self,
        project_id: str,
        node_ids: list[str],
        direction: str = "outgoing",
        edge_types: list[str] | None = None,
        max_depth: int = 2,
        max_nodes: int = 50,
    ) -> dict: ...

    def impact(
        self,
        project_id: str,
        node_id: str,
        max_depth: int = 2,
        max_nodes: int = 50,
    ) -> dict: ...

    def diff(
        self,
        project_id: str,
        source_dir: str | None = None,
        force_rebuild: bool = False,
        max_items: int = 30,
    ) -> dict: ...


class NoOpKnowledgeGraphProvider:
    """Explicit no-op provider for deployments without graph indexing."""

    def rebuild_project(self, project: Any, project_id: str, filepath: str = "") -> None:
        return None

    def search_diagrams(
        self, project_id: str, queries: list[str], top_k: int = 6,
    ) -> dict[str, set[str]]:
        return {}

    @staticmethod
    def _disabled() -> dict:
        return {"error": "knowledge graph provider is disabled"}

    def map_project(self, project_id: str, top_classes: int = 15) -> dict:
        return self._disabled()

    def locate(self, project_id: str, pattern: str, node_types=None,
               source=None, top_k: int = 10) -> dict:
        return self._disabled()

    def expand(self, project_id: str, node_ids: list[str], direction: str = "outgoing",
               edge_types=None, max_depth: int = 2, max_nodes: int = 50) -> dict:
        return self._disabled()

    def impact(self, project_id: str, node_id: str, max_depth: int = 2,
               max_nodes: int = 50) -> dict:
        return self._disabled()

    def diff(self, project_id: str, source_dir: str | None = None,
             force_rebuild: bool = False, max_items: int = 30) -> dict:
        return self._disabled()


def load_knowledge_graph(*, settings=None, **kwargs) -> KnowledgeGraphProvider:
    """Load the configured graph provider through the central manager."""
    from .plugins import get_plugin_manager

    instance = get_plugin_manager().load(
        "knowledge_graph",
        settings=settings,
        kwargs=kwargs,
    )
    return instance if instance is not None else NoOpKnowledgeGraphProvider()


_default_provider: KnowledgeGraphProvider | None = None


def get_knowledge_graph(*, settings=None, **kwargs) -> KnowledgeGraphProvider:
    """Return the process default provider for read-side integrations."""
    global _default_provider
    if _default_provider is None:
        _default_provider = load_knowledge_graph(settings=settings, **kwargs)
    return _default_provider


__all__ = [
    "KnowledgeGraphProvider",
    "NoOpKnowledgeGraphProvider",
    "get_knowledge_graph",
    "load_knowledge_graph",
]
