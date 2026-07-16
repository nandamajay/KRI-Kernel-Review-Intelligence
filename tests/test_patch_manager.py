"""Unit + integration tests for the Patch Manager (offline, cached fixtures)."""

from __future__ import annotations

from kri.common.models import Patch, PatchSeries


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
