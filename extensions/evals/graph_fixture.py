"""Prepare a fixture index through the configured knowledge-graph capability."""
from __future__ import annotations

import time
from pathlib import Path
from typing import cast

from app.agent_base.host_api.services import get_environment_services, get_provider_services
from extensions.knowledge_graph.plugin_api import (
    GraphPreindexProvider, GraphPreindexRequest, GraphPreindexResult,
)
from .models import ProjectManifest


def preindex_project_graph(workspace: Path, manifest: ProjectManifest) -> dict:
    """Index the same fixture snapshot for every comparison arm, without selecting storage."""
    from app.models.uml import Project

    workspace = workspace.resolve()
    project_file = (workspace / manifest.entry_file).resolve()
    source_dir = (workspace / manifest.source_dir).resolve()
    if not project_file.is_relative_to(workspace):
        raise ValueError("evaluation project file escapes workspace")
    if not source_dir.is_relative_to(workspace):
        raise ValueError("evaluation source directory escapes workspace")

    started = time.monotonic()
    project = Project.model_validate_json(project_file.read_text(encoding="utf-8"))
    project_id = get_environment_services().project_id(str(project_file), workspace_root=str(workspace))
    provider = get_provider_services().resolve_provider(
        "knowledge_graph", project_file=str(project_file), workspace_root=str(workspace),
    )
    if provider is None or not callable(getattr(provider, "preindex_project", None)):
        raise ValueError("configured knowledge graph provider does not support fixture preindexing")
    result = cast(GraphPreindexProvider, provider).preindex_project(GraphPreindexRequest(
        project=project, project_id=project_id, project_file=str(project_file),
        source_dir=str(source_dir), workspace_root=str(workspace),
    ))
    if not isinstance(result, GraphPreindexResult):
        raise TypeError("preindex_project must return GraphPreindexResult")
    return {
        "project_id": project_id,
        "index_ms": round((time.monotonic() - started) * 1000, 1),
        "design_nodes": result.design_nodes,
        "source_nodes": result.source_nodes,
        "design_edges": result.design_edges,
        "source_edges": result.source_edges,
    }
