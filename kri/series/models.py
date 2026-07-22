"""WP-S1 series-aware reasoning: frozen dataclasses.

The public type is ``SeriesReviewContext`` (renamed from the spec's
``SeriesContext`` to avoid collision with ``kri.common.models.SeriesContext``,
which is the WP-9.1a cross-patch accumulator used by the evidence engine).
The two types are unrelated: the WP-9.1a type is a symbol-usage graph for
bisectability checks; the WP-S1 type is a per-patch prompt-injection payload
for the intelligent review engine.

Every dataclass here is ``frozen=True`` so a ``SeriesReviewContext`` cannot
be mutated after ``SeriesReviewContextBuilder.build()`` returns. Sec. 40
determinism: two invocations with the same input produce byte-equal outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class PatchIndexEntry:
    """Per-patch positional information within a series."""

    patch_id: str
    index: int
    total: int
    subject: str
    files_changed: tuple[str, ...]


@dataclass(frozen=True)
class SymbolRegistry:
    """Symbols the series itself declares.

    Every value is the patch_id that introduced that symbol.
    """

    compatibles: dict[str, str] = field(default_factory=dict)
    dt_properties: dict[str, str] = field(default_factory=dict)
    c_symbols: dict[str, str] = field(default_factory=dict)
    files_added: dict[str, str] = field(default_factory=dict)

    def declares_compatible(self, s: str) -> bool:
        return s in self.compatibles

    def declares_dt_property(self, s: str) -> bool:
        return s in self.dt_properties

    def declares_c_symbol(self, s: str) -> bool:
        return s in self.c_symbols


@dataclass(frozen=True)
class SeriesReviewContext:
    """Series-level context injected as facts into per-patch review prompts."""

    series_id: str
    title: str
    cover_letter: str | None
    total_patches: int
    patch_index: dict[str, PatchIndexEntry]
    declared_symbols: SymbolRegistry
    file_touch_map: dict[str, tuple[str, ...]]

    def is_multi_patch(self) -> bool:
        return self.total_patches > 1

    def to_metadata(self) -> dict:
        return {
            "series_id": self.series_id,
            "title": self.title,
            "total_patches": self.total_patches,
            "cover_letter_present": self.cover_letter is not None,
            "declared_compatibles": sorted(self.declared_symbols.compatibles.keys()),
            "declared_dt_properties": sorted(self.declared_symbols.dt_properties.keys()),
            "declared_c_symbols": sorted(self.declared_symbols.c_symbols.keys())[:100],
            "files_added_count": len(self.declared_symbols.files_added),
            "files_touched_by_multiple_patches": sorted(
                [p for p, patches in self.file_touch_map.items() if len(patches) > 1]
            ),
        }


@dataclass(frozen=True)
class SeriesProvenance:
    """Reducer-touched-finding audit hook (populated by WP-S1B; unused in WP-S1A)."""

    depends_on_patches: tuple[str, ...] = ()
    absorbed_from: tuple[str, ...] = ()
    suppressed_alternatives: tuple[str, ...] = ()


class ReducerActionKind(str, Enum):
    """Kind of ReducerAction. Populated by WP-S1B; enum reserved here for parity."""

    R1_DECLARED_SYMBOL_SUPPRESS = "declared_symbol_suppress"
    R2_SERIES_PRESENT_SUPPRESS = "series_present_suppress"
    R3_EXTERNAL_TO_INTERNAL_REWRITE = "external_to_internal_rewrite"
    R4_LINE_BUCKET_MERGE = "line_bucket_merge"
    R5_FUNCTION_SCOPE_MERGE = "function_scope_merge"
    R6_LOW_SIGNAL_SUPPRESS = "low_signal_suppress"
    R7_PRE_EXISTING_SUPPRESS = "pre_existing_suppress"
    R8_COUPLING_NOTE = "coupling_note"


@dataclass(frozen=True)
class ReducerAction:
    """WP-S1B audit entry; reserved here for parity so WP-S1A can be imported alone."""

    kind: ReducerActionKind
    patch_id: str
    finding_ref: str
    file: str = ""
    line: int = 0
    reason: str = ""
    absorbed_refs: tuple[str, ...] = ()
    related_patch_id: str = ""

    def to_metadata(self) -> dict:
        return {
            "kind": self.kind.value,
            "patch_id": self.patch_id,
            "finding_ref": self.finding_ref,
            "file": self.file,
            "line": self.line,
            "reason": self.reason,
            "absorbed_refs": list(self.absorbed_refs),
            "related_patch_id": self.related_patch_id,
        }
