# WP-S1 — Series-Aware Reasoning Engineer-Ready Implementation Spec

**Date:** 2026-07-21
**Status:** Design-only — no code, no commits at time of writing
**Prior artifacts:**
- `docs/POST_WP_CP1_QUALITY_GAP_REVIEW_2026-07-21.md` (RubikPi3 quality-gap review — requirements input)
- `docs/WP_S1_SERIES_AWARE_REASONING_2026-07-21.md` (architecture review — approved)

**Approved decisions carried forward:**
- **Pre-pass** `SeriesContextBuilder` — pure diff/string analysis, no LLM, no git, no kernel tree, no network
- **Post-pass** `SeriesReducer` — pure reducer over `(list[PatchReview], SeriesContext)`, deterministic
- **Strategy C metadata discipline** — `SeriesContext` is injected into prompts as *facts* only, never as decision constraints; reducer output is metadata + additive coupling notes only
- **Feature-flag entry** — `series_awareness: bool = True` on `IntelligentReviewEngine`; `False` reproduces pre-WP-S1 output byte-identically
- **Incremental delivery** — split into WP-S1A (builder) and WP-S1B (reducer); each lands independently with its own validation gate

Scope: `IntelligentReviewEngine` gains two new dependencies (`SeriesContextBuilder`, `SeriesReducer`) analogous to the `static_analysis` and `gate` injections. `RepositoryManagerImpl` is not modified. `apply_patch` / `checkout` are not called. `LLMClient` is not modified. Only the two agent prompts and one aggregation step in `reviewer.py` are touched.

---

## 1. Exact File Changes

### 1.1 New files

| File | Purpose | Public API introduced | Scope |
|---|---|---|---|
| `kri/series/__init__.py` | Package export surface | `SeriesContextBuilder`, `SeriesReducer`, `SeriesContext`, `SymbolRegistry`, `PatchIndexEntry`, `SeriesProvenance`, `ReducerAction`, `ReducerActionKind` | ~15 lines |
| `kri/series/models.py` | Frozen dataclasses for series-level types | See §2.1 (`SeriesContext`, `SymbolRegistry`, `PatchIndexEntry`, `SeriesProvenance`, `ReducerAction`) | ~110 lines |
| `kri/series/extractors.py` | Diff-and-string extractors for compatibles / DT properties / C symbols / added files / cover letters | `extract_compatibles(diff) -> set[str]`, `extract_dt_properties(diff) -> set[str]`, `extract_c_symbols(diff) -> set[str]`, `extract_added_files(diff) -> set[str]`, `extract_referenced_symbols(diff, symbols) -> set[str]`, `extract_containing_function(diff, line) -> str \| None` | ~180 lines |
| `kri/series/context.py` | `SeriesContextBuilder` (pre-pass) | `class SeriesContextBuilder: def build(series: PatchSeries) -> SeriesContext` | ~90 lines |
| `kri/series/reducer.py` | `SeriesReducer` (post-pass) + rule modules R1–R8 | `class SeriesReducer: def reduce(patch_reviews, series_ctx) -> ReducerOutcome`; `@dataclass ReducerOutcome` | ~260 lines |
| `kri/series/prompt.py` | `format_series_context(ctx, patch_id) -> str` — mirrors `format_static_findings()` discipline | one public function | ~50 lines |
| `tests/test_series_context.py` | WP-S1A unit tests (10 builder + extractor coverage) | — | ~280 lines |
| `tests/test_series_reducer.py` | WP-S1B unit tests (15 reducer rules) | — | ~380 lines |
| `tests/test_series_wiring.py` | Engine wiring + Strategy-C guard (7 wiring + 1 guard) | — | ~180 lines |
| `tests/test_series_regression_rubikpi.py` | Real-fixture regression (4 tests, replay `/tmp/rubikpi.mbox` fixture) | — | ~150 lines |
| `tests/fixtures/rubikpi.mbox` | Checked-in copy of the RubikPi3 v2 mbox for deterministic replay | — | 2116 lines, ~110 KB |

### 1.2 Modified files

| File | Change | Purpose |
|---|---|---|
| `kri/llm/reviewer.py` | Add `series_awareness`, `series_context_builder`, `series_reducer` params to `__init__`; call builder before agent pool, reducer after; thread `series_ctx` through `_review_patch` and to `format_series_context` for prompt injection; merge reducer's per-patch overrides back into `patch_reviews` before building `IntelligentReport`; extend report metadata with `series_context`, `series_suppressed`, `series_reducer_actions` | Wires the pre- and post-passes into the engine |
| `kri/llm/agents.py` | Extend `CodeQualityAgent.review()` and `SubsystemExpertAgent.review()` signatures to accept `series_context: str = ""`; inject at the `{series_context}` placeholder in each prompt template | Adds the new prompt-injection point |
| `kri/llm/prompts.py` | Add `{series_context}` placeholder to the code-quality prompt template and subsystem-expert prompt template; make `format_series_context` importable alongside `format_static_findings` | Prompt-template surface for series awareness |
| `kri/llm/models.py` | Add optional `series_provenance: SeriesProvenance \| None = None` to `InlineComment` | Per-finding provenance |
| `kri/web/app.py` | Import `SeriesContextBuilder` and `SeriesReducer` from `kri.series`; construct them in `intelligent_review` endpoint; pass to `IntelligentReviewEngine`; extend `renderIntelligent()` JS with series-level panel and per-patch coupling collapsible (see §7) | UI wiring |
| `tests/test_stochastic_confinement.py` | Update `_ALLOWED_CALLS["llm/reviewer.py"]` line numbers if the new `series_context_builder` / `series_reducer` params shift the `time.monotonic()` positions | Sec. 40 line-number drift guard |

### 1.3 No-touch files

| File | Rationale |
|---|---|
| `kri/repo_manager/manager.py` | WP-S1 does not touch git, checkout, apply, or worktrees |
| `kri/repo_manager/gate.py` | WP-T2A concern; WP-S1 is independent |
| `kri/static_analysis/manager.py` | Checkpatch runs as-is; WP-S1 uses its output only through `PatchReview.metadata["checkpatch_findings"]` |
| `kri/llm/client.py` | LLM client unchanged |
| `kri/common/models.py` | `Patch`, `PatchSeries` already carry `sequence`, `series_total`, `cover_letter`, `files_changed` — no schema change needed |
| `kri/common/diff_utils.py` | Existing diff helpers may be *reused* by extractors but the module is not modified |
| Any file under `kri/learning/` | Sec. 40 boundary — WP-S1 is deterministic, learning stays isolated |

---

## 2. Exact API Signatures

### 2.1 `kri/series/models.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class PatchIndexEntry:
    patch_id: str
    index: int                              # 1-based, matches "[PATCH v2 N/M]"
    total: int                              # M in "[PATCH v2 N/M]"
    subject: str
    files_changed: tuple[str, ...]


@dataclass(frozen=True)
class SymbolRegistry:
    """Symbols the series itself declares. Every value is the patch_id that
    introduced that symbol. Used by suppression rule R1."""

    compatibles: dict[str, str] = field(default_factory=dict)
    dt_properties: dict[str, str] = field(default_factory=dict)
    c_symbols: dict[str, str] = field(default_factory=dict)
    files_added: dict[str, str] = field(default_factory=dict)

    def declares_compatible(self, s: str) -> bool: ...
    def declares_dt_property(self, s: str) -> bool: ...
    def declares_c_symbol(self, s: str) -> bool: ...


@dataclass(frozen=True)
class SeriesContext:
    series_id: str
    title: str
    cover_letter: str | None
    total_patches: int
    patch_index: dict[str, PatchIndexEntry]        # keyed by patch_id
    declared_symbols: SymbolRegistry
    file_touch_map: dict[str, tuple[str, ...]]     # path -> ordered patch_ids

    def to_metadata(self) -> dict: ...
    def is_multi_patch(self) -> bool: ...


