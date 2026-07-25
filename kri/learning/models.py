"""Track-B learning data models — WP4-J/WP4-K (Phase-4V2 Track-B).

Defines:
  - ReviewHistoryEntry: a single lore review comment with mandatory provenance
  - ReviewHistorySummary: per-series aggregate surfaced in the API
  - CFMCalibrationReport: WP4-K calibration result
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from kri.common.models import Provenance


class ReviewHistoryEntry(BaseModel):
    """A single maintainer review comment extracted from a real lore thread.

    Constitution §28 / §37: every knowledge item must have provenance.
    ``source_url`` and ``message_id`` are mandatory — absent provenance is a
    Tier-0 STOP condition.
    """

    entry_id: str = Field(
        ...,
        description=(
            "Deterministic ID: sha256(series_id + ':' + message_id + ':' + excerpt_hash)[:16]. "
            "Sec-40: no uuid/random — derived from content."
        ),
    )
    series_id: str
    patch_id: str | None = None
    message_id: str = Field(..., description="Lore message-id (mandatory)")
    source_url: str = Field(..., description="Canonical lore URL (mandatory)")
    reviewer_text: str = Field(..., max_length=500, description="Original comment excerpt")
    extracted_claim: str = Field(..., description="Normalised concern category")
    evidence_type: Literal[
        "review_discussion",
        "accepted_patch",
        "rejected_patch",
        "maintainer_ack",
        "maintainer_nack",
    ]
    confidence_basis: str = Field(
        ..., description="How this was classified (rule name + signal)"
    )
    created_by: str = "WP4-J/LoreIngestionEngine"
    validation_status: Literal["pending", "validated", "rejected"] = "pending"
    provenance: Provenance = Field(default_factory=Provenance)

    @classmethod
    def make_entry_id(cls, series_id: str, message_id: str, reviewer_text: str) -> str:
        """Deterministic Sec-40-safe entry ID from content hash."""
        raw = f"{series_id}:{message_id}:{reviewer_text[:200]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ReviewHistorySummary(BaseModel):
    """Per-series aggregate surfaced in IntelligentReport (WP4-J API wiring)."""

    series_id: str
    entry_count: int = 0
    source_urls: list[str] = Field(default_factory=list)
    claim_categories: dict[str, int] = Field(default_factory=dict)
    evidence_types: dict[str, int] = Field(default_factory=dict)
    has_maintainer_feedback: bool = False


class CFMCalibrationReport(BaseModel):
    """WP4-K CFM shadow calibration result.

    ``production_gate_criteria_met`` is False by default and requires all 7
    gate criteria (Plan §8) plus Governance Auditor + Arbiter approval.

    Correlation significance fields (Track-B.7 D3 fix):
    - ``pearson_t_stat``: t-statistic for H0: ρ=0; t = r * sqrt(n-2) / sqrt(1-r^2).
      None when n < 3 or variance is zero.
    - ``correlation_significant``: True when |t| exceeds the critical value for
      df = n-2 at α=0.05 (two-tailed).  None when t-stat could not be computed.

    Minimum samples for a reliable Pearson r: at least 50. With n < 50 the
    correlation has insufficient statistical power — the gate key
    ``correlation_min_samples_met`` will be False and callers must treat any r
    as exploratory only.
    """

    series_count: int = 0
    entry_count: int = 0
    samples_calibrated: int = 0
    cfm_vs_llm_correlation: float | None = None
    mean_absolute_error: float | None = None
    false_positive_estimate: float | None = None
    factor_contributions: dict[str, float] = Field(default_factory=dict)
    production_gate_criteria_met: bool = False
    recommendation: Literal["CFM_SHADOW_STAYS", "CFM_PRODUCTION_READY"] = (
        "CFM_SHADOW_STAYS"
    )
    gate_criteria_status: dict[str, bool] = Field(default_factory=dict)
    # Track-B.6: per-claim REVIEW_HISTORY factor distribution (claim → factor score)
    review_history_distribution: dict[str, float] = Field(default_factory=dict)
    # Track-B.7 D3: significance metadata for cfm_vs_llm_correlation
    pearson_t_stat: float | None = None
    correlation_significant: bool | None = None


__all__ = [
    "ReviewHistoryEntry",
    "ReviewHistorySummary",
    "CFMCalibrationReport",
]
