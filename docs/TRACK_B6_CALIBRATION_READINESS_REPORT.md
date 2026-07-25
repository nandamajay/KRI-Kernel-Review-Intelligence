# Track-B.6 Calibration + Readiness — Final Execution Report

**Date:** 2026-07-25
**Authority:** Track-B.6 Autonomous Calibration + Readiness Execution Authorization
**Verdict:** `CFM_NEEDS_MORE_CALIBRATION`
**Governance:** PASS (687/687 tests, no STOP conditions triggered)

---

## Executive Summary

Track-B.6 completed a full autonomous calibration and readiness pass across five
validation dimensions: dataset inspection, apply validation, CLI calibration, API/browser
validation, and governance audit.  The shadow infrastructure is sound, governance-clean,
and functioning correctly in shadow-only mode.  CFM_SHADOW_IMPROVED is confirmed
(REVIEW_HISTORY factor now varies by claim category: 0.000–1.000).  However, three
independent gaps prevent production-gate advancement: Pearson correlation is not yet
computable (only 3 distinct CFM score values across 7 claim types; need ≥5), effective
dataset diversity is critically low (4 unique series_ids across 24 mbox files; 71% of
knowledge-store entries are ASoC/DAI), and the API + browser validations returned
PARTIAL.  The verdict is `CFM_NEEDS_MORE_CALIBRATION` — calibration dataset expansion
is required before the production gate question can be revisited.

---

## Commits

| Commit | Description |
|--------|-------------|
| `b945827` | Track-B.5 B5-1: store.by_claim() + summarise_by_series_ids() + lore_evidence_for_claim() |
| `6506540` | Track-B.5 B5-2: _enrich_with_lore_history() + review_history_summary fix |
| `83dde2c` | Track-B.5 B5-3: 10 activation tests + tuple-return compatibility fixes |
| `b7924f7` | Track-B.5 B5-4: TRACK_B5_KNOWLEDGE_ACTIVATION_REPORT.md |
| `341ce18` | Track-B.6 B6-1: claim-triple calibration + review_history_distribution + 7 tests |
| (this commit) | Track-B.6 B6-2: TRACK_B6_CALIBRATION_READINESS_REPORT.md |

---

## Calibration Fix (B6-1)

### Root Cause (CFM_RC-1)

`CFMCalibrator.calibrate()` received only `(id, conf)` pairs — no claim category.
It therefore used `all_entries` for every comment, producing a constant REVIEW_HISTORY
factor regardless of comment content.  This made Pearson non-computable even after
Track-B.5 activated the live enrichment path.

### Resolution

`CFMCalibrator.calibrate()` now accepts `(id, conf, claim)` triples:

```python
def calibrate(
    self,
    llm_comments: list[tuple[str, float]] | list[tuple[str, float, str]],
) -> CFMCalibrationReport:
    has_claims = len(llm_comments) > 0 and len(llm_comments[0]) == 3
    for row in llm_comments:
        claim_category = row[2] if has_claims else None
        if claim_category and claim_category != "review_discussion":
            entries_for_comment = self._store.by_claim(claim_category) or []
        else:
            entries_for_comment = all_entries  # backward-compat
        ...
```

`reviewer.py` now passes `c.category` as the third element:

```python
llm_comments = [
    (hash, c.confidence, c.category)
    for pr in patch_reviews for c in pr.inline_comments
]
```

`CFMCalibrationReport` gains `review_history_distribution: dict[str, float]`
(per-claim REVIEW_HISTORY factor, populated when triples are supplied).

---

## Dataset Inspection

| Metric | Value |
|--------|-------|
| Total dataset entries | 24 |
| All mboxes present | Yes |
| ASoC entries | 10 |
| Non-ASoC entries | 14 |
| Unique series_ids | 4 (across 24 entries — **duplicate-heavy**) |
| Knowledge-store entries (runtime) | 28 (calibration store), 420 (runtime) |

### Knowledge-Store Claim Distribution (calibration store, 28 entries)

| Claim | Entries | REVIEW_HISTORY factor |
|-------|---------|-----------------------|
| `dai` | 13 | 1.000 |
| `review_discussion` | 7 | 0.000 (BLOCK-4) |
| `maintainer_ack` | 3 | 1.000 |
| `locking` | 1 | 0.350 |
| `style` | 1 | 0.000 |
| `dpcm` | 1 | 0.000 |
| `dapm` | 1 | 0.350 |
| `dt_binding` | 1 | 0.350 |

