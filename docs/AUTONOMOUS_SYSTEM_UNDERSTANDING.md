# Autonomous System Understanding

**Date:** 2026-07-24
**Status:** Phase 0 output — read all architecture sources, extracted understanding
**Sources read:** All Layer-1 PDFs, all Layer-3 in-repo docs, 20 commit messages,
module docstrings on 10 key files

---

## 1. Documents Read

| # | Document | SHA-256 prefix | Role | Claim |
|---|----------|----------------|------|-------|
| 1 | `KRI_Architecture_Blueprint_and_Engineering_Constitution.pdf` | f5863828 | Primary constitution | Load-bearing |
| 2 | `KRI_ASoC_Engineering_Intelligence_Model.pdf` | 0ce96bc9 | Domain model | Load-bearing |
| 3 | `KRI_Engineering_Intelligence_Packages.pdf` | 8282aa03 | Package architecture | Load-bearing |
| 4 | `KRI_Skill_Engineering_Framework.pdf` | 6a8a763c | Skill transfer | Load-bearing |
| 5 | `Arch_doc.rtf` (= `Implemantaion_Blueprint.rtf`) | md5 4aa6177c | LLM-generated notes | Supporting |
| 6 | `kri/docs/WP_S1_SERIES_AWARE_REASONING_2026-07-21.md` | — | WP-S1 arch review | Load-bearing |
| 7 | `kri/docs/WP_S1_SERIES_AWARE_REASONING_SPEC_2026-07-21.md` | — | WP-S1B spec | Load-bearing |
| 8 | `kri/docs/WP_S1B_IMPLEMENTATION_READINESS_REVIEW_2026-07-22.md` | — | WP-S1B scope review | **Supersedes items in #7** |
| 9 | `kri/docs/WP_S1B_SHADOW_MODE_REVIEW_2026-07-21.md` | — | Shadow rollout design | Supporting |
| 10 | `kri/docs/PATCH_APPLICABILITY_GATE_ARCHITECTURE_2026-07-20.md` | — | WP-T2A gate arch | Load-bearing |
| 11 | `kri/docs/WP_T2A_APPLICABILITY_GATE_SPEC_2026-07-21.md` | — | WP-T2A spec | Load-bearing |
| 12 | `kri/docs/ARCHITECTURE_AUDIT_2026-07-21.md` | — | Runtime capability matrix | Authoritative for current state |
| 13 | `kri/docs/POST_WP_CP1_QUALITY_GAP_REVIEW_2026-07-21.md` | — | RubikPi3 quality gaps | Requirements for WP-S1 |
| 14 | `kri/docs/FP_CLASSIFICATION_2026-07-16.md` | — | False positive root cause | Pattern layer fix requirements |
| 15 | `kri/docs/BENCHMARK_HISTORY.md` | — | Benchmark progression | Supporting |
| 16 | `kri/docs/MAINTAINER_STYLE_AUDIT_2026-07-21.md` | — | Style analysis | Supporting |
| 17 | Last 20 git commit messages (full bodies) | — | Implementation decisions | Supporting |

---

## 2. Architectural Goals (from L1-A Constitution)

1. **Model maintainer cognition** — not a linter, not a chatbot. Simulate the 8-phase cognitive chain: Perception → Comprehension → Contextualization → Evaluation → Synthesis → Prediction → Decision → Explanation.
2. **Evidence-backed reviews** — every review comment requires an Evidence Graph node. No comment without sourced, verifiable evidence.
3. **Explainability-first** — the output is an education, not a verdict. WHY is primary; WHAT is secondary.
4. **Deterministic execution** — given the same patch + knowledge state, produce byte-identical output. Non-determinism is confined to `kri/learning/`.
5. **Domain isolation** — zero hardcoded ASoC logic in the Generic Runtime. All domain knowledge lives in pluggable Domain Knowledge Packages (DKPs).
6. **Human-in-the-loop** — KRI advises, humans decide. KRI never auto-submits, never impersonates.
7. **Continuous learning** — closed-loop validation against historical maintainer reviews. Every learned pattern must be validated before deployment.

