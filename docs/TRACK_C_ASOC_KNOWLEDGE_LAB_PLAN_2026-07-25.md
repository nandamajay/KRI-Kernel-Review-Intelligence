# Track-C ASoC Knowledge Lab — Governance Review Report
**Date:** 2026-07-25  
**Governance Process:** 7-agent pre-implementation review  
**Status:** APPROVED WITH MODIFICATIONS — implementation scope significantly reduced

---

## Agent Findings Summary

### Agent 1 (Chief Architect) — Plan produced
Full architecture with `kri/knowledge_lab/` package, LabNode/LabEdge models,
JSONL persistence, browser page, and API endpoints. Estimated v1 scope: ~50
core+qcom files, ~1,200 nodes. Basis for review by other agents.

### Agent 2 (ASoC Domain Architect) — Key findings
**Top 3 value relationships** (of 8 proposed graph types):
1. DAI Link → CPU/Codec/Platform triplet (highest bug-finding ratio)
2. DAPM widget + SUPPLY dependency chain (power regression root cause)
3. DPCM FE/BE pairing with format/rate merge flags

**Failure assumptions in naive extractors:**
- Struct initializer macros (`SND_SOC_DAPM_MUX(...)`) are not parseable by regex
- `dai_link[]` arrays are often built dynamically at probe time from DT
- DPCM FE/BE connections resolved at runtime — no static symbol to extract
- OF match tables may be in `common.c` across platform directories

**Conclusion:** The three highest-value relationships all require either
runtime-resolved DT matching or macro expansion — beyond v1 regex extraction.
The only reliably extractable information is static: function definitions,
struct declarations, EXPORT_SYMBOL markers.

### Agent 3 (Historical Knowledge Architect) — Key findings
1. 47 entries from 4 series is below minimum viability (need 200+ across 15+)
2. **Root cause of 74% generic rate:** `extract_reviews()` currently ingests
   cover letters, patch bodies, and author self-responses — not just reviewer replies
3. **Fix:** add author-vs-reviewer filter to `extract_reviews()` before expanding dataset
4. **ASoC signals to add:** dapm/widget/route, jack/detection, machine/platform/codec driver
5. **Fetch strategy:** `lore.kernel.org/linux-sound/<msgid>/t.mbox.gz` for thread mboxes
6. **Recommendation:** Fix extraction quality on existing 24 series before fetching more

### Agent 4 (Browser UX Architect) — Key findings
**ACCEPT:** Review knowledge explorer (lore links, maintainer flags), JSON export links  
**REJECT:** Architecture graph (underlying data is rules/evidence, not DAI topology),
Overview cards (answer no review question), Source explorer (requires data not yet built),
Heatmaps/tables (charting dependency cost > value)

**Critical observation:** `app.py` is already 918 lines; adding another full page inline
is unmaintainable. **Recommendation:** use `StaticFiles` mount → `kri/web/static/knowledge-lab.html`

**Minimum viable page:** Rule table + lore entry table + one stats line. ~120 lines HTML.

### Agent 5 (Governance Auditor) — Findings

| # | Severity | Rule | Resolution |
|---|----------|------|------------|
| A5-1 | **BLOCKER** | Sec-40: `os.walk()` without `sorted()`, `mtime` for cache keys, `uuid4` for IDs | Use `sorted()` always; cache key = `git rev-parse HEAD`; IDs via hashlib |
| A5-2 | WARNING | Tier-0 scope: distinguish lore-origin vs file-origin nodes | Document explicitly; WP-C3 lore nodes keep source_url |
| A5-3 | **BLOCKER** | Sec-9: ASoC-specific extraction must stay in `kri/packages/asoc/` | `kri/knowledge_lab/` is domain-agnostic; ASoC patterns in DKP plugin |
| A5-4 | **BLOCKER** | CFM gate: shadow context must not touch EvidenceGraph scoring fields | Shadow context attaches to new `knowledge_context: dict` on Decision only |
| A5-5 | **BLOCKER** | Safety floor: WP-C6 must not be on critical path for blockers/warnings | WP-C6 deferred entirely per Agent 6 recommendation |
| A5-6 | INFO | Provenance for file-derived nodes: use existing Provenance model | repo_path + commit_hash + transformation_history; source_url=None |

### Agent 6 (Adversarial Reviewer) — Findings

| WP | Verdict | Rationale |
|----|---------|-----------|
| C1 (bulk extraction) | SIMPLIFY | On-demand extraction from diff file list only; not bulk 725-file scan |
| C2 (code/arch graph) | CUT | LLM already reads diff context; graph edges add no unique signal |
| C3 (lore expansion) | DEFER unless targeted | 17% yield historically; only fetch series with known substantive review |
| C4 (browser page) | KEEP (reduced) | Review knowledge explorer only; no architecture graph |
| C5 (API endpoints) | SIMPLIFY | Cut from 5 to 3 endpoints |
| C6 (shadow injection) | DEFER | 65% probability of degrading LLM output; no A/B proof |
| C7 (tests) | KEEP | Mandatory quality gate |
| C8 (docs) | KEEP (reduced) | Final report only |

### Agent 7 (Test Auditor) — Findings
1. All `assert len > 0` and `assert count >= 1` tests **REJECTED**
2. **Fabrication guard required:** for each extracted entity, assert `symbol_name in source_lines[line_no-1]` using actual file
3. **Empty-state test:** all API endpoints return 200 with valid empty JSON when KRI_KERNEL_PATH unset
4. **Sec-40:** `kri/knowledge_lab/` must NOT be excluded from `_iter_kri_files()` scan
5. **Integration test:** after ingesting fixture, entity names must appear in `/knowledge-lab` HTML
6. **Regression risk:** `test_stochastic_confinement.py`, `test_web.py`, Track-B tests

