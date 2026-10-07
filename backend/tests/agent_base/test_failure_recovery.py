"""Recovery reminders describe only currently unresolved, matching targets."""

import asyncio
import json

import pytest

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.agents.react_runtime.failure_recovery import FailureRecoveryController
from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
from app.agent_base.tools.registry import ToolRegistry
from app.core.capabilities import CapabilityPolicy


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / "engine"
    for directory in (source, source / "other"):
        directory.mkdir()
        (directory / "main.py").write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry(policy=CapabilityPolicy(workspace_roots=[str(tmp_path)]))
    for tool in create_foundation_tools(source_dir=str(source), workspace_root=str(tmp_path)):
        registry.register_tool(tool)
    return tmp_path, registry


def detail(name="read_file", path="source/main.py", *, status="error", code="FILE_READ_ERROR", observation="failure", arguments=None):
    if arguments is None:
        arguments = {"changes": [{"op": "patch", "path": path}]} if name == "apply_changes" else {"path": path}
    return {"name": name, "arguments": arguments, "status": status,
            "error_code": code if status != "success" else "", "observation": observation}


def reminders(messages):
    return [message for message in messages if message.get("role") == "system"
            and message.get("content", "").startswith(FailureRecoveryController.HEADER)]


@pytest.mark.parametrize("name,code", [("read_file", "FILE_READ_ERROR"), ("apply_changes", "PATCH_TEXT_NOT_FOUND")])
def test_repeated_failures_keep_one_message_and_unrelated_success_does_not_append(workspace, name, code):
    _, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = [{"role": "user", "content": "fix this"}]
    for _ in range(27):
        controller.update(messages, [detail(name, code=code)])
    assert len(reminders(messages)) == 1
    content = reminders(messages)[0]["content"]
    assert "27 attempts" in content
    assert content.count("Repeated edit failure guard") == (1 if name == "apply_changes" else 0)
    for _ in range(27):
        controller.update(messages, [detail(path="source/other/main.py", status="success")])
    assert reminders(messages)[0]["content"] == content
    assert len(messages) == 2


def test_exact_target_success_removes_reminder_and_resets_attempts(workspace):
    root, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    controller.update(messages, [detail(), detail()])
    controller.update(messages, [detail(path=str(root / "engine" / "main.py"), status="success")])
    assert not reminders(messages)
    controller.update(messages, [detail()])
    assert "1 attempts" in reminders(messages)[0]["content"]


def test_other_unresolved_target_is_retained_when_one_recovers(workspace):
    _, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    controller.update(messages, [detail(), detail(path="source/other/main.py")])
    controller.update(messages, [detail(status="success")])
    assert len(reminders(messages)) == 1
    assert "other" in reminders(messages)[0]["content"]


def test_search_without_hits_does_not_release_edit_guard_but_matching_hits_do(workspace):
    root, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    failure = detail("apply_changes", code="PATCH_TEXT_NOT_FOUND")
    controller.update(messages, [failure, failure])
    controller.update(messages, [detail("search_text", status="success", observation="No matching lines")])
    assert "Repeated edit failure guard" in reminders(messages)[0]["content"]
    controller.update(messages, [detail("search_text", path="workspace", status="success",
                                        observation=f"{root / 'engine' / 'main.py'}:1:1: value = 1")])
    assert not reminders(messages)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_corrected_missing_path_requires_one_known_candidate(workspace, ambiguous):
    root, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    target = str(root / "engine" / "main.py")
    candidates = target + (", " + str(root / "engine" / "other" / "main.py") if ambiguous else "")
    controller.update(messages, [detail(path="source/wrong/main.py", code="PATH_NOT_FOUND",
                                        observation=f"Error: file not found\npossible_paths: {candidates}")])
    controller.update(messages, [detail(path=target, status="success")])
    assert bool(reminders(messages)) is ambiguous


def test_only_failed_batch_target_receives_edit_constraint(workspace):
    root, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    args = {"changes": [{"op": "patch", "path": "source/main.py"},
                        {"op": "patch", "path": "source/other/main.py"}]}
    failed = detail("apply_changes", code="PATCH_TEXT_NOT_FOUND", arguments=args,
                    observation=json.dumps({"change_index": 1}))
    controller.update(messages, [failed])
    controller.update(messages, [detail(status="success")])
    assert reminders(messages)
    assert controller.scopes.path("apply_changes", str(root / "engine" / "other" / "main.py")) in reminders(messages)[0]["content"]
    controller.update(messages, [detail(path="source/other/main.py", status="success")])
    assert not reminders(messages)


def test_unrelated_verification_success_does_not_erase_failure(workspace):
    root, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    args = {"task": "test", "target": "source/main.py", "cwd": "workspace"}
    controller.update(messages, [detail("run_task", code="PROCESS_EXIT_ERROR", arguments=args)])
    controller.update(messages, [detail("run_task", status="success", arguments={**args, "target": "source/other/main.py"})])
    assert reminders(messages)
    controller.update(messages, [detail("run_task", status="success", arguments={**args, "target": str(root / "engine" / "main.py")})])
    assert not reminders(messages)


def test_command_recovery_respects_cwd_relative_target(workspace):
    root, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    controller.update(messages, [detail("run_task", code="PROCESS_EXIT_ERROR", arguments={
        "task": "test", "cwd": "source/other", "target": "main.py",
    })])
    controller.update(messages, [detail("run_task", status="success", arguments={
        "task": "test", "cwd": str(root / "engine" / "other"),
        "target": str(root / "engine" / "other" / "main.py"),
    })])
    assert not reminders(messages)


