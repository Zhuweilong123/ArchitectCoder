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
from collections.abc import Callable, Iterable, Sequence
from copy import deepcopy

from app.agent_base.host_api.context import (
    ContextBuildMetadata, ContextReport, ContextSettingsPort, HistoryMessage,
    MessageFit, ToolHistoryCompaction,
)
from app.agent_base.host_api.data import ModelMessage, ToolSchema


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


def _content(value: ModelMessage) -> str:
    return value.get("content") or ""


def _as_message(value: HistoryMessage) -> ModelMessage:
    if isinstance(value, dict):
        return deepcopy(value)
    return {"role": value.role, "content": value.content}


def _tool_group_end(messages: Sequence[ModelMessage], index: int) -> int:
    end = index + 1
    if messages[index].get("role") == "assistant" and messages[index].get("tool_calls"):
        while end < len(messages) and messages[end].get("role") == "tool":
            end += 1
    return end


def _message_groups(messages: list[ModelMessage]) -> list[list[ModelMessage]]:
    groups = []
    index = 0
    while index < len(messages):
        end = _tool_group_end(messages, index)
        groups.append(messages[index:end])
        index = end
    return groups


def _validate_anchor(messages: Sequence[ModelMessage], index: int | None) -> None:
    if index is not None and not 0 <= index < len(messages):
        raise ValueError("current_user_index must reference an existing message")


def _as_model_history_message(message: ModelMessage) -> ModelMessage:
    """Convert internal history-only roles to valid model message roles."""
    result = message.copy()
    if result.get("role") == "summary":
        result["role"] = "system"
    return result


