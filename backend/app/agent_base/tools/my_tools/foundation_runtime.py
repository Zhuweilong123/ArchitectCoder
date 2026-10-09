"""Internal runtime implementations for the DevAgent foundation contracts.

借鉴 Claude Code 范式的 A 层工具，为「AI 开发助手」补齐底层动手能力：
读现有代码、精确修改、跑命令。所有文件操作经 ``safe_path`` 守卫在 workspace 内；
shell 两级防护：高危命令直接拒绝，敏感命令经 ReviewManager 请求人工批准，
其余命令带超时直接放行；长工具结果由运行时分页后喂给模型。

Usage::

    from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
    tools = create_foundation_tools(source_dir="src/", test_dir="tests/")
"""

from __future__ import annotations

import asyncio
import glob as _glob
import hashlib
import json as _json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.runtime.command import CommandExecutor, ExecutionEnvironmentError, HostShellExecutor
from app.runtime.workspace_paths import WorkspacePathError, WorkspacePathResolver
from app.runtime.process_output import (
    DEFAULT_OUTPUT_LIMIT_BYTES, collect_process_output, normalize_output_limit,
)
from app.agent_base.core.hooks import get_runtime
from app.agent_base.tools.async_tool import AsyncTool
from app.agent_base.tools.result import ToolResult, command_result
from app.agent_base.tools.my_tools.file_search_tools import GrepFileTool
from app.agent_base.tools.my_tools.file_inventory import file_metrics
from app.core.risk_policy import RiskDecision, RiskPolicy

logger = logging.getLogger(__name__)

# 高危命令黑名单：磁盘/分区/引导/加密等不可逆系统破坏 —— 直接拒绝，无申诉。
DENY_LIST = [
    "rm -rf /", "mkfs", "dd if=",
    # 格式化/磁盘/分区操作
    "diskpart", "clean all", "convert gpt", "convert mbr",
    # 引导记录破坏
    "bcdedit /delete", "bootrec /fixmbr", "bootrec /fixboot",
    # 安全策略/加密破坏
    "secedit /configure", "manage-bde -off", "manage-bde -lock",
    # 物理磁盘直写（Windows 下的 dd 风格）
    "dd if=/dev/zero of=\\\\?\\physicaldrive",
]

# 敏感命令灰名单：有破坏力但开发中可能合理 —— 请求人工审核（批准/拒绝）。
REVIEW_LIST = [
    # 文件/目录强制删除（含递归）
    "del /f /s", "del /f /q", "rd /s /q", "rmdir /s /q", "erase /f /s",
    "remove-item -recurse -force", "remove-item -path",
    "clear-content", "set-content",
    # 提权/账户/权限管理
    "sudo", "net user", "net localgroup", "lusrmgr",
    "whoami /privileges", "takeown", "icacls",
    # 注册表修改
    "reg delete", "reg add", "reg import", "reg export",
    # 进程/服务强制终止
    "taskkill /f", "taskkill /pid", "kill -f", "stop-process -force",
    "stop-service", "disable-service", "set-service -startuptype disabled",
    # 系统关机/重启/睡眠
    "shutdown", "reboot", "rundll32 powrprof.dll,setsuspendstate",
    # 网络防火墙与路由
    "netsh advfirewall set allprofiles state off",
    "netsh interface ip set address", "route delete",
    # SMB 共享授权
    "grant-smbshareaccess", "revoke-smbshareaccess",
    # WMI 操作（可能用于删除或修改）
    "wmic process call create", "wmic process delete",
    "wmic product uninstall", "wmic os where",
    # 组策略/计划任务
    "schtasks /delete", "schtasks /create",
    # git 破坏性命令（丢未提交改动 / 覆盖远端历史）
    "git reset --hard", "git push --force", "git push -f",
    "git clean -fd", "git clean -f",
]

# 匹配前统一转小写：命令会 lower()，名单预转小写避免混合大小写条目失效。
DENY_REGEX_LIST = [
    r"(?<![\w-])format(?:\.(?:com|exe))?(?=\s|$)",
    r"(?<![\w-])format-volume(?=\s|$)",
]
_DENY_LIST_LOWER = [p.lower() for p in DENY_LIST]
_REVIEW_LIST_LOWER = [p.lower() for p in REVIEW_LIST]

SHELL_TIMEOUT = 120  # 秒
SHELL_OUTPUT_CAP = DEFAULT_OUTPUT_LIMIT_BYTES  # 采集时的字节硬上限；模型输出由运行时分页
SHELL_REVIEW_TIMEOUT = 300  # 敏感命令人工审核等待上限（秒）