---

## Accepted Recommendations

1. ✅ **A5-1 ACCEPTED:** All `os.walk()` calls wrapped with `sorted()`; node IDs via `hashlib.sha256(f"{node_type}:{file_path}:{line_no}:{name}".encode()).hexdigest()[:16]`; cache key = git rev-parse HEAD (no time-based keys)
2. ✅ **A5-3 ACCEPTED:** `kri/knowledge_lab/` is domain-agnostic; accepts file paths and extraction patterns as data; ASoC-specific patterns provided by `kri/packages/asoc/` at runtime
3. ✅ **A6: WP-C2 CUT:** No code/architecture graph builder in v1; existing DKP graph (rules/patterns/evidence) is sufficient for review
4. ✅ **A6: WP-C6 DEFERRED:** Shadow context injection removed from Track-C scope
5. ✅ **A4: StaticFiles mount:** `kri/web/static/knowledge-lab.html` served from static mount; `app.py` not bloated further
6. ✅ **A3: Fix extraction quality first:** Author-vs-reviewer filter + 8 ASoC signals added to `ingestion.py` before dataset expansion
7. ✅ **A7: Fabrication guard:** Every extracted entity has its `symbol_name` verified at line `line_no` in the source file
8. ✅ **A6: On-demand extraction:** v1 extracts only files in the current diff, not all 725 codecs
9. ✅ **A4: Minimum viable page:** Rule table + lore entry table; no graph canvas

## Rejected Recommendations

| Recommendation | Rejected | Rationale |
|----------------|----------|-----------|
| A6: CUT entire C1 (extractor) | Partially rejected | Extractor is kept but scoped to core+qcom only (~50 files); extraction enables the "file in diff" lookup that makes C4 useful |
| A6: CUT C4 browser page | Partially rejected | Page is kept but reduced to review-knowledge-explorer + rule table per A4 guidance |
| A4: No new API endpoints | Rejected | 3 minimal endpoints needed to serve the static page data-driven; hardcoding data in HTML is worse |
| A5-4: Defer all of WP-C6 | Full accept — WP-C6 removed | |

---

## Revised Implementation Plan

### Scope: 4 work packages (reduced from 8)

**C0: Plan + documentation** (this document)  
**C1: Ingestion quality improvement**  
- Add author-vs-reviewer filter to `kri/learning/ingestion.py:extract_reviews()`
- Add 8 ASoC-domain claim signals to `_CLAIM_SIGNALS`
- Re-run ingestion on existing 24 mboxes; verify signal improvement
- Commit: `kri/learning/ingestion.py`, updated tests

**C2: knowledge_lab package + domain-agnostic extractor**  
Files: `kri/knowledge_lab/models.py`, `kri/knowledge_lab/extractor.py`,
`kri/knowledge_lab/store.py`, `kri/knowledge_lab/__init__.py`
- `LabNode`: node_id, node_type, name, file_path, line_no, subsystem, exported, signature
- `LabEdge`: src, dst, edge_type, file_path, line_no
- `ExtractionManifest`: source_git_sha, file_count, node_count, edge_count, parse_error_count
- Extractor: regex scan for `struct`, `static int/void`, `EXPORT_SYMBOL` on sorted file list
- ASoC-specific patterns injected by `kri/packages/asoc/` (not in knowledge_lab)
- Scope: `sound/soc/*.c` + `sound/soc/qcom/*.c` + 5 selected codecs
- Persist: `.kri/knowledge_lab/lab_nodes.jsonl`, `manifest.json`

**C3: API endpoints (3) + /knowledge-lab page**  
- `GET /api/knowledge/asoc/stats` — node/edge counts, manifest info, review entry counts
- `GET /api/knowledge/asoc/reviews` — lore entries with source_url, maintainer flags, claim categories
- `GET /api/knowledge/asoc/rules` — DKP rules with provenance
- `GET /knowledge-lab` → serves `kri/web/static/knowledge-lab.html`
- Static page: rule table + lore review explorer + stats header
- All endpoints return safe empty state when artifacts absent

**C4: Tests**  
- `tests/test_knowledge_lab_extractor.py` — fabrication guard, determinism, Sec-40
- `tests/test_knowledge_lab_api.py` — empty state, schema validation
- `tests/test_knowledge_lab_ui.py` — page loads, integration (entity names in HTML)
- Full pytest suite passes (640+N)

**Explicitly deferred:**
- WP-C2 graph builder (code/architecture graph): no implementation
- WP-C6 shadow review context injection: deferred to Track-D with A/B framework
- WP-C3 bulk lore expansion: deferred; ingestion quality improvement is the prerequisite

### Architecture Safety Rules (from governance review)
1. `kri/knowledge_lab/` — domain-agnostic only; no ASoC string literals
2. All node IDs — `hashlib.sha256(...)[:16]`; no `uuid`, `random`, `time`
3. All `os.walk()` / `glob()` → wrapped with `sorted()`
4. Cache key — `git rev-parse HEAD` subprocess; never `mtime` or `datetime.now()`
5. API endpoints — no writes to `EvidenceGraph`; no CFM gate influence
6. Safety floor — Knowledge Lab is a read endpoint only; no scoring path modification
7. `kri/knowledge_lab/` — included in `test_stochastic_confinement.py` scan (not excluded)
8. Every `LabNode` — `file_path` + `line_no` must point to real line in real file

### Git Commit Strategy
```
Track-C C0: governance plan review report
Track-C C1: ingestion quality — author filter + ASoC claim signals  
Track-C C2: knowledge_lab package — domain-agnostic extractor + store
Track-C C3: /knowledge-lab page + API endpoints (stats/reviews/rules)
Track-C C4: tests + validation + final report
```
