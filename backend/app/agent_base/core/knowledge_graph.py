"""Stable provider boundary for optional knowledge-graph capabilities."""

from __future__ import annotations

import importlib
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


def _load_factory(provider: str):
    module_name, separator, attribute = provider.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("knowledge graph provider must use 'module:factory' syntax")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise TypeError(f"knowledge graph provider is not callable: {provider}")
    return factory


def load_knowledge_graph(*, settings=None, **kwargs) -> KnowledgeGraphProvider:
    """Load the configured graph provider without a concrete KG import."""
    if settings is None:
        try:
            from app.core.config import get_settings

            settings = get_settings()
        except Exception:
            settings = None

    if settings is not None and not getattr(settings, "agent_knowledge_graph_enabled", True):
        return NoOpKnowledgeGraphProvider()

    provider = str(
        getattr(settings, "agent_knowledge_graph_provider", "knowledge_graph.provider:create")
        or ""
    ).strip()
    if not provider or provider.lower() in {"none", "noop", "disabled"}:
        return NoOpKnowledgeGraphProvider()

    try:
        factory = _load_factory(provider)
        instance = factory(settings=settings, **kwargs)
        required = (
            "rebuild_project",
            "search_diagrams",
            "map_project",
            "locate",
            "expand",
            "impact",
            "diff",
        )
        if not all(callable(getattr(instance, name, None)) for name in required):
            raise TypeError(
                "knowledge graph provider must expose rebuild_project and search_diagrams"
            )
        return instance
    except Exception:
        logger.warning("[KG] provider unavailable; using no-op", exc_info=True)
        return NoOpKnowledgeGraphProvider()


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
