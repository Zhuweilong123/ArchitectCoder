"""Bind the scheduler and contribute its tools through the generic assembly slot."""
from app.agent_base.host_api.services import get_host_services
from .provider import UnavailableArchitectureScheduler


def bind(context):
    request = context.invocation
    host = get_host_services()
    settings = host.extension_context().metadata["execution_settings"]
    provider = host.resolve_provider("orchestration", settings=settings, **request.inputs)
    provider = provider or UnavailableArchitectureScheduler("configured provider is unavailable")
    request.bind("orchestration", provider)
    create_tools = getattr(provider, "create_tools", None)
    if create_tools is not None and request.inputs.get("project_file"):
        request.tools.extend(create_tools() or ())
