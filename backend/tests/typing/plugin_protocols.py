"""CI checks concrete and disabled providers against their public protocols."""
from app.agent_base.adapters.evals import NoOpEvalProvider, _ResilientEvalProvider
from app.agent_base.adapters.knowledge_graph import NoOpKnowledgeGraphProvider
from app.runtime.workspace import WorkspaceManifest
from extensions.design_contract.plugin_api import ArtifactFactsProvider, ContractManifestPort, ContractProvider
from extensions.design_contract.provider import DesignContractProvider
from extensions.evals.plugin_api import EvalProvider
from extensions.evals.provider import LocalEvalProvider
from extensions.knowledge_graph.plugin_api import GraphPreindexProvider, KnowledgeGraphProvider
from extensions.knowledge_graph.provider import LocalKnowledgeGraphProvider
from extensions.memory.plugin_api import MemoryPort
from extensions.memory.provider import SQLiteMemoryProvider


def check_provider_contracts(
    memory: SQLiteMemoryProvider,
    contracts: DesignContractProvider,
    evals: LocalEvalProvider,
    graph: LocalKnowledgeGraphProvider,
    disabled_evals: NoOpEvalProvider,
    disabled_graph: NoOpKnowledgeGraphProvider,
    workspace: WorkspaceManifest,
) -> None:
    memory_port: MemoryPort = memory
    contract_port: ContractProvider = contracts
    facts_port: ArtifactFactsProvider = contracts
    eval_port: EvalProvider = evals
    graph_port: KnowledgeGraphProvider = graph
    preindex_port: GraphPreindexProvider = graph
    disabled_eval_port: EvalProvider = disabled_evals
    disabled_graph_port: KnowledgeGraphProvider = disabled_graph
    manifest_port: ContractManifestPort = workspace

    wrapped_eval_port: EvalProvider = _ResilientEvalProvider(evals)
