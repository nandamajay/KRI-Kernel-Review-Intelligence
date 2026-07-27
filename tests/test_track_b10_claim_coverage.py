"""Track-B.10 Claim Coverage tests.

B10-1  test_B10_new_claim_signals_classify_correctly
B10-2  test_B10_claim_signals_do_not_shadow_maintainer_ack
B10-3  test_B10_no_vendor_specific_strings_in_signals
B10-4  test_B10_accepted_patch_not_reclassified_as_verified
B10-5  test_B10_store_expansion_adds_new_category_entries
B10-6  test_B10_deduplication_prevents_duplicate_entries
B10-7  test_B10_calibration_b10_output_jsonl_schema_valid
B10-8  test_B10_production_gate_remains_disabled
"""

from __future__ import annotations

import inspect
import json
import re
import tempfile
from pathlib import Path

import pytest

from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.learning.calibration import CFMCalibrator
from kri.learning.ingestion import _classify_claim, _CLAIM_SIGNALS
from kri.learning.models import ReviewHistoryEntry
from kri.learning.store import ReviewHistoryStore
from kri.common.models import Provenance

# ---------------------------------------------------------------------------
# Absolute paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/kri")
_LEDGER_DIR = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/data/ledger")
_TRIPLES_B10_JSONL = _LEDGER_DIR / "calibration_triples_b10.jsonl"


# ---------------------------------------------------------------------------
# Helpers (mirrors test_track_b9_calibration_triples.py style)
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
# B10-1: new claim signals classify correctly
# ---------------------------------------------------------------------------


def test_B10_new_claim_signals_classify_correctly() -> None:
    """Each B10 signal pattern must match its expected claim category.

    Tests text samples that exercise each of the 7 new B10 signal groups:
    bug, commit_msg, convention, race, resource_leak, design, api_misuse.
    """
    test_cases: list[tuple[str, str]] = [
        # bug category — must match B10 bug pattern (off-by-one, wrong return value, regression since, etc.)
        # Note: plain "bug" word is NOT in the signal; must use specific phrases
        ("wrong return value returned here", "bug"),
        ("regression since v6.3 broke this", "bug"),
        ("causes a crash on module unload", "bug"),
        # commit_msg category
        ("commit message should describe the fix", "commit_msg"),
        ("the commit log is missing details about the change", "commit_msg"),
        # design category — must use "design issue/flaw/problem", "bad design", "layering violation"
        # Note: "the design of this function" alone does not match; need qualifier
        ("design issue: wrong level of abstraction chosen here", "design"),
        ("bad design: layering violation in this approach", "design"),
        ("layering violation: wrong abstraction layer used", "design"),
        # race category — must NOT use "race condition" (caught by locking first);
        # use data_race / read_once / memory_barrier / rcu patterns from the B10 race signal
        ("data race on the shared counter variable", "race"),
        ("use read_once to safely access this field", "race"),
        # resource_leak category
        ("resource leak on error path", "resource_leak"),
        ("missing put on error unwind", "resource_leak"),
        ("forgot to release the clk_put reference", "resource_leak"),
        # convention category
        ("use array_size here, kernel convention requires it", "convention"),
        ("naming convention violation: prefer is_err() helper", "convention"),
        # api_misuse category
        ("api misuse: incorrect use of this function", "api_misuse"),
        ("should use the devm_request_irq variant instead", "api_misuse"),
    ]

    failures: list[str] = []
    for text, expected in test_cases:
        got, _ = _classify_claim(text)
        if got != expected:
            failures.append(
                f"text={str(text)[:60]!r} → expected '{expected}', got '{got}'"
            )

    assert failures == [], (
        f"B10-1: {len(failures)} misclassified signals:\n"
        + "\n".join(f"  {f}" for f in failures)
    )


# ---------------------------------------------------------------------------
# B10-2: new signals do not shadow maintainer_ack priority
# ---------------------------------------------------------------------------


def test_B10_claim_signals_do_not_shadow_maintainer_ack() -> None:
    """maintainer_ack must take priority over design/convention/bug signals.

    "Acked-by: Greg Kroah-Hartman <..." must return maintainer_ack.
    "Reviewed-by: ..." must return maintainer_ack.
    The first-match ordering of _CLAIM_SIGNALS enforces this.
    """
    ack_texts = [
        "Acked-by: Greg Kroah-Hartman <gregkh@linuxfoundation.org>",
        "Reviewed-by: Mark Brown <broonie@kernel.org>",
        "acked by Linus: good design choice",  # contains 'design' but ack wins
        "Lgtm, though the convention here is odd",  # contains 'convention' but lgtm wins
    ]

    for text in ack_texts:
        got, _ = _classify_claim(text)
        assert got == "maintainer_ack", (
            f"B10-2: expected maintainer_ack for {str(text)[:80]!r}, got '{got}'"
        )

    # Also verify nack is not shadowed
    nack_texts = [
        "Nack: the design is wrong here",
        "NAK — please revert this bug fix",
    ]
    for text in nack_texts:
        got, _ = _classify_claim(text)
        assert got == "maintainer_nack", (
            f"B10-2: expected maintainer_nack for {str(text)[:80]!r}, got '{got}'"
        )


