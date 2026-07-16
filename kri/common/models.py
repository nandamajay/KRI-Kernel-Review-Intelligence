"""KRI core domain-agnostic types.

Every type in this module is part of the Generic Runtime and MUST remain free of
any domain-specific references — a subsystem's C symbol prefix, source path, or
product name (Domain Isolation, Constitution Sec. 9). Domain
content is expressed as *data* flowing through these types, never as hardcoded
identifiers here.

These models implement the Cognition Layer artifact separation
(Constitution Sec. 5.2): Knowledge -> Reasoning -> Decision -> Evidence -> Review.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Severity of a decision/review comment. Domain-agnostic."""

    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"


class ConfidenceLevel(str, Enum):
    """Confidence bands (Blueprint Sec. 16.10). Score->level mapping lives in the
    Confidence Engine; see ``ConfidenceLevel.from_score``."""

    CERTAIN = "certain"          # 0.95 - 1.00
    LIKELY = "likely"            # 0.80 - 0.94
    POSSIBLE = "possible"        # 0.60 - 0.79
    SPECULATIVE = "speculative"  # 0.40 - 0.59
    UNKNOWN = "unknown"          # < 0.40

    @classmethod
    def from_score(cls, score: float) -> ConfidenceLevel:
        """Deterministic score -> level mapping (Blueprint Sec. 16.10)."""
        if score >= 0.95:
            return cls.CERTAIN
        if score >= 0.80:
            return cls.LIKELY
        if score >= 0.60:
            return cls.POSSIBLE
        if score >= 0.40:
            return cls.SPECULATIVE
        return cls.UNKNOWN


class RuleType(str, Enum):
    """Rule classification (Blueprint Sec. 10.3). Drives SubsystemEvidence scoring."""

    HARD = "hard"                  # score weight 1.0
    SOFT = "soft"                  # score weight 0.7
    PHILOSOPHICAL = "philosophical"  # score weight 0.5


class PatchOutcome(str, Enum):
    """Historical patch outcome (Blueprint Sec. 19.2)."""

    ACCEPTED = "accepted"
    MODIFIED = "modified"
    REJECTED = "rejected"
    PENDING = "pending"  # excluded from training


class ReasoningLayer(str, Enum):
    """Six-layer Reasoning Hierarchy (Blueprint Sec. 7). MVP covers 1-3."""

    STRUCTURAL = "structural"
    SEMANTIC = "semantic"
    DESIGN = "design"
    INTEGRATION = "integration"
    MAINTAINABILITY = "maintainability"
    ECOSYSTEM = "ecosystem"


class EvidenceSourceType(str, Enum):
    """Evidence source taxonomy (Blueprint Sec. 15.3), ordered by reliability priority.

    Reliability priority is exposed via ``EVIDENCE_SOURCE_PRIORITY``.
    """

    DOCUMENTATION = "documentation"          # priority 1, Very High
    MAINTAINERS_FILE = "maintainers_file"    # priority 2, Very High
    API_HEADER = "api_header"                # priority 3, Very High
    ACCEPTED_PATCH = "accepted_patch"        # priority 4, High
    REJECTED_PATCH = "rejected_patch"        # priority 5, High
    REVIEW_DISCUSSION = "review_discussion"  # priority 6, High
    COMMIT_MESSAGE = "commit_message"        # priority 7, Medium-High
    STATIC_ANALYSIS = "static_analysis"      # priority 8, Medium
    BLAME_HISTORY = "blame_history"          # priority 9, Medium
    CODE_SIMILARITY = "code_similarity"      # priority 10, Medium
    MAINTAINER_BLOG = "maintainer_blog"      # priority 11, Low-Medium
    DESIGN_INFERENCE = "design_inference"    # priority 12, Low (must be speculative)


# Lower number == higher reliability (Blueprint Sec. 15.3 priority matrix).
EVIDENCE_SOURCE_PRIORITY: dict[EvidenceSourceType, int] = {
    EvidenceSourceType.DOCUMENTATION: 1,
    EvidenceSourceType.MAINTAINERS_FILE: 2,
    EvidenceSourceType.API_HEADER: 3,
    EvidenceSourceType.ACCEPTED_PATCH: 4,
    EvidenceSourceType.REJECTED_PATCH: 5,
    EvidenceSourceType.REVIEW_DISCUSSION: 6,
    EvidenceSourceType.COMMIT_MESSAGE: 7,
    EvidenceSourceType.STATIC_ANALYSIS: 8,
    EvidenceSourceType.BLAME_HISTORY: 9,
    EvidenceSourceType.CODE_SIMILARITY: 10,
    EvidenceSourceType.MAINTAINER_BLOG: 11,
    EvidenceSourceType.DESIGN_INFERENCE: 12,
}


# ---------------------------------------------------------------------------
# Versioning / provenance primitives
# ---------------------------------------------------------------------------


