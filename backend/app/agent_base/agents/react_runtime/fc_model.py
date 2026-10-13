"""Model invocation, timeout retries and lifecycle reporting for native FC."""
from __future__ import annotations

import asyncio
import logging
from typing import Literal, NamedTuple, Protocol, TypedDict, Unpack

from ...host_api.errors import AgentInterrupted
from ...core.hooks import AgentRuntime, get_hooks
from ...host_api.lifecycle import HookAction, HookContext, HookDecision, HookEvent
from ...host_api.agents import ModelPort
from ...host_api.data import ModelMessage, ModelResponse, ToolSchema
from ...host_api.payloads import json_object

logger = logging.getLogger(__name__)


class ModelInvocationAgent(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def llm(self) -> ModelPort: ...
    @property
    def llm_timeout_retries(self) -> int: ...


class ModelRequestContext(TypedDict, total=False):
    current_user_index: int
    scope: str
    estimated_context_tokens: int
    message_count: int
    tool_schema_tokens: int
    tool_schema_mode: str
    tool_schema_stable: bool
    request_budget_target_tokens: int
    request_budget_emergency_tokens: int
    context_soft_threshold_ratio: float
    context_compaction_threshold_ratio: float
    context_hard_limit_tokens: int
    task_total_tokens_observed: int


class ModelInvocationOptions(TypedDict):
    messages: list[ModelMessage]
    tool_specs: list[ToolSchema]
    finalization_mode: bool
    request_context: ModelRequestContext
    runtime: AgentRuntime
    temperature: float
    timeout_seconds: float


class ModelInvocationResult(NamedTuple):
    response: ModelResponse | None
    failure_kind: Literal["", "hook_stop", "timeout"]
    reason: str


async def _invoke_fc_model(agent: ModelInvocationAgent, **kwargs: Unpack[ModelInvocationOptions]) -> ModelInvocationResult:
    from ...core.operations import operation_scope
    runtime = kwargs["runtime"]
    with operation_scope("model", run_id=runtime.run_id, stage=HookEvent.MODEL_BEFORE.value) as operation:
        response = None
        status = "failed"
        try:
            result = await _invoke_fc_model_impl(agent, **kwargs)
            response = result.response
            status = "completed" if response is not None else "blocked" if result.failure_kind == "hook_stop" else "failed"
            operation.status = status
            return result
        except (asyncio.CancelledError, AgentInterrupted):
            status = "cancelled"
            raise
        finally:
            await get_hooks().atrigger(HookEvent.MODEL_AFTER, HookContext(
                HookEvent.MODEL_AFTER, agent.name, run_id=runtime.run_id, runtime=runtime,
                messages=kwargs["messages"], llm_response=response,
                payload={"status": status, "observers_only": status != "completed"},
            ))


async def _invoke_fc_model_impl(
    agent: ModelInvocationAgent,
    *,
    messages: list[ModelMessage],
    tool_specs: list[ToolSchema],
    finalization_mode: bool,
    request_context: ModelRequestContext,
    runtime: AgentRuntime,
    temperature: float,
    timeout_seconds: float,
) -> ModelInvocationResult:
    """Invoke the model inside its hook and tracing boundary.

    Returns ``(response, failure_kind, reason)``. A missing response is an
    intentional terminal condition, either stopped by a hook or timed out.
    """
    before_decision = await get_hooks().atrigger(
        HookEvent.MODEL_BEFORE,
        HookContext(
            event=HookEvent.MODEL_BEFORE,
            agent_name=agent.name,
            run_id=runtime.run_id,
            runtime=runtime,
            messages=messages,
            payload={"current_user_index": request_context.get("current_user_index", -1)},
        ),
    )
    if (
        isinstance(before_decision, HookDecision)
        and before_decision.action == HookAction.STOP
    ):
        return ModelInvocationResult(None, "hook_stop", before_decision.reason)

    from app.agent_base.core.observability import trace_span

    retries = max(0, int(getattr(agent, "llm_timeout_retries", 3)))
    with trace_span(agent.name):
        for attempt in range(retries + 1):
            if runtime.stop_check():
                raise AgentInterrupted("User requested stop")
            try:
                response = await asyncio.wait_for(
                    agent.llm.ainvoke_with_tools(
                        messages=messages,
                        tools=tool_specs,
                        tool_choice="none" if finalization_mode else "auto",
                        temperature=temperature,
                        trace_context={**json_object(request_context), "timeout_seconds": timeout_seconds,
                                       "timeout_attempt": attempt + 1, "timeout_max_attempts": retries + 1},
                    ),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                will_retry = attempt < retries
                await get_hooks().aemit(HookEvent.ERROR, HookContext(
                    event=HookEvent.ERROR, agent_name=agent.name, run_id=runtime.run_id,
                    runtime=runtime, payload={"error_type": "TimeoutError", "source": "llm",
                                              "attempt": attempt + 1, "max_attempts": retries + 1,
                                              "timeout_seconds": timeout_seconds, "will_retry": will_retry},
                ))
                if not will_retry:
                    return ModelInvocationResult(None, "timeout", "llm_timeout")
                logger.warning("[LLM] Request timed out after %.1fs; retry %d/%d",
                               timeout_seconds, attempt + 1, retries)
                continue
            return ModelInvocationResult(response, "", "")

    raise RuntimeError("Model retry loop ended without a result")
