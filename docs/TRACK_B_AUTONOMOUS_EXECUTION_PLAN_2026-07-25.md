# Track-B Autonomous Execution Plan
**Date:** 2026-07-25  
**Status:** B0 — Planning  
**Kernel:** linux-next v7.2-rc4 (`9eebf259d`, 20260723)  
**Git identity:** Ajay Kumar Nandam <ajay.nandam@oss.qualcomm.com>

---

## 1. Executive Summary

Track-B moves KRI from evidence-aware to **historical-knowledge-aware** review. The three authorized
work packages are:

| WP | Name | Description |
|----|------|-------------|
| WP4-I | DKP Version Ranges + Historical Readiness | Wire `VersionRange` into DKP seeding; assess historical knowledge readiness |
| WP4-J | Lore Review Ingestion | Fetch real lore review threads; extract `ReviewHistoryEntry` records with full provenance; seed EKG |
| WP4-K | CFM Calibration | Use WP4-J data to calibrate `REVIEW_HISTORY` and `HISTORICAL_AGREEMENT` confidence factors; shadow-mode only |

Track-B does NOT activate the CFM production gate automatically. Gate criteria must all be met and
approved by Governance Auditor + Arbiter before any mode change.

---

## 2. Codebase Baseline (as of Phase-4V2)

### Existing infrastructure available to Track-B

| Component | Module | Status |
|-----------|--------|--------|
| `KnowledgeGraph` | `kri/knowledge/graph.py` | Exists — temporal property graph |
| `KnowledgeManagerImpl` | `kri/knowledge_manager/manager.py` | Exists — `add_evidence_node`, `query_graph` |
| `Evidence` model | `kri/common/models.py:245` | Exists — `source_type`, `provenance`, `verified`, `strength` |
| `Provenance` model | `kri/common/models.py:159` | Exists — `source_url`, `commit_hash`, etc. |
| `ReviewComment` model | `kri/common/models.py:202` | Exists — `provenance`, `is_maintainer` |
| `HistoricalPatternExtractor` | `kri/learning/extraction.py:149` | Exists — `ingest_thread`, `extract_patterns` |
| `CandidatePattern` | `kri/learning/extraction.py:89` | Exists — `source_urls`, `support_level`, `validated` |
| `ConfidenceEngineImpl` | `kri/confidence_engine/engine.py` | Exists — 8-factor model |
| `_compute_review_history` | `engine.py:193` | Exists — `min(1.0, count * 0.35)` for `REVIEW_DISCUSSION` evidence |
| `_compute_historical_agreement` | `engine.py:142` | Exists — `accepted/(accepted+rejected+1)` |
| `LoreManagerImpl` | `kri/lore_manager/manager.py` | Exists — `extract_reviews`, `fetch`, `load_cached` |
| `EvidenceSourceType.REVIEW_DISCUSSION` | `kri/common/models.py` | Exists |
| `EvidenceSourceType.ACCEPTED_PATCH` | `kri/common/models.py` | Exists |
| CFM shadow mode | `kri/llm/reviewer.py:243,549` | Exists — computes but never gates |

### What Track-B must add

| Item | WP | Module |
|------|----|--------|
| `ReviewHistoryEntry` Pydantic model | WP4-J | `kri/learning/models.py` (new) |
| `ReviewHistoryStore` (persist/query) | WP4-J | `kri/learning/store.py` (new) |
| `LoreIngestionEngine` | WP4-J | `kri/learning/ingestion.py` (new) |
| Lore review dataset (≥20 series, JSONL) | WP4-J | `.kri/lore_review_dataset/` |
| `CFMCalibrationReport` model | WP4-K | `kri/learning/calibration.py` (new) |
| `CFMCalibrator` | WP4-K | `kri/learning/calibration.py` |
| API field `review_history_entries` in `IntelligentReport` | WP4-J | `kri/llm/models.py` |
| Browser rendering for `review_history_entries` | WP4-J | `kri/web/app.py` |
| API field `cfm_calibration` in `IntelligentReport` | WP4-K | `kri/llm/models.py` |
| Browser rendering for CFM shadow score | WP4-K | `kri/web/app.py` |
| DKP `VersionRange` completeness check | WP4-I | `kri/packages/asoc/knowledge.py` assessment |
| Tests: `tests/test_track_b_wp4i.py` | WP4-I | — |
| Tests: `tests/test_track_b_wp4j.py` | WP4-J | — |
| Tests: `tests/test_track_b_wp4k.py` | WP4-K | — |

