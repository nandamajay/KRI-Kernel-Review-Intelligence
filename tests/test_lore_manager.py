"""Unit + integration tests for the Lore Manager (offline, cached fixtures)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kri.common.models import ReviewComment, Severity
from kri.lore_manager import LoreConfig, LoreManagerImpl, LoreOfflineError
from kri.lore_manager.maintainers import parse_maintainers

from .conftest import V5_ROOT_ID

# Minimal, domain-agnostic MAINTAINERS excerpt exercising all identity signals:
# an M: address, an R: reviewer, and T: git-tree lines whose account segment differs
# from the listed posting address (the collision-robustness case).
_MAINTAINERS_SAMPLE = """\
SOME SUBSYSTEM
M:\tAda Lovelace <ada@corp.example>
M:\tGrace Hopper <ghopper@kernel.org>
R:\tAlan Turing <turing@kernel.org>
T:\tgit git://git.kernel.org/pub/scm/linux/kernel/git/ada/linux.git
T:\tgit git://git.kernel.org/pub/scm/linux/kernel/git/turing/linux.git
F:\tdrivers/some/

OTHER SUBSYSTEM
M:\tGrace Hopper <grace@other.example>
T:\tgit git://git.kernel.org/pub/scm/linux/kernel/git/ghopper/linux.git
"""


def test_maintainer_index_exact_email_match() -> None:
    idx = parse_maintainers(_MAINTAINERS_SAMPLE)
    assert idx.is_maintainer("ada@corp.example", "Ada Lovelace")
    assert idx.is_maintainer("ghopper@kernel.org")  # email alone is authoritative


def test_maintainer_index_git_tree_account_identity() -> None:
    # Posting address not on any M:/R: line, but local-part is a kernel.org git-tree
    # account -> a strong maintainer identity (the Krzysztof/krzk@kernel.org case).
    idx = parse_maintainers(_MAINTAINERS_SAMPLE)
    assert "ada" in idx.git_usernames
    assert "ada@corp.example" in idx.emails
    assert idx.is_maintainer("ada@kernel.org", "Ada Lovelace")  # via git username
    assert idx.is_maintainer("turing@kernel.org", "Alan Turing")  # reviewer git tree


def test_maintainer_index_rejects_name_only_collision() -> None:
    # Fabricated identity: real maintainer's display name from an unrelated address
    # with no corroborating signal must NOT be flagged (Gatekeeper HIGH finding).
    idx = parse_maintainers(_MAINTAINERS_SAMPLE)
    assert not idx.is_maintainer("ada@evil.example", "Ada Lovelace")
    assert not idx.is_maintainer("attacker@evil.example", "Grace Hopper")
    assert not idx.is_maintainer(None, "Ada Lovelace")  # name alone never suffices


def test_maintainer_index_corroborated_name_via_known_domain() -> None:
    # Grace Hopper appears with two domains in MAINTAINERS; a name match is accepted
    # only when the posting domain is one she actually uses.
    idx = parse_maintainers(_MAINTAINERS_SAMPLE)
    assert idx.is_maintainer("someone-else@other.example", "Grace Hopper")
    assert not idx.is_maintainer("grace@unrelated.example", "Grace Hopper")


def test_fetch_offline_replays_from_cache(lore_manager: LoreManagerImpl) -> None:
    thread = lore_manager.fetch(V5_ROOT_ID)
    assert thread.thread_id == V5_ROOT_ID
    assert len(thread.messages) == 9
    # provenance-resolvable source url uses the configured (generic) inbox
    assert thread.source_url is not None
    assert "/all/" in thread.source_url


def test_fetch_offline_without_cache_raises(tmp_path: Path) -> None:
    lm = LoreManagerImpl(LoreConfig(cache_dir=tmp_path, inbox="all", offline=True))
    with pytest.raises(LoreOfflineError):
        lm.fetch("does-not-exist@nowhere.invalid")


def test_parse_conversation_is_threaded_and_ordered(lore_manager, v5_thread) -> None:
    convo = lore_manager.parse_conversation(v5_thread)
    assert len(convo) == len(v5_thread.messages)
    # every reply appears after its parent
    positions = {row["message_id"]: i for i, row in enumerate(convo)}
    for row in convo:
        parent = row["in_reply_to"]
        if parent in positions:
            assert positions[parent] < positions[row["message_id"]]
    # depth is 0 for roots and >0 for replies
    assert any(r["depth"] == 0 for r in convo)
    assert any(r["depth"] > 0 for r in convo)


def test_parse_conversation_deterministic(lore_manager, v5_thread) -> None:
    a = lore_manager.parse_conversation(v5_thread)
    b = lore_manager.parse_conversation(v5_thread)
    assert a == b


def test_extract_reviews_sets_maintainer_and_provenance(lore_manager, v5_thread) -> None:
    reviews = lore_manager.extract_reviews(v5_thread)
    assert reviews, "expected review comments in the v5 thread"
    assert all(isinstance(r, ReviewComment) for r in reviews)
    # Mark Brown and Krzysztof are maintainers/reviewers in MAINTAINERS
    maint = [r for r in reviews if r.is_maintainer]
    assert maint, "expected at least one maintainer review"
    # every review carries a resolvable provenance source_url + message-id
    for r in reviews:
        assert r.provenance.source_url and r.provenance.source_url.startswith("https://")
        assert r.provenance.version_or_commit  # message-id
        assert r.target_patch_id is not None
        # retrieval time must not leak into parsed output (determinism)
        assert r.provenance.retrieved_at is None


def test_extract_reviews_detects_reviewed_by_tag(lore_manager, v5_thread) -> None:
    reviews = lore_manager.extract_reviews(v5_thread)
    approvals = [r for r in reviews if r.category == "approval"]
    assert approvals, "expected a Reviewed-by/Acked-by approval in the v5 thread"
    assert all(a.severity == Severity.INFO for a in approvals)


def test_extract_reviews_deterministic(lore_manager, v5_thread) -> None:
    a = lore_manager.extract_reviews(v5_thread)
    b = lore_manager.extract_reviews(v5_thread)
    assert [x.model_dump() for x in a] == [y.model_dump() for y in b]


def test_real_maintainers_flagged_against_kernel_file(lore_manager, v5_thread,
                                                      maintainers_path) -> None:
    if maintainers_path is None:
        pytest.skip("kernel MAINTAINERS file not present")
    idx = lore_manager.maintainers
    # Mark Brown posts from his M: address; flagged by exact email match.
    assert idx.is_maintainer("broonie@kernel.org", "Mark Brown")
    # Krzysztof posts from krzk@kernel.org (NOT his M: address) -- flagged via his
    # kernel.org git-tree account, the data-driven robustness case.
    assert idx.is_maintainer("krzk@kernel.org", "Krzysztof Kozlowski")
    # Both actually appear as maintainer reviews in the parsed v5 thread.
    reviews = lore_manager.extract_reviews(v5_thread)
    authors = {r.author for r in reviews if r.is_maintainer}
    assert any(a and "brown" in a.lower() for a in authors)
    assert any(a and "krzysztof" in a.lower() for a in authors)


def test_fabricated_maintainer_name_not_flagged(lore_manager, maintainers_path) -> None:
    if maintainers_path is None:
        pytest.skip("kernel MAINTAINERS file not present")
    idx = lore_manager.maintainers
    # A spoofed identity: a real maintainer's display name from an unrelated address
    # whose local-part is not one of his kernel.org git-tree accounts.
    assert not idx.is_maintainer("mark@evil.example", "Mark Brown")


def _synthetic_series_thread():
    """Build a small thread: cover letter (0/1), one patch (1/1), a reply to the
    cover letter, and an unrelated off-thread reply."""
    from kri.lore_manager.mbox import Message, SubjectInfo, Thread

    cover = Message(
        message_id="cover@x", subject="[PATCH 0/1] a feature",
        in_reply_to=None,
        subject_info=SubjectInfo(clean="a feature", is_patch=True, sequence=0,
                                 series_total=1, is_cover_letter=True),
    )
    patch = Message(
        message_id="p1@x", subject="[PATCH 1/1] do it",
        in_reply_to="cover@x",
        body="commit msg\n---\ndiff --git a/f.c b/f.c\n",
        has_diff=True,
        subject_info=SubjectInfo(clean="do it", is_patch=True, sequence=1, series_total=1),
    )
    cover_reply = Message(
        message_id="cr@x", subject="Re: [PATCH 0/1] a feature",
        in_reply_to="cover@x", from_name="Rev Iewer", from_email="rev@example.com",
        body="Please add a test for the whole series.\n",
        subject_info=SubjectInfo(clean="a feature", is_reply=True),
    )
    off_thread = Message(
        message_id="off@x", subject="Re: unrelated",
        in_reply_to="somewhere-else@y", from_email="x@example.com",
        body="not part of this series\n",
        subject_info=SubjectInfo(clean="unrelated", is_reply=True),
    )
    return Thread(thread_id="cover@x", source_url="https://lore.kernel.org/all/cover@x/",
                  messages=[cover, patch, cover_reply, off_thread])


def test_cover_letter_reply_kept_as_series_level(lore_manager) -> None:
    thread = _synthetic_series_thread()
    reviews = lore_manager.extract_reviews(thread)
    ids = {r.comment_id for r in reviews}
    # cover-letter reply retained, anchored to the series (not a specific patch)
    assert "rc:cr@x" in ids
    cover_rc = next(r for r in reviews if r.comment_id == "rc:cr@x")
    assert cover_rc.target_patch_id is None
    assert cover_rc.target_series_id == "cover@x"
    # off-thread straggler that resolves to neither patch nor series is dropped
    assert "rc:off@x" not in ids


def test_search_parses_cached_atom(tmp_path: Path) -> None:
    atom = (
        '<?xml version="1.0"?><feed>'
        '<entry><link href="https://lore.kernel.org/all/msg-1@example.com/"/></entry>'
        '<entry><link href="https://lore.kernel.org/all/msg-2@example.com/"/></entry>'
        '<entry><link href="https://lore.kernel.org/all/msg-1@example.com/#related"/></entry>'
        "</feed>"
    )
    lm = LoreManagerImpl(LoreConfig(cache_dir=tmp_path, inbox="all", offline=True))
    key = "search_" + __import__("hashlib").sha1(b"all:my query").hexdigest()[:16] + ".atom"
    (tmp_path / key).write_bytes(atom.encode())
    ids = lm.search("my query")
    assert ids == ["msg-1@example.com", "msg-2@example.com"]
