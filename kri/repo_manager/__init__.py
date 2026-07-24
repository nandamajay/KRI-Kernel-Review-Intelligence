"""KRI Repository Manager (Blueprint Sec. 21.1).

Deterministic git tree operations (checkout/apply/blame/diff) over a local kernel
clone, with graceful handling of shallow clones.
"""

from __future__ import annotations

from .gate import ApplicabilityGate, ApplicabilityResult
from .manager import (
    ApplyResult,
    RepoConfig,
    RepositoryManagerImpl,
    TreeStateInfo,
    clone_or_open,
)

__all__ = [
    "ApplicabilityGate",
    "ApplicabilityResult",
    "ApplyResult",
    "RepoConfig",
    "RepositoryManagerImpl",
    "TreeStateInfo",
    "clone_or_open",
]
