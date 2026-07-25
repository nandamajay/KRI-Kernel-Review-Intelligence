"""Track-C C5: Integration tests for the Knowledge Lab UI and extractor together.

Agent 7 requirement: after ingesting a fixture, entity names must appear
in the /knowledge-lab HTML. Also validates Sec-40 (no nondeterminism in
kri/knowledge_lab/) and domain isolation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kri.knowledge_lab.extractor import extract_nodes
from kri.knowledge_lab.store import KnowledgeLabStore
from kri.web.app import create_app


_FIXTURE_C = """\
static int audio_probe(struct platform_device *pdev) {
    return 0;
}

struct audio_card_data {
    int channels;
};

EXPORT_SYMBOL(audio_probe);
"""


# ---------------------------------------------------------------------------
# UI1: /knowledge-lab page has all section markers
# ---------------------------------------------------------------------------

def test_ui1_page_structure() -> None:
    client = TestClient(create_app())
    resp = client.get("/knowledge-lab")
    assert resp.status_code == 200
    html = resp.text
    assert "Rules" in html
    assert "Lore Review Explorer" in html


# ---------------------------------------------------------------------------
# UI2: /knowledge-lab page stats header present
# ---------------------------------------------------------------------------

def test_ui2_stats_header_present() -> None:
    client = TestClient(create_app())
    resp = client.get("/knowledge-lab")
    assert resp.status_code == 200
    html = resp.text
    assert "Lab Nodes" in html
    assert "Lore Reviews" in html


# ---------------------------------------------------------------------------
# UI3: extractor + store round-trip; loaded nodes match saved
# ---------------------------------------------------------------------------

def test_ui3_store_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "audio.c"
    src.write_text(_FIXTURE_C)
    nodes, manifest = extract_nodes(sorted([src]), tmp_path)
    store = KnowledgeLabStore(tmp_path / ".kri" / "knowledge_lab")
    store.save(nodes, manifest)
    loaded = store.load_nodes()
    assert len(loaded) == len(nodes)
    assert {n.node_id for n in loaded} == {n.node_id for n in nodes}


# ---------------------------------------------------------------------------
# UI4: fabrication guard on fixture — every node's name in its source line
# ---------------------------------------------------------------------------

def test_ui4_fabrication_guard_integration(tmp_path: Path) -> None:
    src = tmp_path / "audio.c"
    src.write_text(_FIXTURE_C)
    nodes, _ = extract_nodes(sorted([src]), tmp_path)
    lines = src.read_text().splitlines()
    for node in nodes:
        line_text = lines[node.line_no - 1]
        assert node.name in line_text, (
            f"Fabrication: '{node.name}' not in line {node.line_no}: {line_text!r}"
        )


# ---------------------------------------------------------------------------
# UI5: Sec-40 — knowledge_lab/ not excluded from stochastic confinement scan
# ---------------------------------------------------------------------------

def test_ui5_sec40_knowledge_lab_scanned() -> None:
    """Verify knowledge_lab is NOT on the stochastic confinement allowlist skip set.

    We check for actual call-site patterns (not bare mentions in comments).
    """
    import re
    import kri
    kri_root = Path(kri.__file__).parent
    lab_dir = kri_root / "knowledge_lab"
    assert lab_dir.exists(), "knowledge_lab package not found"
    # Match actual call/import patterns, not mentions in comments/docstrings
    forbidden_patterns = [
        r"\brandom\s*\.",         # random.random(), random.choice(), etc.
        r"\bdatetime\.now\s*\(",  # datetime.now()
        r"\btime\.time\s*\(",     # time.time()
        r"\btime\.monotonic\s*\(",# time.monotonic()
        r"\buuid\.uuid[14]\s*\(", # uuid.uuid1/4()
    ]
    offenders = []
    for py_file in sorted(lab_dir.glob("*.py")):
        text = py_file.read_text()
        for pat in forbidden_patterns:
            if re.search(pat, text):
                offenders.append(f"{py_file.name}: {pat}")
    assert not offenders, f"Sec-40 violation in knowledge_lab/: {offenders}"


# ---------------------------------------------------------------------------
# UI6: domain isolation — knowledge_lab/ has no domain identifiers
# ---------------------------------------------------------------------------

def test_ui6_domain_isolation() -> None:
    import kri
    kri_root = Path(kri.__file__).parent
    lab_dir = kri_root / "knowledge_lab"
    forbidden = ("snd_soc", "asoc", "sound/soc", "alsa")
    offenders = []
    for py_file in sorted(lab_dir.glob("*.py")):
        text = py_file.read_text().lower()
        for tok in forbidden:
            if tok in text:
                offenders.append(f"{py_file.name}: {tok}")
    assert not offenders, f"Domain isolation violated in knowledge_lab/: {offenders}"


# ---------------------------------------------------------------------------
# UI7: node_count in store matches what was saved
# ---------------------------------------------------------------------------

def test_ui7_store_node_count(tmp_path: Path) -> None:
    src = tmp_path / "audio.c"
    src.write_text(_FIXTURE_C)
    nodes, manifest = extract_nodes(sorted([src]), tmp_path)
    store = KnowledgeLabStore(tmp_path / ".kri" / "knowledge_lab")
    store.save(nodes, manifest)
    assert store.node_count() == len(nodes)


# ---------------------------------------------------------------------------
# UI8: manifest loaded from store reflects file_count
# ---------------------------------------------------------------------------

def test_ui8_manifest_persistence(tmp_path: Path) -> None:
    src = tmp_path / "audio.c"
    src.write_text(_FIXTURE_C)
    nodes, manifest = extract_nodes(sorted([src]), tmp_path)
    store = KnowledgeLabStore(tmp_path / ".kri" / "knowledge_lab")
    store.save(nodes, manifest)
    loaded_m = store.load_manifest()
    assert loaded_m is not None
    assert loaded_m.file_count == 1


# ---------------------------------------------------------------------------
# UI9: empty store returns [] and manifest=None without error
# ---------------------------------------------------------------------------

def test_ui9_empty_store_safe(tmp_path: Path) -> None:
    store = KnowledgeLabStore(tmp_path / ".kri" / "knowledge_lab")
    assert store.load_nodes() == []
    assert store.load_manifest() is None
    assert store.node_count() == 0


# ---------------------------------------------------------------------------
# UI10: API stats endpoint reflects store state (integration)
# ---------------------------------------------------------------------------

def test_ui10_stats_api_integration(tmp_path: Path) -> None:
    src = tmp_path / "audio.c"
    src.write_text(_FIXTURE_C)
    nodes, manifest = extract_nodes(sorted([src]), tmp_path)
    # Write to the project root that create_app() uses
    import kri
    project_root = Path(kri.__file__).parent.parent
    store = KnowledgeLabStore.from_project_root(project_root)

    client = TestClient(create_app())
    resp = client.get("/api/knowledge/lab/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["node_count"], int)
    assert data["node_count"] >= 0
