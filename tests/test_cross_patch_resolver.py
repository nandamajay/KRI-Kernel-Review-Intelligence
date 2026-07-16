"""WP-9.1a Sub-commit 3: cross-patch resolver + bisectability check.

The resolver classifies a missing-symbol/file/binding evidence reference
against the SeriesContext accumulator: if the symbol is introduced by an
earlier patch in the same series, the "missing" finding is a false positive
(kernel patch series routinely split one change across N patches). If the
symbol is only introduced by a *later* patch, that is a genuine
bisectability bug (git bisect will land on a broken intermediate commit).
"""

from __future__ import annotations

from kri.common.models import (
    ConfidenceLevel,
    ConfidenceScore,
    Decision,
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    Patch,
    PatchSeries,
    Provenance,
    ReasoningLayer,
)
from kri.evidence_engine.cross_patch_resolver import check_bisectability
from kri.evidence_engine.engine import EvidenceEngineImpl
from kri.knowledge_manager.manager import KnowledgeManagerImpl
from kri.review_engine.series_context import build_series_context

_PATCH_1_INTRODUCES_DIFF = """\
diff --git a/drivers/x/a.c b/drivers/x/a.c
new file mode 100644
index 000000000000..1111111
--- /dev/null
+++ b/drivers/x/a.c
@@ -0,0 +1,3 @@
+int helper_foo(void)
+{
+	return 0;
+}
"""

_PATCH_2_USES_DIFF = """\
diff --git a/drivers/x/b.c b/drivers/x/b.c
index 2222222..3333333 100644
--- a/drivers/x/b.c
+++ b/drivers/x/b.c
@@ -1,2 +1,3 @@
 int existing(void)
 {
+	helper_foo();
 	return 0;
 }
"""


def _build_two_patch_series(*, introduce_first: bool) -> PatchSeries:
    """2-patch series. If ``introduce_first``, patch 1 introduces helper_foo
    and patch 2 calls it (the correct order). Otherwise patch 1 calls it and
    patch 2 introduces it (a bisectability bug)."""
    if introduce_first:
        diffs = [_PATCH_1_INTRODUCES_DIFF, _PATCH_2_USES_DIFF]
    else:
        diffs = [_PATCH_2_USES_DIFF, _PATCH_1_INTRODUCES_DIFF]

    patches = [
        Patch(
            patch_id=f"p-{seq}",
            subject=f"patch {seq}",
            sequence=seq,
            series_total=2,
            diff=diff,
        )
        for seq, diff in enumerate(diffs, start=1)
    ]
    return PatchSeries(series_id="s-resolver-test", patches=patches)


def test_missing_symbol_suppressed_when_introduced_by_earlier_patch() -> None:
    series = _build_two_patch_series(introduce_first=True)
    ctx = build_series_context(series)

    km = KnowledgeManagerImpl()
    engine = EvidenceEngineImpl(km)

    ev = Evidence(
        evidence_id="ev-missing-1",
        source_type=EvidenceSourceType.MISSING_SYMBOL,
        summary="helper_foo appears undefined in this patch",
        provenance=Provenance(),
        symbol_ref="helper_foo",
        patch_sequence=2,
    )
    verified_ev = engine.verify(ev, series_context=ctx)

    assert verified_ev.verified is False
    assert verified_ev.dropped_reason == "satisfied_by_earlier_patch_in_series"

    decision = Decision(
        decision_id="d-missing-1",
        series_id=series.series_id,
        patch_id="p-2",
        layer=ReasoningLayer.STRUCTURAL,
        statement="helper_foo appears undefined",
    )
    decision = decision.model_copy(
        update={
            "evidence_graph": EvidenceGraph(
                comment_id=decision.decision_id, evidence=[verified_ev]
            ),
            "confidence": ConfidenceScore(score=0.5, level=ConfidenceLevel.SPECULATIVE),
        }
    )

    assert decision.is_publishable() is False



def test_bisectability_violation_flagged_when_symbol_introduced_after_use() -> None:
    series = _build_two_patch_series(introduce_first=False)
    ctx = build_series_context(series)

    violations = check_bisectability(ctx)

    assert len(violations) == 1
    violation = violations[0]
    assert violation.patch_sequence == 1
    assert violation.symbol == "helper_foo"
    assert violation.introduced_at_sequence == 2
