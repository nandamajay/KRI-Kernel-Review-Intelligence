"""WP-S1 Series-Aware Reasoning package.

Public surface: builder + prompt renderer + frozen dataclasses.

The reducer (WP-S1B) is not shipped in this package yet; it will be added in
a follow-up commit boundary. Consumers that only need the builder can import
from here directly.
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

__all__ = [
    "SeriesReviewContextBuilder",
    "SeriesReviewContext",
    "SymbolRegistry",
    "PatchIndexEntry",
    "SeriesProvenance",
    "ReducerAction",
    "ReducerActionKind",
    "format_series_context",
]
