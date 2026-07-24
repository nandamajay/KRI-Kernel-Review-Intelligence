"""WP4-D tests: KnowledgeStateId capture before review fan-out.

Tests:
  D1 - knowledge_state_id appears in IntelligentReport.metadata when km is set
  D2 - knowledge_state_id is absent when knowledge_manager=None
  D3 - snapshot() exception degrades gracefully (no knowledge_state_id, review continues)
  D4 - snapshot() is called exactly once per review() call
  D5 - knowledge_state_id is the string state_id from KnowledgeStateId object
"""

from __future__ import annotations

import copy
import json
from unittest.mock import MagicMock, call

from kri.llm.reviewer import IntelligentReviewEngine
from kri.common.models import Patch, PatchSeries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_series() -> PatchSeries:
    diff = (
        "diff --git a/sound/soc/foo.c b/sound/soc/foo.c\n"
        "index 000..111 100644\n"
        "--- a/sound/soc/foo.c\n"
        "+++ b/sound/soc/foo.c\n"
        "@@ -10,3 +10,4 @@ static int foo(void)\n"
        " \tint a;\n"
        "+\tint b;\n"
        " \treturn a;\n"
    )
    patch = Patch(
        patch_id="p1",
        subject="[PATCH 1/1] test",
        commit_message="",
        files_changed=["sound/soc/foo.c"],
        diff=diff,
        sequence=1,
        series_total=1,
    )
    return PatchSeries(series_id="s-wp4d", title="WP4-D test", cover_letter="", patches=[patch])


def _make_client() -> MagicMock:
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    payloads = [
        {"what_it_does": "adds b", "why_needed": "", "risk_level": "low", "test_recommendations": []},
        [],
        [],
    ]
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


def _make_km(state_id: str = "abc123def456") -> MagicMock:
    km = MagicMock()
    ks = MagicMock()
    ks.state_id = state_id
    km.snapshot.return_value = ks
    return km


# ---------------------------------------------------------------------------
# D1 - knowledge_state_id in metadata
# ---------------------------------------------------------------------------


def test_D1_knowledge_state_id_in_metadata():
    """When knowledge_manager is set, report.metadata['knowledge_state_id'] is populated."""
    km = _make_km(state_id="deadbeef01234567")
    engine = IntelligentReviewEngine(client=_make_client(), knowledge_manager=km)
    report = engine.review(_make_series())
    assert "knowledge_state_id" in report.metadata
    assert report.metadata["knowledge_state_id"] == "deadbeef01234567"


# ---------------------------------------------------------------------------
# D2 - knowledge_state_id absent without km
# ---------------------------------------------------------------------------


def test_D2_no_km_no_state_id():
    """Without knowledge_manager, knowledge_state_id is absent from metadata."""
    engine = IntelligentReviewEngine(client=_make_client(), knowledge_manager=None)
    report = engine.review(_make_series())
    assert "knowledge_state_id" not in report.metadata


# ---------------------------------------------------------------------------
# D3 - snapshot() exception degrades gracefully
# ---------------------------------------------------------------------------


def test_D3_snapshot_exception_degrades():
    """If snapshot() raises, no knowledge_state_id in metadata, review still completes."""
    km = MagicMock()
    km.snapshot.side_effect = RuntimeError("snapshot failed")
    engine = IntelligentReviewEngine(client=_make_client(), knowledge_manager=km)
    report = engine.review(_make_series())
    assert "knowledge_state_id" not in report.metadata
    # Review completed normally.
    assert len(report.patches) == 1


# ---------------------------------------------------------------------------
# D4 - snapshot() called exactly once
# ---------------------------------------------------------------------------


def test_D4_snapshot_called_once():
    """snapshot() is called exactly once per review() invocation."""
    km = _make_km()
    engine = IntelligentReviewEngine(client=_make_client(), knowledge_manager=km)
    engine.review(_make_series())
    km.snapshot.assert_called_once()


# ---------------------------------------------------------------------------
# D5 - state_id is a string
# ---------------------------------------------------------------------------


def test_D5_state_id_is_string():
    """knowledge_state_id in metadata is a plain string (not a KnowledgeStateId object)."""
    km = _make_km(state_id="0123456789abcdef")
    engine = IntelligentReviewEngine(client=_make_client(), knowledge_manager=km)
    report = engine.review(_make_series())
    assert isinstance(report.metadata["knowledge_state_id"], str)
    assert report.metadata["knowledge_state_id"] == "0123456789abcdef"
