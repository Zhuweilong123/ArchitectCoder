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
