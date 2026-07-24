"""WP4-H tests: EKG JSONL snapshot persistence.

Tests:
  H1 - save_snapshot creates file with valid JSON
  H2 - load_snapshot restores graph state
  H3 - atomic write: .tmp file used during save, not present after
  H4 - load_snapshot returns False when file absent (graceful degradation)
  H5 - empty graph persists and round-trips correctly
  H6 - save_snapshot + load_snapshot is idempotent (two saves, two loads equal)
  H7 - load_snapshot returns False on corrupt JSON (graceful degradation)
  H8 - save_snapshot exported from knowledge_manager __init__
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kri.knowledge.graph import KnowledgeGraph
from kri.knowledge_manager import KnowledgeManagerImpl, load_snapshot, save_snapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_km_with_nodes(node_ids: list[str]) -> KnowledgeManagerImpl:
    """Create a KnowledgeManagerImpl with pre-seeded nodes."""
    km = KnowledgeManagerImpl()
    for nid in node_ids:
        km.graph._g.add_node(nid, node_type="test", properties={},
                             version_range=None, provenance=None)
    return km


# ---------------------------------------------------------------------------
# H1 - save_snapshot creates file with valid JSON
# ---------------------------------------------------------------------------


def test_H1_save_creates_valid_json(tmp_path):
    """save_snapshot writes a file that parses as valid JSON."""
    km = _make_km_with_nodes(["n1", "n2"])
    snap_path = tmp_path / "ekg_snapshot.jsonl"
    save_snapshot(km, snap_path)

    assert snap_path.exists()
    text = snap_path.read_text(encoding="utf-8").strip()
    parsed = json.loads(text.splitlines()[0])
    assert "nodes" in parsed
    assert "edges" in parsed
    assert "schema_version" in parsed


# ---------------------------------------------------------------------------
# H2 - load_snapshot restores graph state
# ---------------------------------------------------------------------------


def test_H2_load_restores_graph_state(tmp_path):
    """load_snapshot restores previously saved nodes."""
    km_orig = _make_km_with_nodes(["kernel/mm/core.c", "kernel/fs/io.c"])
    snap_path = tmp_path / "ekg_snapshot.jsonl"
    save_snapshot(km_orig, snap_path)

    km_new = KnowledgeManagerImpl()
    result = load_snapshot(km_new, snap_path)

    assert result is True
    assert set(km_new.graph._g.nodes) == {"kernel/mm/core.c", "kernel/fs/io.c"}


# ---------------------------------------------------------------------------
# H3 - atomic write: .tmp gone after save
# ---------------------------------------------------------------------------


def test_H3_atomic_write_no_tmp_after_save(tmp_path):
    """After save_snapshot completes, no .tmp file should remain."""
    km = _make_km_with_nodes(["n1"])
    snap_path = tmp_path / "ekg_snapshot.jsonl"
    save_snapshot(km, snap_path)

    tmp_file = Path(str(snap_path) + ".tmp")
    assert not tmp_file.exists(), ".tmp file must not exist after successful save"
    assert snap_path.exists()


# ---------------------------------------------------------------------------
# H4 - load returns False when file absent
# ---------------------------------------------------------------------------


def test_H4_load_returns_false_when_absent(tmp_path):
    """load_snapshot returns False (not raise) when file does not exist."""
    km = KnowledgeManagerImpl()
    result = load_snapshot(km, tmp_path / "no_such_file.jsonl")
    assert result is False
    # graph is untouched (still empty)
    assert len(km.graph._g.nodes) == 0


# ---------------------------------------------------------------------------
# H5 - empty graph round-trips correctly
# ---------------------------------------------------------------------------


def test_H5_empty_graph_round_trips(tmp_path):
    """save+load of an empty KnowledgeManager is idempotent."""
    km_empty = KnowledgeManagerImpl()
    snap_path = tmp_path / "empty.jsonl"
    save_snapshot(km_empty, snap_path)

    km_restored = KnowledgeManagerImpl()
    result = load_snapshot(km_restored, snap_path)

    assert result is True
    assert len(km_restored.graph._g.nodes) == 0
    assert len(km_restored.graph._g.edges) == 0


# ---------------------------------------------------------------------------
# H6 - two save+load cycles are equal
# ---------------------------------------------------------------------------


def test_H6_double_save_load_idempotent(tmp_path):
    """Two save+load cycles produce equal graph content hashes."""
    km = _make_km_with_nodes(["a", "b", "c"])
    snap_path = tmp_path / "ekg.jsonl"

    save_snapshot(km, snap_path)
    km2 = KnowledgeManagerImpl()
    load_snapshot(km2, snap_path)
    hash1 = km2.graph.content_hash()

    save_snapshot(km2, snap_path)
    km3 = KnowledgeManagerImpl()
    load_snapshot(km3, snap_path)
    hash2 = km3.graph.content_hash()

    assert hash1 == hash2, "content_hash must be equal across two save/load cycles"


# ---------------------------------------------------------------------------
# H7 - load returns False on corrupt JSON
# ---------------------------------------------------------------------------


def test_H7_load_returns_false_on_corrupt_json(tmp_path):
    """load_snapshot returns False (not raise) on malformed JSON."""
    snap_path = tmp_path / "corrupt.jsonl"
    snap_path.write_text("not valid json\n", encoding="utf-8")

    km = KnowledgeManagerImpl()
    result = load_snapshot(km, snap_path)
    assert result is False


# ---------------------------------------------------------------------------
# H8 - exported from knowledge_manager __init__
# ---------------------------------------------------------------------------


def test_H8_exported_from_knowledge_manager_init():
    """save_snapshot and load_snapshot must be importable from kri.knowledge_manager."""
    from kri.knowledge_manager import load_snapshot as ls, save_snapshot as ss
    assert callable(ss)
    assert callable(ls)


# ---------------------------------------------------------------------------
# H9 - load returns False on OSError (e.g. permission denied)
# ---------------------------------------------------------------------------


def test_H9_load_returns_false_on_oserror(tmp_path):
    """load_snapshot returns False (not raise) on OSError (e.g. PermissionError)."""
    from unittest.mock import patch

    snap_path = tmp_path / "ekg.jsonl"
    snap_path.write_text("{}\n", encoding="utf-8")  # valid file so it exists

    km = KnowledgeManagerImpl()
    with patch.object(type(snap_path), "read_text", side_effect=PermissionError("denied")):
        result = load_snapshot(km, snap_path)
    assert result is False
