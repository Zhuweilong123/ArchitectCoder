"""Filesystem skill discovery and immutable resource snapshots."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.agent_base.core.skills import (
    SkillContent, SkillContext, SkillMeta, SkillReadError, SkillResource,
)

logger = logging.getLogger(__name__)
SKILL_ENTRY = "SKILL.md"
BUILTIN_ROOT = Path(__file__).resolve().parents[2] / "skills"


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse the existing flat name/description format without YAML dependencies."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[index + 1:]).lstrip("\n")
        key, separator, value = line.partition(":")
        if separator:
            meta[key.strip()] = value.strip()
    return {}, text


class FileSkillProvider:
    """Freeze guides and references per instance; a new instance sees updates.

    Snapshot bytes keep a running task independent of subsequent edits. Symlinks
    outside the configured root/skill directory are excluded before any read.
    Only this provider knows filesystem paths.
    """

    def __init__(self, root: Path | str | None = None):
        self._root = Path(root if root is not None else BUILTIN_ROOT).resolve()
        self._entries: list[SkillMeta] = []
        self._guides: dict[str, SkillContent] = {}
        self._resources: dict[str, dict[str, bytes]] = {}
        self._raw_guides: dict[str, str] = {}
        self._snapshot()

    def _snapshot(self) -> None:
        if not self._root.is_dir():
            return
        names: set[str] = set()
        for entry in sorted(self._root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                skill_dir = entry.resolve()
                skill_dir.relative_to(self._root)
                guide_file = (skill_dir / SKILL_ENTRY).resolve()
                guide_file.relative_to(skill_dir)
                raw = guide_file.read_bytes()
                meta, body = parse_frontmatter(raw.decode("utf-8"))
                description = meta.get("description", "").strip()
                name = meta.get("name", "").strip() or entry.name
                if not description or name in names:
                    continue
                resources: dict[str, bytes] = {}
                for path in sorted(skill_dir.rglob("*")):
                    if not path.is_file() or path.name == SKILL_ENTRY:
                        continue
                    try:
                        path.resolve().relative_to(skill_dir)
                        resources[path.relative_to(skill_dir).as_posix()] = path.read_bytes()
                    except (OSError, ValueError):
                        continue
                digest = hashlib.sha256(raw)
                for rel, data in resources.items():
                    digest.update(rel.encode("utf-8") + b"\0")
                    digest.update(hashlib.sha256(data).digest())
                version = digest.hexdigest()
                skill_id = entry.name
                self._entries.append(SkillMeta(
                    skill_id, name, description, version, source="filesystem",
                ))
                self._guides[skill_id] = SkillContent(
                    skill_id, version, body,
                    tuple(SkillResource(rel, len(data)) for rel, data in resources.items()),
                )
                self._resources[skill_id] = resources
                self._raw_guides[skill_id] = raw.decode("utf-8")
                names.add(name)
            except (OSError, ValueError, UnicodeError):
                logger.debug("[Skills] ignoring unreadable or invalid guide: %s", entry)

    def list_skills(self, context: SkillContext | None = None) -> tuple[SkillMeta, ...]:
        return tuple(self._entries)

    def read_skill(self, skill_id: str, resource: str | None = None) -> SkillContent:
        guide = self._guides.get(skill_id)
        if guide is None:
            raise SkillReadError(f"unknown skill id '{skill_id}'")
        if not resource:
            return guide
        relative = resource.replace("\\", "/")
        if ".." in relative.split("/") or relative.startswith("/") or ":" in relative:
            raise SkillReadError(f"'{resource}' escapes skill directory '{skill_id}'")
        relative = str(Path(relative).as_posix())
        if relative == SKILL_ENTRY:
            return SkillContent(skill_id, guide.version, self._raw_guides[skill_id])
        data = self._resources[skill_id].get(relative)
        if data is None:
            listing = "\n".join(f"- {item.name}" for item in guide.resources) or "  (none)"
            raise SkillReadError(f"'{resource}' not found in skill '{skill_id}'.\nReference files:\n{listing}")
        try:
            return SkillContent(skill_id, guide.version, data.decode("utf-8"))
        except UnicodeError as exc:
            raise SkillReadError(f"'{resource}' is not a UTF-8 text resource") from exc


def create(*, settings=None, root=None, **kwargs) -> FileSkillProvider:
    return FileSkillProvider(root)
