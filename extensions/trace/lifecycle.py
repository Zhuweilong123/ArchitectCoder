"""Observe lifecycle boundaries without changing execution decisions."""

def observe(context):
    from app.trace.tracing import current_trace_sink
    tracer = current_trace_sink()
    if tracer is not None:
        runtime = context.runtime
        tracer.event("lifecycle_stage", stage=context.event.value,
                          agent_name=context.agent_name, run_id=context.run_id,
                          plan_id=runtime.plugin_plan_id if runtime else context.payload.get("plugin_plan_id", ""),
                          tool_name=context.tool_name, tool_status=context.tool_status,
                          error_code=context.error_code,
                          stage_data={key: value for key, value in context.payload.items() if key in {
                              "step", "terminal", "status", "error_type", "source", "stop_reason", "tool_count",
                          }})
