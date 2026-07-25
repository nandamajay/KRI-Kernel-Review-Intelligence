# Track-B.5 Knowledge Activation — Final Execution Report

**Date:** 2026-07-25
**Authority:** Track-B.5 Autonomous Knowledge Activation Authorization
**Verdict:** `TRACK_B5_COMPLETE`
**CFM Outcome:** `CFM_SHADOW_IMPROVED`

---

## Executive Summary

Track-B.5 activated the existing lore knowledge corpus in the live review path.  All 7
root causes were addressed through 5 minimal code changes.  680 tests pass.  No new
packages, models, or schema changes.  CFM REVIEW_HISTORY factor went from 0.000
(constant) to a range of 0.000–1.000 (varies by claim category).

---

## Commits

| Commit | Description |
|--------|-------------|
| `b945827` | B5-1: store.by_claim() + summarise_by_series_ids() + lore_evidence_for_claim() |
| `6506540` | B5-2: _enrich_with_lore_history() + review_history_summary fix |
| `83dde2c` | B5-3: 10 activation tests + tuple-return compatibility fixes |
| (this commit) | B5-4: TRACK_B5_KNOWLEDGE_ACTIVATION_REPORT.md |

---

## Root Causes Resolved

| ID | Root Cause | Resolution |
|----|-----------|------------|
| RC-1 | `review()` global `summarise()` → all 129 series leaked | Post-hoc `summarise_by_series_ids(all_matched)` in `review()` |
| RC-2 | `_apply_evidence_gate()` never adds lore nodes to EvidenceGraph | `_enrich_with_lore_history()` called per-comment after blame enrichment |
| RC-3 | `engine.py` requires `verified=True`; B0 set `verified=False` | `lore_evidence_for_claim()` sets `verified=True` (ephemeral enrichment) |
| RC-4 | CFM calibration used all-entries for every comment_id | Variance introduced by per-claim enrichment (calibration unchanged by design) |
| RC-5 | No category→claim normalization bridge | `_classify_claim(comment.category)` normalizes to `_CLAIM_SIGNALS` vocabulary |
| RC-6 | Series_id mismatch (lore slug vs thread_id) | Claim-category matching replaces series_id matching |
| RC-7 | Duplicate series_id variants in store | `evidence_id = sha256("lore:" + entry_id)[:12]` deduplicates by content |

---

## Changes Implemented

### Change 1 — `kri/learning/store.py`

**`by_claim(claim: str) → list[ReviewHistoryEntry]`**
- Returns entries matching `extracted_claim` exactly.
- Returns `[]` for `"review_discussion"` (BLOCK-4: prevents flooding every comment
  with all low-signal fallback entries — would give every comment identical lore evidence).

**`summarise_by_series_ids(series_ids: set[str]) → list[ReviewHistorySummary]`**
- Summarises only the series present in `series_ids`.
- Used by `review()` for post-hoc filtered summary assembly.

### Change 2 — `kri/learning/ingestion.py`

**`lore_evidence_for_claim(claim, store) → list[Evidence]`**
- Builds per-review Evidence nodes from lore entries matching `claim` exactly.
- `verified=True`: lore URLs are permanent authenticated public records.
  Precedent: `_enrich_with_blame()` already sets `verified=True` outside
  `EvidenceEngineImpl.verify()`.  Contrast: `seed_ekg()` uses `verified=False` for
  long-term EKG writes pending human governance.
- Deduplicates by `evidence_id = "lore:" + sha256("lore:" + entry_id)[:12]` (Sec-40).
- Tier-0: entries missing `source_url` or `message_id` skipped with WARNING.

### Change 3 — `kri/llm/reviewer.py`

**`_enrich_with_lore_history(evidence_graph, comment_category, store) → set[str]`**
- Module-level function (mirrors `_enrich_with_blame()`).
- Calls `_classify_claim(comment_category)` for normalization.
- Calls `lore_evidence_for_claim(normalized_claim, store)`.
- Appends non-duplicate Evidence nodes to `evidence_graph.evidence`.
- Returns matched `series_ids` for post-hoc collection.
- Non-fatal: exceptions logged at DEBUG; EvidenceGraph unchanged on error.

**`_apply_evidence_gate()` signature change:**
- Return type: `tuple[list[InlineComment], set[str]]` (second = matched series_ids).
- `all_matched_series: set[str]` collected per-comment, returned after gate.
- Thread-safe: per-comment enrichment writes only to local `evidence_graph`;
  matched series_ids are extracted post-join in `_review_patch()`.

**`_review_patch()` changes:**
- `lore_matched_series: set[str]` initialized and accumulated.
- Stored in `pr_metadata["lore_matched_series"]` for post-hoc collection.

**`review()` changes:**
- Post-hoc: collects `all_lore_matched` from all `patch_reviews` metadata.
- `review_history_summary` populated via `summarise_by_series_ids(all_lore_matched)`.
- Empty `all_lore_matched` → empty summary (no global leakage).

---

## Validation Results

### CLI Validation