@dataclass(frozen=True)
class SeriesProvenance:
    """Attached to InlineComment when the reducer touched a finding.
    Enables audit-trail rendering without changing the human-readable message."""

    depends_on_patches: tuple[str, ...] = ()      # sibling patch_ids referenced
    absorbed_from: tuple[str, ...] = ()           # finding indices merged in
    suppressed_alternatives: tuple[str, ...] = ()


class ReducerActionKind(str, Enum):
    R1_DECLARED_SYMBOL_SUPPRESS = "declared_symbol_suppress"
    R2_SERIES_PRESENT_SUPPRESS = "series_present_suppress"
    R3_EXTERNAL_TO_INTERNAL_REWRITE = "external_to_internal_rewrite"
    R4_LINE_BUCKET_MERGE = "line_bucket_merge"
    R5_FUNCTION_SCOPE_MERGE = "function_scope_merge"
    R6_LOW_SIGNAL_SUPPRESS = "low_signal_suppress"
    R7_PRE_EXISTING_SUPPRESS = "pre_existing_suppress"
    R8_COUPLING_NOTE = "coupling_note"


@dataclass(frozen=True)
class ReducerAction:
    kind: ReducerActionKind
    patch_id: str
    finding_ref: str                              # "P{index}.F{index}" pointer
    file: str = ""
    line: int = 0
    reason: str = ""                              # human-readable rationale
    absorbed_refs: tuple[str, ...] = ()           # for merge actions
    related_patch_id: str = ""                    # for R3/R8

    def to_metadata(self) -> dict: ...


@dataclass(frozen=True)
class ReducerOutcome:
    patch_reviews: list           # list[PatchReview] — mutated copies, never the originals
    actions: tuple[ReducerAction, ...]
    suppressed_count: int
    merged_count: int
    coupling_note_count: int
```

### 2.2 `kri/series/extractors.py`

Pure functions. No I/O. No mutation of inputs. All return frozen collections.

```python
def extract_compatibles(diff: str) -> set[str]:
    """Return every new compatible string added by the diff.
    Handles two YAML forms:
        1. Inline enum:  - const: foo,bar-sndcard
        2. List item:    - foo,bar-sndcard   (under compatible:)
    Only counts additions (lines prefixed with '+', not context or removals)."""

def extract_dt_properties(diff: str) -> set[str]:
    """Return every new top-level property name added under 'properties:' or a
    parent property block in a YAML binding.  Distinguishes property additions
    from example / enum additions by requiring the line to sit under a
    'properties:' anchor."""

def extract_c_symbols(diff: str) -> set[str]:
    """Return every C function, struct, or macro *defined* (not just referenced)
    by additions in the diff.  Function definitions: '+<retval> name(' at column
    0 with an opening '{'.  Struct definitions: '+struct name {'.  Macro
    definitions: '+#define NAME'."""

def extract_added_files(diff: str) -> set[str]:
    """Return every path that appears in a 'diff --git a/... b/...' header
    whose 'new file mode' line is present (i.e. genuinely new files)."""

def extract_referenced_symbols(diff: str, symbols: set[str]) -> set[str]:
    """Return the subset of `symbols` that appear as identifiers in additions
    of `diff`.  Word-boundary match only; no substring matching.  Used by R8
    coupling annotation."""

def extract_containing_function(diff: str, target_line: int) -> str | None:
    """For a target line number in an added-hunk chunk, walk upward through the
    diff to find the nearest 'static <retval> name(' or '<retval> name('
    line.  Return the function name or None if not resolvable."""

def parse_series_index(subject: str) -> tuple[int, int] | None:
    """Parse '[PATCH v2 3/6]' -> (3, 6).  Return None when the header is not
    present or malformed.  Used only as a fallback when Patch.sequence /
    Patch.series_total are zero."""

def extract_cover_letter(series: PatchSeries) -> str | None:
    """Prefer series.cover_letter (already parsed).  If empty, look for a
    patch with sequence==0 and treat its commit_message as the cover.  Return
    None when neither source is populated.  Never fetches externally."""

def is_binary_patch(diff: str) -> bool:
    """True when the diff contains 'Binary files ... differ' or 'GIT binary
    patch'.  Extractors short-circuit on this."""
```

### 2.3 `kri/series/context.py`

```python
from kri.common.models import PatchSeries
from kri.series.models import (
    PatchIndexEntry, SeriesContext, SymbolRegistry,
)


class SeriesContextBuilder:
    """Pure diff-and-string analysis over a PatchSeries.  Deterministic.
    No LLM, no git, no kernel tree, no network."""

    def __init__(self) -> None: ...

    def build(self, series: PatchSeries) -> SeriesContext:
        """Return a fully-populated SeriesContext.  Guaranteed to succeed for
        any non-None PatchSeries; malformed diffs contribute empty extraction
        results but never raise."""
```

**Behavioural contract:**

- `build(series)` is a pure function of `series`.  Two invocations with the same input produce byte-equal `SeriesContext`.
- Series with `len(series.patches) <= 1` produce a `SeriesContext` with `total_patches == len(patches)` and empty `declared_symbols`.  The empty registry is intentional: `format_series_context` renders `""` in that case.
- Binary patches (per `is_binary_patch`) contribute an empty extraction result — no crash, no partial state.
- `patch_index` keys are `Patch.patch_id`; when duplicate `patch_id` values exist (malformed series), the first-seen entry wins and a deterministic warning is `logger.debug()`-logged (never `warning` — this is not an operator-facing anomaly).
- `file_touch_map` values are ordered tuples in the sequence patches appear in `series.patches`.  Deterministic.
- `cover_letter` is delegated to `extract_cover_letter(series)`; the builder does not construct or synthesize a cover letter.

### 2.4 `kri/series/reducer.py`

```python
from kri.llm.models import PatchReview
from kri.series.models import (
    ReducerAction, ReducerOutcome, SeriesContext,
)


class SeriesReducer:
    """Pure reducer over per-patch reviews and the series context.
    Deterministic. Never calls the LLM. Never mutates inputs."""

    def __init__(self, low_signal_confidence_threshold: float = 0.55) -> None:
        self._low_signal_threshold = low_signal_confidence_threshold

    def reduce(
        self,
        patch_reviews: list[PatchReview],
        series_ctx: SeriesContext,
    ) -> ReducerOutcome:
        """Apply rules R1..R8 in the order listed in §6.  Return a new list
        of PatchReview objects (deep copies with modifications) plus the
        audit trail of ReducerAction entries.  The original patch_reviews
        list is NOT mutated."""
```

**Behavioural contract:**

- `reduce()` is a pure function of `(patch_reviews, series_ctx)`.  Two invocations produce byte-equal `ReducerOutcome`.
- Rule application order is fixed and enumerated in §6.  Order matters: R3 rewrites before R4 merges, so a rewritten risk-area can be merged with a duplicate.
- No rule adds a new `InlineComment` from thin air.  R8 (coupling annotation) only appends to `overall_assessment` on an existing `PatchReview` — never synthesizes a finding.
- Every mutation is recorded as a `ReducerAction` in the outcome.  A test asserts `len(outcome.actions) == sum(counts)` (§6, R1–R8).
- The `low_signal_confidence_threshold` (default 0.55) is the only tunable in the entire pass.  Everything else is a rule identity.

### 2.5 `kri/series/prompt.py`

```python
from kri.series.models import SeriesContext


def format_series_context(ctx: SeriesContext, patch_id: str) -> str:
    """Render the series-context block for injection into a per-patch agent
    prompt.  Returns "" when:
        - ctx.total_patches <= 1
        - patch_id not in ctx.patch_index
    Otherwise returns a fixed-format string documented in §4.2 of the spec.
    The returned string never contains upstream trailer tokens (Reviewed-by:,
    Acked-by:, etc.) — enforcement via _TRAILER_RE assertion in tests."""
