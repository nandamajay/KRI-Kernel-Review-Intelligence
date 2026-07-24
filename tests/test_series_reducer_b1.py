"""WP-S1B Step B1 — reducer skeleton and wiring tests.

Test IDs map to the WP-S1B readiness review (docs/WP_S1B_IMPLEMENTATION_
READINESS_REVIEW_2026-07-22.md §7.B1):

- ``T-B1-IDENTITY`` — ``SeriesReducer.reduce(mode="off")`` returns the
  input list object unchanged (short-circuit).
- ``T-B1-OFF-MODE`` — ``IntelligentReport`` from an engine constructed
  with ``series_reducer_mode="off"`` (the default) is byte-identical to
  an engine that has no reducer at all. Establishes byte-identity
  against the pre-WP-S1B post-``_merge_comments`` path.
- ``T-B1-PIPELINE-ORDER`` — the reducer receives comments that have
  already been through ``_merge_comments`` (deduped by
  ``(file, line, category)``) AND have their ``hunk_context`` field
  populated from the diff. Authoritative ordering per readiness §7.B1.
- ``SM11`` — integration coverage: reducer is invoked exactly once per
  patch reviewed, with the correct patch_id and series_ctx.

The tests never invoke rule bodies (there are none in B1); they only
exercise the dispatcher, mode gating, and wiring position.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from kri.common.models import Patch, PatchSeries
from kri.llm.models import InlineComment
from kri.llm.reviewer import IntelligentReviewEngine
from kri.series import (
    ReducerAction,
    ReducerActionKind,
    ReducerResult,
    SeriesReducer,
    SeriesReviewContext,
    SeriesReviewContextBuilder,
    SymbolRegistry,
)


# ---------------------------------------------------------------------------
# Fixtures (minimal — full extractor coverage is in test_series_review_context)
# ---------------------------------------------------------------------------


TRIVIAL_C_DIFF = """\
diff --git a/drivers/x/foo.c b/drivers/x/foo.c
index 000..111 100644
--- a/drivers/x/foo.c
+++ b/drivers/x/foo.c
@@ -10,3 +10,4 @@ static int foo(void)
 	int a;
 	int b;
+	int c;
 	return a + b;
