"""ReflectionAgent — 自我反思 + 迭代优化（messages 累积版）

"生成-验证-修正" 循环。用单一 messages 列表维护全对话上下文，
每一轮 LLM 都能看到原始需求 + 自己的历史输出 + 验证反馈，
不再需要把原始内容拆成 task/context/last_attempt/feedback 多个变量来回拼。

适用场景：代码生成、文案优化、UML 设计等需要反复打磨的任务。

Usage::

    agent = ReflectionAgent(name="反思助手", llm=llm, max_iterations=3)

    def my_validate(content: str) -> str:
        return "" if ok else "问题: ..."

    result = agent.run("写一篇短文", validate=my_validate)
"""

import logging
from typing import Optional, Callable

from ..core.agent import Agent
from ..core.llm import BaseAgentsLLM
from ..core.message import Message
from ..core.config import Config

logger = logging.getLogger(__name__)

# ── 精简提示词 — 只用两个，不再依赖 {context}/{task}/{last_attempt} format 占位 ──

REVIEW_PROMPT = """Review the answer above. Point out specific problems that need fixing.
If everything looks correct and complete, reply "OK"."""

REFINE_PROMPT = """Fix the issues identified above. Keep everything that was correct unchanged.
Output the complete corrected result, not just the fixes."""


class ReflectionAgent(Agent):
    """自我反思 Agent — 用 messages 累积上下文

    执行流程:
    1. initial: 把 input_text 作为第一条 user 消息发给 LLM
    2. validate: 外部验证（可选），通过则停止
    3. review: 把验证反馈作为 user 消息追问 LLM
    4. refine: LLM 根据对话历史中的反馈修正输出
    5. 重复 2-4 直到通过或达到 max_iterations

    用 messages 累积意味着：
    - 原始需求永远在第一条消息里
    - LLM 能看到自己之前所有的输出
    - 验证反馈作为补充消息注入，不改原始内容
    """

    def __init__(
        self,
        name: str,
        llm: BaseAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_iterations: int = 3,
    ):
        super().__init__(name, llm, system_prompt, config)
        self.max_iterations = max_iterations
        logger.info("✅ %s 初始化完成，最大迭代: %d", name, max_iterations)

    def run(
        self,
        input_text: str,
        validate: Optional[Callable[[str], str]] = None,
        **kwargs,
    ) -> str:
        """执行反思-优化循环

        Args:
            input_text: 初始 prompt（包含完整任务描述和原始数据）
            validate: 外部验证函数，签名 (content) -> feedback_str
                      返回空字符串表示通过，停止迭代。
            **kwargs: 透传给 llm.invoke()

        Returns:
            最终回答字符串
        """
        logger.info("🤖 %s 开始处理", self.name)

        from app.services.chat_trace import trace_span

        # Phase 1: 初始生成 — 原始 prompt 就是第一条 user 消息
        with trace_span(f"{self.name}/initial"):
            messages = [{"role": "user", "content": input_text}]
            current = self.llm.invoke(messages, **kwargs)
        messages.append({"role": "assistant", "content": current})
        logger.info("📝 初始生成完成 (%d 字符)", len(current))

        # Phase 2-3: 验证-修正循环
        for iteration in range(1, self.max_iterations + 1):
            logger.info("--- 反思迭代 %d/%d ---", iteration, self.max_iterations)

            # 2. 外部验证
            if not validate:
                break

            with trace_span(f"{self.name}/reflect_hook"):
                try:
                    feedback = validate(current)
                except Exception as e:
                    feedback = f"验证异常: {e}"
            logger.info("🔧 验证反馈: %s", feedback[:120] if feedback else "(通过)")

            if not feedback.strip():
                logger.info("✅ 验证通过，停止迭代")
                break

            # 3. 反思 + 修正 — 把反馈追加到对话中，让 LLM 修正
            with trace_span(f"{self.name}/refine"):
                refine_msg = REFINE_PROMPT + "\n\n" + feedback
                messages.append({"role": "user", "content": refine_msg})

                current = self.llm.invoke(messages, **kwargs)
                messages.append({"role": "assistant", "content": current})
            logger.info("🔧 修正后回答 (%d 字符)", len(current))

        self.add_message(Message(input_text, "user"))
        self.add_message(Message(current, "assistant"))
        logger.info("🏁 %s 完成", self.name)
        return current
