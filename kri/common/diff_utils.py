"""Pure diff-parsing utilities for extracting hunk citations.

All functions are deterministic pure functions of their inputs — no I/O,
no RNG, no wall-clock (Constitution Sec. 40).
"""

from __future__ import annotations

from .models import HunkCitation, Patch


def extract_hunk_citation(
    patch: Patch, match_line_text: str, *, context: int = 2
) -> HunkCitation | None:
    """Find the added line matching ``match_line_text`` and return a HunkCitation
    with ±``context`` lines of surrounding added lines.

    Returns None if ``match_line_text`` is not found among the patch's added lines.
    Only considers lines starting with ``+`` (but not ``+++`` file headers).

    The returned ``verbatim_lines`` include the matched line plus up to ``context``
    added lines before and after it. ``line_start``/``line_end`` are 1-indexed
    positions within the sequence of added lines.
    """
    if not patch.diff or not match_line_text:
        return None

    added_lines: list[str] = []
    current_file: str | None = None
    file_for_line: list[str] = []

    for raw_line in patch.diff.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:]
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            content = raw_line[1:]
            added_lines.append(content)
            file_for_line.append(current_file or "")

    if not added_lines:
        return None

    match_idx: int | None = None
    for i, line in enumerate(added_lines):
        if match_line_text in line:
            match_idx = i
            break

    if match_idx is None:
        return None

    start = max(0, match_idx - context)
    end = min(len(added_lines), match_idx + context + 1)

    verbatim = added_lines[start:end]
    file = file_for_line[match_idx]

    return HunkCitation(
        patch_id=patch.patch_id,
        file=file,
        line_start=start + 1,
        line_end=end,
        verbatim_lines=verbatim,
    )
