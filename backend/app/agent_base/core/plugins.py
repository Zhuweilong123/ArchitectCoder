"""Central lifecycle manager for application extension providers.

The manager standardizes discovery and failure handling while each domain
keeps its own protocol (memory, tracing, evaluations, and so on).  Extension
entry points live in the repository-level ``extensions`` package and use the
``module:factory`` convention.
"""

from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class PluginSpec:
    """Static metadata describing one managed provider slot."""

    name: str
    enabled_setting: str
    provider_setting: str
    default_provider: str
    required_methods: tuple[str, ...]


@dataclass(frozen=True)
class PluginState:
    """Last known state of a managed plugin slot."""

    name: str
    provider: str
    status: str
    error: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "provider": self.provider,
            "status": self.status,
            "error": self.error,
        }


DEFAULT_PLUGIN_SPECS: tuple[PluginSpec, ...] = (
    PluginSpec(
        name="orchestration",
        enabled_setting="agent_orchestration_enabled",
        provider_setting="agent_orchestrator_provider",
        default_provider="extensions.orchestration:create",
        required_methods=("prepare",),
    ),
    PluginSpec(
        name="memory",
        enabled_setting="agent_memory_enabled",
        provider_setting="agent_memory_provider",
        default_provider="extensions.memory:create",
        required_methods=("recall", "archive", "reinforce"),
    ),
    PluginSpec(
        name="trace",
        enabled_setting="agent_trace_enabled",
        provider_setting="agent_trace_provider",
        default_provider="extensions.trace:create",
        required_methods=("create",),
    ),
    PluginSpec(
        name="evals",
        enabled_setting="agent_evals_enabled",
        provider_setting="agent_evals_provider",
        default_provider="extensions.evals:create",
        required_methods=("list_cases", "get_case", "run_case", "list_results"),
    ),
    PluginSpec(
        name="knowledge_graph",
        enabled_setting="agent_knowledge_graph_enabled",
        provider_setting="agent_knowledge_graph_provider",
        default_provider="extensions.knowledge_graph:create",
        required_methods=(
            "rebuild_project",
            "search_diagrams",
            "map_project",
            "locate",
            "expand",
            "impact",
            "diff",
        ),
    ),
)


class PluginManager:
    """Register, load, inspect and safely disable extension providers."""

    def __init__(self, specs: tuple[PluginSpec, ...] = DEFAULT_PLUGIN_SPECS):
        self._specs = {spec.name: spec for spec in specs}
        self._states: dict[str, PluginState] = {}

    @property
    def specs(self) -> tuple[PluginSpec, ...]:
        return tuple(self._specs.values())

    def register(self, spec: PluginSpec) -> None:
        self._specs[spec.name] = spec

    def _ensure_extension_import_path(self) -> None:
        project_root = str(_PROJECT_ROOT)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

    @staticmethod
    def _load_factory(provider: str):
        module_name, separator, attribute = provider.partition(":")
        if not separator or not module_name or not attribute:
            raise ValueError("plugin provider must use 'module:factory' syntax")
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
        if not callable(factory):
            raise TypeError(f"plugin provider is not callable: {provider}")
        return factory

    def _state(self, name: str, provider: str, status: str, error: str = "") -> None:
        self._states[name] = PluginState(name, provider, status, error)

    def load(
        self,
        name: str,
        *,
        settings=None,
        kwargs: Mapping[str, Any] | None = None,
        factory_loader=None,
    ) -> Any | None:
        """Load one provider, returning ``None`` for disabled/unavailable slots."""
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError(f"unknown plugin: {name}")

        if settings is None:
            try:
                from app.core.config import get_settings

                settings = get_settings()
            except Exception:
                settings = None

        enabled = True if settings is None else getattr(settings, spec.enabled_setting, True)
        provider = str(
            getattr(settings, spec.provider_setting, spec.default_provider)
            or spec.default_provider
        ).strip()
        if not enabled or provider.lower() in {"", "none", "noop", "disabled"}:
            self._state(name, provider, "disabled")
            return None

        try:
            self._ensure_extension_import_path()
            factory = (factory_loader or self._load_factory)(provider)
            factory_kwargs = dict(kwargs or {})
            instance = factory(settings=settings, **factory_kwargs)
            missing = [
                method for method in spec.required_methods
                if not callable(getattr(instance, method, None))
            ]
            if missing:
                raise TypeError(
                    f"plugin '{name}' is missing required methods: {', '.join(missing)}"
                )
            self._state(name, provider, "loaded")
            return instance
        except Exception as exc:
            self._state(name, provider, "unavailable", str(exc))
            logger.warning(
                "[Plugins] %s provider unavailable; using domain fallback",
                name,
                exc_info=True,
            )
            return None

    def status(self) -> list[dict[str, str]]:
        """Return states for all registered plugins, including not-yet-loaded ones."""
        return [
            self._states.get(
                spec.name,
                PluginState(spec.name, spec.default_provider, "not_loaded"),
            ).as_dict()
            for spec in self._specs.values()
        ]


_default_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = PluginManager()
    return _default_manager


__all__ = [
    "DEFAULT_PLUGIN_SPECS",
    "PluginManager",
    "PluginSpec",
    "PluginState",
    "get_plugin_manager",
]
