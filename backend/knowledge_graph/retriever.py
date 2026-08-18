"""
GraphRetriever — 知识图谱查询接口.

提供 4 种检索操作:
  1. query()  — BM25 全文检索 + 类型过滤
  2. expand() — n-hop 邻域 BFS 展开
  3. trace()  — 依赖路径追踪 (SQLite recursive CTE)
  4. diff()   — 设计 vs 代码差异分析

Usage:
    retriever = GraphRetriever(db_path="./data/knowledge_graph.db")
    results = await retriever.query("proj", "User login", node_types=["class"])
    neighbors = await retriever.expand(["class_xxx"], depth=2)
    paths = await retriever.trace("class_a", "class_b")
    diff = await retriever.diff("proj")
    retriever.close()
"""

from __future__ import annotations

import glob
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from .database import KnowledgeGraphDB
from .builder import GraphBuilder
from .models import (
    GraphNode, NodeType, EdgeType,
    NodeResult, PathResult, DiffItem, DiffSummary, DiffResult,
    DiffCategory, GraphConfig,
)

logger = logging.getLogger(__name__)


def _parse_iso_dt(s: str) -> datetime:
    """解析 ISO 时间串 (如 updated_at) 为带 UTC 的 datetime; 失败时返回 epoch."""
    if not s:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _mtime_dt(path: str) -> datetime:
    """文件 mtime → 带 UTC 的 datetime (与 _parse_iso_dt 可比)."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    except OSError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


# ═══════════════════════════════════════════════════════════════
# GraphRetriever
# ═══════════════════════════════════════════════════════════════

class GraphRetriever:
    """知识图谱检索引擎.

    封装 KnowledgeGraphDB 的底层 SQL, 提供面向 Agent 友好的查询接口.
    """

    __slots__ = ("db_path", "config", "_db")

    def __init__(self, db_path: str = "./data/knowledge_graph.db",
                 config: Optional[GraphConfig] = None):
        self.db_path = db_path
        self.config = config or GraphConfig(db_path=db_path)
        self._db: Optional[KnowledgeGraphDB] = None

    @property
    def db(self) -> KnowledgeGraphDB:
        if self._db is None:
            self._db = KnowledgeGraphDB(self.db_path)
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # ── query ──────────────────────────────────────────────

    async def query(
        self,
        project_id: str,
        pattern: str,
        node_types: Optional[list[str]] = None,
        source: Optional[str] = None,
        top_k: int = 20,
    ) -> list[NodeResult]:
        """BM25 全文检索 + 类型过滤.

        Args:
            project_id: 项目标识
            pattern:    查询文本 (中文/英文混合, Agent 自然语言)
            node_types: 节点类型过滤, e.g. ["class", "method"]. None = 全部.
            source:     来源过滤 ("design"|"code"|"test"). None = 全部.
            top_k:      最大返回数

        Returns:
            NodeResult 列表, 按 BM25 得分降序.
        """
        results: list[NodeResult] = []

        # 单类型查询走 DB 层自带的类型过滤
        if node_types and len(node_types) == 1:
            results = self.db.search_bm25(
                project_id, pattern, top_k=top_k,
                node_type=node_types[0], source=source,
            )
        elif node_types:
            # 多类型: 分别检索后合并排序
            all_results: list[NodeResult] = []
            for nt in node_types:
                partial = self.db.search_bm25(
                    project_id, pattern, top_k=top_k,
                    node_type=nt, source=source,
                )
                all_results.extend(partial)
            all_results.sort(key=lambda r: r.score, reverse=True)
            results = all_results[:top_k]
        else:
            results = self.db.search_bm25(
                project_id, pattern, top_k=top_k, source=source,
            )

        logger.info(
            f"[Retriever] query({project_id}, {pattern[:60]}...): "
            f"{len(results)} results"
        )
        return results

    # ── expand ─────────────────────────────────────────────

    async def expand(
        self,
        node_ids: list[str],
        depth: int = 1,
        edge_types: Optional[list[str]] = None,
        direction: str = "outgoing",
        max_nodes: int = 50,
    ) -> list[NodeResult]:
        """n-hop 邻域 BFS 展开.

        Args:
            node_ids:   起点节点 ID 列表
            depth:      展开深度 (1 = 直接邻居, 2 = 邻居的邻居)
            edge_types: 边类型过滤. None = 所有类型.
            direction:  "outgoing" | "incoming" | "both"
            max_nodes:  最大返回节点数 (防止图爆炸)

        Returns:
            NodeResult 列表, 每个节点标记其距离起点的 depth.
        """
        if depth < 1:
            depth = 1
        if depth > self.config.max_expand_depth:
            logger.warning(
                f"[Retriever] expand depth {depth} > max {self.config.max_expand_depth}, "
                f"clamping"
            )
            depth = self.config.max_expand_depth

        visited: set[str] = set(node_ids)
        frontier: set[str] = set(node_ids)
        results: list[NodeResult] = []

        # 先加载起点节点
        start_nodes = self.db.get_nodes_by_ids(node_ids)
        for nid in node_ids:
            if nid in start_nodes:
                results.append(NodeResult(node=start_nodes[nid], depth=0))

        for d in range(1, depth + 1):
            if not frontier or len(results) >= max_nodes:
                break

            neighbors = self.db.get_neighbors(
                list(frontier), edge_types=edge_types, direction=direction,
            )

            new_frontier: set[str] = set()
            for origin_id, neigh_list in neighbors.items():
                for (neigh_id, edge_type_str, _edge_id, _props) in neigh_list:
                    if neigh_id not in visited:
                        visited.add(neigh_id)
                        new_frontier.add(neigh_id)

            # 批量加载新节点
            if new_frontier:
                new_nodes = self.db.get_nodes_by_ids(list(new_frontier))
                for nid, node in new_nodes.items():
                    if len(results) < max_nodes:
                        results.append(NodeResult(node=node, depth=d))
                    else:
                        break

            frontier = new_frontier

        logger.info(
            f"[Retriever] expand({len(node_ids)} nodes, depth={depth}): "
            f"{len(results)} results"
        )
        return results

    # ── trace ──────────────────────────────────────────────

    async def trace(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 10,
        edge_types: Optional[list[str]] = None,
    ) -> list[PathResult]:
        """使用 SQLite recursive CTE 查找两节点间所有路径.

        Args:
            source_id:  起点节点 ID
            target_id:  终点节点 ID
            max_depth:  最大搜索深度
            edge_types: 边类型过滤

        Returns:
            路径列表, 按长度升序.
        """
        if max_depth > self.config.trace_max_depth:
            logger.warning(
                f"[Retriever] trace max_depth {max_depth} > "
                f"{self.config.trace_max_depth}, clamping"
            )
            max_depth = self.config.trace_max_depth

        paths = self.db.trace_path(
            source_id, target_id, max_depth=max_depth, edge_types=edge_types,
        )

        logger.info(
            f"[Retriever] trace({source_id[:8]}... → {target_id[:8]}...): "
            f"{len(paths)} paths"
        )
        return paths

    # ── diff ───────────────────────────────────────────────

    async def diff(
        self,
        project_id: str,
        source_dir: Optional[str] = None,
        force_rebuild: bool = False,
    ) -> DiffResult:
        """对比设计层 vs 代码层.

        Args:
            project_id: 项目标识
            source_dir: 源码目录 (如果代码层尚未构建, 则在此目录上先构建)
            force_rebuild: 强制重建代码层 (忽略 mtime 检测)

        Returns:
            DiffResult 包含汇总和详细差异列表.
        """
        # ── 代码层按需重索引: 无代码层 / 源码变更 / 强制重建 时触发 ──
        if source_dir:
            need_rebuild = force_rebuild
            if not need_rebuild:
                existing_code = self.db.find_nodes(project_id, source="code", limit=1)
                if not existing_code:
                    need_rebuild = True
                elif os.path.isdir(source_dir):
                    # 检测是否有源码文件比代码层最新节点更新 (同步演进)
                    latest_kg_dt = _parse_iso_dt(
                        max((n.updated_at for n in existing_code), default="")
                    )
                    for f in glob.glob(os.path.join(source_dir, "**", "*.py"),
                                       recursive=True):
                        if _mtime_dt(f) > latest_kg_dt:
                            need_rebuild = True
                            break
            if need_rebuild:
                logger.info(
                    f"[Retriever] Rebuilding code layer from {source_dir}"
                    f"{' (force)' if force_rebuild else ' (stale)'}"
                )
                builder = GraphBuilder(self.db_path)
                builder.rebuild_code_layer(project_id, source_dir)
                builder.close()

        summary = DiffSummary()
        items: list[DiffItem] = []

        # ── 1. Missing implementation ──
        missing = self.db.get_design_classes_without_implementation(project_id)
        for node in missing:
            items.append(DiffItem(
                severity="error",
                category=DiffCategory.MISSING_IMPLEMENTATION.value,
                message=(
                    f"设计类 '{node.name}' (stereotype={node.properties.get('stereotype', 'class')}) "
                    f"在源码中没有找到实现"
                ),
                design_node_id=node.id,
                detail={
                    "stereotype": node.properties.get("stereotype", ""),
                    "methods": len(node.properties.get("methods", [])),
                    "attributes": len(node.properties.get("attributes", [])),
                },
            ))
        summary.missing_implementations = len(missing)

        # ── 2. Extra code ──
        extra = self.db.get_code_classes_without_design(project_id)
        for node in extra:
            items.append(DiffItem(
                severity="warning",
                category=DiffCategory.EXTRA_CODE.value,
                message=(
                    f"源码类 '{node.name}' 在设计 UML 中没有对应的类"
                ),
                code_node_id=node.id,
                detail={
                    "path": node.properties.get("path", ""),
                },
            ))
        summary.extra_code = len(extra)

        # ── 3. Mismatch (方法签名不一致) ──
        pairs = self.db.get_implemented_pairs(project_id)
        for code_node, design_node in pairs:
            mismatch_details = _compare_class_nodes(design_node, code_node)
            if mismatch_details:
                items.append(DiffItem(
                    severity="warning",
                    category=DiffCategory.MISMATCH.value,
                    message=(
                        f"设计类 '{design_node.name}' 与源码实现存在差异"
                    ),
                    design_node_id=design_node.id,
                    code_node_id=code_node.id,
                    detail={"differences": mismatch_details},
                ))
                summary.mismatches += 1

        # ── 4. No coverage ──
        test_files = self.db.find_nodes(
            project_id, node_type="test_file", source="test", limit=100,
        )
        source_files = self.db.find_nodes(
            project_id, node_type="source_file", source="code", limit=200,
        )
        covered = set()
        for tf in test_files:
            for cov in tf.properties.get("covers", []):
                covered.add(cov)
        for sf in source_files:
            if sf.name not in covered:
                items.append(DiffItem(
                    severity="info",
                    category=DiffCategory.NO_COVERAGE.value,
                    message=f"源码文件 '{sf.name}' 没有对应的测试文件",
                    code_node_id=sf.id,
                ))
                summary.no_coverage += 1

        # ── 汇总 ──
        summary.total_design_classes = len(
            self.db.find_nodes(project_id, node_type="class", source="design", limit=500),
        )
        summary.total_code_classes = len(
            self.db.find_nodes(project_id, node_type="class", source="code", limit=500),
        )

        return DiffResult(summary=summary, items=items)

    # ── Utility: serialize for Agent ──────────────────────

    @staticmethod
    def serialize_node_results(results: list[NodeResult]) -> list[dict]:
        """将 NodeResult 列表序列化为 Agent 友好 JSON."""
        return [
            {
                "id": r.node.id,
                "node_type": r.node.node_type.value,
                "name": r.node.name,
                "source": r.node.source,
                "score": r.score,
                "depth": r.depth,
                "properties": r.node.properties,
            }
            for r in results
        ]

    @staticmethod
    def serialize_path_results(paths: list[PathResult]) -> list[dict]:
        """将 PathResult 列表序列化为 Agent 友好 JSON."""
        return [
            {
                "node_ids": p.node_ids,
                "length": p.length,
                "edges": p.edges,
                "node_names": [
                    n.name if n else "?" for n in p.nodes
                ],
            }
            for p in paths
        ]


# ── Comparison helpers ──────────────────────────────────────────

def _compare_class_nodes(design: GraphNode,
                         code: GraphNode) -> list[dict]:
    """比较设计和代码 CLASS 节点的方法 / 属性差异."""
    diffs: list[dict] = []

    design_methods: dict[str, dict] = {}
    for m in design.properties.get("methods", []):
        if isinstance(m, dict):
            design_methods[m.get("name", "")] = m
        else:
            design_methods[str(m)] = {"name": str(m)}

    code_methods: dict[str, dict] = {}
    for m in code.properties.get("methods", []):
        if isinstance(m, dict):
            code_methods[m.get("name", "")] = m
        else:
            code_methods[str(m)] = {"name": str(m)}

    # 设计有但代码没有的方法
    for mn in design_methods:
        if mn not in code_methods:
            diffs.append({
                "type": "missing_method",
                "name": mn,
                "message": f"方法 '{mn}' 在设计 UML 中定义但源码中缺失",
            })
    # 代码有但设计没有的方法
    for mn in code_methods:
        if mn not in design_methods:
            diffs.append({
                "type": "extra_method",
                "name": mn,
                "message": f"方法 '{mn}' 在源码中存在但设计 UML 中未定义",
            })

    # 参数 / 返回值不匹配 (仅对同名方法)
    for mn in design_methods:
        if mn in code_methods:
            dm = design_methods[mn]
            cm = code_methods[mn]
            if dm.get("return_type", "") != cm.get("return_type", ""):
                diffs.append({
                    "type": "return_type_mismatch",
                    "name": mn,
                    "design": dm.get("return_type"),
                    "code": cm.get("return_type"),
                    "message": f"方法 '{mn}' 返回类型不匹配",
                })

    return diffs
