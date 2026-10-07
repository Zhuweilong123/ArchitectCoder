"""Paths and failure states survive the production tool execution boundary."""

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.result import command_result
from app.core.capabilities import CapabilityPolicy
from app.runtime.workspace_paths import WorkspacePathError, WorkspacePathResolver
from app.trace.tracing import TraceSession
from extensions.trace.chat_trace import ChatTraceLogger


@pytest.fixture
def workspace(tmp_path):
    for directory in ("engine/pkg", "test", "tests", "design/uml"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "tests" / "test_real.py").write_text("actual_marker = 1\n", encoding="utf-8")
    (tmp_path / "test" / "test_real.py").write_text("wrong_marker = 1\n", encoding="utf-8")
    (tmp_path / "engine" / "pkg" / "main.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "design" / "uml" / "model.umlproj").write_text('{"diagrams": [{}]}', encoding="utf-8")
    tools = create_foundation_tools(
        source_dir=str(tmp_path / "engine"), test_dir=str(tmp_path / "test"),
        design_dir=str(tmp_path / "design" / "uml"), workspace_root=str(tmp_path),
    )
    registry = ToolRegistry(policy=CapabilityPolicy(workspace_roots=[str(tmp_path)]))
    for tool in tools:
        registry.register_tool(tool)
    return tmp_path, registry


def execute(registry, name, params):
    return asyncio.run(registry.aexecute_tool_result_with_params(name, params))


def test_project_state_is_excluded_from_workspace_discovery_and_protected(workspace):
    root, registry = workspace
    state = root / ".architectcoder"
    state.mkdir()
    secret = state / "internal.py"
    secret.write_text("actual_marker = 'internal'\n", encoding="utf-8")

    listed = execute(registry, "list_files", {"path": "workspace", "pattern": "**/*.py", "details": False})
    searched = execute(registry, "search_text", {"pattern": "actual_marker"})

    assert listed.status == searched.status == "success"
    assert "test_real.py" in listed.text and "test_real.py" in searched.text
    assert ".architectcoder" not in listed.text
    assert ".architectcoder" not in searched.text
    read = execute(registry, "read_file", {"path": ".architectcoder/internal.py"})
    assert read.error_code == "PROJECT_STATE_PROTECTED"
    changed = execute(registry, "apply_changes", {"changes": [{
        "op": "delete", "path": ".architectcoder/internal.py",
    }]})
    assert changed.status == "error"
    assert secret.read_text(encoding="utf-8") == "actual_marker = 'internal'\n"


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_real_tests_directory_wins_over_alias_in_every_file_tool(workspace, separator):
    root, registry = workspace
    path = separator.join(["tests", "test_real.py"])
    read = execute(registry, "read_file", {"path": path})
    assert read.status == "success"
    assert read.text == "actual_marker = 1"
    listed = execute(registry, "list_files", {"path": "tests", "pattern": "*.py", "details": False})
    assert listed.text == str((root / "tests" / "test_real.py").resolve())
    searched = execute(registry, "search_text", {"path": path, "pattern": "actual_marker"})
    assert searched.status == "success"
    assert str((root / "tests" / "test_real.py").resolve()) in searched.text
    changed = execute(registry, "apply_changes", {"changes": [{
        "op": "patch", "path": path, "old_text": "actual_marker = 1", "new_text": "actual_marker = 2",
    }]})
    assert changed.status == "success"
    assert changed.changes[0].path == str((root / "tests" / "test_real.py").resolve())
    assert (root / "test" / "test_real.py").read_text(encoding="utf-8") == "wrong_marker = 1\n"
    cwd, error = registry.get_tool("run_program")._resolve_cwd("tests")
    assert error is None
    assert cwd == str((root / "tests").resolve())


@pytest.mark.parametrize("scope", ["workspace/engine/pkg", "source/pkg", "engine/pkg"])
def test_inventory_paths_are_identical_and_reusable_in_all_tools(workspace, scope):
    root, registry = workspace
    target = root / "engine" / "pkg" / "main.py"
    listed = execute(registry, "list_files", {"path": scope, "pattern": "*.py", "details": False})
    assert listed.status == "success"
    path = listed.text
    assert path == str(target.resolve())
    assert execute(registry, "read_file", {"path": path}).text == "value = 1"
    search = execute(registry, "search_text", {"path": scope, "pattern": "value"})
    assert f"{path}:1:1: value = 1" in search.text
    changed = execute(registry, "apply_changes", {"changes": [{
        "op": "patch", "path": path, "old_text": "value = 1", "new_text": "value = 2",
    }]})
    assert changed.status == "success"

    async def run(program, args, cwd):
        assert Path(args[-1]) == target.resolve()
        assert Path(args[-1]).read_text(encoding="utf-8") == "value = 2\n"
        return command_result(program, cwd, 0, "ran", argv=[program, *args])

    program = registry.get_tool("run_program")
    program._run_program_cancellable = run
    assert execute(registry, "run_program", {"program": "python", "args": [path], "cwd": "source/pkg"}).status == "success"
    task = registry.get_tool("run_task")
    task._run_program_cancellable = run
    assert execute(registry, "run_task", {"task": "test", "target": path, "cwd": "source/pkg"}).status == "success"


def test_real_design_path_does_not_duplicate_nested_design_root(workspace):
    root, registry = workspace
    target = root / "design" / "uml" / "model.umlproj"
    path = "design/uml/model.umlproj"
    assert execute(registry, "read_file", {"path": path}).status == "success"
    assert execute(registry, "run_task", {"task": "validate", "target": path, "cwd": "design"}).status == "success"
    created = execute(registry, "apply_changes", {"changes": [{
        "op": "create", "path": "design/new.txt", "content": "new",
    }]})
    assert created.status == "success"
    assert (root / "design" / "new.txt").is_file()
    assert not (target.parent / "new.txt").exists()


def test_bare_validation_target_is_relative_to_cwd_even_with_root_namesake(workspace):
    root, registry = workspace
    (root / "model.umlproj").write_text("invalid", encoding="utf-8")
    result = execute(registry, "run_task", {
        "task": "validate", "target": "model.umlproj", "cwd": "workspace/design/uml",
    })
    assert result.status == "success"
    assert str(root / "design" / "uml" / "model.umlproj") in result.text


@pytest.mark.parametrize("name,params", [
    ("read_file", {"path": "workspace/../outside.txt"}),
    ("search_text", {"path": "workspace/../outside.txt", "pattern": "x"}),
    ("list_files", {"path": "workspace/..", "pattern": "*"}),
    ("apply_changes", {"changes": [{"op": "create", "path": "workspace/../outside.txt", "content": "x"}]}),
    ("run_program", {"program": "python", "cwd": "workspace/.."}),
    ("run_task", {"task": "test", "target": "workspace/../outside.txt"}),
])
def test_escape_has_same_structured_error_across_tools(workspace, name, params):
    _, registry = workspace
    tool = registry.get_tool(name)
    async def run():
        result = tool.run_result(params)
        return await result if inspect.isawaitable(result) else result
    result = asyncio.run(run())
    assert result.status == "error"
    assert result.error_code == "PATH_OUTSIDE_WORKSPACE"
    boundary_result = execute(registry, name, params)
    assert boundary_result.status in {"blocked", "error"}
    assert boundary_result.error_code in {"POLICY_BLOCKED", "PATH_OUTSIDE_WORKSPACE"}


@pytest.mark.parametrize("name,params", [
    ("read_file", {"path": "missing.py"}),
    ("search_text", {"path": "missing.py", "pattern": "x"}),
    ("list_files", {"path": "missing", "pattern": "*"}),
    ("run_program", {"program": "python", "cwd": "missing"}),
    ("run_task", {"task": "validate", "target": "missing.umlproj"}),
])
def test_missing_paths_are_errors(workspace, name, params):
    _, registry = workspace
    result = execute(registry, name, params)
    assert result.status == "error"
    assert result.error_code == "PATH_NOT_FOUND"


@pytest.mark.parametrize("params", [{}, {"pattern": "x" * 501}, {"pattern": []}, {"pattern": "x", "path": []}])
def test_search_argument_errors_are_structured(workspace, params):
    _, registry = workspace
    result = execute(registry, "search_text", params)
    assert result.status == "error"
    assert result.error_code == "INVALID_ARGUMENT"


def test_content_with_error_prefix_is_success_and_no_match_is_success(workspace):
    root, registry = workspace
    path = root / "error.txt"
    path.write_text("Error: example text", encoding="utf-8")
    assert execute(registry, "read_file", {"path": str(path)}).status == "success"
    assert execute(registry, "search_text", {"path": str(path), "pattern": "absent"}).status == "success"
    assert execute(registry, "list_files", {"pattern": "*.absent"}).status == "success"


def test_search_io_failure_is_not_reported_as_no_matches(workspace, monkeypatch):
    _, registry = workspace
    def unreadable(*args):
        raise PermissionError("unreadable file")
    monkeypatch.setattr(registry.get_tool("search_text"), "_find_in_file", unreadable)
    result = execute(registry, "search_text", {"path": "tests/test_real.py", "pattern": "x"})
    assert result.status == "error"
    assert result.error_code == "SEARCH_IO_ERROR"


@pytest.mark.parametrize("name,params", [
    ("read_file", {"path": "a.py"}),
    ("search_text", {"pattern": "x"}),
    ("list_files", {"pattern": "*"}),
    ("run_program", {"program": "python"}),
    ("run_task", {"task": "test"}),
])
def test_workspace_not_configured_is_not_success(name, params):
    registry = ToolRegistry()
    for tool in create_foundation_tools():
        registry.register_tool(tool)
    result = execute(registry, name, params)
    assert result.status == "error"
    assert result.error_code == "WORKSPACE_NOT_CONFIGURED"


def test_qualified_pytest_target_preserves_node_selector(workspace):
    root, registry = workspace
    tool = registry.get_tool("run_task")
    captured = {}
    async def run(program, args, cwd):
        captured["args"] = args
        return command_result(program, cwd, 0, "passed", argv=[program, *args])
    tool._run_program_cancellable = run
    result = execute(registry, "run_task", {"task": "test", "target": "tests/test_real.py::test_one"})
    assert result.status == "success"
    assert captured["args"][-1] == str((root / "tests" / "test_real.py").resolve()) + "::test_one"


def test_chinese_search_error_reaches_trace_and_evidence(workspace, monkeypatch):
    root, registry = workspace
    trace = ChatTraceLogger("search_error_contract", log_dir=str(root / "trace"))
    executor = ToolRoundExecutor(registry, agent_name="Test")
    events = []
    monkeypatch.setattr("app.agent_base.agents.react_runtime.tool_round_executor.emit_trace", lambda event, **values: events.append((event, values)))
    with TraceSession(session_id="search_error_contract", sink=trace):
        result = asyncio.run(executor.execute([{
            "id": "search", "type": "function", "function": {
                "name": "search_text", "arguments": json.dumps({"path": "source/missing", "pattern": "x"}),
            },
        }], step=1))
    detail = result.details[0]
    assert "路径无效" in detail["observation"]
    assert detail["status"] == detail["evidence"]["status"] == "error"
    assert detail["error_code"] == "PATH_NOT_FOUND"
    event = next(values for event, values in events if event == "tool_result")
    assert event["error"] == "PATH_NOT_FOUND"
    assert event["evidence"]["status"] == "error"


def test_unconfigured_alias_and_ambiguous_bare_paths_are_explicit(tmp_path):
    resolver = WorkspacePathResolver(workspace_root=str(tmp_path))
    with pytest.raises(WorkspacePathError) as missing:
        resolver.resolve("source/file.py")
    assert missing.value.code == "WORKSPACE_ALIAS_NOT_CONFIGURED"
    first, second = tmp_path / "one", tmp_path / "two"
    for root in (first, second):
        root.mkdir()
        (root / "same.py").write_text("x", encoding="utf-8")
    resolver = WorkspacePathResolver(source_dir=str(first), test_dir=str(second))
    with pytest.raises(WorkspacePathError) as ambiguous:
        resolver.resolve("same.py")
    assert ambiguous.value.code == "PATH_AMBIGUOUS"
    assert resolver.resolve(str(first / "same.py")) == (first / "same.py").resolve()


def test_symlink_cannot_expand_workspace_boundary(tmp_path):
    root, outside = tmp_path / "workspace", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation is unavailable")
    with pytest.raises(WorkspacePathError) as error:
        WorkspacePathResolver(workspace_root=str(root)).resolve("linked/new.txt")
    assert error.value.code == "PATH_OUTSIDE_WORKSPACE"
