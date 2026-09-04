"""知识图谱 v2 工具集 — 面向大型软件项目的结构化理解能力。

本文件是知识图谱工具的唯一实现。旧版 ``knowledge_graph_tools.py`` 与
``explore_project_tools.py`` 已下线删除（2026-08-30），KG 能力统一收敛到 v2。

设计原则:
  - 只回答文件原语（read/grep/bash）给不了的问题：类型化关系、设计-代码
    一致性、大项目的有界结构地图。
  - 每次返回都是「答案」而非「原始 dump」：紧凑序列化（白名单字段，丢弃
    methods[]/attributes[] 全量）+ 显式截断标记。
  - project_id 由工厂绑定，agent 无需手填。

分层架构（自上而下单向依赖，下层不感知上层）:
  L0 常量/类型   — 默认上限 + 复用 knowledge_graph.models 枚举
  L1 provider      — KnowledgeGraphProvider：统一索引、检索和结构化图计算能力
  L2 领域服务     — KGService: 本地 provider 的 SQLite 图计算实现，不碰 agent/序列化
  L3 序列化       — compact_node / bounded / group_by，紧凑有界分组
  L4 工具         — 5 个 AsyncTool 子类，绑定 project_id，解析参数后调 service
  L5 工厂         — create_kg_v2_tools(...)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Optional

from app.agent_base.core.knowledge_graph import KnowledgeGraphProvider, get_knowledge_graph
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# L0 常量 — 默认上限（防止大项目下结果失控）
# ═══════════════════════════════════════════════════════════════

DEFAULT_TOP_K = 10
MAX_MAP_CLASSES = 15
MAX_EXPAND_DEPTH = 2
MAX_EXPAND_NODES = 50
MAX_IMPACT_DEPTH = 2
MAX_IMPACT_NODES = 50
MAX_DIFF_ITEMS = 30


# ═══════════════════════════════════════════════════════════════
# L1 provider 辅助 — 序列化与查询结果整理
# ═══════════════════════════════════════════════════════════════

def _normalize_project_id(project_id: str) -> str:
    """归一化 project_id：剥离 .umlproj / .uml 后缀。

    KG 存储的 project_id 是去后缀后的 basename，agent 侧可能拿到带后缀的文件名。
    """
    pid = (project_id or "").strip()
    if pid.lower().endswith(".umlproj"):
        pid = pid[: -len(".umlproj")]
    elif pid.lower().endswith(".uml"):
        pid = pid[: -len(".uml")]
    return pid


def _normalize_path(path: str) -> str:
    """统一为绝对路径（正斜杠），确保 read_file 能直接读取。"""
    if not path:
        return ""
    try:
        return os.path.abspath(path).replace("\\", "/")
    except Exception:
        return path.replace("\\", "/")


def _parse_json(raw: Any) -> dict:
    """安全解析 properties JSON 字符串。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _build_file_map(db: Any, project_id: str) -> dict:
    """构建 filename → 绝对路径 映射（code 节点定位源码文件用）。"""
    fmap: dict[str, str] = {}
    try:
        rows = db.find_nodes(project_id, node_type="source_file", source="code", limit=2000)
    except Exception:
        logger.warning("[KGv2] file map build failed", exc_info=True)
        return fmap
    for n in rows:
        path = n.properties.get("path", "") if isinstance(n.properties, dict) else ""
        fmap[n.name] = _normalize_path(path) or n.name
    return fmap


# ═══════════════════════════════════════════════════════════════
# L2 领域服务 — 纯图计算，不碰 agent/序列化
# ═══════════════════════════════════════════════════════════════

