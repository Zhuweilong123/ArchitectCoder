"""ReActAgent / InterruptibleAgent 单元测试（Mock LLM，无需真实 API）。"""
import asyncio
import json

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.agents.interruptible import InterruptibleAgent
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry


class EchoTool(Tool):
    def __init__(self):
        super().__init__(name="echo", description="回显输入文本")

    def get_parameters(self):
        return [ToolParameter(name="text", type="string", description="要回显的文本")]

    def run(self, parameters):
        return f"回显: {parameters.get('text', '')}"


class MockLLM:
    """前 ``rounds`` 次返回 tool_call，之后返回纯文本终止循环。"""

    def __init__(self, rounds=2):
        self.rounds = rounds
        self.count = 0

    async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.count += 1
        if self.count <= self.rounds:
            return {
                "content": f"Step {self.count}",
                "tool_calls": [{
                    "id": f"c{self.count}", "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": json.dumps({"text": f"round_{self.count}"}),
                    },
                }],
            }
        return {"content": "完成", "tool_calls": None}


def _registry():
    reg = ToolRegistry()
    reg.register_tool(EchoTool())
    return reg


def test_react_agent_fc_loop_executes_tools():
    llm = MockLLM(rounds=2)
    agent = ReActAgent("Test", llm, _registry(), max_steps=10)

    events = asyncio.run(_collect(agent))

    assert llm.count == 3  # 2 次工具调用 + 1 次最终
    assert events[-1].is_final is True
    assert events[-1].final_answer == "完成"


def test_interruptible_agent_stops():
    llm = MockLLM(rounds=10)
    agent = ReActAgent("Test", llm, _registry(), max_steps=10)

    interruptible = InterruptibleAgent(
        agent=agent,
        should_stop=lambda: llm.count >= 2,
    )

    events = asyncio.run(_collect_interruptible(interruptible))

    assert any(e.get("event") == "stopped" for e in events)
    assert llm.count == 2  # 第 2 轮后被中断，不再继续


async def _collect(agent):
    events = []
    async for p in agent.arun_stream("echo test"):
        events.append(p)
    return events


async def _collect_interruptible(interruptible):
    events = []
    async for e in interruptible.arun_stream("echo several times"):
        events.append(e)
    return events
