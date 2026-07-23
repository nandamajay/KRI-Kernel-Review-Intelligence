"""Structural corpus test — R-rules must fire on real LLM-prose comment shapes.

Based on the RubikPi3 v2 6-patch series quality gap analysis
(kri/docs/POST_WP_CP1_QUALITY_GAP_REVIEW_2026-07-21.md §3).

These tests use hand-authored comment messages that mirror the actual LLM
prose surfaced by the shadow run.  They are NOT snapshot tests and do NOT
replay cached LLM output — the prose is transcribed from the gap review
document and stripped to the essential shape each rule needs to recognise.

Rule coverage in this file:
  R1 — declared-symbol suppression with real compatible-string prose
  R3 — external-to-internal rewrite with real "not-yet-merged" prose
  R4 — line-bucket merge on real dt_binding duplicate-finding prose

R8 is a stub (returns []) and is NOT tested here.
R2 is deferred (not in the tree) and is NOT tested here.
R5/R6/R7 are shadow-only (no mutations) and NOT tested here.
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
# Fixtures: 6-patch series skeleton matching the RubikPi3 v2 structure.
#
# Patches:
#   p1: dt-bindings: qcom,sm8250: Add RubikPi 3 sound card compatible
#       Adds thundercomm,qcs6490-rubikpi3-sndcard to qcom,sm8250.yaml (binding)
#   p2: dt-bindings: es8316: Document everest,jack-detect-inverted
#       Adds everest,es8316 to the es8316 binding
#   p3–p6: machine driver + DTS patches (reviewed for R4 corpus)
# ---------------------------------------------------------------------------

_RUBIKPI3_COMPAT = "thundercomm,qcs6490-rubikpi3-sndcard"
_ES8316_COMPAT = "everest,es8316"


def _rubikpi3_ctx() -> SeriesReviewContext:
    """6-patch series context mirroring the RubikPi3 v2 shadow run."""
    entries = {
        f"p{i}": PatchIndexEntry(
            patch_id=f"p{i}",
            index=i,
            total=6,
            subject=f"[PATCH v2 {i}/6] rubikpi3 patch {i}",
            files_changed=("drivers/soc/foo.c",),
        )
        for i in range(1, 7)
    }
    registry = SymbolRegistry(
        compatibles={
            # p1 adds the binding for the main snd card compatible.
            _RUBIKPI3_COMPAT: "p1",
            # p2 adds the es8316 binding.
            _ES8316_COMPAT: "p2",
        },
        dt_properties={},
        c_symbols={},
        files_added={},
    )
    return SeriesReviewContext(
        series_id="rubikpi3-v2",
        title="RubikPi3 v2 ASoC series",
        cover_letter="Add RubikPi 3 audio support for QCS6490",
        total_patches=6,
        patch_index=entries,
        declared_symbols=registry,
        file_touch_map={},
    )


def _cmt(
    *,
    message: str,
    upstream_comment: str | None = None,
    file_path: str = "sound/soc/qcom/sc8280xp.c",
    line_number: int = 100,
    severity: Severity = Severity.INFO,
    confidence: float = 0.6,
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
# R1 corpus: "missing binding" prose on a symbol declared in the series
# ---------------------------------------------------------------------------

# Verbatim shape from gap analysis §3.1 (findings 5.2, 5.3):
_R1_PROSE_MISSING_BINDING = (
    "The compatible string thundercomm,qcs6490-rubikpi3-sndcard is added to "
    "sc8280xp.c but there is no corresponding DT binding documentation for "
    "this compatible in the bindings directory.  Adding a new compatible to "
    "the machine driver without a corresponding binding schema is not "
    "acceptable — the binding must be documented."
)

_R1_PROSE_BINDING_ABSENT = (
    "missing binding for thundercomm,qcs6490-rubikpi3-sndcard — the "
    "compatible is used here but no binding YAML entry was found."
)


def test_R1_fires_on_missing_binding_prose_when_compatible_declared_in_series():
    """R1 must suppress a 'missing binding' finding when the named compatible
    is present in the series' declared_symbols.compatibles registry.

    Prose mirrors finding 5.3 from the RubikPi3 v2 shadow run (INFO-severity
    shape).  Finding 5.2 was a WARNING with confidence 0.72 which the safety
    floor preserves — see test_R1_safety_floor_preserves_warning_despite_series.
    """
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    # Finding 5.3 shape: INFO severity, confidence 0.65 — not safety-floored.
    # The binding IS in p1; R1 must suppress.
    f53 = _cmt(
        message=_R1_PROSE_BINDING_ABSENT,
        line_number=440,
        file_path="sound/soc/qcom/sc8280xp.c",
        severity=Severity.INFO,
        confidence=0.65,
        category="documentation",
    )

    result = reducer.reduce(
        patch_id="p5", comments=[f53], series_ctx=ctx, mode="on"
    )

    r1_actions = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    assert len(r1_actions) == 1, (
        f"R1 must fire on 'missing binding' prose mentioning a declared compatible. "
        f"Got {len(r1_actions)} R1 actions."
    )
    assert r1_actions[0].related_patch_id == "p1", (
        f"R1 must identify p1 as the declaring sibling. "
        f"Got: {r1_actions[0].related_patch_id}"
    )
    # The finding is suppressed (dropped from output).
    assert len(result.comments) == 0, (
        "R1 must drop the comment from output in mode='on'"
    )


def test_R1_safety_floor_preserves_warning_despite_series_declaration():
    """Safety floor must prevent R1 from suppressing a WARNING (confidence ≥ 0.7)
    even when the named compatible IS declared in the series.

    Gap review §3.1: finding 5.2 was a WARNING with confidence 0.72 that
    flagged a 'missing binding'.  The binding IS in p1 (declared in series).
    BUT the safety floor rule says: never suppress a blocker or a warning
    with confidence ≥ 0.7.  R1 must NOT suppress finding 5.2.

    This is the correct production behavior — a high-confidence warning is
    preserved for maintainer visibility even when series-awareness would
    otherwise suppress it.
    """
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    f52 = _cmt(
        message=_R1_PROSE_MISSING_BINDING,
        line_number=440,
        file_path="sound/soc/qcom/sc8280xp.c",
        severity=Severity.WARNING,
        confidence=0.72,
        category="dt_binding",
    )

    result = reducer.reduce(
        patch_id="p5", comments=[f52], series_ctx=ctx, mode="on"
    )

    r1_actions = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    # Safety floor: WARNING at 0.72 must NOT be suppressed even though the
    # compatible is declared.
    assert len(r1_actions) == 0, (
        f"R1 must NOT fire on a WARNING-severity finding (conf 0.72 ≥ 0.70 floor). "
        f"Got {len(r1_actions)} R1 actions — safety floor violated."
    )
    assert len(result.comments) == 1, (
        "Safety-floored WARNING must survive R1 suppression"
    )


def test_R1_fires_on_both_missing_binding_duplicates_from_corpus():
    """R1 must suppress the non-floored 'missing binding' findings and preserve
    the safety-floored ones.

    The gap review notes findings 5.2 (WARNING 0.72) and 5.3 (INFO 0.65)
    both flag the same missing binding.  R1 should suppress 5.3 (not floored)
    and preserve 5.2 (floored: WARNING ≥ 0.7).

    Output: 1 comment (f52 preserved), 1 R1 action (f53 suppressed).
    """
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    f52 = _cmt(
        message=_R1_PROSE_MISSING_BINDING,
        line_number=440,
        confidence=0.72,
        severity=Severity.WARNING,
        category="dt_binding",
    )
    f53 = _cmt(
        message=_R1_PROSE_BINDING_ABSENT,
        line_number=440,
        confidence=0.65,
        severity=Severity.INFO,
        category="documentation",
    )

    result = reducer.reduce(
        patch_id="p5", comments=[f52, f53], series_ctx=ctx, mode="on"
    )

    r1_actions = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    # Only f53 is suppressible (f52 is safety-floored).
    assert len(r1_actions) == 1, (
        f"Only the non-floored duplicate must be suppressed by R1. "
        f"Got {len(r1_actions)} R1 actions."
    )
    # f52 (WARNING 0.72) must survive; f53 (INFO 0.65) must be dropped.
    assert len(result.comments) == 1
    assert result.comments[0].confidence == 0.72


def test_R1_does_not_fire_on_binding_prose_for_undeclared_compatible():
    """R1 must NOT suppress a 'missing binding' comment when the named
    compatible is NOT in the series' declared_symbols registry.

    Same prose shape; different symbol that was never declared.
    """
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    undeclared_compat_prose = (
        "missing binding for qualcomm,new-codec-sndcard — the compatible "
        "appears in the DTS but has no binding YAML documentation."
    )
    cmt = _cmt(message=undeclared_compat_prose, line_number=440, category="dt_binding")

    result = reducer.reduce(
        patch_id="p5", comments=[cmt], series_ctx=ctx, mode="on"
    )

    r1_actions = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    assert len(r1_actions) == 0, (
        "R1 must not fire when the named compatible is not declared in the series"
    )
    assert len(result.comments) == 1


# ---------------------------------------------------------------------------
# R3 corpus: "not-yet-merged" prose for a dependency that IS in the series
# ---------------------------------------------------------------------------

# Verbatim shape from gap analysis §3.3 (patch 5 risk_area prose):
_R3_PROSE_NOT_YET_MERGED = (
    "This patch depends on the not-yet-merged binding update for "
    "thundercomm,qcs6490-rubikpi3-sndcard — the compatible must be "
    "documented in the bindings before this driver change can be merged."
)

_R3_PROSE_WAITING_ON = (
    "waiting on another patch to add thundercomm,qcs6490-rubikpi3-sndcard "
    "to the sm8250 binding schema."
)


def test_R3_fires_on_not_yet_merged_prose_when_series_declares_it():
    """R3 must rewrite a 'not-yet-merged' finding when the referenced
    symbol IS declared in the series.

    Prose mirrors the patch 5 risk_area shape from the gap review §3.3:
    the LLM said 'depends on not-yet-merged binding' but the binding
    is patch 1 of the same series.
    """
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    cmt = _cmt(
        message=_R3_PROSE_NOT_YET_MERGED,
        line_number=200,
        file_path="sound/soc/qcom/sc8280xp.c",
        severity=Severity.INFO,
        confidence=0.6,
        category="documentation",
    )

    result = reducer.reduce(
        patch_id="p5", comments=[cmt], series_ctx=ctx, mode="on"
    )

    r3_actions = [a for a in result.actions if a.kind == ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE]
    assert len(r3_actions) == 1, (
        f"R3 must fire on 'not-yet-merged' prose referencing a declared symbol. "
        f"Got {len(r3_actions)} R3 actions."
    )
    assert r3_actions[0].related_patch_id == "p1", (
        f"R3 must attribute the rewrite to the declaring sibling p1. "
        f"Got: {r3_actions[0].related_patch_id}"
    )
    # The comment is rewritten, not dropped.
    assert len(result.comments) == 1
    rewritten = result.comments[0]
    assert rewritten.message.startswith("Depends on patch p1: "), (
        f"Rewritten message must begin with 'Depends on patch p1: '. "
        f"Got: {rewritten.message[:60]!r}"
    )
    # The original concern is preserved after the prefix.
    assert "thundercomm,qcs6490-rubikpi3-sndcard" in rewritten.message


def test_R3_fires_on_waiting_on_prose_same_series():
    """R3 must also fire on 'waiting on another patch' prose — a second
    trigger phrase shape observed in the corpus."""
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    cmt = _cmt(
        message=_R3_PROSE_WAITING_ON,
        line_number=300,
        severity=Severity.INFO,
        confidence=0.55,
        category="documentation",
    )

    result = reducer.reduce(
        patch_id="p5", comments=[cmt], series_ctx=ctx, mode="on"
    )

    r3_actions = [a for a in result.actions if a.kind == ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE]
    assert len(r3_actions) == 1
    assert result.comments[0].message.startswith("Depends on patch p1: ")


def test_R3_shadow_mode_records_action_without_rewriting_corpus_prose():
    """Shadow mode: R3 records what it would do but leaves message unchanged."""
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    cmt = _cmt(message=_R3_PROSE_NOT_YET_MERGED, line_number=200)
    original_message = cmt.message

    result = reducer.reduce(
        patch_id="p5", comments=[cmt], series_ctx=ctx, mode="shadow"
    )

    r3_actions = [a for a in result.actions if a.kind == ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE]
    assert len(r3_actions) == 1
    # Shadow must NOT mutate the message.
    assert result.comments[0].message == original_message


# ---------------------------------------------------------------------------
# R4 corpus: duplicate dt_binding findings at the same line bucket
#
# Based on gap review §2.3 and §2.4: findings 6.2/6.3 and 6.4/6.5 are
# duplicate findings in the same file at adjacent lines (759/760 and
# 1554/1556).  Both pairs hit the same line//10 bucket.
# ---------------------------------------------------------------------------

_R4_PROSE_IRQ_POLARITY_A = (
    "The IRQ polarity specified here conflicts with the binding documented "
    "in everest,es8316.yaml — the binding requires active-low but the DTS "
    "specifies active-high.  This will cause IRQ misfire on the RubikPi 3."
)

_R4_PROSE_IRQ_POLARITY_B = (
    "IRQ configuration mismatch: the jack detection interrupt at this node "
    "should be IRQ_TYPE_EDGE_FALLING per the es8316 binding, but "
    "IRQ_TYPE_EDGE_RISING is specified."
)

_R4_PROSE_REGULATOR_A = (
    "regulator-always-on property here may prevent power-management savings; "
    "the codec supply draw is in the µA range so always-on is warranted only "
    "if a board-level constraint requires it — worth a comment."
)

_R4_PROSE_REGULATOR_B = (
    "Consider whether regulator-always-on is necessary for this codec supply; "
    "if the supply draws µA adding always-on costs standby power unnecessarily."
)


def test_R4_merges_duplicate_irq_polarity_findings_from_corpus():
    """R4 merges two duplicate IRQ-polarity findings when neither is safety-floored.

    Based on gap review §2.3 (findings 6.2/6.3) — both at adjacent lines
    in the DTS file.  In the real corpus, finding 6.3 was WARNING at 0.72
    (safety-floored, excluded from R4), but here both are lowered to INFO
    to exercise the merge path.

    See test_R4_safety_floor_excludes_high_conf_warning_from_bucket for the
    complementary assertion about the real-corpus confidence levels.
    """
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    # Both as INFO / confidence below the floor — bucket-eligible.
    # Lines 755 and 759 both map to bucket key 75 (755//10 == 759//10 == 75).
    # Note: line 760 would be bucket 76 — do not use 759+760.
    f63 = _cmt(
        message=_R4_PROSE_IRQ_POLARITY_A,
        file_path="arch/arm64/boot/dts/qcom/sc8280xp-hp-omnibook-x.dts",
        line_number=759,
        severity=Severity.INFO,
        confidence=0.65,
        category="dt_binding",
    )
    f62 = _cmt(
        message=_R4_PROSE_IRQ_POLARITY_B,
        file_path="arch/arm64/boot/dts/qcom/sc8280xp-hp-omnibook-x.dts",
        line_number=755,
        severity=Severity.INFO,
        confidence=0.60,
        category="dt_binding",
    )

    result = reducer.reduce(
        patch_id="p6", comments=[f63, f62], series_ctx=ctx, mode="on"
    )

    r4_actions = [a for a in result.actions if a.kind == ReducerActionKind.R4_LINE_BUCKET_MERGE]
    assert len(r4_actions) == 1, (
        f"R4 must merge the two IRQ-polarity findings into one. "
        f"Got {len(r4_actions)} R4 actions."
    )
    # Keeper is f63 (higher confidence 0.65 > 0.60).
    assert len(result.comments) == 1
    keeper = result.comments[0]
    assert keeper.confidence == 0.65
    assert "IRQ polarity" in keeper.message or "conflicts with the binding" in keeper.message
    # Absorbed finding's text is appended.
    assert "Related remark:" in keeper.message


def test_R4_safety_floor_excludes_high_conf_warning_from_bucket():
    """Safety floor prevents R4 from bucketing a WARNING (confidence ≥ 0.7).

    In the real RubikPi3 corpus, finding 6.3 was a WARNING with confidence
    0.72 (gap review §2.3).  R4 must NOT include safety-floored findings
    in its line-bucket.  The floored finding survives as-is; the
    lower-confidence sibling in the SAME bucket is alone and emits no merge.

    Both findings are in the same bucket (lines 759 and 764 → bucket 75).
    Without the safety floor, R4 would merge them.  With it, f63 is excluded
    from bucketing entirely → f62 is alone → no merge.
    """
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    # Corpus-accurate: f63 is WARNING 0.72 (safety-floored).
    # Lines 759 and 755 are in the same bucket (759//10 == 755//10 == 75).
    f63 = _cmt(
        message=_R4_PROSE_IRQ_POLARITY_A,
        file_path="arch/arm64/boot/dts/qcom/sc8280xp-hp-omnibook-x.dts",
        line_number=759,
        severity=Severity.WARNING,
        confidence=0.72,
        category="dt_binding",
    )
    f62 = _cmt(
        message=_R4_PROSE_IRQ_POLARITY_B,
        file_path="arch/arm64/boot/dts/qcom/sc8280xp-hp-omnibook-x.dts",
        line_number=755,
        severity=Severity.INFO,
        confidence=0.60,
        category="dt_binding",
    )

    result = reducer.reduce(
        patch_id="p6", comments=[f63, f62], series_ctx=ctx, mode="on"
    )

    r4_actions = [a for a in result.actions if a.kind == ReducerActionKind.R4_LINE_BUCKET_MERGE]
    # f63 is safety-floored; excluded from bucketing.
    # f62 is alone in bucket 75; no merge.
    assert len(r4_actions) == 0, (
        f"R4 must not merge when one finding is safety-floored. "
        f"Got {len(r4_actions)} R4 actions."
    )
    assert len(result.comments) == 2, (
        "Both comments must survive: f63 preserved by safety floor, "
        "f62 alone in bucket 75 (no merge partner after f63 is excluded)"
    )


def test_R4_merges_duplicate_regulator_findings_at_same_line_bucket():
    """R4 must merge two regulator-always-on findings at lines 1554/1556
    (bucket key 155), mirroring gap review §2.4 (findings 6.4/6.5).
    """
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    f65 = _cmt(
        message=_R4_PROSE_REGULATOR_A,
        file_path="arch/arm64/boot/dts/qcom/sc8280xp-hp-omnibook-x.dts",
        line_number=1554,
        severity=Severity.INFO,
        confidence=0.55,
        category="dt_binding",
    )
    f64 = _cmt(
        message=_R4_PROSE_REGULATOR_B,
        file_path="arch/arm64/boot/dts/qcom/sc8280xp-hp-omnibook-x.dts",
        line_number=1556,
        severity=Severity.INFO,
        confidence=0.50,
        category="dt_binding",
    )

    result = reducer.reduce(
        patch_id="p6", comments=[f65, f64], series_ctx=ctx, mode="on"
    )

    r4_actions = [a for a in result.actions if a.kind == ReducerActionKind.R4_LINE_BUCKET_MERGE]
    assert len(r4_actions) == 1
    assert len(result.comments) == 1
    keeper = result.comments[0]
    assert keeper.confidence == 0.55  # max of (0.55, 0.50)
    assert "Related remark:" in keeper.message


# ---------------------------------------------------------------------------
# Full series pass: R1 + R3 + R4 on realistic multi-comment patch 5 input
#
# This mirrors what the reducer would receive for patch 5 of the RubikPi3
# series after the LLM produces its findings.
# ---------------------------------------------------------------------------


def test_full_series_pass_rubikpi3_patch5_shape():
    """End-to-end: patch 5 input with the three series-awareness failure
    shapes from the gap review — rules interact correctly.

    Input (6 comments):
      - f52: R1 safety-floor case — WARNING 0.72 'missing binding'; declared
             in p1 BUT safety floor prevents suppression.  SURVIVES.
      - f53: R1 suppression case — INFO 0.65 'missing binding'; declared in
             p1; suppressed by R1.  DROPPED.
      - f_r3: R3 rewrite case — 'not-yet-merged binding'; declared in p1;
              rewritten by R3 with 'Depends on patch p1: ' prefix.  SURVIVES.
      - f_r4a: R4 first candidate — INFO 0.65 IRQ polarity; not safety-floored.
      - f_r4b: R4 second candidate — INFO 0.60 IRQ polarity; not safety-floored;
               same line-bucket as f_r4a.  MERGED into f_r4a.
      - f_keep: Unrelated WARNING 0.80 finding.  SURVIVES.

    Expected output (4 comments):
      1. f52  — WARNING preserved by safety floor
      2. f_r3 — R3-rewritten ("Depends on patch p1: ...")
      3. f_r4a — R4 keeper (absorbed f_r4b; "Related remark:" tail)
      4. f_keep — unaffected

    Rules fired: 1 R1 action, 1 R3 action, 1 R4 action.
    """
    reducer = SeriesReducer()
    ctx = _rubikpi3_ctx()

    f52 = _cmt(
        message=_R1_PROSE_MISSING_BINDING,
        line_number=440,
        severity=Severity.WARNING,
        confidence=0.72,
        category="dt_binding",
    )
    f53 = _cmt(
        message=_R1_PROSE_BINDING_ABSENT,
        line_number=442,
        severity=Severity.INFO,
        confidence=0.65,
        category="documentation",
    )
    f_r3 = _cmt(
        message=_R3_PROSE_NOT_YET_MERGED,
        line_number=200,
        severity=Severity.INFO,
        confidence=0.60,
        category="documentation",
    )
    f_r4a = _cmt(
        message=_R4_PROSE_IRQ_POLARITY_A,
        file_path="arch/arm64/boot/dts/qcom/sc8280xp.dts",
        line_number=759,
        severity=Severity.INFO,
        confidence=0.65,
        category="dt_binding",
    )
    f_r4b = _cmt(
        message=_R4_PROSE_IRQ_POLARITY_B,
        file_path="arch/arm64/boot/dts/qcom/sc8280xp.dts",
        line_number=755,
        severity=Severity.INFO,
        confidence=0.60,
        category="dt_binding",
    )
    f_keep = _cmt(
        message="snd_soc_dapm_ignore_suspend call missing for HEADPHONE widget",
        line_number=600,
        severity=Severity.WARNING,
        confidence=0.80,
        category="api_misuse",
    )

    result = reducer.reduce(
        patch_id="p5",
        comments=[f52, f53, f_r3, f_r4a, f_r4b, f_keep],
        series_ctx=ctx,
        mode="on",
    )

    r1_actions = [a for a in result.actions if a.kind == ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS]
    r3_actions = [a for a in result.actions if a.kind == ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE]
    r4_actions = [a for a in result.actions if a.kind == ReducerActionKind.R4_LINE_BUCKET_MERGE]

    # R1 suppresses f53 only (f52 safety-floored at WARNING 0.72).
    assert len(r1_actions) == 1, f"Expected 1 R1 action, got {len(r1_actions)}"
    # R3 rewrites f_r3.
    assert len(r3_actions) == 1, f"Expected 1 R3 action, got {len(r3_actions)}"
    # R4 merges f_r4a + f_r4b (both INFO, not safety-floored, same bucket).
    assert len(r4_actions) == 1, f"Expected 1 R4 action, got {len(r4_actions)}"

    # Output: f52 (preserved) + f_r3 (rewritten) + f_r4a (keeper) + f_keep = 4
    assert len(result.comments) == 4, (
        f"Expected 4 survivors, got {len(result.comments)}"
    )

    messages = [c.message for c in result.comments]

    # f52 preserved by safety floor.
    assert any(_RUBIKPI3_COMPAT in m and "no corresponding DT binding" in m for m in messages), (
        "f52 (WARNING 0.72) must survive — safety floor prevents R1 suppression"
    )

    # R3-rewritten comment must carry the prefix.
    assert any(m.startswith("Depends on patch p1: ") for m in messages), (
        "R3-rewritten comment must have 'Depends on patch p1: ' prefix"
    )

    # R4 keeper must carry absorbed text.
    r4_keeper_msgs = [m for m in messages if "IRQ polarity" in m or "conflicts with the binding" in m]
    assert len(r4_keeper_msgs) == 1
    assert "Related remark:" in r4_keeper_msgs[0]

    # f_keep must survive.
    assert any("snd_soc_dapm_ignore_suspend" in m for m in messages)

    # Safety floor: all WARNING ≥ 0.7 findings must be in output.
    floored = [c for c in result.comments
               if getattr(c.severity, 'value', c.severity) == "warning" and c.confidence >= 0.7]
    assert len(floored) == 2, (
        f"f52 (WARNING 0.72) and f_keep (WARNING 0.80) must both survive. "
        f"Got {len(floored)} floored findings in output."
    )
