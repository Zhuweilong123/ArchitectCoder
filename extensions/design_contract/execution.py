"""Contract decisions and projections owned by the plugin, not the run loop."""
from app.agent_base.host_api.services import get_host_services
from app.agent_base.host_api.contexts import ContractGateContext, ContractFailureAnalysisContext


def _binding():
    session = get_host_services().extension_context()
    return session, session.providers.get("design_contract") if session else None


def _gate_context(request, session):
    settings = session.metadata["execution_settings"]
    requested = request.data.get("design_contract_enabled")
    enabled = bool(getattr(settings, "agent_design_contract_enabled", True))
    if enabled and not getattr(settings, "strict_production", False) and requested is not None:
        enabled = bool(requested)
    request.checkpoint["contract_enabled"] = enabled
    return ContractGateContext(
        workspace_manifest=request.data["workspace_manifest"],
        changed_paths=tuple(request.data["changed_paths"]),
        emit=request.capabilities["emit"], request_review=request.capabilities.get("request_review"),
        run_id=request.run_id, settings=settings, contract_enabled=enabled)


async def check(context):
    session, binding = _binding()
    if binding is None:
        return
    request = context.invocation
    decision = await binding.gate.evaluate(_gate_context(request, session))
    state = session.state("design_contract.execution")
    state["result"] = decision.result
    state["message"] = decision.message
    value = decision.result.to_dict() if decision.result is not None else {
        "status": "skipped", "changed_paths": [], "violations": [],
        "can_commit": bool(decision.allowed), "requires_confirmation": False}
    value.update(allowed=bool(decision.allowed), decision_message=decision.message or "")
    request.checkpoint["contract_check"] = value
    record = request.data.get("record_event")
    if record:
        record("contract_check", phase="pre_commit", **value)
    if not decision.allowed:
        request.allowed = False
        request.message = decision.message or "设计契约校验阻止提交，变更已回滚。"
        request.stop_reason = "contract_check_failed"
        request.recovery_event = {"event": "contract_recovery_available", "action": "修复设计契约并继续"}


async def rejected(context):
    session, binding = _binding()
    if binding is None:
        return
    request = context.invocation
    state = session.state("design_contract.execution")
    if state.get("result") is not None:
        request.message = await binding.analyzer.analyze(ContractFailureAnalysisContext(
            invoke=request.capabilities["analyze"], result=state["result"],
            message=state["message"], run_id=request.run_id,
            rollback_completed=request.data["rollback_completed"],
            allowed_tools=tuple(request.allowed_tools) if request.allowed_tools is not None else None))
    request.checkpoint["contract_failure_analysis"] = request.message


async def committed(context):
    session, binding = _binding()
    if binding is None:
        return
    request = context.invocation
    result = session.state("design_contract.execution").get("result")
    if result is None:
        return
    finalizer = getattr(binding.gate, "finalize", None)
    outcome = await finalizer(_gate_context(request, session), result) if callable(finalizer) else None
    value = {"status": outcome.status if outcome else "not_requested",
             "graph_status": outcome.graph_status if outcome else "not_requested",
             "check_id": outcome.check_id if outcome else ""}
    request.checkpoint["contract_graph_sync"] = value
    record = request.data.get("record_event")
    if record:
        record("contract_graph_sync", phase="post_commit", **value)
