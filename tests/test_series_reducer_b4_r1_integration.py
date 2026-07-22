"""WP-S1B Step B4 — end-to-end integration: R1 actions surface via metadata.

Adversarial finding 4 (from the B4 review) called out a gap: every R1
unit test drives ``SeriesReducer.reduce`` directly, but nothing proves
R1 actions actually reach ``PatchReview.metadata['series_reducer_actions']``
via the real engine.

This test wires the whole chain: real ``SeriesReducer``, real
``IntelligentReviewEngine``, real payload flow through the three-agent
mock — a 2-patch series where p2's diff declares a compatible and p1's
code-quality agent emits an R1-triggering comment.  Passes iff:

  1. p1's reducer_result.actions contains the R1 suppression, AND
  2. that action is serialised into ``pr.metadata['series_reducer_actions']``
     via the B3 audit surface.

If someone deletes the audit-surface wire-up, or ships a change that
drops R1 actions before the engine assembles metadata, this test fails.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

from kri.common.models import Patch, PatchSeries
from kri.llm.reviewer import IntelligentReviewEngine


# p2 introduces a DT binding YAML that declares the compatible
# ``foo,bar-sndcard`` — this makes it enter
# ``series_ctx.declared_symbols.compatibles`` at build time.
P2_BINDING_DIFF = (
    "diff --git a/Documentation/devicetree/bindings/sound/foo,bar-sndcard.yaml"
    " b/Documentation/devicetree/bindings/sound/foo,bar-sndcard.yaml\n"
    "new file mode 100644\n"
    "index 000..111\n"
    "--- /dev/null\n"
    "+++ b/Documentation/devicetree/bindings/sound/foo,bar-sndcard.yaml\n"
    "@@ -0,0 +1,5 @@\n"
    "+compatible:\n"
    "+  enum:\n"
    "+    - foo,bar-sndcard\n"
)

# p1 touches an unrelated driver — the review payload for p1 will
# include a comment that fires R1 against p2's declaration.
P1_DRIVER_DIFF = (
    "diff --git a/drivers/sound/foo.c b/drivers/sound/foo.c\n"
    "index 000..222 100644\n"
    "--- a/drivers/sound/foo.c\n"
    "+++ b/drivers/sound/foo.c\n"
    "@@ -10,3 +10,4 @@ static int foo(void)\n"
    " \tint a;\n"
    "+\tint c;\n"
    " \treturn a;\n"
)


def _series() -> PatchSeries:
    p1 = Patch(
        patch_id="p1",
        subject="[PATCH 1/2] drivers/sound: prep",
        commit_message="",
        files_changed=["drivers/sound/foo.c"],
        diff=P1_DRIVER_DIFF,
        sequence=1,
        series_total=2,
    )
    p2 = Patch(
        patch_id="p2",
        subject="[PATCH 2/2] dt-bindings: sound: add foo,bar-sndcard",
        commit_message="",
        files_changed=[
            "Documentation/devicetree/bindings/sound/foo,bar-sndcard.yaml"
        ],
        diff=P2_BINDING_DIFF,
        sequence=2,
        series_total=2,
    )
    return PatchSeries(
        series_id="series-b4-r1-int-1",
        title="R1 integration series",
        cover_letter="",
        patches=[p1, p2],
    )


def _fake_client() -> MagicMock:
    """Emit fixed payloads: p1 gets an R1-triggering comment against
    foo,bar-sndcard.  p2 gets nothing worth noting.

    Order matters — 3 agents/patch × 2 patches = 6 payload slots. The
    order the engine consumes them is not strictly guaranteed (agents
    run concurrently per patch), so we make ALL summarizer payloads
    identical, ALL code-quality payloads identical, and ALL subsystem
    payloads identical.  The R1-triggering comment lands in every
    code-quality slot; that means it fires against BOTH patches, which
    is fine — the test asserts p1's metadata carries the R1 action, and
    that assertion holds whether p2's does too.
    """
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}

    payloads = [
        # Cycle these forever; MagicMock exhausts on StopIteration if we
        # use iter(), which is fragile with concurrent futures.
    ]

    def _pop(prompt, **kw):
        p = (prompt if isinstance(prompt, str) else str(prompt)).lower()
        if "summarizer" in p or "summarize" in p or "what_it_does" in p:
            return {"what_it_does": "prep", "why_needed": "",
                    "risk_level": "low", "test_recommendations": []}
        if "code" in p or "quality" in p or "inline_comments" in p:
            return [{
                "file_path": "drivers/sound/foo.c",
                "line_number": 13,
                "category": "documentation",
                "severity": "info",
                "message": "missing binding for foo,bar-sndcard",
                "confidence": 0.55,
            }]
        return []

    client.complete_json.side_effect = _pop
    resp = MagicMock()
    resp.content = "ok"
    client.complete.return_value = resp
    return client


# ---------------------------------------------------------------------------
# T-B4-R1-INT: R1 actions surface via PatchReview.metadata in shadow and on
# ---------------------------------------------------------------------------


def test_R1_shadow_action_surfaces_in_pr_metadata_via_engine():
    """Full end-to-end: real reducer, real engine, mode='shadow'.
    Assert p1's ``PatchReview.metadata['series_reducer_actions']``
    contains the R1 action with kind='declared_symbol_suppress' and
    related_patch_id='p2'."""
    engine = IntelligentReviewEngine(
        client=_fake_client(),
        series_reducer_mode="shadow",
    )
    report = engine.review(_series())

    p1_review = next(pr for pr in report.patches if pr.patch_id == "p1")
    surfaced = p1_review.metadata.get("series_reducer_actions")
    assert surfaced, (
        f"shadow mode with declared-symbol match did not surface any "
        f"reducer action in pr.metadata. Full metadata: {p1_review.metadata}"
    )
    r1_actions = [
        a for a in surfaced
        if a.get("kind") == "declared_symbol_suppress"
    ]
    assert r1_actions, (
        f"reducer emitted actions but none was R1: {surfaced}"
    )
    assert r1_actions[0]["related_patch_id"] == "p2", (
        "R1 misattributed which patch declared the compatible"
    )
    # Shadow: the comment was NOT dropped.
    assert any(
        "missing binding for foo,bar-sndcard" in c.message
        for c in p1_review.inline_comments
    ), "shadow mode must not mutate the comment list"


def test_R1_on_mode_drops_comment_and_still_surfaces_action():
    """Same wiring, mode='on': the R1 finding is dropped from
    ``inline_comments`` AND the action is present in metadata (mode='on'
    still records the audit trail — that is what makes shadow → on
    promotion reversible)."""
    engine = IntelligentReviewEngine(
        client=_fake_client(),
        series_reducer_mode="on",
    )
    report = engine.review(_series())

    p1_review = next(pr for pr in report.patches if pr.patch_id == "p1")
    dropped = not any(
        "missing binding for foo,bar-sndcard" in c.message
        for c in p1_review.inline_comments
    )
    assert dropped, (
        "mode='on' failed to drop the R1-matched comment from p1"
    )
    surfaced = p1_review.metadata.get("series_reducer_actions", [])
    assert any(
        a.get("kind") == "declared_symbol_suppress" for a in surfaced
    ), "mode='on' must still record the R1 action in metadata"
