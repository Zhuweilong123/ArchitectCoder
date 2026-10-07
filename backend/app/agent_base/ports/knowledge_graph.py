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

    def contract_facts(self, project_id: str, max_items: int = 5000) -> dict: ...

    def index_facts(self, facts: Any) -> Any: ...

    def sync_facts(self, facts: Any) -> Any: ...

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