---

## Apply Validation (24 entries — 100% resolved)

The 7 entries that rate-limited during the initial workflow were validated directly
via `git apply --check` against kernel HEAD `30d4efb2f5a515a60fe6b0ca85362cbebea21e2f`.

| Entry | Status | Detail |
|-------|--------|--------|
| L13_rubikpi_asoc | APPLY_FAILED | qcs6490-thundercomm-rubikpi3.dts: No such file; qcom/common.h context mismatch |
| L14_renesas_dpcm | APPLY_FAILED | corrupt patch at line 4923 (DPCM multi-component thread; 46 emails) |
| L15_nuvoton_nau8360 | APPLY_FAILED | Kconfig:173 + Makefile:197 context mismatch |
| L16_nuvoton_nau83g60 | APPLY_FAILED | Kconfig:173 + Makefile:197 context mismatch |
| L17_amd_asoc_cover | PATCH_FORMAT_UNKNOWN | Cover letter only (0/3); no diff --git sections |
| L18_amd_asoc_p1 | APPLY_CLEAN | — |
| L19_amd_asoc_p2 | APPLY_CLEAN | — |
| L20_hid_gyro | APPLY_FAILED | hid-sensor-gyro-3d.c:313 context mismatch |
| L21_staging_cover | PATCH_FORMAT_UNKNOWN | Cover letter only (0/4); no diff --git lines |
| L22_sched_ext | PATCH_FORMAT_UNKNOWN | Cover letter only; 5 patches referenced but absent |
| L23_crypto_pcrypt | APPLY_FAILED | loongarch defconfig absent; crypto/Kconfig:201; pcrypt.c context mismatch |
| L24_mm_thp | PATCH_FORMAT_UNKNOWN | Cover letter only (v6 00/14); no patch emails |
| S1 | APPLY_CLEAN | — |
| S2 | APPLY_FAILED | apple.yaml:123 — M2 Ultra entries not present at HEAD |
| S3 | APPLY_CLEAN | — |
| S4 | APPLY_CLEAN | — |
| S5 | APPLY_CLEAN | — |
| S6 | APPLY_FAILED | hda.c:574 — hda_dsp_dump_ext_rom_status no longer at expected location |
| S7 | APPLY_CLEAN | — |
| S8 | APPLY_FAILED | pgtable.h:2313, memory.c:164, mm_init.c:2699 context mismatches |
| S9 | APPLY_CLEAN | — |
| S10 | APPLY_FAILED | ax88179_178a.c:32 — AX_PAUSE_WATERLVL context mismatch |
| S11 | APPLY_FAILED | loongarch defconfig absent; crypto/Kconfig:201 context mismatch |
| S12 | APPLY_CLEAN | — |

### Apply Breakdown

| Status | Count | % |
|--------|-------|---|
| APPLY_CLEAN | 9 | 37.5% |
| APPLY_FAILED | 11 | 45.8% |
| PATCH_FORMAT_UNKNOWN | 4 | 16.7% |
| APPLY_CONFLICT | 0 | 0% |

**Note:** APPLY_FAILED does not indicate a code quality issue — it reflects dataset
entries that target newer kernel APIs not present at 6.18-rc1 HEAD, or cover-letter
entries captured without their accompanying patch emails.  All APPLY_FAILED entries
have complete provenance and valid lore evidence.

---

## CFM Shadow Calibration

### Calibration Metrics

| Metric | Value |
|--------|-------|
| Knowledge-store entries | 28 (calibration), 420 (runtime) |
| Claim types with entries | 7 |
| Distinct CFM score values | 3 (0, 0.35, 1.0) |
| cfm_mean | 0.436 |
| cfm_min / cfm_max | 0.0 / 1.0 |
| Pearson computed | **No** — only 3 distinct values; need ≥5 |
| review_history_coverage | 100% |
| provenance_coverage | 100% |
| BLOCK-4 guard | Active |

### Verdict: CFM_SHADOW_IMPROVED

REVIEW_HISTORY factor is now content-dependent (varies by claim category):

- Before Track-B.5: constant 0.000 for every comment.
- After Track-B.5 + B.6: 0.000–1.000 depending on claim match.

