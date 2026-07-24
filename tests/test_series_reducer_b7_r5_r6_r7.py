"""WP-S1B Step B7 — R5 (function-scope dedup), R6 (low-signal suppress),
R7 (pre-existing suppress).

All three rules are feature-flagged off by default.  These tests enable them
individually via the `flags` parameter to verify trigger / suppress / survive
behaviour.  Safety floor invariants (BLOCKER and WARNING with confidence >= 0.7
are never suppressed) are asserted for each suppressive rule.
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
# Shared fixtures
# ---------------------------------------------------------------------------


def _ctx(total_patches: int = 2) -> SeriesReviewContext:
    entries = {
        f"p{i}": PatchIndexEntry(
            patch_id=f"p{i}",
            index=i,
            total=total_patches,
            subject=f"[PATCH {i}/{total_patches}] patch-{i}",
            files_changed=(f"drivers/x/file{i}.c",),
        )
        for i in range(1, total_patches + 1)
    }
    return SeriesReviewContext(
        series_id="b7-series",
        title="B7 test series",
        cover_letter="",
        total_patches=total_patches,
        patch_index=entries,
        declared_symbols=SymbolRegistry(
            c_symbols={},
            compatibles={},
            dt_properties={},
            files_added={},
        ),
        file_touch_map={},
    )


def _cmt(
    *,
    message: str,
    upstream_comment: str | None = None,
    file_path: str = "sound/soc/qcom/sc8280xp.c",
    line_number: int = 284,
    severity: Severity = Severity.INFO,
    confidence: float = 0.5,
    category: str = "convention",
    series_prefix: str = "",
) -> InlineComment:
    return InlineComment(
        file_path=file_path,
        line_number=line_number,
        message=message,
        upstream_comment=upstream_comment,
        severity=severity,
        confidence=confidence,
        category=category,
        series_prefix=series_prefix,
    )


# A minimal diff that puts lines 284 and 292 inside sc8280xp_snd_exit,
# in different R4 buckets (284//10==28, 292//10==29).
_SC8280XP_DIFF = (
    "diff --git a/sound/soc/qcom/sc8280xp.c b/sound/soc/qcom/sc8280xp.c\n"
    "index 000..111 100644\n"
    "--- a/sound/soc/qcom/sc8280xp.c\n"
    "+++ b/sound/soc/qcom/sc8280xp.c\n"
    "@@ -280,25 +280,28 @@ static int sc8280xp_snd_exit(struct snd_soc_card *card)\n"
    " {\n"
    " \tvoid *data = snd_soc_card_get_drvdata(card);\n"
    "+\tif (!data)\n"
    "+\t\treturn -EINVAL;\n"
    "+\tkfree(data);\n"
    " \tsome_other_line;\n"
    " \tand_another;\n"
    " \tmore_lines;\n"
    " \tand_more;\n"
    " \teven_more;\n"
    " \talmost_there_now;\n"
    " \tdone_with_it;\n"
    " \tfinal_cleanup_step;\n"
    " \textra_teardown;\n"
    " \tfoo_reset;\n"
    " \treturn 0;\n"
    " }\n"
)


# ---------------------------------------------------------------------------
# R7 — pre-existing suppression
# ---------------------------------------------------------------------------


def test_R7_info_pre_existing_suppressed():
    """U21: info severity + pre-existing phrase → suppressed when R7 enabled."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="This issue is pre-existing and predates the current patch",
        severity=Severity.INFO,
        confidence=0.6,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
        flags={"series_r7_enabled": True},
    )
    assert len(result.comments) == 0, "R7 must suppress info + pre-existing"
    r7 = [a for a in result.actions if a.kind == ReducerActionKind.R7_PRE_EXISTING_SUPPRESS]
    assert len(r7) == 1


