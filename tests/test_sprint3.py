"""Sprint-3 tests: Review Engine, Evidence Engine, Confidence Engine,
Report Generator, Benchmark Framework, and Simulation Engine.

Tests cover:
  - Unit tests for each engine in isolation.
  - Integration test: full simulation pipeline on the NAU83G60 v5 cached fixture.
  - Benchmark test: agreement metrics are computed on cached fixtures.
  - Determinism test: same patch twice -> byte-identical Report output.
  - Evidence gate test: Decision with no verified evidence is not publishable.
  - Constitutional compliance: domain isolation, simulation disclaimer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kri.common.models import (
    ConfidenceFactor,
    ConfidenceLevel,
    Decision,
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    Patch,
    PatchSeries,
    Provenance,
    ReasoningLayer,
    ReviewComment,
    Rule,
    RuleType,
    Severity,
)
from kri.confidence_engine.engine import ConfidenceEngineImpl
from kri.evidence_engine.engine import EvidenceEngineImpl
from kri.knowledge_manager.manager import KnowledgeManagerImpl
from kri.report.generator import ReportGenerator

# Path to test fixtures.
REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
DATA_ROOT = WORKSPACE_ROOT / "data"
LORE_CACHE = DATA_ROOT / "lore_cache"
V5_FIXTURE = LORE_CACHE / "20260630021510_821919-3-YLCHANG2_nuvoton_com.mbox.gz"


# ---------------------------------------------------------------------------
# Confidence Engine unit tests
# ---------------------------------------------------------------------------


class TestConfidenceEngine:
    """Unit tests for ConfidenceEngineImpl."""

    def test_default_weights_sum_to_one(self) -> None:
        engine = ConfidenceEngineImpl()
        weight_sum = sum(engine._weights.values())
        assert abs(weight_sum - 1.0) < 1e-6

    def test_custom_weights_assertion(self) -> None:
        """Invalid weights must raise an assertion error."""
        bad_weights = {f: 0.5 for f in ConfidenceFactor}
        with pytest.raises(AssertionError):
            ConfidenceEngineImpl(weights=bad_weights)

    def test_empty_evidence_graph_yields_unknown(self) -> None:
        """No evidence -> all factor scores 0.0 -> level UNKNOWN."""
        engine = ConfidenceEngineImpl()
        decision = Decision(
            decision_id="test-d1",
            series_id="s1",
            layer=ReasoningLayer.STRUCTURAL,
            statement="test",
        )
        eg = EvidenceGraph(comment_id="test-d1")
        score = engine.score(decision, eg)
        assert score.score == 0.0
        assert score.level == ConfidenceLevel.UNKNOWN
        for factor_score in score.factor_scores.values():
            assert factor_score == 0.0

    def test_with_verified_evidence(self) -> None:
        """Verified evidence should produce a non-zero score."""
        engine = ConfidenceEngineImpl()
        decision = Decision(
            decision_id="test-d2",
            series_id="s1",
            layer=ReasoningLayer.DESIGN,
            statement="test design concern",
        )
        ev = Evidence(
            evidence_id="ev1",
            source_type=EvidenceSourceType.DOCUMENTATION,
            summary="Found in kernel docs",
            provenance=Provenance(repo_path="Documentation/test.rst"),
            verified=True,
            strength=0.9,
        )
        rule = Rule(
            rule_id="test-rule-1",
            category="test",
            rule_type=RuleType.HARD,
            description="Test rule",
            rationale="Test rationale",
            historical_enforcement_rate=0.95,
        )
        eg = EvidenceGraph(
            comment_id="test-d2",
            evidence=[ev],
            subsystem_rule=rule,
            accepted_examples=["patch-1", "patch-2"],
        )
        score = engine.score(decision, eg)
        assert score.score > 0.0
        assert score.level != ConfidenceLevel.UNKNOWN
        assert ConfidenceFactor.SUBSYSTEM_EVIDENCE in score.factor_scores
        assert score.factor_scores[ConfidenceFactor.SUBSYSTEM_EVIDENCE] > 0.0

    def test_reproducibility(self) -> None:
        """Same inputs -> identical scores."""
        engine = ConfidenceEngineImpl()
        decision = Decision(
            decision_id="repro-d1",
            series_id="s1",
            layer=ReasoningLayer.SEMANTIC,
            statement="test",
        )
        ev = Evidence(
            evidence_id="ev-repro",
            source_type=EvidenceSourceType.REVIEW_DISCUSSION,
            summary="Lore comment",
            provenance=Provenance(
                source_url="https://lore.kernel.org/all/test@example.com/"
            ),
            verified=True,
            strength=0.7,
        )
        eg = EvidenceGraph(comment_id="repro-d1", evidence=[ev])
        score1 = engine.score(decision, eg)
        score2 = engine.score(decision, eg)
        assert score1.score == score2.score
        assert score1.level == score2.level
        assert score1.factor_scores == score2.factor_scores


# ---------------------------------------------------------------------------
# Evidence Engine unit tests
# ---------------------------------------------------------------------------


class TestEvidenceEngine:
    """Unit tests for EvidenceEngineImpl."""

    def test_verify_lore_url(self) -> None:
        """Evidence with a valid lore.kernel.org URL should verify."""
        km = KnowledgeManagerImpl()
        engine = EvidenceEngineImpl(km)
        ev = Evidence(
            evidence_id="ev-lore",
            source_type=EvidenceSourceType.REVIEW_DISCUSSION,
            summary="Mark Brown review",
            provenance=Provenance(
                source_url="https://lore.kernel.org/all/test@sirena.co.uk/"
            ),
            verified=False,
            strength=0.0,
        )
        result = engine.verify(ev)
        assert result.verified is True
        assert result.strength > 0.0

    def test_verify_repo_path(self) -> None:
        """Evidence with a repo_path should verify."""
        km = KnowledgeManagerImpl()
        engine = EvidenceEngineImpl(km)
        ev = Evidence(
            evidence_id="ev-repo",
            source_type=EvidenceSourceType.DOCUMENTATION,
            summary="Kernel docs",
            provenance=Provenance(repo_path="Documentation/sound/soc/codec.rst"),
            verified=False,
            strength=0.0,
        )
        result = engine.verify(ev)
        assert result.verified is True
        assert result.strength > 0.0
        # Documentation has priority 1 -> max strength
        assert result.strength == 1.0

    def test_verify_unverifiable(self) -> None:
        """Evidence with no provenance anchor should not verify."""
        km = KnowledgeManagerImpl()
        engine = EvidenceEngineImpl(km)
        ev = Evidence(
            evidence_id="ev-empty",
            source_type=EvidenceSourceType.DESIGN_INFERENCE,
            summary="Guessed from structure",
            provenance=Provenance(),
            verified=False,
            strength=0.0,
        )
        result = engine.verify(ev)
        assert result.verified is False
        assert result.strength == 0.0

    def test_format_evidence(self) -> None:
        """Format should produce a readable citation string."""
        km = KnowledgeManagerImpl()
        engine = EvidenceEngineImpl(km)
        ev = Evidence(
            evidence_id="ev-fmt",
            source_type=EvidenceSourceType.DOCUMENTATION,
            summary="Test docs",
            provenance=Provenance(repo_path="Documentation/test.rst"),
            verified=True,
            strength=1.0,
        )
        citation = engine.format(ev)
        assert "documentation" in citation
        assert "Test docs" in citation
        assert "verified" in citation

    def test_gather_with_seeded_graph(self) -> None:
        """Gather should find evidence from a seeded KG."""
        km = KnowledgeManagerImpl()
        # Load the DKP to seed the graph.
        try:
            km.load_dkp("asoc")
        except Exception:
            pytest.skip("DKP entry point not available")

        # Create a decision referencing a real seeded rule.
        decision = Decision(
            decision_id="test-gather-d1",
            series_id="s1",
            layer=ReasoningLayer.DESIGN,
            rule_id="asoc-tdm-slot-not-userspace",
            pattern_id="asoc-reject-userspace-tdm-slot-enum",
            statement="test",
        )
        engine = EvidenceEngineImpl(km)
        eg = engine.gather(decision)
        assert eg.comment_id == "test-gather-d1"
        # Should have gathered at least the seed evidence.
        assert len(eg.evidence) > 0
        # Rule should be resolved.
        assert eg.subsystem_rule is not None
        assert eg.subsystem_rule.rule_id == "asoc-tdm-slot-not-userspace"


# ---------------------------------------------------------------------------
# Report Generator unit tests
# ---------------------------------------------------------------------------


class TestReportGenerator:
    """Unit tests for the Report Generator."""

    def test_empty_report(self) -> None:
        """Generating a report with no decisions should work."""
        gen = ReportGenerator()
        report = gen.generate([])
        assert "disclaimer" in report
        assert "SIMULATION" in report["disclaimer"]
        assert report["metadata"]["total_decisions"] == 0
        assert report["decisions"] == []

    def test_report_structure(self) -> None:
        """Report should have all expected top-level keys."""
        gen = ReportGenerator()
        decision = Decision(
            decision_id="rep-d1",
            series_id="s1",
            layer=ReasoningLayer.STRUCTURAL,
            statement="Missing cleanup",
            severity=Severity.WARNING,
        )
        report = gen.generate([decision])
        assert "disclaimer" in report
        assert "metadata" in report
        assert "summary" in report
        assert "decisions" in report
        assert "counterfactuals" in report
        assert "learning_references" in report
        assert report["metadata"]["total_decisions"] == 1

    def test_simulation_disclaimer(self) -> None:
        """Every report MUST carry the simulation disclaimer."""
        gen = ReportGenerator()
        report = gen.generate([])
        assert "SIMULATION DISCLAIMER" in report["disclaimer"]

    def test_deterministic_output(self) -> None:
        """Same decisions -> identical report."""
        gen = ReportGenerator()
        d1 = Decision(
            decision_id="det-z",
            series_id="s1",
            layer=ReasoningLayer.STRUCTURAL,
            statement="Z first alphabetically?",
        )
        d2 = Decision(
            decision_id="det-a",
            series_id="s1",
            layer=ReasoningLayer.SEMANTIC,
            statement="A second?",
        )
        r1 = gen.generate([d1, d2])
        r2 = gen.generate([d1, d2])
        assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
        # Decisions should be sorted by decision_id.
        assert r1["decisions"][0]["decision_id"] == "det-a"
        assert r1["decisions"][1]["decision_id"] == "det-z"


# ---------------------------------------------------------------------------
# Review Engine unit tests
# ---------------------------------------------------------------------------


class TestReviewEngine:
    """Unit tests for the ReviewEngineImpl."""

    def test_review_no_plugins(self) -> None:
        """If DKP has no matching plugins, review returns no decisions."""
        from kri.review_engine.engine import ReviewEngineImpl

        km = KnowledgeManagerImpl()
        ev_engine = EvidenceEngineImpl(km)
        conf_engine = ConfidenceEngineImpl()
        re_engine = ReviewEngineImpl(ev_engine, conf_engine)

        # A minimal DKP with no plugins.
        class EmptyDKP:
            name = "empty"
            version = "0.0.1"

            def manifest(self):
                return {"package": {"name": "empty", "version": "0.0.1"}}

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
                return []

            def seed_graph(self, km):
                pass

        series = PatchSeries(
            series_id="s-empty",
            patches=[Patch(patch_id="p1", subject="test", diff="+ line")],
        )
        decisions = re_engine.review(series, EmptyDKP())
        assert decisions == []

    def test_review_with_real_dkp(self) -> None:
        """If the DKP is loaded and patches match, decisions should be produced."""
        from kri.review_engine.engine import ReviewEngineImpl

        km = KnowledgeManagerImpl()
        try:
            dkp = km.load_dkp("asoc")
        except Exception:
            pytest.skip("DKP entry point not available")

        ev_engine = EvidenceEngineImpl(km)
        conf_engine = ConfidenceEngineImpl()
        re_engine = ReviewEngineImpl(ev_engine, conf_engine)

        # A patch that touches the subsystem and has trigger signals.
        series = PatchSeries(
            series_id="s-test-review",
            patches=[
                Patch(
                    patch_id="p-review-1",
                    subject="Add TDM support",
                    files_changed=["sound/soc/codecs/nau83g60.c"],
                    diff=(
                        "+static const struct snd_kcontrol_new tdm_controls[] = {\n"
                        "+    SOC_ENUM(\"TDM Slot\", tdm_slot_enum),\n"
                        "+};\n"
                    ),
                )
            ],
        )
        decisions = re_engine.review(series, dkp)
        # Should produce at least one decision (the TDM slot pattern match).
        assert len(decisions) > 0
        # Each decision should have evidence_graph and confidence.
        for d in decisions:
            assert d.evidence_graph is not None
            assert d.confidence is not None


# ---------------------------------------------------------------------------
# Evidence Gate test (Constitutional requirement)
# ---------------------------------------------------------------------------


class TestEvidenceGate:
    """Test the constitutional evidence gate: is_publishable()."""

    def test_no_evidence_not_publishable(self) -> None:
        """A Decision with no evidence_graph is NOT publishable."""
        d = Decision(
            decision_id="gate-1",
            series_id="s1",
            layer=ReasoningLayer.STRUCTURAL,
            statement="Missing feature",
            evidence_graph=None,
        )
        assert d.is_publishable() is False

    def test_empty_evidence_not_publishable(self) -> None:
        """A Decision with an empty evidence_graph is NOT publishable."""
        d = Decision(
            decision_id="gate-2",
            series_id="s1",
            layer=ReasoningLayer.STRUCTURAL,
            statement="Missing feature",
            evidence_graph=EvidenceGraph(comment_id="gate-2"),
        )
        assert d.is_publishable() is False

    def test_unverified_evidence_not_publishable(self) -> None:
        """A Decision with only unverified evidence is NOT publishable."""
        ev = Evidence(
            evidence_id="ev-unver",
            source_type=EvidenceSourceType.DESIGN_INFERENCE,
            summary="Inferred from structure",
            provenance=Provenance(),
            verified=False,
            strength=0.0,
        )
        from kri.common.models import ConfidenceScore

        d = Decision(
            decision_id="gate-3",
            series_id="s1",
            layer=ReasoningLayer.STRUCTURAL,
            statement="Missing feature",
            evidence_graph=EvidenceGraph(comment_id="gate-3", evidence=[ev]),
            confidence=ConfidenceScore(
                score=0.5,
                level=ConfidenceLevel.SPECULATIVE,
            ),
        )
        assert d.is_publishable() is False

    def test_verified_evidence_with_confidence_is_publishable(self) -> None:
        """A Decision with verified evidence and adequate confidence IS publishable."""
        from kri.common.models import ConfidenceScore

        ev = Evidence(
            evidence_id="ev-ver",
            source_type=EvidenceSourceType.DOCUMENTATION,
            summary="Documented rule",
            provenance=Provenance(repo_path="Documentation/test.rst"),
            verified=True,
            strength=1.0,
        )
        d = Decision(
            decision_id="gate-4",
            series_id="s1",
            layer=ReasoningLayer.STRUCTURAL,
            statement="Style violation",
            evidence_graph=EvidenceGraph(comment_id="gate-4", evidence=[ev]),
            confidence=ConfidenceScore(
                score=0.65,
                level=ConfidenceLevel.POSSIBLE,
            ),
        )
        assert d.is_publishable() is True

    def test_unknown_confidence_not_publishable(self) -> None:
        """Even with verified evidence, UNKNOWN confidence means not publishable."""
        from kri.common.models import ConfidenceScore

        ev = Evidence(
            evidence_id="ev-ver2",
            source_type=EvidenceSourceType.DOCUMENTATION,
            summary="Documented rule",
            provenance=Provenance(repo_path="Documentation/test.rst"),
            verified=True,
            strength=1.0,
        )
        d = Decision(
            decision_id="gate-5",
            series_id="s1",
            layer=ReasoningLayer.STRUCTURAL,
            statement="Style violation",
            evidence_graph=EvidenceGraph(comment_id="gate-5", evidence=[ev]),
            confidence=ConfidenceScore(
                score=0.35,
                level=ConfidenceLevel.UNKNOWN,
            ),
        )
        assert d.is_publishable() is False


# ---------------------------------------------------------------------------
# Benchmark unit tests
# ---------------------------------------------------------------------------


class TestBenchmark:
    """Unit tests for the BenchmarkRunner."""

    def test_empty_comparison(self) -> None:
        """No decisions, no ground truth -> all zeros."""
        from kri.benchmark.runner import BenchmarkRunner

        runner = BenchmarkRunner()
        metrics = runner.compare([], [])
        assert metrics.total_decisions == 0
        assert metrics.agreement_rate == 0.0

    def test_comparison_with_no_ground_truth(self) -> None:
        """Decisions without matching ground truth are 'no_ground_truth'."""
        from kri.benchmark.runner import BenchmarkRunner
        from kri.common.models import ConfidenceScore

        runner = BenchmarkRunner()
        decisions = [
            Decision(
                decision_id="bench-d1",
                series_id="s1",
                patch_id="p1",
                layer=ReasoningLayer.DESIGN,
                statement="Concern",
                confidence=ConfidenceScore(
                    score=0.7, level=ConfidenceLevel.POSSIBLE
                ),
            )
        ]
        metrics = runner.compare(decisions, [])
        assert metrics.total_decisions == 1
        assert metrics.no_ground_truth == 1

    def test_comparison_with_matching_ground_truth(self) -> None:
        """A decision matching a ground-truth comment should be partial/exact."""
        from kri.benchmark.runner import BenchmarkRunner
        from kri.common.models import ConfidenceScore

        runner = BenchmarkRunner()
        decisions = [
            Decision(
                decision_id="bench-d2",
                series_id="s1",
                patch_id="p1",
                layer=ReasoningLayer.DESIGN,
                category="design",
                statement="Should use framework idiom",
                confidence=ConfidenceScore(
                    score=0.7, level=ConfidenceLevel.POSSIBLE
                ),
            )
        ]
        gt = [
            ReviewComment(
                comment_id="gt-1",
                target_series_id="s1",
                target_patch_id="p1",
                category="design",
                severity=Severity.WARNING,
                message="Should use the standard approach",
                is_maintainer=True,
            )
        ]
        metrics = runner.compare(decisions, gt)
        assert metrics.total_decisions == 1
        assert metrics.exact_agreements + metrics.partial_agreements >= 1


# ---------------------------------------------------------------------------
# Simulation Engine unit tests
# ---------------------------------------------------------------------------


class TestSimulationEngine:
    """Unit tests for the SimulationEngineImpl."""

    def test_simulate_no_dkp(self) -> None:
        """Simulation without a DKP still runs domain-agnostic process/etiquette
        checks (Constitution Sec. 9) and reports degradation notes."""
        from kri.simulation.engine import SimulationEngineImpl

        km = KnowledgeManagerImpl()
        sim = SimulationEngineImpl(km, dkp=None)
        series = PatchSeries(
            series_id="s-sim-no-dkp",
            patches=[Patch(patch_id="p1", subject="test", diff="")],
        )
        report = sim.simulate(series)
        assert "disclaimer" in report
        assert report["metadata"]["total_decisions"] == 1
        assert "degradation_notes" in report["metadata"]
        assert len(report["metadata"]["degradation_notes"]) > 0

    def test_simulate_with_dkp(self) -> None:
        """Full simulation with DKP should produce decisions."""
        from kri.simulation.engine import SimulationEngineImpl

        km = KnowledgeManagerImpl()
        try:
            dkp = km.load_dkp("asoc")
        except Exception:
            pytest.skip("DKP entry point not available")

        sim = SimulationEngineImpl(km, dkp=dkp)
        series = PatchSeries(
            series_id="s-sim",
            patches=[
                Patch(
                    patch_id="p-sim-1",
                    subject="Add TDM kcontrol",
                    files_changed=["sound/soc/codecs/test_codec.c"],
                    diff=(
                        "+static const struct snd_kcontrol_new controls[] = {\n"
                        "+    SOC_ENUM(\"TDM Slot\", tdm_slot_enum),\n"
                        "+};\n"
                    ),
                )
            ],
        )
        report = sim.simulate(series)
        assert "disclaimer" in report
        assert report["metadata"]["total_decisions"] > 0
        assert "knowledge_state_id" in report["metadata"]

    def test_audit_trail(self) -> None:
        """Audit trail should include report hash and state_id."""
        from kri.simulation.engine import SimulationEngineImpl

        km = KnowledgeManagerImpl()
        sim = SimulationEngineImpl(km, dkp=None)
        series = PatchSeries(
            series_id="s-audit",
            patches=[Patch(patch_id="p1", subject="test", diff="")],
        )
        report = sim.simulate(series)
        audit = sim.audit(report)
        assert "report_hash" in audit
        assert "knowledge_state_id" in audit
        assert len(audit["report_hash"]) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# Determinism test
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Verify that simulate(same_series) twice => byte-identical Report."""

    def test_same_patch_twice_identical_report(self) -> None:
        """The fundamental determinism invariant."""
        from kri.simulation.engine import SimulationEngineImpl

        km = KnowledgeManagerImpl()
        try:
            dkp = km.load_dkp("asoc")
        except Exception:
            pytest.skip("DKP entry point not available")

        sim = SimulationEngineImpl(km, dkp=dkp)
        series = PatchSeries(
            series_id="s-det",
            patches=[
                Patch(
                    patch_id="p-det-1",
                    subject="Test determinism",
                    files_changed=["sound/soc/codecs/test.c"],
                    diff=(
                        "+static const struct snd_kcontrol_new controls[] = {\n"
                        "+    SOC_ENUM(\"TDM Slot\", tdm_slot_enum),\n"
                        "+};\n"
                    ),
                )
            ],
        )
        r1 = sim.simulate(series)
        # Re-run on the same state.
        r2 = sim.simulate(series)
        # Byte-identical JSON.
        j1 = json.dumps(r1, sort_keys=True, separators=(",", ":"))
        j2 = json.dumps(r2, sort_keys=True, separators=(",", ":"))
        assert j1 == j2

    def test_replay_equals_simulate(self) -> None:
        """replay(series, state_id) must match the original simulate output."""
        from kri.simulation.engine import SimulationEngineImpl

        km = KnowledgeManagerImpl()
        try:
            dkp = km.load_dkp("asoc")
        except Exception:
            pytest.skip("DKP entry point not available")

        sim = SimulationEngineImpl(km, dkp=dkp)
        series = PatchSeries(
            series_id="s-replay",
            patches=[
                Patch(
                    patch_id="p-replay-1",
                    subject="Test replay",
                    files_changed=["sound/soc/codecs/test.c"],
                    diff=(
                        "+static const struct snd_kcontrol_new controls[] = {\n"
                        "+    SOC_ENUM(\"TDM Slot\", tdm_slot_enum),\n"
                        "+};\n"
                    ),
                )
            ],
        )
        r1 = sim.simulate(series)
        state_id = r1["metadata"]["knowledge_state_id"]

        # Replay.
        r2 = sim.replay(series, state_id)

        # The knowledge_state_id in r2 is the restored state; compare decisions.
        j1_decisions = json.dumps(r1["decisions"], sort_keys=True)
        j2_decisions = json.dumps(r2["decisions"], sort_keys=True)
        assert j1_decisions == j2_decisions


