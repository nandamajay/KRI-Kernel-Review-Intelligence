# KRI Architecture Audit

**Date:** 2026-07-21
**Trigger:** First live validation of `/api/review/intelligent` against a real lore thread
**Thread:** `Re: [PATCH v4 0/4] ASoC: qcom and pinctrl: add LPASS LPR voting and Hawi LPASS LPI TLMM`
**Lore URL:** https://lore.kernel.org/all/CAMRc=Mf6tohNxQ40AAcx=MXb7ZQN2mj6j4LJ=4V4ZpQfPktS3w@mail.gmail.com/

---

## 1. Executive Summary

KRI operates as a **patch-text reviewer** today. Neither the rule-based nor the
LLM-powered endpoint applies patches to the kernel tree, runs checkpatch, or
performs static analysis. Repository and static-analysis code exists but is not
wired into the web flow.

The intelligent review endpoint (`/api/review/intelligent`) produces high-quality
findings with inline comments, reasoning, suggested fixes, and lore-style email
replies — all derived from LLM analysis of the raw patch diff.

---

## 2. Current Review Mode

| Endpoint | Mode | Evidence Source |
|----------|------|-----------------|
| `POST /api/review` | Rule-based pattern matching on diff text | ASoC DKP patterns (keyword signals in added lines) |
| `POST /api/review/intelligent` | LLM (Claude via QGenie) analysis of diff text | Patch text + DKP domain context fed to prompt |

**Neither mode** performs:
- Repository checkout
- Patch application (git am / git apply)
- Static analysis (checkpatch, sparse, smatch, coccinelle)
- Build validation
- Blame lookups against the kernel tree

---

## 3. Call Chain: `/api/review` (Rule-Based)

```
UI → POST /api/review
  ↓
  kri/web/app.py:156  review_series()
  ↓
  kri/lore_manager/manager.py        lore.fetch(lore_ref) [HTTP → lore.kernel.org]
  ↓
  kri/patch_manager/manager.py       patches.parse(thread) [mbox → PatchSeries]
  ↓
  kri/knowledge_manager/manager.py   km.load_dkp("asoc") [loads EKG graph]
  ↓
  kri/simulation/engine.py:67        sim.simulate(series)
    ↓
    kri/review_engine/engine.py:69   ReviewEngineImpl.review(series, dkp)
      ↓
      For each patch × each plugin:
        kri/packages/asoc/plugins.py:158   PatternMatchPlugin.applies()
        kri/packages/asoc/plugins.py:183   PatternMatchPlugin.evaluate()
        kri/evidence_engine/engine.py:73   EvidenceEngineImpl.gather()
        kri/confidence_engine/engine.py:81 ConfidenceEngineImpl.score()
    ↓
    kri/report/generator.py              ReportGenerator.generate(decisions)
  ↓
  Returns JSON report
```

**Why 0 decisions for this thread:** The ASoC patterns look for specific keywords
(e.g. `snd_soc_read`, `snd_soc_write`, resume-cleanup patterns, TDM slot patterns).
This patch series (LPR voting + pinctrl) does not contain those signal keywords.

---

## 4. Call Chain: `/api/review/intelligent` (LLM-Powered)

```
UI → POST /api/review/intelligent
  ↓
  kri/web/app.py:193  intelligent_review()
  ↓
  kri/lore_manager/manager.py       lore.fetch(lore_ref)
  ↓
  kri/patch_manager/manager.py      patches.parse(thread)
  ↓
  kri/knowledge_manager/manager.py  km.load_dkp("asoc") [optional domain context]
  ↓
  kri/llm/reviewer.py:43            IntelligentReviewEngine.review(series)
    ↓
    For each patch (up to 4 in parallel via ThreadPoolExecutor):
      kri/llm/reviewer.py:69        _review_patch(patch, series)
        ↓ 3 agents in parallel:
        kri/llm/agents.py:40        PatchSummarizerAgent.analyze()  → PatchSummary
        kri/llm/agents.py:67        CodeQualityAgent.review()       → InlineComment[]
        kri/llm/agents.py:117       SubsystemExpertAgent.review()   → InlineComment[]
        ↓
        _merge_comments(): deduplicate, filter <0.4 confidence, sort by severity
        ↓
        format_lore_reply(): generate lore-style email
    ↓
    kri/llm/reviewer.py:52          _generate_overall_assessment() [final LLM call]
  ↓
  Returns IntelligentReport.model_dump()
```

