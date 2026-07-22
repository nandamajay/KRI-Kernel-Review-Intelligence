"""Offline WP-S1A replay driver for /tmp/rubikpi.mbox.

Measures — without invoking the LLM:
  - prompt-size impact (bytes added by series-context block per patch)
  - series_context metadata shape / declared symbol counts
  - determinism (build twice, compare)

For end-to-end finding-count delta a live LLM run is required (out of scope
of this offline harness); this driver prints the deterministic surface
changes only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from kri.common.models import PatchSeries
from kri.lore_manager.mbox import parse_mbox_bytes
from kri.patch_manager.manager import PatchManagerImpl as PatchManager
from kri.llm.prompts import (
    REVIEW_CODE_QUALITY_PROMPT,
    REVIEW_SUBSYSTEM_PROMPT,
    annotate_diff_with_line_numbers,
    format_static_findings,
)
from kri.series import (
    SeriesReviewContextBuilder,
    format_series_context,
)


def _render_review_prompt(patch, series_context: str) -> str:
    annotated = annotate_diff_with_line_numbers(patch.diff)
    return REVIEW_CODE_QUALITY_PROMPT.format(
        domain_context="",
        static_findings=format_static_findings([]),
        series_context=series_context,
        commit_message=patch.commit_message[:2000],
        annotated_diff=annotated[:12000],
        upstream_comment_instruction="(...)",
    )


def _render_subsystem_prompt(patch, series_context: str) -> str:
    annotated = annotate_diff_with_line_numbers(patch.diff)
    return REVIEW_SUBSYSTEM_PROMPT.format(
        domain_context="",
        static_findings=format_static_findings([]),
        series_context=series_context,
        commit_message=patch.commit_message[:2000],
        files_changed="\n".join(patch.files_changed),
        annotated_diff=annotated[:12000],
        upstream_comment_instruction="(...)",
    )


def main(mbox_path: str) -> int:
    data = Path(mbox_path).read_bytes()
    thread = parse_mbox_bytes(data, thread_id="rubikpi3-replay")
    pm = PatchManager()
    series: PatchSeries = pm.parse(thread)

    print(f"loaded {len(series.patches)} patches from {mbox_path}")
    print(f"series title: {series.title!r}")
    print(f"cover_letter present: {bool(series.cover_letter)}")

    builder = SeriesReviewContextBuilder()
    ctx_a = builder.build(series)
    ctx_b = builder.build(series)
    determinism_ok = repr(ctx_a) == repr(ctx_b)
    print(f"determinism (build == rebuild): {determinism_ok}")

    print(f"total_patches: {ctx_a.total_patches}, "
          f"is_multi_patch: {ctx_a.is_multi_patch()}")
    reg = ctx_a.declared_symbols
    print(
        "declared: "
        f"compatibles={len(reg.compatibles)} "
        f"dt_props={len(reg.dt_properties)} "
        f"c_symbols={len(reg.c_symbols)} "
        f"files_added={len(reg.files_added)}"
    )
    print(f"file_touch_map entries: {len(ctx_a.file_touch_map)}")
    multi = {p: v for p, v in ctx_a.file_touch_map.items() if len(v) > 1}
    print(f"files touched by >1 patch: {len(multi)}")

    if reg.compatibles:
        print("sample compatibles:", sorted(reg.compatibles)[:5])
    if reg.dt_properties:
        print("sample dt_properties:", sorted(reg.dt_properties)[:5])
    if reg.c_symbols:
        print("sample c_symbols:", sorted(reg.c_symbols)[:5])

    print()
    print("=== prompt-size impact per patch (code-quality prompt) ===")
    total_off = 0
    total_on = 0
    for patch in series.patches:
        block = format_series_context(ctx_a, patch.patch_id)
        p_off = _render_review_prompt(patch, "")
        p_on = _render_review_prompt(patch, block)
        s_off = _render_subsystem_prompt(patch, "")
        s_on = _render_subsystem_prompt(patch, block)
        diff_cq = len(p_on) - len(p_off)
        diff_ss = len(s_on) - len(s_off)
        total_off += len(p_off) + len(s_off)
        total_on += len(p_on) + len(s_on)
        entry = ctx_a.patch_index.get(patch.patch_id)
        idx = f"{entry.index}/{entry.total}" if entry else "?/?"
        print(
            f"  patch {idx:5s} {patch.subject[:60]:<60} "
            f"cq+={diff_cq:5d}B ss+={diff_ss:5d}B "
            f"block={len(block):5d}B"
        )
    delta = total_on - total_off
    pct = (delta / total_off * 100.0) if total_off else 0.0
    print()
    print(f"aggregate prompt bytes: OFF={total_off} ON={total_on} "
          f"delta={delta:+d}B ({pct:+.2f}%)")

    # Byte-identity guarantee for single-patch: not applicable to this mbox
    # (multi-patch series), but we can still assert single-patch neutrality
    # on a synthetic 1-patch subset:
    if len(series.patches) >= 1:
        one_patch_series = PatchSeries(
            series_id="one",
            title="synthetic single",
            cover_letter="",
            patches=series.patches[:1],
        )
        one_ctx = builder.build(one_patch_series)
        block_single = format_series_context(
            one_ctx, one_patch_series.patches[0].patch_id
        )
        print(f"single-patch block length (must be 0): {len(block_single)}")
        assert block_single == "", "byte-identity violated for single-patch"

    return 0


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/rubikpi.mbox"
    sys.exit(main(path))
