"""知识图谱构建单元测试（无需 LLM）。"""
from app.models.uml import (
    Project, UmlDiagram, UmlClass, UmlRelation, UmlMethod, UmlAttribute,
    SeqLifeline, Stereotype, RelationType,
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
