"""WP4-C tests: ConfidenceEngineImpl CFM shadow mode.

Tests:
  C1 - cfm_confidence is set when confidence_engine is wired in
  C2 - cfm_confidence is NOT used as gate (shadow mode: LLM confidence is gate)
  C3 - confidence_engine=None leaves cfm_confidence=None
  C4 - confidence_engine.score() exception degrades gracefully
  C5 - cfm_confidence is set on safety-floor comments too
  C6 - cfm_confidence is a ConfidenceScore with valid fields
  C7 - cfm score shadow does not affect evidence_status
"""

from __future__ import annotations

from unittest.mock import MagicMock

from kri.common.models import (
    ConfidenceLevel,
    ConfidenceScore,
    EvidenceGraph,
    Severity,
)
from kri.llm.models import InlineComment
from kri.llm.reviewer import IntelligentReviewEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_comment(
    *,
    severity: Severity = Severity.INFO,
    confidence: float = 0.5,
    category: str = "api_usage",
    message: str = "test",
) -> InlineComment:
    return InlineComment(
        file_path="sound/soc/foo.c",
        line_number=10,
        category=category,
        severity=severity,
        confidence=confidence,
        message=message,
    )


def _make_confidence_score(score: float = 0.25) -> ConfidenceScore:
    return ConfidenceScore(
        score=score,
        level=ConfidenceLevel.from_score(score),
    )


def _make_evidence_engine(*, has_verified: bool = True) -> MagicMock:
    engine = MagicMock()
    graph = MagicMock(spec=EvidenceGraph)
    graph.has_verified_evidence.return_value = has_verified
    graph.subsystem_rule = None
    engine.gather.return_value = graph
    return engine


def _make_confidence_engine(score: float = 0.25) -> MagicMock:
    engine = MagicMock()
    engine.score.return_value = _make_confidence_score(score)
    return engine


def _make_ire(*, evidence_engine=None, confidence_engine=None) -> IntelligentReviewEngine:
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    return IntelligentReviewEngine(
        client=client,
        evidence_engine=evidence_engine,
        confidence_engine=confidence_engine,
    )


def _make_patch_mock() -> MagicMock:
    p = MagicMock()
    p.patch_id = "p1"
    p.subject = "test"
    p.diff = ""
    return p


def _make_series_mock() -> MagicMock:
    s = MagicMock()
    s.series_id = "s1"
    return s


# ---------------------------------------------------------------------------
# C1 - cfm_confidence is set
# ---------------------------------------------------------------------------


def test_C1_cfm_confidence_is_set():
    """When confidence_engine is wired, cfm_confidence is populated on each comment."""
    ev_engine = _make_evidence_engine(has_verified=True)
    conf_engine = _make_confidence_engine(score=0.30)
    engine = _make_ire(evidence_engine=ev_engine, confidence_engine=conf_engine)

    comment = _make_comment()
    result = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)

    assert len(result) == 1
    assert result[0].cfm_confidence is not None
    assert isinstance(result[0].cfm_confidence, ConfidenceScore)
    assert result[0].cfm_confidence.score == 0.30


# ---------------------------------------------------------------------------
# C2 - shadow mode: LLM confidence is still gate, not CFM
# ---------------------------------------------------------------------------


def test_C2_shadow_mode_cfm_does_not_gate():
    """CFM score is irrelevant to which comments survive the gate.

    A comment with NO verified evidence and a CFM score of 0.95 (hypothetical)
    must still be suppressed unless it qualifies for safety floor.
    A comment with verified evidence and CFM score of 0.01 must still pass.
    """
    # No evidence, high CFM → must still be suppressed (not floored)
    ev_engine_empty = _make_evidence_engine(has_verified=False)
    # Trick: return high CFM score even with empty evidence
    conf_engine = MagicMock()
    conf_engine.score.return_value = _make_confidence_score(score=0.95)
    engine = _make_ire(evidence_engine=ev_engine_empty, confidence_engine=conf_engine)

    comment_info = _make_comment(severity=Severity.INFO, confidence=0.5)
    result = engine._apply_evidence_gate([comment_info], _make_patch_mock(), _make_series_mock(), None)
    # High CFM must NOT rescue a non-floor comment — shadow mode means CFM is observational only
    assert result == [], "High CFM must not override evidence gate in shadow mode"
    assert comment_info.evidence_status == "evidence_missing"

    # Verified evidence, low CFM → must still pass
    ev_engine_full = _make_evidence_engine(has_verified=True)
    conf_engine_low = MagicMock()
    conf_engine_low.score.return_value = _make_confidence_score(score=0.01)
    engine2 = _make_ire(evidence_engine=ev_engine_full, confidence_engine=conf_engine_low)

    comment_warn = _make_comment(severity=Severity.WARNING, confidence=0.6)
    result2 = engine2._apply_evidence_gate([comment_warn], _make_patch_mock(), _make_series_mock(), None)
    assert len(result2) == 1, "Low CFM must not block a comment with verified evidence"
    assert result2[0].evidence_status == "supported"


