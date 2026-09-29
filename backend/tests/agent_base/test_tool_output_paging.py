"""The model can recover every character of a long tool result from its trace."""

from __future__ import annotations

import asyncio
import json
import re

from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.tools.base import Tool
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.tool_output import MAX_FED_CHARS, ReadToolOutputTool
from app.trace.tracing import TraceSession
from extensions.trace.chat_trace import ChatTraceLogger


class _TextTool(Tool):
    def __init__(self, value: str):
        super().__init__(name="text_tool", description="Return a fixed text")
        self.value = value

    def get_parameters(self):
        return []

    def run(self, parameters):
        return self.value


def _call(name: str, arguments: dict, call_id: str) -> dict:
    return {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def test_long_tool_output_is_paged_from_current_trace(tmp_path):
    original = "A" * 2800 + "中🙂" + "B" * 5200
    registry = ToolRegistry()
    registry.register_tool(_TextTool(original))
    registry.register_tool(ReadToolOutputTool())
    executor = ToolRoundExecutor(registry, agent_name="Test")
    trace = ChatTraceLogger("paging_test", log_dir=str(tmp_path))

    with TraceSession(session_id="paging_test", sink=trace):
        result = asyncio.run(executor.execute(
            [_call("text_tool", {}, "call_text")], step=1,
        ))
        first = result.tool_results[0]["content"]
        assert len(first) <= MAX_FED_CHARS
        assert result.details[0]["fed_truncated"] is True
        match = re.search(r"output_id=([0-9a-f]{16}); next_offset=(\d+)", first)
        assert match is not None
        output_id, offset_text = match.groups()
        offset = int(offset_text)
        assert first[:offset] == original[:offset]
        assert trace.read_tool_output(output_id) == original

        chunks = [first[:offset]]
        while offset < len(original):
            page = asyncio.run(executor.execute(
                [_call("read_tool_output", {"output_id": output_id,
                                             "offset": offset}, f"call_page_{offset}")],
                step=2,
            )).tool_results[0]["content"]
            assert len(page) <= MAX_FED_CHARS
            header = re.match(
                rf"\[tool output {output_id}; chars {offset}:(\d+) of {len(original)}\]\n",
                page,
            )
            assert header is not None
            end = int(header.group(1))
            assert page.endswith(
                f"\n[next_offset={end if end < len(original) else 'none'}]"
            )
            chunks.append(page[header.end():header.end() + end - offset])
            offset = end

        assert "".join(chunks) == original

    events = [json.loads(line) for line in (tmp_path / "trace_paging_test.jsonl")
              .read_text(encoding="utf-8").splitlines()]
    recorded = next(event for event in events
                    if event.get("event_type") == "tool_result"
                    and event.get("tool_name") == "text_tool")
    assert recorded["observation"] == original
    assert recorded["fed_truncated"] is True
    assert recorded["fed_length"] == len(first)

    with TraceSession(session_id="other_session", sink=ChatTraceLogger(
            "other_session", log_dir=str(tmp_path))):
        assert ReadToolOutputTool().run({"output_id": output_id, "offset": 0}).startswith(
            "Error: output_id was not found"
        )


def test_short_tool_output_is_unchanged(tmp_path):
    registry = ToolRegistry()
    registry.register_tool(_TextTool("short result"))
    executor = ToolRoundExecutor(registry, agent_name="Test")
    with TraceSession(session_id="short_test", sink=ChatTraceLogger(
            "short_test", log_dir=str(tmp_path))):
        result = asyncio.run(executor.execute(
            [_call("text_tool", {}, "call_short")], step=1,
        ))
    assert result.tool_results[0]["content"] == "short result"
    assert result.details[0]["fed_truncated"] is False