def _decode_output(data: bytes) -> str:
    """解码子进程输出：优先 UTF-8，失败回退 GBK。

    中文 Windows 上 cmd.exe 的错误信息是 GBK（cp936），而 Python 子进程
    （PYTHONUTF8=1）输出 UTF-8。不能用 locale.getpreferredencoding()——它在
    PYTHONUTF8=1 下会返回 utf-8，导致 GBK fallback 失效。
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def safe_path(path: str, roots: list[str], require_exist: bool = False) -> Path:
    """解析路径并保证落在任一 workspace root 内，否则抛 ValueError。

    - 绝对路径：``resolve()`` 后必须 ``is_relative_to`` 任一 root
    - 相对路径：默认解析到 ``roots[0]``（source_dir 优先）；
      ``require_exist=True`` 时按顺序尝试所有 root，返回第一个存在的文件
      （读/编辑用，避免「相对路径固定 roots[0]」找不到其他 root 里的文件）
    """
    if not roots:
        raise ValueError("No workspace root configured")
    resolved_roots = [Path(r).resolve() for r in roots if r]
    if not resolved_roots:
        raise ValueError("No workspace root configured")

    p = Path(path)
    if ".architectcoder" in p.parts:
        raise ValueError("Project state is managed by ArchitectCoder")
    if p.is_absolute():
        resolved = p.resolve()
        for root in resolved_roots:
            if resolved.is_relative_to(root):
                if ".architectcoder" in resolved.parts:
                    raise ValueError("Project state is managed by ArchitectCoder")
                return resolved
        raise ValueError(f"Path escapes workspace: {path}")

    # 相对路径：require_exist 时按顺序尝试所有 root，返回第一个存在的
    if require_exist:
        for root in resolved_roots:
            candidate = (root / p).resolve()
            if (candidate.is_relative_to(root) and candidate.exists()
                    and ".architectcoder" not in candidate.parts):
                return candidate

    # 默认 / fallback：固定第一个 root，检查逃逸
    resolved = (resolved_roots[0] / p).resolve()
    if not resolved.is_relative_to(resolved_roots[0]):
        raise ValueError(f"Path escapes workspace: {path}")
    if ".architectcoder" in resolved.parts:
        raise ValueError("Project state is managed by ArchitectCoder")
    return resolved


def _resolve_roots(*roots: str) -> list[str]:
    return [root for root in roots if root]


def _expand_workspace_alias(
    value: str, workspace_root: str = "", source_dir: str = "",
    test_dir: str = "", design_dir: str = "",
) -> str:
    """Compatibility wrapper around the shared path contract."""
    return str(WorkspacePathResolver(
        workspace_root, source_dir, test_dir, design_dir,
    ).resolve(value))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    """在目标文件同目录完成原子替换，避免半写文件被并发读到。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


