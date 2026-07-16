"""Simulation Engine (Blueprint Sec. 21.10 / SPEC §8).

Drives the full Review Simulation Pipeline: parse -> KG lookup -> review ->
evidence -> confidence -> report. Supports replay against a specific knowledge
state for determinism verification, and audit trail generation.

Graceful degradation: if a subsystem fails (build, lore fetch, static analysis),
the simulation continues with a degraded report noting what was unavailable.

Constitutional constraints:
  - Replay: simulate(series, config) followed by replay(series, state_id) with
    the same knowledge state yields byte-identical output (Constitution Sec. 38).
  - No domain identifiers (Constitution Sec. 9).
  - Determinism: no unseeded RNG, no wall-clock in the reasoning path.
"""

from __future__ import annotations

import json
from typing import Any

from kri.common.interfaces import DomainKnowledgePackage
from kri.common.models import (
    Decision,
    KnowledgeStateId,
    PatchSeries,
)
from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.evidence_engine.engine import EvidenceEngineImpl
from kri.knowledge_manager.manager import KnowledgeManagerImpl
from kri.process_rules.manager import ProcessEtiquettePlugin, ProcessRulesManagerImpl
from kri.report.generator import ReportGenerator
from kri.review_engine.engine import ReviewEngineImpl


class SimulationEngineImpl:
    """Concrete :class:`kri.common.interfaces.SimulationEngine`.

    Orchestrates the full pipeline from PatchSeries to ReviewReport. Snapshots
    the knowledge state before review so it can be replayed deterministically.
    """

    def __init__(
        self,
        knowledge_manager: KnowledgeManagerImpl,
        dkp: DomainKnowledgePackage | None = None,
    ) -> None:
        self._km = knowledge_manager
        self._dkp = dkp
        self._evidence_engine = EvidenceEngineImpl(knowledge_manager)
        self._confidence_engine = ConfidenceEngineImpl()
        self._review_engine = ReviewEngineImpl(
            self._evidence_engine, self._confidence_engine
        )
        self._report_generator = ReportGenerator()
        # Domain-agnostic process/etiquette checks (Constitution Sec. 9): run on
        # every patch regardless of which/whether a DKP is loaded.
        self._process_rules = ProcessRulesManagerImpl()
        self._extra_plugins = [ProcessEtiquettePlugin(self._process_rules)]
        # Track simulation results for audit.
        self._last_state_id: KnowledgeStateId | None = None
        self._last_decisions: list[Decision] = []
        self._last_report: dict[str, Any] | None = None
        # Degradation notes.
        self._degradation_notes: list[str] = []

    def simulate(
        self,
        patch_series: PatchSeries,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the complete pipeline for a patch series; return a Review Report.

        Steps:
          1. Snapshot the knowledge state (for replay).
          2. Resolve the DKP (from config or pre-injected).
          3. Execute the review pipeline (ReviewEngine.review).
          4. Generate the Report.
          5. Attach the state_id and audit trail to the report metadata.
        """
        config = config or {}
        self._degradation_notes = []

        # Step 1: Snapshot for replay.
        state_id = self._km.snapshot()
        self._last_state_id = state_id

        # Step 2: Resolve DKP.
        dkp = self._resolve_dkp(config)
        if dkp is None:
            self._degradation_notes.append(
                "No DKP available; running domain-agnostic process/etiquette "
                "checks only."
            )

        # Step 3: Review (process/etiquette checks always run via extra_plugins,
        # even with no DKP resolved).
        try:
            decisions = self._review_engine.review(
                patch_series, dkp, extra_plugins=self._extra_plugins
            )
        except Exception as exc:  # noqa: BLE001
            self._degradation_notes.append(
                f"Review engine encountered an error: {exc}. "
                "Continuing with zero decisions."
            )
            decisions = []

        self._last_decisions = decisions

        # Step 4: Generate report.
        report = self._report_generator.generate(decisions, series=patch_series)

        # Step 5: Attach state + audit metadata.
        report["metadata"]["knowledge_state_id"] = state_id.state_id
        report["metadata"]["degradation_notes"] = self._degradation_notes
        report["metadata"]["dkp_name"] = dkp.name if dkp else None
        report["metadata"]["dkp_version"] = dkp.version if dkp else None

        self._last_report = report
        return report

    def replay(
        self,
        patch_series: PatchSeries,
        knowledge_state: str,
    ) -> dict[str, Any]:
        """Replay a review against a specific (immutable) knowledge state.

        Restores the snapshotted graph and re-runs the pipeline. Because the
        knowledge state is deterministic and the review/evidence/confidence
        engines are deterministic, this yields byte-identical output to the
        original simulate() that used this state_id.
        """
        # Restore the knowledge state.
        self._km.restore(knowledge_state)

        # Rebuild engines against the restored graph.
        self._evidence_engine = EvidenceEngineImpl(self._km)
        self._confidence_engine = ConfidenceEngineImpl()
        self._review_engine = ReviewEngineImpl(
            self._evidence_engine, self._confidence_engine
        )

        # Re-run simulation with the restored state (which is now snapshotted).
        return self.simulate(patch_series)

    def audit(self, report: dict[str, Any]) -> dict[str, Any]:
        """Return the immutable audit trail for a report.

        The audit trail includes: knowledge state id, DKP version, the
        count of decisions (publishable vs. not), evidence coverage, and
        the canonical hash of the report for integrity verification.
        """
        metadata = report.get("metadata", {})
        decisions_data = report.get("decisions", [])
        publishable = [d for d in decisions_data if d.get("publishable", False)]
        unpublishable = [d for d in decisions_data if not d.get("publishable", False)]

        # Canonical hash for integrity (deterministic JSON serialization).
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
        import hashlib
        report_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        return {
            "knowledge_state_id": metadata.get("knowledge_state_id"),
            "dkp_name": metadata.get("dkp_name"),
            "dkp_version": metadata.get("dkp_version"),
            "report_version": metadata.get("report_version"),
            "total_decisions": len(decisions_data),
            "publishable_decisions": len(publishable),
            "unpublishable_decisions": len(unpublishable),
            "evidence_coverage": metadata.get("evidence_coverage", 0.0),
            "degradation_notes": metadata.get("degradation_notes", []),
            "report_hash": report_hash,
            "replay_state_id": metadata.get("knowledge_state_id"),
        }

    def _resolve_dkp(self, config: dict[str, Any]) -> DomainKnowledgePackage | None:
        """Resolve the DKP from config or from the pre-injected handle.

        If config contains 'domain', load it via the KnowledgeManager.
        Otherwise use the pre-injected DKP."""
        domain = config.get("domain")
        if domain:
            try:
                return self._km.load_dkp(domain)
            except Exception:  # noqa: BLE001
                self._degradation_notes.append(
                    f"Failed to load DKP for domain '{domain}'; "
                    "falling back to pre-injected DKP."
                )
        return self._dkp


__all__ = ["SimulationEngineImpl"]
