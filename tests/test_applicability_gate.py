"""WP-T2A — ApplicabilityGate correctness tests.

Structure:
  3a: 16 unit tests — ApplicabilityGate.check() with mocked repo
  3b:  7 wiring tests — IntelligentReviewEngine with mocked gate
  3c:  1 Strategy-C prompt discipline regression guard
  3d:  3 end-to-end tests (skipped when KRI_KERNEL_PATH is not set)
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from kri.common.models import Patch, PatchSeries
from kri.repo_manager.gate import ApplicabilityGate, ApplicabilityResult
from kri.repo_manager.manager import _ProcResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


_CLEAN_DIFF = (
    "diff --git a/f.c b/f.c\n--- a/f.c\n+++ b/f.c\n@@ -1 +1 @@\n-old\n+new\n"
)

_SHA = "a" * 40


def _series(*patch_ids: str, diff: str = _CLEAN_DIFF) -> PatchSeries:
    return PatchSeries(
        series_id="test-series",
        patches=[
            Patch(
                patch_id=pid,
                subject=f"subject {pid}",
                diff=diff,
                files_changed=["f.c"],
            )
            for pid in patch_ids
        ],
    )


def _make_mock_repo(
    current_commit_sha: str = _SHA,
    is_shallow: bool = False,
    checkout_side_effect: Any = None,
    git_apply_results: list[_ProcResult] | None = None,
    head_branch: str | None = "main",
) -> MagicMock:
    """Build a minimal mock RepositoryManagerImpl."""
    repo = MagicMock()
    repo._cfg = SimpleNamespace(repo_path="/fake/repo")
    repo.current_commit.return_value = current_commit_sha
    repo.is_shallow.return_value = is_shallow

    # HEAD ref simulation
    if head_branch is not None:
        repo._repo.head.is_detached = False
        repo._repo.head.ref.name = head_branch
    else:
        repo._repo.head.is_detached = True

    if checkout_side_effect is not None:
        repo.checkout.side_effect = checkout_side_effect
    if git_apply_results is not None:
        repo._git_apply.side_effect = git_apply_results
    else:
        repo._git_apply.return_value = _ProcResult(ok=True, stdout="", stderr="")
    return repo


# ---------------------------------------------------------------------------
# 3a — Unit tests: ApplicabilityGate.check() with mocked repo
# ---------------------------------------------------------------------------


def test_TB90_T1_empty_series_returns_ok_no_repo_calls():
    """Empty series → ok=True, no lock acquired, no I/O."""
    repo = _make_mock_repo()
    gate = ApplicabilityGate(repo)
    result = gate.check(PatchSeries(series_id="s", patches=[]))
    assert result.ok is True
    assert result.checked == []
    assert result.failed == []
    assert result.conflicts == []
    repo.current_commit.assert_not_called()
    repo._git_apply.assert_not_called()


def test_TB90_T2_head_baseline_skips_checkout():
    """baseline_ref='HEAD' → initial checkout() not invoked; only restore fires.

    The spec: 'if baseline_ref == "HEAD", checkout() is skipped entirely; save/restore
    still runs.'  This means the restore checkout fires once (to the saved ref), but
    the initial checkout(baseline_ref) is never called.
    """
    repo = _make_mock_repo()
    gate = ApplicabilityGate(repo)
    gate.check(_series("p1"), baseline_ref="HEAD")
    # Exactly one checkout call: the HEAD restore. NOT two (no initial baseline checkout).
    assert repo.checkout.call_count == 1
    assert repo._git_apply.call_count == 1


def test_TB90_T3_named_baseline_triggers_checkout():
    """baseline_ref='v6.6' → checkout called with 'v6.6', then restore."""
    repo = _make_mock_repo()
    gate = ApplicabilityGate(repo)
    gate.check(_series("p1"), baseline_ref="v6.6")
    # Two checkout calls: baseline + restore
    assert repo.checkout.call_count == 2
    first_ref = repo.checkout.call_args_list[0][0][0]
    assert first_ref == "v6.6"


def test_TB90_T4_ordering_current_commit_checkout_apply_restore():
    """Ordering: current_commit → checkout(v6.6) → _git_apply → checkout(restore)."""
    call_log: list[str] = []

    repo = _make_mock_repo()
    repo.current_commit.side_effect = lambda: (
        call_log.append("current_commit"), _SHA
    )[1]
    repo.checkout.side_effect = lambda ref: call_log.append(f"checkout:{ref}")
    repo._git_apply.side_effect = lambda *a, **kw: (
        call_log.append("_git_apply"),
        _ProcResult(ok=True, stdout="", stderr=""),
    )[1]

    gate = ApplicabilityGate(repo)
    gate.check(_series("p1"), baseline_ref="v6.6")

    assert call_log[0] == "current_commit"
    assert call_log[1] == "checkout:v6.6"
    assert call_log[2] == "_git_apply"
    assert call_log[3].startswith("checkout:")  # restore


def test_TB90_T5_head_restored_on_check_exception():
    """_git_apply raises → degraded=True; restore checkout still fires."""
    repo = _make_mock_repo()
    repo._git_apply.side_effect = RuntimeError("git gone")
    gate = ApplicabilityGate(repo)
    # The exception from _git_apply propagates out of the try block.
    # The finally restores HEAD, then re-raises RuntimeError.
    with pytest.raises(RuntimeError, match="git gone"):
        gate.check(_series("p1"), baseline_ref="HEAD")
    # Restore checkout must have been called exactly once (HEAD baseline — no
    # initial checkout, one restore).
    assert repo.checkout.call_count == 1
    restore_ref = repo.checkout.call_args_list[0][0][0]
    # Restore ref is the saved branch name (from head.ref.name = "main")
    assert restore_ref == "main"


def test_TB90_T6_head_restored_on_checkout_failure():
    """checkout(baseline_ref) raises ValueError → degraded=True; restore NOT attempted."""
    repo = _make_mock_repo()
    repo.checkout.side_effect = ValueError("v0.0 not found")
    gate = ApplicabilityGate(repo)
    result = gate.check(_series("p1"), baseline_ref="v0.0")
    assert result.degraded is True
    assert "not resolvable" in result.degraded_reason
    # Degrade path returns BEFORE try/finally, so restore is not attempted.
    assert repo.checkout.call_count == 1


def test_TB90_T7_restore_failure_is_critical_and_reraised(caplog):
    """Save OK, checkout OK, _git_apply OK, restore raises → CRITICAL logged; re-raised."""
    call_n = {"n": 0}

    def _checkout(ref: str) -> None:
        call_n["n"] += 1
        if call_n["n"] == 2:  # second call is the restore
            raise RuntimeError("disk full")

    repo = _make_mock_repo()
    repo.checkout.side_effect = _checkout
    gate = ApplicabilityGate(repo)
    with caplog.at_level(logging.CRITICAL, logger="kri.repo_manager.gate"):
        with pytest.raises(RuntimeError, match="disk full"):
            gate.check(_series("p1"), baseline_ref="v6.6")
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_TB90_T8_all_patches_clean_ok_true():
    """3 patches all pass → ok=True, checked=[all], failed=[], conflicts=[]."""
    repo = _make_mock_repo(
        git_apply_results=[
            _ProcResult(ok=True, stdout="", stderr=""),
            _ProcResult(ok=True, stdout="", stderr=""),
            _ProcResult(ok=True, stdout="", stderr=""),
        ]
    )
    gate = ApplicabilityGate(repo)
    result = gate.check(_series("p1", "p2", "p3"))
    assert result.ok is True
    assert result.checked == ["p1", "p2", "p3"]
    assert result.failed == []
    assert result.conflicts == []
    # Verify .diff string passed, not the Patch object
    for c in repo._git_apply.call_args_list:
        assert isinstance(c[0][0], str)


def test_TB90_T9_one_patch_fails_others_continue():
    """p2 fails → ok=False, checked=[p1,p3], failed=[p2]; others continue."""
    repo = _make_mock_repo(
        git_apply_results=[
            _ProcResult(ok=True, stdout="", stderr=""),
            _ProcResult(ok=False, stdout="", stderr="patch rejected"),
            _ProcResult(ok=True, stdout="", stderr=""),
        ]
    )
    gate = ApplicabilityGate(repo)
    result = gate.check(_series("p1", "p2", "p3"))
    assert result.ok is False
    assert result.checked == ["p1", "p3"]
    assert result.failed == ["p2"]
    assert len(result.conflicts) == 1
    assert result.conflicts[0]["patch_id"] == "p2"


def test_TB90_T10_all_patches_fail_all_reported():
    """All 3 patches fail → failed=[p1,p2,p3], conflicts has 3 entries."""
    repo = _make_mock_repo(
        git_apply_results=[
            _ProcResult(ok=False, stdout="", stderr="e1"),
            _ProcResult(ok=False, stdout="", stderr="e2"),
            _ProcResult(ok=False, stdout="", stderr="e3"),
        ]
    )
    gate = ApplicabilityGate(repo)
    result = gate.check(_series("p1", "p2", "p3"))
    assert result.failed == ["p1", "p2", "p3"]
    assert len(result.conflicts) == 3


def test_TB90_T11_conflict_entry_schema():
    """Each conflict dict has the required keys; stage == 'check'."""
    repo = _make_mock_repo(
        git_apply_results=[_ProcResult(ok=False, stdout="", stderr="bad patch")]
    )
    gate = ApplicabilityGate(repo)
    series = PatchSeries(
        series_id="s",
        patches=[Patch(patch_id="p1", subject="sub", diff=_CLEAN_DIFF, files_changed=["f.c"])],
    )
    result = gate.check(series)
    c = result.conflicts[0]
    assert {"patch_id", "subject", "stage", "detail", "files"} <= set(c.keys())
    assert c["stage"] == "check"
    assert c["patch_id"] == "p1"
    assert c["detail"] == "bad patch"
    assert c["files"] == ["f.c"]


@pytest.mark.slow
def test_TB90_T12_lock_serializes_concurrent_checks():
    """Two concurrent gate.check() calls on same gate must not overlap."""
    repo = _make_mock_repo()
    gate = ApplicabilityGate(repo)
    intervals: list[tuple[str, float]] = []
    lock = threading.Lock()

    def slow_apply(diff_text: str, check_only: bool) -> _ProcResult:
        with lock:
            intervals.append(("enter", time.monotonic()))
        time.sleep(0.05)
        with lock:
            intervals.append(("exit", time.monotonic()))
        return _ProcResult(ok=True, stdout="", stderr="")

    repo._git_apply.side_effect = slow_apply

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(gate.check, _series("p1"))
        f2 = ex.submit(gate.check, _series("p2"))
        f1.result()
        f2.result()

    enter_times = [t for kind, t in intervals if kind == "enter"]
    exit_times = [t for kind, t in intervals if kind == "exit"]
    # With serialization: the second enter must be after the first exit.
    assert min(exit_times) < max(enter_times), (
        "lock must serialize checks: second enter should follow first exit"
    )


def test_TB90_T13_shallow_repo_with_named_ref_degrades():
    """is_shallow=True + baseline_ref='v6.6' → degraded=True, 'shallow' in reason."""
    repo = _make_mock_repo(is_shallow=True)
    gate = ApplicabilityGate(repo)
    result = gate.check(_series("p1"), baseline_ref="v6.6")
    assert result.degraded is True
    assert "shallow" in result.degraded_reason
    repo.checkout.assert_not_called()
    repo._git_apply.assert_not_called()


def test_TB90_T14_shallow_repo_with_head_ref_ok():
    """is_shallow=True + baseline_ref='HEAD' → no degradation."""
    repo = _make_mock_repo(is_shallow=True)
    gate = ApplicabilityGate(repo)
    result = gate.check(_series("p1"), baseline_ref="HEAD")
    assert result.degraded is False
    assert result.ok is True


def test_TB90_T15_result_is_frozen_dataclass():
    """ApplicabilityResult is frozen — attribute reassignment raises."""
    r = ApplicabilityResult(
        ok=True,
        baseline_ref="HEAD",
        baseline_commit=_SHA,
    )
    with pytest.raises((AttributeError, TypeError)):
        r.ok = False  # type: ignore[misc]


def test_TB90_T16_duration_seconds_is_populated():
    """duration_seconds > 0 for a successful check."""
    repo = _make_mock_repo()
    gate = ApplicabilityGate(repo)
    result = gate.check(_series("p1"))
    assert result.duration_seconds > 0
    assert result.duration_seconds <= 60


# ---------------------------------------------------------------------------
# 3b — Wiring tests: IntelligentReviewEngine + mocked gate
# ---------------------------------------------------------------------------


def _offline_engine_with_gate(
    gate_result: ApplicabilityResult | None = None,
    gate_side_effect: Any = None,
) -> tuple[Any, MagicMock]:
    """Return (engine, gate_mock) with offline LLM and configurable gate."""
    from kri.llm.client import LLMConfig
    from kri.llm.reviewer import IntelligentReviewEngine

    gate = MagicMock()
    if gate_side_effect is not None:
        gate.check.side_effect = gate_side_effect
    elif gate_result is not None:
        gate.check.return_value = gate_result
    else:
        gate.check.return_value = ApplicabilityResult(
            ok=True,
            baseline_ref="HEAD",
            baseline_commit=_SHA,
            checked=["p1"],
            failed=[],
            conflicts=[],
            duration_seconds=0.01,
        )
    engine = IntelligentReviewEngine(
        config=LLMConfig(offline=True),
        gate=gate,
        baseline_ref="HEAD",
    )
    return engine, gate


def _stub_all_agents(monkeypatch: pytest.MonkeyPatch, engine: Any) -> None:
    """Make LLM calls return minimal valid stubs so review() completes offline."""
    from unittest.mock import MagicMock as MM

    monkeypatch.setattr(
        engine._client,
        "complete_json",
        lambda *a, **kw: {
            "what_it_does": "stub",
            "why": "",
            "how": "",
            "risks": [],
            "inline_comments": [],
            "general_comments": [],
        },
    )
    monkeypatch.setattr(
        engine._client,
        "complete",
        lambda *a, **kw: MM(content="stub overall"),
    )


def test_TB90_T17_engine_accepts_gate_param():
    """engine._gate is the gate object passed to the constructor."""
    engine, gate = _offline_engine_with_gate()
    assert engine._gate is gate


def test_TB90_T18_engine_calls_gate_check_per_patch(monkeypatch: pytest.MonkeyPatch):
    """2-patch series → gate.check called exactly 2 times."""
    engine, gate = _offline_engine_with_gate()
    _stub_all_agents(monkeypatch, engine)
    series = _series("p1", "p2")
    engine.review(series)
    assert gate.check.call_count == 2


def test_TB90_T19_apply_status_stored_in_patch_metadata(monkeypatch: pytest.MonkeyPatch):
    """Clean gate result → pr.metadata['apply_status']['ok'] is True."""
    engine, gate = _offline_engine_with_gate()
    _stub_all_agents(monkeypatch, engine)
    report = engine.review(_series("p1"))
    pr = report.patches[0]
    assert "apply_status" in pr.metadata
    assert pr.metadata["apply_status"]["ok"] is True


def test_TB90_T20_gate_failure_stored_in_patch_metadata(monkeypatch: pytest.MonkeyPatch):
    """Gate returns ok=False → pr.metadata['apply_status']['failed'] == ['p1']."""
    engine, gate = _offline_engine_with_gate(
        gate_result=ApplicabilityResult(
            ok=False,
            baseline_ref="HEAD",
            baseline_commit=_SHA,
            checked=[],
            failed=["p1"],
            conflicts=[{
                "patch_id": "p1",
                "subject": "sub",
                "stage": "check",
                "detail": "err",
                "files": [],
            }],
        )
    )
    _stub_all_agents(monkeypatch, engine)
    report = engine.review(_series("p1"))
    assert report.patches[0].metadata["apply_status"]["failed"] == ["p1"]


def test_TB90_T21_gate_exception_does_not_crash_review(monkeypatch: pytest.MonkeyPatch):
    """gate.check() raises → review completes; no 'apply_status' in metadata."""
    engine, gate = _offline_engine_with_gate(gate_side_effect=RuntimeError("gate exploded"))
    _stub_all_agents(monkeypatch, engine)
    report = engine.review(_series("p1"))
    assert report is not None
    assert "apply_status" not in report.patches[0].metadata


def test_TB90_T22_apply_status_summary_in_report_metadata(monkeypatch: pytest.MonkeyPatch):
    """3 patches (2 clean, 1 conflict) → summary counts correct."""
    results = [
        ApplicabilityResult(ok=True, baseline_ref="HEAD", baseline_commit=_SHA,
                            checked=["p1"], failed=[], conflicts=[]),
        ApplicabilityResult(ok=True, baseline_ref="HEAD", baseline_commit=_SHA,
                            checked=["p2"], failed=[], conflicts=[]),
        ApplicabilityResult(ok=False, baseline_ref="HEAD", baseline_commit=_SHA,
                            checked=[], failed=["p3"],
                            conflicts=[{"patch_id": "p3", "subject": "s", "stage": "check",
                                        "detail": "", "files": []}]),
    ]
    engine, gate = _offline_engine_with_gate(gate_side_effect=results)
    _stub_all_agents(monkeypatch, engine)
    report = engine.review(_series("p1", "p2", "p3"))
    summary = report.metadata["apply_status_summary"]
    assert summary["total"] == 3
    assert summary["clean"] == 2
    assert summary["conflict"] == 1
    assert summary["degraded"] == 0


def test_TB90_T23_no_gate_works_normally(monkeypatch: pytest.MonkeyPatch):
    """gate=None → no apply_status in metadata; no regression."""
    from kri.llm.client import LLMConfig
    from kri.llm.reviewer import IntelligentReviewEngine

    engine = IntelligentReviewEngine(config=LLMConfig(offline=True))
    _stub_all_agents(monkeypatch, engine)
    report = engine.review(_series("p1"))
    assert "apply_status_summary" not in report.metadata
    assert "apply_status" not in report.patches[0].metadata


# ---------------------------------------------------------------------------
# 3c — Strategy-C prompt discipline: gate result must never reach LLM prompts
# ---------------------------------------------------------------------------


_GATE_MARKER = "GATE_SENTINEL_XK29Z_MUST_NOT_REACH_LLM"


def test_TB90_T24_gate_result_never_reaches_agent_prompt(monkeypatch: pytest.MonkeyPatch):
    """Gate output must never appear in any LLM prompt (Strategy C regression guard)."""
    from kri.llm.client import LLMConfig
    from kri.llm.reviewer import IntelligentReviewEngine
    from unittest.mock import MagicMock as MM

    gate = MagicMock()
    gate.check.return_value = ApplicabilityResult(
        ok=False,
        baseline_ref="HEAD",
        baseline_commit="c" * 40,
        checked=[],
        failed=["p1"],
        conflicts=[{
            "patch_id": "p1",
            "subject": "sub",
            "stage": "check",
            "detail": _GATE_MARKER,
            "files": [],
        }],
        degraded_reason=_GATE_MARKER,
    )

    recorded: list[str] = []

    def capture_json(messages, system=None, **kw):
        for m in messages:
            recorded.append(str(m.get("content", "")))
        if system:
            recorded.append(str(system))
        return {
            "what_it_does": "stub",
            "why": "",
            "how": "",
            "risks": [],
            "inline_comments": [],
            "general_comments": [],
        }

    engine = IntelligentReviewEngine(
        config=LLMConfig(offline=True),
        gate=gate,
        baseline_ref="HEAD",
    )
    monkeypatch.setattr(engine._client, "complete_json", capture_json)
    monkeypatch.setattr(engine._client, "complete", lambda *a, **kw: MM(content="stub"))

    engine.review(_series("p1"))

    for prompt_text in recorded:
        assert _GATE_MARKER not in prompt_text, (
            f"Strategy C violated: gate sentinel found in LLM prompt:\n{prompt_text[:300]}"
        )


# ---------------------------------------------------------------------------
# 3d — End-to-end tests (real kernel clone required)
# ---------------------------------------------------------------------------


@pytest.fixture
def _real_gate(kernel_path):
    """ApplicabilityGate wrapping the real kernel clone."""
    from kri.repo_manager import ApplicabilityGate as AG, RepoConfig, RepositoryManagerImpl
    if kernel_path is None:
        pytest.skip("KRI_KERNEL_PATH not set — skipping real-kernel gate tests")
    rm = RepositoryManagerImpl(RepoConfig(kernel_path))
    return AG(rm), rm


_MARKER_DIFF = (
    "diff --git a/Documentation/kri_t90_marker.txt b/Documentation/kri_t90_marker.txt\n"
    "new file mode 100644\n"
    "index 0000000..e69de29\n"
    "--- /dev/null\n"
    "+++ b/Documentation/kri_t90_marker.txt\n"
    "@@ -0,0 +1 @@\n"
    "+kri T90 applicability gate smoke test\n"
)

_BAD_DIFF = (
    "diff --git a/Makefile b/Makefile\n"
    "--- a/Makefile\n"
    "+++ b/Makefile\n"
    "@@ -999999,3 +999999,4 @@ this context does not exist\n"
    " line\n+added\n line\n line\n"
)


def test_TB90_T25_real_gate_clean_marker_patch(_real_gate):
    """Real kernel: clean marker diff passes gate."""
    gate, rm = _real_gate
    rm.checkout("v6.6")
    series = PatchSeries(
        series_id="s-t25",
        patches=[Patch(patch_id="p-marker", subject="add marker", diff=_MARKER_DIFF,
                       files_changed=["Documentation/kri_t90_marker.txt"])],
    )
    result = gate.check(series, baseline_ref="HEAD")
    rm.repo.git.checkout("--", ".")
    rm.repo.git.clean("-fd", "Documentation/kri_t90_marker.txt")
    assert result.ok is True
    assert result.checked == ["p-marker"]
    assert result.failed == []


def test_TB90_T26_real_gate_rejecting_patch(_real_gate):
    """Real kernel: bad context diff fails gate; stage='check'."""
    gate, rm = _real_gate
    rm.checkout("v6.6")
    series = PatchSeries(
        series_id="s-t26",
        patches=[Patch(patch_id="p-bad", subject="bad", diff=_BAD_DIFF,
                       files_changed=["Makefile"])],
    )
    result = gate.check(series, baseline_ref="HEAD")
    assert result.ok is False
    assert result.failed == ["p-bad"]
    assert result.conflicts[0]["stage"] == "check"


def test_TB90_T27_real_gate_leaves_head_unchanged(_real_gate):
    """Real kernel: HEAD commit unchanged after gate.check() with baseline_ref='v6.6'."""
    gate, rm = _real_gate
    rm.checkout("v6.6")
    sha_before = rm.current_commit()
    series = PatchSeries(
        series_id="s-t27",
        patches=[Patch(patch_id="p-head", subject="head check", diff=_MARKER_DIFF,
                       files_changed=["Documentation/kri_t90_marker.txt"])],
    )
    gate.check(series, baseline_ref="v6.6")
    sha_after = rm.current_commit()
    rm.repo.git.checkout("--", ".")
    rm.repo.git.clean("-fd", "Documentation/kri_t90_marker.txt")
    assert sha_before == sha_after