```

**Behavioural contract:**

- Deterministic string.  No timestamps, no random ordering, no runtime IDs.
- Single-patch series → `""`.  Bit-identical to pre-WP-S1 for those cases.
- Cover-letter body is truncated at 2000 characters (rendered with a `...` marker).  This cap is a fixed constant, not a config.

---

## 3. Incremental Delivery Plan

WP-S1 splits into two independently-landable work packages.  Each has its own value even if the other never lands.

### 3.1 WP-S1A — `SeriesContextBuilder` + prompt injection

**Deliverable:** builder + prompt injection.  No reducer.  Agents see series facts, but no post-pass runs.

**Files new:** `kri/series/{__init__,models,extractors,context,prompt}.py`, `tests/test_series_context.py`
**Files modified:** `kri/llm/reviewer.py`, `kri/llm/agents.py`, `kri/llm/prompts.py`, `kri/web/app.py`
**Not touched yet:** `kri/series/reducer.py`, `tests/test_series_reducer.py`

**Feature flag:** `series_awareness: bool = True` — set to `False` for pre-WP-S1 behavior.

**Expected effect on RubikPi3 replay:**  agents *may* self-correct some of the missing-binding findings once they see the declared-symbol registry in the prompt.  Expected reduction: ~2 of 3 false positives eliminated; the reducer will finish the job.  Deduplicate findings remain.

**Validation gate before proceeding to WP-S1B:**

1. All 10 builder unit tests pass (see §8.1).
2. All 7 wiring tests pass (see §8.2).
3. Sec. 40 stochastic-confinement suite still green.
4. Byte-identical output on single-patch fixtures (regression guard R3 in §8.4).
5. RubikPi3 replay produces **at most 2** findings with `no.*binding.*document` (down from 3).
6. Prompt trailer-scan (S1 in §8.3) passes.
7. No new agent time regression >10% on the RubikPi3 fixture (measured over 3 runs).

Only when all 7 gates are green does WP-S1B unlock.

### 3.2 WP-S1B — `SeriesReducer`

**Deliverable:** reducer + all suppression / dedup / coupling logic.

**Files new:** `kri/series/reducer.py`, `tests/test_series_reducer.py`, `tests/test_series_regression_rubikpi.py`, `tests/fixtures/rubikpi.mbox`
**Files modified:** `kri/llm/reviewer.py` (add reducer wiring + report-metadata additions), `kri/llm/models.py` (add `series_provenance` optional field), `kri/web/app.py` (series-level panel + per-patch coupling collapsible)

**Feature flag:** the same `series_awareness: bool = True` gates the reducer.  A finer flag `series_reducer_enabled: bool = True` is added under it so operators can enable the builder alone if the reducer misbehaves.

**Expected effect on RubikPi3 replay:**  ≤ 12 total findings (target 9), ≥ 1 coupling note.

**Validation gate before merging WP-S1B:**

1. All 15 reducer unit tests pass (§8.1).
2. All 4 regression tests pass (§8.4).
3. All 5 Constitution tests pass (§8.3).
4. Sec. 40 suite green.
5. RubikPi3 replay: `sum(len(pr.inline_comments) for pr in patches) <= 12`.
6. RubikPi3 replay: zero findings match `no.*binding.*document`, `submitted alone`, `not-yet-merged binding`.
7. RubikPi3 replay: ≥ 1 coupling note references patch 3's helpers from patch 5.
8. Single-patch fixture: byte-identical output vs `series_awareness=False`.

### 3.3 Why the split is safer

- **Blast radius isolation.**  Builder is read-only over the diff and produces additive prompt content.  Reducer *removes* content the agents produced — a much higher-stakes operation.  Landing them together conflates two very different failure modes.
- **Reversibility.**  WP-S1A is trivial to revert (delete the two prompt injection sites + the builder module).  WP-S1B may modify audit tooling / UI panels that downstream operators depend on; separating it makes rollback focused.
- **Independent measurement.**  With A landed but B not, we can measure how much the builder *alone* buys us.  If A gets 50% of the way there, B's cost/benefit needs re-justification.
- **Test parity.**  Builder tests do not depend on reducer tests and vice versa.  Splitting the packages allows the reducer test suite to grow without gating the builder release.

---

## 4. Prompt Integration

### 4.1 Exact insertion location

Two prompt templates are modified:

**CodeQualityAgent prompt** — insertion order:

```
{system_preamble}
{static_findings}     ← WP-CP1 checkpatch block, "" when empty
{series_context}      ← WP-S1A block, "" when single-patch series
{patch_metadata}      ← subject, author, files_changed
{diff}
{closing_instructions}
```

**SubsystemExpertAgent prompt** — insertion order:

```
{system_preamble}
{subsystem_pack}
{static_findings}
{series_context}      ← same placeholder, same rendered content
{patch_metadata}
{diff}
{closing_instructions}
```

**PatchSummarizerAgent** — **NOT modified.**  Summary is intentionally per-patch.  Adding series context would grow the prompt without measurable quality benefit.

### 4.2 Exact render rules

`format_series_context(ctx, patch_id)` returns one of two strings:

**When `ctx.total_patches <= 1` OR `patch_id not in ctx.patch_index`:**

```
""
```

The empty string is inserted verbatim.  The two surrounding newlines in the prompt template collapse to a single blank line — semantically indistinguishable from the pre-WP-S1 prompt.

**Otherwise, the block below is emitted.  Every field is a literal, no interpolation of runtime state beyond series content itself:**

```
## Series Context
This patch is part {index} of a {total}-patch series titled "{title}".

Other patches in this series introduce the following files/symbols. Treat
each item below as PRESENT in the series and do NOT flag any of them as
"missing binding", "missing helper", "not documented", or "external
dependency":

Files added:
  - {path} (patch {index}/{total} — {subject_first_60_chars})
  ... (deterministic order, sorted by patch index then path)

DT compatibles introduced:
  - {compatible} (patch {index}/{total})
  ...

DT properties introduced:
  - {property} (patch {index}/{total})
  ...

C symbols introduced:
  - {symbol} (patch {index}/{total})
  ...

Files touched by this same series (may indicate cross-patch coupling):
  - {path} — patches {index_list}
  ...

