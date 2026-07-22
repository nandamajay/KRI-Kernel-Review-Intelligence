# WP-S1B Shadow-Reducer Architecture Review

**Date:** 2026-07-20  
**Status:** Pre-implementation review of WP-S1B (`SeriesReducer` + R1–R8).  
**Inputs:**
- `docs/WP_S1_SERIES_AWARE_REASONING_SPEC_2026-07-21.md` §2.4, §5, §6, §8, §9, §10
- Landed WP-S1A implementation: `kri/series/{models,extractors,context,prompt}.py`, wiring in `kri/llm/{prompts,agents,reviewer}.py`
- RubikPi3 replay evidence (6 patches, 2 compatibles / 1 DT property / 8 C symbols declared, 1 file touched by >1 patch)

**Scope:** architecture-only. No code. No commits. This document exists to gate WP-S1B on an explicit blast-radius decision before we commit to landing suppressive logic.

---

## 1. Executive Summary

WP-S1A ships a purely additive pass: the reviewer sees more facts about the series, but no finding it emits is removed or edited. WP-S1B is a categorically different intervention — it is the first KRI subsystem that **removes signal** from the review output. Every reducer rule is a *silent* transformation of an existing review: R1/R2/R6/R7 delete findings; R4/R5 merge them (dropping the loser's severity and provenance); R3 rewrites message text in place; only R8 is additive.

Three properties make this class of change qualitatively risky:

1. **Silent failure.** A false-positive suppression produces no error — it produces the *absence* of a finding. Nothing the operator sees on the report page will flag it.
2. **LLM calibration drift.** Rules R1/R2/R7 match on substring patterns in LLM-generated prose; R6 matches on the LLM's self-reported confidence. Both signals drift when the model, temperature, or upstream prompt changes. Unit tests fix the input; production does not.
3. **Blast radius asymmetry with WP-S1A.** WP-S1A is trivially revertible (two prompt slots + one module). WP-S1B mutates PatchReview state, adds metadata contracts consumed by the web UI, and changes the meaning of `inline_comments` shipped downstream. Full revert is a multi-file operation.

**Recommendation (§6):** Introduce shadow mode as a **runtime feature flag** (Option B), not a test-only harness (Option A), and default it to `"shadow"` for the initial WP-S1B landing. Flip to `"on"` in a follow-up commit only after audit-trail evidence over real production traffic shows the reducer is not silently removing genuine findings.

---

## 2. Risk analysis of R1–R8

Each rule is scored on three axes:
- **Trigger robustness** — how likely the trigger fires *only* on the intended pattern.
- **Loss on false positive** — the size of the signal loss if the rule wrongly fires.
- **Detectability** — how easily an operator or test can notice a bad fire.

Scores are ordinal: Low / Medium / High.

### R1 — Declared-symbol suppression

| Axis | Score | Reasoning |
|---|---|---|
| Trigger robustness | **Medium-Low** | Two-stage match: (a) enumerated substring in message/upstream_comment/reasoning, (b) candidate symbol extracted from surrounding text intersects `declared_symbols.compatibles ∪ dt_properties`. Stage (a) is safe; stage (b) is the risk. |
| Loss on FP | **High** | Suppresses a DT-binding / API-misuse finding that would otherwise reach the maintainer. |
| Detectability | **Low** | Silent removal; no user-visible marker without the `series_suppressed` panel (which itself may be collapsed / ignored). |

**Concrete failure scenarios:**

- **Loose symbol regex.** Spec R1 extracts candidates via `[a-z][a-z0-9-]+,[a-z0-9_-]+`. That pattern matches many strings that look like `vendor,part` but are not compatibles the finding is discussing (e.g., a filename fragment, a fixed-clock name, a phandle label mentioned incidentally). If any *one* of these lands in `declared_symbols` while the finding is really about a *different* symbol, R1 fires. RubikPi3 fixture has 2 compatibles and 1 DT property — collision surface is small on that fixture, larger on a series that declares 20+ properties.
- **DT-property namespace overlap.** Property names like `regulator-boot-on`, `clocks`, or `pinctrl-0` are used across dozens of drivers. If a series legitimately adds one such property, R1 becomes able to suppress *any* finding whose prose happens to mention it plus an R1 phrase (`missing binding`, `not documented`). The intent of R1 is to catch "you added this new compatible but forgot the schema" — but the trigger doesn't distinguish "the finding is about *this* symbol" from "the finding *mentions* this symbol".
- **Regex `compatible.*not documented`.** With no `re.DOTALL` guard and a large agent message, this can span logically unrelated sentences (`"...compatible with mainline. The clock rate is not documented in..."`).

**Mitigations already in spec:** word-boundary match on symbols (§10.4). This blocks substring collisions inside one token but does not block cross-sentence conflations.

**Recommended tightening (before or during WP-S1B):**
- Require the extracted candidate to occur in the *same sentence* as the R1 phrase, or within a 200-character window.
- Never fire R1 on `severity == "blocker"`. See §7.

### R2 — Series-present suppression

| Axis | Score | Reasoning |
|---|---|---|
| Trigger robustness | **Low** | Substring-only test on 5 generic phrases; fires when `total_patches > 1`, which is true for every multi-patch series. There is no per-finding cross-check that the "companion" the agent asks for is actually in the series. |
| Loss on FP | **High** | Suppresses a finding whose exact point may be "the specific patch you need is *missing* from this series". |
| Detectability | **Low** | As R1. |

**Concrete failure scenarios:**

- **Legitimate `part of a larger series`.** An agent legitimately writes "This DT change should be part of a larger series that also updates the driver". If the series contains only the DT change and the driver change is genuinely missing, R2 fires *because* the series has ≥2 patches — even though those two patches don't include the requested driver change. The rule confuses "there are ≥2 patches" with "the agent's requested companion is present".
- **Legitimate `not accompanied by`.** "This binding is not accompanied by an example DT node in the same series" — R2 fires because "not accompanied by" is in the enumerated list. But the example DT node really is missing.
- **`is this patch part of a larger series` inversion.** This phrase is the agent *asking* whether a companion exists. R2 treats the question as evidence the companion exists. Backwards.

**R2 has the weakest logical basis of any suppressive rule.** It infers "the reviewer's concern is addressed" from "the series has multiple patches", with no verification that what the reviewer asked for was actually delivered.

### R3 — External-dependency to internal rewrite

| Axis | Score | Reasoning |
|---|---|---|
| Trigger robustness | **Medium** | Two-stage: substring + candidate symbol in `declared_symbols`. Symbol-registry check narrows the trigger. |
| Loss on FP | **Medium** | Not a removal — it's a rewrite. Nuance loss but not signal loss. |
| Detectability | **Medium** | The rewritten message has a fixed prefix (`[Series-internal dependency — patch N/total]`); an operator scanning findings *can* see the mutation, unlike R1/R2/R6/R7. |

**Concrete failure scenarios:**

- **Rewrite destroys nuance.** Agent says "depends on a not-yet-merged patch, and the API is unstable — the consumer should use the callback form instead". R3 replaces the risk-area text with `Depends on patch N/total (subject)`. The "API unstable, use callback form" advice is lost.
- **Symbol collision as in R1.** If a symbol mentioned incidentally is in `declared_symbols`, R3 rewrites a genuinely external dependency as if it were internal.

R3 is safer than R1/R2 because it never removes, but it changes the *meaning* of the finding. Combined with R4/R5 which then compare-and-merge on the rewritten text, R3's semantic drift can propagate.

### R4 — Line-bucket dedup

| Axis | Score | Reasoning |
|---|---|---|
| Trigger robustness | **Medium** | Buckets by `line // 10`; no textual overlap check. |
| Loss on FP | **High** | Drops an entire finding; the kept finding wins on confidence, so the dropped finding's severity is discarded. |
| Detectability | **Medium** | `absorbed_from` provenance and the "Related remark:" prefix in `upstream_comment` create a paper trail — if the UI surfaces them. |

**Concrete failure scenarios:**

- **10-line buckets are coarse.** Lines 220 and 229 are in the same bucket. A NULL-deref bug at 220 and a coding-style nit at 229 merge; the nit's higher confidence wins; the bug's severity is lost.
- **Severity collapse.** Spec §6 R4 says the kept finding wins on confidence. It does not say the merged finding's severity is escalated to the highest of the merged set. This is a specific class of failure: `[BLOCKER, conf=0.55]` + `[INFO, conf=0.85]` → kept = INFO, blocker signal deleted.
- **Category collapse.** Same shape: `[bug]` + `[convention]` at close lines → kept category is `convention`; the bug category is gone from finding-level filters.

**Recommended tightening:**
- Only merge when both findings share `category` (or both are in `{convention, style, nit}`).
- Kept finding's `severity` = `max(severity for f in merged_set)`.

### R5 — Function-scope dedup

| Axis | Score | Reasoning |
|---|---|---|
| Trigger robustness | **Medium** | Jaccard ≥ 0.35 + ≥3 shared tokens ≥ 4 chars. Loose. |
| Loss on FP | **High** | Same as R4. |
| Detectability | **Medium** | Same as R4. |

**Concrete failure scenarios:**

- **Boilerplate token bleed.** Two findings in the same 200-line function share `{return, error, handle, check, kernel}` — 5 tokens ≥ 4 chars, Jaccard easily > 0.35. Merged despite being unrelated.
- **Function scope is coarse in long functions.** A `probe()` function of 200 lines can have unrelated bugs at line 20 and line 180.

**Recommended tightening:**
- Require the shared token set to include a domain-specific token (a filename, symbol, or DT property name, not a control-flow word). Alternative: filter stop-list of kernel-review common words before computing overlap.
- Same-category constraint as R4.

### R6 — Low-signal suppression

| Axis | Score | Reasoning |
|---|---|---|
| Trigger robustness | **Low** | Uses `comment.confidence < 0.55` from the LLM's own self-report. |
| Loss on FP | **Medium-High** | Removes a real subsystem-convention violation reported with low confidence. |
| Detectability | **Low** | Same as R1/R2. |

**Concrete failure scenarios:**

- **Calibration drift.** LLM confidence is not calibrated; different models place the same finding at 0.4 or 0.7 unpredictably. A single threshold fails as models rotate.
- **Convention findings are real.** Genuine convention violations in kernel review (e.g., a DT property in the wrong position, a missing `status = "disabled"`) are legitimately reported as `info` — R6 suppresses them if the LLM hedged confidence.

**Recommended tightening:**
- Per-category thresholds, or gate R6 additionally on `category == "nit"` (not the broader `convention`/`style` set).
- Require an explicit hedge phrase ("possibly", "consider", "minor") in the message, in addition to the confidence threshold.

### R7 — Pre-existing suppression

| Axis | Score | Reasoning |
|---|---|---|
| Trigger robustness | **Medium** | Severity floor at `info` — warnings/blockers are respected. |
| Loss on FP | **Medium** | Info-only floor bounds the loss. |
| Detectability | **Low** | Same as R1/R2. |

**Concrete failure scenarios:**

- **LLM hedge on a real bug.** An agent labels a real bug as `info` because it also says "may pre-date this patch". R7 removes it. The finding was real; the hedge was the model being cautious.
- Not the highest risk — severity floor bounds it. But R7's trust in agent-provided hedges is the same class of dependency as R6's trust in confidence.

### R8 — Coupling annotation

| Axis | Score | Reasoning |
|---|---|---|
| Trigger robustness | **High** | Extract-and-verify: symbol declared in A, referenced in B; only fires when B is silent about that symbol. |
| Loss on FP | **N/A** | R8 is additive. |
| Detectability | **High** | Content is visible in the coupling collapsible. |

R8 is the safest rule. Worst case: it appends a redundant note. It cannot delete or edit an existing finding.

### Summary table

| Rule | Suppressive? | Trigger robustness | Loss on FP | Detectability | Priority for shadow |
|---|---|---|---|---|---|
| R1 | Yes (remove) | Medium-Low | High | Low | **High** |
| R2 | Yes (remove) | **Low** | High | Low | **Highest** |
| R3 | No (rewrite) | Medium | Medium | Medium | Medium |
| R4 | Yes (merge) | Medium | High | Medium | High |
| R5 | Yes (merge) | Medium | High | Medium | High |
| R6 | Yes (remove) | Low | Medium-High | Low | High |
| R7 | Yes (remove) | Medium | Medium | Low | Medium |
| R8 | No (additive) | High | N/A | High | Low |

---

## 3. Rules that could remove genuine maintainer findings

Ordered by expected frequency × severity of the resulting review-signal loss:

1. **R2 (Series-present suppression).** Highest concern. Rule logic conflates "series has ≥2 patches" with "the reviewer's requested companion is present". No verification. Any legitimate `not accompanied by X` observation that mentions a series can be silently removed. **This rule as specified should not land without additional grounding.**

2. **R4 (Line-bucket dedup) — severity collapse.** A high-confidence `info` nit at a nearby line can silently replace a lower-confidence `blocker` bug because the spec merges on confidence and does not escalate severity. This is not a theoretical issue: LLMs assign higher confidence to easy, obvious findings (nits) than to hard, ambiguous findings (real bugs).

3. **R1 (Declared-symbol suppression) — cross-sentence conflation.** The symbol-extraction regex is loose enough that an incidentally-mentioned symbol in a large agent message can trigger the rule for a finding that is truly about a different, undeclared symbol.

4. **R6 (Low-signal suppression) — calibration drift.** Suppression bound to a fixed confidence threshold on an uncalibrated signal will silently remove real convention findings whenever the LLM's confidence distribution shifts.

5. **R5 (Function-scope dedup) — boilerplate token bleed.** Kernel-review vocabulary is highly repetitive; token overlap in a long function reaches Jaccard 0.35 with unrelated content. Same severity-collapse risk as R4.

6. **R7 (Pre-existing suppression) — hedge trust.** Real bugs that the LLM hedges on ("may pre-date this patch") get info severity and R7 removes them.

7. **R3 (External-dep rewrite) — nuance loss.** Rewrites in place; can bury domain-specific detail (unstable API, callback form) behind a generic dependency template.

R8 is not on this list — it adds content only.

---

## 4. Shadow-reducer proposal

### 4.1 Semantics

The shadow reducer applies every rule R1–R8's *trigger evaluation* but **never mutates the PatchReview**. Specifically:

- No finding is removed from `inline_comments`.
- No `message` / `upstream_comment` / `risk_areas` string is rewritten.
- No `severity` / `category` is changed.
- `overall_assessment` receives **no** appended coupling note (R8 is normally additive; in shadow mode it is also audit-only, so the report is comparable across modes).
- The reducer's **audit trail** is fully populated as if the mutations had occurred.

### 4.2 Metadata contract

New `IntelligentReport.metadata` key, additive to the existing WP-S1 contract in §5.1:

```python
{
    # ... existing keys (checkpatch_finding_count, ..., series_context)
    "series_reducer_mode": "shadow",     # "off" | "shadow" | "on"
    "series_shadow_actions": [
        {
            "would_have_been": "suppressed" | "merged" | "rewritten"
                              | "coupling_note_added",
            "kind": "R1_DECLARED_SYMBOL_SUPPRESS" | ... ,   # ReducerActionKind
            "patch_id": str,
            "finding_ref": str,                              # "P{i}.F{j}"
            "file": str,
            "line": int,
            "original_severity": "blocker" | "warning" | "info",
            "original_category": str,
            "original_confidence": float,
            "original_message_head": str,                    # first 240 chars, verbatim
            "matched_pattern": str,                          # the substring/regex that fired
            "matched_symbol": str,                           # for R1/R3/R8; "" otherwise
            "related_patch_id": str,                         # declaring / merge-target
            "related_patch_index": int,                      # convenience
            "absorbed_refs": list[str],                      # R4/R5 only
            "would_be_kept_ref": str,                        # R4/R5 only
            "trigger_window": str                            # for R1: 200-char slice
                                                             # around the match, verbatim
        },
        ...
    ]
}
```

**Key differences from the live `series_reducer_actions` list (§5.1):**
- `would_have_been` is a shadow-only enum that maps to the effective outcome.
- `original_*` snapshots the pre-reducer finding state — essential for post-hoc reasoning about whether the suppression would have been correct.
- `matched_pattern` and `trigger_window` are *provenance* fields: they document *why* the rule fired, in enough detail that an operator can review the decision without replaying the pipeline.

**Deliberately excluded from the metadata contract:**
- The rendered "kept" merge-target `upstream_comment` (spec §6 R4). Shadow mode does not synthesize hypothetical merged text — that would double the reducer's surface area for negligible audit benefit.
- Rewritten R3 message text. Same reason.

### 4.3 Wiring

`SeriesReducer` gains a `mode` constructor argument:

```
mode: Literal["off", "shadow", "on"] = "shadow"
```

- `"off"` — reducer is not constructed. Equivalent to the WP-S1A endpoint. `series_shadow_actions` absent.
- `"shadow"` — reducer constructed; every rule evaluates its trigger; only `series_shadow_actions` is populated; `inline_comments` are byte-identical to the pre-reducer state.
- `"on"` — full behavior per spec §6. `series_reducer_actions` populated; `series_shadow_actions` absent.

`IntelligentReviewEngine.__init__` gains one parameter:

```
series_reducer_mode: Literal["off", "shadow", "on"] = "shadow"
```

The default is intentionally `"shadow"` for the initial WP-S1B landing. See §6.

### 4.4 Determinism (Constitution Sec. 40)

Shadow mode is a strict subset of the live reducer's work: it computes each trigger but skips the mutation. If the live reducer is deterministic (spec §6.1), shadow mode is deterministic too. The Sec. 40 stochastic-confinement AST scan applies to `kri/series/reducer.py` regardless of mode.

**New test to add** (extension of §8.3 S2): `test_shadow_reducer_is_deterministic` — build the shadow output twice on the same input, assert `repr()` equality.

### 4.5 Strategy C reaffirmed

Shadow-action content, like live-reducer content, must never re-enter agent prompts. This is the WP-CP1/WP-T2A "output-safety" boundary. The existing S5 test (§8.3, `test_reducer_output_not_reinjected_into_prompts`) covers this if extended to configure the mode explicitly to `"shadow"` and re-run the check.

### 4.6 Trailer safety

The `matched_pattern`, `trigger_window`, and `original_message_head` fields quote LLM-generated text verbatim. These are the same fields the live reducer would surface via `series_reducer_actions`, so the risk profile is identical. Existing `strip_trailers()` application at the InlineComment model boundary (`kri/llm/sanitize.py`) already scrubs the source strings; a shadow-mode-specific S1 test should assert no trailer tokens appear in any shadow-action field.

### 4.7 UI surfacing

The web UI, when it renders WP-S1B, should treat `series_shadow_actions` symmetrically to `series_reducer_actions`:

- Below the WP-S1 metadata strip, render a collapsible: `▸ Series-aware shadow actions ({count})`.
- Same table format as the reducer-actions table (spec §7.1).
- Adjacent to (not merged with) the live suppressions/actions collapsibles, so an operator running with `mode="shadow"` sees the audit trail; an operator running with `mode="on"` sees the live actions; if both were ever emitted (a future consistency mode), both are visible side by side.

Rendering visibility rule: shadow collapsible visible when `report.metadata.series_reducer_mode == "shadow"` AND `len(series_shadow_actions) > 0`.

### 4.8 What shadow mode does NOT do

- It does not learn a policy. It is a passive audit surface.
- It does not compare shadow output against a prior baseline. That is a downstream tool's job (a diff over consecutive report JSONs).
- It does not veto a live-reducer action. The live reducer, when enabled, ignores shadow output; they are two mutually exclusive modes for one run.

---

## 5. Test additions specific to shadow mode

To be added to the WP-S1B test plan (§8):

| # | Test | Assertion |
|---|---|---|
| SM1 | `test_shadow_mode_preserves_all_findings` | For a fixture with known R1/R2/R4/R6/R7 triggers, `sum(len(pr.inline_comments) for pr in report.patches)` in `mode="shadow"` equals the pre-reducer count. |
| SM2 | `test_shadow_mode_records_would_have_been_suppressed` | Same fixture: `report.metadata["series_shadow_actions"]` contains one entry per suppression the live reducer would have made, with the correct `would_have_been` and `kind` values. |
| SM3 | `test_shadow_mode_records_would_have_been_merged` | Same fixture with R4/R5 triggers: each merge pair produces an entry with `would_have_been == "merged"`, `absorbed_refs` and `would_be_kept_ref` populated. |
| SM4 | `test_shadow_mode_records_would_have_been_rewritten` | R3 trigger: entry with `would_have_been == "rewritten"`, `matched_symbol` and `related_patch_id` populated. |
| SM5 | `test_shadow_mode_is_deterministic` | Two shadow runs produce byte-equal `series_shadow_actions`. |
| SM6 | `test_shadow_mode_output_not_reinjected` | Extend existing S5: mode="shadow" + distinctive marker in a shadow-action field must not appear in any subsequent agent prompt. |
| SM7 | `test_shadow_mode_never_emits_trailer_tokens` | `_TRAILER_RE` scan across all string fields in every shadow-action entry. |
| SM8 | `test_shadow_mode_off_switch_equals_wp_s1a` | `mode="off"` produces a report byte-equal to a WP-S1A-only run on the same input. |
| SM9 | `test_shadow_mode_on_disables_shadow_actions` | `mode="on"` produces `"series_shadow_actions"` absent from `report.metadata`. |
| SM10 | `test_rubikpi3_shadow_mode_captures_expected_suppressions` | Regression: on the RubikPi3 fixture, `series_shadow_actions` contains entries whose `matched_pattern` covers the R1 missing-binding false positives WP-S1B is designed to eliminate. |

Test total delta: +10 shadow-specific tests. Existing R1..R4 regression tests (§8.4) run only in `mode="on"`; they gate the final flip.

---

## 6. Evaluation: does shadow mode need to exist?

**Options as posed:**
- **A.** Only for testing (dev/pytest harness).
- **B.** Runtime feature flag.
- **C.** Not needed.

**Recommendation: B (runtime feature flag).**

### Why not C

Option C's argument is that the WP-S1B spec ships 15 reducer unit tests + 4 regression tests + 5 Constitution tests, and that this is sufficient coverage. The problem: **every test in the WP-S1B plan uses either a synthetic fixture or the single RubikPi3 mbox.** The reducer's actual failure modes — LLM confidence drift (R6), cross-sentence conflation in long agent messages (R1), boilerplate token bleed (R5) — are only observable on real, novel LLM output. A test suite that fixes the input cannot detect drift in the input.

The alternative to shadow mode is one of:
- Ship WP-S1B, wait for a maintainer to report a missing finding, revert. High operator cost, unknown detection latency, and — because reducer suppressions are silent — the reporter has to already know the finding *should* exist.
- Ship WP-S1B behind `series_reducer_enabled=False` (already specified in §3.2). This is *worse* than shadow mode: it produces no evidence about how the reducer *would* behave, so there's no basis on which to later enable it.

C is not defensible for a first-of-its-kind silent-suppression system.

### Why not A

Option A gives us regression testing on the fixtures already covered by the WP-S1B unit suite. It adds no signal beyond what those tests provide: a shadow-mode harness that only ever runs on `tests/fixtures/rubikpi.mbox` is a re-render of the reducer's decisions on data the reducer was tuned against. The interesting decisions — over novel production series — are where shadow mode earns its keep, and Option A cannot see them.

A also creates a dev/prod divergence: the production code path never exercises the shadow branch, so any bug in it (e.g., a shadow-only null dereference, a metadata-shape drift) is silent until someone toggles a test flag. Runtime feature flags force the shadow path to be exercised on every production request during the shadow period, exposing bugs early.

### Why B

1. **Silent-suppression is uniquely dangerous.** WP-S1B is the first KRI subsystem where a bug produces the *absence* of output, not an error. That failure mode demands observability *on real traffic*, not just on curated fixtures.
2. **Cheap to build.** Shadow mode reuses the same rule-trigger evaluation as the live reducer. The only code delta is: skip the mutation, record the shadow action instead of the live action, expose a mode enum.
3. **Cheap to run.** The reducer is a pure Python pass, no LLM call, no I/O. Its extra cost is a fraction of a percent of a review's wall time. Shadow mode adds no runtime beyond that.
4. **Bounded metadata cost.** A typical WP-S1B run emits O(findings) reducer actions. Shadow mode replaces `series_reducer_actions` with `series_shadow_actions` of the same size. No blowup.
5. **Precedent already exists.** `series_awareness: bool` already parameterizes the engine (`kri/llm/reviewer.py:41-50`, `test_W7_series_awareness_off_omits_series_context_and_metadata`). Adding `series_reducer_mode: Literal["off", "shadow", "on"]` is a natural extension, not a new pattern.
6. **Enables a safe rollout narrative.** Land WP-S1B with the default at `"shadow"`. Operate for a defined observation period. Review the audit trail. When the audit is convincing, flip the default to `"on"` in a follow-up PR. If reducer regression later surfaces, flip back to `"shadow"` — no code revert required.
7. **Ties the failure mode into the existing UI surface.** The shadow collapsible sits next to the live reducer collapsible; operators develop familiarity with the audit format before the reducer starts silently acting.

### Rollout plan under Option B

1. **WP-S1B lands with default `series_reducer_mode="shadow"`.** All findings preserved; `series_shadow_actions` populated on every multi-patch review. UI renders the shadow collapsible.
2. **Observation window: N series-reviews or T weeks, whichever is longer.** Operator reviews the `would_have_been_suppressed` list on real traffic. Concretely, they look for entries whose `original_severity=warning|blocker`, or whose `matched_pattern` occurs in a `trigger_window` that reads as a legitimate maintainer concern.
3. **Two possible outcomes.**
   - **Green.** Audit trail shows no false-positive suppressions. Follow-up PR flips the default to `"on"`; shadow mode remains available as an operator escape hatch.
   - **Red.** Audit trail shows a category of finding the reducer wrongly targets. Fix the rule (tighten the trigger, add category/severity guards from §7). Restart the observation window.
4. **Shadow mode is never removed.** It stays as a permanent operator-flippable diagnostic. Zero cost to leave in place; high value the next time a reducer rule changes.

### One caveat

Option B commits us to shipping two code paths (shadow + live) that must stay in sync. Each new rule R_k has to implement both a "would-fire" evaluator and a "fire" mutator. The spec's rule structure (`trigger → suppression/rewrite → metadata → audit-trail`) already isolates the "trigger" phase, so the split maps onto existing structure. This is a low but nonzero maintenance tax; the payoff is decisive during rollout and any future rule change.

---

## 7. Additional recommendations before WP-S1B

Independent of shadow mode, three tightenings should land as part of the WP-S1B commit series. Each corresponds directly to a rule ranked "high risk" in §3.

### 7.1 Severity floor for R1/R2/R6/R7

Any finding with `severity == "blocker"` is never suppressed by R1, R2, R6, or R7. Any finding with `severity == "warning"` and `confidence >= 0.7` is never suppressed by R1, R2, or R6.

Rationale: the reducer's role is noise reduction; it should never delete a high-confidence high-severity finding based on prose pattern matching, no matter how confident the trigger looks. This floor is a categorical safety valve. Blockers are rare; the false-positive cost of leaving one in place is small (an operator sees it, reads it, decides it's spurious). The cost of silently dropping a real blocker is large.

