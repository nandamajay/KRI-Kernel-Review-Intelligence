"""Track-C C2: Knowledge Lab — domain-agnostic node/edge models.

All IDs are derived via hashlib.sha256 (Sec-40 compliant; no uuid/random).
This module contains NO domain-specific identifiers (Sec-9 domain isolation).
Domain-specific extraction patterns are provided by kri/packages/<domain>/ at runtime.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from pydantic import BaseModel, Field


class LabNode(BaseModel):
    """A single extracted symbol from a source file."""

    node_id: str
    node_type: str  # "function", "struct", "export_symbol", "enum"
    name: str
    file_path: str
    line_no: int
    subsystem: str = ""
    exported: bool = False
    signature: str = ""

    @staticmethod
    def make_node_id(node_type: str, file_path: str, line_no: int, name: str) -> str:
        """Deterministic 16-char hex ID (Sec-40)."""
        raw = f"{node_type}:{file_path}:{line_no}:{name}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class LabEdge(BaseModel):
    """A directed relationship between two LabNodes."""

    src: str
    dst: str
    edge_type: str  # "calls", "implements", "exports"
    file_path: str
    line_no: int


class ExtractionManifest(BaseModel):
    """Metadata about a completed extraction run."""

    source_git_sha: str = ""
    file_count: int = 0
    node_count: int = 0
    edge_count: int = 0
    parse_error_count: int = 0
    subsystem_counts: dict[str, int] = Field(default_factory=dict)


__all__ = ["LabNode", "LabEdge", "ExtractionManifest"]
