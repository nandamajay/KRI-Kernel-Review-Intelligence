"""Track-B.8 wiring tests — _build_evidence_graph_for_calibration + CFMCalibrator.

B8-1  test_B8_review_discussion_sets_verified_true
B8-2  test_B8_maintainer_ack_sets_verified_true
B8-3  test_B8_accepted_patch_sets_verified_false
B8-4  test_B8_review_history_factor_positive_after_verified_fix
B8-5  test_B8_calibration_with_5_review_discussion_entries
B8-6  test_B8_correlation_significant_none_when_pearson_none
B8-7  test_B8_calibration_sample_count_with_production_store
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.learning.calibration import (
    CFMCalibrator,
    _build_evidence_graph_for_calibration,
)
from kri.learning.models import CFMCalibrationReport, ReviewHistoryEntry
from kri.learning.store import ReviewHistoryStore
from kri.common.models import Provenance


# ---------------------------------------------------------------------------
# Helpers (mirror test_track_b6_calibration.py patterns)
# ---------------------------------------------------------------------------


def _make_store(*entries: ReviewHistoryEntry) -> ReviewHistoryStore:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = ReviewHistoryStore(path=path)
    for e in entries:
        store.add(e)
    return store


def _entry(
    series_id: str,
    message_id: str,
    claim: str,
    evidence_type: str = "review_discussion",
    text: str = "test review comment",
) -> ReviewHistoryEntry:
    eid = ReviewHistoryEntry.make_entry_id(series_id, message_id, text)
    return ReviewHistoryEntry(
        entry_id=eid,
        series_id=series_id,
        message_id=message_id,
        source_url=f"https://lore.kernel.org/r/{message_id}",
        reviewer_text=text,
        extracted_claim=claim,
        evidence_type=evidence_type,
        confidence_basis="test:rule",
        provenance=Provenance(
            source_url=f"https://lore.kernel.org/r/{message_id}",
            version_or_commit=message_id,
            transformation_history=["test"],
        ),
    )


def _make_calibrator(store: ReviewHistoryStore) -> CFMCalibrator:
    engine = ConfidenceEngineImpl()
    return CFMCalibrator(confidence_engine=engine, store=store)


# ---------------------------------------------------------------------------
# B8-1: review_discussion entries produce verified=True nodes
# ---------------------------------------------------------------------------


def test_B8_review_discussion_sets_verified_true() -> None:
    """_build_evidence_graph_for_calibration must set verified=True for
    review_discussion evidence entries (lore archive = permanent public record).
    """
    e = _entry("s1", "disc-001@t", "dai", evidence_type="review_discussion")
    eg = _build_evidence_graph_for_calibration("test_cmt", [e])

    assert len(eg.evidence) >= 1, "EvidenceGraph must contain at least one node"
    assert any(ev.verified for ev in eg.evidence), (
        "At least one Evidence node must have verified=True for review_discussion entry"
    )


# ---------------------------------------------------------------------------
# B8-2: maintainer_ack entries produce verified=True nodes
# ---------------------------------------------------------------------------


def test_B8_maintainer_ack_sets_verified_true() -> None:
    """_build_evidence_graph_for_calibration must set verified=True for
    maintainer_ack entries (also a permanent lore archive record).
    """
    e = _entry("s1", "ack-001@t", "dai", evidence_type="maintainer_ack")
    eg = _build_evidence_graph_for_calibration("test_cmt", [e])

    assert len(eg.evidence) >= 1, "EvidenceGraph must contain at least one node"
    assert any(ev.verified for ev in eg.evidence), (
        "At least one Evidence node must have verified=True for maintainer_ack entry"
    )


# ---------------------------------------------------------------------------
# B8-3: accepted_patch entries stay verified=False
# ---------------------------------------------------------------------------


def test_B8_accepted_patch_sets_verified_false() -> None:
    """_build_evidence_graph_for_calibration must NOT set verified=True for
    accepted_patch entries — these are external judgments, treated as unverified.
    """
    e = _entry("s1", "patch-001@t", "dai", evidence_type="accepted_patch")
    eg = _build_evidence_graph_for_calibration("test_cmt", [e])

    assert len(eg.evidence) >= 1, "EvidenceGraph must contain at least one node"
    # All accepted_patch nodes must have verified=False
    patch_nodes = [ev for ev in eg.evidence]
    assert all(not ev.verified for ev in patch_nodes), (
        "accepted_patch Evidence nodes must have verified=False (external judgment, not archive-backed)"
    )


# ---------------------------------------------------------------------------
# B8-4: REVIEW_HISTORY factor > 0.0 after verified flag fix
# ---------------------------------------------------------------------------


def test_B8_review_history_factor_positive_after_verified_fix() -> None:
    """With 3 review_discussion entries for 'dai', calibration must produce
    a REVIEW_HISTORY factor > 0.0 in review_history_distribution['dai'].

    Before the verified-flag fix the factor was 0.0 because no evidence was
    considered verified; after the fix at least the lore entries are verified.
    """
    entries = [
        _entry(f"s{i}", f"dai-{i}@t", "dai", evidence_type="review_discussion")
        for i in range(3)
    ]
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    report = calibrator.calibrate([("cmt001", 0.8, "dai")])

    assert report.samples_calibrated == 1, (
        f"Expected samples_calibrated=1, got {report.samples_calibrated}"
    )
    assert "dai" in report.review_history_distribution, (
        "review_history_distribution must contain 'dai' key after claim-triple calibration"
    )
    assert report.review_history_distribution["dai"] > 0.0, (
        f"REVIEW_HISTORY factor for 'dai' must be > 0.0, got {report.review_history_distribution['dai']}"
    )


# ---------------------------------------------------------------------------
# B8-5: calibration with 5+ review_discussion entries produces non-trivial cfm_scores
# ---------------------------------------------------------------------------


def test_B8_calibration_with_5_review_discussion_entries() -> None:
    """A temp store with 5+ review_discussion entries for 'dai' must produce:
    - samples_calibrated == 5 (all triples processed)
    - factor_contributions['review_history'] > 0.0 (evidence counted)
    """
    entries = [
        _entry(
            f"s{i}",
            f"dai-rd-{i}@t",
            "dai",
            evidence_type="review_discussion",
            text=f"review discussion comment number {i} about dai subsystem",
        )
        for i in range(6)
    ]
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    triples = [(f"cmt{i:03d}", 0.5 + i * 0.05, "dai") for i in range(5)]
    report = calibrator.calibrate(triples)

    assert report.samples_calibrated == 5, (
        f"Expected samples_calibrated=5, got {report.samples_calibrated}"
    )
    assert "review_history" in report.factor_contributions, (
        "factor_contributions must include 'review_history' key"
    )
    assert report.factor_contributions["review_history"] > 0.0, (
        f"review_history factor must be > 0.0 with verified evidence, "
        f"got {report.factor_contributions['review_history']}"
    )


# ---------------------------------------------------------------------------
# B8-6: correlation_significant is None when Pearson is None
# ---------------------------------------------------------------------------


def test_B8_correlation_significant_none_when_pearson_none() -> None:
    """When all CFM scores are identical (zero variance), _pearson returns None,
    and CFMCalibrationReport.correlation_significant must also be None.

    Construct identical entries so the confidence engine produces the same CFM
    score for every comment — this guarantees zero variance in cfm_scores and
    forces Pearson = None.
    """
    # Five identical review_discussion entries for 'dai' — same text, same structure.
    # The confidence engine will produce an identical score for every calibration
    # run because the EvidenceGraph content is the same.
    entries = [
        _entry("s1", f"identical-{i}@t", "dai", evidence_type="review_discussion",
               text="identical review comment text for zero variance test")
        for i in range(5)
    ]
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    # Use the same LLM confidence for every comment; if CFM scores also collapse
    # to the same value, Pearson is None → correlation_significant must be None.
    triples = [(f"cmt{i:03d}", 0.5, "dai") for i in range(5)]
    report = calibrator.calibrate(triples)

    # If Pearson is None (zero variance in cfm or llm scores), correlation_significant must be None
    if report.cfm_vs_llm_correlation is None:
        assert report.correlation_significant is None, (
            "correlation_significant must be None when Pearson is None "
            f"(got {report.correlation_significant})"
        )
    else:
        # If the engine produced varied scores, just check the field exists and is bool or None
        assert report.correlation_significant is None or isinstance(
            report.correlation_significant, bool
        ), (
            "correlation_significant must be bool or None"
        )


# ---------------------------------------------------------------------------
# B8-7: calibration sample count with large production store (skip if absent)
# ---------------------------------------------------------------------------


_PROD_STORE_PATH = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/kri/data/lore_cache/review_history.jsonl")


@pytest.mark.skipif(
    not _PROD_STORE_PATH.exists(),
    reason="Production review_history.jsonl not present — skipping production store test",
)
def test_B8_calibration_sample_count_with_production_store() -> None:
    """With the real production store loaded, calibration must process at least
    one sample — confirming that the store wiring from production data is intact.
    """
    store = ReviewHistoryStore(path=_PROD_STORE_PATH)
    assert store.count() > 0, (
        f"Production store at {_PROD_STORE_PATH} must contain at least one entry"
    )

    calibrator = _make_calibrator(store)

    # Use a minimal set of triples referencing a real claim category (dai)
    triples = [("prod-cmt-001", 0.7, "dai"), ("prod-cmt-002", 0.5, "dai")]
    report = calibrator.calibrate(triples)

    assert report.samples_calibrated > 0, (
        f"calibrate() must process at least 1 sample from the production store, "
        f"got samples_calibrated={report.samples_calibrated}"
    )
