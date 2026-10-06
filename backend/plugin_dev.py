"""Create, check and exercise directory plugins without starting the application."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from dataclasses import asdict, is_dataclass
from enum import Enum
from functools import wraps
import inspect
import json
import keyword
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import uuid

BACKEND_ROOT = Path(__file__).resolve().parent
for root in (BACKEND_ROOT.parent, BACKEND_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def scaffold(name, root):
    """Create a complete importable starter; never overwrite an existing directory."""
    if (not re.fullmatch(r"[a-z][a-z0-9_]*", name) or keyword.iskeyword(name)
            or name == "core" or name in sys.stdlib_module_names):
        raise ValueError("Use a lowercase Python package name that is not reserved")
    target = Path(root).resolve() / name
    manifest = {
        "schema_version": 1, "id": name, "version": "0.1.0",
        "provider": f"{name}:create", "enabled_by_default": True,
        "interfaces": {
            "describe": {"stage": "prepare", "required": True, "async": False},
            "echo": {"stage": "tool_before", "required": True, "async": True},
        },
        "defaults": {"label": name}, "dependencies": [],
        "contributions": [{"id": f"{name}.prepare", "stage": "prepare",
                           "handler": f"{name}:observe_prepare", "mode": "observer"}],
    }
    source = '''"""Starter plugin: synchronous/asynchronous services and a phase observer."""


class Provider:
    def __init__(self, label):
        self.label = label

    def describe(self):
        return {"label": self.label}

    async def echo(self, text):
        return {"label": self.label, "text": text}


def create(*, settings, **kwargs):
    return Provider(settings.plugin_configs["PLUGIN_ID"]["label"])


def observe_prepare(context):
    # Observers receive a data snapshot; they do not control execution.
    from app.trace.tracing import current_trace_sink
    sink = current_trace_sink()
    if sink is not None:
        sink.event("plugin_example", plugin="PLUGIN_ID", stage=context.event.value,
                   run_id=context.run_id, agent_name=context.agent_name)
'''.replace("PLUGIN_ID", name)
    fixture = {"config": {"label": name}, "factory_kwargs": {}, "args": [],
               "kwargs": {"text": "hello"}, "context": {"agent_name": "PluginDev", "payload": {}}}
    readme = f'''# {name}

Generated ArchitectCoder plugin. Run these commands from the repository root:

```shell
python backend/plugin_dev.py check "{target.as_posix()}"
python backend/plugin_dev.py check "{target.as_posix()}" --instantiate
python backend/plugin_dev.py run "{target.as_posix()}" --method echo --input "{target.as_posix()}/smoke.json"
python backend/plugin_dev.py run "{target.as_posix()}" --stage prepare
```

`check` imports declarations but does not construct a provider. `--instantiate`
also validates the actual required/optional methods and async declarations.
`run` executes one declared service or this plugin's contributions at one stage
in a private registry. It closes any constructed provider after execution.

Edit `plugin.json`, `__init__.py` and `smoke.json` together. Use `--root` to make
external dependencies available, and `--output report.json` to save results.
Declare asynchronous methods with `async=true`; keep import-time code free of
resource creation. New services still need a business/tool assembly point.

To discover this plugin in the application, add its parent directory to
`PLUGIN_ROOTS` (paths there are relative to `backend/`) and restart. If the root
is already configured, use **Discover plugins** in the architecture panel.
See `docs/plugin-development.md` for fixture format and execution boundaries.
'''
    files = {"plugin.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
             "__init__.py": source, "smoke.json": json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
             "README.md": readme}
    target.mkdir(parents=True, exist_ok=False)
    for filename, content in files.items():
        (target / filename).write_text(content, encoding="utf-8")
    return {"status": "passed", "action": "new", "plugin": name, "directory": str(target), "files": list(files)}


def read_fixture(path):
    data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    fields = {"config": dict, "factory_kwargs": dict, "args": list, "kwargs": dict, "context": dict}
    if not isinstance(data, dict) or set(data) - fields.keys():
        raise ValueError("Fixture must contain only config, factory_kwargs, args, kwargs and context")
    for key, kind in fields.items():
        if not isinstance(data.get(key, kind()), kind):
            raise ValueError(f"Fixture {key} must be a {kind.__name__}")
    context = data.get("context", {})
    types = {"agent_name": str, "payload": dict, "messages": list, "llm_response": dict,
             "tool_name": str, "tool_input": dict, "tool_status": str, "error_code": str, "tool_output": str}
    if set(context) - types.keys() or any(value is not None and not isinstance(value, types[key])
                                        for key, value in context.items()):
        raise ValueError("Invalid fixture context fields or types")
    if context.get("agent_name", "PluginDev") is None or context.get("payload", {}) is None:
        raise ValueError("Context agent_name and payload cannot be null")
    return data


def development_manager(path, roots):
    from backend.config.plugin_catalog import packaged_manifests, read_manifest, scan_manifests
    from app.agent_base.core.plugins import PluginManager, manifest_spec

    path = Path(path).resolve()
    target = read_manifest(path / "plugin.json" if path.is_dir() else path)
    catalog = {item["id"]: item for item in packaged_manifests()}
    # Metadata only; providers unrelated to the target are not imported.
    for item in (*scan_manifests(tuple(str(Path(root).resolve()) for root in roots)), target):
        previous = catalog.get(item["id"])
        if previous and previous["source"] != item["source"]:
            raise ValueError(f"Duplicate plugin ID: {item['id']}")
        catalog[item["id"]] = item
    selected = {}

    def include(name):
        if name not in catalog or name in selected:
            return
        item = catalog[name]
        selected[name] = item
        for dependency in (*item.get("dependencies", ()), *item.get("optional_dependencies", ())):
            include(dependency)

    include(target["id"])
    specs = {name: manifest_spec(item) for name, item in selected.items()}
    PluginManager._validate_slots(specs)
    return PluginManager(tuple(specs.values())), specs[target["id"]]


class RecordingSink:
    def __init__(self, events):
        self.events = events

    def event(self, event_type, **payload):
        row = {"event_type": event_type, **payload}
        self.events.append(row)
        return row


async def close_provider(instance):
    if instance is None or inspect.isawaitable(instance):
        return  # The loader closes rejected factory coroutines itself.
    close = getattr(instance, "aclose", None) or getattr(instance, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


async def inspect_plugin(args):
    from app.agent_base.core.hooks import (
        AgentRuntime, HookContext, HookEvent, HookRegistry, reset_runtime, set_runtime,
    )
    from app.agent_base.core.lifecycle import ExecutionPlan, build_plan, install_plan
    from app.agent_base.core.plugin_runtime import PluginSnapshot, plugin_scope
    from app.trace.tracing import reset_current_trace_sink, set_current_trace_sink

    report = {"status": "passed", "action": args.command, "diagnostics": [], "events": [],
              "provider_instantiated": False}
    instances, original_path = [], list(sys.path)
    snapshot = None
    sink_token = runtime_token = None
    try:
        fixture = read_fixture(args.input)
        manager, spec = development_manager(args.plugin, args.root)
        report.update(plugin=spec.name, version=spec.version)
        # Do not read application .env or require model credentials for a plugin check.
        settings = SimpleNamespace(**{spec.enabled_setting: True})
        manager._overrides = {spec.name: {"config": fixture.get("config", {})}}
        settings = manager.effective_settings(settings)
        registry = HookRegistry()
        snapshot = PluginSnapshot(manager, registry, settings)
        with plugin_scope(snapshot):
            plan = build_plan(manager, settings)
            report["plan_id"] = plan.as_dict()["plan_id"]
            report["declarations"] = [{"plugin": row["name"], "status": row["status"]} for row in plan.plugins]
            report["diagnostics"].extend(diagnostic for row in plan.plugins for diagnostic in row["diagnostics"])
            if any(row["status"] == "unavailable" for row in plan.plugins):
                report["status"] = "failed"
                return report
            # Only target phase hooks run. Dependency services remain callable on demand.
            local_plan = ExecutionPlan(plan.plugins, tuple(pair for pair in plan.contributions
                                                           if pair[0] == spec.name or pair[1].mode == "service"))
            install_plan(local_plan, registry)
            report["plan_id"] = registry.plan_id
            runtime = AgentRuntime(run_id=f"plugin-dev-{uuid.uuid4().hex}", plugin_plan_id=registry.plan_id)
            runtime_token = set_runtime(runtime)
            sink_token = set_current_trace_sink(RecordingSink(report["events"]))
            report["interfaces"] = [{"method": method, "stage": dict(spec.interface_stages)[method],
                                     "required": method in spec.required_methods, "async": method in spec.async_methods}
                                    for method in (*spec.required_methods, *spec.optional_methods)]
            if args.command == "run" and args.method and args.method not in {row["method"] for row in report["interfaces"]}:
                raise ValueError(f"Undeclared plugin interface: {args.method}")
            provider = None
            if getattr(args, "instantiate", False) or (args.command == "run" and args.method):
                def capture_factory(entry):
                    factory = manager._load_factory(entry)

                    @wraps(factory)
                    def create(**kwargs):
                        value = factory(**kwargs)
                        instances.append(value)
                        return value
                    return create

                provider = manager.load(spec.name, settings=settings, kwargs=fixture.get("factory_kwargs", {}),
                                        factory_loader=capture_factory)
                report["provider_instantiated"] = any(value is not None and not inspect.isawaitable(value) for value in instances)
                if provider is None:
                    report["status"] = "failed"
                    report["diagnostics"].extend(diagnostic for row in manager.diagnostics()
                                                 if row["name"] == spec.name for diagnostic in row["diagnostics"])
                    return report
                for row in report["interfaces"]:
                    target = getattr(provider, row["method"], None)
                    row["implemented"] = callable(target)
                    if callable(target):
                        row["signature"] = str(inspect.signature(target))
            if args.command == "run":
                if args.method:
                    method = getattr(provider, args.method, None)
                    if not callable(method):
                        raise ValueError(f"Optional interface is not implemented: {args.method}")
                    positional, keywords = fixture.get("args", []), fixture.get("kwargs", {})
                    inspect.signature(method).bind(*positional, **keywords)
                    value = method(*positional, **keywords)
                    report["result"] = await value if inspect.isawaitable(value) else value
                else:
                    stage = HookEvent(args.stage)
                    if not any(item.stage == stage and item.mode != "service" for _, item in local_plan.contributions):
                        raise ValueError(f"No phase contributions declared by {spec.name} at {stage.value}")
                    context = {"agent_name": "PluginDev", **fixture.get("context", {})}
                    report["result"] = await registry.atrigger(stage, HookContext(stage, run_id=runtime.run_id,
                                                                                runtime=runtime, **context))
                # Non-fatal observer exceptions still fail a development run.
                for event in report["events"]:
                    if event.get("event_type") == "plugin_contribution" and event.get("status") in {"error", "interrupted"}:
                        report["status"] = "failed"
                        report["diagnostics"].append({"code": "contribution_failed", "contribution_id": event["contribution_id"],
                                                      "message": event.get("error_message", event.get("error_type", "Interrupted"))})
    except Exception as exc:
        report["status"] = "failed"
        report["diagnostics"].append({"code": "development_failed", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        for instance in reversed(instances):
            try:
                with plugin_scope(snapshot):
                    await close_provider(instance)
            except Exception as exc:
                report["status"] = "failed"
                report["diagnostics"].append({"code": "cleanup_failed", "message": f"{type(exc).__name__}: {exc}"})
        if sink_token is not None:
            reset_current_trace_sink(sink_token)
        if runtime_token is not None:
            reset_runtime(runtime_token)
        sys.path[:] = original_path
    return report


def json_value(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return str(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("new", help="Generate an importable starter plugin without overwriting files")
    create.add_argument("name")
    create.add_argument("--root", default=str(BACKEND_ROOT.parent / "examples" / "plugins"))
    create.add_argument("--output", help="Write the JSON report to this file")
    for command in ("check", "run"):
        sub = commands.add_parser(command, help="Check declarations and optionally instantiate" if command == "check" else "Exercise a service or phase in a private registry")
        sub.add_argument("plugin", help="Plugin directory or plugin.json path; relative to the working directory")
        sub.add_argument("--root", action="append", default=[], help="Additional dependency directory; repeatable")
        sub.add_argument("--input", help="JSON fixture with config, factory_kwargs, args, kwargs and context")
        sub.add_argument("--output", help="Write the JSON report to this file")
        if command == "check":
            sub.add_argument("--instantiate", action="store_true", help="Construct and validate actual provider methods, then close it")
        else:
            selection = sub.add_mutually_exclusive_group(required=True)
            selection.add_argument("--method", help="One declared provider method")
            selection.add_argument("--stage", help="One public phase or notification contributed by this plugin")
    args = parser.parse_args(argv)
    try:
        # Plugin stdout belongs on stderr, leaving stdout as one machine-readable report.
        with redirect_stdout(sys.stderr):
            report = scaffold(args.name, args.root) if args.command == "new" else asyncio.run(inspect_plugin(args))
        content = json.dumps(report, ensure_ascii=False, indent=2, default=json_value) + "\n"
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
        print(content, end="")
        return int(report["status"] != "passed")
    except Exception as exc:
        print(json.dumps({"status": "failed", "action": args.command,
                          "diagnostics": [{"code": "development_failed", "message": f"{type(exc).__name__}: {exc}"}]},
                         ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
