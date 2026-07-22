"""WP-S1 prompt renderer — series-context block for per-patch prompts.

``format_series_context`` mirrors ``format_static_findings`` discipline
(see ``kri.llm.prompts``): returns "" for the degenerate case (single-patch
series or a patch not in the series index) so the surrounding template
stays byte-identical to the pre-WP-S1 form.
"""

from __future__ import annotations

from kri.series.models import SeriesReviewContext

_COVER_LETTER_CAP = 2000


def format_series_context(ctx: SeriesReviewContext, patch_id: str) -> str:
    """Render the series-context block for injection into a per-patch
    review prompt.

    Returns "" when the series has one or fewer patches OR when ``patch_id``
    is not present in ``ctx.patch_index`` (defensive — should not happen in
    production, but keeps the pure-function contract).
    """
    if ctx.total_patches <= 1:
        return ""
    entry = ctx.patch_index.get(patch_id)
    if entry is None:
        return ""

    lines: list[str] = []
    lines.append("## Series Context")
    lines.append(
        f"This patch is part {entry.index} of a {ctx.total_patches}-patch "
        f"series titled \"{ctx.title}\"."
    )
    lines.append("")
    lines.append(
        "Other patches in this series introduce the following files/symbols. "
        "Treat each item below as PRESENT in the series and do NOT flag any "
        "of them as \"missing binding\", \"missing helper\", \"not documented\", "
        "or \"external dependency\":"
    )
    lines.append("")

    reg = ctx.declared_symbols
    lines.extend(_render_files_added(reg.files_added, ctx.patch_index))
    lines.extend(_render_registry(
        "DT compatibles introduced", reg.compatibles, ctx.patch_index,
    ))
    lines.extend(_render_registry(
        "DT properties introduced", reg.dt_properties, ctx.patch_index,
    ))
    lines.extend(_render_registry(
        "C symbols introduced", reg.c_symbols, ctx.patch_index,
    ))
    lines.extend(_render_touch_map(ctx.file_touch_map, ctx.patch_index))
    lines.extend(_render_cover_letter(ctx.cover_letter))

    # Trailing newline for template symmetry with format_static_findings.
    return "\n".join(lines).rstrip() + "\n\n"


def _render_registry(
    heading: str,
    registry: dict[str, str],
    patch_index: dict[str, "object"],
) -> list[str]:
    if not registry:
        return []
    lines = [f"{heading}:"]
    for sym in sorted(registry.keys()):
        pid = registry[sym]
        entry = patch_index.get(pid)
        if entry is None:
            lines.append(f"  - {sym}")
        else:
            lines.append(f"  - {sym} (patch {entry.index}/{entry.total})")  # type: ignore[attr-defined]
    lines.append("")
    return lines


def _render_files_added(
    files_added: dict[str, str],
    patch_index: dict[str, "object"],
) -> list[str]:
    if not files_added:
        return []
    lines = ["Files added:"]
    for path in sorted(files_added.keys()):
        pid = files_added[path]
        entry = patch_index.get(pid)
        if entry is None:
            lines.append(f"  - {path}")
        else:
            subject = entry.subject[:60] if entry.subject else ""  # type: ignore[attr-defined]
            lines.append(
                f"  - {path} (patch {entry.index}/{entry.total} — {subject})"  # type: ignore[attr-defined]
            )
    lines.append("")
    return lines


def _render_touch_map(
    touch_map: dict[str, tuple[str, ...]],
    patch_index: dict[str, "object"],
) -> list[str]:
    multi = {p: ids for p, ids in touch_map.items() if len(ids) > 1}
    if not multi:
        return []
    lines = ["Files touched by this same series (may indicate cross-patch coupling):"]
    for path in sorted(multi.keys()):
        idxs: list[str] = []
        for pid in multi[path]:
            entry = patch_index.get(pid)
            if entry is not None:
                idxs.append(f"{entry.index}/{entry.total}")  # type: ignore[attr-defined]
        idx_str = ", ".join(idxs) if idxs else "-"
        lines.append(f"  - {path} — patches {idx_str}")
    lines.append("")
    return lines


def _render_cover_letter(cover_letter: str | None) -> list[str]:
    if not cover_letter:
        return []
    body = cover_letter[:_COVER_LETTER_CAP]
    suffix = "..." if len(cover_letter) > _COVER_LETTER_CAP else ""
    lines = [
        f"Cover letter (verbatim, first {len(body)} chars):",
        f"{body}{suffix}",
        "",
    ]
    return lines