**LLM details:**
- Gateway: `https://qgenie-api.qualcomm.com/v1/messages`
- Model: `anthropic::claude-4-6-sonnet`
- Auth: `ANTHROPIC_AUTH_TOKEN` (Bearer → x-api-key)
- Calls per 4-patch series: 13 (3 agents × 4 patches + 1 overall)
- Processing time: ~27s

---

## 5. Patch/Repository Capability Matrix

| # | Capability | Status | Code Location | `/api/review` | `/api/intelligent` |
|---|-----------|--------|---------------|:---:|:---:|
| 1 | Lore fetch | EXECUTED | `kri/lore_manager/` | Yes | Yes |
| 2 | Mbox/thread parsing | EXECUTED | `kri/lore_manager/` | Yes | Yes |
| 3 | Patch-series parsing | EXECUTED | `kri/patch_manager/` | Yes | Yes |
| 4 | Per-patch diff extraction | EXECUTED | `kri/patch_manager/` | Yes | Yes |
| 5 | Repository checkout | IMPLEMENTED_NOT_WIRED | `kri/repo_manager/manager.py:99` | No | No |
| 6 | git apply (patch application) | IMPLEMENTED_NOT_WIRED | `kri/repo_manager/manager.py:153` | No | No |
| 7 | Patch applies-cleanly check | IMPLEMENTED_NOT_WIRED | via apply_patch return | No | No |
| 8 | git blame lookups | IMPLEMENTED_NOT_WIRED | `kri/repo_manager/manager.py:237` | No | No |
| 9 | git diff generation | IMPLEMENTED_NOT_WIRED | `kri/repo_manager/manager.py:275` | No | No |
| 10 | Review against patched tree | NOT_IMPLEMENTED | — | No | No |
| 11 | Review against patch text only | EXECUTED | plugins.py + llm/reviewer.py | Yes | Yes |

---

## 6. Static Analysis Capability Matrix

| Tool | Code Exists | State | File | Wired to API | Executed |
|------|:-----------:|-------|------|:------------:|:--------:|
| checkpatch.pl | Yes | Complete | `kri/static_analysis/manager.py:63` | No | No |
| sparse | Yes | Stub | `kri/static_analysis/manager.py:152` | No | No |
| smatch | Yes | Stub | `kri/static_analysis/manager.py:160` | No | No |
| coccinelle | Yes | Stub | `kri/static_analysis/manager.py:166` | No | No |
| dt_binding_check | No | Absent | — | — | — |
| build validation | No | Absent | — | — | — |
| compile smoke test | No | Absent | — | — | — |
| Kconfig dependency | No | Absent | — | — | — |
| MAINTAINERS routing | Yes | Complete | `kri/lore_manager/maintainers.py` | Indirectly | Metadata only |

---

## 7. Inline Comments Audit