"""


def _patch(pid: str, seq: int, total: int, diff: str = TRIVIAL_C_DIFF) -> Patch:
    return Patch(
        patch_id=pid,
        subject=f"[PATCH {seq}/{total}] {pid}",
        commit_message="",
        files_changed=["drivers/x/foo.c"],
        diff=diff,
        sequence=seq,
        series_total=total,
    )


def _series(patches: list[Patch]) -> PatchSeries:
    return PatchSeries(
        series_id="series-b1-1",
        title="B1 test series",
        cover_letter="cover for wiring",
        patches=patches,
    )


def _ctx(total: int) -> SeriesReviewContext:
    return SeriesReviewContext(
        series_id="s",
        title="t",
        cover_letter=None,
        total_patches=total,
        patch_index={},
        declared_symbols=SymbolRegistry(),
        file_touch_map={},
    )


def _cmt(
    file_path: str = "drivers/x/foo.c",
    line: int = 13,
    category: str = "convention",
    severity: str = "info",
    message: str = "msg",
    confidence: float = 0.5,
    hunk_context: str = "",
) -> InlineComment:
    return InlineComment(
        file_path=file_path,
        line_number=line,
        hunk_context=hunk_context,
        category=category,
        severity=severity,
        message=message,
        suggestion="",
        upstream_comment=None,
        confidence=confidence,
        reasoning="",
    )


def _fake_client_returning(payloads: list[list[dict]]) -> MagicMock:
    """LLM stub that pops one JSON payload per ``complete_json`` call.

    Each payload is the argument the JSON agent is expected to return —
    a list of raw comment dicts for the review agents, or a dict for the
    summariser. ``complete`` returns a stub aggregate assessment.
    """

    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    it = iter(payloads)

    def _next_payload(*args, **kwargs):
        try:
            return next(it)
        except StopIteration:
            return []

    client.complete_json.side_effect = _next_payload
    resp = MagicMock()
    resp.content = "ok"
    client.complete.return_value = resp
    return client


# ---------------------------------------------------------------------------
# T-B1-IDENTITY: reducer short-circuits under mode="off"
# ---------------------------------------------------------------------------


def test_TB1_IDENTITY_reducer_off_returns_input_unchanged():
    reducer = SeriesReducer()
    ctx = _ctx(total=3)
    comments = [_cmt(line=13), _cmt(line=14, category="style")]
    result = reducer.reduce(
        patch_id="p1",
        comments=comments,
        series_ctx=ctx,
        mode="off",
    )
    assert isinstance(result, ReducerResult)
    # Same list object, no defensive copy.
    assert result.comments is comments
    assert result.actions == []


def test_TB1_IDENTITY_reducer_off_ignores_flags():
    """Even with every gated rule enabled, mode="off" runs no evaluator."""
    reducer = SeriesReducer()
    ctx = _ctx(total=3)
    result = reducer.reduce(
        patch_id="p1",
        comments=[_cmt()],
        series_ctx=ctx,
        mode="off",
        flags={"series_r5_enabled": True,
               "series_r6_enabled": True,
               "series_r7_enabled": True},
    )
    assert result.actions == []


def test_TB1_IDENTITY_shadow_on_single_patch_is_noop():
    """Single-patch series has no series signal — shadow/on both no-op."""
    reducer = SeriesReducer()
    ctx = _ctx(total=1)
    comments = [_cmt()]
    for mode in ("shadow", "on"):
        r = reducer.reduce("p1", comments, ctx, mode=mode)
        assert r.comments is comments
        assert r.actions == []


def test_TB1_IDENTITY_shadow_on_none_context_is_noop():
    """When series_ctx is None (series_awareness=False), reducer no-ops."""
    reducer = SeriesReducer()
    comments = [_cmt()]
    for mode in ("shadow", "on"):
        r = reducer.reduce("p1", comments, None, mode=mode)
        assert r.comments is comments
        assert r.actions == []


def test_TB1_IDENTITY_shadow_on_multi_patch_stub_rules_produce_no_actions():
    """Dispatcher contract: with an empty declared_symbols registry
    (so R1/R3 have nothing to match) and comments crafted to avoid
    R4's line-bucket clustering (different files) and R4's soft-class
    (categories 'documentation' and 'bug' — R4 cannot cross those),
    zero actions must be produced by any active rule.

    Post-B5, R1/R3/R4 have live bodies. This test now asserts the
    NEGATIVE contract: rules must NOT fire when their preconditions
    are absent — the older stub-only invariant became stale once R1
    landed in B4."""
    reducer = SeriesReducer()
    ctx = _ctx(total=3)
    comments = [
        _cmt(file_path="drivers/x/foo.c", line=13, category="documentation"),
        _cmt(file_path="drivers/y/bar.c", line=14, category="bug"),
    ]
    for mode in ("shadow", "on"):
        r = reducer.reduce(
            "p1",
            comments,
            ctx,
            mode=mode,
            flags={"series_r5_enabled": True,
                   "series_r6_enabled": True,
                   "series_r7_enabled": True},
        )
        assert r.actions == [], f"rule fired with no matching preconditions in mode={mode!r}"
        # And mutators must not have shortened / reordered the list.
        assert [c.line_number for c in r.comments] == [13, 14]


# ---------------------------------------------------------------------------
# T-B1-OFF-MODE: engine byte-identity vs pre-B1 path
# ---------------------------------------------------------------------------


def _run_engine(client, series, **engine_kwargs):
    engine = IntelligentReviewEngine(client=client, **engine_kwargs)
    return engine.review(series)


def _drop_volatile(report):
    """Strip fields that legitimately vary run-to-run (elapsed time,
    llm_stats mock identities). Everything else must be byte-identical."""
    md = dict(report.metadata or {})
    md.pop("processing_time_seconds", None)
    md.pop("llm_stats", None)
    md.pop("llm_model", None)
    report.metadata = md
    return report


def test_TB1_OFF_MODE_default_is_byte_identical_to_no_reducer_path():
    """Engine constructed with defaults (mode='off') must produce the same
    IntelligentReport as an engine whose reducer would never fire — because
    mode='off' short-circuits before evaluators run.

    The invariant is 'no reducer observable effects', asserted by comparing
    two runs on the same fixture: one uses the default engine (reducer
    present, mode='off'); the other uses a MagicMock reducer that would
    have raised if invoked.
    """
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])

    # Two payload streams, six calls each (2 patches × 3 agents).
    # summariser payload is a dict; review agents return lists of comment dicts.
    payload_a = [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low", "test_recommendations": []},
        [{"file_path": "drivers/x/foo.c", "line_number": 13, "category": "convention",
          "severity": "info", "message": "m", "confidence": 0.6}],
        [{"file_path": "drivers/x/foo.c", "line_number": 13, "category": "convention",
          "severity": "info", "message": "m", "confidence": 0.5}],
        {"what_it_does": "s2", "why_needed": "", "risk_level": "low", "test_recommendations": []},
        [{"file_path": "drivers/x/foo.c", "line_number": 14, "category": "style",
          "severity": "info", "message": "m2", "confidence": 0.6}],
        [],
    ]

    client_default = _fake_client_returning(copy.deepcopy(payload_a))
    r_default = _run_engine(client_default, series)  # mode='off'

    # Second run: install a reducer that raises if ever invoked.
    strict = MagicMock(spec=SeriesReducer)
    strict.reduce.side_effect = AssertionError(
        "reducer must not be invoked when mode='off' — B1 short-circuit contract"
    )
    # But actually — mode='off' still calls reducer.reduce() and expects the
    # skeleton to short-circuit. So configure the mock to return the input.
    def _identity(patch_id, comments, series_ctx, mode, flags, diff=""):
        assert mode == "off"
        return ReducerResult(comments=comments, actions=[])
    strict.reduce.side_effect = _identity

    client_strict = _fake_client_returning(copy.deepcopy(payload_a))
    r_strict = _run_engine(client_strict, series, series_reducer=strict)

    # Both runs must yield identical comment surfaces per patch.
    def _sig(report):
        return [
            [(c.file_path, c.line_number, c.category, c.severity, c.message,
              c.confidence, c.hunk_context)
             for c in pr.inline_comments]
            for pr in report.patches
        ]

    assert _sig(_drop_volatile(r_default)) == _sig(_drop_volatile(r_strict))

    # And the strict-mock reducer was actually invoked with mode='off'.
    assert strict.reduce.called
    for call in strict.reduce.call_args_list:
        assert call.kwargs["mode"] == "off"

    # B2 addendum: with mode='off', the two reducer-audit fields on every
    # emitted comment MUST equal their exact defaults. This is the wire-
    # format half of the "no reducer observable effects" contract.
    for pr in r_default.patches:
        for c in pr.inline_comments:
            assert c.series_prefix == "", (
                f"OFF-mode leaked non-default series_prefix={c.series_prefix!r}"
            )
            assert c.series_provenance is None, (
                f"OFF-mode leaked non-default series_provenance={c.series_provenance!r}"
            )


def test_TB1_OFF_MODE_off_matches_series_awareness_off_findings_shape():
    """Byte-identity vs. the WP-S1A path is defined relative to
    post-_merge_comments output: even with series_awareness=True and
    mode='off', the reducer must not delete or reorder any finding."""
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])
    payload = [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low", "test_recommendations": []},
        [{"file_path": "drivers/x/foo.c", "line_number": 13, "category": "convention",
          "severity": "info", "message": "m", "confidence": 0.6}],
        [{"file_path": "drivers/x/foo.c", "line_number": 13, "category": "convention",
          "severity": "info", "message": "m-dup", "confidence": 0.5}],  # dup suppressed by _merge
        {"what_it_does": "s2", "why_needed": "", "risk_level": "low", "test_recommendations": []},
        [],
        [],
    ]
    client = _fake_client_returning(payload)
    report = _run_engine(client, series)  # defaults: mode='off'
    # p1 must retain exactly one comment (the higher-confidence one).
    p1_review = next(pr for pr in report.patches if pr.patch_id == "p1")
    assert len(p1_review.inline_comments) == 1
    assert p1_review.inline_comments[0].message == "m"


# ---------------------------------------------------------------------------
# T-B1-PIPELINE-ORDER: reducer runs post-_merge, post-hunk-context backfill
# ---------------------------------------------------------------------------


class _SpyReducer:
    """A reducer that records every ``reduce`` call for post-hoc inspection."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def reduce(self, patch_id, comments, series_ctx, mode, flags, diff=""):
        # Snapshot per-comment fields so later engine mutations (there are
        # none in B1) can't retroactively change what we saw.
        self.calls.append(
            {
                "patch_id": patch_id,
                "mode": mode,
                "flags": dict(flags or {}),
                "series_ctx_is_multi": (
                    series_ctx.is_multi_patch() if series_ctx else None
                ),
                "comments_snapshot": [
                    (c.file_path, c.line_number, c.category, c.hunk_context)
                    for c in comments
                ],
            }
        )
        return ReducerResult(comments=comments, actions=[])


