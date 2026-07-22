"""WP-S1 Series-Aware Reasoning package.

Public surface: builder + prompt renderer + reducer + frozen dataclasses.

Step B1 lands the reducer *skeleton*: dispatcher, per-rule
``evaluate_R_k`` / ``apply_R_k`` split, mode/flag plumbing. No rule
bodies yet — those follow in B4+.
"""

from __future__ import annotations

from kri.series.context import SeriesReviewContextBuilder
from kri.series.models import (
    PatchIndexEntry,
    ReducerAction,
    ReducerActionKind,
    SeriesProvenance,
    SeriesReviewContext,
    SymbolRegistry,
)
from kri.series.prompt import format_series_context
from kri.series.reducer import ReducerResult, SeriesReducer

__all__ = [
    "SeriesReviewContextBuilder",
    "SeriesReviewContext",
    "SymbolRegistry",
    "PatchIndexEntry",
    "SeriesProvenance",
    "ReducerAction",
    "ReducerActionKind",
    "ReducerResult",
    "SeriesReducer",
    "format_series_context",
]
