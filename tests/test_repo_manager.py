"""Tests for the Repository Manager against the local (shallow) kernel clone."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from git import GitCommandError

from kri.common.models import Patch, PatchSeries
from kri.repo_manager import ApplyResult, RepoConfig, RepositoryManagerImpl
from kri.repo_manager import manager as repo_mod


@pytest.fixture
def repo(kernel_path):
    return RepositoryManagerImpl(RepoConfig(kernel_path))


def test_open_repo_and_head(repo: RepositoryManagerImpl) -> None:
    assert len(repo.current_commit()) == 40


def test_checkout_tag_is_exact_and_deterministic(repo: RepositoryManagerImpl) -> None:
    a = repo.checkout("v6.6")
    b = repo.checkout("v6.6")
    assert a.commit_hash == b.commit_hash
    assert a.ref == "v6.6"


def test_checkout_unknown_ref_raises_clear_error(repo: RepositoryManagerImpl) -> None:
    with pytest.raises(ValueError) as exc:
        repo.checkout("v0.0-does-not-exist")
    assert "not found" in str(exc.value) or "not resolvable" in str(exc.value)


def test_shallow_clone_detected(repo: RepositoryManagerImpl) -> None:
    # The MVP clone is --depth 1; blame/diff degrade gracefully rather than crash.
    assert isinstance(repo.is_shallow(), bool)


def test_blame_returns_typed_entries_or_empty(repo: RepositoryManagerImpl) -> None:
    entries = repo.blame("sound/soc/soc-core.c", 1)
    assert isinstance(entries, list)
    for e in entries:
        assert {"commit", "author", "summary", "file", "line"} <= set(e.keys())
        assert e["file"] == "sound/soc/soc-core.c"


def test_diff_same_commit_is_empty(repo: RepositoryManagerImpl) -> None:
    assert repo.diff("HEAD", "HEAD") == ""


def test_diff_missing_commit_is_structured_not_raised(repo: RepositoryManagerImpl) -> None:
    out = repo.diff("deadbeefdeadbeef", "v6.6")
    assert out.startswith("# unavailable:")


def test_apply_clean_patch(repo: RepositoryManagerImpl) -> None:
    # A trivial addition to an existing file that applies against v6.6.
    repo.checkout("v6.6")
    diff = (
        "diff --git a/Documentation/kri_test_marker.txt "
        "b/Documentation/kri_test_marker.txt\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
        "--- /dev/null\n"
        "+++ b/Documentation/kri_test_marker.txt\n"
        "@@ -0,0 +1 @@\n"
        "+kri apply-patch smoke test\n"
    )
    series = PatchSeries(
        series_id="s-apply",
        patches=[Patch(patch_id="p1", subject="add marker", diff=diff,
                       files_changed=["Documentation/kri_test_marker.txt"])],
    )
    result = repo.apply_patch(series)
    assert isinstance(result, ApplyResult)
    assert result.ok is True
    assert result.applied == ["p1"]
    # clean up the working tree so the test is repeatable
    repo.repo.git.checkout("--", ".")
    repo.repo.git.clean("-fd", "Documentation/kri_test_marker.txt")


def test_apply_rejecting_patch_returns_structured_failure(repo: RepositoryManagerImpl) -> None:
    repo.checkout("v6.6")
    bad = (
        "diff --git a/Makefile b/Makefile\n"
        "--- a/Makefile\n"
        "+++ b/Makefile\n"
        "@@ -999999,3 +999999,4 @@ this context does not exist\n"
        " line\n+added\n line\n line\n"
    )
    series = PatchSeries(
        series_id="s-bad",
        patches=[Patch(patch_id="pbad", subject="bad", diff=bad,
                       files_changed=["Makefile"])],
    )
    result = repo.apply_patch(series)
    assert result.ok is False
    assert result.failed == ["pbad"]
    assert result.conflicts and result.conflicts[0]["patch_id"] == "pbad"
    assert result.conflicts[0]["stage"] in ("check", "apply")


# ---------------------------------------------------------------------------
# Mocked-network / degraded-path coverage (no real clone or network needed).
# ---------------------------------------------------------------------------


class _FakeGit:
    """Stand-in for repo.git recording fetch/checkout/diff calls."""

    def __init__(self) -> None:
        self.fetch_calls: list[tuple] = []

    def fetch(self, *args):
        self.fetch_calls.append(args)
        return ""

    def checkout(self, *args, **kwargs):
        return ""


class _FakeRepo:
    def __init__(self, git_dir="/tmp/fake/.git", commits=None, shallow=False):
        self.git_dir = git_dir
        self.git = _FakeGit()
        self._commits = commits or {}
        self._shallow = shallow
        self.head = SimpleNamespace(commit=SimpleNamespace(hexsha="0" * 40))

    def commit(self, rev):
        if rev in self._commits:
            return self._commits[rev]
        raise GitCommandError("commit", 128, b"", b"unknown revision")


def _make_manager(repo: _FakeRepo, allow_fetch=False) -> RepositoryManagerImpl:
    mgr = object.__new__(RepositoryManagerImpl)
    mgr._cfg = RepoConfig("/tmp/fake", allow_fetch=allow_fetch)
    mgr._repo = repo  # type: ignore[assignment]
    return mgr


def test_ensure_history_disabled_is_noop() -> None:
    mgr = _make_manager(_FakeRepo(), allow_fetch=False)
    assert mgr.ensure_history("v6.6") is False


def test_ensure_history_fetches_with_depth() -> None:
    repo = _FakeRepo()
    mgr = _make_manager(repo, allow_fetch=True)
    assert mgr.ensure_history("v6.6", depth=50) is True
    assert repo.git.fetch_calls  # a fetch was issued
    assert any("--depth=50" in call for call in repo.git.fetch_calls)


def test_ensure_history_unshallow_when_shallow(monkeypatch) -> None:
    repo = _FakeRepo(shallow=True)
    mgr = _make_manager(repo, allow_fetch=True)
    monkeypatch.setattr(mgr, "is_shallow", lambda: True)
    assert mgr.ensure_history() is True
    assert any("--unshallow" in call for call in repo.git.fetch_calls)


def test_ensure_history_version_fetch_failure_is_swallowed(monkeypatch) -> None:
    repo = _FakeRepo()

    def boom(remote, *args):
        if args and args[0] == "badref":
            raise GitCommandError("fetch", 1, b"", b"no such ref")
        repo.git.fetch_calls.append(args)

    monkeypatch.setattr(repo.git, "fetch", boom)
    mgr = _make_manager(repo, allow_fetch=True)
    # version-specific fetch fails but overall ensure_history still returns True
    assert mgr.ensure_history("badref") is True


def test_ensure_history_returns_false_on_fetch_error(monkeypatch) -> None:
    repo = _FakeRepo()

    def boom(*args, **kwargs):
        raise GitCommandError("fetch", 1, b"", b"network down")

    monkeypatch.setattr(repo.git, "fetch", boom)
    mgr = _make_manager(repo, allow_fetch=True)
    assert mgr.ensure_history() is False


def test_checkout_fetch_fallback_succeeds(monkeypatch) -> None:
    commit = SimpleNamespace(hexsha="a" * 40)
    repo = _FakeRepo()
    mgr = _make_manager(repo, allow_fetch=True)
    monkeypatch.setattr(mgr, "is_shallow", lambda: True)

    calls = {"n": 0}

    def commit_lookup(rev):
        calls["n"] += 1
        if calls["n"] == 1:
            raise GitCommandError("commit", 128, b"", b"unknown")
        return commit

    monkeypatch.setattr(repo, "commit", commit_lookup)
    monkeypatch.setattr(mgr, "ensure_history", lambda v: True)
    tree = mgr.checkout("v6.6")
    assert tree.commit_hash == "a" * 40
    assert tree.ref == "v6.6"


def test_checkout_fetch_fallback_still_missing_raises(monkeypatch) -> None:
    repo = _FakeRepo()
    mgr = _make_manager(repo, allow_fetch=True)
    monkeypatch.setattr(mgr, "is_shallow", lambda: True)
    monkeypatch.setattr(mgr, "ensure_history", lambda v: False)
    with pytest.raises(ValueError) as exc:
        mgr.checkout("v0.0-missing")
    assert "not resolvable even after fetch" in str(exc.value)


def test_blame_parses_typed_entries(monkeypatch) -> None:
    repo = _FakeRepo()
    mgr = _make_manager(repo)

    class FakeCommit:
        hexsha = "b" * 40
        summary = "fix things"
        author = SimpleNamespace(name="Dev", email="dev@example.com")
        authored_datetime = __import__("datetime").datetime(2020, 1, 1)

    fake_commit = FakeCommit()
    # blame yields (commit, lines); include a bytes line to exercise decode branch.
    monkeypatch.setattr(repo, "blame", lambda *a, **k: [(fake_commit, ["ok", b"bytes"])],
                        raising=False)
    monkeypatch.setattr(repo_mod, "Commit", FakeCommit)
    out = mgr.blame("some/file.c", 10)
    assert len(out) == 1
    assert out[0]["commit"] == "b" * 40
    assert out[0]["author"] == "Dev"
    assert out[0]["lines"] == ["ok", "bytes"]
    assert out[0]["line"] == 10


def test_blame_skips_malformed_entries(monkeypatch) -> None:
    repo = _FakeRepo()
    mgr = _make_manager(repo)
    monkeypatch.setattr(repo, "blame",
                        lambda *a, **k: [("not-a-pair",), None, (object(), ["x"])],
                        raising=False)
    monkeypatch.setattr(repo_mod, "Commit", type("C", (), {}))
    assert mgr.blame("f.c", 1) == []


def test_blame_gitcommanderror_returns_empty(monkeypatch) -> None:
    repo = _FakeRepo()
    mgr = _make_manager(repo)

    def boom(*a, **k):
        raise GitCommandError("blame", 128, b"", b"no history")

    monkeypatch.setattr(repo, "blame", boom, raising=False)
    assert mgr.blame("f.c", 1) == []


def test_clone_or_open_opens_existing(tmp_path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(repo_mod, "Repo", lambda p: _FakeRepo())
    mgr = repo_mod.clone_or_open("https://example/repo.git", tmp_path)
    assert isinstance(mgr, RepositoryManagerImpl)


def test_clone_or_open_clones_when_absent(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "newclone"
    ran = {}

    def fake_run(args, check):
        ran["args"] = args
        (dest / ".git").mkdir(parents=True)

    monkeypatch.setattr(repo_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(repo_mod, "Repo", lambda p: _FakeRepo())
    mgr = repo_mod.clone_or_open("https://example/repo.git", dest, depth=1)
    assert isinstance(mgr, RepositoryManagerImpl)
    assert "--depth" in ran["args"]
