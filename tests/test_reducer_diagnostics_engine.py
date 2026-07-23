"""WP-S1B — engine surface for reducer diagnostics.

The engine is the ONLY place cross-agent overlap is computable —
``_merge_comments`` collapses (file, line, category) duplicates
before the reducer sees anything. These tests lock down:

1. ``pr_metadata['reducer_diagnostics']`` is present on every non-off
   run against a multi-patch series (and absent for mode='off').
2. Cross-agent overlap counters reflect the RAW 3-agent output, not the
   post-merge collapsed list.
3. Reducer's own diagnostic counters (r1_precondition_hits, etc.) are
   merged into the same metadata dict — one place to look, one shadow
   log to scrape.

Uses a spy reducer to isolate engine behavior from rule-body drift.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

from kri.common.models import Patch, PatchSeries
from kri.llm.reviewer import IntelligentReviewEngine
from kri.series import ReducerDiagnostics, ReducerResult


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
        series_id="series-diag-1",
        title="Diagnostics engine surface series",
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


class _SpyReducer:
    """Records the ReducerResult it returns so the test can verify the
    engine surfaces its diagnostics into pr_metadata verbatim."""

    def __init__(self, diagnostics: ReducerDiagnostics | None = None) -> None:
        self.calls: list[dict] = []
        self._diag = diagnostics or ReducerDiagnostics()

    def reduce(self, patch_id, comments, series_ctx, mode, flags):
        self.calls.append({"patch_id": patch_id, "mode": mode})
        return ReducerResult(comments=comments, actions=[], diagnostics=self._diag)


def _two_patch_payload_multi_agent() -> list:
    """Payload stream for a 2-patch series × 3 agents/patch.

    Each patch: summarizer + 2 agents that both produce a finding on the
    SAME (file, line // 10) bucket — this simulates real 3-agent overlap
    which _merge_comments then collapses. The engine's overlap counter
    must see the overlap before that collapse.
    """
    p1_summary = {"what_it_does": "s1", "why_needed": "", "risk_level": "low",
                  "test_recommendations": []}
    p1_agent_a = [{"file_path": "drivers/x/foo.c", "line_number": 13,
                   "category": "convention", "severity": "info",
                   "message": "agent-a finding", "confidence": 0.6}]
    p1_agent_b = [{"file_path": "drivers/x/foo.c", "line_number": 14,
                   "category": "convention", "severity": "info",
                   "message": "agent-b finding same bucket", "confidence": 0.7}]
    p2_summary = {"what_it_does": "s2", "why_needed": "", "risk_level": "low",
                  "test_recommendations": []}
    p2_agent_a: list = []
    p2_agent_b: list = []
    return [p1_summary, p1_agent_a, p1_agent_b, p2_summary, p2_agent_a, p2_agent_b]


def _run(mode: str, spy: _SpyReducer):
    p1 = _patch("p1", 1, 2)
    p2 = _patch("p2", 2, 2)
    series = _series([p1, p2])
    client = _fake_client(copy.deepcopy(_two_patch_payload_multi_agent()))
    engine = IntelligentReviewEngine(
        client=client,
        series_reducer=spy,
        series_reducer_mode=mode,  # type: ignore[arg-type]
    )
    return engine.review(series)


# ---------------------------------------------------------------------------
# Presence / absence of the diagnostics key
# ---------------------------------------------------------------------------


def test_diagnostics_key_absent_in_off_mode():
    """mode='off' → no reducer_diagnostics key in pr_metadata. The
    off-mode contract is byte-identity with pre-WP-S1B; adding
    reducer-only metadata would violate that."""
    spy = _SpyReducer()
    report = _run(mode="off", spy=spy)
    for pr in report.patches:
        assert "reducer_diagnostics" not in pr.metadata


def test_diagnostics_key_present_in_shadow_mode():
    """mode='shadow' → key MUST be present on every patch."""
    spy = _SpyReducer()
    report = _run(mode="shadow", spy=spy)
    for pr in report.patches:
        assert "reducer_diagnostics" in pr.metadata


def test_diagnostics_key_present_in_on_mode():
    """mode='on' → key MUST be present on every patch."""
    spy = _SpyReducer()
    report = _run(mode="on", spy=spy)
    for pr in report.patches:
        assert "reducer_diagnostics" in pr.metadata


# ---------------------------------------------------------------------------
# Shape of the diagnostics dict
# ---------------------------------------------------------------------------


def test_diagnostics_dict_contains_all_expected_keys():
    """The merged dict must contain BOTH the reducer's own counters and
    the engine's cross-agent overlap counters. This is the contract
    downstream shadow-log tooling will rely on."""
    spy = _SpyReducer(diagnostics=ReducerDiagnostics(
        r1_precondition_hits=1,
        r3_precondition_hits=2,
        r4_bucket_candidates_pre_floor=3,
        r4_bucket_candidates_post_floor=0,
    ))
    report = _run(mode="shadow", spy=spy)
    for pr in report.patches:
        diag = pr.metadata["reducer_diagnostics"]
        # Reducer-derived
        assert "r1_precondition_hits" in diag
        assert "r3_precondition_hits" in diag
        assert "r4_bucket_candidates_pre_floor" in diag
        assert "r4_bucket_candidates_post_floor" in diag
        # Engine-derived
        assert "per_agent_finding_counts" in diag
        assert "total_line_buckets" in diag
        assert "cross_agent_line_bucket_count" in diag
        assert "cross_agent_line_bucket_pct" in diag


def test_diagnostics_per_agent_field_reports_two_agents():
    """KRI's engine spawns three threads but only two produce
    AgentReviewOutput (code_quality, subsystem). Overlap counters
    reflect *review-agent* overlap, so the counts string must have
    exactly 2 comma-separated numbers. Locks in the review-agent
    count so a future engine change adding a 3rd review agent
    forces an intentional test update rather than silently changing
    the meaning of the metric."""
    spy = _SpyReducer()
    report = _run(mode="shadow", spy=spy)
    for pr in report.patches:
        counts_str = pr.metadata["reducer_diagnostics"]["per_agent_finding_counts"]
        assert len(counts_str.split(",")) == 2, (
            f"expected 2 review agents, got {counts_str!r}"
        )


def test_diagnostics_reducer_counters_surface_verbatim():
    """Spy reducer returns known counter values → engine must forward
    them into pr_metadata unchanged. No arithmetic, no rounding, no
    filtering."""
    diag = ReducerDiagnostics(
        r1_precondition_hits=7,
        r3_precondition_hits=11,
        r4_bucket_candidates_pre_floor=13,
        r4_bucket_candidates_post_floor=2,
    )
    spy = _SpyReducer(diagnostics=diag)
    report = _run(mode="shadow", spy=spy)
    for pr in report.patches:
        d = pr.metadata["reducer_diagnostics"]
        assert d["r1_precondition_hits"] == 7
        assert d["r3_precondition_hits"] == 11
        assert d["r4_bucket_candidates_pre_floor"] == 13
        assert d["r4_bucket_candidates_post_floor"] == 2


# ---------------------------------------------------------------------------
# Cross-agent overlap — the counter that motivated this whole commit
# ---------------------------------------------------------------------------


def test_diagnostics_cross_agent_overlap_sees_pre_merge_collisions():
    """Two agents produce a finding in the same (file, line // 10)
    bucket. _merge_comments will collapse them by (file, line,
    category) BEFORE the reducer sees anything, but the engine's
    overlap counter runs BEFORE that collapse — it must record the
    overlap.

    Assert both the numerator (multi-agent buckets) AND the
    denominator (total buckets) so the reader isn't left guessing
    whether 100% means "1 of 1 overlap" or "100 of 100 overlap".
    This is the core Counter-finding-D signal: is review-agent
    overlap non-zero on real input? If not, R4 has no volume to
    work on."""
    spy = _SpyReducer()
    report = _run(mode="shadow", spy=spy)
    # p1's two agents both fired in file bucket line // 10 == 1
    # (line 13 // 10 == 1, line 14 // 10 == 1).
    p1 = next(pr for pr in report.patches if pr.patch_id == "p1")
    diag = p1.metadata["reducer_diagnostics"]
    assert diag["total_line_buckets"] == 1
    assert diag["cross_agent_line_bucket_count"] == 1
    assert diag["cross_agent_line_bucket_pct"] == 100.0


def test_diagnostics_per_agent_finding_counts_recorded():
    """per_agent_finding_counts is a comma-joined string of raw
    per-agent finding counts. Reads directly out of the shadow log
    without unmarshalling. p1 has 1 finding from each of 2 agents."""
    spy = _SpyReducer()
    report = _run(mode="shadow", spy=spy)
    p1 = next(pr for pr in report.patches if pr.patch_id == "p1")
    counts_str = p1.metadata["reducer_diagnostics"]["per_agent_finding_counts"]
    # Order depends on futures.as_completed → test the multiset.
    counts = sorted(int(x) for x in counts_str.split(","))
    assert counts == [1, 1]


def test_diagnostics_no_overlap_when_agents_diverge():
    """p2 has no findings from either agent → 0 buckets, 0 overlap,
    0.0 pct. Verifies the counter degrades gracefully to zero (not
    ZeroDivisionError) when there is no input population."""
    spy = _SpyReducer()
    report = _run(mode="shadow", spy=spy)
    p2 = next(pr for pr in report.patches if pr.patch_id == "p2")
    diag = p2.metadata["reducer_diagnostics"]
    assert diag["cross_agent_line_bucket_count"] == 0
    assert diag["cross_agent_line_bucket_pct"] == 0.0


def test_diagnostics_emitted_even_when_series_ctx_absent():
    """A single-patch series produces no SeriesReviewContext (reducer
    is a no-op), but shadow mode must still emit an all-zeros
    diagnostics dict so the shadow-log tooling never has to
    distinguish "no key = reducer didn't run" from "no key = single
    patch". The engine gates on mode only, not on series_ctx."""
    p = _patch("p1", 1, 1)
    series = _series([p])
    payload = [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [],
        [],
    ]
    client = _fake_client(copy.deepcopy(payload))
    engine = IntelligentReviewEngine(
        client=client,
        series_reducer=_SpyReducer(),
        series_reducer_mode="shadow",
    )
    report = engine.review(series)
    assert len(report.patches) == 1
    diag = report.patches[0].metadata.get("reducer_diagnostics")
    assert diag is not None, (
        "single-patch series in shadow mode must still emit the "
        "diagnostics dict (see reducer.py F2 fix)."
    )
    # All the reducer-derived counters are default zeros.
    assert diag["r1_precondition_hits"] == 0
    assert diag["r4_bucket_candidates_pre_floor"] == 0
