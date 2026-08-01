"""
GraphBuilder — 从 UML 设计文件和 Python 源码构建知识图谱.

两种触发方式：
  声明式: file_service.save_project / save_diagram 后自动调用 build_from_project()
  探索式: Agent 通过工具调用 build_from_source_file() / build_from_source_dir()

构建策略:
  - 设计层: 从 UmlDiagram JSON 直接解析 (确定性, 无 LLM 参与)
  - 代码层: 从 Python AST 提取类 / 函数 / 导入 (确定性)
  - 测试覆盖: 从文件名模式推断 test_*.py → *.py
  - 增量重建: 声明式重建前清除旧的 source='design' 节点

Cross-reference:
  - lifeline.class_ref → REFERENCES 边指向 CLASS 节点
  - SourceFile 中的类名 → IMPLEMENTS 边指向同名的设计 CLASS 节点
"""

from __future__ import annotations

import ast
import difflib
import json
import logging
import os
import time
from typing import Any, Optional
from uuid import uuid4

from .database import KnowledgeGraphDB
from .models import (
    GraphNode, GraphEdge, EdgeType, NodeType,
    BuildStats, UML_RELATION_TO_EDGE, _utc_now,
)

logger = logging.getLogger(__name__)


# ── Content text synthesis (for FTS) ───────────────────────────

def _build_content_text(node_type: NodeType, name: str,
                        properties: dict) -> str:
    """合成 FTS 索引文本: name + node_type + 关键属性.

    用 jieba/bigram 预分词后以空格分隔 token，确保与查询端的
    tokenize_for_fts() 输出格式一致，中文 BM25 才能正确匹配。
    Fallback: jieba 不可用时直接返回空格分隔的原始文本。
    """
    parts = [name, node_type.value]

    if node_type == NodeType.CLASS:
        stereo = properties.get("stereotype", "")
        if stereo:
            parts.append(stereo)
        # ── 中文注释 — 关键！用户用中文搜索，note 提供中文关键词桥接 ──
        note = properties.get("note", "")
        if note:
            parts.append(note)
        for iface in properties.get("provided_interfaces", []):
            parts.append(str(iface))
        for iface in properties.get("required_interfaces", []):
            parts.append(str(iface))
        methods = properties.get("methods", [])
        if isinstance(methods, list):
            for m in methods:
                if isinstance(m, dict):
                    parts.append(m.get("name", ""))
                    parts.append(m.get("return_type", ""))
                else:
                    parts.append(str(m))
        attrs = properties.get("attributes", [])
        if isinstance(attrs, list):
            for a in attrs:
                if isinstance(a, dict):
                    parts.append(a.get("name", ""))
                    parts.append(a.get("type", ""))
                else:
                    parts.append(str(a))

    elif node_type == NodeType.METHOD:
        parts.append(properties.get("return_type", ""))
        parts.append(properties.get("params", ""))

    elif node_type == NodeType.ATTRIBUTE:
        parts.append(properties.get("type", ""))

    elif node_type == NodeType.COMPONENT:
        for iface in properties.get("provided_interfaces", []):
            parts.append(str(iface))
        for iface in properties.get("required_interfaces", []):
            parts.append(str(iface))

    elif node_type == NodeType.INTERFACE:
        parts.append(properties.get("direction", ""))
        parts.append(properties.get("component_id", ""))

    elif node_type == NodeType.SOURCE_FILE:
        parts.append(properties.get("path", ""))
        parts.append(properties.get("language", ""))
        for cn in properties.get("class_names", []):
            parts.append(str(cn))
        for imp in properties.get("import_names", []):
            parts.append(str(imp))

    elif node_type == NodeType.LIFELINE:
        parts.append(properties.get("class_ref", ""))

    # 去重
    raw_text = " ".join(set(p for p in parts if p))

    # ── jieba 预分词 ──
    try:
        from memory_system.tokenizer import tokenize_for_fts
        return tokenize_for_fts(raw_text)
    except ImportError:
        return raw_text


# ═══════════════════════════════════════════════════════════════
# GraphBuilder
# ═══════════════════════════════════════════════════════════════