def test_R7_warning_pre_existing_survives():
    """U22: warning severity + pre-existing phrase → NOT suppressed (safety floor)."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="This is pre-existing and predates the current patch",
        severity=Severity.WARNING,
        confidence=0.5,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
        flags={"series_r7_enabled": True},
    )
    assert len(result.comments) == 1, "R7 must NOT suppress warning findings"
    r7 = [a for a in result.actions if a.kind == ReducerActionKind.R7_PRE_EXISTING_SUPPRESS]
    assert len(r7) == 0


def test_R7_disabled_by_default_no_suppress():
    """R7 flag defaults to False — pre-existing phrase with no flag → survives."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="This issue predates the current patch — not introduced by this patch",
        severity=Severity.INFO,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
    )
    assert len(result.comments) == 1


def test_R7_shadow_records_action_without_dropping():
    """shadow mode: R7 action recorded but comment survives."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="pre-existing — not something to address here",
        severity=Severity.INFO,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="shadow",
        flags={"series_r7_enabled": True},
    )
    r7 = [a for a in result.actions if a.kind == ReducerActionKind.R7_PRE_EXISTING_SUPPRESS]
    assert len(r7) == 1
    assert len(result.comments) == 1, "shadow mode must not drop comments"


def test_R7_not_something_to_address_here_fires():
    """'not something to address here' is one of the R7 trigger phrases."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="This is not something to address here per kernel guidelines",
        severity=Severity.INFO,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
        flags={"series_r7_enabled": True},
    )
    assert len(result.comments) == 0


# ---------------------------------------------------------------------------
# R6 — low-signal suppression
# ---------------------------------------------------------------------------


def test_R6_below_threshold_suppressed():
    """U23: confidence below default 0.55, category=convention, severity=info → suppressed."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="minor nit about whitespace alignment",
        severity=Severity.INFO,
        confidence=0.50,
        category="convention",
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
        flags={"series_r6_enabled": True},
    )
    assert len(result.comments) == 0, "R6 must suppress below-threshold convention/info"
    r6 = [a for a in result.actions if a.kind == ReducerActionKind.R6_LOW_SIGNAL_SUPPRESS]
    assert len(r6) == 1


def test_R6_at_threshold_survives():
    """U24: confidence == 0.55 (strict < comparison) → NOT suppressed."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="minor style note about indentation",
        severity=Severity.INFO,
        confidence=0.55,
        category="style",
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
        flags={"series_r6_enabled": True},
    )
    assert len(result.comments) == 1, "confidence == threshold must survive (strict <)"


def test_R6_warning_severity_survives():
    """U25: below threshold but severity=warning → NOT suppressed."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="minor nit about style",
        severity=Severity.WARNING,
        confidence=0.40,
        category="convention",
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
        flags={"series_r6_enabled": True},
    )
    assert len(result.comments) == 1, "R6 must never suppress warning findings"


def test_R6_non_suppressible_category_survives():
    """category=bug is not in {convention, nit, style} — R6 must not fire."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="null pointer dereference in probe path",
        severity=Severity.INFO,
        confidence=0.30,
        category="bug",
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
        flags={"series_r6_enabled": True},
    )
    assert len(result.comments) == 1


def test_R6_custom_threshold_respected():
    """SeriesReducer(low_signal_confidence_threshold=0.40) suppresses confidence=0.35."""
    reducer = SeriesReducer(low_signal_confidence_threshold=0.40)
    ctx = _ctx()
    cmt = _cmt(
        message="minor nit",
        severity=Severity.INFO,
        confidence=0.35,
        category="nit",
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
        flags={"series_r6_enabled": True},
    )
    assert len(result.comments) == 0


def test_R6_disabled_by_default():
    """R6 flag defaults to False — low-confidence nit survives without the flag."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt = _cmt(
        message="minor style nit",
        severity=Severity.INFO,
        confidence=0.30,
        category="nit",
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt],
        series_ctx=ctx,
        mode="on",
    )
    assert len(result.comments) == 1


# ---------------------------------------------------------------------------
# R5 — function-scope dedup
# ---------------------------------------------------------------------------


def test_R5_function_scope_merges_across_line_offsets():
    """U19: two findings in the same function (sc8280xp_snd_exit) with related
    text → merged when R5 is enabled. Lines 284 and 292 are in different R4
    buckets (284//10==28, 292//10==29) so R4 does not fire first."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt_a = _cmt(
        message="missing null pointer check before dereferencing card data pointer",
        confidence=0.8,
        line_number=284,
    )
    cmt_b = _cmt(
        message="card data pointer needs null check before accessing struct members",
        confidence=0.6,
        line_number=292,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt_a, cmt_b],
        series_ctx=ctx,
        mode="on",
        flags={"series_r5_enabled": True},
        diff=_SC8280XP_DIFF,
    )
    r5 = [a for a in result.actions if a.kind == ReducerActionKind.R5_FUNCTION_SCOPE_MERGE]
    assert len(r5) == 1, f"R5 must merge overlapping function-scope findings; got {len(r5)}"
    # Higher-confidence finding survives, lower-confidence is absorbed.
    assert len(result.comments) == 1
    assert result.comments[0].confidence == 0.8