---

## 3. WP Breakdown

### WP4-I — DKP Version Ranges / Historical Knowledge Readiness

**Goal:** Audit how well the existing ASoC DKP seeds `VersionRange` data into the EKG. Measure whether
the `VERSION_CONSISTENCY` and `HISTORICAL_AGREEMENT` confidence factors can receive real data.

**Sub-tasks:**
1. Read all `add_node` / `add_edge` calls in `kri/packages/asoc/knowledge.py`.
2. Measure fraction of evidence nodes with non-None `version_range`.
3. Measure fraction of accepted/rejected example lists populated.
4. Produce a `DKPReadinessReport` (in-memory dict, no Pydantic model needed).
5. Add test `test_WP4I_1_dkp_version_range_coverage` — asserts ≥N% of DKP nodes have `version_range`.
6. Add test `test_WP4I_2_ekg_historical_agreement_factor_can_receive_data` — seeds a minimal EKG, runs `_compute_historical_agreement`, asserts non-zero result.
7. **No code changes to DKP** unless a concrete gap is found; gaps logged in WP4-I test as xfail.

**Invariants:**
- mode=off behavior unchanged.
- No new Sec-40 nondeterminism.

---

### WP4-J — Lore Review Ingestion

**Goal:** Fetch ≥20 real lore.kernel.org review threads; parse review discussions; create
`ReviewHistoryEntry` records with full provenance; seed EKG with `REVIEW_DISCUSSION` evidence nodes;
surface in API and browser.

#### B1 — Dataset construction
- Use `wget` to fetch mbox files (curl blocked by Anubis).
- Minimum: 20 series, 5 ASoC, 5 non-ASoC, 5 multi-patch, 5 with review replies, 3 with maintainer feedback.
- Each series record (JSONL at `.kri/lore_review_dataset/index.jsonl`):
  ```json
  {
    "lore_url": "https://lore.kernel.org/...",
    "message_id": "<...>",
    "subject": "...",
    "subsystem": "...",
    "patch_count": 1,
    "reply_count": 3,
    "fetched_at": "2026-07-25",
    "mbox_path": ".kri/lore_review_dataset/series/...",
    "apply_status": "CLEAN|FAILED|UNKNOWN"
  }
  ```

#### B2 — Lore parser
- `LoreIngestionEngine.ingest(mbox_path, series_id, lore_url) → list[ReviewHistoryEntry]`
- Uses existing `LoreManagerImpl.load_cached` + `extract_reviews`.
- Excludes quoted text (lines beginning with `>`).
- Extracts: patch content, reply messages, reviewer comments, ack/reviewed/tested tags.
- Every `ReviewHistoryEntry` has mandatory provenance.

#### B3 — ReviewHistoryEntry model
```python
class ReviewHistoryEntry(BaseModel):
    entry_id: str                     # deterministic hash of (series_id, message_id, excerpt_hash)
    series_id: str
    patch_id: str | None
    message_id: str                   # mandatory
    source_url: str                   # mandatory
    reviewer_text: str                # original comment text (≤500 chars)
    extracted_claim: str              # normalized concern category
    evidence_type: Literal[
        "review_discussion",
        "accepted_patch",
        "rejected_patch",
        "maintainer_ack",
        "maintainer_nack",
    ]
    confidence_basis: str             # how this was classified
    created_by: str = "WP4-J/LoreIngestionEngine"
    validation_status: Literal["pending", "validated", "rejected"] = "pending"
    provenance: Provenance
```

