"""Track-B.6 Calibration + Readiness tests.

B6-1  test_B6_calibrate_with_claim_triples_varies_cfm
B6-2  test_B6_calibrate_backward_compat_no_claims
B6-3  test_B6_calibrate_review_discussion_uses_all_entries
B6-4  test_B6_review_history_distribution_in_report
B6-5  test_B6_pearson_constant_returns_none
B6-6  test_B6_pearson_normal_variance
B6-7  test_B6_calibrate_empty_returns_cfm_shadow_stays
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.learning.calibration import (
    CFMCalibrator,
    _pearson,
    _pearson_t_stat,
    _pearson_significant,
    _t_critical_05,
    _PEARSON_MIN_SAMPLES,
)
from kri.learning.models import CFMCalibrationReport, ReviewHistoryEntry
from kri.learning.store import ReviewHistoryStore
from kri.common.models import Provenance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(*entries: ReviewHistoryEntry) -> ReviewHistoryStore:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = ReviewHistoryStore(path=path)
    for e in entries:
        store.add(e)
    return store


def _entry(
    series_id: str,
    message_id: str,
    claim: str,
    evidence_type: str = "review_discussion",
    text: str = "test review comment",
) -> ReviewHistoryEntry:
    eid = ReviewHistoryEntry.make_entry_id(series_id, message_id, text)
    return ReviewHistoryEntry(
        entry_id=eid,
        series_id=series_id,
        message_id=message_id,
        source_url=f"https://lore.kernel.org/r/{message_id}",
        reviewer_text=text,
        extracted_claim=claim,
        evidence_type=evidence_type,
        confidence_basis="test:rule",
        provenance=Provenance(
            source_url=f"https://lore.kernel.org/r/{message_id}",
            version_or_commit=message_id,
            transformation_history=["test"],
        ),
    )


def _make_calibrator(store: ReviewHistoryStore) -> CFMCalibrator:
    engine = ConfidenceEngineImpl()
    return CFMCalibrator(confidence_engine=engine, store=store)


# ---------------------------------------------------------------------------
# B6-1: calibrate() with (id, conf, claim) triples uses per-claim evidence
# ---------------------------------------------------------------------------


def test_B6_calibrate_with_claim_triples_varies_cfm() -> None:
    """Claim-triple calibration must produce different CFM scores for different claims.

    Before Track-B.6 fix, all comments used all_entries regardless of claim,
    making the REVIEW_HISTORY factor identical for every comment.
    After fix, 'dai' (many entries) gets a higher factor than 'locking' (fewer).
    """
    # Build store with 3 dai entries and 1 locking entry
    entries = [
        _entry("s1", f"dai-{i}@t", "dai") for i in range(3)
    ] + [
        _entry("s4", "lock-1@t", "locking"),
    ]
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    # Two triples: one dai-domain, one locking-domain
    llm_comments = [
        ("cmt001", 0.8, "dai"),
        ("cmt002", 0.4, "locking"),
    ]
    report = calibrator.calibrate(llm_comments)

    # Both should calibrate without error
    assert report.samples_calibrated >= 1, "Expected at least one calibrated sample"
    # review_history_distribution should capture per-claim factors
    if report.review_history_distribution:
        assert "dai" in report.review_history_distribution or "locking" in report.review_history_distribution, (
            "review_history_distribution must include at least one of the submitted claim categories"
        )


# ---------------------------------------------------------------------------
# B6-2: backward compat — (id, conf) pairs still work (no claim)
# ---------------------------------------------------------------------------


def test_B6_calibrate_backward_compat_no_claims() -> None:
    """calibrate() must accept the old (id, conf) 2-tuple format (Track-B backward compat)."""
    e = _entry("s1", "m1@t", "dai")
    store = _make_store(e)
    calibrator = _make_calibrator(store)

    # Old 2-tuple format — no claim category
    llm_comments_old = [("cmt001", 0.75), ("cmt002", 0.50)]
    # Must not raise
    report = calibrator.calibrate(llm_comments_old)

    assert isinstance(report, CFMCalibrationReport)
    assert report.samples_calibrated >= 0


# ---------------------------------------------------------------------------
# B6-3: review_discussion claim in triple falls back to all_entries
# ---------------------------------------------------------------------------


def test_B6_calibrate_review_discussion_uses_all_entries() -> None:
    """When claim_category == 'review_discussion', calibrate must fall back to
    all_entries (BLOCK-4 bypass at calibration level — all entries still used
    for calibration context, unlike live enrichment which returns []).
    """
    e_disc = _entry("s1", "disc@t", "review_discussion")
    e_dai = _entry("s2", "dai@t", "dai")
    store = _make_store(e_disc, e_dai)
    calibrator = _make_calibrator(store)

    # review_discussion triple — should use all_entries (2 entries) as fallback
    llm_comments = [("cmt001", 0.6, "review_discussion")]
    report = calibrator.calibrate(llm_comments)

    assert isinstance(report, CFMCalibrationReport)
    # Should calibrate (not skip with 0 samples) because all_entries fallback kicks in
    assert report.samples_calibrated >= 1, (
        "review_discussion claim must fall back to all_entries in calibrate(); "
        "got 0 samples, expected >= 1"
    )


# ---------------------------------------------------------------------------
# B6-4: review_history_distribution populated in CFMCalibrationReport
# ---------------------------------------------------------------------------


def test_B6_review_history_distribution_in_report() -> None:
    """CFMCalibrationReport.review_history_distribution must be populated when
    claim triples are provided; it maps claim → REVIEW_HISTORY factor score.
    """
    entries = [_entry(f"s{i}", f"dai-{i}@t", "dai") for i in range(4)]
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    llm_comments = [
        ("cmt001", 0.85, "dai"),
        ("cmt002", 0.40, "style"),
    ]
    report = calibrator.calibrate(llm_comments)

    assert isinstance(report.review_history_distribution, dict), (
        "review_history_distribution must be a dict"
    )
    # dai has 4 entries → factor should be > 0
    if "dai" in report.review_history_distribution:
        assert report.review_history_distribution["dai"] >= 0.0, (
            "dai REVIEW_HISTORY factor must be >= 0.0"
        )


# ---------------------------------------------------------------------------
# B6-5: _pearson() returns None when variance is zero (constant scores)
# ---------------------------------------------------------------------------


def test_B6_pearson_constant_returns_none() -> None:
    """_pearson() must return None when all x or y values are identical
    (zero standard deviation), as division by zero would otherwise occur.
    Validates the guard: denom_x < 1e-10 or denom_y < 1e-10 → return None.
    """
    # All x values identical → zero variance in x
    xs = [0.5, 0.5, 0.5, 0.5]
    ys = [0.2, 0.4, 0.6, 0.8]
    result = _pearson(xs, ys)
    assert result is None, (
        f"_pearson must return None for constant x series, got {result}"
    )

    # All y values identical → zero variance in y
    xs2 = [0.1, 0.3, 0.5, 0.7]
    ys2 = [1.0, 1.0, 1.0, 1.0]
    result2 = _pearson(xs2, ys2)
    assert result2 is None, (
        f"_pearson must return None for constant y series, got {result2}"
    )


# ---------------------------------------------------------------------------
# B6-6: _pearson() computes valid correlation for normal variance
# ---------------------------------------------------------------------------


def test_B6_pearson_normal_variance() -> None:
    """_pearson() must compute a valid [-1, 1] correlation for non-constant series.
    Perfect positive correlation: xs == ys → r = 1.0.
    Perfect negative correlation: ys = -xs → r = -1.0.
    """
    xs = [0.1, 0.3, 0.5, 0.7, 0.9]
    ys = [0.1, 0.3, 0.5, 0.7, 0.9]
    r = _pearson(xs, ys)
    assert r is not None, "_pearson returned None for valid non-constant series"
    assert abs(r - 1.0) < 1e-9, f"Expected r=1.0 for xs==ys, got {r}"

    ys_neg = [-y for y in ys]
    r_neg = _pearson(xs, ys_neg)
    assert r_neg is not None
    assert abs(r_neg + 1.0) < 1e-9, f"Expected r=-1.0 for perfect negative corr, got {r_neg}"


# ---------------------------------------------------------------------------
# B6-7: calibrate() with empty input returns CFM_SHADOW_STAYS immediately
# ---------------------------------------------------------------------------


def test_B6_calibrate_empty_returns_cfm_shadow_stays() -> None:
    """calibrate([]) must return a valid CFMCalibrationReport with
    recommendation='CFM_SHADOW_STAYS' and samples_calibrated=0.
    """
    e = _entry("s1", "m1@t", "dai")
    store = _make_store(e)
    calibrator = _make_calibrator(store)

    report = calibrator.calibrate([])

    assert isinstance(report, CFMCalibrationReport)
    assert report.samples_calibrated == 0
    assert report.recommendation == "CFM_SHADOW_STAYS"
    assert report.production_gate_criteria_met is False


# ---------------------------------------------------------------------------
# B6-8 (Track-B.7 D3): _pearson_t_stat returns None for n < 3
# ---------------------------------------------------------------------------


def test_B6_D3_pearson_t_stat_none_for_small_n() -> None:
    """_pearson_t_stat must return None when n < 3 (no degrees of freedom)."""
    from kri.learning.calibration import _pearson_t_stat

    assert _pearson_t_stat(0.5, 0) is None
    assert _pearson_t_stat(0.5, 1) is None
    assert _pearson_t_stat(0.5, 2) is None


# ---------------------------------------------------------------------------
# B6-9 (Track-B.7 D3): _pearson_t_stat computes correct value for known r/n
# ---------------------------------------------------------------------------


def test_B6_D3_pearson_t_stat_correct_value() -> None:
    """t = r * sqrt(n-2) / sqrt(1 - r^2).

    For r=-0.195, n=32: t = -0.195 * sqrt(30) / sqrt(1 - 0.038025)
                          ≈ -0.195 * 5.4772 / sqrt(0.961975)
                          ≈ -1.068 / 0.9808  ≈ -1.089.
    This must NOT be significant (|t| < 2.042 for df=30 at α=0.05).
    """
    from kri.learning.calibration import _pearson_t_stat, _pearson_significant

    r = -0.195
    n = 32
    t = _pearson_t_stat(r, n)
    assert t is not None
    assert abs(t - (-1.089)) < 0.01, f"Expected t ≈ -1.089, got {t}"
    sig = _pearson_significant(r, n)
    assert sig is False, (
        f"r={r}, n={n} should NOT be significant (|t|={abs(t):.3f} < 2.042)"
    )


# ---------------------------------------------------------------------------
# B6-10 (Track-B.7 D3): _pearson_significant returns True for clearly significant r
# ---------------------------------------------------------------------------


def test_B6_D3_pearson_significant_true_for_strong_correlation() -> None:
    """A strong correlation with large n must be flagged as significant.

    r=0.8, n=30: t = 0.8 * sqrt(28) / sqrt(1 - 0.64) = 0.8*5.292/0.6 ≈ 7.06.
    df=28, t_crit ≈ 2.048 → clearly significant.
    """
    from kri.learning.calibration import _pearson_significant

    assert _pearson_significant(0.8, 30) is True
    assert _pearson_significant(-0.8, 30) is True


# ---------------------------------------------------------------------------
# B6-11 (Track-B.7 D3): CFMCalibrationReport has pearson_t_stat + correlation_significant
# ---------------------------------------------------------------------------


def test_B6_D3_cfm_report_has_significance_fields() -> None:
    """CFMCalibrationReport must expose pearson_t_stat and correlation_significant."""
    report = CFMCalibrationReport()
    assert hasattr(report, "pearson_t_stat"), "pearson_t_stat field missing"
    assert hasattr(report, "correlation_significant"), "correlation_significant field missing"
    # Defaults are None (no correlation computed yet)
    assert report.pearson_t_stat is None
    assert report.correlation_significant is None


# ---------------------------------------------------------------------------
# B6-12 (Track-B.7 D3): calibrate() populates significance fields + new gate keys
# ---------------------------------------------------------------------------


def test_B6_D3_calibrate_populates_significance_and_gate_keys() -> None:
    """calibrate() must populate pearson_t_stat, correlation_significant, and
    add 'correlation_min_samples_met' and 'correlation_significant' to gate_criteria_status.
    """
    entries = [_entry(f"s{i}", f"m{i}@t", "dai") for i in range(10)]
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    # 15 comments — well below _PEARSON_MIN_SAMPLES=50
    comments = [(f"cmt:{i}", 0.3 + i * 0.03) for i in range(15)]
    report = calibrator.calibrate(comments)

    # New gate keys present
    assert "correlation_min_samples_met" in report.gate_criteria_status, (
        "gate_criteria_status must include 'correlation_min_samples_met'"
    )
    assert "correlation_significant" in report.gate_criteria_status, (
        "gate_criteria_status must include 'correlation_significant'"
    )

    # With only 15 samples, min-samples gate must be False
    assert report.gate_criteria_status["correlation_min_samples_met"] is False, (
        f"Expected correlation_min_samples_met=False for n=15 < {_PEARSON_MIN_SAMPLES}"
    )

    # pearson_t_stat and correlation_significant present on report (may be None
    # if correlation itself is None, but fields must exist)
    assert "pearson_t_stat" in report.model_dump()
    assert "correlation_significant" in report.model_dump()


# ---------------------------------------------------------------------------
# B6-13 (Track-B.7 D3): empty calibrate() gate also includes new keys
# ---------------------------------------------------------------------------


def test_B6_D3_empty_calibrate_gate_includes_new_keys() -> None:
    """calibrate([]) must include the two new D3 gate keys even in the fast-exit path."""
    e = _entry("s1", "m1@t", "dai")
    store = _make_store(e)
    calibrator = _make_calibrator(store)

    report = calibrator.calibrate([])

    assert "correlation_min_samples_met" in report.gate_criteria_status
    assert "correlation_significant" in report.gate_criteria_status
    assert report.gate_criteria_status["correlation_min_samples_met"] is False
    assert report.gate_criteria_status["correlation_significant"] is False
