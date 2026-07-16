"""Shared pytest fixtures for KRI Sprint-1 tests.

All fixtures are offline: they use the cached lore mbox fixtures under
``data/lore_cache/`` and the local kernel clone under ``data/kernel/linux``. Tests
that require the kernel tree or a specific tool skip gracefully if unavailable, so
the suite is deterministic and CI-friendly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]              # .../kri
WORKSPACE_ROOT = REPO_ROOT.parent                            # .../KRI_Kernel_Review_Intelligence
DATA_ROOT = WORKSPACE_ROOT / "data"
LORE_CACHE = DATA_ROOT / "lore_cache"
KERNEL_PATH = DATA_ROOT / "kernel" / "linux"
MAINTAINERS_PATH = KERNEL_PATH / "MAINTAINERS"

# Canonical fixtures (readable name + root message-id).
V5_FIXTURE = LORE_CACHE / "20260630021510_821919-3-YLCHANG2_nuvoton_com.mbox.gz"
V5_ROOT_ID = "20260630021510.821919-1-YLCHANG2@nuvoton.com"
V6_FIXTURE = LORE_CACHE / "20260708093506_895481-1-YLCHANG2_nuvoton_com.mbox.gz"
V6_ROOT_ID = "20260708093506.895481-1-YLCHANG2@nuvoton.com"
SINGLE_FIXTURE = LORE_CACHE / "20260703123314_147977-1-syed_sabakareem_amd_com.mbox.gz"


@pytest.fixture
def lore_cache_dir() -> Path:
    return LORE_CACHE


@pytest.fixture
def maintainers_path() -> Path | None:
    return MAINTAINERS_PATH if MAINTAINERS_PATH.exists() else None


@pytest.fixture
def kernel_path() -> Path:
    if not KERNEL_PATH.exists():
        pytest.skip("kernel clone not present")
    return KERNEL_PATH


@pytest.fixture
def lore_manager(lore_cache_dir: Path, maintainers_path: Path | None):
    from kri.lore_manager import LoreConfig, LoreManagerImpl

    return LoreManagerImpl(LoreConfig(
        cache_dir=lore_cache_dir,
        inbox="all",
        maintainers_path=maintainers_path,
        offline=True,
    ))


@pytest.fixture
def v5_thread(lore_manager):
    if not V5_FIXTURE.exists():
        pytest.skip("v5 fixture not present")
    return lore_manager.load_cached(V5_FIXTURE)


@pytest.fixture
def v6_thread(lore_manager):
    if not V6_FIXTURE.exists():
        pytest.skip("v6 fixture not present")
    return lore_manager.load_cached(V6_FIXTURE)


@pytest.fixture
def patch_manager(lore_manager):
    from kri.patch_manager import PatchManagerImpl

    return PatchManagerImpl(lore_manager=lore_manager)
