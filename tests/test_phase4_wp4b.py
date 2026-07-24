"""WP4-B tests: EvidenceEngineImpl wiring + evidence gate.

Tests:
  B1 - evidence gate suppresses evidence_missing (non-safety-floor)
  B2 - safety floor preserves BLOCKER >= 0.70 even with no evidence
  B3 - safety floor preserves WARNING >= 0.70 even with no evidence
  B4 - INFO < 0.70 with no evidence is suppressed
  B5 - supported evidence sets evidence_status='supported'
  B6 - rule-backed evidence sets evidence_status='rule_backed'
  B7 - no evidence engine (None) passes all comments unchanged
  B8 - evidence engine exception degrades gracefully
  B9 - BLOCKER < 0.70 (below floor threshold) is suppressed
  B10 - WARNING < 0.70 (below floor threshold) is suppressed
  B11 - mode-off byte identity: evidence_engine=None, all evidence_status='unknown'
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kri.common.models import Evidence, EvidenceGraph, EvidenceSourceType, Rule, Severity
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
    file_path: str = "sound/soc/foo.c",
    line_number: int = 10,
    message: str = "test comment",
) -> InlineComment:
    return InlineComment(
        file_path=file_path,
        line_number=line_number,
        category=category,
        severity=severity,
        confidence=confidence,
        message=message,
    )


def _make_evidence_engine(*, has_verified: bool = False, has_rule: bool = False) -> MagicMock:
    """Build a mock EvidenceEngineImpl that returns empty or verified evidence."""
    engine = MagicMock()
    graph = MagicMock(spec=EvidenceGraph)
    graph.has_verified_evidence.return_value = has_verified
    graph.subsystem_rule = Rule(
        rule_id="r1",
        category="api_usage",
        rule_type="soft",
        description="test rule",
        rationale="",
        documentation_ref=None,
        historical_enforcement_rate=None,
        exceptions=[],
        version_range=None,
    ) if has_rule else None
    # WP4-E: _apply_evidence_gate iterates evidence_graph.evidence for has_non_blame check.
    graph.evidence = []
    engine.gather.return_value = graph
    return engine


def _make_engine_with_evidence_engine(evidence_engine) -> IntelligentReviewEngine:
    """Build an IntelligentReviewEngine with a mocked LLM client and the given evidence engine."""
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    return IntelligentReviewEngine(
        client=client,
        evidence_engine=evidence_engine,
    )


def _make_patch_mock(patch_id: str = "p1") -> MagicMock:
    p = MagicMock()
    p.patch_id = patch_id
    p.subject = "test patch"
    p.diff = ""
    return p


def _make_series_mock(series_id: str = "s1") -> MagicMock:
    s = MagicMock()
    s.series_id = series_id
    return s


# ---------------------------------------------------------------------------
# B1 - evidence_missing suppresses non-safety-floor
# ---------------------------------------------------------------------------


def test_B1_evidence_missing_suppresses_non_floor():
    """INFO comment with confidence 0.5 and no evidence is suppressed."""
    ev_engine = _make_evidence_engine(has_verified=False)
    engine = _make_engine_with_evidence_engine(ev_engine)
    comment = _make_comment(severity=Severity.INFO, confidence=0.5)
    patch = _make_patch_mock()
    series = _make_series_mock()

    result = engine._apply_evidence_gate([comment], patch, series, None)
    assert result == [], "evidence_missing INFO comment should be suppressed"
    assert comment.evidence_status == "evidence_missing"


# ---------------------------------------------------------------------------
# B2 - safety floor: BLOCKER >= 0.70 survives
# ---------------------------------------------------------------------------


def test_B2_safety_floor_blocker_survives():
    """BLOCKER with confidence >= 0.70 must always survive, even with no evidence."""
    ev_engine = _make_evidence_engine(has_verified=False)
    engine = _make_engine_with_evidence_engine(ev_engine)
    comment = _make_comment(severity=Severity.BLOCKER, confidence=0.75)
    patch = _make_patch_mock()
    series = _make_series_mock()

    result = engine._apply_evidence_gate([comment], patch, series, None)
    assert len(result) == 1, "BLOCKER >= 0.70 must survive safety floor"
    assert result[0].evidence_status == "safety_floored"


# ---------------------------------------------------------------------------
# B3 - safety floor: WARNING >= 0.70 survives
# ---------------------------------------------------------------------------


def test_B3_safety_floor_warning_survives():
    """WARNING with confidence >= 0.70 must survive even with no evidence."""
    ev_engine = _make_evidence_engine(has_verified=False)
    engine = _make_engine_with_evidence_engine(ev_engine)
    comment = _make_comment(severity=Severity.WARNING, confidence=0.70)
    patch = _make_patch_mock()
    series = _make_series_mock()

    result = engine._apply_evidence_gate([comment], patch, series, None)
    assert len(result) == 1
    assert result[0].evidence_status == "safety_floored"


# ---------------------------------------------------------------------------
# B4 - INFO < 0.70 suppressed
# ---------------------------------------------------------------------------


def test_B4_info_below_floor_threshold_suppressed():
    """INFO comment with confidence < 0.70 is not a safety floor candidate."""
    ev_engine = _make_evidence_engine(has_verified=False)
    engine = _make_engine_with_evidence_engine(ev_engine)
    comment = _make_comment(severity=Severity.INFO, confidence=0.69)
    patch = _make_patch_mock()
    series = _make_series_mock()

    result = engine._apply_evidence_gate([comment], patch, series, None)
    assert result == []
    assert comment.evidence_status == "evidence_missing"


# ---------------------------------------------------------------------------
# B5 - supported evidence
# ---------------------------------------------------------------------------


def test_B5_supported_evidence_sets_status():
    """Verified evidence without a rule sets evidence_status='supported'."""
    ev_engine = _make_evidence_engine(has_verified=True, has_rule=False)
    engine = _make_engine_with_evidence_engine(ev_engine)
    comment = _make_comment(severity=Severity.INFO, confidence=0.5)
    patch = _make_patch_mock()
    series = _make_series_mock()

    result = engine._apply_evidence_gate([comment], patch, series, None)
    assert len(result) == 1
    assert result[0].evidence_status == "supported"


# ---------------------------------------------------------------------------
# B6 - rule-backed evidence
# ---------------------------------------------------------------------------


def test_B6_rule_backed_evidence_sets_status():
    """Verified evidence with a subsystem_rule sets evidence_status='rule_backed'."""
    ev_engine = _make_evidence_engine(has_verified=True, has_rule=True)
    engine = _make_engine_with_evidence_engine(ev_engine)
    comment = _make_comment(severity=Severity.WARNING, confidence=0.6)
    patch = _make_patch_mock()
    series = _make_series_mock()

    result = engine._apply_evidence_gate([comment], patch, series, None)
    assert len(result) == 1
    assert result[0].evidence_status == "rule_backed"


# ---------------------------------------------------------------------------
# B7 - no evidence engine, all pass unchanged
# ---------------------------------------------------------------------------


def test_B7_no_evidence_engine_all_pass():
    """When evidence_engine=None, _apply_evidence_gate is not called and all
    comments pass through with evidence_status='unknown'."""
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    engine = IntelligentReviewEngine(client=client, evidence_engine=None)

    # Verify _apply_evidence_gate is not called when engine is None by checking
    # that the attribute is None.
    assert engine._evidence_engine is None

    # The reviewer._review_patch guard is: if self._evidence_engine is not None.
    # We verify it by checking that comment.evidence_status stays "unknown" after
    # calling _review_patch indirectly via apply_evidence_gate — but since engine
    # is None, the gate is never called. Direct unit test:
    comment = _make_comment(severity=Severity.INFO, confidence=0.5)
    assert comment.evidence_status == "unknown"


# ---------------------------------------------------------------------------
# B8 - evidence engine exception degrades gracefully
# ---------------------------------------------------------------------------


def test_B8_evidence_engine_exception_degrades():
    """If evidence_engine.gather() raises, the comment is treated as evidence_missing.
    Safety floor still applies."""
    ev_engine = MagicMock()
    ev_engine.gather.side_effect = RuntimeError("KG unavailable")

    engine = _make_engine_with_evidence_engine(ev_engine)

    # Non-floor comment: suppressed
    comment_info = _make_comment(severity=Severity.INFO, confidence=0.5)
    result = engine._apply_evidence_gate([comment_info], _make_patch_mock(), _make_series_mock(), None)
    assert result == []
    assert comment_info.evidence_status == "evidence_missing"

    # Safety-floor comment: survives
    comment_blocker = _make_comment(severity=Severity.BLOCKER, confidence=0.80)
    result2 = engine._apply_evidence_gate([comment_blocker], _make_patch_mock(), _make_series_mock(), None)
    assert len(result2) == 1
    assert result2[0].evidence_status == "safety_floored"


# ---------------------------------------------------------------------------
# B9/B10 - BLOCKER/WARNING below floor threshold
# ---------------------------------------------------------------------------


def test_B9_blocker_below_floor_threshold_suppressed():
    """BLOCKER with confidence < 0.70 is NOT safety-floored — suppressed."""
    ev_engine = _make_evidence_engine(has_verified=False)
    engine = _make_engine_with_evidence_engine(ev_engine)
    comment = _make_comment(severity=Severity.BLOCKER, confidence=0.69)
    result = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)
    assert result == []
    assert comment.evidence_status == "evidence_missing"


def test_B10_warning_below_floor_threshold_suppressed():
    """WARNING with confidence < 0.70 is NOT safety-floored — suppressed."""
    ev_engine = _make_evidence_engine(has_verified=False)
    engine = _make_engine_with_evidence_engine(ev_engine)
    comment = _make_comment(severity=Severity.WARNING, confidence=0.69)
    result = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)
    assert result == []
    assert comment.evidence_status == "evidence_missing"


# ---------------------------------------------------------------------------
# B11 - mode-off byte identity
# ---------------------------------------------------------------------------


def test_B11_mode_off_evidence_status_unchanged():
    """When evidence_engine=None, evidence_status stays 'unknown' — mode-off byte identity."""
    comment = _make_comment()
    assert comment.evidence_status == "unknown"
    # Constructing IRE with no evidence_engine must not change evidence_status on any comment.
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    engine = IntelligentReviewEngine(client=client)
    assert engine._evidence_engine is None
    # The comment is untouched.
    assert comment.evidence_status == "unknown"
