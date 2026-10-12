"""Exercise replay through the real loop-agent factory, without network calls."""
import asyncio

from extensions.trace.replay import replay_agent_session


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