Per `engine.py:193-200`: `min(1.0, verified_review_discussion_count * 0.35)`.

### Why Pearson Is Not Yet Computable

The Pearson correlation requires continuous variation in CFM scores across comments.
With only 3 distinct CFM values (0, 0.35, 1.0) across 7 claim types, the distribution
collapses to three plateaus.  At least 5 distinct values are needed to establish a
meaningful linear relationship between CFM scores and LLM confidence values.

**Path to Pearson:** Expand the claim vocabulary to include intermediate bands.
The current 28-entry store is heavily ASoC/DAI-biased; adding net, mm, crypto,
sched, and driver-model entries with distinct claim categories would produce the
needed score granularity automatically.

---

## API + Browser Validation

### API Checks (all PASS)

| Check | Status |
|-------|--------|
| GET / returns 200 | PASS |
| GET /api/knowledge/lab/stats has review_entry_count | PASS |
| GET /api/knowledge/lab/reviews returns 420 entries | PASS |
| review_history_store loaded on startup | PASS |
| POST /api/review/intelligent endpoint exists | PASS |
| review_history_summary JS block in source | PASS |

**Overall API: PARTIAL** — `lore_matched_series_wired=false` means the B.6 dataset
series_ids are not yet matched against the runtime 420-entry store during live review
calls (see Defect D-1 below).

### Browser Checks

| Check | Status |
|-------|--------|
| Historical Evidence section present | PASS |
| Historical Evidence conditionally rendered | PASS |
| source_url lore.kernel.org links displayed | PASS |
| CFM shadow score per-comment | PASS |
| CFM shown as shadow/observational not gate | PASS |
| CFM calibration section displayed | PASS |
| apply_status shown per-patch | PASS |
| governance_warnings displayed | PASS |
| knowledge_state_id displayed | PASS |
| transformation_history UI rendering | **FAIL** |

**Overall Browser: PARTIAL** — No dedicated HTML block renders the provenance
transformation chain.  `source_url` is surfaced via evidence items, but the full
`transformation_history` array has no dedicated UI section.

---

## Governance Audit

| Check | Status |
|-------|--------|
| CFM gate inactive (shadow-only) | PASS |
| Safety floor intact (BLOCKER/WARNING ≥ 0.70 preserved) | PASS |
| Provenance mandatory (Tier-0 guard active) | PASS |
| BLOCK-4 guard active (`by_claim('review_discussion')` → `[]`) | PASS |
| verified flag correct (ephemeral=True, EKG writes=False) | PASS |
| mode-off behavior unchanged | PASS |
| Sec-40 (no random/uuid/datetime.now outside kri/learning/) | PASS |
| Full suite | 687/687 PASS |

**All 12 STOP conditions: NOT triggered.**

---

## Defects Identified

| ID | Severity | Defect | Fix Path |
|----|----------|--------|----------|
| D-1 | HIGH | `lore_matched_series_wired=false` — B.6 dataset series not yet matched in live review | Wire `by_series_id()` → `summarise_by_series_ids()` path for B.6 entries |
| D-2 | HIGH | High false positive risk — 71.4% of evidence is `review_discussion`; no reliability weighting relative to `maintainer_ack` | Add `evidence_reliability_weight` field; discount `review_discussion` in CFM scoring |
| D-3 | MEDIUM | No dedicated `transformation_history` UI section | Add collapsible Provenance Chain section in `app.py` near lines 948–1000 |
| D-4 | MEDIUM | Cover-letter-only mboxes silently receive `PATCH_FORMAT_UNKNOWN` without pre-validation | Pre-ingestion validator: count `diff --git` lines; emit structured error and skip |
| D-5 | MEDIUM | Duplicate series_ids — 4 unique series_ids across 24 mbox entries | Enforce series_id uniqueness in dataset builder; log and skip duplicates |
| D-6 | MEDIUM | Pearson not computable — only 3 distinct CFM score values | Expand claim vocabulary; target ≥5 distinct claim types across multiple subsystems |
| D-7 | LOW | Strong DAI-domain bias — 13/28 entries are `dai` | Expand ingestion to net, mm, crypto, sched mailing lists |
| D-8 | LOW | S6 (2022 patch) in active dataset | Date filter: exclude mboxes older than 18 months unless tagged `historical_reference` |

---

