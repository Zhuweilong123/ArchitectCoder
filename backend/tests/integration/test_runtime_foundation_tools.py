"""Regression coverage for the host Runtime and stable foundation tools."""

from __future__ import annotations

from app.agent_base.assembly import DevPromptBuilder
from app.agent_base.tools.registry import ToolRegistry
from app.services.change_set import ChangeSet
from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
from app.runtime import (
    NativePowerShellExecutor,
    build_environment_context,
    resolve_command_environment,
)


def _tool(tools, name):
    return next(tool for tool in tools if tool.name == name)


def test_auto_environment_does_not_require_wsl_on_windows():
    assert resolve_command_environment("auto", host_os="nt", platform_name="win32") == "native_windows"


def test_runtime_context_is_embedded_in_static_prompt(tmp_path):
    context = build_environment_context(
        executor=NativePowerShellExecutor(),
        cwd=str(tmp_path),
        workspace_roots=[str(tmp_path)],
    )
    prompt = DevPromptBuilder(
        source_dir=str(tmp_path),
        environment_context=context,
    ).system_prompt

    assert "Host OS: windows" in prompt
    assert "Execution OS: windows" in prompt
    assert "Shell: powershell" in prompt
    assert "Execution mode: windows-powershell" in prompt
    assert prompt.count(str(tmp_path)) == 1


def test_runtime_context_deduplicates_working_directory_and_roots(tmp_path):
    other = tmp_path / "test"
    other.mkdir()
    context = build_environment_context(
        executor=NativePowerShellExecutor(),
        cwd=str(tmp_path),
        workspace_roots=[str(tmp_path), str(other), str(other)],
    )

    prompt = context.to_prompt()
    lines = prompt.splitlines()
    assert lines.count(f"- Working directory: {tmp_path}") == 1
    assert lines.count(f"  - {tmp_path}") == 0
    assert lines.count(f"  - {other}") == 1


def test_prompt_uses_project_root_and_describes_workspace_layout(tmp_path):
    source = tmp_path / "src"
    test = tmp_path / "test"
    design = tmp_path / "design"
    source.mkdir()
    test.mkdir()
    design.mkdir()

    prompt = DevPromptBuilder(
        source_dir=str(source), test_dir=str(test), design_dir=str(design),
    ).system_prompt
    assert f"- Working directory: {tmp_path}" in prompt
    assert f"- design: {design}" in prompt
    assert f"- src: {source}" in prompt
    assert f"- test: {test}" in prompt


