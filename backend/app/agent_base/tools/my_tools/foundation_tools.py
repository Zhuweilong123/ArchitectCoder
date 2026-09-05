"""Stable, OS-neutral tools exposed to the main DevAgent.

These tools express capabilities rather than host commands.  The legacy file
tools remain available to bounded subagents and compatibility callers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
from typing import Any
from pathlib import Path

from app.agent_base.core.hooks import get_runtime
from app.agent_base.execution import ExecutionEnvironmentError
from app.agent_base.tools.base import Tool
from app.agent_base.tools.my_tools.file_system_tools import (
    BashTool,
    GlobTool,
    ReadFileTool,
    SearchTextTool,
    _atomic_write_text,
    _decode_output,
    _expand_workspace_alias,
    _resolve_roots,
    _sha256_text,
    safe_path,
)
from app.runtime import FileSystemOperationError, NativeFileSystem


class ListFilesTool(GlobTool):
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


class ApplyPatchTool(Tool):
    """Apply exact, revision-checked text patches to one or more files."""

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "", change_set=None,
                 workspace_root: str = ""):
        super().__init__(
            name="apply_patch",
            description=(
                "Apply one or more exact text patches inside the workspace. "
                "Each patch replaces old_text with new_text once. Read the file first; "
                "use expected_sha256 to prevent overwriting concurrent edits."
            ),
        )
        self._roots = _resolve_roots(workspace_root, source_dir, test_dir, design_dir)
        self._change_set = change_set

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
                        "patches": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "old_text": {"type": "string"},
                                    "new_text": {"type": "string"},
                                    "expected_sha256": {"type": "string"},
                                },
                                "required": ["path", "old_text", "new_text"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["patches"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, parameters: dict) -> str:
        patches = parameters.get("patches")
        if not isinstance(patches, list) or not patches:
            return "Error: patches must be a non-empty list"

        prepared: dict[str, dict[str, Any]] = {}
        patch_order: list[tuple[str, dict]] = []
        for index, patch in enumerate(patches):
            if not isinstance(patch, dict):
                return f"Error: patches[{index}] must be an object"
            path = patch.get("path", "")
            old_text = patch.get("old_text", "")
            new_text = patch.get("new_text", "")
            if not all(isinstance(value, str) for value in (path, old_text, new_text)):
                return f"Error: patches[{index}] path, old_text, and new_text must be strings"
            try:
                target = safe_path(path, self._roots, require_exist=False)
                key = str(target.resolve())
                state = prepared.get(key)
                if state is None:
                    current = target.read_text(encoding="utf-8") if target.exists() else ""
                    state = {
                        "target": target,
                        "original": current,
                        "working": current,
                    }
                    prepared[key] = state
                current = state["working"]
            except (OSError, ValueError) as exc:
                return f"Error: patches[{index}] {exc}"
            expected = patch.get("expected_sha256")
            actual = _sha256_text(state["original"])
            if expected and str(expected).lower() != actual.lower():
                return f"Conflict: {path} changed since it was read; expected sha256 {expected}, actual {actual}"
            if old_text not in current:
                if current or old_text:
                    return f"Error: text not found in {path}"
                updated = new_text
            else:
                updated = current.replace(old_text, new_text, 1)
            state["working"] = updated
            patch_order.append((key, patch))

        try:
            for state in prepared.values():
                target = state["target"]
                current = state["original"]
                updated = state["working"]
                before_exists = target.exists()
                _atomic_write_text(target, updated)
                if self._change_set is not None:
                    self._change_set.record(str(target), before_exists, current, updated)
        except OSError as exc:
            return f"Error: {exc}"
        return "Applied patches: " + ", ".join(str(patch["path"]) for _, patch in patch_order)


class ApplyChangesTool(ApplyPatchTool):
    """Apply a validated batch of semantic workspace changes.

    ``apply_patch`` remains available to compatibility callers.  The
    model-facing tool uses this broader protocol so file creation, replacement,
    deletion, moves, copies, and directory creation share one policy boundary.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "apply_changes"
        self.description = (
            "Apply one or more semantic workspace changes atomically. Supported operations: "
            "create, replace, patch, delete, move, copy, and mkdir. Use this for all "
            "file changes instead of shell commands; paths stay inside the workspace "
            "and expected_sha256 prevents overwriting concurrent edits."
        )
        self._workspace_root = kwargs.get("workspace_root", "")
        self._source_dir = args[0] if len(args) > 0 else kwargs.get("source_dir", "")
        self._test_dir = args[1] if len(args) > 1 else kwargs.get("test_dir", "")
        self._design_dir = args[2] if len(args) > 2 else kwargs.get("design_dir", "")
        self.aliases = ("apply_patch",)
        self._filesystem = NativeFileSystem()

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
        changes = parameters.get("changes")
        if changes is None and isinstance(parameters.get("patches"), list):
            changes = []
            for patch in parameters["patches"]:
                if not isinstance(patch, dict):
                    changes.append(patch)
                    continue
                old_text = patch.get("old_text", "")
                operation = "patch"
                if old_text == "":
                    try:
                        target = safe_path(patch.get("path", ""), self._roots, require_exist=False)
                        if not target.exists():
                            operation = "create"
                    except (OSError, ValueError):
                        pass
                changes.append({
                    "op": operation,
                    "path": patch.get("path"),
                    "old_text": old_text,
                    "new_text": patch.get("new_text", ""),
                    "content": patch.get("new_text", "") if operation == "create" else None,
                    "expected_sha256": patch.get("expected_sha256"),
                })
        if not isinstance(changes, list) or not changes:
            return "Error: changes must be a non-empty list"

        states: dict[str, dict[str, Any]] = {}
        operations: list[str] = []
        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                return f"Error: changes[{index}] must be an object"
            operation = str(change.get("op") or "").lower().strip()
            try:
                self._plan_change(states, operation, change, index)
            except (OSError, ValueError, FileSystemOperationError) as exc:
                return f"Error: changes[{index}] {exc}"
            operations.append(operation)

        try:
            self._commit_states(states)
        except (OSError, ValueError, FileSystemOperationError) as exc:
            self._restore_states(states)
            return f"Error: changes rolled back: {exc}"
        return "Applied changes: " + ", ".join(operations)

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
            raise ValueError(
                f"{label} changed since it was read; expected sha256 {expected}, actual {actual}"
            )

    def _plan_change(
        self, states: dict[str, dict[str, Any]], operation: str,
        change: dict[str, Any], index: int,
    ) -> None:
        supported = {"create", "replace", "patch", "delete", "move", "copy", "mkdir"}
        if operation not in supported:
            raise ValueError(f"unsupported operation '{operation}'")

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
            if old_text not in current:
                raise ValueError(f"text not found in {state['path']}")
            state["content"] = current.replace(old_text, new_text, 1)
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


