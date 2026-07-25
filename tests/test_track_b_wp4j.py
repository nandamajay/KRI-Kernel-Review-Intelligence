"""Track-B WP4-J tests: lore review ingestion + ReviewHistoryEntry model.

J1  - ReviewHistoryEntry has all required provenance fields
J2  - ReviewHistoryEntry.make_entry_id is deterministic (Sec-40)
J3  - ReviewHistoryEntry round-trips through Pydantic
J4  - ReviewHistoryStore.add deduplicates by entry_id
J5  - ReviewHistoryStore.summarise groups entries by series_id
J6  - LoreIngestionEngine.ingest on Phase-4V2 S1 mbox produces entries with provenance
J7  - Every ingest entry has non-empty source_url and message_id (Tier-0 STOP guard)
J8  - ReviewHistorySummary in IntelligentReport model_dump() when populated
J9  - review_history_summary JS guard present in UI page
J10 - ingest_dataset on dataset index produces at least 1 entry
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kri.common.models import Provenance
from kri.learning.models import (
    CFMCalibrationReport,
    ReviewHistoryEntry,
    ReviewHistorySummary,
)
from kri.learning.store import ReviewHistoryStore
from kri.llm.models import IntelligentReport, PatchReview
from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.web.app import create_app

DATASET_ROOT = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/kri/.kri/lore_review_dataset")
INDEX_PATH = DATASET_ROOT / "index.jsonl"
S1_MBOX = Path("/tmp/kri_v2_s1.mbox")
S13_MBOX = DATASET_ROOT / "series" / "L13_rubikpi_asoc.mbox"


# ---------------------------------------------------------------------------
# J1 - ReviewHistoryEntry has all required provenance fields
# ---------------------------------------------------------------------------


def test_J1_review_history_entry_required_provenance_fields() -> None:
    eid = ReviewHistoryEntry.make_entry_id("sid1", "mid@test", "text")
    entry = ReviewHistoryEntry(
        entry_id=eid,
        series_id="sid1",
        message_id="mid@test",
        source_url="https://lore.kernel.org/r/mid@test",
        reviewer_text="please fix the locking",
        extracted_claim="locking",
        evidence_type="review_discussion",
        confidence_basis="lexical_match:lock",
    )
    assert entry.source_url
    assert entry.message_id
    assert entry.provenance is not None
    assert entry.created_by == "WP4-J/LoreIngestionEngine"
    assert entry.validation_status == "pending"


# ---------------------------------------------------------------------------
# J2 - make_entry_id is deterministic (Sec-40 safety)
# ---------------------------------------------------------------------------


def test_J2_entry_id_is_deterministic() -> None:
    id1 = ReviewHistoryEntry.make_entry_id("sid", "mid@test", "text")
    id2 = ReviewHistoryEntry.make_entry_id("sid", "mid@test", "text")
    assert id1 == id2
    assert len(id1) == 16


# ---------------------------------------------------------------------------
# J3 - ReviewHistoryEntry round-trips through Pydantic
# ---------------------------------------------------------------------------


def test_J3_entry_roundtrip() -> None:
    eid = ReviewHistoryEntry.make_entry_id("sid1", "mid@test", "text")
    entry = ReviewHistoryEntry(
        entry_id=eid,
        series_id="sid1",
        message_id="mid@test",
        source_url="https://lore.kernel.org/r/mid@test",
        reviewer_text="text",
        extracted_claim="style",
        evidence_type="review_discussion",
        confidence_basis="lexical:coding_style",
    )
    d = entry.model_dump()
    entry2 = ReviewHistoryEntry.model_validate(d)
    assert entry2.entry_id == entry.entry_id
    assert entry2.source_url == entry.source_url
    assert entry2.message_id == entry.message_id


# ---------------------------------------------------------------------------
# J4 - ReviewHistoryStore deduplicates by entry_id
# ---------------------------------------------------------------------------


def test_J4_store_deduplication() -> None:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    store = ReviewHistoryStore(path=path)
    eid = ReviewHistoryEntry.make_entry_id("sid", "mid@test", "text")
    entry = ReviewHistoryEntry(
        entry_id=eid,
        series_id="sid",
        message_id="mid@test",
        source_url="https://lore.kernel.org/r/mid@test",
        reviewer_text="text",
        extracted_claim="style",
        evidence_type="review_discussion",
        confidence_basis="x",
    )
    added1 = store.add(entry)
    added2 = store.add(entry)
    assert added1 is True
    assert added2 is False
    assert store.count() == 1


# ---------------------------------------------------------------------------
# J5 - ReviewHistoryStore.summarise groups by series_id
# ---------------------------------------------------------------------------


def test_J5_store_summarise_groups_by_series() -> None:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    store = ReviewHistoryStore(path=path)
    for i, sid in enumerate(["sid_a", "sid_a", "sid_b"]):
        eid = ReviewHistoryEntry.make_entry_id(sid, f"mid@{i}", f"text{i}")
        store.add(ReviewHistoryEntry(
            entry_id=eid,
            series_id=sid,
            message_id=f"mid@{i}",
            source_url=f"https://lore.kernel.org/r/mid@{i}",
            reviewer_text=f"text{i}",
            extracted_claim="style",
            evidence_type="review_discussion",
            confidence_basis="x",
        ))
    summaries = store.summarise()
    assert len(summaries) == 2
    sid_a_sum = next(s for s in summaries if s.series_id == "sid_a")
    assert sid_a_sum.entry_count == 2


# ---------------------------------------------------------------------------
# J6 - LoreIngestionEngine.ingest on real mbox produces entries with provenance
# (only runs if the mbox file exists)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not S1_MBOX.exists(), reason="S1 mbox not present")
def test_J6_ingest_s1_produces_entries_with_provenance() -> None:
    from kri.learning.ingestion import LoreIngestionEngine

    lm = LoreManagerImpl(LoreConfig(cache_dir="/tmp/kri_test_lore_trackb_wp4j"))
    engine = LoreIngestionEngine(lm)
    series_id = "test:s1:wp4j"
    entries = engine.ingest(S1_MBOX, series_id)
    # S1 is a single-patch, single-message mbox — may have 0 review replies
    # (replies = reviewers who responded). The mbox has 1 message (the patch),
    # so 0 review comments is valid. But ingestion must not raise.
    assert isinstance(entries, list)
    for e in entries:
        assert e.source_url, f"entry {e.entry_id} has empty source_url"
        assert e.message_id, f"entry {e.entry_id} has empty message_id"
        assert e.provenance is not None


# ---------------------------------------------------------------------------
# J7 - Every ingest entry has non-empty source_url and message_id
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not S13_MBOX.exists(), reason="L13 Rubikpi mbox not present")
def test_J7_ingest_rubikpi_all_entries_have_provenance() -> None:
    from kri.learning.ingestion import LoreIngestionEngine

    lm = LoreManagerImpl(LoreConfig(cache_dir="/tmp/kri_test_lore_trackb_wp4j"))
    engine = LoreIngestionEngine(lm)
    entries = engine.ingest(S13_MBOX, "test:rubikpi:wp4j")
    # Rubikpi has 17 messages — should produce at least 1 review entry
    assert len(entries) >= 1, "Expected at least 1 entry from rubikpi (17-message thread)"
    for e in entries:
        assert e.source_url, f"entry {e.entry_id} missing source_url (Tier-0 STOP)"
        assert e.message_id, f"entry {e.entry_id} missing message_id (Tier-0 STOP)"


# ---------------------------------------------------------------------------
# J8 - review_history_summary present in IntelligentReport model_dump
# ---------------------------------------------------------------------------


def test_J8_review_history_summary_in_intelligent_report() -> None:
    summary = ReviewHistorySummary(
        series_id="test:wp4j",
        entry_count=3,
        source_urls=["https://lore.kernel.org/r/mid@test"],
        claim_categories={"locking": 2, "style": 1},
        has_maintainer_feedback=True,
    )
    report = IntelligentReport(
        series_id="test",
        series_title="wp4j test",
        review_history_summary=[summary.model_dump()],
    )
    d = report.model_dump()
    assert "review_history_summary" in d
    assert len(d["review_history_summary"]) == 1
    assert d["review_history_summary"][0]["entry_count"] == 3
    assert d["review_history_summary"][0]["has_maintainer_feedback"] is True


# ---------------------------------------------------------------------------
# J9 - review_history_summary JS rendering guard present in UI page
# ---------------------------------------------------------------------------


@pytest.fixture()
def j9_client() -> TestClient:
    from kri.patch_manager import PatchManagerImpl
    lm = LoreManagerImpl(LoreConfig(cache_dir="/tmp/kri_test_lore_trackb_wp4j"))
    pm = PatchManagerImpl(lore_manager=lm)
    return TestClient(create_app(lore_manager=lm, patch_manager=pm))


def test_J9_review_history_summary_js_guard_present(j9_client: TestClient) -> None:
    r = j9_client.get("/")
    assert r.status_code == 200
    assert "review_history_summary" in r.text, (
        "renderIntelligent JS must reference review_history_summary"
    )


# ---------------------------------------------------------------------------
# J10 - ingest_dataset on real index produces at least 1 entry
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not INDEX_PATH.exists(), reason="Dataset index.jsonl not present")
def test_J10_ingest_dataset_produces_entries() -> None:
    from kri.learning.ingestion import ingest_dataset

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        store_path = Path(f.name)

    store = ReviewHistoryStore(path=store_path)
    lm = LoreManagerImpl(LoreConfig(cache_dir="/tmp/kri_test_lore_trackb_wp4j"))
    new_entries = ingest_dataset(
        INDEX_PATH,
        store,
        lm,
        dataset_root=DATASET_ROOT.parent,  # .kri/ dir; mbox_path in index includes 'lore_review_dataset/' prefix
    )
    assert len(new_entries) >= 1, (
        "ingest_dataset produced 0 entries from the dataset index"
    )
    assert store.count() >= 1
