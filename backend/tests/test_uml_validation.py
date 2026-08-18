"""UmlValidationTool 单元测试（无需 LLM）。"""
import json

from app.agent_base.tools.my_tools import UmlValidationTool


def make_class(name, cid, methods=None):
    return {
        "id": cid, "name": name, "stereotype": "class",
        "attributes": [], "note": "",
        "methods": [{"name": m, "params": "", "return_type": "void"} for m in (methods or [])],
        "position": {"x": 100, "y": 100},
        "size": {"width": 200, "height": 150},
        "provided_interfaces": [], "required_interfaces": [],
    }


def _validate(diagrams):
    tool = UmlValidationTool()
    payload = {"diagrams": diagrams, "consistency_report": []}
    return tool.run({"diagrams_json": json.dumps(payload, ensure_ascii=False)})


def test_valid_design_passes():
    diagrams = [
        {"type": "class", "name": "Domain", "component_id": "",
         "data": {"classes": [
             make_class("User", "cls_user", ["getName", "getOrders"]),
             make_class("Order", "cls_order", ["getTotal"]),
         ]}},
        {"type": "sequence", "name": "GetOrders", "component_id": "",
         "data": {
             "lifelines": [
                 {"id": "ll1", "name": "User", "class_ref": "cls_user", "x": 100, "activations": []},
                 {"id": "ll2", "name": "Order", "class_ref": "cls_order", "x": 300, "activations": []},
             ],
             "messages": [
                 {"id": "m1", "from_lifeline": "ll1", "to_lifeline": "ll2",
                  "label": "getTotal()", "type": "sync", "order": 1, "note": ""},
             ],
         }},
    ]
    report = _validate(diagrams)
    assert "未发现一致性问题" in report


def test_bad_class_ref_is_reported():
    diagrams = [
        {"type": "class", "name": "Domain", "component_id": "",
         "data": {"classes": [make_class("PaymentService", "cls_pay", ["charge"])]}},
        {"type": "sequence", "name": "Checkout", "component_id": "",
         "data": {
             "lifelines": [
                 {"id": "ll1", "name": "PaymentService", "class_ref": "cls_payment",
                  "x": 100, "activations": []},
                 {"id": "ll2", "name": "Inventory", "class_ref": "cls_inv",
                  "x": 300, "activations": []},
             ],
             "messages": [
                 {"id": "m1", "from_lifeline": "ll1", "to_lifeline": "ll1",
                  "label": "charge()", "type": "sync", "order": 1, "note": ""},
             ],
         }},
    ]
    report = _validate(diagrams)
    assert "UML 设计验证报告" in report
    assert "未发现一致性问题" not in report


def test_empty_class_ref_does_not_crash():
    diagrams = [
        {"type": "class", "name": "Domain", "component_id": "",
         "data": {"classes": [make_class("Logger", "cls_log", ["log"])]}},
        {"type": "sequence", "name": "LogFlow", "component_id": "",
         "data": {
             "lifelines": [
                 {"id": "ll1", "name": "Logger", "class_ref": "", "x": 100, "activations": []},
             ],
             "messages": [
                 {"id": "m1", "from_lifeline": "ll1", "to_lifeline": "ll1",
                  "label": "log()", "type": "sync", "order": 1, "note": ""},
             ],
         }},
    ]
    report = _validate(diagrams)
    assert isinstance(report, str) and report  # 空 class_ref 应被自动处理而不抛异常


def test_invalid_json_returns_error():
    tool = UmlValidationTool()
    report = tool.run({"diagrams_json": "{not valid json"})
    assert "验证失败" in report
