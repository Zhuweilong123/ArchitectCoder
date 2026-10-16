"""Public access to deployment values, project identities and host paths."""
from collections.abc import Mapping
from .configuration import PluginConfiguration
from .services import get_environment_services, ProjectStoragePort, WorkspacePathsPort


def configuration() -> PluginConfiguration:
    return get_environment_services().configuration()


def plugin_config(plugin: str) -> Mapping[str, object]:
    """Detached plugin-owned configuration; callers do not need host Settings."""
    return get_environment_services().plugin_config(plugin)


def project_storage(project_file: str = '', *, workspace_root: str = '', create: bool = True) -> ProjectStoragePort | None:
    return get_environment_services().project_storage(project_file, workspace_root=workspace_root, create=create)


def project_id(project_file: str = '', *, workspace_root: str = '') -> str:
    return get_environment_services().project_id(project_file, workspace_root=workspace_root)


def workspace_paths(workspace_root: str = '', source_dir: str = '', test_dir: str = '', design_dir: str = '') -> WorkspacePathsPort:
    return get_environment_services().workspace_paths(workspace_root, source_dir, test_dir, design_dir)


def decode_output(value: bytes) -> str:
    return get_environment_services().decode_output(value)
