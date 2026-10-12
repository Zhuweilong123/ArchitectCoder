"""Bounded, structured evidence retained across a ReAct tool run.

Tool observations can be large and are intentionally kept in the trace for
audit.  They are a poor long-lived prompt representation, though: an
extractive prefix may omit the file range that was edited or the test failure
that explains the next action.  ``EvidenceLedger`` records a small, typed
fact for every tool call and can render it when old FC steps are compacted.

This module is deliberately deterministic.  It never reads files or calls an
LLM, so using it does not introduce another latency or token-cost path.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Protocol
from copy import deepcopy
from .host_api.data import JsonObject, JsonValue
from .host_api.payloads import json_object, optional_json_mapping
from .host_api.execution_types import RunCheckpoint, ToolCallDetail, ToolEffects, EvidenceSnapshot


def _short(value: object, limit: int = 180) -> str:
    """Return a bounded, single-line excerpt while retaining both ends."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.68))
    tail = max(1, limit - head - 1)
    return f"{text[:head]}…{text[-tail:]}"


def _sha256(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def _line_range(arguments: JsonObject) -> str:
    try:
        value = arguments.get("offset")
        offset = max(0, int(value)) if isinstance(value, (str, int, float)) else 0
    except (TypeError, ValueError, OverflowError):
        offset = 0
    limit = arguments.get("limit")
    if limit is None:
        return f"lines {offset + 1}+"
    try:
        if not isinstance(limit, (str, int, float)):
            return f"lines {offset + 1}+"
        count = max(1, int(limit))
    except (TypeError, ValueError, OverflowError):
        return f"lines {offset + 1}+"
    return f"lines {offset + 1}-{offset + count}"


def _json_mapping(value: str) -> JsonObject:
    """Best-effort structured extraction without preserving a raw payload."""
    try:
        parsed: object = json.loads(value)
        return json_object(parsed) if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _is_verification_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in (
        "pytest", " test", "test_", "npm run", "npm test", "ruff", "mypy",
        "tsc", "lint", "build", "compile",
    ))


@dataclass
class EvidenceRecord:
    """One compact, model-usable fact derived from a tool call."""

    id: str
    call_id: str
    tool_name: str
    status: str
    facts: list[str] = field(default_factory=list)
    detail: str = ""
    pending_edit: bool = False
    effects: ToolEffects = field(default_factory=lambda: ToolEffects())

    def render(self) -> str:
        state = "ok" if self.status == "success" else self.status
        parts = [f"[{self.id}] {self.tool_name} ({state})"]
        parts.extend(self.facts)
        if self.detail:
            parts.append(self.detail)
        return "; ".join(parts)

    def to_dict(self) -> EvidenceSnapshot:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "status": self.status,
            "facts": list(self.facts),
            "detail": self.detail,
            "pending_edit": self.pending_edit,
            "effects": deepcopy(self.effects),
        }


