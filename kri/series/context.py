"""WP-S1 SeriesReviewContextBuilder — pre-pass over a PatchSeries.

Pure diff-and-string analysis. No LLM, no git, no kernel tree, no network.
Two invocations with the same input produce byte-equal output.

The builder consumes ``PatchSeries.cover_letter`` and each ``Patch.diff``
directly; it does NOT re-parse the mbox and does NOT touch the filesystem.
"""

from __future__ import annotations

import logging

from kri.common.models import Patch, PatchSeries
from kri.series.extractors import (
    extract_added_files,
    extract_c_symbols,
    extract_compatibles,
    extract_cover_letter,
    extract_dt_properties,
    parse_series_index,
)
from kri.series.models import (
    PatchIndexEntry,
    SeriesReviewContext,
    SymbolRegistry,
)

logger = logging.getLogger(__name__)


class SeriesReviewContextBuilder:
    """Pre-pass builder — produces a ``SeriesReviewContext`` from a
    ``PatchSeries``. See ``docs/WP_S1_SERIES_AWARE_REASONING_SPEC_2026-07-21.md``
    Section 2.3 for the behavioural contract.
    """

    def __init__(self) -> None:
        pass

    def build(self, series: PatchSeries) -> SeriesReviewContext:
        patch_index: dict[str, PatchIndexEntry] = {}
        compatibles: dict[str, str] = {}
        dt_properties: dict[str, str] = {}
        c_symbols: dict[str, str] = {}
        files_added: dict[str, str] = {}
        file_touch_map: dict[str, list[str]] = {}

        total = len(series.patches)

        for pos, patch in enumerate(series.patches):
            index, total_from_subject = self._resolve_index(patch, pos, total)
            entry = PatchIndexEntry(
                patch_id=patch.patch_id,
                index=index,
                total=total_from_subject,
                subject=patch.subject,
                files_changed=tuple(patch.files_changed),
            )
            if patch.patch_id in patch_index:
                logger.debug(
                    "duplicate patch_id %s in series %s — keeping first entry",
                    patch.patch_id, series.series_id,
                )
            else:
                patch_index[patch.patch_id] = entry

            for path in patch.files_changed:
                file_touch_map.setdefault(path, []).append(patch.patch_id)

            for sym in extract_compatibles(patch.diff):
                compatibles.setdefault(sym, patch.patch_id)
            for sym in extract_dt_properties(patch.diff):
                dt_properties.setdefault(sym, patch.patch_id)
            for sym in extract_c_symbols(patch.diff):
                c_symbols.setdefault(sym, patch.patch_id)
            for path in extract_added_files(patch.diff):
                files_added.setdefault(path, patch.patch_id)

        registry = SymbolRegistry(
            compatibles=dict(sorted(compatibles.items())),
            dt_properties=dict(sorted(dt_properties.items())),
            c_symbols=dict(sorted(c_symbols.items())),
            files_added=dict(sorted(files_added.items())),
        )

        # Freeze file_touch_map into ordered tuples, preserving series
        # order.  Sort the outer keys for determinism.
        frozen_touch: dict[str, tuple[str, ...]] = {
            path: tuple(patch_ids)
            for path, patch_ids in sorted(file_touch_map.items())
        }

        return SeriesReviewContext(
            series_id=series.series_id,
            title=series.title,
            cover_letter=extract_cover_letter(series),
            total_patches=total,
            patch_index=dict(sorted(patch_index.items())),
            declared_symbols=registry,
            file_touch_map=frozen_touch,
        )

    @staticmethod
    def _resolve_index(patch: Patch, pos: int, total: int) -> tuple[int, int]:
        """Resolve (index, total) for a Patch.

        Preference order:
          1. Patch.sequence + Patch.series_total when both non-zero.
          2. Parsed from Patch.subject via _SUBJECT_INDEX_RE.
          3. positional index (pos + 1) and len(series.patches).
        """
        if patch.sequence and patch.series_total:
            return patch.sequence, patch.series_total
        parsed = parse_series_index(patch.subject)
        if parsed is not None:
            return parsed
        return pos + 1, total
