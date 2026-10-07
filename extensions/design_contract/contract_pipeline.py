from __future__ import annotations

from app.agent_base.host_api.services import get_host_services
from typing import Any
from .plugin_api import ContractProvider, ContractSnapshot
from extensions.knowledge_graph.plugin_api import KnowledgeGraphProvider

from .contract_result import ContractAssembly

def assemble_contract(
    manifest: Any,
    *,
    project_id: str = "",
    scope: str = "project",
    settings=None,
    contract_provider: ContractProvider | None = None,
    knowledge_graph_provider: KnowledgeGraphProvider | None = None,
    index_graph: bool = True,
) -> ContractAssembly:
    """Collect facts once, project the contract, and optionally index the graph.

    Providers predating ``collect_facts`` remain compatible: they use their
    existing ``collect`` method and simply skip the single-pass graph index.
    Graph failures are reported in the assembly result and never invalidate
    the contract snapshot.
    """
    contracts = contract_provider or get_host_services().resolve_provider("design_contract", settings=settings)
    if contracts is None:
        return ContractAssembly(snapshot=ContractSnapshot(project_id=project_id, scope=scope,
            status="blocked", metadata={"reason": "contract provider is unavailable"}),
            graph_status="not_requested")
    collect_facts = getattr(contracts, "collect_facts", None)
    if not callable(collect_facts):
        return ContractAssembly(
            snapshot=contracts.collect(manifest, project_id=project_id, scope=scope),
            graph_status="legacy_provider",
        )

    facts = collect_facts(manifest, project_id=project_id, scope=scope)
    project = getattr(contracts, "snapshot_from_facts", None)
    snapshot = (
        project(facts)
        if callable(project)
        else ContractSnapshot(
            project_id=facts.project_id,
            scope=facts.scope,
            status=facts.status,
            entities=facts.entities,
            mappings=facts.mappings,
            metadata={
                **facts.metadata,
                "errors": list(facts.diagnostics),
            },
        )
    )
    if not index_graph:
        return ContractAssembly(snapshot=snapshot, facts=facts, graph_status="not_requested")

    values = manifest.to_dict() if hasattr(manifest, "to_dict") else dict(manifest or {})
    graph = knowledge_graph_provider or get_host_services().resolve_provider("knowledge_graph",
        settings=settings,
        project_file=str(values.get("project_file") or ""),
        workspace_root=str(values.get("workspace_root") or ""),
    )
    indexer = getattr(graph, "sync_facts", None)
    if not callable(indexer):
        indexer = getattr(graph, "index_facts", None)
    if not callable(indexer):
        return ContractAssembly(snapshot=snapshot, facts=facts, graph_status="unsupported")
    try:
        result = indexer(facts)
    except Exception as exc:  # graph is an optional derived projection
        return ContractAssembly(
            snapshot=snapshot,
            facts=facts,
            graph_status="failed",
            graph_error=str(exc),
        )
    return ContractAssembly(
        snapshot=snapshot,
        facts=facts,
        graph_status="indexed",
        graph_result=result,
    )
