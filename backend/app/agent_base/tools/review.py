"""RequestReviewTool — Agent 自主请求人工审核

当 Agent 需要人工确认代码、测试或设计决策时，调用此工具。
编排层拦截这个调用，推送审核事件到前端，等待人类响应后
将响应作为 tool observation 返回给 Agent，Agent 继续执行。

Usage::

    from app.agent_base.tools.review import RequestReviewTool, ReviewManager

    # 创建工具 + 管理器
    review_mgr = ReviewManager()
    review_tool = RequestReviewTool(manager=review_mgr)

    # 注册到 Agent 的 ToolRegistry
    registry.register_tool(review_tool)

    # 编排层监听
    async def orchestrate(agent, input_text):
        async for progress in agent.arun_stream(input_text):
            if "tool_calls" in progress and review_mgr.has_pending():
                # 推送审核事件到前端
                yield {"event": "request_review", "data": review_mgr.get_pending()}
                # 等待人类响应（通过 WebSocket 注入）
                # review_mgr.resolve(response_text)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Any, List, Optional

from app.agent_base.tools.base import Tool, ToolParameter

logger = logging.getLogger(__name__)


class ReviewRequest:
    """一次审核请求的上下文数据"""

    __slots__ = ("review_type", "title", "content", "question", "_future")

    def __init__(
        self,
        review_type: str,
        title: str = "",
        content: str = "",
        question: str = "",
    ):
        self.review_type = review_type  # "code" | "test" | "design"
        self.title = title
        self.content = content  # 需要审核的具体内容
        self.question = question  # 需要人工回答的问题
        self._future: asyncio.Future | None = None

    def to_dict(self) -> dict:
        return {
            "review_type": self.review_type,
            "title": self.title,
            "content": self.content,
            "question": self.question,
        }

    @property
    def future(self) -> asyncio.Future:
        if self._future is None:
            self._future = asyncio.Future()
        return self._future


class ReviewManager:
    """管理审核请求的生命周期。

    编排层通过此类与 Agent 的审核请求交互：
    1. Agent 调用 ``request_review`` → ``submit()`` 创建一个未来
    2. 编排层查询 ``has_pending()`` → 推送前端
    3. 人类响应 → ``resolve()`` 完成未来
    4. Agent 收到 observation 继续执行
    """

    def __init__(self):
        self._pending: list[ReviewRequest] = []

    def submit(self, review_type: str, title: str = "",
               content: str = "", question: str = "") -> ReviewRequest:
        """Agent 提交一个审核请求。返回带 future 的 ReviewRequest。"""
        req = ReviewRequest(
            review_type=review_type,
            title=title,
            content=content,
            question=question,
        )
        self._pending.append(req)
        logger.info("📋 审核请求已提交: type=%s title=%s", review_type, title)
        return req

    def has_pending(self) -> bool:
        """是否有等待中的审核请求。"""
        return any(
            r.future is not None and not r.future.done()
            for r in self._pending
        )

    def get_pending(self) -> list[dict]:
        """获取所有未完成的审核请求（供前端展示）。"""
        result = []
        for i, r in enumerate(self._pending):
            if r.future is not None and not r.future.done():
                d = r.to_dict()
                d["id"] = i
                result.append(d)
        return result

    def resolve(self, request_index: int, response: str) -> bool:
        """人类对第 N 个请求给出响应。返回是否成功。"""
        if 0 <= request_index < len(self._pending):
            req = self._pending[request_index]
            if req.future is not None and not req.future.done():
                req.future.set_result(response)
                logger.info("✅ 审核请求 %d 已解决: %s", request_index, response[:80])
                return True
        logger.warning("⚠️ 审核请求 %d 不存在或已完成", request_index)
        return False

    def reject(self, request_index: int, reason: str = "Rejected by user") -> bool:
        """人类拒绝了审核请求。"""
        if 0 <= request_index < len(self._pending):
            req = self._pending[request_index]
            if req.future is not None and not req.future.done():
                req.future.set_result(f"❌ 拒绝: {reason}")
                logger.info("🛑 审核请求 %d 已拒绝: %s", request_index, reason)
                return True
        return False

    def reset(self):
        """清空所有请求（流水线重置时调用）。"""
        for r in self._pending:
            if r.future is not None and not r.future.done():
                r.future.set_result("Cancelled: pipeline reset")
        self._pending.clear()


class RequestReviewTool(Tool):
    """Agent 自主请求人工审核的工具。

    Agent 在需要人工判断时调用此工具：
    - 代码生成后不确定是否正确
    - 测试覆盖需要确认
    - 设计决策需要权衡

    编排层拦截此调用，推送审核到前端，用人类的响应
    作为 tool observation 继续 Agent 循环。
    """

    def __init__(self, manager: ReviewManager, timeout: float = 300.0):
        super().__init__(
            name="request_review",
            description=(
                "Call this tool when you need human review of code, test cases, or "
                "design decisions. After calling, your execution pauses to wait for "
                "human feedback. The human's reply is injected as the tool return "
                "value, and you can continue based on it. review_type values: "
                "code (code review), test (test review), design (design review)"
            ),
        )
        self.manager = manager
        self.timeout = timeout

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="review_type",
                type="string",
                description="Review type: code, test, or design",
                required=True,
            ),
            ToolParameter(
                name="title",
                type="string",
                description="Review title (one sentence summarizing what needs review)",
                required=True,
            ),
            ToolParameter(
                name="content",
                type="string",
                description="The specific content to review (code snippet, test list, design description, etc.)",
                required=True,
            ),
            ToolParameter(
                name="question",
                type="string",
                description="The specific question for the human to answer (e.g. 'Is this fix approach correct?')",
                required=True,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> str:
        """执行审核请求 — 提交到管理器并阻塞等待人类响应。

        注意: 这个方法在 Agent 的 ReAct 循环中被调用。
        由于 use_native_fc 模式下工具执行是同步的（在 asyncio 事件循环中），
        这里使用同步等待。编排层应在 Agent 所在事件循环中通过
        resolve() 来触发 future。
        """
        review_type = parameters.get("review_type", "code")
        title = parameters.get("title", "")
        content = parameters.get("content", "")
        question = parameters.get("question", "")

        req = self.manager.submit(
            review_type=review_type,
            title=title,
            content=content,
            question=question,
        )

        logger.info(
            "🔔 Agent 请求人工审核 [%s]: %s — %s",
            review_type, title, question[:100],
        )

        # 在有 running loop 时用 ThreadPoolExecutor 等待 future，
        # 避免 run_until_complete 在已有 loop 中死锁
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()

        if loop.is_running():
            # 在后台线程中等待 — 当前 FC 循环在同一个 loop 中
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(lambda: asyncio.run(
                    asyncio.wait_for(req.future, timeout=self.timeout)
                ))
                try:
                    result = future.result(timeout=self.timeout + 10)
                except concurrent.futures.TimeoutError:
                    result = "⏰ 审核超时: 人类在 %d 秒内未响应" % self.timeout
                except Exception as e:
                    result = f"⚠️ 审核异常: {e}"
        else:
            try:
                result = asyncio.run(
                    asyncio.wait_for(req.future, timeout=self.timeout)
                )
            except asyncio.TimeoutError:
                result = "⏰ 审核超时: 人类在 %d 秒内未响应" % self.timeout
            except Exception as e:
                result = f"⚠️ 审核异常: {e}"

        return f"人工审核结果 [{review_type}]: {result}"