{cover_letter_section}
```

Where `{cover_letter_section}` is one of:

- Empty (when `ctx.cover_letter is None`)
- The literal block below (when `ctx.cover_letter` is populated):

```
Cover letter (verbatim, first {N} chars):
{cover_letter[:2000]}{"..." if len(ctx.cover_letter) > 2000 else ""}
```

Any section whose corresponding registry is empty is omitted entirely (including its heading line — no dangling headers).

### 4.3 Empty-string behaviour for single-patch reviews

- `series.patches` has length 1 → `ctx.total_patches == 1` → `format_series_context` returns `""`.
- The two agent prompts, after `"".join(...)`-style rendering, are byte-identical to their pre-WP-S1 form.
- This property is asserted in the wiring test `test_single_patch_prompt_unchanged` (§8.2, W3).

### 4.4 Byte-identical regression requirements

Two guarantees:

**G1 — Off-switch byte-identity.**  `IntelligentReviewEngine(series_awareness=False, ...).review(series)` produces byte-equal `IntelligentReport` to a WP-CP1-only engine on the same input, including `metadata` key order (relies on Python 3.7+ dict-insertion-order guarantee).

**G2 — Single-patch byte-identity.**  With `series_awareness=True` but `len(series.patches) == 1`, the two agent prompts are byte-equal to the pre-WP-S1 form.  Reducer is invoked but performs no work (no findings to dedupe within a single patch).  Report metadata gains only the fixed key `series_context` with `total_patches == 1` — surface change to the JSON blob only, not to the semantic review.

Both guarantees are enforced by dedicated tests (§8.2, W3 and W7).

---

## 5. Metadata Contracts

### 5.1 `IntelligentReport.metadata` additions

New keys, all optional (present only when `series_awareness=True`):

```python
{
    # ... existing keys (checkpatch_finding_count, processing_time_seconds, llm_model)
    "series_context": {
        "series_id": str,
        "title": str,
        "total_patches": int,
        "cover_letter_present": bool,
        "declared_compatibles": list[str],       # sorted
        "declared_dt_properties": list[str],     # sorted
        "declared_c_symbols": list[str],         # sorted, capped at 100 entries
        "files_added_count": int,
        "files_touched_by_multiple_patches": list[str],   # sorted
    },
    "series_suppressed": [
        {
            "patch_id": str,
            "finding_ref": str,                  # "P{i}.F{j}"
            "file": str,
            "line": int,
            "reason": str,                       # matches ReducerActionKind
            "related_patch_id": str,             # "" when not applicable
        },
        ...
    ],
    "series_reducer_actions": [
        {
            "kind": str,                         # ReducerActionKind value
            "patch_id": str,
            "finding_ref": str,
            "file": str,
            "line": int,
            "reason": str,
            "absorbed_refs": list[str],
            "related_patch_id": str,
        },
        ...
    ],
}
```

**Coexistence with existing keys:**

- `checkpatch_finding_count` (WP-CP1) — unchanged.  Note: the reducer never touches checkpatch findings; they are treated as an authoritative mechanical signal.
- `processing_time_seconds` — measured to include builder + agents + reducer time.  Stays a single scalar.
- `llm_model` — unchanged.

### 5.2 `PatchReview.metadata` additions

New keys, all optional:

```python
{
    # ... existing WP-CP1 key
    "checkpatch_findings": [...],
    # ... new WP-S1 keys
    "series_coupling": [
        {
            "consumes": str,                     # C symbol name
            "introduced_by_patch_id": str,
            "introduced_by_patch_index": int,
            "introduced_by_subject": str,
        },
        ...
    ],
    "series_index": {
        "index": int,
        "total": int,
    },
}
```

**Rules:**

- `series_coupling` is present only when R8 produced at least one note for this patch.  Absent otherwise.
- `series_index` is present when `total > 1`.  Absent for single-patch series.
- Neither key ever appears in the agent prompt (Strategy C).

### 5.3 `InlineComment.series_provenance`

Optional field on the Pydantic model:

```python
class InlineComment(BaseModel):
    # ... existing fields
    series_provenance: SeriesProvenance | None = None