class KGService:
    """封装 5 种理解能力的图计算逻辑，供工具层调用。"""

    def __init__(self, ctx: Any):
        self.ctx = ctx

    # ── map: 有界结构地图 ──────────────────────────────────

    def map_project(self, project_id: str,
                          top_classes: int = MAX_MAP_CLASSES) -> dict:
        """返回项目结构地图：图清单+类数、承重墙类、源码/测试文件统计。"""
        if not project_id:
            return {"error": "未绑定 project_id，无法生成地图"}
        db = self.ctx.db
        stats = db.stats(project_id)
        if stats["total_nodes"] == 0:
            return {"error": "项目在知识图谱中无节点（可能尚未索引）", "stats": stats}

        # 每张设计图及其类数（经 contains 边）
        diagram_rows = db.conn.execute("""\
            SELECT d.id, d.name, d.properties,
                   COUNT(c.id) AS class_count
            FROM kg_nodes d
            LEFT JOIN kg_edges e ON e.source_id = d.id AND e.edge_type = 'contains'
            LEFT JOIN kg_nodes c ON c.id = e.target_id
                                AND c.node_type = 'class' AND c.source = 'design'
            WHERE d.project_id = ? AND d.node_type = 'diagram' AND d.source = 'design'
            GROUP BY d.id
            ORDER BY class_count DESC
            LIMIT 100
        """, (project_id,)).fetchall()
        diagrams = [{
            "name": r["name"],
            "diagram_type": _parse_json(r["properties"]).get("diagram_type", ""),
            "class_count": r["class_count"],
        } for r in diagram_rows]

        # 承重墙：设计层类按 in-degree 排序（被引用最多的类）
        wall_rows = db.conn.execute("""\
            SELECT n.name, n.properties, COUNT(e.source_id) AS indegree
            FROM kg_nodes n
            LEFT JOIN kg_edges e ON e.target_id = n.id
            WHERE n.project_id = ? AND n.node_type = 'class' AND n.source = 'design'
            GROUP BY n.id
            ORDER BY indegree DESC
            LIMIT ?
        """, (project_id, top_classes)).fetchall()
        key_classes = [{
            "name": r["name"],
            "stereotype": _parse_json(r["properties"]).get("stereotype", "class"),
            "in_degree": r["indegree"],
        } for r in wall_rows]

        files = db.find_nodes(project_id, node_type="source_file", source="code", limit=2000)
        tests = db.find_nodes(project_id, node_type="test_file", source="test", limit=500)

        return {
            "project_id": project_id,
            "stats": {
                "total_nodes": stats["total_nodes"],
                "total_edges": stats["total_edges"],
                "by_type": stats["by_type"],
            },
            "diagrams": diagrams,
            "key_classes": key_classes,
            "files": {
                "source_count": len(files),
                "test_count": len(tests),
                "sources": [
                    {"name": f.name, "path": _normalize_path(f.properties.get("path", ""))}
                    for f in files[:20]
                ],
            },
        }

    # ── locate: 精确检索 ────────────────────────────────────

    def locate(self, project_id: str, pattern: str,
                     node_types: Optional[list[str]] = None,
                     source: Optional[str] = None,
                     top_k: int = DEFAULT_TOP_K) -> dict:
        """BM25 全文检索 + 名称模糊匹配（复用 GraphRetriever.query）。"""
        if not project_id:
            return {"error": "未绑定 project_id"}
        if not pattern:
            return {"error": "pattern 为空"}
        results = self.ctx.retriever.query(
            project_id, pattern, node_types=node_types,
            source=source, top_k=top_k,
        )
        file_map = {}
        if any(r.node.source == "code" for r in results):
            file_map = _build_file_map(self.ctx.db, project_id)
        return {"results": [compact_node_result(r, file_map) for r in results]}

    # ── expand: 邻域展开 ────────────────────────────────────

    def expand(self, project_id: str, node_ids: list[str],
                     direction: str = "outgoing",
                     edge_types: Optional[list[str]] = None,
                     max_depth: int = MAX_EXPAND_DEPTH,
                     max_nodes: int = MAX_EXPAND_NODES) -> dict:
        """n-hop BFS 邻域展开，结果带 depth 与经过的边类型。"""
        if not node_ids:
            return {"error": "node_ids 为空"}
        db = self.ctx.db
        visited: set[str] = set(node_ids)
        frontier: list[str] = list(node_ids)
        info: dict[str, dict] = {}  # nid -> {"depth": int, "types": set[str]}

        for depth in range(1, max_depth + 1):
            neighbors = db.get_neighbors(frontier, edge_types=edge_types, direction=direction)
            nxt: list[str] = []
            for _origin, neigh in neighbors.items():
                for (nid, et, _eid, _props) in neigh:
                    if nid in visited:
                        continue
                    visited.add(nid)
                    nxt.append(nid)
                    entry = info.setdefault(nid, {"depth": depth, "types": set()})
                    entry["types"].add(et)
            frontier = nxt
            if not frontier or len(visited) >= max_nodes:
                break

        nodes = db.get_nodes_by_ids(list(info.keys()))
        file_map = _build_file_map(db, project_id)
        expanded = [{
            **compact_node(nodes[nid], file_map),
            "depth": meta["depth"],
            "edge_types": sorted(meta["types"]),
        } for nid, meta in info.items() if nid in nodes]

        return {
            "direction": direction,
            "results": bounded(expanded, max_nodes),
        }

    # ── impact: 反依赖影响分析 ──────────────────────────────

    def impact(self, project_id: str, node_id: str,
                     max_depth: int = MAX_IMPACT_DEPTH,
                     max_nodes: int = MAX_IMPACT_NODES) -> dict:
        """反向依赖 BFS：改这个节点会碰谁。直接依赖(depth=1) vs 传递依赖。"""
        if not node_id:
            return {"error": "node_id 为空"}
        db = self.ctx.db
        target = db.get_node(node_id)
        if target is None:
            return {"error": f"节点不存在: {node_id}（先用 kg_locate / kg_map 拿 id）"}

        visited: set[str] = {node_id}
        frontier: list[str] = [node_id]
        agg: dict[str, dict] = {}  # nid -> {"depth": int, "types": set[str]}

        for depth in range(1, max_depth + 1):
            neighbors = db.get_neighbors(frontier, direction="incoming")
            nxt: list[str] = []
            for _origin, neigh in neighbors.items():
                for (nid, et, _eid, _props) in neigh:
                    if nid in visited:
                        continue
                    visited.add(nid)
                    nxt.append(nid)
                    entry = agg.setdefault(nid, {"depth": depth, "types": set()})
                    entry["depth"] = min(entry["depth"], depth)
                    entry["types"].add(et)
            frontier = nxt
            if not frontier or len(visited) >= max_nodes:
                break

        nodes = db.get_nodes_by_ids(list(agg.keys()))
        file_map = _build_file_map(db, project_id)

        def bucket(recs: list[tuple[str, dict]]) -> dict[str, list[dict]]:
            grouped: dict[str, list[dict]] = {}
            for nid, meta in recs:
                node = nodes.get(nid)
                if node is None:
                    continue
                grouped.setdefault(node.node_type.value, []).append({
                    **compact_node(node, file_map),
                    "edge_types": sorted(meta["types"]),
                })
            return grouped

        direct = [(nid, meta) for nid, meta in agg.items() if meta["depth"] == 1]
        transitive = [(nid, meta) for nid, meta in agg.items() if meta["depth"] > 1]

        return {
            "target": compact_node(target, file_map),
            "direct_dependents": bucket(direct),
            "transitive_dependents": bucket(transitive),
            "total_affected": len(agg),
        }

    # ── diff: 设计-代码一致性 ───────────────────────────────

    def diff(self, project_id: str, source_dir: Optional[str] = None,
                   force_rebuild: bool = False,
                   max_items: int = MAX_DIFF_ITEMS) -> dict:
        """设计层 vs 代码层差异（复用 GraphRetriever.diff，含代码层惰性重建）。"""
        result = self.ctx.retriever.diff(
            project_id, source_dir=source_dir, force_rebuild=force_rebuild,
        )
        items = [{
            "severity": item.severity,
            "category": item.category,
            "message": item.message,
            "design_node_id": item.design_node_id,
            "code_node_id": item.code_node_id,
            "detail": item.detail,
        } for item in result.items]
        return {
            "schema_version": 2,
            "summary": result.summary.to_dict(),
            "items": bounded(items, max_items),
        }


