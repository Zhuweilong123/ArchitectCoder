"""Loading, fallback and validation adapters for optional capabilities."""

from __future__ import annotations
from typing import Any

from extensions.design_contract.plugin_api import ContractSnapshot, ContractProvider

class NoOpContractProvider:
    """Explicit fallback when contract collection is disabled/unavailable."""

    def collect(self, manifest: Any, project_id: str = "", scope: str = "project") -> ContractSnapshot:
        return ContractSnapshot(
            project_id=project_id,
            scope=scope,
            status="blocked",
            metadata={"reason": "contract provider is disabled"},
        )


def load_contracts(*, settings=None, **kwargs) -> ContractProvider:
    """Load the configured contract collector through the plugin manager."""
    from app.agent_base.core.plugins import get_plugin_manager

    provider = get_plugin_manager().load_optional("design_contract", settings=settings, kwargs=kwargs)
    return provider if provider is not None else NoOpContractProvider()


__all__ = [
    "NoOpContractProvider",
    "load_contracts",
]
