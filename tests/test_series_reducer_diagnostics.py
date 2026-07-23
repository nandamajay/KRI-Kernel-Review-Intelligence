"""WP-S1B — reducer diagnostics (Counter-finding-D instrumentation).

These tests exercise :class:`ReducerDiagnostics` and the ``reduce()``-time
counters that answer the two questions the 6-batch shadow run left
unanswered:

1. Does the input to a rule contain the precondition class the rule was
   built for? (r1/r3 precondition hits)
2. Would R4's bucketing find candidate clusters if the safety floor
   didn't filter them out first? (r4 pre/post floor bucket counts)

The counters are diagnostic-only — they DO NOT change what any rule
body decides. Rule-body behavior tests live in ``test_series_reducer_b*``.

Counters must be:
  - populated on every ``mode != "off"`` run
  - a default (all-zeros) instance on ``mode="off"``
  - computed from the pre-rule input, so shadow and on modes see the
    same counters for the same input
"""

from __future__ import annotations

from kri.common.models import Severity
from kri.llm.models import InlineComment
from kri.series import (
    ReducerDiagnostics,
    SeriesReducer,
    SeriesReviewContext,
    SymbolRegistry,
)
from kri.series.models import PatchIndexEntry


# ---------------------------------------------------------------------------
# Fixtures — same shape as B4/B5 tests to keep the mental model consistent.
# ---------------------------------------------------------------------------


def _ctx(
    compatibles: dict[str, str] | None = None,
    dt_properties: dict[str, str] | None = None,
    total_patches: int = 2,
) -> SeriesReviewContext:
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
        title="diagnostics test",
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
# Baseline: off-mode produces the default (all-zeros) diagnostics.
# ---------------------------------------------------------------------------


def test_diagnostics_default_in_off_mode():
    """mode='off' short-circuits before diagnostics compute. Default
    instance holds. This preserves byte-identity with pre-WP-S1B."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(message="missing binding for foo,bar", category="documentation")

    result = reducer.reduce(patch_id="p1", comments=[cmt], series_ctx=ctx, mode="off")

    assert result.diagnostics == ReducerDiagnostics()
    assert result.diagnostics.r3_precondition_hits == 0
    assert result.diagnostics.r4_bucket_candidates_pre_floor == 0
    assert result.diagnostics.r4_bucket_candidates_post_floor == 0


def test_diagnostics_default_in_single_patch_series():
    """Single-patch series short-circuits before diagnostics. Reflects
    reducer contract: no series-signal, no series-diagnostics."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p1"}, total_patches=1)
    cmt = _cmt(message="missing binding for foo,bar")

    result = reducer.reduce(patch_id="p1", comments=[cmt], series_ctx=ctx, mode="shadow")

    assert result.diagnostics == ReducerDiagnostics()


# ---------------------------------------------------------------------------
# R3 precondition counter — absence-shaped prose the LLM does emit.
# ---------------------------------------------------------------------------


def test_diagnostics_r3_hits_on_absence_shape_prose():
    """A finding shaped like the real LLM output from S3:
    "<symbol> ... do not appear to be defined in this patch or any
    other patch in the series". Cites a declared symbol AND uses an
    R3 precondition hint. R3 body does NOT fire (its phrase list
    targets reviewer replies, not LLM prose) — precondition-hit > 0
    while r3_actions == 0."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"LPASS_CLK_ID_MCLK_1": "p2"})
    cmt = _cmt(
        message="LPASS_CLK_ID_MCLK_1 is referenced in Q6PRM_CLK() but does "
                "not appear to be defined in this patch or any other patch "
                "in the series",
        category="design",
    )

    result = reducer.reduce(patch_id="p1", comments=[cmt], series_ctx=ctx, mode="shadow")

    assert result.diagnostics.r3_precondition_hits == 1
    from kri.series import ReducerActionKind
    r3_actions = [a for a in result.actions if a.kind == ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE]
    assert r3_actions == []


def test_diagnostics_r3_no_hit_when_symbol_absent():
    """R3 precondition requires a declared symbol substring hit."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar": "p2"})
    cmt = _cmt(message="baz,quux is not defined anywhere", category="design")

    result = reducer.reduce(patch_id="p1", comments=[cmt], series_ctx=ctx, mode="shadow")

    assert result.diagnostics.r3_precondition_hits == 0


