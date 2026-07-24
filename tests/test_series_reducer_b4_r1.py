"""WP-S1B Step B4 — R1 declared-symbol suppression.

R1 (readiness spec §5.R1): when a review finding complains about a
missing binding for a compatible / DT-property that a sibling patch in
the same series actually declares, suppress the finding.

- ``mode='off'``: reducer short-circuits, no R1 fires.  Covered by
  T-B1-IDENTITY / T-B1-OFF-MODE already.
- ``mode='shadow'``: evaluator runs and emits a ReducerAction, but the
  comment list is UNCHANGED.
- ``mode='on'``: evaluator runs AND the matched comment is removed.

Safety floor from readiness §6.4 is enforced in the evaluator: blockers
and warning-with-confidence≥0.7 findings never generate an R1 action,
so mode='on' cannot delete them.

These tests exercise ``SeriesReducer.reduce`` directly (unit level).
Web/engine integration is covered by earlier B1–B3 tests; wiring is
unchanged in B4.
"""

from __future__ import annotations

from kri.common.models import Severity
from kri.llm.models import InlineComment
from kri.series import (
    ReducerActionKind,
    SeriesReducer,
    SeriesReviewContext,
    SymbolRegistry,
)
from kri.series.models import PatchIndexEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ctx(
    compatibles: dict[str, str] | None = None,
    dt_properties: dict[str, str] | None = None,
    total_patches: int = 2,
) -> SeriesReviewContext:
    """Minimal multi-patch SeriesReviewContext with a chosen symbol registry.
    The patch_index has to be non-empty for is_multi_patch(), and total
    must be ≥2 or the reducer short-circuits before any rule runs.
    """
    entries = {
        f"p{i}": PatchIndexEntry(
            patch_id=f"p{i}",
            index=i,
            total=total_patches,
            subject=f"[PATCH {i}/{total_patches}] p{i}",
            files_changed=("drivers/x/foo.c",),
        )
        for i in range(1, total_patches + 1)
    }
    registry = SymbolRegistry(
        compatibles=compatibles or {},
        dt_properties=dt_properties or {},
        c_symbols={},
        files_added={},
    )
    return SeriesReviewContext(
        series_id="s1",
        title="R1 test series",
        cover_letter="cl",
        total_patches=total_patches,
        patch_index=entries,
        declared_symbols=registry,
        file_touch_map={},
    )


def _cmt(
    *,
    message: str,
    upstream_comment: str | None = None,
    file_path: str = "drivers/x/foo.c",
    line_number: int = 42,
    severity: Severity = Severity.INFO,
    confidence: float = 0.5,
    category: str = "convention",
) -> InlineComment:
    return InlineComment(
        file_path=file_path,
        line_number=line_number,
        message=message,
        upstream_comment=upstream_comment,
        severity=severity,
        confidence=confidence,
        category=category,
    )


# ---------------------------------------------------------------------------
# R1 core matching behaviour
# ---------------------------------------------------------------------------


def test_R1_shadow_matches_declared_compatible_and_records_action_without_mutation():
    """Shadow mode with a matching finding must emit ONE R1 action and
    leave the comment list untouched."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar-sndcard": "p2"})
    cmt = _cmt(
        message="missing binding for foo,bar-sndcard",
        category="documentation",
    )

    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="shadow",
    )

    assert len(result.actions) == 1
    assert result.actions[0].kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS
    assert result.actions[0].related_patch_id == "p2"
    assert "foo,bar-sndcard" in result.actions[0].reason
    # Shadow does NOT mutate.
    assert result.comments == [cmt]


def test_R1_on_mode_suppresses_matched_comment():
    """mode='on': the matched finding disappears from the returned list."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar-sndcard": "p2"})
    matched = _cmt(message="missing binding for foo,bar-sndcard")
    survivor = _cmt(
        message="unrelated style issue", line_number=99, category="style",
    )

    result = reducer.reduce(
        patch_id="p1",
        comments=[matched, survivor],
        series_ctx=ctx,
        mode="on",
    )

    assert len(result.actions) == 1
    assert result.comments == [survivor], (
        "R1 apply must drop only the matched comment, leave survivors intact"
    )


def test_R1_dt_property_match_is_symmetric_to_compatible():
    """R1 fires equally on a declared DT property (same rule, alternate
    registry slot per spec §5.R1)."""
    reducer = SeriesReducer()
    ctx = _ctx(dt_properties={"qcom,foo-clk": "p2"})
    cmt = _cmt(message="no yaml binding for qcom,foo-clk")

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )
    assert result.comments == []
    assert len(result.actions) == 1
    assert result.actions[0].related_patch_id == "p2"


