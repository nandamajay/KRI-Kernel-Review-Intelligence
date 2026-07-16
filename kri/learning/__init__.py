"""KRI learning module — historical pattern extraction (Sprint-2 portion).

The Learning Feedback Loop (Blueprint Sec. 21.9) is completed in Sprint-3; this
package provides the deterministic *extraction + validation* stage that mines real
maintainer review comments into candidate patterns with evidence-count support
levels. Domain-agnostic; no hallucinated confidence.
"""

from __future__ import annotations

from .extraction import (
    SUPPORT_THRESHOLDS,
    CandidatePattern,
    Concern,
    HistoricalPatternExtractor,
    classify_comment,
    support_level,
)

__all__ = [
    "HistoricalPatternExtractor",
    "Concern",
    "CandidatePattern",
    "classify_comment",
    "support_level",
    "SUPPORT_THRESHOLDS",
]
