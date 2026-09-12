"""Read-only model analysis for design-contract gate failures.

The execution coordinator depends only on this port.  Formatting the gate
evidence, injecting it into conversation history, and invoking a read-only
analysis turn remain replaceable domain behavior.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .contract_harness import ContractCheckResult
from .message import Message

logger = logging.getLogger(__name__)

_MAX_VIOLATIONS = 20


@dataclass(frozen=True)
class ContractFailureAnalysisContext:
    agent: Any
    result: ContractCheckResult
    message: str = ""
    run_id: str = ""
    rollback_completed: bool = True
    allowed_tools: tuple[str, ...] | None = None


class ContractFailureAnalyzerPort(Protocol):
    async def analyze(self, context: ContractFailureAnalysisContext) -> str: ...


def build_contract_failure_report(
    result: ContractCheckResult,
    *,
    rollback_completed: bool = True,
) -> str:
    """Build a bounded, factual report suitable for internal model context."""
    lines = [
        "[DESIGN_CONTRACT_GATE_RESULT]",
        f"status: {result.status}",
        f"can_commit: {str(result.can_commit).lower()}",
        f"rollback: {'completed' if rollback_completed else 'not_completed'}",
        f"check_id: {result.check_id}",
        f"project_id: {result.project_id}",
        f"message: {result.message}",
        "changed_paths:",
    ]
    lines.extend(f"- {path}" for path in result.changed_paths[:32])
    lines.append("violations:")
    for item in result.violations[:_MAX_VIOLATIONS]:
        lines.append(
            "- code={code}; severity={severity}; design_entity={design}; "
            "source_entity={source}; path={path}; message={message}".format(
                code=item.code,
                severity=item.severity,
                design=item.design_entity_id,
                source=item.source_entity_id,
                path=item.path,
                message=item.message,
            )
        )
    if len(result.violations) > _MAX_VIOLATIONS:
        lines.append(
            f"- additional violations omitted: "
            f"{len(result.violations) - _MAX_VIOLATIONS}"
        )
    lines.append("[/DESIGN_CONTRACT_GATE_RESULT]")
    return "\n".join(lines)


class NoOpContractFailureAnalyzer:
    async def analyze(self, context: ContractFailureAnalysisContext) -> str:
        return context.message or "设计契约校验阻止提交，变更已回滚。"


class ModelContractFailureAnalyzer:
    """Generate one explanatory note while preserving the tool-schema prefix."""

    async def analyze(self, context: ContractFailureAnalysisContext) -> str:
        report = build_contract_failure_report(
            context.result,
            rollback_completed=context.rollback_completed,
        )
        agent = context.agent
        if hasattr(agent, "add_message"):
            agent.add_message(Message(
                report,
                "summary",
                metadata={"kind": "contract_gate", "run_id": context.run_id},
            ))
        elif hasattr(agent, "append_task_summary"):
            agent.append_task_summary(report)

        prompt = (
            "The previous coding attempt was rejected by an authoritative design "
            "contract gate and has already been rolled back. Read the internal "
            "gate report in the conversation history and write a concise Chinese "
            "failure analysis note with: 结论、根因、影响、修复建议. "
            "Do not call tools, edit files, retry the task, commit changes, or "
            "claim that the change succeeded. Treat the gate report as factual."
        )
        try:
            analysis = await self._invoke_analysis_model(agent, prompt, context)
        except Exception:
            logger.warning("[ContractGate] failure analysis turn failed", exc_info=True)
            analysis = ""
        return str(analysis or "").strip() or (
            context.message or "设计契约校验阻止提交，变更已回滚。"
        )

    async def _invoke_analysis_model(
        self,
        agent: Any,
        prompt: str,
        context: ContractFailureAnalysisContext,
    ) -> str:
        """Invoke the normal tool schema in read-only analysis mode.

        Sending an empty tool list changes the provider request prefix.  The
        analysis turn therefore keeps the normal tool schema and uses the same
        ``tool_choice=auto`` profile as ordinary interaction.  This analyzer
        does not enter the agent's tool execution loop: any tool calls returned
        by this one-shot request are logged and ignored.  A small fallback is
        retained for lightweight test doubles and custom Agent adapters.
        """
        if not all(hasattr(agent, name) for name in (
            "llm", "tool_registry", "context_budget", "_build_fc_system_prompt",
        )):
            return await agent.arun(prompt, allowed_tools=[])

        tools = (
            agent.tool_registry.get_openai_specs_for(context.allowed_tools)
            if context.allowed_tools is not None
            else agent.tool_registry.get_openai_specs()
        )
        built = agent.context_budget.build_messages(
            agent._build_fc_system_prompt(),
            list(getattr(agent, "_history", ())),
            prompt,
            history_summary=getattr(agent, "_history_summary", ""),
            tools=tools,
        )
        response = await asyncio.wait_for(
            agent.llm.ainvoke_with_tools(
                messages=built.messages,
                tools=tools,
                tool_choice="auto",
                trace_context={
                    "kind": "contract_failure_analysis",
                    "run_id": context.run_id,
                },
            ),
            timeout=float(getattr(agent, "llm_timeout_seconds", 120.0) or 120.0),
        )
        if isinstance(response, dict):
            if response.get("tool_calls"):
                logger.warning("[ContractGate] analysis model returned unexpected tool calls")
            analysis = str(response.get("content") or "")
        else:
            analysis = str(getattr(response, "content", "") or "")
        if analysis and hasattr(agent, "_record_turn"):
            agent._record_turn(prompt, analysis)
        return analysis


def load_contract_failure_analyzer(*, settings=None) -> ContractFailureAnalyzerPort:
    """Return the replaceable analyzer used by the Agent assembly."""
    if settings is not None and not getattr(settings, "agent_design_contract_enabled", True):
        return NoOpContractFailureAnalyzer()
    return ModelContractFailureAnalyzer()


__all__ = [
    "ContractFailureAnalysisContext",
    "ContractFailureAnalyzerPort",
    "ModelContractFailureAnalyzer",
    "NoOpContractFailureAnalyzer",
    "build_contract_failure_report",
    "load_contract_failure_analyzer",
]
