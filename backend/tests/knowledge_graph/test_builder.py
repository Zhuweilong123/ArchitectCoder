"""知识图谱构建单元测试（无需 LLM）。"""
from app.models.uml import (
    Project, UmlDiagram, UmlClass, UmlRelation, UmlMethod, UmlAttribute,
    SeqLifeline, SeqMessage, SeqFragment, CompNode, CompRelation,
    Stereotype, RelationType,
)
from knowledge_graph.builder import GraphBuilder
from knowledge_graph.database import KnowledgeGraphDB


def _class_diagram():
    return UmlDiagram(
        name="Domain", diagram_type="class",
        classes=[
            UmlClass(
                id="class_user", name="User", stereotype=Stereotype.CLASS,
                methods=[UmlMethod(name="getName", return_type="str")],
                attributes=[UmlAttribute(name="uid", type="int")],
            ),
            UmlClass(id="class_order", name="Order"),
        ],
        relations=[UmlRelation(id="rel_1", source="class_user", target="class_order",
                               type=RelationType.ASSOCIATION)],
    )


def test_build_class_diagram(tmp_path):
    db_path = str(tmp_path / "kg.db")
    builder = GraphBuilder(db_path=db_path)
    builder.build_from_project(Project(name="p", diagrams=[_class_diagram()]), "p")
    builder.close()

    db = KnowledgeGraphDB(db_path)
    assert len(db.find_nodes("p", node_type="diagram", source="design")) == 1
    assert len(db.find_nodes("p", node_type="class", source="design")) == 2
    assert len(db.find_nodes("p", node_type="method", source="design")) == 1
    assert len(db.find_nodes("p", node_type="attribute", source="design")) == 1
    assert len(db.find_edges(edge_type="association")) == 1
    db.close()


def test_project_scoped_ids(tmp_path):
    """两个项目含同名类，节点 id 应不同（跨项目文件复制不冲突）。"""
    db_path = str(tmp_path / "kg.db")
    builder = GraphBuilder(db_path=db_path)
    builder.build_from_project(Project(name="A", diagrams=[_class_diagram()]), "A")
    builder.build_from_project(Project(name="B", diagrams=[_class_diagram()]), "B")
    builder.close()

    db = KnowledgeGraphDB(db_path)
    user_a = db.find_nodes("A", node_type="class", name="User", source="design")[0]
    user_b = db.find_nodes("B", node_type="class", name="User", source="design")[0]
    assert user_a.id != user_b.id
    db.close()


def test_idempotent_build(tmp_path):
    """重复构建不崩溃、不产生重复节点。"""
    db_path = str(tmp_path / "kg.db")
    builder = GraphBuilder(db_path=db_path)
    project = Project(name="p", diagrams=[_class_diagram()])
    builder.build_from_project(project, "p")
    builder.build_from_project(project, "p")
    builder.close()

    db = KnowledgeGraphDB(db_path)
    assert len(db.find_nodes("p", node_type="class", source="design")) == 2
    db.close()


def test_cross_reference_class_ref(tmp_path):
    """时序图 lifeline 的 class_ref 应建立 REFERENCES 边指向类节点。"""
    db_path = str(tmp_path / "kg.db")
    seq = UmlDiagram(
        name="Flow", diagram_type="sequence",
        lifelines=[SeqLifeline(id="ll1", name="User", class_ref="class_user")],
    )
    project = Project(name="p", diagrams=[_class_diagram(), seq])
    builder = GraphBuilder(db_path=db_path)
    builder.build_from_project(project, "p")
    builder.close()

    db = KnowledgeGraphDB(db_path)
    assert len(db.find_edges(edge_type="references")) == 1
    db.close()