class ReadFileTool(AsyncTool):
    """读文件，按行返回，支持 offset/limit 切片。"""

    _MAX_PATH_SUGGESTIONS = 5
    _MAX_LINES = 2000
    _MAX_OUTPUT_CHARS = 100_000
    _MAX_LINE_CHARS = 16_000
    _OUTPUT_METADATA_RESERVE = 256

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "", change_set=None,
                 workspace_root: str = ""):
        super().__init__(
            name="read_file",
            description=(
                "Read current workspace file content by path. Use offset (0-based "
                "start line) and limit (max 2000 lines) to target a range; omitted "
                "limit reads up to 2000 lines. If the result reports next_line, "
                "continue with read_file at that line. For an oversized line, "
                "also pass its next_char as char_offset."
            ),
        )
        self._roots = _resolve_roots(workspace_root, source_dir, test_dir, design_dir)
        self._paths = WorkspacePathResolver(workspace_root, source_dir, test_dir, design_dir)
        self._workspace_root = workspace_root
        self._source_dir = source_dir
        self._test_dir = test_dir
        self._design_dir = design_dir
        self.read_only = True
        self.can_parallel = True

    async def _execute(self, params: dict) -> str:
        return (await self.run_result(params)).text

    async def _execute_result(self, params: dict) -> ToolResult:
        path = params.get("path", "")
        try:
            fp = self._paths.resolve(path, require_exist=True)
        except WorkspacePathError as e:
            message = self._missing_file_message(path) if e.code == "PATH_NOT_FOUND" else f"Error: {e}"
            return ToolResult.error(message, e.code, retryable=True)
        if not fp.is_file():
            return ToolResult.error(f"Error: not a file: {path}", "NOT_A_FILE", True)
        try:
            offset = max(int(params.get("offset") or 0), 0)
            requested_limit = params.get("limit")
            limit = (self._MAX_LINES if requested_limit is None else
                     min(int(requested_limit), self._MAX_LINES))
            char_offset = int(params.get("char_offset") or 0)
        except (TypeError, ValueError, OverflowError):
            return ToolResult.error("Error: offset, limit, and char_offset must be integers", "INVALID_ARGUMENT", True)
        if limit < 1 or char_offset < 0:
            return ToolResult.error("Error: limit must be positive and char_offset nonnegative", "INVALID_ARGUMENT", True)
        try:
            body = await asyncio.to_thread(
                self._read_window, fp, offset, limit, char_offset,
            )
            return ToolResult.success(body)
        except FileNotFoundError:
            return ToolResult.error(self._missing_file_message(path), "PATH_NOT_FOUND", True)
        except (OSError, UnicodeError) as e:
            return ToolResult.error(f"Error: {e}", "FILE_READ_ERROR", True)
        except ValueError as e:
            return ToolResult.error(f"Error: {e}", "INVALID_ARGUMENT", True)

    def _read_window(self, path: Path, offset: int, limit: int,
                     char_offset: int) -> str:
        """Stream a bounded range and leave a read_file cursor if more remains."""
        parts: list[str] = []
        content_chars = 0
        complete_lines = 0
        next_line: int | None = None
        next_char = 0
        max_content = self._MAX_OUTPUT_CHARS - self._OUTPUT_METADATA_RESERVE

        with path.open("r", encoding="utf-8") as stream:
            for _ in range(offset):
                if not self._discard_line(stream):
                    return ""
            line_number = offset
            while True:
                if complete_lines >= limit:
                    if stream.read(1):
                        next_line = line_number
                    break
                start_char = char_offset if line_number == offset else 0
                if start_char and not self._discard_chars(stream, start_char):
                    raise ValueError(f"char_offset exceeds line {line_number}")
                raw_line = stream.readline(self._MAX_LINE_CHARS + 1)
                if not raw_line:
                    break
                line = raw_line.removesuffix("\n")
                separator_chars = 1 if parts else 0
                available = max_content - content_chars - separator_chars
                if available <= 0:
                    next_line, next_char = line_number, start_char
                    break
                if len(line) > self._MAX_LINE_CHARS:
                    chunk = line[:min(self._MAX_LINE_CHARS, available)]
                    parts.append(chunk)
                    next_line, next_char = line_number, start_char + len(chunk)
                    break
                if len(line) > available:
                    next_line, next_char = line_number, start_char
                    break
                parts.append(line)
                content_chars += separator_chars + len(line)
                complete_lines += 1
                line_number += 1

        body = "\n".join(parts)
        if next_line is None:
            return body
        marker = (
            f"\n\n[read_file partial; lines={offset}:{offset + complete_lines}; "
            f"next_line={next_line}; next_char={next_char}; "
            "continue with read_file using the same path]"
        )
        return body + marker

    @staticmethod
    def _discard_line(stream) -> bool:
        """Skip one line without constructing an unbounded string."""
        while chunk := stream.readline(8192):
            if chunk.endswith("\n"):
                return True
        return False

    @staticmethod
    def _discard_chars(stream, count: int) -> bool:
        """Move within one line using bounded chunks."""
        while count:
            chunk = stream.readline(min(count, 8192))
            if not chunk or chunk.endswith("\n"):
                return False
            count -= len(chunk)
        return True

    def _missing_file_message(self, requested: object) -> str:
        """Return a bounded, actionable error without guessing a target path."""
        requested_text = str(requested or "")
        candidates = self._find_candidates(requested_text)
        lines = [
            f"Error: file not found: {requested_text}",
            "recovery_action: use an exact path returned by list_files or search_text.",
        ]
        if candidates:
            lines.insert(1, "possible_paths: " + ", ".join(candidates))
        return "\n".join(lines)

    def _find_candidates(self, requested: str) -> list[str]:
        """Find a few same-name files for model guidance after a miss.

        This is deliberately advisory: the requested path is never rewritten and
        ambiguous matches are all reported instead of silently selecting one.
        The search is bounded and skips generated/dependency directories.
        """
        name = Path(requested).name.strip()
        if not name or name in {".", ".."}:
            return []
        roots: list[Path] = []
        for value in (
            self._source_dir, self._test_dir, self._design_dir, self._workspace_root,
        ):
            if not value:
                continue
            root = Path(value).resolve()
            if root not in roots and root.is_dir():
                roots.append(root)

        found: list[str] = []
        seen: set[str] = set()
        ignored = {".git", ".architectcoder", "node_modules", "__pycache__", ".pytest_cache"}
        for root in roots:
            try:
                for candidate in root.rglob(name):
                    if any(part in ignored for part in candidate.parts):
                        continue
                    resolved = candidate.resolve()
                    if not resolved.is_file() or not resolved.is_relative_to(root):
                        continue
                    display = self._display_candidate(resolved)
                    if display not in seen:
                        seen.add(display)
                        found.append(display)
                    if len(found) >= self._MAX_PATH_SUGGESTIONS:
                        return found
            except OSError:
                continue
        return found

    def _display_candidate(self, candidate: Path) -> str:
        return self._paths.display(candidate)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to the workspace."},
                        "offset": {"type": "integer", "description": "Start line (0-based)."},
                        "limit": {"type": "integer", "description": "Max lines to return, at most 2000."},
                        "char_offset": {"type": "integer", "description": "Character position within the first line, for continuing an oversized line."},
                    },
                    "required": ["path"],
                },
            },
        }


