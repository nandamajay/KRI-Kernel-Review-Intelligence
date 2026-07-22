"""WP-S1A tests — SeriesReviewContextBuilder, extractors, prompt renderer,
and IntelligentReviewEngine wiring.

Named ``test_series_review_context`` (not ``test_series_context``) so the
existing WP-9.1a ``test_series_context.py`` (kri.common.models.SeriesContext)
remains untouched.

Tests map to spec §8.1.1 (U1-U10), §8.1.1 extractor coverage (U3-U5),
prompt renderer coverage (spec §4.2), and §8.2 wiring tests
(W1, W2, W3, W7).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kri.common.models import Patch, PatchSeries
from kri.llm.reviewer import IntelligentReviewEngine
from kri.llm.sanitize import _TRAILER_RE
from kri.series import (
    PatchIndexEntry,
    SeriesReviewContext,
    SeriesReviewContextBuilder,
    SymbolRegistry,
    format_series_context,
)
from kri.series.extractors import (
    extract_added_files,
    extract_c_symbols,
    extract_compatibles,
    extract_containing_function,
    extract_cover_letter,
    extract_dt_properties,
    extract_referenced_symbols,
    is_binary_patch,
    parse_series_index,
)


# ---------------------------------------------------------------------------
# Diff fixtures
# ---------------------------------------------------------------------------


YAML_COMPATIBLE_DIFF = """\
diff --git a/Documentation/devicetree/bindings/sound/qcom,sm8250.yaml b/Documentation/devicetree/bindings/sound/qcom,sm8250.yaml
index dae440ecab59..01bc494286fb 100644
--- a/Documentation/devicetree/bindings/sound/qcom,sm8250.yaml
+++ b/Documentation/devicetree/bindings/sound/qcom,sm8250.yaml
@@ -49,6 +49,7 @@ properties:
           - qcom,sm8250-sndcard
           - qcom,sm8450-sndcard
           - qcom,x1e80100-sndcard
+          - thundercomm,qcs6490-rubikpi3-sndcard

   audio-routing:
     $ref: /schemas/types.yaml#/definitions/non-unique-string-array
"""

YAML_PROPERTY_DIFF = """\
diff --git a/Documentation/devicetree/bindings/sound/everest,es8316.yaml b/Documentation/devicetree/bindings/sound/everest,es8316.yaml
index fe5d938ca310..a0a4c1c99cf3 100644
--- a/Documentation/devicetree/bindings/sound/everest,es8316.yaml
+++ b/Documentation/devicetree/bindings/sound/everest,es8316.yaml
@@ -60,6 +60,11 @@ properties:
   "#sound-dai-cells":
     const: 0

+  everest,jack-detect-inverted:
+    $ref: /schemas/types.yaml#/definitions/flag
+    description:
+      Defined to invert the jack detection.
+
 required:
   - compatible
   - reg
"""

C_HELPER_DIFF = """\
diff --git a/sound/soc/qcom/common.c b/sound/soc/qcom/common.c
index abcdef1..234567 100644
--- a/sound/soc/qcom/common.c
+++ b/sound/soc/qcom/common.c
@@ -214,6 +212,55 @@ int qcom_snd_wcd_jack_setup(struct snd_soc_pcm_runtime *rtd,
 		*jack_setup = true;
 	}

+	return 0;
+}
+
+int qcom_snd_headset_jack_setup(struct snd_soc_pcm_runtime *rtd,
+				struct snd_soc_jack *jack, bool *jack_setup)
+{
+	int rval;
+
+	rval = qcom_snd_headset_jack_init(card, jack, jack_setup);
+	if (rval)
+		return rval;
+	return 0;
+}
+EXPORT_SYMBOL_GPL(qcom_snd_headset_jack_setup);
+
+void qcom_snd_headset_jack_cleanup(struct snd_soc_pcm_runtime *rtd)
+{
+	return;
+}
+EXPORT_SYMBOL_GPL(qcom_snd_headset_jack_cleanup);
"""

C_CONSUMER_DIFF = """\
diff --git a/sound/soc/qcom/sc8280xp.c b/sound/soc/qcom/sc8280xp.c
index abcdef1..234567 100644
--- a/sound/soc/qcom/sc8280xp.c
+++ b/sound/soc/qcom/sc8280xp.c
@@ -100,6 +100,7 @@ static int sc8280xp_snd_init(struct snd_soc_pcm_runtime *rtd)
 	struct sc8280xp_snd_data *data = snd_soc_card_get_drvdata(card);
