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


@dataclass(frozen=True)
class ContextBudget:
    """Token allocation for one LLM request."""

    max_context_tokens: int = 32768
    output_reserve_tokens: int = 4096
    max_system_tokens: int = 5000
    max_history_tokens: int = 12000
    max_summary_tokens: int = 1500
    max_current_task_tokens: int = 5000
    max_tool_tokens: int = 6000
    max_history_turns: int = 12

    def __post_init__(self) -> None:
        for name in (
            "max_context_tokens", "output_reserve_tokens", "max_system_tokens",
            "max_history_tokens", "max_summary_tokens", "max_current_task_tokens",
            "max_tool_tokens", "max_history_turns",
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
            candidates = [
                index for index, message in enumerate(result)
                if index != 0
                and index != current_user_index
                and message.get("role") != "system"
            ]
            if not candidates:
                break
            result.pop(candidates[0])
            if current_user_index is not None and candidates[0] < current_user_index:
                current_user_index -= 1
            dropped += 1

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