def test_R5_ignores_low_overlap_findings():
    """U20: two findings in the same function with disjoint content → both kept."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt_a = _cmt(
        message="null pointer dereference in cleanup path when init fails",
        confidence=0.8,
        line_number=284,
    )
    cmt_b = _cmt(
        message="wrong return code returned from exit handler function",
        confidence=0.7,
        line_number=292,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt_a, cmt_b],
        series_ctx=ctx,
        mode="on",
        flags={"series_r5_enabled": True},
        diff=_SC8280XP_DIFF,
    )
    r5 = [a for a in result.actions if a.kind == ReducerActionKind.R5_FUNCTION_SCOPE_MERGE]
    assert len(r5) == 0, "R5 must not merge disjoint findings"
    assert len(result.comments) == 2


def test_R5_different_files_not_merged():
    """Findings in different files never share a function scope."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt_a = _cmt(
        message="missing null check before pointer dereference card data struct",
        file_path="sound/soc/qcom/sc8280xp.c",
        line_number=284,
    )
    cmt_b = _cmt(
        message="missing null check before pointer dereference card data struct",
        file_path="sound/soc/qcom/other.c",
        line_number=284,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt_a, cmt_b],
        series_ctx=ctx,
        mode="on",
        flags={"series_r5_enabled": True},
        diff=_SC8280XP_DIFF,
    )
    r5 = [a for a in result.actions if a.kind == ReducerActionKind.R5_FUNCTION_SCOPE_MERGE]
    assert len(r5) == 0