### 7.2 Same-category constraint on R4/R5

`R4_line_bucket_merge` and `R5_function_scope_merge` only fire when both findings share `category`, or when both are in `{convention, style, nit}`. A `bug` and a `convention` finding never merge, regardless of proximity or token overlap.

Rationale: severity collapse (§3, R4) is a specific class of failure produced by cross-category merging. Category equality is a cheap, robust guard.

### 7.3 Reconsider R2 before landing

R2 as specified matches on 5 generic phrases with no verification that the reviewer's requested companion is present. **The rule should either be reformulated with a companion-presence check** (e.g., "R2 fires only when the requested symbol is in `declared_symbols`") **or dropped entirely for the initial WP-S1B landing**, and re-evaluated once shadow-mode audit data is available.

The rule addresses one specific RubikPi3 false positive; if the actual class of false positive is narrow, it is safer to leave the finding in place and let the maintainer dismiss it than to build a rule that risks removing legitimate "the companion is missing" reports.

### 7.4 R1 symbol-extraction window

Tighten R1's candidate-symbol extraction to the same sentence as the R1 phrase, or a fixed-character window around it. As spec'd, the candidate regex scans the entire `message + upstream_comment + reasoning` concatenation, which invites cross-sentence conflation.

---

## 8. Success criteria for WP-S1B under Option B

