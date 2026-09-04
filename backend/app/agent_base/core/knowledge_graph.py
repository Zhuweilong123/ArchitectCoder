"""Stable provider boundary for optional knowledge-graph capabilities."""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


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



class KnowledgeGraphToolFactory(Protocol):
    """Optional Agent-facing tool capability supplied by a KG provider."""

    def create_tools(
        self,
        *,
        project_file: str = "",
        source_dir: str = "",
        include_compare: bool = False,
    ) -> list[Any]: ...


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

    def create_tools(
        self,
        *,
        project_file: str = "",
        source_dir: str = "",
        include_compare: bool = False,
    ) -> list[Any]:
        return []


def load_knowledge_graph(*, settings=None, **kwargs) -> KnowledgeGraphProvider:
    """Load the configured graph provider through the central manager."""
    from .plugins import get_plugin_manager

    instance = get_plugin_manager().load(
        "knowledge_graph",
        settings=settings,
        kwargs=kwargs,
    )
    return instance if instance is not None else NoOpKnowledgeGraphProvider()


def load_knowledge_graph_tools(
    *,
    settings=None,
    project_file: str = "",
    source_dir: str = "",
    include_compare: bool = False,
) -> list[Any]:
    """Load Agent-facing graph tools through the enabled KG provider.

    Tool exposure intentionally follows the existing knowledge-graph plugin
    switch. Providers that only support graph indexing/querying may omit the
    optional ``create_tools`` capability and expose no Agent tools.
    """
    provider = load_knowledge_graph(settings=settings)
    create_tools = getattr(provider, "create_tools", None)
    if not callable(create_tools):
        return []
    try:
        tools = create_tools(
            project_file=project_file,
            source_dir=source_dir,
            include_compare=include_compare,
        )
        return list(tools or ())
    except Exception:
        logger.warning("[KnowledgeGraph] tool creation failed; exposing no KG tools", exc_info=True)
        return []


_default_provider: KnowledgeGraphProvider | None = None


def get_knowledge_graph(*, settings=None, **kwargs) -> KnowledgeGraphProvider:
    """Return the process default provider for read-side integrations."""
    global _default_provider
    if _default_provider is None:
        _default_provider = load_knowledge_graph(settings=settings, **kwargs)
    return _default_provider


__all__ = [
    "KnowledgeGraphProvider",
    "KnowledgeGraphToolFactory",
    "NoOpKnowledgeGraphProvider",
    "get_knowledge_graph",
    "load_knowledge_graph",
    "load_knowledge_graph_tools",
]
