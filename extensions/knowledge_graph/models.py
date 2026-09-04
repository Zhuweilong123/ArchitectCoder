"""
Knowledge Graph data models — Node, Edge, Config, enums, result types.

Follows the memory_system/models.py pattern: dataclasses + enums + _utc_now helper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


# ── Time helpers ──────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


# ── Node Type ─────────────────────────────────────────────────

class NodeType(str, Enum):
    """节点类型 — 覆盖 UML 设计层 + 源码层 + 测试层."""
    PROJECT     = "project"       # 顶层项目节点
    DIAGRAM     = "diagram"       # 单张 UML 图 (class / sequence / component)
    CLASS       = "class"         # UML 类 / 接口 / 抽象类 / 枚举
    COMPONENT   = "component"     # 组件图节点
    LIFELINE    = "lifeline"      # 时序图生命线
    SOURCE_FILE = "source_file"   # Python / 其他语言源码文件
    TEST_FILE   = "test_file"     # pytest / 测试文件
    METHOD      = "method"        # 类方法 / 文件级函数
    ATTRIBUTE   = "attribute"     # 类属性 / 字段
    INTERFACE   = "interface"     # 提供 / 需要的接口 (独立节点, 可跨组件链接)
    FRAGMENT    = "fragment"      # 时序图片段 (loop / alt / opt / par 等组合片段)


# ── Edge Type ──────────────────────────────────────────────────

class EdgeType(str, Enum):
    """边类型 — 覆盖结构关系 + UML 语义关系 + 设计–代码关联."""
    # 结构关系
    CONTAINS       = "contains"        # 父子包含 (Diagram → Class, Project → Diagram, Class → Method)
    # UML 语义关系
    INHERITS       = "inherits"        # 继承 (Class → Class)
    COMPOSITION    = "composition"     # 组合 (Class → Class)
    AGGREGATION    = "aggregation"     # 聚合 (Class → Class)
    ASSOCIATION    = "association"     # 关联 (Class → Class)
    REALIZATION    = "realization"     # 实现接口 (Class → Class / Interface)
    DEPENDENCY     = "dependency"      # 依赖 (Class → Class, Component → Component)
    # 设计–代码关联
    IMPLEMENTS     = "implements"      # 代码实现了设计 (SourceFile → CLASS)
    IMPORTS        = "imports"         # 文件导入 (SourceFile → SourceFile)
    TESTS          = "tests"           # 测试覆盖 (TestFile → SourceFile)
    REFERENCES     = "references"      # 泛引用 (SourceFile / Method → anything)
    MESSAGES       = "messages"        # 时序消息 (Lifeline → Lifeline)
    FRAGMENTS      = "fragments"       # 时序图片段 (Diagram → Fragment)


# ── Diff category ──────────────────────────────────────────────

class DiffCategory(str, Enum):
    MISSING_IMPLEMENTATION = "missing_implementation"
    EXTRA_CODE             = "extra_code"
    MISMATCH               = "mismatch"
    NO_COVERAGE            = "no_coverage"


# UmL RelationType → EdgeType 映射

UML_RELATION_TO_EDGE: dict[str, EdgeType] = {
    "inheritance":  EdgeType.INHERITS,
    "composition":  EdgeType.COMPOSITION,
    "aggregation":  EdgeType.AGGREGATION,
    "association":  EdgeType.ASSOCIATION,
    "realization":  EdgeType.REALIZATION,
    "dependency":   EdgeType.DEPENDENCY,
}


# ── GraphNode ──────────────────────────────────────────────────

@dataclass
class GraphNode:
    """知识图谱中的一个节点.

    Attributes:
        id:          唯一标识 (uuid hex)
        node_type:   节点类型
        name:        人可读名称 (类名 / 组件名 / 文件名 / 方法名)
        project_id:  所属项目标识
        source:      来源 ("design" | "code" | "test")
        properties:  结构化属性 JSON (字段因 node_type 而异, 见约定表)
        content_text: FTS 索引用合成文本 (name + 关键属性拼接)
        embedding:   向量 BLOB (预留, 后续接入 EmbeddingService 后使用)
        created_at:  创建时间 (ISO 格式)
        updated_at:  最后更新时间 (ISO 格式)
    """
    id: str
    node_type: NodeType
    name: str
    project_id: str
    source: str = "design"                # "design" | "code" | "test"
    properties: dict = field(default_factory=dict)
    content_text: str = ""                # FTS 索引用
    embedding: Optional[bytes] = None     # 向量预留
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    # ── computed ──

    @property
    def label(self) -> str:
        """简短的人可读标签."""
        return f"[{self.node_type.value}] {self.name}"

    @property
    def natural_key(self) -> tuple:
        """自然键: (project_id, node_type, name, source)."""
        return (self.project_id, self.node_type.value, self.name, self.source)

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典 (排除 embedding)."""
        d = {
            "id": self.id,
            "node_type": self.node_type.value,
            "name": self.name,
            "project_id": self.project_id,
            "source": self.source,
            "properties": self.properties,
            "content_text": self.content_text,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.embedding is not None:
            d["embedding_model"] = ""  # 预留, 不导出 bytes
        return d

    def to_summary(self) -> dict[str, Any]:
        """Agent 友好摘要 (不包含 content_text / embedding)."""
        return {
            "id": self.id,
            "node_type": self.node_type.value,
            "name": self.name,
            "source": self.source,
            "properties": self.properties,
        }

    def __repr__(self) -> str:
        return (
            f"GraphNode(id={self.id[:8]}..., type={self.node_type.value}, "
            f"name={self.name!r}, project={self.project_id!r}, source={self.source!r})"
        )


# ── GraphEdge ──────────────────────────────────────────────────

@dataclass
class GraphEdge:
    """知识图谱中的一条有向边.

    Attributes:
        id:         唯一标识
        source_id:  源节点 GraphNode.id
        target_id:  目标节点 GraphNode.id
        edge_type:  边类型
        properties: 结构化属性 (e.g. multiplicity, role_name, message_label)
        weight:     边权重 (1.0 默认, 用于图算法)
        created_at: 创建时间
    """
    id: str = field(default_factory=lambda: uuid4().hex)
    source_id: str = ""
    target_id: str = ""
    edge_type: EdgeType = EdgeType.CONTAINS
    properties: dict = field(default_factory=dict)
    weight: float = 1.0
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value,
            "properties": self.properties,
            "weight": self.weight,
            "created_at": self.created_at,
        }

    def __repr__(self) -> str:
        return (
            f"GraphEdge(id={self.id[:8]}..., "
            f"{self.source_id[:8]}... -[{self.edge_type.value}]-> {self.target_id[:8]}...)"
        )