---

## 3. Non-Negotiable Principles (constitutional — never override)

| # | Principle | Source | Implementation location |
|---|-----------|--------|------------------------|
| P1 | No review comment without an Evidence Graph node | §28 Constitution | `kri/evidence_engine/engine.py` |
| P2 | No fabricated evidence, commits, lore URLs, or maintainer quotes | §29 Constitution | Constitutional rule + test |
| P3 | Confidence < 0.40 → suppress the finding, do not present as Unknown-labeled output | §30 Constitution | `kri/llm/reviewer.py:_merge_comments` |
| P4 | Confidence is computed from observable factors, not guessed | §31 Constitution | Confidence Factor Model §16 |
| P5 | Deterministic execution — same inputs + knowledge state → same output | §40 + §38 Constitution | Sec-40 test suite |
| P6 | No `random`, `time.*`, `datetime.now`, `uuid.uuid1/4` outside `kri/learning/` | Engineering Constitution §40 | `tests/test_stochastic_confinement.py` |
| P7 | No `verify=False` outside `kri/llm/client.py` | Standing security constraint | Code review gate |
| P8 | KRI does not impersonate individuals. Profiles are statistical aggregates only. | §34 Constitution | Ethical constraint |
| P9 | Architecture core is frozen: K→R→D→E→V separation, DKP interface, Confidence Engine, Evidence Graph | §32 Constitution | Arch-set files |
| P10 | Blockers and warnings with confidence ≥ 0.7 are never suppressed | Safety floor — standing invariant | `SeriesReducer._is_safety_floored()` |

---

## 4. Global Invariants (property of the running system)

| # | Invariant | Where enforced |
|---|-----------|---------------|
| I1 | `mode="off"` produces byte-identical output to pre-WP-S1A baseline | `tests/test_series_reducer_b1.py`, Validation Layer C |
| I2 | Single-patch series produces output byte-identical to pre-WP-S1 (before series awareness) | `test_no_series_ctx_no_regression` |
| I3 | `_compute_diagnostics` runs on immutable pre-mutation input | `test_diagnostics_reflect_pre_mutation_input_under_mode_on` |
| I4 | `_measure_agent_overlap` runs before `_merge_comments` | `kri/llm/reviewer.py` ordering |
| I5 | R3 rewrites go to `InlineComment.series_prefix`, never to `.message` | Prevents Jaccard drift into R4/R5 |
| I6 | R4/R5 only merge findings sharing the same category (or both in `{convention,style,nit}`) | Same-category guard |
| I7 | Kept finding in R4/R5 merge carries `severity = max(merged_severities)` | Severity-max guard |
| I8 | `finding_ref` is content-derived (blake2b/8), not positional | `_comment_ref()` |
| I9 | Governance rules may only be modified in T3+ scope | Governance module + STOP condition |
| I10 | Commit identity always `Ajay Kumar Nandam <ajay.nandam@oss.qualcomm.com>` + `-s` | STOP condition |

---

## 5. Governance Constraints

| # | Rule | Source |
|---|------|--------|
| G1 | KRI reviews are advisory — they do not block patch submission | §27 Constitution |
| G2 | KRI output must include disclaimer: "This is an engineering intelligence simulation, not an official review." | §27 Constitution |
| G3 | All data sourced from public repositories only. No private data ingested. | §27 Constitution |
| G4 | Domain Knowledge Packages are owned by domain experts. Updates require benchmark validation. | §27, §41 Constitution |
| G5 | Critical knowledge updates (rule changes, API deprecations) require human review. | §27 Constitution — CONFLICT with autonomous execution requirement. See §9 below. |
| G6 | Every evidence link verified by the Evidence Engine before inclusion | §29 Constitution |
| G7 | Knowledge state promotions: dev → staging → production pipeline | §27 Constitution |

---

