"""WP-9.1c: ASoC signal-specificity tightening tests.

Proves that tightened signal-matching logic rejects shallow matches while
still firing on the real anti-pattern each rule describes.
"""
from __future__ import annotations

from kri.common.models import Patch, PatchSeries
from kri.packages.asoc.plugins import PatternMatchPlugin, build_reasoning_plugins


def _make_patch(diff: str, files: list[str] | None = None) -> Patch:
    if files is None:
        files = ["sound/soc/codecs/test-codec.c"]
    return Patch(
        patch_id="p-test-1",
        subject="test patch",
        sequence=1,
        series_total=1,
        diff=diff,
        files_changed=files,
    )


def _make_series(patch: Patch) -> PatchSeries:
    return PatchSeries(series_id="s-test", patches=[patch])


def _get_tdm_plugin() -> PatternMatchPlugin:
    for plugin in build_reasoning_plugins():
        if "tdm-slot" in plugin.plugin_id:
            return plugin
    raise RuntimeError("tdm-slot plugin not found")


def _get_resume_plugin() -> PatternMatchPlugin:
    for plugin in build_reasoning_plugins():
        if "resume-without-cleanup" in plugin.plugin_id:
            return plugin
    raise RuntimeError("resume plugin not found")


# ============================================================
# Sub-commit 1: asoc-tdm-slot-not-userspace
# ============================================================


def test_tdm_slot_rule_does_not_fire_on_kconfig_help_text() -> None:
    """Kconfig entry mentioning 'TDM' in help text must NOT trigger."""
    diff = """\
diff --git a/sound/soc/codecs/Kconfig b/sound/soc/codecs/Kconfig
index aaa..bbb 100644
--- a/sound/soc/codecs/Kconfig
+++ b/sound/soc/codecs/Kconfig
@@ -1,0 +1,5 @@
+config SND_SOC_AW88399
+\ttristate "Awinic AW88399 codec"
+\thelp
+\t  The awinic AW88399 is an I2S/TDM input, high efficiency
+\t  digital smart audio amplifier with kcontrol support.
"""
    patch = _make_patch(diff, files=["sound/soc/codecs/Kconfig"])
    series = _make_series(patch)
    plugin = _get_tdm_plugin()
    decisions = plugin.evaluate(patch, series)
    assert decisions == []


def test_tdm_slot_rule_does_not_fire_on_correct_dai_level_tdm_config() -> None:
    """A patch using set_tdm_slot() (correct practice) must NOT trigger."""
    diff = """\
diff --git a/sound/soc/mediatek/mt7986/mt7986-dai-etdm.c b/sound/soc/mediatek/mt7986/mt7986-dai-etdm.c
index aaa..bbb 100644
--- a/sound/soc/mediatek/mt7986/mt7986-dai-etdm.c
+++ b/sound/soc/mediatek/mt7986/mt7986-dai-etdm.c
@@ -100,0 +100,6 @@
+static int mt7986_set_tdm_slot(struct snd_soc_dai *dai,
+\tunsigned int tx_mask, unsigned int rx_mask,
+\tint slots, int slot_width)
+{
+\treturn mt7986_configure_slots(dai, slots, slot_width);
+}
"""
    patch = _make_patch(diff, files=["sound/soc/mediatek/mt7986/mt7986-dai-etdm.c"])
    series = _make_series(patch)
    plugin = _get_tdm_plugin()
    decisions = plugin.evaluate(patch, series)
    assert decisions == []


def test_tdm_slot_rule_fires_on_actual_soc_enum_definition() -> None:
    """A patch defining SOC_ENUM for TDM slot mapping MUST trigger."""
    diff = """\
diff --git a/sound/soc/codecs/test-codec.c b/sound/soc/codecs/test-codec.c
index aaa..bbb 100644
--- a/sound/soc/codecs/test-codec.c
+++ b/sound/soc/codecs/test-codec.c
@@ -50,0 +50,4 @@
+static SOC_ENUM_SINGLE_DECL(tdm_slot_map_enum,
+\tTEST_REG_TDM, 0,
+\ttdm_slot_text);
+static const struct snd_kcontrol_new tdm_slot_controls[] = {
"""
    patch = _make_patch(diff)
    series = _make_series(patch)
    plugin = _get_tdm_plugin()
    decisions = plugin.evaluate(patch, series)
    assert len(decisions) == 1
    assert decisions[0].category == "asoc-reject-userspace-tdm-slot-enum"


# ============================================================
# Sub-commit 2: asoc-resume-must-clean-up
# ============================================================


