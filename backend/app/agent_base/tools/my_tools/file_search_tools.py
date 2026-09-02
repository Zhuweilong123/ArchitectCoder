"""Bounded text search for files inside the configured project workspace."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from app.agent_base.tools.base import Tool, ToolParameter

class GrepFileTool(Tool):
    """在项目文件内按关键词全文搜索，返回命中行号与上下文。

    与 read_file 互补：当 Agent 不知道目标在哪一行（如某个字段、某个
    JSON key、某段代码）时，先用 grep 定位行号，再用 read_file 的
    offset 按行读取精确片段。相比逐个 read_file 全量扫描，grep 成本更低，
    适合在大型 .umlproj 或源码中快速定位。
    """

    def __init__(
        self,
        source_dir: str = "",
        test_dir: str = "",
        project_file: str = "",
        max_matches: int = 40,
    ):
        super().__init__(
            name="grep",
            description=(
                "Search for a keyword (regular expression) inside project files and "
                "return matching line numbers with context. Use when you need to "
                "locate a specific field, JSON key, class/method name, or snippet "
                "without reading whole files. Supports regex: e.g. 'fragments|messages' "
                "matches either word, '\\\\\"type\\\\\": \\\\\"association' finds association-"
                "typed fields. Inputs: pattern (the regex to search, default substring "
                "match if not a valid regex), path (optional — a single file; if "
                "omitted, searches all files in the project: design file, source "
                "directory, test directory). Returns up to a few matches per file "
                "with line numbers."
            ),
        )
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.project_file = project_file
        self.max_matches = max_matches

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="pattern",
                type="string",
                description=(
                    "The keyword substring to search for, e.g. 'fragments', "
                    "'association', 'generateTransmitSignal'. Case-sensitive."
                ),
                required=True,
            ),
            ToolParameter(
                name="path",
                type="string",
                description=(
                    "Optional single file to search (relative or absolute path "
                    "inside the project). If omitted, searches all project files."
                ),
                required=False,
                default=None,
            ),
        ]

    def _candidate_files(self) -> list[str]:
        """收集可搜索的文件列表：设计文件 + 源码/测试目录下所有文本文件。"""
        files: list[str] = []
        if self.project_file and os.path.isfile(self.project_file):
            files.append(self.project_file)
        for root in (self.source_dir, self.test_dir):
            if not root or not os.path.isdir(root):
                continue
            for dirpath, _dirs, names in os.walk(root):
                for n in names:
                    if n.endswith(".py"):
                        files.append(os.path.join(dirpath, n))
        return files

    def _resolve_allowed_path(self, raw_path: str) -> str | None:
        """与 ReadFileTool 相同的路径解析：绝对或相对项目根。"""
        allowed_roots = [self.source_dir, self.test_dir]
        if self.project_file:
            allowed_roots.append(os.path.dirname(self.project_file))
        if os.path.isabs(raw_path):
            p = os.path.abspath(raw_path)
        else:
            for root in allowed_roots:
                if root:
                    cand = os.path.abspath(os.path.join(root, raw_path))
                    if os.path.isfile(cand):
                        return cand
            p = os.path.abspath(raw_path)
        if not os.path.isfile(p):
            return None
        # 安全边界检查
        try:
            if allowed_roots:
                common = os.path.commonpath([p] + [os.path.abspath(r) for r in allowed_roots if r])
                allowed = os.path.commonpath([os.path.abspath(r) for r in allowed_roots if r])
                if os.path.commonpath([p, allowed]) != allowed:
                    return None
        except ValueError:
            return None
        return p

    def run(self, parameters: Dict[str, Any]) -> str:
        pattern = str(parameters.get("pattern", "")).strip()
        if not pattern:
            return "请提供要搜索的关键词 pattern。"
        if len(pattern) > 500:
            return "pattern 过长（最多 500 字符）。"

        # 优先按正则编译；非法正则回退为字面子串匹配
        try:
            matcher = re.compile(pattern)
            use_regex = True
        except re.error:
            matcher = None
            use_regex = False

        raw_path = parameters.get("path")
        if raw_path:
            target = self._resolve_allowed_path(str(raw_path).strip())
            if target is None:
                return f"路径无效或超出允许范围: {raw_path}"
            files = [target]
        else:
            files = self._candidate_files()

        lines_out: list[str] = []
        total_hits = 0
        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        hit = matcher.search(line) if use_regex else (pattern in line)
                        if hit:
                            total_hits += 1
                            snippet = line.strip()[:200]
                            if total_hits <= self.max_matches:
                                lines_out.append(
                                    f"{os.path.basename(fpath)}:{lineno}: {snippet}"
                                )
            except OSError:
                continue

        mode = "正则" if use_regex else "字面"
        if total_hits == 0:
            return f"在 {len(files)} 个文件中未找到 '{pattern}'（{mode}匹配）。"

        summary = f"找到 {total_hits} 处匹配（{len(files)} 个文件，{mode}匹配），显示前 {min(total_hits, self.max_matches)} 处："
        if total_hits > self.max_matches:
            summary += f"\n...（还有 {total_hits - self.max_matches} 处未显示，可缩小 pattern 或指定单个文件）"
        return "\n".join([summary] + lines_out)
