"""Memory binding and recall options are owned by the memory plugin."""
from app.agent_base.host_api.services import get_host_services


def bind(context):
    request = context.invocation
    settings = get_host_services().extension_context().metadata["execution_settings"]
    provider = get_host_services().resolve_provider("memory", settings=settings, **request.inputs)
    if provider is not None:
        request.bind("memory", provider, options={
            "recall_top_k": settings.agent_memory_recall_top_k,
            "recall_max_tokens": settings.agent_memory_recall_max_tokens})
