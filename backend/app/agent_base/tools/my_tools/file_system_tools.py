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
import hashlib
import json as _json
import logging
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app.agent_base.tools.base import Tool
from app.agent_base.core.hooks import get_runtime
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool
from app.agent_base.tools.my_tools.project_info_tools import GrepFileTool
from app.core.risk_policy import RiskDecision, RiskPolicy

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

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "", change_set=None):
        super().__init__(
            name="read_file",
            description=(
                "Read a file from the workspace and return its text content. "
                "Use offset (start line) and limit (max lines) to read a specific range."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)
        self.read_only = True
        self.can_parallel = True

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

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "", change_set=None):
        super().__init__(
            name="write_file",
            description=(
                "Write text content to a file in the workspace. Creates parent "
                "directories as needed. Overwrites existing files."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)
        self._change_set = change_set

    async def _execute(self, params: dict) -> str:
        path = params.get("path", "")
        content = params.get("content", "")
        try:
            fp = safe_path(path, self._roots)
        except ValueError as e:
            return f"Error: {e}"
        try:
            before_exists = fp.exists()
            current = fp.read_text(encoding="utf-8") if before_exists else ""
            if before_exists and params.get("expected_sha256"):
                expected = str(params["expected_sha256"]).lower()
                actual = _sha256_text(current)
                if actual != expected:
                    return (f"Conflict: {path} changed since it was read; "
                            f"expected sha256 {expected}, actual {actual}")
            _atomic_write_text(fp, content)
            if self._change_set is not None:
                self._change_set.record(str(fp), before_exists, current, content)
        except Exception as e:
            return f"Error: {e}"
        return f"Wrote {len(content)} bytes to {path} (sha256={_sha256_text(content)})"

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
                        "expected_sha256": {"type": "string", "description": "Optional SHA-256 of the current file; prevents overwriting a concurrent edit."},
                    },
                    "required": ["path", "content"],
                },
            },
        }


