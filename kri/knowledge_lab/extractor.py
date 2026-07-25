"""Track-C C2: Knowledge Lab — domain-agnostic source file extractor.

Extracts function definitions, struct declarations, and EXPORT_SYMBOL markers
from C source files using regex. All file traversal uses sorted() for
determinism (Sec-40). Domain-specific patterns must be passed in via
`extra_patterns`; they must not be hardcoded here (Sec-9).

Cache key: git rev-parse HEAD via subprocess — never mtime or time-based functions.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from kri.knowledge_lab.models import ExtractionManifest, LabEdge, LabNode

logger = logging.getLogger(__name__)

# Generic C-source extraction patterns (no domain-specific identifiers)
_BASE_PATTERNS: list[tuple[str, str]] = [
    # static [type] function_name(
    (r"^(?:static\s+)?(?:int|void|bool|long|unsigned\s+\w+|\w+\s*\*)\s+(\w+)\s*\(", "function"),
    # struct name {
    (r"^struct\s+(\w+)\s*\{", "struct"),
    # EXPORT_SYMBOL[_GPL](name);
    (r"^EXPORT_SYMBOL(?:_GPL)?\s*\(\s*(\w+)\s*\)", "export_symbol"),
]


def _get_git_sha(repo_root: Path) -> str:
    """Return HEAD commit SHA for cache key. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _infer_subsystem(file_path: str) -> str:
    """Infer a short subsystem label from the file path."""
    parts = Path(file_path).parts
    for i, part in enumerate(parts):
        if part in ("sound", "drivers", "net", "fs", "arch", "kernel"):
            if i + 1 < len(parts):
                return parts[i + 1]
            return part
    return ""


def extract_nodes_from_file(
    source_file: Path,
    repo_root: Path,
    extra_patterns: Optional[list[tuple[str, str]]] = None,
) -> list[LabNode]:
    """Extract LabNodes from a single C source file.

    `extra_patterns` allows domain packages to inject additional regexes without
    modifying this generic module. Each entry is (pattern, node_type).
    """
    patterns = _BASE_PATTERNS + (extra_patterns or [])
    rel_path = str(source_file.relative_to(repo_root)) if source_file.is_absolute() else str(source_file)
    subsystem = _infer_subsystem(rel_path)
    nodes: list[LabNode] = []

    try:
        lines = source_file.read_text(errors="replace").splitlines()
    except Exception as exc:
        logger.warning("knowledge_lab: failed to read %s: %s", source_file, exc)
        return []

    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        for pattern, node_type in patterns:
            m = re.match(pattern, stripped)
            if m:
                name = m.group(1)
                exported = node_type == "export_symbol"
                node_id = LabNode.make_node_id(node_type, rel_path, line_no, name)
                nodes.append(
                    LabNode(
                        node_id=node_id,
                        node_type=node_type,
                        name=name,
                        file_path=rel_path,
                        line_no=line_no,
                        subsystem=subsystem,
                        exported=exported,
                        signature=stripped[:120],
                    )
                )
                break  # first match wins per line

    return nodes


def extract_nodes(
    file_list: list[Path],
    repo_root: Path,
    extra_patterns: Optional[list[tuple[str, str]]] = None,
) -> tuple[list[LabNode], ExtractionManifest]:
    """Extract LabNodes from an ordered list of source files.

    Files are processed in their given order (caller must sort for determinism).
    Returns (nodes, manifest).
    """
    git_sha = _get_git_sha(repo_root)
    all_nodes: list[LabNode] = []
    parse_errors = 0
    subsystem_counts: dict[str, int] = {}

    for src_file in file_list:
        try:
            file_nodes = extract_nodes_from_file(src_file, repo_root, extra_patterns)
            all_nodes.extend(file_nodes)
            for n in file_nodes:
                if n.subsystem:
                    subsystem_counts[n.subsystem] = subsystem_counts.get(n.subsystem, 0) + 1
        except Exception as exc:
            logger.warning("knowledge_lab: error processing %s: %s", src_file, exc)
            parse_errors += 1

    manifest = ExtractionManifest(
        source_git_sha=git_sha,
        file_count=len(file_list),
        node_count=len(all_nodes),
        edge_count=0,
        parse_error_count=parse_errors,
        subsystem_counts=subsystem_counts,
    )

    logger.info(
        "knowledge_lab: extracted %d nodes from %d files (errors=%d)",
        len(all_nodes),
        len(file_list),
        parse_errors,
    )
    return all_nodes, manifest


def collect_files(
    kernel_path: Path,
    subdirs: list[str],
) -> list[Path]:
    """Collect sorted .c files from specified subdirectories under kernel_path.

    All traversal uses sorted() for Sec-40 determinism.
    """
    collected: list[Path] = []
    for subdir in subdirs:
        target = kernel_path / subdir
        if not target.exists():
            logger.debug("knowledge_lab: subdir not found: %s", target)
            continue
        c_files = sorted(target.glob("*.c"))
        collected.extend(c_files)
    return collected


__all__ = ["extract_nodes", "extract_nodes_from_file", "collect_files"]
