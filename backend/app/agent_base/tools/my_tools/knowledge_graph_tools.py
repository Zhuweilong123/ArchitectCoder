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
from typing import Any, Optional

from app.agent_base.tools.base import Tool
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────

def _open_retriever(db_path: str):
    """延迟导入避免循环依赖."""
    from knowledge_graph.retriever import GraphRetriever
    return GraphRetriever(db_path)


def _serialize_node_result(r) -> dict:
    """序列化一个 NodeResult 为 Agent 友好的 dict."""
    return {
        "id": r.node.id,
        "node_type": r.node.node_type.value,
        "name": r.node.name,
        "source": r.node.source,
        "score": r.score,
        "depth": r.depth,
        "properties": r.node.properties,
    }


def _serialize_node_results(results) -> list[dict]:
    return [_serialize_node_result(r) for r in results]


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
                "在项目知识图谱中全文检索节点。"
                "支持按节点类型过滤（class/component/method/attribute/source_file/lifeline/interface/...）。"
                "返回匹配的节点列表，包含 BM25 相关性得分。"
                "使用场景：查找某个类、组件、方法是否存在；搜索特定功能在代码中的位置；"
                "了解项目中有哪些已设计好的类。"
            ),
        )
        self.db_path = db_path
        self.project_file = project_file

    def _ensure_project_indexed(self, project_id: str) -> bool:
        """确保 project_id 已索引；缺失时尝试从 project_file 按需构建.

        Returns:
            True 表示项目数据可查（原本就有或构建成功），否则 False。
        """
        try:
            from knowledge_graph.database import KnowledgeGraphDB
            db = KnowledgeGraphDB(self.db_path)
            existing = db.find_nodes(project_id, limit=1)
            db.close()
            if existing:
                return True
        except Exception:
            logger.warning("[KG] Failed to check project index", exc_info=True)
            return False

        if not self.project_file:
            return False
        try:
            import os as _os
            if not _os.path.isfile(self.project_file):
                logger.warning("[KG] project_file missing: %s", self.project_file)
                return False
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

            project_id = params.get("project_id", "")
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
                        nodes: list[dict] = []
                        for nt in node_types:
                            for node in db.find_nodes(
                                project_id, node_type=nt,
                                source=params.get("source"), limit=500,
                            ):
                                nodes.append(_serialize_node_result(
                                    NodeResult(node=node, score=0.0)
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
                            "message": "枚举节点失败，请重试或换非空 pattern 检索。",
                            "results": [],
                            "count": 0,
                        }, ensure_ascii=False)

                # 无 node_types 且 pattern 为空：给出引导而非报错
                return json.dumps({
                    "message": (
                        "kg_query 需要非空的 pattern 做全文检索；"
                        "或提供 node_types（如 'class,component,diagram'）以枚举该类型全部节点。"
                        "例如：pattern='diagram' 检索图，或 node_types='class' 枚举所有类。"
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

            serialized = _serialize_node_results(results)

            # 如果没有结果, 返回引导信息, 避免 Agent 原地盲目重试
            if not serialized:
                return json.dumps({
                    "message": (
                        f"在项目 '{project_id}' 中未找到与 '{pattern}' 匹配的节点。"
                        f"建议：1) 换更通用的关键词（如 'class'、'component'、"
                        f"图名中的核心名词）；2) 改用 kg_expand 从已知的 diagram/"
                        f"class 节点 ID 展开查看结构；3) 确认 project_id 是否正确。"
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
                            "description": "项目标识（可用图表名或项目文件名）",
                        },
                        "pattern": {
                            "type": "string",
                            "description": (
                                "查询文本，如 'User login method' 或具体类名/组件名。"
                                "用于全文检索；若留空将返回使用引导而非报错。"
                            ),
                        },
                        "node_types": {
                            "type": "string",
                            "description": (
                                "逗号分隔的节点类型过滤，如 'class,method,component'。"
                                "可用类型: project, diagram, class, component, lifeline, "
                                "source_file, test_file, method, attribute, interface。"
                                "注意: message/messages 是边类型不是节点，查询时序图消息"
                                "请用 lifeline 或 kg_expand。"
                                "不传则查所有类型。"
                            ),
                        },
                        "source": {
                            "type": "string",
                            "description": "来源过滤: 'design' (UML设计) | 'code' (源码) | 'test' (测试)。不传则查所有。",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "最大返回条数，默认20",
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
                "展开知识图谱中的节点关系，查看节点的邻域结构。"
                "支持 1-2 层深度展开，按边类型过滤，控制展开方向。"
                "使用场景：了解一个类有哪些方法/属性/父类/依赖关系；"
                "理解组件内部由哪些子组件和接口构成；"
                "查看某个节点的完整上下文。"
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

            serialized = _serialize_node_results(results)

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
                            "description": "逗号分隔的节点 ID 列表（从 kg_query 结果中获取）",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "展开深度: 1=直接邻居, 2=邻居的邻居。最大2。默认1。",
                        },
                        "edge_types": {
                            "type": "string",
                            "description": (
                                "逗号分隔的边类型过滤，如 'contains,inherits,implements'。"
                                "可用: contains, inherits, composition, aggregation, "
                                "association, realization, dependency, implements, "
                                "imports, tests, references, messages。"
                                "不传则展开所有边类型。"
                            ),
                        },
                        "direction": {
                            "type": "string",
                            "description": "展开方向: 'outgoing' (出边) | 'incoming' (入边) | 'both' (双向)。默认 'outgoing'。",
                        },
                        "max_nodes": {
                            "type": "integer",
                            "description": "最大返回节点数，默认50",
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
                "在知识图谱中追踪两个节点之间的所有依赖路径。"
                "使用场景：理解类之间的继承/依赖链（如 'User 如何间接依赖 Logger'）；"
                "分析从某组件到某接口的调用路径；排查循环依赖。"
                "返回所有路径，每条路径包含经过的节点序列和边类型。"
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
                        f"未找到从 '{params['source_id'][:12]}...' 到 "
                        f"'{params['target_id'][:12]}...' 的路径 "
                        f"(max_depth={params.get('max_depth', 10)})。"
                        f"可能两个节点之间没有直接或间接的连接。"
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
                            "description": "起点节点 ID（从 kg_query 结果中获取）",
                        },
                        "target_id": {
                            "type": "string",
                            "description": "终点节点 ID（从 kg_query 结果中获取）",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "最大搜索深度，默认10。大于10会被截断。",
                        },
                        "edge_types": {
                            "type": "string",
                            "description": "逗号分隔的边类型过滤。不传则使用所有边类型。",
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
                "对比 UML 设计与源码实现，找出差异。"
                "检测 3 类问题: missing_implementation (设计有但代码没实现)、"
                "extra_code (代码有但设计没定义)、mismatch (方法签名不一致)。"
                "使用场景：代码生成后验证完整性；重构前评估差距；"
                "确保设计与实现同步。"
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
                    f"{s['missing_implementations']} 个设计类未实现，"
                    f"调用 generate_code 生成代码"
                )
            if s["mismatches"] > 0:
                suggestions.append(
                    f"{s['mismatches']} 个方法签名不一致，"
                    f"需要同步设计或代码"
                )
            if s["extra_code"] > 0:
                suggestions.append(
                    f"{s['extra_code']} 个源码类在设计 UML 中不存在，"
                    f"可能需要反向工程到 UML 或删除冗余代码"
                )
            if s.get("no_coverage", 0) > 0:
                suggestions.append(
                    f"{s['no_coverage']} 个文件缺少测试覆盖"
                )
            if not suggestions:
                suggestions.append("设计与代码完全一致 ✅")

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
                            "description": "项目标识",
                        },
                        "source_dir": {
                            "type": "string",
                            "description": "源码目录路径。代码层缺失或源码变更时会自动重建；设置 force=true 可强制重建",
                        },
                        "force": {
                            "type": "boolean",
                            "description": "设为 true 时强制重建代码层，忽略变更检测（源码刚生成、未落盘修改 mtime 时有用）",
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
    ]
    logger.info(
        f"[KG] Created {len(tools)} knowledge graph tools "
        f"(db={db_path}, source_dir={source_dir or 'N/A'}, "
        f"project_file={project_file or 'N/A'})"
    )
    return tools
