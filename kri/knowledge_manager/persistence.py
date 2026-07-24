"""WP4-H: EKG JSONL snapshot persistence (Blueprint Sec. 21.4 / SPEC §8).

Atomic write (write to .tmp → os.replace) so a crash mid-write never leaves
a partial file.  JSONL format: one JSON object per line (schema_version,
nodes, edges).  load_snapshot() degrades gracefully — FileNotFoundError and
decode errors are logged at WARNING level and return False.

Constitution Sec. 40 safety: no datetime.now / uuid / random / time.* here.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kri.knowledge_manager.manager import KnowledgeManagerImpl

from kri.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)


def save_snapshot(km: "KnowledgeManagerImpl", path: Path | str) -> None:
    """Atomically persist the current EKG state to *path*.

    Writes the graph's canonical dict as a single JSON line to a .tmp
    file, then renames it over *path* so the write is atomic.  The parent
    directory must already exist (we do not create it silently).
    """
    path = Path(path)
    graph_dict = km.graph.to_dict()
    line = json.dumps(graph_dict, sort_keys=True, separators=(",", ":"))
    tmp_path = Path(str(path) + ".tmp")
    tmp_path.write_text(line + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
    logger.debug("EKG snapshot saved: %s (%d nodes)", path, len(graph_dict.get("nodes", [])))


def load_snapshot(km: "KnowledgeManagerImpl", path: Path | str) -> bool:
    """Load a persisted EKG snapshot into *km*, replacing its current graph.

    Returns True on success, False if the file is absent or unreadable.
    Errors beyond FileNotFoundError are logged at WARNING level and return
    False — never raise, so startup degradation is always graceful.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("EKG snapshot unreadable at %s: %s", path, exc)
        return False
    try:
        graph_dict = json.loads(text.splitlines()[0])
        km._graph = KnowledgeGraph.from_dict(graph_dict)
    except Exception as exc:  # noqa: BLE001
        logger.warning("EKG snapshot decode failed for %s: %s", path, exc)
        return False
    logger.debug("EKG snapshot loaded: %s (%d nodes)", path, len(graph_dict.get("nodes", [])))
    return True
