"""PlanAndSolveAgent — 先规划后执行

将复杂问题分解为步骤序列，然后逐步执行。
Planner 负责生成步骤列表，Executor 负责按步骤执行。

适用场景：复杂多步骤任务、需要结构化分解的问题。

Usage::

    agent = PlanAndSolveAgent(name="规划助手", llm=llm)
    result = agent.run("设计一个用户注册系统，包括表单验证、密码加密、邮箱确认")
"""

import re
import json
import logging
from typing import Optional, TypedDict, Unpack

from ..core.agent import Agent
from ..host_api.agents import SyncModelPort
from ..host_api.data import ModelMessage
from shared.llm_types import TextGenerationOptions
from ..core.message import Message
from backend.config import AgentConfig

logger = logging.getLogger(__name__)

# ── 默认提示词模板 ────────────────────────────────────
DEFAULT_PLANNER_PROMPT = """
You are a top-tier AI planning expert. Your task is to decompose the user's complex question into an action plan made of multiple simple steps.
Make sure each step in the plan is an independent, executable sub-task, and arrange them strictly in logical order.
Your output must be a JSON array where each element is a non-empty string describing a sub-task.

Question: {question}

Output your plan strictly in this format:
```json
["step 1", "step 2", "step 3", ...]
```
"""

DEFAULT_EXECUTOR_PROMPT = """
You are a top-tier AI execution expert. Your task is to solve the problem step by step, strictly following the given plan.
You will receive the original question, the complete plan, and the steps/results completed so far.
Focus on solving the "current step" only, and output only the final answer for that step, without any extra explanation or conversation.

# Original question:
{question}

# Complete plan:
{plan}

# History of steps and results:
{history}

# Current step:
{current_step}

Output only the answer for the "current step":
"""


class PlanPrompts(TypedDict, total=False):
    planner: str
    executor: str


def parse_plan(response: str) -> list[str]:
    """Validate structured plans before execution; retain plain-text line fallback."""
    code_match = re.search(r"```(?:json|python)?\s*(.*?)\s*```", response, re.DOTALL)
    bracket_match = re.search(r"\[.*\]", response, re.DOTALL)
    structured: str | None = None
    if code_match:
        structured = code_match.group(1)
    elif response.strip().startswith(("[", "{")):
        structured = response.strip()
    elif bracket_match:
        structured = bracket_match.group(0)
    if structured is not None:
        try:
            value: object = json.loads(structured)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, list) or not value:
            return []
        steps: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                return []
            steps.append(item.strip())
        return steps
    # An object or malformed array is not an executable prose plan.
    if response.strip().startswith(("[", "{", "```")):
        return []
    lines = [
        re.sub(r"^\d+[\.\)]\s*", "", line.strip()).strip().strip('"').strip("'")
        for line in response.splitlines() if line.strip()
    ]
    return [line for line in lines if line]


class PlanAndSolveAgent(Agent[SyncModelPort]):
    """Plan-and-Solve Agent

    执行流程:
    1. Planner: 将问题分解为 Python 列表格式的步骤
    2. Executor: 逐步执行每个步骤，历史结果传递给后续步骤
    3. 汇总: 最后一步的输出作为最终答案

    Attributes:
        custom_prompts: 自定义提示词字典，支持 "planner" 和 "executor" 键
    """

    def __init__(
        self,
        name: str,
        llm: SyncModelPort,
        system_prompt: Optional[str] = None,
        config: Optional[AgentConfig] = None,
        custom_prompts: PlanPrompts | None = None,
    ) -> None:
        super().__init__(name, llm, system_prompt, config)
        self.planner_prompt = (custom_prompts or {}).get("planner", DEFAULT_PLANNER_PROMPT)
        self.executor_prompt = (custom_prompts or {}).get("executor", DEFAULT_EXECUTOR_PROMPT)
        logger.info("✅ %s 初始化完成", name)

    def run(self, input_text: str, **kwargs: Unpack[TextGenerationOptions]) -> str:
        """执行 规划 → 逐步执行 流程"""
        logger.info("🤖 %s 开始处理: %s", self.name, input_text[:80])

        # Phase 1: 规划
        plan = self._plan(input_text, **kwargs)
        if not plan:
            error_msg = "❌ 规划失败: 无法生成有效计划"
            self.add_message(Message(input_text, "user"))
            self.add_message(Message(error_msg, "assistant"))
            return error_msg

        logger.info("📋 计划生成: %d 个步骤", len(plan))
        for i, step in enumerate(plan, 1):
            logger.info("  步骤 %d: %s", i, step)

        # Phase 2: 逐步执行
        plan_str = "\n".join(f"{i}. {s}" for i, s in enumerate(plan, 1))
        history_parts: list[str] = []
        final_answer = ""

        for idx, step in enumerate(plan):
            history_str = "\n".join(history_parts) if history_parts else "（尚无已完成步骤）"

            executor_input = self.executor_prompt.format(
                question=input_text,
                plan=plan_str,
                history=history_str,
                current_step=step,
            )
            step_result = self.llm.invoke(self._messages(executor_input), **kwargs)
            final_answer = step_result

            history_parts.append(f"步骤 {idx + 1} ({step}): {step_result}")
            logger.info("✅ 步骤 %d/%d 完成 (%d 字符)", idx + 1, len(plan), len(step_result))

        # 汇总
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_answer, "assistant"))
        logger.info("🏁 %s 完成", self.name)
        return final_answer

    def _messages(self, content: str) -> list[ModelMessage]:
        messages: list[ModelMessage] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": content})
        return messages

    def _plan(self, question: str, **kwargs: Unpack[TextGenerationOptions]) -> list[str]:
        """生成执行计划"""
        prompt = self.planner_prompt.format(question=question)
        response = self.llm.invoke(self._messages(prompt), **kwargs)
        return parse_plan(response)