#### B4 — EKG seeding
- `LoreIngestionEngine.seed_ekg(entries, knowledge_manager)`:
  - For each `ReviewHistoryEntry` create an `Evidence` node:
    - `source_type = EvidenceSourceType.REVIEW_DISCUSSION`
    - `provenance.source_url = entry.source_url`
    - `provenance.transformation_history = ["WP4-J:lore_ingestion"]`
    - `verified = True` (source is public lore text, no inference)
    - `strength` = 0.3 base, +0.2 if maintainer ack/nack, +0.1 per additional supporting comment
  - Add to `knowledge_manager.add_evidence_node(evidence)`.

#### API surfacing
- Add `review_history_summary: list[ReviewHistorySummary]` to `IntelligentReport`.
- `ReviewHistorySummary`: `series_id`, `entry_count`, `source_urls: list[str]`, `claim_categories: dict[str, int]`.

#### Browser surfacing
- Render in `renderIntelligent()` JS: collapsible `<details>` per series showing entry count,
  source URLs as links, claim distribution table.
- Label clearly: "Historical Evidence (from lore.kernel.org review threads)".

---

### WP4-K — CFM Shadow Calibration

**Goal:** Use WP4-J `ReviewHistoryEntry` data to populate `REVIEW_DISCUSSION` evidence into EKG and
measure effect on CFM shadow scores. Compare CFM vs LLM confidence. Do not activate production gate.

#### Sub-tasks
1. Implement `CFMCalibrator.calibrate(entries, knowledge_manager, reviews) → CFMCalibrationReport`.
2. For each review in the validation sample set:
   - Seed EKG with matching `ReviewHistoryEntry` records (if any).
   - Run `ConfidenceEngineImpl.score(decision, evidence_graph)`.
   - Compare `cfm_shadow_score` vs `llm_confidence` (from existing `InlineComment.confidence`).
3. Compute: correlation coefficient, mean absolute error, false-positive estimate.
4. Produce `CFMCalibrationReport`:
   ```python
   class CFMCalibrationReport(BaseModel):
       series_count: int
       entry_count: int
       samples_calibrated: int
       cfm_vs_llm_correlation: float | None
       mean_absolute_error: float | None
       false_positive_estimate: float | None
       production_gate_criteria_met: bool = False
       recommendation: Literal["CFM_SHADOW_STAYS", "CFM_PRODUCTION_READY"]
   ```
5. `production_gate_criteria_met = True` only if ALL gate criteria pass (see Section 8).

---

## 4. Agent Responsibilities

| Agent | Phase | Owns |
|-------|-------|------|
| Track-B Architect | B0 | Phase design, WP sequence, scope discipline |
| Lore Acquisition | B1 | Fetch ≥20 series from lore.kernel.org via wget |
| Lore Parser | B2 | Parse review threads, extract ReviewHistoryEntry |
| Knowledge Schema | B3 | ReviewHistoryEntry / CFMCalibrationReport schema |
| Evidence/Provenance | B3/B4 | Verify every item has source_url + message_id |
| CFM Calibration | B5 | Compute shadow CFM, compare vs LLM, no production gate |
| Kernel Source | B6 | KRI_KERNEL_PATH=linux-next, worktree apply |
| CLI Validation | B6 | TestClient path validation |
| Web/API Validation | B6 | API response schema validation |
| Browser Validation | B6 | Playwright BW tests for Track-B fields |
| Review Quality | B7 | Comment classification, FP rate measurement |
| Governance/Auditor | B0,B8 | STOP conditions, evidence fabrication checks |
| Adversarial Reviewer | B8 | Overengineering, stale evidence, bad promotion |
| Test Auditor | B3/B8 | Vacuous test detection |
| Arbiter | B8 | Final commit authorization |

---

## 5. Governance Tiers

