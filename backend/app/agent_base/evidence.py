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
    ) -> EvidenceRecord:
        """Capture a tool fact and return it for progress/trace telemetry."""
        self._sequence += 1
        record = EvidenceRecord(
            id=f"E{self._sequence}", call_id=call_id, tool_name=tool_name,
            status=status,
        )
        path = str(arguments.get("path") or "").strip()

        if tool_name == "read_file":
            if path:
                record.facts.append(f"file={path}")
            # This is a hash of the returned observation/range, not a whole
            # file version.  Do not present it as ``expected_sha256`` for an
            # edit operation.
            record.facts.extend((_line_range(arguments), f"observation_sha={_sha256(observation)}"))
            record.detail = f"excerpt={_short(observation, 220)!r}"
        elif tool_name in {"search_text", "grep"}:
            pattern = str(arguments.get("pattern") or arguments.get("query") or "")
            if pattern:
                record.facts.append(f"query={_short(pattern, 100)!r}")
            if path:
                record.facts.append(f"path={path}")
            match = re.search(r"(?:找到|found)\s+(\d+)", observation, re.IGNORECASE)
            if match:
                record.facts.append(f"matches={match.group(1)}")
            record.detail = f"sample={_short(observation, 220)!r}"
        elif tool_name in {"edit_file", "write_file"}:
            if path:
                record.facts.append(f"file={path}")
            if tool_name == "edit_file":
                target = str(arguments.get("old_text") or "")
                replacement = str(arguments.get("new_text") or "")
                record.facts.extend((
                    f"target_sha={_sha256(target)}",
                    f"replacement_sha={_sha256(replacement)}",
                ))
                record.detail = f"target={_short(target, 180)!r} → {_short(replacement, 180)!r}"
            else:
                content = str(arguments.get("content") or "")
                record.facts.append(f"bytes={len(content.encode('utf-8'))}")
            after = re.search(r"sha256=([0-9a-fA-F]{12,64})", observation)
            if after:
                record.facts.append(f"after_sha={after.group(1)[:12]}")
            record.pending_edit = status == "success"
            self._link_edit_to_reads(path, record)
        elif tool_name == "bash":
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
            record.detail = f"result={_short(observation, 220)!r}"

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
