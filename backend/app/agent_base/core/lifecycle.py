"""Explicit plugin contributions and deterministic lifecycle execution plans."""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import html
import tempfile
from functools import wraps
from dataclasses import asdict, dataclass
from pathlib import Path

from .hooks import HookEvent, HookRegistry, PUBLIC_STAGES, NOTIFICATIONS
from .plugin_dispatch import SERVICE_STAGES, service_contributions

# Only these existing boundaries consume mutations/control decisions.
# Remaining lifecycle phases are observation-only until their contracts expand.
PHASE_MODES = {stage: {"observer"} for stage in HookEvent}
PHASE_MODES[HookEvent.LLM_BEFORE] |= {"transform", "control"}
PHASE_MODES[HookEvent.LLM_AFTER] |= {"transform"}
PHASE_MODES[HookEvent.TOOL_BEFORE] |= {"control"}
PHASE_MODES[HookEvent.TOOL_AFTER] |= {"transform"}
PHASE_MODES[HookEvent.TOOL_BATCH_AFTER] |= {"control", "transform"}
for stage in SERVICE_STAGES:
    PHASE_MODES[stage] |= {"service"}


@dataclass(frozen=True)
class Contribution:
    id: str
    stage: HookEvent
    handler: str
    mode: str = "observer"
    priority: int = 0
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    scope: str = "run"
    fail_closed: bool = False
    interface_id: str = ""

    def resolve(self):
        module, separator, attribute = self.handler.partition(":")
        if not separator or not module or not attribute:
            raise ValueError("handler must use module:callable syntax")
        handler = getattr(importlib.import_module(module), attribute)
        if not callable(handler):
            raise ValueError("lifecycle handlers must be callables")
        inspect.signature(handler).bind(object())
        return handler


def core_contributions():
    from .hooks import default_hook_bindings
    items = []
    for stage, handler, priority, mode, identifier in default_hook_bindings():
        attribute = getattr(handler, "__name__", "_run_policy_hook")
        items.append(Contribution(identifier, stage, f"app.agent_base.core.hooks:{attribute}",
                                  mode, priority))
    return tuple(items)


class OrderingError(ValueError):
    def __init__(self, message, ids):
        super().__init__(message)
        self.ids = set(ids)


def _ordered(items):
    by_id = {item.id: item for _, item in items}
    if len(by_id) != len(items):
        raise ValueError("duplicate contribution IDs")
    edges = {key: set() for key in by_id}
    for _, item in items:
        for dependency in (*item.before, *item.after):
            if dependency not in by_id:
                raise OrderingError(f"unknown dependency {dependency} for {item.id}", [item.id])
            if by_id[dependency].stage != item.stage:
                raise OrderingError("ordering dependencies must belong to the same stage", [item.id])
        for target in item.before:
            edges[target].add(item.id)
        edges[item.id].update(item.after)
    pending = {item.id: (plugin, item) for plugin, item in items}
    result = []
    while pending:
        ready = [pair for key, pair in pending.items() if not edges[key] & pending.keys()]
        if not ready:
            raise OrderingError("contribution ordering cycle or blocked dependency", pending)
        pair = min(ready, key=lambda pair: (-pair[1].priority, pair[1].id))
        result.append(pair)
        del pending[pair[1].id]
    return tuple(result)