# ---------------------------------------------------------------------------
# C3 - confidence_engine=None leaves cfm_confidence=None
# ---------------------------------------------------------------------------


def test_C3_no_confidence_engine_cfm_stays_none():
    """Without confidence_engine, cfm_confidence remains None."""
    ev_engine = _make_evidence_engine(has_verified=True)
    engine = _make_ire(evidence_engine=ev_engine, confidence_engine=None)

    comment = _make_comment()
    result = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)
    assert len(result) == 1
    assert result[0].cfm_confidence is None


# ---------------------------------------------------------------------------
# C4 - confidence_engine.score() exception degrades gracefully
# ---------------------------------------------------------------------------


def test_C4_confidence_engine_exception_degrades():
    """If confidence_engine.score() raises, cfm_confidence stays None; comment survives."""
    ev_engine = _make_evidence_engine(has_verified=True)
    conf_engine = MagicMock()
    conf_engine.score.side_effect = RuntimeError("CFM unavailable")
    engine = _make_ire(evidence_engine=ev_engine, confidence_engine=conf_engine)

    comment = _make_comment()
    result = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)
    assert len(result) == 1, "Exception in CFM must not suppress comment"
    assert result[0].cfm_confidence is None, "cfm_confidence stays None on exception"


# ---------------------------------------------------------------------------
# C5 - cfm_confidence set on safety-floor comments
# ---------------------------------------------------------------------------


def test_C5_cfm_set_on_safety_floor_comment():
    """cfm_confidence is populated even on safety-floor (evidence_missing) comments."""
    ev_engine = _make_evidence_engine(has_verified=False)
    conf_engine = _make_confidence_engine(score=0.15)
    engine = _make_ire(evidence_engine=ev_engine, confidence_engine=conf_engine)

    comment = _make_comment(severity=Severity.BLOCKER, confidence=0.80)
    result = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)
    assert len(result) == 1
    assert result[0].evidence_status == "safety_floored"
    assert result[0].cfm_confidence is not None
    assert result[0].cfm_confidence.score == 0.15


# ---------------------------------------------------------------------------
# C6 - cfm_confidence is a valid ConfidenceScore
# ---------------------------------------------------------------------------


def test_C6_cfm_confidence_is_valid_confidence_score():
    """cfm_confidence must be a ConfidenceScore with level derived from score."""
    ev_engine = _make_evidence_engine(has_verified=True)
    conf_engine = _make_confidence_engine(score=0.85)
    engine = _make_ire(evidence_engine=ev_engine, confidence_engine=conf_engine)

    comment = _make_comment()
    result = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)
    cfm = result[0].cfm_confidence
    assert isinstance(cfm, ConfidenceScore)
    assert cfm.level == ConfidenceLevel.LIKELY  # 0.80–0.94 = LIKELY


# ---------------------------------------------------------------------------
# C7 - cfm score shadow does not affect evidence_status
# ---------------------------------------------------------------------------


def test_C7_cfm_does_not_alter_evidence_status():
    """Regardless of the CFM score value, evidence_status is determined only by
    the evidence_graph — never by cfm_confidence."""
    ev_engine = _make_evidence_engine(has_verified=True)
    ev_engine.gather.return_value.subsystem_rule = None
    conf_engine = _make_confidence_engine(score=0.99)
    engine = _make_ire(evidence_engine=ev_engine, confidence_engine=conf_engine)

    comment = _make_comment()
    result = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)
    # evidence_status='supported' because evidence_graph.has_verified_evidence() == True
    assert result[0].evidence_status == "supported"
    # cfm_confidence populated but did not change evidence_status
    assert result[0].cfm_confidence.score == 0.99
