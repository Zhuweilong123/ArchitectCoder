"""Exercise replay through the real loop-agent factory, without network calls."""
import asyncio
import json

from extensions.trace.replay import HybridToolRegistry, MockToolRegistry, ReplayLLM, replay_agent_session


def recorded_call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments),
    }}


def test_mock_replay_uses_current_agent_constructor(monkeypatch):
    events = [
        {"event_type": "user_message", "message": "hello"},
        {"event_type": "llm_response", "span_path": "DevAgent", "content": "hello back", "tool_calls": None},
        {"event_type": "done", "answer": "hello back"},
    ]
    monkeypatch.setattr("extensions.trace.trace_reader.read_trace", lambda _: {"events": events})
    result = asyncio.run(replay_agent_session("contract-replay", mode="mock"))
    assert result["all_matched"] is True
    assert result["llm_calls"] == 1
    assert result["turns"][0]["final_answer"] == "hello back"


def test_mock_replay_executes_recorded_tools_through_native_boundary(monkeypatch):
    events = [
        {"event_type": "user_message", "message": "read file"},
        {"event_type": "llm_response", "span_path": "DevAgent", "content": "", "tool_calls": [
            recorded_call("call-1", "read_file", {"path": "main.py"}),
        ]},
        {"event_type": "tool_result", "tool_name": "read_file", "observation": "value = 1"},
        {"event_type": "llm_response", "span_path": "DevAgent", "content": "done", "tool_calls": None},
        {"event_type": "done", "answer": "done"},
    ]
    monkeypatch.setattr("extensions.trace.trace_reader.read_trace", lambda _: {"events": events})
    result = asyncio.run(replay_agent_session("contract-tools", mode="mock"))
    assert result["all_matched"] is True
    assert result["tool_calls"] == 1
    assert result["turns"][0]["steps"][0]["tool_calls"][0]["observation"] == "value = 1"


def test_hybrid_replay_preserves_real_and_mock_tool_selection(tmp_path, monkeypatch):
    from app.agent_base.adapters.host_services import ApplicationHostServices
    from app.agent_base.core.hooks import AgentRuntime, reset_runtime, set_runtime
    from app.agent_base.tools.registry import ToolRegistry
    real_calls = []
    from app.agent_base.tools.my_tools.foundation_runtime import ReadFileTool
    from app.core.capabilities import CapabilityPolicy
    (tmp_path / "main.py").write_text("current text", encoding="utf-8")
    real = ToolRegistry(policy=CapabilityPolicy(workspace_roots=[str(tmp_path)]))
    real.register_tool(ReadFileTool(source_dir=str(tmp_path), workspace_root=str(tmp_path)))
    execute_real = real.aexecute_tool_with_params
    async def record_real(name, parameters):
        real_calls.append((name, parameters))
        return await execute_real(name, parameters)
    monkeypatch.setattr(real, "aexecute_tool_with_params", record_real)
    events = [
        {"event_type": "llm_response", "span_path": "DevAgent", "content": "", "tool_calls": [
            recorded_call("call-1", "read_file", {"path": "main.py"}),
            recorded_call("call-2", "apply_changes", {"changes": [{"op": "write", "path": "main.py"}]}),
        ]},
        {"event_type": "tool_result", "tool_name": "read_file", "observation": "old text"},
        {"event_type": "tool_result", "tool_name": "apply_changes", "observation": "recorded edit"},
        {"event_type": "llm_response", "span_path": "DevAgent", "content": "done", "tool_calls": None},
    ]
    registry = HybridToolRegistry(events, real, {"read_file"})
    agent = ApplicationHostServices().create_loop_agent(name="DevAgent", llm=ReplayLLM(events), tool_registry=registry)
    token = set_runtime(AgentRuntime())
    try:
        async def run():
            return [step async for step in agent.arun_stream("check and edit")]
        steps = asyncio.run(run())
    finally:
        reset_runtime(token)
    assert steps[-1].final_answer == "done"
    assert real_calls == [("read_file", {"path": "main.py"})]
    details = steps[0].tool_calls_detail
    assert details[0]["status"] == "success"
    assert "current text" in details[0]["observation"]
    assert details[1]["observation"] == "recorded edit"
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "current text"
    assert registry.cursor == 2


def test_text_registry_adapter_blocks_disallowed_tools_without_consuming_records():
    from app.agent_base.adapters.tool_registry import TextToolRegistryAdapter
    registry = MockToolRegistry([{"event_type": "tool_result", "tool_name": "read_file", "observation": "text"}])
    adapter = TextToolRegistryAdapter(registry)
    adapter.set_allowed_tools([])
    result = asyncio.run(adapter.aexecute_tool_result_with_params("read_file", {"path": "main.py"}))
    assert result.status == "blocked"
    assert result.error_code == "POLICY_BLOCKED"
    assert registry.cursor == 0
