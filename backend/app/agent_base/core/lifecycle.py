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

from .hooks import HookEvent, HookRegistry

# Only these existing boundaries consume mutations/control decisions.
# Remaining lifecycle phases are observation-only until their contracts expand.
PHASE_MODES = {stage: {"observer"} for stage in HookEvent}
PHASE_MODES[HookEvent.LLM_BEFORE] |= {"transform", "control"}
PHASE_MODES[HookEvent.LLM_AFTER] |= {"transform"}
PHASE_MODES[HookEvent.TOOL_BEFORE] |= {"control"}
PHASE_MODES[HookEvent.TOOL_AFTER] |= {"transform"}
PHASE_MODES[HookEvent.TOOL_BATCH_AFTER] |= {"control", "transform"}


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

    def resolve(self):
        module, separator, attribute = self.handler.partition(":")
        if not separator or not module or not attribute:
            raise ValueError("handler must use module:callable syntax")
        handler = getattr(importlib.import_module(module), attribute)
        if not callable(handler) or inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(
            getattr(handler, "__call__", None)
        ):
            raise ValueError("lifecycle handlers must be synchronous callables")
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
        for stage in HookEvent:
            items = [{**asdict(item), "stage": stage.value, "plugin": plugin}
                     for plugin, item in self.contributions if item.stage == stage]
            for order, item in enumerate(items, 1):
                item["order"] = order
            stages.append({"stage": stage.value, "supported_modes": sorted(PHASE_MODES[stage]),
                           "contributions": items})
        data = {"schema_version": 1, "dispatch": "sequential; observers survive control short-circuit",
                "plugins": list(self.plugins), "stages": stages}
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return {"plan_id": digest, **data}

    def mermaid(self, view="schedule"):
        if view not in {"schedule", "organization"}:
            raise ValueError("view must be schedule or organization")
        label = lambda value: json.dumps(html.escape(str(value), quote=True), ensure_ascii=False)
        lines = ["flowchart TD", f"  %% plan_id: {self.as_dict()['plan_id']}"]
        for stage in HookEvent:
            lines.append(f"  {stage.name}[{label(stage.value)}]")
        if view == "schedule":
            lines += ["  RUN_START --> ROUND_BEFORE --> LLM_BEFORE --> MODEL[Model call] --> LLM_AFTER",
                      "  LLM_AFTER -->|tools| TOOL_BATCH_BEFORE -->|per tool| TOOL_BEFORE --> TOOL[Tool execution] --> TOOL_AFTER",
                      "  TOOL_BEFORE -->|blocked| TOOL_AFTER", "  TOOL_BATCH_BEFORE -->|invalid or disallowed| TOOL_AFTER",
                      "  TOOL_AFTER -->|batch joined| TOOL_BATCH_AFTER --> ROUND_AFTER",
                      "  LLM_AFTER -->|no tools| ROUND_AFTER", "  ROUND_AFTER -->|continue| ROUND_BEFORE",
                      "  ROUND_AFTER -->|finish| RUN_FINALIZE --> RUN_END",
                      "  ERROR --> RUN_END", "  CANCEL --> RUN_END"]
        plugins = {row["name"]: f"P{index}" for index, row in enumerate(self.plugins)}
        plugins["core"] = "PCORE"
        if view == "organization":
            for name, node in plugins.items():
                status = next((row["status"] for row in self.plugins if row["name"] == name), "active")
                lines.append(f"  {node}[{label(name + ': ' + status)}]")
        previous = {}
        for index, (plugin, item) in enumerate(self.contributions):
            node = f"C{index}"
            lines.append(f"  {node}[{label(item.id + ' / ' + item.mode)}]")
            if view == "organization":
                lines.append(f"  {plugins[plugin]} --> {node} --> {item.stage.name}")
            else:
                lines.append(f"  {item.stage.name} -.-> {node}")
                if item.stage in previous:
                    lines.append(f"  {previous[item.stage]} -. order .-> {node}")
                previous[item.stage] = node
        if view == "organization":
            for index, row in enumerate(self.plugins):
                for offset, interface in enumerate(row["interfaces"]):
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
        for name, content in files.items():
            target = directory / name
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            try:
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)


def discover_plan(manager, settings) -> ExecutionPlan:
    """Read explicit declarations without constructing storage/LLM providers."""
    rows = []
    items = [("core", item) for item in core_contributions()]
    manager._ensure_extension_import_path()
    for spec in manager.specs:
        provider = str(getattr(settings, spec.provider_setting, spec.default_provider) or spec.default_provider).strip()
        row = {"name": spec.name, "provider": provider, "source": spec.source, "status": "discovered", "error": "",
               "interfaces": list(spec.required_methods), "contributions": []}
        rows.append(row)
        if not getattr(settings, spec.enabled_setting, spec.default_enabled) or provider.lower() in {"none", "noop", "disabled"}:
            row["status"] = "disabled"
            continue
        try:
            manager._load_factory(provider)
            module = importlib.import_module(provider.partition(":")[0])
            declaration = getattr(module, "list_contributions", None)
            declared = tuple(declaration(settings=settings)) if declaration else ()
            for item in declared:
                if not isinstance(item, Contribution) or not isinstance(item.stage, HookEvent):
                    raise ValueError("invalid contribution declaration or stage")
                if not item.id or item.mode not in {"observer", "transform", "control"} or item.scope != "run":
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
            candidate = [*items, *((spec.name, item) for item in declared)]
            if len({item.id for _, item in candidate}) != len(candidate):
                raise ValueError("duplicate contribution IDs")
            items = candidate
            row["contributions"] = [item.id for item in declared]
        except Exception as exc:
            row["status"] = "unavailable"
            row["error"] = f"{type(exc).__name__}: {exc}"
    while True:
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
            items = [(plugin, item) for plugin, item in items if plugin not in rejected]
    return ExecutionPlan(tuple(rows), ordered)


def build_plan(manager, settings):
    manifest = getattr(settings, "plugin_manifest_file", "")
    if manifest:
        path = Path(manifest)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        manager.discover_specs(path)
    return discover_plan(manager, settings)


def install_plan(plan: ExecutionPlan, registry: HookRegistry) -> None:
    """Replace managed bindings idempotently, preserving manually registered hooks."""
    # Resolve everything before mutating the active registry.
    resolved = [(plugin, item, item.resolve()) for plugin, item in plan.contributions]
    registry.clear_managed()
    for order, (plugin, item, handler) in enumerate(resolved):
        @wraps(handler)
        def binding(context, handler=handler):
            return handler(context)
        registry.register(item.stage, binding, priority=len(plan.contributions) - order,
                          fail_closed=item.fail_closed, contribution_id=item.id,
                          plugin=plugin, mode=item.mode)
    registry.plan_id = plan.as_dict()["plan_id"]