def test_build_sequence_diagram(tmp_path):
    """时序图：lifeline + messages（含 self-message）+ fragment。"""
    db_path = str(tmp_path / "kg.db")
    seq = UmlDiagram(
        name="Flow", diagram_type="sequence",
        lifelines=[
            SeqLifeline(id="ll1", name="Client"),
            SeqLifeline(id="ll2", name="Server"),
        ],
        messages=[
            SeqMessage(id="m1", from_lifeline="ll1", to_lifeline="ll2",
                       label="request()", order=1),
            SeqMessage(id="m2", from_lifeline="ll1", to_lifeline="ll1",
                       label="log()", order=2),
            SeqMessage(id="m3", from_lifeline="ll1", to_lifeline="ll1",
                       label="log2()", order=3),
        ],
        fragments=[SeqFragment(id="f1", type="loop", label="[retry]")],
    )
    builder = GraphBuilder(db_path=db_path)
    builder.build_from_project(Project(name="p", diagrams=[seq]), "p")
    builder.close()

    db = KnowledgeGraphDB(db_path)
    assert len(db.find_nodes("p", node_type="lifeline", source="design")) == 2
    assert len(db.find_edges(edge_type="messages")) == 3  # 含 self-message
    assert len(db.find_nodes("p", node_type="fragment", source="design")) == 1
    db.close()


def test_build_component_diagram(tmp_path):
    """组件图：component + interface + dependency 关系。"""
    db_path = str(tmp_path / "kg.db")
    comp = UmlDiagram(
        name="Arch", diagram_type="component",
        components=[
            CompNode(id="comp_a", name="A", provided_interfaces=["IA"]),
            CompNode(id="comp_b", name="B", required_interfaces=["IA"]),
        ],
        comp_relations=[CompRelation(id="crel_1", source="comp_a", target="comp_b")],
    )
    builder = GraphBuilder(db_path=db_path)
    builder.build_from_project(Project(name="p", diagrams=[comp]), "p")
    builder.close()

    db = KnowledgeGraphDB(db_path)
    assert len(db.find_nodes("p", node_type="component", source="design")) == 2
    # 同名接口 IA 被 A 提供、B 需要——自然键按 name 去重，合并为单个接口节点
    assert len(db.find_nodes("p", node_type="interface", source="design")) == 1
    assert len(db.find_edges(edge_type="dependency")) == 1
    db.close()


def test_class_relation_types(tmp_path):
    """类关系五种类型均正确映射为边。"""
    db_path = str(tmp_path / "kg.db")
    classes = [UmlClass(id=f"c_{n}", name=n) for n in ("A", "B", "C", "D", "E", "F")]
    relations = [
        UmlRelation(id="r1", source="c_A", target="c_B", type=RelationType.INHERITANCE),
        UmlRelation(id="r2", source="c_A", target="c_C", type=RelationType.COMPOSITION),
        UmlRelation(id="r3", source="c_A", target="c_D", type=RelationType.AGGREGATION),
        UmlRelation(id="r4", source="c_A", target="c_E", type=RelationType.REALIZATION),
        UmlRelation(id="r5", source="c_A", target="c_F", type=RelationType.DEPENDENCY),
    ]
    diag = UmlDiagram(name="Domain", diagram_type="class",
                      classes=classes, relations=relations)
    builder = GraphBuilder(db_path=db_path)
    builder.build_from_project(Project(name="p", diagrams=[diag]), "p")
    builder.close()

    db = KnowledgeGraphDB(db_path)
    for et in ("inherits", "composition", "aggregation", "realization", "dependency"):
        assert len(db.find_edges(edge_type=et)) == 1, f"edge '{et}' missing"
    db.close()


def test_component_relation_edge_cross_project(tmp_path):
    """相同 crel id 的项目文件跨项目构建，边 id 不冲突。"""
    db_path = str(tmp_path / "kg.db")

    def _comp():
        return UmlDiagram(
            name="Arch", diagram_type="component",
            components=[CompNode(id="comp_a", name="A"), CompNode(id="comp_b", name="B")],
            comp_relations=[CompRelation(id="crel_1", source="comp_a", target="comp_b")],
        )

    builder = GraphBuilder(db_path=db_path)
    builder.build_from_project(Project(name="A", diagrams=[_comp()]), "A")
    builder.build_from_project(Project(name="B", diagrams=[_comp()]), "B")
    builder.close()

    db = KnowledgeGraphDB(db_path)
    assert len(db.find_edges(edge_type="dependency")) == 2
    db.close()