@dataclass(frozen=True)
class ExecutionPlan:
    plugins: tuple[dict, ...]
    contributions: tuple[tuple[str, Contribution], ...]

    def as_dict(self):
        stages = []
        notifications = []
        for stage in (*PUBLIC_STAGES, *NOTIFICATIONS):
            items = [{**asdict(item), "stage": stage.value, "plugin": plugin}
                     for plugin, item in self.contributions if item.stage == stage]
            for order, item in enumerate(items, 1):
                item["order"] = order
            (stages if stage in PUBLIC_STAGES else notifications).append({"stage": stage.value, "supported_modes": sorted(PHASE_MODES[stage]),
                           "contributions": items})
        data = {"schema_version": 1, "dispatch": "sequential sync/async; observers survive control short-circuit; services execute only on matching requests",
                "plugins": list(self.plugins), "stages": stages, "notifications": notifications,
                "run_end_semantics": "execution interval closed; approval and background completion are separate"}
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return {"plan_id": digest, **data}

    def mermaid(self, view="schedule"):
        if view not in {"schedule", "organization"}:
            raise ValueError("view must be schedule or organization")
        label = lambda value: json.dumps(html.escape(str(value), quote=True), ensure_ascii=False)
        lines = ["flowchart TD", f"  %% plan_id: {self.as_dict()['plan_id']}"]
        for stage in PUBLIC_STAGES:
            lines.append(f"  {stage.name}[{label(stage.value)}]")
        if view == "schedule":
            lines += ["  INITIALIZE --> PREPARE --> RUN_START --> ROUND_BEFORE --> MODEL_BEFORE --> MODEL[Model call] --> MODEL_AFTER",
                      "  MODEL_AFTER -->|tools| TOOL_BATCH_BEFORE -->|per tool| TOOL_BEFORE --> TOOL[Tool execution] --> TOOL_AFTER",
                      "  TOOL_BEFORE -->|blocked| TOOL_AFTER", "  TOOL_BATCH_BEFORE -->|invalid or disallowed| TOOL_AFTER",
                      "  TOOL_AFTER -->|batch joined| TOOL_BATCH_AFTER --> ROUND_AFTER",
                      "  MODEL_AFTER -->|no tools| ROUND_AFTER", "  ROUND_AFTER -->|continue| ROUND_BEFORE",
                      "  ROUND_AFTER -->|finish| FINALIZE --> RUN_END"]
        plugins = {row["name"]: f"P{index}" for index, row in enumerate(self.plugins)}
        plugins["core"] = "PCORE"
        if view == "organization":
            for name, node in plugins.items():
                status = next((row["status"] for row in self.plugins if row["name"] == name), "active")
                lines.append(f"  {node}[{label(name + ': ' + status)}]")
        previous = {}
        for index, (plugin, item) in enumerate(self.contributions):
            if item.stage not in PUBLIC_STAGES:
                continue
            node = f"C{index}"
            lines.append(f"  {node}[{label(item.id + ' / ' + item.mode)}]")
            if view == "organization":
                lines.append(f"  {plugins[plugin]} --> {node} --> {item.stage.name}")
            else:
                lines.append(f"  {item.stage.name} -.-> {node}")
                if item.stage in previous and item.mode != "service":
                    lines.append(f"  {previous[item.stage]} -. order .-> {node}")
                if item.mode != "service":
                    previous[item.stage] = node
        if view == "organization":
            for index, row in enumerate(self.plugins):
                for offset, interface in enumerate(row["interfaces"]):
                    if any(binding["method"] == interface for binding in row.get("interface_bindings", ())):
                        continue
                    node = f"I{index}_{offset}"
                    lines.append(f"  {node}[{label(interface + ' / on demand')}]")
                    lines.append(f"  {plugins[row['name']]} -.-> {node}")
        return "\n".join(lines) + "\n"

    def write(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        files = {"plugin-plan.json": json.dumps(self.as_dict(), ensure_ascii=False, indent=2) + "\n",
                 "plugin-schedule.mmd": self.mermaid(),
                 "plugin-organization.mmd": self.mermaid("organization")}
        history = directory / "history"
        history.mkdir(exist_ok=True)
        files[f"history/{self.as_dict()['plan_id']}.json"] = files["plugin-plan.json"]
        for name, content in files.items():
            target = directory / name
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                             prefix=target.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            try:
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)