# ═══════════════════════════════════════════════════════════════
# L3 序列化 — 紧凑/有界/分组，避免原始 dump 膨胀
# ═══════════════════════════════════════════════════════════════

_PROP_WHITELIST = ("stereotype", "path", "language")


def compact_node(node, file_map: Optional[dict] = None) -> dict:
    """节点紧凑序列化：只保留定位 + 少量关键属性，丢弃 methods[]/attributes[]。"""
    props = node.properties if isinstance(node.properties, dict) else {}
    out = {
        "id": node.id,
        "node_type": node.node_type.value,
        "name": node.name,
        "source": node.source,
    }
    for key in _PROP_WHITELIST:
        if key in props:
            out[key] = props[key]
    # code 层定位：优先节点自带 filename（builder 已存），其次 file_map
    if node.source == "code":
        fname = props.get("filename", "")
        out["file"] = (file_map or {}).get(fname) or _normalize_path(props.get("path", "")) or fname
        # read_file 就绪坐标：0 基 offset + limit，消除 agent 的 off-by-one 换算
        lineno = props.get("lineno")
        if lineno is not None:
            lineno = int(lineno)
            end = int(props.get("end_lineno") or lineno)
            out["offset"] = max(lineno - 1, 0)
            out["limit"] = max(end - lineno + 1, 1)
    return out