| Tier | Condition | Action |
|------|-----------|--------|
| TIER-0 | Evidence without provenance | STOP immediately |
| TIER-0 | Pattern node without source_url/message_id | STOP immediately |
| TIER-0 | CFM production gate without meeting all criteria | STOP immediately |
| TIER-1 | Safety floor weakened | STOP immediately |
| TIER-1 | mode=off behavior changed | STOP immediately |
| TIER-1 | Sec-40 nondeterminism introduced | STOP immediately |
| TIER-2 | Browser validation fails after 3 attempts | STOP |
| TIER-2 | Source claim on APPLY_FAILED patch | STOP |
| TIER-3 | CFM shadow score diverges from LLM by >0.5 on mean | Log + continue |
| TIER-3 | Review quality FP rate >40% | Log + note in report |

---

## 6. Data Source Plan

### Lore dataset target

| Category | Count | Strategy |
|----------|-------|----------|
| ASoC/audio patches | ≥5 | Use S1/S2 from Phase-4V2 + fetch 3 more ASoC series |
| Non-ASoC patches | ≥5 | Fetch from fs/, mm/, drivers/, net/ |
| Multi-patch series | ≥5 | S10 from Phase-4V2 + fetch 4 more |
| With review replies | ≥5 | Target threads with >2 messages |
| Maintainer-style feedback | ≥3 | Threads with ack/nack/reviewed-by tags |
| Legacy (known APPLY_FAILED) | ≥1 | S6 from Phase-4V2 |

### Fetch strategy
- `wget -q -O <path>.mbox 'https://lore.kernel.org/.../$msg_id/raw'`
- `LoreManagerImpl.load_cached(path)` for offline parsing
- All fetches recorded in `.kri/lore_review_dataset/index.jsonl`

---

## 7. Schema Summary

### `ReviewHistoryEntry` (new — `kri/learning/models.py`)
Fields: `entry_id`, `series_id`, `patch_id`, `message_id`, `source_url`, `reviewer_text`,
`extracted_claim`, `evidence_type`, `confidence_basis`, `created_by`, `validation_status`, `provenance`.

### `ReviewHistorySummary` (new — `kri/llm/models.py`)
Fields: `series_id`, `entry_count`, `source_urls`, `claim_categories`.

### `CFMCalibrationReport` (new — `kri/learning/calibration.py`)
Fields: `series_count`, `entry_count`, `samples_calibrated`, `cfm_vs_llm_correlation`,
`mean_absolute_error`, `false_positive_estimate`, `production_gate_criteria_met`, `recommendation`.

---

## 8. CFM Production Gate Criteria

All **seven** must pass before `production_gate_criteria_met = True`:

1. ≥20 real lore series ingested with full provenance.
2. CFM shadow scores produced for ≥10 inline comments.
3. CFM-vs-LLM correlation coefficient computed (Pearson, ≥0.0 passes; negative fails).
4. False-positive estimate: ≤40% of high-severity comments where CFM confidence > 0.7 but LLM says low.
5. No safety-floor violation in any sample.
6. Browser/API/CLI surfacing validated for `cfm_calibration` field.
7. Governance Auditor approves + Arbiter approves.

If any criterion fails: `recommendation = "CFM_SHADOW_STAYS"`.

---

## 9. Validation Matrix

For every WP:

| Validation | WP4-I | WP4-J | WP4-K |
|------------|-------|-------|-------|
| WP-specific pytest tests | Y | Y | Y |
| Full pytest suite (no regressions) | Y | Y | Y |
| Sec-40 check | Y | Y | Y |
| mode=off compatibility | Y | Y | Y |
| Safety-floor validation | Y | Y | Y |
| Provenance validation | N/A | Y | Y |
| CLI/TestClient | Y | Y | Y |
| Web/API | N/A | Y | Y |
| Browser/Playwright | N/A | Y | Y |
| Source/worktree | N/A | Y | N/A |

---

## 10. Browser Validation Plan (BW tests for Track-B)

New BW tests added to browser validation script:

| Test | Description |
|------|-------------|
| BW11 | `review_history_summary` present in `/api/review/intelligent` response |
| BW12 | Browser renders "Historical Evidence" section when entries > 0 |
| BW13 | `cfm_calibration` field present in `IntelligentReport` JSON |
| BW14 | CFM shadow score rendered per comment when available |
| BW15 | Source URL links present in historical evidence section |

