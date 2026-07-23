"""Unit tests for the mbox / subject / maintainers parsing primitives."""

from __future__ import annotations

from kri.lore_manager import (
    files_from_diff,
    parse_mbox_bytes,
    parse_subject,
    split_commit_message_and_diff,
    strip_message_id,
)
from kri.lore_manager.maintainers import parse_maintainers

SYNTHETIC_MBOX = b"""From mboxrd@z Thu Jan  1 00:00:00 1970
From: Alice Dev <alice@example.com>
Subject: [PATCH v3 1/2] core: add a helper
Date: Mon, 1 Jan 2024 00:00:00 +0000
Message-ID: <root-1@example.com>

A commit body explaining the change.

Signed-off-by: Alice Dev <alice@example.com>
---
 drivers/foo/bar.c | 2 ++
 1 file changed, 2 insertions(+)

diff --git a/drivers/foo/bar.c b/drivers/foo/bar.c
index 1111111..2222222 100644
--- a/drivers/foo/bar.c
+++ b/drivers/foo/bar.c
@@ -1,2 +1,4 @@
 int x;
+int y;
+int z;

From mboxrd@z Thu Jan  1 00:00:00 1970
From: Bob Maintainer <bob@kernel.org>
Subject: Re: [PATCH v3 1/2] core: add a helper
Date: Mon, 1 Jan 2024 01:00:00 +0000
Message-ID: <reply-1@kernel.org>
In-Reply-To: <root-1@example.com>
References: <root-1@example.com>

On Mon, 1 Jan 2024, Alice Dev wrote:
> +int y;

Please use a blank line here.

Reviewed-by: Bob Maintainer <bob@kernel.org>
"""


def test_parse_subject_versions_and_sequence() -> None:
    info = parse_subject("[PATCH v3 2/5] subsystem: do a thing")
    assert info.is_patch is True
    assert info.version == 3
    assert info.sequence == 2
    assert info.series_total == 5
    assert info.clean == "subsystem: do a thing"
    assert info.is_reply is False


def test_parse_subject_cover_letter_and_reply() -> None:
    cover = parse_subject("[PATCH 0/4] a series")
    assert cover.is_cover_letter is True
    assert cover.sequence == 0
    reply = parse_subject("Re: [PATCH v2] fix it")
    assert reply.is_reply is True
    assert reply.version == 2
    assert reply.clean == "fix it"


def test_parse_subject_non_patch() -> None:
    info = parse_subject("A general discussion topic")
    assert info.is_patch is False
    assert info.version == 1
    assert info.clean == "A general discussion topic"


def test_strip_message_id_variants() -> None:
    assert strip_message_id("<abc@x.com>") == "abc@x.com"
    assert strip_message_id("  abc@x.com ") == "abc@x.com"
    assert strip_message_id("https://lore.kernel.org/all/abc@x.com/") == "abc@x.com"
    assert strip_message_id("https://lore.kernel.org/all/abc@x.com/t.mbox.gz") == "abc@x.com"


def test_split_commit_message_and_diff() -> None:
    body = (
        "Title line\n\nBody paragraph.\n\nSigned-off-by: X <x@y>\n"
        "---\n stat line\n\ndiff --git a/f.c b/f.c\n@@ -1 +1 @@\n-a\n+b\n"
    )
    msg, diff = split_commit_message_and_diff(body)
    assert "Signed-off-by" in msg
    assert "stat line" not in msg           # cut at the --- scissors
    assert diff.startswith("diff --git a/f.c b/f.c")


def test_files_from_diff_order_and_dedup() -> None:
    diff = (
        "diff --git a/z.c b/z.c\n@@\n"
        "diff --git a/a.c b/a.c\n@@\n"
        "diff --git a/z.c b/z.c\n@@\n"
    )
    assert files_from_diff(diff) == ["z.c", "a.c"]


def test_files_from_diff_new_file_uses_a_side() -> None:
    diff = "diff --git a/new.c b/new.c\nnew file mode 100644\n--- /dev/null\n+++ b/new.c\n"
    assert files_from_diff(diff) == ["new.c"]


def test_parse_synthetic_mbox_threading_and_diff() -> None:
    thread = parse_mbox_bytes(SYNTHETIC_MBOX)
    assert len(thread.messages) == 2
    root, reply = thread.messages
    assert root.is_patch is True
    assert root.subject_info.sequence == 1
    assert files_from_diff(root.body) == ["drivers/foo/bar.c"]
    assert reply.is_patch is False
    assert reply.in_reply_to == "root-1@example.com"
    assert reply.references == ["root-1@example.com"]
    # mboxrd body of the patch carries the diff
    assert "diff --git" in root.body


def test_root_inference_prefers_no_parent() -> None:
    thread = parse_mbox_bytes(SYNTHETIC_MBOX)
    assert thread.thread_id == "root-1@example.com"


def test_parse_maintainers() -> None:
    text = (
        "SOME SUBSYSTEM\n"
        "M:\tJane Doe <jane@kernel.org>\n"
        "R:\tJohn Roe <john@example.com>\n"
        "L:\tlist@example.com\n"
        "S:\tMaintained\n"
    )
    idx = parse_maintainers(text)
    assert idx.is_maintainer("jane@kernel.org") is True
    assert idx.is_maintainer("john@example.com") is True
    assert idx.is_maintainer("nobody@nowhere.com") is False
    # Name alone is never sufficient (collision-robust): a display-name match must
    # be corroborated by a posting domain the name actually uses in MAINTAINERS.
    assert idx.is_maintainer(None, "Jane Doe") is False
    assert idx.is_maintainer("someone@kernel.org", "Jane Doe") is True
    assert idx.is_maintainer("jane@evil.example", "Jane Doe") is False


def test_split_commit_message_no_scissors_separator() -> None:
    """When no '---' line is present the commit message ends at the diff header."""
    body = "Fix the bug\n\nSigned-off-by: X <x@y>\ndiff --git a/f.c b/f.c\n@@ -1 +1 @@\n"
    msg, diff = split_commit_message_and_diff(body)
    assert "Signed-off-by" in msg
    assert diff.startswith("diff --git a/f.c b/f.c")


def test_split_commit_message_no_diff_at_all() -> None:
    """When body has no diff at all, commit_message is the full body and diff is empty."""
    body = "Just a message\n\nNo diff here."
    msg, diff = split_commit_message_and_diff(body)
    assert msg == "Just a message\n\nNo diff here."
    assert diff == ""


def test_files_from_diff_deleted_file_uses_a_side() -> None:
    """For a deleted file the b/ side is /dev/null; a/ side must be returned."""
    diff = "diff --git a/old.c b/old.c\ndeleted file mode 100644\n--- a/old.c\n+++ /dev/null\n"
    assert files_from_diff(diff) == ["old.c"]


def test_parse_subject_rfc_patch_variant() -> None:
    """[RFC PATCH ...] prefix should set is_patch=True."""
    info = parse_subject("[RFC PATCH v2 1/3] net: add helper")
    assert info.is_patch is True
    assert info.is_reply is False
    assert info.version == 2
    assert info.sequence == 1
    assert info.series_total == 3
    assert info.clean == "net: add helper"
