"""人工审核机制 — ReviewManager + SubmitUmlReviewTool

核心机制（ReviewManager）：Agent/工具提交审核请求 → 编排层推送审核事件
到前端 → 人类响应通过 WebSocket 注入 → 响应作为 tool observation 返回，
调用方继续执行。当前有两个使用方：

- SubmitUmlReviewTool：UML diff 审核（前端 DiffViewer 对比 accept/reject）
- BashTool：敏感命令审核（高危命令直接拒绝，敏感命令经此机制等人工批准）

Usage::

    from app.agent_base.tools.review import ReviewManager, SubmitUmlReviewTool

    # 创建管理器 + 工具
    review_mgr = ReviewManager()
    review_tool = SubmitUmlReviewTool(manager=review_mgr, project_file=project_file)

    # 注册到 Agent 的 ToolRegistry
    registry.register_tool(review_tool)

    # 人类响应（WebSocket 注入）
    # review_mgr.resolve(request_id, response_text)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import secrets
from typing import Dict, Any, List, Optional

from app.agent_base.tools.base import Tool, ToolParameter
from app.services.diagram_diff import changed_diagrams

logger = logging.getLogger(__name__)


class ReviewRequest:
    """一次审核请求的上下文数据"""

    __slots__ = ("review_type", "title", "content", "question", "metadata", "_future", "_response", "id", "token", "session_id", "project_id")

    def __init__(
        self,
        review_type: str,
        title: str = "",
        content: str = "",
        question: str = "",
        metadata: Optional[dict] = None,
        session_id: str = "",
        project_id: str = "",
    ):
        self.review_type = review_type  # "code" | "test" | "design" | "uml_diff"
        self.title = title
        self.content = content  # 需要审核的具体内容
        self.question = question  # 需要人工回答的问题
        self.metadata = metadata or {}  # 结构化数据（如 UML diagrams）
        self._future: asyncio.Future | None = None
        self._response: str | None = None
        self.session_id = session_id
        self.project_id = project_id
        self.id = -1  # 兼容旧前端的整数 ID；由 manager 单调分配，不再使用 list 下标
        self.token = secrets.token_urlsafe(18)  # 跨 reset / 重连时的不可猜测标识

    def to_dict(self) -> dict:
        return {
            "review_type": self.review_type,
            "title": self.title,
            "content": self.content,
            "question": self.question,
            "metadata": self.metadata,
            "token": self.token,
            "session_id": self.session_id,
            "project_id": self.project_id,
        }

    @property
    def future(self) -> asyncio.Future:
        if self._future is None:
            self._future = asyncio.get_running_loop().create_future()
            if self._response is not None:
                self._future.set_result(self._response)
        return self._future


class ReviewManager:
    """管理审核请求的生命周期。

    编排层通过此类与 Agent 的审核请求交互：
    1. Agent 调用 ``request_review`` → ``submit()`` 创建一个未来
    2. 编排层查询 ``has_pending()`` → 推送前端
    3. 人类响应 → ``resolve()`` 完成未来
    4. Agent 收到 observation 继续执行
    """

    def __init__(
        self,
        session_id: str = "",
        project_id: str = "",
        auto_approve_reviews: bool = False,
    ):
        self._pending: list[ReviewRequest] = []
        self._next_id = 0
        self.session_id = session_id
        self.project_id = project_id
        self.auto_approve_reviews = auto_approve_reviews
        self.approval_events: list[dict] = []
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
            session_id=self.session_id,
            project_id=self.project_id,
        )
        req.id = self._next_id
        self._next_id += 1
        self._pending.append(req)
        self.approval_events.append({
            "event": "review_requested",
            "review_id": req.id,
            "review_type": review_type,
            "title": title,
            "approval_mode": "auto_stub" if self.auto_approve_reviews else "human",
        })
        # Evaluation runs have no interactive frontend. Automatically accept
        # the reviewable operations so the real agent loop remains continuous;
        # high-risk bash commands are still rejected by BashTool before this
        # manager is consulted.
        if self.auto_approve_reviews and review_type in {"uml_diff", "bash_command"}:
            response = json.dumps({
                "decision": "accept",
                "feedback": "Automatically accepted by evaluation approval stub.",
                "approval_mode": "auto_stub",
            }, ensure_ascii=False)
            req._response = response
            self.approval_events.append({
                "event": "review_response",
                "review_id": req.id,
                "review_type": review_type,
                "decision": "accept",
                "approval_mode": "auto_stub",
            })
        logger.info("📋 审核请求已提交: type=%s title=%s", review_type, title)
        return req

    def has_pending(self) -> bool:
        """是否有等待中的审核请求。"""
        return any(
            r._response is None and (r._future is None or not r._future.done())
            for r in self._pending
        )

    def get_pending(self) -> list[dict]:
        """获取所有未完成的审核请求（供前端展示）。"""
        result = []
        for r in self._pending:
            if r._response is None and (r._future is None or not r._future.done()):
                d = r.to_dict()
                d["id"] = r.id
                result.append(d)
        return result

    def _find(self, request_id) -> ReviewRequest | None:
        """按稳定 id/token 查找请求。

        不再把整数解释为当前列表下标：reset 后按下标回退会让旧响应
        错误地完成一条全新的审核请求。
        """
        for req in self._pending:
            if request_id == req.id or request_id == req.token:
                return req
        return None

    def resolve(self, request_index: int | str, response: str, session_id: str = "") -> bool:
        """按稳定请求 ID/token 完成审核。"""
        req = self._find(request_index)
        if req is not None:
            if session_id and req.session_id and session_id != req.session_id:
                return False
            if req._response is not None or (req._future is not None and req._future.done()):
                return False
            if req._future is None:
                req._response = response
            else:
                req._future.set_result(response)
            logger.info("✅ 审核请求 %s 已解决: %s", request_index, response[:80])
            return True
        logger.warning("⚠️ 审核请求 %s 不存在或已完成", request_index)
        return False

    def reject(self, request_index: int | str, reason: str = "Rejected by user") -> bool:
        """人类拒绝了审核请求。"""
        req = self._find(request_index)
        if req is not None:
            response = f"❌ 拒绝: {reason}"
            if req._response is not None or (req._future is not None and req._future.done()):
                return False
            if req._future is None:
                req._response = response
            else:
                req._future.set_result(response)
            logger.info("🛑 审核请求 %s 已拒绝: %s", request_index, reason)
            return True
        return False

    def reset(self):
        """清空所有请求（流水线重置时调用）。"""
        for r in self._pending:
            if r._future is not None and not r._future.done():
                r.future.set_result("Cancelled: pipeline reset")
            elif r._future is None:
                r._response = "Cancelled: pipeline reset"
        self._pending.clear()


class SubmitUmlReviewTool(Tool):
    """Agent 主动提交 UML diff 审核的工具。

    修改 UML 后调用此工具：把修改后的 diagrams 提交给前端 DiffViewer 做
    对比审核，阻塞等待用户 accept/reject + 文字反馈，把结论作为工具返回值
    喂回 agent，形成「修改 → 审核 → 根据反馈继续」的闭环。
    """

    def __init__(self, manager: ReviewManager, timeout: float = 300.0,
                 progress=None, project_file: str = "", workspace_root: str = ""):
        super().__init__(
            name="submit_uml_review",
            description=(
                "Submit the updated UML design for human diff review. Call this "
                "after modifying the design. Pass project_file (the design project path) "
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
        self.workspace_root = workspace_root

    def _resolve_project_file(self, value: str) -> str:
        """Resolve model-supplied project paths against the runtime workspace.

        The model commonly receives paths relative to the configured project
        root (for example ``design/example.umlproj``), while the backend
        process may run from the repository root.  Review payload generation
        must use the same path semantics as the other workspace tools instead
        of depending on the backend process cwd.
        """
        if not value:
            return ""

        raw = Path(value).expanduser()
        if raw.is_absolute():
            return str(raw.resolve())

        roots: list[Path] = []
        if self.workspace_root:
            roots.append(Path(self.workspace_root))
        if self.project_file:
            configured = Path(self.project_file)
            if configured.is_absolute():
                roots.append(configured.resolve().parent.parent)
        roots.append(Path.cwd())

        seen: set[str] = set()
        for root in roots:
            resolved_root = root.resolve()
            root_key = str(resolved_root).casefold()
            if root_key in seen:
                continue
            seen.add(root_key)
            candidate = (resolved_root / raw).resolve()
            if candidate.is_file():
                return str(candidate)

        # Preserve a deterministic path for the error/fallback metadata even
        # when the file is missing; callers can then distinguish it from an
        # intentionally omitted project_file.
        base = Path(self.workspace_root).resolve() if self.workspace_root else Path.cwd()
        return str((base / raw).resolve())

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="project_file",
                type="string",
                description="Path to the design project file. The tool loads the updated diagrams from it (before is captured by the framework).",
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
        project_file = self._resolve_project_file(
            parameters.get("project_file", "") or self.project_file
        )
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
            metadata = {
                "diagrams": after,
                "changed_diagrams": changed_diagrams(after, before),
                "original_diagrams": before,
            }
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
            metadata = {
                "diagrams": diagrams,
                "changed_diagrams": changed_diagrams(diagrams, original),
                "original_diagrams": original,
            }

        req = self.manager.submit(
            review_type="uml_diff",
            title=title,
            content=content,
            question="Please review the UML changes and accept or reject.",
            metadata=metadata,
        )

        # 阻塞前先推审核事件（ProgressRelay → WebSocket），
        # 否则工具不返回，前端永远收不到推送。
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
            # 通知前端审核已超时失效（Agent 将继续自行推进）
            if self.progress is not None:
                self.progress.emit({
                    "event": "review_timeout",
                    "review_id": req.id,
                    "review_type": "uml_diff",
                    "title": title,
                    "timeout": self.timeout,
                })
        return result
