"""Governance policy for candidate memories before persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import MemoryType


@dataclass(frozen=True)
class MemoryWriteDecision:
    allowed: bool
    reason: str
    confidence: float


class MemoryWritePolicy:
    """Deterministic gate between LLM extraction and memory storage."""

    _DURABLE_TYPES = {
        MemoryType.PREFERENCE,
        MemoryType.DECISION,
        MemoryType.REJECTION,
        MemoryType.CONVENTION,
    }

    def __init__(self, min_confidence: float = 0.55, max_summary_length: int = 500):
        self.min_confidence = max(0.0, min(1.0, min_confidence))
        self.max_summary_length = max(1, max_summary_length)

    def evaluate(
        self,
        item: dict[str, Any],
        *,
        user_feedback: str | None = None,
    ) -> MemoryWriteDecision:
        raw_type = item.get("memory_type", MemoryType.INSIGHT)
        try:
            memory_type = MemoryType(raw_type)
        except (TypeError, ValueError):
            return MemoryWriteDecision(False, "unsupported_memory_type", 0.0)

        summary = str(item.get("summary", item.get("content", "")) or "").strip()
        if not summary:
            return MemoryWriteDecision(False, "empty_summary", 0.0)
        if len(summary) > self.max_summary_length:
            return MemoryWriteDecision(False, "summary_too_long", 0.0)

        temporary = item.get("temporary", item.get("is_temporary", False))
        if temporary is True or str(item.get("status", "")).lower() in {
            "temporary", "transient", "candidate_rejected", "rejected",
        }:
            return MemoryWriteDecision(False, "temporary_or_rejected", 0.0)

        try:
            confidence = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            return MemoryWriteDecision(False, "invalid_confidence", 0.0)
        if not 0.0 <= confidence <= 1.0:
            return MemoryWriteDecision(False, "confidence_out_of_range", confidence)

        feedback_validated = user_feedback in {"accepted", "modified"}
        threshold = self.min_confidence
        if memory_type in self._DURABLE_TYPES and feedback_validated:
            threshold = min(threshold, 0.35)
        if confidence < threshold:
            return MemoryWriteDecision(False, "confidence_below_threshold", confidence)

        return MemoryWriteDecision(True, "accepted", confidence)


class MemoryRecallPolicy:
    """Select relevant, diverse memories within the injection budget."""

    _TYPE_PRIORITY = {
        MemoryType.DECISION: 5,
        MemoryType.REJECTION: 4,
        MemoryType.CONVENTION: 3,
        MemoryType.PREFERENCE: 2,
        MemoryType.INSIGHT: 1,
    }

    def __init__(
        self,
        min_score: float = 0.0,
        max_per_type: int = 2,
        duplicate_threshold: float = 0.8,
    ):
        self.min_score = min_score
        self.max_per_type = max(1, max_per_type)
        self.duplicate_threshold = max(0.0, min(1.0, duplicate_threshold))

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in str(text).lower().split() if token}

    def select(self, results: list, *, top_k: int, max_tokens: int) -> list:
        selected = []
        type_counts: dict[MemoryType, int] = {}
        seen_subjects: set[str] = set()
        used_tokens = 0
        ordered = sorted(
            results,
            key=lambda result: (
                result.score,
                self._TYPE_PRIORITY.get(result.entry.memory_type, 0),
            ),
            reverse=True,
        )
        for result in ordered:
            if len(selected) >= max(0, top_k) or result.score <= self.min_score:
                continue
            memory_type = result.entry.memory_type
            if type_counts.get(memory_type, 0) >= self.max_per_type:
                continue
            subject = str(getattr(result.entry, "subject", "") or "").strip().lower()
            if subject and subject in seen_subjects:
                continue
            summary = str(result.entry.summary or "").strip()
            if not summary:
                continue
            candidate_tokens = self._tokens(summary)
            if any(
                self._similarity(candidate_tokens, self._tokens(item.entry.summary))
                >= self.duplicate_threshold
                for item in selected
            ):
                continue
            cost = max(1, len(summary + " " + " ".join(result.entry.tags)) // 2)
            if selected and used_tokens + cost > max(0, max_tokens):
                continue
            if not selected and cost > max(0, max_tokens):
                continue
            selected.append(result)
            type_counts[memory_type] = type_counts.get(memory_type, 0) + 1
            if subject:
                seen_subjects.add(subject)
            used_tokens += cost
        return selected

    @staticmethod
    def _similarity(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)
