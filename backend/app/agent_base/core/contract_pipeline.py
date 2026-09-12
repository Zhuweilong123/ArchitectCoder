"""Single-pass orchestration for contract facts and graph projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import (
    ArtifactFacts,
    ContractProvider,
    ContractSnapshot,
    load_contracts,
)
from .knowledge_graph import KnowledgeGraphProvider, load_knowledge_graph


@dataclass(frozen=True)
class ContractAssembly:
    """Results and lifecycle status from one facts-first contract pass."""

    snapshot: ContractSnapshot
    facts: ArtifactFacts | None = None
    graph_status: str = "not_requested"
    graph_result: Any = None
    graph_error: str = ""


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
    contracts = contract_provider or load_contracts(settings=settings)
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

    graph = knowledge_graph_provider or load_knowledge_graph(settings=settings)
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


__all__ = ["ContractAssembly", "assemble_contract"]
