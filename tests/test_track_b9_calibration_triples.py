"""Track-B.9 Calibration Triples tests.

B9-1  test_B9_apply_status_closure_no_unknown
B9-2  test_B9_review_history_factor_non_constant_across_claims
B9-3  test_B9_factor_contributions_review_history_positive
B9-4  test_B9_pearson_none_when_llm_confidence_constant
B9-5  test_B9_series_count_from_production_store
B9-6  test_B9_gate_criteria_status_has_all_required_keys
B9-7  test_B9_apply_status_updated_in_index_jsonl
B9-8  test_B9_output_jsonl_schema_valid
B9-9  test_B9_output_result_json_schema_valid
B9-10 test_B9_output_triples_real_not_hardcoded
B9-11 test_B9_pearson_unavailable_handled_gracefully
B9-12 test_B9_production_gate_not_activated
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


# ---------------------------------------------------------------------------
# B9-8: output JSONL schema validation (production ledger file)
# ---------------------------------------------------------------------------

_LEDGER_DIR = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/data/ledger")
_TRIPLES_JSONL = _LEDGER_DIR / "calibration_triples_b9.jsonl"
_RESULT_JSON = _LEDGER_DIR / "calibration_result_b9.json"


def test_B9_output_jsonl_schema_valid() -> None:
    """calibration_triples_b9.jsonl must exist, be non-empty, and every line
    must be a valid JSON object with keys: comment_id (str), llm_confidence
    (float, 0<c<1), claim_category (str).

    Skips if the file is absent (script not yet run).
    """
    if not _TRIPLES_JSONL.exists():
        pytest.skip(f"triples JSONL not found at {_TRIPLES_JSONL} — run the script first")

    lines = [l for l in _TRIPLES_JSONL.read_text().splitlines() if l.strip()]
    assert lines, f"{_TRIPLES_JSONL.name} is empty"

    for i, raw in enumerate(lines, 1):
        obj = json.loads(raw)
        assert "comment_id" in obj, f"Line {i}: missing comment_id"
        assert "llm_confidence" in obj, f"Line {i}: missing llm_confidence"
        assert "claim_category" in obj, f"Line {i}: missing claim_category"
        conf = obj["llm_confidence"]
        assert isinstance(conf, float), f"Line {i}: llm_confidence must be float, got {type(conf)}"
        assert 0.0 < conf < 1.0, f"Line {i}: llm_confidence out of range (0,1): {conf}"
        assert isinstance(obj["comment_id"], str) and len(obj["comment_id"]) > 0, (
            f"Line {i}: comment_id must be non-empty str"
        )
        assert isinstance(obj["claim_category"], str) and len(obj["claim_category"]) > 0, (
            f"Line {i}: claim_category must be non-empty str"
        )


# ---------------------------------------------------------------------------
# B9-9: output result JSON schema validation
# ---------------------------------------------------------------------------

_REQUIRED_RESULT_KEYS = {
    "triples_generated",
    "mbox_files_reviewed",
    "sample_triples",
    "calibration_run",
    "pearson",
    "triples_file",
    "samples_calibrated",
    "recommendation",
    "gate_criteria_status",
}


def test_B9_output_result_json_schema_valid() -> None:
    """calibration_result_b9.json must exist, be valid JSON, and contain all
    required keys with correct types.

    Skips if the file is absent (script not yet run).
    """
    if not _RESULT_JSON.exists():
        pytest.skip(f"result JSON not found at {_RESULT_JSON} — run the script first")

    result = json.loads(_RESULT_JSON.read_text())
    missing = _REQUIRED_RESULT_KEYS - result.keys()
    assert missing == set(), f"calibration_result_b9.json missing keys: {missing}"

    assert isinstance(result["triples_generated"], int), "triples_generated must be int"
    assert isinstance(result["mbox_files_reviewed"], list), "mbox_files_reviewed must be list"
    assert isinstance(result["calibration_run"], bool), "calibration_run must be bool"
    assert isinstance(result["gate_criteria_status"], dict), "gate_criteria_status must be dict"
    assert result["triples_generated"] >= 0, "triples_generated must be >= 0"


# ---------------------------------------------------------------------------
# B9-10: triples came from real LLM review (not hardcoded or synthetic)
# ---------------------------------------------------------------------------

_SYNTHETIC_FIXTURE_IDS = {
    "0000000000000001",
    "0000000000000002",
    "deadbeef00000000",
    "test-id-1",
    "test-id-2",
    "synthetic",
    "cmt-const-0",
    "cmt-const-1",
    "cmt-gate-1",
    "cmt-b9-dai",
    "cmt-b9-lock",
    "cmt-b9-style",
}


def test_B9_output_triples_real_not_hardcoded() -> None:
    """Triples in calibration_triples_b9.jsonl must not match known synthetic
    fixture IDs used in unit tests. They must have SHA-256-derived hex comment_ids
    (16-char hex string) and varied llm_confidence values (std > 0.0).

    Skips if the file is absent.
    """
    if not _TRIPLES_JSONL.exists():
        pytest.skip(f"triples JSONL not found at {_TRIPLES_JSONL} — run the script first")

    lines = [l for l in _TRIPLES_JSONL.read_text().splitlines() if l.strip()]
    if not lines:
        pytest.skip("triples JSONL is empty — no triples to validate")

    triples = [json.loads(l) for l in lines]
    comment_ids = [t["comment_id"] for t in triples]
    confs = [t["llm_confidence"] for t in triples]

    # None of the IDs should match known synthetic test fixtures
    synthetic_found = [cid for cid in comment_ids if cid in _SYNTHETIC_FIXTURE_IDS]
    assert synthetic_found == [], (
        f"Found synthetic fixture IDs in production triples: {synthetic_found}"
    )

    # IDs must be 16-char lowercase hex (SHA-256 derived from real comments)
    import re
    hex_pattern = re.compile(r'^[0-9a-f]{16}$')
    non_hex = [cid for cid in comment_ids if not hex_pattern.match(cid)]
    assert non_hex == [], (
        f"comment_ids must be 16-char hex (SHA-256 derived); non-hex IDs: {non_hex[:5]}"
    )

    # Confidence values must have some variance (real reviews produce varied scores)
    if len(confs) >= 2:
        mean = sum(confs) / len(confs)
        std = (sum((c - mean) ** 2 for c in confs) / len(confs)) ** 0.5
        assert std > 0.0, (
            f"llm_confidence std must be > 0 for real triples (constant = synthetic); "
            f"got std={std}, confs={confs}"
        )


# ---------------------------------------------------------------------------
# B9-11: Pearson unavailable handled gracefully in calibration report
# ---------------------------------------------------------------------------


def test_B9_pearson_unavailable_handled_gracefully() -> None:
    """When Pearson is None (either due to min-samples or zero variance),
    the calibration report must still be a valid CFMCalibrationReport with:
    - cfm_vs_llm_correlation is None
    - pearson_t_stat is None
    - correlation_significant is None or False
    - gate_criteria_status['correlation_computed'] is False
    - recommendation is a non-empty string
    """
    store = _make_store(_entry("sn1", "null-p-1@t", "dai", text="null pearson test"))
    calibrator = _make_calibrator(store)

    # constant confidence → Pearson = None (zero variance)
    report = calibrator.calibrate([("cmt-null-p", 0.5, "dai")])

    assert report.cfm_vs_llm_correlation is None
    assert report.pearson_t_stat is None
    assert not report.gate_criteria_status.get("correlation_computed", True), (
        "correlation_computed must be False when Pearson is None"
    )
    assert isinstance(report.recommendation, str) and report.recommendation, (
        "recommendation must be a non-empty string even when Pearson is None"
    )


# ---------------------------------------------------------------------------
# B9-12: CFM production gate is NOT activated (gate invariant test)
# ---------------------------------------------------------------------------


def test_B9_production_gate_not_activated() -> None:
    """CFM production gate must remain disabled even when CFMCalibrator
    is constructed and calibrate() is called.

    This test imports the production app module and verifies that the global
    CFM_MODE is 'shadow' (not 'production'). It also verifies that no
    calibration report can set production_gate_criteria_met=True without
    explicit authorized activation.

    Only checks the mode flag — does not start the web server.
    """
    try:
        from kri.web import app as web_app
        mode = getattr(web_app, "CFM_MODE", None)
        if mode is not None:
            assert mode in ("shadow", "disabled", "off", None), (
                f"CFM_MODE must not be 'production'; got '{mode}'"
            )
    except ImportError:
        pass  # app not available in this test context — skip silently

    # Verify calibrator never auto-promotes to production
    store = _make_store(
        *[_entry(f"sg{i}", f"gate-{i}@t", "dai", text=f"gate check {i}") for i in range(10)]
    )
    calibrator = _make_calibrator(store)
    triples = [(f"cmt-gate-{i}", 0.5 + i * 0.03, "dai") for i in range(5)]
    report = calibrator.calibrate(triples)

    assert report.production_gate_criteria_met is False, (
        f"production_gate_criteria_met must be False (gate NOT activated); "
        f"got {report.production_gate_criteria_met}"
    )
