"""Contract capability loading and disabled/unavailable fallbacks."""

from __future__ import annotations

import copy
from typing import Any, Callable

from app.agent_base.ports.contract_checks import ContractCheckResult
from app.agent_base.ports.contract_gate import ContractGateContext, ContractGateDecision, ContractGatePort
from app.runtime.workspace import WorkspaceManifest

from .contracts import load_contracts
from .review import ReviewAdapter


def build_contract_gate_context(
    *, agent, change_set, review_manager, emit, emit_review,
    project_file="", source_dir="", test_dir="", run_id="",
    settings=None, contract_enabled=True,
) -> ContractGateContext:
    """Freeze host state before handing a request to a plugin."""
    manifest = getattr(agent, "workspace_manifest", {}) or WorkspaceManifest.from_paths(
        project_file=project_file, source_dir=source_dir, test_dir=test_dir,
    ).to_dict()
    return ContractGateContext(
        workspace_manifest=copy.deepcopy(manifest),
        changed_paths=tuple(copy.deepcopy(change_set.manifest())) if change_set is not None else (),
        emit=emit, request_review=ReviewAdapter(review_manager, emit_review).ask if review_manager is not None else None,
        run_id=run_id, settings=settings, contract_enabled=contract_enabled,
    )


class NoOpContractGate:
    async def evaluate(self, context: ContractGateContext) -> ContractGateDecision:
        return ContractGateDecision(allowed=True)

    async def finalize(
        self,
        context: ContractGateContext,
        prior_result: ContractCheckResult | None = None,
    ) -> ContractCheckResult | None:
        return None


class UnavailableContractGate:
    """A configured but missing gate cannot approve a candidate implicitly."""
    async def evaluate(self, context: ContractGateContext) -> ContractGateDecision:
        if not context.contract_enabled or not context.changed_paths:
            return ContractGateDecision(allowed=True)
        result = ContractCheckResult(
            check_id=f"unavailable-{context.run_id}", status="inconclusive",
            project_id="", message="configured contract gate capability is unavailable",
            changed_paths=tuple(str(item.get("path", "")) if isinstance(item, dict) else str(item)
                                for item in context.changed_paths),
            metadata={"reason": "missing_evaluate_capability"},
        )
        await context.emit({"event": "contract_check", **result.to_dict(),
                            "run_id": context.run_id, "contract_enabled": context.contract_enabled})
        return ContractGateDecision(allowed=False, result=result, message=result.message)

    async def finalize(self, context, prior_result=None):
        return None


def load_contract_gate(*, settings=None, language_runner: Callable[..., Any] | None = None,
                       provider=None) -> ContractGatePort:
    if settings is not None and not getattr(settings, "agent_design_contract_enabled", True):
        return NoOpContractGate()
    provider = provider if provider is not None else load_contracts(settings=settings, language_runner=language_runner)
    return provider if callable(getattr(provider, "evaluate", None)) else UnavailableContractGate()


def resolve_contract_enabled(requested: bool | None, settings: Any = None) -> bool:
    """Resolve a per-run request without mutating process-wide settings.

    The server setting is an upper bound.  Strict production mode deliberately
    ignores a client-side disable request.
    """
    configured = bool(getattr(settings, "agent_design_contract_enabled", True))
    if not configured or getattr(settings, "strict_production", False):
        return configured
    return configured if requested is None else bool(requested)
