"""EKG schema constants (SPEC §4.2 / §4.3).

The Engineering Knowledge Graph is a *generic*, domain-agnostic property graph.
This module names the canonical node and edge types from the SPEC so the backend
and the Knowledge Manager share one vocabulary — but the backend does NOT reject
unknown types: a DKP may introduce its own as long as it supplies a version_range
and provenance. Keeping the type set open is what preserves Domain Isolation
(the runtime never hardcodes a *domain's* nodes, only the structural envelope).

No domain identifiers appear here (Constitution Sec. 9).
"""

from __future__ import annotations

from typing import Final

EKG_SCHEMA_VERSION: Final = "1.0"

# --- Node types (SPEC §4.2) ------------------------------------------------
NODE_SUBSYSTEM: Final = "Subsystem"
NODE_RULE: Final = "Rule"
NODE_PATTERN: Final = "Pattern"
NODE_API: Final = "Api"
NODE_FILE: Final = "File"
NODE_COMMIT: Final = "Commit"
NODE_PATCH: Final = "Patch"
NODE_REVIEW_COMMENT: Final = "ReviewComment"
NODE_MAINTAINER: Final = "Maintainer"
NODE_DOCUMENT: Final = "Document"
NODE_CONCEPT: Final = "Concept"
# Evidence + versioned-decision nodes round out the 11 the Cognition layer needs.
NODE_EVIDENCE: Final = "Evidence"
NODE_KERNEL_VERSION: Final = "KernelVersion"

NODE_TYPES: Final = frozenset(
    {
        NODE_SUBSYSTEM,
        NODE_RULE,
        NODE_PATTERN,
        NODE_API,
        NODE_FILE,
        NODE_COMMIT,
        NODE_PATCH,
        NODE_REVIEW_COMMENT,
        NODE_MAINTAINER,
        NODE_DOCUMENT,
        NODE_CONCEPT,
        NODE_EVIDENCE,
        NODE_KERNEL_VERSION,
    }
)

# --- Edge types (SPEC §4.3) ------------------------------------------------
EDGE_GOVERNS: Final = "GOVERNS"                # Rule -> Subsystem/Api/File
EDGE_DEFINED_IN: Final = "DEFINED_IN"          # Api -> File/Header
EDGE_MODIFIES: Final = "MODIFIES"              # Patch/Commit -> File/Api
EDGE_REVIEWS: Final = "REVIEWS"                # ReviewComment -> Patch
EDGE_AUTHORED_BY: Final = "AUTHORED_BY"        # Patch/Commit -> Maintainer
EDGE_MAINTAINS: Final = "MAINTAINS"            # Maintainer -> Subsystem
EDGE_DOCUMENTED_BY: Final = "DOCUMENTED_BY"    # Rule/Api -> Document
EDGE_EXEMPLIFIES: Final = "EXEMPLIFIES"        # Patch -> Pattern
EDGE_SUPERSEDES: Final = "SUPERSEDES"          # Node -> Node (temporal succession)
EDGE_DEPENDS_ON: Final = "DEPENDS_ON"          # Api -> Api
EDGE_VIOLATES: Final = "VIOLATES"              # Patch -> Rule
EDGE_COMPLIES_WITH: Final = "COMPLIES_WITH"    # Patch -> Rule
EDGE_SUPPORTS: Final = "SUPPORTS"              # Evidence -> Rule/Pattern/Decision

EDGE_TYPES: Final = frozenset(
    {
        EDGE_GOVERNS,
        EDGE_DEFINED_IN,
        EDGE_MODIFIES,
        EDGE_REVIEWS,
        EDGE_AUTHORED_BY,
        EDGE_MAINTAINS,
        EDGE_DOCUMENTED_BY,
        EDGE_EXEMPLIFIES,
        EDGE_SUPERSEDES,
        EDGE_DEPENDS_ON,
        EDGE_VIOLATES,
        EDGE_COMPLIES_WITH,
        EDGE_SUPPORTS,
    }
)

__all__ = [
    "EKG_SCHEMA_VERSION",
    "NODE_TYPES",
    "EDGE_TYPES",
    "NODE_SUBSYSTEM",
    "NODE_RULE",
    "NODE_PATTERN",
    "NODE_API",
    "NODE_FILE",
    "NODE_COMMIT",
    "NODE_PATCH",
    "NODE_REVIEW_COMMENT",
    "NODE_MAINTAINER",
    "NODE_DOCUMENT",
    "NODE_CONCEPT",
    "NODE_EVIDENCE",
    "NODE_KERNEL_VERSION",
    "EDGE_GOVERNS",
    "EDGE_DEFINED_IN",
    "EDGE_MODIFIES",
    "EDGE_REVIEWS",
    "EDGE_AUTHORED_BY",
    "EDGE_MAINTAINS",
    "EDGE_DOCUMENTED_BY",
    "EDGE_EXEMPLIFIES",
    "EDGE_SUPERSEDES",
    "EDGE_DEPENDS_ON",
    "EDGE_VIOLATES",
    "EDGE_COMPLIES_WITH",
    "EDGE_SUPPORTS",
]
