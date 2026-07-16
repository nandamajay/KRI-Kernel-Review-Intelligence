"""Lore Manager (Blueprint Sec. 21.3): fetch, cache, and structure lore threads.

Responsibilities:
  * ``fetch(thread_id)``            -- retrieve a public-inbox thread mbox, cached.
  * ``parse_conversation(thread)``  -- ordered, threaded replies (References/In-Reply-To).
  * ``extract_reviews(thread)``     -- ReviewComments with is_maintainer + provenance.
  * ``search(query)``               -- lore search -> matching message-ids.

Network I/O is confined here and to the Repository Manager (SPEC.md Sec. 8). Every
byte fetched is written to a deterministic on-disk cache so unit tests replay
offline. Every extracted artifact carries resolvable :class:`Provenance` (no data
without provenance -- Constitution Sec. 37). Domain isolation: the mailing-list
name is configuration, never a hardcoded identifier; the default is the
subsystem-agnostic ``/all/`` public-inbox.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from kri.common.models import Provenance, ReviewComment, Severity

from .maintainers import MaintainerIndex, load_maintainers
from .mbox import (
    REVIEW_TAGS,
    Message,
    Thread,
    parse_mbox_bytes,
    parse_mbox_gz,
    strip_message_id,
)

_ATOM_HREF_RE = re.compile(r'href="(?P<url>https?://[^"]+)"')
_REVIEW_TAG_RE = re.compile(
    r"^\s*(?P<tag>{}):\s*(?P<who>.+)$".format("|".join(REVIEW_TAGS)),
    re.IGNORECASE | re.MULTILINE,
)


class LoreConfig:
    """Configuration for :class:`LoreManagerImpl` (all values overridable)."""

    def __init__(
        self,
        cache_dir: str | Path,
        base_url: str = "https://lore.kernel.org",
        inbox: str = "all",
        maintainers_path: str | Path | None = None,
        user_agent: str = "git/2.39.0 kri/0.1",
        rate_limit_seconds: float = 1.0,
        timeout: int = 60,
        offline: bool = False,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.base_url = base_url.rstrip("/")
        # inbox is the public-inbox list name; '/all/' is domain-agnostic.
        self.inbox = inbox.strip("/")
        self.maintainers_path = Path(maintainers_path) if maintainers_path else None
        self.user_agent = user_agent
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = timeout
        self.offline = offline


class LoreManagerImpl:
    """Concrete :class:`kri.common.interfaces.LoreManager`."""

    def __init__(self, config: LoreConfig) -> None:
        self._cfg = config
        self._cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0
        self._maintainers: MaintainerIndex | None = None

    # -- maintainers ---------------------------------------------------------
    @property
    def maintainers(self) -> MaintainerIndex:
        if self._maintainers is None:
            if self._cfg.maintainers_path is not None:
                self._maintainers = load_maintainers(self._cfg.maintainers_path)
            else:
                self._maintainers = MaintainerIndex()
        return self._maintainers

    # -- caching helpers -----------------------------------------------------
    def _cache_key(self, message_id: str) -> str:
        """Deterministic filesystem-safe cache key for a message-id."""
        mid = strip_message_id(message_id)
        digest = hashlib.sha1(mid.encode("utf-8")).hexdigest()[:12]
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", mid)[:80]
        return f"{safe}.{digest}"

    def _mbox_path(self, message_id: str) -> Path:
        return self._cfg.cache_dir / f"{self._cache_key(message_id)}.mbox.gz"

    def _thread_url(self, message_id: str) -> str:
        mid = strip_message_id(message_id)
        return f"{self._cfg.base_url}/{self._cfg.inbox}/{mid}/t.mbox.gz"

    def message_url(self, message_id: str) -> str:
        """Public, resolvable lore URL for a message-id (used in provenance)."""
        mid = strip_message_id(message_id)
        return f"{self._cfg.base_url}/{self._cfg.inbox}/{mid}/"

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._cfg.rate_limit_seconds:
            time.sleep(self._cfg.rate_limit_seconds - elapsed)
        self._last_request = time.monotonic()

    def _http_get(self, url: str) -> bytes:
        if self._cfg.offline:
            raise LoreOfflineError(f"offline mode: refusing network fetch of {url}")
        self._rate_limit()
        resp = requests.get(
            url,
            headers={"User-Agent": self._cfg.user_agent},
            timeout=self._cfg.timeout,
            verify=False,
        )
        resp.raise_for_status()
        return resp.content

    # -- interface: fetch ----------------------------------------------------
    def fetch(self, thread_id: str) -> Thread:
        """Fetch (and cache) a lore thread by message-id or lore URL.

        Returns a :class:`Thread`. Cached fetches are offline-replayable: if the
        gzipped mbox already exists on disk it is parsed without any network call.
        """
        mid = strip_message_id(thread_id)
        cache_path = self._mbox_path(mid)
        retrieved_at: str | None = None
        if cache_path.exists():
            data = cache_path.read_bytes()
            retrieved_at = _isoformat(datetime.fromtimestamp(
                cache_path.stat().st_mtime, tz=timezone.utc))
        else:
            data = self._http_get(self._thread_url(mid))
            cache_path.write_bytes(data)
            retrieved_at = _isoformat(datetime.now(tz=timezone.utc))

        thread = parse_mbox_gz(data, thread_id=mid, source_url=self.message_url(mid))
        thread.retrieved_at = retrieved_at
        return thread

    def load_cached(self, path: str | Path) -> Thread:
        """Parse a cached ``.mbox.gz`` (or plain ``.mbox``) fixture from disk.

        Convenience for tests/benchmark fixtures; performs no network I/O."""
        p = Path(path)
        raw = p.read_bytes()
        if p.suffix == ".gz" or p.name.endswith(".mbox.gz"):
            thread = parse_mbox_gz(raw)
        else:
            thread = parse_mbox_bytes(raw)
        thread.source_url = self.message_url(thread.thread_id) if thread.thread_id else None
        thread.retrieved_at = _isoformat(
            datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc))
        return thread

    # -- interface: parse_conversation --------------------------------------
    def parse_conversation(self, thread: Thread) -> list[dict[str, object]]:
        """Return ordered, structured replies with threading depth.

        Ordering is a stable topological walk: each message appears after its
        parent (In-Reply-To). Roots keep their original mbox order. ``depth`` is
        the reply nesting level (0 == root)."""
        by_id = thread.by_id()
        children: dict[str | None, list[Message]] = {}
        roots: list[Message] = []
        for msg in thread.messages:
            parent = msg.in_reply_to if (msg.in_reply_to in by_id) else None
            if parent is None:
                roots.append(msg)
            else:
                children.setdefault(parent, []).append(msg)

        ordered: list[dict[str, object]] = []

        def walk(msg: Message, depth: int) -> None:
            ordered.append({
                "message_id": msg.message_id,
                "depth": depth,
                "subject": msg.subject,
                "clean_subject": msg.subject_info.clean,
                "from_name": msg.from_name,
                "from_email": msg.from_email,
                "in_reply_to": msg.in_reply_to,
                "is_patch": msg.is_patch,
                "is_reply": msg.subject_info.is_reply,
                "is_maintainer": self.maintainers.is_maintainer(msg.from_email, msg.from_name),
                "body": msg.body,
            })
            for child in children.get(msg.message_id, []):
                walk(child, depth + 1)

        for root in roots:
            walk(root, 0)
        # Safety net: include any messages missed by the walk (cycles), preserving order.
        seen = {row["message_id"] for row in ordered}
        for msg in thread.messages:
            if msg.message_id not in seen:
                ordered.append({
                    "message_id": msg.message_id, "depth": 0, "subject": msg.subject,
                    "clean_subject": msg.subject_info.clean, "from_name": msg.from_name,
                    "from_email": msg.from_email, "in_reply_to": msg.in_reply_to,
                    "is_patch": msg.is_patch, "is_reply": msg.subject_info.is_reply,
                    "is_maintainer": self.maintainers.is_maintainer(
                        msg.from_email, msg.from_name),
                    "body": msg.body,
                })
        return ordered

    # -- interface: extract_reviews -----------------------------------------
    def extract_reviews(self, thread: Thread) -> list[ReviewComment]:
        """Extract review comments (replies to patches) as :class:`ReviewComment`.

        A review is any non-patch reply whose ancestry leads to a patch message.
        ``is_maintainer`` is set from the MAINTAINERS index. Review trailer tags
        (Reviewed-by/Acked-by/Nacked-by) raise the severity signal. Every comment
        carries resolvable :class:`Provenance` (the reply's lore URL).

        Cover-letter correlation policy: a reply that resolves to the cover letter
        (``0/N``) or the series root rather than a specific patch is *series-level*
        feedback -- it is retained with ``target_patch_id=None`` and
        ``target_series_id`` set, not dropped. Only replies that anchor to neither a
        patch nor the series (e.g. off-thread stragglers) are excluded."""
        by_id = thread.by_id()
        patch_ids = {m.message_id for m in thread.messages if m.is_patch}
        # Cover-letter / series-root messages: series-level review anchors.
        cover_ids = {m.message_id for m in thread.messages if m.subject_info.is_cover_letter}
        if thread.thread_id:
            cover_ids.add(thread.thread_id)
        series_id = thread.thread_id
        comments: list[ReviewComment] = []

        for msg in thread.messages:
            if msg.is_patch:
                continue
            if not msg.subject_info.is_reply and msg.in_reply_to is None:
                continue
            target_patch = self._resolve_target_patch(msg, by_id, patch_ids)
            if target_patch is None:
                # Not anchored to a specific patch. If it belongs to the series
                # (resolves to the cover letter / series root) keep it as a
                # series-level review; otherwise it is off-thread -> drop.
                if not self._resolves_to_series(msg, by_id, cover_ids):
                    continue

            body = _strip_quotes(msg.body)
            severity, category = self._classify(msg.body)
            provenance = Provenance(
                source_url=self.message_url(msg.message_id),
                version_or_commit=msg.message_id,
                retrieved_at=None,  # kept null: retrieval time must not affect output
                transformation_history=["lore.fetch", "mbox.parse", "extract_reviews"],
                source_confidence=1.0,
            )
            comments.append(ReviewComment(
                comment_id=f"rc:{msg.message_id}",
                target_series_id=series_id,
                target_patch_id=target_patch,
                location=None,
                category=category,
                severity=severity,
                message=body,
                author=msg.from_name or msg.from_email,
                is_maintainer=self.maintainers.is_maintainer(msg.from_email, msg.from_name),
                provenance=provenance,
            ))
        return comments

    def _resolve_target_patch(
        self, msg: Message, by_id: dict[str, Message], patch_ids: set[str]
    ) -> str | None:
        """Walk up In-Reply-To/References to the nearest ancestor patch message."""
        # Direct parent is a patch.
        if msg.in_reply_to in patch_ids:
            return msg.in_reply_to
        # Walk the In-Reply-To chain.
        cur = msg.in_reply_to
        guard = 0
        while cur is not None and cur in by_id and guard < 100:
            if cur in patch_ids:
                return cur
            cur = by_id[cur].in_reply_to
            guard += 1
        # Fall back to References, nearest (last) first.
        for ref in reversed(msg.references):
            if ref in patch_ids:
                return ref
        return None

    def _resolves_to_series(
        self, msg: Message, by_id: dict[str, Message], series_ids: set[str]
    ) -> bool:
        """True if the reply's ancestry (In-Reply-To chain, then References) reaches
        the cover letter or series root -- i.e. it is series-level, not off-thread."""
        if msg.in_reply_to in series_ids:
            return True
        cur = msg.in_reply_to
        guard = 0
        while cur is not None and cur in by_id and guard < 100:
            if cur in series_ids:
                return True
            cur = by_id[cur].in_reply_to
            guard += 1
        return any(ref in series_ids for ref in msg.references)

    @staticmethod
    def _classify(body: str) -> tuple[Severity, str]:
        """Deterministically derive (severity, category) from review trailer tags."""
        tags = {m.group("tag").lower() for m in _REVIEW_TAG_RE.finditer(body)}
        if "nacked-by" in tags:
            return Severity.BLOCKER, "nack"
        if "reviewed-by" in tags or "acked-by" in tags:
            return Severity.INFO, "approval"
        return Severity.INFO, "review_discussion"

    # -- interface: search ---------------------------------------------------
    def search(self, query: str) -> list[str]:
        """Search lore; return matching thread/message-ids (deterministic order).

        Uses the public-inbox Atom search feed (``?q=...&x=A``). Results are cached
        by query hash so tests replay offline. Order follows lore's relevance
        ranking as returned by the feed (stable for a fixed query + snapshot)."""
        key = hashlib.sha1(f"{self._cfg.inbox}:{query}".encode()).hexdigest()[:16]
        cache_path = self._cfg.cache_dir / f"search_{key}.atom"
        if cache_path.exists():
            data = cache_path.read_bytes()
        else:
            url = f"{self._cfg.base_url}/{self._cfg.inbox}/?q={quote_plus(query)}&x=A"
            data = self._http_get(url)
            cache_path.write_bytes(data)
        return self._parse_search_atom(data.decode("utf-8", errors="replace"))

    def _parse_search_atom(self, atom: str) -> list[str]:
        """Extract distinct message-ids from a public-inbox Atom search feed."""
        ids: list[str] = []
        seen: set[str] = set()
        for m in _ATOM_HREF_RE.finditer(atom):
            url = m.group("url")
            path = urlparse(url).path.strip("/")
            segments = [s for s in path.split("/") if s]
            if not segments:
                continue
            last = segments[-1]
            # message permalinks end in the bare message-id (has '@' or a digit id).
            if "#" in last or last in ("t.mbox.gz", "raw", "T", "t"):
                continue
            if "@" in last and last not in seen:
                seen.add(last)
                ids.append(last)
        return ids


class LoreOfflineError(RuntimeError):
    """Raised when a network fetch is attempted in offline mode with no cache."""


def _isoformat(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _strip_quotes(body: str) -> str:
    """Remove quoted reply lines (``> ...``) and the ``On ... wrote:`` attribution,
    leaving the reviewer's own prose. Falls back to the full body if nothing is
    left (e.g. inline-only review)."""
    lines = body.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(">"):
            continue
        if re.match(r"^On .+wrote:\s*$", stripped):
            continue
        kept.append(line)
    result = "\n".join(kept).strip()
    return result if result else body.strip()
