"""DevSystem — Agent 驱动的代码开发编排层

将 CodeValidator、CodeFixer、TestGenerator 等组件组装为完整的
"设计 → 生成 → 验证 → 测试 → 修复" 开发流水线。

每个阶段 yield 与现有前端兼容的 progress event。
支持暂停/恢复和中断信号。

Usage::

    from app.agent_base.tools.my_tools.dev_system import DevSystem

    llm = BaseAgentsLLM.from_settings()
    dev = DevSystem(llm, max_rounds=5, max_fix_iterations=3)

    async for event in dev.run(
        code_files={"app.py": "def add(a,b): return a-b"},
        task_description="Generate and validate a calculator app",
        source_dir="/path/to/project",
        test_dir="/path/to/tests",
    ):
        if event["event"] == "request_review":
            # 推送到前端，等待人工审核
            review_id = event["data"]["id"]
            response = await wait_for_human_review()
            dev.resolve_review(review_id, response)
        elif event["event"] == "stopped":
            break
        else:
            # 渲染进度
            print(f"Stage {event['stage']}: {event['status']}")
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Callable, Optional

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.review import ReviewManager, RequestReviewTool
from app.agent_base.tools.my_tools.code_validator import CodeValidator
from app.agent_base.tools.my_tools.code_fixer import CodeFixer

logger = logging.getLogger(__name__)


# ── 阶段枚举 ──────────────────────────────────────────

class DevPhase:
    """开发阶段常量 — 与现有 Pipeline StageName 对齐"""

    UML_OPTIMIZE = "uml_optimize"
    CODE_GEN = "code_gen"
    CODE_VALIDATE = "code_validate"
    TEST_GEN = "test_gen"
    TEST_VALIDATE = "test_validate"
    CODE_FIX = "code_fix"
    DONE = "done"


# ── DevSystem ──────────────────────────────────────────

class DevSystem:
    """Agent 驱动的代码开发编排系统。

    流程：
    1. CodeValidator — 验证生成的代码，修复语法/导入/运行时错误
    2. RequestReview — Agent 自主请求人工审核（可选）
    3. CodeFixer — pytest 驱动的源码修复
    4. 循环直到通过或人工确认
    """

    def __init__(
        self,
        llm: BaseAgentsLLM,
        max_rounds: int = 5,
        max_fix_iterations: int = 3,
        change_ratio: int = 0,
        design_constraints: dict | None = None,
        source_dir: str = "",
        test_dir: str = "",
        enable_review: bool = True,
    ):
        self.llm = llm
        self.max_rounds = max_rounds
        self.max_fix_iterations = max_fix_iterations
        self.change_ratio = change_ratio
        self.design_constraints = design_constraints
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.enable_review = enable_review

        # 审核管理器
        self.review_mgr = ReviewManager()

        # 组件（延迟创建）
        self._validator: CodeValidator | None = None
        self._fixer: CodeFixer | None = None
        self._stop_flag = False

    # ── 公共 API ──────────────────────────────────────

    async def run(
        self,
        code_files: dict[str, str],
        task_description: str = "",
        should_stop: Callable[[], bool] | None = None,
    ) -> AsyncIterator[dict]:
        """运行完整的开发流水线。

        Args:
            code_files: 初始代码 {filename: content}
            task_description: 任务描述
            should_stop: 可选中断回调

        Yields:
            兼容现有 Pipeline 的 progress dict:
            - {"event": "stage_update", "stage": "...", "status": "...", ...}
            - {"event": "request_review", "stage": "...", "data": {...}}
            - {"event": "done", "result": {...}}
            - {"event": "stopped", "reason": "..."}
        """
        self._stop_flag = False
        current_code = dict(code_files)
        test_code: dict[str, str] = {}
        stop_fn = should_stop or (lambda: False)

        # ═══════════════════════════════════════════════
        # Phase 1: Code Validation
        # ═══════════════════════════════════════════════
        yield self._stage_event(DevPhase.CODE_VALIDATE, "running",
                                "ReAct validating generated code...")

        validator = self._get_validator()
        validation_result = None

        async for progress in validator.validate_stream(
            code_files=current_code,
            task_description=task_description or "Validate and fix code errors",
        ):
            if stop_fn():
                self._stop_flag = True
                yield {"event": "stopped", "reason": "User requested stop"}
                return

            if "result" in progress:
                validation_result = progress["result"]
            else:
                yield self._stage_event(
                    DevPhase.CODE_VALIDATE, "running",
                    f"ReAct round {progress['round']}/{self.max_rounds}...",
                    extra={"react_steps": progress.get("react_steps", [])},
                )

        if validation_result and validation_result.get("success"):
            if validation_result.get("final_code"):
                current_code = validation_result["final_code"]
            yield self._stage_event(
                DevPhase.CODE_VALIDATE, "success",
                f"Validated: {len(current_code)} files OK",
                extra={"files": list(current_code.keys()),
                       "react_summary": validation_result.get("summary", "")},
            )
        else:
            yield self._stage_event(
                DevPhase.CODE_VALIDATE, "failed",
                (validation_result or {}).get("summary", "Validation incomplete"),
            )
            if not self.enable_review:
                return

        if stop_fn():
            self._stop_flag = True
            yield {"event": "stopped", "reason": "User requested stop"}
            return

        # ═══════════════════════════════════════════════
        # Phase 2: Test Execution + Fix（如果有测试）
        # ═══════════════════════════════════════════════
        if test_code:
            yield self._stage_event(
                DevPhase.CODE_FIX, "running",
                "Running tests and fixing code...",
            )

            fixer = self._get_fixer()
            fix_result = await fixer.fix(
                source_code=current_code,
                test_code=test_code,
                task="Fix bugs so all tests pass",
            )

            if fix_result["success"]:
                current_code = fix_result["final_source"]
                yield self._stage_event(
                    DevPhase.CODE_FIX, "success",
                    f"All tests passing: {fix_result['pass_rate']}",
                    extra={
                        "test_output": fix_result["test_output"],
                        "pass_rate": fix_result["pass_rate"],
                    },
                )
            else:
                yield self._stage_event(
                    DevPhase.CODE_FIX, "failed",
                    f"Tests: {fix_result['pass_rate']} passing",
                    extra={"test_output": fix_result["test_output"]},
                )

        # ═══════════════════════════════════════════════
        # Done
        # ═══════════════════════════════════════════════
        yield {
            "event": "done",
            "result": {
                "final_code": current_code,
                "test_code": test_code,
                "files": list(current_code.keys()),
            },
        }

    def stop(self):
        """请求中断当前运行。"""
        self._stop_flag = True

    def request_review(self, review_type: str, title: str,
                       content: str, question: str) -> str:
        """手动发起审核请求（编排层调用）。

        Returns:
            审核请求的 ID 字符串，用于后续 resolve。
        """
        req = self.review_mgr.submit(review_type, title, content, question)
        return str(len(self.review_mgr._pending) - 1)

    def resolve_review(self, request_id: int, response: str) -> bool:
        """解决人工审核请求。"""
        return self.review_mgr.resolve(request_id, response)

    def reject_review(self, request_id: int, reason: str = "") -> bool:
        """拒绝审核请求。"""
        return self.review_mgr.reject(request_id, reason or "Rejected")

    def has_pending_review(self) -> bool:
        """是否有等待中的审核。"""
        return self.review_mgr.has_pending()

    def get_pending_reviews(self) -> list[dict]:
        """获取等待中的审核列表。"""
        return self.review_mgr.get_pending()

    # ── Internal ──────────────────────────────────────

    def _get_validator(self) -> CodeValidator:
        if self._validator is None:
            self._validator = CodeValidator(
                llm=self.llm,
                max_rounds=self.max_rounds,
                change_ratio=self.change_ratio,
                design_constraints=self.design_constraints,
                generated_dir=self.source_dir,
            )
        return self._validator

    def _get_fixer(self) -> CodeFixer:
        if self._fixer is None:
            self._fixer = CodeFixer(
                llm=self.llm,
                max_iterations=self.max_fix_iterations,
                source_dir=self.source_dir,
                test_dir=self.test_dir,
            )
        return self._fixer

    @staticmethod
    def _stage_event(
        stage: str, status: str, logs: str = "",
        extra: dict | None = None,
    ) -> dict:
        """构建与现有前端兼容的 stage_update 事件。"""
        event: dict = {
            "event": "stage_update",
            "stage": stage,
            "status": status,
            "logs": logs,
        }
        if extra:
            event.update(extra)
        return event
