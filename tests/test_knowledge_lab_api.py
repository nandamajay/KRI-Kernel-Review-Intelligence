"""Track-C C5: Tests for Knowledge Lab API endpoints.

Agent 7 requirement: empty-state tests (KRI_KERNEL_PATH unset) and
schema validation. All endpoints must return 200 with valid JSON even when
no extraction has run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kri.web.app import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# A1: /api/knowledge/lab/stats — 200 + required keys in empty state
# ---------------------------------------------------------------------------

def test_a1_stats_empty_state(client: TestClient) -> None:
    resp = client.get("/api/knowledge/lab/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "node_count" in data
    assert "edge_count" in data
    assert "file_count" in data
    assert "source_git_sha" in data
    assert "review_entry_count" in data
    assert isinstance(data["node_count"], int)
    assert isinstance(data["review_entry_count"], int)


# ---------------------------------------------------------------------------
# A2: /api/knowledge/lab/reviews — 200 + entries key
# ---------------------------------------------------------------------------

def test_a2_reviews_empty_state(client: TestClient) -> None:
    resp = client.get("/api/knowledge/lab/reviews")
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert isinstance(data["entries"], list)


# ---------------------------------------------------------------------------
# A3: /api/knowledge/lab/rules — 200 + rules key
# ---------------------------------------------------------------------------

def test_a3_rules_empty_state(client: TestClient) -> None:
    resp = client.get("/api/knowledge/lab/rules")
    assert resp.status_code == 200
    data = resp.json()
    assert "rules" in data
    assert isinstance(data["rules"], list)


# ---------------------------------------------------------------------------
# A4: rules schema — each rule has required fields
# ---------------------------------------------------------------------------

def test_a4_rules_schema(client: TestClient) -> None:
    resp = client.get("/api/knowledge/lab/rules")
    assert resp.status_code == 200
    rules = resp.json()["rules"]
    required = {"rule_id", "category", "rule_type", "description"}
    for rule in rules:
        for field in required:
            assert field in rule, f"rule missing field '{field}': {rule}"


# ---------------------------------------------------------------------------
# A5: reviews schema — each entry has provenance fields
# ---------------------------------------------------------------------------

def test_a5_reviews_schema(client: TestClient) -> None:
    resp = client.get("/api/knowledge/lab/reviews")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    provenance_fields = {"entry_id", "series_id", "source_url", "message_id", "extracted_claim"}
    for entry in entries:
        for field in provenance_fields:
            assert field in entry, f"review entry missing '{field}': {entry}"


# ---------------------------------------------------------------------------
# A6: /knowledge-lab page loads (200)
# ---------------------------------------------------------------------------

def test_a6_knowledge_lab_page_loads(client: TestClient) -> None:
    resp = client.get("/knowledge-lab")
    assert resp.status_code == 200
    assert "Knowledge Lab" in resp.text


# ---------------------------------------------------------------------------
# A7: /knowledge-lab page contains key JS identifiers
# ---------------------------------------------------------------------------

def test_a7_knowledge_lab_js_guards(client: TestClient) -> None:
    resp = client.get("/knowledge-lab")
    assert resp.status_code == 200
    html = resp.text
    assert "knowledge/lab/stats" in html
    assert "knowledge/lab/reviews" in html
    assert "knowledge/lab/rules" in html


# ---------------------------------------------------------------------------
# A8: stats node_count is non-negative integer
# ---------------------------------------------------------------------------

def test_a8_stats_node_count_non_negative(client: TestClient) -> None:
    resp = client.get("/api/knowledge/lab/stats")
    assert resp.status_code == 200
    assert resp.json()["node_count"] >= 0


# ---------------------------------------------------------------------------
# A9: rules are sorted by rule_id (determinism)
# ---------------------------------------------------------------------------

def test_a9_rules_sorted(client: TestClient) -> None:
    resp = client.get("/api/knowledge/lab/rules")
    rules = resp.json()["rules"]
    ids = [r["rule_id"] for r in rules]
    assert ids == sorted(ids), "rules must be sorted by rule_id"


# ---------------------------------------------------------------------------
# A10: all three endpoints return Content-Type: application/json
# ---------------------------------------------------------------------------

def test_a10_content_type(client: TestClient) -> None:
    for path in ["/api/knowledge/lab/stats", "/api/knowledge/lab/reviews", "/api/knowledge/lab/rules"]:
        resp = client.get(path)
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct, f"{path} returned wrong content-type: {ct}"