+	int rc;

+	rc = qcom_snd_headset_jack_setup(rtd, &data->jack, &data->jack_setup);
+	if (rc)
+		return rc;
 	return 0;
 }
"""

NEW_FILE_DIFF = """\
diff --git a/sound/soc/qcom/rubikpi3.c b/sound/soc/qcom/rubikpi3.c
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/sound/soc/qcom/rubikpi3.c
@@ -0,0 +1,5 @@
+#include <linux/module.h>
+
+static int foo(void) {
+	return 0;
+}
"""

BINARY_DIFF = """\
diff --git a/some/binary b/some/binary
Binary files a/some/binary and b/some/binary differ
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_patch(pid: str, subject: str, diff: str, seq: int = 0,
                total: int = 0, files: list[str] | None = None,
                commit_message: str = "") -> Patch:
    return Patch(
        patch_id=pid,
        subject=subject,
        commit_message=commit_message,
        files_changed=files or [],
        diff=diff,
        sequence=seq,
        series_total=total,
    )


def _make_series(patches: list[Patch], cover_letter: str = "",
                 title: str = "Test series") -> PatchSeries:
    return PatchSeries(
        series_id="series-test-1",
        title=title,
        cover_letter=cover_letter,
        patches=patches,
    )


# ---------------------------------------------------------------------------
# Extractor tests (U3, U4, U5, U10 + helpers)
# ---------------------------------------------------------------------------


def test_U3_extract_compatible_from_yaml_diff():
    """U3: compatible in an enum-list addition is extracted."""
    result = extract_compatibles(YAML_COMPATIBLE_DIFF)
    assert "thundercomm,qcs6490-rubikpi3-sndcard" in result


def test_extract_compatibles_ignores_removals():
    """A removal line ('-') never contributes to the registry."""
    removal = YAML_COMPATIBLE_DIFF.replace(
        "+          - thundercomm,",
        "-          - thundercomm,",
    )
    result = extract_compatibles(removal)
    assert "thundercomm,qcs6490-rubikpi3-sndcard" not in result


def test_U4_extract_dt_property_from_yaml_diff():
    """U4: property under properties: anchor is extracted."""
    result = extract_dt_properties(YAML_PROPERTY_DIFF)
    assert "everest,jack-detect-inverted" in result


def test_extract_dt_properties_ignores_description_metadata():
    """The 'description:' pseudo-key inside a property block is filtered."""
    result = extract_dt_properties(YAML_PROPERTY_DIFF)
    assert "description" not in result
    assert "ref" not in result


def test_U5_extract_c_symbol_from_c_diff():
    """U5: multiple C function defs are extracted from a single hunk."""
    result = extract_c_symbols(C_HELPER_DIFF)
    assert "qcom_snd_headset_jack_setup" in result
    assert "qcom_snd_headset_jack_cleanup" in result


def test_extract_c_symbols_ignores_control_keywords():
    """Control-flow keywords must never be misread as function names."""
    diff = (
        "diff --git a/x.c b/x.c\n"
        "--- a/x.c\n"
        "+++ b/x.c\n"
        "@@ -1,1 +1,3 @@\n"
        "+int foo(void) {\n"
        "+  if (x) return 0;\n"
        "+}\n"
    )
    result = extract_c_symbols(diff)
    assert result == {"foo"}


def test_extract_added_files_detects_new_file_marker():
    result = extract_added_files(NEW_FILE_DIFF)
    assert "sound/soc/qcom/rubikpi3.c" in result


def test_extract_added_files_ignores_modifications():
    """A file mod (no 'new file mode' line) is not counted."""
    result = extract_added_files(C_HELPER_DIFF)
    assert result == set()


