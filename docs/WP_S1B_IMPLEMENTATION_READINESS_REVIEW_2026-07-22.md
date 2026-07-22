# WP-S1B Implementation Readiness Review

**Date:** 2026-07-22
**Status:** Design reconciliation between the WP-S1 spec, the WP-S1B shadow-mode review, and the landed WP-S1A implementation.
**Scope:** architecture only. No code. No commits.
**Inputs:**
- `docs/WP_S1_SERIES_AWARE_REASONING_SPEC_2026-07-21.md` (§2.4, §3.2, §5, §6, §8, §9, §10)
- `docs/WP_S1B_SHADOW_MODE_REVIEW_2026-07-21.md` (all sections)
- Current WP-S1A implementation: `kri/series/{models,extractors,context,prompt}.py`, `kri/llm/{reviewer,agents,prompts,models}.py`, `tests/test_series_review_context.py`, `tests/test_stochastic_confinement.py`

---

## 1. Executive Assessment

WP-S1A shipped cleanly: a pure pre-pass builder, deterministic prompt injection, byte-identity preserved for single-patch, empty-string discipline honoured, all 236 tests green. It touched *no* semantic state — a finding the agents emit is a finding the operator sees.

WP-S1B is not that. Every reducer rule except R8 is a **silent transformation** of an existing review: R1/R2/R6/R7 delete findings, R4/R5 merge them (dropping the loser's severity and provenance), R3 rewrites in place. A false-positive suppression produces the *absence* of output, not an error. Nothing on the UI signals that a finding was silently removed unless the operator opens a collapsible they may not know to look at. This changes the risk category from "additive prompt engineering" to "silent semantic mutation of the review shipped downstream."

The WP-S1B shadow-mode review correctly identifies this asymmetry and proposes a shadow → live rollout. The proposal is directionally correct but *understates* two issues:

1. **The reducer, as spec'd, has at least two rules (R2 and R6) whose failure modes cannot be measured by adding shadow observation.** They fail on drift or on logical unsoundness; observing them run harder does not tell you they're right. R2 in particular is not a calibration problem — it is a rule whose premise is wrong.
2. **The shadow proposal ships all eight rules simultaneously.** The whole point of splitting WP-S1A from WP-S1B was blast-radius isolation. Bundling R1–R8 into a single WP-S1B commit reintroduces the bundling risk we already paid to avoid.

Recommendation: **narrow the initial WP-S1B scope to R1 + R3 + R8 + R4 (with severity floor + same-category guard) — the four rules for which the trigger has grounded evidence (declared-symbol registry lookups, addition-line diff facts) — behind a shadow-mode runtime feature flag.** Defer R2 for reformulation. Defer R6 until a calibration study exists. Defer R5 until it has same-category guards. Defer R7 until R1's data confirms that the "hedged pre-existing" pattern is real in production, not just fixture-derived.

This is stricter than the shadow-mode review's recommendation and stricter than the spec. It is justified by the specific mismatches enumerated in §4 below.

---

## 2. Agreement with Review

I agree with the following load-bearing claims in `WP_S1B_SHADOW_MODE_REVIEW_2026-07-21.md`:

1. **Silent-suppression is a categorically different risk class than WP-S1A.** The review's §1 correctly identifies detectability as the missing property. This is the key architectural framing and it should drive the rollout decision.

2. **Shadow mode should be a runtime feature flag, not test-only (Option B over Option A).** Test-only shadow observes only the fixtures the reducer was tuned against; runtime shadow observes drift. The review's argument against Option A (dev/prod divergence) is correct.

3. **R2 as spec'd is logically unsound, not just poorly tuned.** The review's diagnosis — "R2 confuses 'series has ≥2 patches' with 'the reviewer's requested companion is present'" — is exactly right. `total_patches > 1` is true for every multi-patch series; the phrase-match set is generic. There is no verification that what the reviewer asked for was actually delivered by any patch in the series.

4. **R4 severity collapse is a real correctness issue.** The spec §6 R4 keeps the finding with the highest `confidence`, does not escalate severity, and does not constrain by category. A high-confidence `info` nit at line 220 will silently replace a lower-confidence `blocker` at line 229. The review's proposed severity floor (max-severity) and same-category constraint are the correct fixes.

5. **R6 depends on an uncalibrated signal.** Reviewer confidence is model-specific and drifts across prompt/temperature changes. A single fixed threshold is fragile. The review's diagnosis is correct.

6. **R1 candidate-symbol regex is too permissive.** `[a-z][a-z0-9-]+,[a-z0-9_-]+` will match many strings that are not compatibles under discussion in the finding. Cross-sentence conflation is a genuine risk in long agent messages.

7. **R8 is the safest rule; it is additive only.**

8. **The rule-application ordering already spec'd (R1 → R2 → R3 → R7 → R4 → R5 → R6 → R8) is correct.** Suppression before merge; merge before final low-signal pass; coupling annotation last. The review does not challenge this and I concur.

9. **Rendering behavior:** shadow entries in a separate collapsible from live entries, so downstream consumers cannot conflate them. Also correct.

---

## 3. Disagreements with Review

I disagree with, or want to sharpen, the following:

### 3.1 The review assumes shadow mode alone is sufficient risk mitigation

**Claim (review §6):** "Land WP-S1B with the default at `mode='shadow'` … after N series-reviews the audit trail informs the flip." This is correct as a *general* rollout pattern but insufficient as a *sufficiency* argument for R2 and R6.

**Why:** Shadow mode surfaces what a rule *would have done*. For R1/R4/R5/R7/R8 that is directly useful: an operator can look at each `would_have_been_suppressed` entry and decide "yes, this was noise" or "no, this was signal." For R2 and R6, the audit trail is much less informative:

- **R2:** every multi-patch series will produce a `would_have_been_suppressed` entry the moment an agent utters any of the enumerated phrases. The operator has to *reverse-engineer* whether the companion the agent requested was actually in the series — which is precisely the check the rule failed to do. This turns shadow audit into an ongoing manual verification loop, not a one-shot sign-off.
- **R6:** the audit shows confidence values below 0.55 that got suppressed. Whether those were genuine convention violations or spurious low-confidence noise requires reading the diff and judging in-context. Again, this is per-entry human judgment, not batch statistics.

**Consequence:** shadow-mode observation is necessary but not sufficient. For R2 the fix is a rule reformulation (or removal). For R6 the fix is either a calibration study or per-category thresholds derived from data. Neither is unlocked by "just observe longer."

### 3.2 The review treats "same-category constraint on R4/R5" as an optional tightening

The review §7.2 lists this as a recommended tightening; I claim it must be **mandatory**, not optional. Without it, R4/R5 can merge a `bug` and a `convention` in the same 10-line bucket. The severity of the merged output collapses to whichever finding had higher confidence — which for LLM output is systematically the *simpler* finding (the nit). This is not a rare pathological case; it is the normal behaviour of the merge rule on real reviews.

### 3.3 The review keeps R2 as a candidate for landing "with tightening"

The review §7.3 offers "reformulate with a companion-presence check *or* drop entirely for the initial WP-S1B landing." The "or" understates the situation. R2 as spec'd cannot be tightened by prose adjustment — the input signal (phrase-match on generic text) does not carry the information the rule needs. A companion-presence check is not a tightening of R2; it is a different rule with a different implementation. I recommend explicitly **deferring R2** and re-scoping it as a separate future rule ("Rn-companion-present-suppress") with a different trigger.

### 3.4 The review's assumption that "the reducer is a pure Python pass, no LLM call, no I/O" understates the composition cost

The reducer's *inputs* include `comment.message`, `comment.upstream_comment`, and `comment.reasoning` — all of which are LLM-generated strings. Every substring-match rule (R1, R2, R3, R6, R7) is therefore a *string-based classifier trained implicitly on the LLM's current output distribution*. Model rotation (e.g., a future Sonnet 5 upgrade), prompt changes, or temperature drift can shift the distribution and change the fire rate without any code change. The review acknowledges this for R6 but not systemically for R1/R2/R3/R7.

**Consequence:** the reducer needs a regression-guarding contract that is **not** met by "the LLM output matches this fixture" tests. It needs either (a) a periodic re-audit tied to model rotation, or (b) an invariant that "the reducer's decisions on model M are a superset / subset of its decisions on model M-1 within tolerance." The spec has neither and the review does not add either.

### 3.5 The review recommends default `mode="shadow"` — I recommend default `mode="off"` for the first landing

**Difference:** the review lands WP-S1B with the reducer running (in shadow) on every review. I recommend landing it *disabled by default* (`mode="off"`), with `mode="shadow"` requiring an explicit opt-in in the reviewer config for the first N reviews.

**Why:** an "off by default" landing preserves the WP-S1A behavior as the reference for anyone bisecting a report-content regression. It also allows the reducer code to sit in the tree, be exercised in tests, and then be enabled per-operator by config change without a code deployment. This matches how `series_awareness=True` was landed — the code path exists but the safety was in defaulting the *feature-flag geometry* to preserve prior behavior. Landing shadow-on-by-default forces every operator into a mode they didn't ask for; landing off-by-default with a documented shadow flag is safer and easier to revert.

---

## 4. Additional Risks Not Mentioned

The following risks are absent from both the spec §10 and the shadow-mode review:

### 4.1 InlineComment schema change without migration

The spec §5.3 adds `series_provenance: SeriesProvenance | None` to `InlineComment`. `InlineComment` is a Pydantic model; adding a field changes its JSON serialization. Every downstream consumer (report loader, benchmark harness, cached corpora, replay driver) needs to handle the new field. The current landed `InlineComment` (`kri/llm/models.py:13`) has no such field. Backward compatibility depends on the field being `Optional` and defaulting to `None`; the spec §5.4 asserts this. But there is no test that a pre-WP-S1B serialized report can be loaded under WP-S1B, nor a test that a WP-S1B-serialized report loads correctly under a hypothetical older consumer. Both should exist.

### 4.2 The reducer runs on `PatchReview` objects that already went through `_merge_comments` deduplication

`IntelligentReviewEngine._merge_comments` (`kri/llm/reviewer.py:187`) already dedups by `(file_path, line_number, category)` and prefers higher-confidence findings. R4/R5 will therefore run on already-partly-deduped input. The interaction is not analysed in the spec: two agents that reported nearby-but-not-same findings get to R4, while two agents that reported *exactly* the same location have already lost the loser at `_merge_comments`. This means the reducer's merge behavior is asymmetric across agents and R4/R5 test coverage should include the composed pipeline, not just the isolated reducer.

### 4.3 The reducer's rewrite of `comment.message` (R3) mutates the same string later matched by R4/R5

R3 prepends `[Series-internal dependency — patch N/total]` to `comment.message`. R4/R5 then compute merges partially on that same message text. Two findings that were disjoint in the *original* text can become mergeable after both are prefixed by the same R3 tag; the Jaccard denominator shifts and the shared-token count crosses the R5 threshold. This is a specific case of the "R3 semantic drift propagates to R4/R5" risk I mentioned inline; the shadow-mode review notes the drift risk but does not connect it to the merge Jaccard.

**Mitigation:** R5's token overlap should be computed *before* R3's rewrite is applied — or the R3 prefix should be added to a separate field, not to `comment.message` itself.

### 4.4 R5's tokenization loses information about domain vocabulary

The spec §6 R5 tokenizes on whitespace, keeps tokens ≥ 4 chars, computes Jaccard. Kernel review vocabulary — `return`, `error`, `handle`, `check`, `kernel`, `driver`, `probe`, `remove`, `should`, `might` — occurs in almost every finding. Two unrelated findings in the same `probe()` function will share 5+ tokens purely from boilerplate. Recommended tightening (from the review) is a stop-list; I would sharpen this to *requiring* at least one **specific** shared token (a filename, symbol name, DT property, error code identifier — something extracted by `extract_c_symbols` / `extract_compatibles` / `extract_dt_properties`), not just any word ≥ 4 chars.

### 4.5 R8's "already discusses it" check is under-specified

Spec §6 R8: "patch_b's inline_comments do NOT already reference any symbol in `consumed`." The check is not defined at token-level, substring-level, or regex-level. If `sym = "qcs6490_probe"` and the comment says "the qcs6490 probe path is missing", is that a reference? The spec does not answer. Under-specification here is safe (worst case: a redundant coupling note), but it should be resolved before landing so the test coverage means something.

### 4.6 The reducer's `sorted()` iteration order requirement conflicts with `_merge_comments`'s already-sorted output

`_merge_comments` returns comments sorted by `(severity_rank, file_path, line_number)`. The reducer's spec §6.1 says all rules iterate in `sorted()` order (patches by sequence then patch_id; findings by original index). "Original index" is ambiguous after `_merge_comments` has already re-sorted: does it mean the pre-merge index, the post-merge index, or the enumerated position at reducer input? This matters for R4/R5 tie-breaking. Should be explicitly defined.

### 4.7 Cross-agent audit-trail loss

When R4/R5 merge two findings, they record `absorbed_refs`. But the two findings may have come from *different agents* (code-quality vs subsystem-expert). Which agent's opinion "wins" is currently determined by confidence; whether that agent was the right authority for the finding is not evaluated. Losing the subsystem-expert's finding in favour of the code-quality agent's near-duplicate nit is a specific regression risk not covered by any spec test.

**Mitigation:** the audit-trail entry should record `absorbed_agent_source` so the loss is at least visible.

### 4.8 Reducer performance on large series

R5 is O(N²) in comments-per-patch (pairwise Jaccard). For a series with 6 patches × 30 comments each, that's ~2700 pairs to score. Per-review it's cheap; in a batch replay of hundreds of series it accumulates. No performance test exists. The spec's §9.5 "no >10% wall-clock regression" is for WP-S1A only.

### 4.9 Shadow mode reveals the reducer's decisions but the operator has no diff view

Shadow mode records `original_severity`, `original_category`, `original_message_head`. It does *not* record the counterfactual "what the report would look like with the rule applied." For R4/R5 merges especially, the operator wants to see "the kept finding after merge" vs "both original findings" side by side. Without that, the audit is legible but not comparable. This is a UI/audit tool omission, not a code omission — I flag it here as it will surface as an operational complaint.

### 4.10 Constitution Sec. 40 exposure via `_merge_comments` interacting with reducer under `set` iteration

The reducer's spec forbids `set` iteration order. But `_merge_comments` builds a `dict` keyed by `f"{file_path}:{line_number}:{category}"` and returns `sorted(seen.values(), ...)`. That is deterministic. If any future refactor of the merge step changes this to `set`-based, the reducer's determinism guarantee breaks silently. The stochastic-confinement test does not catch this because `set()` construction is not on the denylist. A separate test ("reducer output is stable across 3 build+reduce runs") is needed.

---

## 5. Recommended WP-S1B Scope

I recommend the following scope split for the initial WP-S1B landing:

### 5.1 Land now (initial WP-S1B commit series)

| Rule | Status | Guards required |
|---|---|---|
| **R1** — declared-symbol suppression | Land with guards | (a) same-sentence or 200-char window between R1 phrase and candidate-symbol match; (b) never suppress `severity == "blocker"`; (c) never suppress `severity == "warning" AND confidence ≥ 0.7`; (d) word-boundary match on symbol (spec §10.4). |
| **R3** — external-to-internal rewrite | Land with guards | Same symbol-window guard as R1; store the R3 prefix in a new `series_prefix` field on `InlineComment`, **not** by prepending to `comment.message`, to prevent Jaccard drift into R4/R5. |
| **R4** — line-bucket dedup | Land with guards | (a) same-category constraint (both findings share `category`, or both in `{convention, style, nit}`); (b) kept finding's `severity = max(severities)`; (c) audit-trail includes `absorbed_agent_source`. |
| **R8** — coupling annotation | Land unchanged | Additive-only; lowest risk. Resolve the "already discusses it" ambiguity (§4.5) before landing. |

Rationale: these four rules have triggers grounded in **structural evidence** — the declared-symbol registry (R1, R3, R8) or diff geometry (R4). None depends on interpreting agent prose or on uncalibrated confidence.

### 5.2 Land disabled

| Rule | Status | Reason |
|---|---|---|
| **R5** — function-scope dedup | Land disabled by default | Correct in principle but needs the domain-token constraint from §4.4 before enabling. Land the code + tests; feature-flag it separately (`series_r5_enabled: bool = False`) so shadow-mode audit can gather Jaccard-fire-rate data over real traffic. |
| **R6** — low-signal suppression | Land disabled by default | Fixes the calibration issue by not depending on calibration until we have data. Ship the code so shadow-mode audit records what R6 *would* have suppressed; only enable once a per-category calibration study exists. |
| **R7** — pre-existing suppression | Land disabled by default | Info-severity floor bounds the loss, but R7 depends on the same LLM-hedge signal as R6. Same rationale: ship shadow, defer live. |

### 5.3 Defer entirely

| Rule | Status | Reason |
|---|---|---|
| **R2** — series-present suppression | Defer | Logically unsound as spec'd. Do not ship, even in shadow. Reformulate as "Rn-companion-present-suppress" with a companion-presence check against `declared_symbols`, and re-review as a new rule proposal. |

### 5.4 Summary table

| Rule | Live in initial WP-S1B | In shadow only | Deferred |
|---|---|---|---|
| R1 | Yes (with guards) | | |
| R2 | | | **Yes** |
| R3 | Yes (with guards) | | |
| R4 | Yes (with guards) | | |
| R5 | | Yes | |
| R6 | | Yes | |
| R7 | | Yes | |
| R8 | Yes | | |

Initial WP-S1B ships **4 live rules (R1, R3, R4, R8) + 3 shadow-only rules (R5, R6, R7) + 1 deferred rule (R2)**.

This is narrower than either the spec or the shadow-mode review, and it is *not* Option C from the review's list (Option C was R1+R3+R8 with all suppressive rules deferred; I keep R4 live because its risk is fully addressable by two small guards).

---

## 6. Recommended Safeguards

Mandatory safeguards for the initial WP-S1B landing:

### 6.1 Feature-flag geometry

```
IntelligentReviewEngine(
    ...,
    series_awareness: bool = True,          # existing (WP-S1A)
    series_reducer_mode: Literal["off","shadow","on"] = "off",   # new
    series_r5_enabled: bool = False,        # new — R5 gate
    series_r6_enabled: bool = False,        # new — R6 gate
    series_r7_enabled: bool = False,        # new — R7 gate
)
```

- `series_reducer_mode="off"` is the default. No reducer runs.
- `series_reducer_mode="shadow"` is opt-in. Runs all enabled rules; emits `series_shadow_actions`; mutates nothing.
- `series_reducer_mode="on"` is opt-in. Runs all enabled rules; mutates as spec'd.
- `series_r{5,6,7}_enabled` gate each individual rule within any non-off mode. Default all three to `False`.

R2 has **no flag** because it is not in the code base at all.

### 6.2 Trigger evaluation shared between shadow and live paths

The trigger for each rule R_k is a pure function `evaluate_R_k(patch_review, series_ctx) -> Optional[TriggerMatch]`. The rule's mutation function `apply_R_k(patch_review, trigger_match) -> MutationResult` is called only in `mode="on"`. In `mode="shadow"`, only `evaluate_R_k` runs and its result is recorded. This ensures shadow and live agree on triggers by construction — there is no independent shadow-only evaluator that could drift.

### 6.3 Severity floor across all suppressive rules

R1, R6, R7 must all respect:
- Never suppress `severity == "blocker"`.
- Never suppress `severity == "warning" AND confidence ≥ 0.7`.

This is one shared helper (`_is_safety_floored(comment) -> bool`), applied at the top of each rule's mutation path. Prevents the highest-cost class of false-positive suppression by construction.

### 6.4 Same-category constraint on R4/R5

R4 and R5 must both:
- Only merge findings where `finding_a.category == finding_b.category` OR both categories ∈ `{convention, style, nit}`.
- Kept finding's `severity = max(f.severity for f in merged_set)`.

This is not optional. Without it, R4/R5 exhibit a specific class of severity-collapse regression on real reviews.

### 6.5 R3 rewrite writes to a separate field

Add `InlineComment.series_prefix: str = ""`. R3 sets `series_prefix = "[Series-internal dependency — patch N/total]"`. `comment.message` is unchanged. The UI renders `series_prefix + message` when non-empty. This preserves R4/R5's Jaccard input as the agent originally emitted it.

### 6.6 Audit-trail carries agent-source

Every `ReducerAction` records the agent(s) whose finding was affected. For merge rules, `absorbed_agent_source: tuple[str, ...]` names which agent contributed the dropped finding. Enables a downstream "we systematically drop subsystem-expert findings in favour of code-quality nits" audit.

### 6.7 Determinism regression test

New test `test_reducer_is_deterministic_across_runs`: build+reduce the RubikPi3 fixture three times, assert `repr()` equality on the reducer output. This catches any future `set` / `dict.items()`-ordering regression that the AST scanner misses.

### 6.8 Serialization backward-compatibility test

`test_report_backward_compatibility`: (a) load a pre-WP-S1B serialized report, assert successful parse with default `series_provenance = None`; (b) serialize a WP-S1B report, load it under a minimal pre-WP-S1B schema, assert unknown-field tolerance. Guards §4.1.

### 6.9 Model-rotation guard

Add a docstring-level TODO (not a code check) in the reducer module: "This rule's trigger is a substring match on LLM-generated prose. Re-audit fire rates when the reviewing model is rotated." This is a documentation obligation, not a test. It exists to make the risk visible to whoever changes the model config later.

### 6.10 Prompt-reinjection guard (already present)

Extend the existing S5 test (`test_reducer_output_not_reinjected_into_prompts`) to also cover shadow-mode output. Every field in `series_shadow_actions` must be tested for absence from any subsequent prompt. Strategy C boundary must not be crossed by shadow output either.

---

## 7. Implementation Order

Following the WP-S1 spec's Step 7 – Step 18 structure but respecting the narrower scope:

**Phase A — infrastructure, no live behaviour change**
1. **Step B1.** `kri/series/reducer.py` skeleton — pure module, per-rule `evaluate_R_k` / `apply_R_k` split. Wire the shared severity-floor and same-category helpers. No rule enabled yet.
2. **Step B2.** `InlineComment.series_provenance` + `InlineComment.series_prefix` schema additions. Backward-compat test (§6.8).
3. **Step B3.** `IntelligentReviewEngine` gains `series_reducer_mode` flag, default `"off"`. Wiring test asserts `mode="off"` produces byte-identical output to the WP-S1A endpoint. Sec. 40 allowlist updated for the new `time.monotonic()` line shifts if any.

**Phase B — safe rules land live**
4. **Step B4.** R8 coupling annotation + tests U26/U27/U28. Additive-only, so `mode="on"` is safe from day one for R8. Ambiguity in "already discusses" resolved to token-level exact match on the symbol name.
5. **Step B5.** R1 declared-symbol suppression + guards (same-sentence window, severity floor, word-boundary) + tests U11/U12 + adversarial tests for the guards (a blocker with an R1 phrase must survive; a symbol collision in a different sentence must not fire).
6. **Step B6.** R3 external-to-internal rewrite → into `series_prefix` (not `message`) + tests U15/U16 + a specific test that R4/R5 Jaccard on the R3-affected finding is unchanged.
7. **Step B7.** R4 line-bucket dedup + same-category guard + severity-max + tests U17/U18 + a specific test for the severity-collapse scenario (a blocker at line 220 vs an info nit at line 229 with higher confidence: assert the kept finding is the blocker).
8. **Step B8.** Regression run on RubikPi3 with `mode="on"` and only R1/R3/R4/R8 enabled. Compare finding-count vs the WP-S1A baseline. Target: reduction on the 3 missing-binding false positives via R1; ≥1 coupling note via R8. No blocker or warning silently lost.

**Phase C — shadow-only rules land disabled**
9. **Step B9.** R5 function-scope dedup with the domain-token constraint (§4.4). Feature-flagged off. `evaluate_R5` runs in shadow mode; `apply_R5` gated by `series_r5_enabled`. Tests U19/U20 + a boilerplate-collision negative test.
10. **Step B10.** R6 low-signal suppression. Feature-flagged off. Shadow-mode records fire rate. Tests U23/U24/U25.
11. **Step B11.** R7 pre-existing suppression. Feature-flagged off. Shadow-mode records fire rate. Tests U21/U22.

**Phase D — invariants + UI**
12. **Step B12.** Determinism regression test (§6.7). Strategy-C test extension (§6.10). Reducer-output-safety scan.
13. **Step B13.** UI: report-level `series_reducer_actions` collapsible AND `series_shadow_actions` collapsible (separate). Per-patch coupling collapsible. Both hidden when their list is empty (visibility rule).
14. **Step B14.** RubikPi3 regression fixture + tests.

**Phase E — evidence-gathering window (no code changes)**
15. **Step B15.** Enable `series_reducer_mode="shadow"` on the operator's config for N reviews. Manual audit of shadow entries for R5/R6/R7. Once each rule has ≥ some threshold of clean shadow entries with no false-positive suppressions of blockers/warnings, propose flipping *only that rule's* enable flag to True in a follow-up commit.

R2 does not appear anywhere in this plan. If a companion-presence rule is later desired, it enters as a new rule proposal, not as a WP-S1B follow-up.

---

## 8. Go / No-Go Recommendation

**Go, with narrowed scope (Option C-modified, not the review's Option B).**

**Justification:**

1. **The WP-S1A validation gates were all met.** Series-aware prompt injection landed cleanly with no regression, byte-identity preserved, determinism preserved, 236/236 tests green. The infrastructure to safely land more work exists.

2. **The reducer's blast radius is not homogeneous across rules.** R1, R3, R4, R8 have grounded, structurally-evidenced triggers. R2 is logically unsound. R5/R6/R7 depend on uncalibrated LLM prose signals. Bundling all eight into a single WP-S1B commit — even in shadow — treats them as if they had equal risk. They do not.

3. **Shadow mode is a good rollout mechanism but not a rule-correctness argument.** Shadow observation on real traffic informs the calibration questions (what rate does R6 fire, what proportion of R5 merges look correct). It does not turn a logically unsound rule (R2) into a correct one, and it does not turn an uncalibrated signal (R6's confidence threshold) into a calibrated one.

4. **The narrower scope preserves the value of WP-S1A.** WP-S1A already eliminated most of the missing-binding false positives by injecting the declared-symbol registry into prompts (measured on RubikPi3). R1 finishes that job for the residual cases. R3 and R8 add the "series-internal dependency" reframing and coupling notes. R4 removes the specific line-bucket duplicate class. That is most of the operator value; R2/R5/R6/R7 are marginal by comparison.

5. **The safeguards are cheap.** Severity floors, same-category constraints, `series_prefix` for R3, per-rule enable flags — none of these is a large engineering item, and each closes a specific correctness gap that would otherwise silently ship.

6. **The rollout is reversible.** Reducer flags default to `"off"`. Per-rule flags default to `False` for R5/R6/R7. R2 is not in the tree. Flipping any of these on is a config change, not a code change. Flipping them off after a regression is also a config change.

**What "Go" is conditional on:**
- Every safeguard in §6 lands with the initial WP-S1B commit series. Not "in a follow-up."
- The RubikPi3 regression test asserts that no `blocker` or `warning` (with confidence ≥ 0.7) is dropped by any rule in any mode.
- R2 is removed from the spec (or explicitly marked "deferred pending redesign") before Phase B begins, so no reviewer of the commit series treats R2 as a landed capability.
- The shadow-mode observation window and its exit criteria are documented (a follow-up ADR or the WP-S1B doc itself), so the flip from shadow-per-rule to live-per-rule is not an ad-hoc decision.

---

## Detailed subsection responses

### A. Shadow Mode Architecture

**Rollout model (off → shadow → on):** validated with modification. I recommend the default at first landing be `off`, not `shadow`. Reasoning in §3.5. The three-state enum is otherwise correct.

**Files/classes impacted:**
- `kri/series/reducer.py` — new module.
- `kri/series/models.py` — no changes needed; `ReducerAction`/`ReducerActionKind`/`SeriesProvenance` already stubbed by WP-S1A (`kri/series/models.py:86–130`).
- `kri/llm/models.py` — add `series_provenance: SeriesProvenance | None = None` and (per my §6.5) `series_prefix: str = ""` on `InlineComment`.
- `kri/llm/reviewer.py` — construct reducer conditionally, call after `_review_patch` loop, before `_generate_overall_assessment`. Add flag parameters.
- `kri/web/app.py` — two collapsibles (live + shadow) + coupling panel.
- `tests/test_series_reducer.py` — new.
- `tests/test_series_regression_rubikpi.py` — new.
- `tests/test_series_wiring.py` — extend for mode + per-rule flag coverage.
- `tests/test_stochastic_confinement.py` — no change expected (reducer is pure Python).

**Minimal churn:** yes, if the trigger/mutation split (§6.2) is followed. The reducer is a single new module; wiring is one call site in `_review_patch` orchestration.

**Trigger evaluation shared:** yes, by the `evaluate_R_k` / `apply_R_k` split. Shadow mode calls only evaluators.

**Determinism preserved:** yes if (a) all rules iterate over `sorted()` collections and never over `set`s, (b) `_merge_comments`' input ordering to the reducer is stable (it already is — sorted by severity/file/line at `kri/llm/reviewer.py:199-206`), (c) new determinism test lands (§6.7). Existing Sec. 40 AST scan protects against `time`/`random`/etc. leaking in.

### B. R1–R8 Rule Review

| Rule | Agree with risk? | Valid correctness issue? | Recommended action |
|---|---|---|---|
| R1 | Yes | Yes — loose symbol regex + cross-sentence match | **Land with guards** (same-sentence window, severity floor, word-boundary) |
| R2 | Yes and stronger | Yes — logically unsound (no companion check) | **Defer** — do not ship even in shadow |
| R3 | Partial | Yes — Jaccard drift into R4/R5 (§4.3) | **Land with guards** — write to `series_prefix`, not `message` |
| R4 | Yes | Yes — severity collapse, cross-category merge | **Land with guards** — same-category + severity-max |
| R5 | Yes | Yes — boilerplate token bleed | **Land disabled** — enable after domain-token constraint + shadow evidence |
| R6 | Yes | Yes — uncalibrated confidence threshold | **Land disabled** — enable after per-category calibration study |
| R7 | Yes | Yes — LLM-hedge dependence | **Land disabled** — enable after shadow evidence on real traffic |
| R8 | Yes | Ambiguity in "already discusses" (§4.5) | **Land unchanged** after resolving ambiguity |

Special-attention items:
- **R1 cross-sentence symbol matching:** confirmed real; guard is same-sentence window (200-char slice around the R1 phrase).
- **R2 companion-patch inference:** confirmed absent from spec; not fixable by tightening prose, only by adding a companion-presence check against `declared_symbols`. That is a new rule, not a modified R2.
- **R4 severity collapse:** confirmed by spec text; fixable with severity-max and same-category constraint.
- **R5 category collapse:** confirmed; fixable with same-category + domain-token constraint.
- **R6 confidence calibration:** confirmed; not fixable in code, only in policy — disable until data.

### C. Challenge the Review

**Recommendation 1 — shadow mode as a runtime feature flag (Option B).**
- *What could go wrong:* operators leave shadow mode on indefinitely; the audit trail accumulates without being reviewed; the "flip to on" decision becomes a rubber-stamp. Mitigation: exit-criteria for the flip must be defined in advance (§7 Step B15) and reviewed as a design artifact, not just a config change.
- *Complexity introduced:* two code paths (evaluate + apply), one metadata schema (shadow vs live actions kept separate). Modest.
- *Constitution:* no violation. All rules are deterministic; shadow entries are audit output, not decisions.
- *Maintenance burden:* every new rule R_k must implement both evaluators and mutators. This is a fixed per-rule cost; the payoff is decisive during rollout.

**Recommendation 2 — severity floor for R1/R2/R6/R7.**
- *What could go wrong:* the floor could inadvertently disable a rule the operator wanted to fire on genuinely low-value warning-severity findings. Very unlikely in practice — the floor targets warning + confidence ≥ 0.7, which is a high bar.
- *Complexity:* one shared helper, called from four rule bodies. Trivial.
- *Constitution:* none.
- *Maintenance burden:* negligible.

**Recommendation 3 — same-category constraint on R4/R5.**
- *What could go wrong:* two findings that are the same defect but that different agents categorised differently (`bug` vs `general`) will fail to merge and both survive. This is a false-negative merge, i.e. two findings instead of one. Cheap failure mode — better than merging a `bug` and a `nit`.
- *Complexity:* one boolean check per rule. Trivial.
- *Constitution:* none.
- *Maintenance burden:* if the category vocabulary grows, the "both in `{convention, style, nit}`" allowlist has to grow too. Documented and low.

**Recommendation 4 — R2 reformulation-or-defer.**
- *What could go wrong:* deferring R2 leaves the "submitted alone" false-positive class unaddressed on multi-patch series. But this class rarely surfaces on RubikPi3 (a single-family series) and there is no benchmark evidence it is a top pain point. Not shipping is safer than shipping wrong.
- *Complexity:* zero — the rule is not in the code base.
- *Constitution:* none.
- *Maintenance burden:* zero.

**Recommendation 5 — R1 window tightening.**
- *What could go wrong:* a legitimate R1 fire whose R1 phrase and symbol name are more than 200 chars apart in the agent's message gets missed. That is a false negative — the finding survives. Acceptable failure mode.
- *Complexity:* one regex slice. Trivial.
- *Constitution:* none.
- *Maintenance burden:* zero.

### D. Success Criteria Review

**SM1–SM10 (shadow-mode tests):**
- Missing: no test asserts the reducer's decisions are stable across the `_merge_comments` boundary (§4.2). Add SM11.
- Missing: no test asserts the R3 prefix does not affect R5 Jaccard (§4.3). Add SM12.
- Missing: no test asserts backward compatibility of the serialized report (§4.1, §6.8). Add SM13.
- Missing: no test for the severity-floor helper (§6.3). Add SM14 — "R1 does not suppress a blocker even when trigger phrase and declared symbol match."
- Missing: no test for R4 same-category constraint (§6.4). Add SM15 — "R4 does not merge a bug and a convention at nearby lines."
- Missing: no test that per-rule flags actually gate their rule. Add SM16 — "with `series_r5_enabled=False`, R5 never appears in `series_reducer_actions` in `mode='on'`."
- Redundant: SM8 ("mode='off' equals WP-S1A") is almost the same test as the WP-S1A wiring test W7. Consolidate — one is enough.
- Too weak: SM10 ("captures expected suppressions" on RubikPi3) — should also assert that no `blocker` or `warning` (conf ≥ 0.7) appears in `would_have_been_suppressed`, not just that R1's expected suppressions appear.

**SM-C1–SM-C8 (success criteria for flipping to `"on"`):**
- SM-C1 (default `"shadow"`) — disagree; default should be `"off"`. Rewrite to "default is `"off"`; shadow is opt-in; live-per-rule is opt-in per rule."
- SM-C2 (10 tests pass) — reasonable but the count is wrong given the additions above. Update to reflect the actual test suite size.
- SM-C3 (severity floor + same-category) — agree, mandatory.
- SM-C4 (R2 reformulated-or-excluded) — agree, and I'd sharpen to "excluded from initial landing; treated as a new rule if revisited."
- SM-C5 (N=5 clean series) — too weak. N=5 is small; a 5-series audit will not detect a 5% false-positive suppression rate. Recommend N ≥ 20 distinct series or ≥ 50 total findings observed under shadow before flipping any single rule.
- SM-C6 (1:1 `would_have_been_*` ↔ `ReducerActionKind`) — agree, straightforward.
- SM-C7 (Sec. 40 green) — agree, mandatory.
- SM-C8 (single-line PR to flip default) — unrealistic if we're flipping per-rule. Restate as "flipping any per-rule flag from False to True is a config-only change that requires no test changes." That is achievable with the geometry in §6.1.

**Unrealistic criteria:** the review's SM-C1 assumes shadow-by-default is the safest first landing. I disagree — see §3.5.

### E. Evidence-First Assessment

Applying `evidence > heuristics > model opinion`:

- **R2 should be deferred:** **Yes.** R2's trigger is pure heuristic pattern-match on model opinion (agent-generated prose). No structural evidence anchors the rule. The rule's stated goal (recognise that the reviewer's requested companion is present) requires evidence — a `declared_symbols` lookup — that the rule does not use. Defer.

- **R6 should be disabled initially:** **Yes.** R6 depends on `comment.confidence`, which is model opinion. No calibration study, no per-category thresholds derived from data. Ship the evaluator (as shadow-only) to gather evidence; do not enable the mutator until data supports a threshold.

- **Severity floors should be mandatory:** **Yes.** A severity floor is *not* a heuristic; it is a hard invariant that says the reducer never silently drops a high-confidence high-severity finding. The invariant does not depend on model opinion or on heuristic tuning. It is the cheapest single safeguard that closes the largest single class of catastrophic failure.

- **Same-category merge constraints should be mandatory:** **Yes.** Same reasoning as severity floors. A same-category constraint is an invariant, not a heuristic. It says "we never conflate a bug with a nit." The cost of enforcing it is one boolean check; the benefit is preventing the specific severity-collapse regression that R4/R5 would otherwise ship with.

Summary: all four "should we make this mandatory" questions resolve to **yes**, and the reasoning is the same in each case — the safeguard is grounded in structural invariants, not in heuristic tuning that would require observation to calibrate.

### F. Production Rollout Recommendation

**Not Option A.** Landing WP-S1B directly ignores every risk enumerated above. Reject.

**Not Option B as stated.** The shadow-mode review's Option B lands all 8 rules simultaneously in shadow, default-on. This is safer than direct-live but still bundles heterogeneous risk. Reject as stated; accept only with the modifications in §3.5 and §5 (narrower scope, off-by-default, per-rule flags).

**Not Option C as stated.** The review's Option C is R1 + R3 + R8 only, deferring all suppressive rules. This defers R4, which I believe is safe to land with the two guards (severity-max, same-category). Rejecting R4 unnecessarily forfeits its concrete value (line-bucket duplicates are a real and frequent class).

**Option D — modified narrow scope with shadow-mode-per-rule feature flags.** This is my recommendation. It combines:
- Narrow live scope: R1 + R3 + R4 + R8 (with guards §6.3–§6.5).
- Shadow-only rules: R5 + R6 + R7 (code lands, mutation gated by per-rule flags default False).
- Deferred entirely: R2 (not in code).
- Default reducer mode: `"off"`.
- Explicit exit criteria for enabling any shadow rule (§7 Step B15, §D SM-C5 tightening).

**Defend the choice:**
1. Preserves reversibility — every enable is a config change.
2. Preserves blast-radius isolation — the four live rules have grounded triggers; the three shadow rules have prose-signal triggers under observation.
3. Preserves the WP-S1A precedent — feature flags default to prior behaviour.
4. Preserves the operator's ability to compare shadow entries against real traffic without committing to any live suppression.
5. Preserves the "correctness over schedule" principle — R2 is deferred, R6/R7 are shadow-only, because we do not yet have evidence they are correct.
6. Preserves the "evidence over intuition" principle — R1/R3/R4/R8 are live because their triggers are grounded in *declared_symbols* / *addition-line diff geometry* / *line-number arithmetic*, all of which are structural evidence.

Rules that should not ship in initial WP-S1B (in any mode, including shadow):
- **R2** — logically unsound; do not ship. If a companion-present rule is desired later, propose it as a new rule with `declared_symbols` grounding.

Rules that ship shadow-only in initial WP-S1B (mutation disabled):
- **R5, R6, R7** — trigger evaluators land; mutations gated by per-rule flags default `False`.

Rules that ship live in initial WP-S1B (mutation enabled once `series_reducer_mode="on"`):
- **R1, R3, R4, R8** — with the safeguards in §6.

---

## FINAL RECOMMENDATION

**[GO_WITH_NARROW_SCOPE]**

**Justification:** WP-S1B can safely land, but not as spec'd and not as the shadow-mode review proposes. R2 as spec'd is logically unsound and should not ship in any mode. R5/R6/R7 depend on uncalibrated LLM-prose signals and should land as shadow-only until real-traffic evidence supports enabling them. R1/R3/R4/R8 have structurally-grounded triggers and, with the enumerated safeguards (severity floor for R1; separate `series_prefix` field for R3; same-category + severity-max for R4; ambiguity resolution for R8), can ship live behind an off-by-default runtime flag. This preserves the WP-S1A precedent of feature-flag-default-preserves-prior-behavior, keeps the rollout fully reversible via config changes, and gates every high-risk rule behind evidence gathered from shadow observation before its mutation ever runs on production traffic. Correctness is prioritised over schedule; evidence is prioritised over intuition; and every rule that has not earned its confidence yet stays shadow-only or deferred.