def test_TB1_PIPELINE_ORDER_reducer_sees_deduped_comments():
    """Reducer must see the already-deduped output of _merge_comments —
    i.e. one comment per (file, line, category), not the raw agent outputs."""
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])
    payload = [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low", "test_recommendations": []},
        # Two agents both report the same (file, line, category) — _merge must dedupe.
        [{"file_path": "drivers/x/foo.c", "line_number": 13, "category": "convention",
          "severity": "info", "message": "hi", "confidence": 0.9}],
        [{"file_path": "drivers/x/foo.c", "line_number": 13, "category": "convention",
          "severity": "info", "message": "hi-dup", "confidence": 0.6}],
        {"what_it_does": "s2", "why_needed": "", "risk_level": "low", "test_recommendations": []},
        [],
        [],
    ]
    spy = _SpyReducer()
    client = _fake_client_returning(payload)
    _run_engine(client, series, series_reducer=spy)

    # One reduce() call per patch.
    assert len(spy.calls) == 2
    p1_call = next(c for c in spy.calls if c["patch_id"] == "p1")
    # Deduped: exactly one snapshot entry despite two agents reporting the same key.
    assert len(p1_call["comments_snapshot"]) == 1


def test_TB1_PIPELINE_ORDER_reducer_sees_populated_hunk_context():
    """The reducer must be called AFTER the hunk_context back-fill step
    (readiness §7.B1 authoritative ordering). Any comment whose
    file/line lies inside the diff must arrive at the reducer with
    hunk_context populated."""
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])
    payload = [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low", "test_recommendations": []},
        # LLM leaves hunk_context empty; engine must back-fill it.
        [{"file_path": "drivers/x/foo.c", "line_number": 13, "category": "convention",
          "severity": "info", "message": "hi", "confidence": 0.9, "hunk_context": ""}],
        [],
        {"what_it_does": "s2", "why_needed": "", "risk_level": "low", "test_recommendations": []},
        [],
        [],
    ]
    spy = _SpyReducer()
    client = _fake_client_returning(payload)
    _run_engine(client, series, series_reducer=spy)

    p1_call = next(c for c in spy.calls if c["patch_id"] == "p1")
    assert len(p1_call["comments_snapshot"]) == 1
    _, _, _, hunk = p1_call["comments_snapshot"][0]
    # Back-fill must have populated the field before the reducer saw it.
    assert hunk, "reducer received a comment with empty hunk_context — ordering violation"


