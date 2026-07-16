"""WP-9.1a Sub-commit 2: SeriesContext accumulator.

``build_series_context`` must deterministically accumulate, per patch
sequence, what each patch in a series introduces/removes -- so downstream
reasoning can distinguish "used before declared in this diff" from "declared
earlier in the same series."
"""

from __future__ import annotations

from kri.common.models import Patch, PatchSeries
from kri.review_engine.series_context import build_series_context

_PATCH_1_DIFF = """\
diff --git a/drivers/x/a.c b/drivers/x/a.c
new file mode 100644
index 000000000000..1111111
--- /dev/null
+++ b/drivers/x/a.c
@@ -0,0 +1,3 @@
+int helper_foo(void)
+{
+	return 0;
+}
"""

_PATCH_2_DIFF = """\
diff --git a/drivers/x/b.c b/drivers/x/b.c
index 2222222..3333333 100644
--- a/drivers/x/b.c
+++ b/drivers/x/b.c
@@ -1,2 +1,3 @@
 int existing(void)
 {
+	helper_foo();
 	return 0;
 }
"""

_PATCH_3_DIFF = """\
diff --git a/arch/arm/boot/dts/vendor-my-part.dts b/arch/arm/boot/dts/vendor-my-part.dts
index 4444444..5555555 100644
--- a/arch/arm/boot/dts/vendor-my-part.dts
+++ b/arch/arm/boot/dts/vendor-my-part.dts
@@ -1,2 +1,3 @@
 mypart@0 {
+	compatible = "vendor,my-part";
 };
"""


def _build_three_patch_series() -> PatchSeries:
    patches = [
        Patch(
            patch_id="p-1",
            subject="add helper_foo",
            sequence=1,
            series_total=3,
            files_changed=["drivers/x/a.c"],
            diff=_PATCH_1_DIFF,
        ),
        Patch(
            patch_id="p-2",
            subject="use helper_foo",
            sequence=2,
            series_total=3,
            files_changed=["drivers/x/b.c"],
            diff=_PATCH_2_DIFF,
        ),
        Patch(
            patch_id="p-3",
            subject="add DT compatible",
            sequence=3,
            series_total=3,
            files_changed=["arch/arm/boot/dts/vendor-my-part.dts"],
            diff=_PATCH_3_DIFF,
        ),
    ]
    return PatchSeries(series_id="s-context-test", patches=patches)


def test_series_context_populated_from_synthetic_series() -> None:
    series = _build_three_patch_series()
    ctx = build_series_context(series)

    assert ctx.introduced_symbols[1] == {"helper_foo"}
    assert ctx.new_files[1] == {"drivers/x/a.c"}
    assert ctx.new_dt_compatibles[3] == {"vendor,my-part"}


def test_series_context_is_deterministic() -> None:
    series = _build_three_patch_series()
    ctx_a = build_series_context(series)
    ctx_b = build_series_context(series)

    assert ctx_a == ctx_b
