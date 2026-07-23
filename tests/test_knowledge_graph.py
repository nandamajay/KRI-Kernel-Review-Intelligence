"""Unit tests for KnowledgeGraph — the temporal property graph backend.

Zero test coverage existed before this file. Tests cover the core CRUD
operations, basic query, and round-trip serialization.
"""

from __future__ import annotations

from kri.knowledge.graph import KnowledgeGraph


def test_add_node_and_has_node() -> None:
    g = KnowledgeGraph()
    g.add_node("n1", "driver", properties={"subsystem": "i2c"})
    assert g.has_node("n1")
    assert not g.has_node("missing")


def test_add_node_idempotent() -> None:
    g = KnowledgeGraph()
    g.add_node("n1", "driver", properties={"v": 1})
    g.add_node("n1", "driver", properties={"v": 2})
    assert g.node_count() == 1
    assert g.get_node("n1")["properties"]["v"] == 2


def test_node_count_and_edge_count() -> None:
    g = KnowledgeGraph()
    g.add_node("a", "type_a")
    g.add_node("b", "type_b")
    assert g.node_count() == 2
    g.add_edge("a", "b", "depends_on")
    assert g.edge_count() == 1


def test_get_node_returns_none_for_missing() -> None:
    g = KnowledgeGraph()
    assert g.get_node("nonexistent") is None


def test_get_node_returns_record() -> None:
    g = KnowledgeGraph()
    g.add_node("n1", "compatible", properties={"name": "vendor,chip"})
    rec = g.get_node("n1")
    assert rec is not None
    assert rec["node_type"] == "compatible"
    assert rec["properties"]["name"] == "vendor,chip"


def test_query_by_node_type() -> None:
    g = KnowledgeGraph()
    g.add_node("d1", "driver")
    g.add_node("d2", "driver")
    g.add_node("b1", "binding")
    results = g.query({"match": {"node_type": "driver"}})
    ids = {r["node_id"] for r in results}
    assert ids == {"d1", "d2"}
    assert "b1" not in ids


def test_to_dict_from_dict_round_trip() -> None:
    g = KnowledgeGraph()
    g.add_node("n1", "driver", properties={"k": "v"})
    g.add_node("n2", "binding")
    g.add_edge("n1", "n2", "has_binding")
    data = g.to_dict()
    g2 = KnowledgeGraph.from_dict(data)
    assert g2.node_count() == g.node_count()
    assert g2.edge_count() == g.edge_count()
    assert g2.get_node("n1")["properties"] == {"k": "v"}


def test_content_hash_stable_for_same_graph() -> None:
    g = KnowledgeGraph()
    g.add_node("n1", "driver")
    h1 = g.content_hash()
    h2 = g.content_hash()
    assert h1 == h2
    assert isinstance(h1, str) and len(h1) > 0


def test_content_hash_differs_after_mutation() -> None:
    g = KnowledgeGraph()
    g.add_node("n1", "driver")
    h1 = g.content_hash()
    g.add_node("n2", "binding")
    h2 = g.content_hash()
    assert h1 != h2


def test_copy_is_independent() -> None:
    g = KnowledgeGraph()
    g.add_node("n1", "driver")
    g2 = g.copy()
    g2.add_node("n2", "binding")
    assert g.node_count() == 1
    assert g2.node_count() == 2
