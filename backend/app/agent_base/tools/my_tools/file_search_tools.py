"""Portable, language-neutral text search inside configured workspaces."""

from __future__ import annotations

import codecs
import fnmatch
import io
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List

from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.result import ToolResult
from app.runtime.workspace_paths import WorkspacePathError, WorkspacePathResolver


_BINARY_SAMPLE_BYTES = 8192
_MAX_PATTERN_CHARS = 500
_MAX_INLINE_LINE_CHARS = 16_000
_IGNORED_DIRECTORY_NAMES = {
    ".architectcoder", ".git", ".hg", ".mypy_cache", ".next", ".nuxt",
    ".pytest_cache", ".pytest-local", ".pytest-tmp", ".pytest-trace-refactor",
    ".ruff_cache", ".svn", ".venv", "__pycache__", "bower_components",
    "build", "coverage", "dist", "logs", "node_modules", "target", "temp",
    "tmp", "venv",
}
_TEXT_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
    (codecs.BOM_UTF8, "utf-8-sig"),
)


class GrepFileTool(Tool):
    """Search text files portably and return reusable absolute hit locations."""

    def __init__(
        self,
        source_dir: str = "",
        test_dir: str = "",
        project_file: str = "",
        max_matches: int = 40,
        *,
        design_dir: str = "",
        workspace_root: str = "",
        ignored_directories: set[str] | None = None,
    ):
        super().__init__(
            name="grep",
            description=(
                "Search text files in the configured workspace with a regular expression "
                "or literal substring. The optional path accepts a file or directory, "
                "including source, test, design, or workspace aliases. Results include "
                "absolute paths reusable in file tools and program argv, 1-based line and column numbers, and matching "
                "lines. Binary files and common dependency/build/cache directories are skipped."
            ),
        )
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.design_dir = design_dir or (str(Path(project_file).resolve().parent) if project_file else "")
        self.workspace_root = workspace_root
        self.project_file = project_file
        self.max_matches = max(1, int(max_matches))
        self.ignored_directories = {
            name.casefold() for name in (_IGNORED_DIRECTORY_NAMES | (ignored_directories or set()))
        }

        roots = (
            ("source", source_dir),
            ("test", test_dir),
            ("design", self.design_dir),
            ("workspace", workspace_root),
        )
        self._labeled_roots: list[tuple[str, Path]] = []
        for label, value in roots:
            if not value:
                continue
            path = Path(value).resolve()
            if path.is_file():
                path = path.parent
            if path.is_dir() and all(path != existing for _, existing in self._labeled_roots):
                self._labeled_roots.append((label, path))
        if project_file:
            project_path = Path(project_file).resolve()
            if project_path.is_file():
                project_root = project_path.parent
                if all(project_root != existing for _, existing in self._labeled_roots):
                    self._labeled_roots.append(("design", project_root))

        self._paths = WorkspacePathResolver(workspace_root, source_dir, test_dir, self.design_dir)
        self._workspace_path = self._find_workspace_path()
        self._ignore_patterns = self._load_ignore_patterns()

    def _find_workspace_path(self) -> Path | None:
        if self.workspace_root:
            path = Path(self.workspace_root).resolve()
            return path.parent if path.is_file() else path
        if not self._labeled_roots:
            return None
        try:
            common = Path(os.path.commonpath([str(root) for _, root in self._labeled_roots]))
            return common if common.is_dir() else common.parent
        except (OSError, ValueError):
            return self._labeled_roots[0][1]

    def _load_ignore_patterns(self) -> tuple[str, ...]:
        """Read optional workspace-root .searchignore glob exclusions."""
        if self._workspace_path is None:
            return ()
        ignore_file = self._workspace_path / ".searchignore"
        try:
            lines = ignore_file.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            return ()
        return tuple(
            line.strip().replace("\\", "/").lstrip("/")
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )

    def _matches_searchignore(self, path: Path) -> bool:
        if not self._ignore_patterns or self._workspace_path is None:
            return False
        try:
            relative = path.relative_to(self._workspace_path).as_posix()
        except ValueError:
            return False
        parts = relative.split("/")
        for pattern in self._ignore_patterns:
            if "/" in pattern:
                if fnmatch.fnmatchcase(relative, pattern.rstrip("/")):
                    return True
            elif any(fnmatch.fnmatchcase(part, pattern.rstrip("/")) for part in parts):
                return True
        return False

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="pattern",
                type="string",
                description=(
                    "A case-sensitive regex or literal substring, e.g. 'fragments|messages' "
                    "or 'generateTransmitSignal'. Invalid regex syntax falls back to a literal search."
                ),
                required=True,
            ),
            ToolParameter(
                name="path",
                type="string",
                description=(
                    "Optional file or directory scope, relative to the workspace or an absolute "
                    "path inside it. Aliases: source, test, design, workspace. Directories are recursive."
                ),
                required=False,
                default=None,
            ),
        ]

    def _expand_alias(self, raw_path: str) -> str:
        return str(self._paths.resolve(raw_path))

    def _resolve_search_paths(self, raw_path: str | None) -> list[Path]:
        """Resolve a file/directory scope and keep it within configured roots."""
        if not self._paths.roots:
            raise WorkspacePathError("No workspace root configured", "WORKSPACE_NOT_CONFIGURED")
        if not raw_path:
            if not self._labeled_roots:
                raise WorkspacePathError("workspace directories not found", "PATH_NOT_FOUND")
            return self._default_scan_roots()
        resolved = self._paths.resolve(raw_path, require_exist=True)
        if not (resolved.is_file() or resolved.is_dir()):
            raise WorkspacePathError(f"not a file or directory: {raw_path}", "INVALID_PATH_TYPE")
        return [resolved]

    @staticmethod
    def _is_inside(path: Path, root: Path) -> bool:
        try:
            return path.is_relative_to(root)
        except (OSError, ValueError):
            return False

    def _default_scan_roots(self) -> list[Path]:
        roots = [root for _, root in self._labeled_roots]
        # A workspace root commonly contains source/test/design. Scanning that
        # root once prevents duplicate results from overlapping configured roots.
        return [
            root for root in roots
            if not any(root != other and self._is_inside(root, other) for other in roots)
        ]

    def _iter_files(self, scopes: list[Path], errors: list[str]) -> Iterator[Path]:
        seen: set[str] = set()
        stack = list(reversed(scopes))
        while stack:
            current = stack.pop()
            try:
                if current.is_file():
                    resolved = current.resolve(strict=True)
                    if self._is_allowed(resolved) and not self._matches_searchignore(resolved):
                        key = os.path.normcase(str(resolved))
                        if key not in seen:
                            seen.add(key)
                            yield resolved
                    continue
                with os.scandir(current) as iterator:
                    entries = sorted(
                        iterator, key=lambda entry: entry.name.casefold(), reverse=True,
                    )
            except (OSError, RuntimeError) as exc:
                if len(errors) < 5:
                    errors.append(f"{current}: {exc}")
                continue
            directories: list[Path] = []
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        directory = Path(entry.path).resolve(strict=True)
                        if (self._is_allowed(directory)
                                and entry.name.casefold() not in self.ignored_directories
                                and not self._matches_searchignore(directory)):
                            directories.append(directory)
                    elif entry.is_file(follow_symlinks=False):
                        path = Path(entry.path).resolve(strict=True)
                        key = os.path.normcase(str(path))
                        if (self._is_allowed(path) and key not in seen
                                and not self._matches_searchignore(path)):
                            seen.add(key)
                            yield path
                except (OSError, RuntimeError) as exc:
                    if len(errors) < 5:
                        errors.append(f"{entry.path}: {exc}")
                    continue
            stack.extend(reversed(directories))

    def _is_allowed(self, path: Path) -> bool:
        return any(self._is_inside(path, root) for _, root in self._labeled_roots)

    def _display_path(self, path: Path) -> str:
        return self._paths.display(path)

    @staticmethod
    def _text_encoding(sample: bytes) -> str | None:
        for bom, encoding in _TEXT_BOMS:
            if sample.startswith(bom):
                return encoding
        if b"\0" in sample:
            return None
        controls = sum(byte < 9 or 13 < byte < 32 for byte in sample)
        if sample and controls / len(sample) > 0.10:
            return None
        return "utf-8"

    def _find_in_file(self, path: Path, matcher: re.Pattern[str] | None,
                      pattern: str, use_regex: bool) -> Iterator[tuple[int, int, str, int, int]]:
        """Yield line, column, content and excerpt coordinates for text matches."""
        with path.open("rb") as raw:
            sample = raw.read(_BINARY_SAMPLE_BYTES)
            encoding = self._text_encoding(sample)
            if encoding is None:
                return
            raw.seek(0)
            with io.TextIOWrapper(raw, encoding=encoding, errors="replace", newline=None) as text:
                for line_number, raw_line in enumerate(text, 1):
                    line = raw_line.rstrip("\r\n")
                    match = matcher.search(line) if use_regex and matcher else None
                    if not use_regex:
                        index = line.find(pattern)
                        if index >= 0:
                            match_start = index
                        else:
                            continue
                    elif match:
                        match_start = match.start()
                    else:
                        continue

                    if len(line) <= _MAX_INLINE_LINE_CHARS:
                        yield line_number, match_start + 1, line, 0, len(line)
                        continue
                    window_start = max(0, match_start - _MAX_INLINE_LINE_CHARS // 2)
                    window_start = min(window_start, len(line) - _MAX_INLINE_LINE_CHARS)
                    window_end = window_start + _MAX_INLINE_LINE_CHARS
                    excerpt = line[window_start:window_end]
                    yield line_number, match_start + 1, excerpt, window_start, len(line)

    def run(self, parameters: Dict[str, Any]) -> str:
        return self.run_result(parameters).text

    def run_result(self, parameters: Dict[str, Any]) -> ToolResult:
        pattern = parameters.get("pattern", "")
        if not isinstance(pattern, str):
            return ToolResult.error("Error: pattern must be a string", "INVALID_ARGUMENT", True)
        pattern = pattern.strip()
        if not pattern:
            return ToolResult.error("请提供要搜索的关键词 pattern。", "INVALID_ARGUMENT", True)
        if len(pattern) > _MAX_PATTERN_CHARS:
            return ToolResult.error(f"pattern 过长（最多 {_MAX_PATTERN_CHARS} 字符）。", "INVALID_ARGUMENT", True)

        try:
            matcher = re.compile(pattern)
            use_regex = True
        except re.error:
            matcher = None
            use_regex = False

        raw_path = parameters.get("path")
        if raw_path is not None and not isinstance(raw_path, str):
            return ToolResult.error("Error: path must be a string", "INVALID_ARGUMENT", True)
        try:
            scopes = self._resolve_search_paths(raw_path.strip() if raw_path else None)
        except WorkspacePathError as exc:
            return ToolResult.error(f"路径无效或超出允许范围: {raw_path}; {exc}", exc.code, True)
        except (OSError, RuntimeError) as exc:
            return ToolResult.error(f"Error: {exc}", "SEARCH_IO_ERROR", True)

        lines_out: list[str] = []
        total_hits = 0
        scanned_files = 0
        errors: list[str] = []
        for path in self._iter_files(scopes, errors):
            try:
                for line_number, column, content, excerpt_start, line_length in self._find_in_file(
                    path, matcher, pattern, use_regex,
                ):
                    total_hits += 1
                    if total_hits <= self.max_matches:
                        display = self._display_path(path)
                        row = f"{display}:{line_number}:{column}: {content}"
                        if line_length > len(content):
                            row += (
                                f"\n  [超长行摘录：字符 {excerpt_start + 1}-"
                                f"{excerpt_start + len(content)} / 共 {line_length}；"
                                f"可用 read_file(path='{display}', offset={line_number - 1}, "
                                f"char_offset={excerpt_start}) 继续读取]"
                            )
                        lines_out.append(row)
                scanned_files += 1
            except (OSError, UnicodeError, ValueError) as exc:
                if len(errors) < 5:
                    errors.append(f"{path}: {exc}")

        mode = "正则" if use_regex else "字面"
        if errors:
            return ToolResult.error(
                "\n".join(["Error: search incomplete; some files could not be read", *errors, *lines_out]),
                "SEARCH_IO_ERROR", True,
            )
        if total_hits == 0:
            return ToolResult.success(f"在 {scanned_files} 个已扫描文件中未找到匹配（{mode}搜索）。")

        shown = min(total_hits, self.max_matches)
        summary = (
            f"找到 {total_hits} 行匹配（已扫描 {scanned_files} 个文件，{mode}搜索），"
            f"显示前 {shown} 条："
        )
        if total_hits > shown:
            summary += f"\n[另有 {total_hits - shown} 行未显示，可缩小 pattern 或指定 path]"
        return ToolResult.success("\n".join([summary, *lines_out]))
