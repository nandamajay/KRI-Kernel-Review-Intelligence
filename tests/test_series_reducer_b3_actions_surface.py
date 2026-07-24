"""WP-S1B Step B3 — reducer actions surface into PatchReview.metadata.

Motivation: without this wire-up, ``mode='shadow'`` is unobservable at
the boundary.  The engine calls the reducer, receives
``ReducerResult(comments, actions)``, and — pre-fix — discards
``actions``.  When B4+ rule bodies begin producing actions, operators
running under shadow mode would see nothing.

These two tests lock down:

- **T-B3-ACT-OFF** — mode='off' MUST NOT emit
  ``series_reducer_actions`` in ``PatchReview.metadata``.  Preserves
  byte-identity with the pre-audit-surface path when the reducer is
  disabled.
- **T-B3-ACT-SHADOW** — a spy reducer returning synthetic actions
  surfaces them via ``pr.metadata['series_reducer_actions']``, and each
  entry is a plain dict routed through ``ReducerAction.to_metadata()``.

Two tests, no fanout — enough to prove the plumbing and to break loudly
if someone deletes the wire-up later.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

from kri.common.models import Patch, PatchSeries
from kri.llm.reviewer import IntelligentReviewEngine
from kri.series import ReducerResult
from kri.series.models import ReducerAction, ReducerActionKind


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


def _single_patch_series() -> PatchSeries:
    return PatchSeries(
        series_id="series-b3-act-1",
        title="B3 audit surface",
        cover_letter="",
        patches=[_patch("p1", 1, 1)],
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


def _single_patch_payload() -> list:
    """Payload stream for a 1-patch series × 3 agents/patch."""
    return [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [{"file_path": "drivers/x/foo.c", "line_number": 13,
          "category": "convention", "severity": "info", "message": "m",
          "confidence": 0.6}],
        [],
    ]


class _ActionEmittingReducer:
    """Stand-in reducer that returns a fixed audit-only ReducerResult.

    Used to prove the engine surfaces ``actions`` in ``PatchReview.metadata``.
    Comments pass through unchanged (audit-only, mirroring shadow mode).
    """

    def __init__(self, actions_per_patch: list[ReducerAction]) -> None:
        self._actions = actions_per_patch

    def reduce(self, patch_id, comments, series_ctx, mode, flags, diff=""):
        return ReducerResult(comments=comments, actions=list(self._actions))


# ---------------------------------------------------------------------------
# T-B3-ACT-OFF: mode='off' MUST NOT emit series_reducer_actions
# ---------------------------------------------------------------------------


def test_TB3_ACT_off_mode_omits_series_reducer_actions_key():
    """With the default reducer (mode='off' short-circuits to empty
    actions), ``series_reducer_actions`` MUST NOT appear in
    ``PatchReview.metadata`` — preserves byte-identity with pre-audit
    reviews.  A test that only checks the key is empty would silently
    pass on a bug that writes ``= []`` unconditionally; we assert the
    key is *absent*."""
    client = _fake_client(copy.deepcopy(_single_patch_payload()))
    engine = IntelligentReviewEngine(client=client)  # defaults → mode='off'
    report = engine.review(_single_patch_series())

    assert len(report.patches) == 1
    pr = report.patches[0]
    assert "series_reducer_actions" not in pr.metadata, (
        "off-mode leaked series_reducer_actions into PatchReview.metadata; "
        "the key must be absent when reducer produces no actions"
    )


# ---------------------------------------------------------------------------
# T-B3-ACT-SHADOW: reducer actions surface as list[dict] via to_metadata()
# ---------------------------------------------------------------------------


def test_TB3_ACT_reducer_actions_surface_via_to_metadata():
    """A reducer that emits synthetic actions must have those actions
    show up in ``pr.metadata['series_reducer_actions']`` as a list of
    dicts routed through :meth:`ReducerAction.to_metadata` — the shape
    downstream tooling (dashboards, replay diffs) will read."""
    action = ReducerAction(
        kind=ReducerActionKind.R1_DECLARED_SYMBOL_SUPPRESS,
        patch_id="p1",
        finding_ref="drivers/x/foo.c:13:convention",
        file="drivers/x/foo.c",
        line=13,
        reason="declared symbol test",
    )
    fake_reducer = _ActionEmittingReducer([action])

    client = _fake_client(copy.deepcopy(_single_patch_payload()))
    engine = IntelligentReviewEngine(
        client=client,
        series_reducer=fake_reducer,
        series_reducer_mode="shadow",
    )
    report = engine.review(_single_patch_series())

    pr = report.patches[0]
    assert "series_reducer_actions" in pr.metadata, (
        "shadow mode with non-empty reducer actions did not surface them"
    )
    surfaced = pr.metadata["series_reducer_actions"]
    assert isinstance(surfaced, list)
    assert len(surfaced) == 1
    # Each entry is a plain dict — the transport shape, not a dataclass.
    assert isinstance(surfaced[0], dict)
    # And byte-identical to what to_metadata() itself would return.
    assert surfaced[0] == action.to_metadata()
