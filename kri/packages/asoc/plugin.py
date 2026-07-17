"""ASoC DomainKnowledgePackage (Sprint-2 implementation).

Concrete :class:`kri.common.interfaces.DomainKnowledgePackage` for the ALSA
System-on-Chip (ASoC) subsystem, rooted at ``sound/soc/``.

This is the ONLY module tree permitted to reference ASoC/snd_soc identifiers
(Domain Isolation, Constitution Sec. 9). The Generic Runtime discovers this class
via the ``kri.dkp`` entry point and interacts with it ONLY through the protocol —
it never imports this package by name.

Knowledge sources (all real, all cited — no hallucinated knowledge):
 * kernel Documentation/sound/soc/*.rst and include/sound/soc*.h (Linux v6.6);
 * cached lore review threads under ``data/lore_cache/`` (Mark Brown /
   Krzysztof Kozlowski on the Nuvoton NAU83G60 series).
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from kri.common.models import (
    Evidence,
    EvidenceSourceType,
    KernelVersion,
    Provenance,
    Rule,
)
from kri.knowledge.schema import (
    EDGE_DEFINED_IN,
    EDGE_DOCUMENTED_BY,
    EDGE_EXEMPLIFIES,
    EDGE_GOVERNS,
    EDGE_MAINTAINS,
    EDGE_SUPERSEDES,
    EDGE_SUPPORTS,
    NODE_API,
    NODE_CONCEPT,
    NODE_DOCUMENT,
    NODE_MAINTAINER,
    NODE_PATTERN,
    NODE_RULE,
    NODE_SUBSYSTEM,
)
from kri.knowledge.version import make_range, range_contains

from .knowledge import (
    ASOC_MAINTAINERS,
    ASOC_ROOT,
    ASOC_SUBSYSTEM_ID,
    CANONICAL_RECOMMENDATIONS,
    build_apis,
    build_patterns,
    build_rules,
)
from .plugins import build_reasoning_plugins

if TYPE_CHECKING:
    from kri.knowledge_manager.manager import KnowledgeManagerImpl

_MANIFEST_PATH = Path(__file__).with_name("manifest.yaml")


class AsocDomainKnowledgePackage:
    """ASoC DKP. Implements :class:`kri.common.interfaces.DomainKnowledgePackage`."""

    def __init__(self, manifest_path: Path | None = None) -> None:
        self._manifest_path = manifest_path or _MANIFEST_PATH
        self._manifest_cache: dict[str, Any] | None = None

    # --- identity / manifest --------------------------------------------------
    @property
    def name(self) -> str:
        return str(self.manifest().get("package", {}).get("name", "asoc"))

    @property
    def version(self) -> str:
        return str(self.manifest().get("package", {}).get("version", "0.0.0"))

    def manifest(self) -> dict[str, Any]:
        if self._manifest_cache is None:
            if self._manifest_path.exists():
                self._manifest_cache = yaml.safe_load(self._manifest_path.read_text()) or {}
            else:
                self._manifest_cache = {}
        return self._manifest_cache

    def supports_version(self, kernel_version: KernelVersion) -> bool:
        """True iff ``kernel_version`` falls in the manifest's kernel_version_range."""
        kvr = self.manifest().get("kernel_version_range", {}) or {}
        valid_from = kvr.get("valid_from")
        valid_until = kvr.get("valid_until")
        if valid_from is None:
            return True
        vr = make_range(valid_from, valid_until)
        return range_contains(vr, kernel_version)

    # --- domain routing -------------------------------------------------------
    def owns_file(self, path: str) -> bool:
        if path.startswith(ASOC_ROOT):
            return True
        patterns = self.manifest().get("file_patterns", [])
        return any(fnmatch.fnmatch(path, pat) for pat in patterns)

    def build_target(self) -> str:
        return ASOC_ROOT

    # --- knowledge accessors --------------------------------------------------
    def rules(self, kernel_version: KernelVersion | None = None) -> list[Rule]:
        """Return subsystem rules valid for ``kernel_version`` (all, if None).

        Sorted by ``rule_id`` for determinism."""
        rules = [r for r, _ in build_rules()]
        if kernel_version is not None:
            rules = [r for r in rules if range_contains(r.version_range, kernel_version)]
        return sorted(rules, key=lambda r: r.rule_id)

    def patterns(self) -> list[dict[str, Any]]:
        """Return the validated review-pattern library (sorted by pattern_id)."""
        return sorted(build_patterns(), key=lambda p: p["pattern_id"])

    def reasoning_plugins(self) -> list[Any]:
        """Return the domain reasoning plugins the Review Engine may invoke."""
        return build_reasoning_plugins()

    # --- graph seeding --------------------------------------------------------
    def seed_graph(self, knowledge_manager: KnowledgeManagerImpl) -> None:
        """Populate the EKG with ASoC nodes/edges (idempotent, deterministic).

        Seeds: the Subsystem node, Maintainer nodes (MAINTAINS), Rule nodes
        (GOVERNS subsystem, DOCUMENTED_BY docs), Api nodes (DEFINED_IN headers,
        SUPERSEDES for replaced APIs), Pattern nodes (EXEMPLIFIES/ GOVERNS via the
        rule), and one seed Evidence node per rule/pattern (SUPPORTS) carrying the
        real provenance so the Evidence Engine can verify against it."""
        g = knowledge_manager.graph
        head_range = make_range(
            self.manifest().get("kernel_version_range", {}).get("valid_from", "6.1"),
            self.manifest().get("kernel_version_range", {}).get("valid_until"),
        )
        subsystem_prov = Provenance(
            repo_path="MAINTAINERS::SOUND - SOC LAYER",
            version_or_commit="v6.6",
            transformation_history=["maintainers.parse", "asoc.dkp"],
            source_confidence=1.0,
        )

        # Subsystem.
        g.add_node(
            ASOC_SUBSYSTEM_ID,
            NODE_SUBSYSTEM,
            properties={"name": "asoc", "path_root": ASOC_ROOT},
            version_range=head_range,
            provenance=subsystem_prov,
        )

        # Maintainers -> MAINTAINS -> subsystem.
        for m in ASOC_MAINTAINERS:
            mid = f"maintainer:{m['email']}"
            g.add_node(
                mid,
                NODE_MAINTAINER,
                properties={"name": m["name"], "email": m["email"], "subsystems": ["asoc"]},
                version_range=head_range,
                provenance=subsystem_prov,
            )
            g.add_edge(mid, ASOC_SUBSYSTEM_ID, EDGE_MAINTAINS, properties={"role": "M"})

        # Rules -> GOVERNS subsystem; DOCUMENTED_BY document; + seed Evidence.
        for rule, prov in build_rules():
            g.add_node(
                rule.rule_id,
                NODE_RULE,
                properties={
                    "rule_id": rule.rule_id,
                    "category": rule.category,
                    "rule_type": rule.rule_type.value,
                    "description": rule.description,
                    "rationale": rule.rationale,
                    "doc_ref": rule.documentation_ref,
                    "enforcement_rate": rule.historical_enforcement_rate,
                },
                version_range=rule.version_range,
                provenance=prov,
            )
            g.add_edge(
                rule.rule_id, ASOC_SUBSYSTEM_ID, EDGE_GOVERNS,
                properties={"strength": rule.historical_enforcement_rate or 1.0},
                provenance=prov,
            )
            if rule.documentation_ref:
                doc_id = f"doc:{rule.documentation_ref}"
                g.add_node(
                    doc_id, NODE_DOCUMENT,
                    properties={"path": rule.documentation_ref, "title": rule.documentation_ref},
                    version_range=head_range, provenance=prov,
                )
                g.add_edge(rule.rule_id, doc_id, EDGE_DOCUMENTED_BY, provenance=prov)
            self._seed_evidence(knowledge_manager, rule.rule_id, prov, rule.documentation_ref)

        # Apis -> DEFINED_IN header; SUPERSEDES for replacements.
        for api in build_apis():
            api_id = f"api:{api['symbol']}"
            api_range = make_range(api["introduced_in"], api.get("deprecated_in"))
            g.add_node(
                api_id, NODE_API,
                properties={
                    "symbol": api["symbol"], "kind": api["kind"], "header": api["header"],
                    "deprecated": api.get("deprecated_in") is not None,
                },
                version_range=api_range, provenance=api["provenance"],
            )
            header_id = f"doc:{api['header']}"
            g.add_node(
                header_id, NODE_DOCUMENT,
                properties={"path": api["header"], "title": api["header"]},
                version_range=head_range, provenance=api["provenance"],
            )
            g.add_edge(api_id, header_id, EDGE_DEFINED_IN, provenance=api["provenance"])
            replaced_by = api.get("replaced_by")
            if replaced_by:
                new_id = f"api:{replaced_by}"
                # SUPERSEDES points new -> old (the successor supersedes the old).
                if not g.has_node(new_id):
                    g.add_node(
                        new_id, NODE_API,
                        properties={"symbol": replaced_by, "kind": api["kind"]},
                        version_range=make_range(api.get("deprecated_in", "6.1")),
                        provenance=api["provenance"],
                    )
                g.add_edge(new_id, api_id, EDGE_SUPERSEDES, provenance=api["provenance"])

        # Patterns -> GOVERNS via rule; seed Evidence + example EXEMPLIFIES.
        for pat in build_patterns():
            pid = pat["pattern_id"]
            g.add_node(
                pid, NODE_PATTERN,
                properties={
                    "pattern_id": pid, "description": pat["description"],
                    "outcome": pat["outcome"], "signals": pat.get("signals", []),
                    "layer": pat.get("layer", "design"),
                },
                version_range=head_range, provenance=pat["provenance"],
            )
            rule_id = pat.get("rule_id")
            if rule_id and g.has_node(rule_id):
                g.add_edge(rule_id, pid, EDGE_GOVERNS, provenance=pat["provenance"])
            self._seed_evidence(knowledge_manager, pid, pat["provenance"], None, is_pattern=True)
            # Example patches EXEMPLIFY the pattern (accepted/rejected outcome tag).
            concept_id = f"concept:{pid}"
            g.add_node(
                concept_id, NODE_CONCEPT, properties={"name": pat["description"][:60]},
                version_range=head_range, provenance=pat["provenance"],
            )
            g.add_edge(pid, concept_id, EDGE_EXEMPLIFIES,
                       properties={"outcome": pat["outcome"]}, provenance=pat["provenance"])

        # Register canonical AlternativeRecommendation entries.
        knowledge_manager.register_recommendations(CANONICAL_RECOMMENDATIONS)

    @staticmethod
    def _seed_evidence(
        knowledge_manager: KnowledgeManagerImpl,
        supports_id: str,
        prov: Provenance,
        doc_ref: str | None,
        is_pattern: bool = False,
    ) -> None:
        """Attach one seed :class:`Evidence` node (SUPPORTS the rule/pattern).

        Source type is chosen from the provenance: a lore URL => REVIEW_DISCUSSION;
        a documentation path => DOCUMENTATION; a header => API_HEADER. Seed evidence
        is left ``verified=False`` — the Evidence Engine (Sprint-3) verifies it at
        review time. This keeps the constitutional gate honest."""
        if prov.source_url and "lore.kernel.org" in prov.source_url:
            src = EvidenceSourceType.REVIEW_DISCUSSION
        elif prov.repo_path and prov.repo_path.startswith("Documentation/"):
            src = EvidenceSourceType.DOCUMENTATION
        elif prov.repo_path and prov.repo_path.startswith("include/"):
            src = EvidenceSourceType.API_HEADER
        else:
            src = EvidenceSourceType.DOCUMENTATION
        ev = Evidence(
            evidence_id=f"ev:{supports_id}",
            source_type=src,
            summary=f"Seed evidence for {supports_id} ({'pattern' if is_pattern else 'rule'}).",
            provenance=prov,
            version_range=make_range("6.1"),
            verified=False,
            strength=0.0,
        )
        # Use the manager helper so the SUPPORTS edge + node stay consistent.
        g = knowledge_manager.graph
        g.add_node(
            ev.evidence_id, "Evidence",
            properties={
                "source_type": ev.source_type.value, "summary": ev.summary,
                "verified": ev.verified, "strength": ev.strength,
            },
            version_range=ev.version_range,
            provenance=prov,
        )
        if g.has_node(supports_id):
            g.add_edge(ev.evidence_id, supports_id, EDGE_SUPPORTS, provenance=prov)

        # Add a documentation evidence node if a doc_ref is available.
        if doc_ref and not is_pattern:
            doc_ev_id = f"ev:doc:{supports_id}"
            doc_prov = Provenance(
                repo_path=doc_ref,
                version_or_commit="v6.6",
                transformation_history=["asoc.dkp", "seed_evidence"],
                source_confidence=1.0,
            )
            g.add_node(
                doc_ev_id, "Evidence",
                properties={
                    "source_type": EvidenceSourceType.DOCUMENTATION.value,
                    "summary": f"Documentation reference: {doc_ref}",
                    "verified": False, "strength": 0.0,
                },
                version_range=make_range("6.1"),
                provenance=doc_prov,
            )
            if g.has_node(supports_id):
                g.add_edge(doc_ev_id, supports_id, EDGE_SUPPORTS, provenance=doc_prov)
