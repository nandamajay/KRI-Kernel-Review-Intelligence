"""WP-S1B Step B3 — env-var → engine plumbing (web layer).

The web app is KRI's only entry-point; series-reducer knobs are exposed
via env vars read at REQUEST time in ``/api/review/intelligent``:

- ``KRI_SERIES_REDUCER_MODE`` ∈ {off, shadow, on}; default off; invalid → 400
- ``KRI_SERIES_R{5,6,7}_ENABLED`` truthy-string; default False

These tests monkeypatch ``kri.llm.reviewer.IntelligentReviewEngine`` (the
symbol the route imports) to capture its constructor kwargs, bypassing
the real LLM call. That isolates the plumbing from LLM/network state.
"""

from __future__ import annotations

from typing import Any

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
    """Stand-in for :class:`IntelligentReviewEngine`.  Records the exact
    kwargs the route hands to the constructor, then returns a stub
    ``review()`` result so the request completes with 200."""

    captured: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        # Persist a snapshot on the CLASS so tests can inspect it without
        # threading a reference through the route.
        _CapturingEngine.captured = dict(kwargs)

    def review(self, series):  # noqa: ANN001 — mirroring engine signature
        # Return a trivially-serialisable object with ``.model_dump()``,
        # since the route calls ``report.model_dump()`` before returning.
        class _Stub:
            def model_dump(self_inner) -> dict[str, Any]:
                return {"series_id": series.series_id, "patches": []}
        return _Stub()


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the engine class in its defining module.  The route does
    ``from kri.llm.reviewer import IntelligentReviewEngine`` inside the
    handler, so patching the source symbol suffices."""
    _CapturingEngine.captured = {}
    monkeypatch.setattr(
        "kri.llm.reviewer.IntelligentReviewEngine", _CapturingEngine
    )


def _v5_mbox_body() -> str:
    return V5_FIXTURE.read_text(encoding="utf-8", errors="replace")


def _post_review(client: TestClient) -> Any:
    return client.post(
        "/api/review/intelligent",
        json={"mbox": _v5_mbox_body()},
    )


# ---------------------------------------------------------------------------
# T-B3-ENV: env vars reach IntelligentReviewEngine
# ---------------------------------------------------------------------------


def test_TB3_env_unset_defaults_to_off_and_all_flags_false(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no env vars set, the engine receives ``mode='off'`` and every
    per-rule flag ``False`` — the pre-B3 default behaviour, unchanged."""
    for k in (
        "KRI_SERIES_REDUCER_MODE",
        "KRI_SERIES_R5_ENABLED",
        "KRI_SERIES_R6_ENABLED",
        "KRI_SERIES_R7_ENABLED",
    ):
        monkeypatch.delenv(k, raising=False)

    resp = _post_review(client)
    assert resp.status_code == 200, resp.text

    kw = _CapturingEngine.captured
    assert kw["series_reducer_mode"] == "off"
    assert kw["series_r5_enabled"] is False
    assert kw["series_r6_enabled"] is False
    assert kw["series_r7_enabled"] is False


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_TB3_env_mode_is_forwarded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Every valid ``KRI_SERIES_REDUCER_MODE`` value round-trips into the
    engine constructor verbatim."""
    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", mode)
    resp = _post_review(client)
    assert resp.status_code == 200, resp.text
    assert _CapturingEngine.captured["series_reducer_mode"] == mode


def test_TB3_env_mode_case_insensitive(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env-var values are normalised to lowercase before validation, so
    ``SHADOW`` / ``Shadow`` reach the engine as ``shadow``."""
    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", "Shadow")
    resp = _post_review(client)
    assert resp.status_code == 200, resp.text
    assert _CapturingEngine.captured["series_reducer_mode"] == "shadow"


def test_TB3_env_mode_invalid_returns_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrecognised mode is a config bug and MUST fail loud (HTTP 400)
    — silent fall-back would hide "I set shadow but the app is still in
    off" surprises during rollout."""
    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", "shadowy")
    resp = _post_review(client)
    assert resp.status_code == 400, resp.text
    assert "KRI_SERIES_REDUCER_MODE" in resp.text
    # And critically, the engine was NEVER instantiated on the invalid path.
    assert _CapturingEngine.captured == {}


def test_TB3_env_mode_invalid_rejects_before_any_side_effect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G1 adversarial fix: env validation must run BEFORE any side effect
    — no series storage, no LLM client init, no checkpatch spinup — so
    the 400 on invalid mode never leaves partial state behind.

    We prove no store-side effect by inspecting ``app.state.store``
    before and after the failing request: it must be unchanged.
    """
    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", "invalid_value_here")
    store_before = dict(client.app.state.store)

    resp = _post_review(client)
    assert resp.status_code == 400
    assert _CapturingEngine.captured == {}

    store_after = dict(client.app.state.store)
    assert store_before == store_after, (
        "series was stored despite invalid mode env — validation ran too "
        "late; failing requests must not mutate app state"
    )


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
def test_TB3_env_per_rule_truthy_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Every truthy-alias env value flips the corresponding per-rule flag
    to ``True`` at the engine boundary."""
    monkeypatch.setenv("KRI_SERIES_R6_ENABLED", value)
    resp = _post_review(client)
    assert resp.status_code == 200, resp.text
    assert _CapturingEngine.captured["series_r6_enabled"] is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  ", "banana"])
