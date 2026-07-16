"""Simulation Engine (Blueprint Sec. 21.10 / SPEC §8).

Full pipeline orchestration: parse -> KG lookup -> review -> evidence ->
confidence -> report. Supports replay and audit.
"""

from .engine import SimulationEngineImpl

__all__ = ["SimulationEngineImpl"]
