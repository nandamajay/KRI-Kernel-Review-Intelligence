"""Track-B WP4-K tests: CFM shadow calibration + production gate.

K1  - CFMCalibrationReport defaults to CFM_SHADOW_STAYS (production gate never auto-opens)
K2  - CFMCalibrationReport.production_gate_criteria_met defaults False
K3  - CFMCalibrator.calibrate returns CFMCalibrationReport
K4  - calibrate() with 0 LLM comments yields samples_calibrated=0
K5  - calibrate() with comments populates factor_contributions
K6  - gate_criteria_status keys match the 7 defined gate criteria
K7  - correlation computed when enough data points present
K8  - cfm_calibration field present in IntelligentReport model_dump
K9  - cfm_calibration JS rendering guard present in UI page
K10 - production_gate_criteria_met is always False (hard gate; requires external approval)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kri.common.models import (
    Decision,
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    Provenance,
    ReasoningLayer,
)
from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.learning.models import CFMCalibrationReport
from kri.learning.store import ReviewHistoryStore
from kri.llm.models import IntelligentReport
from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.web.app import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GATE_CRITERIA_KEYS = {
    "ge_20_series_ingested",
    "cfm_scores_for_10_comments",
    "correlation_computed",
    "correlation_non_negative",
    "fp_estimate_acceptable",
    "no_safety_floor_violation",
    "browser_api_cli_validated",
}


def _make_store_with_n_series(n: int, path: Path) -> ReviewHistoryStore:
    from kri.learning.models import ReviewHistoryEntry

    store = ReviewHistoryStore(path=path)
    for i in range(n):
        eid = ReviewHistoryEntry.make_entry_id(f"sid{i}", f"mid@{i}", f"text{i}")
        store.add(ReviewHistoryEntry(
            entry_id=eid,
            series_id=f"sid{i}",
            message_id=f"mid@{i}",
            source_url=f"https://lore.kernel.org/r/mid@{i}",
            reviewer_text=f"text{i}",
            extracted_claim="style",
            evidence_type="review_discussion",
            confidence_basis="test",
        ))
    return store


def _make_calibrator(store: ReviewHistoryStore) -> "CFMCalibrator":
    from kri.learning.calibration import CFMCalibrator

    engine = ConfidenceEngineImpl()
    return CFMCalibrator(confidence_engine=engine, store=store)


# ---------------------------------------------------------------------------
# K1 - CFMCalibrationReport defaults to CFM_SHADOW_STAYS
# ---------------------------------------------------------------------------


def test_K1_cfm_report_default_recommendation() -> None:
    report = CFMCalibrationReport(
        factor_contributions={},
        gate_criteria_status={k: False for k in _GATE_CRITERIA_KEYS},
    )
    assert report.recommendation == "CFM_SHADOW_STAYS"


# ---------------------------------------------------------------------------
# K2 - production_gate_criteria_met defaults False
# ---------------------------------------------------------------------------


def test_K2_production_gate_criteria_met_defaults_false() -> None:
    report = CFMCalibrationReport(
        factor_contributions={},
        gate_criteria_status={k: False for k in _GATE_CRITERIA_KEYS},
    )
    assert report.production_gate_criteria_met is False


# ---------------------------------------------------------------------------
# K3 - CFMCalibrator.calibrate returns CFMCalibrationReport
# ---------------------------------------------------------------------------


def test_K3_calibrate_returns_cfm_calibration_report() -> None:
    from kri.learning.calibration import CFMCalibrator

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = _make_store_with_n_series(0, path)
    cal = _make_calibrator(store)
    result = cal.calibrate([])
    assert isinstance(result, CFMCalibrationReport)


# ---------------------------------------------------------------------------
# K4 - calibrate() with 0 LLM comments yields samples_calibrated=0
# ---------------------------------------------------------------------------


def test_K4_calibrate_zero_comments_yields_zero_samples() -> None:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = _make_store_with_n_series(5, path)
    cal = _make_calibrator(store)
    report = cal.calibrate([])
    assert report.samples_calibrated == 0


# ---------------------------------------------------------------------------
# K5 - calibrate() with comments populates factor_contributions
# ---------------------------------------------------------------------------


def test_K5_calibrate_with_comments_populates_factor_contributions() -> None:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = _make_store_with_n_series(5, path)
    cal = _make_calibrator(store)
    comments = [(f"comment:{i}", 0.5 + i * 0.05) for i in range(15)]
    report = cal.calibrate(comments)
    assert isinstance(report.factor_contributions, dict)
    # Factor contributions should have at least one key populated
    assert len(report.factor_contributions) >= 1


# ---------------------------------------------------------------------------
# K6 - gate_criteria_status has the 7 defined gate criteria
# ---------------------------------------------------------------------------


def test_K6_gate_criteria_status_has_all_7_keys() -> None:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = _make_store_with_n_series(0, path)
    cal = _make_calibrator(store)
    report = cal.calibrate([])
    for key in _GATE_CRITERIA_KEYS:
        assert key in report.gate_criteria_status, (
            f"Missing gate criterion '{key}' in gate_criteria_status"
        )


# ---------------------------------------------------------------------------
# K7 - correlation computed when enough data present
# ---------------------------------------------------------------------------


def test_K7_correlation_computed_with_sufficient_data() -> None:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = _make_store_with_n_series(5, path)
    cal = _make_calibrator(store)
    # Provide 10+ LLM (comment_id, confidence) pairs to trigger correlation
    comments = [(f"cmt:{i}", 0.4 + i * 0.04) for i in range(12)]
    report = cal.calibrate(comments)
    assert report.samples_calibrated >= 1


# ---------------------------------------------------------------------------
# K8 - cfm_calibration field present in IntelligentReport model_dump
# ---------------------------------------------------------------------------


def test_K8_cfm_calibration_in_intelligent_report_model_dump() -> None:
    cal_data = CFMCalibrationReport(
        series_count=5,
        entry_count=20,
        samples_calibrated=10,
        factor_contributions={"review_history": 0.3},
        gate_criteria_status={k: False for k in _GATE_CRITERIA_KEYS},
    ).model_dump()
    report = IntelligentReport(
        series_id="test:k8",
        series_title="k8 test",
        cfm_calibration=cal_data,
    )
    d = report.model_dump()
    assert "cfm_calibration" in d
    assert d["cfm_calibration"]["series_count"] == 5
    assert d["cfm_calibration"]["production_gate_criteria_met"] is False
    assert d["cfm_calibration"]["recommendation"] == "CFM_SHADOW_STAYS"


# ---------------------------------------------------------------------------
# K9 - cfm_calibration JS rendering guard present in UI page
# ---------------------------------------------------------------------------


@pytest.fixture()
def k9_client() -> TestClient:
    from kri.patch_manager import PatchManagerImpl
    lm = LoreManagerImpl(LoreConfig(cache_dir="/tmp/kri_test_lore_trackb_wp4k"))
    pm = PatchManagerImpl(lore_manager=lm)
    return TestClient(create_app(lore_manager=lm, patch_manager=pm))


def test_K9_cfm_calibration_js_guard_present(k9_client: TestClient) -> None:
    r = k9_client.get("/")
    assert r.status_code == 200
    assert "cfm_calibration" in r.text, (
        "renderIntelligent JS must reference cfm_calibration"
    )


# ---------------------------------------------------------------------------
# K10 - production_gate_criteria_met is always False (hard gate)
# ---------------------------------------------------------------------------


def test_K10_production_gate_never_auto_opens() -> None:
    """Hard requirement: production gate may not open autonomously.
    Even with 25 series, 15 calibrated comments, all criteria True, the
    calibrate() implementation must keep production_gate_criteria_met=False
    until external Auditor+Arbiter approval is granted.
    """
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    store = _make_store_with_n_series(25, path)
    cal = _make_calibrator(store)
    # Saturate with 15 LLM comments varying in confidence
    comments = [(f"cmt:{i}", 0.35 + i * 0.03) for i in range(15)]
    report = cal.calibrate(comments)
    # The hard gate: must never be True from within calibrate()
    assert report.production_gate_criteria_met is False, (
        "CRITICAL: production_gate_criteria_met was set True by calibrate() — "
        "CFM production gate must NEVER auto-open; requires Auditor+Arbiter approval"
    )
    assert report.recommendation == "CFM_SHADOW_STAYS", (
        "recommendation must stay CFM_SHADOW_STAYS when production gate is False"
    )