## 6. Current Implementation State vs Blueprint

### Implemented and matching blueprint

| Capability | Blueprint location | Implementation |
|-----------|-------------------|----------------|
| Lore thread ingestion | §21.3, §18.1 | `kri/lore_manager/` |
| Patch series parsing | §21.2 | `kri/patch_manager/` |
| LLM-powered review (3-agent, parallel) | §12, §21.7 | `kri/llm/reviewer.py` |
| Checkpatch execution | §12.5, §21.6 | `kri/static_analysis/manager.py` (wired as of WP-CP1) |
| Series-aware prompt injection | §12.1, WP-S1A | `kri/series/context.py`, `kri/series/prompt.py` |
| SeriesReducer skeleton (R1, R3, R4, R8 live) | WP-S1B spec | `kri/series/reducer.py` |
| Reducer diagnostics (counters) | WP-S1B/B6 | `ReducerDiagnostics` in `reducer.py` |
| Confidence filtering (< 0.4 suppressed) | §30 Constitution | `kri/llm/reviewer.py:_merge_comments` |
| Determinism test suite | §40 Constitution | `tests/test_stochastic_confinement.py` |
| Content-hash finding_ref | Adversarial report finding F2 | `_comment_ref()` blake2b |

### Implemented but not wired to web flow

| Capability | Blueprint location | Implementation |
|-----------|-------------------|----------------|
| Repository checkout + patch application | §12.2, §12.3, §21.1 | `kri/repo_manager/manager.py` — IMPLEMENTED_NOT_WIRED |
| Sparse/smatch/coccinelle | §12.5, §21.6 | `kri/static_analysis/manager.py` — stubs |
| Git blame lookups | §12.6, §21.1 | `kri/repo_manager/manager.py:237` — IMPLEMENTED_NOT_WIRED |

### Missing entirely (blueprint asserts, no code)

| Capability | Blueprint location | Gap |
|-----------|-------------------|-----|
| Engineering Knowledge Graph (EKG) queryable semantic network | §8, §21.4 | Rule-based DKP patterns exist; full EKG graph store absent |
| Evidence Graph per review comment | §15, §28 | Evidence field exists in schema; not populated from the EKG |
| Counterfactual Review Intelligence | §13 | Not implemented |
| Learning Feedback Loop | §18 | Not implemented |
| Build validation | §12.4, §21.5 | Not implemented |
| Benchmark Framework (agreement, FP, calibration) | §24 | Partial — `tests/test_benchmark_regression.py` exists |
| Review Explainability Report (full structured) | §17 | Partial — `IntelligentReport` lacks Evidence Graph |
| Domain Knowledge Package v2 | §9, §10 | Only ASoC v1 DKP (18 patterns) shipped |

---

## 7. Ambiguities (with recommended interpretation)

| # | Ambiguity | My interpretation | Confidence |
|---|-----------|-------------------|------------|
| A1 | §35 "human-in-the-loop" vs autonomous execution requirement | Human approval is required for *blueprint mutations* (T4) and *knowledge state promotions to production*. Autonomous code implementation (T1–T3) does not require per-commit approval — it has governance gates instead. | [Likely] — the autonomous execution requirement is an operational decision that adds to, not overrides, the constitution's human-in-the-loop rules. |
| A2 | §27 "critical knowledge updates require human review" scoping | "Knowledge updates" means DKP pattern changes and EKG mutations, not general code implementation. Code that wires existing capabilities doesn't modify the knowledge state. | [Likely] |
| A3 | "Architecture is frozen" vs WP-S1B/T2A adding new components | Freeze applies to the five-layer K→R→D→E→V separation, the DKP interface contract, the Confidence Engine schema, the Evidence Graph schema. Adding new modules (`SeriesReducer`, `ApplicabilityGate`) that interact through the existing interfaces is explicitly "Allowed Without Review" per §32. | [Certain] — §32 explicitly names "new static analysis scripts" and "UI improvements" as allowed; `SeriesReducer` is analogous. |
| A4 | R5/R6/R7 shadow-only vs disabled: which is the current state? | Per WP-S1B/B6 commit: `series_reducer_mode="off"` by default; only R1/R3/R4/R8 mutate under `mode="on"`. R5/R6/R7 evaluate only. R2 is not in the tree. | [Certain] — confirmed by `kri/series/reducer.py` structure. |
| A5 | Rule-based review engine vs LLM review engine relationship | Both should run. Blueprint §12.10 says "combine all decisions from static analysis, historical comparison, API evolution, and cognition reasoning." Current state: two separate endpoints. The gap is that they should be merged into one pipeline. | [Likely] |

