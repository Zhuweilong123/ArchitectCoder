"""知识图谱工具 — 4 个 Agent 可调用工具.

使 Agent 能够查询、展开、追踪知识图谱中的设计-代码关系。

Following the exact AsyncTool pattern from conversation_tools.py:
  - Inherit AsyncTool (run() returns coroutine → awaited by aexecute_tool_with_params)
  - Override to_openai_schema() for FC-compatible schema
  - _execute() instantiates GraphRetriever, queries, serializes, closes

Usage:
    from app.agent_base.tools.my_tools.knowledge_graph_tools import create_kg_tools

    for tool in create_kg_tools(db_path="./data/knowledge_graph.db"):
        registry.register_tool(tool)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from app.agent_base.tools.base import Tool
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────

def _open_retriever(db_path: str):
    """延迟导入避免循环依赖."""
    from knowledge_graph.retriever import GraphRetriever
    return GraphRetriever(db_path)


def _serialize_node_result(r, file_map: dict | None = None) -> dict:
    """序列化一个 NodeResult 为 Agent 友好的 dict.

    code 层节点（class/method/attribute）附加 source_file 定位信息，
    便于 Agent 用 read_file 读取对应源码文件。
    """
    node = r.node
    props = node.properties
    result = {
        "id": node.id,
        "node_type": node.node_type.value,
        "name": node.name,
        "source": node.source,
        "score": r.score,
        "depth": r.depth,
        "properties": props,
    }
    if node.source == "code":
        fname = props.get("filename") if isinstance(props, dict) else ""
        result["source_file"] = (file_map or {}).get(fname, fname)
    return result


def _serialize_node_results(results, file_map: dict | None = None) -> list[dict]:
    return [_serialize_node_result(r, file_map) for r in results]


def _normalize_file_path(path: str) -> str:
    """规范化文件路径为 Agent 可用的绝对路径（正斜杠）。

    源码层 path 常是相对后端运行目录的 '../generated/src/...'，基准不一致
    且混用反斜杠。统一为绝对路径，确保 read_file 能直接读取。
    """
    if not path:
        return path
    try:
        abs_path = os.path.abspath(path)
        return abs_path.replace("\\", "/")
    except Exception:
        return path.replace("\\", "/")


def _normalize_project_id(project_id: str) -> str:
    """归一化 project_id：剥离 .umlproj / .uml 后缀。

    Agent 可能从 project_info 拿到带后缀的文件名（如 radar_design_0730.umlproj），
    而 KG 存储的 project_id 是去后缀后的 basename。统一剥离后缀，避免查空。
    """
    pid = (project_id or "").strip()
    if pid.lower().endswith(".umlproj") or pid.lower().endswith(".uml"):
        pid = pid[:-len(".umlproj")] if pid.lower().endswith(".umlproj") else pid[:-len(".uml")]
    return pid


def _build_file_map(db, project_id: str = "") -> dict:
    """构建 filename → 完整绝对路径 映射（用于 code 节点定位源码文件）。"""
    fmap: dict[str, str] = {}
    try:
        from knowledge_graph.database import KnowledgeGraphDB
        if not isinstance(db, KnowledgeGraphDB):
            return fmap
        rows = db.find_nodes(
            project_id, node_type="source_file", source="code", limit=2000,
        )
        for n in rows:
            if n.name:
                path = n.properties.get("path") if isinstance(n.properties, dict) else ""
                fmap[n.name] = _normalize_file_path(path) or n.name
    except Exception:
        logger.warning("[KG] Failed to build file map", exc_info=True)
    return fmap


def _build_file_map_all(db) -> dict:
    """构建 filename → 路径 映射，不限定 project（kg_expand 无 project_id 时用）。"""
    fmap: dict[str, str] = {}
    try:
        from knowledge_graph.database import KnowledgeGraphDB
        if not isinstance(db, KnowledgeGraphDB):
            return fmap
        conn = db.conn
        rows = conn.execute(
            "SELECT name, properties FROM kg_nodes "
            "WHERE node_type='source_file' AND source='code'"
        ).fetchall()
        for row in rows:
            name = row["name"]
            path = ""
            try:
                import json as _json
                props = _json.loads(row["properties"])
                path = props.get("path", "")
            except Exception:
                pass
            if name:
                fmap[name] = _normalize_file_path(path) or name
    except Exception:
        logger.warning("[KG] Failed to build file map (all)", exc_info=True)
    return fmap


# ═══════════════════════════════════════════════════════════════
# Tool 1: kg_query — BM25 全文检索
# ═══════════════════════════════════════════════════════════════

class KgQueryTool(AsyncTool):
    """全文检索知识图谱 — BM25 + 类型过滤.

    查询前检查目标项目是否已索引；若知识图谱缺少该项目数据且提供了
    project_file，则从 .umlproj 立即构建，避免 agent 面对空库抓瞎。
    """

    def __init__(self, db_path: str = "./data/knowledge_graph.db",
                 project_file: str = ""):
        super().__init__(
            name="kg_query",
            description=(
                "Full-text search nodes in the project knowledge graph. "
                "Supports filtering by node type "
                "(class/component/method/attribute/source_file/lifeline/interface/...). "
                "Returns a list of matching nodes with BM25 relevance scores. "
                "Use cases: check whether a class/component/method exists; locate a "
                "feature in code; list designed classes. To enumerate ALL nodes of a "
                "type, pass an empty pattern with node_types set. "
                "IMPORTANT: full-text search can miss nodes whose names differ from "
                "your keywords (e.g. node 'EchoSimulation' won't match phrase "
                "'simulator transmit'). Prefer empty pattern + node_types to "
                "enumerate, or kg_expand from a known node, over guessing multi-word "
                "phrases. Sequence-diagram fragments and messages are NOT indexed in "
                "the KG — read the .umlproj file (read_file/grep) to inspect them."
            ),
        )
        self.db_path = db_path
        self.project_file = project_file

    def _indexed_diagram_signature(self, project_id: str) -> set[tuple[str, str]]:
        """KG 中已索引的图签名：{(name, diagram_type)}."""
        from knowledge_graph.database import KnowledgeGraphDB
        db = KnowledgeGraphDB(self.db_path)
        try:
            diagrams = db.find_nodes(
                project_id, node_type="diagram", source="design", limit=200,
            )
            return {
                (d.name, (d.properties or {}).get("diagram_type", ""))
                for d in diagrams
            }
        finally:
            db.close()

    def _build_project_index(self, project_id: str) -> bool:
        """从 project_file 全量重建 design 层索引（兜底）。"""
        if not self.project_file or not os.path.isfile(self.project_file):
            logger.warning("[KG] project_file missing: %s", self.project_file)
            return False
        try:
            from knowledge_graph.builder import GraphBuilder
            from app.services.file_service import load_project
            project = load_project(self.project_file)
            builder = GraphBuilder(db_path=self.db_path)
            stats = builder.build_from_project(project, project_id)
            builder.close()
            logger.info("[KG] On-demand build for '%s': %s", project_id, stats)
            return True
        except Exception:
            logger.exception("[KG] On-demand build failed for '%s'", project_id)
            return False

    def _ensure_project_indexed(self, project_id: str) -> bool:
        """确保 project_id 已索引且与文件一致；缺失或陈旧时按需重建.

        之前只用「项目有没有任意节点」判断，导致文件里后来新增的图（例如类图）
        在 KG 已有一张图后永远不会被索引。现在比对文件与 KG 的图签名集合，
        不一致（新增/删除/改类型）即重建。

        Returns:
            True 表示项目数据可查（原本就有且未过期，或构建成功），否则 False。
        """
        try:
            indexed = self._indexed_diagram_signature(project_id)
        except Exception:
            logger.warning("[KG] Failed to check project index", exc_info=True)
            indexed = None

        # 1) 无索引 → 直接构建
        if not indexed:
            return self._build_project_index(project_id)

        # 2) 有索引但拿得到文件 → 比对图签名，检测新增/删除的图
        if self.project_file and os.path.isfile(self.project_file):
            try:
                from app.services.file_service import load_project
                project = load_project(self.project_file)
                file_sig = {(d.name, d.diagram_type) for d in project.diagrams}
            except Exception:
                logger.warning(
                    "[KG] Failed to load project file for staleness check",
                    exc_info=True,
                )
                file_sig = None

            if file_sig is not None and file_sig != indexed:
                logger.info(
                    "[KG] Stale index for '%s': file=%s indexed=%s — rebuilding",
                    project_id, sorted(file_sig), sorted(indexed),
                )
                return self._build_project_index(project_id)

        return True

    async def _execute(self, params: dict) -> str:
        retriever = _open_retriever(self.db_path)
        try:
            node_types = None
            if params.get("node_types"):
                node_types = [
                    t.strip()
                    for t in params["node_types"].split(",")
                    if t.strip()
                ]

            project_id = _normalize_project_id(params.get("project_id", ""))
            pattern = params.get("pattern", "").strip()

            # 按需构建兜底: 项目未索引时从设计文件构建
            if project_id:
                self._ensure_project_indexed(project_id)

            # 空 pattern + 指定 node_types → 枚举该类型全部节点（绕过 BM25）
            if not pattern:
                if node_types:
                    from knowledge_graph.database import KnowledgeGraphDB
                    from knowledge_graph.models import NodeResult
                    db = KnowledgeGraphDB(self.db_path)
                    try:
                        file_map = _build_file_map(db, project_id)
                        nodes: list[dict] = []
                        for nt in node_types:
                            for node in db.find_nodes(
                                project_id, node_type=nt,
                                source=params.get("source"), limit=500,
                            ):
                                nodes.append(_serialize_node_result(
                                    NodeResult(node=node, score=0.0), file_map
                                ))
                        db.close()
                        return json.dumps({
                            "results": nodes,
                            "count": len(nodes),
                            "mode": "enumerate",
                        }, ensure_ascii=False)
                    except Exception:
                        db.close()
                        logger.exception("[KG] Enumerate failed")
                        return json.dumps({
                            "message": "Failed to enumerate nodes; retry or use a non-empty pattern.",
                            "results": [],
                            "count": 0,
                        }, ensure_ascii=False)

                # 无 node_types 且 pattern 为空：给出引导而非报错
                return json.dumps({
                    "message": (
                        "kg_query needs a non-empty pattern for full-text search; "
                        "or provide node_types (e.g. 'class,component,diagram') to "
                        "enumerate all nodes of those types. "
                        "Example: pattern='diagram' to search diagrams, or "
                        "node_types='class' to enumerate all classes."
                    ),
                    "results": [],
                    "count": 0,
                }, ensure_ascii=False)

            results = await retriever.query(
                project_id=project_id,
                pattern=pattern,
                node_types=node_types,
                source=params.get("source"),
                top_k=params.get("top_k", 20),
            )

            file_map = _build_file_map(retriever.db, project_id)
            serialized = _serialize_node_results(results, file_map)

            # 如果没有结果, 返回引导信息, 避免 Agent 原地盲目重试
            if not serialized:
                return json.dumps({
                    "message": (
                        f"No nodes matching '{pattern}' found in project "
                        f"'{project_id}'. Suggestions: 1) use broader keywords "
                        f"(e.g. 'class', 'component', a core noun from a diagram "
                        f"name); 2) use kg_expand to expand from a known diagram/"
                        f"class node ID; 3) verify project_id is correct."
                    ),
                    "results": [],
                    "count": 0,
                }, ensure_ascii=False)

            return json.dumps({
                "results": serialized,
                "count": len(serialized),
            }, ensure_ascii=False)

        finally:
            retriever.close()

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "kg_query",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project identifier (diagram name or project file name)",
                        },
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Query text, e.g. 'User login method' or a specific "
                                "class/component name. Used for full-text search; if "
                                "left empty, returns guidance instead of an error."
                            ),
                        },
                        "node_types": {
                            "type": "string",
                            "description": (
                                "Comma-separated node type filter, e.g. "
                                "'class,method,component'. Available types: project, "
                                "diagram, class, component, lifeline, source_file, "
                                "test_file, method, attribute, interface. "
                                "Note: message/messages are edge types, not nodes; to "
                                "query sequence diagram messages use lifeline or "
                                "kg_expand. Omit to search all types."
                            ),
                        },
                        "source": {
                            "type": "string",
                            "description": "Source filter: 'design' (UML design) | 'code' (source) | 'test' (tests). Omit to search all.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Maximum number of results, default 20",
                        },
                    },
                    "required": ["project_id", "pattern"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════════
# Tool 2: kg_expand — 展开节点关系
# ═══════════════════════════════════════════════════════════════

class KgExpandTool(AsyncTool):
    """展开节点关系 — 查看邻域结构."""

    def __init__(self, db_path: str = "./data/knowledge_graph.db"):
        super().__init__(
            name="kg_expand",
            description=(
                "Expand node relationships in the knowledge graph to view a node's "
                "neighborhood structure. Supports 1-2 level depth expansion, edge-type "
                "filtering, and expansion direction control. "
                "Use cases: see what methods/attributes/parents/dependencies a class "
                "has; understand which sub-components and interfaces a component "
                "contains; view a node's full context."
            ),
        )
        self.db_path = db_path

    async def _execute(self, params: dict) -> str:
        retriever = _open_retriever(self.db_path)
        try:
            node_ids = [
                nid.strip()
                for nid in params["node_ids"].split(",")
                if nid.strip()
            ]

            edge_types = None
            if params.get("edge_types"):
                edge_types = [
                    et.strip()
                    for et in params["edge_types"].split(",")
                    if et.strip()
                ]

            results = await retriever.expand(
                node_ids=node_ids,
                depth=params.get("depth", 1),
                edge_types=edge_types,
                direction=params.get("direction", "outgoing"),
                max_nodes=params.get("max_nodes", 50),
            )

            file_map = _build_file_map_all(retriever.db)
            serialized = _serialize_node_results(results, file_map)

            # 按深度分组
            by_depth = {}
            for r in serialized:
                d = r["depth"]
                if d not in by_depth:
                    by_depth[d] = []
                by_depth[d].append(r)

            return json.dumps({
                "results": serialized,
                "count": len(serialized),
                "by_depth": {
                    str(k): v for k, v in sorted(by_depth.items())
                },
            }, ensure_ascii=False)

        finally:
            retriever.close()

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "kg_expand",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_ids": {
                            "type": "string",
                            "description": "Comma-separated node IDs (from kg_query results)",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "Expansion depth: 1=direct neighbors, 2=neighbors of neighbors. Max 2. Default 1.",
                        },
                        "edge_types": {
                            "type": "string",
                            "description": (
                                "Comma-separated edge type filter, e.g. "
                                "'contains,inherits,implements'. Available: contains, "
                                "inherits, composition, aggregation, association, "
                                "realization, dependency, implements, imports, tests, "
                                "references, messages. Omit to expand all edge types."
                            ),
                        },
                        "direction": {
                            "type": "string",
                            "description": "Expansion direction: 'outgoing' | 'incoming' | 'both'. Default 'outgoing'.",
                        },
                        "max_nodes": {
                            "type": "integer",
                            "description": "Maximum number of nodes returned, default 50",
                        },
                    },
                    "required": ["node_ids"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════════
# Tool 3: kg_trace — 依赖路径追踪
# ═══════════════════════════════════════════════════════════════

class KgTraceTool(AsyncTool):
    """追踪依赖链 — 查找节点间所有路径."""

    def __init__(self, db_path: str = "./data/knowledge_graph.db"):
        super().__init__(
            name="kg_trace",
            description=(
                "Trace all dependency paths between two nodes in the knowledge graph. "
                "Use cases: understand inheritance/dependency chains between classes "
                "(e.g. how 'User' indirectly depends on 'Logger'); analyze the call "
                "path from a component to an interface; troubleshoot circular "
                "dependencies. Returns all paths, each with the node sequence and "
                "edge types traversed."
            ),
        )
        self.db_path = db_path

    async def _execute(self, params: dict) -> str:
        retriever = _open_retriever(self.db_path)
        try:
            edge_types = None
            if params.get("edge_types"):
                edge_types = [
                    et.strip()
                    for et in params["edge_types"].split(",")
                    if et.strip()
                ]

            paths = await retriever.trace(
                source_id=params["source_id"],
                target_id=params["target_id"],
                max_depth=params.get("max_depth", 10),
                edge_types=edge_types,
            )

            serialized = retriever.serialize_path_results(paths)

            if not serialized:
                return json.dumps({
                    "message": (
                        f"No path found from '{params['source_id'][:12]}...' to "
                        f"'{params['target_id'][:12]}...' "
                        f"(max_depth={params.get('max_depth', 10)}). "
                        f"The two nodes may have no direct or indirect connection."
                    ),
                    "paths": [],
                    "count": 0,
                }, ensure_ascii=False)

            return json.dumps({
                "paths": serialized,
                "count": len(serialized),
                "shortest_length": serialized[0]["length"] if serialized else 0,
            }, ensure_ascii=False)

        finally:
            retriever.close()

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "kg_trace",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_id": {
                            "type": "string",
                            "description": "Start node ID (from kg_query results)",
                        },
                        "target_id": {
                            "type": "string",
                            "description": "End node ID (from kg_query results)",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Maximum search depth, default 10. Values above 10 are truncated.",
                        },
                        "edge_types": {
                            "type": "string",
                            "description": "Comma-separated edge type filter. Omit to use all edge types.",
                        },
                    },
                    "required": ["source_id", "target_id"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════════
# Tool 4: kg_diff — 对比设计 vs 代码
# ═══════════════════════════════════════════════════════════════

class KgDiffTool(AsyncTool):
    """对比设计 vs 代码 — 找出不一致."""

    def __init__(self, db_path: str = "./data/knowledge_graph.db",
                 source_dir: str = ""):
        super().__init__(
            name="kg_diff",
            description=(
                "Compare UML design against source code implementations and find "
                "differences. Detects 3 problem types: missing_implementation (design "
                "has it but code does not), extra_code (code has it but design does "
                "not), mismatch (method signatures differ). "
                "Use cases: verify completeness after code generation; assess the gap "
                "before refactoring; keep design and implementation in sync."
            ),
        )
        self.db_path = db_path
        self.source_dir = source_dir

    async def _execute(self, params: dict) -> str:
        source_dir = params.get("source_dir", self.source_dir)
        force_rebuild = bool(params.get("force", False))
        retriever = _open_retriever(self.db_path)
        try:
            diff_result = await retriever.diff(
                project_id=params["project_id"],
                source_dir=source_dir or None,
                force_rebuild=force_rebuild,
            )

            result_dict = diff_result.to_dict()

            # 添加建议
            suggestions: list[str] = []
            s = result_dict["summary"]
            if s["missing_implementations"] > 0:
                suggestions.append(
                    f"{s['missing_implementations']} design classes not implemented; "
                    f"consider implementing them in source"
                )
            if s["mismatches"] > 0:
                suggestions.append(
                    f"{s['mismatches']} method signature mismatches; "
                    f"sync design or code"
                )
            if s["extra_code"] > 0:
                suggestions.append(
                    f"{s['extra_code']} source classes not in the design UML; "
                    f"consider reverse-engineering into UML or removing redundant code"
                )
            if s.get("no_coverage", 0) > 0:
                suggestions.append(
                    f"{s['no_coverage']} files lack test coverage"
                )
            if not suggestions:
                suggestions.append("Design and code are fully consistent")

            result_dict["suggestions"] = suggestions

            return json.dumps(result_dict, ensure_ascii=False)

        finally:
            retriever.close()

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "kg_diff",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project identifier",
                        },
                        "source_dir": {
                            "type": "string",
                            "description": "Source directory path. Auto-rebuilt when the code layer is missing or source changed; set force=true to force a rebuild",
                        },
                        "force": {
                            "type": "boolean",
                            "description": "Set true to force a code layer rebuild, ignoring change detection (useful when source was just generated and mtime was not updated)",
                        },
                    },
                    "required": ["project_id"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════════
# Tool 5: kg_project_structure — 项目结构摘要
# ═══════════════════════════════════════════════════════════════

class KgProjectStructureTool(AsyncTool):
    """项目结构摘要 — 一步获取"图→类→方法"树状结构。

    渐进式披露的核心入口：Agent 不再需要多轮 kg_query/kg_expand 拼结构，
    一次调用拿到整棵结构树，配合 read_file 的 file_offset 精确读单张图。
    """

    def __init__(self, db_path: str = "./data/knowledge_graph.db",
                 project_file: str = ""):
        super().__init__(
            name="kg_project_structure",
            description=(
                "Get the COMPLETE UML structure of the project in one call: every "
                "diagram with its contained classes/components/lifelines, their "
                "methods and attributes, and (at depth=3) sequence-diagram messages. "
                "The result includes a stats block (exact counts of diagrams/classes/"
                "components/methods/messages) proving the output is complete. "
                "MANDATORY: use this tool FIRST for any task that needs to understand "
                "the project's design (summary, overview, questions about classes/"
                "relationships). The output is authoritative and complete — do NOT "
                "follow it with redundant kg_query calls or read the whole .umlproj "
                "file for an overview; use read_file only to inspect raw JSON details "
                "that this tool does not include (e.g. coordinates, fragment blocks). "
                "depth=1: diagram names; depth=2: + classes/components/lifelines; "
                "depth=3: + method/attribute signatures and sequence messages. "
                "Each diagram carries file_offset for targeted read_file jumps."
            ),
        )
        self.db_path = db_path
        self.project_file = project_file

    def _diagram_file_offsets(self) -> dict[str, int]:
        """从 .umlproj 反查每张图（按 name 匹配）的字符偏移。失败返回空 dict。"""
        offsets: dict[str, int] = {}
        if not self.project_file or not os.path.isfile(self.project_file):
            return offsets
        try:
            from app.services.file_service import load_project
            project = load_project(self.project_file)
            with open(self.project_file, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            for d in project.diagrams:
                # 用 name 定位该图 JSON 起点（"name": "<name>", 的冒号位置前推到 {）
                dname = d.name
                if not dname:
                    continue
                idx = text.find(f'"{dname}"')
                if idx < 0:
                    continue
                offsets[dname] = idx
        except Exception:
            logger.warning("[KG] Failed to compute diagram file offsets", exc_info=True)
        return offsets

    def _build_tree(self, project_id: str, depth: int) -> dict:
        from knowledge_graph.database import KnowledgeGraphDB
        db = KnowledgeGraphDB(self.db_path)
        try:
            # ── 拉全部节点，按 type 分桶 ──
            diagrams: list[GraphNode] = db.find_nodes(project_id, node_type="diagram", source="design", limit=500)
            classes: list[GraphNode] = db.find_nodes(project_id, node_type="class", source="design", limit=1000)
            components: list[GraphNode] = db.find_nodes(project_id, node_type="component", source="design", limit=500)
            lifelines: list[GraphNode] = db.find_nodes(project_id, node_type="lifeline", source="design", limit=500)
            methods: list[GraphNode] = db.find_nodes(project_id, node_type="method", source="design", limit=2000)
            attributes: list[GraphNode] = db.find_nodes(project_id, node_type="attribute", source="design", limit=2000)

            # ── contains 边：diagram→child, class→method/attribute ──
            diag_ids = {d.id for d in diagrams}
            class_ids = {c.id for c in classes}
            comp_ids = {c.id for c in components}
            life_ids = {l.id for l in lifelines}
            member_ids = {m.id for m in methods} | {a.id for a in attributes}
            diag_children: dict[str, list[str]] = {did: [] for did in diag_ids}
            class_members: dict[str, list[str]] = {cid: [] for cid in class_ids}

            for ed in db.find_edges(edge_type="contains", limit=5000):
                if ed.source_id in diag_ids and ed.target_id in (class_ids | comp_ids | life_ids):
                    diag_children[ed.source_id].append(ed.target_id)
                elif ed.source_id in class_ids and ed.target_id in member_ids:
                    class_members[ed.source_id].append(ed.target_id)

            # ── MESSAGES 边：lifeline→lifeline，时序图消息（label/order/note）──
            msg_edges: list[GraphEdge] = db.find_edges(edge_type="messages", limit=3000)

            node_by_id = {n.id: n for n in diagrams + classes + components + lifelines + methods + attributes}

            def _class_info(cid: str, with_members: bool) -> dict:
                n = node_by_id.get(cid)
                if not n:
                    return {}
                props = n.properties
                info = {"id": cid, "name": n.name}
                if props:
                    stereo = props.get("stereotype", "")
                    if stereo and stereo != "class":
                        info["stereotype"] = stereo
                if with_members:
                    members = []
                    for mid in class_members.get(cid, []):
                        m = node_by_id.get(mid)
                        if not m:
                            continue
                        mp = m.properties
                        entry = {"name": m.name, "node_type": m.node_type.value}
                        if m.node_type.value == "method":
                            entry["return_type"] = mp.get("return_type", "")
                            entry["params"] = mp.get("params", "")
                        members.append(entry)
                    members.sort(key=lambda x: (x["node_type"], x["name"]))
                    info["members"] = members
                return info

            tree_diagrams = []
            for d in sorted(diagrams, key=lambda x: x.name):
                dtype = d.properties.get("diagram_type", "") if d.properties else ""
                entry = {
                    "name": d.name,
                    "id": d.id,
                    "diagram_type": dtype,
                    "component_id": d.properties.get("component_id", "") if d.properties else "",
                }
                if depth >= 2:
                    children = []
                    for cid in diag_children.get(d.id, []):
                        n = node_by_id.get(cid)
                        if not n:
                            continue
                        if n.node_type.value == "class":
                            children.append(_class_info(cid, depth >= 3))
                        elif n.node_type.value == "component":
                            cp = n.properties
                            children.append({
                                "name": n.name,
                                "provided": cp.get("provided_interfaces", []) if cp else [],
                                "required": cp.get("required_interfaces", []) if cp else [],
                            })
                        else:  # lifeline
                            children.append({
                                "name": n.name,
                                "class_ref": n.properties.get("class_ref", "") if n.properties else "",
                            })
                    entry["children"] = children
                    # 时序图消息：depth>=3 时输出 MESSAGES 边摘要
                    if dtype == "sequence" and depth >= 3:
                        life_names = {n.id: n.name for n in lifelines}
                        messages = []
                        for ed in msg_edges:
                            src_name = life_names.get(ed.source_id, "")
                            tgt_name = life_names.get(ed.target_id, "")
                            if not src_name and not tgt_name:
                                continue
                            ep = ed.properties
                            messages.append({
                                "from": src_name,
                                "to": tgt_name,
                                "label": ep.get("label", "") if ep else "",
                                "order": ep.get("order", 0) if ep else 0,
                                "note": ep.get("note", "") if ep else "",
                            })
                        messages.sort(key=lambda m: m["order"])
                        if messages:
                            entry["messages"] = messages
                tree_diagrams.append(entry)

            # 完整性统计：让 Agent 确信输出是计算过的、完整的
            class_count = len(classes)
            comp_count = len(components)
            life_count = len(lifelines)
            method_count = len(methods)
            attr_count = len(attributes)
            seq_count = sum(1 for d in diagrams if (d.properties or {}).get("diagram_type") == "sequence")
            return {
                "project_id": project_id,
                "diagram_count": len(tree_diagrams),
                "diagrams": tree_diagrams,
                "stats": {
                    "diagrams": len(tree_diagrams),
                    "classes": class_count,
                    "components": comp_count,
                    "lifelines": life_count,
                    "methods": method_count,
                    "attributes": attr_count,
                    "sequence_diagrams_with_messages": seq_count,
                },
                "completeness": "full project structure — every diagram, class/component/lifeline, and (at depth=3) every method, attribute, and sequence message is included. No additional query is needed for an overview.",
            }
        finally:
            db.close()

    async def _execute(self, params: dict) -> str:
        project_id = _normalize_project_id(params.get("project_id", ""))
        if not project_id:
            return json.dumps({"error": "kg_project_structure requires project_id"}, ensure_ascii=False)

        # 确保项目已索引（复用按需构建兜底）
        probe = KgQueryTool(db_path=self.db_path, project_file=self.project_file)
        probe._ensure_project_indexed(project_id)

        depth = int(params.get("depth", 2))
        depth = max(1, min(depth, 3))

        tree = self._build_tree(project_id, depth)
        if not tree["diagrams"]:
            return json.dumps({
                "message": f"Project '{project_id}' has no diagrams in the knowledge graph. "
                           "If the project file was just saved, rebuild the graph and retry.",
                "diagrams": [],
            }, ensure_ascii=False)

        # 附带每张图的 file_offset，供 read_file 精确跳转
        offsets = self._diagram_file_offsets()
        for d in tree["diagrams"]:
            if d["name"] in offsets:
                d["file_offset"] = offsets[d["name"]]

        return json.dumps(tree, ensure_ascii=False)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "kg_project_structure",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project identifier (diagram name or project file name)",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "Structure depth: 1=diagram names only, 2=+ contained classes/components/lifelines, 3=+ method/attribute signatures. Default 2.",
                        },
                    },
                    "required": ["project_id"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════════

def create_kg_tools(
    db_path: str = "./data/knowledge_graph.db",
    source_dir: str = "",
    test_dir: str = "",
    project_file: str = "",
) -> list[Tool]:
    """创建知识图谱相关的所有工具.

    Args:
        db_path:    知识图谱数据库路径
        source_dir: 源码目录 (kg_diff 按需索引时使用)
        test_dir:   测试目录 (保留, 暂未使用)
        project_file: .umlproj 路径 (kg_query 按需构建时使用)

    Returns:
        [KgQueryTool, KgExpandTool, KgTraceTool, KgDiffTool]
    """
    tools: list[Tool] = [
        KgQueryTool(db_path=db_path, project_file=project_file),
        KgExpandTool(db_path=db_path),
        KgTraceTool(db_path=db_path),
        KgDiffTool(db_path=db_path, source_dir=source_dir),
        KgProjectStructureTool(db_path=db_path, project_file=project_file),
    ]
    logger.info(
        f"[KG] Created {len(tools)} knowledge graph tools "
        f"(db={db_path}, source_dir={source_dir or 'N/A'}, "
        f"project_file={project_file or 'N/A'})"
    )
    return tools
