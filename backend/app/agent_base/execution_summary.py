"""Transport-neutral summaries of Agent task execution."""

from __future__ import annotations


def _excerpt(value: object, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def build_task_execution_summary(
    tool_calls: list[dict],
    checkpoint: dict,
    status: str,
) -> str:
    """Summarize every call in order, bounding individual output excerpts only.

    Later recovery and verification must not be dropped in favour of early
    exploration. File effects describe recorded operations, not a fresh
    filesystem snapshot (commands and external actors can also change files).
    """
    calls = [item for item in (tool_calls or ()) if isinstance(item, dict)]
    counts: dict[str, int] = {}
    execution: list[str] = []
    last_file_operations: dict[str, str] = {}

    for index, item in enumerate(calls, 1):
        name = str(item.get("name") or "tool")
        item_status = str(item.get("status") or "unknown")
        counts[item_status] = counts.get(item_status, 0) + 1
        arguments = item.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        target = (
            arguments.get("path")
            or arguments.get("command")
            or arguments.get("node_id")
            or arguments.get("cwd")
            or ""
        )
        target_text = _excerpt(target, 150)
        evidence = item.get("evidence")
        facts = evidence.get("facts", []) if isinstance(evidence, dict) else []
        fact_text = "; ".join(
            _excerpt(fact, 160) for fact in facts[:3] if str(fact or "").strip()
        )
        observation = _excerpt(item.get("observation"), 240)
        effects = evidence.get("effects") if isinstance(evidence, dict) else None
        effects = effects if isinstance(effects, dict) else {}
        changes = item.get("changes") or effects.get("changes") or []
        file_effects: list[str] = []
        succeeded = item_status in {"success", "completed"}
        if succeeded:
            for change in changes:
                if not isinstance(change, dict):
                    continue
                path = str(change.get("path") or "")
                operation = str(change.get("operation") or "")
                if path and operation:
                    file_effects.append(f"{operation} {path}")
                    last_file_operations[path] = operation
        # Literal argv identifies recovery scripts even when cwd is identical.
        program = arguments.get("program")
        argv = arguments.get("args")
        command = ""
        if program:
            command = " ".join(
                str(part) for part in [program, *(argv if isinstance(argv, list) else [])]
            )
        detail = "; ".join(value for value in (
            str(item.get("error_code") or ""), target_text, command,
            fact_text, *file_effects, observation,
        ) if value)
        verdict = "succeeded" if succeeded else f"failed [{item_status}]"
        execution.append(f"- [{index}] {name} {verdict}" + (f": {detail}" if detail else ""))

    lines = [
        "## Task execution checkpoint",
        f"- Status: {status}",
        f"- Tool calls: {len(calls)}" + (
            " (" + ", ".join(f"{key}:{value}" for key, value in sorted(counts.items())) + ")"
            if counts else ""
        ),
    ]
    changed_files = [str(item) for item in (checkpoint.get("changed_files") or []) if item]
    if changed_files:
        lines.append("- Files touched during this run (not current existence or remaining changes): " + "; ".join(changed_files))
    completed = [str(item) for item in (checkpoint.get("completed_items") or []) if item]
    if completed:
        lines.append("- Completed items: " + "; ".join(completed))
    verification = [str(item) for item in (checkpoint.get("verification") or []) if item]
    if verification:
        lines.append("- Verification attempted: " + "; ".join(verification))
    if execution:
        lines.append("- Execution flow (chronological; failures may be followed by successful recovery):")
        lines.extend(execution)
    if last_file_operations:
        lines.append("- Last successful file-tool operations (not a filesystem snapshot):")
        lines.extend(f"- {path}: {operation}" for path, operation in last_file_operations.items())
    pending = [str(item) for item in (checkpoint.get("pending_items") or []) if item]
    if pending:
        lines.append("- Pending items: " + "; ".join(pending))
    if checkpoint.get("stop_reason"):
        lines.append("- Stop reason: " + _excerpt(checkpoint.get("stop_reason"), 260))

    contract_check = checkpoint.get("contract_check")
    if isinstance(contract_check, dict):
        contract_status = str(contract_check.get("status") or "unknown")
        violations = [
            item for item in (contract_check.get("violations") or [])
            if isinstance(item, dict)
        ]
        counts_by_severity: dict[str, int] = {}
        counts_by_code: dict[str, int] = {}
        for item in violations:
            severity = str(item.get("severity") or "unknown")
            code = str(item.get("code") or "unknown")
            counts_by_severity[severity] = counts_by_severity.get(severity, 0) + 1
            counts_by_code[code] = counts_by_code.get(code, 0) + 1

        details = [f"{len(violations)} finding(s)"]
        if counts_by_severity:
            details.append(
                "severity: " + ", ".join(
                    f"{key}={value}" for key, value in sorted(counts_by_severity.items())
                )
            )
        if counts_by_code:
            details.append(
                "codes: " + ", ".join(
                    f"{key}={value}" for key, value in sorted(counts_by_code.items())
                )
            )
        if "allowed" in contract_check:
            details.append(f"gate={'allowed' if contract_check['allowed'] else 'blocked'}")
        lines.append(f"- Contract check: {contract_status} (" + "; ".join(details) + ")")

        decision_message = _excerpt(contract_check.get("decision_message"), 260)
        if decision_message:
            lines.append("- Contract decision: " + decision_message)
        if violations:
            lines.append("- Contract feedback:")
            for item in violations[:24]:
                severity = str(item.get("severity") or "unknown")
                code = str(item.get("code") or "unknown")
                message = _excerpt(item.get("message"), 200)
                path = _excerpt(item.get("path"), 160)
                location = f" [{path}]" if path else ""
                lines.append(
                    f"- {severity} [{code}]: {message}{location}"
                )
            if len(violations) > 24:
                lines.append(
                    f"- Additional contract findings omitted: {len(violations) - 24}; see Trace."
                )
    return "\n".join(lines)


__all__ = ["build_task_execution_summary"]
