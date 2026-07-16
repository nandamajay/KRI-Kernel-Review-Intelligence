"""KRI Process Rules Manager (Generic Runtime, not a DKP).

Domain-agnostic upstream kernel process/etiquette checks (Signed-off-by,
Fixes: tag format, changelog placement) that apply to every patch regardless
of subsystem. See :mod:`kri.process_rules.manager`.
"""

from __future__ import annotations

from .manager import (
    ProcessEtiquettePlugin,
    ProcessRulesConfig,
    ProcessRulesManagerImpl,
)

__all__ = [
    "ProcessRulesConfig",
    "ProcessRulesManagerImpl",
    "ProcessEtiquettePlugin",
]
