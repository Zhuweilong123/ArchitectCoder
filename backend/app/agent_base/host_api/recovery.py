"""Optional tool capabilities used to match recovery targets."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RecoveryPathPort(Protocol):
    @property
    def recovery_working_directory(self) -> str: ...

    def resolve_recovery_path(self, value: str, *, relative_to: str = "") -> str: ...


class RecoveryToolLookup(Protocol):
    def get_tool(self, name: str) -> object | None: ...
