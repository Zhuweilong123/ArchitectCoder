"""Prepare a project-scoped graph for an isolated evaluation fixture."""

from __future__ import annotations

import time
from pathlib import Path

from .models import ProjectManifest


def preindex_project_graph(workspace: Path, manifest: ProjectManifest) -> dict:
    """Index the same design and source snapshot for every comparison arm."""
    from app.models.uml import Project
    from backend.config.project_storage import project_storage
    from extensions.knowledge_graph.builder import GraphBuilder

    project_file = (workspace / manifest.entry_file).resolve()
    source_dir = (workspace / manifest.source_dir).resolve()
    if not project_file.is_relative_to(workspace.resolve()):
        raise ValueError("evaluation project file escapes workspace")
    if not source_dir.is_relative_to(workspace.resolve()):
        raise ValueError("evaluation source directory escapes workspace")

    started = time.monotonic()
    project = Project.model_validate_json(project_file.read_text(encoding="utf-8"))
    storage = project_storage(str(project_file), workspace_root=str(workspace))
    if storage is None:
        raise ValueError("could not create project-owned graph storage")
    builder = GraphBuilder(db_path=str(storage.graph_db))
    try:
        design = builder.rebuild_project(
            project, storage.project_id, filepath=str(project_file),
        )
        source = builder.rebuild_code_layer(storage.project_id, str(source_dir))
    finally:
        builder.close()
    return {
        "project_id": storage.project_id,
        "index_ms": round((time.monotonic() - started) * 1000, 1),
        "design_nodes": design.total_nodes,
        "source_nodes": source.total_nodes,
        "design_edges": design.total_edges,
        "source_edges": source.total_edges,
    }
