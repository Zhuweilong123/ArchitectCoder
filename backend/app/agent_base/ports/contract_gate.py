"""Stable contract-gate port used by the Agent execution lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from .contract_checks import ContractCheckResult
from .review import ReviewPrompt


@dataclass(frozen=True)
class ContractGateContext:
    """Transport-neutral context supplied by one Agent run."""

    workspace_manifest: dict[str, Any]
    changed_paths: tuple[dict[str, Any] | str, ...]
    emit: Callable[[dict], Awaitable[bool]]
    request_review: Callable[[ReviewPrompt], Awaitable[str]] | None = None
    run_id: str = ""
    settings: Any = None
    contract_enabled: bool = True


@dataclass(frozen=True)
class ContractGateDecision:
    allowed: bool
    result: ContractCheckResult | None = None
    message: str = ""


class ContractGatePort(Protocol):
    async def evaluate(self, context: ContractGateContext) -> ContractGateDecision: ...

    async def finalize(
        self,
        context: ContractGateContext,
        prior_result: ContractCheckResult | None = None,
    ) -> ContractCheckResult | None: ...
