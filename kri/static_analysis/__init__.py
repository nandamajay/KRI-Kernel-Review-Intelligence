"""KRI Static Analysis Manager (Blueprint Sec. 21.6).

Real checkpatch runner + gracefully-degraded sparse/smatch/coccinelle stubs,
normalized to ``StaticFinding`` records.
"""

from __future__ import annotations

from .evidence import finding_to_evidence, findings_to_evidence
from .manager import StaticAnalysisConfig, StaticAnalysisManagerImpl

__all__ = [
    "StaticAnalysisManagerImpl",
    "StaticAnalysisConfig",
    "finding_to_evidence",
    "findings_to_evidence",
]
