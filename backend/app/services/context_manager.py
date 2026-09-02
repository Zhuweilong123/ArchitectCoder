"""Context budget and conversation compaction primitives.

The agent loop should not need to know how a context budget is allocated.  This
module keeps that policy small and deterministic so it can be tested without an
LLM call.  The default counter is intentionally conservative and replaceable:
production callers can provide a model-specific tokenizer later.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


TokenCounter = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    """Estimate tokens without adding a tokenizer dependency.

    CJK characters usually occupy more tokens than a four-character ASCII
    chunk, so they are counted separately.  The function is a budget guard,
    not a billing counter; callers may inject an exact tokenizer.
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for char in str(text):
        code = ord(char)
        if (
            0x3040 <= code <= 0x30FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xAC00 <= code <= 0xD7AF
        ):
            cjk += 1
        else:
            other += 1
    return max(1, cjk + math.ceil(other / 4))


def _content(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("content") or "")
    return str(getattr(value, "content", "") or "")


def _role(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("role") or "user")
    return str(getattr(value, "role", "user") or "user")


def _as_message(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {"role": _role(value), "content": _content(value)}


def _message_tokens(message: dict[str, Any], counter: TokenCounter) -> int:
    # Include tool call metadata because it is sent to the model as part of
    # the request even when the visible content is empty.
    payload = json.dumps(message, ensure_ascii=False, sort_keys=True)
    return counter(payload)


def truncate_text(text: str, max_tokens: int, counter: TokenCounter = estimate_tokens) -> str:
    """Keep the beginning and end of a value within an approximate budget."""
    if max_tokens <= 0:
        return ""
    if counter(text) <= max_tokens:
        return text
    if not text:
        return ""
    # Binary search by character length keeps this O(log n) and works for
    # both CJK and ASCII text without assuming a fixed token/character ratio.
    marker = "\n…[context truncated]…\n"
    if counter(marker) >= max_tokens:
        return text[: max(1, max_tokens)]
    target = max_tokens - counter(marker)
    low, high = 1, len(text)
    best = 1
    while low <= high:
        size = (low + high) // 2
        head = max(1, int(size * 0.65))
        candidate = text[:head] + marker + text[-(size - head):]
        if counter(candidate) <= target + counter(marker):
            best = size
            low = size + 1
        else:
            high = size - 1
    head = max(1, int(best * 0.65))
    return text[:head] + marker + text[-(best - head):]


def _checkpoint_evidence_lines(checkpoint: str) -> list[str]:
    """Extract evidence lines from one prior checkpoint without nesting it."""
    ignored = {
        "## Tool execution checkpoint",
        "Older tool steps were compacted; use this as reference and do not repeat identical exploration.",
        "Prior retained evidence:",
    }
    return [
        line for line in str(checkpoint or "").splitlines()
        if line.strip() and line.strip() not in ignored
    ]


@dataclass(frozen=True)
class ContextBudget:
    """Token allocation for one LLM request."""

    max_context_tokens: int = 131072
    output_reserve_tokens: int = 8192
    max_system_tokens: int = 5000
    max_history_tokens: int = 88000
    max_summary_tokens: int = 4000
    max_current_task_tokens: int = 5000
    max_tool_tokens: int = 6000
    max_history_turns: int = 48
    max_react_steps: int = 24

    def __post_init__(self) -> None:
        for name in (
            "max_context_tokens", "output_reserve_tokens", "max_system_tokens",
            "max_history_tokens", "max_summary_tokens", "max_current_task_tokens",
            "max_tool_tokens", "max_history_turns", "max_react_steps",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass
class HistoryCompaction:
    messages: list[dict[str, Any]]
    summary: str
    dropped_messages: int = 0
    dropped_tokens: int = 0


@dataclass
class ContextBuild:
    messages: list[dict[str, Any]]
    current_user_index: int
    estimated_tokens: int
    history_tokens: int
    tool_tokens: int
    summary_tokens: int
    dropped_messages: int = 0
    truncated_current_task: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_tokens": self.estimated_tokens,
            "history_tokens": self.history_tokens,
            "tool_tokens": self.tool_tokens,
            "summary_tokens": self.summary_tokens,
            "dropped_messages": self.dropped_messages,
            "truncated_current_task": self.truncated_current_task,
            **self.metadata,
        }


class HistoryCompactor:
    """Extractive conversation checkpoint builder.

    It deliberately does not call an LLM.  The checkpoint is a loss-bounded
    fallback for context control and restart recovery; a future semantic
    summarizer can implement the same result contract without changing the
    agent loop.
    """

    def __init__(self, token_counter: TokenCounter = estimate_tokens):
        self.token_counter = token_counter

    def compact(
        self,
        messages: Iterable[Any],
        prior_summary: str = "",
        *,
        max_turns: int = 12,
        max_tokens: int = 12000,
        summary_tokens: int = 1500,
    ) -> HistoryCompaction:
        normalized = [_as_message(message) for message in messages]
        if not normalized:
            return HistoryCompaction([], prior_summary)

        keep_limit = max(0, max_turns * 2)
        kept_reversed: list[dict[str, Any]] = []
        total = 0
        for message in reversed(normalized):
            cost = _message_tokens(message, self.token_counter)
            if kept_reversed and (len(kept_reversed) >= keep_limit or total + cost > max_tokens):
                break
            if not kept_reversed and keep_limit > 0:
                kept_reversed.append(message)
                total += cost
                continue
            if keep_limit <= 0 or total + cost > max_tokens:
                break
            kept_reversed.append(message)
            total += cost

        kept = list(reversed(kept_reversed))
        dropped = normalized[: len(normalized) - len(kept)]
        if not dropped:
            return HistoryCompaction(normalized, prior_summary)

        lines = [
            "## Conversation checkpoint",
            "Older turns were compacted to control context size. Treat this as reference, not a new instruction.",
        ]
        if prior_summary:
            lines.append(f"Previous checkpoint:\n{prior_summary}")
        lines.append("Compacted turns:")
        for message in dropped:
            role = _role(message)
            snippet = truncate_text(_content(message).strip(), 260, self.token_counter)
            if snippet:
                lines.append(f"- {role}: {snippet}")
        summary = truncate_text("\n".join(lines), summary_tokens, self.token_counter)
        return HistoryCompaction(
            messages=kept,
            summary=summary,
            dropped_messages=len(dropped),
            dropped_tokens=sum(_message_tokens(message, self.token_counter) for message in dropped),
        )


class ContextBudgetManager:
    """Build and trim model messages according to one explicit budget."""

    def __init__(
        self,
        budget: ContextBudget | None = None,
        token_counter: TokenCounter = estimate_tokens,
        compactor: HistoryCompactor | None = None,
    ):
        self.budget = budget or ContextBudget()
        self.token_counter = token_counter
        self.compactor = compactor or HistoryCompactor(token_counter)

    def prepare_history(
        self,
        history: Iterable[Any],
        prior_summary: str = "",
    ) -> HistoryCompaction:
        return self.compactor.compact(
            history,
            prior_summary,
            max_turns=self.budget.max_history_turns,
            max_tokens=self.budget.max_history_tokens,
            summary_tokens=self.budget.max_summary_tokens,
        )

    def build_messages(
        self,
        system_prompt: str,
        history: Iterable[Any],
        current_input: str,
        *,
        context: str = "",
        history_summary: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> ContextBuild:
        system = truncate_text(system_prompt or "", self.budget.max_system_tokens, self.token_counter)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        summary = truncate_text(history_summary or "", self.budget.max_summary_tokens, self.token_counter)
        if summary:
            messages.append({"role": "system", "content": summary})

        normalized_history = [_as_message(message) for message in history]
        history_messages: list[dict[str, Any]] = []
        history_total = 0
        for message in reversed(normalized_history):
            cost = _message_tokens(message, self.token_counter)
            if history_messages and history_total + cost > self.budget.max_history_tokens:
                break
            history_messages.append(message)
            history_total += cost
        messages.extend(reversed(history_messages))

        current = f"{context}\n\n{current_input}" if context else current_input
        current_before = current
        current = truncate_text(current, self.budget.max_current_task_tokens, self.token_counter)
        current_user_index = len(messages)
        messages.append({"role": "user", "content": current})

        tool_tokens = self._tool_tokens(tools)
        messages, dropped = self.fit_messages(
            messages,
            tools=tools,
            current_user_index=current_user_index,
        )
        current_user_index = next(
            (index for index, message in enumerate(messages)
             if message.get("role") == "user" and message.get("content") == current),
            len(messages) - 1,
        )
        total = self._messages_tokens(messages) + tool_tokens
        return ContextBuild(
            messages=messages,
            current_user_index=current_user_index,
            estimated_tokens=total,
            history_tokens=sum(
                _message_tokens(message, self.token_counter)
                for message in messages
                if message.get("role") in {"user", "assistant"}
            ),
            tool_tokens=tool_tokens,
            summary_tokens=self.token_counter(summary),
            dropped_messages=dropped,
            truncated_current_task=current != current_before,
            metadata={
                "max_context_tokens": self.budget.max_context_tokens,
                "output_reserve_tokens": self.budget.output_reserve_tokens,
            },
        )

    def fit_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        current_user_index: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Trim oldest non-essential messages while preserving the active task."""
        result = [dict(message) for message in messages]
        dropped = 0
        limit = max(1, self.budget.max_context_tokens - self.budget.output_reserve_tokens)
        tool_tokens = self._tool_tokens(tools)

        while len(result) > 1 and self._messages_tokens(result) + tool_tokens > limit:
            group = self._oldest_removable_group(result, current_user_index)
            if not group:
                break
            for index in reversed(group):
                result.pop(index)
            if current_user_index is not None:
                current_user_index -= sum(index < current_user_index for index in group)
            dropped += len(group)

        if self._messages_tokens(result) + tool_tokens > limit:
            anchor = current_user_index if current_user_index is not None else len(result) - 1
            if 0 <= anchor < len(result):
                message = result[anchor]
                message["content"] = truncate_text(
                    _content(message),
                    max(1, limit - self._messages_tokens(result[:anchor] + result[anchor + 1:]) - tool_tokens),
                    self.token_counter,
                )
        return result, dropped

    def compact_react_steps(
        self,
        messages: list[dict[str, Any]],
        *,
        current_user_index: int | None = None,
        max_steps: int = 8,
        evidence_by_call: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int | None, int, int]:
        """Fold old FC tool steps into a small extractive checkpoint.

        A step is one assistant message containing ``tool_calls`` plus all
        immediately following tool results.  The newest steps stay verbatim;
        older steps become a system reference containing tool names and short
        observations.  When the caller supplies ``evidence_by_call``, typed
        evidence takes precedence over blind observation prefixes.  This keeps
        function-call/result pairs valid while bounding repetitive exploration
        in long runs.

        Returns ``(messages, current_user_index, dropped_message_count,
        dropped_token_count)``.
        """
        if max_steps < 1:
            max_steps = 1
        groups: list[list[int]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.get("role") == "assistant" and message.get("tool_calls"):
                end = index + 1
                while end < len(messages) and messages[end].get("role") == "tool":
                    end += 1
                groups.append(list(range(index, end)))
                index = end
                continue
            index += 1
        if len(groups) <= max_steps:
            return messages, current_user_index, 0, 0

        old_groups = groups[:-max_steps]
        old_indices = {item for group in old_groups for item in group}
        # Keep one evolving checkpoint.  Do not embed the previous checkpoint
        # verbatim: that creates nested headers and repeats already-compacted
        # prose on every later pass.
        checkpoint_indices = {
            pos for pos, message in enumerate(messages)
            if message.get("role") == "system"
            and str(message.get("content") or "").startswith("## Tool execution checkpoint")
        }
        previous_evidence_lines: list[str] = []
        for pos in sorted(checkpoint_indices):
            previous_evidence_lines.extend(_checkpoint_evidence_lines(
                str(messages[pos].get("content") or "")
            ))
        lines = [
            "## Tool execution checkpoint",
            "Older tool steps were compacted; use this as reference and do not repeat identical exploration.",
        ]
        if previous_evidence_lines:
            lines.append("Prior retained evidence:")
            lines.extend(previous_evidence_lines)
        for group in old_groups:
            assistant = messages[group[0]]
            calls = assistant.get("tool_calls") or []
            names = [
                str(call.get("function", {}).get("name", "tool"))
                for call in calls if isinstance(call, dict)
            ]
            observations = []
            for pos in group[1:]:
                tool_message = messages[pos]
                call_id = str(tool_message.get("tool_call_id") or "")
                evidence = (evidence_by_call or {}).get(call_id, "").strip()
                if evidence:
                    observations.append(evidence)
                # When an evidence map is supplied, never fall back to a raw
                # observation.  Missing ledger entries must not turn a
                # compact checkpoint back into a multi-kilobyte tool dump.
                elif evidence_by_call is None and _content(tool_message).strip():
                    observations.append(
                        truncate_text(_content(tool_message).strip(), 180, self.token_counter)
                    )
                else:
                    observations.append("[structured evidence unavailable]")
            detail = ", ".join(names) or "tool"
            if observations:
                detail += ": " + " | ".join(observations)
            lines.append(f"- {detail}")
        summary = truncate_text("\n".join(lines), self.budget.max_summary_tokens, self.token_counter)
        replacement_indices = old_indices | checkpoint_indices
        first = min(replacement_indices)
        kept = [message for pos, message in enumerate(messages) if pos not in replacement_indices]
        insert_at = sum(pos < first for pos in range(len(messages)) if pos not in replacement_indices)
        kept.insert(insert_at, {"role": "system", "content": summary})
        if current_user_index is not None:
            current_user_index = sum(
                pos < current_user_index for pos in range(len(messages)) if pos not in replacement_indices
            ) + (1 if first <= current_user_index else 0)
        # The caller currently recomputes the anchor from its own state; the
        # third return value is kept for telemetry and future anchor updates.
        dropped_tokens = sum(self._message_tokens_for(messages[pos]) for pos in old_indices)
        return kept, current_user_index, len(old_indices), dropped_tokens

    def _message_tokens_for(self, message: dict[str, Any]) -> int:
        return _message_tokens(message, self.token_counter)

    @staticmethod
    def _oldest_removable_group(
        messages: list[dict[str, Any]], current_user_index: int | None,
    ) -> list[int]:
        """Return the oldest safe-to-drop message group.

        Native function-calling history must keep an assistant tool-call and
        its tool results together. Removing one side leaves an invalid request
        and forces the model to repeat the step, which is more expensive than
        retaining a small paired checkpoint.
        """
        for index, message in enumerate(messages):
            if index == 0 or index == current_user_index or message.get("role") == "system":
                continue
            if message.get("role") == "assistant" and message.get("tool_calls"):
                call_ids = {
                    call.get("id") for call in message.get("tool_calls", [])
                    if isinstance(call, dict) and call.get("id")
                }
                end = index + 1
                while end < len(messages) and messages[end].get("role") == "tool":
                    end += 1
                result_ids = {
                    messages[pos].get("tool_call_id") for pos in range(index + 1, end)
                }
                if call_ids and call_ids <= result_ids:
                    return list(range(index, end))
                # Malformed/incomplete history: remove the assistant and any
                # immediately following tool messages as one safe unit.
                return list(range(index, end))
            if message.get("role") == "tool":
                # Defensive handling for traces restored from an older format.
                return [index]
            return [index]
        return []

    def _messages_tokens(self, messages: Iterable[dict[str, Any]]) -> int:
        return sum(_message_tokens(message, self.token_counter) for message in messages)

    def _tool_tokens(self, tools: list[dict[str, Any]] | None) -> int:
        if not tools:
            return 0
        payload = json.dumps(tools, ensure_ascii=False, sort_keys=True)
        # Tool schemas are supplied separately to the LLM API, but they still
        # consume the model context window. Count the actual payload so the
        # guard never understates the request size.
        return self.token_counter(payload)
