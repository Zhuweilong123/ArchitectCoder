"""Disabled failure analysis fallback and optional capability loading."""

from __future__ import annotations

from app.agent_base.ports.contract_analysis import ContractFailureAnalysisContext, ContractFailureAnalyzerPort
from .contracts import load_contracts

class NoOpContractFailureAnalyzer:
    async def analyze(self, context: ContractFailureAnalysisContext) -> str:
        return context.message or "设计契约校验阻止提交，变更已回滚。"


def load_contract_failure_analyzer(*, settings=None, provider=None) -> ContractFailureAnalyzerPort:
    if settings is not None and not getattr(settings, "agent_design_contract_enabled", True):
        return NoOpContractFailureAnalyzer()
    provider = provider if provider is not None else load_contracts(settings=settings)
    return provider if callable(getattr(provider, "analyze", None)) else NoOpContractFailureAnalyzer()
