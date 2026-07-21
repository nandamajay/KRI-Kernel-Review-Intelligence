"""Tests for the central output sanitizer (kri.llm.sanitize).

Verifies that synthetic upstream trailer tags are stripped at the model
boundary from every LLM-generated string field — regardless of the rendering
surface (lore reply, web UI, JSON API, future consumers).
"""

from __future__ import annotations

import pytest

from kri.llm.models import InlineComment, PatchSummary
from kri.llm.sanitize import strip_trailers, strip_trailers_list


# ---------------------------------------------------------------------------
# strip_trailers() unit tests
# ---------------------------------------------------------------------------

ALL_TRAILER_LINES = [
    "Reviewed-by: Some Person <person@example.com>",
    "Acked-by: Some Person <person@example.com>",
    "Tested-by: Some Person <person@example.com>",
    "Signed-off-by: Some Person <person@example.com>",
    "Co-developed-by: Some Person <person@example.com>",
    "Reported-by: Some Person <person@example.com>",
    "Suggested-by: Some Person <person@example.com>",
    "Fixes: abc1234 (\"some commit message\")",
]

PLACEHOLDER_VARIANTS = [
    "Reviewed-by: [Reviewer]",
    "Reviewed-by: <name>",
    "Acked-by: [Maintainer]",
    "Tested-by: <tester@example.com>",
    "Signed-off-by: [Author]",
]

CASE_VARIANTS = [
    "reviewed-by: foo",
    "REVIEWED-BY: foo",
    "Reviewed-By: foo",
    "acked-by: foo",
    "FIXES: abc1234 (commit)",
]


@pytest.mark.parametrize("line", ALL_TRAILER_LINES)
def test_strip_trailers_removes_all_canonical_tags(line: str) -> None:
    result = strip_trailers(line)
    assert result == "", f"Expected empty string, got: {result!r}"


@pytest.mark.parametrize("line", PLACEHOLDER_VARIANTS)
def test_strip_trailers_removes_placeholder_variants(line: str) -> None:
    result = strip_trailers(line)
    assert result == "", f"Expected empty string, got: {result!r}"


@pytest.mark.parametrize("line", CASE_VARIANTS)
def test_strip_trailers_is_case_insensitive(line: str) -> None:
    result = strip_trailers(line)
    assert result == "", f"Expected empty string, got: {result!r}"


def test_strip_trailers_preserves_clean_prose() -> None:
    text = (
        "Should use devm_clk_get() here rather than clk_get() — "
        "the devm variant handles cleanup on error paths correctly."
    )
    assert strip_trailers(text) == text


def test_strip_trailers_removes_trailer_embedded_in_multiline() -> None:
    text = (
        "I would expect the TDM slot configuration to come from the machine driver.\n"
        "Reviewed-by: [Reviewer]\n"
        "This is not a strong reason for a respin on its own."
    )
    result = strip_trailers(text)
    assert "Reviewed-by" not in result
    assert "I would expect" in result
    assert "not a strong reason" in result


def test_strip_trailers_removes_multiple_trailers() -> None:
    text = (
        "Looks reasonable.\n"
        "Reviewed-by: Alice <alice@example.com>\n"
        "Acked-by: Bob <bob@example.com>\n"
        "Tested-by: CI <ci@example.com>\n"
    )
    result = strip_trailers(text)
    assert "Reviewed-by" not in result
    assert "Acked-by" not in result
    assert "Tested-by" not in result
    assert "Looks reasonable" in result


def test_strip_trailers_empty_string() -> None:
    assert strip_trailers("") == ""


def test_strip_trailers_only_trailers_returns_empty() -> None:
    text = "Reviewed-by: [Reviewer]\nAcked-by: <Name>"
    assert strip_trailers(text) == ""


def test_strip_trailers_list() -> None:
    items = [
        "normal risk area",
        "Reviewed-by: [Reviewer]",
        "another risk area",
    ]
    result = strip_trailers_list(items)
    assert result == ["normal risk area", "", "another risk area"]


def test_strip_trailers_does_not_strip_inline_mention() -> None:
    """'Reviewed-by' appearing mid-sentence (not at line start) is NOT a trailer."""
    text = "The patch was discussed but not Reviewed-by anyone formally yet."
    result = strip_trailers(text)
    assert result == text


def test_strip_trailers_strips_with_leading_whitespace() -> None:
    """Trailers with leading spaces (e.g. indented) must also be stripped."""
    text = "  Reviewed-by: [Reviewer]"
    result = strip_trailers(text)
    assert result == ""


# ---------------------------------------------------------------------------
# Model-level sanitization: InlineComment
# ---------------------------------------------------------------------------

