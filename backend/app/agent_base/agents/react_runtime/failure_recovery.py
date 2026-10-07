"""Current tool recovery constraints, separate from historical failure evidence."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EDIT_REFRESH_CODES = frozenset({
    "PATCH_TEXT_NOT_FOUND", "PATCH_AMBIGUOUS", "EXPECTED_SHA_MISMATCH",
    "CONCURRENT_CHANGE", "PROJECT_REVISION_CONFLICT",
})
FILE_ACCESS_TOOLS = frozenset({"read_file", "search_text", "list_files"})
EXECUTION_TOOLS = frozenset({"run_program", "run_task", "shell"})
_SEARCH_LOCATION = re.compile(r"^(.+):\d+:\d+: ", re.MULTILINE)


class RecoveryScopes:
    """Identify the same target across aliases, absolute paths and batch errors."""

    def __init__(self, registry):
        self.registry = registry

    def path(self, name: str, value: str) -> str:
        tool = self.registry.get_tool(name)
        resolver = getattr(tool, "_paths", None)
        if resolver is not None:
            try:
                value = str(resolver.resolve(value))
            except (ValueError, OSError, RuntimeError):
                pass
        return os.path.normcase(os.path.normpath(value.replace("/", os.sep)))

    def paths(self, detail: dict[str, Any]) -> tuple[str, ...]:
        name = detail.get("name", "")
        args = detail.get("arguments")
        if not isinstance(args, dict):
            return ()
        if name != "apply_changes":
            path = args.get("path") or args.get("project_file")
            return (self.path(name, path),) if isinstance(path, str) and path.strip() else ()
        changes = args.get("changes")
        if not isinstance(changes, list):
            return ()
        if detail.get("status") in {"error", "blocked"}:
            try:
                payload = json.loads(detail.get("observation") or "")
            except (ValueError, TypeError):
                payload = {}
            index = payload.get("change_index") if isinstance(payload, dict) else None
            if type(index) is int and 0 <= index < len(changes):
                changes = [changes[index]]
        return tuple(sorted({
            self.path(name, change[field])
            for change in changes if isinstance(change, dict)
            for field in ("path", "from", "to")
            if isinstance(change.get(field), str) and change[field].strip()
        }))

    def invocation(self, detail: dict[str, Any]) -> str:
        args = detail.get("arguments")
        name = str(detail.get("name") or "tool")
        if name in EXECUTION_TOOLS and isinstance(args, dict):
            args = dict(args)
            tool = self.registry.get_tool(name)
            cwd = args.get("cwd") or getattr(tool, "_cwd", "")
            if isinstance(cwd, str) and cwd:
                args["cwd"] = self.path(name, cwd)
            target = args.get("target")
            if isinstance(target, str) and target:
                path, separator, selector = target.partition("::")
                resolver = getattr(tool, "_paths", None)
                qualified = getattr(tool, "_is_qualified_target", lambda value: True)
                if resolver is not None and not qualified(path):
                    try:
                        path = str(resolver.resolve(path, relative_to=args.get("cwd") or ""))
                    except (ValueError, OSError, RuntimeError):
                        pass
                args["target"] = self.path(name, path) + separator + selector
            argv = args.get("args")
            if isinstance(argv, list):
                args["args"] = [self.path(name, value) if isinstance(value, str) and Path(value).is_absolute()
                                else value for value in argv]
        return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)

    def fresh_content_paths(self, detail: dict[str, Any]) -> set[str]:
        if detail.get("status") != "success":
            return set()
        name = detail.get("name")
        if name == "read_file":
            return set(self.paths(detail))
        if name == "search_text":
            return {self.path(name, path) for path in _SEARCH_LOCATION.findall(
                str(detail.get("observation") or ""),
            )}
        return set()


@dataclass
class _Failure:
    detail: dict[str, Any]
    paths: tuple[str, ...]
    attempts: int = 1
    correction: str = ""

    @property
    def name(self) -> str:
        return str(self.detail.get("name") or "tool")

    @property
    def code(self) -> str:
        return str(self.detail.get("error_code") or "TOOL_ERROR")


class FailureRecoveryController:
    HEADER = "## Active tool recovery\n"

    def __init__(self, registry):
        self.scopes = RecoveryScopes(registry)
        self._active: dict[tuple[str, tuple[str, ...], str], _Failure] = {}

    def update(self, messages: list[dict], details: list[dict]) -> None:
        for detail in details:
            name = str(detail.get("name") or "tool")
            paths = self.scopes.paths(detail)
            invocation = self.scopes.invocation(detail)
            if detail.get("status") == "success":
                fresh = self.scopes.fresh_content_paths(detail)
                for key, failure in list(self._active.items()):
                    matched = bool(set(failure.paths) & set(paths))
                    refreshed = bool(set(failure.paths) & fresh)
                    if failure.name == "apply_changes":
                        resolved = set(failure.paths) & (
                            set(paths) if name == "apply_changes" else
                            fresh if failure.code in EDIT_REFRESH_CODES else set()
                        )
                        remaining = tuple(sorted(set(failure.paths) - resolved))
                        if resolved and remaining:
                            del self._active[key]
                            failure.paths = remaining
                            self._active[(failure.name, remaining, "")] = failure
                            continue
                        recovered = bool(resolved)
                    elif failure.name in FILE_ACCESS_TOOLS:
                        recovered = (name == failure.name and matched) or refreshed or (
                            failure.correction and failure.correction in fresh
                        )
                    else:
                        recovered = name == failure.name and invocation == self.scopes.invocation(failure.detail)
                    if not failure.paths and failure.code == "INVALID_ARGUMENT" and name == failure.name:
                        recovered = True
                    elif not failure.paths and name == failure.name and invocation == self.scopes.invocation(failure.detail):
                        recovered = True
                    if recovered:
                        del self._active[key]
                continue
            if detail.get("status") not in {"error", "blocked"}:
                continue
            # Paths identify file work; commands retain their full invocation
            # so an unrelated successful verification cannot erase a failure.
            key = (name, paths, "" if paths else invocation)
            previous = self._active.get(key)
            if name == "apply_changes" and detail.get("status") == "blocked":
                guards = [failure for failure in self._active.values()
                          if failure.name == name and failure.code in EDIT_REFRESH_CODES
                          and set(failure.paths) & set(paths)]
                if guards:
                    for guard in guards:
                        guard.attempts += 1
                    continue
            attempts = previous.attempts + 1 if previous and previous.code == detail.get("error_code") else 1
            failure = _Failure(detail, paths, attempts)
            # A single candidate offered by read_file establishes a safe
            # corrected-path relation. Ambiguous same-name files do not.
            if name == "read_file" and failure.code == "PATH_NOT_FOUND":
                candidate_line = next((line for line in str(detail.get("observation") or "").splitlines()
                                       if line.startswith("possible_paths: ")), "")
                candidates = candidate_line.removeprefix("possible_paths: ").split(", ") if candidate_line else []
                if len(candidates) == 1:
                    failure.correction = self.scopes.path(name, candidates[0])
            self._active[key] = failure
        self.sync(messages)

    def sync(self, messages: list[dict]) -> None:
        """Update one owned system message, also after context compaction."""
        content = self._render() if self._active else ""
        kept = []
        found = False
        for message in messages:
            owned = message.get("role") == "system" and str(message.get("content") or "").startswith(self.HEADER)
            if owned:
                if not content or found:
                    continue
                message["content"] = content
                found = True
            kept.append(message)
        if content and not found:
            kept.append({"role": "system", "content": content})
        messages[:] = kept

    def _render(self) -> str:
        rows = [self.HEADER.rstrip(), "Only the issues below are still unresolved:"]
        failures = list(self._active.values())
        for failure in failures[-8:]:
            scope = ", ".join(failure.paths[:3]) or self.scopes.invocation(failure.detail)[:240]
            prefix = f"- {failure.name}:{failure.code} ({failure.attempts} attempts; {scope[:500]}). "
            if failure.name == "apply_changes" and failure.code in EDIT_REFRESH_CODES:
                instruction = (
                    "Refresh this exact target with read_file or matching search_text results before retrying the edit; "
                    "rebuild a minimal patch from current contents."
                )
                if failure.attempts >= 2:
                    instruction = "Repeated edit failure guard: " + instruction
            elif failure.name in FILE_ACCESS_TOOLS:
                if failure.code == "INVALID_ARGUMENT":
                    instruction = (
                        "Correct the reported file-tool arguments, including offsets/limits or scope, "
                        "and retry this request."
                    )
                else:
                    instruction = (
                        "File access failed. Check the configured scope and use an exact path from list_files or search_text; "
                        "then retry the relevant read/search/list request."
                    )
            elif failure.name in EXECUTION_TOOLS:
                instruction = "Execution failed or was blocked. Inspect the output, correct argv/cwd or the environment, and retry the same verification."
            elif failure.name == "apply_changes":
                instruction = "Correct the reported change operation or arguments before retrying this target."
            else:
                instruction = "Follow the reported error and tool schema, then retry the relevant operation."
            rows.append(prefix + instruction)
        if len(failures) > 8:
            rows.append(f"{len(failures) - 8} additional unresolved issues remain in tool evidence.")
        return "\n".join(rows)