class KernelVersion(BaseModel):
    """A specific kernel version, e.g. "6.9-rc1". Kept as a normalized string plus
    a sortable tuple so temporal validity ranges can be compared deterministically."""

    raw: str = Field(..., description='Original version string, e.g. "6.9-rc1"')
    major: int
    minor: int
    patch: int = 0
    rc: int | None = None

    def sort_key(self) -> tuple[int, int, int, int]:
        # rc versions sort *before* the final release of the same x.y.z
        return (self.major, self.minor, self.patch, self.rc if self.rc is not None else 9999)


class VersionRange(BaseModel):
    """Temporal validity window for KG nodes/edges and DKPs (Blueprint Sec. 8.4).

    ``valid_until=None`` means "still valid at HEAD". Half-open interval semantics:
    [valid_from, valid_until).
    """

    valid_from: KernelVersion
    valid_until: KernelVersion | None = None


class Provenance(BaseModel):
    """Data provenance record (Constitution Sec. 37). Required on every Evidence node."""

    source_url: str | None = Field(None, description="Lore URL or repo path")
    repo_path: str | None = None
    commit_hash: str | None = None
    retrieved_at: datetime | None = None
    version_or_commit: str | None = None
    transformation_history: list[str] = Field(default_factory=list)
    source_confidence: float | None = None


# ---------------------------------------------------------------------------
# Patch domain (Generic Runtime — no domain identifiers)
# ---------------------------------------------------------------------------


class Patch(BaseModel):
    """A single patch within a series (Runtime input, Blueprint Sec. 21.2/23)."""

    patch_id: str
    subject: str
    author: str | None = None
    commit_message: str = ""
    files_changed: list[str] = Field(default_factory=list)
    diff: str = ""
    sequence: int = 0            # position within the series (0/N)
    series_total: int = 0        # N in "PATCH v3 x/N"


class PatchSeries(BaseModel):
    """A patch series parsed from a lore thread or direct upload (Blueprint Sec. 12.1)."""

    series_id: str = Field(..., description="Stable id, e.g. lore thread id")
    title: str = ""
    cover_letter: str = ""
    version: int = 1             # v1, v2, v3...
    patches: list[Patch] = Field(default_factory=list)
    target_kernel_version: KernelVersion | None = None
    lore_thread_url: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class ReviewComment(BaseModel):
    """A maintainer review comment extracted from lore, OR a KRI-generated comment.

    When generated by KRI it MUST carry at least one supporting Evidence node
    (Constitution Sec. 28 / Sec. 15.4)."""

    comment_id: str
    target_series_id: str | None = None
    target_patch_id: str | None = None
    location: str | None = Field(None, description='e.g. "path/to/file.c:45"')
    category: str = Field("", description="Free-form concern category, e.g. api_usage")
    severity: Severity = Severity.INFO
    message: str = ""
    author: str | None = None
    is_maintainer: bool = False   # true for ground-truth maintainer comments
    provenance: Provenance = Field(default_factory=Provenance)


# ---------------------------------------------------------------------------
# Knowledge artifacts
# ---------------------------------------------------------------------------


class Rule(BaseModel):
    """An engineering rule governing a subsystem (Blueprint Sec. 10.3). Rule *content*
    is domain data supplied by a DKP; this container is domain-agnostic."""

    rule_id: str
    category: str = ""
    rule_type: RuleType = RuleType.SOFT
    description: str = ""
    rationale: str = ""
    documentation_ref: str | None = None
    historical_enforcement_rate: float | None = None
    exceptions: list[str] = Field(default_factory=list)
    version_range: VersionRange | None = None


# ---------------------------------------------------------------------------
# Evidence layer
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """A single, sourced, verifiable evidence item (Constitution Sec. 28).

    ``verified`` is set by the Evidence Engine's ``verify()``. Unverified evidence
    must be suppressed or its comment down-graded to Speculative/Unknown."""

    evidence_id: str
    source_type: EvidenceSourceType
    summary: str = ""
    provenance: Provenance
    version_range: VersionRange | None = None
    verified: bool = False
    strength: float = Field(0.0, ge=0.0, le=1.0, description="Evidence Engine strength score")


class EvidenceGraph(BaseModel):
    """The Evidence Graph attached to a single ReviewComment (Blueprint Sec. 15.1).

    Nodes/edges are kept as typed lists so the graph can be serialized into a report
    and independently audited without re-running the system (Constitution Sec. 39)."""

    comment_id: str
    evidence: list[Evidence] = Field(default_factory=list)
    subsystem_rule: Rule | None = None
    accepted_examples: list[str] = Field(default_factory=list)  # patch ids / commit hashes
    rejected_examples: list[str] = Field(default_factory=list)
    alternative_recommendation: str | None = None
    alternative_precedents: list[str] = Field(default_factory=list)

    def has_verified_evidence(self) -> bool:
        """Constitutional gate (Sec. 28): at least one verified Evidence node."""
        return any(e.verified for e in self.evidence)


