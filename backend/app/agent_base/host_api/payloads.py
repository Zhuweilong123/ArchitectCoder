"""Validate persisted state and serialize typed execution payloads at boundaries."""
from __future__ import annotations
from math import isfinite
from typing import cast
from pydantic import TypeAdapter, ValidationError
from app.agent_base.host_api.data import JsonObject, JsonValue
from app.agent_base.host_api.execution_types import RunCheckpoint, ToolExecutionEvidence

_checkpoint_adapter: TypeAdapter[RunCheckpoint] = TypeAdapter(RunCheckpoint)
_execution_evidence_adapter: TypeAdapter[ToolExecutionEvidence] = TypeAdapter(ToolExecutionEvidence)

def json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: json_value(item) for key, item in value.items()}
    raise ValueError('Execution payload must contain only JSON values')

def json_object(value: object) -> JsonObject:
    parsed = json_value(value)
    if not isinstance(parsed, dict):
        raise ValueError('Execution payload must be an object')
    return parsed

def parse_checkpoint(value: object) -> RunCheckpoint:
    payload = json_object(value)
    try:
        _checkpoint_adapter.validate_python(payload, strict=True)
    except ValidationError as exc:
        field = exc.errors()[0]["loc"][0]
        raise ValueError(f"Invalid checkpoint field: {field}") from exc
    # Preserve serializable extension-owned keys while validating every declared field.
    return cast(RunCheckpoint, payload)


def optional_json_mapping(value: JsonValue | None) -> JsonObject | None:
    """Read optional provider JSON sections without treating scalars as objects."""
    return value if isinstance(value, dict) else None


def json_object_list(value: JsonValue | None) -> list[JsonObject]:
    """Keep object entries in optional legacy provider report arrays."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def tool_call_signature(name: str, arguments: JsonObject | str) -> tuple[str, str]:
    """Match native retries by canonical JSON, preserving scalar argument types."""
    import json
    return name, json.dumps(arguments, sort_keys=True, ensure_ascii=False)


def parse_tool_execution_evidence(value: object) -> ToolExecutionEvidence:
    payload = json_object(value)
    try:
        _execution_evidence_adapter.validate_python(payload, strict=True)
    except ValidationError as exc:
        field = exc.errors()[0]['loc'][0]
        raise ValueError(f'Invalid tool evidence field: {field}') from exc
    return cast(ToolExecutionEvidence, payload)
