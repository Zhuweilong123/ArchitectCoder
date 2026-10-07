"""Observe lifecycle boundaries without changing execution decisions."""

def observe(context):
    from app.agent_base.host_api.lifecycle import PUBLIC_STAGES
    from app.agent_base.host_api.services import get_host_services
    host = get_host_services()
    runtime = context.runtime
    operation = host.current_operation()
    host.record_event("lifecycle_stage" if context.event in PUBLIC_STAGES else "runtime_notification", stage=context.event.value,
                      operation_id=operation.operation_id if operation else "",
                      parent_operation_id=operation.parent_operation_id if operation else "",
                      operation_kind=operation.operation_kind if operation else "",
                      scope=operation.scope if operation else "run",
                      agent_name=context.agent_name, run_id=context.run_id,
                      plan_id=runtime.plugin_plan_id if runtime else context.payload.get("plugin_plan_id", ""),
                      tool_name=context.tool_name, tool_status=context.tool_status,
                      error_code=context.error_code,
                      stage_data={key: value for key, value in context.payload.items() if key in {
                          "step", "terminal", "status", "error_type", "source", "stop_reason", "tool_count", "interface_id", "execution_only",
                      }})
