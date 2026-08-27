"""skill_loader 单元测试。"""
from app.agent_base.core.hooks import HookContext, HookEvent, get_hooks
from app.agent_base.tools.my_tools.skill_loader import (
    SkillTool, build_skills_section, discover_skills,
)


def _make_skills(tmp_path):
    """造一个带两个 skill 的临时 skills 目录。"""
    root = tmp_path / "skills"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: does alpha things\n---\n# Alpha\n\nBody A.",
        encoding="utf-8",
    )
    (root / "alpha" / "extra.md").write_text("EXTRA-A", encoding="utf-8")
    (root / "beta").mkdir()
    (root / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: does beta things\n---\n# Beta\n\nBody B.",
        encoding="utf-8",
    )
    # 无 SKILL.md 的目录应被跳过
    (root / "orphan").mkdir()
    (root / "orphan" / "notes.md").write_text("no skill here", encoding="utf-8")
    return root


def test_discover_skips_missing_skill_and_faulty_frontmatter(tmp_path):
    root = _make_skills(tmp_path)
    # 缺 description 的 SKILL.md 也应被跳过
    (root / "broken").mkdir()
    (root / "broken" / "SKILL.md").write_text("---\nname: broken\n---\nBody.", encoding="utf-8")

    names = [s.name for s in discover_skills(root)]
    assert names == ["alpha", "beta"]


def test_build_skills_section_lists_names_and_descriptions(tmp_path):
    root = _make_skills(tmp_path)
    section = build_skills_section(root)
    assert "## Skills" in section
    assert "- alpha: does alpha things" in section
    assert "- beta: does beta things" in section
    assert "orphan" not in section


def test_build_skills_section_empty_when_no_skills(tmp_path):
    assert build_skills_section(tmp_path / "empty") == ""


def test_l2_load_returns_body_and_reference_listing(tmp_path):
    root = _make_skills(tmp_path)
    tool = SkillTool(root)
    out = tool.run({"name": "alpha"})
    assert "Body A." in out
    assert "extra.md" in out          # 引用文件清单


def test_l3_load_reference_file(tmp_path):
    root = _make_skills(tmp_path)
    tool = SkillTool(root)
    assert tool.run({"name": "alpha", "file": "extra.md"}) == "EXTRA-A"


def test_path_traversal_rejected(tmp_path):
    root = _make_skills(tmp_path)
    tool = SkillTool(root)
    out = tool.run({"name": "alpha", "file": "../beta/SKILL.md"})
    assert "escapes skill directory" in out
    out = tool.run({"name": "alpha", "file": "../../outside.txt"})
    assert "escapes skill directory" in out


def test_unknown_skill_lists_available(tmp_path):
    root = _make_skills(tmp_path)
    tool = SkillTool(root)
    out = tool.run({"name": "nope"})
    assert "unknown skill" in out
    assert "alpha, beta" in out


def test_missing_reference_file_lists_available(tmp_path):
    root = _make_skills(tmp_path)
    tool = SkillTool(root)
    out = tool.run({"name": "beta", "file": "zzz.md"})
    assert "not found" in out


def test_schema_enum_restricts_names(tmp_path):
    root = _make_skills(tmp_path)
    tool = SkillTool(root)
    props = tool.to_openai_schema()["function"]["parameters"]["properties"]
    assert props["name"]["enum"] == ["alpha", "beta"]
    assert props["name"]["type"] == "string"


def test_truncate_hook_exempts_skill_tool(tmp_path):
    big = "x" * 15000
    out = get_hooks().trigger(
        HookEvent.TOOL_AFTER,
        HookContext(event=HookEvent.TOOL_AFTER, agent_name="t", tool_name="skill", tool_output=big),
    )
    assert out is None  # 不截断
    out = get_hooks().trigger(
        HookEvent.TOOL_AFTER,
        HookContext(event=HookEvent.TOOL_AFTER, agent_name="t", tool_name="read_file", tool_output=big),
    )
    assert out is not None and "truncated" in out
