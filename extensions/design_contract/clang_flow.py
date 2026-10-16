"""Conservative C++ flow facts from Clang JSON. No diagram rules or execution."""
from app.agent_base.host_api.validation import SourceFunction, SourceOperation


def clang_source_flows(document, source_text, source_path):
    functions = []
    contexts = {}
    def line(node):
        location = node.get("loc") or (node.get("range") or {}).get("begin") or {}
        location = location.get("expansionLoc") or location.get("spellingLoc") or location
        if isinstance(location.get("line"), int):
            return location["line"]
        offset = location.get("offset")
        return source_text.count("\n", 0, offset) + 1 if isinstance(offset, int) else None

    def children(node):
        return [child for child in node.get("inner", []) if isinstance(child, dict)]

    def collect_contexts(node, prefix=""):
        kind, name = node.get("kind"), node.get("name", "")
        if name and kind in {"NamespaceDecl", "CXXRecordDecl", "RecordDecl"}:
            prefix += name + "::"
            if node.get("id"):
                contexts[node["id"]] = prefix
        for child in children(node):
            collect_contexts(child, prefix)

    collect_contexts(document)

    def callee(node):
        if node.get("kind") == "MemberExpr":
            return node.get("name", "")
        ref = node.get("referencedDecl") or {}
        if ref.get("name"):
            return ref["name"]
        for child in children(node):
            found = callee(child)
            if found:
                return found
        return ""

    def flow(function, symbol):
        operations, rank = [], 0
        def walk(node, branches=(), uncertain=False):
            nonlocal rank
            kind, inner, current_line = node.get("kind"), children(node), line(node)
            if node is not function and kind in {"FunctionDecl", "CXXMethodDecl", "LambdaExpr"}:
                return
            uncertain = uncertain or kind in {
                "ForStmt", "WhileStmt", "DoStmt", "CXXForRangeStmt", "CXXTryStmt", "CXXCatchStmt",
                "SwitchStmt", "ConditionalOperator", "BinaryConditionalOperator", "CoawaitExpr", "CoyieldExpr",
            } or (kind == "BinaryOperator" and node.get("opcode") in {"&&", "||"})
            if kind == "IfStmt" and current_line:
                operations.append(SourceOperation("condition", current_line, branches=branches))
                body_count = 2 if node.get("hasElse") else 1
                for child in inner[:-body_count]:
                    walk(child, branches, uncertain)
                for index, child in enumerate(inner[-body_count:]):
                    walk(child, branches + ((current_line, index == 0),), uncertain)
                return
            if kind == "ReturnStmt" and current_line:
                operations.append(SourceOperation("return", current_line, branches=branches))
            # Argument calls have unspecified relative order in C++.
            is_call = kind in {"CallExpr", "CXXMemberCallExpr", "CXXOperatorCallExpr"}
            for index, child in enumerate(inner):
                walk(child, branches, uncertain or (is_call and len(inner) > 2 and index > 0))
            if is_call and current_line:
                operations.append(SourceOperation("call", current_line,
                    callee(inner[0]) if inner else "", branches, rank, not uncertain))
                rank += 1
        walk(function)
        return SourceFunction(symbol, tuple(operations))

    def visit(node, prefix="", inherited_file=None):
        if not isinstance(node, dict):
            return
        kind, name = node.get("kind"), node.get("name", "")
        file = (node.get("loc") or {}).get("file", inherited_file)
        if file and str(file).replace("\\", "/") != str(source_path).replace("\\", "/"):
            return
        if kind in {"FunctionDecl", "CXXMethodDecl"} and name:
            if any(child.get("kind") == "CompoundStmt" for child in children(node)):
                owner = contexts.get(node.get("parentDeclContextId"), prefix)
                functions.append(flow(node, owner + name))
            return
        next_prefix = prefix + name + "::" if name and kind in {"NamespaceDecl", "CXXRecordDecl", "RecordDecl"} else prefix
        for child in children(node):
            visit(child, next_prefix, file)
    visit(document)
    return tuple(functions)
