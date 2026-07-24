# WP-S2A — Cross-Version Review History (Transient Per-Review Layer)
**Status:** SPEC — DRAFT  
**Date:** 2026-07-24  
**Tier:** T3 (follows WP-S1B closeout)  
**Scope:** WP-S2A only — transient, per-review consumption of prior-version maintainer feedback.  
WP-S2B (persistent KB ingestion into `kri/learning/`) is explicitly out of scope for this spec.

---

## 1. Problem Statement

When a submitter sends v5 of a patch series, KRI reviews only v5. The maintainer feedback on v1..v4 — the highest-signal indicator of what reviewers care about for this particular code — is ignored. A new reviewer reading the v5 submission has no memory of what was asked on v2, no visibility into which concerns the author already addressed, and no context about the iterative dialogue that shaped the code.

Real maintainers read the full version history before reviewing v(n). KRI does not. This gap is the primary source of KRI producing redundant feedback (re-raising already-addressed concerns) and missing reviewer-specific patterns (e.g., a maintainer who always asks about devm allocation in this subsystem).

---

## 2. Scope and Non-Goals

**In scope:**

- Discover prior-version thread IDs (v1..v(n-1)) for any versioned series.
- Harvest maintainer critique + author reply pairs from each prior-version thread.
- Reconcile: classify each prior concern as addressed or outstanding before injection.
- Inject outstanding prior concerns as a `## Prior Version Feedback` block in per-patch agent prompts.
- Degrade gracefully when prior versions are unavailable (offline, no cache, search failure).

**Out of scope:**

- Persistent storage of harvested review history (WP-S2B — `kri/learning/` layer).
- Version-diff semantic analysis beyond simple token-presence heuristics (requires EKG).
- Confidence factor model integration (D3 — depends on EKG, deferred).
- Any mutation of `PatchSeries`, `ReviewComment`, or `IntelligentReport` schemas visible in the API response.
- UI changes beyond what is already rendered via `metadata`.

---

## 3. Architecture

### 3.1 New module: `kri/lore_manager/version_discovery.py`

A pure function module (no class). All public functions are deterministic given fixed inputs (no random, no time calls — Sec. 40 compliant). Network I/O is delegated entirely to `LoreManagerImpl.fetch()` and `LoreManagerImpl.search()`, which are already cached and offline-safe.

```
kri/lore_manager/version_discovery.py
├── discover_prior_version_thread_ids(series, lore_manager) -> dict[int, str]
├── fetch_prior_version_series(thread_ids, lore_manager, patch_manager) -> list[PatchSeries]
├── extract_critique_reply_pairs(series) -> list[CritiqueReplyPair]
├── filter_addressed_concerns(pairs, current_series) -> list[CritiqueReplyPair]
└── format_prior_version_context(pairs, current_patch_id) -> str
```

### 3.2 New dataclass: `CritiqueReplyPair`

```python
@dataclass(frozen=True)
class CritiqueReplyPair:
    version: int                      # which prior version (e.g., 2)
    critique: ReviewComment           # the maintainer's comment
    author_reply: str                 # author's direct reply body (empty if no reply)
    address_status: str               # "addressed_explicit" | "addressed_diff" | "outstanding"
    address_notes: str                # human-readable annotation for marginal cases
```

Lives in `kri/lore_manager/version_discovery.py` (local to this module, not promoted to `kri/common/models.py` — it is transient and never serialized to the API response).

### 3.3 Wiring into `IntelligentReviewEngine`

`IntelligentReviewEngine.__init__` accepts:
```python
prior_version_fetcher: PriorVersionFetcher | None = None
```

where `PriorVersionFetcher` is a `Protocol` with:
```python
def fetch(self, series: PatchSeries) -> list[CritiqueReplyPair]: ...
```

This is constructed in `kri/web/app.py`'s `intelligent_review()` handler (same pattern as `ApplicabilityGate` construction in T90).

`_review_patch()` calls `self._prior_version_fetcher.fetch(series)` once per series (not per patch, cached on first call), then formats a per-patch context block via `format_prior_version_context(pairs, patch_id)`.

---

## 4. Discovery Algorithm

### 4.1 Primary: `In-Reply-To` chain traversal

Every `Message` parsed by `mbox.py` has `in_reply_to: str | None`. For versioned series (v2+), submitters routinely set `In-Reply-To` on the cover letter to the v(n-1) cover letter message-id.

