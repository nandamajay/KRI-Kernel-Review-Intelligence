"""Review Engine (Blueprint Sec. 21.7 / SPEC §7).

Cognition Orchestrator — domain-agnostic reasoning pipeline. All domain logic
is delegated to DKP reasoning_plugins.
"""

from .engine import ReviewEngineImpl

__all__ = ["ReviewEngineImpl"]
