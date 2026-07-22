"""WP-S1B Step B3 — engine flag propagation.

The engine constructor accepts four reducer-related knobs:

- ``series_reducer_mode: Literal["off","shadow","on"]``
- ``series_r5_enabled: bool``
- ``series_r6_enabled: bool``
- ``series_r7_enabled: bool``

B3 must prove those knobs reach ``SeriesReducer.reduce()`` unchanged on
every call, and that the trigger/mutation split of the dispatcher holds:
``shadow`` runs evaluators (produces ``actions``) but does not mutate the
comments list; ``on`` runs both.  In B1 all rule bodies are stubs, so the
observable effect of ``shadow``/``on`` is *empty ``actions`` + comments
list unchanged* — but the reducer is still invoked with the correct
``mode`` and ``flags`` kwargs, which is what this suite locks down.

These tests use a spy reducer to record every call; they never touch a
real rule body (there are none in B1) and never depend on any HTTP
layer.  Web-level env→engine wiring is covered in
``test_series_reducer_b3_env.py``.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

from kri.common.models import Patch, PatchSeries
from kri.llm.reviewer import IntelligentReviewEngine
from kri.series import ReducerResult


# ---------------------------------------------------------------------------
# Fixtures (parallel to B1's, but self-contained so this module is standalone)
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


def _patch(pid: str, seq: int, total: int) -> Patch:
    return Patch(
        patch_id=pid,
        subject=f"[PATCH {seq}/{total}] {pid}",
        commit_message="",
        files_changed=["drivers/x/foo.c"],
        diff=TRIVIAL_C_DIFF,
        sequence=seq,
        series_total=total,
    )


def _series(patches: list[Patch]) -> PatchSeries:
    return PatchSeries(
        series_id="series-b3-1",
        title="B3 flag propagation series",
        cover_letter="",
        patches=patches,
    )


def _fake_client(payloads: list) -> MagicMock:
    """Minimal LLM stub — mirrors the fake in the B1/B2 test modules."""
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


class _SpyReducer:
    """Records every ``reduce()`` call and returns the input unchanged.

    Used to prove ``IntelligentReviewEngine.__init__``'s kwargs reach the
    reducer on every per-patch call.  Comments list is identity-preserved
    so downstream engine assembly does not observe a reducer effect —
    keeps the invariant "flag plumbing test never depends on rule bodies".
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def reduce(self, patch_id, comments, series_ctx, mode, flags):
        self.calls.append(
            {
                "patch_id": patch_id,
                "mode": mode,
                # Snapshot: dict is mutable, tests should compare exact contents.
                "flags": dict(flags) if flags else {},
                "n_comments_in": len(comments),
            }
        )
        return ReducerResult(comments=comments, actions=[])


def _two_patch_payload() -> list:
    """Payload stream for a 2-patch series × 3 agents/patch."""
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


def _run(mode: str, flags: dict[str, bool], spy: _SpyReducer):
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])
    client = _fake_client(copy.deepcopy(_two_patch_payload()))
    engine = IntelligentReviewEngine(
        client=client,
        series_reducer=spy,
        series_reducer_mode=mode,   # type: ignore[arg-type]
        series_r5_enabled=flags.get("series_r5_enabled", False),
        series_r6_enabled=flags.get("series_r6_enabled", False),
        series_r7_enabled=flags.get("series_r7_enabled", False),
    )
    engine.review(series)


# ---------------------------------------------------------------------------
# T-B3-FLAG-PROP: every engine kwarg reaches the reducer verbatim
# ---------------------------------------------------------------------------


def test_TB3_flag_propagation_defaults_all_off():
    """Constructor defaults: mode='off', every per-rule flag False.
    Reducer must be called exactly once per patch with that exact
    combination — no drift, no rewriting of kwargs by the engine."""
    spy = _SpyReducer()
    _run(mode="off", flags={}, spy=spy)

    assert len(spy.calls) == 2, f"expected one reduce() per patch, got {len(spy.calls)}"
    for call in spy.calls:
        assert call["mode"] == "off"
        assert call["flags"] == {
            "series_r5_enabled": False,
            "series_r6_enabled": False,
            "series_r7_enabled": False,
        }


def test_TB3_flag_propagation_mode_shadow_reaches_reducer():
    """``series_reducer_mode='shadow'`` at engine level → every reducer
    call has ``mode='shadow'`` — no silent downgrade to off, no shadow-
    without-context fallback (context IS a multi-patch series here)."""
    spy = _SpyReducer()
    _run(mode="shadow", flags={}, spy=spy)

    assert len(spy.calls) == 2
    for call in spy.calls:
        assert call["mode"] == "shadow"


def test_TB3_flag_propagation_mode_on_reaches_reducer():
    """``series_reducer_mode='on'`` propagation, symmetric to shadow."""
    spy = _SpyReducer()
    _run(mode="on", flags={}, spy=spy)

    assert len(spy.calls) == 2
    for call in spy.calls:
        assert call["mode"] == "on"


def test_TB3_flag_propagation_per_rule_flags_reach_reducer():
    """Every per-rule flag is independently plumbed.  Set only R6 True and
    prove the reducer sees exactly ``{r5:False, r6:True, r7:False}`` —
    catches the class of bug where all three flags share a variable."""
    spy = _SpyReducer()
    _run(
        mode="shadow",
        flags={"series_r6_enabled": True},
        spy=spy,
    )
    for call in spy.calls:
        assert call["flags"] == {
            "series_r5_enabled": False,
            "series_r6_enabled": True,
            "series_r7_enabled": False,
        }


def test_TB3_flag_propagation_all_rules_enabled():
    """All three per-rule flags True.  Same guarantee as the single-flag
    case; also proves the engine does not mask flags when mode='on'."""
    spy = _SpyReducer()
    _run(
        mode="on",
        flags={
            "series_r5_enabled": True,
            "series_r6_enabled": True,
            "series_r7_enabled": True,
        },
        spy=spy,
    )
    for call in spy.calls:
        assert call["mode"] == "on"
        assert call["flags"] == {
            "series_r5_enabled": True,
            "series_r6_enabled": True,
            "series_r7_enabled": True,
        }


def test_TB3_flag_propagation_flags_do_not_leak_between_engines():
    """Two engines with different flag combos, run back-to-back, must
    never see each other's flag state at the reducer boundary.  Catches
    any accidental class-level or module-level mutable default."""
    spy_a = _SpyReducer()
    _run(mode="shadow",
         flags={"series_r5_enabled": True},
         spy=spy_a)
    spy_b = _SpyReducer()
    _run(mode="shadow",
         flags={"series_r7_enabled": True},
         spy=spy_b)

    for call in spy_a.calls:
        assert call["flags"]["series_r5_enabled"] is True
        assert call["flags"]["series_r7_enabled"] is False
    for call in spy_b.calls:
        assert call["flags"]["series_r5_enabled"] is False
        assert call["flags"]["series_r7_enabled"] is True


def test_TB3_flag_propagation_patch_ids_reach_reducer_in_order():
    """A secondary correctness check for the wiring: each patch's patch_id
    reaches the reducer exactly once, and both patches in the series show
    up (order is not asserted — the engine runs patches concurrently)."""
    spy = _SpyReducer()
    _run(mode="off", flags={}, spy=spy)
    seen = sorted(call["patch_id"] for call in spy.calls)
    assert seen == ["p1", "p2"]