```

Present only when the reducer touched the finding (merge target, rewrite target).  Absent otherwise.  Serialized via Pydantic's default JSON encoder.

### 5.4 Backward-compatibility guarantees

- WP-CP1 keys (`checkpatch_findings`, `checkpatch_finding_count`) untouched.  Reducer never edits them.
- Any consumer that iterates `pr.metadata.get("checkpatch_findings", [])` continues to work verbatim.
- Every new WP-S1 key is opt-in via presence.  A consumer that never reads them behaves as before.
- Dict key insertion order preserved.  Existing consumers that rely on iteration order (rare, but possible) still see WP-CP1 keys first.
- No renames.  No deletions.  No type changes of existing fields.

---

## 6. Reducer Rule Specification

Rules are applied in the fixed order R1 → R2 → R3 → R7 → R4 → R5 → R6 → R8.  The order is deliberate: suppress obvious false positives first, rewrite external-dep language, then run dedup passes, apply the low-signal filter, and finally add coupling notes.

Each rule is defined by a **trigger**, **suppression behaviour**, **metadata behaviour**, **audit-trail behaviour**, and **tests**.

### R1 — Declared-symbol suppression

**Trigger:**
- For each `InlineComment` on each `PatchReview`:
- Let `text = comment.message + " " + (comment.upstream_comment or "") + " " + (comment.reasoning or "")`.
- Let `text_lower = text.lower()`.
- Match ANY of these lowercase substrings: `"no corresponding yaml binding"`, `"no binding document"`, `"missing binding"`, `"not documented in a binding"`, `"undocumented compatible"`, `"compatible.*not documented"` (regex), `"binding schema is missing"`.
- Extract candidate symbols from `text` using the regex `[a-z0-9,._-]+,[a-z0-9,._-]+-sndcard` for compatibles and `[a-z][a-z0-9-]+,[a-z0-9_-]+` more generally.
- Trigger fires when ANY extracted candidate ∈ `series_ctx.declared_symbols.compatibles` OR `series_ctx.declared_symbols.dt_properties`.

**Suppression:** remove the comment from `patch_review.inline_comments`.

**Metadata:** append to `series_suppressed` with `reason = "declared_by_patch_{index}"` where the index is `series_ctx.patch_index[declaring_patch_id].index`.

**Audit-trail:** record `ReducerAction(kind=R1_DECLARED_SYMBOL_SUPPRESS, patch_id, finding_ref, file, line, related_patch_id=declaring_patch_id, reason=<matched substring>)`.

**Tests:** U11 (declared → suppress), U12 (undeclared → survive).

### R2 — Series-present suppression

**Trigger:**
- Same iteration.
- Match lowercase substring in `text_lower`: `"submitted alone"`, `"not accompanied by"`, `"reviewers usually want the full series"`, `"part of a larger series"`, `"is this patch part of a larger series"`.
- Trigger fires when at least one match AND `series_ctx.total_patches > 1`.

**Suppression:** remove.

**Metadata:** append to `series_suppressed` with `reason = "series_present"`.

**Audit-trail:** record `ReducerAction(kind=R2_SERIES_PRESENT_SUPPRESS, ...)`.

**Tests:** U13 (series > 1 → suppress), U14 (series == 1 → survive).

### R3 — External-dependency to internal rewrite

**Trigger:**
- Iterate over `patch_review.summary.risk_areas` (list of strings) AND over `comment.message`.
- Match: `"not-yet-merged"`, `"not-yet-applied"`, `"another patch"`, `"depends on a patch"`, `"external dependency"`.
- Look up candidate symbols/paths mentioned in the surrounding text; if any is in `series_ctx.declared_symbols` (any registry), the trigger fires.

**Suppression:** DO NOT suppress.  Rewrite in place.  For risk_areas, replace the string with:

```
Depends on patch {N}/{total} ({subject_first_60_chars}) in this same series.
```

For inline comments, prepend a single-line note to `comment.message`:

```
[Series-internal dependency — patch {N}/{total}]
```

**Metadata:** append to `series_reducer_actions` with `kind=R3_EXTERNAL_TO_INTERNAL_REWRITE`.

**Audit-trail:** as above; `related_patch_id` points to the declaring patch.

**Tests:** U15 (rewrite happens), U16 (no rewrite for external-to-series symbol).

### R7 — Pre-existing suppression

**Trigger:** ANY of `"pre-existing"`, `"predates the current patch"`, `"not introduced by this patch"`, `"not something to address here"` in `text_lower` AND `comment.severity == "info"`.

**Suppression:** remove.

**Metadata:** append to `series_suppressed` with `reason = "pre_existing"`.

**Audit-trail:** `ReducerAction(kind=R7_PRE_EXISTING_SUPPRESS, ...)`.

**Tests:** U21 (info + pre-existing → suppress), U22 (warning + pre-existing → survive — warnings are respected regardless of pre-existing framing).

### R4 — Line-bucket dedup

**Trigger:**
- Group `patch_review.inline_comments` by key `(file_path, line_number // 10)`.
- A group with `len >= 2` fires the rule.

**Suppression:**
- Keep the finding with the highest `confidence`; ties broken by lowest original index (stable order).
- For each dropped finding, append its `upstream_comment` (or `message` if `upstream_comment` is empty) as an additional line to the kept finding's `upstream_comment` prefixed by `"Related remark: "`.
- Set kept finding's `series_provenance.absorbed_from` to the tuple of dropped finding_refs.

**Metadata:** each drop appends to `series_reducer_actions` with `kind=R4_LINE_BUCKET_MERGE`, `absorbed_refs=(dropped_ref,)`.

**Audit-trail:** as above.

**Tests:** U17 (two findings at same anchor → merge), U18 (two findings 15 lines apart → don't merge under R4; may merge under R5).

### R5 — Function-scope dedup

**Trigger:**
- For each patch, for each pair of remaining inline_comments in the same file:
  - Compute `fn_a = extract_containing_function(diff, comment_a.line_number)`.
  - Compute `fn_b = extract_containing_function(diff, comment_b.line_number)`.
  - If both non-None and equal, compute token-overlap.  Tokenize each `message + upstream_comment` on whitespace, lowercase, keep tokens with length ≥ 4.
  - Jaccard overlap ≥ 0.35 AND shared token count ≥ 3 → trigger fires.

**Suppression:** same merge behaviour as R4.

**Metadata:** each drop appends `kind=R5_FUNCTION_SCOPE_MERGE`.

**Audit-trail:** as above.

**Tests:** U19 (two findings same function, related text → merge), U20 (two findings same function, unrelated text → don't merge).

### R6 — Low-signal suppression

**Trigger:**
- `comment.confidence < self._low_signal_threshold` (default 0.55)
- AND `comment.category` in `{"convention", "nit", "style"}` (case-insensitive)
- AND `comment.severity == "info"`.

**Suppression:** remove.

**Metadata:** append to `series_suppressed` with `reason = "low_signal"`.

**Audit-trail:** `ReducerAction(kind=R6_LOW_SIGNAL_SUPPRESS, ...)`.

**Tests:** U23 (below threshold → suppress), U24 (at threshold → survive — strict `<` comparison), U25 (below threshold but severity=warning → survive).

### R8 — Coupling annotation (additive, non-suppressive)

**Trigger:**
- For each ordered pair `(patch_a, patch_b)` with `index_a < index_b` in the same series:
  - Let `syms = series_ctx.declared_symbols.c_symbols` restricted to values `== patch_a.patch_id`.
  - Let `consumed = extract_referenced_symbols(patch_b.diff, syms.keys())`.
  - Fires if `consumed` is non-empty AND patch_b's inline_comments do NOT already reference any symbol in `consumed`.

**Suppression:** none.  R8 only adds content.

**Behaviour:**
- For each `sym` in `consumed`: append to `patch_b_review.metadata["series_coupling"]` a dict `{"consumes": sym, "introduced_by_patch_id": patch_a.patch_id, "introduced_by_patch_index": index_a, "introduced_by_subject": patch_a.subject[:60]}`.
- Append a single line to `patch_b_review.overall_assessment`:
  ```
  Series coupling: consumes {sym_list} introduced in patch {index_a}/{total} ({subject_first_60_chars}).
  ```

**Metadata:** the additions above are the metadata.

**Audit-trail:** `ReducerAction(kind=R8_COUPLING_NOTE, patch_id=patch_b.patch_id, finding_ref="", related_patch_id=patch_a.patch_id, reason=<comma-joined sym list>)`.

**Tests:** U26 (helper introduced in A, used in B → note added), U27 (helper introduced but not used → no note), U28 (helper introduced and B already discusses it → no note — respect the agent's own discussion).

### 6.1 Rule interaction guarantees

- Rules R1, R2, R6, R7 are **suppressive** — they remove findings.  A suppressed finding does not participate in R3–R5.
- Rule R3 **rewrites in place** — it does not remove, but it may change the text that R4/R5 later compare.
- Rules R4 and R5 are **merging** — the kept finding's provenance points to the merged ones.
- Rule R8 is **additive** — it never removes, only annotates.  It runs last so its output reflects the reducer's final state, not the pre-reduction agents' output.

**Determinism guard:** all rules iterate over inputs in `sorted()` order (patches by `sequence` then `patch_id`; findings by original index).  No `dict.keys()` order dependency, no `set` iteration dependency.  A test (U29) asserts determinism.

**No-new-findings invariant:** the reducer never appends to `inline_comments`.  Only R3 modifies existing `message` text; R4/R5 modify `upstream_comment` and `series_provenance`; R8 modifies `overall_assessment` and `metadata`.  An invariant test (U30) asserts this.

---

## 7. UI Specification

### 7.1 Report-level rendering

Immediately above the existing WP-CP1 metadata strip in `renderIntelligent()`:

```
Series: {total_patches} patches | Cover letter: {yes|no} | Compatibles: {n} | DT properties: {n} | C symbols: {n}
▸ Series-aware suppressions ({count})   ← <details> collapsible
▸ Series reducer actions ({count})      ← <details> collapsible (audit trail)
```

Visibility rule: the entire block is rendered only when `report.metadata.series_context` is present AND `series_context.total_patches > 1`.  Absent for single-patch series (byte-identical to WP-CP1 output).

The two collapsibles are separate.  Each contains a table with one row per action:

**Suppressions table (columns):** Patch · File:Line · Reason · Original message (first 120 chars).
**Reducer actions table (columns):** Kind · Patch · File:Line · Related patch · Reason.

Both tables use the same monospace styling as the checkpatch findings block.

### 7.2 Per-patch rendering

Order inside each patch card (top to bottom):

1. Subject line (existing)
2. Summary block (existing)
3. **Series coupling collapsible (NEW WP-S1)** — rendered only when `pr.metadata.series_coupling` is present
4. Apply status badge (WP-T2A, if enabled)
5. Checkpatch findings collapsible (WP-CP1)
6. Inline comments (existing)
7. Lore reply (existing)

### 7.3 Series coupling collapsible

Format:

```
▸ Series coupling ({count})
   ┌─ consumes qcom_snd_headset_jack_setup
   │    introduced in patch 3/6 (ASoC: qcom: common: Add generic headset ...)
   ┌─ consumes qcom_snd_headset_jack_cleanup
   │    introduced in patch 3/6 (ASoC: qcom: common: Add generic headset ...)
   └─
```

Rendered from `pr.metadata.series_coupling` list.  Non-expandable by default (short list); the details wrapper is for UI consistency with checkpatch findings.

Colour scheme (matches checkpatch severity idiom):
- Coupling notes use a neutral blue border-left `#3498db` (same as `info` severity in checkpatch findings).
- No red/orange — coupling is informational, not a finding.

### 7.4 Placement relative to existing sections

| Section | Position relative to WP-S1 elements |
|---|---|
| Metadata strip (report top) | Series-level panel appears **above** the strip |
| Summary (per-patch) | Coupling collapsible appears **immediately after** Summary |
| Checkpatch Findings (WP-CP1) | Coupling collapsible appears **above** Checkpatch Findings |
| Issues Found (existing agent output) | Coupling appears **above**; suppressed findings are removed from the list entirely |
| Lore Reply (existing) | Unchanged; coupling never affects this |

### 7.5 Visibility rules summary

| Element | Visible when |
|---|---|
| Report-level series panel | `report.metadata.series_context.total_patches > 1` |
| Suppressions collapsible | `len(report.metadata.series_suppressed) > 0` |
| Reducer actions collapsible | `len(report.metadata.series_reducer_actions) > 0` |
| Per-patch coupling collapsible | `pr.metadata.series_coupling` present and non-empty |

All rules use `&&` on truthy JS checks — no falsy-empty-object edge cases.

---

## 8. Test Plan

Structure exactly mirrors WP-T2A (`test_applicability_gate.py`): 1. unit → 2. wiring → 3. constitution → 4. regression.

### 8.1 Unit tests

**8.1.1 Builder + extractors — `tests/test_series_context.py` (10 tests)**

| # | Test | Assertion |
|---|---|---|
| U1 | `test_build_single_patch_empty_registry` | 1-patch series → `total_patches == 1`, `declared_symbols` all empty, `cover_letter is None` if not set |
| U2 | `test_build_multi_patch_indexes_correctly` | 6-patch RubikPi3 fixture → 6 `PatchIndexEntry` values, all with `total == 6`, indices 1..6 |
| U3 | `test_extract_compatible_from_yaml_diff` | Diff adding `+  - thundercomm,qcs6490-rubikpi3-sndcard` → `extract_compatibles` returns `{"thundercomm,qcs6490-rubikpi3-sndcard"}` |
| U4 | `test_extract_dt_property_from_yaml_diff` | Diff adding `+  everest,jack-detect-inverted:` under `properties:` → `extract_dt_properties` returns `{"everest,jack-detect-inverted"}` |
| U5 | `test_extract_c_symbol_from_c_diff` | Diff adding `+void qcom_snd_headset_jack_setup(...)` with body → `extract_c_symbols` returns `{"qcom_snd_headset_jack_setup"}` |
| U6 | `test_file_touch_map_records_all_writers` | File touched by patches 4 and 5 → `file_touch_map[path] == ("p4", "p5")` |
| U7 | `test_cover_letter_from_series_field` | `series.cover_letter = "hello"` → `ctx.cover_letter == "hello"` |
| U8 | `test_no_cover_letter_returns_none` | Empty `series.cover_letter` AND no seq-0 patch → `ctx.cover_letter is None` |
| U9 | `test_build_is_deterministic` | `SeriesContextBuilder().build(series)` called twice → byte-equal outputs (Sec. 40 property) |
| U10 | `test_binary_patch_skipped_gracefully` | Series containing `Binary files differ` patch → no crash; that patch's symbols are empty |

**8.1.2 Reducer — `tests/test_series_reducer.py` (15 tests)**

| # | Test | Assertion |
|---|---|---|
| U11 | `test_R1_declared_compatible_suppresses` | Finding cites `foo,bar-sndcard`, registry has it → suppressed, action recorded |
| U12 | `test_R1_undeclared_compatible_survives` | Registry empty → finding kept |
| U13 | `test_R2_series_present_suppresses_alone_finding` | `total_patches > 1` + `submitted alone` in text → suppressed |
| U14 | `test_R2_only_one_patch_finding_survives` | `total_patches == 1` → finding kept |
| U15 | `test_R3_external_dep_rewritten_to_internal` | Risk area `not-yet-merged binding` → rewritten to cite sibling |
| U16 | `test_R3_no_rewrite_for_truly_external_symbol` | Symbol not in registry → risk area unchanged |
| U17 | `test_R4_line_bucket_merges_findings_at_same_anchor` | Two findings at `common.c:233` → kept one has both `upstream_comment` values |
| U18 | `test_R4_findings_15_lines_apart_not_merged_by_R4` | Findings at `common.c:220` and `common.c:235` → separate buckets, both kept |
| U19 | `test_R5_function_scope_merges_across_line_offsets` | Findings at `:284` and `:292` inside `sc8280xp_snd_exit` → merged |
| U20 | `test_R5_ignores_low_overlap_findings` | Two findings in same function with disjoint content → both kept |
| U21 | `test_R7_pre_existing_info_suppressed` | `severity=info`, text contains `pre-existing` → suppressed |
| U22 | `test_R7_pre_existing_warning_survives` | `severity=warning`, text contains `pre-existing` → kept |
| U23 | `test_R6_low_confidence_nit_suppressed` | `confidence=0.5, category=convention, severity=info` → suppressed |
| U24 | `test_R6_at_threshold_survives` | `confidence=0.55` (default threshold) → kept (strict `<`) |
| U25 | `test_R6_warning_severity_ignored` | `confidence=0.5, category=convention, severity=warning` → kept |
| U26 | `test_R8_coupling_note_appended` | Patch A introduces `foo()`, patch B uses `foo()` → coupling note on B |
| U27 | `test_R8_no_note_when_symbol_unused` | Patch A introduces `foo()`, patch B does not reference it → no note |
| U28 | `test_R8_no_note_when_already_discussed` | Patch B already has a finding referencing `foo()` → no coupling note |
| U29 | `test_reducer_is_deterministic` | `reduce()` called twice on same input → byte-equal outcome |
| U30 | `test_reducer_never_adds_findings` | For every patch, `len(after.inline_comments) <= len(before.inline_comments)` |
| U31 | `test_reducer_records_all_actions_in_metadata` | Every suppression / merge / rewrite / coupling has an entry in `outcome.actions` |

### 8.2 Wiring tests — `tests/test_series_wiring.py` (7 tests)

| # | Test | Assertion |
|---|---|---|
| W1 | `test_engine_calls_series_context_builder_once_per_review` | Builder invoked exactly once per `engine.review(series)` call |
| W2 | `test_series_context_threaded_to_all_review_patch_calls` | Every `_review_patch` receives the *same* `SeriesContext` instance |
| W3 | `test_single_patch_prompt_unchanged` | With `series_awareness=True` but `len(patches)==1`, the two agent prompts are byte-equal to WP-CP1 form (G2 in §4.4) |
| W4 | `test_reducer_runs_after_all_agents_complete` | Ordering assertion: all agent calls return before `reduce()` is invoked |
| W5 | `test_report_metadata_contains_series_context` | `report.metadata["series_context"]["total_patches"]` matches series size |
| W6 | `test_report_metadata_contains_suppression_list` | Suppression audit list present when at least one R1..R7 fires |
| W7 | `test_series_awareness_off_byte_identical_to_wp_cp1` | With `series_awareness=False`, output byte-equal to a WP-CP1-only engine (G1 in §4.4) |

### 8.3 Constitution / safety tests (5 tests, in `tests/test_series_wiring.py`)

| # | Test | Assertion |
|---|---|---|
| S1 | `test_series_context_never_synthesizes_trailers` | Rendered `{series_context}` string does not match `_TRAILER_RE` from `sanitize.py` |
| S2 | `test_builder_is_stochastic_confinement_compliant` | AST scan of `kri/series/context.py`, `extractors.py`, `models.py`, `prompt.py` finds no denylisted `time.*` / `random.*` / `datetime.*` / `uuid.*` / `secrets.*` — extends the existing `test_stochastic_confinement` walker |
| S3 | `test_reducer_never_calls_llm` | Grep `kri/series/reducer.py` for `client.complete`, `complete_json`, `LLMClient` → zero matches |
| S4 | `test_no_external_fetches_in_pre_or_post_pass` | Grep `kri/series/*.py` for `requests`, `urllib`, `git.`, `subprocess`, `os.system` → zero matches |
| S5 | `test_reducer_output_not_reinjected_into_prompts` | Configure a reducer to emit a distinctive marker string; mock `client.complete_json` to record every prompt; assert marker appears in NO recorded prompt (Strategy C lock-in, mirror of WP-T2A's test 24) |

### 8.4 Regression tests — `tests/test_series_regression_rubikpi.py` (4 tests)

Uses `tests/fixtures/rubikpi.mbox` as a checked-in fixture.  Every test loads the mbox deterministically (no network, no `time.now()`).

| # | Test | Assertion |
|---|---|---|
| R1 | `test_rubikpi3_zero_missing_binding_findings` | After full engine run with `series_awareness=True`, iterate every `pr.inline_comments`; assert none of `message + upstream_comment + reasoning` matches regex `no.*binding.*document|missing binding|not documented in a binding` |
| R2 | `test_rubikpi3_zero_submitted_alone_findings` | Same iteration; assert no match for `submitted alone|part of a larger series|not accompanied by` |
| R3 | `test_rubikpi3_total_findings_le_12` | `sum(len(pr.inline_comments) for pr in report.patches) <= 12` |
| R4 | `test_rubikpi3_at_least_one_coupling_note` | At least one `PatchReview.metadata["series_coupling"]` present; ≥ 1 note references a `qcom_snd_headset_jack_*` symbol introduced by patch 3 |

**Test total: 30 unit (U1..U31 minus placeholder gaps) + 7 wiring + 5 constitution + 4 regression = 46 tests.**  Well above the 31+ target.

### 8.5 Stochastic confinement update

If `series_context_builder` and `series_reducer` params shift `time.monotonic()` positions in `reviewer.py`, run:

```
grep -n "time.monotonic" kri/kri/llm/reviewer.py
```

Update `_ALLOWED_CALLS["llm/reviewer.py"]` with the new line pair.  The existing `test_allowlist_entries_still_point_at_denylisted_calls` test enforces staleness detection.

---

## 9. Success Criteria

All criteria are measurable and testable.  A WP-S1 rollout is successful only when **every** criterion passes.

### 9.1 Correctness

| # | Criterion | Test |
|---|---|---|
| C1 | Zero false-positive "missing binding" findings on RubikPi3 replay | R1 |
| C2 | Zero false-positive "submitted alone" findings on RubikPi3 replay | R2 |
| C3 | RubikPi3 replay total inline_comments ≤ 12 | R3 |
| C4 | RubikPi3 replay produces ≥ 1 coupling note pointing at patch 3's helpers from patch 5 | R4 |
| C5 | Every reducer suppression / merge is recorded in `series_reducer_actions` — no silent removal | U31 |

### 9.2 Regression

| # | Criterion | Test |
|---|---|---|
| C6 | `series_awareness=False` produces byte-equal `IntelligentReport` to WP-CP1-only engine on any input | W7 |
| C7 | `series_awareness=True` with single-patch series produces byte-equal *agent prompts* to WP-CP1-only engine | W3 |
| C8 | WP-CP1 keys (`checkpatch_findings`, `checkpatch_finding_count`) unchanged in shape and semantics | wiring assertion inside W3/W5 |

### 9.3 Determinism (Constitution Sec. 40)

| # | Criterion | Test |
|---|---|---|
| C9 | Builder `build(series)` returns byte-equal output on two invocations | U9 |
| C10 | Reducer `reduce(...)` returns byte-equal output on two invocations | U29 |
| C11 | No new denylisted imports/calls in `kri/series/**` | S2 |
| C12 | Stochastic confinement suite green | existing suite |

### 9.4 Safety

| # | Criterion | Test |
|---|---|---|
| C13 | No upstream trailer synthesis in `{series_context}` render | S1 |
| C14 | Reducer output never re-injected into agent prompts (Strategy C) | S5 |
| C15 | No LLM call inside `kri/series/reducer.py` | S3 |
| C16 | No external fetch inside `kri/series/**` | S4 |

### 9.5 Performance

| # | Criterion | Test |
|---|---|---|
| C17 | Builder + reducer add < 500 ms to a 6-patch review on the RubikPi3 fixture (median over 5 runs) | operator smoke test, documented in commit message |
| C18 | No new agent time regression > 10 % on RubikPi3 fixture | operator smoke test |

### 9.6 Fail-fast markers

If ANY of the following occurs during rollout, revert:

- A test in category 8.1/8.2/8.3 fails on `main` after merge
- Sec. 40 suite reports a violation
- `KRI_SERIES_AWARENESS=0` env override does not reproduce pre-WP-S1 output
- A production intelligent-review request produces a prompt containing content from `series_suppressed` list (Strategy C violation)

---

## 10. Risks

### 10.1 Builder risks

| Risk | Impact | Mitigation | Validation gate |
|---|---|---|---|
| Regex-based extractors miss unusual YAML formats | False negatives in `declared_symbols`; R1 fails to suppress a genuine false-positive finding | Ship extractors with a corpus of ≥20 real-world binding-add diffs from `data/lore_cache/`; add unit tests U3/U4/U5 that assert both positive and negative extraction | U3/U4/U5 pass |
| Diff parsing crashes on malformed patch | Builder raises → engine crashes | Every extractor short-circuits on binary/unparseable input; builder wraps extractors in per-patch try/except at `logger.debug` level | U10 passes |
| Very long diffs cause quadratic regex behaviour | Latency spike | Extractors use precompiled regexes with linear anchors (no `.*.*`); U9 determinism test doubles as a smoke test | C17 latency budget |
| `Patch.sequence` is 0 for a legitimately non-cover patch | Wrong index in `PatchIndexEntry` | Fall back to `parse_series_index(subject)` regex; when both fail, use position in `series.patches` as a last-resort index | U2 (indices correct on RubikPi3 fixture) |

### 10.2 Reducer risks

| Risk | Impact | Mitigation | Validation gate |
|---|---|---|---|
| Over-suppression removes a genuine finding | Loss of review signal | R1/R2 only fire when substring AND symbol-registry match both hold; R6 threshold is a config value; `series_suppressed` is a first-class UI panel so operator sees what was removed and can raise the threshold | R1..R4 pass + operator review of `series_suppressed` panel |
| Under-suppression leaves duplicates | Noise unchanged | Rules R4/R5 are complementary (line-bucket + function-scope); regression test R3 gates ≤12 findings | R3 passes |
| Merge order changes finding semantics | Confusing kept-message | Merge preserves the highest-confidence finding's `message` verbatim; only `upstream_comment` gets appended with a clear `"Related remark:"` prefix | U17/U19 assertions |
| R3 rewrite corrupts prose | Reduces reviewer trust | R3 uses a fixed template with clear provenance; never generates free-form rewrites | U15 asserts exact output format |
| Non-determinism from set iteration | Sec. 40 violation | Every internal iteration uses `sorted()`; U29 asserts byte-identical output on rerun | C10, C9 |

### 10.3 Prompt bloat risks

| Risk | Impact | Mitigation | Validation gate |
|---|---|---|---|
| `{series_context}` inflates the prompt beyond LLM context window | Truncation, degraded quality | Cover letter capped at 2000 chars; `declared_symbols` capped at 100 entries per registry in the rendered block (though not in metadata) | Prompt-size assertion in wiring tests |
| Injected symbols distract the agent from the actual diff | Lower-quality inline comments | The injection block is a fixed structured format; agents are instructed to "treat as PRESENT" — deterministic framing; operator A/B test between `series_awareness=True/False` on the same fixture | Compare quality between R3 result and pre-WP-S1 baseline; regression must show fewer, not more, false positives |
| Series title / cover letter contain a trailer-like string | Trailer-synthesis risk | `_TRAILER_RE` scan runs against the rendered block (test S1) | S1 passes |

### 10.4 Suppression overreach risks

| Risk | Impact | Mitigation | Validation gate |
|---|---|---|---|
| A genuinely missing binding gets suppressed because a *different* binding is in the registry | False negative on a real bug | R1 extracts candidate symbols and matches against the exact symbol in the registry; substring collisions blocked by word-boundary regex | U11/U12 |
| A finding that says "not accompanied by X" where X is a specific requirement, gets suppressed by R2 | Loss of real signal | R2 only fires on generic "submitted alone" / "part of a larger series" phrasing; specific asks are unaffected because they don't match the enumerated substrings | U13 negative case + operator review |
| R6 threshold too aggressive on future series | Suppresses legit `info` findings | Threshold is exposed via `SeriesReducer(low_signal_confidence_threshold=...)`; deployment can raise it | Operator override |
| Reducer becomes the source of truth for what counts as a "real" finding | Erosion of agent independence | Reducer never generates findings; it only removes / merges / annotates.  U30 invariant test enforces this. | U30 |

### 10.5 Regression risks

| Risk | Impact | Mitigation | Validation gate |
|---|---|---|---|
| Single-patch series output changes | Breaks existing behaviour | G2 byte-identity guarantee + W3 test | W3 passes |
| `series_awareness=False` no longer reproduces WP-CP1 output | Feature flag broken | G1 byte-identity guarantee + W7 test | W7 passes |
| Metadata key order shift breaks downstream consumers | Serialization drift | Insert new keys at the end of the metadata dict, preserving WP-CP1 keys first | W5 assertion |
| `time.monotonic()` line drift breaks Sec. 40 allowlist | CI fail on unrelated PR | Update `_ALLOWED_CALLS` in the same PR as `reviewer.py` changes; existing `test_allowlist_entries_still_point_at_denylisted_calls` catches drift | Sec. 40 suite green |

---

## 11. Implementation Order

Suggested commit sequence.  Each commit is independently reviewable and passes its own subset of the test suite.

### Step 1 — `kri/series/models.py` + `kri/series/__init__.py` skeleton

- Create both files with the dataclass definitions from §2.1 and empty exports.
- No wiring changes yet.  Purely additive.

Commit boundary #1: `kri/series/models.py`, `kri/series/__init__.py`.

### Step 2 — `kri/series/extractors.py` + extractor unit tests

- Implement the seven extraction helpers from §2.2.
- Add tests U3, U4, U5, U10 (extractor-focused subset of §8.1.1).

Commit boundary #2: adds `kri/series/extractors.py`, adds extractor tests in `tests/test_series_context.py`.

### Step 3 — `kri/series/context.py` + full builder unit tests

- Implement `SeriesContextBuilder.build()` using extractors from Step 2.
- Add tests U1, U2, U6, U7, U8, U9 (builder-level).
- Sec. 40 stochastic-confinement scan validates this module.

Commit boundary #3: adds `kri/series/context.py`, completes `tests/test_series_context.py`.

### Step 4 — `kri/series/prompt.py` + `format_series_context`

- Implement `format_series_context` per §4.2 rules.
- No wiring into `agents.py` yet.  Test independently by rendering fixtures.
- Add tests: rendering shape, empty-string behaviour, cover-letter cap, trailer-scan.

Commit boundary #4: adds `kri/series/prompt.py`, adds `tests/test_series_prompt.py`.

### Step 5 — Wire builder + prompt into `reviewer.py` and agents (WP-S1A complete)

- Modify `IntelligentReviewEngine.__init__` to accept `series_awareness`, `series_context_builder` params.
- Call `builder.build(series)` at the top of `review()`.
- Thread `series_ctx` through `_review_patch`.
- Extend `CodeQualityAgent.review` and `SubsystemExpertAgent.review` signatures.
- Add `{series_context}` placeholder to the two prompt templates.
- Update `kri/web/app.py` to construct the builder and inject it.
- Add wiring tests W1, W2, W3 (byte-identity single-patch), W7 (byte-identity flag off).
- Update `_ALLOWED_CALLS` line numbers.

Commit boundary #5: **WP-S1A merges here.**  All validation gates in §3.1 must pass.

### Step 6 — Baseline measurement + WP-S1A validation gate

- Run RubikPi3 fixture with WP-S1A landed and reducer NOT yet present.  Record the finding count.
- Verify: expected ~1–2 fewer false-positive "missing binding" findings than the pre-WP-S1 baseline.
- If the WP-S1A validation gate passes, unlock WP-S1B work; otherwise fix issues before proceeding.

Commit boundary #6: none (measurement only, no code changes).

### Step 7 — `kri/series/reducer.py` skeleton + rule R1

- Create `SeriesReducer` class and `ReducerOutcome` dataclass.
- Implement rule R1 (declared-symbol suppression) only.
- Add unit tests U11, U12.

Commit boundary #7: `kri/series/reducer.py` initial scaffold + R1.

### Step 8 — Rules R2, R7 (simple suppressions)

- Implement R2 (series-present suppression) and R7 (pre-existing suppression).
- Add unit tests U13, U14, U21, U22.

Commit boundary #8.

### Step 9 — Rule R3 (external-to-internal rewrite)

- Implement in-place rewrite for risk_areas and message prefixes.
- Add unit tests U15, U16.

Commit boundary #9.

### Step 10 — Rules R4, R5 (dedup)

- Implement line-bucket and function-scope dedup.
- Add unit tests U17, U18, U19, U20.

Commit boundary #10.

### Step 11 — Rule R6 (low-signal suppression)

- Implement confidence + category + severity gate.
- Add unit tests U23, U24, U25.

Commit boundary #11.

### Step 12 — Rule R8 (coupling annotation)

- Implement coupling detection and annotation.
- Add unit tests U26, U27, U28.

Commit boundary #12.

### Step 13 — Determinism / invariant tests

- Add U29 (reducer determinism), U30 (no new findings invariant), U31 (audit trail completeness).

Commit boundary #13.

### Step 14 — Wire reducer into `reviewer.py` (WP-S1B complete)

- Call `reducer.reduce(...)` after all `_review_patch` calls complete.
- Merge reducer outcome into `patch_reviews` list.
- Extend `IntelligentReport.metadata` with `series_context`, `series_suppressed`, `series_reducer_actions`.
- Extend `PatchReview.metadata` with `series_coupling`, `series_index`.
- Add `series_provenance: SeriesProvenance | None` field to `InlineComment`.
- Add wiring tests W4, W5, W6.
- Update `_ALLOWED_CALLS` line numbers if `time.monotonic` shifted.

Commit boundary #14.

### Step 15 — UI wiring

- Add series-level panel to `renderIntelligent()`.
- Add per-patch coupling collapsible.
- Add suppressions / reducer-actions tables.
- Test manually via live intelligent-review request.

Commit boundary #15.

### Step 16 — Constitution guards

- Add tests S1..S5.
- Verify Sec. 40 suite still green.

Commit boundary #16.

### Step 17 — RubikPi3 regression tests

- Copy `/tmp/rubikpi.mbox` into `tests/fixtures/rubikpi.mbox`.
- Add tests R1..R4.
- Verify all 4 pass on the current implementation.

Commit boundary #17: **WP-S1B merges here.**  All validation gates in §3.2 must pass.

### Step 18 — Documentation update

- Update `CLAUDE.md` if it exists with the new `KRI_SERIES_AWARENESS` env var and the metadata schema additions.
- Add operator note in `docs/` describing how to raise the `low_signal_confidence_threshold` if suppression overreach is observed.

Commit boundary #18.

---

## Summary of Constraints Preserved

- No LLM prompt sees reducer output (Strategy C, enforced by test S5).
- No `RepositoryManagerImpl` API modified.  No git call.  No network call.  No kernel-tree dependency.
- No trailer synthesis anywhere (`_TRAILER_RE` scan on rendered block via test S1).
- Sec. 40 stochastic confinement extended cleanly — every new module scanned; allowlist unchanged in shape.
- WP-CP1 metadata keys untouched.  Existing consumers continue to work.
- Feature flag off → byte-identical to WP-CP1.  Single-patch series → byte-identical to WP-CP1.
- Determinism: builder and reducer are pure functions of their inputs; enforced by U9 + U29.
- Diff-and-string analysis only.  No git, no LLM in builder or reducer.

Ready for engineer pickup.  46 tests enumerated; no ambiguity on rule triggers, metadata keys, UI copy, or commit boundaries.  WP-S1A and WP-S1B split cleanly; each landing independently is safe and measurable.
