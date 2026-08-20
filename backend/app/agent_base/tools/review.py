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

    __slots__ = ("review_type", "title", "content", "question", "metadata", "_future", "id")

    def __init__(
        self,
        review_type: str,
        title: str = "",
        content: str = "",
        question: str = "",
        metadata: Optional[dict] = None,
    ):
        self.review_type = review_type  # "code" | "test" | "design" | "uml_diff"
        self.title = title
        self.content = content  # 需要审核的具体内容
        self.question = question  # 需要人工回答的问题
        self.metadata = metadata or {}  # 结构化数据（如 UML diagrams）
        self._future: asyncio.Future | None = None
        self.id = -1  # 在 submit() 里分配（_pending 中的下标）

    def to_dict(self) -> dict:
        return {
            "review_type": self.review_type,
            "title": self.title,
            "content": self.content,
            "question": self.question,
            "metadata": self.metadata,
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
        # 最近一次被接受的设计状态（before 语义）。由编排层在每次 run 前捕获，
        # accept 时刷新；reject 时保持不变（diff 始终是「原始→当前」）。
        self.baseline: list | None = None

    def submit(self, review_type: str, title: str = "",
               content: str = "", question: str = "",
               metadata: Optional[dict] = None) -> ReviewRequest:
        """Agent 提交一个审核请求。返回带 future 的 ReviewRequest。"""
        req = ReviewRequest(
            review_type=review_type,
            title=title,
            content=content,
            question=question,
            metadata=metadata,
        )
        req.id = len(self._pending)
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

    def __init__(self, manager: ReviewManager, timeout: float = 300.0, progress=None):
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
        self.progress = progress

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
        """返回 coroutine，由 aexecute_tool_with_params await（让出 loop 等待 resolve）。"""
        return self._execute(parameters)  # type: ignore[return-value]

    async def _execute(self, parameters: Dict[str, Any]) -> str:
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

        # 阻塞前先把审核事件推给编排层（ProgressRelay → WebSocket），
        # 否则 arun_stream 要等工具返回才 yield，前端永远收不到推送。
        if self.progress is not None:
            self.progress.emit({
                "event": "review",
                "review_id": req.id,
                "review_type": review_type,
                "title": title,
                "content": content,
                "question": question,
                "metadata": req.metadata,
            })

        logger.info(
            "🔔 Agent 请求人工审核 [%s]: %s — %s",
            review_type, title, question[:100],
        )

        try:
            result = await asyncio.wait_for(req.future, timeout=self.timeout)
        except asyncio.TimeoutError:
            result = f"⏰ 审核超时: 人类在 {self.timeout} 秒内未响应"

        return f"人工审核结果 [{review_type}]: {result}"


class SubmitUmlReviewTool(Tool):
    """Agent 主动提交 UML diff 审核的工具。

    修改 UML 后调用此工具：把修改后的 diagrams 提交给前端 DiffViewer 做
    对比审核，阻塞等待用户 accept/reject + 文字反馈，把结论作为工具返回值
    喂回 agent，形成「修改 → 审核 → 根据反馈继续」的闭环。
    """

    def __init__(self, manager: ReviewManager, timeout: float = 300.0,
                 progress=None, project_file: str = ""):
        super().__init__(
            name="submit_uml_review",
            description=(
                "Submit the updated UML design for human diff review. Call this "
                "after modifying the design. Pass project_file (the .umlproj path) "
                "and a one-sentence summary; the tool loads the before/after "
                "diagrams itself and pushes them to the frontend DiffViewer. It "
                "pauses until the user accepts or rejects, then returns the "
                "decision so you can revise if rejected."
            ),
        )
        self.manager = manager
        self.timeout = timeout
        self.progress = progress
        self.project_file = project_file

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="project_file",
                type="string",
                description="Path to the .umlproj project file. The tool loads the updated diagrams from it (before is captured by the framework).",
                required=False,
            ),
            ToolParameter(
                name="summary",
                type="string",
                description="One-sentence summary of what changed, shown to the reviewer.",
                required=False,
            ),
            ToolParameter(
                name="diagrams_json",
                type="string",
                description="Optional JSON string of the updated diagrams (list of diagram objects). Only used when project_file is not provided.",
                required=False,
            ),
            ToolParameter(
                name="original_diagrams_json",
                type="string",
                description="Optional JSON string of the diagrams before modification. Only used when project_file is not provided.",
                required=False,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> str:
        """返回 coroutine，由 aexecute_tool_with_params await（让出 loop 等待 resolve）。"""
        return self._execute(parameters)  # type: ignore[return-value]

    async def _execute(self, parameters: Dict[str, Any]) -> str:
        import json as _json
        import os as _os

        summary = parameters.get("summary", "")
        project_file = parameters.get("project_file", "") or self.project_file
        title = summary or "UML diff review"

        # ── 主路径：框架自己 load before/after（模型只负责改 + 报文件路径 + 摘要）──
        if project_file and _os.path.isfile(project_file):
            try:
                from app.services.file_service import load_project
                after = [d.model_dump() for d in load_project(project_file).diagrams]
            except Exception:
                logger.exception("[SubmitUmlReviewTool] load project failed")
                after = []
            before = self.manager.baseline
            content = title
            metadata = {"diagrams": after, "original_diagrams": before}
        else:
            # ── 兜底：无 project_file 时用显式传入的 diagrams ──
            diagrams_json = parameters.get("diagrams_json", "")
            original_json = parameters.get("original_diagrams_json", "")
            try:
                diagrams = _json.loads(diagrams_json) if isinstance(diagrams_json, str) else diagrams_json
            except _json.JSONDecodeError:
                diagrams = diagrams_json
            original = None
            if original_json:
                try:
                    original = _json.loads(original_json) if isinstance(original_json, str) else original_json
                except _json.JSONDecodeError:
                    original = original_json
            content = diagrams_json if isinstance(diagrams_json, str) else _json.dumps(diagrams, ensure_ascii=False)
            metadata = {"diagrams": diagrams, "original_diagrams": original}

        req = self.manager.submit(
            review_type="uml_diff",
            title=title,
            content=content,
            question="Please review the UML changes and accept or reject.",
            metadata=metadata,
        )

        # 阻塞前先推审核事件（见 RequestReviewTool 同名注释）。
        if self.progress is not None:
            self.progress.emit({
                "event": "review",
                "review_id": req.id,
                "review_type": "uml_diff",
                "title": title,
                "content": req.content,
                "question": req.question,
                "metadata": req.metadata,
            })

        logger.info("🔔 Agent 请求 UML diff 审核: %s", title[:100])

        try:
            result = await asyncio.wait_for(req.future, timeout=self.timeout)
        except asyncio.TimeoutError:
            result = _json.dumps(
                {"decision": "timeout", "feedback": "Human did not respond"},
                ensure_ascii=False,
            )
        return result