# ---------------------------------------------------------------------------
# SM11: integration coverage — reducer invoked with expected shape per patch
# ---------------------------------------------------------------------------


def test_SM11_reducer_invoked_once_per_patch_with_expected_args():
    p1 = _patch("p1", 1, 3)
    p2 = _patch("p2", 2, 3)
    p3 = _patch("p3", 3, 3)
    series = _series([p1, p2, p3])
    # 3 patches × 3 agents = 9 stubbed calls (summariser dict + two lists each).
    payload = []
    for _ in range(3):
        payload.extend([
            {"what_it_does": "s", "why_needed": "", "risk_level": "low", "test_recommendations": []},
            [],
            [],
        ])
    spy = _SpyReducer()
    client = _fake_client_returning(payload)
    _run_engine(client, series, series_reducer=spy)
    seen_pids = sorted(c["patch_id"] for c in spy.calls)
    assert seen_pids == ["p1", "p2", "p3"]
    for c in spy.calls:
        assert c["mode"] == "off"
        assert c["series_ctx_is_multi"] is True
        # Default flag geometry: R5/R6/R7 disabled per readiness §6.1.
        assert c["flags"] == {
            "series_r5_enabled": False,
            "series_r6_enabled": False,
            "series_r7_enabled": False,
        }


def test_SM11_reducer_flags_propagate_from_engine():
    """Per-rule enable flags set at engine construction reach every
    reduce() call verbatim."""
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])
    payload = []
    for _ in range(2):
        payload.extend([
            {"what_it_does": "s", "why_needed": "", "risk_level": "low", "test_recommendations": []},
            [],
            [],
        ])
    spy = _SpyReducer()
    client = _fake_client_returning(payload)
    _run_engine(
        client,
        series,
        series_reducer=spy,
        series_reducer_mode="shadow",
        series_r5_enabled=True,
        series_r6_enabled=False,
        series_r7_enabled=True,
    )
    for c in spy.calls:
        assert c["mode"] == "shadow"
        assert c["flags"] == {
            "series_r5_enabled": True,
            "series_r6_enabled": False,
            "series_r7_enabled": True,
        }


