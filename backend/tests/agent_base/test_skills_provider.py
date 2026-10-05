"""Plugin substitution, task snapshots, and Agent skill wiring."""

import sys
from types import SimpleNamespace

import pytest

from app.agent_base.core.skills import (
    SkillContent, SkillMeta, capture_skill_catalog, load_skills,
)
from app.agent_base.tools.my_tools.skill_loader import SkillTool, build_skills_section
from extensions.skills.provider import FileSkillProvider


class MemoryProvider:
    def list_skills(self, context=None):
        return (SkillMeta("opaque-id", "remote-guide", "Remote instructions", "v1"),)

    def read_skill(self, skill_id, resource=None):
        return SkillContent(skill_id, "v1", "remote body")


def write_guide(root, folder="guide", body="old body"):
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {folder}\ndescription: A guide\n---\n{body}", encoding="utf-8",
    )
    return directory


def test_configured_provider_needs_no_filesystem(monkeypatch):
    provider = MemoryProvider()
    monkeypatch.setitem(sys.modules, "test_skill_extension", SimpleNamespace(create=lambda **kw: provider))
    settings = SimpleNamespace(agent_skills_enabled=True, agent_skills_provider="test_skill_extension:create")
    catalog = capture_skill_catalog(load_skills(settings=settings))
    tool = SkillTool(catalog=catalog)
    assert "remote-guide: Remote instructions" in build_skills_section(catalog=catalog)
    assert tool.to_openai_schema()["function"]["parameters"]["properties"]["name"]["enum"] == ["remote-guide"]
    assert tool.run({"name": "remote-guide"}) == "remote body"


@pytest.mark.parametrize("enabled", [True, False])
def test_disabled_or_unavailable_plugin_degrades_to_empty_catalog(enabled):
    provider = load_skills(settings=SimpleNamespace(
        agent_skills_enabled=enabled, agent_skills_provider="missing_skill_extension:create",
    ))
    catalog = capture_skill_catalog(provider)
    assert catalog.entries == ()
    assert build_skills_section(catalog=catalog) == ""


def test_files_and_catalog_remain_consistent_until_new_agent(tmp_path):
    directory = write_guide(tmp_path)
    ref = directory / "reference.txt"
    ref.write_text("old reference", encoding="utf-8")
    catalog = capture_skill_catalog(FileSkillProvider(tmp_path))
    tool = SkillTool(catalog=catalog)
    write_guide(tmp_path, body="new body")
    ref.unlink()
    write_guide(tmp_path, "added")
    assert "old body" in tool.run({"name": "guide"})
    assert tool.run({"name": "guide", "file": "reference.txt"}) == "old reference"
    assert "name: guide" in tool.run({"name": "guide", "file": "SKILL.md"})
    assert "added" not in build_skills_section(catalog=catalog)
    updated = capture_skill_catalog(FileSkillProvider(tmp_path))
    assert updated.entries[1].version != catalog.entries[0].version
    assert "added" in build_skills_section(catalog=updated)
    assert "new body" in SkillTool(catalog=updated).run({"name": "guide"})


@pytest.mark.parametrize("resource", ["../secret", r"..\secret", "/secret", "C:/secret"])
def test_file_provider_rejects_outside_resources(tmp_path, resource):
    write_guide(tmp_path)
    tool = SkillTool(catalog=capture_skill_catalog(FileSkillProvider(tmp_path)))
    assert "escapes skill directory" in tool.run({"name": "guide", "file": resource})


def test_provider_version_drift_and_read_failure_are_tool_errors():
    provider = MemoryProvider()
    catalog = capture_skill_catalog(provider)
    provider.read_skill = lambda *args: SkillContent("opaque-id", "v2", "new body")
    assert "version changed" in SkillTool(catalog=catalog).run({"name": "remote-guide"})
    def broken(*args):
        raise RuntimeError("private storage details")
    provider.read_skill = broken
    result = SkillTool(catalog=catalog).run({"name": "remote-guide"})
    assert "could not read" in result
    assert "private storage" not in result


