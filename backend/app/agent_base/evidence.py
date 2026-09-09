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
from typing import Any, Iterable


def _short(value: Any, limit: int = 180) -> str:
    """Return a bounded, single-line excerpt while retaining both ends."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.68))
    tail = max(1, limit - head - 1)
    return f"{text[:head]}…{text[-tail:]}"


def _sha256(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def _line_range(arguments: dict[str, Any]) -> str:
    try:
        offset = max(0, int(arguments.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    limit = arguments.get("limit")
    if limit is None:
        return f"lines {offset + 1}+"
    try:
        count = max(1, int(limit))
    except (TypeError, ValueError):
        return f"lines {offset + 1}+"
    return f"lines {offset + 1}-{offset + count}"


def _json_mapping(value: str) -> dict[str, Any]:
    """Best-effort structured extraction without preserving a raw payload."""
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
    effects: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        state = "ok" if self.status == "success" else self.status
        parts = [f"[{self.id}] {self.tool_name} ({state})"]
        parts.extend(self.facts)
        if self.detail:
            parts.append(self.detail)
        return "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "status": self.status,
            "facts": list(self.facts),
            "detail": self.detail,
            "pending_edit": self.pending_edit,
            "effects": self.effects,
        }


class EvidenceLedger:
    """Per-run evidence store with bounded checkpoint rendering.

    Raw observations are never stored here; they remain in ``ChatTrace``.  A
    record retains identifiers, hashes and selected failure/target excerpts so
    the model has a stable reason to re-read a file or re-run a focused check.
    """

    def __init__(self, max_records: int = 32):
        self.max_records = max(1, max_records)
        self._records: list[EvidenceRecord] = []
        self._by_call: dict[str, EvidenceRecord] = {}
        self._sequence = 0

    def record(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        observation: str,
        status: str = "success",
        error_code: str = "",
        effects: dict[str, Any] | None = None,
    ) -> EvidenceRecord:
        """Capture a tool fact and return it for progress/trace telemetry."""
        self._sequence += 1
        record = EvidenceRecord(
            id=f"E{self._sequence}", call_id=call_id, tool_name=tool_name,
            status=status,
            effects=effects or {},
        )
        path = str(arguments.get("path") or "").strip()

        if effects and (effects.get("changes") or effects.get("execution") or effects.get("verification")):
            for change in effects.get("changes") or []:
                record.facts.append(f"{change['operation']} file={change['path']}")
                if change.get("after_hash"):
                    record.facts.append(f"after_sha={change['after_hash'][:12]}")
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
                for change in changes[:8]:
                    if not isinstance(change, dict):
                        continue
                    target = str(change.get("path") or change.get("to") or "")
                    if target:
                        record.facts.append(f"file={target}")
                        self._link_edit_to_reads(target, record)
                    if change.get("op") == "patch":
                        old_text = str(change.get("old_text") or "")
                        new_text = str(change.get("new_text") or "")
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
            stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
            files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
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
            summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
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
                    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
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


def update_checkpoint_evidence(checkpoint: dict, details: list[dict]) -> None:
    """Accumulate actual effects across steps and resumptions, preserving failed checks."""
    files = list(checkpoint.get("changed_files") or [])
    mutations = dict(checkpoint.get("mutation_evidence") or {})
    checks = list(checkpoint.get("verification_results") or [])
    for detail in details:
        for change in detail.get("changes") or []:
            if detail.get("status") == "success":
                mutations[change["path"]] = dict(change)
                if change["path"] not in files:
                    files.append(change["path"])
        verification = detail.get("verification")
        if verification:
            # A retry replaces only the same check; unrelated failures remain visible.
            checks = [item for item in checks if (item["kind"], item["scope"]) != (
                verification["kind"], verification["scope"],
            )]
            checks.append(dict(verification))
    checkpoint["changed_files"] = files
    checkpoint["mutation_evidence"] = mutations
    checkpoint["verification_results"] = checks
    checkpoint["verification"] = [
        f"{item['kind']} {item['scope']}: {'passed' if item['passed'] else 'failed'}"
        for item in checks
    ] or list(checkpoint.get("verification") or [])