class BaseListFilesTool(AsyncTool):
    """Shared glob listing and file metrics for workspace tools."""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "",
                 workspace_root: str = ""):
        super().__init__(
            name="list_files",
            description=(
                "List workspace files with compact metrics. B=bytes, L=physical lines, "
                "S=symbol hints, I=interface hints, D=dependency-statement hints; "
                "S/I/D are approximate. Results are absolute paths reusable in "
                "file tools and literal program argv. "
                "When asked for an overview, summarize counts and largest files; group "
                "full inventories by directory when the user asks for all files."
            ),
        )
        self._roots = _resolve_roots(workspace_root, source_dir, test_dir, design_dir)
        self._paths = WorkspacePathResolver(workspace_root, source_dir, test_dir, design_dir)
        self.read_only = True
        self.can_parallel = True

    async def _execute(self, params: dict) -> str:
        return (await self.run_result(params)).text

    async def _execute_result(self, params: dict) -> ToolResult:
        if not self._roots:
            return ToolResult.error("Error: No workspace root configured", "WORKSPACE_NOT_CONFIGURED")
        try:
            patterns = self._query_patterns(params)
            return await asyncio.to_thread(
                self._list_result, self._roots, patterns,
                details=params.get("details") is not False,
                limit=self._result_limit(params.get("limit")),
                extensions=self._query_extensions(params),
                summary=params.get("summary") is True,
            )
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}", "INVALID_ARGUMENT", True)
        except OSError as exc:
            return ToolResult.error(f"Error: {exc}", "FILE_LIST_ERROR", True)

    @staticmethod
    def _query_patterns(params: dict) -> list[str]:
        if "extensions" in params and ("patterns" in params or params.get("pattern") not in (None, "")):
            raise ValueError("use extensions, pattern, or patterns separately")
        if "patterns" in params:
            if params.get("pattern") not in (None, ""):
                raise ValueError("use either pattern or patterns, not both")
            patterns = params["patterns"]
            if not isinstance(patterns, list) or not patterns:
                raise ValueError("patterns must be a non-empty array of Python glob strings")
        else:
            pattern = params.get("pattern")
            patterns = ["**/*" if pattern in (None, "") else pattern]
        for pattern in patterns:
            if (
                not isinstance(pattern, str) or not pattern
                or Path(pattern).is_absolute()
                or ".." in pattern.replace("\\", "/").split("/")
            ):
                raise ValueError("patterns must be non-empty relative Python globs without '..'")
        # Normalize common union notation to the same multi-pattern contract.
        # Keep literal patterns too: braces are valid filename characters.
        expanded = list(patterns)
        pending = list(patterns)
        while pending:
            pattern = pending.pop()
            union = re.search(r"\{([^{}]*,[^{}]*)\}", pattern)
            if union is None:
                continue
            for alternative in union.group(1).split(","):
                query = pattern[:union.start()] + alternative + pattern[union.end():]
                if query not in expanded:
                    expanded.append(query)
                    pending.append(query)
                    if len(expanded) > 100:
                        raise ValueError("query expands to more than 100 patterns; narrow the query")
        return list(dict.fromkeys(expanded))

    @staticmethod
    def _query_extensions(params: dict) -> list[str] | None:
        extensions = params.get("extensions")
        if "extensions" not in params:
            return None
        if not isinstance(extensions, list) or not extensions:
            raise ValueError("extensions must be a non-empty array of suffixes")
        normalized = []
        for extension in extensions:
            if not isinstance(extension, str) or not re.fullmatch(r"\.?[\w-]+", extension):
                raise ValueError("extensions must be literal suffixes, e.g. '.log' or 'tmp'")
            normalized.append("." + extension.lstrip(".").casefold())
        return list(dict.fromkeys(normalized))

    @staticmethod
    def _result_limit(value: object) -> int:
        try:
            return max(1, min(int(value), 1000)) if value is not None else 200
        except (TypeError, ValueError):
            return 200

    def _format_matches(self, roots: list[str], pattern: str | list[str], *,
                        details: bool = True, limit: int = 200) -> str:
        return self._list_result(roots, pattern, details=details, limit=limit).text

    def _list_result(self, roots: list[str], pattern: str | list[str], *,
                     details: bool = True, limit: int = 200,
                     extensions: list[str] | None = None, summary: bool = False) -> ToolResult:
        selected: list[tuple[str, Path]] = []
        seen: set[str] = set()
        total = 0
        files = directories = 0
        suffix_counts: dict[str, int] = {}
        oldest = newest = None
        errors: list[dict] = []
        patterns = [pattern] if isinstance(pattern, str) else pattern
        for root in roots:
            rp = Path(root).resolve()
            # glob silently suppresses directory access errors; preflight the
            # selected root so a failed listing cannot become "no matches".
            with os.scandir(rp):
                pass
            # Python glob suppresses traversal errors. Account for them when
            # querying recursively so an inaccessible subtree is not counted
            # as successfully checked. Internal tool state remains excluded.
            if any("**" in query for query in patterns):
                for _, dirs, _ in os.walk(rp, followlinks=False, onerror=lambda exc: errors.append({
                    "path": str(exc.filename), "error": str(exc),
                })):
                    dirs[:] = [name for name in dirs if name.casefold() != ".architectcoder"]
            matches = (
                match for query in patterns
                for match in _glob.iglob(query, root_dir=rp, recursive=True, include_hidden=True)
            )
            for match in matches:
                candidate = (rp / match).resolve()
                if any(part.casefold() == ".architectcoder" for part in candidate.parts):
                    continue
                name = self._paths.display(candidate) if candidate.is_relative_to(rp) else str(match)
                if not candidate.is_relative_to(rp) or name in seen:
                    continue
                if extensions is not None and candidate.suffix.casefold() not in extensions:
                    continue
                seen.add(name)
                try:
                    stat = candidate.stat()
                    if candidate.is_dir():
                        if extensions is not None:
                            continue
                        directories += 1
                    else:
                        files += 1
                        suffix = candidate.suffix.casefold() or "(none)"
                        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
                        oldest = stat.st_mtime if oldest is None else min(oldest, stat.st_mtime)
                        newest = stat.st_mtime if newest is None else max(newest, stat.st_mtime)
                except OSError as exc:
                    errors.append({"path": str(candidate), "error": str(exc)})
                    continue
                total += 1
                if not summary and len(selected) < limit:
                    selected.append((name, candidate))
        metadata = {
            # Counts come first so bounded cross-turn excerpts retain the
            # result even when scope paths and pattern arrays are long.
            "matched": total, "shown": len(selected),
            "entry_limit_truncated": not summary and total > len(selected),
            "files": files, "directories": directories,
            "extension_counts": suffix_counts,
            "modified_utc": {
                "min": datetime.fromtimestamp(oldest, timezone.utc).isoformat() if oldest is not None else None,
                "max": datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest is not None else None,
                "source": "filesystem stat.st_mtime; matched files only",
            },
            "scan_complete": not errors, "error_count": len(errors),
            "summary_only": summary, "extensions": extensions,
            "patterns": patterns, "roots": roots,
            "scope": "matched entries only", "provenance": "not measured",
        }
        query_header = "[file query " + _json.dumps(metadata, ensure_ascii=False) + "]"
        header = (
            "B=bytes L=physical lines S=symbol hints I=interface hints "
            "D=dependency-statement hints; S/I/D are approximate."
            if details else ""
        )
        rows = [query_header]
        if header:
            rows.append(header)
        for name, path in selected:
            metrics = file_metrics(path) if details else ""
            row = f"{name}\t{metrics}" if metrics else name
            rows.append(row)
        shown = len(selected)
        if not total:
            rows.append("(no matches)" if not errors else "(no readable matches; scan incomplete)")
        if not summary and total > shown:
            rows.append(
                f"[truncated: showing {shown} of {total} entries (entry limit); "
                "narrow path/pattern]"
            )
        if errors:
            rows.append("[scan errors] " + _json.dumps(errors, ensure_ascii=False))
        return ToolResult(
            status="error" if errors else "success", data="\n".join(rows),
            error_code="FILE_LIST_INCOMPLETE" if errors else "",
            retryable=bool(errors), execution_evidence={"file_query": metadata, "scan_errors": errors},
        )

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'; default '**/*'."},
                        "patterns": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                     "description": "Alternative to pattern: union of Python globs, with duplicates removed."},
                        "extensions": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                       "description": "Alternative to glob: recursive file suffix filter, e.g. ['.tmp', '.log']."},
                        "summary": {"type": "boolean", "description": "Return aggregate counts and filesystem modification-time range without listing entries."},
                        "details": {"type": "boolean", "description": "Include file metrics (default true)."},
                        "limit": {"type": "integer", "description": "Maximum entries (default 200, maximum 1000)."},
                    },
                },
            },
        }


class ShellTool(AsyncTool):
    """在 workspace 内跑 shell 命令（高危直接拒绝 + 敏感人工审核 + 超时守卫）。"""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "",
                 review_manager=None, progress=None,
                 review_timeout: float = SHELL_REVIEW_TIMEOUT,
                 timeout: float = SHELL_TIMEOUT,
                 output_cap: int = SHELL_OUTPUT_CAP,
                 risk_policy: RiskPolicy | None = None,
                 command_executor: CommandExecutor | None = None,
                 workspace_root: str = ""):
        self._command_executor = command_executor or HostShellExecutor()
        super().__init__(
            name="shell",
            description=self._command_executor.profile.tool_description,
        )
        self._roots = _resolve_roots(workspace_root, source_dir, test_dir, design_dir)
        self._cwd = workspace_root or (self._roots[0] if self._roots else "")
        self._paths = WorkspacePathResolver(workspace_root, source_dir, test_dir, design_dir)
        self._review_manager = review_manager
        self._progress = progress
        self._review_timeout = review_timeout
        self._timeout = max(0.1, min(float(timeout), 3600.0))
        self._output_cap = normalize_output_limit(output_cap)
        self._risk_policy = risk_policy or RiskPolicy(
            deny_patterns=DENY_LIST,
            deny_regex_patterns=DENY_REGEX_LIST,
            approval_patterns=REVIEW_LIST,
        )

    async def _execute(self, params: dict) -> str:
        return (await self.run_result(params)).text

    async def _execute_result(self, params: dict):
        command = params.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return ToolResult.error("Error: command must be a non-empty string", "INVALID_ARGUMENT", True)
        cwd, cwd_error = self._resolve_cwd(params.get("cwd"))
        if cwd_error:
            return self._cwd_error_result(cwd_error)

        lowered = command.lower()
        risk = self._risk_policy.evaluate("shell", {"command": command})
        if risk.action == "deny":
            logger.info("High-risk command denied: %s (matches %s)", command[:100], risk.pattern)
            return ToolResult(status="blocked", data=f"Error: command denied (high-risk, matches deny list: {risk.pattern})", error_code="POLICY_DENIED")
        approval_scope = self._risk_policy.approval_scope("shell", {"command": command})
        if risk.action == "ask":
            verdict = await self._request_approval(command, risk, approval_scope)
            if verdict is not None:
                return verdict

        # ── 高危：直接拒绝，不执行、不审核 ──
        for pattern in _DENY_LIST_LOWER:
            if pattern in lowered:
                logger.info("🚫 高危命令直接拒绝: %s (matches %s)", command[:100], pattern)
                return ToolResult(status="blocked", data=f"Error: command denied (high-risk, matches deny list: {pattern})", error_code="POLICY_DENIED")

        # ── 敏感：请求人工审核，批准才执行 ──
        for pattern in _REVIEW_LIST_LOWER:
            if pattern in lowered and risk.action != "ask":
                verdict = await self._request_approval(command, pattern)
                if verdict is not None:
                    return verdict  # 拒绝/超时/无通道 → 不执行
                break  # 批准 → 继续执行

        validator = getattr(self._command_executor, "validate_shell_command", None)
        syntax_error = (
            validator(command)
            if callable(validator)
            else self._validate_shell_command(command)
        )
        if syntax_error:
            return ToolResult.error(f"Error: {syntax_error}", "COMMAND_SYNTAX_INVALID", True)

        environment_error = self._command_executor.validate_command(command)
        if environment_error:
            return ToolResult.error(f"Error: {environment_error}", "EXECUTION_ENVIRONMENT_ERROR", True)

        return await self._run_command(command, cwd)

    def _resolve_cwd(self, raw_cwd) -> tuple[str | None, WorkspacePathError | None]:
        """Resolve a labelled or workspace-relative cwd without shell cd."""
        if raw_cwd in (None, ""):
            raw_cwd = "."
        if not isinstance(raw_cwd, str):
            return None, WorkspacePathError("cwd must be a string", "INVALID_ARGUMENT")
        try:
            candidate = self._paths.directory(raw_cwd, default_root=self._cwd)
        except WorkspacePathError as exc:
            return None, exc
        return str(candidate), None

    @staticmethod
    def _cwd_error_result(error: WorkspacePathError) -> ToolResult:
        return ToolResult.error(f"Error: {error}", error.code, True)

    @staticmethod
    def _validate_shell_command(command: str) -> str | None:
        """限制 shell=True 的逃逸面。

        The production command contract is POSIX bash.  The host adapter owns
        WSL/native process launch; this validator only enforces the stable
        agent-facing command subset.
        """
        if len(command) > 4000:
            return "command is too long (maximum 4000 characters)"
        if any(token in command for token in ("\n", "\r", ";", "&&", "||", "|", ">", "<", "`", "$(")):
            return "shell operators and command substitution are not allowed"
        lowered = command.lower()
        if re.search(r"\b(powershell|pwsh|cmd|wsl|bash|sh)\s*(\.exe)?\s*(/c|/k|-command|-c)\b", lowered):
            return "nested shell invocation is not allowed"
        if re.search(r"\b(python|python3|py)\s+(-\w+\s+)*-c\b", lowered):
            return "inline interpreter code is not allowed"
        executable = command.strip().split(None, 1)[0].strip('"')
        executable = os.path.basename(executable).lower()
        allowed = {
            "python", "python3", "pytest", "git", "echo", "printf", "pwd", "uname", "ls", "find", "rg",
            "grep", "cat", "head", "tail", "sort", "wc", "pip", "uv", "node",
            "npm", "npx", "pnpm", "yarn", "ruff", "mypy", "make", "cargo", "go",
        }
        if executable.endswith(".exe"):
            executable = executable[:-4]
        if executable not in allowed:
            return f"executable '{executable}' is not allowed"
        return None

    async def _request_approval(
        self,
        command: str,
        pattern: str | RiskDecision,
        approval_scope: dict[str, str] | None = None,
    ) -> ToolResult | None:
        """敏感命令走 ReviewManager 人工审核。返回 None 表示批准可执行，
        否则返回结构化拒绝结果（拒绝/超时/无审核通道都不执行）。"""
        decision = pattern if isinstance(pattern, RiskDecision) else RiskDecision(
            "ask", "high", "sensitive command", pattern,
        )
        matched_pattern = decision.pattern
        scope = approval_scope or self._risk_policy.approval_scope(
            "shell", {"command": command},
        )
        if self._review_manager is None or self._progress is None:
            logger.warning("🚫 敏感命令无审核通道，拒绝执行: %s", command[:100])
            return ToolResult(status="blocked", data=(
                f"Error: command requires human approval (matches sensitive list: "
                f"{matched_pattern}), but no review channel is available. Command NOT executed."
            ), error_code="APPROVAL_REQUIRED")

        title = "敏感命令请求审核"
        req = self._review_manager.submit(
            review_type="shell_command",
            title=title,
            content=command,
            metadata={
                "risk_level": decision.level,
                "risk_reason": decision.reason,
                "approval_scope": scope,
            },
            question=f"命令命中敏感规则「{pattern}」，是否允许执行？",
        )

        # 阻塞前先把审核事件推给编排层（ProgressRelay → WebSocket），
        # 否则工具不返回，前端永远收不到推送。
        self._progress.emit({
            "event": "review",
            "review_id": req.id,
            "review_type": "shell_command",
            "title": title,
            "content": command,
            "question": req.question,
            "metadata": req.metadata,
        })
        logger.info("🔔 敏感命令等待人工审核: %s", command[:100])

        try:
            result = await asyncio.wait_for(req.future, timeout=self._review_timeout)
        except asyncio.TimeoutError:
            self._progress.emit({
                "event": "review_timeout",
                "review_id": req.id,
                "review_type": "shell_command",
                "title": title,
                "timeout": self._review_timeout,
            })
            logger.warning("⏰ 敏感命令审核超时，拒绝执行: %s", command[:100])
            return ToolResult(status="blocked", data=f"Error: approval timed out after {self._review_timeout}s. Command NOT executed.", error_code="APPROVAL_TIMEOUT")

        # 前端 reviewStore.accept/reject 发的是 {"decision", "feedback"} JSON；
        # 非 JSON（连接断开清理/旧协议纯文本）一律视为拒绝 —— fail closed。
        try:
            parsed = _json.loads(result)
            decision = parsed.get("decision", "")
            feedback = parsed.get("feedback", "")
        except (ValueError, AttributeError):
            decision, feedback = "", str(result)

        if decision == "accept":
            if not self._risk_policy.approval_is_valid(
                "shell", {"command": command}, scope,
            ):
                return ToolResult(status="blocked", data="Error: approval scope mismatch. Command NOT executed.", error_code="APPROVAL_SCOPE_MISMATCH")
            logger.info("✅ 敏感命令已批准: %s", command[:100])
            return None
        logger.info("🛑 敏感命令被拒绝: %s — %s", command[:100], feedback[:80])
        return ToolResult(status="blocked", data=f"Error: command rejected by user: {feedback or 'no reason given'}. Command NOT executed.", error_code="APPROVAL_REJECTED")

    async def _run_command(self, command: str, cwd: str | None = None) -> str | ToolResult:
        cwd = cwd if cwd is not None else (self._cwd or None)
        return await self._run_command_cancellable(command, cwd)

    async def _run_command_cancellable(self, command: str, cwd: str | None) -> str | ToolResult:
        def _start():
            return self._command_executor.start(command, cwd)

        try:
            proc = await asyncio.to_thread(_start)
        except (OSError, ExecutionEnvironmentError) as e:
            return ToolResult.error(f"Error: {type(e).__name__}: {e}", "PROCESS_START_ERROR", True)

        try:
            captured = await collect_process_output(
                proc, terminate=self._command_executor.terminate,
                timeout=self._timeout, stop_check=get_runtime().stop_check,
                output_limit=self._output_cap,
            )
        except OSError as e:
            return ToolResult.error(f"Error: {type(e).__name__}: {e}", "PROCESS_IO_ERROR", True)

        if captured.reason == "canceled":
            return ToolResult.error("Error: command canceled", "PROCESS_CANCELED")
        if captured.reason == "timeout":
            return ToolResult.error(f"Error: command timed out after {self._timeout:g}s", "PROCESS_TIMEOUT", True)
        out = (_decode_output(captured.stdout) + _decode_output(captured.stderr)).strip()
        if captured.reason == "output_limit":
            return ToolResult.error(
                f"Error: OUTPUT_LIMIT: command output is incomplete; collected "
                f"{captured.collected_bytes} of at least {captured.limit_bytes + 1} bytes "
                f"(limit {captured.limit_bytes}). Process stopped.\n{out}",
                "OUTPUT_LIMIT",
            )
        if proc.returncode:
            out = f"Error: command exited with code {proc.returncode}: {out or '(no output)'}"
        return command_result(command, cwd, proc.returncode, out or "(no output)")

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "One command only; no cd, chaining, pipes, redirection, nested shells, or inline code.",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Optional directory alias: source, test, design, workspace; or a relative directory inside the workspace.",
                        },
                    },
                    "required": ["command"],
                },
            },
        }


class SearchTextTool(GrepFileTool):
    """Structured project search exposed under the stable ``search_text`` name."""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "",
                 workspace_root: str = ""):
        project_file = design_dir if os.path.isfile(design_dir) else ""
        if project_file:
            design_dir = os.path.dirname(os.path.abspath(project_file))
        super().__init__(
            source_dir=source_dir, test_dir=test_dir,
            project_file=project_file, design_dir=design_dir,
            workspace_root=workspace_root,
        )
        self.design_dir = design_dir
        self.workspace_root = workspace_root
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.name = "search_text"
        self.description = (
            "Search text files across the configured project roots, regardless of "
            "programming language or file extension. Uses a case-sensitive regex, "
            "falling back to a literal substring when the regex is invalid. The optional "
            "path accepts one file or a recursive directory scope such as source, test, "
            "design, or workspace, including alias subpaths. Real workspace entries "
            "take priority over aliases. Results include absolute path, line, and "
            "column. Binary files and common dependency/build/cache directories are skipped; "
            "workspace .searchignore can add exclusions."
        )
        self.read_only = True
        self.can_parallel = True

    def run(self, parameters):
        return GrepFileTool.run_result(self, parameters).text

    async def run_result(self, parameters):
        return await asyncio.to_thread(GrepFileTool.run_result, self, parameters)

    async def _execute(self, parameters):
        return (await self.run_result(parameters)).text