def test_R1_upstream_comment_field_is_also_scanned():
    """The trigger phrase / symbol may appear in ``upstream_comment``
    rather than ``message`` — R1 must scan both."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(
        message="see below",
        upstream_comment="undocumented compatible foo,bar",
    )
    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="shadow"
    )
    assert len(result.actions) == 1


# ---------------------------------------------------------------------------
# Negative / non-match cases
# ---------------------------------------------------------------------------


def test_R1_no_trigger_phrase_no_action():
    """Finding cites a declared symbol but lacks any trigger phrase —
    R1 must NOT fire (avoid suppressing unrelated commentary)."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(message="foo,bar is a strange name for a soundcard")

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )
    assert result.actions == []
    assert result.comments == [cmt]


def test_R1_trigger_phrase_but_symbol_not_declared_no_action():
    """Finding has a trigger phrase but the cited symbol is not declared
    anywhere in the series — R1 must NOT fire (the reviewer is right)."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(message="missing binding for baz,quux")

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )
    assert result.actions == []
    assert result.comments == [cmt]


def test_R1_single_patch_series_is_no_op():
    """A single-patch series has no sibling to declare anything. The
    reducer short-circuits before R1 even runs (see B1 skeleton)."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p1"}, total_patches=1)
    cmt = _cmt(message="undocumented compatible foo,bar")

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )
    assert result.actions == []
    assert result.comments == [cmt]


def test_R1_off_mode_is_no_op_even_on_perfect_match():
    """Mode='off' short-circuits BEFORE any rule.  Byte-identity holds."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(message="missing binding for foo,bar")

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="off"
    )
    assert result.actions == []
    assert result.comments == [cmt]


# ---------------------------------------------------------------------------
# Safety floor (readiness §6.4)
# ---------------------------------------------------------------------------


def test_R1_blocker_finding_never_suppressed():
    """A blocker matching every R1 trigger + symbol must survive under
    mode='on' — the safety floor forbids suppression regardless of rule
    match. NO action is emitted (audit reflects reality)."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(
        message="missing binding for foo,bar",
        severity=Severity.BLOCKER,
        confidence=0.9,
    )

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )
    assert result.comments == [cmt]
    assert result.actions == []


def test_R1_high_confidence_warning_never_suppressed():
    """Warning with confidence ≥ 0.7 hits the safety floor."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(
        message="missing binding for foo,bar",
        severity=Severity.WARNING,
        confidence=0.85,
    )
    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )
    assert result.comments == [cmt]
    assert result.actions == []


def test_R1_low_confidence_warning_still_suppressible():
    """Warning below the floor is NOT protected — R1 may suppress it."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(
        message="missing binding for foo,bar",
        severity=Severity.WARNING,
        confidence=0.4,
    )
    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )
    assert result.comments == []
    assert len(result.actions) == 1


# ---------------------------------------------------------------------------
# Case sensitivity / longest-symbol wins
# ---------------------------------------------------------------------------


def test_R1_case_insensitive_trigger_and_symbol():
    """Trigger phrases and symbol matches are both case-insensitive."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,BarSndCard": "p2"})
    cmt = _cmt(message="MISSING BINDING for FOO,barsndcard")

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="shadow"
    )
    assert len(result.actions) == 1


def test_R1_longest_matching_symbol_wins_in_reason():
    """When two declared symbols overlap (e.g. 'foo,bar' vs
    'foo,bar-sndcard'), the longer / more-specific match must be recorded
    in the action's ``reason`` — a shorter prefix would misattribute
    which patch owns the binding."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2", "foo,bar-sndcard": "p3"})
    cmt = _cmt(message="missing binding for foo,bar-sndcard")

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="shadow"
    )
    assert len(result.actions) == 1
    assert "foo,bar-sndcard" in result.actions[0].reason
    assert result.actions[0].related_patch_id == "p3", (
        "longest-symbol-wins broken: shorter prefix stole attribution"
    )


# ---------------------------------------------------------------------------
# Multi-comment cases
# ---------------------------------------------------------------------------


def test_R1_only_matching_findings_are_dropped_from_multi_list():
    """A list mixing matched, non-triggered, and safety-floored comments
    must produce actions only for the true match and drop only that one."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    matched = _cmt(message="missing binding for foo,bar")
    non_triggered = _cmt(
        message="foo,bar looks unusual but no rule violation",
        line_number=50,
    )
    blocker_floored = _cmt(
        message="missing binding for foo,bar in header",
        line_number=60,
        severity=Severity.BLOCKER,
        confidence=0.9,
    )

    result = reducer.reduce(
        patch_id="p1",
        comments=[matched, non_triggered, blocker_floored],
        series_ctx=ctx,
        mode="on",
    )

    assert result.comments == [non_triggered, blocker_floored]
    assert len(result.actions) == 1
    assert result.actions[0].line == 42


def test_R1_symbol_declared_by_same_patch_still_suppresses():
    """R1 does NOT carve out symbols declared by the patch being
    reviewed — the whole series contributes to the "binding is coming"
    signal, even if that binding is in the same patch."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p1"})  # p1 itself declares it
    cmt = _cmt(message="missing binding for foo,bar")

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )
    assert result.comments == []
    assert len(result.actions) == 1
    assert result.actions[0].related_patch_id == "p1"


