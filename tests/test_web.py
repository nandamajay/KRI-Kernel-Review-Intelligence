"""Tests for the FastAPI web app (offline, injected managers)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.patch_manager import PatchManagerImpl
from kri.web import create_app

from .conftest import LORE_CACHE, MAINTAINERS_PATH, V5_FIXTURE


@pytest.fixture
def client():
    lm = LoreManagerImpl(LoreConfig(
        cache_dir=LORE_CACHE,
        inbox="all",
        maintainers_path=MAINTAINERS_PATH if MAINTAINERS_PATH.exists() else None,
        offline=True,
    ))
    pm = PatchManagerImpl(lore_manager=lm)
    return TestClient(create_app(lore_manager=lm, patch_manager=pm))


def test_index_serves_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "KRI" in r.text


def test_submit_mbox_returns_series(client: TestClient) -> None:
    if not V5_FIXTURE.exists():
        pytest.skip("fixture missing")
    import gzip

    mbox = gzip.decompress(V5_FIXTURE.read_bytes()).decode("utf-8", "replace")
    r = client.post("/api/series", json={"mbox": mbox})
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == 5
    assert len(data["patches"]) == 2


def test_submit_requires_input(client: TestClient) -> None:
    r = client.post("/api/series", json={})
    assert r.status_code == 400


def test_get_series_and_reviews_roundtrip(client: TestClient) -> None:
    if not V5_FIXTURE.exists():
        pytest.skip("fixture missing")
    r = client.post(
        "/api/series",
        json={"lore_ref": "20260630021510.821919-1-YLCHANG2@nuvoton.com"},
    )
    assert r.status_code == 200
    sid = r.json()["series_id"]

    r2 = client.get(f"/api/series/{sid}")
    assert r2.status_code == 200
    assert r2.json()["series_id"] == sid

    r3 = client.get(f"/api/series/{sid}/reviews")
    assert r3.status_code == 200
    reviews = r3.json()["reviews"]
    # every patch present; at least one maintainer review with a resolvable url
    assert reviews
    all_reviews = [c for cs in reviews.values() for c in cs]
    assert any(c["is_maintainer"] for c in all_reviews)
    assert all(c["source_url"].startswith("https://") for c in all_reviews)


def test_get_missing_series_404(client: TestClient) -> None:
    assert client.get("/api/series/nope@nowhere").status_code == 404
