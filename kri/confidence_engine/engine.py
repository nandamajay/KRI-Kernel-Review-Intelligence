"""Confidence Engine (Blueprint Sec. 16 / SPEC §6).

Computes reproducible, calibrated, explainable, conservative confidence scores
for review decisions. The 8-factor model weights are configurable (defaults from
SPEC §6.3) and both factor_scores and factor_weights are stored in the returned
ConfidenceScore for full auditability.

Properties (Constitution Sec. 31): Reproducible (same inputs + same knowledge
state => same score), Calibrated (weights tuned against benchmark), Explainable
(per-factor breakdown + text), Conservative (unknown beats wrong; missing => 0).
"""

from __future__ import annotations

from kri.common.models import (
    ConfidenceFactor,
    ConfidenceLevel,
    ConfidenceScore,
    Decision,
    EvidenceGraph,
    EvidenceSourceType,
    RuleType,
)

# ---------------------------------------------------------------------------
# SPEC §6.3 default weights (sum = 1.00)
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: dict[ConfidenceFactor, float] = {
    ConfidenceFactor.HISTORICAL_AGREEMENT: 0.20,
    ConfidenceFactor.SUBSYSTEM_EVIDENCE: 0.20,
    ConfidenceFactor.DOCUMENTATION_SUPPORT: 0.15,
    ConfidenceFactor.API_CERTAINTY: 0.15,
    ConfidenceFactor.CODE_SIMILARITY: 0.10,
    ConfidenceFactor.REVIEW_HISTORY: 0.10,
    ConfidenceFactor.VERSION_CONSISTENCY: 0.05,
    ConfidenceFactor.RUNTIME_EVIDENCE: 0.05,
}

# Source types that count toward documentation_support.
_DOCUMENTATION_SOURCE_TYPES = frozenset({
    EvidenceSourceType.DOCUMENTATION,
    EvidenceSourceType.MAINTAINERS_FILE,
    EvidenceSourceType.API_HEADER,
})

# Rule type -> base weight mapping for subsystem_evidence factor.
_RULE_TYPE_WEIGHT: dict[RuleType, float] = {
    RuleType.HARD: 1.0,
    RuleType.SOFT: 0.7,
    RuleType.PHILOSOPHICAL: 0.5,
}

# Epsilon for floating-point weight-sum validation.
_EPSILON = 1e-6


