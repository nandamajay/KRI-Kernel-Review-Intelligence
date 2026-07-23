"""Unit + integration tests for the Patch Manager (offline, cached fixtures)."""

from __future__ import annotations

from kri.common.models import Patch, PatchSeries
from kri.lore_manager.mbox import Message, SubjectInfo, Thread


def test_parse_thread_into_series(patch_manager, v5_thread) -> None:
    series = patch_manager.parse(v5_thread)
    assert isinstance(series, PatchSeries)
    assert series.version == 5
    assert series.series_id == v5_thread.thread_id
    assert len(series.patches) == 2
    # patches ordered by sequence
    assert [p.sequence for p in series.patches] == [1, 2]
    # cover letter + title populated from the 0/N message
    assert series.cover_letter
    assert "Add Nuvoton" in series.title
    # provenance carries a resolvable source
    assert series.provenance.version_or_commit == series.series_id
    # every patch has files parsed from its diff
    for p in series.patches:
        assert p.files_changed
        assert p.diff


def test_parse_mbox_bytes_directly(patch_manager) -> None:
    from .conftest import V5_FIXTURE

    raw = V5_FIXTURE.read_bytes()  # gzipped
    series = patch_manager.parse(raw)
    assert len(series.patches) == 2


def test_extract_versions(patch_manager, v5_thread) -> None:
    series = patch_manager.parse(v5_thread)
    assert patch_manager.extract_versions(series) == [5]


def test_correlate_reviews_covers_every_patch(patch_manager, v5_thread) -> None:
    series = patch_manager.parse(v5_thread)
    correlated = patch_manager.correlate_reviews(series)
    # every patch_id present as a key
    assert set(correlated.keys()) == {p.patch_id for p in series.patches}
    # at least one patch has a maintainer review mapped to it
    total_maint = sum(
        1 for comments in correlated.values() for c in comments if c.is_maintainer
    )
    assert total_maint >= 1
    # DoD: every maintainer comment maps to a patch_id in the series
    for comments in correlated.values():
        for c in comments:
            assert c.target_patch_id in correlated


def test_correlate_reviews_deterministic(patch_manager, v5_thread) -> None:
    series = patch_manager.parse(v5_thread)
    a = patch_manager.correlate_reviews(series)
    b = patch_manager.correlate_reviews(series)
    assert {k: [c.comment_id for c in v] for k, v in a.items()} == {
        k: [c.comment_id for c in v] for k, v in b.items()
    }


def test_normalize_is_idempotent_and_recomputes_files() -> None:
    from kri.patch_manager import PatchManagerImpl

    pm = PatchManagerImpl()
    patch = Patch(
        patch_id="p1",
        subject="  core: thing  ",
        diff="diff --git a/x.c b/x.c\r\n@@ -1 +1 @@\r\n-a\r\n+b\r\n",
        files_changed=["stale.c"],
    )
    n1 = pm.normalize(patch)
    assert n1.subject == "core: thing"
    assert n1.files_changed == ["x.c"]          # recomputed from diff, not the stale list
    assert "\r" not in n1.diff
    n2 = pm.normalize(n1)
    assert n1 == n2


def test_parse_single_patch_series(patch_manager) -> None:
    from .conftest import SINGLE_FIXTURE

    if not SINGLE_FIXTURE.exists():
        return
    series = patch_manager.parse(patch_manager._coerce_thread(SINGLE_FIXTURE.read_bytes()))
    assert len(series.patches) == 1
    assert series.patches[0].files_changed


def _make_patch_msg(message_id: str, seq: int, total: int, with_diff: bool = True) -> Message:
    diff = "diff --git a/x.c b/x.c\n@@ -1 +1 @@\n-a\n+b\n" if with_diff else ""
    return Message(
        message_id=message_id,
        subject=f"[PATCH {seq}/{total}] subsys: do thing",
        subject_info=SubjectInfo(
            is_patch=True,
            is_reply=False,
            sequence=seq,
            series_total=total,
        ),
        body=diff,
        has_diff=with_diff,
        from_email="author@example.com",
    )


