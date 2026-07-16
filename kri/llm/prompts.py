"""Prompt templates for kernel patch review agents."""

from __future__ import annotations

SYSTEM_KERNEL_REVIEWER = """\
You are an experienced Linux kernel maintainer reviewing patch submissions. \
You provide specific, actionable feedback referencing exact lines in the diff. \
You are direct but constructive, like real kernel reviewers on lore.kernel.org.

Key principles:
- Reference specific lines (e.g., "In file.c, line N")
- Explain WHY something is wrong, not just that it is
- Suggest the correct approach when possible
- Distinguish between blockers (must fix) and suggestions (nice to have)
- Never fabricate issues; only comment on things visible in the diff
- If the code looks correct, say so — do not invent problems
- Do NOT flag naming/identifier "inconsistencies" between a driver's short name, a \
compatible string, a filename, and a chip's marketing name or series title unless it \
causes a genuine, verifiable defect (e.g. a DT compatible string that will not match \
any binding, or a symbol collision). A stylistic guess about what a name "should" be \
is not a defect — do not raise it as one, and never as a blocker
"""

SUMMARIZE_PATCH_PROMPT = """\
Analyze the following kernel patch and provide a structured summary.

## Commit Message
{commit_message}

## Files Changed
{files_changed}

## Cover Letter
{cover_letter}

## Diff
{diff}

Respond with a JSON object:
{{
  "what_it_does": "Plain-English explanation of what this patch does (2-3 sentences)",
  "subsystem": "The kernel subsystem this belongs to (e.g., drivers/net, fs/ext4)",
  "components_touched": ["list", "of", "logical", "components"],
  "change_type": "new_driver|bugfix|refactor|feature|cleanup|dt_binding",
  "risk_areas": ["potential risk areas identified"]
}}
"""

REVIEW_CODE_QUALITY_PROMPT = """\
Review the following kernel patch for correctness issues. Focus on:
- Missing error handling (unchecked return values, missing IS_ERR checks)
- Resource leaks (memory, locks, clocks, regulators not freed on error paths)
- NULL pointer dereferences
- Use-after-free or double-free
- Race conditions or missing synchronization
- Integer overflow/underflow
- Missing bounds checks
- Incorrect API usage

{domain_context}

## Commit Message
{commit_message}

## Diff (with line numbers on the new-file side)
{annotated_diff}

Respond with a JSON array of issues found. For each issue:
{{
  "file_path": "path/to/file.c",
  "line_number": <new-side line number where the issue is>,
  "category": "bug|error_handling|resource_leak|race|null_deref|api_misuse",
  "severity": "blocker|warning|info",
  "message": "Clear explanation of the issue and how to fix it",
  "suggestion": "Corrected code snippet if applicable (optional, null if none)",
  "confidence": <0.0-1.0 how sure you are>,
  "reasoning": "Why this is an issue"
}}

If you find no issues, return an empty array [].
Only report issues you are confident about (>= 0.5). Do not fabricate problems.
"""

REVIEW_SUBSYSTEM_PROMPT = """\
Review this kernel patch for subsystem convention and design issues. Focus on:
- Wrong API used (deprecated function when devm_ variant exists, etc.)
- Subsystem design patterns not followed
- DT binding conventions violated
- Missing or incorrect Kconfig/Makefile integration
- Commit message format issues for this subsystem
- Maintainer preferences and historical review patterns

Do NOT report a "convention" or "api_misuse" issue based on a naming/identifier \
mismatch (driver short name vs. compatible string vs. filename vs. chip marketing \
name) unless you can point to an actual rule, doc, or binding it violates. If you \
cannot cite a concrete convention it breaks, it is not a finding — leave it out.

{domain_context}

## Commit Message
{commit_message}

## Files Changed
{files_changed}

## Diff (with line numbers)
{annotated_diff}

Respond with a JSON array of issues found (same format as code quality review):
{{
  "file_path": "path/to/file.c",
  "line_number": <line number>,
  "category": "api_misuse|design|convention|dt_binding|commit_msg",
  "severity": "blocker|warning|info",
  "message": "What's wrong and what should be done instead",
  "suggestion": "Corrected code or approach (optional)",
  "confidence": <0.0-1.0>,
  "reasoning": "Why this violates conventions"
}}

If the patch follows all conventions correctly, return [].
"""

AGGREGATE_REVIEW_PROMPT = """\
You are writing the final review summary for a kernel patch. Below are the \
findings from multiple analysis passes. Synthesize them into a brief overall \
assessment (2-4 sentences) covering: the patch's overall quality, the most \
important issues found (if any), and whether it is ready to merge or needs revision.

## Patch Summary
{summary}

## Issues Found
{issues_text}

## Rule-Based Findings
{rule_findings}

Write a concise overall assessment paragraph. Be direct like a kernel maintainer.
"""


def annotate_diff_with_line_numbers(diff: str) -> str:
    """Add new-file line numbers to a unified diff for LLM reference.

    Produces output like:
        diff --git a/foo.c b/foo.c
        --- a/foo.c
        +++ b/foo.c
        @@ -10,5 +10,7 @@ context_function
        10:  context line
        11: +added line
        12: +another added line
            -removed line
        12:  more context
    """
    lines = diff.split("\n")
    output: list[str] = []
    new_lineno = 0
    in_hunk = False

    for line in lines:
        if line.startswith("@@"):
            in_hunk = True
            # Parse @@ -old,count +new,count @@
            parts = line.split("+")
            if len(parts) >= 2:
                num_part = parts[1].split(",")[0].split(" ")[0]
                try:
                    new_lineno = int(num_part) - 1
                except ValueError:
                    new_lineno = 0
            output.append(line)
        elif not in_hunk:
            output.append(line)
        elif line.startswith("+"):
            new_lineno += 1
            output.append(f"{new_lineno:4d}: +{line[1:]}")
        elif line.startswith("-"):
            output.append(f"    : -{line[1:]}")
        else:
            new_lineno += 1
            output.append(f"{new_lineno:4d}:  {line}")

    return "\n".join(output)


def build_domain_context(rules: list | None = None, patterns: list | None = None) -> str:
    """Format DKP rules and patterns as LLM prompt context."""
    if not rules and not patterns:
        return ""

    parts: list[str] = []
    if rules:
        parts.append("## Subsystem Rules")
        for r in rules[:10]:
            desc = getattr(r, "description", str(r))
            rtype = getattr(r, "rule_type", "")
            parts.append(f"- [{rtype}] {desc}")

    if patterns:
        parts.append("\n## Known Review Patterns")
        for p in patterns[:10]:
            desc = p.get("description", "") if isinstance(p, dict) else str(p)
            outcome = p.get("outcome", "") if isinstance(p, dict) else ""
            parts.append(f"- [{outcome}] {desc}")

    return "\n".join(parts)