# ---------------------------------------------------------------------------
# R4 bucket counters — the pre-floor / post-floor delta is the F3 signal.
# ---------------------------------------------------------------------------


def test_diagnostics_r4_pre_floor_counts_all_bucket_candidates():
    """Two findings on the same line // 10 with matching category-class
    → 1 pre-floor bucket, regardless of severity/confidence."""
    reducer = SeriesReducer()
    ctx = _ctx()
    a = _cmt(message="a", line_number=42, confidence=0.9,
             severity=Severity.WARNING, category="design")
    b = _cmt(message="b", line_number=44, confidence=0.85,
             severity=Severity.WARNING, category="design")

    result = reducer.reduce(patch_id="p1", comments=[a, b], series_ctx=ctx, mode="shadow")

    assert result.diagnostics.r4_bucket_candidates_pre_floor == 1


def test_diagnostics_r4_post_floor_drops_floored_bucket_members():
    """Two warning@0.85 findings on the same bucket — pre-floor 1
    candidate, post-floor 0 because both members are safety-floored.
    This is the F3 signal from the counter-report: R4 has zero
    post-floor candidates because real LLM output warnings all live
    above 0.7 confidence."""
    reducer = SeriesReducer()
    ctx = _ctx()
    a = _cmt(message="a", line_number=42, confidence=0.85,
             severity=Severity.WARNING, category="design")
    b = _cmt(message="b", line_number=44, confidence=0.9,
             severity=Severity.WARNING, category="design")

    result = reducer.reduce(patch_id="p1", comments=[a, b], series_ctx=ctx, mode="shadow")

    assert result.diagnostics.r4_bucket_candidates_pre_floor == 1
    assert result.diagnostics.r4_bucket_candidates_post_floor == 0


def test_diagnostics_r4_post_floor_matches_r4_evaluator_output():
    """When post_floor >= 1 AND no floor-triggered downgrade, R4 must
    emit at least one action. Verifies the diagnostic and the rule
    body agree on the same bucket-set."""
    reducer = SeriesReducer()
    ctx = _ctx()
    a = _cmt(message="a", line_number=42, confidence=0.5,
             severity=Severity.INFO, category="convention")
    b = _cmt(message="b", line_number=44, confidence=0.6,
             severity=Severity.INFO, category="style")

    result = reducer.reduce(patch_id="p1", comments=[a, b], series_ctx=ctx, mode="shadow")

    assert result.diagnostics.r4_bucket_candidates_pre_floor == 1
    assert result.diagnostics.r4_bucket_candidates_post_floor == 1
    from kri.series import ReducerActionKind
    r4_actions = [a for a in result.actions if a.kind == ReducerActionKind.R4_LINE_BUCKET_MERGE]
    assert len(r4_actions) == 1


def test_diagnostics_r4_soft_category_bucket_collapses_to_one():
    """Convention + style + nit collapse to a single '_soft' key. Three
    findings in the same line // 10 with mixed soft categories → 1
    bucket, size 3."""
    reducer = SeriesReducer()
    ctx = _ctx()
    conv = _cmt(message="c", line_number=41, category="convention", confidence=0.5)
    style = _cmt(message="s", line_number=42, category="style", confidence=0.5)
    nit = _cmt(message="n", line_number=43, category="nit", confidence=0.5)

    result = reducer.reduce(patch_id="p1", comments=[conv, style, nit],
                            series_ctx=ctx, mode="shadow")

    assert result.diagnostics.r4_bucket_candidates_pre_floor == 1
    assert result.diagnostics.r4_bucket_candidates_post_floor == 1


