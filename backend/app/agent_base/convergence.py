"""Progress-based convergence decisions for long-running Agent loops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ConvergenceDecision:
    """A controller decision after one tool round."""

    action: str = "continue"  # continue | recover | finalize
    reason: str = ""
    message: str = ""


class ConvergenceController:
    """Detect repeated or non-productive behavior without a step limit.

    Normal productive work is unbounded.  The controller only intervenes when
    the same semantic action/failure repeats or a complete tool round produces
    no new evidence or state change.
    """

    def __init__(
        self,
        *,
        max_stalled_rounds: int = 3,
        max_recovery_rounds: int = 2,
        repeat_action_threshold: int = 3,
    ) -> None:
        self.max_stalled_rounds = max(1, int(max_stalled_rounds))
        self.max_recovery_rounds = max(1, int(max_recovery_rounds))
        self.repeat_action_threshold = max(2, int(repeat_action_threshold))
        self._action_results: dict[str, str] = {}
        self._action_counts: dict[str, int] = {}
        self._failure_counts: dict[str, int] = {}
        self.stalled_rounds = 0
        self.recovery_rounds = 0

    @staticmethod
    def _fingerprint(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def _action_key(cls, detail: dict[str, Any]) -> str:
        return cls._fingerprint({
            "name": detail.get("name", ""),
            "arguments": detail.get("arguments", {}),
        })

    @classmethod
    def _failure_key(cls, detail: dict[str, Any]) -> str:
        return cls._fingerprint({
            "name": detail.get("name", ""),
            "arguments": detail.get("arguments", {}),
            "status": detail.get("status", ""),
            "error_code": detail.get("error_code", ""),
        })

    @classmethod
    def _result_key(cls, detail: dict[str, Any]) -> str:
        return cls._fingerprint({
            "status": detail.get("status", ""),
            "error_code": detail.get("error_code", ""),
            "observation": detail.get("observation", ""),
            "changes": detail.get("changes", []),
            "verification": detail.get("verification"),
        })

    def observe(self, details: Iterable[dict[str, Any]]) -> ConvergenceDecision:
        """Record one tool round and decide whether the loop should continue."""
        records = [detail for detail in details if isinstance(detail, dict)]
        if not records:
            self.stalled_rounds += 1
            return self._stalled_decision()

        meaningful_progress = False
        repeated_failure: str | None = None
        repeated_action: str | None = None

        for detail in records:
            action_key = self._action_key(detail)
            result_key = self._result_key(detail)
            previous_result = self._action_results.get(action_key)
            self._action_counts[action_key] = self._action_counts.get(action_key, 0) + 1
            self._action_results[action_key] = result_key

            status = str(detail.get("status") or "")
            if status == "success" and (
                detail.get("changes")
                or detail.get("verification")
                or previous_result != result_key
            ):
                meaningful_progress = True

            if status != "success":
                failure_key = self._failure_key(detail)
                count = self._failure_counts.get(failure_key, 0) + 1
                self._failure_counts[failure_key] = count
                if count >= self.max_recovery_rounds + 1:
                    repeated_failure = failure_key
                elif count >= self.max_recovery_rounds:
                    repeated_failure = failure_key

            if (
                previous_result == result_key
                and self._action_counts[action_key] >= self.repeat_action_threshold
            ):
                repeated_action = action_key

        if meaningful_progress:
            self.stalled_rounds = 0
            self.recovery_rounds = 0
            return ConvergenceDecision()

        self.stalled_rounds += 1
        if repeated_failure is not None:
            if self._failure_counts.get(repeated_failure, 0) > self.max_recovery_rounds:
                return ConvergenceDecision(
                    "finalize", "repeated_failure",
                    "The same tool failure repeated after recovery attempts. "
                    "Stop retrying and report the blocker using the evidence above.",
                )
            self.recovery_rounds += 1
            return ConvergenceDecision(
                "recover", "repeated_failure",
                "The previous tool failure repeated. Change the strategy, narrow the "
                "target, or run the focused verification; do not repeat the same call.",
            )

        if repeated_action is not None:
            self.recovery_rounds += 1
            if self.recovery_rounds > self.max_recovery_rounds:
                return ConvergenceDecision(
                    "finalize", "repeated_action",
                    "The same semantic tool action produced no new evidence. "
                    "Stop exploring and report the verified result or blocker.",
                )
            return ConvergenceDecision(
                "recover", "repeated_action",
                "The same semantic tool action produced no new evidence. "
                "Use the existing evidence and take the smallest next action.",
            )

        return self._stalled_decision()

    def _stalled_decision(self) -> ConvergenceDecision:
        if self.stalled_rounds >= self.max_stalled_rounds:
            return ConvergenceDecision(
                "finalize", "convergence_stalled",
                "No new evidence or state change was produced across recovery attempts. "
                "Stop the loop and clearly report completed work, remaining work, and uncertainty.",
            )
        if self.stalled_rounds >= self.max_stalled_rounds - 1:
            self.recovery_rounds += 1
            return ConvergenceDecision(
                "recover", "convergence_stalled",
                "The last tool round produced no new evidence or state change. "
                "Choose one concrete action that changes state or explain the blocker.",
            )
        return ConvergenceDecision()


__all__ = ["ConvergenceController", "ConvergenceDecision"]
