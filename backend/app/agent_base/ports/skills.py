"""Provider-neutral skill data and read-only provider protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SkillContext:
    workspace_root: str = ""


@dataclass(frozen=True)
class SkillMeta:
    skill_id: str
    name: str
    description: str
    version: str
    scope: str = "builtin"
    source: str = ""


@dataclass(frozen=True)
class SkillResource:
    name: str
    size_bytes: int


@dataclass(frozen=True)
class SkillContent:
    skill_id: str
    version: str
    text: str
    resources: tuple[SkillResource, ...] = ()


class SkillReadError(Exception):
    """A resource is missing, inaccessible, or incompatible with the catalog."""


class SkillProvider(Protocol):
    """One instance represents one consistent skill version set.

    Reading resources never executes them. Storage and access checks belong to
    the provider; consumers use opaque skill/resource identifiers.
    """

    def list_skills(self, context: SkillContext | None = None) -> tuple[SkillMeta, ...]: ...

    def read_skill(self, skill_id: str, resource: str | None = None) -> SkillContent: ...
