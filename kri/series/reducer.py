"""WP-S1B Series Reducer — skeleton (Step B1).

The reducer is the post-review, post-merge phase that applies series-aware
rules (R1, R3–R8) to a per-patch ``list[InlineComment]``. Every rule is
split into an ``evaluate_R_k`` trigger (records what WOULD happen — always
runs when the rule is enabled) and an ``apply_R_k`` mutator (only runs when
``mode == "on"``). ``mode == "shadow"`` produces audit records without
mutating findings; ``mode == "off"`` short-circuits entirely and is
byte-identical to the pre-WP-S1B post-``_merge_comments`` path.

**Step B1 status**: skeleton only. No rule bodies yet.

- **R2 is deliberately NOT registered.** Readiness review §5 defers R2 on
  logical grounds — it conflates "series has ≥2 patches" with "companion
  finding present". A future WP-S1B milestone may re-introduce it with a
  proper companion-presence check.
- **R5 / R6 / R7 are gated** behind per-rule feature flags
  ``series_r{5,6,7}_enabled`` and default to disabled per readiness §6.1.
- Rule sequencing (spec §5.2, readiness §6.3): R1 → R3 → R4 → R5 → R6 → R7 → R8.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from kri.series.models import (
    ReducerAction,
    ReducerActionKind,
    SeriesReviewContext,
)

if TYPE_CHECKING:
    from kri.llm.models import InlineComment

Mode = Literal["off", "shadow", "on"]

# Confidence floor at which a "warning" finding must not be suppressed.
# Blockers are ALWAYS floored (regardless of confidence).
_WARNING_CONFIDENCE_FLOOR = 0.7

# Categories that may be merged across mismatched labels (readiness §6.4).
_SOFT_CATEGORIES = frozenset({"convention", "style", "nit"})

# R1 trigger phrases (readiness spec §5.R1). Match is case-insensitive
# and substring-based; anchoring to whole words / regex is intentionally
# NOT done — real reviewer text is prose, not a fixed grammar. The
# phrase list is kept narrow: every entry is binding-specific so a
# generic "not documented" comment cannot fire the rule alone.
_R1_TRIGGER_PHRASES: tuple[str, ...] = (
    "no corresponding yaml binding",
    "no binding document",
    "missing binding",
    "undocumented compatible",
    "not documented in bindings",
    "no dt binding",
)


@dataclass(frozen=True)
class _Rule:
    """A registered reducer rule.

    ``flag`` is the name of the per-rule enable flag in the ``flags`` dict
    passed to :meth:`SeriesReducer.reduce`. ``None`` means the rule is
    always enabled whenever ``mode != "off"``.
    """

    kind: ReducerActionKind
    flag: str | None


@dataclass(frozen=True)
class ReducerResult:
    """Return value from :meth:`SeriesReducer.reduce`.

    ``comments`` is the possibly-mutated per-patch comment list. For
    ``mode="off"`` and ``mode="shadow"``, it is exactly the same list as
    was passed in. ``actions`` is the audit trail — empty for ``mode="off"``
    since no evaluator runs.
    """

    comments: list[InlineComment]
    actions: list[ReducerAction] = field(default_factory=list)


class SeriesReducer:
    """Post-merge, per-patch series-aware comment reducer.

    Public entry point: :meth:`reduce`. For every registered rule the
    dispatcher runs an ``evaluate_R_k`` trigger (which returns a list of
    :class:`ReducerAction` records describing what the rule *would* do)
    and, when ``mode == "on"`` and the rule fired, an ``apply_R_k``
    mutator. Rules whose ``flag`` attribute is ``None`` are always active;
    gated rules require the corresponding entry in the ``flags`` dict.

    Every rule body is a stub in B1; real behaviour lands in B4+ per the
    readiness review §7 milestone plan.
    """

    def __init__(self) -> None:
        # Rule ordering matches spec §5.2 / readiness §6.3.
        # R2 is deliberately omitted (deferred).
        self._rules: tuple[_Rule, ...] = (
            _Rule(ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS, None),
            _Rule(ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE, None),
            _Rule(ReducerActionKind.R4_LINE_BUCKET_MERGE, None),
            _Rule(ReducerActionKind.R5_FUNCTION_SCOPE_MERGE, "series_r5_enabled"),
            _Rule(ReducerActionKind.R6_LOW_SIGNAL_SUPPRESS, "series_r6_enabled"),
            _Rule(ReducerActionKind.R7_PRE_EXISTING_SUPPRESS, "series_r7_enabled"),
            _Rule(ReducerActionKind.R8_COUPLING_NOTE, None),
        )

    def reduce(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext | None,
        mode: Mode = "off",
        flags: dict[str, bool] | None = None,
    ) -> ReducerResult:
        """Run enabled rules against ``comments`` for a single patch.

        - ``mode="off"``: return input unchanged, no evaluators run.
          Guarantees byte-identity with the pre-WP-S1B path.
        - ``mode="shadow"``: run every enabled evaluator; NEVER mutate
          ``comments``; return actions for audit.
        - ``mode="on"``: run evaluators, then apply mutators.

        The reducer is a no-op when ``series_ctx`` is ``None`` or the
        series is single-patch — series-aware reasoning has no signal
        there. Byte-identity is preserved in both cases.
        """
        if mode == "off":
            return ReducerResult(comments=comments, actions=[])
        if series_ctx is None or not series_ctx.is_multi_patch():
            return ReducerResult(comments=comments, actions=[])

        flags = flags or {}
        actions: list[ReducerAction] = []
        working = comments  # mutated only in mode == "on"

        for rule in self._rules:
            if rule.flag is not None and not flags.get(rule.flag, False):
                continue
            evaluate_fn = getattr(self, f"_evaluate_{rule.kind.name.split('_')[0]}")
            apply_fn = getattr(self, f"_apply_{rule.kind.name.split('_')[0]}")
            rule_actions = evaluate_fn(patch_id, working, series_ctx)
            actions.extend(rule_actions)
            if mode == "on" and rule_actions:
                working = apply_fn(working, rule_actions)

        return ReducerResult(comments=working, actions=actions)

    # ------------------------------------------------------------------
    # Shared helpers.
    # ------------------------------------------------------------------

    @staticmethod
    def _is_safety_floored(comment: InlineComment) -> bool:
        """Return True when ``comment`` must never be suppressed.

        Readiness review §6.4: blockers are always kept, and warnings with
        confidence ≥ 0.7 are always kept. Suppressive rules (R1, R6, R7)
        must consult this before deleting a finding.
        """
        sev = getattr(comment.severity, "value", comment.severity)
        if sev == "blocker":
            return True
        if sev == "warning" and comment.confidence >= _WARNING_CONFIDENCE_FLOOR:
            return True
        return False

    @staticmethod
    def _same_category(a: InlineComment, b: InlineComment) -> bool:
        """Whether two comments may be merged under R4 / R5 (readiness §6.4).

        Same category, OR both categories fall inside the "soft" set
        ``{convention, style, nit}``.
        """
        if a.category == b.category:
            return True
        return a.category in _SOFT_CATEGORIES and b.category in _SOFT_CATEGORIES

    # ------------------------------------------------------------------
    # Rule stubs. Every ``_evaluate_R_k`` returns [] and every ``_apply_R_k``
    # is the identity map. Real bodies land in B4 (R1/R3), B5 (R4), B6 (R8),
    # B7 (R5/R6/R7) per readiness §7.
    # ------------------------------------------------------------------

    def _evaluate_R1(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> list[ReducerAction]:
        """Trigger R1 for findings that complain about a missing binding
        for a compatible / DT-property that a sibling patch in this
        series actually declares.

        Matching is intentionally narrow to keep false-positive rate
        low: (1) the finding's text must contain a binding-specific
        trigger phrase, AND (2) the finding must literally mention the
        declared symbol as a substring. The symbol match is the real
        disambiguator; the phrase list only weeds out unrelated
        comments that happen to mention a symbol string.

        Safety floor per readiness §6.4 is applied here (not in apply_R1)
        so audit output under mode='shadow' already reflects what would
        actually be suppressed — a floored finding NEVER generates an
        action.
        """
        registry = series_ctx.declared_symbols
        if not registry.compatibles and not registry.dt_properties:
            return []

        actions: list[ReducerAction] = []
        for idx, comment in enumerate(comments):
            if self._is_safety_floored(comment):
                continue

            haystack_parts = [comment.message or ""]
            if comment.upstream_comment:
                haystack_parts.append(comment.upstream_comment)
            haystack = " ".join(haystack_parts).lower()

            if not any(phrase in haystack for phrase in _R1_TRIGGER_PHRASES):
                continue

            matched_symbol: str | None = None
            declaring_patch: str | None = None
            # Longest declared symbol first — a specific compatible
            # like "foo,bar-sndcard" wins over a prefix match on "foo,bar".
            for sym in sorted(
                list(registry.compatibles.keys()) + list(registry.dt_properties.keys()),
                key=len,
                reverse=True,
            ):
                if sym and sym.lower() in haystack:
                    matched_symbol = sym
                    declaring_patch = (
                        registry.compatibles.get(sym)
                        or registry.dt_properties.get(sym)
                        or ""
                    )
                    break

            if matched_symbol is None:
                continue

            actions.append(
                ReducerAction(
                    kind=ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS,
                    patch_id=patch_id,
                    finding_ref=f"{comment.file_path}:{comment.line_number}:{idx}",
                    file=comment.file_path,
                    line=comment.line_number,
                    reason=f"declared_by_{declaring_patch}:{matched_symbol}",
                    related_patch_id=declaring_patch or "",
                )
            )
        return actions

    def _apply_R1(
        self,
        comments: list[InlineComment],
        actions: list[ReducerAction],
    ) -> list[InlineComment]:
        """Drop every comment whose ``file:line:index`` was flagged by
        :meth:`_evaluate_R1`. Uses index (positional) rather than object
        identity so a later rule that re-ordered the list would still
        match — but in the current sequencing R1 runs first, so the
        list is untouched at this point.
        """
        if not actions:
            return comments
        drop_refs = {a.finding_ref for a in actions}
        return [
            c
            for idx, c in enumerate(comments)
            if f"{c.file_path}:{c.line_number}:{idx}" not in drop_refs
        ]

    def _evaluate_R3(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> list[ReducerAction]:
        return []

    def _apply_R3(
        self,
        comments: list[InlineComment],
        actions: list[ReducerAction],
    ) -> list[InlineComment]:
        return comments

    def _evaluate_R4(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> list[ReducerAction]:
        return []

    def _apply_R4(
        self,
        comments: list[InlineComment],
        actions: list[ReducerAction],
    ) -> list[InlineComment]:
        return comments

    def _evaluate_R5(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> list[ReducerAction]:
        return []

    def _apply_R5(
        self,
        comments: list[InlineComment],
        actions: list[ReducerAction],
    ) -> list[InlineComment]:
        return comments

    def _evaluate_R6(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> list[ReducerAction]:
        return []

    def _apply_R6(
        self,
        comments: list[InlineComment],
        actions: list[ReducerAction],
    ) -> list[InlineComment]:
        return comments

    def _evaluate_R7(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> list[ReducerAction]:
        return []

    def _apply_R7(
        self,
        comments: list[InlineComment],
        actions: list[ReducerAction],
    ) -> list[InlineComment]:
        return comments

    def _evaluate_R8(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> list[ReducerAction]:
        return []

    def _apply_R8(
        self,
        comments: list[InlineComment],
        actions: list[ReducerAction],
    ) -> list[InlineComment]:
        return comments
