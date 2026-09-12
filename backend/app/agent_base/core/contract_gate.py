"""Stable contract-gate port used by the Agent execution lifecycle."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from app.runtime.workspace import WorkspaceManifest

from .contract_harness import ContractCheckResult, ContractHarness


@dataclass(frozen=True)
class ContractGateContext:
    """Transport-neutral context supplied by one Agent run."""

    agent: Any
    change_set: Any
    review_manager: Any
    emit: Callable[[dict], Awaitable[bool]]
    emit_review: Callable[[dict], Awaitable[None]]
    project_file: str = ""
    source_dir: str = ""
    test_dir: str = ""
    run_id: str = ""
    settings: Any = None
    contract_enabled: bool = True


@dataclass(frozen=True)
class ContractGateDecision:
    allowed: bool
    result: ContractCheckResult | None = None
    message: str = ""


class ContractGatePort(Protocol):
    async def evaluate(self, context: ContractGateContext) -> ContractGateDecision: ...

    async def finalize(
        self,
        context: ContractGateContext,
        prior_result: ContractCheckResult | None = None,
    ) -> ContractCheckResult | None: ...


class NoOpContractGate:
    async def evaluate(self, context: ContractGateContext) -> ContractGateDecision:
        return ContractGateDecision(allowed=True)

    async def finalize(
        self,
        context: ContractGateContext,
        prior_result: ContractCheckResult | None = None,
    ) -> ContractCheckResult | None:
        return None


class DefaultContractGate:
    """Default implementation backed by the facts-first contract harness."""

    review_timeout_seconds = 300.0

    async def evaluate(self, context: ContractGateContext) -> ContractGateDecision:
        change_set = context.change_set
        if change_set is None or not change_set.has_changes:
            return ContractGateDecision(allowed=True)

        if not context.contract_enabled:
            changed = tuple(
                str(item.get("path", "")) if isinstance(item, dict) else str(item)
                for item in change_set.manifest()
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

        changed = change_set.manifest()
        workspace_values = getattr(context.agent, "workspace_manifest", {}) or {}
        if not workspace_values:
            workspace_values = WorkspaceManifest.from_paths(
                project_file=context.project_file,
                source_dir=context.source_dir,
                test_dir=context.test_dir,
            ).to_dict()
        result = await asyncio.to_thread(
            ContractHarness().check,
            workspace_values,
            project_id="",
            changed_paths=changed,
            settings=context.settings,
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
        if context.review_manager is None:
            # Non-interactive transports observe warnings but do not block.
            return ContractGateDecision(allowed=True, result=result)

        title = "设计契约校验需要确认"
        request = context.review_manager.submit(
            review_type="design_contract",
            title=title,
            content=_review_content(result),
            question="检测到契约警告，是否仍然提交本次代码变更？",
            metadata={
                "check_id": result.check_id,
                "status": result.status,
                "violations": [item.to_dict() for item in result.violations[:50]],
            },
        )
        await context.emit_review({
            "event": "review",
            "review_id": request.id,
            "review_type": "design_contract",
            "title": title,
            "content": request.content,
            "question": request.question,
            "metadata": request.metadata,
        })
        try:
            raw = await asyncio.wait_for(
                request.future,
                timeout=self.review_timeout_seconds,
            )
        except asyncio.TimeoutError:
            await context.emit_review({
                "event": "review_timeout",
                "review_id": request.id,
                "review_type": "design_contract",
                "title": title,
                "timeout": self.review_timeout_seconds,
            })
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
        if prior_result is None or context.change_set is None or not context.contract_enabled:
            return None
        changed = context.change_set.manifest()
        workspace_values = getattr(context.agent, "workspace_manifest", {}) or {}
        if not workspace_values:
            workspace_values = WorkspaceManifest.from_paths(
                project_file=context.project_file,
                source_dir=context.source_dir,
                test_dir=context.test_dir,
            ).to_dict()
        return await asyncio.to_thread(
            ContractHarness().check,
            workspace_values,
            project_id="",
            changed_paths=changed,
            settings=context.settings,
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


def load_contract_gate(*, settings=None) -> ContractGatePort:
    """Load the configured gate without exposing implementation details."""
    if settings is not None and not getattr(settings, "agent_design_contract_enabled", True):
        return NoOpContractGate()
    return DefaultContractGate()


def resolve_contract_enabled(requested: bool | None, settings: Any = None) -> bool:
    """Resolve a per-run request without mutating process-wide settings.

    The server setting is an upper bound.  Strict production mode deliberately
    ignores a client-side disable request.
    """
    configured = bool(getattr(settings, "agent_design_contract_enabled", True))
    if not configured or getattr(settings, "strict_production", False):
        return configured
    return configured if requested is None else bool(requested)


__all__ = [
    "ContractGateContext",
    "ContractGateDecision",
    "ContractGatePort",
    "DefaultContractGate",
    "NoOpContractGate",
    "load_contract_gate",
    "resolve_contract_enabled",
]
