"""Track-C C5: Tests for kri/knowledge_lab/extractor.py.

Key property: fabrication guard — every extracted node must have its
symbol_name present in the actual source line at the recorded line_no.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kri.knowledge_lab.extractor import collect_files, extract_nodes, extract_nodes_from_file
from kri.knowledge_lab.models import LabNode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SIMPLE_C = """\
/* simple test source */
static int my_func(int x) {
    return x;
}

struct my_struct {
    int field;
};

EXPORT_SYMBOL(my_func);
"""

_EMPTY_C = "/* nothing here */\n"


def _write_fixture(tmp: Path, name: str, content: str) -> Path:
    p = tmp / name
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# E1: basic extraction produces expected nodes
# ---------------------------------------------------------------------------

def test_e1_basic_extraction(tmp_path: Path) -> None:
    src = _write_fixture(tmp_path, "mod.c", _SIMPLE_C)
    nodes, manifest = extract_nodes(sorted([src]), tmp_path)
    names = {n.name for n in nodes}
    assert "my_func" in names
    assert "my_struct" in names
    assert manifest.node_count == len(nodes)
    assert manifest.file_count == 1


# ---------------------------------------------------------------------------
# E2: fabrication guard — symbol_name present at recorded line
# ---------------------------------------------------------------------------

def test_e2_fabrication_guard(tmp_path: Path) -> None:
    src = _write_fixture(tmp_path, "mod.c", _SIMPLE_C)
    nodes, _ = extract_nodes(sorted([src]), tmp_path)
    lines = src.read_text().splitlines()
    for node in nodes:
        # The node's name must appear in the source line at line_no.
        line_text = lines[node.line_no - 1]
        assert node.name in line_text, (
            f"Fabrication: node '{node.name}' not found at {node.file_path}:{node.line_no} "
            f"(line: {line_text!r})"
        )


# ---------------------------------------------------------------------------
# E3: determinism — same file produces same node IDs across two calls
# ---------------------------------------------------------------------------

def test_e3_determinism(tmp_path: Path) -> None:
    src = _write_fixture(tmp_path, "mod.c", _SIMPLE_C)
    nodes_a, _ = extract_nodes(sorted([src]), tmp_path)
    nodes_b, _ = extract_nodes(sorted([src]), tmp_path)
    ids_a = sorted(n.node_id for n in nodes_a)
    ids_b = sorted(n.node_id for n in nodes_b)
    assert ids_a == ids_b


# ---------------------------------------------------------------------------
# E4: node_id is 16-char hex (Sec-40)
# ---------------------------------------------------------------------------

def test_e4_node_id_format(tmp_path: Path) -> None:
    src = _write_fixture(tmp_path, "mod.c", _SIMPLE_C)
    nodes, _ = extract_nodes(sorted([src]), tmp_path)
    assert nodes, "expected at least one node"
    for node in nodes:
        assert len(node.node_id) == 16
        assert all(c in "0123456789abcdef" for c in node.node_id)


# ---------------------------------------------------------------------------
# E5: empty file produces 0 nodes (no fabrication from blank input)
# ---------------------------------------------------------------------------

def test_e5_empty_file_no_nodes(tmp_path: Path) -> None:
    src = _write_fixture(tmp_path, "empty.c", _EMPTY_C)
    nodes, manifest = extract_nodes(sorted([src]), tmp_path)
    assert nodes == []
    assert manifest.node_count == 0


# ---------------------------------------------------------------------------
# E6: EXPORT_SYMBOL nodes have exported=True
# ---------------------------------------------------------------------------

def test_e6_export_symbol_flag(tmp_path: Path) -> None:
    src = _write_fixture(tmp_path, "mod.c", _SIMPLE_C)
    nodes, _ = extract_nodes(sorted([src]), tmp_path)
    exported = [n for n in nodes if n.node_type == "export_symbol"]
    assert exported, "expected at least one export_symbol node"
    assert all(n.exported for n in exported)


# ---------------------------------------------------------------------------
# E7: extra_patterns are injected and produce extra nodes
# ---------------------------------------------------------------------------

def test_e7_extra_patterns(tmp_path: Path) -> None:
    content = "SND_SOC_DAPM_MUX(\"Playback Mux\", SND_SOC_NOPM, 0, 0, &mux_ctrl);\n"
    src = _write_fixture(tmp_path, "widget.c", content)
    extra = [(r'^SND_SOC_DAPM_\w+\s*\("([^"]+)"', "dapm_widget")]
    nodes, _ = extract_nodes(sorted([src]), tmp_path, extra_patterns=extra)
    widget_names = [n.name for n in nodes if n.node_type == "dapm_widget"]
    assert "Playback Mux" in widget_names


# ---------------------------------------------------------------------------
# E8: collect_files returns sorted list
# ---------------------------------------------------------------------------

def test_e8_collect_files_sorted(tmp_path: Path) -> None:
    sub = tmp_path / "sound" / "soc"
    sub.mkdir(parents=True)
    (sub / "b.c").write_text("")
    (sub / "a.c").write_text("")
    (sub / "c.c").write_text("")
    files = collect_files(tmp_path, ["sound/soc"])
    names = [f.name for f in files]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# E9: missing subdir is silently skipped (no exception)
# ---------------------------------------------------------------------------

def test_e9_missing_subdir_silent(tmp_path: Path) -> None:
    files = collect_files(tmp_path, ["nonexistent/path"])
    assert files == []


# ---------------------------------------------------------------------------
# E10: manifest reflects file_count and parse_error_count
# ---------------------------------------------------------------------------

def test_e10_manifest_metadata(tmp_path: Path) -> None:
    a = _write_fixture(tmp_path, "a.c", _SIMPLE_C)
    b = _write_fixture(tmp_path, "b.c", _EMPTY_C)
    _, manifest = extract_nodes(sorted([a, b]), tmp_path)
    assert manifest.file_count == 2
    assert manifest.parse_error_count == 0