Algorithm:
1. Extract the cover letter message from `series.patches[0].series_id` (the current thread's root).
2. Walk `Message.in_reply_to` up the chain through `lore_manager.fetch()` until either:
   - We reach a message with `SubjectInfo.version == 1` (found v1), OR
   - `in_reply_to` is `None` or resolves to a non-patch message, OR
   - We have walked more than `max_depth=10` steps (guard against infinite loops from malformed threading).
3. For each step, validate the resolved thread is a patch series by checking `parse_subject()` yields `is_cover_letter=True` and `version < current_version`.
4. Author-match guard: verify `PatchSeries.patches[0].author` matches the current series' author (case-insensitive email comparison).

### 4.2 Fallback: `lore_manager.search()`

Only triggered when the `In-Reply-To` chain traversal fails to find a prior version.

```python
query = f'"{title_normalized}" v{target_version}'
```

where `title_normalized = re.sub(r'[:\-–—,\.]+$', '', series.series_title.lower()).strip()`.

Results filtered by:
- `SubjectInfo.version == target_version` (not just any occurrence of "v{n}" in subject)
- `is_cover_letter=True`
- Author email matches (same guard as above)

### 4.3 Failure modes and degradation

| Failure | Detection | Response |
|---|---|---|
| `In-Reply-To` absent (submitter sent as new thread) | `Message.in_reply_to is None` | Fall through to search |
| Lore offline / cache miss | `LoreOfflineError` / network timeout | Log warning, return `[]` for that version |
| Author mismatch (false match from search) | email != series author | Skip candidate; do not inject |
| Title drift between versions | Search returns zero results | Log at DEBUG, return `[]` for that version |
| Prior-version thread has no patch messages | `len(PatchSeries.patches) == 0` | Skip that version silently |

Degradation contract: `fetch()` on `PriorVersionFetcher` NEVER raises. All exceptions are caught; worst case is empty `list[CritiqueReplyPair]`. The review proceeds identically to a review with no prior-version data.

---

## 5. Already-Addressed Reconciliation

The core design risk is injecting stale concerns. Three heuristics, applied in order:

### H1 — Explicit author acknowledgement (suppress)

Author's reply body (after `_strip_quotes`) contains a word from the acknowledgement set:
```python
_ACK_WORDS = re.compile(r'\b(fixed|done|addressed|changed|updated|applied|removed|dropped|rewritten|reworked|corrected)\b', re.I)
```

If the regex matches, mark `address_status="addressed_explicit"`. Do not inject this concern.

### H2 — Diff pattern absence (annotate)

1. Extract 2–5 lowercase tokens from the critique body that look like a symbol or function call: `re.findall(r'\b[a-z_][a-z_0-9]{3,}\b', critique.message.lower())[:5]`.
2. For the same file(s) touched by the prior-version concern (from `critique.target_patch_id → prior_series.patches`), check whether those tokens appear on any `+` line in the corresponding v(n) patch diff.
3. If none of the tokens appear on any `+` diff line: mark `address_status="addressed_diff"`. Do not inject.
4. If tokens appear but context is different: mark `address_status="outstanding"`, `address_notes="[may be addressed in v{n}]"`. Inject with annotation.

H2 fires reliably for API-misuse comments naming specific functions. It is unreliable for stylistic comments (use H1 as the primary gate for those).

### H3 — No-reply outstanding (inject with low weight)

If neither H1 nor H2 fires:
- `address_status="outstanding"`, inject with full weight.
- If `author_reply == ""` (no author reply found): also annotate `address_notes="[author did not reply to this concern]"` — signals to the LLM that this may be deliberately unaddressed.

### Filter decision table

| H1 | H2 | H3 | Decision |
|---|---|---|---|
| explicit ack | — | — | suppress |
| — | pattern absent | — | suppress |
| — | pattern present (partially) | — | inject + note "may be addressed" |
| — | tokens not extractable | no reply | inject + note "author did not reply" |
| — | tokens not extractable | reply exists | inject with no annotation |

---

## 6. Prompt Injection Format

### 6.1 Per-patch block format

```
## Prior Version Feedback

The following maintainer concerns were raised on earlier versions of this patch and
appear unresolved as of v{n}. Consider whether they apply to this version.

- [v{version}, {author}] {critique.message}
  {address_notes if address_notes else ""}

- [v{version}, {author}] ...
```

Rules:
- Only include concerns where `address_status == "outstanding"` AND `critique.target_patch_id` maps to the current patch (by file overlap or explicit `target_patch_id` match).
- Maximum 5 concerns per patch (take highest-severity first, then most-recent version first).
- Block is entirely absent (empty string returned by `format_prior_version_context`) when the filtered list is empty.
- All strings are passed through `esc()` equivalent before injection (no raw HTML in prompts).

### 6.2 Prompt template changes

`kri/llm/prompts.py` — add `{prior_version_context}` placeholder after `{series_context}` in:
- `REVIEW_CODE_QUALITY_PROMPT` (currently line ~153)
- `REVIEW_SUBSYSTEM_PROMPT` (currently line ~189)

Placement rule: if `prior_version_context == ""`, the block is absent (no trailing whitespace added). This preserves byte-identity for the existing test `test_series_reducer_b12_determinism_bytes` when no prior version data is available.

---

## 7. Strategy C (Gate Result Boundary)

Strategy C from WP-T2A: "gate results never injected into any LLM agent prompt." WP-S2A introduces a different concern: the Strategy C rule must be extended:

**Extended Strategy C boundary for WP-S2A:**
- Prior-version review comments ARE injected into agent prompts (this is the feature).
- The `ApplicabilityGate` result (`apply_status`) is still NEVER injected (prior WP-T2A constraint preserved).
- The `CritiqueReplyPair.address_status` field is internal bookkeeping only — it must not be included in the injected text (would anchor the LLM on our categorization rather than its own reasoning).

Test: `test_TB_S2A_gate_result_still_never_in_prompt` — extends `test_TB90_T24_gate_result_never_reaches_agent_prompt` to cover the new `prior_version_context` injection path.

---

## 8. Constitution §40 (Stochastic Confinement)

`kri/lore_manager/version_discovery.py` must not contain any calls to `random`, `time.time`, `datetime.now`, `uuid.uuid1/4`. This module is pure computation over data fetched by `LoreManagerImpl`. Any elapsed-time telemetry (if added) must use `time.monotonic()` and be added to `_ALLOWED_CALLS` in `test_stochastic_confinement.py`.

---

## 9. Test Specification

Minimum test coverage for WP-S2A — 12 tests across 3 sections:

### 9a — Discovery tests (5 tests)

| ID | Name | Assertion |
|---|---|---|
| TS2A-1 | `test_discover_finds_prior_via_in_reply_to` | Mock `lore_manager.fetch` with a chain of 3 versions; assert `discover_prior_version_thread_ids` returns `{1: id1, 2: id2, 3: id3}` |
| TS2A-2 | `test_discover_falls_back_to_search_when_no_in_reply_to` | `in_reply_to=None`; mock `search` returns candidate; assert result contains version 1 |
| TS2A-3 | `test_discover_rejects_author_mismatch` | Search returns a candidate with different author email; assert result is empty |
| TS2A-4 | `test_discover_degrades_on_offline_error` | `fetch` raises `LoreOfflineError`; assert `discover_prior_version_thread_ids` returns `{}`, no exception |
| TS2A-5 | `test_discover_respects_max_depth_guard` | Mock an `in_reply_to` cycle (A → B → A); assert no infinite loop, returns partial result |

### 9b — Reconciliation tests (4 tests)

| ID | Name | Assertion |
|---|---|---|
| TS2A-6 | `test_h1_explicit_ack_suppresses_concern` | Author reply contains "fixed in v3"; assert `address_status == "addressed_explicit"` |
| TS2A-7 | `test_h2_diff_pattern_absent_suppresses` | Critique mentions `devm_clk_get`; v(n) `+` lines do not contain it; assert suppressed |
| TS2A-8 | `test_h2_diff_pattern_present_injects_with_note` | `devm_clk_get` appears in v(n) diff; assert injected with "may be addressed" annotation |
| TS2A-9 | `test_no_reply_injects_with_no_author_reply_note` | `author_reply == ""`; assert injected with "author did not reply" annotation |

### 9c — Prompt injection + wiring tests (3 tests)

| ID | Name | Assertion |
|---|---|---|
| TS2A-10 | `test_format_prior_version_context_empty_when_no_outstanding` | All concerns addressed; assert `format_prior_version_context([], "p1")` returns `""` |
| TS2A-11 | `test_prior_version_context_reaches_agent_prompt` | One outstanding concern; engine's mock `code_quality.review` receives a prompt containing `"Prior Version Feedback"` |
| TS2A-12 | `test_gate_result_still_never_in_prompt_after_s2a` | `apply_status` sentinel never appears in any prompt argument even when prior-version context is injected |

---

## 10. File Inventory

| File | Action | Change Type |
|---|---|---|
| `kri/lore_manager/version_discovery.py` | CREATE | New module |
| `kri/lore_manager/__init__.py` | MODIFY | Export `discover_prior_version_thread_ids`, `PriorVersionFetcher` |
| `kri/llm/prompts.py` | MODIFY | Add `{prior_version_context}` placeholder |
| `kri/llm/reviewer.py` | MODIFY | Accept `prior_version_fetcher`, wire into `_review_patch` |
| `kri/web/app.py` | MODIFY | Construct `PriorVersionFetcher` in `intelligent_review()` |
| `tests/test_version_discovery.py` | CREATE | 12 tests (TS2A-1..12) |
| `tests/test_stochastic_confinement.py` | POSSIBLY MODIFY | If `time.monotonic()` added to version_discovery |

---

## 11. Implementation Estimate

| Component | Lines (non-test) |
|---|---|
| `version_discovery.py` (all functions + `CritiqueReplyPair`) | ~280 |
| `lore_manager/__init__.py` exports | ~5 |
| `prompts.py` placeholder additions | ~8 |
| `reviewer.py` wiring | ~40 |
| `app.py` fetcher construction | ~20 |
| **Total non-test** | **~353** |
| `tests/test_version_discovery.py` (12 tests) | ~200 |

---

## 12. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Noisy prior concerns degrade review | HIGH | Already-addressed reconciliation (§5) + max-5-concerns cap (§6.1) |
| False author-match from search | MEDIUM | Author email guard (§4.1 / §4.2) |
| Offline lore degrades gracefully | LOW | `fetch()` catches `LoreOfflineError`; review proceeds without prior context |
| Strategy C boundary violated | HIGH | Extended Strategy C test TS2A-12 (§7) |
| Stochastic confinement violation | MEDIUM | No nondeterministic calls in `version_discovery.py`; Sec. 40 test unchanged |
| Byte-determinism broken for existing tests | MEDIUM | Empty-string guard in `format_prior_version_context` (§6.2) preserves byte-identity |
| `In-Reply-To` cycle causes infinite loop | LOW | `max_depth=10` guard (§4.1) |

---

## 13. Open Questions for Readiness Review

1. **Max concerns cap**: is 5 the right limit? Kernel patch series can have v10+ with 50+ maintainer comments. Too many concerns may overwhelm the prompt context. Consider per-severity prioritization (all blockers + top-3 warnings).

2. **Version range**: how far back should we look? Fetching v1 when we are at v10 adds significant latency (10 `lore_manager.fetch()` calls). Proposed default: `max_prior_versions=3` (v(n-1), v(n-2), v(n-3)). Configurable via env var `KRI_PRIOR_VERSION_DEPTH`.

3. **H2 token extraction quality**: the 5-token heuristic extracts identifiers but misses natural-language concerns like "this function is too long". H2 will not fire for stylistic comments. Acceptable? Or should we add a sentence-similarity heuristic (e.g., ngram overlap)?

4. **Test fixturing**: TS2A-1..5 require mock lore data representing a 3-version series. Should we create a real 3-version fixture from a known kernel series (e.g., the RubikPi3 series used by other fixtures)? Or use entirely synthetic data?

5. **`series_prefix` interference**: R3 in WP-S1B writes to `InlineComment.series_prefix`. If a prior-version concern is about a cross-series dependency, the R3 annotation and the `Prior Version Feedback` block may say similar things. No dedup mechanism exists. Acceptable duplication?

---

## Appendix A: Key Existing Primitives

- `LoreManagerImpl.fetch(thread_id)` → `Thread` — network-cached, offline-safe
- `LoreManagerImpl.search(query)` → `list[str]` — message-ids from Atom feed, cached
- `LoreManagerImpl.extract_reviews(thread)` → `list[ReviewComment]`
- `LoreManagerImpl.parse_conversation(thread)` → `list[dict]` — In-Reply-To-ordered
- `PatchManagerImpl.parse(thread)` → `PatchSeries`
- `PatchManagerImpl.extract_versions(series)` → `list[int]` (current thread only)
- `format_series_context(ctx, patch_id)` → `str` in `kri/series/prompt.py`
- `ReviewComment.is_maintainer`, `.message`, `.author`, `.target_patch_id`
- `Message.in_reply_to`, `.is_cover_letter`, `.from_email` from `kri/lore_manager/mbox.py`
- `SubjectInfo.version` from `parse_subject()` in `mbox.py`
