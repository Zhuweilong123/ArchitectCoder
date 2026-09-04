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
from typing import Optional, Dict, List

from ..core.agent import Agent
from ..core.llm import BaseAgentsLLM
from ..core.message import Message
from backend.config import AgentConfig

logger = logging.getLogger(__name__)

# ── 默认提示词模板 ────────────────────────────────────
DEFAULT_PLANNER_PROMPT = """
You are a top-tier AI planning expert. Your task is to decompose the user's complex question into an action plan made of multiple simple steps.
Make sure each step in the plan is an independent, executable sub-task, and arrange them strictly in logical order.
Your output must be a Python list where each element is a string describing a sub-task.

Question: {question}

Output your plan strictly in this format:
```python
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


class PlanAndSolveAgent(Agent):
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
        llm: BaseAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[AgentConfig] = None,
        custom_prompts: Optional[Dict[str, str]] = None,
    ):
        super().__init__(name, llm, system_prompt, config)
        self.planner_prompt = (custom_prompts or {}).get("planner", DEFAULT_PLANNER_PROMPT)
        self.executor_prompt = (custom_prompts or {}).get("executor", DEFAULT_EXECUTOR_PROMPT)
        logger.info("✅ %s 初始化完成", name)

    def run(self, input_text: str, **kwargs) -> str:
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
        history_parts: List[str] = []

        for idx, step in enumerate(plan):
            history_str = "\n".join(history_parts) if history_parts else "（尚无已完成步骤）"

            executor_input = self.executor_prompt.format(
                question=input_text,
                plan=plan_str,
                history=history_str,
                current_step=step,
            )
            messages = [{"role": "user", "content": executor_input}]
            step_result = self.llm.invoke(messages, **kwargs)

            history_parts.append(f"步骤 {idx + 1} ({step}): {step_result}")
            logger.info("✅ 步骤 %d/%d 完成 (%d 字符)", idx + 1, len(plan), len(step_result))

        # 汇总
        final_answer = history_parts[-1].split(": ", 1)[-1] if history_parts else ""
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_answer, "assistant"))
        logger.info("🏁 %s 完成", self.name)
        return final_answer

    def _plan(self, question: str, **kwargs) -> List[str]:
        """生成执行计划"""
        prompt = self.planner_prompt.format(question=question)
        messages = [{"role": "user", "content": prompt}]
        response = self.llm.invoke(messages, **kwargs)

        # 尝试从 ```python ... ``` 代码块中提取
        code_match = re.search(r"```(?:python)?\s*(\[.*?\])\s*```", response, re.DOTALL)
        if code_match:
            try:
                plan = json.loads(code_match.group(1))
                if isinstance(plan, list) and len(plan) > 0:
                    return plan
            except json.JSONDecodeError:
                pass

        # 尝试直接解析为 JSON 列表
        bracket_match = re.search(r"\[.*\]", response, re.DOTALL)
        if bracket_match:
            try:
                plan = json.loads(bracket_match.group(0))
                if isinstance(plan, list) and len(plan) > 0:
                    return plan
            except json.JSONDecodeError:
                pass

        # 回退：按行解析
        lines = response.strip().split("\n")
        steps = [
            re.sub(r"^\d+[\.\)]\s*", "", line).strip().strip('"').strip("'")
            for line in lines
            if line.strip() and not line.strip().startswith("```")
        ]
        steps = [s for s in steps if s]
        return steps if steps else []
