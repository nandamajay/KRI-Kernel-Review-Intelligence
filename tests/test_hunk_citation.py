"""WP-9.2a Sub-commit 1: HunkCitation model + extract_hunk_citation tests.

Tests the pure function ``extract_hunk_citation`` and the ``HunkCitation`` model.
Each test is designed to FAIL against pre-change code and PASS after.
"""

from __future__ import annotations

from kri.common.diff_utils import extract_hunk_citation
from kri.common.models import HunkCitation, Patch


def _make_patch(diff: str, patch_id: str = "p-1") -> Patch:
    return Patch(
        patch_id=patch_id,
        subject="test",
        sequence=1,
        series_total=1,
        diff=diff,
        files_changed=["sound/soc/codecs/test.c"],
    )


SINGLE_FILE_DIFF = """\
diff --git a/sound/soc/codecs/test.c b/sound/soc/codecs/test.c
index aaa..bbb 100644
--- a/sound/soc/codecs/test.c
+++ b/sound/soc/codecs/test.c
@@ -10,0 +10,7 @@
+static int test_probe(struct platform_device *pdev)
+{
+\tstruct test_priv *priv;
+\tpriv = devm_kzalloc(&pdev->dev, sizeof(*priv), GFP_KERNEL);
+\tif (!priv)
+\t\treturn -ENOMEM;
+\treturn 0;
+}
"""

MULTI_FILE_DIFF = """\
diff --git a/sound/soc/codecs/Kconfig b/sound/soc/codecs/Kconfig
index aaa..bbb 100644
--- a/sound/soc/codecs/Kconfig
+++ b/sound/soc/codecs/Kconfig
@@ -1,0 +1,3 @@
+config SND_SOC_TEST
+\ttristate "Test codec"
+\tdepends on I2C
diff --git a/sound/soc/codecs/test.c b/sound/soc/codecs/test.c
index ccc..ddd 100644
--- a/sound/soc/codecs/test.c
+++ b/sound/soc/codecs/test.c
@@ -10,0 +10,4 @@
+static int test_resume(struct device *dev)
+{
+\treturn test_reinit(dev);
+}
"""


def test_extract_hunk_citation_found_with_context() -> None:
    """match_line_text found → HunkCitation with ±2 context lines."""
    patch = _make_patch(SINGLE_FILE_DIFF)
    result = extract_hunk_citation(patch, "devm_kzalloc")

    assert result is not None
    assert isinstance(result, HunkCitation)
    assert result.patch_id == "p-1"
    assert result.file == "sound/soc/codecs/test.c"
    # devm_kzalloc is at index 3 (0-based) among added lines;
    # context=2 means lines [1..5] (0-based), i.e. line_start=2, line_end=6 (1-based)
    assert result.line_start == 2
    assert result.line_end == 6
    assert len(result.verbatim_lines) == 5
    assert any("devm_kzalloc" in ln for ln in result.verbatim_lines)
    # Context includes surrounding lines
    assert any("struct test_priv" in ln for ln in result.verbatim_lines)
    assert any("!priv" in ln for ln in result.verbatim_lines)


def test_extract_hunk_citation_not_found_returns_none() -> None:
    """match_line_text not found → returns None."""
    patch = _make_patch(SINGLE_FILE_DIFF)
    result = extract_hunk_citation(patch, "NONEXISTENT_SYMBOL_xyz")

    assert result is None


def test_extract_hunk_citation_multi_file_correct_attribution() -> None:
    """Multi-file patch → correct file attribution for matched line."""
    patch = _make_patch(MULTI_FILE_DIFF, patch_id="p-multi")
    # Match on a line in the second file
    result = extract_hunk_citation(patch, "test_resume")

    assert result is not None
    assert result.patch_id == "p-multi"
    assert result.file == "sound/soc/codecs/test.c"
    assert any("test_resume" in ln for ln in result.verbatim_lines)

    # Match on a line in the first file (Kconfig)
    result2 = extract_hunk_citation(patch, "SND_SOC_TEST")

    assert result2 is not None
    assert result2.file == "sound/soc/codecs/Kconfig"
    assert any("SND_SOC_TEST" in ln for ln in result2.verbatim_lines)


# ---------------------------------------------------------------------------
# extract_hunk_context (formatter.py) — reviewer backfill
# ---------------------------------------------------------------------------


def test_extract_hunk_context_returns_windowed_lines() -> None:
    """extract_hunk_context returns ≤2*window+1 lines centred on target."""
    from kri.llm.formatter import extract_hunk_context

    diff = (
        "diff --git a/drivers/x/foo.c b/drivers/x/foo.c\n"
        "@@ -10,5 +10,6 @@\n"
        " int a;\n"
        " int b;\n"
        "+int c;\n"
        " int d;\n"
        " int e;\n"
    )
    lines = diff.splitlines()
    result = extract_hunk_context(lines, "drivers/x/foo.c", 12, window=1)
    assert len(result) <= 3
    assert any("int c" in ln for ln in result)


def test_extract_hunk_context_wrong_file_returns_empty() -> None:
    """When file_path does not match any diff --git header, returns empty list."""
    from kri.llm.formatter import extract_hunk_context

    diff = (
        "diff --git a/drivers/x/foo.c b/drivers/x/foo.c\n"
        "@@ -1,2 +1,3 @@\n"
        " int a;\n"
        "+int b;\n"
    )
    lines = diff.splitlines()
    result = extract_hunk_context(lines, "drivers/x/bar.c", 2)
    assert result == []


def test_extract_hunk_context_skips_removed_lines() -> None:
    """Removed lines (starting with '-') must not appear in context."""
    from kri.llm.formatter import extract_hunk_context

    diff = (
        "diff --git a/drivers/x/foo.c b/drivers/x/foo.c\n"
        "@@ -1,3 +1,3 @@\n"
        " int keep;\n"
        "-int removed;\n"
        "+int added;\n"
    )
    lines = diff.splitlines()
    result = extract_hunk_context(lines, "drivers/x/foo.c", 2, window=2)
    assert all(not ln.startswith("-") for ln in result)
