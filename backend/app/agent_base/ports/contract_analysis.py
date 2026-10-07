"""Read-only model analysis for design-contract gate failures.

The plugin owns evidence formatting and prompts.  A host callback exposes
read-only model invocation without giving the plugin a live Agent instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from .contract_checks import ContractCheckResult
from .analysis import AnalysisRequest


@dataclass(frozen=True)
class ContractFailureAnalysisContext:
    result: ContractCheckResult
    invoke: Callable[[AnalysisRequest], Awaitable[str]]
    message: str = ""
    run_id: str = ""
    rollback_completed: bool = True
    allowed_tools: tuple[str, ...] | None = None


class ContractFailureAnalyzerPort(Protocol):
    async def analyze(self, context: ContractFailureAnalysisContext) -> str: ...
