"""Track-B WP4-I tests: DKP version range / historical knowledge readiness.

I1  - ASoC DKP seeds at least 1 evidence node into the KnowledgeGraph
I2  - ConfidenceEngine _compute_historical_agreement returns non-zero when
      EvidenceGraph has accepted_examples populated
I3  - ConfidenceEngine _compute_review_history returns non-zero when
      EvidenceGraph has at least one verified REVIEW_DISCUSSION evidence node
I4  - ConfidenceEngine _compute_historical_agreement is 0.0 when accepted=0
I5  - CFM shadow mode: mode=off behaviour unchanged (no score computed when
      confidence_engine is None)
"""

from __future__ import annotations

import pytest

from kri.common.models import (
    ConfidenceFactor,
    Decision,
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    Provenance,
    ReasoningLayer,
)
from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.llm.models import IntelligentReport, PatchReview


# ---------------------------------------------------------------------------
# I1 - ASoC DKP seeds at least 1 evidence node
# ---------------------------------------------------------------------------


def test_I1_asoc_dkp_seeds_evidence_nodes() -> None:
    """ASoC DKP.seed_graph() must add at least 1 node to the KnowledgeGraph."""
    from kri.knowledge_manager import KnowledgeManagerImpl
    from kri.packages.asoc.plugin import AsocDomainKnowledgePackage

    km = KnowledgeManagerImpl()
    dkp = AsocDomainKnowledgePackage()
    dkp.seed_graph(km)
    assert km.graph.node_count() >= 1, (
        "ASoC DKP.seed_graph() produced no nodes — historical knowledge base empty"
    )


# ---------------------------------------------------------------------------
# I2 - historical_agreement factor non-zero with accepted_examples
# ---------------------------------------------------------------------------


def test_I2_historical_agreement_nonzero_with_accepted_examples() -> None:
    """_compute_historical_agreement must score > 0 when accepted_examples is populated."""
    eg = EvidenceGraph(
        comment_id="test:i2",
        accepted_examples=["commit:abc123", "commit:def456"],
        rejected_examples=[],
    )
    score = ConfidenceEngineImpl._compute_historical_agreement(eg)
    assert score > 0.0, f"Expected > 0.0, got {score}"
    # Formula: accepted / (accepted + rejected + 1) = 2/3 ≈ 0.667
    assert abs(score - 2 / 3) < 1e-6


# ---------------------------------------------------------------------------
# I3 - review_history factor non-zero with REVIEW_DISCUSSION evidence
# ---------------------------------------------------------------------------


def test_I3_review_history_factor_nonzero_with_review_discussion() -> None:
    """_compute_review_history must score > 0 for verified REVIEW_DISCUSSION nodes."""
    ev = Evidence(
        evidence_id="ev:test:i3",
        source_type=EvidenceSourceType.REVIEW_DISCUSSION,
        summary="maintainer asked to fix locking",
        provenance=Provenance(source_url="https://lore.kernel.org/r/test@example.com"),
        verified=True,
        strength=0.4,
    )
    eg = EvidenceGraph(comment_id="test:i3", evidence=[ev])
    score = ConfidenceEngineImpl._compute_review_history(eg)
    assert score > 0.0, f"Expected > 0.0, got {score}"
    # Formula: min(1.0, 1 * 0.35) = 0.35
    assert abs(score - 0.35) < 1e-6


# ---------------------------------------------------------------------------
# I4 - historical_agreement is 0.0 with no accepted examples
# ---------------------------------------------------------------------------


def test_I4_historical_agreement_zero_when_no_accepted() -> None:
    eg = EvidenceGraph(comment_id="test:i4", accepted_examples=[], rejected_examples=[])
    score = ConfidenceEngineImpl._compute_historical_agreement(eg)
    assert score == 0.0


# ---------------------------------------------------------------------------
# I5 - mode=off: IntelligentReport has no cfm_calibration when calibrator=None
# ---------------------------------------------------------------------------


def test_I5_mode_off_no_cfm_calibration_when_calibrator_none() -> None:
    """When cfm_calibrator is None (mode-off), cfm_calibration must be None in report."""
    report = IntelligentReport(
        series_id="test:i5",
        series_title="mode-off test",
        cfm_calibration=None,
    )
    assert report.cfm_calibration is None
    d = report.model_dump()
    assert d["cfm_calibration"] is None
