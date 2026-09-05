"""回归测试：UML prompt 按需加载设计指南和跨图案例说明。"""

from app.services.uml_prompting import _build_global_prompt


def test_cross_guide_loads_cross_example_description_on_demand():
    prompt, system, is_empty = _build_global_prompt(
        diagrams=[{
            "diagram_type": "component",
            "name": "Architecture",
            "components": [{"id": "comp_auth", "name": "AuthService"}],
            "comp_relations": [],
        }],
        instructions="检查跨图一致性",
        scope={
            "guides_needed": ["cross"],
            "include_index": True,
            "include_all_rules": True,
            "output_scope": "all",
        },
    )

    assert not is_empty
    assert "## 7. 推荐跨图案例" in system
    assert "唯一数据源" in system
    assert "cross_diagram_example.umlproj" in system
    assert "## Existing Diagram Data:" in prompt