def _make_comment(**kwargs) -> InlineComment:
    defaults = dict(
        file_path="sound/soc/foo.c",
        line_number=42,
        message="Should use devm_clk_get() here.",
    )
    defaults.update(kwargs)
    return InlineComment(**defaults)


def test_inline_comment_message_stripped() -> None:
    c = _make_comment(message="Issue found.\nReviewed-by: [Reviewer]")
    assert "Reviewed-by" not in c.message
    assert "Issue found" in c.message


def test_inline_comment_upstream_comment_stripped() -> None:
    c = _make_comment(upstream_comment="Good point.\nAcked-by: <Name>")
    assert "Acked-by" not in (c.upstream_comment or "")
    assert "Good point" in (c.upstream_comment or "")


def test_inline_comment_reasoning_stripped() -> None:
    c = _make_comment(reasoning="This is wrong.\nSigned-off-by: [Author]")
    assert "Signed-off-by" not in c.reasoning
    assert "This is wrong" in c.reasoning


def test_inline_comment_suggestion_stripped() -> None:
    c = _make_comment(suggestion="Use devm_.\nReviewed-by: Bob <bob@example.com>")
    assert "Reviewed-by" not in (c.suggestion or "")
    assert "Use devm_" in (c.suggestion or "")


def test_inline_comment_none_suggestion_unchanged() -> None:
    c = _make_comment(suggestion=None)
    assert c.suggestion is None


def test_inline_comment_clean_fields_unchanged() -> None:
    clean_msg = "Should use devm_clk_get() rather than clk_get()."
    c = _make_comment(message=clean_msg)
    assert c.message == clean_msg


# ---------------------------------------------------------------------------
# Model-level sanitization: PatchSummary
# ---------------------------------------------------------------------------

def test_patch_summary_what_it_does_stripped() -> None:
    s = PatchSummary(
        what_it_does="adds a new codec driver.\nReviewed-by: [Reviewer]"
    )
    assert "Reviewed-by" not in s.what_it_does
    assert "adds a new codec driver" in s.what_it_does


def test_patch_summary_risk_areas_stripped() -> None:
    s = PatchSummary(
        what_it_does="adds codec",
        risk_areas=["probe ordering", "Reviewed-by: [Reviewer]", "DMA alignment"],
    )
    assert all("Reviewed-by" not in r for r in s.risk_areas)
    assert "probe ordering" in s.risk_areas
    assert "DMA alignment" in s.risk_areas


def test_patch_summary_clean_text_unchanged() -> None:
    desc = "adds support for the XYZ CODEC via ASoC driver framework"
    s = PatchSummary(what_it_does=desc)
    assert s.what_it_does == desc


# ---------------------------------------------------------------------------
# Regression: the original failure mode
# ---------------------------------------------------------------------------

def test_regression_lore_reply_excludes_trailers() -> None:
    """Regression guard: the original bug that prompted WP-M1.1.

    A comment with 'Reviewed-by: [Reviewer]' embedded in upstream_comment
    must not appear in the lore reply.
    """
    from kri.common.models import Patch
    from kri.llm.formatter import format_lore_reply

    patch = Patch(
        patch_id="p1",
        subject="ASoC: foo: add codec driver",
        author="dev@example.com",
        diff=(
            "diff --git a/sound/soc/foo.c b/sound/soc/foo.c\n"
            "--- a/sound/soc/foo.c\n"
            "+++ b/sound/soc/foo.c\n"
            "@@ -1,3 +1,4 @@\n"
            " int foo(void) {\n"
            "+\tclk_get(dev, \"mclk\");\n"
            " \treturn 0;\n"
            " }\n"
        ),
    )

    # Simulate what a misbehaving LLM would return — trailer embedded in field
    comment = _make_comment(
        file_path="sound/soc/foo.c",
        line_number=2,
        message="Should use devm_clk_get().",
        upstream_comment=(
            "Should use devm_clk_get() here rather than clk_get() — "
            "the devm variant handles cleanup on error paths correctly.\n"
            "Reviewed-by: [Reviewer]"
        ),
    )

    summary = PatchSummary(
        what_it_does="adds a clock consumer without devm.\nReviewed-by: [Reviewer]",
        risk_areas=["clock leak on error path", "Acked-by: <Name>"],
    )

    reply = format_lore_reply(patch, summary, [comment])

    import re
    trailer_re = re.compile(
        r"(Reviewed-by|Acked-by|Tested-by|Signed-off-by|Co-developed-by"
        r"|Reported-by|Suggested-by|Fixes)\s*:",
        re.IGNORECASE,
    )
    assert not trailer_re.search(reply), (
        f"Trailer tag found in lore reply:\n{reply}"
    )
