"""KRI Patch Manager (Blueprint Sec. 21.2).

Parse patch series from lore threads / mbox into the frozen ``PatchSeries`` model,
detect version history, correlate reviews to patches, and normalize patches.
"""

from __future__ import annotations

from .manager import PatchManagerImpl

__all__ = ["PatchManagerImpl"]
