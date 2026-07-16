"""KRI runtime module interfaces (Generic Runtime contract).

Every runtime module from Blueprint Sec. 21 is declared here as a ``typing.Protocol``
so builder agents implement against a stable, checkable contract. These interfaces are
part of the *frozen* core architecture (Constitution Sec. 32): changing a signature
requires architectural review.

Domain Isolation (Constitution Sec. 9): nothing in this module may reference a
concrete domain (a subsystem's C symbol prefix, source path, or product name).
Domain behavior enters only through the ``DomainKnowledgePackage`` protocol, whose
concrete implementations live under ``kri/packages/<domain>/`` and are discovered
via the ``kri.dkp`` entry-point group.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kri.common.models import (
    ConfidenceScore,
    Decision,
    Evidence,
    EvidenceGraph,
    KernelVersion,
    KnowledgeStateId,
    Patch,
    PatchSeries,
    ReviewComment,
    Rule,
    SeriesContext,
)

# ---------------------------------------------------------------------------
# Result value objects that cross module boundaries but are not core artifacts.
# Kept intentionally loose (Any/dict) where the shape is builder-owned; tightened
# where the data-flow contract in SPEC.md pins it.
# ---------------------------------------------------------------------------

TreeState = Any            # opaque handle to a checked-out/applied git tree
Diff = str                 # unified diff text
StaticFinding = dict[str, Any]   # normalized: {tool, file, line, category, severity, message}
GraphQuery = dict[str, Any]      # structured query (see SPEC.md KG query patterns)
GraphResult = list[dict[str, Any]]
ReviewReport = dict[str, Any]    # Review Explainability Report (Blueprint Sec. 17)
Pattern = dict[str, Any]         # extracted/validated review pattern (Blueprint Sec. 10.1)


# ===========================================================================
# 21.1 Repository Manager
# ===========================================================================


@runtime_checkable
class RepositoryManager(Protocol):
    """Clone/maintain kernel git repos; deterministic tree state for patch application."""

    def checkout(self, version: str) -> TreeState:
        """Check out the tree at an exact version/tag/branch. Returns a tree handle."""
        ...

    def apply_patch(self, series: PatchSeries) -> TreeState:
        """Apply a patch series to the current tree. Returns the applied tree, or
        raises/records a structured failure report if application fails."""
        ...

    def blame(self, file: str, line: int) -> list[dict[str, Any]]:
        """Return commit history for a specific file:line."""
        ...

    def diff(self, commit_a: str, commit_b: str) -> Diff:
        """Return the unified diff between two commits."""
        ...


# ===========================================================================
# 21.2 Patch Manager
# ===========================================================================


@runtime_checkable
class PatchManager(Protocol):
    """Parse patch series from lore threads or direct upload."""

    def parse(self, thread: Any) -> PatchSeries:
        """Parse a raw thread/mbox into a PatchSeries object."""
        ...

    def extract_versions(self, series: PatchSeries) -> list[int]:
        """Return the version history (e.g. [1, 2, 3]) present in the thread."""
        ...

    def correlate_reviews(self, series: PatchSeries) -> dict[str, list[ReviewComment]]:
        """Map each patch_id to the review comments that target it."""
        ...

    def normalize(self, patch: Patch) -> Patch:
        """Return a standardized patch representation for analysis."""
        ...


# ===========================================================================
# 21.3 Lore Manager
# ===========================================================================


@runtime_checkable
class LoreManager(Protocol):
    """Ingest and cache lore.kernel.org threads; parse into structured conversations."""

    def fetch(self, thread_id: str) -> Any:
        """Fetch (and cache) a lore thread by id. Returns a raw thread object."""
        ...

    def parse_conversation(self, thread: Any) -> list[dict[str, Any]]:
        """Parse an email thread into ordered, structured replies."""
        ...

    def extract_reviews(self, thread: Any) -> list[ReviewComment]:
        """Extract maintainer review comments (is_maintainer=True) tagged with patch refs."""
        ...

    def search(self, query: str) -> list[str]:
        """Search cached/remote lore; return matching thread ids."""
        ...


# ===========================================================================
# 21.4 Knowledge Manager
# ===========================================================================


@runtime_checkable
class KnowledgeManager(Protocol):
    """Own the Engineering Knowledge Graph and DKP loading; provide versioned queries.

    The Knowledge Manager is the ONLY module that loads DKPs. The Review Engine
    receives a loaded DKP handle; it never imports domain packages itself."""

    def load_dkp(self, domain: str) -> DomainKnowledgePackage:
        """Load a Domain Knowledge Package by domain name via the kri.dkp entry points."""
        ...

    def query_graph(self, query: GraphQuery) -> GraphResult:
        """Execute a temporal graph query (see SPEC.md KG query patterns)."""
        ...

    def get_evidence(self, decision: Decision) -> list[Evidence]:
        """Retrieve candidate evidence nodes supporting a decision."""
        ...

    def snapshot(self) -> KnowledgeStateId:
        """Snapshot the entire knowledge state; returns an immutable state id."""
        ...

    def restore(self, state_id: str) -> KnowledgeStateId:
        """Restore a previously snapshotted knowledge state for replay."""
        ...


# ===========================================================================
# 21.5 Kernel Builder
# ===========================================================================


@runtime_checkable
class KernelBuilder(Protocol):
    """Configure and build affected subsystems; capture warnings/errors."""

    def configure(self, target: str, config: dict[str, Any]) -> dict[str, Any]:
        """Produce a build configuration (defconfig + enabled options) for a target."""
        ...

    def build(self, target: str) -> dict[str, Any]:
        """Build a target; return {status, logs, ...}."""
        ...

    def get_warnings(self) -> list[str]:
        """Return compiler warnings from the last build."""
        ...

    def get_errors(self) -> list[str]:
        """Return compiler errors from the last build."""
        ...


# ===========================================================================
# 21.6 Static Analysis Manager
# ===========================================================================


@runtime_checkable
class StaticAnalysisManager(Protocol):
    """Orchestrate checkpatch/sparse/smatch/coccinelle; normalize + filter false positives."""

    def run_checkpatch(self, patch: Patch) -> list[StaticFinding]:
        ...

    def run_sparse(self, files: list[str]) -> list[StaticFinding]:
        ...

    def run_smatch(self, files: list[str]) -> list[StaticFinding]:
        ...

    def run_coccinelle(self, files: list[str], scripts: list[str]) -> list[StaticFinding]:
        ...

    def normalize(self, output: Any) -> list[StaticFinding]:
        """Normalize raw tool output into StaticFinding records."""
        ...


# ===========================================================================
# 21.7 Review Engine (Cognition Orchestrator) — CONTAINS NO DOMAIN LOGIC
# ===========================================================================


@runtime_checkable
class ReviewEngine(Protocol):
    """Orchestrate the Cognition Layer and the six-layer Reasoning Hierarchy.

    Constitutional constraint (Blueprint Sec. 21.7, Sec. 9): this module contains
    NO domain-specific logic. All domain reasoning is delegated to the DKP passed in."""

    def review(
        self,
        patch_series: PatchSeries,
        dkp: DomainKnowledgePackage | None,
        extra_plugins: list["ReasoningPlugin"] | None = None,
    ) -> list[Decision]:
        """Produce structured Decisions for a patch series using the supplied DKP.

        ``extra_plugins`` are domain-agnostic reasoning plugins (e.g. kernel
        etiquette checks) that run regardless of which/whether a DKP is loaded."""
        ...

    def explain(self, decision: Decision) -> EvidenceGraph:
        """Return the Evidence Graph justifying a decision."""
        ...

    def generate_report(self, decisions: list[Decision]) -> ReviewReport:
        """Assemble the structured Review Explainability Report."""
        ...


# ===========================================================================
# 21.8 Evidence Engine
# ===========================================================================


@runtime_checkable
class EvidenceEngine(Protocol):
    """Assemble, verify, and format evidence (Constitution Sec. 28/29 enforcement point)."""

    def gather(
        self, decision: Decision, *, series_context: SeriesContext | None = None
    ) -> EvidenceGraph:
        """Assemble an Evidence Graph for a decision from all available sources.

        ``series_context`` (WP-9.1a) is the cross-patch accumulator built once
        for the whole series; when present it is forwarded to ``verify()`` so
        missing-symbol/file/binding evidence can be resolved against it."""
        ...

    def verify(
        self, evidence: Evidence, *, series_context: SeriesContext | None = None
    ) -> Evidence:
        """Verify an evidence item is sourced, relevant, verifiable, versioned.
        Returns the item with ``verified`` and ``strength`` populated. Unverifiable
        evidence must be flagged (verified=False).

        ``series_context`` (WP-9.1a) is keyword-only with a default of
        ``None`` -- when provided and ``evidence.source_type`` is one of the
        cross-patch source types (missing symbol/file/binding), the item is
        resolved against the series before falling back to provenance-based
        verification."""
        ...

    def format(self, evidence: Evidence) -> str:
        """Render an evidence item as a human-readable citation."""
        ...


# ===========================================================================
# 21.9 Learning Engine (stochastic elements confined here — Constitution Sec. 40)
# ===========================================================================


@runtime_checkable
class LearningEngine(Protocol):
    """Execute the Learning Feedback Loop: ingest -> extract -> validate -> update."""

    def ingest(self, thread: Any) -> list[Pattern]:
        """Ingest a historical thread; return candidate patterns."""
        ...

    def validate(self, pattern: Pattern) -> dict[str, Any]:
        """Validate a pattern (multi-example, statistical significance, FP check).
        Returns a validation result including pass/fail and metrics."""
        ...

    def update_knowledge(self, pattern: Pattern) -> KnowledgeStateId:
        """Apply a validated pattern to the knowledge state; returns new state id."""
        ...

    def benchmark(self) -> dict[str, Any]:
        """Run the benchmark suite; return performance metrics."""
        ...


# ===========================================================================
# 21.10 Simulation Engine
# ===========================================================================


@runtime_checkable
class SimulationEngine(Protocol):
    """Drive the full Review Simulation Pipeline; support replay and audit."""

    def simulate(self, patch_series: PatchSeries, config: dict[str, Any]) -> ReviewReport:
        """Run the complete pipeline for a patch series; return a Review Report."""
        ...

    def replay(self, patch_series: PatchSeries, knowledge_state: str) -> ReviewReport:
        """Replay a review against a specific (immutable) knowledge state."""
        ...

    def audit(self, report: ReviewReport) -> dict[str, Any]:
        """Return the immutable audit trail for a report."""
        ...


# ===========================================================================
# Confidence Engine  (Blueprint Sec. 16) — factor model lives here
# ===========================================================================


@runtime_checkable
class ConfidenceEngine(Protocol):
    """Compute reproducible, calibrated, explainable, conservative confidence."""

    def score(self, decision: Decision, evidence_graph: EvidenceGraph) -> ConfidenceScore:
        """Compute the weighted confidence score and level for a decision.
        Same inputs + same knowledge state => same score (Constitution Sec. 31/40)."""
        ...


# ===========================================================================
# DKP interface contract — THE extension boundary (Blueprint Sec. 9.2)
# ===========================================================================


@runtime_checkable
class DomainKnowledgePackage(Protocol):
    """A pluggable Domain Knowledge Package (Blueprint Sec. 9.2).

    Concrete DKPs live under ``kri/packages/<domain>/`` and are registered in the
    ``kri.dkp`` entry-point group. The Generic Runtime interacts with a domain ONLY
    through this protocol; it never imports a package module by name.

    A DKP ships a YAML manifest (see SPEC.md "DKP Manifest Schema") describing what
    it provides/requires and its file_patterns + reasoning_plugins triggers. The
    Python entry point below exposes that data plus the reasoning plugins.
    """

    # --- identity / manifest --------------------------------------------------
    @property
    def name(self) -> str:
        """Domain name, e.g. the value registered in the kri.dkp entry-point group."""
        ...

    @property
    def version(self) -> str:
        """Semantic version of this DKP."""
        ...

    def manifest(self) -> dict[str, Any]:
        """Return the parsed manifest (package/schema/requires/file_patterns/plugins)."""
        ...

    def supports_version(self, kernel_version: KernelVersion) -> bool:
        """True if this DKP's kernel_version_range covers the given version."""
        ...

    # --- domain routing -------------------------------------------------------
    def owns_file(self, path: str) -> bool:
        """True if ``path`` matches this domain's file_patterns."""
        ...

    def build_target(self) -> str:
        """The build target (e.g. a directory) for affected files."""
        ...

    # --- knowledge accessors (domain data, returned as generic types) ---------
    def rules(self, kernel_version: KernelVersion | None = None) -> list[Rule]:
        """Return subsystem rules valid for the given kernel version."""
        ...

    def patterns(self) -> list[Pattern]:
        """Return the validated review-pattern library for this domain."""
        ...

    def reasoning_plugins(self) -> list[ReasoningPlugin]:
        """Return the domain reasoning plugins the Review Engine may invoke."""
        ...

    def seed_graph(self, knowledge_manager: KnowledgeManager) -> None:
        """Populate the Engineering Knowledge Graph with this domain's nodes/edges."""
        ...