class EditFileTool(AsyncTool):
    """精确文本替换（只替换首次出现）。"""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "", change_set=None):
        super().__init__(
            name="edit_file",
            description=(
                "Replace the first occurrence of old_text with new_text in a file. "
                "Use for precise, small edits without rewriting the whole file."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)
        self._change_set = change_set

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

        expected = params.get("expected_sha256")
        actual = _sha256_text(text)
        if expected and actual.lower() != str(expected).lower():
            return (f"Conflict: {path} changed since it was read; "
                    f"expected sha256 {expected}, actual {actual}")
        if old_text not in text:
            return f"Error: text not found in {path}"
        updated = text.replace(old_text, new_text, 1)
        try:
            _atomic_write_text(fp, updated)
        except Exception as e:
            return f"Error: {e}"
        if self._change_set is not None:
            self._change_set.record(str(fp), True, text, updated)
        return f"Edited {path} (sha256={_sha256_text(updated)})"

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
                        "expected_sha256": {"type": "string", "description": "Optional SHA-256 of the current file; prevents lost updates."},
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
        self.read_only = True
        self.can_parallel = True

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
                 review_timeout: float = BASH_REVIEW_TIMEOUT,
                 timeout: float = BASH_TIMEOUT,
                 output_cap: int = BASH_OUTPUT_CAP,
                 risk_policy: RiskPolicy | None = None):
        super().__init__(
            name="bash",
            description=(
                "Run one shell command in a configured workspace directory. "
                "Use cwd='source', 'test', or 'design' instead of cd. Do not chain "
                "commands, use pipes/redirection, nested shells, or inline interpreter code. "
                "Use search_text for discovery and delete_path for deletion. "
                "High-risk commands are denied; sensitive commands require approval."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)
        self._cwd = self._roots[0] if self._roots else ""
        self._cwd_aliases = {
            "source": os.path.abspath(source_dir) if source_dir else "",
            "test": os.path.abspath(test_dir) if test_dir else "",
            "design": os.path.abspath(design_dir) if design_dir else "",
            "workspace": self._cwd,
        }
        self._review_manager = review_manager
        self._progress = progress
        self._review_timeout = review_timeout
        self._timeout = max(0.1, min(float(timeout), 3600.0))
        self._output_cap = max(1024, min(int(output_cap), 1_000_000))
        self._risk_policy = risk_policy or RiskPolicy(
            deny_patterns=DENY_LIST,
            approval_patterns=REVIEW_LIST,
        )

    async def _execute(self, params: dict) -> str:
        command = params.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return "Error: command must be a non-empty string"
        cwd, cwd_error = self._resolve_cwd(params.get("cwd"))
        if cwd_error:
            return f"Error: {cwd_error}"

        lowered = command.lower()
        risk = self._risk_policy.evaluate("bash", {"command": command})
        if risk.action == "deny":
            logger.info("High-risk command denied: %s (matches %s)", command[:100], risk.pattern)
            return f"Error: command denied (high-risk, matches deny list: {risk.pattern})"
        approval_scope = self._risk_policy.approval_scope("bash", {"command": command})
        if risk.action == "ask":
            verdict = await self._request_approval(command, risk, approval_scope)
            if verdict is not None:
                return verdict

        # File discovery has a dedicated, path-sandboxed ``glob`` tool.  Pure
        # shell directory listings add noisy output and frequently lead the
        # agent to repeat the same discovery in several shell dialects.
        # Preserve bash for its intended execution role (tests/builds and
        # explicit file operations), but nudge listing-only requests to glob.
        compact = " ".join(lowered.replace("\r", " ").replace("\n", " ").split())
        listing_markers = ("dir ", "dir/", "ls ", "get-childitem")
        has_listing = any(marker in compact for marker in listing_markers)
        execution_markers = ("pytest", "python -m", "npm ", "pnpm ", "yarn ", "git ")
        if has_listing and not any(marker in compact for marker in execution_markers):
            return (
                "Use glob for workspace file discovery. Bash is reserved for "
                "executing tests, builds, or an explicit file operation."
            )

        # ── 高危：直接拒绝，不执行、不审核 ──
        for pattern in _DENY_LIST_LOWER:
            if pattern in lowered:
                logger.info("🚫 高危命令直接拒绝: %s (matches %s)", command[:100], pattern)
                return f"Error: command denied (high-risk, matches deny list: {pattern})"

        # ── 敏感：请求人工审核，批准才执行 ──
        for pattern in _REVIEW_LIST_LOWER:
            if pattern in lowered and risk.action != "ask":
                verdict = await self._request_approval(command, pattern)
                if verdict is not None:
                    return verdict  # 拒绝/超时/无通道 → 不执行
                break  # 批准 → 继续执行

        syntax_error = self._validate_shell_command(command)
        if syntax_error:
            return f"Error: {syntax_error}"

        return await self._run_command(command, cwd)

    def _resolve_cwd(self, raw_cwd) -> tuple[str | None, str | None]:
        """Resolve a labelled or workspace-relative cwd without shell cd."""
        if raw_cwd in (None, ""):
            return self._cwd or None, None
        if not isinstance(raw_cwd, str):
            return None, "cwd must be a string"
        value = raw_cwd.strip()
        alias = self._cwd_aliases.get(value.lower())
        if alias:
            if not os.path.isdir(alias):
                return None, f"cwd directory does not exist: {value}"
            return alias, None
        try:
            candidate = safe_path(value, self._roots, require_exist=True)
        except ValueError as exc:
            return None, str(exc)
        if not candidate.is_dir():
            return None, f"cwd is not a directory: {value}"
        return str(candidate), None

    @staticmethod
    def _validate_shell_command(command: str) -> str | None:
        """限制 shell=True 的逃逸面。

        现有工具仍支持 Windows 的 ``echo``/``dir`` 等 shell 内建命令，
        但禁止命令串联、重定向、命令替换和脚本解释器内联代码。
        """
        if len(command) > 4000:
            return "command is too long (maximum 4000 characters)"
        if any(token in command for token in ("\n", "\r", ";", "&&", "||", "|", ">", "<", "`", "$(")):
            return "shell operators and command substitution are not allowed"
        lowered = command.lower()
        if re.search(r"\b(powershell|pwsh|cmd)\s*(\.exe)?\s*(/c|/k|-command|-c)\b", lowered):
            return "nested shell invocation is not allowed"
        if re.search(r"\b(python|python3|py)\s+(-\w+\s+)*-c\b", lowered):
            return "inline interpreter code is not allowed"
        executable = command.strip().split(None, 1)[0].strip('"')
        executable = os.path.basename(executable).lower()
        allowed = {
            "python", "python3", "py", "pytest", "git", "echo", "dir", "ls",
            "type", "where", "find", "findstr", "rg", "grep", "cat", "head",
            "tail", "sort", "wc", "pip", "uv", "node", "npm", "ruff", "copy",
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
    ) -> Optional[str]:
        """敏感命令走 ReviewManager 人工审核。返回 None 表示批准可执行，
        否则返回拒绝原因文本（fail closed：拒绝/超时/无审核通道都不执行）。"""
        decision = pattern if isinstance(pattern, RiskDecision) else RiskDecision(
            "ask", "high", "sensitive command", pattern,
        )
        matched_pattern = decision.pattern
        scope = approval_scope or self._risk_policy.approval_scope(
            "bash", {"command": command},
        )
        if self._review_manager is None or self._progress is None:
            logger.warning("🚫 敏感命令无审核通道，拒绝执行: %s", command[:100])
            return (
                f"Error: command requires human approval (matches sensitive list: "
                f"{matched_pattern}), but no review channel is available. Command NOT executed."
            )

        title = "敏感命令请求审核"
        req = self._review_manager.submit(
            review_type="bash_command",
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
            if not self._risk_policy.approval_is_valid(
                "bash", {"command": command}, scope,
            ):
                return "Error: approval scope mismatch. Command NOT executed."
            logger.info("✅ 敏感命令已批准: %s", command[:100])
            return None
        logger.info("🛑 敏感命令被拒绝: %s — %s", command[:100], feedback[:80])
        return f"Error: command rejected by user: {feedback or 'no reason given'}. Command NOT executed."

    async def _run_command(self, command: str, cwd: str | None = None) -> str:
        cwd = cwd if cwd is not None else (self._cwd or None)
        return await self._run_command_cancellable(command, cwd)

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

    async def _run_command_cancellable(self, command: str, cwd: str | None) -> str:
        def _start():
            kwargs = {
                "shell": True,
                "cwd": cwd,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            return subprocess.Popen(command, **kwargs)

        try:
            proc = await asyncio.to_thread(_start)
        except OSError as e:
            return f"Error: {type(e).__name__}: {e}"

        communicate = asyncio.create_task(asyncio.to_thread(proc.communicate))
        deadline = asyncio.get_running_loop().time() + self._timeout
        try:
            while not communicate.done():
                if get_runtime().stop_check():
                    self._terminate_process(proc)
                    await asyncio.shield(communicate)
                    return "Error: command canceled"
                if asyncio.get_running_loop().time() >= deadline:
                    self._terminate_process(proc)
                    await asyncio.shield(communicate)
                    return f"Error: command timed out after {self._timeout:g}s"
                await asyncio.sleep(0.05)
            stdout, stderr = await communicate
        except asyncio.CancelledError:
            self._terminate_process(proc)
            await asyncio.shield(communicate)
            raise
        except OSError as e:
            return f"Error: {type(e).__name__}: {e}"

        out = (_decode_output(stdout) + _decode_output(stderr)).strip()
        out = out[:self._output_cap] if len(out) > self._output_cap else out
        return out or "(no output)"

    @staticmethod
    def _terminate_process(proc: subprocess.Popen) -> None:
        """Terminate the command process group, including child processes."""
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=5, check=False,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            try:
                proc.kill()
            except OSError:
                pass

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

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = ""):
        # ``create_file_system_tools`` receives the design directory rather
        # than the project file. Keep the inherited bounded scanner and add
        # the design root explicitly, without widening its path boundary.
        project_file = design_dir if os.path.isfile(design_dir) else ""
        super().__init__(source_dir, test_dir, project_file)
        self.design_dir = design_dir
        self.name = "search_text"
        self.description = (
            "Search project source, tests, and design files by text or regular expression. "
            "Returns file names, line numbers, and short matching snippets."
        )
        self.read_only = True
        self.can_parallel = True

    async def _execute(self, parameters):
        # Keep the same async testing/dispatch shape as the other filesystem
        # tools while retaining GrepFileTool's bounded synchronous scanner.
        return self.run(parameters)

    def _candidate_files(self) -> list[str]:
        files = super()._candidate_files()
        root = self.design_dir
        if root and os.path.isdir(root):
            for dirpath, _dirs, names in os.walk(root):
                files.extend(
                    os.path.join(dirpath, name) for name in names
                    if name.endswith((".umlproj", ".uml", ".json"))
                )
        return list(dict.fromkeys(files))

    def _resolve_allowed_path(self, raw_path: str) -> str | None:
        resolved = super()._resolve_allowed_path(raw_path)
        if resolved:
            return resolved
        root = self.design_dir
        if not root:
            return None
        candidate = os.path.abspath(raw_path) if os.path.isabs(raw_path) else os.path.abspath(os.path.join(root, raw_path))
        if not os.path.isfile(candidate):
            return None
        try:
            if os.path.commonpath([candidate, os.path.abspath(root)]) != os.path.abspath(root):
                return None
        except ValueError:
            return None
        return candidate


class DeletePathTool(Tool):
    """Delete an explicit workspace file, with opt-in safe directory removal."""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "",
                 change_set=None):
        super().__init__(
            name="delete_path",
            description=(
                "Delete one explicit file inside the workspace. Directories are refused unless "
                "recursive=true; protected and workspace-root paths are always refused."
            ),
        )
        self._roots = _resolve_roots(source_dir, test_dir, design_dir)
        self._change_set = change_set

    def get_parameters(self):
        from app.agent_base.tools.base import ToolParameter
        return [
            ToolParameter(name="path", type="string", description="Explicit workspace-relative or absolute path.", required=True),
            ToolParameter(name="recursive", type="boolean", description="Allow deleting a directory tree; default false.", required=False, default=False),
        ]

    def run(self, parameters):
        raw_path = str(parameters.get("path", "")).strip()
        if not raw_path:
            return "Error: path must be a non-empty string"
        try:
            target = safe_path(raw_path, self._roots, require_exist=True)
        except ValueError as exc:
            return f"Error: {exc}"
        if not target.exists():
            return f"Error: path not found: {raw_path}"
        if target in {Path(root).resolve() for root in self._roots}:
            return "Error: deleting a workspace root is not allowed"
        if any(part in {".git", ".ssh"} or part.startswith(".env") for part in target.parts):
            return "Error: deleting protected paths is not allowed"
        recursive = bool(parameters.get("recursive", False))
        if target.is_dir() and not recursive:
            return "Error: target is a directory; set recursive=true explicitly"
        before = ""
        if target.is_file():
            try:
                before = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                before = ""
        try:
            if self._change_set is not None and target.is_file():
                self._change_set.record(str(target), True, before, "")
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            return f"Error: delete failed: {exc}"
        return f"Deleted: {raw_path}"


def create_file_system_tools(source_dir: str = "", test_dir: str = "", design_dir: str = "",
                             review_manager=None, progress=None, change_set=None,
                             bash_timeout: float = BASH_TIMEOUT,
                             bash_output_cap: int = BASH_OUTPUT_CAP) -> list[Tool]:
    """创建 A 层文件系统原语工具列表。

    review_manager/progress：敏感命令人工审核通道（ReviewManager + ProgressRelay）。
    不传则敏感命令 fail closed（拒绝执行），高危命令仍直接拒绝。
    """
    return [
        ReadFileTool(source_dir, test_dir, design_dir, change_set=change_set),
        WriteFileTool(source_dir, test_dir, design_dir, change_set=change_set),
        EditFileTool(source_dir, test_dir, design_dir, change_set=change_set),
        GlobTool(source_dir, test_dir, design_dir),
        SearchTextTool(source_dir, test_dir, design_dir),
        DeletePathTool(source_dir, test_dir, design_dir, change_set=change_set),
        BashTool(source_dir, test_dir, design_dir,
                 review_manager=review_manager, progress=progress,
                 timeout=bash_timeout, output_cap=bash_output_cap),
    ]
