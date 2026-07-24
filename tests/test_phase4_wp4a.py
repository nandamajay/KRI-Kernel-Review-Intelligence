"""WP4-A tests: evidence_status field + Decision converter.

6 tests:
  A1 - test_schema_evidence_status_roundtrip
  A2 - test_schema_cfm_confidence_defaults_none
  A3 - test_mode_off_byte_identity_pre_wiring
  A4 - test_decision_converter_maps_category
  A5 - test_decision_converter_maps_severity
  A6 - test_decision_converter_rule_id_none_when_no_dkp_match
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from kri.common.models import ReasoningLayer, Severity
from kri.llm.models import InlineComment
from kri.llm.reviewer import _comment_layer, _llm_comment_to_decision, _match_dkp_rule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_comment(**kwargs) -> InlineComment:
    defaults = dict(file_path="sound/soc/foo.c", line_number=42, message="test msg")
    defaults.update(kwargs)
    return InlineComment(**defaults)


def _make_patch(patch_id: str = "p1"):
    p = MagicMock()
    p.patch_id = patch_id
    p.subject = "Test patch"
    return p


def _make_series(series_id: str = "s1"):
    s = MagicMock()
    s.series_id = series_id
    return s


# ---------------------------------------------------------------------------
# A1 - evidence_status roundtrip
# ---------------------------------------------------------------------------


def test_schema_evidence_status_roundtrip():
    """evidence_status survives JSON serialization and deserialization."""
    c = _make_comment(evidence_status="supported")
    raw = json.loads(c.model_dump_json())
    assert raw["evidence_status"] == "supported"


def test_schema_evidence_status_default_unknown():
    """evidence_status defaults to 'unknown' on a fresh InlineComment."""
    c = _make_comment()
    assert c.evidence_status == "unknown"
    raw = json.loads(c.model_dump_json())
    assert raw["evidence_status"] == "unknown"


# ---------------------------------------------------------------------------
# A2 - cfm_confidence defaults to None
# ---------------------------------------------------------------------------


def test_schema_cfm_confidence_defaults_none():
    """cfm_confidence defaults to None and does not raise."""
    c = _make_comment()
    assert c.cfm_confidence is None
    raw = json.loads(c.model_dump_json())
    assert raw["cfm_confidence"] is None


# ---------------------------------------------------------------------------
# A3 - mode-off byte identity unchanged
# ---------------------------------------------------------------------------


def test_mode_off_byte_identity_pre_wiring():
    """Adding evidence_status/cfm_confidence must not break mode='off' baseline.

    The baseline captured in .kri/ledger/baselines/ is for the REVIEW OUTPUT
    (IntelligentReport), not for InlineComment schema. This test verifies that
    the fields are purely additive: an InlineComment deserialized from pre-WP4-A
    JSON (without evidence_status) still works.
    """
    pre_wp4a_json = json.dumps({
        "file_path": "sound/soc/test.c",
        "line_number": 10,
        "message": "use devm_clk_get",
        "severity": "warning",
        "confidence": 0.75,
    })
    c = InlineComment.model_validate_json(pre_wp4a_json)
    assert c.evidence_status == "unknown"
    assert c.cfm_confidence is None


# ---------------------------------------------------------------------------
# A4 - decision converter maps category to layer
# ---------------------------------------------------------------------------


def test_decision_converter_maps_category():
    """_llm_comment_to_decision() maps known categories to correct ReasoningLayer."""
    comment = _make_comment(category="api_usage")
    d = _llm_comment_to_decision(comment, _make_patch(), _make_series())
    assert d.layer == ReasoningLayer.SEMANTIC

    comment_style = _make_comment(category="style")
    d2 = _llm_comment_to_decision(comment_style, _make_patch(), _make_series())
    assert d2.layer == ReasoningLayer.STRUCTURAL

    comment_unknown = _make_comment(category="something_novel")
    d3 = _llm_comment_to_decision(comment_unknown, _make_patch(), _make_series())
    assert d3.layer == ReasoningLayer.SEMANTIC  # fallback


# ---------------------------------------------------------------------------
# A5 - decision converter maps severity
# ---------------------------------------------------------------------------


def test_decision_converter_maps_severity():
    """_llm_comment_to_decision() preserves severity from InlineComment."""
    for sev in (Severity.INFO, Severity.WARNING, Severity.BLOCKER):
        comment = _make_comment(severity=sev)
        d = _llm_comment_to_decision(comment, _make_patch(), _make_series())
        assert d.severity == sev


# ---------------------------------------------------------------------------
# A6 - rule_id is None when no DKP match
# ---------------------------------------------------------------------------


def test_decision_converter_rule_id_none_when_no_dkp_match():
    """With dkp=None or dkp returning no matching rule, decision.rule_id is None."""
    comment = _make_comment(category="api_usage")
    d = _llm_comment_to_decision(comment, _make_patch(), _make_series(), dkp=None)
    assert d.rule_id is None


def test_decision_converter_rule_id_matched_from_dkp():
    """When DKP has a matching rule, decision.rule_id is set."""
    mock_rule = MagicMock()
    mock_rule.rule_id = "asoc-rule-003"
    mock_rule.category = "api_usage"

    mock_dkp = MagicMock()
    mock_dkp.rules.return_value = [mock_rule]

    comment = _make_comment(category="api_usage")
    d = _llm_comment_to_decision(comment, _make_patch(), _make_series(), dkp=mock_dkp)
    assert d.rule_id == "asoc-rule-003"


def test_decision_converter_decision_id_is_deterministic():
    """Same inputs always produce the same decision_id (§40)."""
    comment = _make_comment(file_path="drivers/foo.c", line_number=99, message="check null")
    patch = _make_patch("patch-001")
    series = _make_series("series-001")
    d1 = _llm_comment_to_decision(comment, patch, series)
    d2 = _llm_comment_to_decision(comment, patch, series)
    assert d1.decision_id == d2.decision_id
    assert len(d1.decision_id) == 16  # blake2b/8 = 8 bytes = 16 hex chars


def test_decision_converter_evidence_graph_initialized_empty():
    """Decision.evidence_graph is initialized as empty EvidenceGraph, not None."""
    comment = _make_comment()
    d = _llm_comment_to_decision(comment, _make_patch(), _make_series())
    assert d.evidence_graph is not None
    assert d.evidence_graph.evidence == []
    assert not d.evidence_graph.has_verified_evidence()