def _make_reply_msg(message_id: str, in_reply_to: str) -> Message:
    """Reviewer reply quoting the patch diff — must NOT be treated as a patch."""
    diff_quote = "> diff --git a/x.c b/x.c\n> @@ -1 +1 @@\n"
    return Message(
        message_id=message_id,
        subject="Re: [PATCH 1/1] subsys: do thing",
        subject_info=SubjectInfo(
            is_patch=True,   # [PATCH...] parsed after Re: stripped
            is_reply=True,
            sequence=1,
            series_total=1,
        ),
        body=f"Looks good.\n\n{diff_quote}",
        has_diff=True,       # quoted diff triggers the has_diff heuristic
        from_email="reviewer@example.com",
        in_reply_to=in_reply_to,
    )


def test_reviewer_reply_quoting_diff_excluded_from_patches() -> None:
    """Regression: reviewer reply with a quoted diff must not appear in series.patches."""
    from kri.patch_manager import PatchManagerImpl

    patch_msg = _make_patch_msg("patch-1@x", seq=1, total=1)
    reply_msg = _make_reply_msg("reply-1@x", in_reply_to="patch-1@x")
    thread = Thread(
        thread_id="patch-1@x",
        messages=[patch_msg, reply_msg],
    )
    pm = PatchManagerImpl()
    series = pm.parse(thread)

    assert len(series.patches) == 1, (
        "reviewer reply must be excluded; only the original patch should survive"
    )
    assert series.patches[0].patch_id == "patch-1@x"


def test_is_patch_property_excludes_reply_even_with_diff() -> None:
    """Unit test for Message.is_patch — reply with diff must return False."""
    msg = _make_reply_msg("reply-2@x", in_reply_to="patch-2@x")
    assert msg.is_patch is False, "is_patch must be False for reply messages"


def test_is_patch_property_true_for_genuine_patch() -> None:
    """Unit test for Message.is_patch — genuine patch returns True."""
    msg = _make_patch_msg("patch-3@x", seq=1, total=1)
    assert msg.is_patch is True


def test_correlate_reviews_returns_empty_without_lore_manager() -> None:
    """correlate_reviews degrades gracefully when no LoreManager is injected."""
    from kri.patch_manager import PatchManagerImpl

    pm = PatchManagerImpl()  # no lore_manager
    patch = Patch(patch_id="p-a", subject="core: thing", diff="", files_changed=[])
    series = PatchSeries(
        series_id="p-a",
        title="thing",
        patches=[patch],
        version=1,
    )
    result = pm.correlate_reviews(series)
    assert result == {"p-a": []}, "no lore_manager => all patches get empty review lists"


def test_extract_versions_multi_version_patches() -> None:
    """extract_versions returns all distinct vN values across patch subjects."""
    from kri.patch_manager import PatchManagerImpl

    pm = PatchManagerImpl()
    p1 = Patch(patch_id="p1", subject="[PATCH v2 1/2] a", diff="", files_changed=[])
    p2 = Patch(patch_id="p2", subject="[PATCH v3 2/2] b", diff="", files_changed=[])
    series = PatchSeries(series_id="p1", title="a", patches=[p1, p2], version=2)
    versions = pm.extract_versions(series)
    assert versions == [2, 3]


def test_parse_thread_with_only_replies_yields_empty_patches() -> None:
    """A thread that contains only reviewer replies and no patch messages must
    produce a PatchSeries with an empty patches list rather than crashing."""
    from kri.patch_manager import PatchManagerImpl

    reply_msg = _make_reply_msg("reply-only@x", in_reply_to="original@x")
    thread = Thread(thread_id="original@x", messages=[reply_msg])
    pm = PatchManagerImpl()
    series = pm.parse(thread)
    assert series.patches == [], "reply-only thread must produce empty patch list"
    assert series.series_id == "original@x"
