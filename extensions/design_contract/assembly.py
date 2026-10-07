"""Bind contract policy and declare the required transaction check."""
from types import SimpleNamespace
from app.agent_base.host_api.services import get_host_services
from app.agent_base.host_api.contexts import ContractGateDecision
from app.agent_base.host_api.contract_checks import ContractCheckResult
from .analysis import ModelContractFailureAnalyzer


class UnavailableGate:
    async def evaluate(self, context):
        if not context.contract_enabled or not context.changed_paths:
            return ContractGateDecision(allowed=True)
        result = ContractCheckResult(check_id=f"unavailable-{context.run_id}", status="inconclusive",
            project_id="", message="configured contract gate capability is unavailable",
            metadata={"reason": "missing_evaluate_capability"})
        await context.emit({"event": "contract_check", **result.to_dict(), "run_id": context.run_id,
                            "contract_enabled": context.contract_enabled})
        return ContractGateDecision(allowed=False, result=result, message=result.message)


def bind(context):
    request = context.invocation
    host = get_host_services()
    settings = host.extension_context().metadata["execution_settings"]
    provider = host.resolve_provider("design_contract", settings=settings,
        language_runner=request.inputs.get("language_runner"))
    gate = provider if callable(getattr(provider, "evaluate", None)) else UnavailableGate()
    analyzer = provider if callable(getattr(provider, "analyze", None)) else ModelContractFailureAnalyzer()
    request.bind("design_contract", SimpleNamespace(gate=gate, analyzer=analyzer))
    request.required_bindings.setdefault("execution.check", []).append("design_contract.execution.check")
