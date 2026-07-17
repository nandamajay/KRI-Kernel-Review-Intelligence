"""WP-9.2a Sub-commit 4: Maintainer-idiomatic report template tests.

Proves:
1. render_maintainer_comment() produces maintainer-style "> quoted" output.
2. render_maintainer_comment() degrades gracefully with missing optional fields.
3. ReportGenerator.generate(format="maintainer") includes maintainer_comments.
4. ReportGenerator.generate(format="json") does NOT include maintainer_comments.
"""

from __future__ import annotations

from kri.common.models import (
    AlternativeRecommendation,
    ConfidenceLevel,
    ConfidenceScore,
    Decision,
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    HunkCitation,
    Provenance,
    ReasoningLayer,
    Rule,
    RuleType,
    Severity,
)
from kri.report.generator import ReportGenerator
from kri.report.maintainer_template import render_maintainer_comment


def _make_full_decision() -> Decision:
    """Create a fully-populated publishable decision for testing."""
    eg = EvidenceGraph(
        comment_id="d-full",
        hunk_citation=HunkCitation(
            patch_id="p-1",
            file="sound/soc/codecs/test.c",
            line_start=3,
            line_end=5,
            verbatim_lines=[
                "\tpriv->buf = kzalloc(BUF_SIZE, GFP_KERNEL);",
                "\tif (!priv->buf)",
                "\t\treturn -ENOMEM;",
            ],
        ),
        alternative_recommendation=AlternativeRecommendation(
            snippet="p->buf = devm_kzalloc(dev, BUF_SIZE, GFP_KERNEL);",
            language="c",
            rationale="devm_ allocation auto-frees on driver detach.",
        ),
        alternative_precedents=[
            'ba9ea6b3d282 ("ASoC: mediatek: mt8183: Fix probe resource cleanup")',
            '44a4b0e62bcb ("ASoC: mediatek: mt8192 probe cleanup")',
        ],
        subsystem_rule=Rule(
            rule_id="asoc-resume-must-clean-up",
            category="error_paths",
            rule_type=RuleType.SOFT,
            description="Resume resources must have matching cleanup.",
        ),
        evidence=[
            Evidence(
                evidence_id="ev-1",
                source_type=EvidenceSourceType.REVIEW_DISCUSSION,
                summary="Mark Brown nacked resume without cleanup",
                provenance=Provenance(
                    source_url="https://lore.kernel.org/all/test-msg-id/",
                ),
                verified=True,
                strength=0.8,
            ),
        ],
    )
    return Decision(
        decision_id="d-full",
        series_id="s-1",
        patch_id="p-1",
        layer=ReasoningLayer.SEMANTIC,
        severity=Severity.WARNING,
        location="sound/soc/codecs/test.c",
        statement="Resume handler allocates without cleanup path.",
        rule_id="asoc-resume-must-clean-up",
        evidence_graph=eg,
        confidence=ConfidenceScore(
            score=0.65,
            level=ConfidenceLevel.POSSIBLE,
            explanation="",
        ),
    )


def _make_minimal_decision() -> Decision:
    """Decision with no hunk, no recommendation, no precedents."""
    eg = EvidenceGraph(
        comment_id="d-min",
        evidence=[
            Evidence(
                evidence_id="ev-2",
                source_type=EvidenceSourceType.DOCUMENTATION,
                summary="Documented requirement",
                provenance=Provenance(repo_path="Documentation/sound/soc/codec.rst"),
                verified=True,
                strength=1.0,
            ),
        ],
    )
    return Decision(
        decision_id="d-min",
        series_id="s-1",
        patch_id="p-1",
        layer=ReasoningLayer.STRUCTURAL,
        severity=Severity.INFO,
        location="sound/soc/codecs/test.c",
        statement="Missing devm registration pattern.",
        evidence_graph=eg,
        confidence=ConfidenceScore(
            score=0.85,
            level=ConfidenceLevel.LIKELY,
            explanation="",
        ),
    )


def test_render_full_maintainer_comment() -> None:
    """Full decision renders with hunk quote, suggestion, precedent, ref."""
    decision = _make_full_decision()
    comment = render_maintainer_comment(decision)

    # Hunk quoted with > prefix
    assert "> \tpriv->buf = kzalloc(BUF_SIZE, GFP_KERNEL);" in comment
    # Location (with line range from HunkCitation: line_start=3, line_end=5)
    assert "At sound/soc/codecs/test.c:3-5:" in comment
    # Statement
    assert "Resume handler allocates without cleanup path." in comment
    # Suggested fix block
    assert "Suggested fix:" in comment
    assert "```c" in comment
    assert "devm_kzalloc" in comment
    assert "Rationale:" in comment
    # Precedent (plural — two entries)
    assert "Precedents:" in comment
    assert "ba9ea6b3d282" in comment
    # Confidence note (below 0.80)
    assert "[Confidence: possible" in comment
    # Evidence reference
    assert "Ref: https://lore.kernel.org/all/test-msg-id/" in comment