---

## 11. Source-Level Validation Plan

Same worktree approach as Phase-4V2:
- Apply patch to linux-next v7.2-rc4 worktree.
- Run KRI review with worktree as `KRI_KERNEL_PATH`.
- For APPLY_CLEAN: verify historical evidence appears in response.
- For APPLY_FAILED (S6): verify no source-derived historical evidence fabricated.

---

## 12. Backend-to-Frontend Consistency Table (target)

| Feature | Backend | API | Browser UI | CLI | Source | Tested | Gap |
|---------|---------|-----|------------|-----|--------|--------|-----|
| review_history_summary | reviewer.py | IntelligentReport | renderIntelligent() | TestClient | worktree | test_track_b_wp4j | — |
| ReviewHistoryEntry provenance | learning/models.py | review_history_summary.source_urls | source URL links | TestClient | — | test_track_b_wp4j | — |
| cfm_calibration | learning/calibration.py | IntelligentReport | cfm section | TestClient | — | test_track_b_wp4k | — |
| cfm_shadow_score (per comment) | reviewer.py existing | inline_comments[].cfm_confidence | comment card | TestClient | — | test_track_b_wp4k | — |
| knowledge_state_id | knowledge_manager | metadata.knowledge_state_id | renderIntelligent() | TestClient | — | V6 (existing) | — |
| governance_warnings | reviewer.py | patches[].governance_warnings | gov block | TestClient | — | V8/V9 (existing) | — |

---

## 13. STOP Conditions (verbatim from authorization)

1. Evidence stored without provenance.
2. Pattern node created without source_url/message_id.
3. Lore data treated as truth without validation.
4. CFM production gate activates without meeting criteria.
5. Safety floor weakened.
6. mode=off behavior changes.
7. Sec-40 nondeterminism introduced.
8. Browser validation fails after 3 attempts.
9. Source-level claims made when patch failed to apply.
10. Kernel source worktree corrupted.
11. Track-B parser fabricates review comments.
12. Unsupported historical pattern affects severity.
13. Full suite regresses beyond 3 fix attempts.
14. Governance Auditor raises constitutional violation.
15. Data quality too poor to proceed safely.

---

## 14. Commit Strategy

| Commit | Contents | Push |
|--------|----------|------|
| B0 | `docs/TRACK_B_AUTONOMOUS_EXECUTION_PLAN_2026-07-25.md` | yes |
| WP4-I | `kri/learning/models.py` (if new), `tests/test_track_b_wp4i.py` | yes |
| WP4-J | `kri/learning/models.py`, `kri/learning/store.py`, `kri/learning/ingestion.py`, `kri/llm/models.py`, `kri/web/app.py`, `tests/test_track_b_wp4j.py`, `.kri/lore_review_dataset/` | yes |
| WP4-K | `kri/learning/calibration.py`, `kri/llm/models.py`, `kri/web/app.py`, `tests/test_track_b_wp4k.py` | yes |
| B9 | `docs/TRACK_B_AUTONOMOUS_EXECUTION_REPORT_2026-07-25.md` | yes |

Never `git add -A`. Stage explicit files only.

---

## 15. Final Success Criteria

**TRACK_B_COMPLETE** if:
- WP4-I, WP4-J, WP4-K all committed and passing.
- ≥20 lore series in dataset with full provenance.
- `ReviewHistoryEntry` persisted with 100% provenance coverage.
- `review_history_summary` surfaced in API + browser.
- CFM shadow calibration completed; `CFMCalibrationReport` in API.
- 48/48 Phase-4V2 validation paths still PASS (no regression).
- All Track-B WP tests passing.
- 0 STOP conditions.

**TRACK_B_PARTIAL** if:
- ≥1 WP complete, ≥1 WP incomplete due to data quality or infrastructure limit.
- All completed WPs validated.

**TRACK_B_BLOCKED** if:
- Infrastructure gap prevents progress (e.g., lore fetch blocked, EKG persistence broken).

**STOP_CONDITION_HIT** if:
- Any STOP condition above triggers.
