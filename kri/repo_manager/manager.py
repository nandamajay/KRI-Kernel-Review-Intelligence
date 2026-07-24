"""Repository Manager (Blueprint Sec. 21.1): deterministic git tree operations.

Wraps a local kernel git clone (via GitPython) to provide:
  * ``checkout(version)``   -- exact checkout by tag / branch / commit.
  * ``apply_patch(series)`` -- apply a PatchSeries, returning a structured result
                               (never raising on a rejected patch).
  * ``blame(file, line)``   -- commit history for a file:line.
  * ``diff(a, b)``          -- unified diff between two commits.

Shallow clones: the MVP kernel tree is a ``--depth 1`` clone. This manager detects
shallowness and degrades gracefully (blame/diff over unavailable history return a
structured, empty-but-explained result rather than crashing). ``ensure_history()``
can deepen/fetch on demand when the network permits.

Only this module and the Lore Manager perform I/O against external state
(SPEC.md Sec. 8). Determinism: a given ``version`` string always resolves to the
same tree; no wall-clock or RNG affects results. Domain-agnostic throughout.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from git import GitCommandError, InvalidGitRepositoryError, Repo
from git.exc import BadName, BadObject, NoSuchPathError
from git.objects import Commit

# git rev-parse failures for an unknown ref: GitPython raises these, plus ValueError
# for malformed rev strings. Grouped so callers degrade instead of crashing.
_REV_ERRORS = (GitCommandError, BadName, BadObject, ValueError)


@dataclass
class TreeStateInfo:
    """Handle to a checked-out / applied git tree (the ``TreeState`` crossing type)."""

    repo_path: str
    ref: str                      # requested version string
    commit_hash: str              # resolved HEAD commit hash
    is_shallow: bool = False
    applied_series: str | None = None


@dataclass
class ApplyResult:
    """Structured result of :meth:`RepositoryManagerImpl.apply_patch`.

    ``ok`` is False on any reject; ``conflicts`` lists per-patch failure detail so
    callers never have to catch exceptions (SPEC.md DoD)."""

    ok: bool
    tree: TreeStateInfo | None = None
    applied: list[str] = field(default_factory=list)      # patch_ids applied
    failed: list[str] = field(default_factory=list)       # patch_ids that failed
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""


class RepoConfig:
    """Configuration for :class:`RepositoryManagerImpl`."""

    def __init__(
        self,
        repo_path: str | Path,
        allow_fetch: bool = False,
        remote: str = "origin",
    ) -> None:
        self.repo_path = Path(repo_path)
        self.allow_fetch = allow_fetch
        self.remote = remote


class RepositoryManagerImpl:
    """Concrete :class:`kri.common.interfaces.RepositoryManager`."""

    def __init__(self, config: RepoConfig) -> None:
        self._cfg = config
        try:
            self._repo = Repo(str(config.repo_path))
        except (InvalidGitRepositoryError, NoSuchPathError) as exc:
            raise ValueError(f"not a git repository: {config.repo_path}: {exc}") from exc

    # -- properties ----------------------------------------------------------
    @property
    def repo(self) -> Repo:
        return self._repo

    def is_shallow(self) -> bool:
        return (Path(self._repo.git_dir) / "shallow").exists()

    def current_commit(self) -> str:
        return self._repo.head.commit.hexsha

    # -- interface: checkout -------------------------------------------------
    def checkout(self, version: str) -> TreeStateInfo:
        """Check out the tree at an exact tag/branch/commit.

        Resolves ``version`` to a commit and detaches HEAD there for a
        deterministic tree. Raises ``ValueError`` with a clear message if the ref
        is unknown in this (possibly shallow) clone."""
        try:
            commit = self._repo.commit(version)
        except _REV_ERRORS as exc:
            if self._cfg.allow_fetch:
                self.ensure_history(version)
                try:
                    commit = self._repo.commit(version)
                except _REV_ERRORS as exc2:
                    raise ValueError(
                        f"version {version!r} not resolvable even after fetch: {exc2}"
                    ) from exc2
            else:
                raise ValueError(
                    f"version {version!r} not found in clone "
                    f"(shallow={self.is_shallow()}); enable allow_fetch to deepen: {exc}"
                ) from exc
        self._repo.git.checkout(commit.hexsha, force=True)
        return TreeStateInfo(
            repo_path=str(self._cfg.repo_path),
            ref=version,
            commit_hash=commit.hexsha,
            is_shallow=self.is_shallow(),
        )

    def ensure_history(self, version: str | None = None, depth: int | None = None) -> bool:
        """Deepen/fetch history so ``version`` and blame become available.

        Returns True on a successful fetch. No-op (returns False) if fetching is
        disabled. Network op -- confined to this manager."""
        if not self._cfg.allow_fetch:
            return False
        try:
            args = []
            if depth is not None:
                args.append(f"--depth={depth}")
            elif self.is_shallow():
                args.append("--unshallow")
            self._repo.git.fetch(self._cfg.remote, *args)
            if version:
                try:
                    self._repo.git.fetch(self._cfg.remote, version)
                except GitCommandError:
                    pass
            return True
        except GitCommandError:
            return False

    # -- interface: apply_patch ---------------------------------------------
    def apply_patch(self, series: Any) -> ApplyResult:
        """Apply a :class:`~kri.common.models.PatchSeries` to the working tree.

        Resets the working tree to HEAD first so consecutive calls are idempotent —
        dirty state from a prior run does not corrupt the next apply.  Uses
        ``git apply --check`` (dry run) then ``git apply`` per patch, in series order.
        On any reject it records a structured conflict entry and stops. Never raises."""
        try:
            self._reset_tree()
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, message=f"tree reset failed: {exc}")

        patches = getattr(series, "patches", None)
        if patches is None:
            return ApplyResult(ok=False, message="series has no patches")

        applied: list[str] = []
        failed: list[str] = []
        conflicts: list[dict[str, Any]] = []

        for patch in patches:
            diff_text = getattr(patch, "diff", "") or ""
            pid = getattr(patch, "patch_id", "?")
            if not diff_text.strip():
                # No diff (e.g. cover letter) -- skip, not a failure.
                continue
            check = self._git_apply(diff_text, check_only=True)
            if not check.ok:
                failed.append(pid)
                conflicts.append({
                    "patch_id": pid,
                    "subject": getattr(patch, "subject", ""),
                    "stage": "check",
                    "detail": check.stderr.strip(),
                    "files": list(getattr(patch, "files_changed", [])),
                })
                break
            real = self._git_apply(diff_text, check_only=False)
            if not real.ok:
                failed.append(pid)
                conflicts.append({
                    "patch_id": pid,
                    "subject": getattr(patch, "subject", ""),
                    "stage": "apply",
                    "detail": real.stderr.strip(),
                    "files": list(getattr(patch, "files_changed", [])),
                })
                break
            applied.append(pid)

        ok = not failed
        tree = TreeStateInfo(
            repo_path=str(self._cfg.repo_path),
            ref="HEAD",
            commit_hash=self.current_commit(),
            is_shallow=self.is_shallow(),
            applied_series=getattr(series, "series_id", None),
        ) if ok else None
        return ApplyResult(
            ok=ok,
            tree=tree,
            applied=applied,
            failed=failed,
            conflicts=conflicts,
            message="applied cleanly" if ok else f"{len(failed)} patch(es) failed to apply",
        )

    def _reset_tree(self) -> None:
        """Restore the working tree to HEAD.

        Called at the start of apply_patch() so consecutive calls on the same
        Repo object do not accumulate dirty state from prior runs."""
        self._repo.git.checkout("--", ".")
        self._repo.git.clean("-fd")

    def _git_apply(self, diff_text: str, check_only: bool) -> _ProcResult:
        """Run ``git apply`` on a diff via a temp file. Returns a structured result."""
        if not diff_text.endswith("\n"):
            diff_text += "\n"
        tmp: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".diff", delete=False, dir=None
            ) as fh:
                fh.write(diff_text)
                tmp = fh.name
            args = ["apply", "--verbose"]
            if check_only:
                args.append("--check")
            args.append(tmp)
            try:
                self._repo.git.execute(["git", *args])
                return _ProcResult(ok=True, stdout="", stderr="")
            except GitCommandError as exc:
                return _ProcResult(ok=False, stdout=str(exc.stdout), stderr=str(exc.stderr))
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)

    # -- interface: blame ----------------------------------------------------
    def blame(self, file: str, line: int) -> list[dict[str, Any]]:
        """Return the commit history for ``file:line`` as typed dicts.

        On a shallow clone the blame history is limited to what is present; rather
        than crash, this returns whatever git can produce (often a single grafted
        commit) or an empty list with no exception."""
        try:
            entries = self._repo.blame(
                self._repo.head.commit.hexsha, file, L=f"{line},{line}"
            )
        except GitCommandError:
            return []
        out: list[dict[str, Any]] = []
        if not entries:
            return out
        for entry in entries:
            # GitPython yields (commit, lines) pairs; guard defensively.
            if not isinstance(entry, (tuple, list)) or len(entry) < 2:
                continue
            commit, lines = entry[0], entry[1]
            if not isinstance(commit, Commit):
                continue
            if not isinstance(lines, (list, tuple)):
                lines = []
            out.append({
                "commit": commit.hexsha,
                "author": commit.author.name,
                "author_email": commit.author.email,
                "summary": commit.summary,
                "authored_date": commit.authored_datetime.isoformat(),
                "lines": [ln if isinstance(ln, str) else ln.decode("utf-8", "replace")
                          for ln in lines],
                "file": file,
                "line": line,
            })
        return out

    # -- interface: diff -----------------------------------------------------
    def diff(self, commit_a: str, commit_b: str) -> str:
        """Return the unified diff between two commits (``git diff a b``).

        Returns a structured explanation string (prefixed ``# unavailable:``) rather
        than raising when a commit is missing from a shallow clone."""
        try:
            ca = self._repo.commit(commit_a)
            cb = self._repo.commit(commit_b)
        except _REV_ERRORS as exc:
            return f"# unavailable: cannot resolve commits in this clone: {exc}"
        try:
            return self._repo.git.diff(ca.hexsha, cb.hexsha)
        except GitCommandError as exc:
            return f"# unavailable: git diff failed: {exc.stderr}"


@dataclass
class _ProcResult:
    ok: bool
    stdout: str
    stderr: str


def clone_or_open(url: str, dest: str | Path, depth: int | None = 1) -> RepositoryManagerImpl:
    """Open ``dest`` if it is a clone, else shallow-clone ``url`` into it.

    Convenience for bootstrapping; the tests use an existing local clone. Network
    op confined to this module."""
    dest = Path(dest)
    if (dest / ".git").exists() or (dest.exists() and (dest / "HEAD").exists()):
        return RepositoryManagerImpl(RepoConfig(dest))
    args = ["git", "clone"]
    if depth:
        args += ["--depth", str(depth)]
    args += [url, str(dest)]
    subprocess.run(args, check=True)
    return RepositoryManagerImpl(RepoConfig(dest))
