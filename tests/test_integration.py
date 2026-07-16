"""End-to-end integration tests + Protocol conformance for Sprint-1 modules.

These exercise the full data-flow contract (SPEC.md Sec. 8) offline:
  cached lore mbox -> LoreManager -> PatchManager -> PatchSeries + reviews,
and a real checkpatch run producing structured findings.
"""

from __future__ import annotations

import pytest

from kri.common.interfaces import (
    LoreManager,
    PatchManager,
    RepositoryManager,
    StaticAnalysisManager,
)
from kri.common.models import Patch
from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.patch_manager import PatchManagerImpl
from kri.static_analysis import StaticAnalysisConfig, StaticAnalysisManagerImpl

from .conftest import KERNEL_PATH, LORE_CACHE, MAINTAINERS_PATH, V5_FIXTURE, V5_ROOT_ID


def test_protocols_are_structurally_satisfied(kernel_path) -> None:
    from kri.repo_manager import RepoConfig, RepositoryManagerImpl

    lm = LoreManagerImpl(LoreConfig(cache_dir=LORE_CACHE, inbox="all", offline=True))
    pm = PatchManagerImpl(lore_manager=lm)
    sam = StaticAnalysisManagerImpl(StaticAnalysisConfig(repo_path=kernel_path))
    rm = RepositoryManagerImpl(RepoConfig(kernel_path))
    assert isinstance(lm, LoreManager)
    assert isinstance(pm, PatchManager)
    assert isinstance(sam, StaticAnalysisManager)
    assert isinstance(rm, RepositoryManager)


def test_integration_cached_thread_to_series_with_reviews() -> None:
    """(a) parse a real cached lore thread end-to-end into a PatchSeries."""
    if not V5_FIXTURE.exists():
        pytest.skip("v5 fixture missing")
    lm = LoreManagerImpl(LoreConfig(
        cache_dir=LORE_CACHE, inbox="all",
        maintainers_path=MAINTAINERS_PATH if MAINTAINERS_PATH.exists() else None,
        offline=True,
    ))
    pm = PatchManagerImpl(lore_manager=lm)

    thread = lm.fetch(V5_ROOT_ID)                 # offline replay from cache
    series = pm.parse(thread)
    assert series.version == 5
    assert len(series.patches) == 2

    correlated = pm.correlate_reviews(series)
    # ground-truth maintainer comments correlate to a patch (benchmark oracle)
    maint_comments = [
        c for cs in correlated.values() for c in cs if c.is_maintainer
    ]
    assert maint_comments
    for c in maint_comments:
        assert c.provenance.source_url  # resolvable provenance (Constitution Sec. 37)
        assert c.target_patch_id in {p.patch_id for p in series.patches}


def test_integration_checkpatch_structured_findings() -> None:
    """(b) run checkpatch against a patch and get structured findings."""
    if not KERNEL_PATH.exists():
        pytest.skip("kernel clone missing")
    sam = StaticAnalysisManagerImpl(StaticAnalysisConfig(repo_path=KERNEL_PATH))
    bad = Patch(
        patch_id="p-bad",
        subject="bad style",
        diff=(
            "diff --git a/drivers/x/y.c b/drivers/x/y.c\n"
            "index 1111111..2222222 100644\n"
            "--- a/drivers/x/y.c\n"
            "+++ b/drivers/x/y.c\n"
            "@@ -1,2 +1,3 @@\n"
            " int a;\n"
            "+int b=2;\n"
            " int c;\n"
        ),
    )
    findings = sam.run_checkpatch(bad)
    assert findings
    assert any(f["severity"] == "blocker" for f in findings)
    assert all(f["tool"] == "checkpatch" for f in findings)