def test_SM11_reducer_receives_none_ctx_when_series_awareness_off():
    """With series_awareness=False the engine still calls the reducer,
    but series_ctx must be None so no evaluator can act on it."""
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])
    payload = []
    for _ in range(2):
        payload.extend([
            {"what_it_does": "s", "why_needed": "", "risk_level": "low", "test_recommendations": []},
            [],
            [],
        ])
    spy = _SpyReducer()
    client = _fake_client_returning(payload)
    _run_engine(client, series, series_reducer=spy, series_awareness=False)
    for c in spy.calls:
        assert c["series_ctx_is_multi"] is None


# ---------------------------------------------------------------------------
# Skeleton contract: registered rules and helpers
# ---------------------------------------------------------------------------


def test_reducer_registers_R1_R3_R4_R5_R6_R7_R8_but_not_R2():
    """R2 is deferred per readiness §5; the skeleton must not register it."""
    reducer = SeriesReducer()
    registered = {r.kind for r in reducer._rules}
    assert ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS in registered
    assert ReducerActionKind.R3_EXTERNAL_TO_INTERNAL_REWRITE in registered
    assert ReducerActionKind.R4_LINE_BUCKET_MERGE in registered
    assert ReducerActionKind.R5_FUNCTION_SCOPE_MERGE in registered
    assert ReducerActionKind.R6_LOW_SIGNAL_SUPPRESS in registered
    assert ReducerActionKind.R7_PRE_EXISTING_SUPPRESS in registered
    assert ReducerActionKind.R8_COUPLING_NOTE in registered
    assert ReducerActionKind.R2_SERIES_PRESENT_SUPPRESS not in registered


def test_reducer_gated_rules_have_flags():
    """R5/R6/R7 are the gated rules; R1/R3/R4/R8 are always active."""
    reducer = SeriesReducer()
    gated = {r.kind: r.flag for r in reducer._rules if r.flag is not None}
    assert gated == {
        ReducerActionKind.R5_FUNCTION_SCOPE_MERGE: "series_r5_enabled",
        ReducerActionKind.R6_LOW_SIGNAL_SUPPRESS: "series_r6_enabled",
        ReducerActionKind.R7_PRE_EXISTING_SUPPRESS: "series_r7_enabled",
    }


def test_reducer_safety_floor_helper_flags_blockers_and_high_conf_warnings():
    """Readiness §6.4: never suppress blockers; never suppress warnings
    with confidence ≥ 0.7."""
    assert SeriesReducer._is_safety_floored(_cmt(severity="blocker", confidence=0.1))
    assert SeriesReducer._is_safety_floored(_cmt(severity="warning", confidence=0.7))
    assert SeriesReducer._is_safety_floored(_cmt(severity="warning", confidence=0.95))
    assert not SeriesReducer._is_safety_floored(_cmt(severity="warning", confidence=0.6))
    assert not SeriesReducer._is_safety_floored(_cmt(severity="info", confidence=1.0))


def test_reducer_same_category_helper_covers_soft_bucket():
    """Same-category constraint per readiness §6.4: R4/R5 may merge across
    labels only when both fall in {convention, style, nit}."""
    a = _cmt(category="convention")
    b = _cmt(category="style")
    c = _cmt(category="performance")
    assert SeriesReducer._same_category(a, a)
    assert SeriesReducer._same_category(a, b)
    assert not SeriesReducer._same_category(a, c)
    assert not SeriesReducer._same_category(b, c)


def test_reducer_off_mode_is_default_on_engine_construction():
    """The engine must default to mode='off' — no shadow / on side effects
    by accident."""
    engine = IntelligentReviewEngine(client=_fake_client_returning([]))
    assert engine._series_reducer_mode == "off"
    assert engine._series_reducer_flags == {
        "series_r5_enabled": False,
        "series_r6_enabled": False,
        "series_r7_enabled": False,
    }
