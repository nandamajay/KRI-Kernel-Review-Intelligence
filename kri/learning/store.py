"""Track-B WP4-J: ReviewHistoryStore — persistent JSONL store for ReviewHistoryEntry.

Designed for the lore review dataset scale (~100-500 entries). Each entry is
stored as a line of JSON in `<ledger_dir>/review_history.jsonl`. The store
is append-only; deduplication uses entry_id.

Sec-40: no datetime.now() / uuid / random outside kri/learning/.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from kri.learning.models import ReviewHistoryEntry, ReviewHistorySummary


class ReviewHistoryStore:
    """Append-only JSONL store for ReviewHistoryEntry records.

    Thread-safety: single-process only (write lock per flush).
    """

    _DEFAULT_PATH = (
        Path(os.environ.get("KRI_LEDGER_DIR", ".kri/ledger"))
        / "review_history.jsonl"
    )

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = path or self._DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, ReviewHistoryEntry] = {}
        if self._path.exists():
            self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, entry: ReviewHistoryEntry) -> bool:
        """Add an entry; returns False if entry_id already present (dedup)."""
        if entry.entry_id in self._entries:
            return False
        self._entries[entry.entry_id] = entry
        with self._path.open("a") as f:
            f.write(entry.model_dump_json() + "\n")
        return True

    def all(self) -> list[ReviewHistoryEntry]:
        return list(self._entries.values())

    def by_series(self, series_id: str) -> list[ReviewHistoryEntry]:
        return [e for e in self._entries.values() if e.series_id == series_id]

    def by_claim(self, claim: str) -> list[ReviewHistoryEntry]:
        """Return entries matching extracted_claim exactly.

        Returns [] for 'review_discussion' — prevents broad fallback flood that
        would give every comment identical lore evidence (Track-B.5 BLOCK-4).
        """
        if claim == "review_discussion":
            return []
        return [e for e in self._entries.values() if e.extracted_claim == claim]

    def count(self) -> int:
        return len(self._entries)

    def summarise(self) -> list[ReviewHistorySummary]:
        """Return one ReviewHistorySummary per series_id."""
        by_sid: dict[str, list[ReviewHistoryEntry]] = {}
        for e in self._entries.values():
            by_sid.setdefault(e.series_id, []).append(e)

        summaries: list[ReviewHistorySummary] = []
        for sid, entries in sorted(by_sid.items()):
            cats: dict[str, int] = {}
            etypes: dict[str, int] = {}
            urls: list[str] = []
            has_maint = False
            for e in entries:
                cats[e.extracted_claim] = cats.get(e.extracted_claim, 0) + 1
                etypes[e.evidence_type] = etypes.get(e.evidence_type, 0) + 1
                if e.source_url and e.source_url not in urls:
                    urls.append(e.source_url)
                if e.evidence_type in ("maintainer_ack", "maintainer_nack"):
                    has_maint = True
            summaries.append(
                ReviewHistorySummary(
                    series_id=sid,
                    entry_count=len(entries),
                    source_urls=urls,
                    claim_categories=cats,
                    evidence_types=etypes,
                    has_maintainer_feedback=has_maint,
                )
            )
        return summaries

    def summarise_by_series_ids(self, series_ids: set[str]) -> list[ReviewHistorySummary]:
        """Summarise only the series present in series_ids.

        Used by Track-B.5 post-hoc matched-series assembly: after enrichment, only
        the series that actually contributed Evidence nodes are surfaced.
        """
        if not series_ids:
            return []
        by_sid: dict[str, list[ReviewHistoryEntry]] = {}
        for e in self._entries.values():
            if e.series_id in series_ids:
                by_sid.setdefault(e.series_id, []).append(e)

        summaries: list[ReviewHistorySummary] = []
        for sid, entries in sorted(by_sid.items()):
            cats: dict[str, int] = {}
            etypes: dict[str, int] = {}
            urls: list[str] = []
            has_maint = False
            for e in entries:
                cats[e.extracted_claim] = cats.get(e.extracted_claim, 0) + 1
                etypes[e.evidence_type] = etypes.get(e.evidence_type, 0) + 1
                if e.source_url and e.source_url not in urls:
                    urls.append(e.source_url)
                if e.evidence_type in ("maintainer_ack", "maintainer_nack"):
                    has_maint = True
            summaries.append(
                ReviewHistorySummary(
                    series_id=sid,
                    entry_count=len(entries),
                    source_urls=urls,
                    claim_categories=cats,
                    evidence_types=etypes,
                    has_maintainer_feedback=has_maint,
                )
            )
        return summaries

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                entry = ReviewHistoryEntry.model_validate(obj)
                self._entries[entry.entry_id] = entry
            except Exception:
                pass  # malformed line — skip silently (load is best-effort)


__all__ = ["ReviewHistoryStore"]

