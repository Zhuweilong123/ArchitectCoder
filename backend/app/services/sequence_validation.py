"""Sequence structure diagnostics. Never infer control flow from prose or layout.

Legacy frames remain loadable and receive warnings; explicit operand structures
are checked before an agent can claim successful project validation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pydantic import ValidationError
from app.models.uml import UmlDiagram


@dataclass(frozen=True)
class SequenceDiagnostic:
    severity: str
    code: str
    path: str
    message: str

    def to_dict(self):
        return asdict(self)


def validate_sequence_diagrams(diagrams: list[dict]) -> list[SequenceDiagnostic]:
    diagnostics: list[SequenceDiagnostic] = []
    for index, diagram in enumerate(diagrams):
        if isinstance(diagram, dict) and diagram.get("diagram_type") == "sequence":
            try:
                normalized = UmlDiagram.model_validate(diagram).model_dump()
            except ValidationError as exc:
                diagnostics.append(SequenceDiagnostic("error", "SEQ_SCHEMA", f"diagrams[{index}]", str(exc)))
                continue
            diagnostics.extend(_validate(normalized, f"diagrams[{index}]"))
    return diagnostics


def format_sequence_diagnostics(diagnostics: list[SequenceDiagnostic]) -> str:
    lines = [f"{d.severity.upper()} {d.code} {d.path}: {d.message}" for d in diagnostics[:40]]
    if len(diagnostics) > 40:
        lines.append(f"... {len(diagnostics) - 40} additional diagnostics")
    return "\n".join(lines)


def _validate(diagram: dict, path: str) -> list[SequenceDiagnostic]:
    result: list[SequenceDiagnostic] = []

    def emit(code, location, message, severity="error"):
        result.append(SequenceDiagnostic(severity, code, location, message))

    def items(value, location):
        if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
            emit("SEQ_STRUCTURE", location, "Expected an array of objects.")
            return []
        return value

    def refs(value, location):
        if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
            emit("SEQ_REFERENCE_LIST", location, "Expected an array of non-empty IDs.")
            return []
        if len(value) != len(set(value)):
            emit("SEQ_DUPLICATE_REFERENCE", location, "IDs must not be repeated.")
        return value

    def bounds(item, location):
        start, end = item.get("y_start"), item.get("y_end")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)) or end <= start:
            emit("SEQ_RANGE", location, "Finite y_start < y_end is required.")
            return None
        return start, end

    seen: set[str] = set()

    def identify(item, location):
        key = item.get("id")
        if not isinstance(key, str) or not key:
            emit("SEQ_ID", location, "A non-empty ID is required.")
            return ""
        if key in seen:
            emit("SEQ_DUPLICATE_ID", location, f"Duplicate ID: {key}.")
        seen.add(key)
        return key

    lifelines = items(diagram.get("lifelines", []), path + ".lifelines")
    messages = items(diagram.get("messages", []), path + ".messages")
    fragments = items(diagram.get("fragments", []), path + ".fragments")
    life_ids = {identify(x, f"{path}.lifelines[{i}]") for i, x in enumerate(lifelines)} - {""}
    life_by_id = {x["id"]: x for x in lifelines}
    message_by_id = {}
    for i, message in enumerate(messages):
        location = f"{path}.messages[{i}]"
        key = identify(message, location)
        message_by_id[key] = message
        if message.get("type", "sync") not in {"sync", "async", "return", "simple", "self"}:
            emit("SEQ_MESSAGE_TYPE", location, "Unknown message type.")
        for endpoint in ("from_lifeline", "to_lifeline"):
            if message.get(endpoint) not in life_ids:
                emit("SEQ_MESSAGE_ENDPOINT", location, f"Unknown {endpoint}: {message.get(endpoint)}.")
        if message.get("type") == "self" and message.get("from_lifeline") != message.get("to_lifeline"):
            emit("SEQ_SELF_ENDPOINT", location, "Self-message endpoints must be identical.")

    fragment_by_id = {}
    for i, fragment in enumerate(fragments):
        key = identify(fragment, f"{path}.fragments[{i}]")
        fragment_by_id[key] = fragment

    # Direct ownership only: a nested operand owns its messages, not its parent.
    owners: dict[str, str] = {}
    fragment_bounds = {}
    operand_by_fragment = {}
    for i, fragment in enumerate(fragments):
        location = f"{path}.fragments[{i}]"
        kind = fragment.get("type", "loop")
        if kind not in {"loop", "alt", "opt", "break", "par", "critical", "neg"}:
            emit("SEQ_FRAGMENT_TYPE", location, "Unknown combined-fragment operator.")
        region = bounds(fragment, location)
        fragment_bounds[fragment.get("id", "")] = region
        covered = refs(fragment.get("lifeline_ids", []), location + ".lifeline_ids")
        if set(covered) - life_ids:
            emit("SEQ_LIFELINE_REFERENCE", location, "Fragment references unknown lifelines.")
        operands = items(fragment.get("operands", []), location + ".operands")
        if operands and not covered:
            emit("SEQ_LIFELINE_COVERAGE", location, "Structured fragments require explicit lifeline_ids.")
        for lifeline_id in covered:
            lifeline = life_by_id.get(lifeline_id)
            axis = lifeline.get("x", 100) + 70 if lifeline else None
            if axis is not None and not fragment["x"] <= axis <= fragment["x"] + fragment["width"]:
                emit("SEQ_FRAME_COVERAGE", location, f"Frame does not visually cover lifeline {lifeline_id}.")
        operand_by_fragment[fragment.get("id", "")] = {}
        if not operands:
            if kind in {"alt", "par"}:
                emit("SEQ_LEGACY_OPERANDS", location,
                     "Legacy frame has no explicit operands; adjacent frames/labels do not establish alternative or parallel branches. Add operands before claiming semantic validation.", "warning")
            continue
        if kind in {"alt", "par"} and len(operands) < 2:
            emit("SEQ_OPERAND_COUNT", location, f"{kind} requires at least two operands in this authoring contract; use opt for one optional branch.")
        if kind in {"loop", "opt", "break", "critical", "neg"} and len(operands) != 1:
            emit("SEQ_OPERAND_COUNT", location, f"{kind} requires exactly one operand.")
        previous_end = None
        else_count = 0
        for j, operand in enumerate(operands):
            op_location = f"{location}.operands[{j}]"
            op_id = identify(operand, op_location)
            operand_by_fragment[fragment.get("id", "")][op_id] = operand
            guard = operand.get("guard", "")
            if not isinstance(guard, str):
                emit("SEQ_GUARD", op_location, "Guard must be text.")
                guard = ""
            normalized_guard = guard.strip().strip("[]").strip().casefold()
            if kind in {"alt", "opt", "break", "loop"} and not normalized_guard:
                emit("SEQ_GUARD", op_location, "Write an explicit condition (or true); do not invent an implicit success branch.")
            if normalized_guard == "else":
                else_count += 1
                if kind != "alt" or j != len(operands) - 1:
                    emit("SEQ_ELSE_GUARD", op_location, "else is only supported as the last operand of alt.")
            op_region = bounds(operand, op_location)
            if op_region:
                if region and not (region[0] <= op_region[0] < op_region[1] <= region[1]):
                    emit("SEQ_OPERAND_RANGE", op_location, "Operand must lie inside its fragment.")
                if previous_end is not None and op_region[0] < previous_end:
                    emit("SEQ_OPERAND_OVERLAP", op_location, "Operand regions must be ordered and non-overlapping.")
                previous_end = op_region[1]
            for message_id in refs(operand.get("message_ids", []), op_location + ".message_ids"):
                message = message_by_id.get(message_id)
                if message is None:
                    emit("SEQ_MESSAGE_REFERENCE", op_location, f"Unknown message: {message_id}.")
                    continue
                if message_id in owners:
                    emit("SEQ_MESSAGE_OWNERSHIP", op_location, f"Message {message_id} already belongs to {owners[message_id]}.")
                owners[message_id] = op_id
                y = message.get("y")
                if op_region and (not isinstance(y, (int, float)) or isinstance(y, bool) or not math.isfinite(y) or not op_region[0] < y < op_region[1]):
                    emit("SEQ_MESSAGE_RANGE", op_location, f"Message {message_id} must be strictly inside its operand region.")
                if covered and {message.get("from_lifeline"), message.get("to_lifeline")} - set(covered):
                    emit("SEQ_MESSAGE_COVERAGE", op_location, f"Message {message_id} uses lifelines outside fragment coverage.")
        if else_count > 1:
            emit("SEQ_ELSE_GUARD", location, "alt may contain only one else operand.")

    # Explicit frames cannot silently contain unassigned messages. Children
    # have their own owners; their membership is checked independently.
    for i, fragment in enumerate(fragments):
        for j, operand in enumerate(fragment.get("operands", [])):
            region = (operand["y_start"], operand["y_end"])
            coverage = set(fragment.get("lifeline_ids", []))
            for message in messages:
                y = message.get("y")
                if (message.get("id") not in owners and isinstance(y, (int, float))
                        and region[0] < y < region[1]
                        and {message["from_lifeline"], message["to_lifeline"]} <= coverage):
                    emit("SEQ_UNASSIGNED_MESSAGE", f"{path}.fragments[{i}].operands[{j}]",
                         f"Message {message['id']} is inside an operand but has no explicit owner.")

    for i, fragment in enumerate(fragments):
        location = f"{path}.fragments[{i}]"
        parent_id = fragment.get("parent_fragment_id", "")
        parent_operand_id = fragment.get("parent_operand_id", "")
        parent = fragment_by_id.get(parent_id) if isinstance(parent_id, str) else None
        if bool(parent_id) != bool(parent_operand_id):
            emit("SEQ_PARENT_REFERENCE", location, "Nested fragments require both parent_fragment_id and parent_operand_id.")
        if parent_id:
            parent_operand = operand_by_fragment.get(parent_id, {}).get(parent_operand_id) if isinstance(parent_id, str) and isinstance(parent_operand_id, str) else None
            if parent is None or parent_operand is None:
                emit("SEQ_PARENT_REFERENCE", location, "Unknown parent fragment/operand.")
            else:
                child_range = fragment_bounds.get(fragment.get("id", ""))
                parent_range = bounds(parent_operand, location + ".parent_operand")
                if child_range and parent_range and not (parent_range[0] <= child_range[0] < child_range[1] <= parent_range[1]):
                    emit("SEQ_PARENT_RANGE", location, "Nested fragment must lie inside its parent operand.")
                parent_coverage = set(parent.get("lifeline_ids", []))
                if parent_coverage and set(fragment.get("lifeline_ids", [])) - parent_coverage:
                    emit("SEQ_PARENT_COVERAGE", location, "Nested fragment coverage exceeds its parent.")
            visited = {fragment.get("id")}
            cursor = parent
            while cursor:
                key = cursor.get("id")
                if key in visited:
                    emit("SEQ_PARENT_CYCLE", location, "Fragment nesting contains a cycle.")
                    break
                visited.add(key)
                next_parent = cursor.get("parent_fragment_id", "")
                cursor = fragment_by_id.get(next_parent) if isinstance(next_parent, str) else None
        if fragment.get("type") == "break":
            required = set(parent.get("lifeline_ids", [])) if parent else life_ids
            required = required or life_ids
            covered = set(fragment.get("lifeline_ids", []))
            if covered and required - covered:
                emit("SEQ_BREAK_COVERAGE", location, "break must cover all lifelines of the enclosing interaction/fragment.")
            elif not covered:
                emit("SEQ_BREAK_SCOPE", location, "Declare lifeline_ids to make break scope checkable; a local return does not terminate the enclosing interaction.", "warning")
    return result
