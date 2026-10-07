"""Capture one skill catalog for the main agent and all child tool schemas."""
from app.agent_base.host_api.services import get_host_services
from app.agent_base.host_api.contexts import SkillContext


def catalog(context):
    request = context.invocation
    host = get_host_services()
    provider = host.resolve_provider("skills", settings=host.extension_context().metadata["execution_settings"])
    if provider is not None:
        request.bind("skills", provider)
        request.values["skill_catalog"] = request.inputs["capture_catalog"](
            provider, context=SkillContext(workspace_root=request.inputs["workspace_root"]))