class ShellTool(BashTool):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "shell"


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
            "Run an executable with an argument list in the workspace. "
            "Prefer this over shell when the operation does not need shell syntax."
        )

    async def _execute(self, params: dict) -> str:
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
            return f"Error: {validation_error}"

        display_command = _quote_program(program, args, self._command_executor)
        risk = self._risk_policy.evaluate("bash", {"command": display_command})
        if risk.action == "deny":
            return f"Error: program denied (high-risk, matches deny list: {risk.pattern})"
        if risk.action == "ask":
            verdict = await self._request_approval(
                display_command, risk,
                self._risk_policy.approval_scope("bash", {"command": display_command}),
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
            return f"Error: program exited with code {proc.returncode}: {output or '(no output)'}"
        return output or "(no output)"

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "run_task"
        self.description = (
            "Run a semantic development task. Supported tasks: test, build, lint, "
            "format, typecheck, validate. Validate checks UML project files directly "
            "when target is a .umlproj/.uml/.json file; target paths are relative to "
            "cwd when cwd is provided, and cwd accepts source, test, design, or workspace. "
            "For a full test directory, use cwd=\"test\" with no target or target=\".\"."
        )

    async def _execute(self, params: dict) -> str:
        task = str(params.get("task", "")).lower().strip()
        if task not in self.TASKS:
            return f"Error: unsupported task '{task}'"
        if task == "validate" and params.get("target"):
            target = str(params["target"]).strip()
            if target.lower().endswith((".umlproj", ".uml", ".json")):
                return self._validate_project_file(target, params.get("cwd"))
        program, base_args = self.TASKS[task]
        target = params.get("target")
        args = list(base_args)
        if target:
            if not isinstance(target, str):
                return "Error: target must be a string"
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
        return await RunProgramTool._execute(self, {
            "program": program, "args": args, "cwd": params.get("cwd"),
        })

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
                        "task": {"type": "string", "enum": list(self.TASKS)},
                        "target": {"type": "string"},
                        "cwd": {"type": "string"},
                    },
                    "required": ["task"], "additionalProperties": False,
                },
            },
        }


def create_foundation_tools(
    source_dir: str = "", test_dir: str = "", design_dir: str = "",
    review_manager=None, progress=None, change_set=None, command_executor=None,
    workspace_root: str = "",
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
        RunTaskTool(**common),
        ShellTool(**common),
    ]


__all__ = [
    "ApplyChangesTool", "ApplyPatchTool", "ListFilesTool", "RunProgramTool", "RunTaskTool",
    "ShellTool", "create_foundation_tools",
]
