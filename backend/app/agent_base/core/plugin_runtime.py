"""Publish plugin generations while task contexts retain their original generation."""
from __future__ import annotations

import asyncio
import copy
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from weakref import WeakValueDictionary


@dataclass(frozen=True)
class PluginSnapshot:
    manager: object
    registry: object
    settings: object
    plan: object = None


_bound: ContextVar[PluginSnapshot | None] = ContextVar("plugin_snapshot", default=None)
_active: PluginSnapshot | None = None
_known = WeakValueDictionary()


def current_snapshot():
    return _bound.get() or _active


def publish_snapshot(snapshot):
    global _active
    previous, _active = _active, snapshot
    if snapshot is not None and snapshot.registry.plan_id:
        _known[snapshot.registry.plan_id] = snapshot
    return previous


def snapshot_for_plan(plan_id):
    return _known.get(plan_id)


@contextmanager
def plugin_scope(snapshot=None):
    """Nested tasks and asyncio.to_thread inherit the enclosing generation."""
    token = _bound.set(snapshot or current_snapshot())
    try:
        yield _bound.get()
    finally:
        _bound.reset(token)


def pin_plugins(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        with plugin_scope():
            return await function(*args, **kwargs)
    return wrapped


class PluginRefreshService:
    """Add-only refresh. Existing implementations, settings and routes require restart."""

    def __init__(self, snapshot, directory):
        self.active = snapshot
        self.directory = Path(directory)
        self.snapshots = {snapshot.registry.plan_id: snapshot}
        self._lock = asyncio.Lock()
        self._candidate_lock = threading.Lock()
        # Remember attempted imports too: rejected candidates must never reload Python code.
        self._code_baseline = {spec.source: spec.implementation_digest for spec in snapshot.manager.specs}
        self.last_result = None

    def snapshot_for_plan(self, plan_id):
        return self.snapshots.get(plan_id)

    def _candidate(self):
        # An API cancellation can leave its import worker running; serialize those workers too.
        with self._candidate_lock:
            return self._build_candidate()

    def _build_candidate(self):
        from .hooks import HookRegistry
        from .lifecycle import build_plan, install_plan
        from .plugins import PluginManager
        previous = self.active
        original = previous.manager
        manager = PluginManager(None if original._auto_scan else tuple(original._manual_specs.values()))
        if original._auto_scan:
            for spec in original._manual_specs.values():
                manager.register(spec)
        # Only unmanaged host hooks are carried forward; managed contributions are recompiled.
        registry = HookRegistry()
        registry._hooks = {stage: list(items) for stage, items in previous.registry._hooks.items()}
        registry._metadata = copy.deepcopy(previous.registry._metadata)
        registry.clear_managed()
        candidate = PluginSnapshot(manager, registry, previous.settings)
        with plugin_scope(candidate):
            manager.configure(previous.settings)
            effective = manager.effective_settings(previous.settings)
            old_specs = {spec.name: spec for spec in original.specs}
            new_specs = {spec.name: spec for spec in manager.specs}
            diagnostics = []
            for name, spec in old_specs.items():
                if new_specs.get(name) != spec:
                    diagnostics.append({"plugin": name, "code": "restart_required", "message": "Existing plugin declarations or code changed, or the plugin was removed; restart the backend."})
            for spec in manager.specs:
                if spec.source in self._code_baseline and spec.implementation_digest != self._code_baseline[spec.source]:
                    diagnostics.append({"plugin": spec.name, "code": "restart_required", "message": "Previously discovered plugin code changed; restart the backend."})
                if spec.name not in old_specs and spec.router_provider:
                    diagnostics.append({"plugin": spec.name, "code": "restart_required", "message": "New HTTP routes require a backend restart."})
            for spec in original.specs:
                name = spec.name
                if name not in new_specs:
                    continue
                old_settings = previous.settings
                if (getattr(effective, spec.enabled_setting) != getattr(old_settings, spec.enabled_setting)
                    or getattr(effective, spec.provider_setting) != getattr(old_settings, spec.provider_setting)
                    or effective.plugin_configs[name] != old_settings.plugin_configs[name]):
                    diagnostics.append({"plugin": name, "code": "restart_required", "message": "Existing plugin deployment configuration changed; restart the backend."})
            if diagnostics:
                return None, diagnostics, []
            manager._states = dict(original._states)
            manager._diagnostics = copy.deepcopy(original._diagnostics)
            added = sorted(set(new_specs) - set(old_specs))
            for spec in manager.specs:
                self._code_baseline[spec.source] = spec.implementation_digest
            plan = build_plan(manager, effective)
            diagnostics = [item for row in plan.plugins if row["status"] == "unavailable" for item in row["diagnostics"]]
            for name in (*old_specs, "core"):
                old_items = sorted((item for plugin, item in previous.plan.contributions if plugin == name), key=lambda item: item.id)
                new_items = sorted((item for plugin, item in plan.contributions if plugin == name), key=lambda item: item.id)
                if old_items != new_items:
                    diagnostics.append({"plugin": name, "code": "restart_required", "message": "Existing compiled interface contributions changed; restart the backend."})
            if diagnostics:
                return None, diagnostics, added
            install_plan(plan, registry)
            frozen = manager.freeze(effective)
            return PluginSnapshot(frozen, registry, frozen._fixed_settings, plan), [], added

    async def refresh(self):
        async with self._lock:
            previous = self.active
            try:
                candidate, diagnostics, added = await asyncio.to_thread(self._candidate)
                if diagnostics:
                    result = {"status": "rejected", "plan_id": previous.registry.plan_id, "added": [], "diagnostics": diagnostics}
                elif candidate.registry.plan_id == previous.registry.plan_id:
                    result = {"status": "unchanged", "plan_id": previous.registry.plan_id, "added": [], "diagnostics": []}
                else:
                    # Persist before activation; archive failure keeps the active generation intact.
                    await asyncio.to_thread(candidate.plan.write, self.directory)
                    self.snapshots[candidate.registry.plan_id] = candidate
                    self.active = candidate
                    publish_snapshot(candidate)  # One pointer swap; no in-place mutation of old registries.
                    result = {"status": "published", "plan_id": candidate.registry.plan_id,
                              "previous_plan_id": previous.registry.plan_id, "added": added, "diagnostics": []}
            except Exception as exc:
                result = {"status": "rejected", "plan_id": previous.registry.plan_id, "added": [],
                          "diagnostics": [{"plugin": "", "code": "refresh_failed", "message": f"{type(exc).__name__}: {exc}"}]}
            self.last_result = result
            return result
