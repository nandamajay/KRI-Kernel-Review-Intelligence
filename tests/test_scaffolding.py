"""Scaffolding smoke tests.

These confirm the skeleton is importable and the frozen contracts + Domain
Isolation invariant hold. Builder agents extend the suite per SPEC.md.
"""

from __future__ import annotations

import pkgutil
from pathlib import Path

import kri
from kri.common import models
from kri.common.interfaces import DomainKnowledgePackage, ReviewEngine
from kri.common.models import ConfidenceLevel, Decision, EvidenceGraph


def test_version_exposed() -> None:
    assert kri.__version__ == "0.1.0"


def test_core_types_importable() -> None:
    # A representative sample of the frozen core contracts.
    assert models.Severity.BLOCKER.value == "blocker"
    assert len(list(models.ReasoningLayer)) == 6
    assert len(list(models.ConfidenceFactor)) == 8
    assert len(models.EVIDENCE_SOURCE_PRIORITY) == 15


def test_confidence_level_from_score_mapping() -> None:
    assert ConfidenceLevel.from_score(0.99) is ConfidenceLevel.CERTAIN
    assert ConfidenceLevel.from_score(0.85) is ConfidenceLevel.LIKELY
    assert ConfidenceLevel.from_score(0.65) is ConfidenceLevel.POSSIBLE
    assert ConfidenceLevel.from_score(0.45) is ConfidenceLevel.SPECULATIVE
    assert ConfidenceLevel.from_score(0.10) is ConfidenceLevel.UNKNOWN


def test_decision_publishable_gate() -> None:
    # No evidence graph => not publishable (Constitution Sec. 28).
    d = Decision(
        decision_id="d1",
        series_id="s1",
        layer=models.ReasoningLayer.STRUCTURAL,
    )
    assert d.is_publishable() is False
    # Evidence graph with no verified nodes still fails the gate.
    d.evidence_graph = EvidenceGraph(comment_id="c1")
    assert d.evidence_graph.has_verified_evidence() is False
    assert d.is_publishable() is False


def test_runtime_protocols_are_runtime_checkable() -> None:
    # Protocols decorated @runtime_checkable support isinstance checks.
    assert isinstance(ReviewEngine, type)
    assert isinstance(DomainKnowledgePackage, type)


def test_asoc_dkp_satisfies_protocol() -> None:
    # The lone concrete DKP must structurally satisfy the extension boundary.
    from kri.packages.asoc.plugin import AsocDomainKnowledgePackage

    dkp = AsocDomainKnowledgePackage()
    assert isinstance(dkp, DomainKnowledgePackage)
    assert dkp.name == "asoc"
    assert dkp.owns_file("sound/soc/soc-core.c") is True
    assert dkp.owns_file("drivers/net/foo.c") is False


def test_domain_isolation_generic_runtime_has_no_asoc_identifiers() -> None:
    """Constitution Sec. 9: the Generic Runtime must contain NO domain identifiers.

    Scan every module outside kri/packages/ for forbidden tokens.
    """
    forbidden = ("snd_soc", "asoc", "sound/soc", "alsa")
    kri_root = Path(kri.__file__).parent
    offenders: list[str] = []
    for path in kri_root.rglob("*.py"):
        rel = path.relative_to(kri_root).as_posix()
        if rel.startswith("packages/"):
            continue  # domain packages are the only place identifiers may appear
        text = path.read_text().lower()
        for token in forbidden:
            if token in text:
                offenders.append(f"{rel}: {token}")
    assert not offenders, f"Domain isolation violated: {offenders}"


def test_all_module_packages_importable() -> None:
    # Every scaffolded subpackage must import cleanly.
    kri_root = Path(kri.__file__).parent
    for mod in pkgutil.iter_modules([str(kri_root)]):
        __import__(f"kri.{mod.name}")


def test_kernel_version_sort_key_rc_sorts_before_release() -> None:
    """RC versions must sort before the final release of the same x.y.z."""
    from kri.common.models import KernelVersion

    v_rc1 = KernelVersion(raw="6.9-rc1", major=6, minor=9, rc=1)
    v_final = KernelVersion(raw="6.9", major=6, minor=9)
    assert v_rc1.sort_key() < v_final.sort_key()


def test_kernel_version_sort_key_orders_major_minor() -> None:
    """Major/minor ordering must be numeric, not lexicographic."""
    from kri.common.models import KernelVersion

    v_68 = KernelVersion(raw="6.8", major=6, minor=8)
    v_69 = KernelVersion(raw="6.9", major=6, minor=9)
    v_610 = KernelVersion(raw="6.10", major=6, minor=10)
    keys = [v_610.sort_key(), v_68.sort_key(), v_69.sort_key()]
    assert sorted(keys) == [v_68.sort_key(), v_69.sort_key(), v_610.sort_key()]


def test_version_range_open_ended_has_none_until() -> None:
    """VersionRange with valid_until=None means 'still valid at HEAD'."""
    from kri.common.models import KernelVersion, VersionRange

    v = KernelVersion(raw="6.9", major=6, minor=9)
    vr = VersionRange(valid_from=v)
    assert vr.valid_until is None
