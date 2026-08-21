"""文件系统原语工具 — read_file / write_file / edit_file / glob / bash

借鉴 Claude Code 范式的 A 层工具，为「AI 开发助手」补齐底层动手能力：
读现有代码、精确修改、跑命令。所有文件操作经 ``safe_path`` 守卫在 workspace 内；
bash 两级防护：高危命令直接拒绝，敏感命令经 ReviewManager 请求人工批准，
其余命令带超时直接放行；输出截断由默认 ``TruncateHook``（core/hooks.py）负责。

Usage::

    from app.agent_base.tools.my_tools.file_system_tools import create_file_system_tools
    tools = create_file_system_tools(source_dir="src/", test_dir="tests/")
"""

from __future__ import annotations

import asyncio
import glob as _glob
import json as _json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from app.agent_base.tools.base import Tool
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool

logger = logging.getLogger(__name__)

# 高危命令黑名单：磁盘/分区/引导/加密等不可逆系统破坏 —— 直接拒绝，无申诉。
DENY_LIST = [
    "rm -rf /", "mkfs", "dd if=",
    # 格式化/磁盘/分区操作
    "format", "diskpart", "clean all", "convert gpt", "convert mbr",
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
_DENY_LIST_LOWER = [p.lower() for p in DENY_LIST]
_REVIEW_LIST_LOWER = [p.lower() for p in REVIEW_LIST]

BASH_TIMEOUT = 120  # 秒
BASH_OUTPUT_CAP = 50000  # 内部输出上限，避免大输出占内存；喂给模型前再由 TruncateHook 截断
BASH_REVIEW_TIMEOUT = 300  # 敏感命令人工审核等待上限（秒）


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
    """在 workspace 内跑 shell 命令（高危直接拒绝 + 敏感人工审核 + 超时守卫）。"""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "",
                 review_manager=None, progress=None,
                 review_timeout: float = BASH_REVIEW_TIMEOUT):
        super().__init__(
            name="bash",
            description=(
                "Run a shell command in the workspace (e.g. git status, pytest, lint). "
                "Returns combined stdout/stderr. High-risk commands are denied outright; "
                "sensitive commands pause for human approval before running."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)
        self._cwd = self._roots[0] if self._roots else ""
        self._review_manager = review_manager
        self._progress = progress
        self._review_timeout = review_timeout

    async def _execute(self, params: dict) -> str:
        command = params.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return "Error: command must be a non-empty string"

        lowered = command.lower()

        # ── 高危：直接拒绝，不执行、不审核 ──
        for pattern in _DENY_LIST_LOWER:
            if pattern in lowered:
                logger.info("🚫 高危命令直接拒绝: %s (matches %s)", command[:100], pattern)
                return f"Error: command denied (high-risk, matches deny list: {pattern})"

        # ── 敏感：请求人工审核，批准才执行 ──
        for pattern in _REVIEW_LIST_LOWER:
            if pattern in lowered:
                verdict = await self._request_approval(command, pattern)
                if verdict is not None:
                    return verdict  # 拒绝/超时/无通道 → 不执行
                break  # 批准 → 继续执行

        return await self._run_command(command)

    async def _request_approval(self, command: str, pattern: str) -> Optional[str]:
        """敏感命令走 ReviewManager 人工审核。返回 None 表示批准可执行，
        否则返回拒绝原因文本（fail closed：拒绝/超时/无审核通道都不执行）。"""
        if self._review_manager is None or self._progress is None:
            logger.warning("🚫 敏感命令无审核通道，拒绝执行: %s", command[:100])
            return (
                f"Error: command requires human approval (matches sensitive list: "
                f"{pattern}), but no review channel is available. Command NOT executed."
            )

        title = "敏感命令请求审核"
        req = self._review_manager.submit(
            review_type="bash_command",
            title=title,
            content=command,
            question=f"命令命中敏感规则「{pattern}」，是否允许执行？",
        )

        # 阻塞前先把审核事件推给编排层（ProgressRelay → WebSocket），
        # 否则工具不返回，前端永远收不到推送。
        self._progress.emit({
            "event": "review",
            "review_id": req.id,
            "review_type": "bash_command",
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
                "review_type": "bash_command",
                "title": title,
                "timeout": self._review_timeout,
            })
            logger.warning("⏰ 敏感命令审核超时，拒绝执行: %s", command[:100])
            return f"Error: approval timed out after {self._review_timeout}s. Command NOT executed."

        # 前端 reviewStore.accept/reject 发的是 {"decision", "feedback"} JSON；
        # 非 JSON（连接断开清理/旧协议纯文本）一律视为拒绝 —— fail closed。
        try:
            parsed = _json.loads(result)
            decision = parsed.get("decision", "")
            feedback = parsed.get("feedback", "")
        except (ValueError, AttributeError):
            decision, feedback = "", str(result)

        if decision == "accept":
            logger.info("✅ 敏感命令已批准: %s", command[:100])
            return None
        logger.info("🛑 敏感命令被拒绝: %s — %s", command[:100], feedback[:80])
        return f"Error: command rejected by user: {feedback or 'no reason given'}. Command NOT executed."

    async def _run_command(self, command: str) -> str:
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


def create_file_system_tools(source_dir: str = "", test_dir: str = "", design_dir: str = "",
                             review_manager=None, progress=None) -> list[Tool]:
    """创建 A 层文件系统原语工具列表。

    review_manager/progress：敏感命令人工审核通道（ReviewManager + ProgressRelay）。
    不传则敏感命令 fail closed（拒绝执行），高危命令仍直接拒绝。
    """
    return [
        ReadFileTool(source_dir, test_dir, design_dir),
        WriteFileTool(source_dir, test_dir, design_dir),
        EditFileTool(source_dir, test_dir, design_dir),
        GlobTool(source_dir, test_dir, design_dir),
        BashTool(source_dir, test_dir, design_dir,
                 review_manager=review_manager, progress=progress),
    ]
