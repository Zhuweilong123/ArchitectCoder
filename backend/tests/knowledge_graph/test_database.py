"""Regression checks for edge identity and natural-key deduplication."""

import pytest

from extensions.knowledge_graph.database import KnowledgeGraphDB
from extensions.knowledge_graph.models import EdgeType, GraphEdge


@pytest.fixture
def db(tmp_path):
    database = KnowledgeGraphDB(str(tmp_path / "kg.db"))
    yield database
    database.close()


def _write(db, edges, batch):
    if batch:
        db.upsert_edges_batch(edges)
    else:
        for edge in edges:
            db.upsert_edge(edge)


@pytest.mark.parametrize("batch", [False, True])
def test_upsert_edge_updates_properties_of_existing_id(db, batch):
    _write(db, [GraphEdge(id="contains", source_id="project", target_id="diagram",
                         properties={"index": 0})], batch)
    _write(db, [GraphEdge(id="contains", source_id="project", target_id="diagram",
                         properties={"index": 1}, weight=2.0)], batch)

    edges = db.find_edges(source_id="project")
    assert len(edges) == 1
    assert edges[0].id == "contains"
    assert edges[0].properties == {"index": 1}
    assert edges[0].weight == 2.0


@pytest.mark.parametrize("batch", [False, True])
def test_upsert_edge_keeps_natural_key_deduplication(db, batch):
    _write(db, [GraphEdge(id="old", source_id="a", target_id="b", properties={"label": "uses"})], batch)
    _write(db, [GraphEdge(id="new", source_id="a", target_id="b", properties={"label": "uses"}, weight=3.0)], batch)

    edges = db.find_edges(source_id="a")
    assert len(edges) == 1
    assert edges[0].id == "new"
    assert edges[0].weight == 3.0


@pytest.mark.parametrize("batch", [False, True])
def test_upsert_edge_preserves_distinct_parallel_messages(db, batch):
    _write(db, [
        GraphEdge(id="first", source_id="a", target_id="b", edge_type=EdgeType.MESSAGES,
                  properties={"order": 1, "label": "request"}),
        GraphEdge(id="second", source_id="a", target_id="b", edge_type=EdgeType.MESSAGES,
                  properties={"order": 2, "label": "reply"}),
    ], batch)
    assert {edge.id for edge in db.find_edges(source_id="a")} == {"first", "second"}