def _message_tokens(message: ModelMessage, counter: TokenCounter) -> int:
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
    # The counter may include serialization overhead. Measure each candidate,
    # including its marker, rather than treating tokens as character counts.
    marker = "\n…[context truncated]…\n"
    use_marker = counter(marker) <= max_tokens

    def candidate(size: int) -> str:
        if not use_marker:
            return text[:size]
        head = max(1, int(size * 0.65)) if size else 0
        tail = size - head
        return text[:head] + marker + (text[-tail:] if tail else "")

    low, high = 0, len(text)
    best = ""
    while low <= high:
        size = (low + high) // 2
        value = candidate(size)
        if counter(value) <= max_tokens:
            best = value
            low = size + 1
        else:
            high = size - 1
    return best


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

    max_context_tokens: int = 256000
    max_system_tokens: int = 5000
    # Zero derives the history/checkpoint target from the soft threshold.
    max_history_tokens: int = 0
    max_summary_tokens: int = 4000
    max_current_task_tokens: int = 5000
    max_tool_tokens: int = 6000
    max_history_turns: int = 48
    soft_threshold_ratio: float = 0.78125
    compaction_trigger_ratio: float = 0.9

    def __post_init__(self) -> None:
        if self.max_history_tokens <= 0:
            object.__setattr__(
                self,
                "max_history_tokens",
                max(1, int(self.max_context_tokens * self.soft_threshold_ratio)),
            )
        for name in (
            "max_context_tokens", "max_system_tokens",
            "max_history_tokens", "max_summary_tokens", "max_current_task_tokens",
            "max_tool_tokens", "max_history_turns",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in ("soft_threshold_ratio", "compaction_trigger_ratio"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if self.soft_threshold_ratio > self.compaction_trigger_ratio:
            raise ValueError("soft_threshold_ratio must not exceed compaction_trigger_ratio")


@dataclass
class HistoryCompaction:
    messages: list[ModelMessage]
    summary: str
    dropped_messages: int = 0
    dropped_tokens: int = 0


@dataclass
class ContextBuild:
    messages: list[ModelMessage]
    current_user_index: int
    estimated_tokens: int
    history_tokens: int
    tool_tokens: int
    summary_tokens: int
    dropped_messages: int = 0
    truncated_current_task: bool = False
    metadata: ContextBuildMetadata = field(default_factory=ContextBuildMetadata)

    def to_dict(self) -> ContextReport:
        report: ContextReport = {
            "estimated_tokens": self.estimated_tokens,
            "history_tokens": self.history_tokens,
            "tool_tokens": self.tool_tokens,
            "summary_tokens": self.summary_tokens,
            "dropped_messages": self.dropped_messages,
            "truncated_current_task": self.truncated_current_task,
        }
        if "max_context_tokens" in self.metadata:
            report["max_context_tokens"] = self.metadata["max_context_tokens"]
        return report


class HistoryCompactor:
    """Extractive conversation checkpoint builder.

    It deliberately does not call an LLM.  The checkpoint is a loss-bounded
    fallback for context control and restart recovery; a future semantic
    summarizer can implement the same result contract without changing the
    agent loop.
    """

    def __init__(self, token_counter: TokenCounter = estimate_tokens) -> None:
        self.token_counter = token_counter

    def compact(
        self,
        messages: Iterable[HistoryMessage],
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
        kept_groups: list[list[ModelMessage]] = []
        kept_count = 0
        total = 0
        for group in reversed(_message_groups(normalized)):
            cost = sum(_message_tokens(message, self.token_counter) for message in group)
            if keep_limit <= 0:
                break
            if kept_groups and (kept_count + len(group) > keep_limit or total + cost > max_tokens):
                break
            # Retain the newest complete group, even when it alone exceeds
            # the soft history budget. Never separate a call from its results.
            kept_groups.append(group)
            kept_count += len(group)
            total += cost

        kept = [message for group in reversed(kept_groups) for message in group]
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
            role = message["role"]
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
    ) -> None:
        self.budget = budget or ContextBudget()
        self.token_counter = token_counter
        self.compactor = compactor or HistoryCompactor(token_counter)

    @classmethod
    def from_settings(cls, settings: ContextSettingsPort | None = None) -> "ContextBudgetManager":
        """Build an isolated manager from the central Agent context settings."""
        if settings is None:
            from backend.config import get_settings
            settings = get_settings()
        return cls(budget=ContextBudget(
            max_context_tokens=settings.agent_context_hard_limit_tokens,
            max_history_turns=settings.agent_context_max_history_turns,
            max_summary_tokens=settings.agent_context_max_summary_tokens,
            soft_threshold_ratio=settings.agent_context_soft_threshold_ratio,
            compaction_trigger_ratio=settings.agent_context_compaction_threshold_ratio,
        ))

    def prepare_history(
        self,
        history: Iterable[HistoryMessage],
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
        history: Iterable[HistoryMessage],
        current_input: str,
        *,
        context: str = "",
        history_summary: str = "",
        tools: list[ToolSchema] | None = None,
    ) -> ContextBuild:
        system = truncate_text(system_prompt or "", self.budget.max_system_tokens, self.token_counter)
        messages: list[ModelMessage] = [{"role": "system", "content": system}]
        summary = truncate_text(history_summary or "", self.budget.max_summary_tokens, self.token_counter)
        if summary:
            messages.append({"role": "system", "content": summary})

        normalized_history = [_as_message(message) for message in history]
        model_history = [_as_model_history_message(message) for message in normalized_history]
        history_groups: list[list[ModelMessage]] = []
        history_total = 0
        for group in reversed(_message_groups(model_history)):
            cost = sum(_message_tokens(message, self.token_counter) for message in group)
            if history_groups and history_total + cost > self.budget.max_history_tokens:
                break
            history_groups.append(group)
            history_total += cost
        history_messages = [message for group in reversed(history_groups) for message in group]
        messages.extend(history_messages)

        current = f"{context}\n\n{current_input}" if context else current_input
        current_before = current
        current = truncate_text(current, self.budget.max_current_task_tokens, self.token_counter)
        current_user_index = len(messages)
        messages.append({"role": "user", "content": current})

        tool_tokens = self._tool_tokens(tools)
        fitted = self.fit_messages(
            messages,
            tools=tools,
            current_user_index=current_user_index,
        )
        messages = fitted.messages
        if fitted.current_user_index is None:
            raise RuntimeError("Context fitting lost the current user anchor")
        current_user_index = fitted.current_user_index
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
            dropped_messages=len(normalized_history) - len(history_messages) + fitted.dropped_messages,
            truncated_current_task=current != current_before or fitted.truncated_current_task,
            metadata={
                "max_context_tokens": self.budget.max_context_tokens,
            },
        )

    def estimate_request_tokens(
        self,
        messages: Iterable[ModelMessage],
        *,
        tools: list[ToolSchema] | None = None,
    ) -> int:
        """Estimate the full request footprint, including tool schemas."""
        return self._messages_tokens(messages) + self._tool_tokens(tools)

    def tool_schema_tokens(self, tools: list[ToolSchema] | None = None) -> int:
        """Return the estimated token footprint of the exact tool payload."""
        return self._tool_tokens(tools)

    def should_compact(
        self,
        messages: Iterable[ModelMessage],
        *,
        tools: list[ToolSchema] | None = None,
    ) -> bool:
        """Return whether the request is using too much of its context budget."""
        return self.estimate_request_tokens(messages, tools=tools) >= (
            self.budget.max_context_tokens * self.budget.compaction_trigger_ratio
        )

    def soft_threshold_reached(
        self,
        messages: Iterable[ModelMessage],
        *,
        tools: list[ToolSchema] | None = None,
    ) -> bool:
        """Return whether the request should explicitly converge."""
        return self.estimate_request_tokens(messages, tools=tools) >= (
            self.budget.max_context_tokens * self.budget.soft_threshold_ratio
        )

    def fit_messages(
        self,
        messages: list[ModelMessage],
        *,
        tools: list[ToolSchema] | None = None,
        current_user_index: int | None = None,
    ) -> MessageFit:
        """Trim oldest non-essential messages while preserving the active task."""
        _validate_anchor(messages, current_user_index)
        result = deepcopy(messages)
        truncated = False
        dropped = 0
        limit = max(1, self.budget.max_context_tokens)
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
                original_content = _content(message)
                available = limit - self._messages_tokens(result[:anchor] + result[anchor + 1:]) - tool_tokens

                def serialized_content_tokens(content: str) -> int:
                    candidate = message.copy()
                    candidate["content"] = content
                    return _message_tokens(candidate, self.token_counter)

                message["content"] = truncate_text(
                    original_content, max(0, available), serialized_content_tokens,
                )
                truncated = _content(message) != original_content
        return MessageFit(result, current_user_index, dropped, truncated)

    def compact_tool_history(
        self,
        messages: list[ModelMessage],
        *,
        current_user_index: int | None = None,
        target_tokens: int | None = None,
        evidence_by_call: dict[str, str] | None = None,
    ) -> ToolHistoryCompaction:
        """Fold old FC tool history into a token-bounded checkpoint.

        A step is one assistant message containing tool calls plus all
        immediately following tool results. The newest steps stay verbatim
        until the requested message-token target is reached; older steps become
        a system reference containing tool names and short observations. When
        structured evidence is supplied, it takes precedence over raw
        observation prefixes. This keeps function-call/result pairs valid
        without using a fixed step count.

        Returns ``(messages, current_user_index, dropped_message_count,
        dropped_token_count)``.
        """
        _validate_anchor(messages, current_user_index)
        messages = deepcopy(messages)
        target_tokens = max(
            1,
            int(target_tokens if target_tokens is not None else self.budget.max_history_tokens),
        )
        groups: list[list[int]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.get("role") == "assistant" and message.get("tool_calls"):
                end = _tool_group_end(messages, index)
                groups.append(list(range(index, end)))
                index = end
                continue
            index += 1
        # Keep one evolving checkpoint.  Do not embed the previous checkpoint
        # verbatim: that creates nested headers and repeats already-compacted
        # prose on every later pass.
        checkpoint_indices = {
            pos for pos, message in enumerate(messages)
            if message.get("role") == "system"
            and str(message.get("content") or "").startswith("## Tool execution checkpoint")
        }
        all_group_indices = {item for group in groups for item in group}
        base_indices = set(range(len(messages))) - all_group_indices - checkpoint_indices
        retained_groups: list[list[int]] = []
        retained_tokens = sum(
            self._message_tokens_for(messages[pos]) for pos in base_indices
        )
        for group in reversed(groups):
            group_tokens = sum(self._message_tokens_for(messages[pos]) for pos in group)
            # Even the newest step must become a checkpoint if it alone
            # exceeds the history target; otherwise fit_messages drops it.
            if retained_tokens + group_tokens > target_tokens:
                break
            retained_groups.append(group)
            retained_tokens += group_tokens
        retained_groups.reverse()
        old_groups = groups[: len(groups) - len(retained_groups)]
        if not old_groups:
            return ToolHistoryCompaction(messages, current_user_index, 0, 0)
        old_indices = {item for group in old_groups for item in group}
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
        dropped_tokens = sum(self._message_tokens_for(messages[pos]) for pos in old_indices)
        return ToolHistoryCompaction(kept, current_user_index, len(old_indices), dropped_tokens)

    def _message_tokens_for(self, message: ModelMessage) -> int:
        return _message_tokens(message, self.token_counter)

    @staticmethod
    def _oldest_removable_group(
        messages: list[ModelMessage], current_user_index: int | None,
    ) -> list[int]:
        """Return the oldest safe-to-drop message group.

        Native function-calling history must keep an assistant tool-call and
        its tool results together. Removing one side leaves an invalid request
        and forces the model to repeat the step, which is more expensive than
        retaining a small paired checkpoint.
        """
        for index, message in enumerate(messages):
            removable_summary = (
                message.get("role") == "system"
                and str(message.get("content") or "").startswith("## Task execution checkpoint")
            )
            if index == 0 or index == current_user_index or (
                message.get("role") == "system" and not removable_summary
            ):
                continue
            if message.get("role") == "assistant" and message.get("tool_calls"):
                return list(range(index, _tool_group_end(messages, index)))
            if message.get("role") == "tool":
                # Defensive handling for traces restored from an older format.
                return [index]
            return [index]
        return []

    def _messages_tokens(self, messages: Iterable[ModelMessage]) -> int:
        return sum(_message_tokens(message, self.token_counter) for message in messages)

    def _tool_tokens(self, tools: list[ToolSchema] | None) -> int:
        if not tools:
            return 0
        payload = json.dumps(tools, ensure_ascii=False, sort_keys=True)
        # Tool schemas are supplied separately to the LLM API, but they still
        # consume the model context window. Count the actual payload so the
        # guard never understates the request size.
        return self.token_counter(payload)
