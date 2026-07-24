"""KRI Knowledge Manager (Blueprint Sec. 21.4).

Owns the Engineering Knowledge Graph and loads Domain Knowledge Packages via the
``kri.dkp`` entry-point group — never by direct import (Domain Isolation).
"""

from __future__ import annotations

from .manager import (
    DKP_ENTRY_POINT_GROUP,
    RUNTIME_VERSION,
    DkpLoadError,
    KnowledgeManagerImpl,
)
from .persistence import load_snapshot, save_snapshot

__all__ = [
    "KnowledgeManagerImpl",
    "DkpLoadError",
    "DKP_ENTRY_POINT_GROUP",
    "RUNTIME_VERSION",
    "save_snapshot",
    "load_snapshot",
]