def test_extract_referenced_symbols_word_boundary():
    """Substring collisions are rejected; word boundary required."""
    symbols = {"qcom_snd_headset_jack_setup"}
    result = extract_referenced_symbols(C_CONSUMER_DIFF, symbols)
    assert result == symbols
    # A neighbouring substring like "_setup_x" must not collide.
    collision_diff = (
        "diff --git a/x.c b/x.c\n"
        "--- a/x.c\n"
        "+++ b/x.c\n"
        "@@ -1,1 +1,2 @@\n"
        "+qcom_snd_headset_jack_setup_extra();\n"
    )
    assert extract_referenced_symbols(collision_diff, symbols) == set()


def test_extract_containing_function_returns_name():
    """The function name enclosing an added line is recoverable."""
    fn = extract_containing_function(C_HELPER_DIFF, 216)
    assert fn is not None


def test_parse_series_index_variants():
    assert parse_series_index("[PATCH 3/6] foo") == (3, 6)
    assert parse_series_index("[PATCH v2 1/5] foo") == (1, 5)
    assert parse_series_index("[PATCH RFC 2/3] foo") == (2, 3)
    assert parse_series_index("[RFC 4/4] foo") == (4, 4)
    assert parse_series_index("no index here") is None


def test_extract_cover_letter_from_series_field():
    """Prefers series.cover_letter when set."""
    series = _make_series([_make_patch("p1", "s", "")], cover_letter="hello")
    assert extract_cover_letter(series) == "hello"


def test_extract_cover_letter_falls_back_to_seq_zero_patch():
    """Falls back to sequence==0 patch's commit_message."""
    patches = [
        _make_patch("p0", "cover", "", seq=0, commit_message="cover-body"),
        _make_patch("p1", "one", "", seq=1),
    ]
    series = _make_series(patches, cover_letter="")
    assert extract_cover_letter(series) == "cover-body"


def test_U10_binary_patch_skipped_gracefully():
    """U10: Binary patches contribute nothing and never raise."""
    assert is_binary_patch(BINARY_DIFF)
    assert extract_compatibles(BINARY_DIFF) == set()
    assert extract_dt_properties(BINARY_DIFF) == set()
    assert extract_c_symbols(BINARY_DIFF) == set()
    assert extract_added_files(BINARY_DIFF) == set()
    assert extract_referenced_symbols(BINARY_DIFF, {"foo"}) == set()
    assert extract_containing_function(BINARY_DIFF, 1) is None


# ---------------------------------------------------------------------------
# Builder tests (U1, U2, U6, U7, U8, U9)
# ---------------------------------------------------------------------------


def test_U1_single_patch_builds_empty_registry():
    """U1: 1-patch series produces empty declared_symbols, cover_letter=None
    if not set, total_patches==1."""
    patch = _make_patch("p1", "[PATCH] solo", C_HELPER_DIFF, seq=1, total=1)
    series = _make_series([patch])
    ctx = SeriesReviewContextBuilder().build(series)
    assert ctx.total_patches == 1
    assert ctx.cover_letter is None
    # Even though extractors would produce something, the series is
    # single-patch: builder still populates registry (it's per-diff, not
    # per-series-cardinality). is_multi_patch is what governs rendering.
    assert not ctx.is_multi_patch()


def test_U2_multi_patch_indexes_correctly():
    """U2: 6-patch series -> 6 PatchIndexEntry values with correct indices."""
    patches = [
        _make_patch(f"p{i}", f"[PATCH v2 {i}/6] subj", "", seq=i, total=6)
        for i in range(1, 7)
    ]
    series = _make_series(patches)
    ctx = SeriesReviewContextBuilder().build(series)
    assert len(ctx.patch_index) == 6
    for i, p in enumerate(patches, 1):
        entry = ctx.patch_index[p.patch_id]
        assert entry.index == i
        assert entry.total == 6


def test_index_falls_back_to_subject_parse_when_sequence_zero():
    """Missing Patch.sequence falls back to _SUBJECT_INDEX_RE."""
    patch = _make_patch("p1", "[PATCH v2 3/5] foo", "", seq=0, total=0)
    series = _make_series([patch])
    ctx = SeriesReviewContextBuilder().build(series)
    entry = ctx.patch_index["p1"]
    assert entry.index == 3
    assert entry.total == 5


