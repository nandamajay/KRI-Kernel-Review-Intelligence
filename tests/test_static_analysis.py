"""Tests for the Static Analysis Manager (checkpatch real; others degraded)."""

from __future__ import annotations

import shutil

import pytest

from kri.common.models import Patch
from kri.static_analysis import StaticAnalysisConfig, StaticAnalysisManagerImpl

BAD_DIFF = (
    "diff --git a/drivers/foo/bar.c b/drivers/foo/bar.c\n"
    "index 1234567..89abcde 100644\n"
    "--- a/drivers/foo/bar.c\n"
    "+++ b/drivers/foo/bar.c\n"
    "@@ -1,3 +1,6 @@\n"
    " int foo(void)\n"
    " {\n"
    "+\tint x=1;\n"
    "+\tif(x)\n"
    "+\t\tprintk(\"hello\\n\");\n"
    " \treturn 0;\n"
    " }\n"
)


@pytest.fixture
def sam(kernel_path):
    return StaticAnalysisManagerImpl(StaticAnalysisConfig(repo_path=kernel_path))


def test_checkpatch_finds_and_normalizes_errors(sam: StaticAnalysisManagerImpl) -> None:
    patch = Patch(patch_id="p1", subject="bad style", diff=BAD_DIFF)
    findings = sam.run_checkpatch(patch)
    assert findings, "expected checkpatch findings on a deliberately bad diff"
    # normalized shape
    for f in findings:
        assert set(f.keys()) >= {"tool", "file", "line", "category", "severity", "message"}
        assert f["tool"] == "checkpatch"
        assert f["severity"] in ("blocker", "warning", "info")
    # at least one blocker (ERROR: spaces required around '=')
    assert any(f["severity"] == "blocker" for f in findings)
    # findings are sorted deterministically
    keys = [(f["file"] or "", f["line"], f["category"], f["message"]) for f in findings]
    assert keys == sorted(keys)


def test_checkpatch_deterministic(sam: StaticAnalysisManagerImpl) -> None:
    patch = Patch(patch_id="p1", subject="bad", diff=BAD_DIFF)
    assert sam.run_checkpatch(patch) == sam.run_checkpatch(patch)


def test_checkpatch_empty_diff(sam: StaticAnalysisManagerImpl) -> None:
    assert sam.run_checkpatch(Patch(patch_id="p", subject="empty", diff="")) == []


def test_checkpatch_missing_tool_degrades(tmp_path) -> None:
    sam = StaticAnalysisManagerImpl(StaticAnalysisConfig(
        repo_path=tmp_path, checkpatch_path=tmp_path / "nope.pl"))
    findings = sam.run_checkpatch(Patch(patch_id="p", subject="x", diff=BAD_DIFF))
    assert len(findings) == 1
    assert findings[0]["degraded"] is True
    assert findings[0]["category"] == "tool_unavailable"


def test_sparse_degrades_gracefully(kernel_path) -> None:
    sam = StaticAnalysisManagerImpl(StaticAnalysisConfig(repo_path=kernel_path))
    findings = sam.run_sparse(["sound/soc/soc-core.c"])
    assert len(findings) == 1
    assert findings[0]["tool"] == "sparse"
    assert findings[0]["degraded"] is True
    if shutil.which("sparse") is None:
        assert "not installed" in findings[0]["message"]


def test_smatch_and_coccinelle_degrade(kernel_path) -> None:
    sam = StaticAnalysisManagerImpl(StaticAnalysisConfig(repo_path=kernel_path))
    assert sam.run_smatch(["x.c"])[0]["degraded"] is True
    assert sam.run_coccinelle(["x.c"], ["s.cocci"])[0]["degraded"] is True


def test_normalize_backfills_defaults(kernel_path) -> None:
    sam = StaticAnalysisManagerImpl(StaticAnalysisConfig(repo_path=kernel_path))
    out = sam.normalize([{"tool": "x", "message": "m"}])
    assert out[0]["file"] is None
    assert out[0]["line"] == 0
    assert out[0]["severity"] == "info"


def test_normalize_parses_raw_checkpatch_string(kernel_path) -> None:
    sam = StaticAnalysisManagerImpl(StaticAnalysisConfig(repo_path=kernel_path))
    raw = (
        "ERROR:SPACING: spaces required around that '='\n"
        "#8: FILE: drivers/foo/bar.c:3:\n"
        "+\tint x=1;\n"
    )
    out = sam.normalize(raw)
    assert len(out) == 1
    assert out[0]["severity"] == "blocker"
    assert out[0]["file"] == "drivers/foo/bar.c"
    assert out[0]["line"] == 3
    assert out[0]["category"] == "spacing"
