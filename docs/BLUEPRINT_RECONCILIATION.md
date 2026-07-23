# Blueprint Reconciliation

**Date:** 2026-07-24
**Status:** Phase 3 output — maps every blueprint capability to implementation state
**Inputs:**
- All Layer-1 PDFs (Constitution, ASoC Model, Packages, Skills)
- All Layer-3 in-repo specs
- Current code: `kri/series/reducer.py`, `kri/llm/reviewer.py`, `kri/llm/models.py`, `kri/common/models.py`
- Architecture audit `ARCHITECTURE_AUDIT_2026-07-21.md`
- WP-S1B readiness review `WP_S1B_IMPLEMENTATION_READINESS_REVIEW_2026-07-22.md`

---

## 1. Capability Map

For each capability named in the blueprint, the status is one of:
- `Implemented` — code + tests exist, matches spec
- `Partial` — code exists, spec unmet in some dimension
- `Missing` — spec asserts it, no code
- `Diverged` — code exists but semantics differ from spec

### 1.1 Review Intelligence Pipeline (§12 Constitution)

| Step | Blueprint | Status | Evidence |
|------|-----------|--------|----------|
| §12.1 Lore ingestion + mbox parsing | Full thread + all replies | `Implemented` | `kri/lore_manager/`, `kri/patch_manager/` |
| §12.2 Repository preparation | Clone + checkout at target version | `Partial — not wired` | `kri/repo_manager/manager.py:99` exists but no web endpoint calls it |
| §12.3 Patch application + dependency resolution | `git apply`, failure analysis | `Partial — not wired` | `kri/repo_manager/manager.py:153` exists, `ApplicabilityGate` landed (WP-T2A) but not fully wired |
| §12.4 Build verification | Build + log capture | `Missing` | No build runner wired |
| §12.5 Static analysis | checkpatch (wired), sparse/smatch/coccinelle (stubs) | `Partial` | `kri/static_analysis/manager.py`; checkpatch wired as of WP-CP1 |
| §12.6 Historical pattern comparison | EKG query for similar patches | `Missing` | EKG graph store not implemented; only 18 DKP pattern rules exist |
| §12.7 API evolution comparison | Detect deprecated APIs | `Missing` | No API lifecycle knowledge graph |
| §12.8 Subsystem convention comparison | Apply DKP rules | `Partial` | 18 ASoC rules in `kri/packages/asoc/plugins.py`; very limited scope |
| §12.9 Correctness reasoning | Six-layer Reasoning Hierarchy | `Partial` | LLM agents cover layers 1–3 implicitly; layers 4–6 not separately addressed |
| §12.10 Review generation | Combine + deduplicate + prioritize | `Implemented` | `_merge_comments()` + `SeriesReducer.reduce()` |
| §12.11 Explanation production | Evidence Graph per comment | `Missing` | `reasoning` field populated but not a proper Evidence Graph |
| §12.12 Confidence assignment | Computed from factor model | `Partial` | LLM assigns confidence scores; Confidence Factor Model (§16) not implemented |
| §12.13 Evidence linking | At least one Evidence node per comment | `Missing` | Constitutional rule §28 requires this; not implemented for LLM path |

### 1.2 Series Awareness (WP-S1)

