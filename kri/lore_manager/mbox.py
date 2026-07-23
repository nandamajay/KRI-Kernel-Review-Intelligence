"""Email / mbox parsing primitives for the Lore Manager (Blueprint Sec. 21.3).

This module turns a public-inbox ``t.mbox.gz`` byte stream into structured,
domain-agnostic :class:`Message` / :class:`Thread` objects. It performs no network
I/O and no domain-specific interpretation: it only understands RFC-822 email and
the ``git format-patch`` layout (subject prefixes, unified diffs, trailers).

Determinism (Constitution Sec. 31): parsing is a pure function of the input bytes.
Message order is preserved exactly as it appears in the mbox; nothing depends on
dict/set iteration order or wall-clock.
"""

from __future__ import annotations

import gzip
import re
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
from email.utils import parseaddr

from pydantic import BaseModel, Field

# A git format-patch subject prefix, e.g. "[PATCH v3 2/5]" or "[RFC PATCH]".
_PREFIX_RE = re.compile(r"\[(?P<body>[^\]]*\bPATCH\b[^\]]*)\]", re.IGNORECASE)
_VERSION_RE = re.compile(r"\bv(?P<n>\d+)\b", re.IGNORECASE)
_SEQ_RE = re.compile(r"\b(?P<seq>\d+)\s*/\s*(?P<total>\d+)\b")
_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)", re.MULTILINE)
_REPLY_RE = re.compile(r"^\s*re:\s*", re.IGNORECASE)
_MBOX_SEP_RE = re.compile(rb"^From \S+.*$", re.MULTILINE)

# Review trailer tags (Blueprint Sec. 15.3 review-discussion evidence).
REVIEW_TAGS = ("reviewed-by", "acked-by", "nacked-by", "tested-by", "reported-by")


class SubjectInfo(BaseModel):
    """Structured decomposition of a patch subject line."""

    clean: str = ""            # subject with all [PATCH ...] prefixes stripped
    is_patch: bool = False     # subject carried a [PATCH...] prefix
    is_reply: bool = False     # subject started with "Re:"
    version: int = 1           # v1/v2/v3 (1 if unversioned)
    sequence: int = 0          # x in "x/N"
    series_total: int = 0      # N in "x/N"
    is_cover_letter: bool = False  # "0/N"


class Message(BaseModel):
    """A single parsed email message from a lore thread."""

    message_id: str
    subject: str = ""
    from_name: str = ""
    from_email: str = ""
    date: str = ""                     # raw Date header (metadata, not used in logic)
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)
    body: str = ""                     # decoded text/plain body
    subject_info: SubjectInfo = Field(default_factory=SubjectInfo)
    has_diff: bool = False

    @property
    def is_patch(self) -> bool:
        """A message is a patch iff it carries a PATCH subject prefix, has a diff,
        and is not a reply (reviewer replies quoting a diff are excluded)."""
        return self.subject_info.is_patch and self.has_diff and not self.subject_info.is_reply


class Thread(BaseModel):
    """An ordered collection of messages forming one lore discussion thread."""

    thread_id: str                      # canonical message-id of the root
    source_url: str | None = None
    messages: list[Message] = Field(default_factory=list)
    retrieved_at: str | None = None     # ISO metadata; never affects parsed output

    def by_id(self) -> dict[str, Message]:
        """Message-id -> Message lookup (first occurrence wins, deterministic)."""
        out: dict[str, Message] = {}
        for m in self.messages:
            out.setdefault(m.message_id, m)
        return out


# ---------------------------------------------------------------------------
# Header / subject helpers
# ---------------------------------------------------------------------------


