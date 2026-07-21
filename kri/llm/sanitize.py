"""Central output sanitizer for KRI LLM-generated text.

This module is the single trust boundary between LLM output and every public
surface KRI produces (lore reply, web UI, JSON API, future consumers).

No synthetic upstream trailer tag may escape through any path.  All Pydantic
models that hold LLM-generated string fields apply strip_trailers() at
validation time, so sanitization is automatic regardless of where the field
is consumed.
"""

from __future__ import annotations

import re

# Canonical upstream trailer tags that KRI must never synthesize.
# Covers real tags, placeholder forms (Reviewed-by: [Reviewer],
# Acked-by: <Name>), and the Fixes: shortlog form.
_TRAILER_RE = re.compile(
    r"^\s*("
    r"Reviewed-by"
    r"|Acked-by"
    r"|Tested-by"
    r"|Signed-off-by"
    r"|Co-developed-by"
    r"|Reported-by"
    r"|Suggested-by"
    r"|Fixes"
    r")\s*:",
    re.IGNORECASE,
)


def strip_trailers(text: str) -> str:
    """Remove upstream trailer lines from LLM-generated text.

    Filters every line that matches a known trailer tag prefix, regardless of
    whether it carries a real name, a placeholder ([Reviewer], <Name>), or
    any other value.  Returns the cleaned text with leading/trailing whitespace
    stripped.
    """
    if not text:
        return text
    cleaned = [line for line in text.splitlines() if not _TRAILER_RE.match(line)]
    return "\n".join(cleaned).strip()


def strip_trailers_list(items: list[str]) -> list[str]:
    """Apply strip_trailers() to each element of a string list."""
    return [strip_trailers(item) for item in items]
