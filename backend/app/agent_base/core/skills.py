"""Provider-neutral, read-only skills port and per-Agent catalog."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


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


class NoOpSkillProvider:
    def list_skills(self, context: SkillContext | None = None) -> tuple[SkillMeta, ...]:
        return ()

    def read_skill(self, skill_id: str, resource: str | None = None) -> SkillContent:
        raise SkillReadError("skills provider is disabled or unavailable")


@dataclass(frozen=True)
class SkillCatalog:
    """Share one directory and provider between prompt and tool schemas."""

    provider: SkillProvider
    entries: tuple[SkillMeta, ...]

    def read_skill(self, skill_id: str, resource: str | None = None) -> SkillContent:
        meta = next((item for item in self.entries if item.skill_id == skill_id), None)
        if meta is None:
            raise SkillReadError(f"unknown skill id '{skill_id}'")
        try:
            content = self.provider.read_skill(skill_id, resource)
            if not isinstance(content, SkillContent):
                raise SkillReadError("skill provider returned invalid content")
            if content.skill_id != meta.skill_id or content.version != meta.version:
                raise SkillReadError("skill version changed; start a new task to refresh the catalog")
            return content
        except SkillReadError:
            raise
        except Exception as exc:
            logger.warning("[Skills] resource read failed", exc_info=True)
            raise SkillReadError("skill provider could not read the requested resource") from exc


def capture_skill_catalog(
    provider: SkillProvider | None = None, *, context: SkillContext | None = None,
) -> SkillCatalog:
    provider = provider if provider is not None else load_skills()
    try:
        entries = tuple(provider.list_skills(context))
        ids: set[str] = set()
        names: set[str] = set()
        for entry in entries:
            if not isinstance(entry, SkillMeta) or not all((
                entry.skill_id, entry.name, entry.description, entry.version,
            )) or entry.skill_id in ids or entry.name in names:
                raise ValueError("skill catalog contains invalid or duplicate metadata")
            ids.add(entry.skill_id)
            names.add(entry.name)
        return SkillCatalog(provider, entries)
    except Exception:
        logger.warning("[Skills] directory unavailable; continuing without skills", exc_info=True)
        return SkillCatalog(NoOpSkillProvider(), ())


def load_skills(*, settings=None, **kwargs) -> SkillProvider:
    from .plugins import get_plugin_manager

    provider = get_plugin_manager().load("skills", settings=settings, kwargs=kwargs)
    return provider if provider is not None else NoOpSkillProvider()
