"""Provider-neutral L1 prompt directory and L2/L3 skill tool adapter."""

from __future__ import annotations

from pathlib import Path
from typing import List

from app.agent_base.adapters.skills import (SkillCatalog, capture_skill_catalog, load_skills)
from extensions.skills.plugin_api import SkillMeta, SkillReadError
from app.agent_base.tools.base import Tool, ToolParameter


def _catalog(root: Path | None, catalog: SkillCatalog | None) -> SkillCatalog:
    if catalog is not None:
        return catalog
    return capture_skill_catalog(load_skills(**({"root": root} if root is not None else {})))


def discover_skills(root: Path | None = None) -> tuple[SkillMeta, ...]:
    """Compatibility entry point; discovery belongs to the configured provider."""
    return _catalog(root, None).entries


def build_skills_section(root: Path | None = None, *, catalog: SkillCatalog | None = None) -> str:
    skills = _catalog(root, catalog).entries
    if not skills:
        return ""
    lines = [
        "## Skills",
        "On-demand knowledge packs. When a task matches one, call "
        "skill(name) FIRST and follow what it says — do not work from memory:",
    ]
    lines.extend(f"- {skill.name}: {skill.description}" for skill in skills)
    return "\n".join(lines)


class SkillTool(Tool):
    def __init__(self, root: Path | None = None, *, catalog: SkillCatalog | None = None):
        super().__init__(
            name="skill",
            description=(
                "Load a knowledge pack listed under '## Skills' in the system "
                "prompt. Call with name only to get the skill's main guide plus "
                "its reference file list; pass file to load one reference file. "
                "Read the complete returned guide before following it; if a "
                "continuation marker appears, use read_tool_output for the rest."
            ),
        )
        self.catalog = _catalog(root, catalog)

    def get_parameters(self) -> List[ToolParameter]:
        return []

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": [s.name for s in self.catalog.entries]},
                        "file": {"type": "string", "description": (
                            "Optional reference file, relative to the skill directory, "
                            "as listed by a prior skill(name) call."
                        )},
                    },
                    "required": ["name"],
                },
            },
        }

    def run(self, parameters: dict) -> str:
        name = (parameters.get("name") or "").strip()
        skill = next((s for s in self.catalog.entries if s.name == name), None)
        if skill is None:
            available = ", ".join(s.name for s in self.catalog.entries) or "(none)"
            return f"Error: unknown skill '{name}'. Available: {available}"
        resource = (parameters.get("file") or "").strip() or None
        try:
            content = self.catalog.read_skill(skill.skill_id, resource)
        except SkillReadError as exc:
            return f"Error: {exc}"
        if resource or not content.resources:
            return content.text
        listing = "\n".join(f"- {item.name} ({item.size_bytes} bytes)" for item in content.resources)
        return (
            f"{content.text}\n\n---\n"
            f'Reference files (load with skill(name="{skill.name}", file=...)):\n{listing}'
        )
