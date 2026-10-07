"""Assemble all declared contributions without selecting plugin identities."""
from app.agent_base.host_api.assembly import AssemblyRequest
from app.agent_base.host_api.lifecycle import HookContext, HookEvent


async def assemble_extensions(context, *, settings, inputs, interface_id="assembly.bind"):
    from app.agent_base.core.hooks import get_hooks, HookRegistry
    from app.agent_base.core.plugins import get_plugin_manager
    from app.agent_base.core.lifecycle import discover_plan, install_plan
    from app.agent_base.core.extension_context import extension_scope
    registry = context._registry or get_hooks()
    manager = get_plugin_manager()
    manager.configure(settings)
    if not registry.plan_id:
        registry = HookRegistry()
        plan = discover_plan(manager, settings)
        failed = [row["name"] for row in plan.plugins if row["status"] == "unavailable"
                  and any(str(item.get("interface_id", "")).startswith("assembly.")
                          for item in manager.get_spec(row["name"]).contributions)]
        if failed:
            raise RuntimeError(f"plugin assembly declarations are unavailable: {', '.join(failed)}")
        install_plan(plan, registry)
    effective = manager.effective_settings(settings)
    for spec in manager.specs:
        provider = str(getattr(effective, spec.provider_setting, spec.default_provider) or spec.default_provider)
        if not getattr(effective, spec.enabled_setting, spec.default_enabled) or provider.lower() in {"none", "noop", "disabled"}:
            continue
        required = [item["id"] for item in spec.contributions
                    if item.get("interface_id") == interface_id]
        if any(not registry.has_contribution(identifier, interface_id=interface_id) for identifier in required):
            raise RuntimeError(f"plugin assembly binding is unavailable: {spec.name}")
    request = AssemblyRequest(inputs, lambda name, provider, **kwargs:
        context.bind(name, provider, settings=settings, **kwargs))
    request.interface_id = interface_id
    context.metadata["execution_settings"] = settings
    with extension_scope(context):
        await registry.ainvoke(HookContext(HookEvent.INITIALIZE, "DevAgent", invocation=request,
            payload={"interface_id": request.interface_id}))
    request.validate()
    for slot, identifiers in request.required_bindings.items():
        if any(not registry.has_contribution(identifier, interface_id=slot) for identifier in identifiers):
            raise RuntimeError(f"required assembly capability is unavailable: {slot}")
        bindings = context.metadata.setdefault("required_execution_bindings", {}).setdefault(slot, [])
        bindings.extend(identifier for identifier in identifiers if identifier not in bindings)
    context._registry = registry
    return request