def test_foundation_tools_use_project_root_with_named_directory_aliases(tmp_path):
    source = tmp_path / "src"
    test = tmp_path / "test"
    design = tmp_path / "design"
    source.mkdir()
    test.mkdir()
    design.mkdir()
    (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (test / "test_main.py").write_text("def test_main(): pass\n", encoding="utf-8")

    tools = create_foundation_tools(
        str(source), str(test), str(design), workspace_root=str(tmp_path),
    )
    import asyncio

    listed = asyncio.run(_tool(tools, "list_files")._execute({
        "path": "workspace", "pattern": "**/*.py",
    }))
    assert "src\\main.py" in listed
    assert "test\\test_main.py" in listed

    content = asyncio.run(_tool(tools, "read_file")._execute({
        "path": "src\\main.py",
    }))
    assert content == "print('ok')"
    shell = _tool(tools, "shell")
    cwd, error = shell._resolve_cwd("source")
    assert error is None
    assert cwd == str(source)


def test_foundation_tool_surface_has_seven_stable_tools(tmp_path):
    names = [tool.name for tool in create_foundation_tools(str(tmp_path))]
    assert names == [
        "list_files", "read_file", "search_text", "apply_changes",
        "run_program", "run_task", "shell",
    ]


def test_apply_changes_keeps_apply_patch_as_non_schema_compatibility_alias(tmp_path):
    target = tmp_path / "main.py"
    target.write_text("value = 1\n", encoding="utf-8")
    tools = create_foundation_tools(str(tmp_path))
    registry = ToolRegistry()
    for tool in tools:
        registry.register_tool(tool)

    assert registry.get_tool("apply_patch") is registry.get_tool("apply_changes")
    assert "apply_patch" not in registry.list_tools()
    result = registry.execute_tool_with_params("apply_patch", {
        "patches": [{
            "path": "main.py", "old_text": "value = 1", "new_text": "value = 2",
        }],
    })
    assert result.startswith("Applied changes:")
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert [
        spec["function"]["name"] for spec in registry.get_openai_specs()
    ].count("apply_changes") == 1


def test_list_files_includes_root_files_and_resolves_scopes(tmp_path):
    source = tmp_path / "src"
    test = tmp_path / "test"
    design = tmp_path / "design"
    source.mkdir()
    test.mkdir()
    design.mkdir()
    (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "pkg").mkdir()
    (source / "pkg" / "module.py").write_text("value = 1\n", encoding="utf-8")
    (test / "test_main.py").write_text("def test_main(): pass\n", encoding="utf-8")
    (design / "model.umlproj").write_text("{}\n", encoding="utf-8")

    tool = create_foundation_tools(str(source), str(test), str(design))[0]
    import asyncio

    source_result = asyncio.run(tool._execute({"pattern": "**/*"}))
    assert "main.py" in source_result
    assert "pkg" in source_result
    assert "test_main.py" not in source_result

    test_result = asyncio.run(tool._execute({
        "path": str(test), "pattern": "**/*.py",
    }))
    assert test_result.strip() == "test_main.py"

    workspace_result = asyncio.run(tool._execute({
        "path": "workspace", "pattern": "**/*",
    }))
    assert "main.py" in workspace_result
    assert "test_main.py" in workspace_result
    assert "model.umlproj" in workspace_result


def test_apply_patch_supports_create_and_exact_replace(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("value = 1\n", encoding="utf-8")
    patch = _tool(create_foundation_tools(str(source)), "apply_changes")

    result = patch.run({
        "changes": [
            {"op": "patch", "path": "main.py", "old_text": "value = 1", "new_text": "value = 2"},
            {"op": "create", "path": "new.py", "content": "print('ok')\n"},
        ],
    })

    assert "Applied changes" in result
    assert (source / "main.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (source / "new.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_apply_patch_accumulates_multiple_patches_for_one_file(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    target = source / "main.py"
    target.write_text("value = 1\nname = 'old'\n", encoding="utf-8")
    patch = _tool(create_foundation_tools(str(source)), "apply_changes")

    result = patch.run({
        "changes": [
            {"op": "patch", "path": "main.py", "old_text": "value = 1", "new_text": "value = 2"},
            {"op": "patch", "path": "main.py", "old_text": "name = 'old'", "new_text": "name = 'new'"},
        ],
    })

    assert "Applied changes" in result
    assert target.read_text(encoding="utf-8") == "value = 2\nname = 'new'\n"


def test_apply_changes_supports_generic_file_lifecycle_operations(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    target = source / "existing.txt"
    target.write_text("before\n", encoding="utf-8")
    binary = source / "cache.pyc"
    binary.write_bytes(b"\xcb\x00\x00\x00\x00")
    empty_dir = source / "empty"
    empty_dir.mkdir()
    changes = _tool(create_foundation_tools(str(source)), "apply_changes")

    result = changes.run({
        "changes": [
            {"op": "mkdir", "path": "generated"},
            {"op": "create", "path": "generated/a.txt", "content": "created\n"},
            {"op": "copy", "from": "generated/a.txt", "to": "generated/b.txt"},
            {"op": "move", "from": "generated/b.txt", "to": "moved.txt"},
            {"op": "replace", "path": "moved.txt", "content": "moved\n"},
            {"op": "delete", "path": "generated/a.txt"},
            {"op": "delete", "path": "cache.pyc"},
            {"op": "delete", "path": "empty"},
        ],
    })

    assert result == "Applied changes: mkdir, create, copy, move, replace, delete, delete, delete"
    assert not (source / "generated" / "a.txt").exists()
    assert not (source / "generated" / "b.txt").exists()
    assert not binary.exists()
    assert not empty_dir.exists()
    assert (source / "moved.txt").read_text(encoding="utf-8") == "moved\n"
    assert target.read_text(encoding="utf-8") == "before\n"


def test_apply_changes_validates_before_commit(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    target = source / "main.py"
    target.write_text("value = 1\n", encoding="utf-8")
    changes = _tool(create_foundation_tools(str(source)), "apply_changes")

    result = changes.run({
        "changes": [
            {"op": "patch", "path": "main.py", "old_text": "value = 1", "new_text": "value = 2"},
            {"op": "unknown", "path": "main.py"},
        ],
    })

    assert "unsupported operation" in result
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_change_set_commit_ignores_deleted_stale_uml_projects(tmp_path):
    project = tmp_path / "current.umlproj"
    stale = tmp_path / "stale.umlproj"
    project.write_text('{"name": "current", "diagrams": []}\n', encoding="utf-8")
    stale.write_text('{"name": "stale", "diagrams": []}\n', encoding="utf-8")
    change_set = ChangeSet(project_file=str(project))
    change_set.begin()
    changes = _tool(create_foundation_tools(str(tmp_path), change_set=change_set), "apply_changes")

    result = changes.run({"changes": [{
        "op": "patch", "path": "current.umlproj",
        "old_text": '"name": "current"', "new_text": '"name": "changed"',
    }]})
    assert result.startswith("Applied changes:")
    result = changes.run({"changes": [{"op": "delete", "path": "stale.umlproj"}]})

    assert result.startswith("Applied changes:")
    assert not stale.exists()
    change_set.commit()
    assert project.exists()


def test_validate_task_validates_uml_project_directly(tmp_path):
    source = tmp_path / "src"
    test = tmp_path / "test"
    design = tmp_path / "design"
    source.mkdir()
    test.mkdir()
    design.mkdir()
    project = design / "model.umlproj"
    project.write_text(
        '{"diagrams": [{"diagram_type": "component"}]}\n',
        encoding="utf-8",
    )
    tool = _tool(
        create_foundation_tools(
            str(source), str(test), str(design), workspace_root=str(tmp_path),
        ),
        "run_task",
    )

    import asyncio

    result = asyncio.run(tool._execute({
        "task": "validate", "target": "model.umlproj", "cwd": "design",
    }))

    assert result == f"Validated UML project: {project} (diagrams=1)"


def test_power_shell_adapter_owns_shell_syntax_validation():
    executor = NativePowerShellExecutor()
    assert executor.validate_shell_command("Get-ChildItem -Force") is None
    assert "nested shell" in executor.validate_shell_command("bash -c ls")