---

## 8. Risk Areas

| # | Risk | Impact | Mitigation |
|---|------|--------|-----------|
| R1 | EKG not implemented — all "evidence" is currently LLM inference, not traced evidence | Constitutional rule §28 violated: comments exist without Evidence Graph nodes | Medium-term: DKP v2 must build EKG nodes. Short-term: LLM reasoning field serves as partial evidence. |
| R2 | `_R1_PRECONDITION_HINTS` word "binding" matches too broadly | 0/15 hits in 6-series scan were legitimate R1 targets — indicates the hint vocab is too permissive | Task #69: consider narrowing or retiring this counter |
| R3 | PatchManager parses reviewer replies as patches | Creates spurious patches in the series; some of the 17 R1 "hits" are from reviewer-reply text | Task #70: PatchManager parsing fix |
| R4 | `_measure_agent_overlap` currently measures before `_merge_comments` but only 2 review agents exist | The metric is accurate but the naming "3-agent" in older code caused confusion | Fixed in B6; test locks the count at 2 |
| R5 | R3 series_prefix not yet stored in a separate field | WP-S1B/B5 committed R3 to `InlineComment` — check whether `series_prefix` field was added or prefix is in `message` | Verify in Phase 3 reconciliation |
| R6 | Shadow-mode evidence collection for R5/R6/R7 not yet started | Need ≥20 distinct series or ≥50 findings observed before enabling those rules | Task: shadow-mode batch run scheduled after T1 baseline |

---

## 9. Conflicts (between sources)

See `BLUEPRINT_RECONCILIATION.md` for the full conflict register with proposed resolution.

**Summary of load-bearing conflicts:**

| # | Type | Sources | Status |
|---|------|---------|--------|
| C1 | INTENT_VS_REALITY | Constitution §35 "human always in control" vs autonomous execution requirement | Resolved by A1 interpretation above — autonomy operates within T1–T3 constraints, not as a bypass |
| C2 | INTENT_VS_REALITY | Constitution §28 "no comment without Evidence Graph node" vs current LLM-only path | Unresolved gap — LLM reasoning field is not a proper Evidence Graph. Blueprint aspirational for now; must be addressed in DKP v2. |
| C3 | VERSION_CONFLICT | WP-S1B spec §5 ships R2; WP-S1B readiness review §3.3 defers R2 entirely | **Readiness review is newer and explicitly supersedes spec on this point.** R2 is deferred. |
| C4 | VERSION_CONFLICT | WP-S1B spec §6 R3 prepends to `comment.message`; readiness review §6.5 says R3 must write to `series_prefix` field | **Readiness review supersedes.** Verify which was actually implemented in B5. |
| C5 | INTENT_VS_REALITY | Blueprint §27 "knowledge updates require benchmark validation + human approval" vs autonomous DKP changes | Any task touching ASoC DKP patterns goes through governance gate; no automated DKP promotion to production. |

---

## 10. Recommended Interpretation for Each Ambiguity

Recorded in §7 above. All five ambiguities have recommended interpretations.
The load-bearing one is **A1** — the autonomous execution framework operates
within, not in violation of, the constitution's human-in-the-loop requirements.
The distinction: T1–T3 code implementation does not require per-commit human
approval. T4 blueprint mutations and production DKP promotions do.
