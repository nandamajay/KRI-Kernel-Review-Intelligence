"""WP4-V tests: Phase-4V surfacing gap fixes.

Covers two Track-A surfacing gaps found during real-world validation:
  V1  - PatchReview model has governance_warnings field
  V2  - governance_warnings defaults to empty list
  V3  - governance_warnings round-trips through Pydantic (serialization path)
  V4  - PatchReview accepts governance_warnings list (field wiring confirmed)
  V5  - governance_warnings present in model_dump() even when empty (not excluded)
  V6  - renderIntelligent JS has knowledge_state_id guard expression
  V7  - renderIntelligent JS has governance_warnings loop rendering
  V8  - governance_warnings UI block is guarded (not shown when empty)
  V9  - governance_warnings present in /api/review/intelligent JSON response
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kri.llm.models import IntelligentReport, PatchReview
from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.patch_manager import PatchManagerImpl
from kri.web.app import create_app


# ---------------------------------------------------------------------------
# V1 - PatchReview model has governance_warnings field
# ---------------------------------------------------------------------------


def test_V1_patch_review_has_governance_warnings_field() -> None:
    pr = PatchReview(patch_id="p1")
    assert hasattr(pr, "governance_warnings")


# ---------------------------------------------------------------------------
# V2 - governance_warnings defaults to empty list
# ---------------------------------------------------------------------------


def test_V2_governance_warnings_default_empty() -> None:
    pr = PatchReview(patch_id="p1")
    assert pr.governance_warnings == []


# ---------------------------------------------------------------------------
# V3 - governance_warnings round-trips through Pydantic (serialization path)
# Tests model_dump() / model_validate() — independent from live-object V2
# ---------------------------------------------------------------------------


def test_V3_governance_warnings_roundtrip() -> None:
    msgs = ["§28 bypass: evidence_missing BLOCKER in output"]
    pr = PatchReview(patch_id="p1", governance_warnings=msgs)
    d = pr.model_dump()
    assert d["governance_warnings"] == msgs
    pr2 = PatchReview.model_validate(d)
    assert pr2.governance_warnings == msgs


# ---------------------------------------------------------------------------
# V4 - PatchReview accepts governance_warnings list
# ---------------------------------------------------------------------------


def test_V4_patch_review_accepts_governance_warnings_list() -> None:
    """PatchReview(governance_warnings=[...]) must not raise — wires the field."""
    violations = [
        "§28 bypass: evidence_missing BLOCKER published in output for p/test.c:10"
    ]
    pr = PatchReview(patch_id="p2", governance_warnings=violations)
    assert pr.governance_warnings == violations
    assert len(pr.governance_warnings) == 1


# ---------------------------------------------------------------------------
# V5 - governance_warnings present in model_dump() even when empty
# Verifies no exclude=True / exclude_defaults logic silently drops the field.
# (Distinct from V2: exercises the Pydantic serializer, not just attribute access)
# ---------------------------------------------------------------------------


def test_V5_governance_warnings_in_model_dump_when_empty() -> None:
    pr = PatchReview(patch_id="p1")
    d = pr.model_dump()
    assert "governance_warnings" in d
    assert d["governance_warnings"] == []


# ---------------------------------------------------------------------------
# UI tests — static page assertions
# ---------------------------------------------------------------------------


@pytest.fixture()
def ui_client() -> TestClient:
    lm = LoreManagerImpl(LoreConfig(cache_dir="/tmp/kri_test_lore_cache_wp4v"))
    pm = PatchManagerImpl(lore_manager=lm)
    return TestClient(create_app(lore_manager=lm, patch_manager=pm))


# ---------------------------------------------------------------------------
# V6 - renderIntelligent JS has knowledge_state_id guard expression
# Checks the specific guard: if(r.metadata.knowledge_state_id)
# ---------------------------------------------------------------------------


def test_V6_knowledge_state_id_render_guard(ui_client: TestClient) -> None:
    r = ui_client.get("/")
    assert r.status_code == 200
    # Must contain the exact JS guard expression from the rendering block
    assert "r.metadata.knowledge_state_id" in r.text


# ---------------------------------------------------------------------------
# V7 - renderIntelligent JS has governance_warnings loop rendering
# Checks the for-loop rendering JS (not subsumed by V8's guard check)
# ---------------------------------------------------------------------------


def test_V7_governance_warnings_loop_rendered(ui_client: TestClient) -> None:
    r = ui_client.get("/")
    assert r.status_code == 200
    # Must contain the for-loop that renders each violation string
    assert "for(const w of pr.governance_warnings)" in r.text


# ---------------------------------------------------------------------------
# V8 - governance_warnings UI block is guarded (not shown when empty)
# ---------------------------------------------------------------------------


def test_V8_governance_warnings_guard_present(ui_client: TestClient) -> None:
    """JS must guard: pr.governance_warnings && pr.governance_warnings.length"""
    r = ui_client.get("/")
    assert r.status_code == 200
    assert "governance_warnings&&pr.governance_warnings.length" in r.text


# ---------------------------------------------------------------------------
# V9 - governance_warnings present in /api/review/intelligent JSON response
# Integration test: verifies the field propagates from PatchReview through
# IntelligentReport.model_dump() to the HTTP response body.
# Uses a capturing mock so no real LLM or git I/O is required.
# ---------------------------------------------------------------------------

_MINIMAL_MBOX = """\
From mboxrd@z Thu Jan  1 00:00:00 1970
From: Test Author <test@example.com>
Subject: [PATCH] test: add foo to bar
Date: Mon, 01 Jan 2024 00:00:00 +0000
Message-Id: <test-patch-v9@example.com>

This patch adds foo to bar.

Signed-off-by: Test Author <test@example.com>
---
 bar.c | 1 +
 1 file changed, 1 insertion(+)

diff --git a/bar.c b/bar.c
index 0000000..1111111 100644
--- a/bar.c
+++ b/bar.c
@@ -1,1 +1,2 @@
 int x = 0;
+int foo = 1;
"""


class _MockReviewEngine:
    """Returns a canned IntelligentReport with governance_warnings populated."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    def review(self, series: Any) -> Any:  # noqa: ANN001
        pr = PatchReview(
            patch_id="mock-patch-1",
            subject="[PATCH] test: add foo to bar",
            governance_warnings=["§28 bypass: evidence_missing BLOCKER at bar.c:2"],
        )
        report = IntelligentReport(
            series_id=series.series_id,
            series_title="test series",
            patches=[pr],
            metadata={"llm_model": "mock", "processing_time_seconds": 0.0},
        )
        return report


def test_V9_governance_warnings_in_api_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/review/intelligent JSON must include governance_warnings on each patch."""
    monkeypatch.setattr("kri.llm.reviewer.IntelligentReviewEngine", _MockReviewEngine)

    lm = LoreManagerImpl(LoreConfig(cache_dir="/tmp/kri_test_lore_cache_wp4v"))
    pm = PatchManagerImpl(lore_manager=lm)
    client = TestClient(create_app(lore_manager=lm, patch_manager=pm))

    resp = client.post("/api/review/intelligent", json={"mbox": _MINIMAL_MBOX})
    assert resp.status_code == 200
    data = resp.json()
    patches = data.get("patches", [])
    assert len(patches) >= 1, "Expected at least one patch in response"
    patch_data = patches[0]
    assert "governance_warnings" in patch_data, (
        "governance_warnings must be present in each patch of the API response"
    )
    assert patch_data["governance_warnings"] == [
        "§28 bypass: evidence_missing BLOCKER at bar.c:2"
    ]
