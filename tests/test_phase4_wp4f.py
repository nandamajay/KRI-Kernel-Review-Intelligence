"""WP4-F tests: Evidence UI rendering in renderIntelligent() JS.

Tests verify that evidence_status and cfm_confidence fields are rendered in
the embedded page JavaScript.  Uses static template assertions (GET /) following
the established TB91 pattern in test_web.py.

Tests:
  F1 - page embeds evidence_status rendering JS
  F2 - evidence_status badge colors present for rule_backed
  F3 - evidence_status badge colors present for safety_floored
  F4 - evidence_status badge colors present for evidence_missing
  F5 - cfm_confidence rendering JS present
  F6 - shadow mode label present in cfm rendering
  F7 - evidence_status unknown is skipped (guard present)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.patch_manager import PatchManagerImpl
from kri.web.app import create_app


@pytest.fixture()
def client() -> TestClient:
    lm = LoreManagerImpl(LoreConfig(cache_dir="/tmp/kri_test_lore_cache_wp4f"))
    pm = PatchManagerImpl(lore_manager=lm)
    return TestClient(create_app(lore_manager=lm, patch_manager=pm))


# ---------------------------------------------------------------------------
# F1 - evidence_status rendering JS present
# ---------------------------------------------------------------------------


def test_F1_evidence_status_js_present(client: TestClient) -> None:
    """The index page must embed JS that reads c.evidence_status."""
    r = client.get("/")
    assert r.status_code == 200
    assert "evidence_status" in r.text


# ---------------------------------------------------------------------------
# F2 - rule_backed badge color present
# ---------------------------------------------------------------------------


def test_F2_rule_backed_badge_color(client: TestClient) -> None:
    """The index page must embed the rule_backed badge color."""
    r = client.get("/")
    assert r.status_code == 200
    assert "rule_backed" in r.text


# ---------------------------------------------------------------------------
# F3 - safety_floored badge color present
# ---------------------------------------------------------------------------


def test_F3_safety_floored_badge_color(client: TestClient) -> None:
    """The index page must embed the safety_floored badge color."""
    r = client.get("/")
    assert r.status_code == 200
    assert "safety_floored" in r.text


# ---------------------------------------------------------------------------
# F4 - evidence_missing badge color present
# ---------------------------------------------------------------------------


def test_F4_evidence_missing_badge_color(client: TestClient) -> None:
    """The index page must embed the evidence_missing badge color."""
    r = client.get("/")
    assert r.status_code == 200
    assert "evidence_missing" in r.text


# ---------------------------------------------------------------------------
# F5 - cfm_confidence JS present
# ---------------------------------------------------------------------------


def test_F5_cfm_confidence_js_present(client: TestClient) -> None:
    """The index page must embed JS that reads c.cfm_confidence."""
    r = client.get("/")
    assert r.status_code == 200
    assert "cfm_confidence" in r.text


# ---------------------------------------------------------------------------
# F6 - shadow mode label present
# ---------------------------------------------------------------------------


def test_F6_shadow_mode_label_present(client: TestClient) -> None:
    """The index page must display 'shadow mode' label next to CFM score."""
    r = client.get("/")
    assert r.status_code == 200
    assert "shadow mode" in r.text


# ---------------------------------------------------------------------------
# F7 - evidence_status 'unknown' guard present
# ---------------------------------------------------------------------------


def test_F7_unknown_status_guarded(client: TestClient) -> None:
    """The JS must guard against rendering the badge when evidence_status is 'unknown'."""
    r = client.get("/")
    assert r.status_code == 200
    # Guard: c.evidence_status && c.evidence_status!=='unknown'
    assert "evidence_status!=='unknown'" in r.text