def test_reference_change_updates_version_without_guide_change(tmp_path):
    directory = write_guide(tmp_path)
    reference = directory / "reference.txt"
    reference.write_text("before", encoding="utf-8")
    first = FileSkillProvider(tmp_path)
    reference.write_text("after", encoding="utf-8")
    second = FileSkillProvider(tmp_path)
    assert first.list_skills()[0].version != second.list_skills()[0].version
    assert first.read_skill("guide", "reference.txt").text == "before"
    assert second.read_skill("guide", "reference.txt").text == "after"


def test_external_symlink_resource_is_excluded(tmp_path):
    root = tmp_path / "skills"
    directory = write_guide(root)
    secret = tmp_path / "secret.txt"
    secret.write_text("outside", encoding="utf-8")
    try:
        (directory / "link.txt").symlink_to(secret)
    except OSError:
        pytest.skip("Creating symlinks requires host privileges")
    provider = FileSkillProvider(root)
    assert not provider.read_skill("guide").resources
    assert "not found" in SkillTool(catalog=capture_skill_catalog(provider)).run({
        "name": "guide", "file": "link.txt",
    })


def test_invalid_provider_directory_degrades_safely():
    provider = MemoryProvider()
    provider.list_skills = lambda context=None: (provider.__class__().list_skills()[0],) * 2
    assert capture_skill_catalog(provider).entries == ()


def test_subagent_toolkits_share_prompt_and_tool_catalog(tmp_path):
    from app.agent_base.tools.my_tools.subagent_tool import SpawnSubagentTool
    catalog = capture_skill_catalog(MemoryProvider())
    tool = SpawnSubagentTool(llm=object(), source_dir=str(tmp_path), skill_catalog=catalog)
    for kind, registry in tool.sub_registries.items():
        skills = [registry.get_tool("skill")] if "skill" in registry.list_tools() else []
        if kind == "read_only":
            assert not skills
            assert "## Skills" not in tool.system_prompts[kind]
        else:
            assert skills[0].catalog is catalog
            assert "remote-guide" in tool.system_prompts[kind]


def test_conversation_tools_omit_disabled_skills(tmp_path):
    from app.agent_base.tools.my_tools.conversation_tools import create_conversation_tools
    from app.agent_base.core.skills import NoOpSkillProvider
    tools, _ = create_conversation_tools(
        object(), source_dir=str(tmp_path), include_review=False,
        skill_catalog=capture_skill_catalog(NoOpSkillProvider()),
    )
    assert all(tool.name != "skill" for tool in tools)


@pytest.mark.parametrize("enabled", [True, False])
def test_dev_agent_assembly_uses_one_catalog(monkeypatch, tmp_path, enabled):
    import asyncio
    from app.agent_base import assembly
    from app.agent_base.core.memory import NoOpMemory
    from app.agent_base.core.skills import NoOpSkillProvider
    from backend.config import get_settings
    provider = MemoryProvider() if enabled else NoOpSkillProvider()
    calls = []
    def listed(context=None):
        calls.append(context)
        return MemoryProvider().list_skills() if enabled else ()
    provider.list_skills = listed
    monkeypatch.setattr(assembly, "load_skills", lambda **kw: provider)
    monkeypatch.setattr(assembly, "load_memory", lambda **kw: NoOpMemory())
    monkeypatch.setattr(assembly, "get_settings", lambda: get_settings().model_copy(update={
        "agent_main_subagent_enabled": True,
    }))
    agent, _, prompt = asyncio.run(assembly.create_dev_agent(object(), workspace_root=str(tmp_path)))
    assert len(calls) == 1
    assert calls[0].workspace_root == str(tmp_path)
    skill = agent.tool_registry.get_tool("skill")
    assert (skill is not None) == enabled
    assert ("remote-guide" in prompt.system_prompt) == enabled
    spawn = agent.tool_registry.get_tool("spawn_subagent")
    for kind, registry in spawn.sub_registries.items():
        if enabled and kind != "read_only":
            assert registry.get_tool("skill").catalog is skill.catalog
        else:
            assert registry.get_tool("skill") is None