The following criteria supplement spec §9. WP-S1B is not considered ready to flip its default from `"shadow"` to `"on"` until all pass:

| # | Criterion | Evidence |
|---|---|---|
| SM-C1 | Shadow mode ships as the default for `series_reducer_mode` in the WP-S1B landing commit. | Diff review. |
| SM-C2 | All 10 shadow-mode tests (SM1..SM10) pass. | pytest. |
| SM-C3 | The severity floor (§7.1) and same-category constraint (§7.2) are implemented. | Unit tests added alongside R4/R5/R1/R2/R6/R7 tests. |
| SM-C4 | R2 is either reformulated with a companion-presence check or excluded from the initial landing. | Spec review + code review. |
| SM-C5 | Observation window audit on real production traffic (≥N=5 distinct multi-patch series) shows zero `would_have_been_suppressed` entries whose `original_severity in {"blocker", "warning"}` and `original_confidence >= 0.7`. | Manual audit of `series_shadow_actions` payloads. |
| SM-C6 | Every rule's `would_have_been_*` category correlates 1:1 with its `ReducerActionKind` in shadow-mode output. | SM2/SM3/SM4 assertions. |
| SM-C7 | Sec. 40 stochastic-confinement suite green with `series_reducer_mode="shadow"` as the default. | Existing suite. |
| SM-C8 | Flipping default to `"on"` is a single-line PR that requires no test changes. | Diff review before the flip. |