# ---------------------------------------------------------------------------
# B10-3: no vendor-specific strings in _CLAIM_SIGNALS
# ---------------------------------------------------------------------------


def test_B10_no_vendor_specific_strings_in_signals() -> None:
    """_CLAIM_SIGNALS must not contain ASoC-vendor-specific strings.

    Prohibited vendor prefixes: qualcomm, mediatek, qcom_, mt_, nxp_, imx_
    Domain-generic terms like 'lpass' are acceptable (already present).
    """
    vendor_strings = ["qualcomm", "mediatek", "qcom_", "mt_", "nxp_", "imx_"]

    violations: list[str] = []
    for pattern, category in _CLAIM_SIGNALS:
        pattern_lower = pattern.lower()
        for vendor in vendor_strings:
            if vendor in pattern_lower:
                violations.append(
                    f"category={category!r}: pattern contains vendor string {vendor!r}: {str(pattern)[:80]!r}"
                )

    assert violations == [], (
        f"B10-3: _CLAIM_SIGNALS contains vendor-specific strings (must be domain-generic):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# B10-4: accepted_patch evidence_type not counted as verified review_discussion
# ---------------------------------------------------------------------------


def test_B10_accepted_patch_not_reclassified_as_verified() -> None:
    """An entry with evidence_type='accepted_patch' must have verified=False
    when processed through _build_evidence_graph_for_calibration.

    The calibration engine sets verified=False for accepted_patch entries
    (only review_discussion/maintainer_ack/maintainer_nack get verified=True).
    """
    from kri.learning.calibration import _build_evidence_graph_for_calibration

    accepted_entry = _entry(
        series_id="s-ap-1",
        message_id="ap-msg-1@test",
        claim="memory_safety",
        evidence_type="accepted_patch",
        text="applied, thanks for the fix",
    )

    eg = _build_evidence_graph_for_calibration("cmt-ap-test", [accepted_entry])

    # All evidence nodes for accepted_patch must have verified=False
    for ev in eg.evidence:
        assert ev.verified is False, (
            f"B10-4: accepted_patch evidence node must have verified=False; "
            f"got verified={ev.verified} for evidence_id={ev.evidence_id}"
        )


# ---------------------------------------------------------------------------
# B10-5: store expansion adds new category entries
# ---------------------------------------------------------------------------


def test_B10_store_expansion_adds_new_category_entries() -> None:
    """Adding a 'design' entry to the store and calibrating with a design triple
    must result in review_history_distribution['design'] > 0.0.

    This verifies the B10 store expansion wires new categories into CFM scoring.
    """
    design_entries = [
        _entry(f"s-design-{i}", f"design-{i}@t", "design", text=f"design issue comment {i}")
        for i in range(3)
    ]
    store = _make_store(*design_entries)
    calibrator = _make_calibrator(store)

    report = calibrator.calibrate([
        ("cmt-b10-design-1", 0.7, "design"),
        ("cmt-b10-design-2", 0.55, "design"),
    ])

    dist = report.review_history_distribution
    assert isinstance(dist, dict), "review_history_distribution must be a dict"

    design_score = dist.get("design", 0.0)
    assert design_score > 0.0, (
        f"B10-5: review_history_distribution['design'] must be > 0.0 after "
        f"adding 3 design entries to store; got {design_score} (dist={dist})"
    )


# ---------------------------------------------------------------------------
# B10-6: deduplication prevents duplicate entries
# ---------------------------------------------------------------------------


def test_B10_deduplication_prevents_duplicate_entries() -> None:
    """Writing two identical entries to the store must result in count() == 1.

    make_entry_id() must produce identical hashes for identical inputs.
    """
    text = "race condition on the shared counter"
    series_id = "s-dedup-1"
    message_id = "dedup-msg-1@test"

    # Verify make_entry_id is deterministic
    eid1 = ReviewHistoryEntry.make_entry_id(series_id, message_id, text)
    eid2 = ReviewHistoryEntry.make_entry_id(series_id, message_id, text)
    assert eid1 == eid2, (
        f"B10-6: make_entry_id must be deterministic; got {eid1} != {eid2}"
    )

    entry_a = _entry(series_id, message_id, "race", text=text)
    entry_b = _entry(series_id, message_id, "race", text=text)

    assert entry_a.entry_id == entry_b.entry_id, (
        "B10-6: two entries with identical (series_id, message_id, text) must have "
        f"identical entry_id; got {entry_a.entry_id!r} vs {entry_b.entry_id!r}"
    )

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = ReviewHistoryStore(path=path)

    added_a = store.add(entry_a)
    added_b = store.add(entry_b)

    assert added_a is True, "B10-6: first add must return True"
    assert added_b is False, "B10-6: second add of duplicate must return False"
    assert store.count() == 1, (
        f"B10-6: store.count() must be 1 after adding two identical entries; "
        f"got {store.count()}"
    )


# ---------------------------------------------------------------------------
# B10-7: calibration_triples_b10.jsonl schema validation
# ---------------------------------------------------------------------------


def test_B10_calibration_b10_output_jsonl_schema_valid() -> None:
    """calibration_triples_b10.jsonl must exist, be non-empty, and every line
    must be a valid JSON object with:
      - comment_id: non-empty str (16-char hex from SHA-256)
      - llm_confidence: float in (0, 1) exclusive
      - claim_category: non-empty str

    Skips if the file is absent (script not yet run).
    """
    if not _TRIPLES_B10_JSONL.exists():
        pytest.skip(
            f"calibration_triples_b10.jsonl not found at {_TRIPLES_B10_JSONL} "
            "— run scripts/run_live_calibration_b10.py first"
        )

    lines = [l for l in _TRIPLES_B10_JSONL.read_text().splitlines() if l.strip()]
    assert lines, f"{_TRIPLES_B10_JSONL.name} is empty"

    hex_pattern = re.compile(r'^[0-9a-f]{16}$')

    for i, raw in enumerate(lines, 1):
        obj = json.loads(raw)
        assert "comment_id" in obj, f"Line {i}: missing comment_id"
        assert "llm_confidence" in obj, f"Line {i}: missing llm_confidence"
        assert "claim_category" in obj, f"Line {i}: missing claim_category"

        cid = obj["comment_id"]
        assert isinstance(cid, str) and len(cid) > 0, (
            f"Line {i}: comment_id must be non-empty str, got {cid!r}"
        )
        assert hex_pattern.match(cid), (
            f"Line {i}: comment_id must be 16-char lowercase hex (SHA-256 derived); "
            f"got {cid!r}"
        )

        conf = obj["llm_confidence"]
        assert isinstance(conf, float), (
            f"Line {i}: llm_confidence must be float, got {type(conf)}"
        )
        assert 0.0 < conf < 1.0, (
            f"Line {i}: llm_confidence must be in (0, 1) exclusive; got {conf}"
        )

        cat = obj["claim_category"]
        assert isinstance(cat, str) and len(cat) > 0, (
            f"Line {i}: claim_category must be non-empty str, got {cat!r}"
        )


# ---------------------------------------------------------------------------
# B10-8: production gate remains disabled with >= 50 B10 triples
# ---------------------------------------------------------------------------


def test_B10_production_gate_remains_disabled() -> None:
    """CFMCalibrator with >= 50 B10-category triples must not activate the gate.

    Asserts:
    - production_gate_criteria_met == False
    - recommendation != 'CFM_PRODUCTION_READY' (must be CFM_SHADOW_STAYS)

    The gate is permanently disabled: all_met=False in calibration.py and
    Governance Auditor + Arbiter approval is required externally.
    """
    # Build a store with 60 entries across B10 categories
    b10_categories = [
        "bug", "commit_msg", "convention", "race",
        "resource_leak", "design", "api_misuse",
    ]
    entries = []
    for i in range(60):
        cat = b10_categories[i % len(b10_categories)]
        entries.append(
            _entry(
                f"s-gate-{i}",
                f"gate-b10-{i}@t",
                cat,
                text=f"b10 gate check comment {i} {cat}",
            )
        )

    store = _make_store(*entries)
    calibrator = _make_calibrator(store)

    # 60 triples — more than the 50-sample minimum
    triples = [
        (f"cmt-b10-gate-{i}", 0.5 + (i % 5) * 0.08, b10_categories[i % len(b10_categories)])
        for i in range(60)
    ]
    report = calibrator.calibrate(triples)

    assert report.production_gate_criteria_met is False, (
        f"B10-8: production_gate_criteria_met must be False (gate permanently disabled "
        f"until Governance Auditor approval); got {report.production_gate_criteria_met}"
    )

    assert report.recommendation != "CFM_PRODUCTION_READY", (
        f"B10-8: recommendation must not be 'CFM_PRODUCTION_READY'; "
        f"got '{report.recommendation}' (expected CFM_SHADOW_STAYS or similar)"
    )

    assert report.recommendation == "CFM_SHADOW_STAYS", (
        f"B10-8: recommendation must be 'CFM_SHADOW_STAYS'; "
        f"got '{report.recommendation}'"
    )
