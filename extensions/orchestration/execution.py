"""Orchestration policy attached to the generic execution preparation slot."""
from app.agent_base.host_api.services import get_host_services
from app.agent_base.host_api.orchestration import OrchestrationRequest


async def prepare(context):
    host = get_host_services()
    session = host.extension_context()
    provider = session.providers.get("orchestration") if session else None
    if provider is None:
        return
    request = context.invocation
    data = request.data
    previous = data.get("previous_checkpoint", {})
    settings = session.metadata["execution_settings"]
    enabled = bool(getattr(settings, "agent_orchestration_enabled", False)
                   and getattr(settings, "agent_knowledge_graph_enabled", False)
                   and previous.get("architecture_scheduling_mode") is not False)
    request.checkpoint.update({
        "architecture_schedule_root": previous.get("architecture_schedule_root") or previous.get("run_id") or request.run_id,
        "architecture_scheduling_mode": enabled, "architecture_schedule_version": 2,
    })
    tools = tuple(data.get("available_tools", ()))
    if enabled:
        result = await provider.prepare(OrchestrationRequest(
            user_message=data["user_message"], project_file=data["project_file"],
            source_dir=data["source_dir"], test_dir=data["test_dir"],
            previous_checkpoint=previous, available_tools=tools, run_id=request.run_id))
        excluded = result.excluded_tools
        ready = result.metadata.get("architecture_scheduling") == "demand_driven_ready"
        request.context = "\n\n".join(filter(None, [request.context, result.context]))
        data["phase"] = result.phase
        record = data.get("record_event")
        if record:
            record("orchestration_preparation", **result.metadata, phase=result.phase)
    else:
        ready = False
        excluded = ("route_architecture", "explore_architecture")
    host.runtime().policy_metadata["architecture_scheduling_enabled"] = ready
    host.runtime().policy_metadata["architecture_schedule_root"] = request.checkpoint["architecture_schedule_root"]
    if excluded:
        request.allowed_tools = [name for name in tools if name not in excluded
                                 and (request.allowed_tools is None or name in request.allowed_tools)]