def compact_node_result(r, file_map: Optional[dict] = None) -> dict:
    d = compact_node(r.node, file_map)
    if r.score:
        d["score"] = r.score
    if r.depth:
        d["depth"] = r.depth
    return d


def bounded(items: list, cap: int) -> dict:
    """有界包装：显式标注 total/returned/truncated，防止 agent 以为没截断。"""
    items = list(items)
    total = len(items)
    shown = items[:cap]
    return {
        "items": shown,
        "total": total,
        "returned": len(shown),
        "truncated": total > cap,
    }


# ═══════════════════════════════════════════════════════════════
# L4 工具层 — AsyncTool 子类，绑定 project_id
# ═══════════════════════════════════════════════════════════════

async def _with_provider(
    provider: KnowledgeGraphProvider,
    fn: Callable[[], Any],
) -> Any:
    """把 provider 的同步图计算卸载到线程池，避免阻塞事件循环。

    provider 可以是本地 SQLite 实现，也可以是远程图谱实现；工具层不感知
    数据库连接和资源生命周期。
    """
    return await asyncio.to_thread(fn)


class _KgTool(AsyncTool):
    """恢复基类 Tool.to_openai_schema 行为（由 get_parameters 生成 schema）。

    AsyncTool 默认 to_openai_schema 抛 NotImplementedError，这里改回由
    get_parameters() 自动生成，各工具只需覆写 get_parameters。
    """

    def to_openai_schema(self) -> dict:
        return Tool.to_openai_schema(self)


class KgMapTool(_KgTool):
    """项目结构地图 — 图清单/承重墙/文件统计（有界摘要）。"""

    def __init__(self, provider: KnowledgeGraphProvider, project_id: str):
        super().__init__(
            name="get_project_map",
            description=(
                "Return a bounded structural map of the current project: diagram list with "
                "class counts, load-bearing classes (highest in-degree = most depended on), "
                "and source/test file counts. Use this to get the overall shape of a project "
                "before diving in — it is the project map, not a text search."
            ),
        )
        self.provider = provider
        self.project_id = project_id

    def get_parameters(self) -> list:
        return [
            ToolParameter(
                name="top_classes", type="number",
                description="How many load-bearing classes to list",
                required=False, default=MAX_MAP_CLASSES,
            ),
        ]

    async def _execute(self, params: dict) -> str:
        top = int(params.get("top_classes") or MAX_MAP_CLASSES)
        result = await _with_provider(
            self.provider,
            lambda: self.provider.map_project(self.project_id, top),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)


