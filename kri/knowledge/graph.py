"""Engineering Knowledge Graph — NetworkX temporal backend (SPEC §4).

A generic, domain-agnostic, temporal property graph on a NetworkX ``MultiDiGraph``.
It stores nodes/edges with a common envelope (``node_id``/``node_type``,
``version_range``, ``provenance``, ``properties``) and answers *as-of* temporal
queries (SPEC §4.4/§4.5). It is the ONLY module that imports ``networkx``
(SPEC §4.6 — the Neo4j migration boundary).

**Domain Isolation (Constitution Sec. 9):** no domain identifier appears here.
The graph stores whatever nodes/edges a DKP seeds into it; it hardcodes none.

**Determinism (Constitution Sec. 31):** insertion never depends on dict/set order;
every query result is sorted by a stable key; serialization is canonical
(sorted keys, sorted node/edge lists) so equal graphs serialize byte-for-byte and
snapshots are reproducible.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import networkx as nx

from kri.common.models import KernelVersion, Provenance, VersionRange

from .schema import EKG_SCHEMA_VERSION
from .version import coerce_version, range_contains


class KnowledgeGraph:
    """Temporal property graph backend for the EKG.

    Nodes and edges each carry a :class:`~kri.common.models.VersionRange` and a
    :class:`~kri.common.models.Provenance`. Elements are never mutated across
    kernel versions; a temporal succession is modelled by closing the old
    element's ``valid_until`` and adding a new element (SPEC §4.1).
    """

    def __init__(self) -> None:
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()
        self.schema_version = EKG_SCHEMA_VERSION

    # -- construction --------------------------------------------------------
    def add_node(
        self,
        node_id: str,
        node_type: str,
        *,
        properties: dict[str, Any] | None = None,
        version_range: VersionRange | None = None,
        provenance: Provenance | None = None,
    ) -> str:
        """Insert or replace a node. Returns the node_id.

        Idempotent for equal inputs (determinism): re-adding the same node_id
        overwrites its envelope, so seeding a DKP twice yields one graph.
        """
        self._g.add_node(
            node_id,
            node_type=node_type,
            properties=dict(properties or {}),
            version_range=_dump_range(version_range),
            provenance=_dump_prov(provenance),
        )
        return node_id

    def add_edge(
        self,
        src: str,
        dst: str,
        edge_type: str,
        *,
        properties: dict[str, Any] | None = None,
        version_range: VersionRange | None = None,
        provenance: Provenance | None = None,
    ) -> tuple[str, str, str]:
        """Insert an edge ``(src)-[edge_type]->(dst)``.

        The edge key is the ``edge_type`` so parallel edges of *different* types
        coexist while a repeated ``(src, type, dst)`` triple is idempotent
        (determinism). Endpoints must already exist.
        """
        if src not in self._g:
            raise KeyError(f"unknown source node: {src!r}")
        if dst not in self._g:
            raise KeyError(f"unknown target node: {dst!r}")
        self._g.add_edge(
            src,
            dst,
            key=edge_type,
            edge_type=edge_type,
            properties=dict(properties or {}),
            version_range=_dump_range(version_range),
            provenance=_dump_prov(provenance),
        )
        return (src, dst, edge_type)

    # -- introspection -------------------------------------------------------
    def has_node(self, node_id: str) -> bool:
        return node_id in self._g

    def node_count(self) -> int:
        return int(self._g.number_of_nodes())

    def edge_count(self) -> int:
        return int(self._g.number_of_edges())

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        if node_id not in self._g:
            return None
        return self._node_record(node_id)

    # -- querying ------------------------------------------------------------
    def query(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a temporal :class:`GraphQuery` (SPEC §4.5).

        Supported keys: ``as_of`` (KernelVersion.raw; required for determinism of
        temporal data), ``match`` ({node_type, properties}), ``where``
        (property -> value | list-of-allowed), ``traverse`` (list of hops:
        {edge, direction:in|out, to:node_type}), and ``limit``.

        Results are node/path records sorted by ``node_id`` (stable). When a
        ``traverse`` is present each result also carries the ``path`` of visited
        node_ids and the terminal node's fields.
        """
        as_of = _as_of(query.get("as_of"))
        match = query.get("match") or {}
        want_type = match.get("node_type")
        want_props = match.get("properties") or {}
        where = query.get("where") or {}
        traverse = query.get("traverse") or []
        limit = query.get("limit")

        seeds: list[str] = []
        for node_id in sorted(self._g.nodes):
            rec = self._node_record(node_id)
            if not self._node_visible(rec, as_of):
                continue
            if want_type is not None and rec["node_type"] != want_type:
                continue
            if not _props_match(rec["properties"], want_props):
                continue
            if not _where_match(rec, where):
                continue
            seeds.append(node_id)

        results: list[dict[str, Any]]
        if not traverse:
            results = [self._node_record(nid) for nid in seeds]
        else:
            results = []
            for nid in seeds:
                for path in self._traverse(nid, traverse, as_of):
                    terminal = self._node_record(path[-1])
                    row = dict(terminal)
                    row["path"] = path
                    results.append(row)
            # Stable ordering across traversal results.
            results.sort(key=lambda r: (r["node_id"], tuple(r.get("path", []))))

        if isinstance(limit, int) and limit >= 0:
            results = results[:limit]
        return results

    def _traverse(
        self, start: str, hops: list[dict[str, Any]], as_of: KernelVersion | None
    ) -> list[list[str]]:
        """Depth-first traversal honouring per-hop edge type/direction/target type.

        Returns each full path (list of node_ids) that satisfies every hop, in
        stable (sorted) order. Temporal filtering is applied to both the edge and
        the landing node."""
        paths: list[list[str]] = [[start]]
        for hop in hops:
            edge_type = hop.get("edge")
            direction = hop.get("direction", "out")
            to_type = hop.get("to")
            next_paths: list[list[str]] = []
            for path in paths:
                node = path[-1]
                for nbr in self._neighbors(node, edge_type, direction, as_of):
                    rec = self._node_record(nbr)
                    if not self._node_visible(rec, as_of):
                        continue
                    if to_type is not None and rec["node_type"] != to_type:
                        continue
                    next_paths.append([*path, nbr])
            paths = next_paths
        paths.sort()
        return paths

    def _neighbors(
        self,
        node: str,
        edge_type: str | None,
        direction: str,
        as_of: KernelVersion | None,
    ) -> list[str]:
        out: list[str] = []
        if direction in ("out", "both"):
            for _, dst, key, data in self._g.out_edges(node, keys=True, data=True):
                if (edge_type is None or key == edge_type) and self._edge_visible(data, as_of):
                    out.append(dst)
        if direction in ("in", "both"):
            for src, _, key, data in self._g.in_edges(node, keys=True, data=True):
                if (edge_type is None or key == edge_type) and self._edge_visible(data, as_of):
                    out.append(src)
        return sorted(set(out))

    def edges_of(
        self, node_id: str, edge_type: str | None = None, direction: str = "out"
    ) -> list[dict[str, Any]]:
        """Return full edge records incident to ``node_id`` (stable order)."""
        recs: list[dict[str, Any]] = []
        if direction in ("out", "both"):
            for src, dst, key, data in self._g.out_edges(node_id, keys=True, data=True):
                if edge_type is None or key == edge_type:
                    recs.append(_edge_record(src, dst, key, data))
        if direction in ("in", "both"):
            for src, dst, key, data in self._g.in_edges(node_id, keys=True, data=True):
                if edge_type is None or key == edge_type:
                    recs.append(_edge_record(src, dst, key, data))
        recs.sort(key=lambda e: (e["src"], e["edge_type"], e["dst"]))
        return recs

    # -- temporal visibility -------------------------------------------------
    @staticmethod
    def _node_visible(rec: dict[str, Any], as_of: KernelVersion | None) -> bool:
        if as_of is None:
            return True
        return range_contains(_load_range(rec.get("version_range")), as_of)

    @staticmethod
    def _edge_visible(data: dict[str, Any], as_of: KernelVersion | None) -> bool:
        if as_of is None:
            return True
        return range_contains(_load_range(data.get("version_range")), as_of)

    def _node_record(self, node_id: str) -> dict[str, Any]:
        data = self._g.nodes[node_id]
        return {
            "node_id": node_id,
            "node_type": data.get("node_type", ""),
            "properties": dict(data.get("properties", {})),
            "version_range": data.get("version_range"),
            "provenance": data.get("provenance"),
        }

    # -- serialization / snapshotting ---------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Canonical, deterministic serialization of the whole graph.

        Nodes sorted by ``node_id``; edges sorted by ``(src, edge_type, dst)``.
        The result round-trips through :meth:`from_dict` to an equal graph and,
        via :func:`json.dumps(..., sort_keys=True)`, hashes reproducibly."""
        nodes = [self._node_record(nid) for nid in sorted(self._g.nodes)]
        edges = [
            _edge_record(src, dst, key, data)
            for src, dst, key, data in sorted(
                self._g.edges(keys=True, data=True),
                key=lambda e: (e[0], e[2], e[1]),
            )
        ]
        return {
            "schema_version": self.schema_version,
            "nodes": nodes,
            "edges": edges,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeGraph:
        g = cls()
        g.schema_version = data.get("schema_version", EKG_SCHEMA_VERSION)
        for n in data.get("nodes", []):
            g._g.add_node(
                n["node_id"],
                node_type=n.get("node_type", ""),
                properties=dict(n.get("properties", {})),
                version_range=n.get("version_range"),
                provenance=n.get("provenance"),
            )
        for e in data.get("edges", []):
            g._g.add_edge(
                e["src"],
                e["dst"],
                key=e["edge_type"],
                edge_type=e["edge_type"],
                properties=dict(e.get("properties", {})),
                version_range=e.get("version_range"),
                provenance=e.get("provenance"),
            )
        return g

    def canonical_json(self) -> str:
        """Deterministic JSON string of the graph (sorted keys)."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        """Stable SHA-256 of the canonical serialization — the graph's identity."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def copy(self) -> KnowledgeGraph:
        """Deep, structural copy via canonical round-trip (guarantees equality)."""
        return KnowledgeGraph.from_dict(self.to_dict())


