"""Read plugin-owned metadata and deployment overrides without importing plugins."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_PLUGIN_ROOT = BACKEND_ROOT.parent / "extensions"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _SettingsView(SimpleNamespace):
    def __getattr__(self, key):
        return getattr(object.__getattribute__(self, "_base"), key)


def config_path(value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (BACKEND_ROOT / path).resolve()


def _entry(value):
    if not isinstance(value, str):
        return False
    module, separator, name = value.partition(":")
    return bool(separator and all(_IDENTIFIER.fullmatch(part) for part in module.split(".")) and _IDENTIFIER.fullmatch(name))


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid plugin JSON: {path}: {exc.msg} (line {exc.lineno})") from exc


def read_manifest(path):
    path = Path(path).resolve()
    data = _read_json(path)
    allowed = {"schema_version", "id", "version", "slot", "provider", "enabled_by_default", "settings",
               "interfaces", "defaults", "dependencies", "optional_dependencies", "router", "contributions", "contribution_loader"}
    if not isinstance(data, dict) or set(data) - allowed or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError(f"Invalid plugin manifest schema: {path}")
    identifier = data.get("id")
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier) or identifier == "core":
        raise ValueError(f"Invalid plugin ID: {path}")
    if not isinstance(data.get("version"), str) or not data["version"].strip() or not _entry(data.get("provider")):
        raise ValueError(f"Plugin requires a version and module:factory provider: {path}")
    if type(data.get("enabled_by_default", True)) is not bool:
        raise ValueError(f"Invalid plugin enabled flag: {path}")
    if data.get("slot") is not None and (not isinstance(data["slot"], str) or not _IDENTIFIER.fullmatch(data["slot"])):
        raise ValueError(f"Invalid plugin slot: {path}")
    settings = data.get("settings", {})
    if not isinstance(settings, dict) or set(settings) - {"enabled", "provider", "prefix"} or any(
            not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) for value in settings.values()):
        raise ValueError(f"Invalid plugin settings aliases: {path}")
    interfaces = data.get("interfaces", {})
    if not isinstance(interfaces, dict):
        raise ValueError(f"Plugin interfaces must be an object: {path}")
    for name, declaration in interfaces.items():
        if not _IDENTIFIER.fullmatch(name) or name.startswith("_") or name in {"close", "aclose"} or not isinstance(declaration, dict) or set(declaration) - {"stage", "required", "async", "wrap_result"}:
            raise ValueError(f"Invalid plugin interface: {path}")
        if not isinstance(declaration.get("stage", "run_start"), str) or any(
                type(declaration[key]) is not bool for key in ("required", "async", "wrap_result") if key in declaration):
            raise ValueError(f"Invalid plugin interface phase or flags: {path}")
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict) or any(not _IDENTIFIER.fullmatch(key) for key in defaults):
        raise ValueError(f"Invalid plugin defaults: {path}")
    for key in ("dependencies", "optional_dependencies"):
        values = data.get(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) or not _IDENTIFIER.fullmatch(item) for item in values) or len(values) != len(set(values)):
            raise ValueError(f"Invalid plugin dependencies: {path}")
    for key in ("router", "contribution_loader"):
        if key in data and not _entry(data[key]):
            raise ValueError(f"Invalid plugin {key} entry: {path}")
    if not isinstance(data.get("contributions", []), list) or any(not isinstance(item, dict) for item in data.get("contributions", [])):
        raise ValueError(f"Invalid plugin contributions: {path}")
    contribution_fields = {"id", "stage", "handler", "mode", "priority", "before", "after", "scope", "fail_closed", "interface_id"}
    for item in data.get("contributions", []):
        if set(item) - contribution_fields or any(not isinstance(item.get(key), str) or not item[key] for key in ("id", "stage", "handler")) or not _entry(item["handler"]):
            raise ValueError(f"Invalid plugin contribution entry: {path}")
        for key in ("before", "after"):
            values = item.get(key, [])
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values) or len(values) != len(set(values)):
                raise ValueError(f"Invalid contribution ordering: {path}")
        if type(item.get("priority", 0)) is not int or type(item.get("fail_closed", False)) is not bool:
            raise ValueError(f"Invalid contribution priority or failure policy: {path}")
    manifest_digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    implementation = hashlib.sha256()
    # Content identity, not executable-code reload. Ignore caches and generated files.
    for source in sorted(path.parent.rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        implementation.update(json.dumps(source.relative_to(path.parent).as_posix()).encode())
        implementation.update(hashlib.sha256(source.read_bytes()).digest())
    implementation_digest = implementation.hexdigest()
    revision = hashlib.sha256(f"{manifest_digest}:{implementation_digest}".encode()).hexdigest()
    return {**data, "source": str(path), "manifest_digest": manifest_digest,
            "implementation_digest": implementation_digest, "revision": revision}


def scan_manifests(roots):
    """Only direct child directories with plugin.json count as plugins."""
    declarations, sources, slots = {}, set(), {}
    for root in sorted({config_path(value) for value in roots}, key=str):
        if not root.is_dir():
            raise ValueError(f"Plugin root is not a directory: {root}")
        for path in sorted(root.glob("*/plugin.json")):
            path = path.resolve()
            if path in sources:
                continue
            data = read_manifest(path)
            identifier = data["id"]
            if identifier in declarations:
                raise ValueError(f"Duplicate plugin ID {identifier}: {declarations[identifier]['source']} and {path}")
            slot = data.get("slot", identifier)
            if slot in slots:
                raise ValueError(f"Duplicate plugin slot {slot}: {slots[slot]} and {identifier}")
            slots[slot] = identifier
            declarations[identifier] = data
            sources.add(path)
    # A slot cannot shadow another plugin's ID.
    for slot, identifier in slots.items():
        if slot in declarations and slot != identifier:
            raise ValueError(f"Plugin slot {slot} conflicts with plugin ID")
    return tuple(declarations[key] for key in sorted(declarations))


@lru_cache()
def packaged_manifests():
    return scan_manifests((BUILTIN_PLUGIN_ROOT,))


def packaged_default(plugin, key, fallback=None):
    data = next((item for item in packaged_manifests() if item["id"] == plugin), {})
    return copy.deepcopy(data.get("defaults", {}).get(key, fallback))


def packaged_enabled(plugin):
    data = next((item for item in packaged_manifests() if item["id"] == plugin), None)
    return data.get("enabled_by_default", True) if data else False


def legacy_manifests(value):
    """Translate the previous additional-plugin list to the same metadata model."""
    if not value:
        return ()
    path = config_path(value)
    data = _read_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("plugins"), list):
        raise ValueError("plugin manifest requires schema_version=1 and a plugins list")
    result, names = [], set()
    for item in data["plugins"]:
        if not isinstance(item, dict) or set(item) - {"name", "provider", "enabled", "interfaces", "interface_stages"}:
            raise ValueError("invalid plugin manifest entry")
        name, provider = item.get("name"), item.get("provider")
        methods, stages = item.get("interfaces", []), item.get("interface_stages", {})
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) or name == "core" or not _entry(provider):
            raise ValueError("plugin manifest requires name and module:factory provider")
        if type(item.get("enabled", True)) is not bool or not isinstance(methods, list) or any(
                not isinstance(method, str) or not _IDENTIFIER.fullmatch(method) for method in methods):
            raise ValueError("invalid enabled flag or interface list")
        if not isinstance(stages, dict) or any(key not in methods or not isinstance(stage, str) for key, stage in stages.items()):
            raise ValueError("interface_stages must map declared interfaces to service phases")
        if name in names:
            raise ValueError(f"duplicate or reserved plugin name: {name}")
        names.add(name)
        result.append({"id": name, "provider": provider, "enabled_by_default": item.get("enabled", True),
                       "interfaces": {method: {"stage": stages.get(method, "run_start")} for method in methods},
                       "version": "legacy", "source": str(path)})
    return tuple(result)


def deployment_overrides(path, identifiers):
    if not path:
        return {}
    path = config_path(path)
    data = _read_json(path)
    if not isinstance(data, dict) or set(data) != {"schema_version", "plugins"} or data["schema_version"] != 1 or not isinstance(data["plugins"], dict):
        raise ValueError(f"Invalid plugin deployment config: {path}")
    for identifier, options in data["plugins"].items():
        if identifier not in identifiers or not isinstance(options, dict) or set(options) - {"enabled", "provider", "config"}:
            raise ValueError(f"Unknown plugin or invalid override for {identifier}: {path}")
        if "enabled" in options and type(options["enabled"]) is not bool:
            raise ValueError(f"Invalid enabled override for {identifier}")
        if "provider" in options and not _entry(options["provider"]) and options["provider"] not in {"none", "noop", "disabled"}:
            raise ValueError(f"Invalid provider override for {identifier}")
        if not isinstance(options.get("config", {}), dict):
            raise ValueError(f"Invalid parameter overrides for {identifier}")
    return data["plugins"]


def resolved_settings(settings, declarations, overrides):
    """Explicit settings/environment > deployment file > plugin defaults."""
    attributes = vars(settings) if hasattr(settings, "__dict__") else {}
    explicit = getattr(settings, "model_fields_set", None)
    def is_explicit(key):
        return key in explicit if explicit is not None else hasattr(settings, key)
    fields = getattr(type(settings), "model_fields", {})
    updates, configs, owners = {}, {}, {}
    for data in declarations:
        identifier = data["id"]
        aliases = data.get("settings", {})
        enabled = aliases.get("enabled", f"plugin_{identifier}_enabled")
        provider = aliases.get("provider", f"plugin_{identifier}_provider")
        prefix = aliases.get("prefix", f"plugin_{identifier}_")
        keys = [enabled, provider, *(key if key.startswith(("agent_", "plugin_")) else prefix + key for key in data.get("defaults", {}))]
        for key in keys:
            if key in owners:
                raise ValueError(f"Conflicting plugin setting {key}: {owners[key]} and {identifier}")
            owners[key] = identifier
        options = overrides.get(identifier, {})
        defaults = data.get("defaults", {})
        unknown = set(options.get("config", {})) - defaults.keys()
        if unknown:
            raise ValueError(f"Unknown config keys for plugin {identifier}: {', '.join(sorted(unknown))}")
        values = {enabled: options.get("enabled", data.get("enabled_by_default", True)),
                  provider: options.get("provider", data["provider"])}
        config = {}
        for key, default in defaults.items():
            attribute = key if key.startswith(("agent_", "plugin_")) else prefix + key
            value = options.get("config", {}).get(key, copy.deepcopy(default))
            if key in options.get("config", {}) and default is not None and type(value) is not type(default) and not (type(default) is float and type(value) in {int, float}):
                raise ValueError(f"Invalid config type for plugin {identifier}: {key}")
            values[attribute] = value
            config[key] = getattr(settings, attribute) if is_explicit(attribute) else value
        for key, value in values.items():
            if not is_explicit(key):
                if key in fields:
                    from pydantic import TypeAdapter
                    value = TypeAdapter(fields[key].annotation).validate_python(value)
                updates[key] = value
        configs[identifier] = config
    if hasattr(settings, "model_copy"):
        result = settings.model_copy(update=updates)
        # Inherited plugin defaults are not explicit deployment/environment overrides.
        object.__setattr__(result, "__pydantic_fields_set__", set(explicit))
    else:
        result = _SettingsView(**{**attributes, **updates, "_base": settings})
    object.__setattr__(result, "plugin_configs", configs)
    return result
