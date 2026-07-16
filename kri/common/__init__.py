"""KRI common: domain-agnostic core types and runtime interfaces.

Re-exports the frozen core contracts (Constitution Sec. 32) so builder agents can
``from kri.common import Decision, ReviewEngine`` without reaching into submodules.
"""

from kri.common import interfaces, models
from kri.common.interfaces import (
    ConfidenceEngine,
    DomainKnowledgePackage,
    EvidenceEngine,
    KernelBuilder,
    KnowledgeManager,
    LearningEngine,
    LoreManager,
    PatchManager,
    ReasoningPlugin,
    RepositoryManager,
    ReviewEngine,
    SimulationEngine,
    StaticAnalysisManager,
)
from kri.common.models import (
    EVIDENCE_SOURCE_PRIORITY,
    ConfidenceFactor,
    ConfidenceLevel,
    ConfidenceScore,
    Decision,
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    KernelVersion,
    KnowledgeStateId,
    Patch,
    PatchOutcome,
    PatchSeries,
    Provenance,
    ReasoningLayer,
    ReviewComment,
    Rule,
    RuleType,
    Severity,
    VersionRange,
)

__all__ = [
    "interfaces",
    "models",
    # interfaces
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
    # models
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
]