def discover_plan(manager, settings) -> ExecutionPlan:
    """Read explicit declarations without constructing storage/LLM providers."""
    manager.configure(settings)
    settings = manager.effective_settings(settings)
    dependency_errors = manager.dependency_errors(settings)
    rows = []
    items = [("core", item) for item in core_contributions()]
    manager._ensure_extension_import_path()
    for spec in manager.specs:
        provider = str(getattr(settings, spec.provider_setting, spec.default_provider) or spec.default_provider).strip()
        row = {"name": spec.name, "provider": provider, "source": spec.source, "status": "discovered", "error": "",
                "version": spec.version, "slot": spec.slot or spec.name,
                "revision": spec.revision, "manifest_digest": spec.manifest_digest,
                "implementation_digest": spec.implementation_digest, "diagnostics": [],
                "dependencies": list(spec.dependencies), "optional_dependencies": list(spec.optional_dependencies),
                "config_keys": list(spec.defaults),
                "interfaces": list(dict.fromkeys((*spec.required_methods, *spec.optional_methods))),
                "contributions": [], "interface_bindings": []}
        rows.append(row)
        if not getattr(settings, spec.enabled_setting, spec.default_enabled) or provider.lower() in {"none", "noop", "disabled"}:
            row["status"] = "disabled"
            continue
        if spec.name in dependency_errors:
            row["status"] = "unavailable"
            row["error"] = dependency_errors[spec.name]
            row["diagnostics"] = [manager.diagnostic(spec, "plan", "dependencies", "dependency_unavailable", row["error"], provider)]
            continue
        phase = "import"
        try:
            factory = manager._load_factory(provider)
            phase = "factory_signature"
            if inspect.iscoroutinefunction(factory):
                raise TypeError("plugin factories must be synchronous")
            inspect.signature(factory).bind_partial(settings=settings)
            module = importlib.import_module(provider.partition(":")[0])
            phase = "contributions"
            declaration = (manager._load_factory(spec.contribution_loader) if spec.contribution_loader
                           else getattr(module, "list_contributions", None) if not spec.manifest_owned else None)
            configured = tuple(Contribution(**{**item, "stage": HookEvent(item["stage"]),
                "before": tuple(item.get("before", ())), "after": tuple(item.get("after", ()))}) for item in spec.contributions)
            services = service_contributions(spec)
            declared = (*configured, *(tuple(declaration(settings=settings)) if declaration else ()), *services)
            for item in declared:
                if not isinstance(item, Contribution) or not isinstance(item.stage, HookEvent):
                    raise ValueError("invalid contribution declaration or stage")
                if not item.id or item.mode not in {"observer", "transform", "control", "service"} or item.scope not in {"run", "invocation"}:
                    raise ValueError("invalid contribution ID, mode or scope")
                if item.mode not in PHASE_MODES[item.stage]:
                    raise ValueError(f"{item.mode} is unsupported at {item.stage.value}")
                if not isinstance(item.id, str) or type(item.priority) is not int or type(item.fail_closed) is not bool:
                    raise ValueError("invalid contribution ID, priority or failure policy")
                for dependencies in (item.before, item.after):
                    if not isinstance(dependencies, tuple) or any(not isinstance(key, str) or not key for key in dependencies):
                        raise ValueError("ordering dependencies must be tuples of contribution IDs")
                if item.mode != "control" and item.fail_closed:
                    raise ValueError("fail_closed requires a control contribution")
                item.resolve()
                if item.mode == "service" and not item.interface_id:
                    raise ValueError("service contribution requires an interface ID")
                if item.scope == "invocation" and item.mode != "service":
                    raise ValueError("invocation scope is reserved for service executors")
            candidate = [*items, *((spec.name, item) for item in declared)]
            if len({item.id for _, item in candidate}) != len(candidate):
                raise ValueError("duplicate contribution IDs")
            items = candidate
            row["contributions"] = [item.id for item in declared]
            row["interface_bindings"] = [{"method": item.interface_id.split(".", 1)[1], "stage": item.stage.value,
                                           "contribution_id": item.id} for item in services]
        except Exception as exc:
            row["status"] = "unavailable"
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["diagnostics"] = [manager.diagnostic(spec, "plan", phase, f"{phase}_failed", row["error"], provider)]
    while True:
        # A declared dependency can also fail during handler/provider import.
        unavailable = {row["name"] for row in rows if row["status"] != "discovered"}
        dependent_failures = {spec.name: next((dependency for dependency in spec.dependencies if dependency in unavailable), "")
                              for spec in manager.specs}
        rejected = set()
        for row in rows:
            dependency = dependent_failures[row["name"]]
            if row["status"] == "discovered" and dependency:
                row.update(status="unavailable", error=f"required plugin dependency is unavailable: {dependency}",
                           contributions=[], interface_bindings=[])
                row["diagnostics"] = [manager.diagnostic(manager.get_spec(row["name"]), "plan", "dependencies", "dependency_unavailable", row["error"], row["provider"])]
                rejected.add(row["name"])
        if rejected:
            items = [(plugin, item) for plugin, item in items if plugin not in rejected]
            continue
        try:
            ordered = _ordered(items)
            break
        except OrderingError as exc:
            rejected = {plugin for plugin, item in items if item.id in exc.ids and plugin != "core"}
            if not rejected:
                raise
            for row in rows:
                if row["name"] in rejected:
                    row["status"] = "unavailable"
                    row["error"] = str(exc)
                    row["contributions"] = []
                    row["interface_bindings"] = []
                    row["diagnostics"] = [manager.diagnostic(manager.get_spec(row["name"]), "plan", "ordering", "ordering_failed", row["error"], row["provider"])]
            items = [(plugin, item) for plugin, item in items if plugin not in rejected]
    manager._plan_errors = {row["name"]: row["error"] for row in rows if row["status"] == "unavailable"}
    return ExecutionPlan(tuple(rows), ordered)


def build_plan(manager, settings):
    manager.configure(settings)
    return discover_plan(manager, settings)


def install_plan(plan: ExecutionPlan, registry: HookRegistry) -> None:
    """Replace managed bindings idempotently, preserving manually registered hooks."""
    # Resolve everything before mutating the active registry.
    resolved = [(plugin, item, item.resolve()) for plugin, item in plan.contributions]
    versions = {row["name"]: row for row in plan.plugins}
    registry.clear_managed()
    for order, (plugin, item, handler) in enumerate(resolved):
        @wraps(handler)
        def binding(context, handler=handler):
            return handler(context)
        registry.register(item.stage, binding, priority=len(plan.contributions) - order,
                          fail_closed=item.fail_closed, contribution_id=item.id,
                          plugin=plugin, mode=item.mode, interface_id=item.interface_id,
                          plugin_version=versions.get(plugin, {}).get("version", ""),
                          plugin_revision=versions.get(plugin, {}).get("revision", ""))
    registry.plan_id = plan.as_dict()["plan_id"]