# ---------------------------------------------------------------------------
# module-private serialization helpers
# ---------------------------------------------------------------------------


def _dump_range(vr: VersionRange | None) -> dict[str, Any] | None:
    return vr.model_dump(mode="json") if vr is not None else None


def _load_range(raw: dict[str, Any] | None) -> VersionRange | None:
    return VersionRange.model_validate(raw) if raw else None


def _dump_prov(prov: Provenance | None) -> dict[str, Any] | None:
    return prov.model_dump(mode="json") if prov is not None else None


def _as_of(raw: Any) -> KernelVersion | None:
    if raw is None:
        return None
    if isinstance(raw, KernelVersion):
        return raw
    return coerce_version(str(raw))


def _edge_record(src: str, dst: str, key: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "src": src,
        "dst": dst,
        "edge_type": data.get("edge_type", key),
        "properties": dict(data.get("properties", {})),
        "version_range": data.get("version_range"),
        "provenance": data.get("provenance"),
    }


def _props_match(props: dict[str, Any], want: dict[str, Any]) -> bool:
    for k, v in want.items():
        if props.get(k) != v:
            return False
    return True


def _where_match(rec: dict[str, Any], where: dict[str, Any]) -> bool:
    """A ``where`` clause matches on node properties; a value may be a scalar
    (equality) or a list (membership). Missing keys fail the clause."""
    props = rec["properties"]
    for k, allowed in where.items():
        val = props.get(k, rec.get(k))
        if isinstance(allowed, list):
            if val not in allowed:
                return False
        elif val != allowed:
            return False
    return True