class EvidenceLedger:
    """Per-run evidence store with bounded checkpoint rendering.

    Raw observations are never stored here; they remain in ``ChatTrace``.  A
    record retains identifiers, hashes and selected failure/target excerpts so
    the model has a stable reason to re-read a file or re-run a focused check.
    """

    def __init__(self, max_records: int = 32) -> None:
        self.max_records = max(1, max_records)
        self._records: list[EvidenceRecord] = []
        self._by_call: dict[str, EvidenceRecord] = {}
        self._sequence = 0

    def record(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: JsonObject,
        observation: str,
        status: str = "success",
        error_code: str = "",
        effects: ToolEffects | None = None,
    ) -> EvidenceRecord:
        """Capture a tool fact and return it for progress/trace telemetry."""
        self._sequence += 1
        record = EvidenceRecord(
            id=f"E{self._sequence}", call_id=call_id, tool_name=tool_name,
            status=status,
            effects=deepcopy(effects or {}),
        )
        path = str(arguments.get("path") or "").strip()

        if effects and (effects.get("changes") or effects.get("execution") or effects.get("verification")):
            for change in effects.get("changes") or []:
                record.facts.append(f"{change['operation']} file={change['path']}")
                after_hash = change.get("after_hash")
                if after_hash:
                    record.facts.append(f"after_sha={after_hash[:12]}")
                self._link_edit_to_reads(change["path"], record)
            record.pending_edit = bool(effects.get("changes")) and status == "success"
            execution = effects.get("execution")
            if execution:
                record.facts.extend((f"cwd={execution['cwd']}", f"exit_code={execution['exit_code']}"))
            verification = effects.get("verification")
            if verification:
                record.facts.append(
                    f"verification={verification['kind']} scope={verification['scope']} "
                    f"passed={verification['passed']}"
                )
            record.detail = f"result={_short(observation, 180)!r}"
        elif tool_name == "read_file":
            if path:
                record.facts.append(f"file={path}")
            # This is a hash of the returned observation/range, not a whole
            # file version.  Do not present it as ``expected_sha256`` for an
            # edit operation.
            record.facts.extend((_line_range(arguments), f"observation_sha={_sha256(observation)}"))
            record.detail = f"excerpt={_short(observation, 220)!r}"
        elif tool_name == "search_text":
            pattern = str(arguments.get("pattern") or arguments.get("query") or "")
            if pattern:
                record.facts.append(f"query={_short(pattern, 100)!r}")
            if path:
                record.facts.append(f"path={path}")
            match = re.search(r"(?:找到|found)\s+(\d+)", observation, re.IGNORECASE)
            if match:
                record.facts.append(f"matches={match.group(1)}")
            record.detail = f"sample={_short(observation, 220)!r}"
        elif tool_name == "list_files":
            pattern = str(arguments.get("pattern") or "**/*")
            record.facts.append(f"pattern={_short(pattern, 100)!r}")
            if path:
                record.facts.append(f"path={path}")
            record.detail = f"result={_short(observation, 220)!r}"
        elif tool_name == "apply_changes":
            changes = arguments.get("changes")
            if isinstance(changes, list):
                record.facts.append(f"changes={len(changes)}")
                for requested_change in changes[:8]:
                    if not isinstance(requested_change, dict):
                        continue
                    target = str(requested_change.get("path") or requested_change.get("to") or "")
                    if target:
                        record.facts.append(f"file={target}")
                        self._link_edit_to_reads(target, record)
                    if requested_change.get("op") == "patch":
                        old_text = str(requested_change.get("old_text") or "")
                        new_text = str(requested_change.get("new_text") or "")
                        record.facts.extend((
                            f"target_sha={_sha256(old_text)}",
                            f"replacement_sha={_sha256(new_text)}",
                        ))
            record.pending_edit = status == "success"
            record.detail = f"result={_short(observation, 220)!r}"
        elif tool_name in {"run_program", "run_task"}:
            program = str(arguments.get("program") or arguments.get("task") or "")
            if program:
                record.facts.append(f"execution={_short(program, 180)!r}")
            cwd = str(arguments.get("cwd") or "")
            if cwd:
                record.facts.append(f"cwd={cwd}")
            record.detail = f"result={_short(observation, 180)!r}"
        elif tool_name == "get_project_map":
            payload = _json_mapping(observation)
            stats = optional_json_mapping(payload.get("stats")) or {}
            files = optional_json_mapping(payload.get("files")) or {}
            if payload.get("project_id"):
                record.facts.append(f"project_id={payload['project_id']}")
            for key in ("total_nodes", "total_edges"):
                if key in stats:
                    record.facts.append(f"{key}={stats[key]}")
            diagrams = payload.get("diagrams")
            if isinstance(diagrams, list):
                record.facts.append(f"diagrams={len(diagrams)}")
            for key in ("source_count", "test_count"):
                if key in files:
                    record.facts.append(f"{key}={files[key]}")
            if not record.facts:
                record.detail = f"result_sha={_sha256(observation)}"
        elif tool_name == "compare_design_code":
            payload = _json_mapping(observation)
            summary = optional_json_mapping(payload.get("summary")) or {}
            for key in (
                "coverage_rate", "mismatches", "missing_implementations",
                "extra_code", "total_design_classes", "total_code_classes",
            ):
                if key in summary:
                    record.facts.append(f"{key}={summary[key]}")
            item_payload = payload.get("items")
            item_list = item_payload.get("items") if isinstance(item_payload, dict) else item_payload
            if isinstance(item_list, list):
                locations: list[str] = []
                for item in item_list[:8]:
                    if not isinstance(item, dict):
                        continue
                    detail = optional_json_mapping(item.get("detail")) or {}
                    refs = [detail.get("code_ref"), detail.get("design_ref")]
                    for ref in refs:
                        if not isinstance(ref, dict):
                            continue
                        location = str(ref.get("file") or ref.get("project_file") or ref.get("name") or "")
                        if ref.get("start_line") is not None:
                            location += f":{ref['start_line']}-{ref.get('end_line', ref['start_line'])}"
                        if ref.get("class") and ref.get("method"):
                            location = f"{ref['class']}.{ref['method']}@{location}"
                        if location:
                            locations.append(location)
                if locations:
                    record.facts.append("locations=" + ";".join(locations[:8]))
            if not record.facts:
                record.detail = f"result_sha={_sha256(observation)}"
        elif tool_name == "todo_write":
            todos = arguments.get("todos")
            if isinstance(todos, list):
                counts: dict[str, int] = {}
                for todo in todos:
                    if isinstance(todo, dict):
                        state = str(todo.get("status") or "unknown")
                        counts[state] = counts.get(state, 0) + 1
                record.facts.append(
                    "todos=" + ",".join(f"{state}:{count}" for state, count in sorted(counts.items()))
                )
            if not record.facts:
                record.detail = f"result_sha={_sha256(observation)}"
        elif tool_name == "shell":
            command = str(arguments.get("command") or "")
            if command:
                record.facts.append(f"command={_short(command, 180)!r}")
            cwd = str(arguments.get("cwd") or "")
            if cwd:
                record.facts.append(f"cwd={cwd}")
            code = re.search(r"exited with code\s+(-?\d+)", observation, re.IGNORECASE)
            if code:
                record.facts.append(f"exit_code={code.group(1)}")
            elif status == "success":
                record.facts.append("exit_code=0")
            if _is_verification_command(command):
                record.facts.append("verification")
                if status == "success":
                    self._mark_edits_verified()
            if status != "success":
                record.detail = f"failure={_short(observation, 300)!r}"
            elif observation and observation != "(no output)":
                record.detail = f"result={_short(observation, 180)!r}"
        else:
            if error_code:
                record.facts.append(f"error_code={error_code}")
            record.detail = f"result={_short(observation, 120)!r}"

        if status != "success" and error_code and not any(
            fact.startswith("error_code=") for fact in record.facts
        ):
            record.facts.append(f"error_code={error_code}")
        if status != "success" and not record.detail:
            record.detail = f"failure={_short(observation, 300)!r}"

        self._records.append(record)
        self._by_call[call_id] = record
        if len(self._records) > self.max_records:
            removed = self._records.pop(0)
            self._by_call.pop(removed.call_id, None)
        return record

    def summary_for(self, call_ids: Iterable[str]) -> dict[str, str]:
        """Return only the facts belonging to compacted native tool calls."""
        return {
            call_id: record.render()
            for call_id in call_ids
            if (record := self._by_call.get(call_id)) is not None
        }

    def _link_edit_to_reads(self, path: str, edit: EvidenceRecord) -> None:
        if not path:
            return
        for record in reversed(self._records):
            if record.tool_name != "read_file" or f"file={path}" not in record.facts:
                continue
            if "used_by_edit=" not in " ".join(record.facts):
                record.facts.append(f"used_by_edit={edit.id}")
            return

    def _mark_edits_verified(self) -> None:
        for record in self._records:
            if record.pending_edit:
                record.pending_edit = False
                record.facts.append("verified")