class GraphBuilder:
    """知识图谱构建器.

    Usage:
        builder = GraphBuilder(db_path="./data/knowledge_graph.db")
        stats = builder.build_from_project(project, "my_project")
        stats = builder.build_from_source_file("app.py", "my_project")
        builder.close()
    """

    __slots__ = ("db_path", "_db")

    def __init__(self, db_path: str = "./data/knowledge_graph.db"):
        self.db_path = db_path
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

    # ── Public API: Design layer ──────────────────────────

    def build_from_project(self, project: Any,  # app.models.uml.Project
                           project_id: str,
                           filepath: str = "") -> BuildStats:
        """从 Project 对象构建整个项目的知识图谱.

        Args:
            project:    app.models.uml.Project 实例
            project_id: 项目标识 (用于隔离)
            filepath:   项目文件路径 (可选, 用于日志)

        Returns:
            构建统计.
        """
        t0 = time.monotonic()
        stats = BuildStats(source="declarative")

        # ── 清除旧的设计层节点 ──
        removed = self.db.delete_nodes_by_project_source(project_id, "design")
        stats.nodes_removed = removed

        # ── 创建 PROJECT 节点 ──
        project_node = GraphNode(
            id=self._make_id("project", project_id, project.name, "design"),
            node_type=NodeType.PROJECT,
            name=project.name,
            project_id=project_id,
            source="design",
            properties={
                "version": getattr(project, "version", "1.0"),
                "diagram_count": len(project.diagrams) if hasattr(project, "diagrams") else 0,
            },
        )
        project_node.content_text = _build_content_text(
            NodeType.PROJECT, project_node.name, project_node.properties,
        )
        self.db.upsert_node(project_node)
        stats.nodes_added += 1

        # ── 构建每张图 ──
        if not hasattr(project, "diagrams"):
            stats.elapsed_ms = (time.monotonic() - t0) * 1000
            return stats

        for i, diagram in enumerate(project.diagrams):
            diag_name = getattr(diagram, "name", f"Diagram_{i + 1}")
            diag_stats = self.build_from_diagram(
                diagram, project_id, diag_name, project_node.id,
            )
            stats.nodes_added += diag_stats.nodes_added
            stats.nodes_updated += diag_stats.nodes_updated
            stats.edges_added += diag_stats.edges_added

            # DIAGRAM → PROJECT (CONTAINS)
            diag_node = self._find_diagram_node(project_id, diag_name)
            if diag_node:
                edge = GraphEdge(
                    id=self._make_id("edge", project_node.id, diag_node.id, "contains"),
                    source_id=project_node.id,
                    target_id=diag_node.id,
                    edge_type=EdgeType.CONTAINS,
                    properties={"index": i},
                )
                self.db.upsert_edge(edge)
                stats.edges_added += 1

        # ── 跨图关联 ──
        cross_stats = self._build_cross_references(project, project_id)
        stats.edges_added += cross_stats.edges_added

        stats.elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"[KG] build_from_project: {stats}")
        return stats

    def rebuild_project(self, project: Any,  # app.models.uml.Project
                        project_id: str) -> BuildStats:
        """增量重建整个项目: 逐图更新, 而非全量删除重建.

        与 build_from_project() 的区别:
          - 只删除每张图下的旧实体, 不整库清空 design 层
          - 处理图删除场景: 移除当前 project 中已不存在的旧 diagram 节点
          - 重跑跨图引用 (references 边依赖所有图, 需在增量后重建)

        用于 save_project() 钩子, 大型工程改一张图不再触发全量重建.
        """
        t0 = time.monotonic()
        stats = BuildStats(source="declarative")

        # ── 确保 PROJECT 节点存在 (首存时创建, 已有则更新属性) ──
        project_node = GraphNode(
            id=self._make_id("project", project_id, project.name, "design"),
            node_type=NodeType.PROJECT,
            name=project.name,
            project_id=project_id,
            source="design",
            properties={
                "version": getattr(project, "version", "1.0"),
                "diagram_count": len(getattr(project, "diagrams", [])),
            },
        )
        project_node.content_text = _build_content_text(
            NodeType.PROJECT, project_node.name, project_node.properties,
        )
        self.db.upsert_node(project_node)
        stats.nodes_added += 1

        # ── 图删除场景: 移除当前 project 中已不存在的旧 diagram 节点 ──
        current_diag_names = {
            getattr(d, "name", f"Diagram_{i + 1}")
            for i, d in enumerate(getattr(project, "diagrams", []))
        }
        existing_diags = self.db.find_nodes(
            project_id, node_type="diagram", name="", source="design",
        )
        for old_diag in existing_diags:
            if old_diag.name not in current_diag_names:
                old_ids = self.db.get_descendant_ids(old_diag.id)
                to_del = old_ids + [old_diag.id]
                removed = self.db.delete_nodes_by_ids(to_del)
                stats.nodes_removed += removed
                logger.info(
                    f"[KG] rebuild_project: removed deleted diagram "
                    f"'{old_diag.name}' (-{removed})"
                )

        # ── 逐图增量重建 ──
        if not hasattr(project, "diagrams"):
            stats.elapsed_ms = (time.monotonic() - t0) * 1000
            return stats

        for i, diagram in enumerate(project.diagrams):
            diag_name = getattr(diagram, "name", f"Diagram_{i + 1}")
            d_stats = self.rebuild_diagram(
                diagram, project_id, diag_name, project_node.id,
            )
            stats.nodes_added += d_stats.nodes_added
            stats.nodes_removed += d_stats.nodes_removed
            stats.edges_added += d_stats.edges_added

            # 确保 PROJECT → DIAGRAM contains 边存在
            diag_node = self._find_diagram_node(project_id, diag_name)
            if diag_node:
                edge = GraphEdge(
                    id=self._make_id("edge", project_node.id, diag_node.id, "contains"),
                    source_id=project_node.id,
                    target_id=diag_node.id,
                    edge_type=EdgeType.CONTAINS,
                    properties={"index": i},
                )
                self.db.upsert_edge(edge)
                stats.edges_added += 1

        # ── 跨图引用重建 (references 边依赖所有图) ──
        cross_stats = self._build_cross_references(project, project_id)
        stats.edges_added += cross_stats.edges_added

        stats.elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"[KG] rebuild_project('{project_id}'): {stats}")
        return stats

    def rebuild_diagram(self, diagram: Any,  # app.models.uml.UmlDiagram
                        project_id: str,
                        diagram_name: str,
                        parent_project_id: str) -> BuildStats:
        """增量重建单张图的设计层节点和边.

        对比全量 build_from_project():
          - 只删除该图下的旧实体 (DIAGRAM → contains → * 递归后代), 不动其他图
          - 重新构建该图的 DIAGRAM 节点与实体层
          - 保留 PROJECT → DIAGRAM 的 contains 边 (由调用方维护, 见 rebuild_project)

        用于保存时按图增量更新, 避免大型工程全量重建.
        """
        # 1. 删除该图下的旧实体 (递归后代, 避免悬空)
        diag_node = self._find_diagram_node(project_id, diagram_name)
        removed = 0
        if diag_node:
            old_ids = self.db.get_descendant_ids(diag_node.id)
            if old_ids:
                removed = self.db.delete_nodes_by_ids(old_ids)

        # 2. 重新构建该图 (upsert DIAGRAM 节点 + 实体层)
        stats = self.build_from_diagram(
            diagram, project_id, diagram_name, parent_project_id,
        )
        stats.nodes_removed = removed
        logger.info(f"[KG] rebuild_diagram('{diagram_name}'): "
                    f"+{stats.nodes_added} nodes, -{removed} old, {stats.elapsed_ms:.0f}ms")
        return stats

    def build_from_diagram(self, diagram: Any,  # app.models.uml.UmlDiagram
                           project_id: str,
                           diagram_name: str,
                           parent_project_id: str) -> BuildStats:
        """从单张 UmlDiagram 构建图谱.

        Args:
            diagram:            app.models.uml.UmlDiagram 实例
            project_id:         项目标识
            diagram_name:       diagram.name
            parent_project_id:  PROJECT 节点 ID

        Returns:
            构建统计.
        """
        t0 = time.monotonic()
        stats = BuildStats(source="declarative")

        diagram_type = getattr(diagram, "diagram_type", "class")
        component_id = getattr(diagram, "component_id", "")

        # ── DIAGRAM 节点 ──
        diag_node = GraphNode(
            id=self._make_id("diagram", project_id, diagram_name, "design"),
            node_type=NodeType.DIAGRAM,
            name=diagram_name,
            project_id=project_id,
            source="design",
            properties={
                "diagram_type": diagram_type,
                "component_id": component_id,
                "version": getattr(diagram, "version", "1.0"),
            },
        )
        diag_node.content_text = _build_content_text(
            NodeType.DIAGRAM, diag_node.name, diag_node.properties,
        )
        self.db.upsert_node(diag_node)
        stats.nodes_added += 1

        # ── 按类型分派构建 ──
        if diagram_type == "class":
            cls_stats = self._build_class_layer(diagram, project_id, diag_node.id)
            stats.nodes_added += cls_stats.nodes_added
            stats.edges_added += cls_stats.edges_added
        elif diagram_type == "sequence":
            seq_stats = self._build_sequence_layer(diagram, project_id, diag_node.id)
            stats.nodes_added += seq_stats.nodes_added
            stats.edges_added += seq_stats.edges_added
        elif diagram_type == "component":
            comp_stats = self._build_component_layer(diagram, project_id, diag_node.id)
            stats.nodes_added += comp_stats.nodes_added
            stats.edges_added += comp_stats.edges_added

        stats.elapsed_ms = (time.monotonic() - t0) * 1000
        return stats

    # ── Public API: Code layer ────────────────────────────

    def build_from_source_file(self, filepath: str,
                               project_id: str,
                               content: Optional[str] = None) -> BuildStats:
        """从单个 Python 源码文件构建代码层节点.

        使用 AST 提取类 / 函数 / 导入, 并与设计层 CLASS 节点建立 IMPLEMENTS 边.
        """
        t0 = time.monotonic()
        stats = BuildStats(source="exploratory")

        if content is None:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(f"[KG] Cannot read {filepath}: {exc}")
                stats.elapsed_ms = (time.monotonic() - t0) * 1000
                return stats

        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            logger.warning(f"[KG] Syntax error in {filepath}: {exc}")
            stats.elapsed_ms = (time.monotonic() - t0) * 1000
            return stats

        filename = os.path.basename(filepath)
        rel_path = filepath

        # ── SOURCE_FILE 节点 ──
        class_names: list[str] = []
        import_names: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_names.append(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    import_names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                import_names.append(mod)

        source_node = GraphNode(
            id=self._make_id("source_file", project_id, filename, "code"),
            node_type=NodeType.SOURCE_FILE,
            name=filename,
            project_id=project_id,
            source="code",
            properties={
                "path": rel_path,
                "language": "python",
                "class_names": class_names,
                "import_names": import_names,
            },
        )
        source_node.content_text = _build_content_text(
            NodeType.SOURCE_FILE, source_node.name, source_node.properties,
        )
        self.db.upsert_node(source_node)
        stats.nodes_added += 1

        # ── METHOD / ATTRIBUTE / CLASS 节点 ──
        nodes_to_add: list[GraphNode] = []
        edges_to_add: list[GraphEdge] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                cls_stats = _extract_class_ast(
                    node, project_id, source_node.id, filename,
                )
                nodes_to_add.extend(cls_stats.get("nodes", []))
                edges_to_add.extend(cls_stats.get("edges", []))

                # IMPLEMENTS 边: 查找设计层同名的 CLASS 节点
                design_classes = self.db.find_nodes(
                    project_id, node_type="class", name=node.name, source="design",
                )
                # 改名容错: 精确匹配失败时, 用相似度/包含关系做 fallback
                if not design_classes:
                    design_classes = _find_design_classes_fuzzy(
                        self.db, project_id, node.name,
                    )
                for dc in design_classes:
                    match_method = (
                        "exact_name"
                        if dc.name == node.name
                        else "fuzzy_name"
                    )
                    edges_to_add.append(GraphEdge(
                        id=self._make_id("edge", source_node.id, dc.id, "implements"),
                        source_id=source_node.id,
                        target_id=dc.id,
                        edge_type=EdgeType.IMPLEMENTS,
                        properties={"match_method": match_method},
                    ))

            elif isinstance(node, ast.FunctionDef):
                # 文件级函数 → METHOD 节点
                method_node = GraphNode(
                    id=self._make_id("method", project_id, f"{filename}:{node.name}", "code"),
                    node_type=NodeType.METHOD,
                    name=node.name,
                    project_id=project_id,
                    source="code",
                    properties={
                        "return_type": _get_return_annotation(node),
                        "params": _get_params_str(node),
                        "visibility": "+",
                        "is_static": False,
                        "is_abstract": False,
                        "filename": filename,
                        "parent_class": "",
                    },
                )
                method_node.content_text = _build_content_text(
                    NodeType.METHOD, method_node.name, method_node.properties,
                )
                nodes_to_add.append(method_node)
                edges_to_add.append(GraphEdge(
                    id=self._make_id("edge", source_node.id, method_node.id, "contains"),
                    source_id=source_node.id,
                    target_id=method_node.id,
                    edge_type=EdgeType.CONTAINS,
                ))

        # 批量 upsert
        if nodes_to_add:
            self.db.upsert_nodes_batch(nodes_to_add)
            stats.nodes_added += len(nodes_to_add)
        if edges_to_add:
            self.db.upsert_edges_batch(edges_to_add)
            stats.edges_added += len(edges_to_add)

        stats.elapsed_ms = (time.monotonic() - t0) * 1000
        return stats

    def build_from_source_dir(self, dir_path: str,
                              project_id: str) -> BuildStats:
        """从整个源码目录递归构建代码层节点."""
        t0 = time.monotonic()
        agg = BuildStats(source="exploratory")

        if not os.path.isdir(dir_path):
            logger.warning(f"[KG] Source dir not found: {dir_path}")
            agg.elapsed_ms = (time.monotonic() - t0) * 1000
            return agg

        # 递归收集所有 .py（支持包/子目录结构），跳过 __init__ 外的隐藏文件
        py_files: list[str] = []
        for root, _dirs, files in os.walk(dir_path):
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.join(root, f))

        for py_file in py_files:
            stats = self.build_from_source_file(py_file, project_id)
            agg.nodes_added += stats.nodes_added
            agg.edges_added += stats.edges_added

        agg.elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"[KG] build_from_source_dir({dir_path}): {agg}")
        return agg

    def rebuild_code_layer(self, project_id: str, source_dir: str) -> BuildStats:
        """重建代码层: 先清后建 (不碰设计层).

        与设计层对称的增量入口, 供 diff() 检测到源码变更时调用.
        """
        removed = self.db.delete_nodes_by_project_source(project_id, "code")
        stats = self.build_from_source_dir(source_dir, project_id)
        stats.nodes_removed = removed
        logger.info(f"[KG] rebuild_code_layer({project_id}): {stats}")
        return stats

    def build_test_coverage(self, test_dir: str,
                            project_id: str) -> BuildStats:
        """扫描 test_*.py 文件，推断测试覆盖关系."""
        t0 = time.monotonic()
        stats = BuildStats(source="exploratory")

        if not os.path.isdir(test_dir):
            logger.warning(f"[KG] Test dir not found: {test_dir}")
            stats.elapsed_ms = (time.monotonic() - t0) * 1000
            return stats

        for fname in os.listdir(test_dir):
            if not fname.endswith(".py") or not os.path.isfile(os.path.join(test_dir, fname)):
                continue

            fpath = os.path.join(test_dir, fname)

            # 推断被测试的源文件
            covered: list[str] = []
            if fname.startswith("test_"):
                covered.append(fname[5:])  # test_user.py → user.py
            elif fname.endswith("_test.py"):
                covered.append(fname[:-8] + ".py")

            # 从 AST import 中进一步推断
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        if mod and not mod.startswith("."):
                            covered.append(mod + ".py")
            except (SyntaxError, OSError):
                pass

            # 创建 TEST_FILE 节点
            test_node = GraphNode(
                id=self._make_id("test_file", project_id, fname, "test"),
                node_type=NodeType.TEST_FILE,
                name=fname,
                project_id=project_id,
                source="test",
                properties={
                    "path": fpath,
                    "language": "python",
                    "covers": list(set(covered)),
                },
            )
            test_node.content_text = _build_content_text(
                NodeType.TEST_FILE, test_node.name, test_node.properties,
            )
            self.db.upsert_node(test_node)
            stats.nodes_added += 1

            # TESTS 边
            for cov in set(covered):
                source_files = self.db.find_nodes(
                    project_id, node_type="source_file", name=cov, source="code",
                )
                for sf in source_files:
                    edge = GraphEdge(
                        id=self._make_id("edge", test_node.id, sf.id, "tests"),
                        source_id=test_node.id,
                        target_id=sf.id,
                        edge_type=EdgeType.TESTS,
                        properties={"inferred": True},
                    )
                    self.db.upsert_edge(edge)
                    stats.edges_added += 1

        stats.elapsed_ms = (time.monotonic() - t0) * 1000
        return stats

    def build_incremental(self, project_id: str,
                          design: Any = None,
                          source_dir: str = "",
                          test_dir: str = "") -> BuildStats:
        """一站式增量构建: 设计 + 源码 + 测试.

        用于 Agent 对话开头确保 KG 数据是最新的.
        代码层只追加 build, 不删除已有节点.
        """
        t0 = time.monotonic()
        agg = BuildStats(source="exploratory")

        if design is not None:
            ds = self.build_from_project(design, project_id)
            agg.nodes_added += ds.nodes_added
            agg.nodes_removed += ds.nodes_removed
            agg.edges_added += ds.edges_added

        # 检查是否需要构建代码层 (避免重复)
        existing_code = self.db.find_nodes(project_id, source="code", limit=1)
        if not existing_code and source_dir:
            ss = self.build_from_source_dir(source_dir, project_id)
            agg.nodes_added += ss.nodes_added
            agg.edges_added += ss.edges_added

        if test_dir:
            ts = self.build_test_coverage(test_dir, project_id)
            agg.nodes_added += ts.nodes_added
            agg.edges_added += ts.edges_added

        agg.elapsed_ms = (time.monotonic() - t0) * 1000
        return agg

    # ── Internal: Class diagram layer ─────────────────────

    def _build_class_layer(self, diagram: Any, project_id: str,
                           parent_diag_id: str) -> BuildStats:
        """构建类图: CLASS + ATTRIBUTE + METHOD 节点 + 关系边."""
        stats = BuildStats(source="declarative")
        nodes_to_add: list[GraphNode] = []
        edges_to_add: list[GraphEdge] = []

        # 收集所有类 ID (在本图中)
        class_nodes: dict[str, GraphNode] = {}

        for cls in getattr(diagram, "classes", []):
            cls_id = getattr(cls, "id", self._make_id("class", project_id, cls.name, "design"))
            stereo = getattr(cls, "stereotype", None)
            stereo_val = stereo.value if hasattr(stereo, "value") else str(stereo or "class")

            # 方法名列表 (用于 content_text)
            method_names: list[dict] = []
            methods = getattr(cls, "methods", [])
            for m in methods:
                method_names.append({
                    "name": getattr(m, "name", ""),
                    "return_type": getattr(m, "return_type", ""),
                    "params": getattr(m, "params", ""),
                    "visibility": getattr(m, "visibility", "+").value
                        if hasattr(getattr(m, "visibility", "+"), "value") else "+",
                    "is_static": getattr(m, "is_static", False),
                    "is_abstract": getattr(m, "is_abstract", False),
                })

            # 属性名列表
            attr_names: list[dict] = []
            attributes = getattr(cls, "attributes", [])
            for a in attributes:
                attr_names.append({
                    "name": getattr(a, "name", ""),
                    "type": getattr(a, "type", ""),
                    "visibility": getattr(a, "visibility", "+").value
                        if hasattr(getattr(a, "visibility", "+"), "value") else "+",
                    "is_static": getattr(a, "is_static", False),
                    "default_value": getattr(a, "default_value", None),
                })

            provided = list(getattr(cls, "provided_interfaces", []))
            required = list(getattr(cls, "required_interfaces", []))

            cls_node = GraphNode(
                id=cls_id,
                node_type=NodeType.CLASS,
                name=getattr(cls, "name", ""),
                project_id=project_id,
                source="design",
                properties={
                    "stereotype": stereo_val,
                    "visibility": "+",
                    "provided_interfaces": provided,
                    "required_interfaces": required,
                    "is_abstract": stereo_val == "abstract",
                    "note": getattr(cls, "note", ""),
                    "methods": method_names,
                    "attributes": attr_names,
                },
            )
            cls_node.content_text = _build_content_text(
                NodeType.CLASS, cls_node.name, cls_node.properties,
            )
            nodes_to_add.append(cls_node)
            class_nodes[cls_node.id] = cls_node

            # DIAGRAM → CLASS (contains)
            edges_to_add.append(GraphEdge(
                id=self._make_id("edge", parent_diag_id, cls_id, "contains"),
                source_id=parent_diag_id,
                target_id=cls_id,
                edge_type=EdgeType.CONTAINS,
            ))

            # CLASS → METHOD (contains)
            for m in methods:
                m_name = getattr(m, "name", "")
                m_id = self._make_id("method", project_id, f"{cls.name}.{m_name}", "design")
                m_node = GraphNode(
                    id=m_id,
                    node_type=NodeType.METHOD,
                    name=m_name,
                    project_id=project_id,
                    source="design",
                    properties={
                        "return_type": getattr(m, "return_type", ""),
                        "params": getattr(m, "params", ""),
                        "visibility": getattr(m, "visibility", "+").value
                            if hasattr(getattr(m, "visibility", "+"), "value") else "+",
                        "is_static": getattr(m, "is_static", False),
                        "is_abstract": getattr(m, "is_abstract", False),
                    },
                )
                m_node.content_text = _build_content_text(
                    NodeType.METHOD, m_node.name, m_node.properties,
                )
                nodes_to_add.append(m_node)
                edges_to_add.append(GraphEdge(
                    id=self._make_id("edge", cls_id, m_id, "contains"),
                    source_id=cls_id,
                    target_id=m_id,
                    edge_type=EdgeType.CONTAINS,
                ))

            # CLASS → ATTRIBUTE (contains)
            for a in attributes:
                a_name = getattr(a, "name", "")
                a_id = self._make_id("attribute", project_id, f"{cls.name}.{a_name}", "design")
                a_node = GraphNode(
                    id=a_id,
                    node_type=NodeType.ATTRIBUTE,
                    name=a_name,
                    project_id=project_id,
                    source="design",
                    properties={
                        "type": getattr(a, "type", ""),
                        "visibility": getattr(a, "visibility", "+").value
                            if hasattr(getattr(a, "visibility", "+"), "value") else "+",
                        "is_static": getattr(a, "is_static", False),
                        "default_value": getattr(a, "default_value", None),
                    },
                )
                a_node.content_text = _build_content_text(
                    NodeType.ATTRIBUTE, a_node.name, a_node.properties,
                )
                nodes_to_add.append(a_node)
                edges_to_add.append(GraphEdge(
                    id=self._make_id("edge", cls_id, a_id, "contains"),
                    source_id=cls_id,
                    target_id=a_id,
                    edge_type=EdgeType.CONTAINS,
                ))

        # ── 类间关系 ──
        for rel in getattr(diagram, "relations", []):
            src_id = getattr(rel, "source", "")
            tgt_id = getattr(rel, "target", "")
            rel_type = getattr(rel, "type", None)
            rel_type_str = rel_type.value if hasattr(rel_type, "value") else str(rel_type or "association")

            edge_type = UML_RELATION_TO_EDGE.get(rel_type_str, EdgeType.ASSOCIATION)

            edges_to_add.append(GraphEdge(
                id=getattr(rel, "id", self._make_id("edge", src_id, tgt_id, rel_type_str)),
                source_id=src_id,
                target_id=tgt_id,
                edge_type=edge_type,
                properties={
                    "multiplicity_source": getattr(rel, "multiplicity_source", ""),
                    "multiplicity_target": getattr(rel, "multiplicity_target", ""),
                    "role_name": getattr(rel, "role_name", ""),
                    "note": getattr(rel, "note", ""),
                },
            ))

        # 批量写入
        if nodes_to_add:
            self.db.upsert_nodes_batch(nodes_to_add)
            stats.nodes_added = len(nodes_to_add)
        if edges_to_add:
            self.db.upsert_edges_batch(edges_to_add)
            stats.edges_added = len(edges_to_add)

        return stats

    # ── Internal: Sequence diagram layer ──────────────────

    def _build_sequence_layer(self, diagram: Any, project_id: str,
                              parent_diag_id: str) -> BuildStats:
        """构建时序图: LIFELINE + MESSAGES 边."""
        stats = BuildStats(source="declarative")
        nodes_to_add: list[GraphNode] = []
        edges_to_add: list[GraphEdge] = []

        lifeline_id_map: dict[str, str] = {}  # lifeline.id → GraphNode.id

        for ll in getattr(diagram, "lifelines", []):
            ll_orig_id = getattr(ll, "id", "")
            ll_name = getattr(ll, "name", "")
            ll_node_id = self._make_id("lifeline", project_id, ll_name, "design")
            lifeline_id_map[ll_orig_id] = ll_node_id

            ll_node = GraphNode(
                id=ll_node_id,
                node_type=NodeType.LIFELINE,
                name=ll_name,
                project_id=project_id,
                source="design",
                properties={
                    "class_ref": getattr(ll, "class_ref", ""),
                },
            )
            ll_node.content_text = _build_content_text(
                NodeType.LIFELINE, ll_node.name, ll_node.properties,
            )
            nodes_to_add.append(ll_node)

            # DIAGRAM → LIFELINE
            edges_to_add.append(GraphEdge(
                id=self._make_id("edge", parent_diag_id, ll_node_id, "contains"),
                source_id=parent_diag_id,
                target_id=ll_node_id,
                edge_type=EdgeType.CONTAINS,
            ))

        # ── MESSAGES ──
        for msg in getattr(diagram, "messages", []):
            from_id = lifeline_id_map.get(getattr(msg, "from_lifeline", ""), "")
            to_id = lifeline_id_map.get(getattr(msg, "to_lifeline", ""), "")
            if from_id and to_id:
                edges_to_add.append(GraphEdge(
                    id=getattr(msg, "id", self._make_id("edge", from_id, to_id, "messages")),
                    source_id=from_id,
                    target_id=to_id,
                    edge_type=EdgeType.MESSAGES,
                    properties={
                        "label": getattr(msg, "label", ""),
                        "type": getattr(msg, "type", "sync"),
                        "order": getattr(msg, "order", 0),
                        "note": getattr(msg, "note", ""),
                    },
                ))

        # 批量写入
        if nodes_to_add:
            self.db.upsert_nodes_batch(nodes_to_add)
            stats.nodes_added = len(nodes_to_add)
        if edges_to_add:
            self.db.upsert_edges_batch(edges_to_add)
            stats.edges_added = len(edges_to_add)

        return stats

    # ── Internal: Component diagram layer ─────────────────

    def _build_component_layer(self, diagram: Any, project_id: str,
                               parent_diag_id: str) -> BuildStats:
        """构建组件图: COMPONENT + INTERFACE 节点 + 关系边."""
        stats = BuildStats(source="declarative")
        nodes_to_add: list[GraphNode] = []
        edges_to_add: list[GraphEdge] = []

        comp_nodes: dict[str, str] = {}  # comp.id → GraphNode.id

        for comp in getattr(diagram, "components", []):
            comp_orig_id = getattr(comp, "id", "")
            comp_name = getattr(comp, "name", "")
            comp_node_id = self._make_id("component", project_id, comp_name, "design")
            comp_nodes[comp_orig_id] = comp_node_id

            comp_node = GraphNode(
                id=comp_node_id,
                node_type=NodeType.COMPONENT,
                name=comp_name,
                project_id=project_id,
                source="design",
                properties={
                    "parent_id": getattr(comp, "parent_id", ""),
                    "provided_interfaces": list(getattr(comp, "provided_interfaces", [])),
                    "required_interfaces": list(getattr(comp, "required_interfaces", [])),
                },
            )
            comp_node.content_text = _build_content_text(
                NodeType.COMPONENT, comp_node.name, comp_node.properties,
            )
            nodes_to_add.append(comp_node)

            # DIAGRAM → COMPONENT
            edges_to_add.append(GraphEdge(
                id=self._make_id("edge", parent_diag_id, comp_node_id, "contains"),
                source_id=parent_diag_id,
                target_id=comp_node_id,
                edge_type=EdgeType.CONTAINS,
            ))

            # INTERFACE 节点
            for iface in comp_node.properties.get("provided_interfaces", []):
                iface_node = GraphNode(
                    id=self._make_id("interface", project_id, f"{comp_name}.{iface}", "design"),
                    node_type=NodeType.INTERFACE,
                    name=iface,
                    project_id=project_id,
                    source="design",
                    properties={
                        "direction": "provided",
                        "component_id": comp_node_id,
                    },
                )
                iface_node.content_text = _build_content_text(
                    NodeType.INTERFACE, iface_node.name, iface_node.properties,
                )
                nodes_to_add.append(iface_node)
                edges_to_add.append(GraphEdge(
                    id=self._make_id("edge", comp_node_id, iface_node.id, "contains"),
                    source_id=comp_node_id,
                    target_id=iface_node.id,
                    edge_type=EdgeType.CONTAINS,
                    properties={"direction": "provided"},
                ))

            for iface in comp_node.properties.get("required_interfaces", []):
                iface_node = GraphNode(
                    id=self._make_id("interface", project_id, f"{comp_name}.{iface}", "design"),
                    node_type=NodeType.INTERFACE,
                    name=iface,
                    project_id=project_id,
                    source="design",
                    properties={
                        "direction": "required",
                        "component_id": comp_node_id,
                    },
                )
                iface_node.content_text = _build_content_text(
                    NodeType.INTERFACE, iface_node.name, iface_node.properties,
                )
                nodes_to_add.append(iface_node)
                edges_to_add.append(GraphEdge(
                    id=self._make_id("edge", comp_node_id, iface_node.id, "contains"),
                    source_id=comp_node_id,
                    target_id=iface_node.id,
                    edge_type=EdgeType.CONTAINS,
                    properties={"direction": "required"},
                ))

        # ── COMPONENT 父子嵌套 ──
        for comp in getattr(diagram, "components", []):
            parent_id = getattr(comp, "parent_id", "")
            if parent_id and parent_id in comp_nodes:
                child_id = comp_nodes.get(getattr(comp, "id", ""), "")
                parent_gn_id = comp_nodes[parent_id]
                if child_id and parent_gn_id:
                    edges_to_add.append(GraphEdge(
                        id=self._make_id("edge", parent_gn_id, child_id, "contains"),
                        source_id=parent_gn_id,
                        target_id=child_id,
                        edge_type=EdgeType.CONTAINS,
                    ))

        # ── COMPONENT 关系 ──
        for rel in getattr(diagram, "comp_relations", []):
            src = comp_nodes.get(getattr(rel, "source", ""), "")
            tgt = comp_nodes.get(getattr(rel, "target", ""), "")
            if src and tgt:
                rel_type = getattr(rel, "type", "dependency")
                edge_type = EdgeType.DEPENDENCY if rel_type == "dependency" else EdgeType.ASSOCIATION
                edges_to_add.append(GraphEdge(
                    id=getattr(rel, "id", self._make_id("edge", src, tgt, rel_type)),
                    source_id=src,
                    target_id=tgt,
                    edge_type=edge_type,
                ))

        # 批量写入
        if nodes_to_add:
            self.db.upsert_nodes_batch(nodes_to_add)
            stats.nodes_added = len(nodes_to_add)
        if edges_to_add:
            self.db.upsert_edges_batch(edges_to_add)
            stats.edges_added = len(edges_to_add)

        return stats

    # ── Cross-diagram references ──────────────────────────

    def _build_cross_references(self, project: Any,
                                project_id: str) -> BuildStats:
        """构建跨图关联:
        - lifeline.class_ref → REFERENCES 边指向 CLASS 节点
        - diagram.component_id → REFERENCES 边指向 COMPONENT 节点
        """
        stats = BuildStats(source="declarative")

        for diagram in getattr(project, "diagrams", []):
            diag_type = getattr(diagram, "diagram_type", "class")

            if diag_type == "sequence":
                for ll in getattr(diagram, "lifelines", []):
                    class_ref = getattr(ll, "class_ref", "")
                    if class_ref:
                        # 查找设计层 CLASS 节点
                        class_nodes = self.db.find_nodes(
                            project_id, node_type="class", name="", source="design",
                        )
                        # 用 class_ref 当 class name 或 class id 查
                        design_classes = [
                            n for n in class_nodes
                            if n.name == class_ref or n.id == class_ref
                        ]
                        # 如果有 lifeline.name 匹配也行
                        if not design_classes:
                            ll_name = getattr(ll, "name", "")
                            design_classes = [
                                n for n in class_nodes
                                if n.name == ll_name or n.name == class_ref
                            ]
                        ll_node = self._find_lifeline_node(project_id, getattr(ll, "name", ""))
                        for dc in design_classes:
                            if ll_node:
                                edge = GraphEdge(
                                    id=self._make_id("edge", ll_node.id, dc.id, "references"),
                                    source_id=ll_node.id,
                                    target_id=dc.id,
                                    edge_type=EdgeType.REFERENCES,
                                    properties={"via": "class_ref"},
                                )
                                self.db.upsert_edge(edge)
                                stats.edges_added += 1

            elif diag_type == "component":
                component_id = getattr(diagram, "component_id", "")
                if component_id and component_id in _get_comp_ids(project):
                    diag_name = getattr(diagram, "name", "")
                    diag_node = self._find_diagram_node(project_id, diag_name)
                    comp_nodes = self.db.find_nodes(
                        project_id, node_type="component", name="", source="design",
                    )
                    matching = [n for n in comp_nodes if n.id == component_id]
                    for cn in matching:
                        if diag_node:
                            edge = GraphEdge(
                                id=self._make_id("edge", diag_node.id, cn.id, "references"),
                                source_id=diag_node.id,
                                target_id=cn.id,
                                edge_type=EdgeType.REFERENCES,
                                properties={"via": "component_id"},
                            )
                            self.db.upsert_edge(edge)
                            stats.edges_added += 1

        return stats

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _make_id(prefix: str, project_id: str, name: str,
                 source: str) -> str:
        """生成确定性节点 ID (基于自然键 hash)."""
        key = f"{project_id}|{source}|{name}"
        # 取前 8 位 hash 保证短 ID
        import hashlib
        h = hashlib.md5(key.encode()).hexdigest()[:8]
        return f"{prefix}_{h}"

    def _find_diagram_node(self, project_id: str,
                           name: str) -> Optional[GraphNode]:
        nodes = self.db.find_nodes(
            project_id, node_type="diagram", name=name, source="design",
        )
        return nodes[0] if nodes else None

    def _find_lifeline_node(self, project_id: str,
                            name: str) -> Optional[GraphNode]:
        nodes = self.db.find_nodes(
            project_id, node_type="lifeline", name=name, source="design",
        )
        return nodes[0] if nodes else None


