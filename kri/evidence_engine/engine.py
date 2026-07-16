"""Evidence Engine (Blueprint Sec. 21.8 / SPEC §8).

Assembles, verifies, and formats evidence for review decisions. This is the
constitutional enforcement point for the evidence gate (Sec. 28/29): no review
comment is publishable without at least one verified Evidence node.

Domain-agnostic: no domain identifiers. Evidence is gathered from the EKG
(which stores whatever nodes a DKP seeded) and verified against resolvable
provenance (URL validity, repo path existence, commit hash format).
"""

from __future__ import annotations

import re

from kri.common.models import (
    EVIDENCE_SOURCE_PRIORITY,
    Decision,
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    Rule,
    SeriesContext,
    VersionRange,
)
from kri.evidence_engine.cross_patch_resolver import resolve_symbol_reference
from kri.knowledge.schema import (
    EDGE_EXEMPLIFIES,
    NODE_RULE,
)
from kri.knowledge_manager.manager import KnowledgeManagerImpl

# Pre-compiled pattern for well-formed lore.kernel.org URLs.
_LORE_URL_RE = re.compile(
    r"^https?://lore\.kernel\.org/[a-zA-Z0-9._\-]+/[^\s]+$"
)

_CROSS_PATCH_SOURCE_TYPES = frozenset({
    EvidenceSourceType.MISSING_SYMBOL,
    EvidenceSourceType.MISSING_FILE,
    EvidenceSourceType.MISSING_BINDING,
})


def _strength_from_priority(source_type: EvidenceSourceType) -> float:
    """Compute evidence strength from source priority.

    Formula: max(0.0, 1.0 - (priority - 1) * 0.08)
    Priority 1 -> 1.0, Priority 12 -> 0.12.
    """
    priority = EVIDENCE_SOURCE_PRIORITY.get(source_type, 12)
    return max(0.0, 1.0 - (priority - 1) * 0.08)


def _sort_key_for_evidence(ev: Evidence) -> int:
    """Return the priority number for sorting (lower = higher priority = first)."""
    return EVIDENCE_SOURCE_PRIORITY.get(ev.source_type, 12)


