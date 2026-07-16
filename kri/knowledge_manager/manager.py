"""Knowledge Manager (Blueprint Sec. 21.4 / SPEC §5).

Owns the Engineering Knowledge Graph and is the ONLY module that loads Domain
Knowledge Packages. DKPs are discovered via the ``kri.dkp`` entry-point group —
the Generic Runtime NEVER does ``import kri.packages.<domain>`` (Constitution
Sec. 9 / SPEC §1, §5.2). The Review Engine receives a loaded DKP *handle* and
touches a domain only through the :class:`DomainKnowledgePackage` protocol.

Determinism / replayability (Constitution Sec. 31/38): ``snapshot()`` captures the
graph plus the loaded-DKP versions into an immutable :class:`KnowledgeStateId`
keyed by the graph's content hash; ``restore(state_id)`` reinstates that exact
graph so a review can be replayed against the knowledge state it originally used.
"""

from __future__ import annotations

from datetime import date
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from kri.common.interfaces import DomainKnowledgePackage
from kri.common.models import (
    Decision,
    Evidence,
    KernelVersion,
    KnowledgeStateId,
    Provenance,
    VersionRange,
)
from kri.knowledge import KnowledgeGraph, coerce_version
from kri.knowledge.schema import (
    EDGE_SUPPORTS,
    EKG_SCHEMA_VERSION,
    NODE_EVIDENCE,
)

# The entry-point group that DKPs register under (SPEC §5.2). Hardcoding the
# *group name* is not a domain identifier — no domain is named here.
DKP_ENTRY_POINT_GROUP = "kri.dkp"

RUNTIME_VERSION = "0.1.0"

# A fixed epoch keeps snapshot ids reproducible (no wall-clock — Constitution
# Sec. 31/40). The state identity is the graph content hash, not the date.
_SNAPSHOT_EPOCH = date(2000, 1, 1)


class DkpLoadError(RuntimeError):
    """Raised when a requested DKP cannot be discovered or validated."""


