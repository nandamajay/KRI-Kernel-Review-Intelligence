"""Static-finding -> EKG runtime Evidence normalization (Sprint-2 light-touch).

The Sprint-1 Static Analysis Manager already produces normalized ``StaticFinding``
dicts and gracefully degrades sparse/smatch/coccinelle when the binaries are absent
(``degraded=True`` findings). This module converts a ``StaticFinding`` into an
:class:`Evidence` node so runtime tool output feeds the ``runtime_evidence``
confidence factor uniformly with the rest of the EKG.

We do NOT re-implement any tool runner (that is Sprint-1's manager). We only bridge
its output into the knowledge layer. Domain-agnostic; deterministic. Degraded
findings become *unverified*, zero-strength evidence so a missing tool can never
inflate confidence (Constitution: conservative, no fabricated evidence).
"""

from __future__ import annotations

import hashlib
from typing import Any

from kri.common.models import (
    Evidence,
    EvidenceSourceType,
    Provenance,
    VersionRange,
)

# checkpatch/sparse severities -> a coarse evidence strength when NOT degraded.
_SEVERITY_STRENGTH = {"blocker": 0.9, "warning": 0.6, "info": 0.3}


def finding_to_evidence(
    finding: dict[str, Any], version_range: VersionRange | None = None
) -> Evidence:
    """Convert a normalized ``StaticFinding`` dict into an :class:`Evidence`.

    Deterministic id derived from the finding's stable fields. Degraded findings
    (tool unavailable) are marked ``verified=False, strength=0.0`` — they document
    that a tool did not run, never that code is clean/broken."""
    tool = str(finding.get("tool", "unknown"))
    file = finding.get("file")
    line = int(finding.get("line", 0) or 0)
    category = str(finding.get("category", ""))
    severity = str(finding.get("severity", "info"))
    message = str(finding.get("message", ""))
    degraded = bool(finding.get("degraded", False))
    patch_id = finding.get("patch_id")

    key = f"{tool}|{file}|{line}|{category}|{message}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    evidence_id = f"static:{tool}:{digest}"

    prov = Provenance(
        repo_path=f"{file}:{line}" if file else None,
        version_or_commit=str(patch_id) if patch_id else None,
        transformation_history=[f"{tool}.run", "static.normalize", "finding_to_evidence"],
        source_confidence=0.0 if degraded else 1.0,
    )
    summary = (
        f"[{tool}] tool unavailable: {message}"
        if degraded
        else f"[{tool}/{severity}] {message} ({file}:{line})"
    )
    return Evidence(
        evidence_id=evidence_id,
        source_type=EvidenceSourceType.STATIC_ANALYSIS,
        summary=summary,
        provenance=prov,
        version_range=version_range,
        # Verification is the Evidence Engine's job (Sprint-3); a degraded finding
        # is never verifiable, so we keep it False + zero strength here.
        verified=False,
        strength=0.0 if degraded else _SEVERITY_STRENGTH.get(severity, 0.3),
    )


def findings_to_evidence(
    findings: list[dict[str, Any]], version_range: VersionRange | None = None
) -> list[Evidence]:
    """Convert + de-duplicate a list of findings into Evidence (stable order)."""
    by_id: dict[str, Evidence] = {}
    for f in findings:
        ev = finding_to_evidence(f, version_range)
        by_id[ev.evidence_id] = ev
    return [by_id[k] for k in sorted(by_id)]


__all__ = ["finding_to_evidence", "findings_to_evidence"]