def test_blocked_retry_of_batch_does_not_add_constraints_to_unaffected_files(workspace):
    root, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    controller.update(messages, [detail("apply_changes", code="PATCH_TEXT_NOT_FOUND")])
    controller.update(messages, [detail("apply_changes", code="POLICY_BLOCKED", status="blocked", arguments={
        "changes": [{"op": "patch", "path": "source/main.py"},
                    {"op": "patch", "path": "source/other/main.py"}],
    })])
    content = reminders(messages)[0]["content"]
    assert content.count("apply_changes:") == 1
    assert controller.scopes.path("apply_changes", str(root / "engine" / "other" / "main.py")) not in content


def test_multi_path_edit_keeps_unrefreshed_target_constraint(workspace):
    _, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    controller.update(messages, [detail("apply_changes", code="EXPECTED_SHA_MISMATCH", arguments={
        "changes": [{"op": "move", "from": "source/main.py", "to": "source/other/main.py"}],
    })])
    controller.update(messages, [detail(status="success")])
    assert reminders(messages)
    assert "other" in reminders(messages)[0]["content"]
    controller.update(messages, [detail(path="source/other/main.py", status="success")])
    assert not reminders(messages)


def test_sync_survives_message_copy_and_compaction_without_duplicates(workspace):
    _, registry = workspace
    controller = FailureRecoveryController(registry)
    messages = []
    controller.update(messages, [detail()])
    messages = [dict(message) for message in messages] * 2
    controller.sync(messages)
    assert len(reminders(messages)) == 1
    messages.clear()
    controller.sync(messages)
    assert len(reminders(messages)) == 1
    controller.update(messages, [detail(status="success")])
    assert not messages


def test_tool_guard_uses_exact_canonical_target_and_actual_failed_batch_index(workspace):
    root, registry = workspace
    executor = ToolRoundExecutor(registry, agent_name="test")
    from app.agent_base.tools.result import ToolResult
    args = {"changes": [{"op": "patch", "path": "source/main.py"},
                        {"op": "patch", "path": "source/other/main.py"}]}
    executor._update_edit_recovery_state("apply_changes", args,
        ToolResult.error({"change_index": 1}, "PATCH_TEXT_NOT_FOUND"))
    expected = executor._normalise_path(str(root / "engine" / "other" / "main.py"))
    assert executor._edit_recovery_paths == {expected}
    executor._update_edit_recovery_state("read_file", {"path": "source/main.py"}, ToolResult.success("value = 1"))
    assert executor._edit_recovery_paths == {expected}
    executor._update_edit_recovery_state("search_text", {"path": "source/other/main.py"}, ToolResult.success("No matches"))
    assert executor._edit_recovery_paths == {expected}
    executor._update_edit_recovery_state("read_file", {"path": str(root / "engine" / "other" / "main.py")}, ToolResult.success("value = 1"))
    assert not executor._edit_recovery_paths


def test_fc_loop_removes_read_failure_reminder_before_next_model_request(workspace):
    root, registry = workspace
    (root / "engine" / "target.py").write_text("target", encoding="utf-8")
    class LLM:
        def __init__(self):
            self.requests = []
        async def ainvoke_with_tools(self, messages, tools, **kwargs):
            self.requests.append([dict(message) for message in messages])
            count = len(self.requests)
            if count == 1:
                path = "source/wrong/target.py"
            elif count == 2:
                path = str(root / "engine" / "other" / "main.py")
            elif count == 3:
                path = str(root / "engine" / "target.py")
            else:
                return {"content": "done", "tool_calls": None}
            return {"content": "", "tool_calls": [{
                "id": str(count), "type": "function", "function": {
                    "name": "read_file", "arguments": json.dumps({"path": path}),
                },
            }]}
    llm = LLM()
    agent = ReActAgent("Test", llm, registry)
    async def run():
        return [event async for event in agent.arun_stream("read the file")]
    events = asyncio.run(run())
    assert events[-1].final_answer == "done"
    assert len(reminders(llm.requests[1])) == 1
    assert not any("Repeated edit failure guard" in message.get("content", "") for message in llm.requests[1])
    assert len(reminders(llm.requests[2])) == 1
    assert not reminders(llm.requests[3])


def test_fc_loop_refreshes_failed_edit_and_retries_with_canonical_path(workspace):
    root, registry = workspace
    class LLM:
        def __init__(self):
            self.requests = []
        async def ainvoke_with_tools(self, messages, tools, **kwargs):
            self.requests.append([dict(message) for message in messages])
            step = len(self.requests)
            if step == 1:
                name, arguments = "apply_changes", {"changes": [{
                    "op": "patch", "path": "source/main.py", "old_text": "missing", "new_text": "value = 2",
                }]}
            elif step == 2:
                name, arguments = "read_file", {"path": str(root / "engine" / "main.py")}
            elif step == 3:
                name, arguments = "apply_changes", {"changes": [{
                    "op": "patch", "path": "workspace/engine/main.py", "old_text": "value = 1", "new_text": "value = 2",
                }]}
            else:
                return {"content": "done", "tool_calls": None}
            return {"content": "", "tool_calls": [{
                "id": str(step), "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments),
                },
            }]}
    llm = LLM()
    agent = ReActAgent("Test", llm, registry)
    async def run():
        return [event async for event in agent.arun_stream("fix the file")]
    events = asyncio.run(run())
    assert events[-1].final_answer == "done"
    assert reminders(llm.requests[1])
    assert not reminders(llm.requests[2])
    assert not reminders(llm.requests[3])
    assert (root / "engine" / "main.py").read_text(encoding="utf-8") == "value = 2\n"
