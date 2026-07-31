"""ReActAgent — Reasoning + Acting 范式

实现 Thought → Action → Observation 循环。
每次只执行一个步骤，使用工具获取信息，最终给出答案。

Usage::

    agent = ReActAgent(name="推理助手", llm=llm, tool_registry=registry, max_steps=5)
    result = agent.run("最近有什么关于AI的热点新闻？")
"""

import re
import logging
from typing import Optional, List

from ..core.agent import Agent
from ..core.llm import BaseAgentsLLM
from ..core.message import Message
from ..core.config import Config
from ..tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ── ReAct 提示词模板 ──────────────────────────────────
REACT_PROMPT = """你是一个具备推理和行动能力的AI助手。你可以通过思考分析问题，然后调用合适的工具来获取信息，最终给出准确的答案。

## 可用工具
{tools}

## 工作流程
请严格按照以下格式进行回应，每次只能执行一个步骤:

Thought: 分析当前问题，思考需要什么信息或采取什么行动。
Action: 选择一个行动，格式必须是以下之一:
- `{{tool_name}}[{{tool_input}}]` - 调用指定工具
- `Finish[最终答案]` - 当你有足够信息给出最终答案时

## 重要提醒
1. 每次回应必须包含Thought和Action两部分
2. 工具调用的格式必须严格遵循:工具名[参数]
3. 只有当你确信有足够信息回答问题时，才使用Finish
4. 如果工具返回的信息不够，继续使用其他工具或相同工具的不同参数

## 当前任务
**Question:** {question}

## 执行历史
{history}

现在开始你的推理和行动:
"""


class ReActAgent(Agent):
    """ReAct (Reasoning + Acting) Agent

    核心循环:
    1. 构建 prompt → 2. LLM 推理 → 3. 解析 Thought/Action →
    4. 执行 Action → 5. 观察结果 → 回到 1 或 Finish

    Attributes:
        max_steps: 最大循环步数，防止无限循环
        custom_prompt: 自定义提示词模板（可选）
    """

    def __init__(
        self,
        name: str,
        llm: BaseAgentsLLM,
        tool_registry: ToolRegistry,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 5,
        custom_prompt: Optional[str] = None,
    ):
        super().__init__(name, llm, system_prompt, config)
        self.tool_registry = tool_registry
        self.max_steps = max_steps
        self.current_history: List[str] = []
        self.prompt_template = custom_prompt or REACT_PROMPT
        logger.info("✅ %s 初始化完成，最大步数: %d", name, max_steps)

    def run(self, input_text: str, **kwargs) -> str:
        """运行 ReAct 循环"""
        self.current_history = []
        current_step = 0

        logger.info("\n🤖 %s 开始处理问题: %s", self.name, input_text)

        while current_step < self.max_steps:
            current_step += 1
            logger.info("\n--- 第 %d 步 ---", current_step)

            # 1. 构建提示词
            tools_desc = self.tool_registry.get_tools_description()
            history_str = "\n".join(self.current_history)
            prompt = self.prompt_template.format(
                tools=tools_desc,
                question=input_text,
                history=history_str,
            )

            # 2. 调用 LLM
            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm.invoke(messages, **kwargs)

            # 3. 解析输出
            thought, action = self._parse_output(response_text)
            logger.info("  Thought: %s", thought[:100] if thought else "无")
            if action:
                logger.info("  Action: %s", action)

            # 4. 检查是否完成
            if action and action.startswith("Finish"):
                final_answer = self._parse_action_input(action)
                self.add_message(Message(input_text, "user"))
                self.add_message(Message(final_answer, "assistant"))
                logger.info("🏁 %s 完成", self.name)
                return final_answer

            # 5. 执行工具调用
            if action:
                tool_name, tool_input = self._parse_action(action)
                if tool_name:
                    observation = self.tool_registry.execute_tool(tool_name, tool_input)
                    self.current_history.append(f"Step {current_step}: Action: {action}")
                    self.current_history.append(f"Step {current_step}: Observation: {observation}")
                    logger.info("  Observation: %s", observation[:100])
                else:
                    self.current_history.append(f"Step {current_step}: 无效的Action格式")
            else:
                self.current_history.append(f"Step {current_step}: 未解析到Action")

        # 达到最大步数
        final_answer = "抱歉，我无法在限定步数内完成这个任务。"
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_answer, "assistant"))
        logger.warning("⚠️ %s 达到最大步数 %d", self.name, self.max_steps)
        return final_answer

    # ── 解析逻辑 ─────────────────────────────────────

    def _parse_output(self, text: str) -> tuple:
        """解析 LLM 输出，提取 (Thought, Action)"""
        thought = None
        action = None

        thought_match = re.search(r"Thought:\s*(.+?)(?=\n\s*(?:Action:|$))", text, re.DOTALL)
        if thought_match:
            thought = thought_match.group(1).strip()

        action_match = re.search(r"Action:\s*(.+)", text)
        if action_match:
            action = action_match.group(1).strip()

        return thought, action

    def _parse_action(self, action_text: str) -> tuple:
        """解析 Action 文本，提取 (tool_name, tool_input)"""
        # Format: tool_name[tool_input]  or  Finish[final_answer]
        match = re.match(r"(\w+)\[(.*)\]", action_text)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def _parse_action_input(self, action_text: str) -> str:
        """解析 Finish[answer] 中的最终答案"""
        match = re.match(r"Finish\[(.*)\]", action_text, re.DOTALL)
        if match:
            return match.group(1)
        return action_text