# ── Name matching helpers ──────────────────────────────────────

def _name_similarity(a: str, b: str) -> float:
    """两个名称的相似度 (0.0-1.0).

    - 先忽略大小写与下划线
    - SequenceMatcher 比值
    - 一方包含另一方 (如 UserServiceImpl 包含 User) 额外加分
    """
    norm = lambda s: s.lower().replace("_", "")
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    base = difflib.SequenceMatcher(None, na, nb).ratio()
    if na in nb or nb in na:
        base += 0.15
    return min(base, 1.0)


def _find_design_classes_fuzzy(db: KnowledgeGraphDB, project_id: str,
                               name: str) -> list[GraphNode]:
    """精确匹配失败时, 按相似度查找设计层 CLASS 节点 (改名容错).

    阈值 0.8: 覆盖大小写变化、前后缀增删 (如 UserService → UserServiceImpl).
    为避免误配, 只有严格大于精确名且得分达标才返回.
    """
    candidates = db.find_nodes(
        project_id, node_type="class", name="", source="design",
    )
    best = [
        n for n in candidates
        if n.name != name and _name_similarity(name, n.name) >= 0.8
    ]
    # 按相似度降序, 只取最高分 (避免一个源码类匹配多个设计类)
    if not best:
        return []
    best.sort(key=lambda n: _name_similarity(name, n.name), reverse=True)
    top_score = _name_similarity(name, best[0].name)
    return [n for n in best if _name_similarity(name, n.name) == top_score]


