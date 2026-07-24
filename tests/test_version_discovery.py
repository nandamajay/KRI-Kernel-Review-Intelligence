"""Tests for WP-S2A: kri/lore_manager/version_discovery.py.

13 tests across 3 sections:
  9a — Discovery (TS2A-1..5 + TS2A-5b)
  9b — Reconciliation (TS2A-6..9)
  9c — Prompt injection + wiring (TS2A-10..12)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kri.lore_manager.mbox import SubjectInfo
from kri.lore_manager.version_discovery import (
    CritiqueReplyPair,
    PriorVersionFetcher,
    _reconcile,
    discover_prior_version_thread_ids,
    format_prior_version_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subject_info(
    version: int = 1,
    is_cover_letter: bool = True,
    is_patch: bool = False,
    is_reply: bool = False,
    sequence: int = 0,
    series_total: int = 1,
) -> SubjectInfo:
    return SubjectInfo(
        clean="Test patch",
        is_patch=is_patch,
        is_reply=is_reply,
        version=version,
        sequence=sequence,
        series_total=series_total,
        is_cover_letter=is_cover_letter,
    )


def _make_message(
    message_id: str,
    from_email: str = "author@example.com",
    in_reply_to: str | None = None,
    subject_info: SubjectInfo | None = None,
) -> Any:
    msg = MagicMock()
    msg.message_id = message_id
    msg.from_email = from_email
    msg.in_reply_to = in_reply_to
    msg.subject_info = subject_info or _make_subject_info()
    msg.body = ""
    return msg


def _make_thread(thread_id: str, messages: list[Any]) -> Any:
    t = MagicMock()
    t.thread_id = thread_id
    t.messages = messages
    return t


def _make_series(version: int = 5, series_id: str = "v5@example.com",
                 series_title: str = "Test Driver") -> Any:
    series = MagicMock()
    series.version = version
    series.series_id = series_id
    series.series_title = series_title
    patch = MagicMock()
    patch.author_email = "author@example.com"
    patch.diff = ""
    patch.files_changed = []
    series.patches = [patch]
    return series


def _make_review_comment(
    message: str = "Use devm_clk_get here",
    author: str = "maintainer@kernel.org",
    severity: str = "warning",
    is_maintainer: bool = True,
    message_id: str = "rev1@example.com",
) -> Any:
    rc = MagicMock()
    rc.message = message
    rc.author = author
    rc.severity = severity
    rc.is_maintainer = is_maintainer
    rc.message_id = message_id
    return rc


# ---------------------------------------------------------------------------
# 9a — Discovery tests
# ---------------------------------------------------------------------------


def test_TS2A_1_discovers_prior_via_in_reply_to():
    """Primary path: In-Reply-To chain returns v4, v3 thread IDs."""
    lore = MagicMock()
    series = _make_series(version=5, series_id="v5@example.com")

    v5_cover = _make_message("v5@example.com", in_reply_to="v4@example.com",
                             subject_info=_make_subject_info(version=5))
    v5_thread = _make_thread("v5@example.com", [v5_cover])

    v4_cover = _make_message("v4@example.com", from_email="author@example.com",
                             in_reply_to="v3@example.com",
                             subject_info=_make_subject_info(version=4))
    v4_thread = _make_thread("v4@example.com", [v4_cover])

    v3_cover = _make_message("v3@example.com", from_email="author@example.com",
                             in_reply_to=None,
                             subject_info=_make_subject_info(version=3))
    v3_thread = _make_thread("v3@example.com", [v3_cover])

    def fake_fetch(tid):
        return {"v5@example.com": v5_thread,
                "v4@example.com": v4_thread,
                "v3@example.com": v3_thread}[tid]

    lore.fetch.side_effect = fake_fetch

    result = discover_prior_version_thread_ids(series, lore)
    assert 4 in result
    assert result[4] == "v4@example.com"
    assert 3 in result
    assert result[3] == "v3@example.com"


def test_TS2A_2_falls_back_to_search_when_no_in_reply_to():
    """When cover letter has no In-Reply-To, search fallback finds v4."""
    lore = MagicMock()
    series = _make_series(version=5, series_id="v5@example.com",
                          series_title="Add ASOC driver for Foo")

    v5_cover = _make_message("v5@example.com", in_reply_to=None,
                             subject_info=_make_subject_info(version=5))
    v5_thread = _make_thread("v5@example.com", [v5_cover])

    v4_cover = _make_message("v4@example.com", from_email="author@example.com",
                             in_reply_to=None,
                             subject_info=_make_subject_info(version=4))
    v4_thread = _make_thread("v4@example.com", [v4_cover])

    lore.fetch.side_effect = lambda tid: (
        v5_thread if tid == "v5@example.com" else v4_thread
    )
    lore.search.return_value = ["v4@example.com"]

    result = discover_prior_version_thread_ids(series, lore)
    assert 4 in result
    assert result[4] == "v4@example.com"


def test_TS2A_3_rejects_author_mismatch():
    """Search candidate with different author email must be rejected."""
    lore = MagicMock()
    series = _make_series(version=2, series_id="v2@example.com",
                          series_title="My Patch")

    v2_cover = _make_message("v2@example.com", in_reply_to=None,
                             subject_info=_make_subject_info(version=2))
    v2_thread = _make_thread("v2@example.com", [v2_cover])

    different_author_cover = _make_message(
        "v1@other.org", from_email="someone_else@example.com",
        subject_info=_make_subject_info(version=1))
    different_author_thread = _make_thread("v1@other.org", [different_author_cover])

    lore.fetch.side_effect = lambda tid: (
        v2_thread if tid == "v2@example.com" else different_author_thread
    )
    lore.search.return_value = ["v1@other.org"]

    result = discover_prior_version_thread_ids(series, lore)
    assert result == {}


def test_TS2A_4_degrades_on_network_error():
    """Exception from lore.fetch must not propagate; returns {}."""
    lore = MagicMock()
    series = _make_series(version=3, series_id="v3@example.com")

    lore.fetch.side_effect = Exception("network timeout")
    lore.search.side_effect = Exception("search down")

    result = discover_prior_version_thread_ids(series, lore)
    assert result == {}


def test_TS2A_5_max_depth_guard_prevents_infinite_loop():
    """An In-Reply-To chain that cycles must terminate via max_depth guard."""
    lore = MagicMock()
    series = _make_series(version=5, series_id="v5@example.com")

    v5_cover = _make_message("v5@example.com", in_reply_to="va@example.com",
                             subject_info=_make_subject_info(version=5))
    v5_thread = _make_thread("v5@example.com", [v5_cover])

    # va → vb → va (cycle with version that would never reach 1)
    va_cover = _make_message("va@example.com", from_email="author@example.com",
                             in_reply_to="vb@example.com",
                             subject_info=_make_subject_info(version=4))
    va_thread = _make_thread("va@example.com", [va_cover])

    vb_cover = _make_message("vb@example.com", from_email="author@example.com",
                             in_reply_to="va@example.com",
                             subject_info=_make_subject_info(version=3))
    vb_thread = _make_thread("vb@example.com", [vb_cover])

    def fake_fetch(tid):
        return {"v5@example.com": v5_thread,
                "va@example.com": va_thread,
                "vb@example.com": vb_thread}.get(tid, MagicMock(
                    thread_id=tid, messages=[]))

    lore.fetch.side_effect = fake_fetch
    lore.search.return_value = []

    # Must terminate without hanging or raising
    result = discover_prior_version_thread_ids(series, lore)
    # Partial result is acceptable; no infinite loop
    assert isinstance(result, dict)
    assert lore.fetch.call_count <= 15  # max_depth=10 + initial + some slack


def test_TS2A_5b_returns_empty_for_v1_series():
    """v1 series must return {} immediately without calling fetch or search."""
    lore = MagicMock()
    series = _make_series(version=1, series_id="v1@example.com")

    result = discover_prior_version_thread_ids(series, lore)
    assert result == {}
    lore.fetch.assert_not_called()
    lore.search.assert_not_called()


# ---------------------------------------------------------------------------
# 9b — Reconciliation tests
# ---------------------------------------------------------------------------


def _make_pair(
    critique_message: str = "Use devm_clk_get here",
    author_reply: str = "",
    version: int = 3,
    target_patch_files: list[str] | None = None,
) -> CritiqueReplyPair:
    return CritiqueReplyPair(
        version=version,
        critique_message=critique_message,
        critique_author="maintainer@kernel.org",
        critique_severity="warning",
        target_patch_files=target_patch_files or [],
        author_reply=author_reply,
        address_status="outstanding",
        address_notes="",
    )


def _make_current_series(diff_plus_lines: str = "") -> Any:
    series = MagicMock()
    series.version = 5
    p = MagicMock()
    p.diff = diff_plus_lines
    p.files_changed = []
    series.patches = [p]
    return series


def test_TS2A_6_h1_explicit_ack_suppresses():
    """Author reply containing 'fixed' → addressed_explicit, not injected."""
    pair = _make_pair(author_reply="fixed in this version, thanks")
    current = _make_current_series()
    result = _reconcile(pair, current)
    assert result.address_status == "addressed_explicit"


def test_TS2A_7_h2_pattern_absent_annotates_with_note():
    """When concern tokens absent from v(n) diff → inject with verify note."""
    pair = _make_pair(critique_message="Use devm_clk_get here", author_reply="")
    # v5 diff has no devm_clk_get on + lines
    current = _make_current_series(diff_plus_lines="+\tsome_other_call();\n")
    result = _reconcile(pair, current)
    assert result.address_status == "outstanding"
    assert "verify resolution" in result.address_notes or "no matching" in result.address_notes


def test_TS2A_8_h2_pattern_present_injects_may_be_addressed():
    """When concern token IS present in v(n) diff → inject with 'may be addressed'."""
    pair = _make_pair(critique_message="Use devm_clk_get here", author_reply="")
    # v5 diff still has devm_clk_get in a + line
    current = _make_current_series(diff_plus_lines="+\treturn devm_clk_get(dev, NULL);\n")
    result = _reconcile(pair, current)
    assert result.address_status == "outstanding"
    assert "may be addressed" in result.address_notes


def test_TS2A_9_no_author_reply_annotates():
    """No author reply → address_notes is non-empty (either H2 note or no-reply note)."""
    # Use a critique with tokens not in the diff → H2 fires with "no matching" note
    pair = _make_pair(author_reply="", critique_message="must use devm_kzalloc here")
    current = _make_current_series(diff_plus_lines="+\tsome_other_thing();\n")
    result = _reconcile(pair, current)
    assert result.address_status == "outstanding"
    assert result.address_notes != ""

    # When critique has no extractable tokens AND no reply → H3 fires with no-reply note
    pair_no_tokens = _make_pair(author_reply="", critique_message="OK")
    result2 = _reconcile(pair_no_tokens, current)
    assert result2.address_status == "outstanding"
    assert "author did not reply" in result2.address_notes


# ---------------------------------------------------------------------------
# 9c — Prompt injection + wiring tests
# ---------------------------------------------------------------------------


def test_TS2A_10_format_returns_empty_when_no_outstanding():
    """All addressed → format_prior_version_context returns empty string."""
    pairs = [
        CritiqueReplyPair(
            version=3, critique_message="Use devm_clk_get",
            critique_author="m@k.org", critique_severity="warning",
            target_patch_files=[], author_reply="fixed",
            address_status="addressed_explicit", address_notes="",
        )
    ]
    result = format_prior_version_context(pairs, "p1@example.com")
    assert result == ""


def test_TS2A_11_format_includes_outstanding_concern():
    """Outstanding concern → block contains 'Prior Version Feedback' header."""
    pairs = [
        CritiqueReplyPair(
            version=3, critique_message="missing error check on clk_prepare_enable",
            critique_author="reviewer@kernel.org", critique_severity="warning",
            target_patch_files=[], author_reply="",
            address_status="outstanding", address_notes="[author did not reply]",
        )
    ]
    result = format_prior_version_context(pairs, "p1@example.com")
    assert "Prior Version Feedback" in result
    assert "clk_prepare_enable" in result
    assert "v3" in result


def test_TS2A_12_prior_version_block_does_not_contain_apply_status():
    """Strategy C extension: apply_status sentinel must never appear in prior version block."""
    apply_status_sentinel = "apply_status"
    pairs = [
        CritiqueReplyPair(
            version=2, critique_message="missing devm_clk_get",
            critique_author="m@k.org", critique_severity="blocker",
            target_patch_files=[], author_reply="",
            address_status="outstanding", address_notes="",
        )
    ]
    block = format_prior_version_context(pairs, "p1@example.com")
    assert apply_status_sentinel not in block
    assert "address_status" not in block
