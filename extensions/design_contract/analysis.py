"""Failure report and model prompt policy owned by the design-contract plugin."""

from __future__ import annotations
import logging
from app.agent_base.host_api.contract_checks import ContractCheckResult
from app.agent_base.host_api.contexts import AnalysisRequest, ContractFailureAnalysisContext


logger = logging.getLogger(__name__)
_MAX_VIOLATIONS = 20

def build_contract_failure_report(
    result: ContractCheckResult,
    *,
    rollback_completed: bool | None = True,
) -> str:
    """Build a bounded, factual report suitable for internal model context."""
    lines = [
        "[DESIGN_CONTRACT_GATE_RESULT]",
        f"status: {result.status}",
        f"can_commit: {str(result.can_commit).lower()}",
        f"rollback: {'not_required' if rollback_completed is None else 'completed' if rollback_completed else 'not_completed'}",
        f"check_id: {result.check_id}",
        f"project_id: {result.project_id}",
        f"message: {result.message}",
        "changed_paths:",
    ]
    lines.extend(f"- {path}" for path in result.changed_paths[:32])
    errors = sum(item.severity == "error" for item in result.violations)
    lines.append(f"violation_counts: errors={errors}; other={len(result.violations) - errors}")
    lines.append("violations (errors first):")
    ordered = sorted(result.violations, key=lambda item: item.severity != "error")
    for item in ordered[:_MAX_VIOLATIONS]:
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
        from collections import Counter
        omitted = Counter((item.severity, item.code) for item in ordered[_MAX_VIOLATIONS:])
        lines.extend(f"- omitted severity={severity}; code={code}; count={count}"
                     for (severity, code), count in omitted.items())
    lines.append("[/DESIGN_CONTRACT_GATE_RESULT]")
    return "\n".join(lines)


class ModelContractFailureAnalyzer:
    """Generate one explanatory note while preserving the tool-schema prefix."""

    async def analyze(self, context: ContractFailureAnalysisContext) -> str:
        report = build_contract_failure_report(
            context.result,
            rollback_completed=context.rollback_completed,
        )
        prompt = (
            "The previous coding attempt was rejected by an authoritative design "
            "contract gate. Read the internal "
            "gate report in the conversation history and write a concise Chinese "
            "failure analysis note with: 结论、根因、影响、修复建议. "
            "Do not call tools, edit files, retry the task, commit changes, or "
            "claim that the change succeeded. Treat the gate report as factual. "
            "Explain the blocking errors before warnings. Do not infer that omitted "
            "violations are warnings. Describe rollback using the report: not_required "
            "means there were no changes to roll back; not_completed does not mean completed."
        )
        try:
            analysis = await context.invoke(AnalysisRequest(
                prompt=prompt, evidence=report, run_id=context.run_id, allowed_tools=context.allowed_tools,
                metadata={"kind": "contract_failure_analysis"},
            ))
        except Exception:
            logger.warning("[ContractGate] failure analysis turn failed", exc_info=True)
            analysis = ""
        return str(analysis or "").strip() or (
            context.message or "设计契约校验未通过，请查看门禁报告。"
        )