def test_resume_rule_does_not_fire_on_kconfig_help_text() -> None:
    """Kconfig entry mentioning 'suspend/resume' must NOT trigger."""
    diff = """\
diff --git a/sound/soc/codecs/Kconfig b/sound/soc/codecs/Kconfig
index aaa..bbb 100644
--- a/sound/soc/codecs/Kconfig
+++ b/sound/soc/codecs/Kconfig
@@ -1,0 +1,5 @@
+config SND_SOC_TEST_CODEC
+\ttristate "Test codec with suspend/resume support"
+\thelp
+\t  Supports runtime PM with kzalloc-based buffer management
+\t  for resume path initialization.
"""
    patch = _make_patch(diff, files=["sound/soc/codecs/Kconfig"])
    series = _make_series(patch)
    plugin = _get_resume_plugin()
    decisions = plugin.evaluate(patch, series)
    assert decisions == []


def test_resume_rule_does_not_fire_on_function_name_only() -> None:
    """A resume function that doesn't allocate must NOT trigger."""
    diff = """\
diff --git a/sound/soc/renesas/rz-ssi.c b/sound/soc/renesas/rz-ssi.c
index aaa..bbb 100644
--- a/sound/soc/renesas/rz-ssi.c
+++ b/sound/soc/renesas/rz-ssi.c
@@ -200,0 +200,5 @@
+static int rz_ssi_resume(struct device *dev)
+{
+\tstruct rz_ssi *ssi = dev_get_drvdata(dev);
+\treturn rz_ssi_init_hw(ssi);
+}
"""
    patch = _make_patch(diff, files=["sound/soc/renesas/rz-ssi.c"])
    series = _make_series(patch)
    plugin = _get_resume_plugin()
    decisions = plugin.evaluate(patch, series)
    assert decisions == []


def test_resume_rule_fires_on_kzalloc_in_resume_handler() -> None:
    """A resume handler allocating without cleanup MUST trigger."""
    diff = """\
diff --git a/sound/soc/codecs/test-codec.c b/sound/soc/codecs/test-codec.c
index aaa..bbb 100644
--- a/sound/soc/codecs/test-codec.c
+++ b/sound/soc/codecs/test-codec.c
@@ -300,0 +300,8 @@
+static int test_codec_resume(struct device *dev)
+{
+\tstruct test_priv *priv = dev_get_drvdata(dev);
+\tpriv->buf = kzalloc(BUF_SIZE, GFP_KERNEL);
+\tif (!priv->buf)
+\t\treturn -ENOMEM;
+\treturn test_codec_reinit(priv);
+}
"""
    patch = _make_patch(diff)
    series = _make_series(patch)
    plugin = _get_resume_plugin()
    decisions = plugin.evaluate(patch, series)
    assert len(decisions) == 1
    assert decisions[0].category == "asoc-reject-resume-without-cleanup"


# ============================================================
# Sub-commit 3: _first_owned_file preference order
# ============================================================


def test_location_prefers_c_over_kconfig() -> None:
    """When a patch touches Kconfig + .c, location must report the .c file."""
    diff = """\
diff --git a/sound/soc/codecs/test-codec.c b/sound/soc/codecs/test-codec.c
index aaa..bbb 100644
--- a/sound/soc/codecs/test-codec.c
+++ b/sound/soc/codecs/test-codec.c
@@ -50,0 +50,3 @@
+static SOC_ENUM_SINGLE_DECL(tdm_slot_map_enum,
+\tTEST_REG_TDM, 0,
+\ttdm_slot_text);
"""
    patch = _make_patch(
        diff,
        files=[
            "sound/soc/codecs/Kconfig",
            "sound/soc/codecs/Makefile",
            "sound/soc/codecs/test-codec.c",
            "sound/soc/codecs/test-codec.h",
        ],
    )
    series = _make_series(patch)
    plugin = _get_tdm_plugin()
    decisions = plugin.evaluate(patch, series)
    assert len(decisions) == 1
    assert decisions[0].location == "sound/soc/codecs/test-codec.c"


def test_location_prefers_h_over_kconfig_when_no_c() -> None:
    """When a patch touches Kconfig + .h but no .c, location must report .h."""
    diff = """\
diff --git a/sound/soc/codecs/test-codec.h b/sound/soc/codecs/test-codec.h
index aaa..bbb 100644
--- a/sound/soc/codecs/test-codec.h
+++ b/sound/soc/codecs/test-codec.h
@@ -50,0 +50,3 @@
+static SOC_ENUM_SINGLE_DECL(tdm_slot_map_enum,
+\tTEST_REG_TDM, 0,
+\ttdm_slot_text);
"""
    patch = _make_patch(
        diff,
        files=[
            "sound/soc/codecs/Kconfig",
            "sound/soc/codecs/test-codec.h",
        ],
    )
    series = _make_series(patch)
    plugin = _get_tdm_plugin()
    decisions = plugin.evaluate(patch, series)
    assert len(decisions) == 1
    assert decisions[0].location == "sound/soc/codecs/test-codec.h"
