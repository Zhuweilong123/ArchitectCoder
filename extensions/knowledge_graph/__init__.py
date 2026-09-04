"""SQLite knowledge-graph extension.

Graph storage, indexing, retrieval, v2 tools and the provider adapter are
kept together here.  The old ``knowledge_graph`` package is a compatibility
facade only.
"""

from .builder import GraphBuilder
from .database import KnowledgeGraphDB
from .models import (
    BuildStats, DiffCategory, DiffItem, DiffResult, DiffSummary, EdgeType,
    GraphConfig, GraphEdge, GraphNode, NodeResult, NodeType, PathResult,
    UML_RELATION_TO_EDGE, _utc_now,
)
from .provider import LocalKnowledgeGraphProvider, create
from .retriever import GraphRetriever

__all__ = [
    "create", "LocalKnowledgeGraphProvider", "GraphNode", "GraphEdge",
    "GraphConfig", "NodeType", "EdgeType", "DiffCategory",
    "UML_RELATION_TO_EDGE", "NodeResult", "PathResult", "DiffItem",
    "DiffSummary", "DiffResult", "BuildStats", "_utc_now",
    "KnowledgeGraphDB", "GraphBuilder", "GraphRetriever",
]
