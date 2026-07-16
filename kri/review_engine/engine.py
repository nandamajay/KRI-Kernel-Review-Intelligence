"""Review Engine — Cognition Orchestrator (Blueprint Sec. 21.7 / SPEC §7).

Orchestrates the six-layer Reasoning Hierarchy (MVP: layers 1-3, structural,
semantic, design). The engine is DOMAIN-AGNOSTIC: all domain reasoning is
delegated to the DKP's reasoning_plugins. Domain knowledge enters only through
the DomainKnowledgePackage protocol handle.

Flow (SPEC §7 / §8):
  1. For each patch in the series, evaluate trigger matching against the DKP's
     reasoning_plugins.
  2. Matching plugins produce candidate Decisions (no evidence/confidence yet).
  3. Each Decision is routed through the Evidence Engine (gather + verify).
  4. Each Decision is scored by the Confidence Engine.
  5. Decisions failing is_publishable() are retained but flagged.
  6. generate_report() renders surviving Decisions into the ReviewReport.

Constitutional constraints:
  - NO domain identifiers in this module (Constitution Sec. 9).
  - Every Decision must carry >=1 verified Evidence node OR is_publishable()
    returns False (Constitution Sec. 28).
  - Deterministic: same inputs + same knowledge state => identical output
    (Constitution Sec. 31/40). No RNG, no wall-clock.
"""

from __future__ import annotations

from typing import Any

from kri.common.interfaces import (
    ConfidenceEngine,
    DomainKnowledgePackage,
    EvidenceEngine,
    ReasoningPlugin,
)
from kri.common.models import (
    Decision,
    EvidenceGraph,
    PatchSeries,
    ReasoningLayer,
)
from kri.review_engine.series_context import build_series_context

# MVP reasoning layers (SPEC §7): structural, semantic, design.
_MVP_LAYERS: frozenset[ReasoningLayer] = frozenset({
    ReasoningLayer.STRUCTURAL,
    ReasoningLayer.SEMANTIC,
    ReasoningLayer.DESIGN,
})


class ReviewEngineImpl:
    """Concrete :class:`kri.common.interfaces.ReviewEngine`.

    Orchestrates reasoning-plugin dispatch, evidence gathering, and confidence
    scoring. Domain-agnostic by construction: all domain logic lives in the DKP
    plugins; the engine merely evaluates generic triggers, invokes plugins whose
    triggers match, and pipes the resulting Decisions through the evidence and
    confidence pipeline.
    """

    def __init__(
        self,
        evidence_engine: EvidenceEngine,
        confidence_engine: ConfidenceEngine,
    ) -> None:
        self._evidence = evidence_engine
        self._confidence = confidence_engine

    def review(
        self,
        patch_series: PatchSeries,
        dkp: DomainKnowledgePackage | None,
        extra_plugins: list[ReasoningPlugin] | None = None,
    ) -> list[Decision]:
        """Produce structured Decisions for a patch series using the supplied DKP.

        ``extra_plugins`` are domain-agnostic plugins (e.g. kernel etiquette checks)
        that run regardless of which/whether a DKP is loaded — they are merged
        alongside the DKP's own reasoning_plugins(), never a substitute for the
        Domain Isolation boundary: the engine still does not know or care where
        either plugin list came from.

        For each patch: resolve matching plugins -> evaluate -> gather evidence
        -> score confidence. Returns all Decisions (both publishable and not),
        sorted by decision_id for determinism.
        """
        plugins: list[ReasoningPlugin] = list(dkp.reasoning_plugins()) if dkp else []
        plugins.extend(extra_plugins or [])
        decisions: list[Decision] = []

        series_context = build_series_context(patch_series)

        for patch in sorted(
            patch_series.patches, key=lambda p: (p.sequence or 0, p.patch_id)
        ):
            for plugin in self._matching_plugins(plugins, patch, patch_series):
                # Only run MVP layers.
                plugin_layer = _get_plugin_layer(plugin)
                if plugin_layer not in _MVP_LAYERS:
                    continue

                raw_decisions = plugin.evaluate(
                    patch, patch_series, series_context=series_context
                )
                for decision in raw_decisions:
                    # Attach evidence graph.
                    evidence_graph = self._evidence.gather(
                        decision, series_context=series_context
                    )
                    decision = decision.model_copy(
                        update={"evidence_graph": evidence_graph}
                    )

                    # Score confidence.
                    confidence = self._confidence.score(decision, evidence_graph)
                    decision = decision.model_copy(
                        update={"confidence": confidence}
                    )

                    decisions.append(decision)

        # Stable ordering by decision_id (determinism).
        decisions.sort(key=lambda d: d.decision_id)
        return decisions

    def explain(self, decision: Decision) -> EvidenceGraph:
        """Return the Evidence Graph justifying a decision.

        If the decision already has an evidence_graph attached, return it
        directly. Otherwise, gather fresh evidence via the Evidence Engine."""
        if decision.evidence_graph is not None:
            return decision.evidence_graph
        return self._evidence.gather(decision)

    def generate_report(self, decisions: list[Decision]) -> dict[str, Any]:
        """Assemble the structured Review Explainability Report.

        Delegates to the Report Generator module. This method provides a
        convenience entry point from the ReviewEngine protocol; the full
        report module offers richer formatting."""
        from kri.report.generator import ReportGenerator

        generator = ReportGenerator()
        return generator.generate(decisions)

    @staticmethod
    def _matching_plugins(
        plugins: list[Any],
        patch: Any,
        series: PatchSeries,
    ) -> list[ReasoningPlugin]:
        """Return plugins whose trigger matches the patch (deterministic order).

        Trigger evaluation is generic: we call plugin.applies(patch, series)
        which the plugin implements. The engine does NOT parse triggers itself
        beyond this dispatch — trigger semantics are owned by the plugin.

        Plugins are already sorted by plugin_id (the DKP contract ensures this),
        but we re-sort here defensively."""
        matching: list[ReasoningPlugin] = []
        for plugin in sorted(plugins, key=lambda p: p.plugin_id):
            if plugin.applies(patch, series):
                matching.append(plugin)
        return matching


def _get_plugin_layer(plugin: Any) -> ReasoningLayer:
    """Extract the reasoning layer from a plugin.

    Plugins may declare their layer as a property. Falls back to DESIGN
    (the most conservative MVP layer) if not declared."""
    layer = getattr(plugin, "layer", None)
    if isinstance(layer, ReasoningLayer):
        return layer
    return ReasoningLayer.DESIGN


__all__ = ["ReviewEngineImpl"]
