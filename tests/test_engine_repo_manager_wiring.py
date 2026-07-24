"""Blueprint Sec. 21.1 — repo_manager wired into intelligent_review endpoint.

Three tests:

TB89-1  apply_status present in the response body when KRI_KERNEL_PATH is set
         to a valid repo.  RepositoryManagerImpl.apply_patch is monkeypatched so
         no real git I/O is needed.

TB89-2  apply_status is {"status": "skipped"} when _default_kernel_path returns
         None (no kernel clone available — the common CI/offline case).

TB89-3  apply_status["status"] == "error" when RepositoryManagerImpl.__init__
         raises ValueError (bad repo_path) — confirms the try/except guard fires
         and the endpoint still returns 200 rather than 500.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.patch_manager import PatchManagerImpl
from kri.repo_manager.manager import ApplyResult
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


class _StubEngine:
    """Minimal IntelligentReviewEngine stand-in.  Returns a serialisable report."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    def review(self, series):  # noqa: ANN001
        class _R:
            def model_dump(self_inner) -> dict[str, Any]:
                return {"series_id": series.series_id, "patches": [], "metadata": {}}
        return _R()


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kri.llm.reviewer.IntelligentReviewEngine", _StubEngine)


def _mbox_body() -> str:
    return V5_FIXTURE.read_text(encoding="utf-8", errors="replace")


def _post(client: TestClient) -> Any:
    return client.post("/api/review/intelligent", json={"mbox": _mbox_body()})


# ---------------------------------------------------------------------------
# TB89-1: apply_status in response when kernel_path set and apply succeeds
# ---------------------------------------------------------------------------


def test_TB89_apply_status_present_when_kernel_path_set(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """apply_status key must be present in response metadata when kernel_path
    is configured.  apply_patch is stubbed to return a clean ApplyResult."""
    stub_result = ApplyResult(ok=True, applied=["p1"], failed=[], conflicts=[], message="applied cleanly")

    mock_rm = MagicMock()
    mock_rm.apply_patch.return_value = stub_result

    monkeypatch.setattr("kri.web.app._default_kernel_path", lambda: tmp_path)
    monkeypatch.setattr(
        "kri.web.app.RepositoryManagerImpl",
        lambda cfg: mock_rm,
    )

    resp = _post(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "metadata" in body, "response must contain metadata key"
    assert "apply_status" in body["metadata"], "metadata must contain apply_status"
    assert body["metadata"]["apply_status"]["status"] == "ok"
    assert body["metadata"]["apply_status"]["applied"] == ["p1"]


# ---------------------------------------------------------------------------
# TB89-2: apply_status is "skipped" when no kernel path available
# ---------------------------------------------------------------------------


def test_TB89_apply_status_skipped_when_no_kernel_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _default_kernel_path returns None (no kernel clone), the endpoint
    must set apply_status to {status: skipped} and still return 200."""
    monkeypatch.setattr("kri.web.app._default_kernel_path", lambda: None)

    resp = _post(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metadata"]["apply_status"] == {"status": "skipped"}


# ---------------------------------------------------------------------------
# TB89-3: apply_status error on bad repo path (ValueError from constructor)
# ---------------------------------------------------------------------------


def test_TB89_apply_status_error_on_bad_repo_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If RepositoryManagerImpl.__init__ raises ValueError (not a git repo),
    the endpoint must capture the error as apply_status["status"]=="error"
    and still return 200 — not 500."""
    def _raise_value_error(cfg):  # noqa: ANN001
        raise ValueError(f"not a git repository: {cfg.repo_path}")

    monkeypatch.setattr("kri.web.app._default_kernel_path", lambda: tmp_path)
    monkeypatch.setattr("kri.web.app.RepositoryManagerImpl", _raise_value_error)

    resp = _post(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metadata"]["apply_status"]["status"] == "error"
    assert "not a git repository" in body["metadata"]["apply_status"]["message"]