def _decode(value: str | None) -> str:
    """Decode RFC-2047 encoded-word headers to a plain unicode string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (ValueError, LookupError):
        return value.strip()


def strip_message_id(raw: str) -> str:
    """Normalize a Message-ID or lore URL to the bare id (no angle brackets)."""
    raw = raw.strip()
    # If given a lore URL, pull the id out of the path.
    if raw.startswith("http://") or raw.startswith("https://"):
        path = raw.split("://", 1)[1]
        parts = [p for p in path.split("/")[1:] if p]  # drop host
        # message-id is the last path segment that contains an '@' or looks like an id
        for seg in reversed(parts):
            seg = seg.split("#", 1)[0]
            if seg and seg not in ("t.mbox.gz", "raw", "t", "T"):
                raw = seg
                break
    return raw.strip().lstrip("<").rstrip(">").strip()


def parse_subject(subject: str) -> SubjectInfo:
    """Decompose a subject line into version / sequence / clean text.

    Handles ``Re:`` reply prefixes and one-or-more ``[... PATCH ...]`` tag blocks.
    """
    info = SubjectInfo()
    text = subject.strip()
    if _REPLY_RE.match(text):
        info.is_reply = True
        text = _REPLY_RE.sub("", text, count=1).strip()

    # Consume leading bracketed prefixes; the last PATCH prefix carries version/seq.
    while text.startswith("["):
        end = text.find("]")
        if end == -1:
            break
        block = text[1:end]
        rest = text[end + 1 :].lstrip()
        m = _PREFIX_RE.match(f"[{block}]")
        if m:
            info.is_patch = True
            vm = _VERSION_RE.search(block)
            if vm:
                info.version = int(vm.group("n"))
            sm = _SEQ_RE.search(block)
            if sm:
                info.sequence = int(sm.group("seq"))
                info.series_total = int(sm.group("total"))
                if info.sequence == 0:
                    info.is_cover_letter = True
        text = rest
    info.clean = text.strip()
    return info


# ---------------------------------------------------------------------------
# Body / diff helpers
# ---------------------------------------------------------------------------


def _extract_text_plain(msg: EmailMessage) -> str:
    """Return the concatenated text/plain payload of a (possibly multipart) email."""
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.is_multipart():
                parts.append(_decode_payload(part))
    else:
        parts.append(_decode_payload(msg))
    return "".join(parts)


def _decode_payload(part: EmailMessage) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def split_commit_message_and_diff(body: str) -> tuple[str, str]:
    """Split a patch body into (commit_message, unified_diff).

    The commit message includes trailers (Signed-off-by, etc.) up to the first
    ``---`` scissors line or the first ``diff --git`` header, whichever is first.
    The diff is everything from the first ``diff --git`` onward.
    """
    diff_start = body.find("\ndiff --git ")
    if body.startswith("diff --git "):
        diff_start = 0
    diff = ""
    if diff_start != -1:
        diff = body[diff_start:].lstrip("\n")

    # Commit message ends at the first standalone '---' separator line, else at diff.
    msg_region = body if diff_start == -1 else body[:diff_start]
    lines = msg_region.splitlines()
    cut = len(lines)
    for i, line in enumerate(lines):
        if line.rstrip() == "---":
            cut = i
            break
    commit_message = "\n".join(lines[:cut]).strip()
    return commit_message, diff


def files_from_diff(diff: str) -> list[str]:
    """Return the files touched by a unified diff, in first-appearance order."""
    seen: list[str] = []
    for m in _DIFF_GIT_RE.finditer(diff):
        path = m.group("b")
        if path == "/dev/null":
            path = m.group("a")
        if path not in seen:
            seen.append(path)
    return seen


# ---------------------------------------------------------------------------
# mbox parsing
# ---------------------------------------------------------------------------


def _split_mbox(data: bytes) -> list[bytes]:
    """Split raw mbox bytes into per-message byte blocks on ``From `` separators."""
    matches = list(_MBOX_SEP_RE.finditer(data))
    if not matches:
        return [data] if data.strip() else []
    blocks: list[bytes] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(data)
        block = data[start:end]
        # Drop the leading "From ..." envelope line before RFC-822 parsing.
        nl = block.find(b"\n")
        if nl != -1:
            block = block[nl + 1 :]
        blocks.append(block)
    return blocks


def _unescape_mboxrd(body: str) -> str:
    """Undo mboxrd ``>From`` quoting in message bodies."""
    return re.sub(r"^(>+)From ", lambda m: m.group(1)[1:] + "From ", body, flags=re.MULTILINE)


def parse_message(block: bytes) -> Message | None:
    """Parse one RFC-822 message block into a :class:`Message`."""
    email_msg = message_from_bytes(block)
    mid_raw = email_msg.get("Message-ID") or email_msg.get("Message-Id")
    if not mid_raw:
        return None
    message_id = strip_message_id(mid_raw)
    subject = _decode(email_msg.get("Subject"))
    from_name, from_email = parseaddr(_decode(email_msg.get("From")))

    in_reply_to = email_msg.get("In-Reply-To")
    in_reply_to = strip_message_id(in_reply_to) if in_reply_to else None

    refs_raw = email_msg.get("References", "") or ""
    references = [strip_message_id(r) for r in refs_raw.split() if r.strip()]

    body = _unescape_mboxrd(_extract_text_plain(email_msg))
    subject_info = parse_subject(subject)
    has_diff = "\ndiff --git " in body or body.startswith("diff --git ")

    return Message(
        message_id=message_id,
        subject=subject,
        from_name=from_name,
        from_email=from_email.lower(),
        date=email_msg.get("Date", "") or "",
        in_reply_to=in_reply_to,
        references=references,
        body=body,
        subject_info=subject_info,
        has_diff=has_diff,
    )


def parse_mbox_bytes(data: bytes, thread_id: str | None = None,
                     source_url: str | None = None) -> Thread:
    """Parse raw mbox bytes (already decompressed) into a :class:`Thread`."""
    messages: list[Message] = []
    for block in _split_mbox(data):
        try:
            msg = parse_message(block)
        except (ValueError, KeyError):
            msg = None
        if msg is not None:
            messages.append(msg)

    root_id = thread_id or _infer_root_id(messages)
    return Thread(
        thread_id=root_id,
        source_url=source_url,
        messages=messages,
    )


def parse_mbox_gz(data: bytes, thread_id: str | None = None,
                  source_url: str | None = None) -> Thread:
    """Parse gzipped mbox bytes into a :class:`Thread`."""
    return parse_mbox_bytes(gzip.decompress(data), thread_id=thread_id, source_url=source_url)


def _infer_root_id(messages: list[Message]) -> str:
    """Pick the thread root: the message no other message replies before, i.e. the
    one with no In-Reply-To, preferring a cover letter. Deterministic."""
    if not messages:
        return ""
    ids = {m.message_id for m in messages}
    # Prefer a cover letter (0/N) with no external parent.
    for m in messages:
        if m.subject_info.is_cover_letter and (m.in_reply_to is None or m.in_reply_to not in ids):
            return m.message_id
    for m in messages:
        if m.in_reply_to is None or m.in_reply_to not in ids:
            return m.message_id
    return messages[0].message_id
