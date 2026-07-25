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


# ---------------------------------------------------------------------------
# A11: /api/knowledge/lab/reviews — each entry contains provenance sub-object
#      with transformation_history (D2 serialization fix)
# ---------------------------------------------------------------------------

def test_a11_reviews_provenance_key_present(client: TestClient) -> None:
    """Every review entry must expose a provenance sub-object (D2 fix)."""
    resp = client.get("/api/knowledge/lab/reviews")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    for entry in entries:
        assert "provenance" in entry, f"entry missing 'provenance': {entry}"
        prov = entry["provenance"]
        assert "transformation_history" in prov, (
            f"provenance missing 'transformation_history': {prov}"
        )
        assert isinstance(prov["transformation_history"], list), (
            "transformation_history must be a list"
        )


# ---------------------------------------------------------------------------
# A12: /api/knowledge/lab/reviews — transformation_history items are returned
#      when entries with non-empty provenance exist in the store (D2 content)
# ---------------------------------------------------------------------------

def test_a12_reviews_provenance_transformation_history_content() -> None:
    """Entries with transformation_history steps expose them in the API."""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient as _TC

    from kri.common.models import Provenance
    from kri.learning.models import ReviewHistoryEntry
    from kri.learning.store import ReviewHistoryStore
    from kri.web.app import create_app

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        store_path = tmp_dir_path / "review_history.jsonl"

        store = ReviewHistoryStore(path=store_path)
        eid = ReviewHistoryEntry.make_entry_id("s1", "mid@a12", "reviewer comment")
        prov = Provenance(
            source_url="https://lore.kernel.org/r/mid@a12",
            transformation_history=["ingested_by:LoreIngestionEngine", "validated:lexical_match"],
        )
        entry = ReviewHistoryEntry(
            entry_id=eid,
            series_id="s1",
            message_id="mid@a12",
            source_url="https://lore.kernel.org/r/mid@a12",
            reviewer_text="reviewer comment",
            extracted_claim="locking",
            evidence_type="review_discussion",
            confidence_basis="lexical_match:lock",
            provenance=prov,
        )
        store.add(entry)

        import unittest.mock as mock
        import kri.web.app as _app_mod
        with mock.patch.object(_app_mod, "_default_cache_dir", return_value=tmp_dir_path):
            tc = _TC(create_app())
            resp = tc.get("/api/knowledge/lab/reviews")

        assert resp.status_code == 200
        entries = resp.json()["entries"]
        matched = [e for e in entries if e["entry_id"] == eid]
        assert matched, "seeded entry not found in response"
        prov_out = matched[0]["provenance"]
        assert prov_out["transformation_history"] == [
            "ingested_by:LoreIngestionEngine",
            "validated:lexical_match",
        ], f"unexpected transformation_history: {prov_out['transformation_history']}"


# ---------------------------------------------------------------------------
# A13: /knowledge-lab page — Provenance Chain column header present (D4 UI fix)
# ---------------------------------------------------------------------------

def test_a13_knowledge_lab_provenance_chain_column(client: TestClient) -> None:
    """The /knowledge-lab page must contain a 'Provenance Chain' column header."""
    resp = client.get("/knowledge-lab")
    assert resp.status_code == 200
    html = resp.text
    assert "Provenance Chain" in html, (
        "knowledge-lab.html missing 'Provenance Chain' column header"
    )


# ---------------------------------------------------------------------------
# A14: /knowledge-lab page — transformation_history rendering JS present (D4 UI fix)
# ---------------------------------------------------------------------------

def test_a14_knowledge_lab_transformation_history_js(client: TestClient) -> None:
    """The /knowledge-lab page JS must reference transformation_history for rendering."""
    resp = client.get("/knowledge-lab")
    assert resp.status_code == 200
    html = resp.text
    assert "transformation_history" in html, (
        "knowledge-lab.html JS missing 'transformation_history' reference"
    )
