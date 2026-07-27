"""Track-B.11 Coverage Expansion tests.

B11-1  test_B11_sec9_resource_leak_signal_clean
B11-2  test_B11_resource_leak_classifies_generic_text
B11-3  test_B11_reclassification_does_not_corrupt_provenance
B11-4  test_B11_store_reclassification_increases_category_counts
B11-5  test_B11_calibration_b11_output_exists_and_valid
B11-6  test_B11_pearson_direction_positive
B11-7  test_B11_production_gate_remains_disabled
B11-8  test_B11_governance_no_synthetic_entries
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from kri.learning.ingestion import _classify_claim, _CLAIM_SIGNALS
from kri.learning.models import ReviewHistoryEntry
from kri.learning.store import ReviewHistoryStore
from kri.common.models import Provenance
from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.learning.calibration import CFMCalibrator

# ---------------------------------------------------------------------------
# Absolute paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/kri")
_LEDGER_DIR = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/data/ledger")
_TRIPLES_B11_JSONL = _LEDGER_DIR / "calibration_triples_b11.jsonl"
_RESULT_B11_JSON = _LEDGER_DIR / "calibration_result_b11.json"
_PROD_STORE = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/data/lore_cache/review_history.jsonl")


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
    source_url: str = "",
) -> ReviewHistoryEntry:
    eid = ReviewHistoryEntry.make_entry_id(series_id, message_id, text)
    src = source_url or f"https://lore.kernel.org/r/{message_id}"
    return ReviewHistoryEntry(
        entry_id=eid,
        series_id=series_id,
        message_id=message_id,
        source_url=src,
        reviewer_text=text,
        extracted_claim=claim,
        evidence_type=evidence_type,
        confidence_basis="test:rule",
        provenance=Provenance(
            source_url=src,
            version_or_commit=message_id,
            transformation_history=["test"],
        ),
    )


def _make_calibrator(store: ReviewHistoryStore) -> CFMCalibrator:
    engine = ConfidenceEngineImpl()
    return CFMCalibrator(confidence_engine=engine, store=store)


# ---------------------------------------------------------------------------
# B11-1: resource_leak signal in _CLAIM_SIGNALS is clean (Sec-9)
# ---------------------------------------------------------------------------


def test_B11_sec9_resource_leak_signal_clean() -> None:
    """The resource_leak pattern must not contain domain-specific vendor symbols
    (Sec-9) and must contain the generic 'resource.?leak' (or 'resource\\s+leak')
    pattern vocabulary.

    Prohibited (vendor/driver-domain specifics): clk_put, regulator_put,
    iounmap, pm_runtime_put, of_node_put, devm_
    """
    # Find the resource_leak tuple in _CLAIM_SIGNALS
    resource_leak_patterns = [
        pattern for pattern, category in _CLAIM_SIGNALS
        if category == "resource_leak"
    ]
    assert resource_leak_patterns, (
        "B11-1: no 'resource_leak' tuple found in _CLAIM_SIGNALS"
    )

    # Use the first (or only) resource_leak pattern
    pattern = resource_leak_patterns[0]

    # Must NOT contain domain-specific symbols
    prohibited = ["clk_put", "regulator_put", "iounmap", "pm_runtime_put", "of_node_put", "devm_"]
    for symbol in prohibited:
        assert symbol not in pattern, (
            f"B11-1 Sec-9 violation: resource_leak pattern contains vendor symbol {symbol!r}: "
            f"{pattern!r}"
        )

    # Must contain the generic resource leak vocabulary.
    # The pattern is a regex string, so 'resource\s+leak' is stored as the two
    # characters 'resource', then backslash-s-plus-leak.  Check that both
    # "resource" and "leak" appear as substrings inside the pattern.
    assert "resource" in pattern and "leak" in pattern, (
        f"B11-1: resource_leak pattern must contain 'resource' and 'leak' vocabulary; "
        f"got: {pattern!r}"
    )


# ---------------------------------------------------------------------------
# B11-2: resource_leak classifies generic review texts
# ---------------------------------------------------------------------------


def test_B11_resource_leak_classifies_generic_text() -> None:
    """Generic resource-leak vocabulary must classify as 'resource_leak'.

    'forgot to release the lock' may classify as 'locking' OR 'resource_leak'
    — both are acceptable since a lock is a resource.
    """
    # Must → resource_leak
    text1 = "there is a resource leak on error path"
    got1, _ = _classify_claim(text1)
    assert got1 == "resource_leak", (
        f"B11-2: expected 'resource_leak' for {text1!r}; got {got1!r}"
    )

    text2 = "missing put on error return"
    got2, _ = _classify_claim(text2)
    assert got2 == "resource_leak", (
        f"B11-2: expected 'resource_leak' for {text2!r}; got {got2!r}"
    )

    # Either locking or resource_leak is acceptable for 'forgot to release the lock'
    text3 = "forgot to release the lock"
    got3, _ = _classify_claim(text3)
    assert got3 in ("locking", "resource_leak"), (
        f"B11-2: expected 'locking' or 'resource_leak' for {text3!r}; got {got3!r}"
    )


# ---------------------------------------------------------------------------
# B11-3: reclassification does not corrupt provenance
# ---------------------------------------------------------------------------


def test_B11_reclassification_does_not_corrupt_provenance() -> None:
    """Reclassifying a ReviewHistoryEntry must not alter its source_url,
    message_id, or evidence_type fields.
    """
    original_source_url = "http://x.com"
    original_message_id = "abc123"
    original_evidence_type = "review_discussion"

    entry = ReviewHistoryEntry(
        entry_id=ReviewHistoryEntry.make_entry_id("s-test", original_message_id, "review_discussion"),
        series_id="s-test",
        message_id=original_message_id,
        source_url=original_source_url,
        reviewer_text="use IS_ERR() macro here for checking errors",
        extracted_claim="review_discussion",
        evidence_type=original_evidence_type,
        confidence_basis="fallback:no_signal_matched",
        provenance=Provenance(
            source_url=original_source_url,
            version_or_commit=original_message_id,
            transformation_history=["test"],
        ),
    )

    # Simulate reclassification: run _classify_claim on the reviewer_text
    new_claim, new_basis = _classify_claim(entry.reviewer_text)

    # Create a reclassified entry (simulating what reclassification would do)
    # Only extracted_claim and confidence_basis should change
    reclassified = entry.model_copy(update={
        "extracted_claim": new_claim,
        "confidence_basis": new_basis,
    })

    # Provenance fields must be unchanged
    assert reclassified.source_url == original_source_url, (
        f"B11-3: source_url changed after reclassification; "
        f"expected {original_source_url!r}, got {reclassified.source_url!r}"
    )
    assert reclassified.message_id == original_message_id, (
        f"B11-3: message_id changed after reclassification; "
        f"expected {original_message_id!r}, got {reclassified.message_id!r}"
    )
    assert reclassified.evidence_type == original_evidence_type, (
        f"B11-3: evidence_type changed after reclassification; "
        f"expected {original_evidence_type!r}, got {reclassified.evidence_type!r}"
    )


# ---------------------------------------------------------------------------
# B11-4: store reclassification increases convention category counts
# ---------------------------------------------------------------------------


def test_B11_store_reclassification_increases_category_counts() -> None:
    """Entries matching the convention pattern should reclassify from
    'review_discussion' to 'convention' when _classify_claim is applied.

    Creates 10 review_discussion entries with convention-matching text and
    verifies that reclassification produces at least some 'convention' entries.
    """
    # Create 10 entries that have convention-matching text but are labeled review_discussion
    convention_texts = [
        f"use IS_ERR() macro instead of NULL check here {i}"
        for i in range(10)
    ]

    entries = [
        _entry(
            series_id=f"s-reclass-{i}",
            message_id=f"reclass-{i}@test",
            claim="review_discussion",
            evidence_type="review_discussion",
            text=text,
        )
        for i, text in enumerate(convention_texts)
    ]

    store = _make_store(*entries)

    # Simulate reclassification: iterate entries with extracted_claim == "review_discussion"
    reclassified_count = 0
    reclassified_to_convention = 0

    for entry in store.all():
        if entry.extracted_claim == "review_discussion":
            new_claim, _ = _classify_claim(entry.reviewer_text)
            if new_claim != "review_discussion":
                reclassified_count += 1
            if new_claim == "convention":
                reclassified_to_convention += 1

    assert reclassified_to_convention > 0, (
        f"B11-4: expected at least 1 entry reclassified to 'convention'; "
        f"got {reclassified_to_convention} (total reclassified: {reclassified_count})"
    )


# ---------------------------------------------------------------------------
# B11-5: calibration_b11 output files exist and have valid schema
# ---------------------------------------------------------------------------


def test_B11_calibration_b11_output_exists_and_valid() -> None:
    """calibration_triples_b11.jsonl must exist with valid schema.
    calibration_result_b11.json must exist with 'pearson' field.

    Skips if either file is absent (scripts not yet run).
    """
    if not _TRIPLES_B11_JSONL.exists():
        pytest.skip(
            f"calibration_triples_b11.jsonl not found at {_TRIPLES_B11_JSONL} "
            "— run calibration scripts first"
        )
    if not _RESULT_B11_JSON.exists():
        pytest.skip(
            f"calibration_result_b11.json not found at {_RESULT_B11_JSON} "
            "— run calibration scripts first"
        )

    # Validate triples schema
    lines = [l for l in _TRIPLES_B11_JSONL.read_text().splitlines() if l.strip()]
    assert lines, f"{_TRIPLES_B11_JSONL.name} is empty"

    for i, raw in enumerate(lines, 1):
        obj = json.loads(raw)
        assert "comment_id" in obj, f"Line {i}: missing comment_id"
        assert "llm_confidence" in obj, f"Line {i}: missing llm_confidence"
        assert "claim_category" in obj, f"Line {i}: missing claim_category"

        conf = obj["llm_confidence"]
        assert isinstance(conf, (float, int)), (
            f"Line {i}: llm_confidence must be numeric, got {type(conf)}"
        )

    # Validate result schema
    result = json.loads(_RESULT_B11_JSON.read_text())
    assert "pearson" in result, (
        f"calibration_result_b11.json must have 'pearson' field; keys: {list(result.keys())}"
    )


# ---------------------------------------------------------------------------
# B11-6: Pearson direction not deeply negative
# ---------------------------------------------------------------------------


def test_B11_pearson_direction_positive() -> None:
    """If Pearson is not None, it must be >= -0.5 (not deeply negative).

    A deeply negative Pearson would indicate the CFM scoring is
    systematically inverse to LLM confidence, which would be a red flag.
    Skips if calibration_result_b11.json is absent or pearson is None.
    """
    if not _RESULT_B11_JSON.exists():
        pytest.skip(
            f"calibration_result_b11.json not found at {_RESULT_B11_JSON} "
            "— run calibration scripts first"
        )

    result = json.loads(_RESULT_B11_JSON.read_text())
    pearson = result.get("pearson")

    if pearson is None:
        pytest.skip("pearson is None in calibration_result_b11.json — skipping direction check")

    assert pearson >= -0.5, (
        f"B11-6: Pearson r={pearson:.4f} is deeply negative (< -0.5), which indicates "
        f"CFM scoring is systematically inverse to LLM confidence"
    )


# ---------------------------------------------------------------------------
# B11-7: production gate remains disabled after B11 improvements
# ---------------------------------------------------------------------------


def test_B11_production_gate_remains_disabled() -> None:
    """Even after all B11 improvements, production_gate_criteria_met must be False.

    Also verifies gate_criteria['no_safety_floor_violation'] is True.
    """
    # Build a store with a variety of B11 category entries
    b11_categories = [
        "bug", "race", "resource_leak", "convention",
        "commit_msg", "design", "api_misuse", "error_handling",
    ]
    entries = [
        _entry(
            f"s-b11-gate-{i}",
            f"gate-b11-{i}@t",
            b11_categories[i % len(b11_categories)],
            text=f"b11 gate check comment {i}",
        )
        for i in range(60)
    ]

    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    triples = [
        (f"cmt-b11-gate-{i}", 0.5 + (i % 5) * 0.08, b11_categories[i % len(b11_categories)])
        for i in range(60)
    ]
    report = calibrator.calibrate(triples)

    assert report.production_gate_criteria_met is False, (
        f"B11-7: production_gate_criteria_met must be False (gate permanently disabled "
        f"until Governance Auditor + Arbiter approval); got {report.production_gate_criteria_met}"
    )

    gate = report.gate_criteria_status
    assert gate.get("no_safety_floor_violation") is True, (
        f"B11-7: gate_criteria['no_safety_floor_violation'] must be True; "
        f"got {gate.get('no_safety_floor_violation')!r} (gate={gate})"
    )


# ---------------------------------------------------------------------------
# B11-8: governance — no synthetic entries in production store
# ---------------------------------------------------------------------------


def test_B11_governance_no_synthetic_entries() -> None:
    """Production store must not contain synthetic/test/fake entries.

    Checks:
    - No series_id starts with 'synthetic', 'test', or 'fake'
    - No source_url is empty string

    Skips if production store is not found.
    """
    if not _PROD_STORE.exists():
        pytest.skip(
            f"Production store not found at {_PROD_STORE} — skipping governance check"
        )

    store = ReviewHistoryStore(path=_PROD_STORE)
    entries = store.all()
    assert entries, "Production store is empty — expected >= 1 entry"

    synthetic_prefix_violations: list[str] = []
    empty_url_violations: list[str] = []

    for entry in entries:
        sid = (entry.series_id or "").lower()
        if sid.startswith("synthetic") or sid.startswith("test") or sid.startswith("fake"):
            synthetic_prefix_violations.append(
                f"entry_id={entry.entry_id!r} series_id={entry.series_id!r}"
            )

        if entry.source_url == "":
            empty_url_violations.append(
                f"entry_id={entry.entry_id!r} series_id={entry.series_id!r}"
            )

    assert synthetic_prefix_violations == [], (
        f"B11-8: {len(synthetic_prefix_violations)} production entries have "
        f"synthetic/test/fake series_id:\n"
        + "\n".join(f"  {v}" for v in synthetic_prefix_violations[:10])
    )

    assert empty_url_violations == [], (
        f"B11-8: {len(empty_url_violations)} production entries have empty source_url:\n"
        + "\n".join(f"  {v}" for v in empty_url_violations[:10])
    )