---

## 9. Non-recommendations (explicit)

To close off avenues that might seem attractive but should not be pursued:

- **Do not use shadow mode to auto-tune the reducer.** Building a learning loop that fits reducer thresholds to shadow-mode observations mixes agent output into a decision surface — a Sec. 40 hazard and a Strategy C hazard. Shadow mode is passive audit; parameter tuning is a human decision informed by the audit.
- **Do not merge `series_shadow_actions` and `series_reducer_actions` into one list with a `mode` field per entry.** Keeping them separate top-level keys prevents downstream consumers from silently ingesting shadow entries as if they were live.
- **Do not skip R8 in shadow mode.** R8 in shadow mode records the coupling notes it would have added, without appending them to `overall_assessment`. Report `overall_assessment` remains byte-comparable across modes.
- **Do not enable shadow mode on single-patch series.** WP-S1A already short-circuits multi-patch behavior when `total_patches <= 1`; the reducer (shadow or live) should stay behind the same guard. Single-patch reports must keep their G2 byte-identity property (§4.4 in the WP-S1 spec).

---

## 10. Summary of asks

1. **Adopt shadow mode as a runtime feature flag** (Option B). Default to `"shadow"` for the initial WP-S1B landing. Flip to `"on"` in a follow-up commit after the audit window.
2. **Land the three tightenings in §7** (severity floor, same-category merge, R2 reformulation-or-defer, R1 window tightening) as part of the WP-S1B commit series.
3. **Add the 10 shadow-mode tests (SM1..SM10)** to the WP-S1B test plan; keep them at the same enforcement level as the existing R1..R4 regression tests.
4. **Retain shadow mode permanently** as an operator escape hatch, not as a rollout-only scaffold.

No code changes are proposed by this document. WP-S1B implementation begins after these architecture decisions are accepted.
