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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from backend.config.plugin_catalog import (
    BUILTIN_PLUGIN_ROOT, config_path, scan_manifests, packaged_manifests,
    deployment_overrides, resolved_settings,
)

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
    router_provider: str = ""
    default_enabled: bool = True
    source: str = "builtin"
    interface_stages: tuple[tuple[str, str], ...] = ()
    optional_methods: tuple[str, ...] = ()
    async_methods: tuple[str, ...] = ()
    wrapped_results: tuple[str, ...] = ()
    version: str = ""
    slot: str = ""
    dependencies: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    contribution_loader: str = ""
    contributions: tuple[dict, ...] = ()
    defaults: dict = field(default_factory=dict)
    settings_prefix: str = ""
    manifest_owned: bool = False


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


def manifest_spec(data):
    interfaces = data.get("interfaces", {})
    aliases = data.get("settings", {})
    return PluginSpec(
        name=data["id"], enabled_setting=aliases.get("enabled", f"plugin_{data['id']}_enabled"),
        provider_setting=aliases.get("provider", f"plugin_{data['id']}_provider"),
        default_provider=data["provider"],
        required_methods=tuple(name for name, item in interfaces.items() if item.get("required", True)),
        optional_methods=tuple(name for name, item in interfaces.items() if not item.get("required", True)),
        interface_stages=tuple((name, item.get("stage", "run_start")) for name, item in interfaces.items()),
        async_methods=tuple(name for name, item in interfaces.items() if item.get("async", False)),
        wrapped_results=tuple(name for name, item in interfaces.items() if item.get("wrap_result", False)),
        router_provider=data.get("router", ""), default_enabled=data.get("enabled_by_default", True),
        source=data["source"], version=data["version"], slot=data.get("slot", data["id"]),
        dependencies=tuple(data.get("dependencies", ())),
        optional_dependencies=tuple(data.get("optional_dependencies", ())),
        contribution_loader=data.get("contribution_loader", ""),
        contributions=tuple(data.get("contributions", ())),
        defaults=data.get("defaults", {}), settings_prefix=aliases.get("prefix", f"plugin_{data['id']}_"),
        manifest_owned=True,
    )


# Compatibility snapshot generated from plugin-owned files.
DEFAULT_PLUGIN_SPECS = tuple(manifest_spec(data) for data in packaged_manifests())


