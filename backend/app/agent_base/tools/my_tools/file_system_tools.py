"""文件系统原语工具 — read_file / write_file / edit_file / glob / bash

借鉴 Claude Code 范式的 A 层工具，为「AI 开发助手」补齐底层动手能力：
读现有代码、精确修改、跑命令。所有文件操作经 ``safe_path`` 守卫在 workspace 内，
bash 带 deny list + 超时；输出截断由默认 ``TruncateHook``（core/hooks.py）负责。

Usage::

    from app.agent_base.tools.my_tools.file_system_tools import create_file_system_tools
    tools = create_file_system_tools(source_dir="src/", test_dir="tests/")
"""

from __future__ import annotations

import asyncio
import glob as _glob
import logging
import subprocess
from pathlib import Path
from typing import Optional

from app.agent_base.tools.base import Tool
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool

logger = logging.getLogger(__name__)

# 危险命令黑名单。
DENY_LIST = [
    # 原有 Linux/Unix 危险指令
    "rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=",
    
    # -------- Windows 扩展 --------
    # 文件/目录强制删除（含递归）
    "del /f /s", "del /f /q", "rd /s /q", "rmdir /s /q",
    "erase /f /s",
    
    # 格式化/磁盘操作
    "format", "diskpart", "clean all", "convert gpt", "convert mbr",
    
    # 系统设置破坏
    "reg delete", "reg add", "reg import", "reg export",
    "bcdedit /delete", "bootrec /fixmbr", "bootrec /fixboot",
    
    # 用户/权限管理（提权或删除）
    "net user", "net localgroup", "lusrmgr",
    "whoami /privileges", "takeown", "icacls",
    
    # 进程/服务强制终止
    "taskkill /f", "taskkill /pid", "kill -f", "Stop-Process -Force",
    
    # 系统关机/重启/睡眠（带强制参数）
    "shutdown /r /f", "shutdown /s /f", "shutdown /l", "shutdown /a",
    "rundll32 powrprof.dll,SetSuspendState",
    
    # 网络防火墙与路由
    "netsh advfirewall set allprofiles state off",
    "netsh interface ip set address",
    "route delete",
    
    # PowerShell 高危命令
    "Remove-Item -Recurse -Force", "Remove-Item -Path",
    "Clear-Content", "Set-Content",
    "Stop-Service", "Disable-Service",
    "Set-Service -StartupType Disabled",
    "Grant-SmbShareAccess", "Revoke-SmbShareAccess",
    
    # WMI 操作（可能用于删除或修改）
    "wmic process call create", "wmic process delete",
    "wmic product uninstall", "wmic os where",
    
    # 组策略/计划任务
    "secedit /configure", "schtasks /delete", "schtasks /create",
    
    # BitLocker/加密破坏
    "manage-bde -off", "manage-bde -lock",
    
    # 分区表破坏
    "dd if=/dev/zero of=\\\\?\\PhysicalDrive",  # Windows 下的 dd 风格
]

BASH_TIMEOUT = 120  # 秒
BASH_OUTPUT_CAP = 50000  # 内部输出上限，避免大输出占内存；喂给模型前再由 TruncateHook 截断


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
    if p.is_absolute():
        resolved = p.resolve()
        for root in resolved_roots:
            if resolved.is_relative_to(root):
                return resolved
        raise ValueError(f"Path escapes workspace: {path}")

    # 相对路径：require_exist 时按顺序尝试所有 root，返回第一个存在的
    if require_exist:
        for root in resolved_roots:
            candidate = (root / p).resolve()
            if candidate.is_relative_to(root) and candidate.exists():
                return candidate

    # 默认 / fallback：固定第一个 root，检查逃逸
    resolved = (resolved_roots[0] / p).resolve()
    if not resolved.is_relative_to(resolved_roots[0]):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


def _resolve_roots(source_dir: str, test_dir: str, design_dir: str = "") -> list[str]:
    return [d for d in (source_dir, test_dir, design_dir) if d]


class ReadFileTool(AsyncTool):
    """读文件，按行返回，支持 offset/limit 切片。"""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = ""):
        super().__init__(
            name="read_file",
            description=(
                "Read a file from the workspace and return its text content. "
                "Use offset (start line) and limit (max lines) to read a specific range."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)

    async def _execute(self, params: dict) -> str:
        path = params.get("path", "")
        try:
            fp = safe_path(path, self._roots, require_exist=True)
        except ValueError as e:
            return f"Error: {e}"
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return f"Error: file not found: {path}"
        except Exception as e:
            return f"Error: {e}"

        offset = max(int(params.get("offset") or 0), 0)
        limit = params.get("limit")
        limit = int(limit) if limit is not None else None
        lines = lines[offset:]
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)

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
                        "limit": {"type": "integer", "description": "Max lines to return."},
                    },
                    "required": ["path"],
                },
            },
        }


