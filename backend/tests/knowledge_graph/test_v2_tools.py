"""知识图谱 v2 工具集单元测试（无需 LLM）。"""
from tests.support.knowledge_graph import build_knowledge_graph, knowledge_graph_tools
from tests.support.tool_helpers import run_json_tool, tool_by_name

_run = run_json_tool
_tool_by_name = tool_by_name


_build_kg = build_knowledge_graph
_tools = knowledge_graph_tools


def test_kg_map(tmp_path):
    db_path, source_dir, _ = _build_kg(tmp_path)
    result = _run(_tool_by_name(_tools(db_path, source_dir), "get_project_map"), {})
    assert "error" not in result
    assert result["project_id"] == "proj"
    assert result["stats"]["total_nodes"] > 0
    diagrams = result["diagrams"]
    assert len(diagrams) == 1
    assert diagrams[0]["name"] == "Domain"
    assert diagrams[0]["class_count"] == 2
    names = {c["name"] for c in result["key_classes"]}
    assert "User" in names
    # 地图是紧凑摘要：不含 methods[]/attributes[] 全量
    assert result["files"]["source_count"] == 2


def test_kg_locate(tmp_path):
    db_path, source_dir, _ = _build_kg(tmp_path)
    result = _run(_tool_by_name(_tools(db_path, source_dir), "find_nodes"),
                  {"query": "User"})
    assert "error" not in result
    hits = result["results"]
    assert any(h["name"] == "User" for h in hits)
    user = next(h for h in hits if h["name"] == "User")
    # code 层命中带 source_file 定位
    if user["source"] == "code":
        assert user.get("file")


def test_kg_locate_empty_query(tmp_path):
    db_path, source_dir, _ = _build_kg(tmp_path)
    result = _run(_tool_by_name(_tools(db_path, source_dir), "find_nodes"),
                  {"query": ""})
    assert "error" in result


def test_kg_locate_lineno(tmp_path):
    """code 层节点应带 read_file 就绪坐标（0 基 offset + limit），消除 off-by-one。"""
    db_path, source_dir, _ = _build_kg(tmp_path)
    result = _run(_tool_by_name(_tools(db_path, source_dir), "find_nodes"),
                  {"query": "getName", "node_types": ["method"], "source": "code"})
    hits = [h for h in result["results"] if h["name"] == "getName"]
    assert hits
    m = hits[0]
    assert m["file"]
    # a.py: "class User:" 第 1 行 (1 基)，"def getName" 第 2-3 行
    # AST lineno=2 → offset=1 (0 基)；end_lineno=3 → limit=2 行
    assert m["offset"] == 1
    assert m["limit"] == 2


def test_kg_expand(tmp_path):
    db_path, source_dir, _ = _build_kg(tmp_path)
    tools = _tools(db_path, source_dir)
    # 拿 User 设计类节点 id
    located = _run(_tool_by_name(tools, "find_nodes"), {"query": "User", "node_types": ["class"], "source": "design"})
    user = next(h for h in located["results"] if h["name"] == "User")
    result = _run(_tool_by_name(tools, "expand_neighbors"), {"node_ids": [user["id"]]})
    assert "error" not in result
    assert result["direction"] == "outgoing"
    assert result["results"]["returned"] >= 1
    # 有界包装字段存在
    assert "truncated" in result["results"]


def test_kg_impact(tmp_path):
    db_path, source_dir, _ = _build_kg(tmp_path)
    tools = _tools(db_path, source_dir)
    # Order 设计类节点：被 User（association）与 b.py（implements）依赖
    located = _run(_tool_by_name(tools, "find_nodes"), {"query": "Order", "node_types": ["class"], "source": "design"})
    order = next(h for h in located["results"] if h["name"] == "Order")
    result = _run(_tool_by_name(tools, "analyze_impact"), {"node_id": order["id"]})
    assert "error" not in result
    assert result["target"]["name"] == "Order"
    assert result["total_affected"] >= 1
    direct = result["direct_dependents"]
    # 设计层 User 通过 association 依赖 Order；b.py 通过 implements 依赖 Order
    assert any("class" in direct or "source_file" in direct for _ in [direct])


def test_kg_diff(tmp_path):
    db_path, source_dir, _ = _build_kg(tmp_path)
    result = _run(_tool_by_name(_tools(db_path, source_dir), "compare_design_code"), {})
    assert "error" not in result
    summary = result["summary"]
    # User/Order 均被实现 → 无缺失；Admin 无设计 → extra_code
    assert summary["missing_implementations"] == 0
    assert summary["extra_code"] >= 1
    assert "total_design_classes" in summary
    assert "items" in result


def test_to_openai_schema(tmp_path):
    db_path, source_dir, _ = _build_kg(tmp_path)
    tool = _tool_by_name(_tools(db_path, source_dir), "find_nodes")
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "find_nodes"
    props = schema["function"]["parameters"]["properties"]
    assert "query" in props
    assert "query" in schema["function"]["parameters"]["required"]


def test_factory_wiring(tmp_path):
    db_path, source_dir, _ = _build_kg(tmp_path)
    tools = _tools(db_path, source_dir)
    names = [t.name for t in tools]
    assert names == ["get_project_map", "find_nodes", "expand_neighbors", "analyze_impact", "compare_design_code"]
