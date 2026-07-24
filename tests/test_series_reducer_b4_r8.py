"""WP-S1B Step B4 — R8 coupling annotation rule.

R8 (readiness spec §6 R8): when patch_b (the patch being reviewed) references
a C symbol introduced by an earlier sibling patch (patch_a), emit an
R8_COUPLING_NOTE action recording the coupling relationship. The action is
additive only — no inline comment is modified or dropped.

Ambiguity resolution (readiness review §4.5): "already discusses it" = the
symbol name appears as an exact token (word-boundary match) in any inline
comment's message or upstream_comment text.

Guard: fires only when series_ctx.declared_symbols.c_symbols contains symbols
introduced by OTHER patches (not the one being reviewed).
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
    c_symbols: dict[str, str] | None = None,
    total_patches: int = 3,
) -> SeriesReviewContext:
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
    registry = SymbolRegistry(
        c_symbols=c_symbols or {},
        compatibles={},
        dt_properties={},
        files_added={},
    )
    return SeriesReviewContext(
        series_id="r8-series",
        title="R8 test series",
        cover_letter="",
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
# R8 basic coupling detection
# ---------------------------------------------------------------------------


def test_R8_emits_coupling_note_when_diff_references_foreign_symbol():
    """Patch p2 uses qcom_snd_setup() which was introduced by p1.
    R8 must emit R8_COUPLING_NOTE with related_patch_id='p1' and
    reason='qcom_snd_setup'."""
    reducer = SeriesReducer()
    ctx = _ctx(c_symbols={"qcom_snd_setup": "p1"})
    diff = (
        "diff --git a/sound/soc/qcom/lpass.c b/sound/soc/qcom/lpass.c\n"
        "index 000..111 100644\n"
        "--- a/sound/soc/qcom/lpass.c\n"
        "+++ b/sound/soc/qcom/lpass.c\n"
        "@@ -10,3 +10,4 @@\n"
        "+\tqcom_snd_setup(dev, &cfg);\n"
    )
    cmt = _cmt(message="missing error check on initialisation path")
    result = reducer.reduce(
        patch_id="p2", comments=[cmt], series_ctx=ctx, mode="on", diff=diff
    )

    r8 = [a for a in result.actions if a.kind == ReducerActionKind.R8_COUPLING_NOTE]
    assert len(r8) == 1
    assert r8[0].related_patch_id == "p1"
    assert "qcom_snd_setup" in r8[0].reason
    # Additive: all comments survive unchanged.
    assert len(result.comments) == 1
    assert result.comments[0].message == cmt.message


def test_R8_no_action_when_diff_does_not_reference_foreign_symbol():
    """Patch p2's diff does not mention the symbol introduced by p1.
    R8 must not fire."""
    reducer = SeriesReducer()
    ctx = _ctx(c_symbols={"qcom_snd_setup": "p1"})
    diff = (
        "diff --git a/drivers/x/other.c b/drivers/x/other.c\n"
        "index 000..111 100644\n"
        "--- a/drivers/x/other.c\n"
        "+++ b/drivers/x/other.c\n"
        "@@ -5,3 +5,4 @@\n"
        "+\tpr_info(\"unrelated change\\n\");\n"
    )
    cmt = _cmt(message="just an info comment")
    result = reducer.reduce(
        patch_id="p2", comments=[cmt], series_ctx=ctx, mode="on", diff=diff
    )

    r8 = [a for a in result.actions if a.kind == ReducerActionKind.R8_COUPLING_NOTE]
    assert len(r8) == 0


def test_R8_no_action_when_inline_comment_already_discusses_symbol():
    """If the reviewer already mentioned the symbol in a comment, R8 must
    not emit a duplicate coupling note (readiness review §4.5)."""
    reducer = SeriesReducer()
    ctx = _ctx(c_symbols={"qcom_snd_setup": "p1"})
    diff = (
        "diff --git a/sound/soc/qcom/lpass.c b/sound/soc/qcom/lpass.c\n"
        "index 000..111 100644\n"
        "--- a/sound/soc/qcom/lpass.c\n"
        "+++ b/sound/soc/qcom/lpass.c\n"
        "@@ -10,3 +10,4 @@\n"
        "+\tqcom_snd_setup(dev, &cfg);\n"
    )
    # The comment already mentions the symbol.
    cmt = _cmt(message="qcom_snd_setup is called without checking the return value")
    result = reducer.reduce(
        patch_id="p2", comments=[cmt], series_ctx=ctx, mode="on", diff=diff
    )

    r8 = [a for a in result.actions if a.kind == ReducerActionKind.R8_COUPLING_NOTE]
    assert len(r8) == 0, (
        "R8 must not fire when inline comment already discusses the coupled symbol"
    )


def test_R8_no_action_for_symbols_declared_by_own_patch():
    """R8 only fires for symbols from OTHER patches. A symbol introduced by
    the current patch p2 must not trigger a coupling note."""
    reducer = SeriesReducer()
    ctx = _ctx(c_symbols={"local_helper": "p2", "foreign_fn": "p1"})
    diff = (
        "diff --git a/drivers/x/foo.c b/drivers/x/foo.c\n"
        "index 000..111 100644\n"
        "--- a/drivers/x/foo.c\n"
        "+++ b/drivers/x/foo.c\n"
        "@@ -1,3 +1,5 @@\n"
        "+\tlocal_helper();\n"
    )
    cmt = _cmt(message="generic review comment")
    result = reducer.reduce(
        patch_id="p2", comments=[cmt], series_ctx=ctx, mode="on", diff=diff
    )

    r8 = [a for a in result.actions if a.kind == ReducerActionKind.R8_COUPLING_NOTE]
    assert len(r8) == 0, (
        "symbols introduced by the current patch must not trigger a coupling note"
    )


def test_R8_groups_by_introducing_patch():
    """Two symbols introduced by different patches produce two separate
    R8_COUPLING_NOTE actions."""
    reducer = SeriesReducer()
    ctx = _ctx(c_symbols={"fn_a": "p1", "fn_b": "p2"}, total_patches=3)
    diff = (
        "diff --git a/drivers/x/foo.c b/drivers/x/foo.c\n"
        "index 000..111 100644\n"
        "--- a/drivers/x/foo.c\n"
        "+++ b/drivers/x/foo.c\n"
        "@@ -1,3 +1,6 @@\n"
        "+\tfn_a(x);\n"
        "+\tfn_b(y);\n"
    )
    cmt = _cmt(message="unrelated review comment")
    result = reducer.reduce(
        patch_id="p3", comments=[cmt], series_ctx=ctx, mode="on", diff=diff
    )

    r8 = [a for a in result.actions if a.kind == ReducerActionKind.R8_COUPLING_NOTE]
    assert len(r8) == 2
    introducers = {a.related_patch_id for a in r8}
    assert introducers == {"p1", "p2"}


def test_R8_shadow_records_action_without_modifying_comments():
    """mode='shadow': R8_COUPLING_NOTE action is emitted but no comment is
    modified (R8 is additive, _apply_R8 is a no-op in both modes)."""
    reducer = SeriesReducer()
    ctx = _ctx(c_symbols={"qcom_snd_setup": "p1"})
    diff = (
        "diff --git a/sound/soc/qcom/lpass.c b/sound/soc/qcom/lpass.c\n"
        "index 000..111 100644\n"
        "--- a/sound/soc/qcom/lpass.c\n"
        "+++ b/sound/soc/qcom/lpass.c\n"
        "@@ -10,3 +10,4 @@\n"
        "+\tqcom_snd_setup(dev, &cfg);\n"
    )
    cmt = _cmt(message="generic comment not mentioning the symbol")
    result = reducer.reduce(
        patch_id="p2", comments=[cmt], series_ctx=ctx, mode="shadow", diff=diff
    )

    r8 = [a for a in result.actions if a.kind == ReducerActionKind.R8_COUPLING_NOTE]
    assert len(r8) == 1, "R8 action must be recorded in shadow mode"
    assert result.comments[0].message == cmt.message, (
        "shadow mode must not modify comments"
    )


def test_R8_no_action_when_no_c_symbols_in_series():
    """If the series has no declared c_symbols, R8 must emit nothing."""
    reducer = SeriesReducer()
    ctx = _ctx(c_symbols={})
    diff = (
        "diff --git a/drivers/x/foo.c b/drivers/x/foo.c\n"
        "index 000..111 100644\n"
        "--- a/drivers/x/foo.c\n"
        "+++ b/drivers/x/foo.c\n"
        "@@ -1,3 +1,4 @@\n"
        "+\tpr_info(\"hello\\n\");\n"
    )
    cmt = _cmt(message="generic comment")
    result = reducer.reduce(
        patch_id="p2", comments=[cmt], series_ctx=ctx, mode="on", diff=diff
    )

    r8 = [a for a in result.actions if a.kind == ReducerActionKind.R8_COUPLING_NOTE]
    assert len(r8) == 0


def test_R8_no_action_when_diff_is_empty():
    """If no diff is provided, R8 cannot detect references and must not fire."""
    reducer = SeriesReducer()
    ctx = _ctx(c_symbols={"qcom_snd_setup": "p1"})
    cmt = _cmt(message="generic comment")
    result = reducer.reduce(
        patch_id="p2", comments=[cmt], series_ctx=ctx, mode="on", diff=""
    )

    r8 = [a for a in result.actions if a.kind == ReducerActionKind.R8_COUPLING_NOTE]
    assert len(r8) == 0