class WriteFileTool(AsyncTool):
    """写文件到 workspace（覆盖或新建，自动建父目录）。"""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = ""):
        super().__init__(
            name="write_file",
            description=(
                "Write text content to a file in the workspace. Creates parent "
                "directories as needed. Overwrites existing files."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)

    async def _execute(self, params: dict) -> str:
        path = params.get("path", "")
        content = params.get("content", "")
        try:
            fp = safe_path(path, self._roots)
        except ValueError as e:
            return f"Error: {e}"
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
        except Exception as e:
            return f"Error: {e}"
        return f"Wrote {len(content)} bytes to {path}"

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
                        "content": {"type": "string", "description": "Full text content to write."},
                    },
                    "required": ["path", "content"],
                },
            },
        }


class EditFileTool(AsyncTool):
    """精确文本替换（只替换首次出现）。"""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = ""):
        super().__init__(
            name="edit_file",
            description=(
                "Replace the first occurrence of old_text with new_text in a file. "
                "Use for precise, small edits without rewriting the whole file."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)

    async def _execute(self, params: dict) -> str:
        path = params.get("path", "")
        old_text = params.get("old_text", "")
        new_text = params.get("new_text", "")
        try:
            fp = safe_path(path, self._roots, require_exist=True)
        except ValueError as e:
            return f"Error: {e}"
        try:
            text = fp.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"Error: file not found: {path}"
        except Exception as e:
            return f"Error: {e}"

        if old_text not in text:
            return f"Error: text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"

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
                        "old_text": {"type": "string", "description": "Exact text to replace."},
                        "new_text": {"type": "string", "description": "Replacement text."},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        }


class GlobTool(AsyncTool):
    """按 glob 模式在 workspace 内查找文件。"""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = ""):
        super().__init__(
            name="glob",
            description=(
                "Find files in the workspace matching a glob pattern "
                "(e.g. '**/*.py', 'src/*.ts'). Returns relative paths."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)

    async def _execute(self, params: dict) -> str:
        pattern = params.get("pattern", "")
        if not self._roots:
            return "(no workspace)"
        results: list[str] = []
        for root in self._roots:
            rp = Path(root).resolve()
            try:
                matches = _glob.glob(pattern, root_dir=rp)
            except Exception:
                continue
            for match in matches:
                if (rp / match).resolve().is_relative_to(rp):
                    results.append(str(match))
        seen: set[str] = set()
        uniq = [r for r in results if not (r in seen or seen.add(r))]
        return "\n".join(uniq) if uniq else "(no matches)"

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'."},
                    },
                    "required": ["pattern"],
                },
            },
        }


class BashTool(AsyncTool):
    """在 workspace 内跑 shell 命令（deny list + 超时守卫）。"""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = ""):
        super().__init__(
            name="bash",
            description=(
                "Run a shell command in the workspace (e.g. git status, pytest, lint). "
                "Returns combined stdout/stderr. Denied for dangerous commands."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)
        self._cwd = self._roots[0] if self._roots else ""

    async def _execute(self, params: dict) -> str:
        command = params.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return "Error: command must be a non-empty string"

        lowered = command.lower()
        for pattern in DENY_LIST:
            if pattern in lowered:
                return f"Error: command denied (matches deny list: {pattern})"

        cwd = self._cwd or None

        def _run():
            # 同步 subprocess，在线程池执行：不依赖事件循环类型。
            # 直接 await asyncio.create_subprocess_shell 在 SelectorEventLoop
            # （uvicorn 在 Windows 上的默认 loop）下会抛 NotImplementedError。
            return subprocess.run(
                command, shell=True, cwd=cwd,
                capture_output=True, timeout=BASH_TIMEOUT,
            )

        try:
            proc = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {BASH_TIMEOUT}s"
        except OSError as e:
            return f"Error: {type(e).__name__}: {e}"

        out = (_decode_output(proc.stdout) + _decode_output(proc.stderr)).strip()
        out = out[:BASH_OUTPUT_CAP] if len(out) > BASH_OUTPUT_CAP else out
        return out or "(no output)"

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to run."},
                    },
                    "required": ["command"],
                },
            },
        }


def create_file_system_tools(source_dir: str = "", test_dir: str = "", design_dir: str = "") -> list[Tool]:
    """创建 A 层文件系统原语工具列表。"""
    return [
        ReadFileTool(source_dir, test_dir, design_dir),
        WriteFileTool(source_dir, test_dir, design_dir),
        EditFileTool(source_dir, test_dir, design_dir),
        GlobTool(source_dir, test_dir, design_dir),
        BashTool(source_dir, test_dir, design_dir),
    ]
