"""Track-B WP4-K: CFM Shadow Calibration Engine.

Computes CFM shadow scores using Track-B lore review evidence, compares against
LLM self-reported confidence, and produces a CFMCalibrationReport.

CFM remains shadow-only unless all 7 gate criteria pass (Plan §8). The
``production_gate_criteria_met`` field defaults to False and requires an
explicit Governance Auditor + Arbiter approval signal.

Sec-40: no random/uuid/datetime.now; Pearson correlation computed
deterministically from sample data.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kri.common.models import (
    ConfidenceFactor,
    Decision,
    EvidenceGraph,
    EvidenceSourceType,
    Provenance,
    ReasoningLayer,
    RuleType,
)
from kri.learning.models import CFMCalibrationReport, ReviewHistoryEntry

if TYPE_CHECKING:
    from kri.confidence_engine.engine import ConfidenceEngineImpl
    from kri.learning.store import ReviewHistoryStore

logger = logging.getLogger(__name__)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient; None if fewer than 2 samples."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denom_x = sum((x - mx) ** 2 for x in xs) ** 0.5
    denom_y = sum((y - my) ** 2 for y in ys) ** 0.5
    if denom_x < 1e-10 or denom_y < 1e-10:
        return None
    return num / (denom_x * denom_y)


def _mean_abs_error(xs: list[float], ys: list[float]) -> float | None:
    if not xs:
        return None
    return sum(abs(x - y) for x, y in zip(xs, ys)) / len(xs)


def _build_evidence_graph_for_calibration(
    comment_id: str,
    entries: list[ReviewHistoryEntry],
) -> EvidenceGraph:
    """Build an EvidenceGraph populated with lore review evidence for calibration."""
    from kri.common.models import Evidence as EvidenceModel
    import hashlib as _hl

    evidence_nodes = []
    accepted = []
    rejected = []

    for e in entries:
        source_type = EvidenceSourceType.REVIEW_DISCUSSION
        if e.evidence_type == "accepted_patch":
            source_type = EvidenceSourceType.ACCEPTED_PATCH
        elif e.evidence_type == "rejected_patch":
            source_type = EvidenceSourceType.REJECTED_PATCH

        if e.evidence_type == "accepted_patch" and e.patch_id:
            accepted.append(e.patch_id)
        elif e.evidence_type in ("rejected_patch", "maintainer_nack") and e.patch_id:
            rejected.append(e.patch_id)

        ev_id = "calib:" + _hl.sha256(f"calib:{e.entry_id}".encode()).hexdigest()[:12]
        ev = EvidenceModel(
            evidence_id=ev_id,
            source_type=source_type,
            summary=f"{e.extracted_claim}: {e.reviewer_text[:60]}",
            provenance=e.provenance,
            verified=False,
            strength=e.provenance.source_confidence or 0.3,
        )
        evidence_nodes.append(ev)

    return EvidenceGraph(
        comment_id=comment_id,
        evidence=evidence_nodes,
        accepted_examples=list(set(accepted)),
        rejected_examples=list(set(rejected)),
    )


class CFMCalibrator:
    """Calibrate CFM shadow scores against LLM confidence using Track-B lore data.

    Usage::

        calibrator = CFMCalibrator(confidence_engine, store)
        report = calibrator.calibrate(llm_comments)
    """

    def __init__(
        self,
        confidence_engine: "ConfidenceEngineImpl",
        store: "ReviewHistoryStore",
    ) -> None:
        self._engine = confidence_engine
        self._store = store

    def calibrate(
        self,
        llm_comments: list[tuple[str, float]] | list[tuple[str, float, str]],
    ) -> CFMCalibrationReport:
        """Run shadow calibration.

        Args:
            llm_comments: list of (comment_id, llm_confidence) pairs,
                OR (comment_id, llm_confidence, claim_category) triples.
                When claim_category is present (Track-B.6), evidence selection
                uses store.by_claim(category) — matching the live review path.
                When absent (Track-B / backward-compat), falls back to all_entries.

        Returns:
            CFMCalibrationReport with shadow scores + comparison metrics.
        """
        all_entries = self._store.all()
        series_count = len({e.series_id for e in all_entries})
        entry_count = len(all_entries)

        if not llm_comments:
            logger.info("WP4-K: no llm_comments provided; returning empty calibration")
            gate_empty: dict[str, bool] = {
                "ge_20_series_ingested": series_count >= 20,
                "cfm_scores_for_10_comments": False,
                "correlation_computed": False,
                "correlation_non_negative": False,
                "fp_estimate_acceptable": False,
                "no_safety_floor_violation": True,
                "browser_api_cli_validated": False,
            }
            return CFMCalibrationReport(
                series_count=series_count,
                entry_count=entry_count,
                samples_calibrated=0,
                recommendation="CFM_SHADOW_STAYS",
                gate_criteria_status=gate_empty,
            )

        # Detect whether claim categories were provided (Track-B.6 path)
        has_claims = len(llm_comments) > 0 and len(llm_comments[0]) == 3

        cfm_scores: list[float] = []
        llm_scores: list[float] = []
        high_cfm_low_llm: int = 0

        for row in llm_comments:
            comment_id: str = row[0]
            llm_conf: float = row[1]
            claim_category: str | None = row[2] if has_claims else None  # type: ignore[index]

            # Track-B.6: use claim-based evidence when category is available.
            # Mirrors the live review path (_enrich_with_lore_history).
            if claim_category and claim_category != "review_discussion":
                claim_entries = self._store.by_claim(claim_category)
                entries_for_comment = claim_entries if claim_entries else []
            else:
                # Backward-compat: fall back to all entries (Track-B behaviour)
                entries_for_comment = all_entries

            eg = _build_evidence_graph_for_calibration(comment_id, entries_for_comment)

            # Build a minimal Decision for the confidence engine
            decision = Decision(
                decision_id=comment_id,
                series_id="calib:wp4k",
                layer=ReasoningLayer.SEMANTIC,
            )

            try:
                score = self._engine.score(decision, eg)
                cfm_score = score.score
            except Exception as exc:
                logger.debug("WP4-K: cfm score failed for %s: %s", comment_id, exc)
                continue

            cfm_scores.append(cfm_score)
            llm_scores.append(llm_conf)

            if cfm_score > 0.7 and llm_conf < 0.35:
                high_cfm_low_llm += 1

        samples = len(cfm_scores)
        corr = _pearson(cfm_scores, llm_scores)
        mae = _mean_abs_error(cfm_scores, llm_scores)
        fp_est = (high_cfm_low_llm / samples) if samples > 0 else None

        # Factor contribution summary (average across all calibrations)
        factor_contribs: dict[str, float] = {}
        rh_distribution: dict[str, float] = {}  # Track-B.6: per-claim REVIEW_HISTORY distribution
        if cfm_scores:
            factor_totals: dict[str, float] = {}
            factor_count = 0
            for row in llm_comments:
                cmt_id: str = row[0]
                claim_cat: str | None = row[2] if has_claims else None  # type: ignore[index]
                if claim_cat and claim_cat != "review_discussion":
                    claim_ents = self._store.by_claim(claim_cat)
                    ents_row = claim_ents if claim_ents else []
                else:
                    ents_row = all_entries
                eg = _build_evidence_graph_for_calibration(cmt_id, ents_row)
                dec = Decision(
                    decision_id=cmt_id,
                    series_id="calib:wp4k",
                    layer=ReasoningLayer.SEMANTIC,
                )
                try:
                    sc = self._engine.score(dec, eg)
                    for factor, val in sc.factor_scores.items():
                        key = factor.value if hasattr(factor, "value") else str(factor)
                        factor_totals[key] = factor_totals.get(key, 0.0) + val
                    # Track-B.6: record per-claim REVIEW_HISTORY factor
                    if claim_cat and claim_cat not in rh_distribution:
                        rh_key = "review_history"
                        for f, v in sc.factor_scores.items():
                            fk = f.value if hasattr(f, "value") else str(f)
                            if fk == rh_key:
                                rh_distribution[claim_cat] = round(v, 4)
                    factor_count += 1
                except Exception:
                    pass
            if factor_count > 0:
                factor_contribs = {k: round(v / factor_count, 4) for k, v in factor_totals.items()}

        # Gate criteria evaluation
        gate: dict[str, bool] = {
            "ge_20_series_ingested": series_count >= 20,
            "cfm_scores_for_10_comments": samples >= 10,
            "correlation_computed": corr is not None,
            "correlation_non_negative": (corr is not None and corr >= 0.0),
            "fp_estimate_acceptable": (fp_est is not None and fp_est <= 0.40),
            "no_safety_floor_violation": True,  # Validated by Safety Floor checks separately
            "browser_api_cli_validated": False,  # Set externally after B6 validation
        }
        # Only shadow stays — production_gate_criteria_met not auto-set
        all_met = False  # Governance Auditor + Arbiter approval required externally

        recommendation: str = "CFM_SHADOW_STAYS"
        if all_met:
            recommendation = "CFM_PRODUCTION_READY"

        logger.info(
            "WP4-K: calibration complete — series=%d entries=%d samples=%d "
            "corr=%s mae=%s fp=%s",
            series_count,
            entry_count,
            samples,
            f"{corr:.3f}" if corr is not None else "N/A",
            f"{mae:.3f}" if mae is not None else "N/A",
            f"{fp_est:.3f}" if fp_est is not None else "N/A",
        )

        return CFMCalibrationReport(
            series_count=series_count,
            entry_count=entry_count,
            samples_calibrated=samples,
            cfm_vs_llm_correlation=corr,
            mean_absolute_error=mae,
            false_positive_estimate=fp_est,
            factor_contributions=factor_contribs,
            production_gate_criteria_met=False,
            recommendation=recommendation,
            gate_criteria_status=gate,
            review_history_distribution=rh_distribution,
        )


__all__ = ["CFMCalibrator"]