@runtime_checkable
class ReasoningPlugin(Protocol):
    """A single domain reasoning plugin declared by a DKP (manifest ``reasoning_plugins``).

    Plugins are triggered by conditions such as ``file_touched:<pattern>`` or
    ``api_used:<pattern>``. The Review Engine evaluates triggers generically and
    invokes matching plugins; the plugin returns domain Decisions (which the Generic
    Runtime then scores and evidence-checks uniformly)."""

    @property
    def plugin_id(self) -> str:
        ...

    @property
    def trigger(self) -> str:
        """Trigger expression, e.g. "file_touched:<dir>/" or "api_used:<symbol_prefix>*"."""
        ...

    def applies(self, patch: Patch, series: PatchSeries) -> bool:
        """Cheap generic check of whether this plugin should run for the patch."""
        ...

    def evaluate(
        self,
        patch: Patch,
        series: PatchSeries,
        *,
        series_context: SeriesContext | None = None,
    ) -> list[Decision]:
        """Run domain reasoning; return Decisions (evidence/confidence filled later).

        ``series_context`` is the cross-patch accumulator built once for the
        whole series (WP-9.1a); it is keyword-only with a default of ``None``
        so existing plugins that don't accept/use it keep working unchanged."""
        ...


__all__ = [
    "RepositoryManager",
    "PatchManager",
    "LoreManager",
    "KnowledgeManager",
    "KernelBuilder",
    "StaticAnalysisManager",
    "ReviewEngine",
    "EvidenceEngine",
    "LearningEngine",
    "SimulationEngine",
    "ConfidenceEngine",
    "DomainKnowledgePackage",
    "ReasoningPlugin",
]
