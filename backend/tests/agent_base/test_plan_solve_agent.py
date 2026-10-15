"""The synchronous planner depends only on its model port and validated steps."""
from copy import deepcopy
from typing import Unpack

import pytest

from app.agent_base.agents.plan_solve_agent import PlanAndSolveAgent, parse_plan
from app.agent_base.host_api.agents import SyncModelPort
from shared.llm_types import ModelMessage, TextGenerationOptions


class SyncModel:
    provider = "test"

    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[list[ModelMessage], TextGenerationOptions]] = []

    def invoke(self, messages: list[ModelMessage], **options: Unpack[TextGenerationOptions]) -> str:
        self.calls.append((deepcopy(messages), options))
        return next(self.responses)


@pytest.mark.parametrize("response", [
    '[" first ", "second"]',
    '```json\n["first", "second"]\n```',
    '```python\n["first", "second"]\n```',
    'Here is the plan: ["first", "second"]',
    '1. first\n2) second',
])
def test_plan_accepts_supported_formats(response):
    assert parse_plan(response) == ["first", "second"]


@pytest.mark.parametrize("response", [
    '[]', '[1]', '["ok", null]', '["ok", {}]', '["ok", ["nested"]]',
    '["ok", "  "]', '["unterminated"', '{"steps": ["first"]}',
    '```json\n["ok", false]\n```', "",
])
def test_invalid_structured_plan_never_reaches_executor(response):
    model = SyncModel([response])
    agent = PlanAndSolveAgent("planner", model)

    assert "规划失败" in agent.run("question")
    assert len(model.calls) == 1
    assert [message.role for message in agent.get_history()] == ["user", "assistant"]


def test_sync_only_model_receives_options_system_prompt_and_prior_results():
    model = SyncModel(['["first", "second"]', "first result", "answer: exact\ntext"])
    port: SyncModelPort = model
    agent = PlanAndSolveAgent("planner", port, system_prompt="system instruction")

    assert agent.run("question", temperature=0.2, max_tokens=40) == "answer: exact\ntext"
    assert len(model.calls) == 3
    for messages, options in model.calls:
        assert messages[0] == {"role": "system", "content": "system instruction"}
        assert options == {"temperature": 0.2, "max_tokens": 40}
    assert "first result" in model.calls[-1][0][-1]["content"]
    assert agent.get_history()[-1].content == "answer: exact\ntext"