class KgLocateTool(_KgTool):
    """定位节点 — BM25 检索 + 名称匹配，返回紧凑节点 + 源码文件。"""

    def __init__(self, provider: KnowledgeGraphProvider, project_id: str):
        super().__init__(
            name="find_nodes",
            description=(
                "Locate nodes in the project knowledge graph by full-text search "
                "(BM25 + name match). Returns compact nodes with source file when available. "
                "Use for: does class X exist, where is a feature, list all classes of a type. "
                "This finds STRUCTURE — for exact line content use read_file, for arbitrary "
                "symbol/string search use bash grep."
            ),
        )
        self.provider = provider
        self.project_id = project_id

    def get_parameters(self) -> list:
        return [
            ToolParameter(
                name="query", type="string",
                description="Search text: class/component/method name or feature keyword",
                required=True,
            ),
            ToolParameter(
                name="node_types", type="array",
                description="Filter node types, e.g. ['class','method']. Empty = all.",
                required=False,
            ),
            ToolParameter(
                name="source", type="string",
                description="Filter by source: 'design' | 'code' | 'test'. Empty = all.",
                required=False,
            ),
            ToolParameter(
                name="top_k", type="number", description="Max results",
                required=False, default=DEFAULT_TOP_K,
            ),
        ]

    async def _execute(self, params: dict) -> str:
        query = str(params.get("query") or "").strip()
        node_types = params.get("node_types") or None
        source = (str(params.get("source") or "").strip()) or None
        top_k = int(params.get("top_k") or DEFAULT_TOP_K)
        result = await _with_provider(
            self.provider,
            lambda: self.provider.locate(
                self.project_id, query, node_types, source, top_k,
            ),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)