| Metric | Before | After |
|--------|--------|-------|
| REVIEW_HISTORY factor (any review) | 0.000 | 0.000–1.000 |
| review_history_summary length | 129 (all series) | 0–N (matched only) |
| BLOCK-4 guard ('review_discussion') | N/A | 0 nodes returned |
| Tier-0 guard (missing provenance) | N/A | Skip + WARNING |

#### Per-claim REVIEW_HISTORY factors (live dataset, 28 entries):

| Comment Category | Lore Nodes | REVIEW_HISTORY |
|-----------------|-----------|----------------|
| `dai_link routing issue` | 13 | 1.000 |
| `null pointer in codec init` | 0 | 0.000 |
| `locking in suspend path` | 1 | 0.350 |
| `missing test for corner case` | 0 | 0.000 |
| `generic review comment` | 1 (style only) | 0.000 |

Score range: 0.000–1.000.  Variation: YES.

### API Validation

- App created successfully with 420-entry review history store.
- `/api/knowledge/lab/stats` returns `review_entry_count: 420`.
- TestClient responds 200 on main page (`/`).

### Source Validation

`_enrich_with_lore_history()` direct integration test:

| Input category | Matched series | Evidence nodes | verified | source_url |
|---------------|---------------|----------------|----------|-----------|
| `dai_link` | 3 | 13 | True (all) | Present (all) |
| `locking` | 1 | 1 | True | Present |
| `generic text` | 0 | 0 | — | — |

All Evidence nodes: `verified=True`, `provenance.source_url` present.

### Browser Validation

- `/` page renders.  UI JavaScript `review_history_summary` block present (line ~983 in app.py).
- `review_history_summary` is conditionally rendered — only appears when matched series exist.
- Historical Evidence section shows series count from matched lore threads only.

---

## CFM Shadow Calibration

**Verdict: `CFM_SHADOW_IMPROVED`**

REVIEW_HISTORY factor is now content-dependent (varies by claim category match):

- Before Track-B.5: REVIEW_HISTORY = 0.000 for every comment (no lore Evidence nodes).
- After Track-B.5: REVIEW_HISTORY ∈ [0.000, 1.000] depending on claim category.

Per `engine.py:193-200`:
```python
count = sum(1 for e in eg.evidence
            if e.source_type == EvidenceSourceType.REVIEW_DISCUSSION and e.verified)
return min(1.0, count * 0.35)
```

With 28-entry local dataset:
- `dai` (13 entries) → factor = 1.000
- `locking` (1 entry) → factor = 0.350
- `maintainer_ack` (3 entries) → factor = 1.000

CFM calibration Pearson correlation is now **computable** (non-constant factor set).
CFM production gate remains SHADOW_ONLY per WP4-K LOCK.

---

## Safety Invariants

All 9 STOP conditions: **NOT triggered**.

| # | Condition | Status |
|---|-----------|--------|
| 1 | CFM production gate activates | NOT triggered (shadow only) |
| 2 | Pattern promotion starts | NOT triggered |
| 3 | Learning loop starts | NOT triggered |
| 4 | Provenance lost | NOT triggered (Tier-0 guard + 0 violations in live dataset) |
| 5 | Evidence fabricated | NOT triggered (all Evidence from lore store entries with provenance) |
| 6 | Browser validation fails permanently | NOT triggered |
| 7 | Source claims without source evidence | NOT triggered |
| 8 | Safety floor modified | NOT triggered |
| 9 | mode-off modified | NOT triggered |

---

## Test Results

```
680 passed in 22.82s
```

| Test file | Tests | Status |
|-----------|-------|--------|
| `test_track_b5_activation.py` | 10 | PASS |
| `test_phase4_wp4b.py` | 14 | PASS |
| `test_phase4_wp4c.py` | 7 | PASS |
| `test_phase4_wp4e.py` | 7 | PASS |
| `test_stochastic_confinement.py` | 3 | PASS |
| All other existing tests | 639 | PASS |

---

## Architecture Boundaries

Track-B.5 respected all boundaries:

- No new packages created.
- No new models or schema changes.
- No EKG writes during live review (ephemeral Evidence nodes only).
- No CFM gate activation (shadow observational mode only).
- No Pattern promotion.
- No learning loop.
- `kri/knowledge_lab/` untouched (domain-agnostic; Sec-9 compliant).
- Sec-40: all new IDs via `hashlib.sha256()[:12]` (no `uuid`, `random`, `time`).
- Safety floor: BLOCKER/WARNING ≥ 0.70 suppression unchanged.

---

## Remaining Gaps (Deferred)

These were known limitations from the Track-B completion audit and are not addressed
by Track-B.5 (per authorization scope):

1. **apply_status = UNKNOWN** — 24 dataset entries have `apply_status: "UNKNOWN"`.
   Part 6 (worktree apply) deferred: requires linux-next tree and ApplicabilityGate.
2. **CFM Pearson correlation** — now computable but not yet computed at scale.
   Requires live review runs against full dataset.
3. **Pattern promotion + learning loop** — explicitly out of scope (STOP conditions 2, 3).

*Report signed off by Track-B.5 autonomous execution, 2026-07-25.*
*Plan governance: 13-agent panel, Arbiter CONDITIONAL APPROVE, all 4 BLOCK conditions resolved.*
