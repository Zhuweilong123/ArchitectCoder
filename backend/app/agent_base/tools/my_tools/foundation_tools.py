"""Stable, OS-neutral tools exposed to the main DevAgent.

These tools express capabilities rather than host commands.  The legacy file
tools remain available only to compatibility and trace-replay callers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
from dataclasses import replace
from typing import Any
from pathlib import Path

from app.agent_base.core.hooks import get_runtime
from app.runtime.command import ExecutionEnvironmentError
from app.agent_base.tools.base import Tool
from app.agent_base.tools.result import (
    CommandEvidence,
    ToolResult,
    FileChange,
    VerificationEvidence,
    command_result,
)
from app.agent_base.tools.my_tools.foundation_runtime import (
    ShellTool,
    ListFilesTool as FoundationListFilesRuntime,
    ReadFileTool,
    SearchTextTool,
    _decode_output,
    _expand_workspace_alias,
    _resolve_roots,
    safe_path,
)
from app.runtime import (
    ApprovalClass,
    ExecutionPolicy,
    FileSystemOperationError,
    NativeFileSystem,
    TaskKind,
    TaskPlan,
    TaskSpec,
    TaskResolver,
    ToolchainProfile,
)


class _ApplyChangesError(ValueError):
    """Structured, model-actionable validation error for apply_changes."""

    def __init__(self, message: str, code: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details


def _normalise_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _newline_style(value: str) -> str:
    return "\r\n" if "\r\n" in value else "\n"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ListFilesTool(FoundationListFilesRuntime):
    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "",
                 workspace_root: str = ""):
        super().__init__(source_dir, test_dir, design_dir, workspace_root=workspace_root)
        self._source_dir = source_dir
        self._test_dir = test_dir
        self._design_dir = design_dir
        self._workspace_root = workspace_root
        self.name = "list_files"
        self.description = (
            "List files in the workspace matching a glob pattern. "
            "The default path is the source working directory; use source, test, "
            "design, or workspace aliases to select another scope."
        )

    async def _execute(self, params: dict) -> str:
        path = str(params.get("path") or ".").strip()
        pattern = str(params.get("pattern") or "**/*").strip()
        roots, scoped_pattern, error = self._resolve_scope(path, pattern)
        if error:
            return error
        return self._format_matches(roots, scoped_pattern)

    def _resolve_scope(self, path: str, pattern: str) -> tuple[list[str], str, str | None]:
        """Resolve a list scope without mixing an absolute path into a glob root."""
        configured = [root for root in (self._source_dir, self._test_dir, self._design_dir) if root]
        if not configured:
            return [], pattern, "(no workspace)"

        aliases = {
            "source": self._source_dir,
            "src": self._source_dir,
            "test": self._test_dir,
            "tests": self._test_dir,
            "design": self._design_dir,
        }
        normalized = path.replace("/", os.sep).rstrip("\\/") or "."
        lowered = normalized.lower()
        if normalized in {"", "."} or lowered in {"source", "src"}:
            return [self._source_dir] if self._source_dir else [], pattern, None
        if lowered == "workspace":
            return [self._workspace_root] if self._workspace_root else configured, pattern, None
        if lowered in aliases:
            root = aliases[lowered]
            return ([root] if root else [], pattern, None) if root else ([], pattern, f"Error: workspace alias not configured: {path}")

        requested = Path(normalized)
        if requested.is_absolute():
            requested = requested.resolve()
            for root in configured + ([self._workspace_root] if self._workspace_root else []):
                root_path = Path(root).resolve()
                try:
                    relative = requested.relative_to(root_path)
                except ValueError:
                    continue
                if not requested.exists() or not requested.is_dir():
                    return [], pattern, f"Error: directory not found: {path}"
                scoped = os.path.join(str(relative), pattern) if str(relative) != "." else pattern
                return [root], scoped, None
            return [], pattern, f"Error: path escapes workspace: {path}"

        # Resolve a relative subdirectory against each configured root. This
        # keeps paths such as ``radar_sim`` useful while preserving boundaries.
        for root in ([self._workspace_root] if self._workspace_root else []) + configured:
            root_path = Path(root).resolve()
            candidate = (root_path / requested).resolve()
            if candidate.is_dir() and candidate.is_relative_to(root_path):
                relative = candidate.relative_to(root_path)
                scoped = os.path.join(str(relative), pattern) if str(relative) != "." else pattern
                return [root], scoped, None
        return [], pattern, f"Error: directory not found: {path}"

    def to_openai_schema(self) -> dict:
        schema = super().to_openai_schema()
        props = schema["function"]["parameters"]["properties"]
        props["path"] = {
            "type": "string",
            "description": (
                "Directory scope: source, test, design, workspace, an allowed absolute path, "
                "or a relative subdirectory. Defaults to source."
            ),
        }
        return schema


class ApplyChangesTool(Tool):
    """Apply a validated batch of semantic workspace changes."""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "", change_set=None,
                 workspace_root: str = ""):
        self._roots = _resolve_roots(workspace_root, source_dir, test_dir, design_dir)
        self._change_set = change_set
        super().__init__(name="apply_changes", description=(
            "Apply one or more semantic workspace changes atomically. Supported operations: "
            "create, replace, patch, delete, move, copy, and mkdir. Use this for all "
            "file changes instead of shell commands; paths stay inside the workspace "
            "and expected_sha256 prevents overwriting concurrent edits."
        ))
        self._workspace_root = workspace_root
        self._source_dir = source_dir
        self._test_dir = test_dir
        self._design_dir = design_dir
        self._filesystem = NativeFileSystem()

    def get_parameters(self) -> list:
        return []

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "changes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "op": {
                                        "type": "string",
                                        "enum": [
                                            "create", "replace", "patch", "delete",
                                            "move", "copy", "mkdir",
                                        ],
                                    },
                                    "path": {"type": "string"},
                                    "from": {"type": "string"},
                                    "to": {"type": "string"},
                                    "content": {"type": "string"},
                                    "old_text": {"type": "string"},
                                    "new_text": {"type": "string"},
                                    "expected_sha256": {"type": "string"},
                                },
                                "required": ["op"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["changes"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, parameters: dict) -> str:
        return self.run_result(parameters).text

    def run_result(self, parameters: dict) -> ToolResult:
        return ToolResult.from_value(self._apply_changes(parameters))

    @staticmethod
    def _error_result(
        message: str,
        code: str,
        *,
        retryable: bool = True,
        **details: Any,
    ) -> ToolResult:
        payload = {
            "error": message,
            "error_code": code,
            "retryable": retryable,
            **details,
        }
        if retryable:
            payload.setdefault(
                "recovery_action",
                "Inspect the reported target and retry with a smaller, current-state change.",
            )
        return ToolResult.error(payload, code, retryable=retryable)

    @staticmethod
    def _error_details(
        change: dict[str, Any],
        index: int,
        operation: str,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {
            "change_index": index,
            "operation": operation,
        }
        path = change.get("path") or change.get("to") or change.get("from")
        if isinstance(path, str) and path.strip():
            details["path"] = path
        return details

    def _classify_error(
        self,
        exc: Exception,
        change: dict[str, Any],
        index: int,
        operation: str,
        states: dict[str, dict[str, Any]],
    ) -> ToolResult:
        message = str(exc)
        details = self._error_details(change, index, operation)
        if isinstance(exc, _ApplyChangesError):
            # Keep the path as supplied by the model so the recovery call can
            # reuse it.  Preserve a resolved path separately for diagnostics.
            custom_details = dict(exc.details)
            resolved_path = custom_details.pop("path", None)
            if resolved_path is not None:
                if details.get("path"):
                    details["resolved_path"] = resolved_path
                else:
                    details["path"] = resolved_path
            details.update(custom_details)
            return self._error_result(
                message,
                exc.code,
                **details,
            )
        if "text not found" in message.lower():
            state = next(
                (
                    value for value in states.values()
                    if str(value.get("path")) == str(details.get("path"))
                ),
                None,
            )
            if state and state.get("initial_raw") is not None:
                details["current_file_sha256"] = hashlib.sha256(
                    state["initial_raw"]
                ).hexdigest()
            old_text = change.get("old_text")
            if isinstance(old_text, str):
                details["old_text_sha256"] = _sha256_text(old_text)
            return self._error_result(
                message,
                "PATCH_TEXT_NOT_FOUND",
                recovery_action=(
                    "Read or search the current target file, then rebuild the smallest patch. "
                    "Do not repeat the same old_text."
                ),
                **details,
            )
        if "unsupported operation" in message.lower():
            return self._error_result(
                message,
                "UNSUPPORTED_OPERATION",
                recovery_action="Use one of create, replace, patch, delete, move, copy, or mkdir.",
                **details,
            )
        if "changed since it was read" in message.lower():
            return self._error_result(
                message,
                "EXPECTED_SHA_MISMATCH",
                recovery_action="Re-read the target file and rebuild the change from its current contents.",
                **details,
            )
        if "concurrent change detected" in message.lower():
            return self._error_result(
                message,
                "CONCURRENT_CHANGE",
                recovery_action="Re-read the target and retry only after confirming the current state.",
                **details,
            )
        return self._error_result(message, "TOOL_REPORTED_ERROR", **details)

    def _apply_changes(self, parameters: dict):
        changes = parameters.get("changes")
        if not isinstance(changes, list) or not changes:
            return self._error_result(
                "changes must be a non-empty list",
                "INVALID_CHANGE_BATCH",
                recovery_action="Send a non-empty changes array.",
            )

        states: dict[str, dict[str, Any]] = {}
        operations: list[str] = []
        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                return self._error_result(
                    f"changes[{index}] must be an object",
                    "INVALID_CHANGE",
                    change_index=index,
                    recovery_action="Send each change as an object with an operation and target.",
                )
            operation = str(change.get("op") or "").lower().strip()
            try:
                self._plan_change(states, operation, change, index)
            except (OSError, ValueError, FileSystemOperationError) as exc:
                return self._classify_error(
                    exc, change, index, operation, states,
                )
            operations.append(operation)

        try:
            self._commit_states(states)
        except (OSError, ValueError, FileSystemOperationError) as exc:
            self._restore_states(states)
            return self._error_result(
                f"changes rolled back: {exc}",
                "CHANGE_COMMIT_FAILED",
                recovery_action="Re-read affected files and retry the smallest valid change batch.",
            )
        result = ToolResult.success("Applied changes: " + ", ".join(operations))
        for state in states.values():
            if (state["exists"], state["is_dir"], state["content"]) == (
                state["initial_exists"], state["initial_is_dir"], state["initial_content"],
            ):
                continue
            before = state["initial_raw"]
            content = state["content"]
            after = content.encode("utf-8") if isinstance(content, str) else content
            operation = "delete" if not state["exists"] else (
                "mkdir" if state["is_dir"] else (
                    "create" if not state["initial_exists"] else "replace"
                )
            )
            result.changes.append(FileChange(
                str(state["path"]), operation,
                hashlib.sha256(before).hexdigest() if before is not None else "",
                hashlib.sha256(after).hexdigest() if state["exists"] and after is not None else "",
            ))
        return result

    def _path(self, value: Any, index: int, field: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        value = _expand_workspace_alias(
            value, self._workspace_root, self._source_dir,
            self._test_dir, self._design_dir,
        )
        return safe_path(value, self._roots, require_exist=False)

    def _state(self, states: dict[str, dict[str, Any]], path: Path) -> dict[str, Any]:
        key = str(path.resolve())
        if key not in states:
            exists = path.exists()
            is_dir = exists and path.is_dir()
            content = None
            if exists and not is_dir:
                raw = self._filesystem.read_bytes(path)
                try:
                    content = raw.decode("utf-8")
                    is_text = True
                except UnicodeDecodeError:
                    content = raw
                    is_text = False
            else:
                raw = None
                is_text = False
            states[key] = {
                "path": path,
                "initial_exists": exists,
                "initial_is_dir": is_dir,
                "initial_content": content,
                "initial_raw": raw,
                "initial_is_text": is_text,
                "exists": exists,
                "is_dir": is_dir,
                "content": content,
                "is_text": is_text,
            }
        return states[key]

    @staticmethod
    def _file_content(state: dict[str, Any], label: str) -> str:
        if not state["exists"]:
            raise ValueError(f"{label} does not exist")
        if state["is_dir"]:
            raise ValueError(f"{label} must be a file")
        if not state["is_text"]:
            raise ValueError(f"{label} must be a UTF-8 text file for this operation")
        return str(state["content"] or "")

    @staticmethod
    def _file_value(state: dict[str, Any], label: str) -> str | bytes:
        if not state["exists"]:
            raise ValueError(f"{label} does not exist")
        if state["is_dir"]:
            raise ValueError(f"{label} must be a file")
        return state["content"] if state["is_text"] else bytes(state["content"] or b"")

    @staticmethod
    def _check_expected(state: dict[str, Any], expected: Any, label: str) -> None:
        if not expected:
            return
        raw = state.get("initial_raw")
        actual = hashlib.sha256(raw).hexdigest() if raw is not None else ""
        if str(expected).lower() != actual.lower():
            raise _ApplyChangesError(
                f"{label} changed since it was read; expected sha256 {expected}, actual {actual}",
                "EXPECTED_SHA_MISMATCH",
                expected_sha256=str(expected),
                actual_sha256=actual,
            )

    def _plan_change(
        self, states: dict[str, dict[str, Any]], operation: str,
        change: dict[str, Any], index: int,
    ) -> None:
        supported = {"create", "replace", "patch", "delete", "move", "copy", "mkdir"}
        if operation not in supported:
            raise _ApplyChangesError(
                f"unsupported operation '{operation}'",
                "UNSUPPORTED_OPERATION",
            )

        if operation in {"move", "copy"}:
            source = self._state(states, self._path(change.get("from"), index, "from"))
            target = self._state(states, self._path(change.get("to"), index, "to"))
            content = self._file_value(source, "source")
            self._check_expected(source, change.get("expected_sha256"), "source")
            if target["exists"]:
                raise ValueError("target already exists")
            target.update(
                exists=True, is_dir=False, content=content,
                is_text=source["is_text"],
            )
            if operation == "move":
                source.update(exists=False, is_dir=False, content=None)
            return

        state = self._state(states, self._path(change.get("path"), index, "path"))
        if operation == "create":
            if state["exists"]:
                raise ValueError("target already exists")
            content = change.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            state.update(exists=True, is_dir=False, content=content, is_text=True)
        elif operation == "replace":
            self._file_content(state, "target")
            self._check_expected(state, change.get("expected_sha256"), "target")
            content = change.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            state.update(content=content, is_text=True)
        elif operation == "patch":
            current = self._file_content(state, "target")
            self._check_expected(state, change.get("expected_sha256"), "target")
            old_text = change.get("old_text")
            new_text = change.get("new_text")
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                raise ValueError("old_text and new_text must be strings")
            if old_text in current:
                state["content"] = current.replace(old_text, new_text, 1)
                return

            normalized_current = _normalise_newlines(current)
            normalized_old = _normalise_newlines(old_text)
            normalized_new = _normalise_newlines(new_text)
            match_count = normalized_current.count(normalized_old)
            if match_count == 1:
                patched = normalized_current.replace(normalized_old, normalized_new, 1)
                state["content"] = patched.replace("\n", _newline_style(current))
                return
            if match_count > 1:
                raise _ApplyChangesError(
                    f"patch text matches {match_count} locations in {state['path']}",
                    "PATCH_AMBIGUOUS",
                    path=str(state["path"]),
                    match_count=match_count,
                    old_text_sha256=_sha256_text(old_text),
                    recovery_action="Read a narrower range and provide a unique patch anchor.",
                )
            raise _ApplyChangesError(
                f"text not found in {state['path']}",
                "PATCH_TEXT_NOT_FOUND",
                path=str(state["path"]),
                old_text_sha256=_sha256_text(old_text),
                current_file_sha256=hashlib.sha256(
                    bytes(state["initial_raw"] or b"")
                ).hexdigest(),
                recovery_action=(
                    "Read or search the current target file, then rebuild the smallest patch. "
                    "Do not repeat the same old_text."
                ),
            )
        elif operation == "delete":
            if not state["exists"]:
                raise ValueError("target does not exist")
            if not state["is_dir"]:
                self._check_expected(state, change.get("expected_sha256"), "target")
            elif change.get("expected_sha256"):
                raise ValueError("expected_sha256 is only supported for files")
            state.update(exists=False, is_dir=False, content=None, is_text=False)
        elif operation == "mkdir":
            if state["exists"]:
                raise ValueError("target already exists")
            state.update(exists=True, is_dir=True, content=None, is_text=False)

    def _assert_unchanged(self, state: dict[str, Any]) -> None:
        path = state["path"]
        exists = path.exists()
        if exists != state["initial_exists"]:
            raise ValueError(f"concurrent change detected: {path}")
        if exists and path.is_dir() != state["initial_is_dir"]:
            raise ValueError(f"concurrent change detected: {path}")
        if exists and not state["initial_is_dir"]:
            if self._filesystem.read_bytes(path) != state["initial_raw"]:
                raise ValueError(f"concurrent change detected: {path}")

    def _commit_states(self, states: dict[str, dict[str, Any]]) -> None:
        for state in states.values():
            self._assert_unchanged(state)
        changed = [
            state for state in states.values()
            if (state["exists"], state["is_dir"], state["content"])
            != (state["initial_exists"], state["initial_is_dir"], state["initial_content"])
        ]
        for state in sorted(changed, key=lambda item: len(item["path"].parts), reverse=True):
            if state["initial_exists"] and not state["exists"]:
                if state["initial_is_dir"]:
                    self._filesystem.delete_directory(state["path"])
                else:
                    self._filesystem.delete_file(state["path"])
        for state in sorted(changed, key=lambda item: len(item["path"].parts)):
            if not state["exists"]:
                continue
            path = state["path"]
            if state["is_dir"]:
                if not path.exists():
                    self._filesystem.make_directory(path)
            else:
                if state["is_text"]:
                    self._filesystem.write_text(path, str(state["content"] or ""))
                else:
                    self._filesystem.write_bytes(path, bytes(state["content"] or b""))
        for state in changed:
            if (
                self._change_set is not None
                and not state["is_dir"]
                and not state["initial_is_dir"]
                and (state["initial_is_text"] or state["is_text"])
            ):
                self._change_set.record(
                    str(state["path"]), state["initial_exists"],
                    str(state["initial_content"] or ""),
                    str(state["content"] or "") if state["exists"] else "",
                )

    def _restore_states(self, states: dict[str, dict[str, Any]]) -> None:
        for state in sorted(states.values(), key=lambda item: len(item["path"].parts), reverse=True):
            path = state["path"]
            try:
                if path.exists() and not state["initial_exists"]:
                    if path.is_dir():
                        self._filesystem.delete_directory(path)
                    else:
                        self._filesystem.delete_file(path)
            except FileSystemOperationError:
                pass
        for state in sorted(states.values(), key=lambda item: len(item["path"].parts)):
            if not state["initial_exists"]:
                continue
            try:
                if state["initial_is_dir"]:
                    if not state["path"].exists():
                        self._filesystem.make_directory(state["path"])
                else:
                    if state["initial_is_text"]:
                        self._filesystem.write_text(
                            state["path"], str(state["initial_content"] or "")
                        )
                    else:
                        self._filesystem.write_bytes(
                            state["path"], bytes(state["initial_raw"] or b"")
                        )
            except FileSystemOperationError:
                pass


def _quote_program(program: str, args: list[str], executor) -> str:
    values = [program, *args]
    if getattr(getattr(executor, "profile", None), "name", "") == "windows-powershell":
        return "& " + " ".join("'" + value.replace("'", "''") + "'" for value in values)
    return shlex.join(values)


class RunProgramTool(ShellTool):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "run_program"
        self.description = (
            "Run one allowlisted executable directly with literal argv in the workspace. "
            "Use for Python/Node/pytest or another program when args can be separate strings; "
            "do not pass powershell/cmd/bash, -Command, pipes, chaining, or shell syntax. "
            "Use run_task for test/build/lint/format/typecheck/validate."
        )

    async def _execute(self, params: dict) -> str:
        return (await self.run_result(params)).text

    async def _execute_result(self, params: dict):
        program = params.get("program", "")
        args = params.get("args", [])
        if not isinstance(program, str) or not program.strip():
            return "Error: program must be a non-empty string"
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            return "Error: args must be a list of strings"
        program = program.strip()
        cwd, cwd_error = self._resolve_cwd(params.get("cwd"))
        if cwd_error:
            return f"Error: {cwd_error}"
        validator = getattr(self._command_executor, "validate_program", None)
        if callable(validator):
            validation_error = validator(program, args)
        else:
            validation_error = self._validate_shell_command(
                _quote_program(program, args, self._command_executor)
            )
        if validation_error:
            return (
                f"Error: {validation_error} "
                "run_program accepts a direct executable and literal argv only; "
                "use run_task for standard project tasks; use shell only for one simple "
                "native command that does not require interpreter code."
            )

        display_command = _quote_program(program, args, self._command_executor)
        risk = self._risk_policy.evaluate("shell", {"command": display_command})
        if risk.action == "deny":
            return f"Error: program denied (high-risk, matches deny list: {risk.pattern})"
        if risk.action == "ask":
            verdict = await self._request_approval(
                display_command, risk,
                self._risk_policy.approval_scope("shell", {"command": display_command}),
            )
            if verdict is not None:
                return verdict
        return await self._run_program_cancellable(program, args, cwd)

    async def _run_program_cancellable(
        self, program: str, args: list[str], cwd: str | None,
    ) -> str:
        def _start():
            return self._command_executor.start_program(program, args, cwd)

        try:
            proc = await asyncio.to_thread(_start)
        except (OSError, ExecutionEnvironmentError) as exc:
            return f"Error: {type(exc).__name__}: {exc}"

        communicate = asyncio.create_task(asyncio.to_thread(proc.communicate))
        deadline = asyncio.get_running_loop().time() + self._timeout
        try:
            while not communicate.done():
                if get_runtime().stop_check():
                    self._command_executor.terminate(proc)
                    await asyncio.shield(communicate)
                    return "Error: program canceled"
                if asyncio.get_running_loop().time() >= deadline:
                    self._command_executor.terminate(proc)
                    await asyncio.shield(communicate)
                    return f"Error: program timed out after {self._timeout:g}s"
                await asyncio.sleep(0.05)
            stdout, stderr = await communicate
        except asyncio.CancelledError:
            self._command_executor.terminate(proc)
            await asyncio.shield(communicate)
            raise
        except OSError as exc:
            return f"Error: {type(exc).__name__}: {exc}"

        output = (_decode_output(stdout) + _decode_output(stderr)).strip()
        output = output[:self._output_cap] if len(output) > self._output_cap else output
        if proc.returncode:
            output = f"Error: program exited with code {proc.returncode}: {output or '(no output)'}"
        return command_result(
            _quote_program(program, args, self._command_executor), cwd,
            proc.returncode, output or "(no output)", argv=[program, *args],
        )

    def to_openai_schema(self) -> dict:
        return {
            "type": "function", "function": {
                "name": self.name, "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "program": {"type": "string"},
                        "args": {"type": "array", "items": {"type": "string"}},
                        "cwd": {"type": "string"},
                    },
                    "required": ["program"], "additionalProperties": False,
                },
            },
        }


class RunTaskTool(RunProgramTool):
    TASKS = {
        "test": ("python", ["-m", "pytest"]),
        "build": ("npm", ["run", "build"]),
        "lint": ("ruff", ["check"]),
        "format": ("ruff", ["format"]),
        "typecheck": ("mypy", []),
        "validate": ("python", ["-m", "pytest"]),
    }

    def __init__(self, *args, execution_broker=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "run_task"
        self.description = (
            "Run a semantic project task such as test, build, lint, format, typecheck, or validate; "
            "custom task names declared by the project manifest are also accepted. "
            "Tasks are resolved from the project's build metadata when available; "
            "use this for project verification/build work instead of composing commands. "
            "The runtime may execute required prerequisite tasks automatically and returns "
            "step-level execution evidence. Use profile to select a build variant and "
            "dry_run=true to preview the plan without executing it. "
            "validate checks UML project files directly for .umlproj/.uml/.json targets. "
            "target is relative to cwd; cwd accepts source, test, design, or workspace. "
            "For the full test suite use cwd=\"test\" with no target or target=\".\"."
        )
        self._task_resolver = TaskResolver()
        self._execution_broker = execution_broker

    @property
    def execution_broker(self):
        """Expose the execution capability for composition-root wiring."""
        return self._execution_broker

    async def _execute_result(self, params: dict):
        task = str(params.get("task", "")).lower().strip()
        if not task:
            return "Error: task must be a non-empty string"
        profile = params.get("profile", "")
        if profile is None:
            profile = ""
        if not isinstance(profile, str):
            return "Error: profile must be a string"
        profile = profile.strip().lower()
        dry_run = params.get("dry_run", False)
        if not isinstance(dry_run, bool):
            return "Error: dry_run must be a boolean"
        if task == "validate" and params.get("target"):
            target = str(params["target"]).strip()
            if target.lower().endswith((".umlproj", ".uml", ".json")):
                result = ToolResult.from_value(self._validate_project_file(target, params.get("cwd")))
                result.verification = VerificationEvidence(
                    "validate", str(params.get("cwd") or "") + "/" + target,
                    result.status == "success",
                )
                return result
        target = params.get("target")
        if target:
            if not isinstance(target, str):
                return "Error: target must be a string"
        resolved_cwd, cwd_error = self._resolve_cwd(params.get("cwd"))
        if cwd_error:
            return f"Error: {cwd_error}"
        resolution = self._task_resolver.resolve(
            task, resolved_cwd or self._cwd, target=target,
            profile=profile,
        )
        if resolution.project_root and not resolution.resolved:
            return (
                f"Error: unable to resolve task '{task}' for project "
                f"{resolution.project_root}: {resolution.reason}"
            )
        if resolution.resolved:
            if dry_run:
                return self._plan_result(resolution)
            if self._execution_broker is not None:
                return await self._execute_resolved_task(resolution, target)
            program, args = resolution.task.argv[0], list(resolution.task.argv[1:])
            execution_cwd = resolution.project_root
        else:
            if task not in self.TASKS:
                return (
                    f"Error: unable to resolve project task '{task}'. "
                    "Declare it in .architectcoder/tasks.json or use a supported project manifest."
                )
            program, base_args = self.TASKS[task]
            args = list(base_args)
            execution_cwd = resolved_cwd
            # A model may carry the cwd alias into target as well. Treat
            # ``target=test, cwd=test`` as the intended full-suite command
            # instead of executing pytest against the non-existent test/test.
            raw_cwd = params.get("cwd")
            if not (
                task == "test"
                and isinstance(raw_cwd, str)
                and target.strip().lower() == raw_cwd.strip().lower()
            ):
                args.append(target)
        if dry_run:
            try:
                kind = TaskKind(task) if task in TaskKind._value2member_map_ else TaskKind.CUSTOM
                legacy_task = TaskSpec(
                    task_id=f"compatibility.{task}", kind=kind,
                    argv=(program, *args), toolchain_id="compatibility",
                    source="run_task-fallback",
                )
            except ValueError as exc:
                return f"Error: unable to create task plan: {exc}"
            return self._plan_result(
                TaskPlan(
                    requested_task=task, steps=(legacy_task,), profile=profile,
                    rationale="compatibility task fallback",
                )
            )
        result = ToolResult.from_value(await RunProgramTool._execute_result(self, {
            "program": program, "args": args, "cwd": execution_cwd,
        }))
        if task != "format" and result.execution is not None:
            result.verification = VerificationEvidence(
                task, result.execution.cwd + "/" + str(target or "."),
                result.execution.exit_code == 0, result.execution.exit_code,
            )
        return result

    @staticmethod
    def _plan_result(resolution) -> ToolResult:
        """Return a non-mutating, model-visible execution plan."""
        plan = resolution if isinstance(resolution, TaskPlan) else resolution.plan
        if plan is None:
            return ToolResult.error("Error: resolved task has no execution plan", "TASK_PLAN_INVALID")
        payload = plan.to_dict()
        return ToolResult(
            status="success",
            data=payload,
            execution_evidence={
                "kind": "task_plan",
                "status": "planned",
                "plan": payload,
            },
        )

    async def _execute_resolved_task(self, resolution, target: str | None) -> ToolResult:
        """Run a resolved task plan through the broker and preserve evidence.

        Adapters may attach prerequisite tasks (for example CMake configure
        before build, or build before test).  The model still makes one
        semantic ``run_task`` call; orchestration remains deterministic and
        inside the runtime rather than being delegated to prompt wording.
        """
        plan = (*getattr(resolution, "prerequisites", ()), resolution.task)
        steps: list[tuple[TaskSpec, ToolResult]] = []
        for planned_task in plan:
            step_result = await self._execute_task_spec(planned_task, resolution.project_root)
            steps.append((planned_task, step_result))
            if step_result.status != "success":
                break

        if len(steps) == 1:
            return steps[0][1]

        final_task = resolution.task
        final_result = steps[-1][1]
        failed = next(((task, result) for task, result in steps if result.status != "success"), None)
        overall_status = "success" if failed is None else failed[1].status
        execution_plan = getattr(resolution, "plan", None)
        step_payload = []
        for planned_task, result in steps:
            step_payload.append({
                "task_id": planned_task.task_id,
                "kind": planned_task.kind.value,
                "status": result.status,
                "command": list(planned_task.argv),
                "cwd": result.execution.cwd if result.execution else resolution.project_root,
                "output": result.text,
                "error_code": result.error_code,
                "execution_evidence": result.execution_evidence,
            })
        evidence = {
            "task_id": final_task.task_id,
            "status": overall_status,
            "profile": execution_plan.profile if execution_plan is not None else "",
            "failed_step": failed[0].task_id if failed is not None else None,
            "plan": (
                execution_plan.to_dict()
                if execution_plan is not None
                else {"requested_task": final_task.kind.value, "steps": [task.to_dict() for task in plan]}
            ),
            "steps": step_payload,
        }
        last_execution = next(
            (result.execution for _task, result in reversed(steps) if result.execution is not None),
            None,
        )
        verification = final_result.verification
        if verification is None and overall_status != "success":
            verification = VerificationEvidence(
                final_task.kind.value, final_task.task_id, False,
                last_execution.exit_code if last_execution else None,
            )
        return ToolResult(
            status="success" if overall_status == "success" else overall_status,
            data={
                "task": final_task.task_id,
                "status": overall_status,
                "steps": step_payload,
                "output": final_result.text,
            },
            error_code="" if overall_status == "success" else (
                "TASK_PREREQUISITE_FAILED" if failed and failed[0] is not final_task
                else final_result.error_code
            ),
            execution=last_execution,
            verification=verification,
            execution_evidence=evidence,
        )

    async def _execute_task_spec(self, task: TaskSpec, project_root: str) -> ToolResult:
        """Execute one task spec; kept separate so plans share one policy path."""
        execution_cwd, cwd_error = self._resolve_task_cwd(task.cwd, project_root)
        if cwd_error:
            return ToolResult(
                status="blocked",
                data=f"Error: task cwd policy violation: {cwd_error}",
                error_code="TASK_PATH_POLICY",
            )
        display_command = _quote_program(task.argv[0], list(task.argv[1:]), self._command_executor)
        risk = self._risk_policy.evaluate("shell", {"command": display_command})
        if risk.action == "deny":
            return ToolResult(
                status="blocked", data=f"Error: task denied (high-risk, matches {risk.pattern})",
                error_code="TASK_DENIED",
            )
        if risk.action == "ask":
            verdict = await self._request_approval(
                display_command, risk,
                self._risk_policy.approval_scope("shell", {"command": display_command}),
            )
            if verdict is not None:
                return ToolResult.from_value(verdict)
        execution_task = task
        if task.approval is ApprovalClass.USER_APPROVAL:
            verdict = await self._request_approval(display_command, "task approval required")
            if verdict is not None:
                return ToolResult.from_value(verdict)
            execution_task = replace(task, approval=ApprovalClass.SANDBOX_AUTO)
        policy = ExecutionPolicy(
            sandbox=str(getattr(self._execution_broker, "sandbox_name", "workspace")),
            network=execution_task.network,
            approval=execution_task.approval,
            environment=getattr(self._command_executor.profile, "name", "unknown"),
        )
        evidence = await self._execution_broker.execute(
            execution_task, execution_cwd, policy=policy,
        )
        status = "success" if evidence.status == "success" else (
            "blocked" if evidence.status == "blocked" else "error"
        )
        result = ToolResult(
            status=status,
            data=evidence.output,
            error_code="" if status == "success" else f"TASK_{evidence.status.upper()}",
            execution=(
                CommandEvidence(display_command, evidence.cwd, evidence.exit_code)
                if evidence.exit_code is not None else None
            ),
            execution_evidence=evidence.to_dict(),
        )
        if task.kind.value not in {"format", "custom"} and evidence.exit_code is not None:
            result.verification = VerificationEvidence(
                task.kind.value, display_command, evidence.status == "success", evidence.exit_code,
            )
        return result

    @staticmethod
    def _resolve_task_cwd(raw_cwd: str, project_root: str) -> tuple[str | None, str | None]:
        """Resolve a manifest cwd relative to its detected project root.

        ``TaskSpec.cwd`` is intentionally project-relative for declarative
        manifests.  Keeping this check at the tool/broker boundary prevents a
        manifest from escaping the project root while still allowing build
        directories such as ``build`` or ``out``.
        """
        root = Path(project_root).expanduser().resolve()
        value = str(raw_cwd or "workspace").strip()
        if not value or value in {".", "workspace"}:
            candidate = root
        else:
            path = Path(value).expanduser()
            candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
        try:
            inside = candidate == root or candidate.is_relative_to(root)
        except ValueError:
            inside = False
        if not inside:
            return None, "cwd is outside the project root"
        if not candidate.is_dir():
            return None, f"cwd directory does not exist: {value}"
        return str(candidate), None

    def _validate_project_file(self, target: str, raw_cwd) -> str:
        candidates: list[Path] = []
        if raw_cwd:
            cwd, cwd_error = self._resolve_cwd(raw_cwd)
            if cwd_error:
                return f"Error: {cwd_error}"
            candidates.append(Path(cwd) / target)
        # Accept both canonical forms:
        #   target="model.umlproj", cwd="design"
        #   target="design/model.umlproj"
        # The second form is common when an Agent carries the workspace alias
        # into a task target, and should not become workspace/design/design/...
        candidates.append(Path(target))
        path = None
        last_error: Exception | None = None
        seen: set[str] = set()
        for candidate in candidates:
            try:
                key = str(candidate)
                if key in seen:
                    continue
                seen.add(key)
                path = safe_path(str(candidate), self._roots, require_exist=True)
                if not path.is_file():
                    raise FileNotFoundError(path)
                break
            except (OSError, ValueError) as exc:
                last_error = exc
        if path is None:
            return f"Error: {last_error or 'project file not found'}"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return f"Error: invalid project JSON: {exc}"
        diagrams = data.get("diagrams") if isinstance(data, dict) else None
        if not isinstance(diagrams, list) or not diagrams:
            return "Error: UML project must contain a non-empty diagrams list"
        if not all(isinstance(diagram, dict) for diagram in diagrams):
            return "Error: UML project diagrams must be objects"
        return f"Validated UML project: {path} (diagrams={len(diagrams)})"

    def to_openai_schema(self) -> dict:
        return {
            "type": "function", "function": {
                "name": self.name, "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "Semantic task name such as build/test/lint, or a custom task "
                                "declared by the project's task manifest."
                            ),
                        },
                        "target": {"type": "string"},
                        "cwd": {"type": "string"},
                        "profile": {
                            "type": "string",
                            "description": "Optional build profile such as debug, release, asan, or coverage.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "If true, return the resolved execution plan without starting processes.",
                        },
                    },
                    "required": ["task"], "additionalProperties": False,
                },
            },
        }


def create_foundation_tools(
    source_dir: str = "", test_dir: str = "", design_dir: str = "",
    review_manager=None, progress=None, change_set=None, command_executor=None,
    workspace_root: str = "", execution_broker=None,
) -> list[Tool]:
    common = dict(
        source_dir=source_dir, test_dir=test_dir, design_dir=design_dir,
        review_manager=review_manager, progress=progress,
        command_executor=command_executor,
        workspace_root=workspace_root,
    )
    return [
        ListFilesTool(source_dir, test_dir, design_dir, workspace_root=workspace_root),
        ReadFileTool(
            source_dir, test_dir, design_dir,
            change_set=change_set, workspace_root=workspace_root,
        ),
        SearchTextTool(source_dir, test_dir, design_dir, workspace_root=workspace_root),
        ApplyChangesTool(
            source_dir, test_dir, design_dir,
            change_set=change_set, workspace_root=workspace_root,
        ),
        RunProgramTool(**common),
        RunTaskTool(**common, execution_broker=execution_broker),
        ShellTool(**common),
    ]


__all__ = [
    "ApplyChangesTool", "ListFilesTool", "RunProgramTool", "RunTaskTool",
    "ShellTool", "create_foundation_tools",
]