def test_R5_disabled_by_default():
    """Without the flag, R5 does not fire even with overlapping function-scope findings."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt_a = _cmt(
        message="missing null pointer check before dereferencing card data pointer",
        confidence=0.8,
        line_number=284,
    )
    cmt_b = _cmt(
        message="card data pointer needs null check before accessing struct members",
        confidence=0.6,
        line_number=292,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt_a, cmt_b],
        series_ctx=ctx,
        mode="on",
        diff=_SC8280XP_DIFF,
    )
    r5 = [a for a in result.actions if a.kind == ReducerActionKind.R5_FUNCTION_SCOPE_MERGE]
    assert len(r5) == 0
    assert len(result.comments) == 2


def test_R5_safety_floor_not_merged():
    """BLOCKER findings are safety-floored and must never be merged by R5."""
    reducer = SeriesReducer()
    ctx = _ctx()
    cmt_a = _cmt(
        message="missing null pointer check before dereferencing card data pointer",
        severity=Severity.BLOCKER,
        confidence=0.9,
        line_number=284,
    )
    cmt_b = _cmt(
        message="card data pointer needs null check before accessing struct members",
        severity=Severity.BLOCKER,
        confidence=0.8,
        line_number=292,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt_a, cmt_b],
        series_ctx=ctx,
        mode="on",
        flags={"series_r5_enabled": True},
        diff=_SC8280XP_DIFF,
    )
    r5 = [a for a in result.actions if a.kind == ReducerActionKind.R5_FUNCTION_SCOPE_MERGE]
    assert len(r5) == 0, "R5 must never merge safety-floored (BLOCKER) findings"
    assert len(result.comments) == 2


# Extended diff covering 35 context lines so that lines 284, 292, AND 301 all
# map to sc8280xp_snd_exit (buckets 28, 29, 30 — three distinct R4 buckets).
_SC8280XP_DIFF_3BUCKET = (
    "diff --git a/sound/soc/qcom/sc8280xp.c b/sound/soc/qcom/sc8280xp.c\n"
    "index 000..111 100644\n"
    "--- a/sound/soc/qcom/sc8280xp.c\n"
    "+++ b/sound/soc/qcom/sc8280xp.c\n"
    "@@ -280,35 +280,38 @@ static int sc8280xp_snd_exit(struct snd_soc_card *card)\n"
    " {\n"
    " \tvoid *data = snd_soc_card_get_drvdata(card);\n"
    "+\tif (!data)\n"
    "+\t\treturn -EINVAL;\n"
    "+\tkfree(data);\n"
    " \tsome_other_line;\n"
    " \tand_another;\n"
    " \tmore_lines;\n"
    " \tand_more;\n"
    " \teven_more;\n"
    " \talmost_there_now;\n"
    " \tdone_with_it;\n"
    " \tfinal_cleanup_step;\n"
    " \textra_teardown;\n"
    " \tfoo_reset;\n"
    " \tbaz_line_a;\n"
    " \tbaz_line_b;\n"
    " \tbaz_line_c;\n"
    " \tbaz_line_d;\n"
    " \tbaz_line_e;\n"
    " \tbaz_line_f;\n"
    " \tbaz_line_g;\n"
    " \tbaz_line_h;\n"
    " \tbaz_line_i;\n"
    " \tbaz_line_j;\n"
    " \treturn 0;\n"
    " }\n"
)


def test_R5_three_way_no_content_loss():
    """Regression: when comment A (intermediate confidence) is absorbed by B,
    the inner loop must break so that A is not also used as keeper for C.
    If the break is missing, C's content would be orphaned into a dropped
    keeper_updates entry and silently lost.

    Three comments, all in sc8280xp_snd_exit, different R4 buckets:
      A (line 292, conf=0.7), B (line 284, conf=0.9), C (line 301, conf=0.5).
    Pairwise order in the inner loop: (A,B), (A,C), (B,C).
    - (A,B): B.conf > A.conf → B is keeper, A absorbed. Break inner loop.
    - (B,C): B.conf > C.conf → B is keeper, C absorbed.
    After both merges, B (the single survivor) must contain BOTH A's AND C's
    content in its message.
    """
    reducer = SeriesReducer()
    ctx = _ctx()
    # Craft messages with enough shared tokens to trigger Jaccard.
    # The shared vocabulary: "null pointer dereference check card data"
    cmt_a = _cmt(  # intermediate confidence — will be absorbed by B
        message="null pointer dereference missing check on card data teardown path",
        confidence=0.7,
        line_number=292,
    )
    cmt_b = _cmt(  # highest confidence — the single survivor
        message="null pointer dereference missing check card data cleanup handler",
        confidence=0.9,
        line_number=284,
    )
    cmt_c = _cmt(  # lowest confidence — must also be absorbed by B, not orphaned
        message="null pointer dereference card data pointer check missing handler",
        confidence=0.5,
        line_number=301,
    )
    result = reducer.reduce(
        patch_id="p1",
        comments=[cmt_a, cmt_b, cmt_c],
        series_ctx=ctx,
        mode="on",
        flags={"series_r5_enabled": True},
        diff=_SC8280XP_DIFF_3BUCKET,
    )
    r5 = [a for a in result.actions if a.kind == ReducerActionKind.R5_FUNCTION_SCOPE_MERGE]
    assert len(r5) == 2, f"Expected 2 R5 merge actions; got {len(r5)}"
    assert len(result.comments) == 1, "Only one comment must survive"
    surviving = result.comments[0]
    # B must carry content from A AND C (order is B>A, B>C or B>C in the second merge)
    assert cmt_a.message in surviving.message or any(
        s in surviving.message for s in cmt_a.message.split()[:4]
    ), "A's content must appear in the surviving comment"
    assert cmt_c.message in surviving.message or any(
        s in surviving.message for s in cmt_c.message.split()[:4]
    ), "C's content must appear in the surviving comment"
