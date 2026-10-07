"""Default local knowledge-graph provider backed by SQLite."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from app.agent_base.host_api.environment import project_storage

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
        self.db_path = self._database_path(
            settings, db_path=db_path,
            project_file=str(kwargs.get("project_file") or ""),
            workspace_root=str(kwargs.get("workspace_root") or ""),
        )

    @staticmethod
    def _database_path(settings=None, *, db_path=None, project_file="", workspace_root="") -> str:
        if settings is None:
            from app.agent_base.host_api.environment import configuration as get_settings

            settings = get_settings()
        configured = str(db_path or getattr(settings, "agent_knowledge_graph_db_path", "") or "").strip()
        if configured:
            return str(Path(configured).resolve())
        storage = project_storage(project_file, workspace_root=workspace_root)
        return str((storage.state_dir / "knowledge_graph.db")) if storage else ""

    def _path_for(self, *, project_file: str = "", workspace_root: str = "") -> str:
        if self.db_path:
            return self.db_path
        return self._database_path(
            self.settings, project_file=project_file, workspace_root=workspace_root,
        )

    def rebuild_project(self, project: Any, project_id: str, filepath: str = "") -> Any:
        path = self._path_for(project_file=filepath)
        if not path:
            raise ValueError("project path is required for knowledge graph indexing")
        builder = GraphBuilder(db_path=path)
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

        if not self.db_path:
            return {"error": "project path is required for knowledge graph queries"}
        context = _LocalKnowledgeGraphContext(self.db_path)
        try:
            return callback(KGService(context))
        finally:
            context.close()

    def map_project(self, project_id: str, top_classes: int = 15) -> dict:
        return self._run_service(
            lambda service: service.map_project(project_id, top_classes),
        )

    def contract_facts(self, project_id: str, max_items: int = 5000) -> dict:
        if not os.path.isfile(self.db_path):
            return {
                "available": False,
                "project_id": project_id,
                "nodes": [],
                "edges": [],
                "error": "knowledge graph index does not exist",
            }
        return self._run_service(
            lambda service: service.contract_facts(project_id, max_items),
        )

    def index_facts(self, facts: Any) -> Any:
        """Project shared ``ArtifactFacts`` into the local graph when requested."""
        metadata = getattr(facts, "metadata", {}) or {}
        path = self._path_for(
            project_file=str(metadata.get("project_file") or ""),
            workspace_root=str(metadata.get("workspace_root") or ""),
        )
        if not path:
            raise ValueError("project path is required for knowledge graph indexing")
        builder = GraphBuilder(db_path=path)
        try:
            return builder.index_facts(facts)
        finally:
            builder.close()

    def sync_facts(self, facts: Any) -> Any:
        """Synchronize the local graph from changed artifact facts."""
        metadata = getattr(facts, "metadata", {}) or {}
        path = self._path_for(
            project_file=str(metadata.get("project_file") or ""),
            workspace_root=str(metadata.get("workspace_root") or ""),
        )
        if not path:
            raise ValueError("project path is required for knowledge graph indexing")
        builder = GraphBuilder(db_path=path)
        try:
            return builder.sync_facts(facts)
        finally:
            builder.close()

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
        from app.agent_base.host_api.services import get_host_services

        scoped = self if self.db_path or not project_file else LocalKnowledgeGraphProvider(
            settings=self.settings, project_file=project_file,
        )
        return create_kg_v2_tools(
            project_file=project_file,
            source_dir=source_dir,
            include_compare=include_compare,
            provider=get_host_services().schedule_provider(scoped, "knowledge_graph"),
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
