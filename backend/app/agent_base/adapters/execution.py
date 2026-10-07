"""Dispatch host transaction boundaries through the compiled plugin plan."""
from app.agent_base.host_api.execution import ExecutionRequest, ExecutionSlots
from app.agent_base.host_api.lifecycle import HookContext, HookEvent


async def dispatch_execution(request: ExecutionRequest, *, agent_name="Agent"):
    from app.agent_base.core.hooks import get_hooks, get_runtime
    from app.agent_base.core.operations import operation_scope
    from app.agent_base.core.extension_context import current_extension_context
    registry = get_hooks()
    session = current_extension_context()
    required = session.metadata.get("required_execution_bindings", {}).get(request.interface_id, ()) if session else ()
    if any(not registry.has_contribution(identifier, interface_id=request.interface_id) for identifier in required):
        raise RuntimeError(f"required execution capability is unavailable: {request.interface_id}")
    stage = HookEvent.PREPARE if request.interface_id == ExecutionSlots.PREPARE else HookEvent.FINALIZE
    with operation_scope("execution", run_id=request.run_id, stage=stage.value):
        await registry.ainvoke(HookContext(stage, agent_name, run_id=request.run_id,
            runtime=get_runtime(), payload={"interface_id": request.interface_id}, invocation=request))
    return request