# ── AST extraction helpers ──────────────────────────────────────

def _extract_class_ast(
    cls_node: ast.ClassDef,
    project_id: str,
    parent_file_id: str,
    filename: str,
) -> dict[str, list]:
    """从一个 ast.ClassDef 提取子节点和边."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    cls_id = GraphBuilder._make_id(
        "class", project_id, f"{filename}:{cls_node.name}", "code",
    )

    # 基类名 (用于继承推断)
    bases = [
        _get_name(b) for b in cls_node.bases
        if isinstance(b, (ast.Name, ast.Attribute))
    ]

    # ── CLASS 节点本身 (此前缺失，导致 code 层无 class、无法定位文件) ──
    method_names: list[str] = []
    attr_names: list[str] = []
    cls_node_obj = GraphNode(
        id=cls_id,
        node_type=NodeType.CLASS,
        name=cls_node.name,
        project_id=project_id,
        source="code",
        properties={
            "bases": bases,
            "filename": filename,
            "methods": [],
            "attributes": [],
        },
    )
    cls_node_obj.content_text = _build_content_text(
        NodeType.CLASS, cls_node_obj.name, cls_node_obj.properties,
    )
    nodes.append(cls_node_obj)

    for item in cls_node.body:
        if isinstance(item, ast.FunctionDef):
            method_names.append(item.name)
            m_id = GraphBuilder._make_id(
                "method", project_id, f"{filename}:{cls_node.name}.{item.name}", "code",
            )
            m_node = GraphNode(
                id=m_id,
                node_type=NodeType.METHOD,
                name=item.name,
                project_id=project_id,
                source="code",
                properties={
                    "return_type": _get_return_annotation(item),
                    "params": _get_params_str(item),
                    "visibility": "+",  # AST 无法区分 public/private
                    "is_static": _is_static(item),
                    "is_abstract": _is_abstract(item),
                    "filename": filename,
                    "parent_class": cls_node.name,
                },
            )
            m_node.content_text = _build_content_text(
                NodeType.METHOD, m_node.name, m_node.properties,
            )
            nodes.append(m_node)
            edges.append(GraphEdge(
                id=GraphBuilder._make_id("edge", cls_id, m_id, "contains"),
                source_id=cls_id,
                target_id=m_id,
                edge_type=EdgeType.CONTAINS,
            ))

        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            attr_names.append(item.target.id)
            a_id = GraphBuilder._make_id(
                "attribute", project_id, f"{filename}:{cls_node.name}.{item.target.id}", "code",
            )
            a_node = GraphNode(
                id=a_id,
                node_type=NodeType.ATTRIBUTE,
                name=item.target.id,
                project_id=project_id,
                source="code",
                properties={
                    "type": _get_annotation_str(item.annotation),
                    "visibility": "+",
                    "is_static": False,
                    "default_value": None,
                    "filename": filename,
                    "parent_class": cls_node.name,
                },
            )
            a_node.content_text = _build_content_text(
                NodeType.ATTRIBUTE, a_node.name, a_node.properties,
            )
            nodes.append(a_node)
            edges.append(GraphEdge(
                id=GraphBuilder._make_id("edge", cls_id, a_id, "contains"),
                source_id=cls_id,
                target_id=a_id,
                edge_type=EdgeType.CONTAINS,
            ))

        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    attr_names.append(target.id)

    # SOURCE_FILE → CLASS (contains, code layer)
    edges.append(GraphEdge(
        id=GraphBuilder._make_id("edge", parent_file_id, cls_id, "contains"),
        source_id=parent_file_id,
        target_id=cls_id,
        edge_type=EdgeType.CONTAINS,
    ))

    return {"nodes": nodes, "edges": edges}


def _get_name(node: ast.expr) -> str:
    """从 AST 表达式提取名称."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _get_name(node.value) + "." + node.attr
    return ""