def test_render_minimal_maintainer_comment() -> None:
    """Minimal decision renders gracefully without optional fields."""
    decision = _make_minimal_decision()
    comment = render_maintainer_comment(decision)

    # Statement present
    assert "Missing devm registration pattern." in comment
    # Location present
    assert "At sound/soc/codecs/test.c:" in comment
    # No hunk (no > lines)
    assert not any(line.startswith("> ") for line in comment.splitlines())
    # No suggested fix
    assert "Suggested fix:" not in comment
    # No precedent
    assert "Precedent:" not in comment
    # No confidence note (score >= 0.80)
    assert "[Confidence:" not in comment
    # Evidence ref present
    assert "Ref: Documentation/sound/soc/codec.rst" in comment


def test_generate_maintainer_format_includes_comments() -> None:
    """generate(format='maintainer') includes maintainer_comments list."""
    gen = ReportGenerator()
    decision = _make_full_decision()
    report = gen.generate([decision], format="maintainer")

    assert "maintainer_comments" in report
    assert len(report["maintainer_comments"]) == 1
    mc = report["maintainer_comments"][0]
    assert mc["decision_id"] == "d-full"
    assert mc["location"] == "sound/soc/codecs/test.c"
    assert "kzalloc" in mc["comment"]


def test_generate_json_format_no_comments() -> None:
    """generate(format='json') does NOT include maintainer_comments."""
    gen = ReportGenerator()
    decision = _make_full_decision()
    report = gen.generate([decision], format="json")

    assert "maintainer_comments" not in report
    # But regular decisions are still there
    assert len(report["decisions"]) == 1


def test_location_single_line_from_hunk_citation() -> None:
    """When line_start == line_end, location is 'file:N:'."""
    eg = EvidenceGraph(
        comment_id="d-single",
        hunk_citation=HunkCitation(
            patch_id="p-1",
            file="sound/soc/codecs/foo.c",
            line_start=7,
            line_end=7,
            verbatim_lines=["\tret = do_thing();"],
        ),
        evidence=[
            Evidence(
                evidence_id="ev-sl",
                source_type=EvidenceSourceType.DOCUMENTATION,
                summary="Doc reference",
                provenance=Provenance(repo_path="Documentation/sound/soc/codec.rst"),
                verified=True,
                strength=1.0,
            ),
        ],
    )
    decision = Decision(
        decision_id="d-single",
        series_id="s-1",
        patch_id="p-1",
        layer=ReasoningLayer.STRUCTURAL,
        severity=Severity.WARNING,
        location="sound/soc/codecs/foo.c",
        statement="Missing error check.",
        evidence_graph=eg,
        confidence=ConfidenceScore(
            score=0.85,
            level=ConfidenceLevel.LIKELY,
            explanation="",
        ),
    )
    comment = render_maintainer_comment(decision)
    assert "At sound/soc/codecs/foo.c:7:" in comment


def test_location_no_hunk_falls_back() -> None:
    """Without HunkCitation, location header has no line number."""
    eg = EvidenceGraph(
        comment_id="d-nohunk",
        evidence=[
            Evidence(
                evidence_id="ev-nh",
                source_type=EvidenceSourceType.DOCUMENTATION,
                summary="Doc reference",
                provenance=Provenance(repo_path="Documentation/sound/soc/codec.rst"),
                verified=True,
                strength=1.0,
            ),
        ],
    )
    decision = Decision(
        decision_id="d-nohunk",
        series_id="s-1",
        patch_id="p-1",
        layer=ReasoningLayer.STRUCTURAL,
        severity=Severity.INFO,
        location="sound/soc/codecs/bar.c",
        statement="Style note.",
        evidence_graph=eg,
        confidence=ConfidenceScore(
            score=0.90,
            level=ConfidenceLevel.LIKELY,
            explanation="",
        ),
    )
    comment = render_maintainer_comment(decision)
    assert "At sound/soc/codecs/bar.c:" in comment
    # No line numbers in the location
    assert "bar.c:1" not in comment
