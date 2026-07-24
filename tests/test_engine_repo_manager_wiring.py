"""Blueprint Sec. 21.1 — ApplicabilityGate wired into intelligent_review endpoint.

T90 superseded T89's series-level apply_patch() call with a per-patch gate.
These tests verify the T90 wiring: gate construction, graceful degradation on
bad repo path, and gate-disabled behavior when kernel_path is absent.

TB89-1  ApplicabilityGate is constructed and passed to IntelligentReviewEngine
         when kernel_path is set.  Gate is monkeypatched so no real git I/O.

TB89-2  When _default_kernel_path returns None, gate=None is passed to the
         engine and the endpoint returns 200.

TB89-3  When RepositoryManagerImpl raises ValueError (not a git repo), the
         endpoint catches the error, passes gate=None to the engine, and
         still returns 200.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.patch_manager import PatchManagerImpl
from kri.web import create_app

from .conftest import LORE_CACHE, MAINTAINERS_PATH, V5_FIXTURE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    lm = LoreManagerImpl(LoreConfig(
        cache_dir=LORE_CACHE,
        inbox="all",
        maintainers_path=MAINTAINERS_PATH if MAINTAINERS_PATH.exists() else None,
        offline=True,
    ))
    pm = PatchManagerImpl(lore_manager=lm)
    return TestClient(create_app(lore_manager=lm, patch_manager=pm))


class _CapturingEngine:
    """Stand-in for IntelligentReviewEngine that records constructor kwargs."""

    captured: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _CapturingEngine.captured = dict(kwargs)

    def review(self, series):  # noqa: ANN001
        class _R:
            def model_dump(self_inner) -> dict[str, Any]:
                return {"series_id": series.series_id, "patches": [], "metadata": {}}
        return _R()


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _CapturingEngine.captured = {}
    monkeypatch.setattr("kri.llm.reviewer.IntelligentReviewEngine", _CapturingEngine)


def _mbox_body() -> str:
    return V5_FIXTURE.read_text(encoding="utf-8", errors="replace")


def _post(client: TestClient) -> Any:
    return client.post("/api/review/intelligent", json={"mbox": _mbox_body()})


# ---------------------------------------------------------------------------
# TB89-1: ApplicabilityGate passed to engine when kernel_path set
# ---------------------------------------------------------------------------


def test_TB89_gate_passed_to_engine_when_kernel_path_set(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When kernel_path is set and RepositoryManagerImpl construction succeeds,
    an ApplicabilityGate instance must be passed to IntelligentReviewEngine."""
    mock_rm = MagicMock()
    mock_gate = MagicMock()

    monkeypatch.setattr("kri.web.app._default_kernel_path", lambda: tmp_path)
    monkeypatch.setattr("kri.web.app.RepositoryManagerImpl", lambda cfg: mock_rm)
    monkeypatch.setattr("kri.web.app.ApplicabilityGate", lambda rm: mock_gate)

    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert _CapturingEngine.captured.get("gate") is mock_gate


# ---------------------------------------------------------------------------
# TB89-2: gate=None when no kernel path available
# ---------------------------------------------------------------------------


def test_TB89_gate_none_when_no_kernel_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _default_kernel_path returns None, gate=None must be passed to
    IntelligentReviewEngine and the endpoint still returns 200."""
    monkeypatch.setattr("kri.web.app._default_kernel_path", lambda: None)

    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert _CapturingEngine.captured.get("gate") is None


# ---------------------------------------------------------------------------
# TB89-3: gate=None on bad repo path (ValueError from constructor)
# ---------------------------------------------------------------------------


def test_TB89_gate_none_on_bad_repo_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If RepositoryManagerImpl raises ValueError (not a git repo), the endpoint
    must catch it, pass gate=None to the engine, and return 200 — not 500."""
    def _raise_value_error(cfg):  # noqa: ANN001
        raise ValueError(f"not a git repository: {cfg.repo_path}")

    monkeypatch.setattr("kri.web.app._default_kernel_path", lambda: tmp_path)
    monkeypatch.setattr("kri.web.app.RepositoryManagerImpl", _raise_value_error)

    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert _CapturingEngine.captured.get("gate") is None