| Component | Spec | Status | Evidence |
|-----------|------|--------|----------|
| `SeriesContextBuilder` (pre-pass) | Pure diff analysis, no LLM | `Implemented` | `kri/series/context.py` |
| `SymbolRegistry` (declared symbols) | compatibles, dt_properties, c_symbols, files_added | `Implemented` | `kri/series/models.py` |
| Prompt injection `{series_context}` | Into CodeQuality + SubsystemExpert prompts | `Implemented` | `kri/series/prompt.py`, `kri/llm/agents.py` |
| R1 — Declared-symbol suppression | With severity floor + word-boundary + same-sentence window | `Implemented` | `kri/series/reducer.py:_evaluate_R1 / _apply_R1` |
| R2 — Series-present suppression | Deferred per readiness review §3.3 | `Correctly absent` | R2 not in codebase — per WP-S1B readiness review §5.3 |
| R3 — External-to-internal rewrite | Readiness §6.5: write to `series_prefix` field, not `message` | **DIVERGED** | `reducer.py:_apply_R3:565` prepends to `comment.message`. `series_prefix` field exists on `InlineComment` but is not used by R3. |
| R4 — Line-bucket dedup | Same-category + severity-max | `Implemented` | `kri/series/reducer.py:_evaluate_R4 / _apply_R4` |
| R5 — Function-scope dedup | Land disabled; shadow evaluate only | `Implemented` | `kri/series/reducer.py:_evaluate_R5`; `series_r5_enabled=False` |
| R6 — Low-signal suppression | Land disabled | `Implemented` | `series_r6_enabled=False` |
| R7 — Pre-existing suppression | Land disabled | `Implemented` | `series_r7_enabled=False` |
| R8 — Coupling annotation | Additive only | `Implemented` | `kri/series/reducer.py:_evaluate_R8 / _apply_R8` |
| `ReducerDiagnostics` | B6 counters | `Implemented` | `kri/series/reducer.py:ReducerDiagnostics` |
| `_measure_agent_overlap` before `_merge_comments` | Pre-merge measurement | `Implemented` | `kri/llm/reviewer.py` |

### 1.3 Applicability Gate (WP-T2A)

| Component | Spec | Status | Evidence |
|-----------|------|--------|----------|
| `ApplicabilityGate.check()` | Strategy C — metadata only, not in prompts | `Implemented` | `kri/repo_manager/gate.py` |
| `apply_status` in `PatchReview.metadata` | Gate result surfaced as metadata | `Implemented` | `kri/llm/reviewer.py` |
| Full worktree-based patch apply | WP-T2A spec — `git apply` against real tree | `Partial` | Gate runs; worktree management unverified |

### 1.4 Learning Architecture (§18–§20 Constitution)

| Capability | Status | Gap |
|-----------|--------|-----|
| Historical review ingestion | `Missing` | No automated ingest pipeline |
| Concern extraction from lore | `Missing` | Only manual classification in `FP_CLASSIFICATION_2026-07-16.md` |
| Pattern generalization | `Missing` | Current patterns are hand-authored |
| Pattern validation (statistical) | `Missing` | Benchmark framework exists but no generalization-to-validation pipeline |
| Knowledge versioning | `Missing` | DKP has no version update mechanism |
| Replayability | `Partial` | `replay_rubikpi_wp_s1a.py` exists; full replay framework absent |

### 1.5 Engineering Knowledge Graph (§8 Constitution)

| Component | Status | Gap |
|-----------|--------|-----|
| Node taxonomy (Subsystem, Maintainer, File, Function, ...) | `Missing` | Schema defined in spec; no graph store |
| Edge taxonomy (MAINTAINS, OWNS, CONTAINS, ...) | `Missing` | Schema defined; no graph store |
| EKG query for API correctness | `Missing` | API lifecycle knowledge absent |
| Evidence Graph per review comment | `Missing` | Constitutional rule §28 breached for all LLM-generated comments |
| Knowledge versioning (valid_from / valid_until) | `Missing` | Not implemented |

### 1.6 Runtime Modules

| Module | Blueprint (§21) | Status | Notes |
|--------|-----------------|--------|-------|
| Lore Manager | `kri/lore_manager/` | `Implemented` | |
| Patch Manager | `kri/patch_manager/` | `Partial` | Parses reviewer replies as patches — Task #70 |
| Repository Manager | `kri/repo_manager/` | `Partial — not wired` | |
| Knowledge Manager | `kri/knowledge_manager/` | `Partial` | Only loads ASoC DKP; no graph queries |
| Kernel Builder | — | `Missing` | |
| Static Analysis Manager | `kri/static_analysis/` | `Partial` | checkpatch complete; others are stubs |
| Review Engine | `kri/llm/reviewer.py` | `Partial` | No EKG integration; no Evidence Graph |
| Evidence Engine | `kri/evidence_engine/` | `Partial` | Returns rule-based evidence; no LLM path evidence |
| Learning Engine | `kri/learning/` | `Missing` | Placeholder only |
| Simulation Engine | `kri/simulation/` | `Partial` | Routes to both review paths; lacks full pipeline |

---

## 2. Drift Classification