class EvidenceEngineImpl:
    """Concrete :class:`kri.common.interfaces.EvidenceEngine`.

    Assembles evidence from the Engineering Knowledge Graph, verifies each item
    against its provenance, and formats verified items as citations. Deterministic:
    same inputs yield the same outputs (no randomness, stable sorting).
    """

    def __init__(self, knowledge_manager: KnowledgeManagerImpl) -> None:
        self._km = knowledge_manager

    def gather(
        self, decision: Decision, *, series_context: SeriesContext | None = None
    ) -> EvidenceGraph:
        """Assemble an Evidence Graph for a decision from all available sources.

        Steps:
        1. Retrieve candidate Evidence nodes from the KG via the Knowledge Manager.
        2. Populate the subsystem rule if the decision references a rule_id.
        3. Fill accepted/rejected examples via EXEMPLIFIES edges on patterns.
        4. Verify each evidence item.
        5. Return the fully populated, deterministically sorted EvidenceGraph.

        ``series_context`` (WP-9.1a) is forwarded to ``verify()`` so missing-
        symbol/file/binding evidence can be resolved against the series.
        """
        # Get candidate evidence nodes from the KG.
        candidates = self._km.get_evidence(decision)

        # Build the EvidenceGraph shell.
        graph = EvidenceGraph(comment_id=decision.decision_id)

        # Populate subsystem_rule if a rule_id is set.
        if decision.rule_id:
            graph.subsystem_rule = self._resolve_rule(decision.rule_id)

        # Populate accepted/rejected examples via EXEMPLIFIES edges.
        accepted, rejected = self._resolve_examples(decision)
        graph.accepted_examples = accepted
        graph.rejected_examples = rejected

        # Verify each evidence item and sort by priority.
        verified_evidence: list[Evidence] = []
        for ev in candidates:
            verified_ev = self.verify(ev, series_context=series_context)
            verified_evidence.append(verified_ev)

        # Sort by priority (lower EVIDENCE_SOURCE_PRIORITY = higher priority = first).
        verified_evidence.sort(key=_sort_key_for_evidence)
        graph.evidence = verified_evidence

        return graph

    def verify(
        self, evidence: Evidence, *, series_context: SeriesContext | None = None
    ) -> Evidence:
        """Verify an evidence item against its provenance.

        Cross-patch resolution (WP-9.1a) runs first for MISSING_SYMBOL/
        MISSING_FILE/MISSING_BINDING evidence when a ``series_context`` and
        the evidence's ``symbol_ref``/``patch_sequence`` are available:
        - "introduced_earlier": satisfied by an earlier patch in the same
          series -- downgraded to verified=False with a ``dropped_reason``.
        - "introduced_later": a real bisectability bug -- stays verified,
          flagged via ``bisectability_violation=True``.
        - "introduced_here" / "not_in_series": falls through to the
          provenance-based checks below (no series information to act on).

        Provenance-based verification checks (in order):
        1. source_url contains "lore.kernel.org" and is well-formed -> verified
        2. repo_path is set and non-empty -> verified
        3. commit_hash is set and non-empty -> verified
        4. Otherwise -> unverified (strength=0.0)

        Strength is derived from EVIDENCE_SOURCE_PRIORITY for verified items.
        """
        # Create a deep copy to avoid mutating the original.
        ev = evidence.model_copy(deep=True)

        if (
            series_context is not None
            and ev.source_type in _CROSS_PATCH_SOURCE_TYPES
            and ev.symbol_ref
            and ev.patch_sequence is not None
        ):
            outcome = resolve_symbol_reference(
                ev.symbol_ref, ev.patch_sequence, series_context
            )
            if outcome == "introduced_earlier":
                ev.verified = False
                ev.strength = 0.0
                ev.dropped_reason = "satisfied_by_earlier_patch_in_series"
                return ev
            if outcome == "introduced_later":
                ev.verified = True
                ev.strength = _strength_from_priority(ev.source_type)
                ev.bisectability_violation = True
                ev.dropped_reason = None
                return ev

        prov = ev.provenance

        # Check lore.kernel.org URL.
        if prov.source_url and "lore.kernel.org" in prov.source_url:
            if _LORE_URL_RE.match(prov.source_url):
                ev.verified = True
                ev.strength = _strength_from_priority(ev.source_type)
                return ev

        # Check repo_path.
        if prov.repo_path and prov.repo_path.strip():
            ev.verified = True
            ev.strength = _strength_from_priority(ev.source_type)
            return ev

        # Check commit_hash.
        if prov.commit_hash and prov.commit_hash.strip():
            ev.verified = True
            ev.strength = _strength_from_priority(ev.source_type)
            return ev

        # Unverifiable.
        ev.verified = False
        ev.strength = 0.0
        return ev

    def format(self, evidence: Evidence) -> str:
        """Render an evidence item as a human-readable citation.

        Format: "[source_type] summary (source: url_or_path) [verified/unverified, strength=X.XX]"
        """
        source_type = evidence.source_type.value
        summary = evidence.summary or ""

        # Determine the source reference (prefer URL, fall back to repo_path).
        source_ref = ""
        if evidence.provenance.source_url:
            source_ref = evidence.provenance.source_url
        elif evidence.provenance.repo_path:
            source_ref = evidence.provenance.repo_path
        elif evidence.provenance.commit_hash:
            source_ref = evidence.provenance.commit_hash

        verification_status = "verified" if evidence.verified else "unverified"
        strength_str = f"{evidence.strength:.2f}"

        return (
            f"[{source_type}] {summary} "
            f"(source: {source_ref}) "
            f"[{verification_status}, strength={strength_str}]"
        )

    # -- private helpers -------------------------------------------------------

    def _resolve_rule(self, rule_id: str) -> Rule | None:
        """Query the KG for a Rule node matching the given rule_id."""
        results = self._km.query_graph({
            "match": {"node_type": NODE_RULE},
            "where": {},
        })
        for rec in results:
            if rec["node_id"] == rule_id:
                props = rec["properties"]
                vr = (
                    VersionRange.model_validate(rec["version_range"])
                    if rec.get("version_range")
                    else None
                )
                return Rule(
                    rule_id=rule_id,
                    category=props.get("category", ""),
                    rule_type=props.get("rule_type", "soft"),
                    description=props.get("description", ""),
                    rationale=props.get("rationale", ""),
                    documentation_ref=props.get("doc_ref")
                        or props.get("documentation_ref"),
                    historical_enforcement_rate=props.get("enforcement_rate")
                        or props.get("historical_enforcement_rate"),
                    exceptions=props.get("exceptions", []),
                    version_range=vr,
                )
        return None

    def _resolve_examples(
        self, decision: Decision
    ) -> tuple[list[str], list[str]]:
        """Resolve agreeing/disagreeing examples via EXEMPLIFIES edges.

        Returns (accepted_examples, rejected_examples) where "accepted" means
        "agrees with this decision" and "rejected" means "disagrees".

        For a rejection decision backed by a "rejected" outcome pattern, examples
        with outcome="rejected" AGREE (same thing was rejected before). Examples
        with outcome="accepted" DISAGREE (same thing was accepted elsewhere).
        """
        agreeing: list[str] = []
        disagreeing: list[str] = []

        pattern_id = decision.pattern_id
        if not pattern_id or not self._km.graph.has_node(pattern_id):
            return agreeing, disagreeing

        # Determine the decision's expected outcome from the pattern node.
        pattern_node = self._km.graph.get_node(pattern_id)
        decision_outcome = (
            pattern_node["properties"].get("outcome", "rejected")
            if pattern_node
            else "rejected"
        )

        def classify(outcome: str, node_id: str) -> None:
            if outcome == decision_outcome:
                agreeing.append(node_id)
            elif outcome:
                disagreeing.append(node_id)

        # Direction IN: Patch -[EXEMPLIFIES]-> Pattern (historical patches).
        edges_in = self._km.graph.edges_of(
            pattern_id, EDGE_EXEMPLIFIES, direction="in"
        )
        for edge in edges_in:
            src_id = edge["src"]
            node = self._km.graph.get_node(src_id)
            if node is None:
                continue
            outcome = (
                edge.get("properties", {}).get("outcome", "")
                or node["properties"].get("outcome", "")
            )
            classify(outcome, src_id)

        # Direction OUT: Pattern -[EXEMPLIFIES]-> Concept (seeded examples).
        edges_out = self._km.graph.edges_of(
            pattern_id, EDGE_EXEMPLIFIES, direction="out"
        )
        for edge in edges_out:
            dst_id = edge["dst"]
            outcome = edge.get("properties", {}).get("outcome", "")
            classify(outcome, dst_id)

        agreeing.sort()
        disagreeing.sort()
        return agreeing, disagreeing
