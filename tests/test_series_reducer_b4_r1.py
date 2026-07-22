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
        message="no corresponding YAML binding for foo,bar-sndcard",
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
    cmt = _cmt(message="no binding document for qcom,foo-clk")

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
        upstream_comment="no corresponding YAML binding for foo,bar",
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
    cmt = _cmt(message="no corresponding YAML binding for baz,quux")

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
    cmt = _cmt(message="no corresponding YAML binding for foo,bar")

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
    cmt = _cmt(message="NO CORRESPONDING YAML BINDING for FOO,barsndcard")

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
    cmt = _cmt(message="no corresponding YAML binding for foo,bar-sndcard")

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
