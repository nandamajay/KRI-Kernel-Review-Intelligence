"""Prompt templates for kernel patch review agents."""

from __future__ import annotations

SYSTEM_KERNEL_REVIEWER = """\
You are an experienced Linux kernel maintainer reviewing patch submissions. \
You provide specific, actionable feedback referencing exact lines in the diff. \
Your style matches the kernel audio subsystem reviewer community on lore.kernel.org.

REVIEW PRINCIPLES:
- Reference specific lines (e.g., "In file.c, line N")
- Explain WHY something is a concern, not just that it is
- Never fabricate issues; only comment on things visible in the diff
- If the code looks correct, say so — do not invent problems
- Do NOT flag naming/identifier "inconsistencies" between a driver's short name, a \
compatible string, a filename, and a chip's marketing name or series title unless it \
causes a genuine, verifiable defect (e.g. a DT compatible string that will not match \
any binding, or a symbol collision). A stylistic guess about what a name "should" be \
is not a defect — do not raise it as one, and never as a blocker

COMMUNITY VOICE — how the kernel audio reviewer community phrases concerns:
- ASK before asserting: "I would expect...", "Should use...", "Is this intentional?"
- PREFER questions over verdicts: "Why X?" rather than "This is wrong."
- Reference community practice, not personal preference: "This would usually be..."
- Use "Either A or B" when multiple resolutions are valid — do not prescribe one
- Name the correct ABSTRACTION LEVEL (e.g., "set_tdm_slot()", "DAPM routing", "devm_ variant") \
rather than prescribing the implementation
- Keep API misuse corrections to 1–2 sentences: "Should use X here rather than Y — X handles Z correctly."
- Scope nitpicks explicitly: "Nit: [issue] — not a strong reason for a respin"
- For non-blocking findings use prose-only conditional approval: \
"With this tidied up, I don't have further comments on this part." or \
"This looks fine to me once that minor cleanup is addressed." — \
NEVER write trailer lines such as Reviewed-by:, Acked-by:, Tested-by:, \
Signed-off-by:, Co-developed-by:, Reported-by:, Suggested-by:, or Fixes:
- Do NOT say "you need to", "you must", "you have to"
- Do NOT say "this is wrong" — say "I would expect" or "should use"
- STRICT PROHIBITION: never generate any upstream trailer tag in any form — \
not real, not placeholder, not template. No "Reviewed-by: [name]", \
no "Acked-by: [Reviewer]", no "Reviewed-by: <name>". Prose only.
"""

# Category-matched few-shot examples derived from the kernel audio lore corpus study.
# Injected into upstream_comment instructions to anchor the LLM to real community style.
# One example per category — kept short to avoid prompt bloat.
_FEW_SHOT: dict[str, str] = {
    "api_misuse": (
        "Example from the kernel audio community:\n"
        "> +static SOC_ENUM_SINGLE_DECL(rt1320_brown_out_enum, 0, 0, rt1320_brown_out_mode);\n"
        "On/off switches should be a Switch control, not an enum."
    ),
    "design": (
        "Example from the kernel audio community:\n"
        "> +static const char *const tdm_data_length[] = { \"16\", \"32\" };\n"
        "I would expect TDM to be configured by set_tdm_slot() from the machine driver, "
        "not from userspace. I see the driver does actually have a set_tdm_slot() operation..."
    ),
    "bug": (
        "Example from the kernel audio community:\n"
        "> +       ret = request_firmware_nowait(THIS_MODULE, true, fw_name, dev, GFP_KERNEL, ctx, cb);\n"
        "Both suspend and remove should clean up anything that's pending — if the firmware "
        "load doesn't complete before either path runs, the callbacks may fire on freed memory."
    ),
    "error_handling": (
        "Example from the kernel audio community:\n"
        "> +       ret = read_device_properties(priv);\n"
        "> +       if (ret)\n"
        "> +               return ret;\n"
        "This will fail probe if the property is absent from DT, making it a de-facto required "
        "property even though it is not marked as such in the bindings. "
        "Either the driver should tolerate the property being absent, or the binding should "
        "mark it mandatory."
    ),
    "convention": (
        "Example from the kernel audio community:\n"
        "> +'status' property in the middle of other properties\n"
        "Nit: 'status' should be the last property — file-wide. "
        "Not a strong reason for a respin on its own."
    ),
    "dt_binding": (
        "Example from the kernel audio community:\n"
        "> +compatible = \"vendor,chip-lpass-lpi-pinctrl\";\n"
        "This introduces a new compatible string but I don't see a DT binding document in "
        "this series. New compatible strings need a binding document — either a new schema "
        "or an update to an existing one covering the LPASS LPI family. "
        "Could you clarify whether this is coming in a follow-up, or was it accidentally omitted?"
    ),
    "commit_msg": (
        "Example from the kernel audio community:\n"
        "Nit: pm_ptr() (here and in the Subject), but it's a minor one."
    ),
    "race": (
        "Example from the kernel audio community:\n"
        "> +       ret = pm_runtime_resume(component->dev);\n"
        "Other controls in this driver ignore writes before hw_init is set — should this one?"
    ),
}


