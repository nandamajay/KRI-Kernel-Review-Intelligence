"""Track-C C2: Knowledge Lab — JSONL-backed node store.

Persists extracted LabNodes and the ExtractionManifest to disk under
.kri/knowledge_lab/. All persistence is append-safe; existing file is
replaced entirely on a full re-extraction.

No nondeterminism outside kri/learning/ (Sec-40).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from kri.knowledge_lab.models import ExtractionManifest, LabNode

logger = logging.getLogger(__name__)

_DEFAULT_SUBDIR = ".kri/knowledge_lab"
_NODES_FILE = "lab_nodes.jsonl"
_MANIFEST_FILE = "manifest.json"


class KnowledgeLabStore:
    """Persist and query LabNodes extracted from the source tree."""

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._nodes_path = base_dir / _NODES_FILE
        self._manifest_path = base_dir / _MANIFEST_FILE

    def save(self, nodes: list[LabNode], manifest: ExtractionManifest) -> None:
        """Write nodes and manifest to disk, replacing any previous extraction."""
        self._base.mkdir(parents=True, exist_ok=True)
        with self._nodes_path.open("w") as fh:
            for node in nodes:
                fh.write(node.model_dump_json() + "\n")
        self._manifest_path.write_text(manifest.model_dump_json(indent=2))
        logger.info(
            "knowledge_lab store: saved %d nodes to %s", len(nodes), self._nodes_path
        )

    def load_nodes(self) -> list[LabNode]:
        """Load all LabNodes from the JSONL file. Returns [] if file absent."""
        if not self._nodes_path.exists():
            return []
        nodes: list[LabNode] = []
        for line in self._nodes_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    nodes.append(LabNode.model_validate_json(line))
                except Exception as exc:
                    logger.warning("knowledge_lab store: bad node line: %s", exc)
        return nodes

    def load_manifest(self) -> Optional[ExtractionManifest]:
        """Load ExtractionManifest. Returns None if absent."""
        if not self._manifest_path.exists():
            return None
        try:
            return ExtractionManifest.model_validate_json(
                self._manifest_path.read_text()
            )
        except Exception as exc:
            logger.warning("knowledge_lab store: bad manifest: %s", exc)
            return None

    def node_count(self) -> int:
        """Number of nodes currently stored."""
        if not self._nodes_path.exists():
            return 0
        return sum(1 for line in self._nodes_path.read_text().splitlines() if line.strip())

    @classmethod
    def from_project_root(cls, project_root: Path) -> "KnowledgeLabStore":
        return cls(project_root / _DEFAULT_SUBDIR)


__all__ = ["KnowledgeLabStore"]
