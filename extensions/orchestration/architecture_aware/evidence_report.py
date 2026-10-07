"""Normalize worker reports into bounded, read-range-backed findings."""

from __future__ import annotations

import json
import re
from typing import Any

from extensions.orchestration.plugin_api import ExplorationEvidence


_RANGE = re.compile(r"^lines (\d+)(?:-(\d+)|\+)$")


def _read_ranges(records: list[dict[str, Any]]) -> list[tuple[str, int, int | None]]:
    ranges: list[tuple[str, int, int | None]] = []
    for record in records:
        if record.get("tool_name") != "read_file":
            continue
        path = ""
        start = 0
        end = None
        for fact in record.get("facts") or ():
            if not isinstance(fact, str):
                continue
            if fact.startswith("file="):
                path = fact[5:].replace("\\", "/").casefold().strip("./")
            elif match := _RANGE.match(fact):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else None
        if path and start:
            ranges.append((path, start, end))
    return ranges


def _payload(report: str) -> dict[str, Any]:
    raw = report.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, count=1, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw, count=1)
    try:
        result = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _bounded_strings(value: Any, count: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip()[:220] for item in value[:count]
                 if isinstance(item, str) and item.strip())


def normalize_worker_report(
    report: str, records: list[dict[str, Any]],
) -> tuple[str, tuple[ExplorationEvidence, ...], tuple[str, ...], tuple[str, ...]]:
    """Accept only claims whose file and line fall in a successful read range."""
    payload = _payload(report)
    ranges = _read_ranges(records)
    evidence: list[ExplorationEvidence] = []
    rejected = 0
    findings = payload.get("findings")
    for item in (findings[:8] if isinstance(findings, list) else ()):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").replace("\\", "/").strip("./")[:240]
        symbol = str(item.get("symbol") or "").strip()[:100]
        behavior = " ".join(str(item.get("behavior") or "").split())[:360]
        role = str(item.get("role") or "").strip().lower()
        try:
            first = int(item.get("start_line") or 0)
            last = int(item.get("end_line") or first)
        except (TypeError, ValueError):
            first = last = 0
        key = path.casefold()
        covered = any(
            (known == key or known.endswith("/" + key) or key.endswith("/" + known))
            and first >= start and (end is None or last <= end)
            for known, start, end in ranges
        ) if key and first > 0 and last >= first else False
        if not covered or not behavior or role not in {"edit", "dependency", "test"}:
            rejected += 1
            continue
        evidence.append(ExplorationEvidence(
            path=path, start_line=first, end_line=last,
            symbol=symbol, behavior=behavior, role=role,
        ))
        if len(evidence) >= 5:
            break
    unresolved = _bounded_strings(payload.get("unresolved"), 4)
    excluded = _bounded_strings(payload.get("excluded_candidates"), 4)
    if rejected:
        unresolved += (f"{rejected} reported finding(s) lacked matching file-tool line evidence",)
    if evidence:
        edits = sum(item.role == "edit" for item in evidence)
        summary = (
            f"{len(evidence)} read-range-backed findings ({edits} edit candidates); "
            f"{len(unresolved)} unresolved; {len(excluded)} excluded candidates. "
            "Read exact edit sites before changing code."
        )
    else:
        summary = (
            "No structured file-and-line finding could be verified. "
            "Treat the worker narrative as a lead only: "
            + " ".join(report.split())[:1000]
        )
    return summary, tuple(evidence), unresolved, excluded
