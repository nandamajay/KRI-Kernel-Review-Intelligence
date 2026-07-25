"""WP4-E tests: BLAME_HISTORY evidence wiring.

Tests:
  E1 - blame evidence promotes evidence_missing → blame_backed
  E2 - blame evidence verified=True when commit_hash present
  E3 - blame evidence strength is 0.36 (BLAME_HISTORY priority=9)
  E4 - no repo_manager → no blame enrichment
  E5 - blame failure degrades gracefully
  E6 - blame_backed has lower priority than rule_backed
  E7 - _enrich_with_blame is idempotent with empty blame result
  E8 - evidence_id is deterministic (blake2b of file:line:commit)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from kri.common.models import EvidenceGraph, EvidenceSourceType, Severity
from kri.llm.models import InlineComment
from kri.llm.reviewer import IntelligentReviewEngine, _enrich_with_blame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_comment(**kwargs) -> InlineComment:
    defaults = dict(file_path="sound/soc/foo.c", line_number=10, message="test", severity=Severity.INFO, confidence=0.5)
    defaults.update(kwargs)
    return InlineComment(**defaults)


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


def _make_ev_engine_empty() -> MagicMock:
    engine = MagicMock()
    graph = MagicMock(spec=EvidenceGraph)
    graph.has_verified_evidence.return_value = False
    graph.subsystem_rule = None
    graph.evidence = []
    engine.gather.return_value = graph
    return engine


def _make_repo_manager_with_blame(commits: list[dict] | None = None) -> MagicMock:
    rm = MagicMock()
    rm.blame.return_value = commits if commits is not None else [{"commit": "abc123def456789", "summary": "fix stuff"}]
    return rm


def _make_ire(*, evidence_engine=None, repo_manager=None) -> IntelligentReviewEngine:
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    return IntelligentReviewEngine(
        client=client,
        evidence_engine=evidence_engine,
        repo_manager=repo_manager,
    )


# ---------------------------------------------------------------------------
# E1 - blame promotes evidence_missing → blame_backed
# ---------------------------------------------------------------------------


def test_E1_blame_promotes_to_blame_backed():
    """When blame returns commits, an evidence_missing comment becomes blame_backed."""
    ev_engine = _make_ev_engine_empty()
    rm = _make_repo_manager_with_blame()

    # Mock the evidence_graph to allow direct mutation
    real_graph = EvidenceGraph(comment_id="test")
    ev_engine.gather.return_value = real_graph

    engine = _make_ire(evidence_engine=ev_engine, repo_manager=rm)
    comment = _make_comment(severity=Severity.INFO, confidence=0.5)
    result, _ = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)

    assert len(result) == 1
    assert result[0].evidence_status == "blame_backed"


# ---------------------------------------------------------------------------
# E2 - blame evidence is verified=True when commit_hash present
# ---------------------------------------------------------------------------


def test_E2_blame_evidence_is_verified():
    """Blame evidence nodes with a commit_hash are marked verified=True."""
    ev_engine = _make_ev_engine_empty()
    rm = _make_repo_manager_with_blame([{"commit": "deadbeef01234567", "summary": "add driver"}])

    real_graph = EvidenceGraph(comment_id="test")
    ev_engine.gather.return_value = real_graph

    engine = _make_ire(evidence_engine=ev_engine, repo_manager=rm)
    comment = _make_comment()
    engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)

    blame_evs = [e for e in real_graph.evidence if e.source_type == EvidenceSourceType.BLAME_HISTORY]
    assert len(blame_evs) == 1
    assert blame_evs[0].verified is True


# ---------------------------------------------------------------------------
# E3 - blame evidence strength is 0.36
# ---------------------------------------------------------------------------


def test_E3_blame_evidence_strength():
    """BLAME_HISTORY priority=9 → strength = max(0, 1-(9-1)*0.08) = 0.36."""
    ev_engine = _make_ev_engine_empty()
    rm = _make_repo_manager_with_blame()
    real_graph = EvidenceGraph(comment_id="test")
    ev_engine.gather.return_value = real_graph

    engine = _make_ire(evidence_engine=ev_engine, repo_manager=rm)
    comment = _make_comment()
    engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)

    blame_ev = next(e for e in real_graph.evidence if e.source_type == EvidenceSourceType.BLAME_HISTORY)
    assert abs(blame_ev.strength - 0.36) < 1e-9


# ---------------------------------------------------------------------------
# E4 - no repo_manager → no blame enrichment
# ---------------------------------------------------------------------------


def test_E4_no_repo_manager_no_blame():
    """Without repo_manager, no BLAME_HISTORY evidence is added."""
    ev_engine = _make_ev_engine_empty()
    real_graph = EvidenceGraph(comment_id="test")
    ev_engine.gather.return_value = real_graph

    engine = _make_ire(evidence_engine=ev_engine, repo_manager=None)
    comment = _make_comment()
    result, _ = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)

    assert result == []  # evidence_missing, no floor
    assert all(e.source_type != EvidenceSourceType.BLAME_HISTORY for e in real_graph.evidence)


# ---------------------------------------------------------------------------
# E5 - blame failure degrades gracefully
# ---------------------------------------------------------------------------


def test_E5_blame_failure_degrades():
    """If repo_manager.blame() raises, comment falls through to evidence_missing."""
    ev_engine = _make_ev_engine_empty()
    real_graph = EvidenceGraph(comment_id="test")
    ev_engine.gather.return_value = real_graph

    rm = MagicMock()
    rm.blame.side_effect = Exception("git unavailable")
    engine = _make_ire(evidence_engine=ev_engine, repo_manager=rm)

    comment = _make_comment()
    result, _ = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)
    assert result == []
    assert comment.evidence_status == "evidence_missing"


# ---------------------------------------------------------------------------
# E6 - rule_backed has priority over blame_backed
# ---------------------------------------------------------------------------


def test_E6_rule_backed_overrides_blame_backed():
    """If both rule-backed and blame evidence are present, rule_backed wins."""
    from kri.common.models import Rule
    ev_engine = _make_ev_engine_empty()
    real_graph = EvidenceGraph(comment_id="test")
    real_graph.subsystem_rule = Rule(
        rule_id="r1", category="api_usage", rule_type="soft",
        description="", rationale="", documentation_ref=None,
        historical_enforcement_rate=None, exceptions=[], version_range=None,
    )
    # Mark has_verified from the real graph via blame enrichment
    ev_engine.gather.return_value = real_graph

    rm = _make_repo_manager_with_blame()
    engine = _make_ire(evidence_engine=ev_engine, repo_manager=rm)

    comment = _make_comment()
    result, _ = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)

    assert len(result) == 1
    assert result[0].evidence_status == "rule_backed"


# ---------------------------------------------------------------------------
# E7 - empty blame result is a no-op
# ---------------------------------------------------------------------------


def test_E7_empty_blame_no_evidence_added():
    """When blame returns no entries, no evidence nodes are added."""
    ev_engine = _make_ev_engine_empty()
    real_graph = EvidenceGraph(comment_id="test")
    ev_engine.gather.return_value = real_graph

    rm = _make_repo_manager_with_blame([])  # empty list
    engine = _make_ire(evidence_engine=ev_engine, repo_manager=rm)

    comment = _make_comment()
    result, _ = engine._apply_evidence_gate([comment], _make_patch_mock(), _make_series_mock(), None)
    assert result == []
    assert real_graph.evidence == []


# ---------------------------------------------------------------------------
# E8 - evidence_id is deterministic
# ---------------------------------------------------------------------------


def test_E8_blame_evidence_id_deterministic():
    """evidence_id for BLAME_HISTORY nodes is blake2b of file:line:commit — deterministic."""
    import hashlib
    graph = EvidenceGraph(comment_id="c1")
    rm = _make_repo_manager_with_blame([{"commit": "abc123", "summary": "test"}])

    _enrich_with_blame(graph, "sound/soc/foo.c", 42, rm)

    expected_id = hashlib.blake2b(b"sound/soc/foo.c:42:abc123", digest_size=8).hexdigest()
    assert graph.evidence[0].evidence_id == expected_id