def test_diagnostics_r4_singleton_bucket_not_counted():
    """A bucket of size 1 is not a candidate for R4. This mirrors the
    len(cluster) >= 2 check in _evaluate_R4."""
    reducer = SeriesReducer()
    ctx = _ctx()
    solo = _cmt(message="solo", line_number=42, confidence=0.5)
    other = _cmt(message="elsewhere", line_number=99,
                 file_path="drivers/y/bar.c", confidence=0.5)

    result = reducer.reduce(patch_id="p1", comments=[solo, other],
                            series_ctx=ctx, mode="shadow")

    assert result.diagnostics.r4_bucket_candidates_pre_floor == 0
    assert result.diagnostics.r4_bucket_candidates_post_floor == 0


# ---------------------------------------------------------------------------
# Cross-mode: diagnostics are input-derived, so mode='shadow' and
# mode='on' see identical counters for identical inputs.
# ---------------------------------------------------------------------------


def test_diagnostics_shadow_and_on_produce_identical_counters():
    """Diagnostics are computed from the pre-rule input list only.
    Neither shadow-nor-on's downstream mutation loops can perturb them
    — verifies the counters are honest across replay."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar-sndcard": "p2"})
    cmts = [
        _cmt(message="foo,bar-sndcard binding schema issue", category="dt_binding",
             line_number=10),
        _cmt(message="foo,bar-sndcard not defined here", category="design",
             line_number=20),
        _cmt(message="c1", line_number=42, confidence=0.5, category="convention"),
        _cmt(message="c2", line_number=44, confidence=0.5, category="style"),
    ]

    shadow = reducer.reduce(patch_id="p1", comments=cmts, series_ctx=ctx, mode="shadow")
    on = reducer.reduce(patch_id="p1", comments=cmts, series_ctx=ctx, mode="on")

    assert shadow.diagnostics == on.diagnostics
    # Sanity: at least one of the counters should be non-zero on this
    # fixture — otherwise the test isn't actually exercising the code.
    assert (
        shadow.diagnostics.r3_precondition_hits
        + shadow.diagnostics.r4_bucket_candidates_pre_floor
    ) >= 1


def test_diagnostics_reflect_pre_mutation_input_under_mode_on():
    """Under mode='on', _apply_R3 sets series_prefix on matched comments.
    Diagnostics MUST count the pre-mutation input — if a future refactor
    accidentally reads the post-mutation list, this test catches it because
    the r3_precondition_hits counter uses the original message content
    (before series_prefix is set).

    Fixture: one R3-triggering finding that will be TAGGED under mode='on'.
    r3_precondition_hits must be 1 regardless of mode, since diagnostics
    run against the original input list."""
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"foo,bar-sndcard": "p2"})
    r3_target = _cmt(
        message="waiting on another patch to add foo,bar-sndcard — it is not defined yet",
        category="design",
        line_number=10,
        confidence=0.5,
    )

    on = reducer.reduce(
        patch_id="p1", comments=[r3_target], series_ctx=ctx, mode="on"
    )

    from kri.series import ReducerActionKind
    r3_actions = [
        a for a in on.actions
        if a.kind == ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE
    ]
    assert len(r3_actions) == 1, "fixture must trigger R3 for this test to be meaningful"

    # Diagnostics count the pre-mutation input → r3_precondition_hits == 1.
    assert on.diagnostics.r3_precondition_hits == 1


# ---------------------------------------------------------------------------
# to_metadata() shape stability — the engine wires this dict into
# report.metadata for shadow logging, so field names must be stable.
# ---------------------------------------------------------------------------


def test_diagnostics_to_metadata_field_names_are_stable():
    """to_metadata() field names are load-bearing — shadow-log
    aggregation tooling will read these keys. Locking them in prevents
    silent rename breakage."""
    diag = ReducerDiagnostics(
        r3_precondition_hits=2,
        r4_bucket_candidates_pre_floor=3,
        r4_bucket_candidates_post_floor=4,
    )
    payload = diag.to_metadata()
    assert set(payload.keys()) == {
        "r3_precondition_hits",
        "r4_bucket_candidates_pre_floor",
        "r4_bucket_candidates_post_floor",
    }
    assert payload["r3_precondition_hits"] == 2
    assert payload["r4_bucket_candidates_pre_floor"] == 3
    assert payload["r4_bucket_candidates_post_floor"] == 4


# ---------------------------------------------------------------------------
# Diagnostics corpus tests — real RubikPi3 v2 prose shapes
#
# These tests use the same prose from test_series_reducer_corpus.py but
# target the diagnostic counters specifically.  The key question answered:
# does the trigger-phrase prose (R3 action) vs absence-shaped prose
# (r3_precondition_hits) count differently in diagnostics?
# ---------------------------------------------------------------------------


def test_diagnostics_r3_trigger_phrase_fires_action_but_not_precondition_hit():
    """R3's trigger-phrase vocabulary (for detecting maintainer-reply style
    prose) is SEPARATE from the r3_precondition_hints vocabulary (for
    detecting LLM absence-shaped prose).

    The RubikPi3 corpus prose "depends on the not-yet-merged binding update
    for thundercomm,qcs6490-rubikpi3-sndcard" contains an R3 trigger phrase
    ("depends on the not-yet-merged") that fires an R3 action.  It does NOT
    contain an r3_precondition_hint word ("not defined", "not declared", etc.)
    so r3_precondition_hits must remain 0.

    This documents that the precondition counter is aimed at LLM prose shapes
    the trigger vocabulary misses — the two detection vocabularies are
    complementary, not redundant.
    """
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"thundercomm,qcs6490-rubikpi3-sndcard": "p1"}, total_patches=6)

    cmt = _cmt(
        message=(
            "This patch depends on the not-yet-merged binding update for "
            "thundercomm,qcs6490-rubikpi3-sndcard — the compatible must be "
            "documented in the bindings before this driver change can be merged."
        ),
        category="documentation",
        confidence=0.60,
    )

    result = reducer.reduce(patch_id="p5", comments=[cmt], series_ctx=ctx, mode="shadow")

    from kri.series import ReducerActionKind
    r3_actions = [a for a in result.actions if a.kind == ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE]

    # R3 trigger phrase fires — action is recorded.
    assert len(r3_actions) == 1, (
        f"R3 trigger phrase ('depends on the not-yet-merged') must fire an action. "
        f"Got {len(r3_actions)} R3 actions."
    )
    # r3_precondition_hits is 0 — this prose has no absence-shape hint words.
    assert result.diagnostics.r3_precondition_hits == 0, (
        f"r3_precondition_hits must be 0 for trigger-phrase prose (no absence hint words). "
        f"Got {result.diagnostics.r3_precondition_hits}."
    )


def test_diagnostics_r3_absence_prose_increments_hit_but_does_not_fire_action():
    """LLM absence-shaped prose ("does not appear to be defined") increments
    r3_precondition_hits but does NOT fire R3 action because it has no R3
    trigger phrase.

    This is the case the diagnostic counter was designed to measure: the LLM
    produces prose the trigger vocabulary misses, so R3 evaluates (and finds
    no trigger phrase → no action) while the precondition counter records
    that the input had the structural shape R3 was designed for.
    """
    reducer = SeriesReducer()
    ctx = _ctx(compatibles={"thundercomm,qcs6490-rubikpi3-sndcard": "p1"}, total_patches=6)

    cmt = _cmt(
        message=(
            "thundercomm,qcs6490-rubikpi3-sndcard does not appear to be defined "
            "in this patch or any other patch in the series"
        ),
        category="documentation",
        confidence=0.60,
    )

    result = reducer.reduce(patch_id="p5", comments=[cmt], series_ctx=ctx, mode="shadow")

    from kri.series import ReducerActionKind
    r3_actions = [a for a in result.actions if a.kind == ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE]

    # No trigger phrase → no R3 action.
    assert len(r3_actions) == 0, (
        f"Absence-shaped prose without a trigger phrase must not fire R3. "
        f"Got {len(r3_actions)} R3 actions."
    )
    # But precondition counter must record the hit.
    assert result.diagnostics.r3_precondition_hits == 1, (
        f"r3_precondition_hits must be 1 for absence-shaped prose. "
        f"Got {result.diagnostics.r3_precondition_hits}."
    )
