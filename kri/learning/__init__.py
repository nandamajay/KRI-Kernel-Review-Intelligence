"""KRI learning module — historical pattern extraction (Sprint-2 portion).

The Learning Feedback Loop (Blueprint Sec. 21.9) is completed in Sprint-3; this
package provides the deterministic *extraction + validation* stage that mines real
maintainer review comments into candidate patterns with evidence-count support
levels. Domain-agnostic; no hallucinated confidence.

Track-B (WP4-I/J/K) adds:
  - ReviewHistoryEntry / ReviewHistorySummary / CFMCalibrationReport (models.py)
  - ReviewHistoryStore (store.py)
  - LoreIngestionEngine (ingestion.py)
  - CFMCalibrator (calibration.py)
"""

from __future__ import annotations

from .calibration import CFMCalibrator
from .extraction import (
    SUPPORT_THRESHOLDS,
    CandidatePattern,
    Concern,
    HistoricalPatternExtractor,
    classify_comment,
    support_level,
)
from .ingestion import LoreIngestionEngine, ingest_dataset
from .models import CFMCalibrationReport, ReviewHistoryEntry, ReviewHistorySummary
from .store import ReviewHistoryStore

__all__ = [
    "HistoricalPatternExtractor",
    "Concern",
    "CandidatePattern",
    "classify_comment",
    "support_level",
    "SUPPORT_THRESHOLDS",
    "ReviewHistoryEntry",
    "ReviewHistorySummary",
    "CFMCalibrationReport",
    "ReviewHistoryStore",
    "LoreIngestionEngine",
    "ingest_dataset",
    "CFMCalibrator",
]