# ---------------------------------------------------------------------------
# False-positive guard (adversarial finding 1)
# ---------------------------------------------------------------------------


def test_R1_positive_binding_comment_not_suppressed():
    """Adversarial-report finding 1: a phrase like "no binding document
    issues" reads as praise, not a complaint. Earlier drafts included
    "no binding document" and "no corresponding yaml binding" as
    triggers; both were dropped because they fire on positive comments
    that also mention a declared symbol.  This test locks in that the
    narrow phrase list refuses to suppress praise text — even when the
    declared symbol IS cited.

    Note: lines and category are chosen so R4 (post-B5) does NOT bucket
    these two — different line buckets (10 // 10 = 1, 45 // 10 = 4) and
    a hard category ("documentation") so R4's soft-class merge cannot
    conflate them either.  This test is scoped to R1's false-positive
    guard, not R4."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar-sndcard": "p2"})
    # Reads as a positive comment: no rule violation is asserted.
    praise_a = _cmt(
        message="foo,bar-sndcard has no binding document issues — clean YAML.",
        line_number=10,
        category="documentation",
    )
    praise_b = _cmt(
        message="foo,bar-sndcard: no corresponding YAML binding gap detected.",
        line_number=45,
        category="documentation",
    )

    result = reducer.reduce(
        patch_id="p1",
        comments=[praise_a, praise_b],
        series_ctx=ctx,
        mode="on",
    )
    r1_actions = [
        a for a in result.actions
        if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS
    ]
    assert r1_actions == [], (
        "R1 fired on a positive comment — phrase list is too broad. "
        "Drop the ambiguous phrase; the symbol match alone is not a "
        "sufficient trigger."
    )
    assert result.comments == [praise_a, praise_b]


# ---------------------------------------------------------------------------
# R1 scope boundary: only compatibles + dt_properties are checked
# ---------------------------------------------------------------------------


def _ctx_with_files_added(symbol: str, declaring_patch: str) -> SeriesReviewContext:
    """Builds a context where symbol is ONLY in files_added, not in
    compatibles or dt_properties.  R1 must NOT fire on such a symbol."""
    entries = {
        f"p{i}": PatchIndexEntry(
            patch_id=f"p{i}",
            index=i,
            total=2,
            subject=f"[PATCH {i}/2] p{i}",
            files_changed=("drivers/x/foo.c",),
        )
        for i in range(1, 3)
    }
    registry = SymbolRegistry(
        compatibles={},
        dt_properties={},
        c_symbols={},
        files_added={symbol: declaring_patch},
    )
    return SeriesReviewContext(
        series_id="s1",
        title="R1 scope boundary test",
        cover_letter="cl",
        total_patches=2,
        patch_index=entries,
        declared_symbols=registry,
        file_touch_map={},
    )


def _ctx_with_c_symbols(symbol: str, declaring_patch: str) -> SeriesReviewContext:
    """Builds a context where symbol is ONLY in c_symbols."""
    entries = {
        f"p{i}": PatchIndexEntry(
            patch_id=f"p{i}",
            index=i,
            total=2,
            subject=f"[PATCH {i}/2] p{i}",
            files_changed=("drivers/x/foo.c",),
        )
        for i in range(1, 3)
    }
    registry = SymbolRegistry(
        compatibles={},
        dt_properties={},
        c_symbols={symbol: declaring_patch},
        files_added={},
    )
    return SeriesReviewContext(
        series_id="s1",
        title="R1 scope boundary test",
        cover_letter="cl",
        total_patches=2,
        patch_index=entries,
        declared_symbols=registry,
        file_touch_map={},
    )


def test_R1_does_not_fire_on_files_added_only_symbol():
    """R1 only consults registry.compatibles and registry.dt_properties.
    A symbol that is only in registry.files_added must NOT trigger R1
    suppression.

    This is a scope boundary test — it documents the current implementation
    boundary.  If R1 is ever expanded to cover files_added, this test must
    be updated explicitly rather than silently breaking.

    Rationale for the boundary: 'files_added' tracks which source files a
    patch introduces, not DT binding declarations.  Suppressing a 'missing
    binding' finding because a *file* was added by a sibling patch would be
    a semantic overreach — the binding might still be absent even if the file
    exists.
    """
    reducer = SeriesReducer()
    ctx = _ctx_with_files_added("sound/soc/qcom/sc8280xp.c", "p2")

    cmt = _cmt(
        message="missing binding for sound/soc/qcom/sc8280xp.c — no YAML schema found",
        category="documentation",
        confidence=0.60,
    )

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )

    r1_actions = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    assert len(r1_actions) == 0, (
        f"R1 must NOT fire when the symbol is only in files_added (not compatibles/dt_properties). "
        f"Got {len(r1_actions)} R1 actions.  If this assertion fails after an R1 expansion, "
        f"update this test with explicit coverage for the new behavior."
    )
    assert len(result.comments) == 1, "Comment must survive when R1 does not fire"


def test_R1_does_not_fire_on_c_symbols_only_symbol():
    """R1 only consults registry.compatibles and registry.dt_properties.
    A symbol that is only in registry.c_symbols must NOT trigger R1.

    Rationale: c_symbols are C-language symbols (function names, struct names,
    #define identifiers).  R1 is designed for DT binding declarations.
    Expanding R1 to cover c_symbols would conflate DT-binding suppression
    with C API availability — a different semantic question that would need
    its own rule.
    """
    reducer = SeriesReducer()
    ctx = _ctx_with_c_symbols("snd_soc_register_card", "p2")

    cmt = _cmt(
        message="missing binding for snd_soc_register_card — no YAML schema found",
        category="documentation",
        confidence=0.60,
    )

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )

    r1_actions = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    assert len(r1_actions) == 0, (
        f"R1 must NOT fire when the symbol is only in c_symbols. "
        f"Got {len(r1_actions)} R1 actions."
    )
    assert len(result.comments) == 1


def test_R1_fires_when_same_symbol_in_both_compatibles_and_files_added():
    """If a symbol appears in BOTH compatibles AND files_added, R1 fires
    because compatibles is checked.  This confirms the rule fires correctly
    even when the symbol is duplicated across registry fields.
    """
    reducer = SeriesReducer()
    entries = {
        f"p{i}": PatchIndexEntry(
            patch_id=f"p{i}",
            index=i,
            total=2,
            subject=f"[PATCH {i}/2] p{i}",
            files_changed=("drivers/x/foo.c",),
        )
        for i in range(1, 3)
    }
    registry = SymbolRegistry(
        compatibles={"foo,bar-sndcard": "p2"},
        dt_properties={},
        c_symbols={},
        files_added={"foo,bar-sndcard": "p2"},
    )
    ctx = SeriesReviewContext(
        series_id="s1", title="R1 scope boundary", cover_letter="cl",
        total_patches=2, patch_index=entries, declared_symbols=registry,
        file_touch_map={},
    )

    cmt = _cmt(
        message="missing binding for foo,bar-sndcard",
        category="documentation",
        confidence=0.60,
    )

    result = reducer.reduce(
        patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on"
    )

    r1_actions = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    assert len(r1_actions) == 1, (
        f"R1 must fire when the symbol is in compatibles (even if also in files_added). "
        f"Got {len(r1_actions)} R1 actions."
    )
    assert len(result.comments) == 0


# ---------------------------------------------------------------------------
# R1 word-boundary guard (readiness spec §7.B5)
# ---------------------------------------------------------------------------


def test_R1_word_boundary_fires_on_exact_token_match():
    """R1 must fire when the declared symbol appears as a whole token."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(
        message="foo,bar binding is missing from this patch",
        category="documentation",
        confidence=0.6,
    )
    result = reducer.reduce(patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on")

    r1 = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    assert len(r1) == 1, "R1 must fire on exact word-boundary symbol match"


def test_R1_word_boundary_does_not_fire_on_partial_token():
    """R1 must NOT fire when the declared symbol is only a substring of
    a longer token.  'foo,bar' declared; comment contains 'foo,bar-extended'
    — the declared symbol is a prefix of a different compatible and must
    not suppress a finding about foo,bar-extended."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    # 'foo,bar' appears only as a prefix inside 'foo,bar-extended', never as
    # a standalone token.  R1 must NOT fire.
    cmt = _cmt(
        message="foo,bar-extended binding is missing from this series",
        category="documentation",
        confidence=0.6,
    )
    result = reducer.reduce(patch_id="p1", comments=[cmt], series_ctx=ctx, mode="on")

    r1 = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    assert len(r1) == 0, (
        "R1 must not fire when declared symbol is only a substring of a longer token; "
        f"got {len(r1)} action(s)"
    )
