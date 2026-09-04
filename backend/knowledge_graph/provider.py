"""Default local knowledge-graph provider backed by SQLite."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .builder import GraphBuilder
from .retriever import GraphRetriever


class LocalKnowledgeGraphProvider:
    """Adapter exposing the existing SQLite graph implementation."""

    def __init__(self, settings=None, db_path: str | None = None, **kwargs):
        self.settings = settings
        self.db_path = str(Path(db_path or self._default_db_path(settings)).resolve())

    @staticmethod
    def _default_db_path(settings=None) -> str:
        if settings is None:
            from app.core.config import get_settings

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
