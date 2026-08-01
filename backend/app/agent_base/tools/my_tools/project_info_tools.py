"""项目信息工具 — 让 Agent 按需获取项目上下文，无需每轮注入 prompt。

ProjectInfoTool 零参数，调用时返回当前项目的文件级信息：
设计文件路径/大小、源码目录文件列表、测试目录文件列表、增量 vs 全新开发策略。

深层设计元素（类/组件/接口/图）不在此工具范围，由 kg_query / kg_expand 等
知识图谱工具负责，避免首轮对话加载过大上下文。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from app.agent_base.tools.base import Tool, ToolParameter


def build_project_context(
    source_dir: str,
    test_dir: str,
    project_file: str,
) -> str:
    """构建项目上下文摘要（文件路径信息，不包含文件内容）。"""
    lines: list[str] = []

    # ── 设计文件 ──
    if project_file and os.path.isfile(project_file):
        fname = os.path.basename(project_file)
        fsize = os.path.getsize(project_file)
        lines.append(f"- 设计文件: {project_file} ({fname}, {fsize} bytes)")
    elif project_file:
        lines.append(f"- 设计文件: {project_file} (未保存或路径无效)")
    else:
        lines.append("- 设计文件: 未保存")

    # ── 源码目录 ──
    if source_dir and os.path.isdir(source_dir):
        try:
            files = [f for f in os.listdir(source_dir) if os.path.isfile(os.path.join(source_dir, f))]
            py_files = [f for f in files if f.endswith('.py')]
            lines.append(f"- 源码目录: {source_dir} ({len(files)} 文件, {len(py_files)} 个 .py)")
            if py_files:
                sample = py_files[:20]
                lines.append(f"  源码文件: {', '.join(sample)}" + (f" ... 等{len(py_files)}个" if len(py_files) > 20 else ""))
        except OSError:
            lines.append(f"- 源码目录: {source_dir} (无法读取)")
    elif source_dir:
        lines.append(f"- 源码目录: {source_dir} (目录不存在)")
    else:
        lines.append("- 源码目录: 未设置（将从 UML 新生成代码）")

    # ── 测试目录 ──
    if test_dir and os.path.isdir(test_dir):
        try:
            files = [f for f in os.listdir(test_dir) if os.path.isfile(os.path.join(test_dir, f))]
            test_files = [f for f in files if f.startswith('test_') or f.endswith('_test.py')]
            lines.append(f"- 测试目录: {test_dir} ({len(files)} 文件, {len(test_files)} 个测试)")
            if test_files:
                sample = test_files[:20]
                lines.append(f"  测试文件: {', '.join(sample)}" + (f" ... 等{len(test_files)}个" if len(test_files) > 20 else ""))
        except OSError:
            lines.append(f"- 测试目录: {test_dir} (无法读取)")
    elif test_dir:
        lines.append(f"- 测试目录: {test_dir} (目录不存在)")
    else:
        lines.append("- 测试目录: 未设置（将自动生成 pytest 测试）")

    has_source = bool(source_dir and os.path.isdir(source_dir))
    has_test = bool(test_dir and os.path.isdir(test_dir))

    if has_source and has_test:
        lines.append("\n这是已有项目！优先增量修改而非全量覆盖。先检查已有代码和测试状态再决定策略。")
    elif has_source:
        lines.append("\n基于已有源码增量开发。先了解现有代码再修改。")
    elif has_test:
        lines.append("\n可能需要从测试反推代码实现（TDD）。")
    else:
        lines.append("\n全新项目：需求 → UML 设计 → 代码生成 → 验证 → 测试。")

    return "\n".join(lines)


class ProjectInfoTool(Tool):
    """返回当前项目文件级结构信息，供 Agent 按需获取。"""

    def __init__(
        self,
        source_dir: str = "",
        test_dir: str = "",
        project_file: str = "",
    ):
        super().__init__(
            name="project_info",
            description=(
                "获取当前项目的基本信息：设计文件路径/大小、源码目录文件列表、"
                "测试目录文件列表，以及增量 vs 全新开发策略。"
                "需要了解项目布局时调用此工具（通常在新会话开始时调用一次）。"
                "如需查询 UML 设计元素（类、组件、接口、图），"
                "请使用 kg_query / kg_expand / kg_trace 等知识图谱工具。"
            ),
        )
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.project_file = project_file

    def get_parameters(self) -> List[ToolParameter]:
        # 零参数：路径在构造时已注入
        return []

    def run(self, parameters: Dict[str, Any]) -> str:
        return build_project_context(self.source_dir, self.test_dir, self.project_file)


class ReadFileTool(Tool):
    """读取项目内文件内容，供 Agent 拿到完整、新鲜、无截断的原始内容。

    与 KG 摘要互补：KG 告诉 Agent 项目里有什么（路径/结构），本工具负责
    按路径读取文件内容，让 Agent 基于原始文件做精确分析（总结、改代码、
    查细节）。知识图谱作为"加载哪些原始文件的依据"，原始文件本身才是
    权威真相源。
    """

    def __init__(
        self,
        source_dir: str = "",
        test_dir: str = "",
        project_file: str = "",
        max_chars: int = 20000,
    ):
        super().__init__(
            name="read_file",
            description=(
                "读取项目内文件的完整内容。输入文件路径（相对项目根或绝对路径），"
                "返回文件内容。用于：查看 .umlproj 设计的完整 JSON、读取源码/测试"
                "文件内容、核对设计细节。知识图谱只提供摘要，本工具提供原始内容。"
                "可读范围限于项目相关目录（设计文件、源码目录、测试目录）。"
            ),
        )
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.project_file = project_file
        self.max_chars = max_chars

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="path",
                type="string",
                description=(
                    "要读取的文件路径。可用相对路径（如 'src/app.py'）或绝对路径。"
                    "路径应位于项目设计文件、源码目录或测试目录内。"
                ),
                required=True,
            ),
        ]

    def _allowed_root(self) -> str:
        """计算允许读取的根目录（取三个项目目录中已存在的最近公共根）。"""
        roots = [d for d in (self.source_dir, self.test_dir) if d]
        if self.project_file:
            roots.append(os.path.dirname(self.project_file))
        if not roots:
            return os.getcwd()
        # 用所有根的最短共同前缀做安全边界（宽松，足够阻止任意路径穿越）
        common = os.path.commonpath([os.path.abspath(r) for r in roots])
        return common

    def run(self, parameters: Dict[str, Any]) -> str:
        raw_path = str(parameters.get("path", "")).strip()
        if not raw_path:
            return "请提供要读取的文件路径。"

        abs_path = os.path.abspath(raw_path)
        allowed = self._allowed_root()
        # 安全边界：解析后的路径必须在允许根目录内，防任意文件读取
        try:
            common = os.path.commonpath([abs_path, allowed])
        except ValueError:
            return f"路径无效: {raw_path}"
        if common != allowed:
            return (
                f"路径超出允许范围（仅可读项目设计/源码/测试目录）。"
                f"给定: {raw_path}，允许根: {allowed}"
            )

        if not os.path.isfile(abs_path):
            return f"文件不存在: {raw_path}"

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            return f"读取失败: {e}"

        if len(content) > self.max_chars:
            content = content[: self.max_chars] + "\n...[内容过长已截断]"
        return content
