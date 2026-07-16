"""WP-9.1a Sub-commit 1: patch series must be reviewed in sequence order.

Regression test for the string-sort-on-patch_id bug: ``sorted(patches, key=
lambda p: p.patch_id)`` is a string sort, so on any series with >= 10 patches
"p-10" sorts before "p-2" -- patches were reviewed out of numeric order.
"""

from __future__ import annotations

from kri.common.interfaces import DomainKnowledgePackage
from kri.common.models import Decision, Patch, PatchSeries, ReasoningLayer
from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.evidence_engine.engine import EvidenceEngineImpl
from kri.knowledge_manager.manager import KnowledgeManagerImpl
from kri.review_engine.engine import ReviewEngineImpl


class _SequenceRecordingPlugin:
    """Stub ReasoningPlugin: applies to every patch, records the order in
    which patch.sequence is observed, and emits no Decisions."""

    def __init__(self, observed: list[int]) -> None:
        self._observed = observed

    @property
    def plugin_id(self) -> str:
        return "test:sequence-recorder"

    @property
    def trigger(self) -> str:
        return "always"

    @property
    def layer(self) -> ReasoningLayer:
        return ReasoningLayer.STRUCTURAL

    def applies(self, patch: Patch, series: PatchSeries) -> bool:
        return True

    def evaluate(self, patch: Patch, series: PatchSeries) -> list[Decision]:
        self._observed.append(patch.sequence)
        return []


class _StubDKP:
    """Minimal DomainKnowledgePackage exposing only the recording plugin."""

    name = "stub"
    version = "0.0.1"

    def __init__(self, plugin: _SequenceRecordingPlugin) -> None:
        self._plugin = plugin

    def manifest(self):
        return {"package": {"name": "stub", "version": "0.0.1"}}

    def supports_version(self, kv):
        return True

    def owns_file(self, path):
        return False

    def build_target(self):
        return ""

    def rules(self, kv=None):
        return []

    def patterns(self):
        return []

    def reasoning_plugins(self):
        return [self._plugin]

    def seed_graph(self, km):
        pass


def _build_out_of_order_series() -> PatchSeries:
    """12 patches, sequences 1..12, patch_ids assigned so a string sort on
    patch_id puts them out of numeric order (e.g. "p-10" < "p-2")."""
    patches = [
        Patch(
            patch_id=f"p-{seq}",
            subject=f"patch {seq}",
            sequence=seq,
            series_total=12,
            diff="+ line",
        )
        for seq in range(1, 13)
    ]
    return PatchSeries(series_id="s-order-test", patches=patches)


def test_patches_reviewed_in_sequence_order() -> None:
    km = KnowledgeManagerImpl()
    ev_engine = EvidenceEngineImpl(km)
    conf_engine = ConfidenceEngineImpl()
    re_engine = ReviewEngineImpl(ev_engine, conf_engine)

    observed: list[int] = []
    plugin = _SequenceRecordingPlugin(observed)
    dkp: DomainKnowledgePackage = _StubDKP(plugin)  # type: ignore[assignment]

    series = _build_out_of_order_series()
    re_engine.review(series, dkp)

    assert observed == list(range(1, 13))
