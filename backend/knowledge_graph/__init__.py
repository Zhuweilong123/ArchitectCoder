"""
Knowledge Graph — UML Designer 项目知识图谱系统.

为 AI 助手提供结构化的项目理解能力:
  - 从 UML 设计文件 (.uml / .umlproj) 自动构建设计层图谱
  - 从 Python 源码 (AST) 按需构建代码层图谱
  - 通过 FTS5 全文检索 + 图遍历查询项目结构

Usage:
    from knowledge_graph import GraphBuilder, GraphRetriever, KnowledgeGraphDB

    # 构建
    builder = GraphBuilder(db_path="./data/knowledge_graph.db")
    stats = builder.build_from_project(project, "my_project")

    # 检索
    retriever = GraphRetriever(db_path="./data/knowledge_graph.db")
    results = await retriever.query("my_project", "User login")
"""

__version__ = "1.0.0"

from .models import (
    # Core
    GraphNode,
    GraphEdge,
    GraphConfig,
    # Enums
    NodeType,
    EdgeType,
    DiffCategory,
    UML_RELATION_TO_EDGE,
    # Results
    NodeResult,
    PathResult,
    DiffItem,
    DiffSummary,
    DiffResult,
    BuildStats,
    # Helpers
    _utc_now,
)

from .database import KnowledgeGraphDB
from .builder import GraphBuilder
from .retriever import GraphRetriever

__all__ = [
    # Core
    "GraphNode",
    "GraphEdge",
    "GraphConfig",
    # Enums
    "NodeType",
    "EdgeType",
    "DiffCategory",
    "UML_RELATION_TO_EDGE",
    # Results
    "NodeResult",
    "PathResult",
    "DiffItem",
    "DiffSummary",
    "DiffResult",
    "BuildStats",
    # Storage / Builder / Retriever
    "KnowledgeGraphDB",
    "GraphBuilder",
    "GraphRetriever",
]
