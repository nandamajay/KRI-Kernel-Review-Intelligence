"""WP-S1B Step B14 — R5/R6/R7 exercised through IntelligentReviewEngine.

These tests verify that R5, R6, and R7 produce actions when enabled via
explicit opt-in through the engine constructor using mode='shadow' (audit
trail without mutation), and that mode='off' with the new True defaults
produces zero actions (byte-identity invariant preserved).

Defaults as of Task #87: series_r5_enabled=True, series_r6_enabled=True,
series_r7_enabled=True.  All three rules are exercised here via explicit
opts to document the expected shadow behaviour.

Rule-body unit tests live in test_series_reducer_b7_r5_r6_r7.py.
These tests verify the engine integration path only.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

from kri.common.models import Patch, PatchSeries
from kri.llm.reviewer import IntelligentReviewEngine


# ---------------------------------------------------------------------------
# Shared fixtures (adapted from B3)
# ---------------------------------------------------------------------------

TRIVIAL_C_DIFF = (
    "diff --git a/drivers/x/foo.c b/drivers/x/foo.c\n"
    "index 000..111 100644\n"
    "--- a/drivers/x/foo.c\n"
    "+++ b/drivers/x/foo.c\n"
    "@@ -10,3 +10,4 @@ static int foo(void)\n"
    " \tint a;\n"
    "+\tint c;\n"
    " \treturn a + b;\n"
)

# Diff covering lines 284 and 292 inside sc8280xp_snd_exit — needed so
# _extract_fn returns a non-None function name for both R5 comment lines.
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


def _patch(pid: str, seq: int, total: int, diff: str = TRIVIAL_C_DIFF,
           files: list[str] | None = None) -> Patch:
    return Patch(
        patch_id=pid,
        subject=f"[PATCH {seq}/{total}] {pid}",
        commit_message="",
        files_changed=files or ["drivers/x/foo.c"],
        diff=diff,
        sequence=seq,
        series_total=total,
    )


def _series(patches: list[Patch]) -> PatchSeries:
    return PatchSeries(
        series_id="series-b14",
        title="B14 default flags series",
        cover_letter="",
        patches=patches,
    )


def _fake_client(payloads: list) -> MagicMock:
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    it = iter(payloads)

    def _pop(*a, **kw):
        try:
            return next(it)
        except StopIteration:
            return []

    client.complete_json.side_effect = _pop
    resp = MagicMock()
    resp.content = "ok"
    client.complete.return_value = resp
    return client


def _two_patch_payload_generic() -> list:
    """Generic 2-patch payload with non-triggering comments."""
    return [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [{"file_path": "drivers/x/foo.c", "line_number": 13,
          "category": "convention", "severity": "info", "message": "m",
          "confidence": 0.6}],
        [],
        {"what_it_does": "s2", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [{"file_path": "drivers/x/foo.c", "line_number": 14,
          "category": "style", "severity": "info", "message": "m2",
          "confidence": 0.6}],
        [],
    ]


# ---------------------------------------------------------------------------
# TB14-R5: function-scope merge fires through engine in shadow mode
# ---------------------------------------------------------------------------


def test_TB14_r5_fires_function_scope_merge_via_engine_shadow():
    """R5 must produce at least one function_scope_merge action when
    series_r5_enabled=True (default) and mode='shadow', given two comments
    at different R4 buckets but the same function in the diff."""
    p1 = _patch(
        "p1", 1, 2,
        diff=_SC8280XP_DIFF,
        files=["sound/soc/qcom/sc8280xp.c"],
    )
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])

    # Two comments at lines 284 (bucket 28) and 292 (bucket 29) — different R4
    # buckets so R4 cannot merge them.  Both inside sc8280xp_snd_exit per the
    # diff hunk header.  Overlapping "null pointer check card data" vocabulary
    # (≥3 shared tokens of ≥4 chars) and Jaccard ≥ 0.35.
    r5_comments = [
        {
            "file_path": "sound/soc/qcom/sc8280xp.c",
            "line_number": 284,
            "category": "convention",
            "severity": "info",
            "message": "null pointer check card data missing before dereference",
            "confidence": 0.8,
        },
        {
            "file_path": "sound/soc/qcom/sc8280xp.c",
            "line_number": 292,
            "category": "convention",
            "severity": "info",
            "message": "null pointer card data check should happen earlier here",
            "confidence": 0.6,
        },
    ]
    payload = [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        r5_comments,
        [],
        {"what_it_does": "s2", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [],
        [],
    ]
    client = _fake_client(payload)
    engine = IntelligentReviewEngine(
        client=client,
        series_reducer_mode="shadow",
        # r5 enabled via default (True) — no explicit kwarg
    )
    report = engine.review(series)

    all_actions = [
        a
        for pr in report.patches
        for a in (pr.metadata or {}).get("series_reducer_actions", [])
    ]
    r5_actions = [a for a in all_actions if a["kind"] == "function_scope_merge"]
    assert len(r5_actions) >= 1, (
        f"R5 must produce at least one function_scope_merge action; got actions: {all_actions}"
    )
    # Shadow mode: comments must not be dropped
    p1_review = next(pr for pr in report.patches if pr.patch_id == "p1")
    assert len(p1_review.inline_comments) == 2, (
        "shadow mode must not drop comments"
    )


# ---------------------------------------------------------------------------
# TB14-R6: low-signal suppression fires through engine in shadow mode
# ---------------------------------------------------------------------------


def test_TB14_r6_fires_low_signal_suppress_via_engine_shadow():
    """R6 must produce at least one low_signal_suppress action when
    series_r6_enabled=True (default) and mode='shadow', given a comment
    with confidence < 0.55 in a suppressible category."""
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])

    payload = [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [
            {
                "file_path": "drivers/x/foo.c",
                "line_number": 13,
                "category": "nit",
                "severity": "info",
                "message": "minor indentation nit about alignment here",
                "confidence": 0.40,  # < 0.55 threshold → R6 fires
            }
        ],
        [],
        {"what_it_does": "s2", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [],
        [],
    ]
    client = _fake_client(payload)
    engine = IntelligentReviewEngine(
        client=client,
        series_reducer_mode="shadow",
        # r6 enabled via default (True) — no explicit kwarg
    )
    report = engine.review(series)

    all_actions = [
        a
        for pr in report.patches
        for a in (pr.metadata or {}).get("series_reducer_actions", [])
    ]
    r6_actions = [a for a in all_actions if a["kind"] == "low_signal_suppress"]
    assert len(r6_actions) >= 1, (
        f"R6 must produce at least one low_signal_suppress action; got: {all_actions}"
    )
    # Shadow mode: comment must survive
    p1_review = next(pr for pr in report.patches if pr.patch_id == "p1")
    assert len(p1_review.inline_comments) >= 1, (
        "shadow mode must not drop comments"
    )


# ---------------------------------------------------------------------------
# TB14-R7: pre-existing suppression fires through engine in shadow mode
# ---------------------------------------------------------------------------


def test_TB14_r7_fires_pre_existing_suppress_via_engine_shadow():
    """R7 must produce at least one pre_existing_suppress action when
    series_r7_enabled=True (default) and mode='shadow', given a comment
    containing a trigger phrase with severity info."""
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])

    payload = [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [
            {
                "file_path": "drivers/x/foo.c",
                "line_number": 13,
                "category": "convention",
                "severity": "info",
                "message": "This issue is pre-existing and predates the current patch",
                "confidence": 0.6,
            }
        ],
        [],
        {"what_it_does": "s2", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [],
        [],
    ]
    client = _fake_client(payload)
    engine = IntelligentReviewEngine(
        client=client,
        series_reducer_mode="shadow",
        # r7 enabled via default (True) — no explicit kwarg
    )
    report = engine.review(series)

    all_actions = [
        a
        for pr in report.patches
        for a in (pr.metadata or {}).get("series_reducer_actions", [])
    ]
    r7_actions = [a for a in all_actions if a["kind"] == "pre_existing_suppress"]
    assert len(r7_actions) >= 1, (
        f"R7 must produce at least one pre_existing_suppress action; got: {all_actions}"
    )
    # Shadow mode: comment must survive
    p1_review = next(pr for pr in report.patches if pr.patch_id == "p1")
    assert len(p1_review.inline_comments) >= 1, (
        "shadow mode must not drop comments"
    )


# ---------------------------------------------------------------------------
# TB14-OFF: mode='off' with True defaults produces zero actions
# ---------------------------------------------------------------------------


def test_TB14_mode_off_with_true_defaults_produces_zero_actions():
    """mode='off' must produce zero reducer actions regardless of the True
    defaults for r5/r6/r7.  The reducer short-circuits before flags are
    consulted, so byte-identity is preserved."""
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])
    client = _fake_client(copy.deepcopy(_two_patch_payload_generic()))
    engine = IntelligentReviewEngine(
        client=client,
        series_reducer_mode="off",
        # r5/r6/r7 take True defaults — must be irrelevant under mode='off'
    )
    report = engine.review(series)

    all_actions = [
        a
        for pr in report.patches
        for a in (pr.metadata or {}).get("series_reducer_actions", [])
    ]
    assert all_actions == [], (
        "mode='off' must produce zero reducer actions regardless of flag defaults"
    )
    for pr in report.patches:
        assert "reducer_diagnostics" not in (pr.metadata or {}), (
            "mode='off' must not emit reducer_diagnostics"
        )