# ---------------------------------------------------------------------------
# Confidence layer
# ---------------------------------------------------------------------------


class ConfidenceFactor(str, Enum):
    """The 8 confidence factors (Blueprint Sec. 16.1)."""

    HISTORICAL_AGREEMENT = "historical_agreement"
    SUBSYSTEM_EVIDENCE = "subsystem_evidence"
    DOCUMENTATION_SUPPORT = "documentation_support"
    API_CERTAINTY = "api_certainty"
    CODE_SIMILARITY = "code_similarity"
    REVIEW_HISTORY = "review_history"
    VERSION_CONSISTENCY = "version_consistency"
    RUNTIME_EVIDENCE = "runtime_evidence"


class ConfidenceScore(BaseModel):
    """Computed confidence for a decision/comment (Constitution Sec. 31).

    Must be reproducible, calibrated, explainable, conservative. The per-factor
    ``factor_scores`` make the score inspectable."""

    score: float = Field(..., ge=0.0, le=1.0)
    level: ConfidenceLevel
    factor_scores: dict[ConfidenceFactor, float] = Field(default_factory=dict)
    factor_weights: dict[ConfidenceFactor, float] = Field(default_factory=dict)
    explanation: str = ""


# ---------------------------------------------------------------------------
# Decision layer
# ---------------------------------------------------------------------------


class Decision(BaseModel):
    """A structured, language-agnostic reasoning conclusion (Blueprint Sec. 5.2).

    Decisions are produced by the Review Engine, backed by an EvidenceGraph, scored
    by the Confidence Engine, and only then rendered into a ReviewComment."""

    decision_id: str
    series_id: str
    patch_id: str | None = None
    layer: ReasoningLayer
    category: str = ""
    severity: Severity = Severity.INFO
    location: str | None = None
    statement: str = Field("", description="Language-agnostic conclusion")
    rule_id: str | None = None
    pattern_id: str | None = None
    evidence_graph: EvidenceGraph | None = None
    confidence: ConfidenceScore | None = None

    def is_publishable(self) -> bool:
        """A decision may become a review comment only if it has verified evidence
        and confidence >= 0.40 (Constitution Sec. 28, 29, 30)."""
        if self.evidence_graph is None or not self.evidence_graph.has_verified_evidence():
            return False
        if self.confidence is None:
            return False
        return self.confidence.level != ConfidenceLevel.UNKNOWN


# ---------------------------------------------------------------------------
# Knowledge state / versioning
# ---------------------------------------------------------------------------


class KnowledgeStateId(BaseModel):
    """Immutable identifier of a Knowledge State snapshot (Blueprint Sec. 20.1).

    A Knowledge State bundles the EKG schema version, Generic Runtime version, all
    loaded DKP versions, and the learning-iteration counter. Required for replay
    (Constitution Sec. 38)."""

    state_id: str
    created_at: date
    ekg_schema_version: str
    runtime_version: str
    dkp_versions: dict[str, str] = Field(default_factory=dict)  # domain -> semver
    learning_iteration: int = 0


class SeriesContext(BaseModel):
    """Cross-patch accumulator for a PatchSeries (WP-9.1a; SPEC.md Sec. 12).

    Kernel patch series routinely split one change across N patches: a symbol
    declared in patch 1/N and used in patch 3/N is correct kernel practice, not
    an error. Built once, deterministically, from a PatchSeries via
    ``kri.review_engine.series_context.build_series_context`` -- a pure
    function of the series (+ target_kernel_version), so it introduces no
    Constitution Sec. 40 nondeterminism.

    Every dict is keyed by ``Patch.sequence`` (the position within the series
    at which the symbol/file/etc. was introduced or removed)."""

    series_id: str
    target_kernel_version: KernelVersion | None = None
    introduced_symbols: dict[int, set[str]] = Field(default_factory=dict)
    removed_symbols: dict[int, set[str]] = Field(default_factory=dict)
    new_files: dict[int, set[str]] = Field(default_factory=dict)
    deleted_files: dict[int, set[str]] = Field(default_factory=dict)
    new_kconfig_symbols: dict[int, set[str]] = Field(default_factory=dict)
    new_dt_compatibles: dict[int, set[str]] = Field(default_factory=dict)
    maintainers_deltas: dict[int, list[str]] = Field(default_factory=dict)
    kbuild_edits: dict[int, set[str]] = Field(default_factory=dict)


__all__ = [
    "Severity",
    "ConfidenceLevel",
    "RuleType",
    "PatchOutcome",
    "ReasoningLayer",
    "EvidenceSourceType",
    "EVIDENCE_SOURCE_PRIORITY",
    "KernelVersion",
    "VersionRange",
    "Provenance",
    "Patch",
    "PatchSeries",
    "ReviewComment",
    "Rule",
    "Evidence",
    "EvidenceGraph",
    "ConfidenceFactor",
    "ConfidenceScore",
    "Decision",
    "KnowledgeStateId",
    "SeriesContext",
]
