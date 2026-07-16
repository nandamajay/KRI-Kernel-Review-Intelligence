"""KRI Engineering Knowledge Graph (EKG) — SPEC §4.

Generic, temporal, domain-agnostic property graph on a NetworkX backend. This is
the only package that imports ``networkx`` (the SPEC §4.6 migration boundary).
"""

from __future__ import annotations

from . import schema
from .graph import KnowledgeGraph
from .version import (
    coerce_version,
    make_range,
    parse_kernel_version,
    range_contains,
)

__all__ = [
    "KnowledgeGraph",
    "schema",
    "parse_kernel_version",
    "coerce_version",
    "make_range",
    "range_contains",
]
