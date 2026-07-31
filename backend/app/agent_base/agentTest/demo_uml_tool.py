"""
UmlValidationTool 使用示例

运行方式:
    cd backend && python app/agent_base/agentTest/demo_uml_tool.py
"""

import sys
import os

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import json
from app.agent_base.tools.my_tools import UmlValidationTool


def make_class(name: str, cid: str, methods: list = None):
    return {
        "id": cid, "name": name, "stereotype": "class",
        "attributes": [], "note": "",
        "methods": [{"name": m, "params": "", "return_type": "void"} for m in (methods or [])],
        "position": {"x": 100, "y": 100},
        "size": {"width": 200, "height": 150},
        "provided_interfaces": [], "required_interfaces": [],
    }


def main():
    tool = UmlValidationTool()

    # ──────────────────────────────────────────────
    # 场景 1: 正确设计
    # ──────────────────────────────────────────────
    print("=" * 55)
    print("  场景 1: 无问题的设计")
    print("=" * 55)

    good = {
        "diagrams": [{
            "type": "class", "name": "Domain", "component_id": "",
            "data": {
                "classes": [
                    make_class("User", "cls_user", ["getName", "getOrders"]),
                    make_class("Order", "cls_order", ["getTotal"]),
                ]
            }
        }, {
            "type": "sequence", "name": "GetOrders", "component_id": "",
            "data": {
                "lifelines": [
                    {"id": "ll1", "name": "User", "class_ref": "cls_user", "x": 100, "activations": []},
                    {"id": "ll2", "name": "Order", "class_ref": "cls_order", "x": 300, "activations": []},
                ],
                "messages": [
                    {"id": "m1", "from_lifeline": "ll1", "to_lifeline": "ll2",
                     "label": "getTotal()", "type": "sync", "order": 1, "note": ""},
                ]
            }
        }],
        "consistency_report": [],
    }
    print(tool.run({"diagrams_json": json.dumps(good, ensure_ascii=False)}))
    print()

    # ──────────────────────────────────────────────
    # 场景 2: 有问题 + 自动修复
    # ──────────────────────────────────────────────
    print("=" * 55)
    print("  场景 2: 引用错误 + 模糊匹配自动修复")
    print("=" * 55)

    bad = {
        "diagrams": [{
            "type": "class", "name": "Domain", "component_id": "",
            "data": {
                "classes": [
                    make_class("PaymentService", "cls_pay", ["charge"]),
                ]
            }
        }, {
            "type": "sequence", "name": "Checkout", "component_id": "",
            "data": {
                "lifelines": [
                    # 引用错误: cls_payment → 模糊匹配到 cls_pay
                    {"id": "ll1", "name": "PaymentService", "class_ref": "cls_payment", "x": 100, "activations": []},
                    # 引用完全不存在
                    {"id": "ll2", "name": "Inventory", "class_ref": "cls_inv", "x": 300, "activations": []},
                ],
                "messages": [
                    {"id": "m1", "from_lifeline": "ll1", "to_lifeline": "ll1",
                     "label": "charge()", "type": "sync", "order": 1, "note": ""},
                ]
            }
        }],
        "consistency_report": [],
    }
    print(tool.run({"diagrams_json": json.dumps(bad, ensure_ascii=False)}))
    print()

    # ──────────────────────────────────────────────
    # 场景 3: 空引用自动分配
    # ──────────────────────────────────────────────
    print("=" * 55)
    print("  场景 3: 空 class_ref 自动分配")
    print("=" * 55)

    missing = {
        "diagrams": [{
            "type": "class", "name": "Domain", "component_id": "",
            "data": {
                "classes": [
                    make_class("Logger", "cls_log", ["log"]),
                ]
            }
        }, {
            "type": "sequence", "name": "LogFlow", "component_id": "",
            "data": {
                "lifelines": [
                    # class_ref 为空但 name="Logger" → 自动匹配 cls_log
                    {"id": "ll1", "name": "Logger", "class_ref": "", "x": 100, "activations": []},
                ],
                "messages": [
                    {"id": "m1", "from_lifeline": "ll1", "to_lifeline": "ll1",
                     "label": "log()", "type": "sync", "order": 1, "note": ""},
                ]
            }
        }],
        "consistency_report": [],
    }
    print(tool.run({"diagrams_json": json.dumps(missing, ensure_ascii=False)}))


if __name__ == "__main__":
    main()
