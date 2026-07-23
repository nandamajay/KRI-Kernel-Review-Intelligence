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

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from kri.series.models import (
    ReducerAction,
    ReducerActionKind,
    SeriesReviewContext,
)

if TYPE_CHECKING:
    from kri.llm.models import InlineComment


def _comment_ref(comment: InlineComment) -> str:
    """Content-derived stable id for a comment within one reduce() call.

    Positional indexes were used in the first R1 draft but became stale
    the moment any rule reordered the working list. This ref survives
    reordering: it hashes the message body (truncated) plus file, line,
    and category, giving a collision-free handle within one per-patch
    reduce().

    Deterministic (Sec. 40): no time, no rng, no address-of.
    """
    payload = f"{comment.file_path}\x00{comment.line_number}\x00{comment.category}\x00{comment.message}"
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
    return f"{comment.file_path}:{comment.line_number}:{digest}"

Mode = Literal["off", "shadow", "on"]

# Confidence floor at which a "warning" finding must not be suppressed.
# Blockers are ALWAYS floored (regardless of confidence).
_WARNING_CONFIDENCE_FLOOR = 0.7

# Categories that may be merged across mismatched labels (readiness §6.4).
_SOFT_CATEGORIES = frozenset({"convention", "style", "nit"})

# R1 trigger phrases (readiness spec §5.R1). Match is case-insensitive
# and substring-based. The list is intentionally NARROW: every phrase
# must be unambiguously negative — a reviewer who writes it is
# complaining, not praising. Phrases like "no binding document" were
# considered and dropped because they read comfortably as praise
# ("foo has no binding document issues"). Keeping the list narrow is
# the primary defense against false-positive suppression.
_R1_TRIGGER_PHRASES: tuple[str, ...] = (
    "missing binding",
    "undocumented compatible",
    "no dt binding",
    "no yaml binding",
    "not documented in bindings",
    "binding is missing",
)

# R3 trigger phrases (readiness spec §5.R3). Same narrow-phrase
# discipline as R1: every phrase reads only as flagging an external
# dependency, never as praise. The symbol match against
# declared_symbols is again the real disambiguator.
#
# Adversarial-report finding 1: bare phrases like "another patch" or
# "external dependency" fire on negations too ("this does NOT depend
# on the not-yet-merged foo,bar-sndcard patch"). The phrase set below
# is restricted to assertion forms — a reviewer using these phrases
# is committing to "this finding is about an outside dependency",
# which stays true under most negation reshufflings.
_R3_TRIGGER_PHRASES: tuple[str, ...] = (
    "depends on the not-yet-merged",
    "depends on the not yet merged",
    "depends on a not-yet-merged",
    "depends on a not yet merged",
    "waiting on a separate patch",
    "waiting on another patch",
    "requires the not-yet-merged",
    "requires the not yet merged",
    "not-yet-applied series",
    "not yet applied series",
)