def is_full_test_suite(tool_name: str, arguments: object) -> bool:
    """Recognize an unfiltered pytest run, not a focused or selected test."""
    if not isinstance(arguments, dict):
        return False
    if tool_name == "run_task":
        return (
            arguments.get("task") == "test"
            and arguments.get("target") in (None, "", ".")
        )
    if tool_name != "run_program":
        return False
    program = str(arguments.get("program") or "").replace("\\", "/").rsplit("/", 1)[-1]
    program = program.lower().removesuffix(".exe")
    argv = arguments.get("args")
    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
        return False
    if program in {"python", "python3"} and argv[:2] == ["-m", "pytest"]:
        argv = argv[2:]
    elif program != "pytest":
        return False
    return all(arg in {"-q", "-qq", "--quiet", "-v", "-vv", "--verbose"} for arg in argv)


class VerificationPort(Protocol):
    @property
    def kind(self) -> str: ...
    @property
    def scope(self) -> str: ...
    @property
    def passed(self) -> bool: ...


def record_runtime_verification(
    verifications: dict[tuple[str, str], bool],
    check: VerificationPort,
    tool_name: str,
    arguments: object,
) -> None:
    """A passing full suite supersedes earlier focused test failures."""
    if check.kind == "test" and check.passed and is_full_test_suite(tool_name, arguments):
        for key in tuple(verifications):
            if key[0] == "test":
                del verifications[key]
    verifications[(check.kind, check.scope)] = check.passed


def update_checkpoint_evidence(checkpoint: RunCheckpoint, details: list[ToolCallDetail]) -> None:
    """Accumulate effects across steps and resumptions, retaining unresolved failures."""
    files = list(checkpoint.get("changed_files") or [])
    mutations = dict(checkpoint.get("mutation_evidence") or {})
    checks = list(checkpoint.get("verification_results") or [])
    for detail in details:
        for change in detail.get("changes") or []:
            if detail.get("status") == "success":
                mutations[change["path"]] = change.copy()
                if change["path"] not in files:
                    files.append(change["path"])
        verification = detail.get("verification")
        if verification:
            full_suite_passed = (
                verification["kind"] == "test"
                and verification["passed"]
                and is_full_test_suite(detail.get("name", ""), detail.get("arguments"))
            )
            checks = [item for item in checks if not (
                (full_suite_passed and item["kind"] == "test")
                or (item["kind"], item["scope"]) == (
                    verification["kind"], verification["scope"],
                )
            )]
            checks.append(verification.copy())
    checkpoint["changed_files"] = files
    checkpoint["mutation_evidence"] = mutations
    checkpoint["verification_results"] = checks
    checkpoint["verification"] = [
        f"{item['kind']} {item['scope']}: {'passed' if item['passed'] else 'failed'}"
        for item in checks
    ] or list(checkpoint.get("verification") or [])
