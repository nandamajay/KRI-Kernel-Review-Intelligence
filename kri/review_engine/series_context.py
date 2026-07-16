"""SeriesContext builder (WP-9.1a; SPEC.md Sec. 12).

Kernel patch series routinely split one change across N patches: a symbol
declared in patch 1/N and used in patch 3/N is correct kernel practice, not an
error. ``build_series_context`` walks a PatchSeries' patches in sequence
order and accumulates a deterministic, per-sequence record of what each patch
introduces/removes, so downstream reasoning (the cross-patch resolver) can
tell "used before declared in this diff" apart from "declared earlier in the
same series."

Determinism (Constitution Sec. 40): this is a pure function of
``PatchSeries`` (+ its own ``target_kernel_version``) -- no RNG, no
wall-clock, no I/O. Two calls with an equal input series return equal output.

Extraction is intentionally NOT exhaustive -- it needs to catch the common
kernel-patch shapes, not parse C/DT/Kconfig grammars fully:
  - C function definitions: a top-level ``+<ret-type> <name>(`` in an added
    line is treated as introducing ``<name>``; a corresponding removed line
    is treated as removing it.
  - C function calls: any ``<name>(`` in an added line (excluding the
    introducing line itself and common C keywords like ``if``/``for``) is
    treated as a *use* of ``<name>``, feeding the cross-patch resolver's
    bisectability check.
  - New/deleted files: ``diff --git a/X b/Y`` header followed by
    ``new file mode`` / ``deleted file mode``.
  - DT compatibles: ``.compatible = "..."`` (or ``compatible = "...";`` in a
    .dts/.dtsi) in an added line.
  - Kconfig symbols: ``+config <SYMBOL>`` in an added line of a file named
    ``Kconfig*``.
  - Kbuild edits: any added/removed line in a file named ``Makefile`` or
    ``Kconfig*`` that isn't already captured as a new Kconfig symbol.
  - MAINTAINERS deltas: added lines in a file named exactly ``MAINTAINERS``.
"""

from __future__ import annotations

import re

from kri.common.models import Patch, PatchSeries, SeriesContext

_FUNC_DEF_RE = re.compile(r"^\+\w[\w\s*]*?\b(\w+)\s*\(")
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
_NEW_FILE_RE = re.compile(r"^new file mode\b")
_DELETED_FILE_RE = re.compile(r"^deleted file mode\b")
_DT_COMPATIBLE_RE = re.compile(r'^\+.*\bcompatible\s*=\s*"([^"]+)"')
_KCONFIG_SYMBOL_RE = re.compile(r"^\+config\s+(\w+)")
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_C_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "return", "sizeof", "defined", "do", "else",
})


def _added_lines(diff: str) -> list[str]:
    return [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def _removed_lines(diff: str) -> list[str]:
    return [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]


def _is_kconfig_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("Kconfig")


def _is_makefile(path: str) -> bool:
    return path.rsplit("/", 1)[-1] == "Makefile"


def _current_file_from_diff_headers(diff: str) -> dict[int, str]:
    """Map each line index to the file path whose hunk it belongs to, so
    per-line extraction (Kconfig/Makefile/MAINTAINERS) can be scoped
    correctly for a multi-file patch."""
    lines = diff.splitlines()
    current = ""
    mapping: dict[int, str] = {}
    for i, line in enumerate(lines):
        m = _DIFF_HEADER_RE.match(line)
        if m:
            current = m.group(2)
        mapping[i] = current
    return mapping


def build_series_context(series: PatchSeries) -> SeriesContext:
    """Pure function: build a SeriesContext from a PatchSeries.

    Iterates ``series.patches`` in sequence order (matching the sub-commit-1
    ordering fix) so later patches never influence earlier sequence numbers'
    entries, keeping the accumulation itself order-independent of Python dict
    iteration/hash ordering (all dict values are sets, sorted at read time by
    callers as needed)."""
    ctx = SeriesContext(
        series_id=series.series_id,
        target_kernel_version=series.target_kernel_version,
    )

    for patch in sorted(series.patches, key=lambda p: (p.sequence or 0, p.patch_id)):
        seq = patch.sequence
        _accumulate_patch(ctx, seq, patch)

    return ctx


def _accumulate_patch(ctx: SeriesContext, seq: int, patch: Patch) -> None:
    diff = patch.diff
    lines = diff.splitlines()
    file_of_line = _current_file_from_diff_headers(diff)

    introduced: set[str] = set()
    removed: set[str] = set()
    used: set[str] = set()
    new_files: set[str] = set()
    deleted_files: set[str] = set()
    new_kconfig: set[str] = set()
    new_compatibles: set[str] = set()
    maintainers_lines: list[str] = []
    kbuild_edits: set[str] = set()

    for i, line in enumerate(lines):
        current_file = file_of_line.get(i, "")

        header = _DIFF_HEADER_RE.match(line)
        if header:
            continue
        if _NEW_FILE_RE.match(line):
            new_files.add(current_file)
            continue
        if _DELETED_FILE_RE.match(line):
            deleted_files.add(current_file)
            continue

        if line.startswith("+") and not line.startswith("+++"):
            func_m = _FUNC_DEF_RE.match(line)
            def_name = func_m.group(1) if func_m else None
            if def_name:
                introduced.add(def_name)

            for call_m in _CALL_RE.finditer(line[1:]):
                name = call_m.group(1)
                if name == def_name or name in _C_KEYWORDS:
                    continue
                used.add(name)

            dt_m = _DT_COMPATIBLE_RE.match(line)
            if dt_m:
                new_compatibles.add(dt_m.group(1))

            if current_file and _is_kconfig_file(current_file):
                kconfig_m = _KCONFIG_SYMBOL_RE.match(line)
                if kconfig_m:
                    new_kconfig.add(kconfig_m.group(1))
                else:
                    kbuild_edits.add(current_file)
            elif current_file and _is_makefile(current_file):
                kbuild_edits.add(current_file)

            if current_file == "MAINTAINERS":
                maintainers_lines.append(line[1:].strip())

        elif line.startswith("-") and not line.startswith("---"):
            func_m = _FUNC_DEF_RE.match("+" + line[1:])
            if func_m:
                removed.add(func_m.group(1))

    if introduced:
        ctx.introduced_symbols[seq] = introduced
    if removed:
        ctx.removed_symbols[seq] = removed
    if used:
        ctx.used_symbols[seq] = used
    if new_files:
        ctx.new_files[seq] = new_files
    if deleted_files:
        ctx.deleted_files[seq] = deleted_files
    if new_kconfig:
        ctx.new_kconfig_symbols[seq] = new_kconfig
    if new_compatibles:
        ctx.new_dt_compatibles[seq] = new_compatibles
    if maintainers_lines:
        ctx.maintainers_deltas[seq] = maintainers_lines
    if kbuild_edits:
        ctx.kbuild_edits[seq] = kbuild_edits


__all__ = ["build_series_context"]