class KnowledgeManagerImpl:
    """Concrete :class:`kri.common.interfaces.KnowledgeManager`.

    Backed by a single in-process :class:`KnowledgeGraph`. Snapshots are stored
    in-memory keyed by ``state_id.state_id`` so a review can be replayed against
    the exact graph state it used (SPEC §8 replay invariant).
    """

    def __init__(self, graph: KnowledgeGraph | None = None) -> None:
        self._graph = graph or KnowledgeGraph()
        self._loaded: dict[str, DomainKnowledgePackage] = {}
        self._snapshots: dict[str, tuple[dict[str, Any], dict[str, str], int]] = {}
        self._learning_iteration = 0

    # -- graph access (used by DKP.seed_graph and the Review/Evidence engines) --
    @property
    def graph(self) -> KnowledgeGraph:
        """The underlying EKG. DKPs call this from ``seed_graph`` to add nodes."""
        return self._graph

    def loaded_domains(self) -> dict[str, str]:
        """domain -> version for every DKP currently loaded (sorted, deterministic)."""
        return {name: dkp.version for name, dkp in sorted(self._loaded.items())}

    # -- interface: load_dkp -------------------------------------------------
    def load_dkp(self, domain: str) -> DomainKnowledgePackage:
        """Discover, instantiate, validate, and seed a DKP by domain name.

        Resolution is via the ``kri.dkp`` entry-point group ONLY (SPEC §5.2). The
        manifest's ``package.name`` MUST equal the entry-point name (and the
        requested domain). After validation the DKP seeds its nodes/edges into the
        graph and the handle is cached + returned."""
        ep = self._find_entry_point(domain)
        if ep is None:
            raise DkpLoadError(
                f"no DKP registered for domain {domain!r} in "
                f"entry-point group {DKP_ENTRY_POINT_GROUP!r}"
            )
        try:
            dkp_cls = ep.load()
        except Exception as exc:  # noqa: BLE001 - surface any import/attr failure
            raise DkpLoadError(f"failed to load DKP {domain!r}: {exc}") from exc

        dkp = dkp_cls()
        self._validate_dkp(dkp, domain, ep)

        # Seed the graph, then cache the handle. Seeding is idempotent (the graph
        # overwrites equal node_ids) so re-loading a domain does not duplicate.
        dkp.seed_graph(self)
        self._loaded[dkp.name] = dkp
        return dkp

    def _find_entry_point(self, domain: str) -> EntryPoint | None:
        eps = entry_points(group=DKP_ENTRY_POINT_GROUP)
        for ep in eps:
            if ep.name == domain:
                return ep
        return None

    @staticmethod
    def _validate_dkp(
        dkp: DomainKnowledgePackage, domain: str, ep: EntryPoint
    ) -> None:
        if not isinstance(dkp, DomainKnowledgePackage):
            raise DkpLoadError(
                f"entry point {ep.value!r} does not satisfy DomainKnowledgePackage"
            )
        manifest = dkp.manifest()
        pkg = (manifest or {}).get("package", {})
        name = pkg.get("name")
        if name != domain:
            raise DkpLoadError(
                f"manifest package.name {name!r} != requested domain {domain!r}"
            )
        if name != ep.name:
            raise DkpLoadError(
                f"manifest package.name {name!r} != entry-point name {ep.name!r}"
            )
        declared = (manifest.get("schema", {}) or {}).get("ekg_schema_version")
        if declared not in (None, EKG_SCHEMA_VERSION):
            raise DkpLoadError(
                f"DKP {name!r} targets EKG schema {declared!r}; "
                f"runtime provides {EKG_SCHEMA_VERSION!r}"
            )

    # -- interface: query_graph ---------------------------------------------
    def query_graph(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a temporal graph query (SPEC §4.5). Delegates to the backend;
        results are deterministically ordered by the backend."""
        return self._graph.query(query)

    # -- interface: get_evidence --------------------------------------------
    def get_evidence(self, decision: Decision) -> list[Evidence]:
        """Gather candidate :class:`Evidence` nodes supporting a decision.

        Resolves Evidence nodes linked (``SUPPORTS``) to the decision's Rule /
        pattern, plus any Evidence already attached to its EvidenceGraph. Results
        are de-duplicated by ``evidence_id`` and stably ordered."""
        found: dict[str, Evidence] = {}

        for anchor in (decision.rule_id, decision.pattern_id):
            if not anchor or not self._graph.has_node(anchor):
                continue
            for edge in self._graph.edges_of(anchor, EDGE_SUPPORTS, direction="in"):
                ev = self._evidence_from_node(edge["src"])
                if ev is not None:
                    found[ev.evidence_id] = ev

        if decision.evidence_graph is not None:
            for ev in decision.evidence_graph.evidence:
                found[ev.evidence_id] = ev

        return [found[k] for k in sorted(found)]

    def _evidence_from_node(self, node_id: str) -> Evidence | None:
        rec = self._graph.get_node(node_id)
        if rec is None or rec["node_type"] != NODE_EVIDENCE:
            return None
        props = rec["properties"]
        prov = (
            Provenance.model_validate(rec["provenance"])
            if rec.get("provenance")
            else Provenance()
        )
        vr = (
            VersionRange.model_validate(rec["version_range"])
            if rec.get("version_range")
            else None
        )
        try:
            return Evidence(
                evidence_id=node_id,
                source_type=props["source_type"],
                summary=props.get("summary", ""),
                provenance=prov,
                version_range=vr,
                verified=bool(props.get("verified", False)),
                strength=float(props.get("strength", 0.0)),
            )
        except Exception:  # noqa: BLE001 - malformed node => not usable evidence
            return None

    def add_evidence_node(self, evidence: Evidence, supports: str | None = None) -> str:
        """Helper: materialize an :class:`Evidence` model as an Evidence node,
        optionally linking it (``SUPPORTS``) to a Rule/Pattern/Decision node.

        Used by the Evidence/Learning engines to persist runtime evidence
        (e.g. normalized static findings) into the EKG with full provenance."""
        self._graph.add_node(
            evidence.evidence_id,
            NODE_EVIDENCE,
            properties={
                "source_type": evidence.source_type.value,
                "summary": evidence.summary,
                "verified": evidence.verified,
                "strength": evidence.strength,
            },
            version_range=evidence.version_range,
            provenance=evidence.provenance,
        )
        if supports is not None and self._graph.has_node(supports):
            self._graph.add_edge(
                evidence.evidence_id,
                supports,
                EDGE_SUPPORTS,
                provenance=evidence.provenance,
            )
        return evidence.evidence_id

    # -- interface: snapshot / restore --------------------------------------
    def snapshot(self) -> KnowledgeStateId:
        """Capture the full knowledge state; return an immutable state id.

        The ``state_id`` is the graph's content hash (SPEC §4 canonical
        serialization), so equal graphs snapshot to equal ids — reproducible
        (Constitution Sec. 38). ``dkp_versions`` and the learning iteration are
        captured for replay bookkeeping."""
        dkp_versions = self.loaded_domains()
        state_hash = self._graph.content_hash()
        self._snapshots[state_hash] = (
            self._graph.to_dict(),
            dkp_versions,
            self._learning_iteration,
        )
        return KnowledgeStateId(
            state_id=state_hash,
            created_at=_SNAPSHOT_EPOCH,
            ekg_schema_version=self._graph.schema_version,
            runtime_version=RUNTIME_VERSION,
            dkp_versions=dkp_versions,
            learning_iteration=self._learning_iteration,
        )

    def restore(self, state_id: str) -> KnowledgeStateId:
        """Restore a previously snapshotted state by its ``state_id`` string.

        NOTE (frozen-interface quirk, SPEC §3): ``snapshot()`` returns a
        :class:`KnowledgeStateId` object while ``restore`` takes a ``str`` — so
        snapshots are keyed by ``state_id.state_id``. Restoring rebuilds the graph
        from the stored canonical form, yielding a byte-for-byte identical graph."""
        if state_id not in self._snapshots:
            raise KeyError(f"unknown knowledge state id: {state_id!r}")
        graph_dict, dkp_versions, iteration = self._snapshots[state_id]
        self._graph = KnowledgeGraph.from_dict(graph_dict)
        self._learning_iteration = iteration
        return KnowledgeStateId(
            state_id=state_id,
            created_at=_SNAPSHOT_EPOCH,
            ekg_schema_version=self._graph.schema_version,
            runtime_version=RUNTIME_VERSION,
            dkp_versions=dict(dkp_versions),
            learning_iteration=iteration,
        )

    # -- learning bookkeeping ------------------------------------------------
    def bump_learning_iteration(self) -> int:
        """Advance the learning-iteration counter (used by the Learning Engine
        when it commits a new knowledge state)."""
        self._learning_iteration += 1
        return self._learning_iteration

    def supports_version(self, domain: str, version: KernelVersion | str) -> bool:
        """Convenience: does a loaded DKP support a version?"""
        dkp = self._loaded.get(domain)
        if dkp is None:
            return False
        return dkp.supports_version(coerce_version(version))
