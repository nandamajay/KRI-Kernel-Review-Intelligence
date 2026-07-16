"""Patch Manager (Blueprint Sec. 21.2): thread -> PatchSeries and review correlation.

Turns a parsed lore :class:`~kri.lore_manager.mbox.Thread` (or raw mbox bytes) into
the frozen :class:`~kri.common.models.PatchSeries`/:class:`~kri.common.models.Patch`
artifacts, detects version history from subject prefixes, correlates review
comments to individual patches, and normalizes patches to a standard form.

Domain-agnostic: operates purely on abstract subjects/diffs/message-ids. Determinism
(Constitution Sec. 31): output depends only on the input thread bytes; message and
patch ordering is stable (by sequence, then mbox order).
"""

from __future__ import annotations

from kri.common.models import (
    Patch,
    PatchSeries,
    Provenance,
    ReviewComment,
)
from kri.lore_manager.manager import LoreOfflineError
from kri.lore_manager.mbox import (
    Message,
    Thread,
    files_from_diff,
    parse_mbox_bytes,
    parse_mbox_gz,
    split_commit_message_and_diff,
)


class PatchManagerImpl:
    """Concrete :class:`kri.common.interfaces.PatchManager`.

    A :class:`~kri.lore_manager.LoreManagerImpl` may be injected so
    ``correlate_reviews`` can reuse its maintainer-aware review extraction; if
    omitted, correlation falls back to structural threading only.
    """

    def __init__(self, lore_manager: object | None = None) -> None:
        self._lore = lore_manager

    # -- interface: parse ----------------------------------------------------
    def parse(self, thread: object) -> PatchSeries:
        """Parse a thread/mbox into a :class:`PatchSeries`.

        Accepts a :class:`Thread`, raw mbox ``bytes`` (optionally gzipped), or an
        mbox ``str``. Patches are the messages carrying a ``[PATCH]`` subject and a
        unified diff, ordered by their ``x/N`` sequence then mbox order. The cover
        letter (``0/N``) populates ``cover_letter``; its subject becomes the title."""
        t = self._coerce_thread(thread)

        patch_msgs = [m for m in t.messages if m.is_patch]
        cover = self._find_cover_letter(t)

        version = self._series_version(patch_msgs, cover)
        patches: list[Patch] = []
        # Stable order: by sequence when present, else original mbox order.
        ordered = sorted(
            enumerate(patch_msgs),
            key=lambda it: (it[1].subject_info.sequence or (it[0] + 1), it[0]),
        )
        series_total = max(
            (m.subject_info.series_total for m in patch_msgs), default=0)
        for _, msg in ordered:
            patches.append(self._message_to_patch(msg, t.thread_id, series_total))

        title = ""
        cover_letter = ""
        if cover is not None:
            title = cover.subject_info.clean
            cover_letter, _ = split_commit_message_and_diff(cover.body)
        elif patches:
            title = patches[0].subject

        provenance = Provenance(
            source_url=t.source_url,
            version_or_commit=t.thread_id,
            transformation_history=["lore.fetch", "mbox.parse", "patch.parse"],
            source_confidence=1.0,
        )
        return PatchSeries(
            series_id=t.thread_id or (patches[0].patch_id if patches else "unknown"),
            title=title,
            cover_letter=cover_letter,
            version=version,
            patches=patches,
            target_kernel_version=None,  # resolved downstream from the target tree
            lore_thread_url=t.source_url,
            provenance=provenance,
        )

    # -- interface: extract_versions ----------------------------------------
    def extract_versions(self, series: PatchSeries) -> list[int]:
        """Return the sorted, distinct version numbers present in the series.

        The series' own parsed ``version`` (from the cover letter or first patch's
        ``vN`` prefix) is always included. Individual patch subjects are also
        scanned, so if a series is assembled from patches carrying differing ``vN``
        prefixes every distinct version is surfaced. Deterministic: ascending order."""
        versions: set[int] = {series.version}
        for p in series.patches:
            v = _version_from_subject(p.subject)
            if v is not None:
                versions.add(v)
        return sorted(versions)

    # -- interface: correlate_reviews ---------------------------------------
    def correlate_reviews(self, series: PatchSeries) -> dict[str, list[ReviewComment]]:
        """Map each patch_id to the review comments targeting it.

        Every patch_id in the series is present as a key (empty list if no
        reviews). Requires the originating thread; the injected LoreManager is used
        to extract reviews with maintainer identification. If reviews cannot be
        recovered (no lore manager / no cached thread), returns empty lists."""
        result: dict[str, list[ReviewComment]] = {p.patch_id: [] for p in series.patches}
        comments = self._reviews_for_series(series)
        for c in comments:
            pid = c.target_patch_id
            if pid in result:
                result[pid].append(c)
            elif pid is not None:
                result.setdefault(pid, []).append(c)
        # Deterministic ordering within each patch: by comment_id.
        for pid in result:
            result[pid].sort(key=lambda c: c.comment_id)
        return result

    # -- interface: normalize -----------------------------------------------
    def normalize(self, patch: Patch) -> Patch:
        """Return a standardized copy of a patch.

        Normalization (idempotent, deterministic):
          * subject stripped of surrounding whitespace;
          * ``files_changed`` recomputed from the diff (authoritative) and sorted;
          * commit_message trailing whitespace trimmed;
          * diff line endings normalized to ``\\n``.
        """
        diff = patch.diff.replace("\r\n", "\n").replace("\r", "\n")
        files = files_from_diff(diff)
        if not files:
            files = sorted(set(patch.files_changed))
        else:
            files = sorted(set(files))
        return Patch(
            patch_id=patch.patch_id,
            subject=patch.subject.strip(),
            author=patch.author,
            commit_message=patch.commit_message.strip(),
            files_changed=files,
            diff=diff,
            sequence=patch.sequence,
            series_total=patch.series_total,
        )

    # -- helpers -------------------------------------------------------------
    def _coerce_thread(self, thread: object) -> Thread:
        if isinstance(thread, Thread):
            return thread
        if isinstance(thread, bytes):
            if thread[:2] == b"\x1f\x8b":  # gzip magic
                return parse_mbox_gz(thread)
            return parse_mbox_bytes(thread)
        if isinstance(thread, str):
            return parse_mbox_bytes(thread.encode("utf-8"))
        raise TypeError(f"cannot parse thread of type {type(thread)!r}")

    def _message_to_patch(self, msg: Message, series_id: str, series_total: int) -> Patch:
        commit_message, diff = split_commit_message_and_diff(msg.body)
        files = files_from_diff(diff)
        author = msg.from_name or msg.from_email or None
        return Patch(
            patch_id=msg.message_id,
            subject=msg.subject_info.clean or msg.subject,
            author=author,
            commit_message=commit_message,
            files_changed=files,
            diff=diff,
            sequence=msg.subject_info.sequence,
            series_total=msg.subject_info.series_total or series_total,
        )

    @staticmethod
    def _find_cover_letter(thread: Thread) -> Message | None:
        for m in thread.messages:
            if m.subject_info.is_cover_letter and not m.subject_info.is_reply:
                return m
        return None

    @staticmethod
    def _series_version(patch_msgs: list[Message], cover: Message | None) -> int:
        if cover is not None:
            return cover.subject_info.version
        if patch_msgs:
            return patch_msgs[0].subject_info.version
        return 1

    def _reviews_for_series(self, series: PatchSeries) -> list[ReviewComment]:
        if self._lore is None:
            return []
        thread = self._series_thread(series)
        if thread is None:
            return []
        extract = getattr(self._lore, "extract_reviews", None)
        if extract is None:
            return []
        return list(extract(thread))

    def _series_thread(self, series: PatchSeries) -> Thread | None:
        """Recover the originating Thread for a series via the injected LoreManager
        cache (offline-friendly). A cache miss in offline mode surfaces as
        :class:`LoreOfflineError`; any I/O or parse failure degrades to ``None``
        rather than propagating."""
        fetch = getattr(self._lore, "fetch", None)
        if fetch is None:
            return None
        try:
            return fetch(series.series_id)
        except (LoreOfflineError, OSError, ValueError):
            return None


def _version_from_subject(subject: str) -> int | None:
    import re
    m = re.search(r"\bv(\d+)\b", subject, re.IGNORECASE)
    return int(m.group(1)) if m else None
