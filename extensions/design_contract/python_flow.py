"""Python control-flow fact extraction; no UML or validation policy."""
import ast
from app.agent_base.host_api.validation import SourceFunction, SourceOperation

def _functions(tree):
    result = {}
    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result[name] = child
                walk(child, name + ".")
    walk(tree)
    return result


def _operations(function):
    result = {"call": {}, "condition": {}, "return": {}}
    result["call_order"] = {}
    result["order_unknown"] = set()
    def walk(node, branches=(), uncertain=False):
        if node is not function and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        uncertain = uncertain or isinstance(node, (
            ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom,
            ast.Try, ast.TryStar, ast.For, ast.AsyncFor, ast.While,
            ast.IfExp, ast.BoolOp, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
        ))
        kind = "call" if isinstance(node, ast.Call) else "return" if isinstance(node, ast.Return) else "condition" if isinstance(node, ast.If) else None
        if kind:
            result[kind].setdefault(node.lineno, []).append((node, branches))
        if isinstance(node, ast.If):
            walk(node.test, branches, uncertain)
            for child in node.body:
                walk(child, branches + ((node.lineno, True),), uncertain)
            for child in node.orelse:
                walk(child, branches + ((node.lineno, False),), uncertain)
        else:
            # Python evaluates call arguments before invoking the outer call;
            # assignment RHS precedes target evaluation. Line order alone is
            # not execution order for multiline nested calls.
            if isinstance(node, ast.Assign):
                children = [node.value, *node.targets]
            elif isinstance(node, ast.Dict):
                children = [child for pair in zip(node.keys, node.values) for child in pair if child is not None]
            else:
                children = ast.iter_child_nodes(node)
            for child in children:
                walk(child, branches, uncertain)
        if isinstance(node, ast.Call):
            result["call_order"][id(node)] = len(result["call_order"])
            if uncertain:
                result["order_unknown"].add(id(node))
    walk(function)
    return result



def python_source_flows(tree):
    flows = []
    for symbol, function in _functions(tree).items():
        facts = _operations(function)
        operations = []
        for kind in ("call", "condition", "return"):
            for line, entries in facts[kind].items():
                for node, branches in entries:
                    name = ""
                    if kind == "call":
                        name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                    operations.append(SourceOperation(kind, line, name, branches,
                        facts["call_order"].get(id(node), 0), id(node) not in facts["order_unknown"]))
        flows.append(SourceFunction(symbol, tuple(operations)))
    return tuple(flows)