| Question | Answer |
|----------|--------|
| Does KRI generate inline_comments? | YES — `/api/review/intelligent` only |
| Present in response schema? | YES — `InlineComment` in `kri/llm/models.py` |
| Fields populated | file_path, line_number, category, severity, message, suggestion, confidence, reasoning |
| `hunk_context` populated? | Often empty (LLM doesn't reliably fill it) |
| Rendered in UI? | Partially — file, line, category, severity, message, suggestion, confidence shown |
| NOT rendered | `hunk_context` (code snippet), `reasoning` (why explanation) |

---

## 8. Explainability / "Why?" Audit

| Desired field | In JSON? | Populated? | Rendered? | Gap |
|--------------|:--------:|:----------:|:---------:|-----|
| File | Yes (`file_path`) | Yes | Yes | — |
| Line | Yes (`line_number`) | Yes | Yes | — |
| Code snippet | Yes (`hunk_context`) | Often empty | No | Needs extraction from diff |
| Finding | Yes (`message`) | Yes | Yes | — |
| Why? explanation | Yes (`reasoning`) | Yes | **No** | UI ignores field |
| Evidence | Rule-based only | Yes | Yes (rule-based) | Missing for intelligent |
| Confidence | Yes (`confidence`) | Yes | Yes | — |
| Confidence rationale | Implicit in `reasoning` | Partial | **No** | No dedicated field |
| Upstream review comment | Yes (`lore_reply`) | Yes | Yes (collapsible) | — |
| Suggested fix | Yes (`suggestion`) | Yes | Yes | — |

---

## 9. UI Rendering Bugs

| Bug | Backend produces | UI reads | Effect |
|-----|-----------------|----------|--------|
| Factor scores field name | `"factors": {...}` | `d.confidence.factor_scores` | Never renders |
| Factor weights missing | Not serialized | `d.confidence.factor_weights` | Always 0 |
| `d.why` (rule rationale) | Populated | Not accessed | Hidden from user |
| `d.where` (location) | Populated | Not accessed | Hidden from user |

---

## 10. Finding Source Classification (Live Test)

All findings from the intelligent review of this lore thread:

| Patch | Finding | Source |
|-------|---------|--------|
| Patch 2/4 (q6prm) | LPR payload sets SLEEP_DISABLE on release path | LLM reasoning from patch text |
| Patch 2/4 (q6prm) | Same issue at line 96 (duplicate angle) | LLM reasoning from patch text |
| Patch 4/4 (pinctrl) | Missing DT binding for new compatible string | LLM reasoning from patch text + DT conventions |

**All findings are LLM-generated from patch text only.** No repository evidence, no
checkpatch output, no static analysis, no applied-tree state was used.

---

## 11. Architecture Gaps

| # | Gap | Classification | Effort |
|---|-----|---------------|--------|
| 1 | `reasoning` not rendered ("Why?" button) | Quick win (UI-only) | 5 lines JS |
| 2 | `hunk_context` not rendered (code snippet) | Quick win (UI-only) | 3 lines JS |
| 3 | `hunk_context` often empty from LLM | Backend fix needed | Extract from diff |
| 4 | `factor_scores`/`factor_weights` field name bug | Quick win (UI-only) | Fix JS field names |
| 5 | `d.why` (rule rationale) not rendered | Quick win (UI-only) | Add to renderReport() |
| 6 | `d.where` (location) not rendered | Quick win (UI-only) | Add to renderReport() |
| 7 | Patch apply status | Implemented not wired | Wire repo_manager |
| 8 | checkpatch execution | Implemented not wired | Wire static_analysis |
| 9 | Applied-tree review | Needs architecture | New pipeline |
| 10 | Rule-based returns 0 for most threads | MVP-limited | Only 18 narrow patterns |
| 11 | Evidence for intelligent review | Missing | Feed repo evidence to LLM |

---

## 12. Recommended Next Steps

### Phase 1 — UI Quick Wins (no backend changes needed)

1. Render `reasoning` as collapsible "Why?" section
2. Render `hunk_context` as code block above finding
3. Fix `factor_scores` → `factors` field name in `renderReport()`
4. Render `d.why` and `d.where` in rule-based cards
5. Add "Review Mode" badge (LLM vs Rule-Based)

### Phase 2 — Backend/Schema Improvements

1. Populate `hunk_context` by extracting from diff in `_review_patch()`
2. Add `confidence_rationale` field to InlineComment
3. Include `alternative_recommendation` in `/api/review` JSON
4. Wire `rule_based_decisions` (run both engines, merge results)

### Phase 3 — Maintainer-Grade Execution

1. Wire `repo_manager.apply_patch()` into review flow
2. Add patch-applies-cleanly status to response
3. Wire checkpatch.pl execution for applied series
4. Feed apply + checkpatch results into LLM prompt
5. Add git blame context for modified lines

---

## 13. Final Verdict

**A)** KRI is currently a **patch-text reviewer**, not a full patched-tree
maintainer simulator.

**B)** checkpatch and patch-apply are **implemented but not wired** to the web
flow. sparse/smatch/coccinelle are stubs. Build validation is absent.

**C)** The current JSON **can support** the desired explainability UI with quick
wins — `reasoning`, `suggestion`, `lore_reply`, `confidence` are all populated.
The main gap is `hunk_context` (code snippet) which needs backend extraction.

**D)** Recommendation: **Do Phase 1 UI quick wins first** (~30 min), then evaluate
more lore threads. The `reasoning` field already contains excellent explanations
that users cannot currently see.

---

## References

- Prior context: WP-9.2a-polish-v3 (`bfb22ba`), WP-KERNEL-REFRESH (`cd8a66e`)
- Live test: 13 LLM calls, 26.9s, 46,757 input tokens, 5,724 output tokens
- Response JSON: 4 patches reviewed, 3 inline findings (2 blocker, 1 warning)
