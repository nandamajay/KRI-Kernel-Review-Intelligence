"""Process Rules Manager (Generic Runtime, not a DKP).

Domain-agnostic upstream kernel process/etiquette checks that apply to every
patch regardless of subsystem (Domain Isolation, Constitution Sec. 9):
missing Signed-off-by:, malformed Fixes: tags, and changelog notes placed
above the '---' tearline. These run against already-parsed
``Patch.commit_message``/``subject`` fields; no raw mbox re-parsing and no
kernel-tree access is required at check time.

Evidence is pre-attached directly onto each Decision's ``evidence_graph``
rather than seeded into the Engineering Knowledge Graph, since this is not a
DKP and owns no Rule/Pattern nodes. ``KnowledgeManagerImpl.get_evidence()``
explicitly merges evidence already attached to ``decision.evidence_graph``
into what it returns, and ``EvidenceEngineImpl.verify()`` marks
``Documentation/``-cited evidence as verified via ``repo_path`` — satisfying
the constitutional evidence gate (Sec. 28) without pattern/DKP machinery.
Because no Rule node is seeded into the KG, the ``subsystem_evidence`` /
``historical_agreement`` / ``code_similarity`` confidence factors stay 0.0 for
these decisions, so confidence typically lands in the SPECULATIVE/UNKNOWN
band. That is intentional and conservative (Constitution Sec. 31: "unknown
beats wrong") rather than a bug — these findings surface in the Review
Report regardless of publishability, closing the "zero etiquette coverage"
gap without fabricating grounding this module does not have.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from kri.common.models import (
    Decision,
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    Patch,
    PatchSeries,
    Provenance,
    ReasoningLayer,
    SeriesContext,
    Severity,
)
from kri.knowledge.version import make_range


def _doc(repo_path: str) -> Provenance:
    return Provenance(
        repo_path=repo_path,
        version_or_commit="v6.6",
        transformation_history=["process_rules.manager"],
        source_confidence=1.0,
    )


# Small local rule table (mirrors the shape of a DKP's own raw-rule tables,
# minus the DKP-only fields) -- these are never seeded as NODE_RULE graph
# nodes; rule_id here is purely a citation/grouping key.
_PROCESS_RULES: dict[str, dict[str, Any]] = {
    "process-signed-off-by": {
        "description": (
            "Every patch must carry a Signed-off-by: trailer from its author."
        ),
        "doc_ref": "Documentation/process/submitting-patches.rst",
    },
    "process-fixes-tag-format": {
        "description": (
            "A Fixes: tag must reference a commit as a 12+ hex-char SHA "
            'followed by a parenthesized, quoted one-line summary, e.g. '
            'Fixes: 54a4f0239f2e ("...").'
        ),
        "doc_ref": "Documentation/process/submitting-patches.rst",
    },
    "process-changelog-placement": {
        "description": (
            "Per-version changelog notes (what changed between v1/v2/...) "
            "belong after the '---' tearline, not in the permanent commit "
            "message."
        ),
        "doc_ref": "Documentation/process/submitting-patches.rst",
    },
}

_SOB_RE = re.compile(r"(?m)^Signed-off-by:\s*\S")
_FIXES_LINE_RE = re.compile(r"(?m)^Fixes:\s*(.+)$")
_FIXES_VALID_RE = re.compile(r'^[0-9a-fA-F]{12,40}\s+\("[^"]+"\)\s*$')
_CHANGELOG_RE = re.compile(
    r"(?im)^(changes?\s+(in|since|from)\s+v\d+|v\d+\s*[:\-]\s)"
)


class ProcessRulesConfig:
    """Configuration for :class:`ProcessRulesManagerImpl`.

    ``kernel_path`` is resolved the same way ``web.app._default_maintainers``
    resolves ``KRI_KERNEL_PATH`` -- kept for parity with other managers (e.g.
    :mod:`kri.static_analysis.manager`) and future citation/version pinning.
    None of the checks below read the kernel tree; they operate purely on
    already-parsed ``Patch`` fields.
    """

    def __init__(self, kernel_path: str | Path | None = None) -> None:
        if kernel_path is None:
            env = os.environ.get("KRI_KERNEL_PATH")
            kernel_path = Path(env) if env else None
        elif isinstance(kernel_path, str):
            kernel_path = Path(kernel_path)
        self.kernel_path: Path | None = (
            kernel_path if kernel_path and kernel_path.exists() else None
        )


class ProcessRulesManagerImpl:
    """Deterministic upstream kernel process/etiquette checker.

    Never raises: every check is a pure regex/substring scan of already-parsed
    ``Patch.commit_message``/``subject`` text, so there is no external
    tool/tree dependency to degrade gracefully around (unlike
    :class:`kri.static_analysis.manager.StaticAnalysisManagerImpl`).
    """

    def __init__(self, config: ProcessRulesConfig | None = None) -> None:
        self._config = config or ProcessRulesConfig()

    def check(self, patch: Patch, series: PatchSeries) -> list[Decision]:
        """Run all process/etiquette checks against a single patch.

        Returns Decisions sorted by ``decision_id`` for determinism."""
        decisions: list[Decision] = []
        decisions.extend(self._check_signed_off_by(patch, series))
        decisions.extend(self._check_fixes_tag(patch, series))
        decisions.extend(self._check_changelog_placement(patch, series))
        decisions.sort(key=lambda d: d.decision_id)
        return decisions

    # -- individual checks ---------------------------------------------------

    def _check_signed_off_by(
        self, patch: Patch, series: PatchSeries
    ) -> list[Decision]:
        if _SOB_RE.search(patch.commit_message):
            return []
        statement = (
            f'Patch "{patch.subject}" is missing a Signed-off-by: trailer. '
            "Every patch must carry the author's Signed-off-by: line "
            "(Developer's Certificate of Origin) before it can be merged."
        )
        return [
            self._build_decision(
                "process-signed-off-by",
                patch,
                series,
                statement,
                Severity.BLOCKER,
            )
        ]

    def _check_fixes_tag(
        self, patch: Patch, series: PatchSeries
    ) -> list[Decision]:
        decisions: list[Decision] = []
        for i, m in enumerate(_FIXES_LINE_RE.finditer(patch.commit_message)):
            tag_body = m.group(1).strip()
            if _FIXES_VALID_RE.match(tag_body):
                continue
            statement = (
                f'Fixes: tag "{tag_body}" does not match the required format '
                '"<12+ hex char SHA> (\\"<one-line summary>\\")", e.g. '
                'Fixes: 54a4f0239f2e ("KVM: MMU: make kvm_mmu_zap_page() '
                'return the number of pages it actually freed").'
            )
            decisions.append(
                self._build_decision(
                    "process-fixes-tag-format",
                    patch,
                    series,
                    statement,
                    Severity.WARNING,
                    extra_doc_refs=("Documentation/process/5.Posting.rst",),
                    suffix=str(i),
                )
            )
        return decisions

    def _check_changelog_placement(
        self, patch: Patch, series: PatchSeries
    ) -> list[Decision]:
        m = _CHANGELOG_RE.search(patch.commit_message)
        if not m:
            return []
        statement = (
            f'Patch "{patch.subject}" appears to include per-version '
            f'changelog notes ("{m.group(0).strip()}") inside the permanent '
            "commit message. Version changelogs describing what changed "
            "between revisions belong after the '---' tearline, not above it."
        )
        return [
            self._build_decision(
                "process-changelog-placement",
                patch,
                series,
                statement,
                Severity.INFO,
            )
        ]

    # -- shared Decision construction ----------------------------------------

    @staticmethod
    def _build_decision(
        rule_id: str,
        patch: Patch,
        series: PatchSeries,
        statement: str,
        severity: Severity,
        *,
        extra_doc_refs: tuple[str, ...] = (),
        suffix: str | None = None,
    ) -> Decision:
        rule = _PROCESS_RULES[rule_id]
        decision_id = f"process:{rule_id}:{patch.patch_id}"
        if suffix is not None:
            decision_id = f"{decision_id}:{suffix}"

        doc_refs: list[str] = []
        for ref in (rule["doc_ref"], *extra_doc_refs):
            if ref and ref not in doc_refs:
                doc_refs.append(ref)

        evidence = [
            Evidence(
                evidence_id=f"ev:{decision_id}:{idx}",
                source_type=EvidenceSourceType.DOCUMENTATION,
                summary=f"{rule['description']} (see {ref}).",
                provenance=_doc(ref),
                version_range=make_range("6.1"),
                verified=False,
                strength=0.0,
            )
            for idx, ref in enumerate(doc_refs)
        ]

        return Decision(
            decision_id=decision_id,
            series_id=series.series_id,
            patch_id=patch.patch_id,
            layer=ReasoningLayer.STRUCTURAL,
            category="process",
            severity=severity,
            location=None,
            statement=statement,
            rule_id=rule_id,
            pattern_id=None,
            evidence_graph=EvidenceGraph(comment_id=decision_id, evidence=evidence),
            confidence=None,
        )


class ProcessEtiquettePlugin:
    """Thin ``ReasoningPlugin``-protocol adapter around
    :class:`ProcessRulesManagerImpl`.

    Always runs regardless of DKP/subsystem (``trigger="always"``) --
    etiquette checks apply to every patch. Passed to
    ``ReviewEngineImpl.review()`` via ``extra_plugins``, never through
    ``DomainKnowledgePackage.reasoning_plugins()``."""

    def __init__(self, manager: ProcessRulesManagerImpl) -> None:
        self._manager = manager

    @property
    def plugin_id(self) -> str:
        return "process:etiquette"

    @property
    def trigger(self) -> str:
        return "always"

    @property
    def layer(self) -> ReasoningLayer:
        return ReasoningLayer.STRUCTURAL

    def applies(self, patch: Patch, series: PatchSeries) -> bool:
        return True

    def evaluate(
        self,
        patch: Patch,
        series: PatchSeries,
        *,
        series_context: SeriesContext | None = None,
    ) -> list[Decision]:
        return self._manager.check(patch, series)


__all__ = [
    "ProcessRulesConfig",
    "ProcessRulesManagerImpl",
    "ProcessEtiquettePlugin",
]
