"""Historical pattern extraction — the Learning Engine's extract stage (Sprint-2).

Ingests real patch series + their maintainer review comments (via the Sprint-1
Lore/Patch managers), classifies the *concerns* maintainers raised, and generalises
them into candidate :class:`Pattern` dicts. Patterns are validated against the
evidence-count thresholds from the SPEC:

    >= 5 examples  => "possible"
    >= 20 examples => "likely"
    >= 50 examples => "certain"
    (below 5 => "insufficient" / LOW support — NOT yet validated)

With only the three cached fixtures we cannot reach these thresholds, so the
pipeline correctly reports LOW support rather than fabricating confidence
(Constitution: no hallucinated knowledge). Every extracted pattern cites the exact
:class:`ReviewComment`s it generalises (provenance).

**Domain Isolation:** this module is part of the Generic Runtime — it contains NO
domain identifiers. Concern categorisation is driven by generic lexical signals
(supplied as data), never by hardcoded subsystem terms. Determinism: extraction is
a pure function of its inputs; no wall-clock, no RNG. (Stochastic model elements,
if any, are confined to the Learning *loop* in Sprint-3 per Constitution Sec. 40.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from kri.common.interfaces import LoreManager, PatchManager
from kri.common.models import (
    PatchOutcome,
    ReviewComment,
    Severity,
)

# Evidence-count thresholds (SPEC learning validation).
SUPPORT_THRESHOLDS: dict[str, int] = {"certain": 50, "likely": 20, "possible": 5}


def support_level(example_count: int) -> str:
    """Map an evidence count to a support label (deterministic, conservative)."""
    if example_count >= SUPPORT_THRESHOLDS["certain"]:
        return "certain"
    if example_count >= SUPPORT_THRESHOLDS["likely"]:
        return "likely"
    if example_count >= SUPPORT_THRESHOLDS["possible"]:
        return "possible"
    return "insufficient"


# Generic concern-category lexicon. These are language/process signals a reviewer
# uses across *any* subsystem — request-for-change phrasing, correctness, and
# process — not domain terms. Ordered; first match wins for a comment's primary
# category. Kept here as data so the runtime stays domain-agnostic.
_CONCERN_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("nack", ("nacked-by",)),
    ("approval", ("reviewed-by", "acked-by", "tested-by")),
    ("error_handling", ("cleanup", "leak", "free", "error path", "unwind", "double")),
    ("locking", ("lock", "mutex", "spinlock", "race", "deadlock")),
    ("api_misuse", ("should use", "instead of", "use the", "api", "helper", "wrapper")),
    ("design", ("expect", "usually", "should be", "belongs", "not from", "abstraction")),
    ("documentation", ("document", "kernel-doc", "comment", "binding", "dt-binding")),
    ("style", ("checkpatch", "whitespace", "formatting", "indent", "coding style")),
]

_CHANGE_REQUEST_RE = re.compile(
    r"\b(should|must|please|instead|expect|why not|drop|remove|don't|do not|needs? to)\b",
    re.IGNORECASE,
)


@dataclass
class Concern:
    """A single classified maintainer concern extracted from one ReviewComment."""

    comment_id: str
    category: str
    is_change_request: bool
    author: str | None
    is_maintainer: bool
    target_patch_id: str | None
    source_url: str | None
    excerpt: str


@dataclass
class CandidatePattern:
    """A generalised candidate pattern with its supporting evidence + support level."""

    pattern_id: str
    category: str
    description: str
    outcome: str
    example_comment_ids: list[str] = field(default_factory=list)
    example_patch_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    support_count: int = 0
    support_level: str = "insufficient"
    validated: bool = False

    def as_pattern(self) -> dict[str, Any]:
        """Render to the generic :class:`Pattern` dict shape (with provenance)."""
        return {
            "pattern_id": self.pattern_id,
            "category": self.category,
            "description": self.description,
            "outcome": self.outcome,
            "examples": sorted(self.example_patch_ids),
            "evidence_comment_ids": sorted(self.example_comment_ids),
            "provenance_urls": sorted(self.source_urls),
            "support_count": self.support_count,
            "support_level": self.support_level,
            "validated": self.validated,
        }


def classify_comment(comment: ReviewComment) -> Concern:
    """Classify one :class:`ReviewComment` into a generic concern.

    Deterministic: matches the concern lexicon in fixed order. The maintainer's
    own trailer category (approval/nack) from the Lore Manager takes precedence."""
    text = (comment.message or "").lower()
    category = ""
    # Honour the structural category the Lore Manager already derived from tags.
    if comment.category in ("nack", "approval"):
        category = comment.category
    else:
        for name, signals in _CONCERN_SIGNALS:
            if any(sig in text for sig in signals):
                category = name
                break
        if not category:
            category = "review_discussion"
    is_change = bool(_CHANGE_REQUEST_RE.search(comment.message or "")) or category == "nack"
    return Concern(
        comment_id=comment.comment_id,
        category=category,
        is_change_request=is_change,
        author=comment.author,
        is_maintainer=comment.is_maintainer,
        target_patch_id=comment.target_patch_id,
        source_url=comment.provenance.source_url if comment.provenance else None,
        excerpt=(comment.message or "").strip()[:200],
    )


class HistoricalPatternExtractor:
    """Extract + validate historical review patterns from cached lore fixtures.

    Offline by construction: it consumes a :class:`LoreManager` (offline replay)
    and a :class:`PatchManager`. Designed to scale to ~100-150 fetched threads;
    tests run over the 3 cached fixtures."""

    def __init__(self, lore_manager: LoreManager, patch_manager: PatchManager) -> None:
        self._lore = lore_manager
        self._patch = patch_manager

    # -- ingest one thread ---------------------------------------------------
    def ingest_thread(self, thread: Any) -> list[Concern]:
        """Extract classified maintainer concerns from one parsed thread.

        Only maintainer comments that are change-requests (or nacks) count as
        learning signal — an approval trailer is recorded but is not a concern to
        generalise into a *rejected*-style pattern (benchmark-honest)."""
        comments = self._lore.extract_reviews(thread)
        concerns: list[Concern] = []
        for c in comments:
            concern = classify_comment(c)
            concerns.append(concern)
        # Deterministic order by comment_id.
        concerns.sort(key=lambda x: x.comment_id)
        return concerns

    # -- generalise across many threads -------------------------------------
    def extract_patterns(self, threads: list[Any]) -> list[CandidatePattern]:
        """Ingest every thread, group maintainer change-request concerns by
        category, and emit one candidate pattern per category with its support
        level. Deterministic ordering throughout."""
        buckets: dict[str, CandidatePattern] = {}
        all_concerns: list[Concern] = []
        for t in threads:
            all_concerns.extend(self.ingest_thread(t))

        for concern in all_concerns:
            # Learning signal = a maintainer asking for a change (or a nack).
            if not (concern.is_maintainer and concern.is_change_request):
                continue
            if concern.category in ("approval",):
                continue
            cp = buckets.get(concern.category)
            if cp is None:
                cp = CandidatePattern(
                    pattern_id=f"learned:{concern.category}",
                    category=concern.category,
                    description=(
                        f"Maintainers requested changes categorised as "
                        f"'{concern.category}'."
                    ),
                    outcome="rejected" if concern.category == "nack" else "modified",
                )
                buckets[concern.category] = cp
            cp.example_comment_ids.append(concern.comment_id)
            if concern.target_patch_id:
                cp.example_patch_ids.append(concern.target_patch_id)
            if concern.source_url:
                cp.source_urls.append(concern.source_url)

        patterns: list[CandidatePattern] = []
        for cp in buckets.values():
            cp.example_comment_ids = sorted(set(cp.example_comment_ids))
            cp.example_patch_ids = sorted(set(cp.example_patch_ids))
            cp.source_urls = sorted(set(cp.source_urls))
            cp.support_count = len(cp.example_comment_ids)
            cp.support_level = support_level(cp.support_count)
            cp.validated = cp.support_level != "insufficient"
            patterns.append(cp)
        patterns.sort(key=lambda p: p.pattern_id)
        return patterns

    def validate(self, pattern: CandidatePattern) -> dict[str, Any]:
        """Return a validation result: pass/fail against thresholds + metrics.

        A pattern only 'passes' (is promotable to knowledge) once it reaches at
        least the 'possible' threshold (>=5 supporting comments). Below that it is
        reported as LOW support — never promoted, never fabricated confidence."""
        level = support_level(pattern.support_count)
        return {
            "pattern_id": pattern.pattern_id,
            "support_count": pattern.support_count,
            "support_level": level,
            "passed": level != "insufficient",
            "threshold_possible": SUPPORT_THRESHOLDS["possible"],
            "example_comment_ids": pattern.example_comment_ids,
            "provenance_urls": pattern.source_urls,
        }


__all__ = [
    "HistoricalPatternExtractor",
    "Concern",
    "CandidatePattern",
    "classify_comment",
    "support_level",
    "SUPPORT_THRESHOLDS",
    "PatchOutcome",
    "Severity",
]
