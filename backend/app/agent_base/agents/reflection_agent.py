"""ReflectionAgent — 自我反思 + 迭代优化（增强版）

"执行-反思-优化" 三阶段循环。
支持注入外部验证工具实现客观化反馈，替代纯 LLM 自省。

适用场景：代码生成、文案优化、UML 设计等需要反复打磨的任务。

Usage::

    agent = ReflectionAgent(name="反思助手", llm=llm, max_iterations=3)

    # 可选: 注入外部验证 Hook
    def my_reflect(task, content, context):
        return "验证报告: ..."

    result = agent.run("写一篇短文", reflect_hook=my_reflect)
"""

import logging
from typing import Optional, Dict, Callable

from ..core.agent import Agent
from ..core.llm import BaseAgentsLLM
from ..core.message import Message
from ..core.config import Config

logger = logging.getLogger(__name__)

# ── 默认三阶段提示词 ────────────────────────────────────
DEFAULT_PROMPTS = {
    "initial": """
Complete the following task:

{context}
Task: {task}

Provide a complete and accurate answer.
""",
    "reflect": """
Carefully review the answer below and find possible problems or room for improvement:

# Context:
{context}

# Original task:
{task}

# Current answer:
{content}

# Automated validation result:
{auto_feedback}

Combine the automated validation result, analyze the quality of this answer, point out its shortcomings, and give specific improvement suggestions.
If the answer is already good and automated validation found no problems, reply "no improvement needed".
""",
    "refine": """
Improve your answer based on the feedback:

# Context:
{context}

# Original task:
{task}

# Previous answer:
{last_attempt}

# Feedback:
{feedback}

Provide an improved answer.
""",
}


class ReflectionAgent(Agent):
    """自我反思 Agent（增强版）

    执行流程:
    1. initial         → 首轮生成
    2. reflect (Hook)  → 外部工具验证 + LLM 语义审查 → 生成反馈
    3. refine          → 根据反馈重新生成
    4. post_process    → 输出后处理（规范化、布局等）
    5. 重复 2-4 直到验证通过或达到 max_iterations

    Attributes:
        max_iterations: 最大反思-优化循环次数
        custom_prompts: 自定义三阶段提示词字典
        context: 注入到所有阶段的上下文信息
    """

    def __init__(
        self,
        name: str,
        llm: BaseAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_iterations: int = 3,
        custom_prompts: Optional[Dict[str, str]] = None,
        context: str = "",
    ):
        super().__init__(name, llm, system_prompt, config)
        self.max_iterations = max_iterations
        self.prompts = {**DEFAULT_PROMPTS, **(custom_prompts or {})}
        self.context = context
        logger.info("✅ %s 初始化完成，最大迭代: %d", name, max_iterations)

    def run(
        self,
        input_text: str,
        reflect_hook: Optional[Callable[[str, str, str], str]] = None,
        post_process: Optional[Callable[[str], str]] = None,
        **kwargs,
    ) -> str:
        """执行反思-优化循环

        Args:
            input_text: 用户任务描述
            reflect_hook: 外部验证 Hook，签名 ``(task, content, context) -> feedback_str``
                          返回的反馈注入到 LLM 反思 prompt 的 ``{auto_feedback}`` 占位符。
                          返回空字符串表示无问题，停止迭代。
            post_process: 输出后处理 Hook，签名 ``(content) -> processed_content``
                          每轮 refine 后和最终结果都会调用。
            **kwargs: 透传给 llm.invoke()

        Returns:
            最终回答字符串
        """
        logger.info("🤖 %s 开始处理: %s", self.name, input_text[:80])

        # Phase 1: 初始生成
        initial_prompt = self.prompts["initial"].format(
            task=input_text,
            context=self.context,
        )
        messages = [{"role": "user", "content": initial_prompt}]
        current_answer = self.llm.invoke(messages, **kwargs)
        logger.info("📝 初始回答生成完成 (%d 字符)", len(current_answer))

        # 后处理
        if post_process:
            current_answer = post_process(current_answer)

        # Phase 2-3: 反思-优化循环
        for iteration in range(1, self.max_iterations + 1):
            logger.info("--- 反思迭代 %d/%d ---", iteration, self.max_iterations)

            # 2. 反思: 外部 Hook + LLM 语义审查
            auto_feedback = ""
            if reflect_hook:
                try:
                    auto_feedback = reflect_hook(input_text, current_answer, self.context)
                    logger.info("🔧 外部验证: %s", auto_feedback[:120])
                except Exception as e:
                    auto_feedback = f"外部验证执行失败: {e}"
                    logger.warning("⚠️ 外部验证 Hook 异常: %s", e)

                # 如果外部验证完全通过，无需迭代
                if not auto_feedback.strip():
                    logger.info("✅ 外部验证通过，停止迭代")
                    break

            # LLM 语义审查（含自动验证结果）
            reflect_prompt = self.prompts["reflect"].format(
                task=input_text,
                content=current_answer,
                context=self.context,
                auto_feedback=auto_feedback or "（未启用自动验证）",
            )
            messages = [{"role": "user", "content": reflect_prompt}]
            feedback = self.llm.invoke(messages, **kwargs)
            logger.info("🔍 反馈: %s", feedback[:120])

            # 检查是否无需改进
            if "no improvement needed" in feedback.lower():
                logger.info("✅ 回答已被判定为满意，停止迭代")
                break

            # 3. 精炼
            refine_prompt = self.prompts["refine"].format(
                task=input_text,
                context=self.context,
                last_attempt=current_answer,
                feedback=feedback,
            )
            messages = [{"role": "user", "content": refine_prompt}]
            current_answer = self.llm.invoke(messages, **kwargs)
            logger.info("🔧 精炼后回答 (%d 字符)", len(current_answer))

            # 后处理
            if post_process:
                current_answer = post_process(current_answer)

        self.add_message(Message(input_text, "user"))
        self.add_message(Message(current_answer, "assistant"))
        logger.info("🏁 %s 完成", self.name)
        return current_answer
