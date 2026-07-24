"""Applicability Gate (Blueprint WP-T2A / Sec. 21.1).

Answers "does this series apply cleanly against a given baseline?" without
permanently mutating HEAD.  Uses ``git apply --check`` (dry run) only — the
working tree is never modified.

## Concurrency model (Option A — single-lock, Tier 1.5)

Every ``ApplicabilityGate`` instance that wraps the same on-disk repository
shares a process-level lock keyed by the resolved repo path.  This prevents
the cross-request race where two concurrent HTTP requests each perform
``checkout()`` against the same git directory.  The lock serialises the
entire save → checkout → per-patch check → restore sequence across all
callers in the process.

Worktree-based isolation (Option B) is the planned Tier 2 upgrade.

## Per-patch check-only limitation

Each patch is checked against the baseline tree in isolation (via a
single-element ``PatchSeries``).  Because ``git apply --check`` does NOT
modify the working tree, patch N is always checked against the baseline
state — not the state after patches 1..N-1 are applied.  For a dependent
multi-patch series (the common case for driver and subsystem work), any
patch that contextually depends on a prior patch will fail the check even
if the full series applies cleanly.  Consumers must treat a ``failed``
result on a non-first patch as "may be a dependency order artefact, not
necessarily a genuine conflict."

The gate is informational metadata only (Strategy C): gate results are
stored in ``PatchReview.metadata["apply_status"]`` and are never injected
into any LLM agent prompt.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from kri.common.models import PatchSeries
from kri.repo_manager.manager import RepositoryManagerImpl

logger = logging.getLogger(__name__)

# Process-level lock registry: maps resolved repo path → Lock.
# Shared across all ApplicabilityGate instances that wrap the same directory.
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD: threading.Lock = threading.Lock()


def _get_path_lock(repo_path: str) -> threading.Lock:
    """Return (creating if needed) the per-path process-level lock."""
    with _PATH_LOCKS_GUARD:
        if repo_path not in _PATH_LOCKS:
            _PATH_LOCKS[repo_path] = threading.Lock()
        return _PATH_LOCKS[repo_path]


GateStage = Literal["none", "check"]


@dataclass(frozen=True)
class ApplicabilityResult:
    """Structured result of :meth:`ApplicabilityGate.check`.

    ``ok`` is True only when every patch in the series passed
    ``git apply --check``.  ``degraded`` means the gate could not run (shallow
    clone, unresolvable ref, etc.) — the review proceeds normally; treat the
    result as unavailable, not as a failure."""

    ok: bool
    baseline_ref: str
    baseline_commit: str
    checked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    stage: GateStage = "check"
    degraded: bool = False
    degraded_reason: str = ""
    duration_seconds: float = 0.0


class ApplicabilityGate:
    """Answer 'does this patch apply cleanly?' without mutating HEAD.

    Thread-safe: all ``check()`` calls on instances that wrap the same
    on-disk repository are serialised via a shared process-level lock.
    """

    def __init__(self, repo: RepositoryManagerImpl) -> None:
        self._repo = repo
        # Shared across all gate instances for the same repo path — prevents
        # the cross-request concurrent-checkout race (Adversarial BREAK-1).
        self._lock: threading.Lock = _get_path_lock(str(repo._cfg.repo_path))

    def check(
        self,
        series: PatchSeries,
        baseline_ref: str = "HEAD",
    ) -> ApplicabilityResult:
        """Run ``git apply --check`` for every patch against ``baseline_ref``.

        Returns an :class:`ApplicabilityResult`.  Never raises — all error
        paths return a degraded result.

        Patches are checked independently against the baseline tree (see module
        docstring for the per-patch isolation caveat on dependent series).
        """
        # Fast path: empty series — no lock, no I/O.
        if not series.patches:
            return ApplicabilityResult(
                ok=True,
                baseline_ref=baseline_ref,
                baseline_commit="",
                checked=[],
                failed=[],
                conflicts=[],
            )

        with self._lock:
            return self._check_locked(series, baseline_ref)

    def _check_locked(
        self, series: PatchSeries, baseline_ref: str
    ) -> ApplicabilityResult:
        """Perform the full save → checkout → check → restore sequence.

        Called only while holding ``self._lock``.
        """
        # time.monotonic(): elapsed-wall-clock telemetry only.
        # Feeds ApplicabilityResult.duration_seconds, which is stored in
        # PatchReview.metadata["apply_status"]["duration_seconds"].
        # Never read by any Decision/Confidence/Report computation.
        t0 = time.monotonic()

        # Save current HEAD ref (branch name when on a branch; hexsha when
        # detached).  Restoring via the branch name avoids permanently
        # detaching HEAD (Adversarial BREAK-2 fix).
        saved_ref: str = ""
        try:
            # Prefer the symbolic branch name so restore re-attaches HEAD.
            head = self._repo._repo.head
            if not head.is_detached:
                saved_ref = head.ref.name
            else:
                saved_ref = self._repo.current_commit()
        except Exception as exc:  # noqa: BLE001
            return ApplicabilityResult(
                ok=False,
                baseline_ref=baseline_ref,
                baseline_commit="",
                degraded=True,
                degraded_reason=f"could not read HEAD: {type(exc).__name__}",
                duration_seconds=time.monotonic() - t0,
            )

        saved_commit = self._repo.current_commit()

        # Shallow-clone degradation — no network in the gate path.
        if self._repo.is_shallow() and baseline_ref != "HEAD":
            return ApplicabilityResult(
                ok=False,
                baseline_ref=baseline_ref,
                baseline_commit=saved_commit,
                degraded=True,
                degraded_reason=f"shallow clone: cannot check out '{baseline_ref}' without fetching",
                duration_seconds=time.monotonic() - t0,
            )

        # Checkout the baseline ref (skipped for HEAD — no-op).
        if baseline_ref != "HEAD":
            try:
                self._repo.checkout(baseline_ref)
            except ValueError:
                return ApplicabilityResult(
                    ok=False,
                    baseline_ref=baseline_ref,
                    baseline_commit=saved_commit,
                    degraded=True,
                    degraded_reason=f"ref '{baseline_ref}' not resolvable",
                    duration_seconds=time.monotonic() - t0,
                )
            except Exception as exc:  # noqa: BLE001
                return ApplicabilityResult(
                    ok=False,
                    baseline_ref=baseline_ref,
                    baseline_commit=saved_commit,
                    degraded=True,
                    degraded_reason=f"checkout failed: {type(exc).__name__}",
                    duration_seconds=time.monotonic() - t0,
                )

        # All degrade paths above return BEFORE this point, so the finally
        # below only runs when we either skipped checkout (HEAD baseline) or
        # successfully checked out baseline_ref.
        checked: list[str] = []
        failed: list[str] = []
        conflicts: list[dict] = []

        try:
            for patch in series.patches:
                diff_text = getattr(patch, "diff", "") or ""
                pid = getattr(patch, "patch_id", "?")
                if not diff_text.strip():
                    # Cover letter / empty diff — skip, not a failure.
                    continue
                # _git_apply is the sanctioned integration point from manager.py.
                result = self._repo._git_apply(diff_text, check_only=True)
                if result.ok:
                    checked.append(pid)
                else:
                    failed.append(pid)
                    conflicts.append({
                        "patch_id": pid,
                        "subject": getattr(patch, "subject", ""),
                        "stage": "check",
                        "detail": result.stderr.strip(),
                        "files": list(getattr(patch, "files_changed", [])),
                    })
                    # Do NOT break — continue checking all patches so the
                    # caller gets a full picture (WP-T2A §2 contract).
        finally:
            # Restore HEAD unconditionally.  On failure, log CRITICAL and
            # re-raise — visible corruption is preferable to silent HEAD drift.
            try:
                self._repo.checkout(saved_ref)
            except Exception as restore_exc:  # noqa: BLE001
                logger.critical(
                    "ApplicabilityGate: HEAD restore FAILED — repo at '%s' "
                    "may be in a corrupt state. Saved ref was %r. Error: %s",
                    self._repo._cfg.repo_path,
                    saved_ref,
                    restore_exc,
                )
                raise

        elapsed = time.monotonic() - t0
        return ApplicabilityResult(
            ok=len(failed) == 0,
            baseline_ref=baseline_ref,
            baseline_commit=saved_commit,
            checked=checked,
            failed=failed,
            conflicts=conflicts,
            duration_seconds=elapsed,
        )