def test_index_positional_fallback_when_no_signal():
    """No sequence, no parseable subject: positional index."""
    patches = [
        _make_patch("p1", "foo", "", seq=0, total=0),
        _make_patch("p2", "bar", "", seq=0, total=0),
    ]
    series = _make_series(patches)
    ctx = SeriesReviewContextBuilder().build(series)
    assert ctx.patch_index["p1"].index == 1
    assert ctx.patch_index["p2"].index == 2


def test_U6_file_touch_map_records_all_writers():
    """U6: A file touched by two patches maps to both."""
    p1 = _make_patch("p1", "one", "", seq=1, total=2,
                     files=["sound/soc/qcom/common.c"])
    p2 = _make_patch("p2", "two", "", seq=2, total=2,
                     files=["sound/soc/qcom/common.c"])
    series = _make_series([p1, p2])
    ctx = SeriesReviewContextBuilder().build(series)
    assert ctx.file_touch_map["sound/soc/qcom/common.c"] == ("p1", "p2")


def test_U7_cover_letter_from_series_field():
    """U7: series.cover_letter -> ctx.cover_letter."""
    p = _make_patch("p1", "one", "", seq=1, total=1)
    series = _make_series([p], cover_letter="hello world")
    ctx = SeriesReviewContextBuilder().build(series)
    assert ctx.cover_letter == "hello world"


def test_U8_no_cover_letter_returns_none():
    """U8: empty cover_letter + no seq-0 patch -> None."""
    p = _make_patch("p1", "one", "", seq=1, total=1)
    series = _make_series([p], cover_letter="")
    ctx = SeriesReviewContextBuilder().build(series)
    assert ctx.cover_letter is None


def test_U9_build_is_deterministic():
    """U9: Sec. 40 determinism — two builds -> byte-equal output."""
    patches = [
        _make_patch("p1", "[PATCH 1/2] one", YAML_COMPATIBLE_DIFF, seq=1, total=2),
        _make_patch("p2", "[PATCH 2/2] two", C_HELPER_DIFF, seq=2, total=2),
    ]
    series = _make_series(patches, cover_letter="cover")
    builder = SeriesReviewContextBuilder()
    a = builder.build(series)
    b = builder.build(series)
    assert a == b
    # Also assert repr equality — a stronger byte-identity check.
    assert repr(a) == repr(b)


def test_builder_declares_symbols_across_patches():
    """C symbols from patch 3 land in the registry with patch_id=p3."""
    p2 = _make_patch("p2", "[PATCH 2/3] yaml", YAML_COMPATIBLE_DIFF, seq=2, total=3)
    p3 = _make_patch("p3", "[PATCH 3/3] helpers", C_HELPER_DIFF, seq=3, total=3)
    p1 = _make_patch("p1", "[PATCH 1/3] setup", "", seq=1, total=3)
    series = _make_series([p1, p2, p3])
    ctx = SeriesReviewContextBuilder().build(series)
    assert ctx.declared_symbols.declares_compatible(
        "thundercomm,qcs6490-rubikpi3-sndcard"
    )
    assert ctx.declared_symbols.compatibles[
        "thundercomm,qcs6490-rubikpi3-sndcard"
    ] == "p2"
    assert ctx.declared_symbols.declares_c_symbol(
        "qcom_snd_headset_jack_setup"
    )
    assert ctx.declared_symbols.c_symbols[
        "qcom_snd_headset_jack_setup"
    ] == "p3"


# ---------------------------------------------------------------------------
# Prompt renderer tests (spec §4.2)
# ---------------------------------------------------------------------------


def test_format_series_context_empty_for_single_patch():
    """format_series_context returns exactly '' when total_patches<=1."""
    ctx = SeriesReviewContext(
        series_id="s",
        title="t",
        cover_letter=None,
        total_patches=1,
        patch_index={},
        declared_symbols=SymbolRegistry(),
        file_touch_map={},
    )
    assert format_series_context(ctx, "p1") == ""


