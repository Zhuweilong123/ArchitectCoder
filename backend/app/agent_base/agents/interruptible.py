"""InterruptibleAgent — 可中断的 Agent 包装器

在前端驱动的编排中，需要随时能够暂停或中断 Agent 的执行。
本模块提供一个薄包装，在每轮 ReAct 步骤前检查中断信号。

Usage::

    from app.agent_base.agents.interruptible import InterruptibleAgent

    agent = ReActAgent(name="dev", llm=llm, tool_registry=registry)
    interruptible = InterruptibleAgent(
        agent=agent,
        should_stop=lambda: check_stop_flag(),
    )
    async for progress in interruptible.arun_stream("生成代码"):
        if progress.get("event") == "stopped":
            print("Agent was stopped by user")
            break
        # ... render progress ...
"""

from __future__ import annotations

import logging
from typing import Callable, AsyncIterator

from ..agents.react_agent import ReActAgent, ReActProgress

logger = logging.getLogger(__name__)


class InterruptibleAgent:
    """可中断的 ReActAgent 包装器。

    在每轮循环前后检查 ``should_stop`` 回调，
    返回 True 时立即停止并 yield 停止事件。

    Attributes:
        agent: 被包装的 ReActAgent 实例
        should_stop: 无参数回调，返回 True 时中断
    """

    def __init__(
        self,
        agent: ReActAgent,
        should_stop: Callable[[], bool] | None = None,
    ):
        self.agent = agent
        self._should_stop_fn = should_stop or (lambda: False)
        self._stopped = False

    # ── 委托属性 ──────────────────────────────────────

    @property
    def should_stop(self) -> bool:
        """检查是否应中止（每次调用都会查询回调）。"""
        return self._should_stop_fn()

    @property
    def stopped(self) -> bool:
        """是否已经被中断过。"""
        return self._stopped

    # ── 公共 API ──────────────────────────────────────

    async def arun_stream(
        self, input_text: str, **kwargs
    ) -> AsyncIterator[dict]:
        """流式运行，每轮检查中断信号。

        Yield 的 dict 格式:
            - 正常进度: ReActProgress.to_dict()
            - 中断: {"event": "stopped", "reason": "...", "step": N}
            - 完成: {"event": "done", "result": "..."}
        """
        self._stopped = False
        final_answer = ""

        async for progress in self.agent.arun_stream(input_text, **kwargs):
            d = progress.to_dict()

            # 每轮后检查中断
            if self.should_stop:
                self._stopped = True
                d["event"] = "stopped"
                d["reason"] = "User requested stop"
                logger.info("🛑 %s 被中断 (step=%s)", self.agent.name, progress.step)
                yield d
                return

            yield d

            if progress.is_final:
                final_answer = progress.final_answer

        if not self._stopped:
            yield {
                "event": "done",
                "result": final_answer,
            }