# R3 precondition matches the "absence" shape a series-aware LLM
# would emit when it thinks a symbol is undeclared. The prose the
# 6-batch scan surfaced ("... but the symbol X does not appear to be
# defined in this patch or any other patch in the series") is
# structurally what R3 should rewrite when the symbol IS in fact
# declared elsewhere in the series.
_R3_PRECONDITION_HINTS: tuple[str, ...] = (
    "not defined",
    "not declared",
    "referenced but",
    "referenced without",
    "undefined",
    "missing definition",
    "does not appear to be defined",
    "no such symbol",
    "unresolved reference",
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
class ReducerDiagnostics:
    """Per-patch precondition counters emitted alongside every non-``off`` run.

    Every field counts occurrences *before* any rule body decides what to
    do — a rule that fires zero actions can still show a non-zero
    precondition count, which is exactly what shadow validation needs to
    tell "rule silent because input is empty" apart from "rule silent
    because gate is over-tight".

    The counters answer the questions the 6-batch shadow run could not:

    - ``r3_precondition_hits``: findings citing a declared symbol AND an
      absence-shaped word (not defined / referenced but / undefined /
      missing definition). Non-zero here + zero R3 actions == R3's
      trigger vocabulary is aimed at maintainer replies, not the
      LLM's own prose.
    - ``r4_bucket_candidates_pre_floor``: buckets of size ≥ 2 with the
      safety floor NOT applied. Non-zero here + zero
      ``_post_floor`` == the safety floor is the bottleneck (F3 stands).
      Zero here == no bucket volume; R4 has no work to do regardless
      of floor policy.
    - ``r4_bucket_candidates_post_floor``: same buckets *after* the floor
      filter — this is the exact population R4's evaluator sees.

    Deterministic (Sec. 40): counters are computed from input state
    only, no time/rng/address involvement.
    """

    r3_precondition_hits: int = 0
    r4_bucket_candidates_pre_floor: int = 0
    r4_bucket_candidates_post_floor: int = 0

    def to_metadata(self) -> dict[str, int]:
        return {
            "r3_precondition_hits": self.r3_precondition_hits,
            "r4_bucket_candidates_pre_floor": self.r4_bucket_candidates_pre_floor,
            "r4_bucket_candidates_post_floor": self.r4_bucket_candidates_post_floor,
        }


@dataclass(frozen=True)
class ReducerResult:
    """Return value from :meth:`SeriesReducer.reduce`.

    ``comments`` is the possibly-mutated per-patch comment list. For
    ``mode="off"`` and ``mode="shadow"``, it is exactly the same list as
    was passed in. ``actions`` is the audit trail — empty for ``mode="off"``
    since no evaluator runs. ``diagnostics`` records precondition counts
    the rule bodies did NOT act on (see :class:`ReducerDiagnostics`) — it
    is populated on every non-``off`` run and is a plain default when
    ``mode="off"``.
    """

    comments: list[InlineComment]
    actions: list[ReducerAction] = field(default_factory=list)
    diagnostics: ReducerDiagnostics = field(default_factory=lambda: ReducerDiagnostics())


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

        # Diagnostics — computed from the INPUT comment list (pre-rule).
        # These counters answer "does the input contain the precondition
        # class this rule was built for?" independently of whether any
        # rule body fires. See :class:`ReducerDiagnostics` for the
        # questions each counter answers.
        diagnostics = self._compute_diagnostics(comments, series_ctx)

        for rule in self._rules:
            if rule.flag is not None and not flags.get(rule.flag, False):
                continue
            evaluate_fn = getattr(self, f"_evaluate_{rule.kind.name.split('_')[0]}")
            apply_fn = getattr(self, f"_apply_{rule.kind.name.split('_')[0]}")
            rule_actions = evaluate_fn(patch_id, working, series_ctx)
            actions.extend(rule_actions)
            if mode == "on" and rule_actions:
                working = apply_fn(working, rule_actions)

        return ReducerResult(comments=working, actions=actions, diagnostics=diagnostics)

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

    def _compute_diagnostics(
        self,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> ReducerDiagnostics:
        """Count precondition-hits per rule, independent of what any rule
        body decides.

        Runs once per reduce() call before any rule executes; input is
        the raw comment list the reducer received. Counters are cheap
        (single pass + per-symbol substring check) and Sec-40-safe.
        """
        registry = series_ctx.declared_symbols
        declared = list(registry.compatibles.keys()) + list(registry.dt_properties.keys())
        declared_lower = [s.lower() for s in declared if s]

        r3_hits = 0
        for comment in comments:
            haystack_parts = [comment.message or ""]
            if comment.upstream_comment:
                haystack_parts.append(comment.upstream_comment)
            haystack = " ".join(haystack_parts).lower()

            cites_symbol = any(sym in haystack for sym in declared_lower)
            if not cites_symbol:
                continue
            if any(hint in haystack for hint in _R3_PRECONDITION_HINTS):
                r3_hits += 1

        # R4 bucket candidates — pre-floor and post-floor. Both use the
        # same (file, line // 10, category-class) key as _evaluate_R4 to
        # guarantee _post_floor matches exactly what R4 will see.
        pre_buckets: dict[tuple[str, int, str], int] = {}
        post_buckets: dict[tuple[str, int, str], int] = {}
        for comment in comments:
            cat_class = (
                "_soft" if comment.category in _SOFT_CATEGORIES else comment.category
            )
            key = (comment.file_path, comment.line_number // 10, cat_class)
            pre_buckets[key] = pre_buckets.get(key, 0) + 1
            if not self._is_safety_floored(comment):
                post_buckets[key] = post_buckets.get(key, 0) + 1

        pre_ge2 = sum(1 for count in pre_buckets.values() if count >= 2)
        post_ge2 = sum(1 for count in post_buckets.values() if count >= 2)

        return ReducerDiagnostics(
            r3_precondition_hits=r3_hits,
            r4_bucket_candidates_pre_floor=pre_ge2,
            r4_bucket_candidates_post_floor=post_ge2,
        )

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
        for comment in comments:
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
                    finding_ref=_comment_ref(comment),
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
        """Drop every comment whose content-derived ref appears in
        ``actions``. Refs are content-based (see :func:`_comment_ref`)
        so evaluate/apply stay in sync even if a preceding rule reorders
        the comment list — a class of bug the positional-index
        implementation had latent."""
        if not actions:
            return comments
        drop_refs = {a.finding_ref for a in actions}
        return [c for c in comments if _comment_ref(c) not in drop_refs]

    def _evaluate_R3(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> list[ReducerAction]:
        """Trigger R3 for findings that flag an *external* dependency
        which is actually declared by a sibling patch in this series.

        Two-part gate matching R1's discipline:
          1. Trigger phrase in message + upstream_comment (case-insensitive).
          2. Declared-symbol substring hit (longest-symbol-wins).

        Unlike R1, R3 does NOT suppress the finding — it rewrites it to
        say "Depends on patch <sibling>: <original>". This is a *softer*
        action, so the safety floor is not consulted: even a blocker
        gets clearer wording, which never loses information for the
        maintainer.
        """
        registry = series_ctx.declared_symbols
        if not registry.compatibles and not registry.dt_properties:
            return []

        actions: list[ReducerAction] = []
        for comment in comments:
            haystack_parts = [comment.message or ""]
            if comment.upstream_comment:
                haystack_parts.append(comment.upstream_comment)
            haystack = " ".join(haystack_parts).lower()

            if not any(phrase in haystack for phrase in _R3_TRIGGER_PHRASES):
                continue

            matched_symbol: str | None = None
            declaring_patch: str | None = None
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

            # Do not re-tag an already-tagged finding. Idempotence guard
            # against the same reducer being applied twice (e.g. replay
            # tooling) — after the C4 fix, the tag lives in series_prefix.
            already_tagged = bool(comment.series_prefix)
            if already_tagged:
                continue

            actions.append(
                ReducerAction(
                    kind=ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE,
                    patch_id=patch_id,
                    finding_ref=_comment_ref(comment),
                    file=comment.file_path,
                    line=comment.line_number,
                    reason=f"declared_by_{declaring_patch}:{matched_symbol}",
                    related_patch_id=declaring_patch or "",
                )
            )
        return actions

    def _apply_R3(
        self,
        comments: list[InlineComment],
        actions: list[ReducerAction],
    ) -> list[InlineComment]:
        """Tag matched comments with ``series_prefix`` naming the declaring
        sibling patch. Returns a NEW list of comments (via ``model_copy``)
        so the input list is not mutated in place — matters if a caller
        retained a reference to it. ``message`` is left intact (C4 fix:
        writing to ``series_prefix`` avoids contaminating the content-hash
        used by downstream rules)."""
        if not actions:
            return comments
        rewrites: dict[str, str] = {a.finding_ref: a.related_patch_id for a in actions}
        out: list[InlineComment] = []
        for c in comments:
            ref = _comment_ref(c)
            sibling = rewrites.get(ref)
            if sibling:
                out.append(c.model_copy(update={"series_prefix": f"Depends on patch {sibling}: "}))
            else:
                out.append(c)
        return out

    def _evaluate_R4(
        self,
        patch_id: str,
        comments: list[InlineComment],
        series_ctx: SeriesReviewContext,
    ) -> list[ReducerAction]:
        """Cluster findings by ``(file, line // 10, category-class)`` and
        emit one merge action per cluster of size ≥ 2.

        Design points:
          - Safety-floored findings (blockers, high-conf warnings) are
            EXCLUDED from destructive bucketing — they never absorb siblings
            and never get absorbed. Merging them would either delete a
            blocker (forbidden) or hide siblings behind one (info loss).
          - When ALL members of a bucket are floored (post_floor=0 but
            pre_floor>0), R4 emits an R4_LINE_BUCKET_ANNOTATE action instead
            of R4_LINE_BUCKET_MERGE. The floored findings are tagged with
            series_prefix (no suppression) so the maintainer sees the
            cluster relationship without losing any finding.
          - "Category-class" collapses ``{convention, style, nit}`` to a
            single bucket via ``_same_category`` — spec §6.4 permits
            cross-soft merging.
          - Keeper = max confidence within bucket. Ties broken by input
            order (stable sort).
          - Bucketing preserves input order so audit output is
            deterministic across replays.
        """
        buckets: dict[tuple[str, int, str], list[InlineComment]] = {}
        floored_buckets: dict[tuple[str, int, str], list[InlineComment]] = {}
        for comment in comments:
            cat_class = "_soft" if comment.category in _SOFT_CATEGORIES else comment.category
            key = (comment.file_path, comment.line_number // 10, cat_class)
            if self._is_safety_floored(comment):
                floored_buckets.setdefault(key, []).append(comment)
            else:
                buckets.setdefault(key, []).append(comment)

        actions: list[ReducerAction] = []
        for cluster in buckets.values():
            if len(cluster) < 2:
                continue
            # Stable sort: max confidence first, ties preserve input order.
            ordered = sorted(cluster, key=lambda c: -c.confidence)
            keeper = ordered[0]
            absorbed = ordered[1:]
            actions.append(
                ReducerAction(
                    kind=ReducerActionKind.R4_LINE_BUCKET_MERGE,
                    patch_id=patch_id,
                    finding_ref=_comment_ref(keeper),
                    file=keeper.file_path,
                    line=keeper.line_number,
                    reason=f"cluster_size={len(cluster)}",
                    absorbed_refs=tuple(_comment_ref(a) for a in absorbed),
                    related_patch_id="",
                )
            )

        # Soft annotation: floored buckets where no non-floored candidate
        # exists. Emit one annotate action per floored cluster of size ≥ 2.
        for key, cluster in floored_buckets.items():
            if len(cluster) < 2:
                continue
            if key in buckets:
                continue  # mixed bucket; merge side already handles it
            ordered = sorted(cluster, key=lambda c: -c.confidence)
            actions.append(
                ReducerAction(
                    kind=ReducerActionKind.R4_LINE_BUCKET_ANNOTATE,
                    patch_id=patch_id,
                    finding_ref=_comment_ref(ordered[0]),
                    file=ordered[0].file_path,
                    line=ordered[0].line_number,
                    reason=f"floored_cluster_size={len(cluster)}",
                    absorbed_refs=tuple(_comment_ref(c) for c in ordered),
                    related_patch_id="",
                )
            )
        return actions

    def _apply_R4(
        self,
        comments: list[InlineComment],
        actions: list[ReducerAction],
    ) -> list[InlineComment]:
        """Apply R4 merge and annotate actions. New list returned; inputs not mutated.

        MERGE: absorbed siblings are dropped; their snippet is appended to the
        keeper's message as "Related remark: …".

        ANNOTATE: all cluster members remain; each gets series_prefix set to
        "[floored-cluster] " (no suppression). An existing series_prefix (e.g.
        from R3) is preserved — no double-tagging.
        """
        if not actions:
            return comments

        merge_actions = [
            a for a in actions if a.kind == ReducerActionKind.R4_LINE_BUCKET_MERGE
        ]
        annotate_actions = [
            a for a in actions if a.kind == ReducerActionKind.R4_LINE_BUCKET_ANNOTATE
        ]

        keeper_updates: dict[str, list[str]] = {}
        drop_refs: set[str] = set()
        for a in merge_actions:
            drop_refs.update(a.absorbed_refs)
            # setdefault (NOT bare assign) so two actions targeting the
            # same keeper accumulate their snippets instead of clobbering.
            keeper_updates.setdefault(a.finding_ref, [])

        # Build ref → comment map once for absorbed-snippet lookup.
        ref_to_cmt = {_comment_ref(c): c for c in comments}

        for a in merge_actions:
            for absorbed_ref in a.absorbed_refs:
                absorbed = ref_to_cmt.get(absorbed_ref)
                if absorbed is None:
                    continue
                snippet = absorbed.upstream_comment or absorbed.message or ""
                if snippet:
                    keeper_updates[a.finding_ref].append(snippet)

        annotate_refs: set[str] = set()
        for a in annotate_actions:
            annotate_refs.update(a.absorbed_refs)

        out: list[InlineComment] = []
        for c in comments:
            ref = _comment_ref(c)
            if ref in drop_refs:
                continue
            snippets = keeper_updates.get(ref)
            if snippets:
                tail = "".join(f"\nRelated remark: {s}" for s in snippets)
                out.append(c.model_copy(update={"message": (c.message or "") + tail}))
            elif ref in annotate_refs and not c.series_prefix:
                out.append(c.model_copy(update={"series_prefix": "[floored-cluster] "}))
            else:
                out.append(c)
        return out

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
