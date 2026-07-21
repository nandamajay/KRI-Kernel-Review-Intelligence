"""Integration tests for WP-CP1: checkpatch wiring into IntelligentReviewEngine.

Verifies that:
- IntelligentReviewEngine accepts a static_analysis parameter.
- run_checkpatch() is called before agent threads and findings are stored.
- Findings are injected as {static_findings} prompt context in both agents.
- PatchReview.metadata carries checkpatch_findings for real findings.
- IntelligentReport.metadata carries checkpatch_finding_count.
- Degraded findings (tool unavailable) do NOT appear in metadata or prompt.
- format_static_findings() produces correct output.
- format_static_findings() returns "" when all findings are degraded.
- The engine works normally when static_analysis=None (no regression).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kri.common.models import Patch, PatchSeries
from kri.llm.models import IntelligentReport, PatchReview
from kri.llm.prompts import format_static_findings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_DIFF = (
    "diff --git a/sound/soc/foo.c b/sound/soc/foo.c\n"
    "index 1234567..89abcde 100644\n"
    "--- a/sound/soc/foo.c\n"
    "+++ b/sound/soc/foo.c\n"
    "@@ -1,3 +1,5 @@\n"
    " int foo(void)\n"
    " {\n"
    "+\tint x=1;\n"
    "+\tif(x) printk(\"hello\\n\");\n"
    " \treturn 0;\n"
    " }\n"
)

REAL_FINDING = {
    "tool": "checkpatch",
    "file": "sound/soc/foo.c",
    "line": 3,
    "category": "spacing",
    "severity": "blocker",
    "message": "spaces required around that '='",
    "patch_id": "p1",
    "degraded": False,
}

DEGRADED_FINDING = {
    "tool": "checkpatch",
    "file": None,
    "line": 0,
    "category": "tool_unavailable",
    "severity": "info",
    "message": "checkpatch.pl not found in tree",
    "patch_id": None,
    "degraded": True,
}


def _make_series(patch_id: str = "p1") -> PatchSeries:
    patch = Patch(
        patch_id=patch_id,
        subject="ASoC: foo: add codec driver",
        author="dev@example.com",
        diff=MINIMAL_DIFF,
        commit_message="ASoC: foo: add codec driver\n\nAdds a basic codec driver.",
    )
    return PatchSeries(series_id="s1", patches=[patch])


def _mock_llm_client() -> MagicMock:
    """LLM client that always returns an empty findings array (offline)."""
    client = MagicMock()
    resp = MagicMock()
    resp.content = "[]"
    client.complete_json.return_value = []
    client.complete.return_value = resp
    client._cfg = MagicMock()
    client._cfg.model = "test-model"
    client.stats = {}
    return client


def _mock_static_analysis(findings: list[dict]) -> MagicMock:
    sa = MagicMock()
    sa.run_checkpatch.return_value = findings
    return sa


# ---------------------------------------------------------------------------
# format_static_findings() unit tests
# ---------------------------------------------------------------------------

def test_format_static_findings_real_finding() -> None:
    result = format_static_findings([REAL_FINDING])
    assert "Checkpatch Findings" in result
    assert "sound/soc/foo.c:3" in result
    assert "BLOCKER" in result
    assert "spacing" in result
    assert "spaces required around" in result


def test_format_static_findings_empty_list() -> None:
    assert format_static_findings([]) == ""


def test_format_static_findings_only_degraded() -> None:
    """Degraded-only findings must not produce any prompt context."""
    assert format_static_findings([DEGRADED_FINDING]) == ""


def test_format_static_findings_mixed_skips_degraded() -> None:
    result = format_static_findings([DEGRADED_FINDING, REAL_FINDING])
    assert "Checkpatch Findings" in result
    assert "tool_unavailable" not in result
    assert "checkpatch.pl not found" not in result
    assert "sound/soc/foo.c" in result


def test_format_static_findings_caps_at_20() -> None:
    findings = [
        {**REAL_FINDING, "line": i, "message": f"issue {i}", "degraded": False}
        for i in range(30)
    ]
    result = format_static_findings(findings)
    # Only first 20 should appear
    assert result.count("- [") == 20


def test_format_static_findings_severity_labels() -> None:
    for sev in ("blocker", "warning", "info"):
        f = {**REAL_FINDING, "severity": sev, "degraded": False}
        result = format_static_findings([f])
        assert sev.upper() in result


# ---------------------------------------------------------------------------
# IntelligentReviewEngine wiring tests (offline, mocked LLM)
# ---------------------------------------------------------------------------

def test_engine_accepts_static_analysis_param() -> None:
    """Engine must accept static_analysis without error."""
    from kri.llm.reviewer import IntelligentReviewEngine
    sa = _mock_static_analysis([REAL_FINDING])
    engine = IntelligentReviewEngine(
        client=_mock_llm_client(), static_analysis=sa
    )
    assert engine._static_analysis is sa


def test_engine_calls_run_checkpatch_per_patch() -> None:
    """run_checkpatch() must be called once per patch in the series."""
    from kri.llm.reviewer import IntelligentReviewEngine
    sa = _mock_static_analysis([REAL_FINDING])
    engine = IntelligentReviewEngine(client=_mock_llm_client(), static_analysis=sa)
    series = _make_series()
    engine.review(series)
    sa.run_checkpatch.assert_called_once()


def test_engine_checkpatch_finding_in_patch_metadata() -> None:
    """Real checkpatch findings must appear in PatchReview.metadata."""
    from kri.llm.reviewer import IntelligentReviewEngine
    sa = _mock_static_analysis([REAL_FINDING])
    engine = IntelligentReviewEngine(client=_mock_llm_client(), static_analysis=sa)
    report = engine.review(_make_series())
    pr = report.patches[0]
    assert "checkpatch_findings" in pr.metadata
    findings = pr.metadata["checkpatch_findings"]
    assert len(findings) == 1
    assert findings[0]["category"] == "spacing"
    assert findings[0]["severity"] == "blocker"


def test_engine_checkpatch_count_in_report_metadata() -> None:
    """checkpatch_finding_count must be aggregated in IntelligentReport.metadata."""
    from kri.llm.reviewer import IntelligentReviewEngine
    sa = _mock_static_analysis([REAL_FINDING])
    engine = IntelligentReviewEngine(client=_mock_llm_client(), static_analysis=sa)
    report = engine.review(_make_series())
    assert report.metadata["checkpatch_finding_count"] == 1


def test_engine_degraded_finding_excluded_from_metadata() -> None:
    """Degraded findings must not appear in PatchReview.metadata."""
    from kri.llm.reviewer import IntelligentReviewEngine
    sa = _mock_static_analysis([DEGRADED_FINDING])
    engine = IntelligentReviewEngine(client=_mock_llm_client(), static_analysis=sa)
    report = engine.review(_make_series())
    pr = report.patches[0]
    # Either no key at all or empty list
    findings = pr.metadata.get("checkpatch_findings", [])
    assert findings == []


def test_engine_degraded_finding_count_is_zero() -> None:
    from kri.llm.reviewer import IntelligentReviewEngine
    sa = _mock_static_analysis([DEGRADED_FINDING])
    engine = IntelligentReviewEngine(client=_mock_llm_client(), static_analysis=sa)
    report = engine.review(_make_series())
    assert report.metadata["checkpatch_finding_count"] == 0


def test_engine_no_static_analysis_works() -> None:
    """Engine with static_analysis=None must behave identically to before WP-CP1."""
    from kri.llm.reviewer import IntelligentReviewEngine
    engine = IntelligentReviewEngine(client=_mock_llm_client(), static_analysis=None)
    report = engine.review(_make_series())
    assert isinstance(report, IntelligentReport)
    assert report.metadata.get("checkpatch_finding_count", 0) == 0


def test_engine_checkpatch_exception_does_not_crash() -> None:
    """If run_checkpatch() raises, the engine must continue and return a report."""
    from kri.llm.reviewer import IntelligentReviewEngine
    sa = MagicMock()
    sa.run_checkpatch.side_effect = RuntimeError("checkpatch exploded")
    engine = IntelligentReviewEngine(client=_mock_llm_client(), static_analysis=sa)
    report = engine.review(_make_series())
    assert isinstance(report, IntelligentReport)
    # No findings → count is 0
    assert report.metadata.get("checkpatch_finding_count", 0) == 0


# ---------------------------------------------------------------------------
# Prompt injection tests — verify {static_findings} reaches the LLM prompt
# ---------------------------------------------------------------------------

def test_static_findings_injected_into_code_quality_prompt() -> None:
    """CodeQualityAgent must receive checkpatch findings in its prompt."""
    from kri.llm.agents import CodeQualityAgent

    sa_findings = [REAL_FINDING]
    patch = Patch(
        patch_id="p1",
        subject="test",
        diff=MINIMAL_DIFF,
        commit_message="test",
    )
    series = _make_series()
    client = _mock_llm_client()
    agent = CodeQualityAgent(client)
    agent.review(patch, series, static_findings=sa_findings)

    # Extract the prompt that was sent to the LLM
    call_args = client.complete_json.call_args
    prompt = call_args[0][0][0]["content"]
    assert "Checkpatch Findings" in prompt
    assert "sound/soc/foo.c:3" in prompt


def test_static_findings_injected_into_subsystem_prompt() -> None:
    """SubsystemExpertAgent must receive checkpatch findings in its prompt."""
    from kri.llm.agents import SubsystemExpertAgent

    sa_findings = [REAL_FINDING]
    patch = Patch(
        patch_id="p1",
        subject="test",
        diff=MINIMAL_DIFF,
        commit_message="test",
        files_changed=["sound/soc/foo.c"],
    )
    series = _make_series()
    client = _mock_llm_client()
    agent = SubsystemExpertAgent(client)
    agent.review(patch, series, static_findings=sa_findings)

    call_args = client.complete_json.call_args
    prompt = call_args[0][0][0]["content"]
    assert "Checkpatch Findings" in prompt
    assert "BLOCKER" in prompt


def test_no_static_findings_no_checkpatch_section_in_prompt() -> None:
    """When no findings, {static_findings} must expand to '' — no section header."""
    from kri.llm.agents import CodeQualityAgent

    patch = Patch(
        patch_id="p1",
        subject="test",
        diff=MINIMAL_DIFF,
        commit_message="test",
    )
    series = _make_series()
    client = _mock_llm_client()
    agent = CodeQualityAgent(client)
    agent.review(patch, series, static_findings=[])

    call_args = client.complete_json.call_args
    prompt = call_args[0][0][0]["content"]
    assert "Checkpatch Findings" not in prompt


def test_degraded_only_findings_no_checkpatch_section_in_prompt() -> None:
    """Degraded-only findings must not pollute the LLM prompt."""
    from kri.llm.agents import CodeQualityAgent

    patch = Patch(
        patch_id="p1",
        subject="test",
        diff=MINIMAL_DIFF,
        commit_message="test",
    )
    series = _make_series()
    client = _mock_llm_client()
    agent = CodeQualityAgent(client)
    agent.review(patch, series, static_findings=[DEGRADED_FINDING])

    call_args = client.complete_json.call_args
    prompt = call_args[0][0][0]["content"]
    assert "Checkpatch Findings" not in prompt
    assert "tool_unavailable" not in prompt


# ---------------------------------------------------------------------------
# Real checkpatch integration (requires kernel tree)
# ---------------------------------------------------------------------------

def test_engine_real_checkpatch_on_bad_diff(kernel_path) -> None:
    """End-to-end: bad diff produces checkpatch findings in the report."""
    from kri.llm.reviewer import IntelligentReviewEngine
    from kri.static_analysis import StaticAnalysisConfig, StaticAnalysisManagerImpl

    sa = StaticAnalysisManagerImpl(StaticAnalysisConfig(repo_path=kernel_path))
    engine = IntelligentReviewEngine(client=_mock_llm_client(), static_analysis=sa)
    report = engine.review(_make_series())

    # The MINIMAL_DIFF has "int x=1;" — checkpatch should flag it
    assert report.metadata["checkpatch_finding_count"] > 0
    pr = report.patches[0]
    findings = pr.metadata.get("checkpatch_findings", [])
    assert len(findings) > 0
    assert all(not f["degraded"] for f in findings)
    assert any(f["severity"] == "blocker" for f in findings)


def test_engine_real_checkpatch_count_matches_patch_metadata(kernel_path) -> None:
    """checkpatch_finding_count in report must equal sum of per-patch findings."""
    from kri.llm.reviewer import IntelligentReviewEngine
    from kri.static_analysis import StaticAnalysisConfig, StaticAnalysisManagerImpl

    sa = StaticAnalysisManagerImpl(StaticAnalysisConfig(repo_path=kernel_path))
    engine = IntelligentReviewEngine(client=_mock_llm_client(), static_analysis=sa)
    report = engine.review(_make_series())

    expected = sum(
        len(pr.metadata.get("checkpatch_findings", []))
        for pr in report.patches
    )
    assert report.metadata["checkpatch_finding_count"] == expected
