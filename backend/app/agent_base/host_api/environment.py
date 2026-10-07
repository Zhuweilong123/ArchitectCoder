"""Public access to deployment values, project identities and host paths."""
from .services import get_host_services


def configuration():
    return get_host_services().configuration()


def plugin_config(plugin: str):
    """Detached plugin-owned configuration; callers do not need host Settings."""
    return get_host_services().plugin_config(plugin)


def project_storage(project_file="", **kwargs):
    return get_host_services().project_storage(project_file, **kwargs)


def project_id(project_file="", **kwargs):
    return get_host_services().project_id(project_file, **kwargs)


def workspace_paths(*args, **kwargs):
    return get_host_services().workspace_paths(*args, **kwargs)


def decode_output(value):
    return get_host_services().decode_output(value)
