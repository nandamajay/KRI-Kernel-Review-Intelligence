"""Cross-patch resolver (WP-9.1a; SPEC.md Sec. 12).

Kernel patch series routinely split one change across N patches: a symbol
declared in patch 1/N and used in patch 3/N is correct kernel practice, not a
"missing symbol" defect. This module resolves a candidate missing-symbol/
missing-file/missing-binding finding against the :class:`SeriesContext`
accumulator (built once per series, see
:mod:`kri.review_engine.series_context`) so the Evidence Engine can suppress
false positives that are actually satisfied by an earlier patch in the same
series, and upgrade genuine bisectability bugs (a symbol used by an earlier
patch than the one that introduces it -- ``git bisect`` will find a broken
intermediate commit).

Determinism (Constitution Sec. 40): both functions here are pure functions of
their arguments -- no RNG, no wall-clock, no I/O.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from kri.common.models import SeriesContext

ResolutionOutcome = Literal[
    "introduced_earlier", "introduced_here", "introduced_later", "not_in_series"
]


def resolve_symbol_reference(
    symbol: str, patch_sequence: int, ctx: SeriesContext
) -> ResolutionOutcome:
    """Classify a symbol reference at ``patch_sequence`` against the series.

    - "introduced_earlier": some sequence < patch_sequence introduces the
      symbol -- the reference is satisfied by an earlier patch in the series.
    - "introduced_here": ``patch_sequence`` itself introduces the symbol.
    - "introduced_later": some sequence > patch_sequence introduces the
      symbol -- a bisectability bug (used before it exists).
    - "not_in_series": no patch in the series introduces the symbol at all.
    """
    introducing_sequences = [
        seq for seq, symbols in ctx.introduced_symbols.items() if symbol in symbols
    ]
    if not introducing_sequences:
        return "not_in_series"

    earlier = [seq for seq in introducing_sequences if seq < patch_sequence]
    if earlier:
        return "introduced_earlier"
    if patch_sequence in introducing_sequences:
        return "introduced_here"
    return "introduced_later"


class BisectabilityViolation(NamedTuple):
    """A symbol used at ``patch_sequence`` but only introduced at
    ``introduced_at_sequence`` > ``patch_sequence`` -- ``git bisect`` will
    land on a broken intermediate commit between the two."""

    patch_sequence: int
    symbol: str
    introduced_at_sequence: int


def check_bisectability(ctx: SeriesContext) -> list[BisectabilityViolation]:
    """Enumerate every use-before-introduce symbol reference in the series.

    Deterministic ordering: sorted by (patch_sequence, symbol)."""
    violations: list[BisectabilityViolation] = []

    for use_seq, symbols in ctx.used_symbols.items():
        for symbol in symbols:
            outcome = resolve_symbol_reference(symbol, use_seq, ctx)
            if outcome != "introduced_later":
                continue
            introduced_at = min(
                seq
                for seq, introduced in ctx.introduced_symbols.items()
                if symbol in introduced and seq > use_seq
            )
            violations.append(
                BisectabilityViolation(
                    patch_sequence=use_seq,
                    symbol=symbol,
                    introduced_at_sequence=introduced_at,
                )
            )

    violations.sort(key=lambda v: (v.patch_sequence, v.symbol))
    return violations


__all__ = [
    "ResolutionOutcome",
    "resolve_symbol_reference",
    "BisectabilityViolation",
    "check_bisectability",
]
