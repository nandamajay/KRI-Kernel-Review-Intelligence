"""Maintainer-idiomatic report template (WP-9.2a Sub-commit 4).

Renders a Decision into a format that mimics how real kernel maintainers write
inline review comments on lore:

    > offending code quoted from the patch
    ^
    Concern statement.

    Suggested fix:
    ```c
    corrected code snippet
    ```

    Rationale: why this is the correct approach.

    Precedent: <link or reference to accepted patch>

All rendering is deterministic (no wall-clock, no RNG).
"""

from __future__ import annotations

from kri.common.models import Decision


def render_maintainer_comment(decision: Decision) -> str:
    """Render a publishable Decision as a maintainer-idiomatic review comment.

    Returns a multi-line string in the style real maintainers use on lore.
    Falls back gracefully when optional fields (hunk_citation,
    alternative_recommendation, alternative_precedents) are absent.
    """
    parts: list[str] = []
    eg = decision.evidence_graph

    # 1. Quoted hunk (if available)
    if eg and eg.hunk_citation:
        hunk = eg.hunk_citation
        for line in hunk.verbatim_lines:
            parts.append(f"> {line}")
        parts.append("")

    # 2. Location pointer (with line range from HunkCitation when available)
    if eg and eg.hunk_citation and decision.location:
        hunk = eg.hunk_citation
        if hunk.line_start == hunk.line_end:
            parts.append(f"At {decision.location}:{hunk.line_start}:")
        else:
            parts.append(f"At {decision.location}:{hunk.line_start}-{hunk.line_end}:")
        parts.append("")
    elif decision.location:
        parts.append(f"At {decision.location}:")
        parts.append("")

    # 3. Concern statement
    parts.append(decision.statement)
    parts.append("")

    # 4. Suggested fix (if structured recommendation available)
    if eg and eg.alternative_recommendation:
        rec = eg.alternative_recommendation
        parts.append("Suggested fix:")
        parts.append(f"```{rec.language}")
        parts.append(rec.snippet)
        parts.append("```")
        parts.append("")
        if rec.rationale:
            parts.append(f"Rationale: {rec.rationale}")
            parts.append("")

    # 5. Precedent references (if available)
    if eg and eg.alternative_precedents:
        if len(eg.alternative_precedents) == 1:
            parts.append(f"Precedent: {eg.alternative_precedents[0]}")
        else:
            parts.append("Precedents:")
            for prec in eg.alternative_precedents:
                parts.append(f"  - {prec}")
        parts.append("")

    # 6. Confidence note (if below LIKELY threshold)
    if decision.confidence and decision.confidence.score < 0.80:
        parts.append(
            f"[Confidence: {decision.confidence.level.value} "
            f"({decision.confidence.score:.2f})]"
        )
        parts.append("")

    # 7. Evidence citation (first verified evidence item)
    if eg and eg.evidence:
        verified = [e for e in eg.evidence if e.verified]
        if verified:
            ev = verified[0]
            ref = ev.provenance.source_url or ev.provenance.repo_path or ""
            if ref:
                parts.append(f"Ref: {ref}")
                parts.append("")

    # Strip trailing blank line
    while parts and parts[-1] == "":
        parts.pop()

    return "\n".join(parts)
