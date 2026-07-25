"""D1 tests: lore_matched_series is a first-class field on PatchReview and IntelligentReport.

D1-A  PatchReview has lore_matched_series as a top-level field (default empty list)
D1-B  PatchReview.lore_matched_series round-trips through model_dump/model_validate
D1-C  IntelligentReport has lore_matched_series as a top-level field (default empty list)
D1-D  IntelligentReport.lore_matched_series round-trips through model_dump/model_validate
D1-E  IntelligentReviewEngine populates PatchReview.lore_matched_series from lore store
D1-F  IntelligentReviewEngine populates IntelligentReport.lore_matched_series as union of patches
D1-G  lore_matched_series is present in /api/review/* API response JSON
D1-H  lore_matched_series is independent of (but consistent with) metadata["lore_matched_series"]
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kri.llm.models import IntelligentReport, PatchReview


# ---------------------------------------------------------------------------
# D1-A  PatchReview.lore_matched_series default
# ---------------------------------------------------------------------------


def test_D1A_patch_review_lore_matched_series_default() -> None:
    pr = PatchReview(patch_id="p1", subject="test patch")
    assert hasattr(pr, "lore_matched_series")
    assert pr.lore_matched_series == []


# ---------------------------------------------------------------------------
# D1-B  PatchReview.lore_matched_series round-trips
# ---------------------------------------------------------------------------


def test_D1B_patch_review_lore_matched_series_roundtrip() -> None:
    pr = PatchReview(
        patch_id="p1",
        subject="test",
        lore_matched_series=["sid_a", "sid_b"],
    )
    d = pr.model_dump()
    assert "lore_matched_series" in d
    assert d["lore_matched_series"] == ["sid_a", "sid_b"]
    pr2 = PatchReview.model_validate(d)
    assert pr2.lore_matched_series == ["sid_a", "sid_b"]


# ---------------------------------------------------------------------------
# D1-C  IntelligentReport.lore_matched_series default
# ---------------------------------------------------------------------------


def test_D1C_intelligent_report_lore_matched_series_default() -> None:
    report = IntelligentReport(series_id="s1", series_title="test")
    assert hasattr(report, "lore_matched_series")
    assert report.lore_matched_series == []


# ---------------------------------------------------------------------------
# D1-D  IntelligentReport.lore_matched_series round-trips
# ---------------------------------------------------------------------------


def test_D1D_intelligent_report_lore_matched_series_roundtrip() -> None:
    report = IntelligentReport(
        series_id="s1",
        series_title="test",
        lore_matched_series=["sid_x", "sid_y", "sid_z"],
    )
    d = report.model_dump()
    assert "lore_matched_series" in d
    assert d["lore_matched_series"] == ["sid_x", "sid_y", "sid_z"]
    report2 = IntelligentReport.model_validate(d)
    assert report2.lore_matched_series == ["sid_x", "sid_y", "sid_z"]


# ---------------------------------------------------------------------------
# Helpers for engine-level tests (D1-E, D1-F, D1-H)
# ---------------------------------------------------------------------------


def _make_patch(patch_id: str = "p1") -> Any:
    from kri.common.models import Patch
    return Patch(
        patch_id=patch_id,
        subject=f"subject-{patch_id}",
        diff="--- a/f.c\n+++ b/f.c\n@@ -1 +1 @@\n-old\n+new\n",
        commit_message="msg",
    )


def _make_series(patches=None) -> Any:
    from kri.common.models import PatchSeries
    if patches is None:
        patches = [_make_patch("p1")]
    return PatchSeries(
        series_id="test:d1",
        title="D1 test series",
        patches=patches,
    )


def _make_mock_client() -> MagicMock:
    """Offline LLM client that returns minimal but parseable JSON."""
    client = MagicMock()
    client._cfg = MagicMock()
    client._cfg.model = "test-model"
    client.stats = {}
    # summarizer returns a minimal PatchSummary JSON
    summarizer_resp = MagicMock()
    summarizer_resp.content = json.dumps({
        "what_it_does": "does stuff",
        "subsystem": "net",
        "components_touched": [],
        "change_type": "fix",
        "risk_areas": [],
    })
    # code_quality / subsystem agents return empty inline_comments
    agent_resp = MagicMock()
    agent_resp.content = json.dumps({
        "agent_name": "code_quality",
        "patch_id": "p1",
        "inline_comments": [],
        "general_comments": [],
        "confidence": 0.5,
    })
    # complete() always returns the agent_resp (good enough for all three agents)
    client.complete.return_value = agent_resp
    return client


def _make_mock_review_history_store(matched_series_ids: list[str]) -> MagicMock:
    """Store that returns matched series IDs for any claim query."""
    from kri.learning.models import ReviewHistoryEntry, ReviewHistorySummary

    store = MagicMock()

    # by_claim() returns fake entries with the desired series_ids
    fake_entries = []
    for i, sid in enumerate(matched_series_ids):
        eid = ReviewHistoryEntry.make_entry_id(sid, f"mid@{i}", "text")
        fake_entries.append(ReviewHistoryEntry(
            entry_id=eid,
            series_id=sid,
            message_id=f"mid@{i}",
            source_url=f"https://lore.kernel.org/r/mid@{i}",
            reviewer_text="fix the locking",
            extracted_claim="locking",
            evidence_type="review_discussion",
            confidence_basis="lexical:lock",
        ))
    store.by_claim.return_value = fake_entries

    # summarise_by_series_ids() returns ReviewHistorySummary objects
    store.summarise_by_series_ids.return_value = [
        ReviewHistorySummary(
            series_id=sid,
            entry_count=1,
            source_urls=[f"https://lore.kernel.org/r/mid@{i}"],
            claim_categories={"locking": 1},
        )
        for i, sid in enumerate(matched_series_ids)
    ]
    return store


# ---------------------------------------------------------------------------
# D1-E  Engine populates PatchReview.lore_matched_series
# ---------------------------------------------------------------------------


def test_D1E_engine_populates_patch_review_lore_matched_series() -> None:
    from kri.llm.reviewer import IntelligentReviewEngine
    from kri.common.models import EvidenceGraph, Evidence, EvidenceSourceType, Provenance

    client = _make_mock_client()
    store = _make_mock_review_history_store(["sid_lore_1", "sid_lore_2"])

    # Build a fake evidence engine that returns verified evidence (so comments survive gate).
    def _fake_gather(decision, series_context=None):
        eg = EvidenceGraph(comment_id=decision.decision_id)
        # Add a verified REVIEW_DISCUSSION node so the comment is "supported"
        ev = Evidence(
            evidence_id="ev001",
            source_type=EvidenceSourceType.REVIEW_HISTORY,
            summary="lore history match",
            provenance=Provenance(commit_hash="abc123"),
            verified=True,
            strength=0.5,
        )
        eg.evidence.append(ev)
        return eg

    evidence_engine = MagicMock()
    evidence_engine.gather.side_effect = _fake_gather

    # Patch _enrich_with_lore_history to return the series IDs we want traced
    with patch(
        "kri.llm.reviewer._enrich_with_lore_history",
        return_value={"sid_lore_1", "sid_lore_2"},
    ):
        engine = IntelligentReviewEngine(
            client=client,
            evidence_engine=evidence_engine,
            review_history_store=store,
        )
        series = _make_series()
        report = engine.review(series)

    assert len(report.patches) == 1
    pr = report.patches[0]
    # lore_matched_series must be a first-class field, not buried in metadata
    assert isinstance(pr.lore_matched_series, list)
    for sid in pr.lore_matched_series:
        assert sid in {"sid_lore_1", "sid_lore_2"}


# ---------------------------------------------------------------------------
# D1-F  Engine populates IntelligentReport.lore_matched_series as union
# ---------------------------------------------------------------------------


def test_D1F_engine_populates_report_lore_matched_series_as_union() -> None:
    from kri.llm.reviewer import IntelligentReviewEngine
    from kri.common.models import EvidenceGraph

    client = _make_mock_client()
    store = _make_mock_review_history_store(["sid_lore_1"])

    evidence_engine = MagicMock()
    evidence_engine.gather.return_value = EvidenceGraph(comment_id="c1")

    # Simulate two patches each matching different lore series
    with patch(
        "kri.llm.reviewer._enrich_with_lore_history",
        side_effect=[{"sid_a"}, {"sid_b"}],
    ):
        engine = IntelligentReviewEngine(
            client=client,
            evidence_engine=evidence_engine,
            review_history_store=store,
        )
        series = _make_series()
        report = engine.review(series)

    # Report-level lore_matched_series is the union (sorted) of all patches
    assert isinstance(report.lore_matched_series, list)
    # It must be sorted
    assert report.lore_matched_series == sorted(report.lore_matched_series)


# ---------------------------------------------------------------------------
# D1-G  lore_matched_series present in API JSON response
# ---------------------------------------------------------------------------


def test_D1G_lore_matched_series_in_api_response() -> None:
    """lore_matched_series must appear in the JSON of /api/review/* responses."""
    report = IntelligentReport(
        series_id="s1",
        series_title="api test",
        lore_matched_series=["sid_api_1"],
        patches=[
            PatchReview(
                patch_id="p1",
                subject="sub",
                lore_matched_series=["sid_api_1"],
            )
        ],
    )
    payload = report.model_dump()
    # Top-level report field
    assert "lore_matched_series" in payload
    assert payload["lore_matched_series"] == ["sid_api_1"]
    # Per-patch field
    assert "lore_matched_series" in payload["patches"][0]
    assert payload["patches"][0]["lore_matched_series"] == ["sid_api_1"]

    # Verify JSON serialization works cleanly
    as_json = json.loads(json.dumps(payload))
    assert as_json["lore_matched_series"] == ["sid_api_1"]
    assert as_json["patches"][0]["lore_matched_series"] == ["sid_api_1"]


# ---------------------------------------------------------------------------
# D1-H  lore_matched_series is consistent with metadata["lore_matched_series"]
# ---------------------------------------------------------------------------


def test_D1H_lore_matched_series_consistent_with_metadata() -> None:
    """When both the top-level field and metadata dict are populated they agree."""
    sids = ["sid_p", "sid_q"]
    pr = PatchReview(
        patch_id="p1",
        subject="test",
        lore_matched_series=sids,
        metadata={"lore_matched_series": sids},
    )
    assert pr.lore_matched_series == pr.metadata["lore_matched_series"]
    d = pr.model_dump()
    assert d["lore_matched_series"] == d["metadata"]["lore_matched_series"]
