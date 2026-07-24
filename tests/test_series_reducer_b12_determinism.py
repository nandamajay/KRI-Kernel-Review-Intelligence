"""WP-S1B Step B12 — reducer determinism and Strategy-C prompt-reinjection guard.

§6.7 Determinism: two reduce() calls on the same input must produce repr()-equal
output. This catches any future regression from set-based iteration or dict
ordering instability that the Sec. 40 AST scan cannot detect.

§6.10 Strategy-C boundary: series_prefix, R3/R4/R5/R6/R7/R8 action tags, and
shadow-mode reducer fields must never appear in the series-context block
injected into subsequent LLM prompts. format_series_context() reads only from
SeriesReviewContext.declared_symbols (registry + metadata), not from
ReducerResult — but this test pins that invariant so future refactors cannot
accidentally break it.
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
from kri.series.prompt import format_series_context


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ctx(
    total_patches: int = 4,
    c_symbols: dict[str, str] | None = None,
    compatibles: dict[str, str] | None = None,
) -> SeriesReviewContext:
    entries = {
        f"p{i}": PatchIndexEntry(
            patch_id=f"p{i}",
            index=i,
            total=total_patches,
            subject=f"[PATCH {i}/{total_patches}] patch-{i}",
            files_changed=(f"sound/soc/qcom/file{i}.c",),
        )
        for i in range(1, total_patches + 1)
    }
    return SeriesReviewContext(
        series_id="b12-series",
        title="B12 test series",
        cover_letter="Cross-patch series for determinism test.",
        total_patches=total_patches,
        patch_index=entries,
        declared_symbols=SymbolRegistry(
            c_symbols=c_symbols or {"qcom_snd_init": "p1", "sc8280xp_probe": "p2"},
            compatibles=compatibles or {"qcom,sc8280xp-sndcard": "p1"},
            dt_properties={"qcom,model": "p1"},
            files_added={"sound/soc/qcom/sc8280xp.c": "p1"},
        ),
        file_touch_map={"sound/soc/qcom/sc8280xp.c": ("p1", "p3")},
    )


def _comments() -> list[InlineComment]:
    """A realistic comment set with enough variety to exercise R4/R5/R6/R7."""
    return [
        InlineComment(
            file_path="sound/soc/qcom/sc8280xp.c",
            line_number=142,
            message="missing binding for qcom,sc8280xp-sndcard in this patch",
            severity=Severity.WARNING,
            confidence=0.8,
            category="documentation",
        ),
        InlineComment(
            file_path="sound/soc/qcom/sc8280xp.c",
            line_number=148,
            message="missing binding for qcom,sc8280xp-sndcard needs documentation",
            severity=Severity.INFO,
            confidence=0.6,
            category="documentation",
        ),
        InlineComment(
            file_path="sound/soc/qcom/sc8280xp.c",
            line_number=200,
            message="minor nit about whitespace alignment around assignment",
            severity=Severity.INFO,
            confidence=0.40,
            category="nit",
        ),
        InlineComment(
            file_path="sound/soc/qcom/sc8280xp.c",
            line_number=201,
            message="style issue with extra spaces before assignment operator",
            severity=Severity.INFO,
            confidence=0.35,
            category="style",
        ),
        InlineComment(
            file_path="sound/soc/qcom/sc8280xp.c",
            line_number=300,
            message="this is pre-existing and predates the current patch entirely",
            severity=Severity.INFO,
            confidence=0.55,
            category="convention",
        ),
    ]


# ---------------------------------------------------------------------------
# §6.7 Determinism tests
# ---------------------------------------------------------------------------


def test_reducer_output_is_deterministic_across_runs():
    """§6.7: three reduce() calls on the same input must produce repr()-equal
    ReducerResult values, confirming no set-iteration or dict-ordering drift."""
    reducer = SeriesReducer()
    ctx = _ctx()
    comments = _comments()

    results = [
        reducer.reduce(
            patch_id="p3",
            comments=comments,
            series_ctx=ctx,
            mode="shadow",
        )
        for _ in range(3)
    ]

    r0 = repr(results[0])
    r1 = repr(results[1])
    r2 = repr(results[2])

    assert r0 == r1, "Reducer output differs between run 1 and run 2"
    assert r1 == r2, "Reducer output differs between run 2 and run 3"


def test_reducer_output_is_deterministic_mode_on():
    """§6.7: determinism in mode='on' (comments are mutated) — same guarantee."""
    reducer = SeriesReducer()
    ctx = _ctx(
        compatibles={"foo,bar": "p1"},
    )
    comments = [
        InlineComment(
            file_path="drivers/x/foo.c",
            line_number=10,
            message="missing binding for foo,bar — not declared in this patch",
            severity=Severity.INFO,
            confidence=0.6,
            category="documentation",
        ),
        InlineComment(
            file_path="drivers/x/foo.c",
            line_number=12,
            message="binding is missing for foo,bar — must be added",
            severity=Severity.INFO,
            confidence=0.5,
            category="documentation",
        ),
    ]

    results = [
        reducer.reduce(
            patch_id="p2",
            comments=list(comments),  # fresh copy each run
            series_ctx=ctx,
            mode="on",
        )
        for _ in range(3)
    ]

    r0 = repr(results[0])
    r1 = repr(results[1])
    r2 = repr(results[2])

    assert r0 == r1
    assert r1 == r2


def test_reducer_actions_list_order_is_stable():
    """Action list order must be stable across runs — not set-derived."""
    reducer = SeriesReducer()
    ctx = _ctx()
    comments = _comments()

    results = [
        reducer.reduce(
            patch_id="p3",
            comments=comments,
            series_ctx=ctx,
            mode="shadow",
        )
        for _ in range(5)
    ]

    action_reprs = [repr([a.kind for a in r.actions]) for r in results]
    assert len(set(action_reprs)) == 1, (
        "Action order differs across runs: " + str(set(action_reprs))
    )


# ---------------------------------------------------------------------------
# §6.10 Strategy-C prompt-reinjection guard
# ---------------------------------------------------------------------------

# These string markers are reducer-output artefacts that must never appear
# in a series-context block injected into an LLM prompt.
_REDUCER_MARKERS = (
    "[floored-cluster]",
    "[Series-internal dependency",
    "series_prefix",
    "R4_LINE_BUCKET_MERGE",
    "R4_LINE_BUCKET_ANNOTATE",
    "R3_EXTERNAL_TO_INTERNAL_REWRITE",
    "R5_FUNCTION_SCOPE_MERGE",
    "R6_LOW_SIGNAL_SUPPRESS",
    "R7_PRE_EXISTING_SUPPRESS",
    "R8_COUPLING_NOTE",
    "Related remark:",
    "series_reducer_actions",
    "absorbed_refs",
)


def test_format_series_context_contains_no_reducer_output_markers():
    """§6.10: format_series_context output must not contain any reducer
    output artefact. Reducer results (series_prefix tags, action kind names,
    absorbed_refs, Related remark markers) must never cross the Strategy-C
    boundary into prompts sent to subsequent patch agents."""
    ctx = _ctx()

    for patch_id in ["p1", "p2", "p3", "p4"]:
        rendered = format_series_context(ctx, patch_id)
        for marker in _REDUCER_MARKERS:
            assert marker not in rendered, (
                f"Reducer artefact {marker!r} found in format_series_context "
                f"output for patch {patch_id!r}. Strategy-C boundary violated."
            )


def test_format_series_context_after_reduce_still_clean():
    """§6.10: running a full reduce() cycle and then calling format_series_context
    on the SAME SeriesReviewContext must still produce a reducer-clean prompt block.
    This verifies that reduce() does not mutate SeriesReviewContext."""
    reducer = SeriesReducer()
    ctx = _ctx()
    comments = _comments()

    reducer.reduce(
        patch_id="p3",
        comments=comments,
        series_ctx=ctx,
        mode="on",
    )

    # After reduce(), ctx must be unchanged and the prompt must be clean.
    for patch_id in ["p1", "p2", "p3", "p4"]:
        rendered = format_series_context(ctx, patch_id)
        for marker in _REDUCER_MARKERS:
            assert marker not in rendered, (
                f"Reducer artefact {marker!r} found in prompt after reduce() — "
                f"SeriesReviewContext was mutated by the reducer."
            )


def test_series_context_block_does_not_include_inline_comment_fields():
    """The series-context prompt block is built from SeriesReviewContext alone,
    not from InlineComment or ReducerResult. Verify that InlineComment field
    names that carry reducer data do not appear in the rendered output."""
    ctx = _ctx()
    rendered = format_series_context(ctx, "p2")

    # InlineComment fields that carry reducer output — none should appear as
    # template variables in the prompt block.
    ic_reducer_fields = ("series_prefix", "series_provenance", "upstream_comment")
    for field in ic_reducer_fields:
        assert field not in rendered, (
            f"InlineComment field {field!r} found in format_series_context output — "
            "reducer data is leaking into the prompt template."
        )
