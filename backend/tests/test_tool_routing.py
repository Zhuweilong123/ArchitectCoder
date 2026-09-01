from app.agent_base.core.hooks import AgentRuntime, reset_runtime, set_runtime
from app.services.agent_chat_ws import (
    _requires_todo_plan, _select_tools_for_message, _todo_progress_state,
)


class _Registry:
    def __init__(self, names):
        self._names = names

    def list_tools(self):
        return list(self._names)


TOOLS = [
    "read_file", "glob", "write_file", "edit_file", "bash", "todo_write",
    "skill", "submit_uml_review", "get_project_map", "find_nodes",
    "compare_design_code", "spawn_subagent",
]


def _route(message):
    return _select_tools_for_message(_Registry(TOOLS), message)


def test_clear_greeting_is_tool_free_but_ambiguous_request_fails_open():
    assert _route("hello") == []
    assert _route("please look at this") is None


def test_code_route_keeps_every_core_execution_tool():
    route = _route("fix the failing pytest case in the source code")
    assert {"read_file", "glob", "write_file", "edit_file", "bash"} <= set(route)
    assert "skill" not in route


def test_design_route_adds_helpers_without_hiding_write_capability():
    route = _route("update the UML class diagram")
    assert {"read_file", "glob", "write_file", "edit_file", "bash"} <= set(route)
    assert {"skill", "submit_uml_review", "get_project_map", "find_nodes"} <= set(route)


def test_cross_domain_request_keeps_core_and_design_helpers():
    route = _route("sync the UML design with the source code")
    assert {"read_file", "glob", "write_file", "edit_file", "bash"} <= set(route)
    assert {"skill", "submit_uml_review", "get_project_map", "find_nodes"} <= set(route)
    assert {"todo_write", "spawn_subagent"} <= set(route)


def test_planning_route_requires_design_code_and_coordination_signals():
    assert "spawn_subagent" not in _route("fix the source code test")
    route = _route("migrate the UML design and source code, then verify the tests")
    assert {"todo_write", "spawn_subagent"} <= set(route)


def test_todo_progress_state_uses_runtime_as_the_authoritative_snapshot():
    runtime_token = set_runtime(AgentRuntime(
        todos=[{
            "content": "run focused test",
            "status": "in_progress",
            "kind": "verification",
            "acceptance": "test passes",
            "untrusted": "not exposed",
        }],
        requires_acceptance_todos=True,
        strategy_subagent_used=True,
    ))
    try:
        state = _todo_progress_state()
    finally:
        reset_runtime(runtime_token)

    assert state == {
        "todos": [{
            "content": "run focused test",
            "status": "in_progress",
            "kind": "verification",
            "acceptance": "test passes",
        }],
        "planning_mode": True,
        "strategy_advised": True,
    }


def test_explicit_read_only_intent_is_the_hard_safety_boundary():
    route = _route("read-only UML design review")
    assert {"read_file", "glob", "skill", "submit_uml_review"} <= set(route)
    assert "todo_write" in route
    assert not {"write_file", "edit_file", "bash"} & set(route)


def test_todo_plan_is_default_for_non_chat_tasks_only():
    assert _requires_todo_plan("hello") is False
    assert _requires_todo_plan("rename every component") is True
