"""Track-B.9 Calibration Triples tests.

B9-1  test_B9_apply_status_closure_no_unknown
B9-2  test_B9_review_history_factor_non_constant_across_claims
B9-3  test_B9_factor_contributions_review_history_positive
B9-4  test_B9_pearson_none_when_llm_confidence_constant
B9-5  test_B9_series_count_from_production_store
B9-6  test_B9_gate_criteria_status_has_all_required_keys
B9-7  test_B9_apply_status_updated_in_index_jsonl
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.learning.calibration import CFMCalibrator
from kri.learning.models import CFMCalibrationReport, ReviewHistoryEntry
from kri.learning.store import ReviewHistoryStore
from kri.common.models import Provenance

# ---------------------------------------------------------------------------
# Absolute paths — safe regardless of cwd when running pytest
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/kri")
_INDEX_JSONL = _PROJECT_ROOT / ".kri" / "lore_review_dataset" / "index.jsonl"
_PROD_STORE = _PROJECT_ROOT / ".kri" / "ledger" / "review_history.jsonl"

# ---------------------------------------------------------------------------
# Helpers (re-exported pattern from test_track_b6_calibration.py)
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
# B9-1: apply_status closure — index.jsonl must have no UNKNOWN entries
# ---------------------------------------------------------------------------


def test_B9_apply_status_closure_no_unknown() -> None:
    """After B9 apply-status closure, no entry in index.jsonl may have
    apply_status == 'UNKNOWN'.

    This test reads the production dataset index and asserts the closure
    invariant is satisfied: all entries have a resolved apply_status.
    """
    if not _INDEX_JSONL.exists():
        pytest.skip(f"index.jsonl not found at {_INDEX_JSONL}")

    unknown_entries = []
    with _INDEX_JSONL.open() as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            record = json.loads(raw)
            status = record.get("apply_status", "MISSING")
            if status == "UNKNOWN":
                unknown_entries.append((lineno, record.get("series_id", "<no-id>")))

    assert unknown_entries == [], (
        f"Found {len(unknown_entries)} entries with apply_status='UNKNOWN' "
        f"after B9 closure; expected 0. Offending entries: {unknown_entries[:5]}"
    )


# ---------------------------------------------------------------------------
# B9-2: REVIEW_HISTORY factor is non-constant across claim categories
# ---------------------------------------------------------------------------


def test_B9_review_history_factor_non_constant_across_claims() -> None:
    """review_history_distribution must vary by claim: 'dai' (3 entries)
    must score higher than 'locking' (1 entry); 'style' (0 entries) must be
    absent or zero in the distribution.
    """
    entries = (
        [_entry(f"s{i}", f"dai-{i}@t", "dai", text=f"dai comment {i}") for i in range(3)]
        + [_entry("s10", "lock-1@t", "locking", text="locking comment")]
    )
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    llm_comments = [
        ("cmt-b9-dai", 0.8, "dai"),
        ("cmt-b9-lock", 0.5, "locking"),
        ("cmt-b9-style", 0.3, "style"),
    ]
    report = calibrator.calibrate(llm_comments)

    dist = report.review_history_distribution
    assert isinstance(dist, dict), "review_history_distribution must be a dict"

    dai_score = dist.get("dai", 0.0)
    locking_score = dist.get("locking", 0.0)

    assert dai_score > locking_score, (
        f"Expected dai ({dai_score}) > locking ({locking_score}) "
        "since dai has 3 entries and locking has 1 entry"
    )

    style_score = dist.get("style", 0.0)
    assert style_score == 0.0, (
        f"Expected style score == 0.0 (no entries), got {style_score}"
    )


# ---------------------------------------------------------------------------
# B9-3: calibration triple path — factor_contributions["review_history"] > 0
# ---------------------------------------------------------------------------


def test_B9_factor_contributions_review_history_positive() -> None:
    """With 5 'maintainer_ack' review_discussion entries in the store and two
    triples for that claim category, factor_contributions['review_history']
    must be > 0.0 after calibration.

    Each review_discussion entry is verified and contributes 0.35 to the
    REVIEW_HISTORY factor (min-capped at 1.0), so 5 entries give 1.0.
    """
    entries = [
        _entry(f"sm{i}", f"mack-{i}@t", "maintainer_ack", text=f"ack comment {i}")
        for i in range(5)
    ]
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    report = calibrator.calibrate([
        ("cmt-b9-ack1", 0.8, "maintainer_ack"),
        ("cmt-b9-ack2", 0.4, "maintainer_ack"),
    ])

    rh = report.factor_contributions.get("review_history", 0.0)
    assert rh > 0.0, (
        f"factor_contributions['review_history'] must be > 0.0 when store has "
        f"5 verified maintainer_ack entries; got {rh}"
    )


# ---------------------------------------------------------------------------
# B9-4: Pearson is None when LLM confidence has zero variance
# ---------------------------------------------------------------------------


def test_B9_pearson_none_when_llm_confidence_constant() -> None:
    """When all LLM confidence values are identical (constant y series),
    _pearson must return None (denom_y < 1e-10) and the report fields
    cfm_vs_llm_correlation and pearson_t_stat must both be None.
    """
    entries = [_entry(f"sp{i}", f"pm{i}@t", "dai", text=f"pc {i}") for i in range(5)]
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    # All LLM confidences identical (0.6) → zero variance → Pearson = None
    triples = [(f"cmt-const-{i}", 0.6, "dai") for i in range(5)]
    report = calibrator.calibrate(triples)

    assert report.cfm_vs_llm_correlation is None, (
        f"cfm_vs_llm_correlation must be None when all LLM scores are constant 0.6; "
        f"got {report.cfm_vs_llm_correlation}"
    )
    assert report.pearson_t_stat is None, (
        f"pearson_t_stat must be None when correlation is None; "
        f"got {report.pearson_t_stat}"
    )


# ---------------------------------------------------------------------------
# B9-5: series_count from production store
# ---------------------------------------------------------------------------


def test_B9_series_count_from_production_store() -> None:
    """When the production ReviewHistoryStore is present, calibrate() must
    report series_count > 0 and entry_count >= 100.

    Skips if the production store file is absent.
    """
    if not _PROD_STORE.exists():
        pytest.skip(f"production store not found at {_PROD_STORE}")

    store = ReviewHistoryStore(path=_PROD_STORE)
    if store.count() == 0:
        pytest.skip("production store is empty")

    calibrator = _make_calibrator(store)
    # One minimal triple to get a non-empty calibration report
    report = calibrator.calibrate([("cmt-prod-probe", 0.5)])

    assert report.series_count > 0, (
        f"series_count must be > 0 for production store; got {report.series_count}"
    )
    assert report.entry_count >= 100, (
        f"entry_count must be >= 100 for production store (expected ~420); "
        f"got {report.entry_count}"
    )


# ---------------------------------------------------------------------------
# B9-6: gate_criteria_status has all required keys after B9 fix
# ---------------------------------------------------------------------------

_REQUIRED_GATE_KEYS = {
    "ge_20_series_ingested",
    "cfm_scores_for_10_comments",
    "correlation_computed",
    "correlation_non_negative",
    "fp_estimate_acceptable",
    "no_safety_floor_violation",
    "browser_api_cli_validated",
    "correlation_min_samples_met",
    "correlation_significant",
}


def test_B9_gate_criteria_status_has_all_required_keys() -> None:
    """gate_criteria_status must contain all 9 required keys (including the 2
    added in Track-B.7 D3: correlation_min_samples_met, correlation_significant)
    for both the empty-input path and the non-empty calibration path.
    """
    entries = [_entry(f"sg{i}", f"gk{i}@t", "dai", text=f"gate {i}") for i in range(3)]
    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    # Non-empty path
    report_ne = calibrator.calibrate([("cmt-gate-1", 0.7, "dai")])
    missing_ne = _REQUIRED_GATE_KEYS - set(report_ne.gate_criteria_status.keys())
    assert missing_ne == set(), (
        f"gate_criteria_status missing keys (non-empty path): {missing_ne}"
    )

    # Empty path (fast-exit)
    report_empty = calibrator.calibrate([])
    missing_empty = _REQUIRED_GATE_KEYS - set(report_empty.gate_criteria_status.keys())
    assert missing_empty == set(), (
        f"gate_criteria_status missing keys (empty path): {missing_empty}"
    )


# ---------------------------------------------------------------------------
# B9-7: apply_status updated in index.jsonl (integration)
# ---------------------------------------------------------------------------


def test_B9_apply_status_updated_in_index_jsonl() -> None:
    """Integration check: the production index.jsonl must have:
    - UNKNOWN count == 0
    - APPLY_CLEAN count >= 20 (combined across all ingest tracks)

    Skips if index.jsonl is absent.
    """
    if not _INDEX_JSONL.exists():
        pytest.skip(f"index.jsonl not found at {_INDEX_JSONL}")

    counts: dict[str, int] = {}
    total = 0
    with _INDEX_JSONL.open() as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            record = json.loads(raw)
            status = record.get("apply_status", "MISSING")
            counts[status] = counts.get(status, 0) + 1
            total += 1

    assert total > 0, "index.jsonl is empty — expected at least one entry"

    unknown_count = counts.get("UNKNOWN", 0)
    assert unknown_count == 0, (
        f"Expected apply_status='UNKNOWN' count == 0 after B9 closure; "
        f"got {unknown_count} (all counts: {counts})"
    )

    apply_clean_count = counts.get("APPLY_CLEAN", 0)
    assert apply_clean_count >= 20, (
        f"Expected apply_status='APPLY_CLEAN' count >= 20; "
        f"got {apply_clean_count} (all counts: {counts})"
    )
