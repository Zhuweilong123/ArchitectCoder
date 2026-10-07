"""Design-contract gate and warning review policy owned by the plugin."""

from __future__ import annotations
import asyncio
import json
import uuid
from typing import Any, Callable
from app.agent_base.host_api.contract_checks import ContractCheckResult
from .contract_harness import ContractHarness
from app.agent_base.host_api.contexts import ReviewPrompt, ContractGateContext, ContractGateDecision


class DefaultContractGate:
    """Default implementation backed by the facts-first contract harness."""

    review_timeout_seconds = 300.0

    def __init__(self, language_runner: Callable[..., Any] | None = None, collector=None) -> None:
        # Inject the compiler/AST boundary at composition time.  The gate
        # should not inspect Agent tools or their implementation details.
        self._language_runner = language_runner
        self._collector = collector

    async def evaluate(self, context: ContractGateContext) -> ContractGateDecision:
        if not context.changed_paths:
            return ContractGateDecision(allowed=True)

        if not context.contract_enabled:
            changed = tuple(
                str(item.get("path", "")) if isinstance(item, dict) else str(item)
                for item in context.changed_paths
            )
            result = ContractCheckResult(
                check_id=f"disabled-{uuid.uuid4().hex[:12]}",
                status="not_applicable",
                project_id="",
                changed_paths=tuple(path for path in changed if path),
                message="design contract disabled for this run",
                metadata={
                    "reason": "disabled_by_request",
                    "contract_enabled": False,
                },
            )
            await context.emit({
                "event": "contract_check",
                **result.to_dict(),
                "run_id": context.run_id,
                "contract_enabled": False,
            })
            return ContractGateDecision(allowed=True, result=result)

        changed = context.changed_paths
        workspace_values = context.workspace_manifest
        result = await asyncio.to_thread(
            ContractHarness().check,
            workspace_values,
            project_id="",
            changed_paths=changed,
            settings=context.settings,
            language_runner=self._language_runner,
            contract_provider=self._collector,
            index_graph=False,
        )
        payload = result.to_dict()
        payload["run_id"] = context.run_id
        payload["contract_enabled"] = context.contract_enabled
        if not await context.emit({"event": "contract_check", **payload}):
            return ContractGateDecision(
                allowed=False,
                result=result,
                message="契约校验结果无法送达前端，变更未提交",
            )

        if result.status in {"pass", "not_applicable"}:
            return ContractGateDecision(allowed=True, result=result)
        if result.status in {"block", "inconclusive"}:
            return ContractGateDecision(
                allowed=False,
                result=result,
                message=result.message or "设计契约校验未通过，变更未提交",
            )
        if context.request_review is None:
            # Non-interactive transports observe warnings but do not block.
            return ContractGateDecision(allowed=True, result=result)

        title = "设计契约校验需要确认"
        prompt = ReviewPrompt(
            review_type="design_contract", title=title, content=_review_content(result),
            question="检测到契约警告，是否仍然提交本次代码变更？", timeout_seconds=self.review_timeout_seconds,
            metadata={"check_id": result.check_id, "status": result.status,
                      "violations": [item.to_dict() for item in result.violations[:50]]},
        )
        try:
            raw = await context.request_review(prompt)
        except asyncio.TimeoutError:
            return ContractGateDecision(
                allowed=False,
                result=result,
                message="契约确认超时，变更未提交",
            )
        try:
            parsed = json.loads(raw)
            decision = parsed.get("decision", "") if isinstance(parsed, dict) else ""
        except (TypeError, ValueError):
            decision = ""
        if decision == "accept":
            return ContractGateDecision(allowed=True, result=result)
        return ContractGateDecision(
            allowed=False,
            result=result,
            message="用户未确认设计契约警告，变更未提交",
        )

    async def finalize(
        self,
        context: ContractGateContext,
        prior_result: ContractCheckResult | None = None,
    ) -> ContractCheckResult | None:
        """Persist the accepted facts only after the candidate was committed."""
        if prior_result is None or not context.changed_paths or not context.contract_enabled:
            return None
        changed = context.changed_paths
        workspace_values = context.workspace_manifest
        return await asyncio.to_thread(
            ContractHarness().check,
            workspace_values,
            project_id="",
            changed_paths=changed,
            settings=context.settings,
            language_runner=self._language_runner,
            contract_provider=self._collector,
            index_graph=True,
        )


def _review_content(result: ContractCheckResult) -> str:
    lines = [result.message or "设计契约校验需要确认", f"状态: {result.status}"]
    if result.changed_paths:
        lines.append("变更文件:")
        lines.extend(f"- {path}" for path in result.changed_paths[:20])
    if result.violations:
        lines.append("契约问题:")
        for item in result.violations[:20]:
            prefix = "错误" if item.severity == "error" else "警告"
            location = f" [{item.path}]" if item.path else ""
            lines.append(f"- {prefix}: {item.message}{location}")
        if len(result.violations) > 20:
            lines.append(f"- ... 其余 {len(result.violations) - 20} 项已省略")
    return "\n".join(lines)