def _get_return_annotation(func: ast.FunctionDef) -> str:
    """提取函数返回类型注解."""
    if func.returns:
        return _get_annotation_str(func.returns)
    return ""


def _get_annotation_str(node: ast.expr | None) -> str:
    """AST 注解转字符串."""
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Subscript):
        return _get_annotation_str(node.value)
    return ""


def _get_params_str(func: ast.FunctionDef) -> str:
    """提取函数参数列表字符串."""
    parts: list[str] = []
    for arg in func.args.args:
        p = arg.arg
        if arg.annotation:
            p += ": " + _get_annotation_str(arg.annotation)
        parts.append(p)
    return ", ".join(parts)


def _is_static(func: ast.FunctionDef) -> bool:
    """判断是否为 staticmethod."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "staticmethod":
            return True
    return False


def _is_abstract(func: ast.FunctionDef) -> bool:
    """判断是否为 abstractmethod."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "abstractmethod":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == "abstractmethod":
            return True
    return False


def _get_comp_ids(project: Any) -> set[str]:
    """从项目中提取所有组件 ID."""
    ids: set[str] = set()
    for d in getattr(project, "diagrams", []):
        if getattr(d, "diagram_type", "") == "component":
            for c in getattr(d, "components", []):
                cid = getattr(c, "id", "")
                if cid:
                    ids.add(cid)
    return ids
