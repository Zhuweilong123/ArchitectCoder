"""Execute and normalize one native Function Calling tool batch."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from ...core.hooks import (
    HookAction,
    HookContext,
    HookDecision,
    HookEvent,
    HookRegistry,
    get_hooks,
    get_runtime,
)
from ...evidence import EvidenceLedger, record_runtime_verification
from ...tools.registry import ToolRegistry
from ...tools.result import ToolResult
from ...tools.tool_output import first_tool_output_page, tool_output_page_budget
from app.agent_base.core.observability import current_trace_sink, emit_trace
from app.runtime.tool_outputs import current_tool_output_store
from .failure_recovery import EDIT_REFRESH_CODES, RecoveryScopes


@dataclass
class ToolRoundResult:
    """Structured output consumed by the ReAct loop after a tool batch."""

    tool_results: list[dict] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)


class ToolRoundExecutor:
    """Own tool-call parsing, execution, evidence, and tool lifecycle Hooks."""

    _EDIT_RECOVERY_CODES = EDIT_REFRESH_CODES

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
        # A failed edit must be based on a fresh observation.  This state is
        # deliberately local to one task execution, not persisted in session
        # history, so a later user turn starts with a clean recovery boundary.
        self._edit_recovery_paths: set[str] = set()
        self._recovery_scopes = RecoveryScopes(tool_registry)
        self._previous_attempts: dict[tuple[str, str], dict] = {}

    async def execute(self, tool_calls: list[dict], *, step: int) -> ToolRoundResult:
        parsed_calls = self._parse_calls(tool_calls)

        async def execute_one(
            tool_call: dict,
            tool_name: str,
            tool_args: dict | str,
            blocked: str | None,
        ):
            # Record the call at the point the tool actually starts.  The
            # previous implementation waited for ReActProgress and recorded
            # tool_call/tool_result afterwards, which made a blocking review
            # appear before its submit_uml_review tool_call in the trace.
            span_id = emit_trace(
                "tool_call",
                step=step,
                tool_name=tool_name,
                arguments=tool_args if isinstance(tool_args, dict) else {},
                tool_call_id=str(tool_call.get("id") or ""),
            ) or ""
            execution = await self._execute_one(tool_name, tool_args, blocked,
                                                event_id=str(tool_call.get("id") or span_id))
            return execution, span_id

        executable = [item for item in parsed_calls if item[3] is None]
        parallel = len(executable) > 1 and all(
            self.tool_registry.can_parallel(item[1]) for item in executable
        )
        if parallel:
            executions = await asyncio.gather(*(
                execute_one(item[0], item[1], item[2], item[3]) for item in parsed_calls
            ))
        else:
            executions = []
            for item in parsed_calls:
                executions.append(await execute_one(item[0], item[1], item[2], item[3]))

        result = ToolRoundResult()
        for item, execution_with_span in zip(parsed_calls, executions):
            tc, tool_name, tool_args, blocked = item
            execution, tool_span = execution_with_span
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

            signature = (tool_name, json.dumps(tool_args, sort_keys=True, ensure_ascii=False))
            previous = self._previous_attempts.get(signature)
            retry_of = None
            if (previous and previous["step"] < step
                    and previous["status"] not in {"success", "completed"}):
                retry_of = previous["call_id"]
            attempt = {
                "call_id": str(tc.get("id") or ""), "step": step,
                "status": tool_result.status, "exact_retry_of": retry_of,
            }
            self._previous_attempts[signature] = attempt
            tool_result.execution_evidence = {
                **(tool_result.execution_evidence or {}), "call_attempt": attempt,
            }
            if retry_of:
                marker = "\n[execution attempt " + json.dumps(attempt, ensure_ascii=False) + "]"
                observation_full += marker
                observation_fed += marker

            page_budget = tool_output_page_budget(tool_name)
            if (page_budget is not None and observation_fed == observation_full
                    and len(observation_full) > page_budget):
                store = current_tool_output_store() or self.tool_registry.output_store
                output_id = store.put(tool_name, observation_full)
                observation_fed = first_tool_output_page(
                    observation_full, output_id, max_chars=page_budget,
                )

            if tool_result.verification is not None:
                check = tool_result.verification
                record_runtime_verification(
                    self.verifications, check, tool_name, tool_args,
                )

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
            emit_trace(
                "tool_result",
                span_id=tool_span,
                tool_name=tool_name,
                observation=observation_full,
                duration_ms=float(duration_ms or 0.0),
                error=(
                    str(tool_result.error_code)
                    if tool_result.status not in {"", "success", "completed"}
                    else ""
                ),
                fed_truncated=observation_full != observation_fed,
                fed_length=len(observation_fed),
                evidence=evidence.to_dict(),
            )
            self._update_edit_recovery_state(tool_name, tool_args, tool_result)
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

    @staticmethod
    def _normalise_path(value: object) -> str:
        return os.path.normcase(os.path.normpath(str(value).replace("/", os.sep)))

    def _paths_for_call(self, tool_name: str, tool_args: object,
                        tool_result: ToolResult | None = None) -> set[str]:
        detail = {"name": tool_name, "arguments": tool_args}
        if tool_result is not None:
            detail.update(status=tool_result.status, observation=tool_result.text)
        return set(self._recovery_scopes.paths(detail))

    @classmethod
    def _paths_overlap(cls, left: str, right: str) -> bool:
        left = cls._normalise_path(left)
        right = cls._normalise_path(right)
        return left == right

    def _update_edit_recovery_state(
        self,
        tool_name: str,
        tool_args: object,
        tool_result: ToolResult,
    ) -> None:
        paths = self._paths_for_call(tool_name, tool_args, tool_result)
        if tool_name == "apply_changes":
            if tool_result.error_code in self._EDIT_RECOVERY_CODES:
                self._edit_recovery_paths.update(paths)
            elif tool_result.status == "success":
                self._edit_recovery_paths = {
                    pending for pending in self._edit_recovery_paths
                    if not any(self._paths_overlap(pending, path) for path in paths)
                }
        elif tool_name in {"read_file", "search_text"} and tool_result.status == "success":
            paths = self._recovery_scopes.fresh_content_paths({
                "name": tool_name, "arguments": tool_args,
                "status": tool_result.status, "observation": tool_result.text,
            })
            self._edit_recovery_paths = {
                pending for pending in self._edit_recovery_paths
                if not any(self._paths_overlap(pending, path) for path in paths)
            }

    def _parse_calls(self, tool_calls: list[dict]) -> list[tuple[dict, str, dict | str, str | None]]:
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
            elif tool_name == "apply_changes":
                edit_paths = self._paths_for_call(tool_name, tool_args)
                stale_paths = sorted(
                    pending for pending in self._edit_recovery_paths
                    if any(self._paths_overlap(pending, path) for path in edit_paths)
                )
                if stale_paths:
                    blocked = (
                        "Recovery required before editing these previously failed targets: "
                        f"{', '.join(stale_paths[:6])}. "
                        "First call read_file or search_text for the exact target, then "
                        "rebuild a fresh, minimal apply_changes request."
                    )
            parsed_calls.append((tool_call, tool_name, tool_args, blocked))
        return parsed_calls

    async def _execute_one(
        self, tool_name, tool_args, blocked, *, event_id="",
    ):
        from ...core.operations import operation_scope
        with operation_scope("tool", run_id=get_runtime().run_id, stage=HookEvent.TOOL_BEFORE.value) as operation:
            try:
                result = await self._execute_one_impl(tool_name, tool_args, blocked, event_id=event_id)
                status = result[2].status
                operation.status = {"success": "completed", "error": "failed"}.get(status, status)
                return result
            except BaseException as exc:
                if operation.stage != HookEvent.TOOL_AFTER.value:
                    from ...core.exceptions import AgentInterrupted
                    from ...host_api.errors import AgentAwaitingReview
                    await self.hooks.aemit(HookEvent.TOOL_AFTER, HookContext(
                        HookEvent.TOOL_AFTER, self.agent_name, run_id=get_runtime().run_id,
                        tool_name=tool_name, payload={"status": "waiting_approval" if isinstance(exc, AgentAwaitingReview) else "cancelled" if isinstance(exc, (asyncio.CancelledError, AgentInterrupted)) else "failed", "observers_only": True},
                    ))
                raise

    async def _execute_one_impl(
        self,
        tool_name: str,
        tool_args: dict | str,
        blocked: str | None,
        *, event_id: str = "",
    ):
        if blocked is not None:
            return await self._after_tool(tool_name, tool_args,
                ToolResult(status="blocked", data=blocked, error_code="POLICY_BLOCKED"), 0.0, event_id=event_id)

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
            return await self._after_tool(tool_name, tool_args,
                ToolResult(status="blocked", data=blocked, error_code="POLICY_BLOCKED"), 0.0, event_id=event_id)

        veto = await self.hooks.atrigger(
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
            return await self._after_tool(tool_name, tool_args,
                ToolResult(status="blocked", data=veto_message, error_code="HOOK_VETO"), 0.0, event_id=event_id)

        from app.agent_base.core.observability import trace_span

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

        return await self._after_tool(tool_name, tool_args, tool_result, duration_ms, event_id=event_id)

    async def _after_tool(self, tool_name, tool_args, tool_result, duration_ms, *, event_id=""):
        runtime = get_runtime()
        observation_full = tool_result.text
        fed = await self.hooks.atrigger(
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
                payload={"result": {"status": tool_result.status, "error_code": tool_result.error_code,
                                    "retryable": tool_result.retryable, **tool_result.effects()},
                         "duration_ms": duration_ms, "event_id": event_id,
                         "trace_id": str(getattr(current_trace_sink(), "trace_id", "") or "")},
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