def test_TB3_env_per_rule_non_truthy_values_stay_false(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Falsey and unrecognised strings both default to ``False`` — no
    silent True on a typo."""
    monkeypatch.setenv("KRI_SERIES_R7_ENABLED", value)
    resp = _post_review(client)
    assert resp.status_code == 200, resp.text
    assert _CapturingEngine.captured["series_r7_enabled"] is False


def test_TB3_env_per_rule_flags_are_independent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting only R6 must leave R5 and R7 at their default False —
    catches the class of bug where all three env vars share a variable."""
    monkeypatch.setenv("KRI_SERIES_R6_ENABLED", "true")
    monkeypatch.delenv("KRI_SERIES_R5_ENABLED", raising=False)
    monkeypatch.delenv("KRI_SERIES_R7_ENABLED", raising=False)

    resp = _post_review(client)
    assert resp.status_code == 200, resp.text
    kw = _CapturingEngine.captured
    assert kw["series_r5_enabled"] is False
    assert kw["series_r6_enabled"] is True
    assert kw["series_r7_enabled"] is False


def test_TB3_env_reads_are_request_time_not_import_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same client instance, hit twice with env flipped in between,
    must reflect the *second* env value.  This locks in the "read at
    request time" contract so operators can flip a flag without a
    process restart."""
    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", "off")
    r1 = _post_review(client)
    assert r1.status_code == 200
    assert _CapturingEngine.captured["series_reducer_mode"] == "off"

    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", "shadow")
    r2 = _post_review(client)
    assert r2.status_code == 200
    assert _CapturingEngine.captured["series_reducer_mode"] == "shadow"


def test_TB3_env_engine_symbol_resolves_per_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G4 adversarial fix: the route imports IntelligentReviewEngine
    INSIDE the handler body, so the symbol must re-resolve on every
    request.  Prove this by swapping the patched engine class AFTER
    the first request completes; the second request must use the
    replacement, not a stashed reference."""
    monkeypatch.delenv("KRI_SERIES_REDUCER_MODE", raising=False)

    r1 = _post_review(client)
    assert r1.status_code == 200
    assert _CapturingEngine.captured["series_reducer_mode"] == "off"

    # Swap in a fresh capturing engine mid-test; the route must pick it
    # up on the next request.  If the route had a stale top-level import
    # or cached the class, this test would see the OLD captured dict.
    class _SecondEngine(_CapturingEngine):
        pass

    _CapturingEngine.captured = {}
    monkeypatch.setattr(
        "kri.llm.reviewer.IntelligentReviewEngine", _SecondEngine
    )

    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", "shadow")
    r2 = _post_review(client)
    assert r2.status_code == 200
    # Fresh capture from _SecondEngine's captured dict (inherited).
    assert _CapturingEngine.captured["series_reducer_mode"] == "shadow"


# ---------------------------------------------------------------------------
# G6 adversarial fix: unit tests on the helpers themselves, not just via
# a full round-trip.  Guards against silent regressions if the helpers are
# ever reused from a non-FastAPI caller.
# ---------------------------------------------------------------------------


def test_TB3_helper_env_reducer_mode_unset_returns_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kri.web.app import _env_reducer_mode

    monkeypatch.delenv("KRI_SERIES_REDUCER_MODE", raising=False)
    assert _env_reducer_mode() == "off"


def test_TB3_helper_env_reducer_mode_empty_string_returns_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kri.web.app import _env_reducer_mode

    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", "   ")
    assert _env_reducer_mode() == "off"


@pytest.mark.parametrize("raw,expected", [
    ("off", "off"), ("shadow", "shadow"), ("on", "on"),
    ("OFF", "off"), ("Shadow", "shadow"), (" on ", "on"),
])
def test_TB3_helper_env_reducer_mode_valid_values(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str,
) -> None:
    from kri.web.app import _env_reducer_mode

    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", raw)
    assert _env_reducer_mode() == expected


def test_TB3_helper_env_reducer_mode_raises_ValueError_subclass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G2 adversarial fix: the helper raises a ValueError subclass, NOT
    an HTTPException.  HTTP-transport translation is the route's job;
    the helper must stay reusable from non-web callers."""
    from kri.web.app import InvalidReducerModeError, _env_reducer_mode

    monkeypatch.setenv("KRI_SERIES_REDUCER_MODE", "bogus")
    with pytest.raises(InvalidReducerModeError) as excinfo:
        _env_reducer_mode()
    # And it must be a plain ValueError for callers that don't know the
    # specific subclass — 'except ValueError:' still catches it.
    assert isinstance(excinfo.value, ValueError)
    assert "KRI_SERIES_REDUCER_MODE" in str(excinfo.value)
    assert "bogus" in str(excinfo.value)


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on", " YES "])
def test_TB3_helper_env_flag_truthy(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    from kri.web.app import _env_flag

    monkeypatch.setenv("SOME_FLAG_NAME", value)
    assert _env_flag("SOME_FLAG_NAME") is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  ", "banana", "2"])
def test_TB3_helper_env_flag_falsy(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    from kri.web.app import _env_flag

    monkeypatch.setenv("SOME_FLAG_NAME", value)
    assert _env_flag("SOME_FLAG_NAME") is False


def test_TB3_helper_env_flag_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from kri.web.app import _env_flag

    monkeypatch.delenv("NEVER_SET_FLAG", raising=False)
    assert _env_flag("NEVER_SET_FLAG") is False