class PluginManager:
    """Register, load, inspect and safely disable extension providers."""

    def __init__(self, specs: tuple[PluginSpec, ...] | None = None):
        self._auto_scan = specs is None
        self._manual_specs = {spec.name: spec for spec in (specs or ())}
        specs = DEFAULT_PLUGIN_SPECS if specs is None else specs
        self._specs = {spec.name: spec for spec in specs}
        self._states: dict[str, PluginState] = {}
        self._configuration = None
        self._overrides = {}
        self._plan_errors = {}

    @property
    def specs(self) -> tuple[PluginSpec, ...]:
        return tuple(self._specs.values())

    def register(self, spec: PluginSpec) -> None:
        self._specs[spec.name] = spec
        self._manual_specs[spec.name] = spec
        self._configuration = None

    def get_spec(self, name):
        spec = self._specs.get(name)
        if spec is None:
            spec = next((item for item in self.specs if item.slot == name), None)
        if spec is None:
            raise KeyError(f"unknown plugin: {name}")
        return spec

    def discover_directories(self, roots):
        candidates = {item["id"]: manifest_spec(item) for item in scan_manifests(roots)}
        combined = dict(self._specs)
        for name, spec in candidates.items():
            if name in combined and combined[name] != spec:
                raise ValueError(f"duplicate or reserved plugin name: {name}")
            combined[name] = spec
        self._validate_slots(combined)
        self._specs = combined

    @staticmethod
    def _validate_slots(specs):
        slots = {}
        for spec in specs.values():
            slot = spec.slot or spec.name
            if slot == "core" or slot in slots or slot in specs and slot != spec.name:
                raise ValueError(f"duplicate or conflicting plugin slot: {slot}")
            slots[slot] = spec.name

    def configure(self, settings):
        roots = tuple(getattr(settings, "plugin_roots", ()))
        manifest = getattr(settings, "plugin_manifest_file", "")
        config = getattr(settings, "plugin_config_file", "")
        if not self._auto_scan and not any((roots, manifest, config)):
            return
        signature = (roots, manifest, config)
        if signature == self._configuration:
            return
        # Build a candidate so a failed scan/config never partially mutates us.
        candidate = PluginManager(tuple(self._manual_specs.values()))
        if self._auto_scan or roots:
            candidate.discover_directories((BUILTIN_PLUGIN_ROOT, *roots) if self._auto_scan else roots)
        if manifest:
            candidate.discover_specs(config_path(manifest))
        overrides = deployment_overrides(config, set(candidate._specs))
        candidate._overrides = overrides
        candidate.effective_settings(settings)
        self._specs = candidate._specs
        self._overrides = overrides
        self._configuration = signature
        self._plan_errors = {}

    def effective_settings(self, settings):
        declarations = ({"id": spec.name, "provider": spec.default_provider,
            "enabled_by_default": spec.default_enabled, "defaults": spec.defaults,
            "settings": {"enabled": spec.enabled_setting, "provider": spec.provider_setting,
                         "prefix": spec.settings_prefix or f"plugin_{spec.name}_"}} for spec in self.specs)
        return resolved_settings(settings, declarations, self._overrides)

    def dependency_errors(self, settings):
        errors, visiting, visited = {}, [], set()
        def enabled(spec):
            return getattr(settings, spec.enabled_setting, spec.default_enabled) and str(
                getattr(settings, spec.provider_setting, spec.default_provider)).lower() not in {"none", "noop", "disabled"}
        def visit(name):
            if name in visiting:
                for member in visiting[visiting.index(name):]:
                    errors[member] = "plugin dependency cycle: " + " -> ".join((*visiting, name))
                return
            if name in visited:
                return
            visiting.append(name)
            for dependency in self._specs[name].dependencies:
                target = self._specs.get(dependency)
                if target is None or not enabled(target):
                    errors[name] = f"required plugin dependency is missing or disabled: {dependency}"
                else:
                    visit(dependency)
                    if dependency in errors:
                        errors.setdefault(name, f"required plugin dependency is unavailable: {dependency}")
            visiting.pop()
            visited.add(name)
        for spec in self.specs:
            if enabled(spec):
                visit(spec.name)
        return errors

    def discover_specs(self, manifest_path) -> None:
        """Compatibility: read the previous explicit additional-plugin list."""
        from backend.config.plugin_catalog import legacy_manifests
        from .hooks import HookEvent, PUBLIC_STAGES
        from dataclasses import replace
        combined = dict(self._specs)
        for data in legacy_manifests(manifest_path):
            spec = manifest_spec(data)
            spec = replace(spec, manifest_owned=False,
                interface_stages=tuple((method, HookEvent(stage).value)
                                       for method, stage in spec.interface_stages))
            if any(HookEvent(stage) not in PUBLIC_STAGES for _, stage in spec.interface_stages):
                raise ValueError("interfaces must bind to public phases")
            if spec.name in combined and combined[spec.name] != spec:
                raise ValueError(f"duplicate or reserved plugin name: {spec.name}")
            combined[spec.name] = spec
        self._validate_slots(combined)
        self._specs = combined

    def _ensure_extension_import_path(self) -> None:
        roots = {_PROJECT_ROOT, *(Path(spec.source).parent.parent for spec in self.specs if spec.manifest_owned)}
        for root in sorted(roots, key=str):
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))

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
        if settings is None:
            try:
                from backend.config import get_settings

                settings = get_settings()
            except Exception:
                settings = None

        self.configure(settings)
        settings = self.effective_settings(settings)
        spec = self.get_spec(name)

        enabled = spec.default_enabled if settings is None else getattr(settings, spec.enabled_setting, spec.default_enabled)
        provider = str(
            getattr(settings, spec.provider_setting, spec.default_provider)
            or spec.default_provider
        ).strip()
        if not enabled or provider.lower() in {"", "none", "noop", "disabled"}:
            self._state(spec.name, provider, "disabled")
            return None

        error = self._plan_errors.get(spec.name) or self.dependency_errors(settings).get(spec.name)
        if error:
            self._state(spec.name, provider, "unavailable", error)
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
            self._state(spec.name, provider, "loaded")
            from .plugin_dispatch import ScheduledProvider
            return ScheduledProvider(instance, spec)
        except Exception as exc:
            self._state(spec.name, provider, "unavailable", str(exc))
            logger.warning(
                "[Plugins] %s provider unavailable; using domain fallback",
                name,
                exc_info=True,
            )
            return None

    def load_contribution(
        self,
        name: str,
        method: str,
        *,
        settings=None,
        kwargs: Mapping[str, Any] | None = None,
        default=None,
    ):
        """Call an optional provider contribution without domain-specific host code."""
        instance = self.load_optional(name, settings=settings, kwargs=kwargs)
        if instance is None:
            return default if default is not None else []
        contributor = getattr(instance, method, None)
        if not callable(contributor):
            return default if default is not None else []
        try:
            result = contributor(**dict(kwargs or {}))
            return result if result is not None else (default if default is not None else [])
        except Exception:
            logger.warning("[Plugins] %s contribution %s failed", name, method, exc_info=True)
            return default if default is not None else []

    def load_optional(self, name, **kwargs):
        """Domain ports retain their NoOp behavior when a plugin is absent."""
        try:
            return self.load(name, **kwargs)
        except KeyError:
            return None

    def load_router(self, name: str, *, settings=None):
        """Load an optional plugin-owned FastAPI router without loading its provider."""
        if settings is None:
            try:
                from backend.config import get_settings

                settings = get_settings()
            except Exception:
                settings = None
        self.configure(settings)
        spec = self.get_spec(name)
        if not spec.router_provider:
            return None
        # Keep the route mounted even when the provider is disabled so clients
        # receive the provider's explicit 503 response instead of a route 404.
        try:
            self._ensure_extension_import_path()
            module_name, separator, attribute = spec.router_provider.partition(":")
            if not separator or not module_name or not attribute:
                raise ValueError("plugin router must use 'module:attribute' syntax")
            module = importlib.import_module(module_name)
            router = getattr(module, attribute)
            if router is None:
                raise TypeError(f"plugin router is empty: {spec.router_provider}")
            return router
        except Exception as exc:
            logger.warning("[Plugins] %s router unavailable", name, exc_info=True)
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