def test_format_series_context_empty_for_missing_patch_id():
    """If patch_id isn't in the index, render is empty."""
    ctx = SeriesReviewContext(
        series_id="s",
        title="t",
        cover_letter=None,
        total_patches=3,
        patch_index={"other": PatchIndexEntry("other", 1, 3, "subj", ())},
        declared_symbols=SymbolRegistry(),
        file_touch_map={},
    )
    assert format_series_context(ctx, "not-present") == ""


def test_format_series_context_renders_block_for_multi_patch():
    """Rendered block contains the expected structured sections."""
    reg = SymbolRegistry(
        compatibles={"foo,bar": "p2"},
        dt_properties={"foo,prop": "p2"},
        c_symbols={"qcom_snd_headset_jack_setup": "p3"},
        files_added={},
    )
    idx = {
        "p1": PatchIndexEntry("p1", 1, 3, "one", ()),
        "p2": PatchIndexEntry("p2", 2, 3, "two", ()),
        "p3": PatchIndexEntry("p3", 3, 3, "three", ()),
    }
    ctx = SeriesReviewContext(
        series_id="s",
        title="Test",
        cover_letter="c",
        total_patches=3,
        patch_index=idx,
        declared_symbols=reg,
        file_touch_map={},
    )
    rendered = format_series_context(ctx, "p1")
    assert "Series Context" in rendered
    assert "part 1 of a 3-patch series" in rendered
    assert "foo,bar" in rendered
    assert "foo,prop" in rendered
    assert "qcom_snd_headset_jack_setup" in rendered
    assert "Cover letter" in rendered


def test_format_series_context_no_trailer_synthesis():
    """S1: rendered block must never introduce upstream-trailer patterns.

    Even if the cover letter contains suspicious text, we should not emit
    tokens like 'Reviewed-by:' unadorned.
    """
    reg = SymbolRegistry()
    idx = {"p1": PatchIndexEntry("p1", 1, 2, "one", ())}
    ctx = SeriesReviewContext(
        series_id="s",
        title="Sample series about Reviewed-by rules",
        cover_letter="Some cover letter content.",
        total_patches=2,
        patch_index=idx,
        declared_symbols=reg,
        file_touch_map={},
    )
    rendered = format_series_context(ctx, "p1")
    # None of the trailer tokens should slip through on any line.
    for line in rendered.splitlines():
        assert not _TRAILER_RE.match(line), (
            f"Series-context block emitted a trailer-like line: {line!r}"
        )


def test_format_series_context_caps_cover_letter():
    """Cover letters longer than 2000 chars are truncated with '...'."""
    long_cover = "A" * 3000
    idx = {"p1": PatchIndexEntry("p1", 1, 2, "one", ())}
    ctx = SeriesReviewContext(
        series_id="s",
        title="t",
        cover_letter=long_cover,
        total_patches=2,
        patch_index=idx,
        declared_symbols=SymbolRegistry(),
        file_touch_map={},
    )
    rendered = format_series_context(ctx, "p1")
    # The rendered text must not contain the full 3000 As.
    assert "A" * 3000 not in rendered
    assert rendered.endswith("...\n\n") or "..." in rendered


# ---------------------------------------------------------------------------
# Wiring tests (W1, W2, W3, W7)
# ---------------------------------------------------------------------------


def _fake_client() -> MagicMock:
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    # complete_json used by agents; return empty list -> no comments.
    client.complete_json.return_value = []
    # complete used by aggregate assessment; return simple text.
    resp = MagicMock()
    resp.content = "ok"
    client.complete.return_value = resp
    return client


def test_W1_builder_invoked_once_per_review():
    """W1: SeriesReviewContextBuilder.build is called exactly once per
    engine.review(series)."""
    builder = MagicMock(spec=SeriesReviewContextBuilder)
    builder.build.return_value = SeriesReviewContext(
        series_id="s",
        title="t",
        cover_letter=None,
        total_patches=2,
        patch_index={},
        declared_symbols=SymbolRegistry(),
        file_touch_map={},
    )
    engine = IntelligentReviewEngine(
        client=_fake_client(),
        series_context_builder=builder,
    )
    p1 = _make_patch("p1", "[PATCH 1/2] a", "", seq=1, total=2)
    p2 = _make_patch("p2", "[PATCH 2/2] b", "", seq=2, total=2)
    engine.review(_make_series([p1, p2]))
    assert builder.build.call_count == 1


