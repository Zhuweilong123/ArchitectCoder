from extensions.design_contract.clang_flow import clang_source_flows


def test_out_of_line_cpp_method_uses_declared_namespace_and_class():
    ast = {"kind": "TranslationUnitDecl", "inner": [
        {"kind": "NamespaceDecl", "name": "engine", "id": "ns", "inner": [
            {"kind": "CXXRecordDecl", "name": "Planner", "id": "planner"}]},
        {"kind": "CXXMethodDecl", "name": "plan", "parentDeclContextId": "planner", "inner": [
            {"kind": "CompoundStmt", "inner": [{"kind": "ReturnStmt", "loc": {"line": 5}}]}]},
    ]}
    facts = clang_source_flows(ast, "", "planner.cpp")
    assert len(facts) == 1
    assert facts[0].symbol == "engine::Planner::plan"
    assert facts[0].operations[0].kind == "return"
