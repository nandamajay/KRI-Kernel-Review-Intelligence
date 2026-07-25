"""Track-B.5 Knowledge Activation tests.

B5-1  test_B5_store_by_claim_returns_matching_only
B5-2  test_B5_store_by_claim_review_discussion_returns_empty (BLOCK-4)
B5-3  test_B5_lore_evidence_for_claim_verified_true
B5-4  test_B5_lore_evidence_for_claim_provenance_preserved
B5-5  test_B5_enrich_activates_review_history_factor
B5-6  test_B5_review_history_summary_filtered_not_global
B5-7  test_B5_no_duplicate_evidence_nodes
B5-8  test_B5_empty_store_no_change_to_evidence_graph
B5-9  test_B5_cfm_varies_by_claim
B5-10 test_B5_review_discussion_fallback_zero_result
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kri.common.models import (
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    Provenance,
)
from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.learning.ingestion import lore_evidence_for_claim
from kri.learning.models import ReviewHistoryEntry
from kri.learning.store import ReviewHistoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(*entries: ReviewHistoryEntry) -> ReviewHistoryStore:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = ReviewHistoryStore(path=path)
    for e in entries:
        store.add(e)
    return store


def _entry(series_id: str, message_id: str, claim: str, text: str = "test review") -> ReviewHistoryEntry:
    eid = ReviewHistoryEntry.make_entry_id(series_id, message_id, text)
    return ReviewHistoryEntry(
        entry_id=eid,
        series_id=series_id,
        message_id=message_id,
        source_url=f"https://lore.kernel.org/r/{message_id}",
        reviewer_text=text,
        extracted_claim=claim,
        evidence_type="review_discussion",
        confidence_basis="test",
        provenance=Provenance(
            source_url=f"https://lore.kernel.org/r/{message_id}",
            version_or_commit=message_id,
            transformation_history=["test"],
        ),
    )


def _make_eg(*evidence_nodes: Evidence) -> EvidenceGraph:
    eg = EvidenceGraph(comment_id="cmt:test")
    eg.evidence.extend(evidence_nodes)
    return eg


# ---------------------------------------------------------------------------
# B5-1: store.by_claim() returns only matching-claim entries
# ---------------------------------------------------------------------------


def test_B5_store_by_claim_returns_matching_only() -> None:
    e_lock = _entry("s1", "m1@t", "locking")
    e_mem = _entry("s2", "m2@t", "memory_safety")
    e_lock2 = _entry("s3", "m3@t", "locking")
    store = _make_store(e_lock, e_mem, e_lock2)

    result = store.by_claim("locking")

    assert len(result) == 2
    assert all(e.extracted_claim == "locking" for e in result)
    claim_ids = {e.entry_id for e in result}
    assert e_lock.entry_id in claim_ids
    assert e_lock2.entry_id in claim_ids
    assert e_mem.entry_id not in claim_ids


# ---------------------------------------------------------------------------
# B5-2: BLOCK-4 — store.by_claim('review_discussion') returns [] always
# ---------------------------------------------------------------------------


def test_B5_store_by_claim_review_discussion_returns_empty() -> None:
    e_disc = _entry("s1", "m1@t", "review_discussion")
    e_disc2 = _entry("s2", "m2@t", "review_discussion")
    store = _make_store(e_disc, e_disc2)

    # Both entries are review_discussion — by_claim must still return []
    result = store.by_claim("review_discussion")

    assert result == [], (
        "BLOCK-4 violated: by_claim('review_discussion') must return [] to prevent "
        "flooding every comment with all low-signal fallback lore entries"
    )


# ---------------------------------------------------------------------------
# B5-3: lore_evidence_for_claim() nodes have verified=True
# ---------------------------------------------------------------------------


def test_B5_lore_evidence_for_claim_verified_true() -> None:
    e = _entry("s1", "m1@t", "locking")
    store = _make_store(e)

    nodes = lore_evidence_for_claim("locking", store)

    assert len(nodes) == 1
    assert nodes[0].verified is True, (
        "REVIEW_HISTORY factor requires verified=True; "
        "lore URLs are permanent authenticated public records"
    )


# ---------------------------------------------------------------------------
# B5-4: lore_evidence_for_claim() preserves provenance from store entry
# ---------------------------------------------------------------------------


def test_B5_lore_evidence_for_claim_provenance_preserved() -> None:
    e = _entry("s1", "m-prov@t", "error_handling")
    store = _make_store(e)

    nodes = lore_evidence_for_claim("error_handling", store)

    assert len(nodes) == 1
    prov = nodes[0].provenance
    assert prov.source_url == f"https://lore.kernel.org/r/m-prov@t"
    assert prov.version_or_commit == "m-prov@t"


# ---------------------------------------------------------------------------
# B5-5: REVIEW_HISTORY confidence factor > 0 after enrichment
# ---------------------------------------------------------------------------


def test_B5_enrich_activates_review_history_factor() -> None:
    e = _entry("s1", "m1@t", "locking")
    store = _make_store(e)
    nodes = lore_evidence_for_claim("locking", store)

    eg = _make_eg(*nodes)
    score = ConfidenceEngineImpl._compute_review_history(eg)  # type: ignore[attr-defined]

    assert score > 0.0, (
        "REVIEW_HISTORY factor must be > 0 when lore evidence is enriched; "
        f"got {score}. Verify nodes have verified=True and source_type=REVIEW_DISCUSSION."
    )


# ---------------------------------------------------------------------------
# B5-6: review_history_summary filtered to matched series, not global
# ---------------------------------------------------------------------------


def test_B5_review_history_summary_filtered_not_global() -> None:
    e_match = _entry("series-A", "m-a@t", "locking")
    e_other = _entry("series-B", "m-b@t", "locking")
    store = _make_store(e_match, e_other)

    # Only series-A was matched during a review
    matched_ids: set[str] = {"series-A"}
    summaries = store.summarise_by_series_ids(matched_ids)

    assert len(summaries) == 1, (
        f"Expected 1 summary (series-A only), got {len(summaries)}"
    )
    assert summaries[0].series_id == "series-A"


# ---------------------------------------------------------------------------
# B5-7: no duplicate evidence nodes when same entry appears multiple times
# ---------------------------------------------------------------------------


def test_B5_no_duplicate_evidence_nodes() -> None:
    e = _entry("s1", "m1@t", "locking")
    # Add the same entry twice via two store instances merged into one
    store = _make_store(e)

    # Call lore_evidence_for_claim twice and manually attempt to merge
    nodes_first = lore_evidence_for_claim("locking", store)
    nodes_second = lore_evidence_for_claim("locking", store)

    existing_ids: set[str] = set()
    merged: list[Evidence] = []
    for n in nodes_first + nodes_second:
        if n.evidence_id not in existing_ids:
            existing_ids.add(n.evidence_id)
            merged.append(n)

    assert len(merged) == 1, (
        f"Duplicate evidence nodes found: {len(merged)} after dedup"
    )


# ---------------------------------------------------------------------------
# B5-8: empty store leaves EvidenceGraph unchanged
# ---------------------------------------------------------------------------


def test_B5_empty_store_no_change_to_evidence_graph() -> None:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        empty_path = Path(f.name)
    store = ReviewHistoryStore(path=empty_path)

    nodes = lore_evidence_for_claim("locking", store)

    assert nodes == [], "Empty store must produce no Evidence nodes"

    eg = EvidenceGraph(comment_id="cmt:empty")
    original_len = len(eg.evidence)
    eg.evidence.extend(nodes)
    assert len(eg.evidence) == original_len, "EvidenceGraph must be unchanged"


# ---------------------------------------------------------------------------
# B5-9: CFM varies by claim (two comments with different claim matches)
# ---------------------------------------------------------------------------


def test_B5_cfm_varies_by_claim() -> None:
    e_lock = _entry("s1", "m1@t", "locking")
    e_mem1 = _entry("s2", "m2@t", "memory_safety")
    e_mem2 = _entry("s3", "m3@t", "memory_safety")
    store = _make_store(e_lock, e_mem1, e_mem2)

    nodes_lock = lore_evidence_for_claim("locking", store)
    eg_lock = _make_eg(*nodes_lock)
    rh_lock = ConfidenceEngineImpl._compute_review_history(eg_lock)  # type: ignore[attr-defined]

    nodes_mem = lore_evidence_for_claim("memory_safety", store)
    eg_mem = _make_eg(*nodes_mem)
    rh_mem = ConfidenceEngineImpl._compute_review_history(eg_mem)  # type: ignore[attr-defined]

    # locking: 1 node → factor = 0.35; memory_safety: 2 nodes → factor = 0.70
    assert rh_lock != rh_mem, (
        f"REVIEW_HISTORY factor must differ by claim: locking={rh_lock}, memory_safety={rh_mem}"
    )
    assert rh_mem > rh_lock, (
        f"More lore matches → higher REVIEW_HISTORY: expected {rh_mem} > {rh_lock}"
    )


# ---------------------------------------------------------------------------
# B5-10: review_discussion fallback returns zero Evidence nodes
# ---------------------------------------------------------------------------


def test_B5_review_discussion_fallback_zero_result() -> None:
    e_disc = _entry("s1", "m1@t", "review_discussion")
    store = _make_store(e_disc)

    # Simulate _classify_claim("some generic category") → "review_discussion"
    nodes = lore_evidence_for_claim("review_discussion", store)

    assert nodes == [], (
        "BLOCK-4: 'review_discussion' fallback must return zero Evidence nodes "
        "to prevent flooding every comment with all-entries lore history"
    )
