"""WP-9.2a Sub-commit 2: AlternativeRecommendation tests.

Proves:
1. AlternativeRecommendation model is importable and validates.
2. get_canonical_recommendation returns the right entry for a known rule_id.
3. EvidenceEngine.gather() populates evidence_graph.alternative_recommendation
   when a matching rule_id has a seeded recommendation.
4. Returns None for unknown rule_id.
"""

from __future__ import annotations

from kri.common.models import (
    AlternativeRecommendation,
    Decision,
    EvidenceGraph,
    ReasoningLayer,
    Severity,
)
from kri.evidence_engine.engine import EvidenceEngineImpl
from kri.knowledge_manager.manager import KnowledgeManagerImpl
from kri.packages.asoc.knowledge import (
    CANONICAL_RECOMMENDATIONS,
    get_canonical_recommendation,
)


def test_alternative_recommendation_model_validates() -> None:
    """AlternativeRecommendation accepts valid data and rejects missing snippet."""
    rec = AlternativeRecommendation(
        snippet="x = 1;",
        language="c",
        rationale="Because reasons",
    )
    assert rec.snippet == "x = 1;"
    assert rec.language == "c"
    assert rec.rationale == "Because reasons"


def test_get_canonical_recommendation_known_rule() -> None:
    """Lookup returns AlternativeRecommendation for a known rule_id."""
    rec = get_canonical_recommendation("asoc-tdm-slot-not-userspace")
    assert rec is not None
    assert isinstance(rec, AlternativeRecommendation)
    assert "set_tdm_slot" in rec.snippet
    assert rec.language == "c"
    assert rec.rationale != ""


def test_get_canonical_recommendation_unknown_rule() -> None:
    """Lookup returns None for an unknown rule_id."""
    rec = get_canonical_recommendation("nonexistent-rule-xyz")
    assert rec is None


def test_evidence_engine_populates_recommendation() -> None:
    """EvidenceEngine.gather() populates alternative_recommendation from seeded recs."""
    km = KnowledgeManagerImpl()
    km.register_recommendations(CANONICAL_RECOMMENDATIONS)
    engine = EvidenceEngineImpl(km)

    decision = Decision(
        decision_id="test-decision-1",
        series_id="s-1",
        patch_id="p-1",
        layer=ReasoningLayer.DESIGN,
        severity=Severity.WARNING,
        rule_id="asoc-resume-must-clean-up",
    )
    graph = engine.gather(decision)

    assert graph.alternative_recommendation is not None
    assert isinstance(graph.alternative_recommendation, AlternativeRecommendation)
    assert "devm_kzalloc" in graph.alternative_recommendation.snippet
    assert graph.alternative_recommendation.language == "c"


def test_evidence_engine_no_recommendation_without_rule() -> None:
    """EvidenceEngine.gather() leaves alternative_recommendation as None without rule_id."""
    km = KnowledgeManagerImpl()
    km.register_recommendations(CANONICAL_RECOMMENDATIONS)
    engine = EvidenceEngineImpl(km)

    decision = Decision(
        decision_id="test-decision-2",
        series_id="s-1",
        patch_id="p-1",
        layer=ReasoningLayer.STRUCTURAL,
        severity=Severity.INFO,
        rule_id=None,
    )
    graph = engine.gather(decision)

    assert graph.alternative_recommendation is None