# ── Query Results ──────────────────────────────────────────────

@dataclass
class NodeResult:
    """检索 / 展开 / 追踪返回的节点 (附带上下文)."""
    node: GraphNode
    score: float = 0.0              # BM25 分 (query 时有效, 取反后为正相关)
    depth: int = 0                  # 距起点的距离 (expand / trace 时有效)
    path: list[str] = field(default_factory=list)  # trace 时记录经过的节点 ID


@dataclass
class PathResult:
    """trace 操作返回的单条路径."""
    node_ids: list[str] = field(default_factory=list)      # 路径上的节点 ID 序列
    edges: list[dict] = field(default_factory=list)         # [{edge_type, source_id, target_id, properties}]
    length: int = 0
    nodes: list[GraphNode] = field(default_factory=list)    # 预加载的节点对象


@dataclass
class DiffItem:
    """diff 操作返回的单条差异."""
    severity: str = "info"          # "error" | "warning" | "info"
    category: str = ""              # DiffCategory 值
    message: str = ""
    design_node_id: str = ""
    code_node_id: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class DiffSummary:
    """diff 操作的汇总统计."""
    total_design_classes: int = 0
    total_code_classes: int = 0
    missing_implementations: int = 0
    extra_code: int = 0
    mismatches: int = 0
    no_coverage: int = 0

    @property
    def coverage_rate(self) -> float:
        implemented = self.total_design_classes - self.missing_implementations
        if self.total_design_classes == 0:
            return 1.0
        return round(implemented / self.total_design_classes, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_design_classes": self.total_design_classes,
            "total_code_classes": self.total_code_classes,
            "missing_implementations": self.missing_implementations,
            "extra_code": self.extra_code,
            "mismatches": self.mismatches,
            "no_coverage": self.no_coverage,
            "coverage_rate": self.coverage_rate,
        }


@dataclass
class DiffResult:
    """diff 操作的完整结果."""
    summary: DiffSummary = field(default_factory=DiffSummary)
    items: list[DiffItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "details": [
                {
                    "severity": item.severity,
                    "category": item.category,
                    "message": item.message,
                    "design_node_id": item.design_node_id,
                    "code_node_id": item.code_node_id,
                    "detail": item.detail,
                }
                for item in self.items
            ],
        }


# ── Config ─────────────────────────────────────────────────────

@dataclass
class GraphConfig:
    """知识图谱全局配置."""
    db_path: str = "./data/knowledge_graph.db"
    fts_top_k: int = 20
    max_expand_depth: int = 2         # expand() 默认最大深度
    trace_max_depth: int = 10         # trace() 递归 CTE 最大深度
    embedding_dim: int = 384          # 预留


# ── Build Stats ────────────────────────────────────────────────

@dataclass
class BuildStats:
    """GraphBuilder 构建结果统计."""
    nodes_added: int = 0
    nodes_updated: int = 0
    nodes_removed: int = 0
    edges_added: int = 0
    edges_removed: int = 0
    source: str = ""                  # "declarative" | "exploratory"
    elapsed_ms: float = 0.0

    @property
    def total_nodes(self) -> int:
        return self.nodes_added + self.nodes_updated

    @property
    def total_edges(self) -> int:
        return self.edges_added

    def __repr__(self) -> str:
        return (
            f"BuildStats(nodes: +{self.nodes_added} ~{self.nodes_updated} "
            f"-{self.nodes_removed}, edges: +{self.edges_added} "
            f"-{self.edges_removed}, source={self.source!r}, "
            f"{self.elapsed_ms:.1f}ms)"
        )


# ── Node property conventions ──────────────────────────────────
# (文档性约定, 不会在运行时校验)

"""
| NodeType     | properties 关键字段                                     |
|--------------|--------------------------------------------------------|
| PROJECT      | {version, diagram_count}                               |
| DIAGRAM      | {diagram_type: "class"/"sequence"/"component",         |
|              |  component_id, grid_size, ...}                         |
| CLASS        | {stereotype, visibility, provided_interfaces[],        |
|              |  required_interfaces[], is_abstract, methods[],        |
|              |  attributes[]}                                         |
| COMPONENT    | {parent_id, provided_interfaces[],                     |
|              |  required_interfaces[], width, height}                |
| LIFELINE     | {class_ref}                                            |
| SOURCE_FILE  | {path, language, class_names[], import_names[]}        |
| TEST_FILE    | {path, language, covers[]}                             |
| METHOD       | {return_type, params, visibility, is_static,           |
|              |  is_abstract}                                          |
| ATTRIBUTE    | {type, visibility, is_static, default_value}           |
| INTERFACE    | {direction: "provided"/"required", component_id}       |
| FRAGMENT     | {fragment_type: "loop"/"alt"/"opt"/..., x, width,       |
|              |  y_start, y_end}                                       |
"""