class KgExpandTool(_KgTool):
    """邻域展开与反向依赖影响分析 — BFS 有界。"""

    def __init__(self, provider: KnowledgeGraphProvider, project_id: str):
        super().__init__(
            name="expand_neighbors",
            description=(
                "Expand the neighborhood of graph node(s) (bounded BFS). Given node ids from "
                "find_nodes / get_project_map, return connected nodes with distance and edge "
                "types. Default mode='neighbors' answers what is connected to a node. For change "
                "risk, use mode='impact' with exactly one node id: it returns the target, direct "
                "dependents, transitive dependents, grouped by node type, including test files."
            ),
        )
        self.provider = provider
        self.project_id = project_id

    def get_parameters(self) -> list:
        return [
            ToolParameter(
                name="node_ids", type="array",
                description="Starting node ids (from find_nodes / get_project_map); impact mode requires exactly one.",
                required=True,
            ),
            ToolParameter(
                name="mode", type="string",
                description="'neighbors' (default) for bounded BFS or 'impact' for reverse dependency summary.",
                required=False, default="neighbors",
            ),
            ToolParameter(
                name="direction", type="string",
                description="'outgoing' (default) | 'incoming' | 'both'",
                required=False, default="outgoing",
            ),
            ToolParameter(
                name="edge_types", type="array",
                description="Filter edge types, e.g. ['contains','depends']. Empty = all.",
                required=False,
            ),
            ToolParameter(
                name="max_depth", type="number",
                description="BFS depth (1 = direct neighbors)",
                required=False, default=MAX_EXPAND_DEPTH,
            ),
            ToolParameter(
                name="max_nodes", type="number", description="Result cap",
                required=False, default=MAX_EXPAND_NODES,
            ),
        ]

    async def _execute(self, params: dict) -> str:
        node_ids = params.get("node_ids") or []
        mode = str(params.get("mode") or "neighbors").strip().lower() or "neighbors"
        if mode == "impact":
            if len(node_ids) != 1:
                return json.dumps({
                    "error": "impact 模式要求 node_ids 恰好包含一个节点 id",
                }, ensure_ascii=False, indent=2)
            max_depth = int(params.get("max_depth") or MAX_IMPACT_DEPTH)
            max_nodes = int(params.get("max_nodes") or MAX_IMPACT_NODES)
            result = await _with_provider(
                self.provider,
                lambda: self.provider.impact(
                    self.project_id, str(node_ids[0]), max_depth, max_nodes,
                ),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        if mode != "neighbors":
            return json.dumps({
                "error": "mode 必须是 neighbors 或 impact",
            }, ensure_ascii=False, indent=2)
        direction = str(params.get("direction") or "outgoing").strip().lower() or "outgoing"
        edge_types = params.get("edge_types") or None
        max_depth = int(params.get("max_depth") or MAX_EXPAND_DEPTH)
        max_nodes = int(params.get("max_nodes") or MAX_EXPAND_NODES)
        result = await _with_provider(
            self.provider,
            lambda: self.provider.expand(
                self.project_id, node_ids, direction, edge_types, max_depth, max_nodes,
            ),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)


class KgDiffTool(_KgTool):
    """设计-代码一致性 — 缺失实现/多余代码/签名漂移/测试覆盖。"""

    def __init__(self, provider: KnowledgeGraphProvider, project_id: str, source_dir: str = ""):
        super().__init__(
            name="compare_design_code",
            description=(
                "Compare the UML design layer against the code layer: missing implementations, "
                "extra code, method signature mismatches, untested files. Returns a summary "
                "count + bounded detail items. Each item includes deterministic design_ref/code_ref "
                "locations; code_ref has file, start_line/end_line and a read_hint for read_file, "
                "while method mismatches include method-level refs and expected/actual values. "
                "Use those refs to read only the affected code before semantic verification. "
                "verification indicates whether the result is structural or coverage-index based. "
                "On first use this may lazily index the source code layer."
            ),
        )
        self.provider = provider
        self.project_id = project_id
        self.source_dir = source_dir

    def get_parameters(self) -> list:
        return [
            ToolParameter(
                name="force_rebuild", type="boolean",
                description="Force full rebuild of the code layer before diffing",
                required=False, default=False,
            ),
            ToolParameter(
                name="max_items", type="number",
                description="Max detail items to return",
                required=False, default=MAX_DIFF_ITEMS,
            ),
        ]

    async def _execute(self, params: dict) -> str:
        force = bool(params.get("force_rebuild") or False)
        max_items = int(params.get("max_items") or MAX_DIFF_ITEMS)
        result = await _with_provider(
            self.provider,
            lambda: self.provider.diff(self.project_id, self.source_dir, force, max_items),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# L5 工厂
# ═══════════════════════════════════════════════════════════════

def create_kg_v2_tools(
    db_path: str | None = None,
    project_file: str = "",
    source_dir: str = "",
    include_compare: bool = False,
    provider: KnowledgeGraphProvider | None = None,
) -> list[Tool]:
    """创建 KG v2 工具集（默认 3 个），project_id 由 project_file 自动绑定。

    ``compare_design_code`` 保留为显式 opt-in 工具。窄范围编辑任务通常不需要
    全量设计-代码漂移分析；默认不暴露它，避免探索阶段产生大体量输出。

    Args:
        db_path:     兼容旧调用的本地 SQLite 路径；传入 provider 时忽略
        project_file:.umlproj 路径（用于推导 project_id）
        source_dir:  源码目录（kg_diff 按需索引代码层时使用）
        provider:    已加载的知识图谱 provider；未传入时使用默认 provider

    Returns:
        [KgMapTool, KgLocateTool, KgExpandTool]；include_compare=True 时追加 KgDiffTool
    """
    if provider is None:
        if db_path is None:
            provider = get_knowledge_graph()
        else:
            # Preserve the old test/embedding API while still routing every
            # tool operation through the provider contract.
            from extensions.knowledge_graph.provider import create as create_local_provider

            provider = create_local_provider(db_path=db_path)

    project_id = ""
    if project_file:
        project_id = _normalize_project_id(
            os.path.splitext(os.path.basename(project_file))[0],
        )
    tools: list[Tool] = [
        KgMapTool(provider, project_id),
        KgLocateTool(provider, project_id),
        KgExpandTool(provider, project_id),
    ]
    if include_compare:
        tools.append(KgDiffTool(provider, project_id, source_dir))
    return tools
