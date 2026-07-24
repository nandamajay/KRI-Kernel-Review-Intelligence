"""WP4-G tests: Governance check for evidence_missing BLOCKER/WARNING.

Tests:
  G1 - check_evidence_status returns empty list when all is well
  G2 - check_evidence_status flags evidence_missing BLOCKER
  G3 - check_evidence_status flags evidence_missing WARNING
  G4 - check_evidence_status does NOT flag evidence_missing INFO
  G5 - check_evidence_status does NOT flag supported BLOCKER
  G6 - check_evidence_status does NOT flag safety_floored BLOCKER
  G7 - check_evidence_status exported from governance __init__
"""

from __future__ import annotations

from kri.common.models import Severity
from kri.governance import check_evidence_status
from kri.llm.models import InlineComment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_comment(*, severity: Severity = Severity.INFO, evidence_status: str = "unknown") -> InlineComment:
    return InlineComment(
        file_path="sound/soc/foo.c",
        line_number=10,
        message="test",
        severity=severity,
        evidence_status=evidence_status,
    )


# ---------------------------------------------------------------------------
# G1 - no violations when evidence is good
# ---------------------------------------------------------------------------


def test_G1_no_violations_when_evidence_supported():
    """check_evidence_status returns empty list when no evidence_missing BLOCKER/WARNING."""
    comments = [
        _make_comment(severity=Severity.BLOCKER, evidence_status="rule_backed"),
        _make_comment(severity=Severity.WARNING, evidence_status="supported"),
        _make_comment(severity=Severity.INFO, evidence_status="evidence_missing"),
    ]
    assert check_evidence_status(comments) == []


# ---------------------------------------------------------------------------
# G2 - flags evidence_missing BLOCKER
# ---------------------------------------------------------------------------


def test_G2_flags_evidence_missing_blocker():
    """evidence_missing BLOCKER is a §28 violation."""
    comments = [_make_comment(severity=Severity.BLOCKER, evidence_status="evidence_missing")]
    violations = check_evidence_status(comments)
    assert len(violations) == 1
    assert "blocker" in violations[0]
    assert "evidence_missing" in violations[0]
    assert "§28" in violations[0]


# ---------------------------------------------------------------------------
# G3 - flags evidence_missing WARNING
# ---------------------------------------------------------------------------


def test_G3_flags_evidence_missing_warning():
    """evidence_missing WARNING is a §28 violation."""
    comments = [_make_comment(severity=Severity.WARNING, evidence_status="evidence_missing")]
    violations = check_evidence_status(comments)
    assert len(violations) == 1
    assert "warning" in violations[0]


# ---------------------------------------------------------------------------
# G4 - does NOT flag evidence_missing INFO
# ---------------------------------------------------------------------------


def test_G4_no_violation_for_info_evidence_missing():
    """INFO with evidence_missing is not a §28 violation (only BLOCKER/WARNING)."""
    comments = [_make_comment(severity=Severity.INFO, evidence_status="evidence_missing")]
    assert check_evidence_status(comments) == []


# ---------------------------------------------------------------------------
# G5 - does NOT flag supported BLOCKER
# ---------------------------------------------------------------------------


def test_G5_no_violation_supported_blocker():
    """A BLOCKER with evidence_status='supported' has no violation."""
    comments = [_make_comment(severity=Severity.BLOCKER, evidence_status="supported")]
    assert check_evidence_status(comments) == []


# ---------------------------------------------------------------------------
# G6 - does NOT flag safety_floored BLOCKER
# ---------------------------------------------------------------------------


def test_G6_no_violation_safety_floored():
    """A BLOCKER with evidence_status='safety_floored' is not a violation."""
    comments = [_make_comment(severity=Severity.BLOCKER, evidence_status="safety_floored")]
    assert check_evidence_status(comments) == []


# ---------------------------------------------------------------------------
# G7 - exported from governance __init__
# ---------------------------------------------------------------------------


def test_G7_exported_from_governance_init():
    """check_evidence_status must be importable from kri.governance."""
    from kri.governance import check_evidence_status as cec
    assert callable(cec)
