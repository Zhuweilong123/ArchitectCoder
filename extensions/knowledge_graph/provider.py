"""Default local knowledge-graph provider backed by SQLite."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from .builder import GraphBuilder
from .database import KnowledgeGraphDB
from .retriever import GraphRetriever


class _LocalKnowledgeGraphContext:
    """Per-operation SQLite resources used by the local provider."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = None
        self._retriever = None

    @property
    def db(self) -> KnowledgeGraphDB:
        if self._db is None:
            self._db = KnowledgeGraphDB(self.db_path)
        return self._db

    @property
    def retriever(self) -> GraphRetriever:
        if self._retriever is None:
            self._retriever = GraphRetriever(self.db_path)
        return self._retriever

    def close(self) -> None:
        for resource in (self._retriever, self._db):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        self._retriever = None
        self._db = None


class LocalKnowledgeGraphProvider:
    """Adapter exposing the existing SQLite graph implementation."""

    def __init__(self, settings=None, db_path: str | None = None, **kwargs):
        self.settings = settings
        self.db_path = str(Path(db_path or self._default_db_path(settings)).resolve())

    @staticmethod
    def _default_db_path(settings=None) -> str:
        if settings is None:
            from backend.config import get_settings

            settings = get_settings()
        configured = str(getattr(settings, "agent_knowledge_graph_db_path", "") or "").strip()
        if configured:
            return configured
        return os.path.join(
            os.path.dirname(settings.uml_dir),
            "data",
            "knowledge_graph.db",
        )

    def rebuild_project(self, project: Any, project_id: str, filepath: str = "") -> Any:
        builder = GraphBuilder(db_path=self.db_path)
        try:
            return builder.rebuild_project(project, project_id, filepath=filepath)
        finally:
            builder.close()

    def search_diagrams(
        self, project_id: str, queries: list[str], top_k: int = 6,
    ) -> dict[str, set[str]]:
        if not project_id or not queries or not os.path.isfile(self.db_path):
            return {}
        retriever = GraphRetriever(db_path=self.db_path)
        hits: dict[str, set[str]] = {}
        try:
            for query in queries:
                for result in retriever.db.search_bm25(
                    project_id=project_id,
                    query=query,
                    top_k=top_k,
                ):
                    self._collect_diagram_hits(retriever, result, hits)
        finally:
            retriever.close()
        return hits

    def _run_service(self, callback: Callable[[Any], Any]) -> Any:
        """Run the existing local graph service behind this provider."""
        # Imported lazily to keep the provider usable without loading Agent
        # tool classes during application startup.
        from .tools import KGService

        context = _LocalKnowledgeGraphContext(self.db_path)
        try:
            return callback(KGService(context))
        finally:
            context.close()

    def map_project(self, project_id: str, top_classes: int = 15) -> dict:
        return self._run_service(
            lambda service: service.map_project(project_id, top_classes),
        )

    def locate(self, project_id: str, pattern: str, node_types=None,
               source=None, top_k: int = 10) -> dict:
        return self._run_service(
            lambda service: service.locate(
                project_id, pattern, node_types, source, top_k,
            ),
        )

    def expand(self, project_id: str, node_ids: list[str], direction: str = "outgoing",
               edge_types=None, max_depth: int = 2, max_nodes: int = 50) -> dict:
        return self._run_service(
            lambda service: service.expand(
                project_id, node_ids, direction, edge_types, max_depth, max_nodes,
            ),
        )

    def impact(self, project_id: str, node_id: str, max_depth: int = 2,
               max_nodes: int = 50) -> dict:
        return self._run_service(
            lambda service: service.impact(project_id, node_id, max_depth, max_nodes),
        )

    def diff(self, project_id: str, source_dir: str | None = None,
             force_rebuild: bool = False, max_items: int = 30) -> dict:
        return self._run_service(
            lambda service: service.diff(
                project_id, source_dir, force_rebuild, max_items,
            ),
        )

    def create_tools(
        self,
        *,
        project_file: str = "",
        source_dir: str = "",
        include_compare: bool = False,
    ) -> list[Any]:
        """Create Agent tools while keeping the concrete factory in this extension."""
        from .tools import create_kg_v2_tools

        return create_kg_v2_tools(
            project_file=project_file,
            source_dir=source_dir,
            include_compare=include_compare,
            provider=self,
        )

    @staticmethod
    def _collect_diagram_hits(retriever, result, output: dict[str, set[str]]) -> None:
        node = result.node
        node_type = node.node_type.value
        if node_type == "diagram":
            output.setdefault(node.name, set()).add(f"{node.name}({node_type})")

        pending = {(node.id, node.name, node_type)}
        seen: set[str] = set()
        while pending:
            current_id, current_name, current_type = pending.pop()
            if current_id in seen:
                continue
            seen.add(current_id)
            incoming = retriever.db.conn.execute(
                "SELECT e.source_id, s.name, s.node_type "
                "FROM kg_edges e JOIN kg_nodes s ON e.source_id = s.id "
                "WHERE e.target_id = ?",
                (current_id,),
            ).fetchall()
            for source_id, source_name, source_type in incoming:
                if source_type == "diagram":
                    output.setdefault(source_name, set()).add(
                        f"{current_name}({current_type})"
                    )
                elif source_type != "project":
                    pending.add((source_id, source_name, source_type))


def create(*, settings=None, **kwargs):
    return LocalKnowledgeGraphProvider(settings=settings, **kwargs)


__all__ = ["LocalKnowledgeGraphProvider", "create"]
