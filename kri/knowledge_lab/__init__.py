"""Track-C C2: kri/knowledge_lab — domain-agnostic source knowledge extraction.

This package provides:
  - LabNode / LabEdge / ExtractionManifest models
  - extract_nodes() / collect_files() — regex-based C source extractor
  - KnowledgeLabStore — JSONL persistence layer

Domain-specific extraction patterns (e.g. DAPM macros) are injected
by kri/packages/<domain>/ at runtime via the `extra_patterns` argument.
This package itself must contain NO domain-specific identifiers (Sec-9).
"""

from kri.knowledge_lab.extractor import collect_files, extract_nodes, extract_nodes_from_file
from kri.knowledge_lab.models import ExtractionManifest, LabEdge, LabNode
from kri.knowledge_lab.store import KnowledgeLabStore

__all__ = [
    "LabNode",
    "LabEdge",
    "ExtractionManifest",
    "extract_nodes",
    "extract_nodes_from_file",
    "collect_files",
    "KnowledgeLabStore",
]