| # | Capability | Status | Drift type |
|---|-----------|--------|-----------|
| D1 | R3 writes to `message` not `series_prefix` | Diverged | `incidental_regression` — readiness review explicitly required `series_prefix` field but implementation predates or ignores this requirement |
| D2 | Evidence Graph absent for LLM comments | Missing | `unresolved_gap` — blueprint §28 is a constitutional rule; LLM path was built first for MVP |
| D3 | LLM assigns confidence via model judgment, not via Confidence Factor Model §16 | Partial / Diverged | `intentional_evolution` — Confidence Factor Model requires historical data (EKG) that doesn't exist; LLM confidence is a practical substitute |
| D4 | EKG not implemented | Missing | `unresolved_gap` — full EKG requires historical ingestion pipeline |
| D5 | Repository Manager not wired to web flow | Not wired | `unresolved_gap` — capability exists, just not exposed |
| D6 | `_R1_PRECONDITION_HINTS` matches "binding" too broadly | Diverged (precision) | `incidental_regression` — 0/15 hits in 6-series scan legitimate; counter has near-zero signal value |
| D7 | PatchManager parses reviewer replies as patches | Bug | `incidental_regression` — spec says parse the patch series; reviewer replies are not patches |
| D8 | Generic Runtime contains ASoC-specific logic in some places | Partial | `incidental_regression` — blueprint §9.1 says no ASoC-specific logic in Generic Runtime |

---

## 3. Conflicts (must not be resolved silently)

### C1 — Constitution §35 vs Autonomous Execution Requirement

**Doc A:** Constitution §35: "Humans are always in control. KRI is an advisory tool. Engineers must make final decisions about patch submission. KRI must not auto-submit or auto-reply to mailing lists."

**Doc B:** User requirement (2026-07-24): autonomous execution through T1–T3 without human approval gates.

**Classification:** INTENT_VS_REALITY (operational policy vs constitutional principle)

**Recommended interpretation:** The constitutional rule targets user-facing patch submission behavior (KRI must not auto-submit patches to lore.kernel.org). It does not govern KRI's internal development workflow. Autonomous code implementation inside the KRI repository does not violate this rule. The rule's concern is: KRI should not be mistaken for an official maintainer. An autonomous development agent writing test cases inside the KRI repo is not the same as KRI pretending to be a maintainer on lore.kernel.org.

**STATUS: RESOLVED by scope interpretation.** Autonomous execution applies to KRI's own development workflow, not to KRI's patch submission behavior.

---

### C2 — Constitution §28 (Evidence Graph required) vs Current LLM Path

**Doc A:** Constitution §28: "No review comment shall be generated without supporting evidence in the Evidence Graph. The Evidence Graph must contain at least one Evidence node linked to the comment's Decision node."

**Doc B:** Current implementation: all LLM-generated comments in the intelligent review endpoint have `reasoning` field populated but no formal Evidence Graph node. Evidence Engine (`kri/evidence_engine/`) is wired only to the rule-based path.

**Classification:** INTENT_VS_REALITY — blueprint aspirational

**Evidence:** Architecture Audit §12 confirms this gap explicitly.

**Recommended resolution:** Treat as `blueprint_aspirational` for the LLM path currently. The `reasoning` field is a pragmatic substitute until the EKG is built. This is an MVP gap, not a constitutional violation in spirit (the blueprint explicitly says evidence must be traceable; LLM reasoning is internally traceable even if not stored in a graph structure). DKP v2 work must address this formally.

**STATUS: UNRESOLVED — requires Phase 4+ task to build Evidence Graph integration for LLM path.**

---

### C3 — WP-S1B Spec §5 (R2 ships) vs WP-S1B Readiness Review §3.3 (R2 deferred)

**Doc A:** `WP_S1_SERIES_AWARE_REASONING_SPEC_2026-07-21.md` §4: R2 is one of eight rules to implement.

**Doc B:** `WP_S1B_IMPLEMENTATION_READINESS_REVIEW_2026-07-22.md` §3.3: "R2 as spec'd is logically unsound and should not ship in any mode. If a companion-presence rule is desired later, propose it as a new rule with `declared_symbols` grounding."

**Classification:** VERSION_CONFLICT — readiness review is explicitly superseding

**Resolution:** Readiness review is newer and explicitly names the spec document as its input. R2 is deferred. R2 is not in the codebase. **This is the correct state.**

