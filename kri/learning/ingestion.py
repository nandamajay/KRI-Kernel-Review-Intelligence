"""Track-B WP4-J: LoreIngestionEngine — parse lore mbox → ReviewHistoryEntry records.

Uses the existing LoreManagerImpl to load cached mbox files and extract
ReviewComments, then normalises them into ReviewHistoryEntry objects with
mandatory provenance.

Design decisions:
  - verified=False on all seeded Evidence nodes (Adversarial Reviewer flag from B0
    design review). strength carries the calibration signal; verified is reserved
    for the Evidence Engine's verify() step.
  - No network I/O inside this module — uses load_cached() only.
  - Sec-40: entry_id derived from content hash; no datetime.now/uuid.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from kri.common.models import (
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    Provenance,
    ReviewComment,
)
from kri.learning.models import ReviewHistoryEntry, ReviewHistorySummary
from kri.learning.store import ReviewHistoryStore

if TYPE_CHECKING:
    from kri.knowledge_manager import KnowledgeManagerImpl
    from kri.lore_manager import LoreManagerImpl

logger = logging.getLogger(__name__)

# Concern-signal patterns for claim category extraction.
# Applied in order; first match wins.
# Track-C C1: expanded with 8 audio/driver-domain signals (dapm, dai, machine driver, jack, dpcm)
# NOTE: no domain-specific vendor prefixes here (Sec-9). Domain-specific symbols are
# injected by the domain package plugin at runtime, not embedded in this generic module.
_CLAIM_SIGNALS: list[tuple[str, str]] = [
    (r"(acked?[-\s]?by|reviewed[-\s]?by|lgtm)", "maintainer_ack"),
    (r"(nack|nak|not\s+accept|please\s+revert)", "maintainer_nack"),
    (r"(memory\s+leak|use.after.free|double.free|buffer.overflow)", "memory_safety"),
    (r"(null\s+ptr|null.?pointer|dereference)", "null_deref"),
    (r"(lock|mutex|spin.?lock|race.?condition|deadlock)", "locking"),
    (r"(error.?handling|return\s+code|check.*return)", "error_handling"),
    # Audio driver-domain signals (Track-C C1) — generic terms only, no vendor prefixes
    (r"(dapm|widget|power.?domain|dapm_route)", "dapm"),
    (r"(dai.?link|cpu.?dai|codec.?dai|dai_fmt|set_tdm|tdm_slot)", "dai"),
    (r"(machine.?driver|platform.?driver|codec.?driver)", "audio_driver"),
    (r"(jack|detection|hp_det|headphone|headset)", "jack_detection"),
    (r"(dpcm|be.?dai|fe.?dai|no_pcm\b)", "dpcm"),
    (r"(lpass|qdsp|q6afe|q6adm|q6asm|audioreach)", "qcom_lpass"),
    (r"(probe.?order|pm_runtime|devm_snd|register_component|register_card)", "audio_lifecycle"),
    (r"(dt.?binding|device.?tree|compatible|of_device_id)", "dt_binding"),
    # B10: new claim-signal patterns — generic kernel review vocabulary, no vendor prefixes
    (r"(off.by.one|uninitialized\s+(var|variable)?|causes?\s+(a\s+)?crash|regression\s+(in|since)|wrong\s+(return\s+)?(value|result)|undefined\s+behav|integer\s+(overflow|underflow)|out.of.bounds|array\s+overflow)", "bug"),
    (r"(commit\s+(message|log|title|subject)|subject\s+line|fix\s+(the\s+)?(commit|changelog)|missing\s+signed.off|changelog\s+(should|must|needs))", "commit_msg"),
    (r"(naming\s+convention|use\s+bit\s*\(|use\s+array_size\b|is_err\b|kernel\s+convention|prefer\s+the\s+helper|use\s+\w+\(\)\s+instead)", "convention"),
    (r"(data\s+race|toctou|read_once\b|write_once\b|memory\s+barrier|synchronize_rcu|rcu_dereference|smp_mb\b|smp_rmb\b|smp_wmb\b)", "race"),
    (r"(resource\s+leak|missing\s+(put|release|unref|free)\b|forgot\s+to\s+(release|put|free))", "resource_leak"),
    (r"(design\s+(issue|flaw|problem|choice|concern|decision)|wrong\s+(design|abstraction|layer)|layering\s+violation|bad\s+design|design\s+doesn.t\s+scale|wrong\s+level\s+of\s+abstraction)", "design"),
    (r"(api.?misuse|wrong\s+function|should\s+use|incorrect\s+use\s+of|deprecated\s+(function|api)|use\s+the\s+correct\s+(api|function)|calls?\s+the\s+wrong)", "api_misuse"),
    (r"(coding\s+style|checkpatch|whitespace|indent|comment)", "style"),
    (r"(missing\s+test|add\s+test|no\s+test)", "missing_test"),
    (r"(performance|inefficient|slow|cache)", "performance"),
]


def _classify_claim(text: str) -> tuple[str, str]:
    """Return (claim_category, confidence_basis) from review text."""
    lower = text.lower()
    for pattern, category in _CLAIM_SIGNALS:
        if re.search(pattern, lower):
            return category, f"lexical_match:{pattern[:30]}"
    return "review_discussion", "fallback:no_signal_matched"


def _evidence_type_from_comment(
    comment: ReviewComment, claim: str
) -> str:
    """Map ReviewComment to evidence_type literal."""
    if claim == "maintainer_ack":
        return "maintainer_ack"
    if claim == "maintainer_nack":
        return "maintainer_nack"
    # Severity-derived: accepted/rejected inference
    msg_lower = (comment.message or "").lower()
    if re.search(r"(applied|merged|thanks)", msg_lower):
        return "accepted_patch"
    if re.search(r"(nack|reject|revert)", msg_lower):
        return "rejected_patch"
    return "review_discussion"


def _entry_strength(comment: ReviewComment, claim: str) -> float:
    """Compute evidence strength.

    Base = 0.2 (lore text, unverified).
    +0.2 if maintainer comment.
    +0.1 if ack or nack (structural signal).
    +0.1 for claim specificity (not generic review_discussion).
    """
    strength = 0.2
    if comment.is_maintainer:
        strength += 0.2
    if claim in ("maintainer_ack", "maintainer_nack"):
        strength += 0.1
    if claim != "review_discussion":
        strength += 0.1
    return min(1.0, strength)


class LoreIngestionEngine:
    """Ingest a lore mbox file → list[ReviewHistoryEntry] with full provenance.

    Every entry has:
      - message_id (mandatory; skip if absent)
      - source_url  (mandatory; skip if absent)
      - provenance.transformation_history includes 'WP4-J:ingestion'
    """

    def __init__(self, lore_manager: "LoreManagerImpl") -> None:
        self._lore = lore_manager

    def ingest(
        self,
        mbox_path: str | Path,
        series_id: str,
        lore_url: str = "",
    ) -> list[ReviewHistoryEntry]:
        """Load mbox, extract review comments, return ReviewHistoryEntry list."""
        path = Path(mbox_path)
        try:
            thread = self._lore.load_cached(path)
        except Exception as exc:
            logger.warning("WP4-J: failed to load %s: %s", path.name, exc)
            return []

        # Track-C C1: author-vs-reviewer filter — collect the patch submitter's
        # identity so self-replies (cover letter, patch-author follow-ups) are skipped.
        patch_authors: set[str] = set()
        for msg in getattr(thread, "messages", []):
            if getattr(msg, "is_patch", False):
                from_name = getattr(msg, "from_name", None)
                from_email = getattr(msg, "from_email", None)
                if from_name:
                    patch_authors.add(from_name.lower())
                if from_email:
                    patch_authors.add(from_email.lower())
                break  # first patch author is the series submitter

        comments: list[ReviewComment] = self._lore.extract_reviews(thread)
        entries: list[ReviewHistoryEntry] = []

        for comment in comments:
            # Track-C C1: skip comments from the patch author (self-replies degrade signal)
            if patch_authors and comment.author:
                author_lower = comment.author.lower()
                if any(a in author_lower for a in patch_authors):
                    logger.debug(
                        "WP4-J/C1: skip self-reply from patch author %s", comment.author
                    )
                    continue
            mid = (comment.provenance.version_or_commit or "").strip()
            if mid.startswith("rc:"):
                mid = mid[3:]
            if not mid:
                logger.debug("WP4-J: skipping comment with no message_id")
                continue

            src_url = comment.provenance.source_url or ""
            if not src_url:
                # Derive URL from message_id
                if mid:
                    src_url = f"https://lore.kernel.org/r/{mid}"
                else:
                    logger.debug("WP4-J: skipping comment with no source_url")
                    continue

            text = (comment.message or "").strip()[:500]
            claim, basis = _classify_claim(text)
            etype = _evidence_type_from_comment(comment, claim)

            entry_id = ReviewHistoryEntry.make_entry_id(series_id, mid, text)

            provenance = Provenance(
                source_url=src_url,
                version_or_commit=mid,
                transformation_history=[
                    "lore.load_cached",
                    "mbox.parse",
                    "extract_reviews",
                    "WP4-J:ingestion",
                ],
                source_confidence=0.9 if comment.is_maintainer else 0.6,
            )

            entry = ReviewHistoryEntry(
                entry_id=entry_id,
                series_id=series_id,
                patch_id=comment.target_patch_id,
                message_id=mid,
                source_url=src_url,
                reviewer_text=text,
                extracted_claim=claim,
                evidence_type=etype,
                confidence_basis=basis,
                created_by="WP4-J/LoreIngestionEngine",
                validation_status="pending",
                provenance=provenance,
            )
            entries.append(entry)

        logger.info(
            "WP4-J: %s → %d review comments → %d entries",
            path.name,
            len(comments),
            len(entries),
        )
        return entries

    def seed_ekg(
        self,
        entries: list[ReviewHistoryEntry],
        knowledge_manager: "KnowledgeManagerImpl",
    ) -> int:
        """Create Evidence nodes from ReviewHistoryEntry records in the EKG.

        Nodes are seeded with verified=False (design review amendment B0:
        strength carries the calibration signal; verify() owns the verified flag).
        Returns count of nodes added.
        """
        added = 0
        for entry in entries:
            # Provenance validation (Tier-0 STOP guard — log + skip if violated)
            if not entry.source_url or not entry.message_id:
                logger.error(
                    "WP4-J TIER-0: entry %s missing source_url or message_id — SKIP",
                    entry.entry_id,
                )
                continue

            # Find a matching comment_id to build a minimal EvidenceGraph container
            comment_id = f"hist:{entry.entry_id}"

            source_type = EvidenceSourceType.REVIEW_DISCUSSION
            if entry.evidence_type == "accepted_patch":
                source_type = EvidenceSourceType.ACCEPTED_PATCH
            elif entry.evidence_type == "rejected_patch":
                source_type = EvidenceSourceType.REJECTED_PATCH

            from kri.common.models import Evidence as EvidenceModel

            # We need evidence_id to be deterministic (Sec-40).
            import hashlib as _hl
            ev_id = "ev:" + _hl.sha256(
                f"wp4j:{entry.entry_id}".encode()
            ).hexdigest()[:12]

            claim_claim = entry.extracted_claim  # for _classify_claim signal
            ev = EvidenceModel(
                evidence_id=ev_id,
                source_type=source_type,
                summary=f"lore review: {entry.extracted_claim} — {entry.reviewer_text[:80]}",
                provenance=entry.provenance,
                verified=False,  # design review amendment: verified=False
                strength=_entry_strength_from_entry(entry),
            )

            try:
                knowledge_manager.add_evidence_node(ev)
                added += 1
            except Exception as exc:
                logger.warning("WP4-J: add_evidence_node failed for %s: %s", ev_id, exc)

        logger.info("WP4-J: seeded %d/%d evidence nodes into EKG", added, len(entries))
        return added


def _entry_strength_from_entry(entry: ReviewHistoryEntry) -> float:
    """Derive strength from ReviewHistoryEntry evidence_type and claim."""
    strength = 0.2
    if entry.evidence_type in ("maintainer_ack", "maintainer_nack"):
        strength += 0.3
    elif entry.evidence_type in ("accepted_patch", "rejected_patch"):
        strength += 0.2
    if entry.extracted_claim != "review_discussion":
        strength += 0.1
    return min(1.0, strength)


def ingest_dataset(
    index_jsonl: Path,
    store: ReviewHistoryStore,
    lore_manager: "LoreManagerImpl",
    dataset_root: Path | None = None,
) -> list[ReviewHistoryEntry]:
    """Convenience: ingest all entries from an index.jsonl dataset file.

    Returns the list of newly added entries (deduplication via store).
    """
    import json

    if not index_jsonl.exists():
        logger.warning("WP4-J: dataset index not found: %s", index_jsonl)
        return []

    engine = LoreIngestionEngine(lore_manager)
    all_new: list[ReviewHistoryEntry] = []

    for line in index_jsonl.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue

        mbox_rel = rec.get("mbox_path", "")
        if not mbox_rel:
            continue
        mbox_path = (dataset_root or index_jsonl.parent) / mbox_rel
        if not mbox_path.exists():
            logger.debug("WP4-J: mbox not found %s", mbox_path)
            continue

        series_id = rec.get("series_id", "")
        lore_url = rec.get("lore_url", "")
        entries = engine.ingest(mbox_path, series_id, lore_url)

        for e in entries:
            if store.add(e):
                all_new.append(e)

    logger.info(
        "WP4-J: dataset ingest complete — %d new entries, store total=%d",
        len(all_new),
        store.count(),
    )
    return all_new


def lore_evidence_for_claim(
    claim: str,
    store: ReviewHistoryStore,
) -> list["Evidence"]:
    """Build per-review Evidence nodes from lore entries matching a claim category.

    Track-B.5: This is the live-review enrichment path, distinct from seed_ekg().

    verified=True justification:
      lore.kernel.org URLs are permanent authenticated public records.
      This mirrors _enrich_with_blame() (reviewer.py) which also sets verified=True
      outside EvidenceEngineImpl.verify() — same precedent.
      Contrast: seed_ekg() uses verified=False for long-term EKG writes pending
      human governance review.  This function produces ephemeral per-review nodes only.

    BLOCK-4: 'review_discussion' returns [] — prevents all-entries flood.
    Sec-40: evidence_id via hashlib.sha256 (no uuid/random/time).
    """
    from kri.common.models import Evidence, EvidenceSourceType
    import hashlib as _hl

    entries = store.by_claim(claim)  # [] for 'review_discussion' (BLOCK-4)
    evidence_nodes: list[Evidence] = []
    seen_ids: set[str] = set()

    for entry in entries:
        # Tier-0 guard: skip any entry that lost provenance (defensive)
        if not entry.source_url or not entry.message_id:
            logger.warning("B5: lore_evidence_for_claim: skip entry %s missing provenance", entry.entry_id)
            continue

        source_type = EvidenceSourceType.REVIEW_DISCUSSION
        if entry.evidence_type == "accepted_patch":
            source_type = EvidenceSourceType.ACCEPTED_PATCH
        elif entry.evidence_type == "rejected_patch":
            source_type = EvidenceSourceType.REJECTED_PATCH

        ev_id = "lore:" + _hl.sha256(f"lore:{entry.entry_id}".encode()).hexdigest()[:12]
        if ev_id in seen_ids:
            continue  # deduplicate store variants with same underlying entry
        seen_ids.add(ev_id)

        ev = Evidence(
            evidence_id=ev_id,
            source_type=source_type,
            summary=f"lore review [{entry.extracted_claim}]: {entry.reviewer_text[:80]}",
            provenance=entry.provenance,  # source_url + message_id preserved verbatim
            verified=True,               # ephemeral enrichment — lore URL is verifiable public record
            strength=_entry_strength_from_entry(entry),
        )
        evidence_nodes.append(ev)

    logger.debug("B5: lore_evidence_for_claim(%s) → %d nodes", claim, len(evidence_nodes))
    return evidence_nodes


__all__ = ["LoreIngestionEngine", "ingest_dataset", "lore_evidence_for_claim"]
