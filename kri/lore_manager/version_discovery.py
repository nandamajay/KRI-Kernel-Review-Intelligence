"""WP-S2A — Cross-version review history (transient per-review layer).

Discovers prior-version threads for a versioned patch series, harvests
maintainer critique + author reply pairs, reconciles already-addressed
concerns, and formats a prompt-injection block.

Design constraints (Sec. 40 / Constitution §28):
- No random, time.time, datetime.now, uuid.uuid1/4 calls in this module.
- All network I/O is delegated to LoreManagerImpl (already cached/offline-safe).
- ``fetch()`` on the returned ``PriorVersionFetcher`` NEVER raises — all
  exceptions are caught; worst case is empty list[CritiqueReplyPair].
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of prior-version threads to walk back.
_MAX_PRIOR_VERSIONS = 3
# Guard against malformed In-Reply-To chains (cycles, deep nesting).
_MAX_DEPTH = 10
# Maximum concerns injected per patch.
_MAX_CONCERNS_PER_PATCH = 5

_ACK_WORDS = re.compile(
    r"\b(fixed|done|addressed|changed|updated|applied|removed|dropped|"
    r"rewritten|reworked|corrected|resolved|handled|incorporated)\b",
    re.IGNORECASE,
)

_IDENTIFIER_RE = re.compile(r"\b[a-z_][a-z_0-9]{3,}\b")


@dataclass(frozen=True)
class CritiqueReplyPair:
    """A maintainer concern from a prior version with its author reply."""

    version: int
    critique_message: str
    critique_author: str
    critique_severity: str
    target_patch_files: list[str]
    author_reply: str
    address_status: str   # "addressed_explicit" | "outstanding"
    address_notes: str    # annotation forwarded to the LLM prompt (not address_status)


class PriorVersionFetcher:
    """Fetches and reconciles prior-version maintainer concerns for a series.

    Constructed once per request; ``fetch()`` caches results after first call.
    """

    def __init__(self, lore_manager: Any, patch_manager: Any) -> None:
        self._lore = lore_manager
        self._pm = patch_manager
        self._cache: list[CritiqueReplyPair] | None = None

    def fetch(self, series: Any) -> list[CritiqueReplyPair]:
        if self._cache is not None:
            return self._cache
        try:
            self._cache = _fetch_impl(series, self._lore, self._pm)
        except Exception as exc:
            logger.warning("PriorVersionFetcher: unexpected error: %s", exc)
            self._cache = []
        return self._cache


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _fetch_impl(series: Any, lore: Any, pm: Any) -> list[CritiqueReplyPair]:
    thread_ids = discover_prior_version_thread_ids(series, lore)
    if not thread_ids:
        return []

    pairs: list[CritiqueReplyPair] = []
    for version in sorted(thread_ids.keys())[-_MAX_PRIOR_VERSIONS:]:
        thread_id = thread_ids[version]
        try:
            thread = lore.fetch(thread_id)
        except Exception as exc:
            logger.warning("WP-S2A: could not fetch v%d thread %s: %s", version, thread_id, exc)
            continue
        try:
            prior_series = pm.parse(thread)
        except Exception as exc:
            logger.warning("WP-S2A: could not parse v%d series: %s", version, exc)
            continue

        raw_pairs = extract_critique_reply_pairs(thread, lore, version)
        for pair in raw_pairs:
            reconciled = _reconcile(pair, series)
            pairs.append(reconciled)

    return pairs


def discover_prior_version_thread_ids(
    series: Any,
    lore: Any,
) -> dict[int, str]:
    """Return ``{version: thread_id}`` for discovered prior versions of *series*.

    Uses In-Reply-To chain traversal as primary strategy, lore search as
    fallback. Returns ``{}`` for v1 series or when all discovery attempts fail.
    """
    current_version: int = getattr(series, "version", 1)
    if current_version <= 1:
        return {}

    series_id: str = series.series_id
    author_email: str = _series_author_email(series)

    result: dict[int, str] = {}

    # --- Primary: walk In-Reply-To chain ---
    try:
        result = _walk_in_reply_to(series_id, current_version, author_email, lore)
    except Exception as exc:
        logger.warning("WP-S2A: In-Reply-To walk failed: %s", exc)

    if result:
        return result

    # --- Fallback: lore search ---
    try:
        result = _search_fallback(series, current_version, author_email, lore)
    except Exception as exc:
        logger.warning("WP-S2A: search fallback failed: %s", exc)

    return result


def _walk_in_reply_to(
    series_id: str,
    current_version: int,
    author_email: str,
    lore: Any,
) -> dict[int, str]:
    result: dict[int, str] = {}
    seen: set[str] = {series_id}
    depth = 0

    # Fetch the current thread and find its cover letter.
    try:
        thread = lore.fetch(series_id)
    except Exception:
        return {}

    cover = _find_cover_letter(thread)
    if cover is None or cover.in_reply_to is None:
        return {}

    next_id = cover.in_reply_to

    while depth < _MAX_DEPTH and next_id and next_id not in seen:
        seen.add(next_id)
        depth += 1

        try:
            prior_thread = lore.fetch(next_id)
        except Exception as exc:
            logger.debug("WP-S2A: could not fetch %s: %s", next_id, exc)
            break

        prior_cover = _find_cover_letter(prior_thread)
        if prior_cover is None:
            break

        # Validate: must be a cover letter, version < current, author match.
        si = prior_cover.subject_info
        if not si.is_cover_letter:
            break
        v = si.version
        if v >= current_version or v < 1:
            break
        if not _author_matches(prior_cover.from_email, author_email):
            break

        result[v] = prior_thread.thread_id
        logger.debug("WP-S2A: discovered v%d thread %s via In-Reply-To", v, prior_thread.thread_id)

        if v == 1:
            break

        if prior_cover.in_reply_to is None:
            break
        next_id = prior_cover.in_reply_to

    return result


def _search_fallback(
    series: Any,
    current_version: int,
    author_email: str,
    lore: Any,
) -> dict[int, str]:
    result: dict[int, str] = {}
    title_raw = getattr(series, "series_title", "") or ""
    title_normalized = re.sub(r"[:\-–—,\.]+$", "", title_raw.lower()).strip()
    if not title_normalized:
        return {}

    for target_version in range(current_version - 1, max(0, current_version - 1 - _MAX_PRIOR_VERSIONS), -1):
        if target_version < 1:
            break
        query = f'"{title_normalized}" v{target_version}'
        try:
            candidates = lore.search(query)
        except Exception as exc:
            logger.debug("WP-S2A: search failed for '%s': %s", query, exc)
            continue

        for msg_id in candidates:
            try:
                thread = lore.fetch(msg_id)
            except Exception:
                continue
            cover = _find_cover_letter(thread)
            if cover is None:
                continue
            si = cover.subject_info
            if not si.is_cover_letter or si.version != target_version:
                continue
            if not _author_matches(cover.from_email, author_email):
                continue
            result[target_version] = thread.thread_id
            logger.debug(
                "WP-S2A: discovered v%d thread %s via search", target_version, thread.thread_id
            )
            break  # take first author-matching result per version

    return result


def _find_cover_letter(thread: Any) -> Any | None:
    """Return the cover-letter Message (message_id == thread.thread_id)."""
    for msg in thread.messages:
        if msg.message_id == thread.thread_id:
            return msg
    # Fallback: any message flagged is_cover_letter
    for msg in thread.messages:
        if msg.subject_info.is_cover_letter:
            return msg
    return None


def _author_matches(email_a: str, email_b: str) -> bool:
    return email_a.lower().strip() == email_b.lower().strip()


def _series_author_email(series: Any) -> str:
    patches = getattr(series, "patches", [])
    if patches:
        return getattr(patches[0], "author_email", "") or ""
    return ""


# ---------------------------------------------------------------------------
# Critique/reply pair extraction
# ---------------------------------------------------------------------------


def extract_critique_reply_pairs(
    thread: Any,
    lore: Any,
    version: int,
) -> list[CritiqueReplyPair]:
    """Extract (maintainer critique, author reply) pairs from *thread*.

    Delegates to ``lore.extract_reviews`` for the maintainer comments, then
    matches each to a direct author reply via ``parse_conversation`` threading.
    """
    try:
        review_comments = lore.extract_reviews(thread)
    except Exception as exc:
        logger.warning("WP-S2A: extract_reviews failed: %s", exc)
        return []

    try:
        conversation = lore.parse_conversation(thread)
    except Exception as exc:
        logger.warning("WP-S2A: parse_conversation failed: %s", exc)
        conversation = []

    # Build message_id → reply-body map for direct (non-maintainer) children.
    by_id: dict[str, dict] = {row["message_id"]: row for row in conversation}
    author_replies: dict[str, str] = {}
    for row in conversation:
        parent_id = row.get("in_reply_to")
        if parent_id and not row.get("is_maintainer", False):
            # This is an author (non-maintainer) direct reply to parent_id.
            if parent_id not in author_replies:
                # strip quoted lines to get just the author's prose
                author_replies[parent_id] = _strip_quotes(row.get("body", ""))

    pairs: list[CritiqueReplyPair] = []
    for rc in review_comments:
        if not rc.is_maintainer:
            continue
        msg_id = getattr(rc, "message_id", "") or ""
        reply = author_replies.get(msg_id, "")

        # Infer files from any matching conversation row
        files: list[str] = []
        if msg_id in by_id:
            row = by_id[msg_id]
            files = list(row.get("files", []))

        pairs.append(
            CritiqueReplyPair(
                version=version,
                critique_message=rc.message or "",
                critique_author=rc.author or "",
                critique_severity=getattr(rc, "severity", "warning") or "warning",
                target_patch_files=files,
                author_reply=reply,
                address_status="outstanding",
                address_notes="",
            )
        )
    return pairs


def _strip_quotes(body: str) -> str:
    """Remove quoted lines (lines starting with '>') from an email body."""
    lines = [ln for ln in body.splitlines() if not ln.strip().startswith(">")]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Already-addressed reconciliation
# ---------------------------------------------------------------------------


def _reconcile(pair: CritiqueReplyPair, current_series: Any) -> CritiqueReplyPair:
    """Apply H1 + H2 heuristics to determine address_status and address_notes."""

    # H1: explicit author acknowledgement → suppress
    if pair.author_reply and _ACK_WORDS.search(pair.author_reply):
        return CritiqueReplyPair(
            version=pair.version,
            critique_message=pair.critique_message,
            critique_author=pair.critique_author,
            critique_severity=pair.critique_severity,
            target_patch_files=pair.target_patch_files,
            author_reply=pair.author_reply,
            address_status="addressed_explicit",
            address_notes="",
        )

    # H2: diff pattern absence → annotate (NOT suppress)
    tokens = _IDENTIFIER_RE.findall(pair.critique_message.lower())[:5]
    notes = ""
    if tokens:
        current_version_num = getattr(current_series, "version", "?")
        plus_lines = _gather_plus_lines(current_series, pair.target_patch_files)
        all_present = all(tok in plus_lines for tok in tokens)
        none_present = not any(tok in plus_lines for tok in tokens)
        if none_present:
            notes = f"[no matching diff pattern in v{current_version_num}; verify resolution]"
        elif not all_present:
            notes = f"[may be addressed in v{current_version_num}]"

    # H3: no author reply
    if not notes and not pair.author_reply:
        notes = "[author did not reply to this concern]"

    return CritiqueReplyPair(
        version=pair.version,
        critique_message=pair.critique_message,
        critique_author=pair.critique_author,
        critique_severity=pair.critique_severity,
        target_patch_files=pair.target_patch_files,
        author_reply=pair.author_reply,
        address_status="outstanding",
        address_notes=notes,
    )


def _gather_plus_lines(series: Any, target_files: list[str]) -> str:
    """Return all '+' diff lines from *series* patches touching *target_files*."""
    parts: list[str] = []
    for patch in getattr(series, "patches", []):
        diff = getattr(patch, "diff", "") or ""
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                parts.append(line[1:].lower())
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------


def format_prior_version_context(
    pairs: list[CritiqueReplyPair],
    patch_id: str,
) -> str:
    """Format outstanding prior-version concerns as a prompt block.

    Returns ``""`` when the list is empty (no prior data or all concerns
    addressed), preserving byte-identity for existing tests.

    *patch_id* is used for logging only; matching is based on ``target_patch_files``
    overlap or a broad match when file lists are unavailable.
    """
    outstanding = [p for p in pairs if p.address_status == "outstanding"]
    if not outstanding:
        return ""

    # Sort: highest severity first (blocker > warning > info), then most recent version first.
    _SEV_ORDER = {"blocker": 0, "warning": 1, "info": 2}
    outstanding.sort(
        key=lambda p: (_SEV_ORDER.get(p.critique_severity, 1), -p.version)
    )
    selected = outstanding[:_MAX_CONCERNS_PER_PATCH]

    lines = [
        "## Prior Version Feedback",
        "",
        "The following maintainer concerns were raised on earlier versions of this patch",
        "and appear unresolved. Consider whether they apply to the current version.",
        "",
    ]
    for pair in selected:
        line = f"- [v{pair.version}, {pair.critique_author}] {pair.critique_message}"
        if pair.address_notes:
            line += f" {pair.address_notes}"
        lines.append(line)

    lines.append("")
    return "\n".join(lines)
