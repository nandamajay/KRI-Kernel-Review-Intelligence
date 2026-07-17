"""Benchmark precision/recall/F1 floor regression (Phase-A audit Sec. 6; SPEC.md §11).

Pins today's actual measured quality on the 125 cached lore fixtures as a
floor: any future change that drops KRI's agreement quality below this floor
fails CI. The floor is deliberately conservative (today's actual numbers,
rounded down) -- every future sprint MUST ratchet it upward, never loosen it.
See docs/BENCHMARK_HISTORY.md for the running history.

Also pins determinism: two back-to-back runs over the same fixtures must
produce byte-identical aggregate numbers (Constitution Sec. 40).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from kri.benchmark.runner import BenchmarkRunner
from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.evidence_engine.engine import EvidenceEngineImpl
from kri.knowledge_manager.manager import KnowledgeManagerImpl
from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.patch_manager import PatchManagerImpl
from kri.process_rules.manager import ProcessEtiquettePlugin, ProcessRulesManagerImpl
from kri.review_engine.engine import ReviewEngineImpl

from .conftest import KERNEL_PATH, LORE_CACHE, MAINTAINERS_PATH

PRECISION_FLOOR = 0.55
RECALL_FLOOR = 0.95
F1_FLOOR = 0.70


def _run_benchmark_over_cached_fixtures() -> dict[str, Any]:
    """Aggregate exact/false-positive/false-negative counts across every
    cached fixture that carries real maintainer ground truth, then compute
    pooled precision/recall/F1 -- mirrors kri.web.app's /api/benchmark
    endpoint (same lore/patch/review construction), but aggregated for a
    single pass/fail regression gate instead of per-fixture reporting."""
    maintainers_path = MAINTAINERS_PATH if MAINTAINERS_PATH.exists() else None
    lore = LoreManagerImpl(LoreConfig(
        cache_dir=LORE_CACHE,
        inbox="all",
        maintainers_path=maintainers_path,
        offline=True,
    ))
    patch_manager = PatchManagerImpl(lore_manager=lore)

    km = KnowledgeManagerImpl()
    dkp = None
    dkp_domain = os.environ.get("KRI_DKP_DOMAIN", "asoc")
    if dkp_domain:
        try:
            dkp = km.load_dkp(dkp_domain)
        except Exception:  # noqa: BLE001
            dkp = None

    ev_engine = EvidenceEngineImpl(km)
    conf_engine = ConfidenceEngineImpl()
    re_engine = ReviewEngineImpl(ev_engine, conf_engine)
    extra_plugins = [ProcessEtiquettePlugin(ProcessRulesManagerImpl())]

    runner = BenchmarkRunner()

    total_exact = 0
    total_fp = 0
    total_fn = 0
    total_decisions = 0
    fixtures_used = 0

    for fixture_path in sorted(LORE_CACHE.glob("*.mbox.gz")):
        try:
            thread = lore.load_cached(fixture_path)
            series = patch_manager.parse(thread)
            ground_truth = lore.extract_reviews(thread)
        except Exception:  # noqa: BLE001
            continue
        if not any(c.is_maintainer for c in ground_truth):
            continue

        fixtures_used += 1
        decisions = re_engine.review(series, dkp, extra_plugins=extra_plugins)
        metrics = runner.compare(decisions, ground_truth, series).to_dict()
        total_exact += metrics["exact_agreements"]
        total_fp += metrics["false_positives"]
        total_fn += metrics["false_negatives"]
        total_decisions += metrics["total_decisions"]

    precision = total_exact / (total_exact + total_fp) if (total_exact + total_fp) > 0 else 0.0
    recall = total_exact / (total_exact + total_fn) if (total_exact + total_fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "fixtures_used": fixtures_used,
        "total_decisions": total_decisions,
        "exact": total_exact,
        "false_positives": total_fp,
        "false_negatives": total_fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def test_precision_floor_on_cached_fixtures() -> None:
    """Constitution / audit Sec. 6: pin today's measured precision/recall/F1
    as a regression floor over every cached fixture with maintainer ground
    truth."""
    if not LORE_CACHE.exists() or not any(LORE_CACHE.glob("*.mbox.gz")):
        pytest.skip("lore cache fixtures not present")
    if not MAINTAINERS_PATH.exists():
        pytest.skip("kernel MAINTAINERS file not present")

    result = _run_benchmark_over_cached_fixtures()

    assert result["fixtures_used"] > 0, "no cached fixtures had maintainer ground truth"

    print(
        f"\nbenchmark: fixtures_used={result['fixtures_used']} "
        f"total_decisions={result['total_decisions']} "
        f"exact={result['exact']} fp={result['false_positives']} "
        f"fn={result['false_negatives']} "
        f"precision={result['precision']:.4f} recall={result['recall']:.4f} "
        f"f1={result['f1']:.4f}"
    )

    assert result["precision"] >= PRECISION_FLOOR, (
        f"precision {result['precision']:.4f} fell below the pinned floor "
        f"{PRECISION_FLOOR} -- see docs/BENCHMARK_HISTORY.md"
    )
    assert result["recall"] >= RECALL_FLOOR, (
        f"recall {result['recall']:.4f} fell below the pinned floor "
        f"{RECALL_FLOOR} -- see docs/BENCHMARK_HISTORY.md"
    )
    assert result["f1"] >= F1_FLOOR, (
        f"f1 {result['f1']:.4f} fell below the pinned floor {F1_FLOOR} -- "
        f"see docs/BENCHMARK_HISTORY.md"
    )


def test_benchmark_is_deterministic_across_runs() -> None:
    """Constitution Sec. 40: two back-to-back runs over the same cached
    fixtures must produce byte-identical aggregate numbers."""
    if not LORE_CACHE.exists() or not any(LORE_CACHE.glob("*.mbox.gz")):
        pytest.skip("lore cache fixtures not present")
    if not MAINTAINERS_PATH.exists():
        pytest.skip("kernel MAINTAINERS file not present")

    run1 = _run_benchmark_over_cached_fixtures()
    run2 = _run_benchmark_over_cached_fixtures()

    assert run1 == run2, (
        "benchmark run is non-deterministic across two back-to-back runs "
        f"(Constitution Sec. 40): {run1!r} != {run2!r}"
    )
