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
    """Build a bounded deterministic checkpoint for the next Agent turn."""
    calls = [item for item in (tool_calls or ()) if isinstance(item, dict)]
    counts: dict[str, int] = {}
    successes: list[str] = []
    failures: list[str] = []
    success_seen: set[tuple[str, str]] = set()
    failure_seen: set[tuple[str, str, str]] = set()

    for item in calls:
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
        if item_status in {"success", "completed"}:
            signature = (name, target_text or fact_text)
            if signature not in success_seen and len(successes) < 12:
                success_seen.add(signature)
                detail = "; ".join(value for value in (target_text, fact_text) if value)
                successes.append(f"- {name} succeeded" + (f" ({detail})" if detail else ""))
        else:
            signature = (name, str(item.get("error_code") or ""), observation)
            if signature not in failure_seen and len(failures) < 12:
                failure_seen.add(signature)
                detail = "; ".join(value for value in (
                    str(item.get("error_code") or ""), target_text, observation,
                ) if value)
                failures.append(f"- {name} failed" + (f": {detail}" if detail else ""))

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
        lines.append("- Changed files: " + "; ".join(changed_files[:16]))
    completed = [str(item) for item in (checkpoint.get("completed_items") or []) if item]
    if completed:
        lines.append("- Completed items: " + "; ".join(completed[-8:]))
    verification = [str(item) for item in (checkpoint.get("verification") or []) if item]
    if verification:
        lines.append("- Verification attempted: " + "; ".join(verification[-8:]))
    if successes:
        lines.append("- Successful execution flow:")
        lines.extend(successes)
    if failures:
        lines.append("- Failed execution flow:")
        lines.extend(failures)
    if len(successes) < sum(
        1 for item in calls if str(item.get("status") or "") in {"success", "completed"}
    ):
        lines.append("- Additional successful calls are available in Trace.")
    pending = [str(item) for item in (checkpoint.get("pending_items") or []) if item]
    if pending:
        lines.append("- Pending items: " + "; ".join(pending[-8:]))
    if checkpoint.get("stop_reason"):
        lines.append("- Stop reason: " + _excerpt(checkpoint.get("stop_reason"), 260))
    return "\n".join(lines)


__all__ = ["build_task_execution_summary"]
