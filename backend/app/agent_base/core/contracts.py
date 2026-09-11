"""Stable port for design/source/test contract collection plugins."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ContractEntity:
    """One normalized fact collected from a project artifact."""

    entity_id: str
    entity_type: str
    name: str
    path: str = ""
    line: int | None = None
    parent_id: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractMapping:
    """A traceable relation between design, source, and test facts."""

    mapping_id: str
    design_entity_id: str = ""
    source_entity_id: str = ""
    test_entity_ids: tuple[str, ...] = ()
    confidence: str = "inferred"
    strategy: str = ""


@dataclass(frozen=True)
class ContractSnapshot:
    """Immutable, serializable project contract snapshot."""

    project_id: str
    scope: str
    status: str
    entities: tuple[ContractEntity, ...] = ()
    mappings: tuple[ContractMapping, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContractProvider(Protocol):
    """Provider port implemented by optional contract collector plugins."""

    def collect(
        self,
        manifest: Any,
        project_id: str = "",
        scope: str = "project",
    ) -> ContractSnapshot: ...


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
    from .plugins import get_plugin_manager

    provider = get_plugin_manager().load("design_contract", settings=settings, kwargs=kwargs)
    return provider if provider is not None else NoOpContractProvider()


__all__ = [
    "ContractEntity",
    "ContractMapping",
    "ContractSnapshot",
    "ContractProvider",
    "NoOpContractProvider",
    "load_contracts",
]