## Tests Added (B6-1)

| Test | Coverage |
|------|----------|
| `test_B6_calibrate_with_claim_triples_varies_cfm` | Claim-triple path uses per-claim evidence |
| `test_B6_calibrate_backward_compat_no_claims` | (id, conf) 2-tuple backward compat |
| `test_B6_calibrate_review_discussion_uses_all_entries` | `review_discussion` → all_entries fallback |
| `test_B6_review_history_distribution_in_report` | `review_history_distribution` field populated |
| `test_B6_pearson_constant_returns_none` | `_pearson()` returns None for constant series |
| `test_B6_pearson_normal_variance` | `_pearson()` correct for perfect correlation |
| `test_B6_calibrate_empty_returns_cfm_shadow_stays` | Empty input → `CFM_SHADOW_STAYS`, `samples=0` |

**Full suite: 687/687 passing.**

---

## Safety Invariants

All 12 STOP conditions: **NOT triggered**.

| # | Condition | Status |
|---|-----------|--------|
| 1 | CFM production gate activates | NOT triggered |
| 2 | CFM starts suppressing/publishing comments | NOT triggered |
| 3 | Safety floor changes | NOT triggered |
| 4 | mode-off behavior changes | NOT triggered |
| 5 | Evidence stored without provenance | NOT triggered |
| 6 | Source-level claim emitted for apply-failed patch | NOT triggered |
| 7 | Pattern promotion starts | NOT triggered |
| 8 | Learning loop starts | NOT triggered |
| 9 | Browser validation fails beyond 3 attempts | NOT triggered |
| 10 | Full suite regresses beyond 3 attempts | NOT triggered |
| 11 | Kernel worktree corrupted | NOT triggered |
| 12 | Correlation fabricated / reported without valid sample/variance | NOT triggered |

---

## Readiness Verdict

**`CFM_NEEDS_MORE_CALIBRATION`**

The Track-B.6 validation cycle completes with `CFM_SHADOW_IMPROVED` and a full
governance PASS (687/687 tests, no STOP conditions triggered), but falls short of
production readiness on three independent grounds:

1. **Pearson not computable**: only 3 distinct CFM score values (0, 0.35, 1.0) exist
   across 7 claim types.  The calibrator correctly records `pearson_computed=false`
   and emits a `pearson_limitation` message noting the need for ≥5 distinct values.

2. **Dataset diversity critically low**: 24 mbox files collapse to only 4 unique
   series_ids; 13 of 28 knowledge-store entries are narrow DAI-domain ASoC claims;
   `unsupported_rate_estimate = 71.4%`; `false_positive_risk = HIGH`.

3. **API + browser validations PARTIAL**: `lore_matched_series_wired=false` means B.6
   dataset series are not yet connected to the live evidence pipeline;
   `transformation_history` has no dedicated UI rendering.

None of these are governance or safety failures.  The CFM gate remains inactive, the
safety floor is intact, and provenance hygiene is excellent (100% coverage, Tier-0
guard active).  The system correctly operates in shadow mode and can proceed to a
calibration expansion pass without Track-D work.

**To advance to `CFM_PRODUCTION_READY`**, the following must be resolved:
- Expand knowledge store to ≥5 distinct claim types with sufficient entries to produce
  ≥5 distinct CFM score bands.
- Resolve D-1 (lore_matched_series wiring) and D-3 (transformation_history UI) for
  full API + browser PASS.
- Run calibration replay over live review submissions to accumulate Pearson sample data.

---

## Architecture Boundaries Respected

- No CFM gate activation (shadow-only, WP4-K LOCK).
- No new packages, models, or schema additions beyond `review_history_distribution` field.
- No EKG writes during live review (ephemeral Evidence nodes only).
- No Pattern promotion, no learning loop.
- `kri/knowledge_lab/` untouched (domain-agnostic; Sec-9 compliant).
- Sec-40: all new IDs via `hashlib.sha256()[:12]` (no `uuid`, `random`, `time`).
- Safety floor: BLOCKER/WARNING ≥ 0.70 unchanged.

---

*Report signed off by Track-B.6 autonomous execution, 2026-07-25.*
*Governance: 12-agent multi-phase workflow + direct bash apply validation, Arbiter verdict CFM_NEEDS_MORE_CALIBRATION, all 12 STOP conditions clear.*
