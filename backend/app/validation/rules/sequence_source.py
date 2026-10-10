"""Sequence consistency rules over normalized source facts, independent of language parsers."""
import re
from pathlib import Path
from app.agent_base.host_api.validation import CheckResult, ValidationDiagnostic

def validate_sequence_sources(diagram: dict, location: str, workspace_root: str | None, source_provider=None, source_cache=None):
    diagnostics = []
    def emit(code, message, path=location, severity="error"):
        diagnostics.append(ValidationDiagnostic(severity, code, path, message))

    scopes = diagram.get("source_scopes", [])
    if not scopes:
        emit("SEQ_SOURCE_UNVERIFIED", "No source_scopes: call order, early exits and parent returns have not been checked against source.", severity="warning")
        return diagnostics
    if not workspace_root:
        emit("SEQ_SOURCE_UNAVAILABLE", "A workspace root is required to verify source evidence.", severity="warning")
        return diagnostics

    root = Path(workspace_root).resolve()
    lifelines = {item["id"] for item in diagram["lifelines"]}
    messages = {item["id"]: item for item in diagram["messages"]}
    operations = {}
    scopes_by_id = {}
    unavailable_scopes, ambiguous_scopes = set(), set()
    for i, scope in enumerate(scopes):
        path = f"{location}.source_scopes[{i}]"
        scope_id = scope["id"]
        if not scope_id or scope_id in scopes_by_id:
            emit("SEQ_SOURCE_SCOPE", "Source scope IDs must be non-empty and unique.", path)
            continue
        scopes_by_id[scope_id] = scope
        if scope["lifeline_id"] not in lifelines or scope["coverage"] not in {"partial", "returns"}:
            emit("SEQ_SOURCE_SCOPE", "Unknown executing lifeline or coverage; use partial or returns.", path)
            continue
        entry = messages.get(scope.get("entry_message_id"))
        if (scope["coverage"] == "returns" or scope.get("entry_message_id")) and not entry:
            emit("SEQ_SOURCE_ENTRY", "Return coverage requires an explicit entry_message_id, including the caller lifeline.", path)
        if entry and (entry["to_lifeline"] != scope["lifeline_id"] or entry["type"] not in {"sync", "self"}):
            emit("SEQ_SOURCE_ENTRY", "The entry message must call the scope's executing lifeline.", path)
        try:
            raw = Path(scope["path"])
            source = (root / raw).resolve()
            if raw.is_absolute() or not source.is_relative_to(root):
                raise ValueError("Use a workspace-relative source path contained in the workspace, including symlink targets.")
            if not source.is_file():
                raise ValueError("Source file does not exist.")
            if source_provider is None:
                unavailable_scopes.add(scope_id)
                emit("SEQ_SOURCE_UNAVAILABLE", "Source facts provider is unavailable.", path, "warning")
                continue
            cache = source_cache if source_cache is not None else {}
            cache_key = str(source)
            if cache_key not in cache:
                try:
                    cache[cache_key] = source_provider.source_facts(str(source), workspace_root=str(root))
                except Exception as exc:
                    unavailable_scopes.add(scope_id)
                    emit("SEQ_SOURCE_UNAVAILABLE", f"Source facts collection failed: {exc}", path, "warning")
                    continue
            artifact = cache[cache_key]
            if artifact.status != "success":
                unavailable_scopes.add(scope_id)
                code = "SEQ_SOURCE_UNSUPPORTED" if artifact.status == "unsupported" else "SEQ_SOURCE_UNAVAILABLE"
                emit(code, artifact.reason or "Source extraction is unavailable.", path, "warning")
                continue
            functions = [f for f in artifact.functions if f.symbol == scope["symbol"]]
            if len(functions) > 1:
                ambiguous_scopes.add(scope_id)
                emit("SEQ_SOURCE_AMBIGUOUS", "Multiple functions match the qualified symbol; overload selection has not been verified.", path, "warning")
                continue
            if not functions:
                raise ValueError("Qualified function symbol not found.")
            function = functions[0]
            facts = {"call": {}, "condition": {}, "return": {}, "call_order": {}, "order_unknown": set()}
            for op in function.operations:
                facts[op.kind].setdefault(op.line, []).append((op, op.branches))
                if op.kind == "call":
                    facts["call_order"][id(op)] = op.rank
                    if not op.order_known:
                        facts["order_unknown"].add(id(op))
            operations[scope_id] = facts
        except (OSError, UnicodeError, ValueError) as exc:
            emit("SEQ_SOURCE_REFERENCE", f"Cannot verify {scope['path']}::{scope['symbol']}: {exc}", path)

    def resolve(ref, path, required_kind=None):
        scope_id, kind, line = ref["scope_id"], ref["kind"], ref["line"]
        if required_kind and kind != required_kind:
            emit("SEQ_SOURCE_KIND", f"Expected {required_kind} evidence.", path)
            return None
        if scope_id not in operations:
            if scope_id in unavailable_scopes:
                emit("SEQ_SOURCE_UNAVAILABLE", f"Source facts unavailable for {scope_id}.", path, "warning")
            elif scope_id in ambiguous_scopes:
                emit("SEQ_SOURCE_AMBIGUOUS", f"Source scope is ambiguous: {scope_id}.", path, "warning")
            else:
                emit("SEQ_SOURCE_REFERENCE", f"Unverified/unknown source scope: {scope_id}.", path)
            return None
        found = operations[scope_id].get(kind, {}).get(line, [])
        if not found:
            emit("SEQ_SOURCE_REFERENCE", f"No {kind} source operation begins at source line {line} in {scope_id}.", path)
            return None
        if len(found) != 1:
            emit("SEQ_SOURCE_AMBIGUOUS", f"Multiple {kind} operations at line {line}; split statements before claiming order verification.", path, "warning")
            return None
        return found[0]

    # Record guard evidence for direct and enclosing operands.
    guard_by_operand = {}
    owner = {}
    fragments = {f["id"]: f for f in diagram["fragments"]}
    for f in fragments.values():
        for operand in f["operands"]:
            guard = operand.get("source_guard")
            if guard and resolve(guard, f"{location}.operand[{operand['id']}].source_guard", "condition"):
                if f["type"] not in {"alt", "opt", "break"}:
                    emit("SEQ_SOURCE_GUARD_REGION", "An if guard needs an alt, opt or break operand, not an unconditional region.")
                else:
                    guard_by_operand[(f["id"], operand["id"])] = (guard["scope_id"], guard["line"])
            for message_id in operand["message_ids"]:
                owner[message_id] = (f["id"], operand["id"])

    def guards(message_id):
        current = owner.get(message_id)
        result, visited = set(), set()
        while current and current not in visited:
            visited.add(current)
            if current in guard_by_operand:
                result.add(guard_by_operand[current])
            f = fragments.get(current[0], {})
            current = (f.get("parent_fragment_id"), f.get("parent_operand_id")) if f.get("parent_fragment_id") else None
        return result

    evidenced_returns = set()
    calls = {}
    returns = {}
    for i, message in enumerate(diagram["messages"]):
        path = f"{location}.messages[{i}]"
        reply = message.get("reply_to")
        for ref in message.get("source_refs", []):
            resolved = resolve(ref, path + ".source_refs")
            if not resolved:
                continue
            node, branches = resolved
            scope = scopes_by_id[ref["scope_id"]]
            if ref["kind"] == "call":
                if message["from_lifeline"] != scope["lifeline_id"]:
                    emit("SEQ_SOURCE_CALLER", "The source function's lifeline must send this call.", path)
                if id(node) in operations[ref["scope_id"]]["order_unknown"]:
                    emit("SEQ_SOURCE_ORDER_UNVERIFIED", "Call is inside a loop, short-circuit, exception or async construct; automatic total-order comparison is not applicable.", path, "warning")
                else:
                    calls.setdefault(ref["scope_id"], []).append((ref["line"], branches, message, operations[ref["scope_id"]]["call_order"][id(node)]))
                names = re.findall(r"\b([A-Za-z_]\w*)\s*\(", message["label"])
                actual_name = node.name
                if len(names) == 1 and actual_name and names[0] != actual_name:
                    emit("SEQ_SOURCE_CALL_LABEL", f"Label names {names[0]}, but source line {ref['line']} calls {actual_name}.", path)
            elif ref["kind"] == "return":
                returns.setdefault(ref["scope_id"], []).append((ref["line"], branches, message))
                evidenced_returns.add((ref["scope_id"], ref["line"]))
                if message["type"] != "return" or message["from_lifeline"] != scope["lifeline_id"] or not reply or not scope["entry_message_id"] or reply != scope["entry_message_id"]:
                    emit("SEQ_SOURCE_RETURN", "A source return must reply to the scope entry call from the executing lifeline; a child reply does not represent its parent's return.", path)
                if branches and not all((ref["scope_id"], condition_line) in guards(message["id"]) for condition_line, _ in branches):
                    emit("SEQ_EARLY_EXIT_GUARD", "Conditional source return needs explicit operand source_guard evidence for its enclosing conditions; condition text in a message is insufficient.", path)

    for scope_id, scope in scopes_by_id.items():
        if scope_id not in operations:
            continue
        if scope["coverage"] == "returns":
            for line in operations[scope_id]["return"]:
                if (scope_id, line) not in evidenced_returns:
                    emit("SEQ_SOURCE_RETURN_MISSING", f"Unrepresented return: {scope['path']}::{scope['symbol']} line {line}.")
        else:
            emit("SEQ_SOURCE_PARTIAL", f"Scope {scope_id} is partial; missing source exits have not been exhaustively checked.", severity="warning")
        def separate_alternatives(first, second):
            def ancestors(message_id):
                current, found, seen = owner.get(message_id), {}, set()
                while current and current not in seen:
                    seen.add(current)
                    fragment = fragments.get(current[0], {})
                    if fragment.get("type") == "alt":
                        found[current[0]] = current[1]
                    current = (fragment.get("parent_fragment_id"), fragment.get("parent_operand_id")) if fragment.get("parent_fragment_id") else None
                return found
            left, right = ancestors(first), ancestors(second)
            return any(left[key] != right[key] for key in left.keys() & right.keys())

        for return_line, return_branches, returned in returns.get(scope_id, []):
            for call_line, call_branches, called, _ in calls.get(scope_id, []):
                if not return_branches or return_line >= call_line or separate_alternatives(returned["id"], called["id"]):
                    continue
                if any(dict(call_branches).get(line) is not direction for line, direction in return_branches if line in dict(call_branches)):
                    continue
                if returned["order"] >= called["order"]:
                    emit("SEQ_EARLY_EXIT_ORDER", f"Conditional return {returned['id']} (line {return_line}) must precede later continuation {called['id']} (line {call_line}); do not defer precondition failure until after the solver call.")
        # Evaluation order is checked only for compatible sequential branches.
        # Skip explicit parallel regions instead of imposing a total order.
        for a, (line_a, branches_a, msg_a, rank_a) in enumerate(calls.get(scope_id, [])):
            for line_b, branches_b, msg_b, rank_b in calls.get(scope_id, [])[a + 1:]:
                if line_a == line_b or msg_a["id"] == msg_b["id"]:
                    continue
                if separate_alternatives(msg_a["id"], msg_b["id"]):
                    continue
                if any(dict(branches_b).get(line) is not direction for line, direction in branches_a if line in dict(branches_b)):
                    continue
                def parallel(message_id):
                    current, seen = owner.get(message_id), set()
                    while current and current not in seen:
                        seen.add(current)
                        fragment = fragments.get(current[0], {})
                        if fragment.get("type") == "par":
                            return True
                        current = (fragment.get("parent_fragment_id"), fragment.get("parent_operand_id")) if fragment.get("parent_fragment_id") else None
                    return False
                if parallel(msg_a["id"]) or parallel(msg_b["id"]):
                    continue
                reversed_visual = msg_a["y"] > 0 and msg_b["y"] > 0 and (rank_a - rank_b) * (msg_a["y"] - msg_b["y"]) <= 0
                if (rank_a - rank_b) * (msg_a["order"] - msg_b["order"]) <= 0 or reversed_visual:
                    emit("SEQ_SOURCE_ORDER", f"Call order contradicts {scope['path']}::{scope['symbol']}: {msg_a['id']} (line {line_a}) and {msg_b['id']} (line {line_b}).")
    emit("SEQ_SOURCE_LIMITS", "Evidence checks cover referenced source calls, conditional return placement and reply endpoints only; guard equivalence, dispatch, exceptions and whole-program behavior still require review.", severity="warning")
    return diagnostics


class SequenceSourceRule:
    rule_id = "sequence.source"
    diagram_types = ("sequence",)

    def check(self, diagram, path, context):
        if not diagram.get("source_scopes"):
            return CheckResult(self.rule_id, path, "not_applicable", reason="No source evidence requested by this diagram; source consistency was not checked.")
        diagnostics = validate_sequence_sources(diagram, path, context.workspace_root,
            context.source_provider, context.source_cache)
        codes = {d.code for d in diagnostics}
        status = "unsupported" if "SEQ_SOURCE_UNSUPPORTED" in codes else "unavailable" if "SEQ_SOURCE_UNAVAILABLE" in codes else "partial" if codes & {
            "SEQ_SOURCE_PARTIAL", "SEQ_SOURCE_AMBIGUOUS", "SEQ_SOURCE_ORDER_UNVERIFIED", "SEQ_SOURCE_LIMITS"} else "checked"
        return CheckResult(self.rule_id, path, status, tuple(diagnostics),
            "Bounded evidence verification; whole-program equivalence is outside this rule.",
            coverage_met=bool(diagram.get("source_scopes")) and codes <= {"SEQ_SOURCE_LIMITS"})