def _upstream_comment_instruction(category: str | None = None) -> str:
    """Build the upstream_comment field instruction with a category-matched example."""
    base = (
        "REQUIRED for every finding. Write a maintainer-style review comment that could be "
        "posted verbatim to lore.kernel.org after human review.\n"
        "STYLE RULES:\n"
        "- Ask before asserting: prefer 'I would expect...' over 'This is wrong.'\n"
        "- For API misuse: 'Should use X here rather than Y — X handles Z correctly.'\n"
        "- For design tensions: 'Either [option A] or [option B].'\n"
        "- For DT/binding issues: reference 'make dtbs_check W=1' and cite the missing element.\n"
        "- Scope nitpicks: 'Nit: [issue] — not a strong reason for a respin.'\n"
        "- For info/convention/style severity: end the comment with a prose-only conditional "
        "signal such as 'With this tidied up, I don't have further comments on this part.' "
        "or 'This looks fine to me once that minor cleanup is addressed.' "
        "or 'This is not something I would treat as blocking by itself.'\n"
        "- NEVER write upstream trailer lines in any form — not real, not placeholder, "
        "not template. Forbidden: Reviewed-by:, Acked-by:, Tested-by:, Signed-off-by:, "
        "Co-developed-by:, Reported-by:, Suggested-by:, Fixes:. No 'Reviewed-by: [name]', "
        "no 'Acked-by: <Reviewer>', no placeholder tags of any kind. Prose only.\n"
        "- Keep the comment proportional: API misuse = 1–2 sentences; design = 3–5 sentences.\n"
        "- Do NOT say 'you need to', 'you must', 'you have to'.\n"
        "- Do NOT simulate any named maintainer — use community voice only."
    )
    example = _FEW_SHOT.get(category or "", "")
    if example:
        return f"{base}\n{example}"
    return base


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
{static_findings}
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
  "message": "Terse technical summary of the issue (1–2 sentences). State the concern; do not prescribe the fix.",
  "suggestion": "Corrected code snippet if applicable (optional, null if none)",
  "upstream_comment": "{upstream_comment_instruction}",
  "confidence": <0.0-1.0 how sure you are>,
  "reasoning": "Why this is an issue — evidence from the diff"
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
- Community review patterns for this subsystem

Do NOT report a "convention" or "api_misuse" issue based on a naming/identifier \
mismatch (driver short name vs. compatible string vs. filename vs. chip marketing \
name) unless you can point to an actual rule, doc, or binding it violates. If you \
cannot cite a concrete convention it breaks, it is not a finding — leave it out.

{domain_context}
{static_findings}
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
  "message": "Terse technical summary of the issue (1–2 sentences). State the concern; do not prescribe the fix.",
  "suggestion": "Corrected code or approach (optional)",
  "upstream_comment": "{upstream_comment_instruction}",
  "confidence": <0.0-1.0>,
  "reasoning": "Why this violates conventions — evidence from the diff"
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


def format_static_findings(findings: list[dict]) -> str:
    """Format checkpatch findings as a concise prompt context block.

    Returns an empty string when there are no real findings (degraded-only
    or empty list) so prompts stay clean when checkpatch is unavailable.
    """
    real = [f for f in findings if not f.get("degraded")]
    if not real:
        return ""
    lines = ["## Checkpatch Findings (from scripts/checkpatch.pl --no-tree)"]
    for f in real[:20]:  # cap at 20 to avoid prompt bloat
        loc = f"{f.get('file') or '?'}:{f.get('line') or '?'}"
        sev = f.get("severity", "info").upper()
        cat = f.get("category", "")
        msg = f.get("message", "")
        lines.append(f"- [{sev}] {loc} ({cat}): {msg}")
    lines.append("")
    return "\n".join(lines) + "\n"