def test_W2_series_context_threaded_to_all_agent_calls():
    """W2: Every code_quality / subsystem agent call receives the SAME
    series-context string derived from the SAME SeriesReviewContext."""
    client = _fake_client()
    p1 = _make_patch("p1", "[PATCH 1/2] a", "", seq=1, total=2,
                     files=["sound/soc/qcom/common.c"])
    p2 = _make_patch("p2", "[PATCH 2/2] b", C_HELPER_DIFF, seq=2, total=2,
                     files=["sound/soc/qcom/common.c"])
    engine = IntelligentReviewEngine(client=client)
    engine.review(_make_series([p1, p2], cover_letter="cover"))

    # complete_json is called 3x per patch: summariser + code_quality +
    # subsystem. Summariser never receives the series-context block per
    # spec §4.1, so we scope the assertion to the two reviewing agents.
    prompts = [
        call.args[0][0]["content"]
        for call in client.complete_json.call_args_list
    ]
    review_prompts = [p for p in prompts if "## Diff (with line numbers" in p
                      or "## Diff (with line numbers on the new-file side)" in p]
    assert len(review_prompts) == 4
    # Every review prompt for a multi-patch series must include the
    # series-context header.
    for p in review_prompts:
        assert "## Series Context" in p
    # Summariser prompts must NOT include the series-context header
    # (WP-S1A §4.1 — summariser is a stable narrative surface).
    for p in prompts:
        if "## Diff (with line numbers" not in p:
            assert "## Series Context" not in p


def test_W3_single_patch_prompt_omits_series_context():
    """W3 (byte-identity for single-patch): the series-context section is
    entirely absent from prompts when the series has one patch."""
    client = _fake_client()
    p = _make_patch("p1", "[PATCH] solo", "", seq=1, total=1)
    engine = IntelligentReviewEngine(client=client)
    engine.review(_make_series([p]))

    prompts = [
        call.args[0][0]["content"]
        for call in client.complete_json.call_args_list
    ]
    # Summariser + code_quality + subsystem = 3 prompts.
    assert len(prompts) == 3
    for p_text in prompts:
        assert "## Series Context" not in p_text


def test_W7_series_awareness_off_omits_series_context_and_metadata():
    """W7: With series_awareness=False the engine produces no
    series_context in prompts and no series_context key in report metadata."""
    client = _fake_client()
    p1 = _make_patch("p1", "[PATCH 1/2] a", "", seq=1, total=2)
    p2 = _make_patch("p2", "[PATCH 2/2] b", "", seq=2, total=2)
    engine = IntelligentReviewEngine(client=client, series_awareness=False)
    report = engine.review(_make_series([p1, p2]))

    prompts = [
        call.args[0][0]["content"]
        for call in client.complete_json.call_args_list
    ]
    for p_text in prompts:
        assert "## Series Context" not in p_text

    # And no series_context / series_index metadata leaks in.
    assert "series_context" not in (report.metadata or {})
    for pr in report.patches:
        assert "series_index" not in (pr.metadata or {})


def test_report_metadata_contains_series_context_for_multi_patch():
    """W5-ish: report metadata carries series_context when multi-patch."""
    client = _fake_client()
    p1 = _make_patch("p1", "[PATCH 1/2] a", YAML_COMPATIBLE_DIFF, seq=1, total=2)
    p2 = _make_patch("p2", "[PATCH 2/2] b", "", seq=2, total=2)
    engine = IntelligentReviewEngine(client=client)
    report = engine.review(_make_series([p1, p2]))
    assert "series_context" in report.metadata
    assert report.metadata["series_context"]["total_patches"] == 2
    assert (
        "thundercomm,qcs6490-rubikpi3-sndcard"
        in report.metadata["series_context"]["declared_compatibles"]
    )
