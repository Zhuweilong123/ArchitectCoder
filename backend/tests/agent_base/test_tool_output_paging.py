"""The model can recover complete tool results independently of tracing."""

from __future__ import annotations

import asyncio
import json
import re

from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.tools.base import Tool
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.tool_output import MAX_FED_CHARS, ReadToolOutputTool
from app.runtime.trace_session import TraceSession
from app.runtime.tool_outputs import current_tool_output_store, ToolOutputStore
from app.agent_base.adapters.tracing import NoOpTraceProvider
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


def test_long_tool_output_is_paged_and_recorded(tmp_path):
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
        assert current_tool_output_store().read(output_id) == original

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


def test_paging_without_trace_and_registry_isolation():
    original = "中🙂" * 4000
    registry = ToolRegistry()
    registry.register_tool(_TextTool(original))
    reader = ReadToolOutputTool()
    registry.register_tool(reader)
    executor = ToolRoundExecutor(registry, agent_name="Test")
    result = asyncio.run(executor.execute([_call("text_tool", {}, "c")], step=1))
    first = result.tool_results[0]["content"]
    output_id, offset = re.search(r"output_id=([0-9a-f]{16}); next_offset=(\d+)", first).groups()
    assert registry.output_store.read(output_id) == original
    assert original[int(offset):int(offset) + 2600] in reader.run({"output_id": output_id, "offset": offset})
    other = ToolRegistry()
    other_reader = ReadToolOutputTool()
    other.register_tool(other_reader)
    assert other_reader.run_result({"output_id": output_id, "offset": 0}).error_code == "TOOL_OUTPUT_NOT_FOUND"
    with TraceSession(session_id="disabled", provider=NoOpTraceProvider()) as sink:
        assert not sink.path
        result = asyncio.run(executor.execute([_call("text_tool", {}, "d")], step=1))
        output_id = re.search(r"output_id=([0-9a-f]{16})", result.tool_results[0]["content"]).group(1)
        assert current_tool_output_store().read(output_id) == original
        assert original[:2600] in reader.run({"output_id": output_id, "offset": 0})
    assert current_tool_output_store() is None
    assert registry.output_store.read(output_id) is None


def test_output_store_evicts_old_results_and_rejects_oversized_results():
    store = ToolOutputStore(max_bytes=12, max_records=2)
    first = store.put("tool", "中🙂")
    second = store.put("tool", "abc")
    third = store.put("tool", "xyz")
    assert store.read(first) is None
    assert store.read(second) == "abc"
    assert store.read(third) == "xyz"
    assert store.put("tool", "中" * 5) == ""
    assert store.read(second) == "abc"
