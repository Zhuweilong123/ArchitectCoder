"""Small risk classifier shared by tools that can require approval."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RiskDecision:
    action: str  # allow | ask | deny
    level: str   # low | medium | high | critical
    reason: str = ""
    pattern: str = ""


class RiskPolicy:
    """Classify a tool invocation without making approval decisions itself."""

    def __init__(
        self,
        *,
        deny_patterns: Iterable[str] = (),
        approval_patterns: Iterable[str] = (),
    ) -> None:
        self._deny = tuple(str(item).lower() for item in deny_patterns if item)
        self._approval = tuple(str(item).lower() for item in approval_patterns if item)

    def evaluate(self, tool_name: str, parameters: dict[str, Any]) -> RiskDecision:
        if tool_name != "bash":
            return RiskDecision("allow", "low")
        command = str(parameters.get("command", ""))
        lowered = command.lower()
        for pattern in self._deny:
            if pattern in lowered:
                return RiskDecision("deny", "critical", "high-risk command", pattern)
        for pattern in self._approval:
            if pattern in lowered:
                return RiskDecision("ask", "high", "sensitive command", pattern)
        return RiskDecision("allow", "low")

    @staticmethod
    def approval_scope(tool_name: str, parameters: dict[str, Any]) -> dict[str, str]:
        command = str(parameters.get("command", ""))
        return {
            "tool": tool_name,
            "input_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        }

    def approval_is_valid(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        scope: dict[str, str],
    ) -> bool:
        return scope == self.approval_scope(tool_name, parameters)
