import asyncio
import json
from types import SimpleNamespace

from app.agent_base.core.message import Message
from app.services.session_compression import SessionContextCompressor


class _SemanticLLM:
    def __init__(self):
        self.calls = []

    async def ainvoke_with_metadata(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        payload = json.loads(messages[-1]["content"])
        source = payload["turns"][0]["source"]
        return {
            "content": json.dumps({
                "facts": [{"content": "保留的事实", "sources": [source]}],
                "decisions": [],
                "constraints": [],
                "preferences": [],
                "completed": [],
                "unresolved": [],
            }, ensure_ascii=False),
        }


def _history(turns: int):
    messages = []
    for index in range(turns):
        messages.extend([
            Message(f"user turn {index} " + "x" * 900, "user"),
            Message(f"assistant turn {index} " + "y" * 900, "assistant"),
        ])
    return messages


def test_session_compression_keeps_the_latest_three_complete_turns():
    llm = _SemanticLLM()
    agent = SimpleNamespace(_history=_history(4), _history_summary="")
    compressor = SessionContextCompressor(
        llm,
        hard_limit_tokens=1000,
        trigger_ratio=0.7,
    )

    result = asyncio.run(compressor.maybe_compress(agent, session_id="session-1"))

    assert result.compressed is True
    assert result.dropped_messages == 2
    assert [message.content.split(" ")[0:3] for message in agent._history] == [
        ["user", "turn", "1"], ["assistant", "turn", "1"],
        ["user", "turn", "2"], ["assistant", "turn", "2"],
        ["user", "turn", "3"], ["assistant", "turn", "3"],
    ]
    assert "保留的事实" in agent._history_summary
    assert "source:" in agent._history_summary
    assert llm.calls[0][1]["model"] == "deepseek-flash"
    assert llm.calls[0][1]["trace_context"]["scope"] == "session_compression"
