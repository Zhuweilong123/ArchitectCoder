"""Execute and normalize one native Function Calling tool batch."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ...core.hooks import (
    HookAction,
    HookContext,
    HookDecision,
    HookEvent,
    HookRegistry,
    get_hooks,
    get_runtime,
)
from ...evidence import EvidenceLedger
from ...tools.registry import ToolRegistry
from ...tools.result import ToolResult


@dataclass
class ToolRoundResult:
    """Structured output consumed by the ReAct loop after a tool batch."""

    tool_results: list[dict] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)


class ToolRoundExecutor:
    """Own tool-call parsing, execution, evidence, and tool lifecycle Hooks."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        agent_name: str,
        allowed_tools: Optional[set[str]] = None,
        hooks: Optional[HookRegistry] = None,
        evidence_ledger: Optional[EvidenceLedger] = None,
        evidence_summary: Optional[list[dict]] = None,
        current_history: Optional[list[str]] = None,
        verifications: Optional[dict[tuple[str, str], bool]] = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.agent_name = agent_name
        self.allowed_tools = allowed_tools
        self.hooks = hooks or get_hooks()
        self.evidence_ledger = evidence_ledger or EvidenceLedger()
        self.evidence_summary = evidence_summary if evidence_summary is not None else []
        self.current_history = current_history if current_history is not None else []
        self.verifications = verifications if verifications is not None else {}

    async def execute(self, tool_calls: list[dict], *, step: int) -> ToolRoundResult:
        parsed_calls = self._parse_calls(tool_calls)

        async def execute_one(
            tool_name: str,
            tool_args: dict | str,
            blocked: str | None,
        ):
            return await self._execute_one(tool_name, tool_args, blocked)

        executable = [item for item in parsed_calls if item[3] is None]
        parallel = len(executable) > 1 and all(
            self.tool_registry.can_parallel(item[1]) for item in executable
        )
        if parallel:
            executions = await asyncio.gather(*(
                execute_one(item[1], item[2], item[3]) for item in parsed_calls
            ))
        else:
            executions = []
            for item in parsed_calls:
                executions.append(await execute_one(item[1], item[2], item[3]))

        result = ToolRoundResult()
        for item, execution in zip(parsed_calls, executions):
            tc, tool_name, tool_args, blocked = item
            if blocked is not None and isinstance(tool_args, str):
                observation_full = observation_fed = blocked
                tool_result = ToolResult(
                    status="blocked", data=blocked, error_code="INVALID_ARGUMENTS",
                )
                duration_ms = 0.0
            else:
                observation_full, observation_fed, tool_result, duration_ms = execution
            if isinstance(tool_args, str):
                observation_full = observation_fed = execution[0]

            if tool_result.verification is not None:
                check = tool_result.verification
                self.verifications[(check.kind, check.scope)] = check.passed

            evidence = self.evidence_ledger.record(
                call_id=str(tc.get("id") or ""),
                tool_name=tool_name,
                arguments=tool_args if isinstance(tool_args, dict) else {},
                observation=observation_full,
                status=tool_result.status,
                error_code=tool_result.error_code,
                effects=tool_result.effects(),
            )
            self.evidence_summary.append(evidence.to_dict())
            del self.evidence_summary[:-32]
            self.current_history.append(
                f"Step {step}: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})"
                f" → {observation_fed[:150]}"
            )
            result.tool_results.append({
                "tool_call_id": tc["id"],
                "content": observation_fed,
            })
            result.actions.append(tool_name)
            result.details.append({
                "name": tool_name,
                "arguments": tool_args,
                "observation": observation_full,
                "fed_truncated": observation_full != observation_fed,
                "fed_length": len(observation_fed),
                "status": tool_result.status,
                "error_code": tool_result.error_code,
                "retryable": tool_result.retryable,
                "duration_ms": round(duration_ms, 1),
                "evidence": evidence.to_dict(),
                **tool_result.effects(),
            })

        return result

    def _parse_calls(self, tool_calls: list[dict]) -> list[tuple[dict, str, dict | str, str | None]]:
        runtime = get_runtime()
        budget = runtime.execution_budget
        parsed_calls = []
        for tool_call in tool_calls:
            fn = tool_call["function"]
            tool_name = fn["name"]
            try:
                tool_args = json.loads(fn["arguments"])
            except json.JSONDecodeError:
                err_obs = (
                    f"Invalid JSON arguments for '{tool_name}'. "
                    f"Raw: {fn.get('arguments', '')[:200]}. Please re-send with valid JSON."
                )
                parsed_calls.append((tool_call, tool_name, fn.get("arguments", ""), err_obs))
                continue

            blocked: str | None = None
            if self.allowed_tools is not None and tool_name not in self.allowed_tools:
                blocked = (
                    f"Tool '{tool_name}' is not enabled for this turn. "
                    f"Use one of: {', '.join(sorted(self.allowed_tools)) or '(none)'}"
                )
            elif budget is not None and budget.tool_call_count >= budget.max_tool_calls:
                blocked = (
                    f"Tool-call budget exceeded ({budget.max_tool_calls}). "
                    "Stop calling tools and summarize the result."
                )
            parsed_calls.append((tool_call, tool_name, tool_args, blocked))
        return parsed_calls

    async def _execute_one(
        self,
        tool_name: str,
        tool_args: dict | str,
        blocked: str | None,
    ):
        if blocked is not None:
            return (
                blocked,
                blocked,
                ToolResult(status="blocked", data=blocked, error_code="POLICY_BLOCKED"),
                0.0,
            )

        runtime = get_runtime()
        if (
            runtime.requires_todo_plan
            and not runtime.todos
            and tool_name != "todo_write"
        ):
            blocked = (
                "Task planning is required before other tools. "
                "Call todo_write first with the task checklist."
            )
            return (
                blocked,
                blocked,
                ToolResult(status="blocked", data=blocked, error_code="POLICY_BLOCKED"),
                0.0,
            )

        veto = self.hooks.trigger(
            HookEvent.TOOL_BEFORE,
            HookContext(
                event=HookEvent.TOOL_BEFORE,
                agent_name=self.agent_name,
                run_id=runtime.run_id,
                runtime=runtime,
                tool_name=tool_name,
                tool_input=tool_args,
            ),
        )
        if isinstance(veto, HookDecision):
            if veto.action in {HookAction.VETO, HookAction.STOP, "veto", "stop"}:
                veto = veto.message or veto.reason
            else:
                veto = None
        if veto is not None:
            veto_message = str(veto)
            return (
                veto_message,
                veto_message,
                ToolResult(status="blocked", data=veto_message, error_code="HOOK_VETO"),
                0.0,
            )

        from app.trace.tracing import trace_span

        with trace_span(f"{self.agent_name}/{tool_name}"):
            started_tool = time.monotonic()
            tool_result = await self.tool_registry.aexecute_tool_result_with_params(
                tool_name, tool_args,
            )
        duration_ms = (time.monotonic() - started_tool) * 1000
        try:
            from app.services.agent_metrics import get_agent_metrics
            get_agent_metrics().record_tool(tool_name, tool_result.status, duration_ms)
        except Exception:
            pass

        observation_full = tool_result.text
        fed = self.hooks.trigger(
            HookEvent.TOOL_AFTER,
            HookContext(
                event=HookEvent.TOOL_AFTER,
                agent_name=self.agent_name,
                run_id=runtime.run_id,
                runtime=runtime,
                tool_name=tool_name,
                tool_input=tool_args,
                tool_output=observation_full,
                tool_status=tool_result.status,
                error_code=tool_result.error_code,
            ),
        )
        if isinstance(fed, HookDecision):
            fed = fed.message if fed.action in {HookAction.REPLACE, "replace"} else None
        return (
            observation_full,
            fed if fed is not None else observation_full,
            tool_result,
            duration_ms,
        )


__all__ = ["ToolRoundExecutor", "ToolRoundResult"]
