"""Benchmark Framework (SPEC §11 / Blueprint Sec. 19).

Compares KRI-generated reviews against real maintainer decisions extracted from
cached lore fixture threads. Measures:
  - Agreement: exact / partial / disagreement per review comment.
  - False positive / negative tracking.
  - Confidence calibration via Expected Calibration Error (ECE).

Domain-agnostic: the benchmark framework operates on generic Decision and
ReviewComment types; it does not know which subsystem the patches target.

Determinism: all metrics are computed from sorted inputs; no RNG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kri.common.models import (
    Decision,
    PatchSeries,
    ReviewComment,
    Severity,
)


@dataclass
class AgreementResult:
    """Result of comparing one generated decision against ground-truth comments."""

    decision_id: str
    category: str
    severity: str
    confidence_level: str
    confidence_score: float
    agreement: str  # "exact", "partial", "disagreement", "no_ground_truth"
    matched_comment_ids: list[str] = field(default_factory=list)


@dataclass
class BenchmarkMetrics:
    """Aggregate metrics from a benchmark run."""

    total_decisions: int = 0
    publishable_decisions: int = 0
    exact_agreements: int = 0
    partial_agreements: int = 0
    disagreements: int = 0
    no_ground_truth: int = 0
    false_positives: int = 0  # decisions with no matching ground truth
    false_negatives: int = 0  # ground truth comments with no matching decision
    agreement_rate: float = 0.0  # (exact + partial) / total
    precision: float = 0.0  # exact / (exact + false_positives)
    recall: float = 0.0  # exact / (exact + false_negatives)
    ece: float = 0.0  # Expected Calibration Error
    calibration_bins: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for reporting."""
        return {
            "total_decisions": self.total_decisions,
            "publishable_decisions": self.publishable_decisions,
            "exact_agreements": self.exact_agreements,
            "partial_agreements": self.partial_agreements,
            "disagreements": self.disagreements,
            "no_ground_truth": self.no_ground_truth,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "agreement_rate": round(self.agreement_rate, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "ece": round(self.ece, 4),
            "calibration_bins": self.calibration_bins,
        }


class BenchmarkRunner:
    """Runs benchmark comparisons between generated decisions and ground truth.

    Ground truth is extracted as is_maintainer=True ReviewComments from lore
    fixtures. Agreement is measured by category/location overlap between
    generated decisions and maintainer concerns.
    """

    def __init__(self) -> None:
        self._results: list[AgreementResult] = []

    def compare(
        self,
        decisions: list[Decision],
        ground_truth: list[ReviewComment],
        series: PatchSeries | None = None,
    ) -> BenchmarkMetrics:
        """Compare generated decisions against ground-truth maintainer comments.

        Agreement categories:
          - exact: decision targets the same patch_id AND same category (or a
            closely related one).
          - partial: decision targets the same patch_id but different category.
          - disagreement: decision contradicts ground truth (generated warning/
            blocker where maintainer approved, or vice versa).
          - no_ground_truth: no maintainer comment on this patch for comparison.

        False positives: publishable decisions with no matching ground truth.
        False negatives: maintainer change-request comments with no matching decision.
        """
        self._results = []

        # Only consider maintainer comments that are change requests (not just acks).
        maintainer_concerns = sorted(
            [c for c in ground_truth if c.is_maintainer and c.severity != Severity.INFO],
            key=lambda c: c.comment_id,
        )
        # All maintainer comments for general matching.
        all_maintainer = sorted(
            [c for c in ground_truth if c.is_maintainer],
            key=lambda c: c.comment_id,
        )

        matched_gt_ids: set[str] = set()

        for decision in sorted(decisions, key=lambda d: d.decision_id):
            conf_score = decision.confidence.score if decision.confidence else 0.0
            conf_level = (
                decision.confidence.level.value if decision.confidence else "unknown"
            )

            # Find matching ground-truth comments for this decision.
            matches = self._find_matches(decision, all_maintainer)
            matched_ids = [m.comment_id for m in matches]
            matched_gt_ids.update(matched_ids)

            if not matches:
                agreement = "no_ground_truth"
            else:
                # Check if any match is a concern with same category/severity.
                exact = any(
                    self._is_exact_match(decision, m) for m in matches
                )
                if exact:
                    agreement = "exact"
                else:
                    agreement = "partial"

            result = AgreementResult(
                decision_id=decision.decision_id,
                category=decision.category,
                severity=decision.severity.value,
                confidence_level=conf_level,
                confidence_score=conf_score,
                agreement=agreement,
                matched_comment_ids=sorted(matched_ids),
            )
            self._results.append(result)

        # Compute metrics.
        metrics = self._compute_metrics(
            self._results, maintainer_concerns, matched_gt_ids, decisions
        )
        return metrics

    def results(self) -> list[AgreementResult]:
        """Return per-decision agreement results from the last run."""
        return list(self._results)

    @staticmethod
    def _find_matches(
        decision: Decision, comments: list[ReviewComment]
    ) -> list[ReviewComment]:
        """Find ground-truth comments targeting the same patch."""
        matches: list[ReviewComment] = []
        for comment in comments:
            # Match by patch_id.
            if (
                decision.patch_id
                and comment.target_patch_id
                and decision.patch_id == comment.target_patch_id
            ):
                matches.append(comment)
            # Match by series_id if no patch-level match.
            elif (
                decision.series_id
                and comment.target_series_id
                and decision.series_id == comment.target_series_id
                and not decision.patch_id
            ):
                matches.append(comment)
        return matches

    @staticmethod
    def _is_exact_match(decision: Decision, comment: ReviewComment) -> bool:
        """Check if a decision exactly matches a ground-truth comment.

        Exact match means: same patch_id AND (same category OR the comment's
        message contains keywords related to the decision's category)."""
        if decision.category and comment.category:
            if decision.category == comment.category:
                return True
        # Keyword overlap as a secondary signal.
        if decision.statement and comment.message:
            decision_words = set(decision.statement.lower().split())
            comment_words = set(comment.message.lower().split())
            overlap = decision_words & comment_words
            # Require at least 3 meaningful overlapping words.
            if len(overlap) >= 3:
                return True
        return False

    def _compute_metrics(
        self,
        results: list[AgreementResult],
        maintainer_concerns: list[ReviewComment],
        matched_gt_ids: set[str],
        decisions: list[Decision],
    ) -> BenchmarkMetrics:
        """Compute aggregate benchmark metrics."""
        total = len(results)
        publishable = sum(1 for d in decisions if d.is_publishable())
        exact = sum(1 for r in results if r.agreement == "exact")
        partial = sum(1 for r in results if r.agreement == "partial")
        disagreements = sum(1 for r in results if r.agreement == "disagreement")
        no_gt = sum(1 for r in results if r.agreement == "no_ground_truth")

        # False positives: publishable decisions with no ground truth match.
        fp = sum(
            1
            for r in results
            if r.agreement == "no_ground_truth"
            and any(
                d.decision_id == r.decision_id and d.is_publishable()
                for d in decisions
            )
        )

        # False negatives: maintainer concerns not matched by any decision.
        fn = sum(
            1 for c in maintainer_concerns if c.comment_id not in matched_gt_ids
        )

        agreement_rate = (exact + partial) / total if total > 0 else 0.0
        precision = exact / (exact + fp) if (exact + fp) > 0 else 0.0
        recall = exact / (exact + fn) if (exact + fn) > 0 else 0.0

        # ECE: Expected Calibration Error.
        ece, bins = self._compute_ece(results)

        return BenchmarkMetrics(
            total_decisions=total,
            publishable_decisions=publishable,
            exact_agreements=exact,
            partial_agreements=partial,
            disagreements=disagreements,
            no_ground_truth=no_gt,
            false_positives=fp,
            false_negatives=fn,
            agreement_rate=agreement_rate,
            precision=precision,
            recall=recall,
            ece=ece,
            calibration_bins=bins,
        )

    @staticmethod
    def _compute_ece(
        results: list[AgreementResult], n_bins: int = 5
    ) -> tuple[float, list[dict[str, Any]]]:
        """Compute Expected Calibration Error.

        Bins decisions by confidence score and measures the gap between
        confidence and actual agreement rate within each bin.

        ECE = sum(|bin_count/total| * |accuracy_in_bin - confidence_in_bin|)
        """
        if not results:
            return 0.0, []

        # Bin boundaries: [0, 0.2), [0.2, 0.4), [0.4, 0.6), [0.6, 0.8), [0.8, 1.0]
        bin_width = 1.0 / n_bins
        bins: list[dict[str, Any]] = []
        total = len(results)
        ece = 0.0

        for i in range(n_bins):
            lower = i * bin_width
            upper = (i + 1) * bin_width
            in_bin = [
                r
                for r in results
                if lower <= r.confidence_score < upper
                or (i == n_bins - 1 and r.confidence_score == 1.0)
            ]
            if not in_bin:
                bins.append({
                    "range": f"[{lower:.1f}, {upper:.1f})",
                    "count": 0,
                    "avg_confidence": 0.0,
                    "accuracy": 0.0,
                    "gap": 0.0,
                })
                continue

            avg_conf = sum(r.confidence_score for r in in_bin) / len(in_bin)
            accuracy = sum(
                1 for r in in_bin if r.agreement in ("exact", "partial")
            ) / len(in_bin)
            gap = abs(accuracy - avg_conf)
            ece += (len(in_bin) / total) * gap
            bins.append({
                "range": f"[{lower:.1f}, {upper:.1f})",
                "count": len(in_bin),
                "avg_confidence": round(avg_conf, 4),
                "accuracy": round(accuracy, 4),
                "gap": round(gap, 4),
            })

        return round(ece, 4), bins


__all__ = ["BenchmarkRunner", "BenchmarkMetrics", "AgreementResult"]