**STATUS: RESOLVED — readiness review governs.**

---

### C4 — WP-S1B Spec §6 (R3 prepends to message) vs Readiness Review §6.5 (R3 writes to series_prefix)

**Doc A:** `WP_S1_SERIES_AWARE_REASONING_SPEC_2026-07-21.md` §4 R3: rewrite by prepending to `comment.message`.

**Doc B:** `WP_S1B_IMPLEMENTATION_READINESS_REVIEW_2026-07-22.md` §6.5: "R3 rewrite writes to a separate field. Add `InlineComment.series_prefix: str = ""`. R3 sets `series_prefix = ...`. `comment.message` is unchanged. The UI renders `series_prefix + message` when non-empty."

**Classification:** VERSION_CONFLICT — and the implementation chose the WRONG resolution

**Current code state:** `kri/series/reducer.py:_apply_R3:565` — `new_msg = f"Depends on patch {sibling}: {c.message}"` — prepends to `comment.message`. This means R4/R5 Jaccard computation runs on R3-modified messages, creating the exact token overlap risk the readiness review warned about.

`InlineComment.series_prefix` field EXISTS (added in B2 schema) but is NOT written by R3.

**STATUS: UNRESOLVED — requires fix. Drift type: `incidental_regression`. Fix: update `_apply_R3` to write `series_prefix` instead of prepending to `message`. Add test asserting that R4/R5 Jaccard is unchanged after R3 fires.**

---

### C5 — Constitution §27 (DKP updates require human review) vs Autonomous DKP Changes

**Doc A:** Constitution §27: "Critical knowledge updates (rule changes, API deprecations) require human review."

**Doc B:** Autonomous execution plan allows T3-tier changes to `kri/governance/**` and potentially ASoC DKP patterns.

**Classification:** SCOPE_CONFLICT

**Resolution:** T3 authorizes editing arch-set files including `kri/governance/**`. However, "promoting a DKP rule to production" is a separate step governed by §27. Changes to rule files in the development tree are T3 operations; promoting those rules as "validated knowledge" requires the benchmark pipeline. In practice, no production DKP promotion happens autonomously — the benchmark framework that would validate it doesn't exist yet.

**STATUS: RESOLVED by scope. T3 edits to rule files are permitted. Production DKP promotion requires benchmark validation.**

---

## 4. Tasks Generated by Reconciliation

The following tasks are created by this analysis (to be tracked in the ledger):

| Task | Tier | Priority | Source |
|------|------|----------|--------|
| Fix R3 to write `series_prefix` field instead of prepending to `message` | T2 | High | C4 — UNRESOLVED conflict |
| Add test: R4/R5 Jaccard unchanged when R3 fires | T1 | High | C4 companion |
| Fix PatchManager to not parse reviewer replies as patches | T2 | Medium | D7 |
| Retire or narrow `_R1_PRECONDITION_HINTS` (0/15 precision) | T2 | Medium | D6, Task #69 |
| Wire `repo_manager` into intelligent review endpoint | T2 | Medium | D5 |
| Add Evidence Graph stub for LLM path (partial C2 resolution) | T3 | Low | C2 — aspirational |

These are in addition to the existing task backlog (#64, #66, #68, #69, #70).

---

## 5. Architecture Compliance Summary

| Constitutional Principle | Compliance | Note |
|--------------------------|------------|------|
| P1: No comment without Evidence Graph | Partial | LLM path uses reasoning field as substitute |
| P2: No fabricated evidence | Compliant | Evidence Engine verifies all rule-based evidence |
| P3: Confidence < 0.4 suppressed | Compliant | `_merge_comments()` enforces this |
| P4: Confidence computed, not guessed | Partial | LLM path deviates from Factor Model |
| P5: Deterministic execution | Compliant | Sec-40 test suite covers this |
| P6: Sec-40 (no random/time outside learning) | Compliant | `tests/test_stochastic_confinement.py` |
| P7: No verify=False outside LLMClient | Compliant | Code review gate |
| P8: No impersonation | Compliant | Statistical profiles only |
| P9: Architecture core frozen | Compliant | K→R→D→E→V separation maintained; new modules added through interface contracts |
| P10: Blockers/warnings never suppressed | Compliant | `_is_safety_floored()` in reducer |
