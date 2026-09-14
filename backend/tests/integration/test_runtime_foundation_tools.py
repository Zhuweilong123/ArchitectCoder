"""Regression coverage for the host Runtime and stable foundation tools."""

from __future__ import annotations

import os
import platform
import json

from app.agent_base.assembly import DevPromptBuilder
from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.result import ToolResult
from app.services.change_set import ChangeSet
from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
from app.runtime import (
    ExecutionEvidence,
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

    assert f"Host OS: {platform.system().lower()}" in prompt
    assert f"Execution OS: {context.execution_os}" in prompt
    assert f"Shell: {context.shell}" in prompt
    assert f"Execution mode: {context.execution_mode}" in prompt
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
    assert os.path.join("src", "main.py") in listed
    assert os.path.join("test", "test_main.py") in listed

    content = asyncio.run(_tool(tools, "read_file")._execute({
        "path": "src\\main.py",
    }))
    assert content == "print('ok')"
    shell = _tool(tools, "shell")
    cwd, error = shell._resolve_cwd("source")
    assert error is None
    assert cwd == str(source)


def test_read_file_reports_bounded_path_candidates_after_miss(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    package = source / "package"
    package.mkdir()
    target = package / "database.py"
    target.write_text("value = 1\n", encoding="utf-8")
    read_file = _tool(create_foundation_tools(str(source)), "read_file")

    import asyncio
    result = asyncio.run(read_file._execute({"path": "database.py"}))

    assert "Error: file not found: database.py" in result
    assert "possible_paths: source/package/database.py" in result
    assert "recovery_action:" in result
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_foundation_tool_surface_has_seven_stable_tools(tmp_path):
    names = [tool.name for tool in create_foundation_tools(str(tmp_path))]
    assert names == [
        "list_files", "read_file", "search_text", "apply_changes",
        "run_program", "run_task", "shell",
    ]


def test_execution_tools_advertise_disjoint_routing_contract(tmp_path):
    tools = create_foundation_tools(str(tmp_path))
    descriptions = {tool.name: tool.description for tool in tools}

    assert "project task" in descriptions["run_task"]
    assert "literal argv" in descriptions["run_program"]
    assert "shell" in descriptions["run_program"]
    assert "last resort" in descriptions["shell"]
    assert "run_task" in descriptions["shell"]
    assert "run_program" in descriptions["shell"]


def test_apply_changes_exposes_only_the_stable_contract(tmp_path):
    target = tmp_path / "main.py"
    target.write_text("value = 1\n", encoding="utf-8")
    tools = create_foundation_tools(str(tmp_path))
    registry = ToolRegistry()
    for tool in tools:
        registry.register_tool(tool)

    assert registry.get_tool("apply_changes") is not None
    assert registry.get_tool("apply_patch") is None
    result = registry.execute_tool_with_params("apply_changes", {
        "changes": [{
            "op": "patch", "path": "main.py", "old_text": "value = 1", "new_text": "value = 2",
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


def test_apply_changes_supports_create_and_exact_replace(tmp_path):
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


def test_apply_changes_normalizes_patch_line_endings_and_preserves_target_style(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    target = source / "main.py"
    target.write_bytes(b"value = 1\r\nname = 'old'\r\n")
    patch = _tool(create_foundation_tools(str(source)), "apply_changes")

    result = patch.run_result({
        "changes": [{
            "op": "patch",
            "path": "main.py",
            "old_text": "value = 1\nname = 'old'\n",
            "new_text": "value = 2\nname = 'new'\n",
        }],
    })

    assert result.status == "success"
    assert target.read_bytes() == b"value = 2\r\nname = 'new'\r\n"


def test_apply_changes_returns_actionable_patch_error(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    target = source / "main.py"
    target.write_text("value = 1\n", encoding="utf-8")
    patch = _tool(create_foundation_tools(str(source)), "apply_changes")

    result = patch.run_result({
        "changes": [{
            "op": "patch",
            "path": "main.py",
            "old_text": "value = 2",
            "new_text": "value = 3",
        }],
    })

    payload = json.loads(result.text)
    assert result.status == "error"
    assert result.error_code == "PATCH_TEXT_NOT_FOUND"
    assert result.retryable
    assert payload["path"] == "main.py"
    assert payload["recovery_action"].startswith("Read or search")
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_tool_round_executor_requires_fresh_read_after_failed_edit():
    executor = ToolRoundExecutor(ToolRegistry(), agent_name="test")
    executor._edit_recovery_paths = {executor._normalise_path("main.py")}
    call = {
        "id": "call-1",
        "function": {
            "name": "apply_changes",
            "arguments": json.dumps({
                "changes": [{
                    "op": "patch",
                    "path": "main.py",
                    "old_text": "old",
                    "new_text": "new",
                }],
            }),
        },
    }

    parsed = executor._parse_calls([call])

    assert parsed[0][3] is not None
    assert "read_file or search_text" in parsed[0][3]


def test_tool_round_executor_clears_edit_recovery_after_fresh_read():
    executor = ToolRoundExecutor(ToolRegistry(), agent_name="test")
    executor._edit_recovery_paths = {executor._normalise_path("main.py")}

    executor._update_edit_recovery_state(
        "read_file",
        {"path": "main.py"},
        ToolResult.success("value = 1"),
    )

    assert executor._edit_recovery_paths == set()


def test_apply_changes_accumulates_multiple_patches_for_one_file(tmp_path):
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


def test_change_set_normalizes_model_edited_project_revision(tmp_path):
    project = tmp_path / "current.umlproj"
    project.write_text(
        json.dumps({"name": "current", "revision": 43, "diagrams": []}, indent=2),
        encoding="utf-8",
    )
    change_set = ChangeSet(project_file=str(project))
    change_set.begin()
    changes = _tool(create_foundation_tools(str(tmp_path), change_set=change_set), "apply_changes")

    result = changes.run({"changes": [{
        "op": "patch",
        "path": "current.umlproj",
        "old_text": '"name": "current",\n  "revision": 43',
        "new_text": '"name": "changed",\n  "revision": 44',
    }]})

    assert result.startswith("Applied changes:")
    manifest = change_set.commit()
    assert manifest
    saved = json.loads(project.read_text(encoding="utf-8"))
    assert saved["name"] == "changed"
    assert saved["revision"] == 44


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


def test_validate_task_accepts_workspace_qualified_target_with_cwd_alias(tmp_path):
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
        "task": "validate", "target": "design/model.umlproj", "cwd": "design",
    }))

    assert result == f"Validated UML project: {project} (diagrams=1)"


def test_run_task_does_not_duplicate_test_alias_as_target(tmp_path):
    source = tmp_path / "src"
    test = tmp_path / "test"
    design = tmp_path / "design"
    source.mkdir()
    test.mkdir()
    design.mkdir()
    tool = _tool(
        create_foundation_tools(
            str(source), str(test), str(design), workspace_root=str(tmp_path),
        ),
        "run_task",
    )

    captured = {}

    async def fake_run(program, args, cwd):
        captured.update(program=program, args=args, cwd=cwd)
        return "ok"

    tool._run_program_cancellable = fake_run
    import asyncio

    result = asyncio.run(tool._execute({
        "task": "test", "target": "test", "cwd": "test",
    }))

    assert result == "ok"
    assert captured["args"] == ["-m", "pytest"]
    assert captured["cwd"] == str(test)


def test_run_task_prefers_project_task_resolver(tmp_path):
    source = tmp_path / "src"
    test = tmp_path / "test"
    design = tmp_path / "design"
    source.mkdir()
    test.mkdir()
    design.mkdir()
    (tmp_path / "package.json").write_text(
        '{"scripts": {"build": "vite build"}}', encoding="utf-8",
    )
    tool = _tool(
        create_foundation_tools(
            str(source), str(test), str(design), workspace_root=str(tmp_path),
        ),
        "run_task",
    )
    captured = {}

    async def fake_run(program, args, cwd):
        captured.update(program=program, args=args, cwd=cwd)
        return "resolved"

    tool._run_program_cancellable = fake_run
    import asyncio

    result = asyncio.run(tool._execute({"task": "build", "cwd": "workspace"}))

    assert result == "resolved"
    assert captured == {
        "program": "npm",
        "args": ["run", "build"],
        "cwd": str(tmp_path),
    }


def test_resolved_task_uses_execution_broker_and_preserves_evidence(tmp_path):
    source = tmp_path / "src"
    test = tmp_path / "test"
    design = tmp_path / "design"
    source.mkdir()
    test.mkdir()
    design.mkdir()
    (tmp_path / "CMakeLists.txt").write_text("", encoding="utf-8")

    class Broker:
        async def execute(self, task, cwd, *, policy=None):
            return ExecutionEvidence(
                task_id=task.task_id,
                status="success",
                toolchain_id=task.toolchain_id,
                command=task.argv,
                cwd=cwd,
                exit_code=0,
                output="built",
                diagnostics={"category": "process_exit"},
            )

    tool = _tool(
        create_foundation_tools(
            str(source), str(test), str(design), workspace_root=str(tmp_path),
            execution_broker=Broker(),
        ),
        "run_task",
    )

    import asyncio
    result = asyncio.run(tool.run_result({"task": "build", "cwd": "workspace"}))

    assert result.status == "success"
    assert result.text == "built"
    assert result.execution_evidence["toolchain_id"] == "cpp-cmake"
    assert result.verification.passed


def test_resolved_task_executes_cmake_prerequisites_and_aggregates_evidence(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("", encoding="utf-8")
    (tmp_path / "CMakePresets.json").write_text(json.dumps({
        "version": 2,
        "configurePresets": [{"name": "default", "displayName": "Debug", "binaryDir": "${sourceDir}/build"}],
        "buildPresets": [{"name": "default", "configurePreset": "default"}],
        "testPresets": [{"name": "default", "configurePreset": "default", "configuration": "Debug"}],
    }), encoding="utf-8")
    commands = []

    class Broker:
        sandbox_name = "workspace"

        async def execute(self, task, cwd, *, policy=None):
            commands.append(task.argv)
            return ExecutionEvidence(
                task_id=task.task_id,
                status="success",
                toolchain_id=task.toolchain_id,
                command=task.argv,
                cwd=cwd,
                exit_code=0,
                output=task.kind.value,
            )

    tool = _tool(
        create_foundation_tools(
            str(tmp_path / "src"), str(tmp_path / "test"), str(tmp_path / "design"),
            workspace_root=str(tmp_path), execution_broker=Broker(),
        ),
        "run_task",
    )
    result = __import__("asyncio").run(tool.run_result({"task": "test"}))

    assert result.status == "success"
    assert commands == [
        ("cmake", "--preset", "default"),
        ("cmake", "--build", "--preset", "default"),
        ("ctest", "--preset", "default"),
    ]
    assert [step["status"] for step in result.execution_evidence["steps"]] == [
        "success", "success", "success",
    ]
    assert result.verification.passed


def test_run_task_dry_run_returns_plan_without_starting_processes(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("", encoding="utf-8")
    (tmp_path / "CMakePresets.json").write_text(json.dumps({
        "version": 2,
        "configurePresets": [{"name": "default", "displayName": "Debug", "binaryDir": "${sourceDir}/build"}],
        "buildPresets": [{"name": "default", "configurePreset": "default"}],
        "testPresets": [{"name": "default", "configurePreset": "default"}],
    }), encoding="utf-8")
    tools = create_foundation_tools(
        str(tmp_path / "src"), str(tmp_path / "test"), str(tmp_path / "design"),
        workspace_root=str(tmp_path),
    )
    tool = _tool(tools, "run_task")

    result = __import__("asyncio").run(tool.run_result({
        "task": "test", "profile": "debug", "dry_run": True,
    }))

    assert result.status == "success"
    assert result.execution_evidence["status"] == "planned"
    assert result.execution_evidence["plan"]["profile"] == "debug"
    assert [step["kind"] for step in result.data["steps"]] == ["configure", "build", "test"]
    assert not (tmp_path / "build").exists()


def test_run_task_schema_exposes_profile_and_dry_run():
    tool = _tool(create_foundation_tools(), "run_task")
    properties = tool.to_openai_schema()["function"]["parameters"]["properties"]

    assert properties["profile"]["type"] == "string"
    assert properties["dry_run"]["type"] == "boolean"


def test_manifest_task_cwd_is_resolved_relative_to_project_root(tmp_path):
    import asyncio

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    marker = tmp_path / ".architectcoder"
    marker.mkdir()
    (marker / "tasks.json").write_text(
        json.dumps({
            "language": "cpp",
            "toolchain": "cpp-clang",
            "tasks": {
                "build": {
                    "argv": ["cmake", "--build", "."],
                    "cwd": "build",
                },
            },
        }),
        encoding="utf-8",
    )
    captured = {}

    class Broker:
        async def execute(self, task, cwd, *, policy=None):
            captured["cwd"] = cwd
            return ExecutionEvidence(
                task_id=task.task_id,
                status="success",
                toolchain_id=task.toolchain_id,
                command=task.argv,
                cwd=cwd,
                exit_code=0,
                output="built",
            )

    tool = _tool(
        create_foundation_tools(
            str(tmp_path / "src"), str(tmp_path / "test"), str(tmp_path / "design"),
            workspace_root=str(tmp_path), execution_broker=Broker(),
        ),
        "run_task",
    )
    result = asyncio.run(tool.run_result({"task": "build"}))

    assert result.status == "success"
    assert captured["cwd"] == str(build_dir)


def test_manifest_task_cwd_cannot_escape_project_root(tmp_path):
    import asyncio

    marker = tmp_path / ".architectcoder"
    marker.mkdir()
    (marker / "tasks.json").write_text(
        json.dumps({
            "tasks": {
                "build": {"argv": ["cmake"], "cwd": "../outside"},
            },
        }),
        encoding="utf-8",
    )
    tool = _tool(
        create_foundation_tools(
            str(tmp_path), workspace_root=str(tmp_path), execution_broker=object(),
        ),
        "run_task",
    )
    result = asyncio.run(tool.run_result({"task": "build"}))

    assert result.status == "blocked"
    assert result.error_code == "TASK_PATH_POLICY"


def test_manifest_custom_task_name_is_not_blocked_by_language_or_task_allowlist(tmp_path):
    import asyncio

    marker = tmp_path / ".architectcoder"
    marker.mkdir()
    (marker / "tasks.json").write_text(
        json.dumps({
            "language": "zig",
            "toolchain": {"id": "zig", "version": "0.13"},
            "tasks": {
                "coverage": {
                    "argv": ["zig", "build", "test"],
                },
            },
        }),
        encoding="utf-8",
    )
    captured = {}

    class Broker:
        sandbox_name = "workspace"

        async def execute(self, task, cwd, *, policy=None):
            captured.update(task=task, cwd=cwd)
            return ExecutionEvidence(
                task_id=task.task_id,
                status="success",
                toolchain_id=task.toolchain_id,
                command=task.argv,
                cwd=cwd,
                exit_code=0,
                output="coverage complete",
            )

    tool = _tool(
        create_foundation_tools(
            str(tmp_path), workspace_root=str(tmp_path), execution_broker=Broker(),
        ),
        "run_task",
    )
    result = asyncio.run(tool.run_result({"task": "coverage"}))

    assert result.status == "success"
    assert captured["task"].toolchain_id == "zig"
    assert captured["task"].argv == ("zig", "build", "test")


def test_power_shell_adapter_owns_shell_syntax_validation():
    executor = NativePowerShellExecutor()
    assert executor.validate_shell_command("Get-ChildItem -Force") is None
    assert executor.validate_shell_command(r"(Get-Content .\main.py).Count") is None
    assert "nested shell" in executor.validate_shell_command("bash -c ls")


def test_power_shell_resolved_tasks_use_worker_path_not_global_allowlist(monkeypatch):
    executor = NativePowerShellExecutor()
    monkeypatch.setattr(
        "app.runtime.command.shutil.which",
        lambda program: "C:\\toolchains\\bin\\" + program + ".exe",
    )

    # CMake is deliberately not an interactive run_program allowlist entry,
    # but a resolver-owned task may use whatever tool the worker advertises.
    assert executor.validate_program("cmake", ["--build", "build"]) is not None
    assert executor.validate_resolved_program("cmake", ["--build", "build"]) is None


def test_missing_resolved_tool_is_reported_as_toolchain_unavailable(monkeypatch):
    executor = NativePowerShellExecutor()
    monkeypatch.setattr("app.runtime.command.shutil.which", lambda program: None)

    error = executor.validate_resolved_program("unknown-compiler", [])

    assert error.startswith("toolchain executable 'unknown-compiler'")