class ConfidenceEngineImpl:
    """Deterministic, conservative, 8-factor confidence scorer.

    Satisfies the ``ConfidenceEngine`` protocol from ``kri.common.interfaces``.
    """

    def __init__(self, weights: dict[ConfidenceFactor, float] | None = None) -> None:
        self._weights: dict[ConfidenceFactor, float] = (
            dict(weights) if weights is not None else dict(_DEFAULT_WEIGHTS)
        )
        weight_sum = sum(self._weights.values())
        assert abs(weight_sum - 1.0) < _EPSILON, (
            f"Confidence factor weights must sum to 1.0, got {weight_sum}"
        )

    # ------------------------------------------------------------------
    # Protocol method
    # ------------------------------------------------------------------

    def score(self, decision: Decision, evidence_graph: EvidenceGraph) -> ConfidenceScore:
        """Compute the weighted confidence score and level for a decision.

        Same inputs + same knowledge state => same score (Constitution Sec. 31/40).
        Missing evidence results in a factor score of 0.0 (conservative).
        """
        factor_scores: dict[ConfidenceFactor, float] = {
            ConfidenceFactor.HISTORICAL_AGREEMENT: (
                self._compute_historical_agreement(evidence_graph)
            ),
            ConfidenceFactor.SUBSYSTEM_EVIDENCE: (
                self._compute_subsystem_evidence(evidence_graph)
            ),
            ConfidenceFactor.DOCUMENTATION_SUPPORT: (
                self._compute_documentation_support(evidence_graph)
            ),
            ConfidenceFactor.API_CERTAINTY: (
                self._compute_api_certainty(evidence_graph)
            ),
            ConfidenceFactor.CODE_SIMILARITY: (
                self._compute_code_similarity(evidence_graph)
            ),
            ConfidenceFactor.REVIEW_HISTORY: (
                self._compute_review_history(evidence_graph)
            ),
            ConfidenceFactor.VERSION_CONSISTENCY: (
                self._compute_version_consistency(evidence_graph)
            ),
            ConfidenceFactor.RUNTIME_EVIDENCE: (
                self._compute_runtime_evidence(evidence_graph)
            ),
        }

        # Weighted sum.
        final_score = sum(
            factor_scores[f] * self._weights[f] for f in ConfidenceFactor
        )
        # Clamp to [0.0, 1.0] for safety (should already be in range).
        final_score = max(0.0, min(1.0, final_score))

        level = ConfidenceLevel.from_score(final_score)
        explanation = self._build_explanation(factor_scores)

        return ConfidenceScore(
            score=final_score,
            level=level,
            factor_scores=factor_scores,
            factor_weights=dict(self._weights),
            explanation=explanation,
        )

    # ------------------------------------------------------------------
    # Factor computation (deterministic, conservative)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_historical_agreement(eg: EvidenceGraph) -> float:
        """If evidence_graph has accepted_examples, score = len(accepted) /
        (len(accepted) + len(rejected) + 1). Else 0.0."""
        accepted = len(eg.accepted_examples)
        if accepted == 0:
            return 0.0
        rejected = len(eg.rejected_examples)
        return accepted / (accepted + rejected + 1)

    @staticmethod
    def _compute_subsystem_evidence(eg: EvidenceGraph) -> float:
        """If subsystem_rule is set, compute from rule_type weight * enforcement_rate.
        Else 0.0."""
        rule = eg.subsystem_rule
        if rule is None:
            return 0.0
        type_weight = _RULE_TYPE_WEIGHT.get(rule.rule_type, 0.5)
        rate = rule.historical_enforcement_rate
        enforcement_rate = rate if rate is not None else 0.0
        return type_weight * enforcement_rate

    @staticmethod
    def _compute_documentation_support(eg: EvidenceGraph) -> float:
        """Count verified evidence items with source_type in (DOCUMENTATION,
        MAINTAINERS_FILE, API_HEADER). Score = min(1.0, count * 0.4)."""
        count = sum(
            1 for e in eg.evidence
            if e.source_type in _DOCUMENTATION_SOURCE_TYPES and e.verified
        )
        return min(1.0, count * 0.4)

    @staticmethod
    def _compute_api_certainty(eg: EvidenceGraph) -> float:
        """Count verified evidence items with source_type == API_HEADER.
        Score = min(1.0, count * 0.5)."""
        count = sum(
            1 for e in eg.evidence
            if e.source_type == EvidenceSourceType.API_HEADER and e.verified
        )
        return min(1.0, count * 0.5)

    @staticmethod
    def _compute_code_similarity(eg: EvidenceGraph) -> float:
        """If accepted_examples or rejected_examples exist,
        score = min(1.0, (len(accepted) + len(rejected)) * 0.2). Else 0.0."""
        total = len(eg.accepted_examples) + len(eg.rejected_examples)
        if total == 0:
            return 0.0
        return min(1.0, total * 0.2)

    @staticmethod
    def _compute_review_history(eg: EvidenceGraph) -> float:
        """Count verified evidence items with source_type == REVIEW_DISCUSSION.
        Score = min(1.0, count * 0.35)."""
        count = sum(
            1 for e in eg.evidence
            if e.source_type == EvidenceSourceType.REVIEW_DISCUSSION and e.verified
        )
        return min(1.0, count * 0.35)

    @staticmethod
    def _compute_version_consistency(eg: EvidenceGraph) -> float:
        """If any evidence has a version_range set, score = 0.7.
        If all verified evidence has version_range, score = 1.0. Else 0.0."""
        if not eg.evidence:
            return 0.0

        any_has_version = any(e.version_range is not None for e in eg.evidence)
        if not any_has_version:
            return 0.0

        verified_items = [e for e in eg.evidence if e.verified]
        if verified_items and all(e.version_range is not None for e in verified_items):
            return 1.0

        return 0.7

    @staticmethod
    def _compute_runtime_evidence(eg: EvidenceGraph) -> float:
        """Count verified evidence items with source_type == STATIC_ANALYSIS.
        Score = min(1.0, count * 0.5)."""
        count = sum(
            1 for e in eg.evidence
            if e.source_type == EvidenceSourceType.STATIC_ANALYSIS and e.verified
        )
        return min(1.0, count * 0.5)

    # ------------------------------------------------------------------
    # Explanation builder
    # ------------------------------------------------------------------

    def _build_explanation(self, factor_scores: dict[ConfidenceFactor, float]) -> str:
        """Build a human-readable explanation showing top contributing factors."""
        # Compute weighted contributions and sort descending.
        contributions: list[tuple[ConfidenceFactor, float, float]] = []
        for factor in ConfidenceFactor:
            raw = factor_scores[factor]
            weighted = raw * self._weights[factor]
            contributions.append((factor, raw, weighted))

        contributions.sort(key=lambda t: t[2], reverse=True)

        # Show top factors that actually contribute (weighted > 0).
        parts: list[str] = []
        for factor, raw, weighted in contributions:
            if weighted <= 0.0:
                continue
            parts.append(
                f"{factor.value}={raw:.2f} "
                f"(w={self._weights[factor]:.2f}, contrib={weighted:.3f})"
            )

        if not parts:
            return "No contributing factors; confidence based entirely on missing evidence."

        return "Top factors: " + "; ".join(parts)