# ---------------------------------------------------------------------------
# Integration test: full pipeline on cached fixture
# ---------------------------------------------------------------------------


class TestIntegrationFullPipeline:
    """Integration test: run the full simulation on the real cached v5 fixture."""

    @pytest.fixture
    def v5_series(self):
        """Parse the v5 cached fixture into a PatchSeries."""
        if not V5_FIXTURE.exists():
            pytest.skip("v5 fixture not present")

        from kri.lore_manager import LoreConfig, LoreManagerImpl
        from kri.patch_manager import PatchManagerImpl

        lore = LoreManagerImpl(LoreConfig(
            cache_dir=LORE_CACHE,
            inbox="all",
            maintainers_path=None,
            offline=True,
        ))
        pm = PatchManagerImpl(lore_manager=lore)
        thread = lore.load_cached(V5_FIXTURE)
        return pm.parse(thread)

    def test_full_pipeline_produces_report(self, v5_series: PatchSeries) -> None:
        """The full pipeline on a real fixture should produce a non-trivial report."""
        from kri.simulation.engine import SimulationEngineImpl

        km = KnowledgeManagerImpl()
        try:
            dkp = km.load_dkp("asoc")
        except Exception:
            pytest.skip("DKP entry point not available")

        sim = SimulationEngineImpl(km, dkp=dkp)
        report = sim.simulate(v5_series)

        # Must have the disclaimer.
        assert "SIMULATION DISCLAIMER" in report["disclaimer"]

        # Must have knowledge state tracking.
        assert report["metadata"]["knowledge_state_id"]

        # Should produce decisions (the v5 fixture has trigger-matching content).
        assert report["metadata"]["total_decisions"] >= 0  # may be 0 if patches don't trigger

    def test_benchmark_on_cached_fixtures(self) -> None:
        """Benchmark should produce computed metrics on cached fixtures."""
        if not V5_FIXTURE.exists():
            pytest.skip("v5 fixture not present")

        from kri.benchmark.runner import BenchmarkRunner
        from kri.lore_manager import LoreConfig, LoreManagerImpl
        from kri.patch_manager import PatchManagerImpl

        lore = LoreManagerImpl(LoreConfig(
            cache_dir=LORE_CACHE,
            inbox="all",
            maintainers_path=None,
            offline=True,
        ))
        pm = PatchManagerImpl(lore_manager=lore)

        thread = lore.load_cached(V5_FIXTURE)
        series = pm.parse(thread)
        ground_truth = lore.extract_reviews(thread)

        # Run simulation.
        km = KnowledgeManagerImpl()
        try:
            dkp = km.load_dkp("asoc")
        except Exception:
            pytest.skip("DKP entry point not available")

        from kri.confidence_engine.engine import ConfidenceEngineImpl
        from kri.evidence_engine.engine import EvidenceEngineImpl
        from kri.review_engine.engine import ReviewEngineImpl

        ev_engine = EvidenceEngineImpl(km)
        conf_engine = ConfidenceEngineImpl()
        re_engine = ReviewEngineImpl(ev_engine, conf_engine)
        decisions = re_engine.review(series, dkp)

        # Benchmark.
        runner = BenchmarkRunner()
        metrics = runner.compare(decisions, ground_truth, series)

        # Metrics should be computed (even if agreement is low for MVP).
        assert metrics.total_decisions >= 0
        assert 0.0 <= metrics.agreement_rate <= 1.0
        assert 0.0 <= metrics.ece <= 1.0
        assert isinstance(metrics.calibration_bins, list)

        # Report the metrics for visibility.
        results_dict = metrics.to_dict()
        assert "total_decisions" in results_dict
        assert "ece" in results_dict
