# KRI — Authoritative Implementation Specification (SPEC.md)

**Status:** Frozen core (v0.1.0). **Owner:** Architect. **Audience:** builder agents.
**Source of truth:** `KRI_Architecture_Blueprint_and_Engineering_Constitution.pdf`
(Blueprint + Constitution), `KRI_Engineering_Intelligence_Packages.pdf` (ASoC),
`KRI_Skill_Engineering_Framework.pdf`.

This document is normative. Where it uses **MUST / MUST NOT / SHOULD** it is a
testable requirement. Section references like "Sec. 21.7" point at the Blueprint /
Constitution. If this SPEC and a builder's intuition disagree, the SPEC wins;
raise an architectural-review request rather than diverging.

---

## 0. Table of contents

1. Tech stack + rationale
2. Frozen core: data model (`kri/common/models.py`)
3. Frozen core: runtime interfaces (`kri/common/interfaces.py`)
4. Engineering Knowledge Graph (EKG) schema
5. DKP contract: manifest schema + Python entry-point convention
6. Confidence Engine factor model
7. Reasoning hierarchy + Cognition Layer artifact flow
8. Data-flow contracts between modules
9. Per-sprint module ownership map
10. Constitutional compliance — testable assertions
11. Definition-of-Done checklist per module
12. Open questions / ambiguities in the source docs

---

## 1. Tech stack + rationale

| Concern | Decision | Rationale |
|---|---|---|
| Language | **Python 3.10+** | Blueprint reference language; rich kernel-tooling & email/mbox ecosystem; `match`, PEP 604 unions. Pinned `requires-python = ">=3.10"`. |
| Data modeling | **Pydantic v2** (`>=2.6,<3.0`) | Validation + JSON (de)serialization for reports, deterministic round-trips, typed contracts. All core artifacts are `BaseModel`. |
| Knowledge Graph (MVP) | **NetworkX** (`>=3.2,<4.0`), in-process `MultiDiGraph` | Zero-infra for MVP; temporal edges modeled as edge attributes. **Migration path:** see §4.6 — the `KnowledgeManager` interface hides the backend so a later Neo4j/Memgraph swap changes no callers. |
| Web API/UI | **FastAPI** (`>=0.110,<1.0`) + **uvicorn[standard]** | Async, typed, auto OpenAPI; Pydantic-native. |
| Git access | **GitPython** (`>=3.1.43,<4.0`) | Clone/checkout/blame/diff/apply against `git.kernel.org` trees. |
| HTTP (lore) | **requests** (`>=2.31,<3.0`) | Fetch `lore.kernel.org` threads/mbox. |
| Config/manifests | **PyYAML** (`>=6.0,<7.0`) | DKP manifests + pattern libraries. |
| Tests | **pytest** (`>=8.1,<9.0`) + **pytest-cov** | Standard. Compliance assertions live here. |
| Lint / format | **ruff** (`>=0.4,<1.0`) | `E,F,I,UP,B`, line length 100. |
| Types | **mypy** (`>=1.10,<2.0`) | Interfaces are `Protocol`s; keep them checkable. |

**DKP discovery:** the `kri.dkp` entry-point group in `pyproject.toml`. The Generic
Runtime MUST NOT `import kri.packages.<domain>` directly.

**Determinism note:** no wall-clock, no unseeded RNG, no network calls inside the
reasoning/confidence path. Network I/O is confined to Repo/Lore managers with a cache.

---

## 2. Frozen core: data model (`kri/common/models.py`)

Implements the Cognition Layer artifact separation (Sec. 5.2):
**Knowledge → Reasoning → Decision → Evidence → Review.**

### 2.1 Enumerations

- `Severity`: `info | warning | blocker`.
- `ConfidenceLevel`: `certain | likely | possible | speculative | unknown`, with
  deterministic `from_score(score) -> ConfidenceLevel` (§6.4).
- `RuleType`: `hard(1.0) | soft(0.7) | philosophical(0.5)` — weights feed
  SubsystemEvidence scoring (§6.2).
- `PatchOutcome`: `accepted | modified | rejected | pending` (`pending` excluded
  from training).
- `ReasoningLayer`: `structural | semantic | design | integration | maintainability
  | ecosystem` (6 layers; **MVP implements 1–3**).
- `EvidenceSourceType` (12 values) + `EVIDENCE_SOURCE_PRIORITY` map (Documentation=1
  … DesignInference=12; lower = more reliable). See Sec. 15.3 priority matrix.

### 2.2 Versioning / provenance

- `KernelVersion{raw, major, minor, patch, rc}` with `sort_key()` (rc sorts before
  the matching final release).
- `VersionRange{valid_from, valid_until=None}` — half-open `[from, until)`,
  `None` == "valid at HEAD".
- `Provenance{source_url, repo_path, commit_hash, retrieved_at, version_or_commit,
  transformation_history, source_confidence}` — **required on every Evidence node**
  (Constitution Sec. 37).

### 2.3 Patch domain

- `Patch{patch_id, subject, author, commit_message, files_changed, diff, sequence,
  series_total}`.
- `PatchSeries{series_id, title, cover_letter, version, patches[], target_kernel_version,
  lore_thread_url, provenance}`.
- `ReviewComment{comment_id, target_series_id, target_patch_id, location, category,
  severity, message, author, is_maintainer, provenance}`. `is_maintainer=True`
  marks ground-truth maintainer comments (benchmark oracle).

### 2.4 Knowledge / evidence / confidence / decision

- `Rule{rule_id, category, rule_type, description, rationale, documentation_ref,
  historical_enforcement_rate, exceptions, version_range}` — container is
  domain-agnostic; *content* is DKP data.
- `Evidence{evidence_id, source_type, summary, provenance, version_range, verified,
  strength}`. `verified`/`strength` set by `EvidenceEngine.verify()`.
- `EvidenceGraph{comment_id, evidence[], subsystem_rule, accepted_examples[],
  rejected_examples[], alternative_recommendation, alternative_precedents[]}` with
  `has_verified_evidence()` — the constitutional gate (Sec. 28).
- `ConfidenceFactor` (8 values, §6.1); `ConfidenceScore{score, level, factor_scores,
  factor_weights, explanation}`.
- `Decision{decision_id, series_id, patch_id, layer, category, severity, location,
  statement, rule_id, pattern_id, evidence_graph, confidence}` with
  `is_publishable()` — MUST have verified evidence AND confidence level ≠ `unknown`.
- `KnowledgeStateId{state_id, created_at, ekg_schema_version, runtime_version,
  dkp_versions, learning_iteration}` — immutable snapshot id for replay (Sec. 38).

These signatures are **frozen**. Additive optional fields are allowed under
architectural review; renames/removals are not.

---

## 3. Frozen core: runtime interfaces (`kri/common/interfaces.py`)

All are `typing.Protocol` (`@runtime_checkable`). Builders implement structurally.
Loose crossing types: `TreeState=Any`, `Diff=str`,
`StaticFinding=dict{tool,file,line,category,severity,message}`, `GraphQuery=dict`,
`GraphResult=list[dict]`, `ReviewReport=dict`, `Pattern=dict`.

| # | Protocol | Methods |
|---|---|---|
| 21.1 | `RepositoryManager` | `checkout(version)->TreeState`; `apply_patch(series)->TreeState`; `blame(file,line)->list[dict]`; `diff(a,b)->Diff` |
| 21.2 | `PatchManager` | `parse(thread)->PatchSeries`; `extract_versions(series)->list[int]`; `correlate_reviews(series)->dict[str,list[ReviewComment]]`; `normalize(patch)->Patch` |
| 21.3 | `LoreManager` | `fetch(thread_id)->Any`; `parse_conversation(thread)->list[dict]`; `extract_reviews(thread)->list[ReviewComment]`; `search(query)->list[str]` |
| 21.4 | `KnowledgeManager` | `load_dkp(domain)->DomainKnowledgePackage`; `query_graph(GraphQuery)->GraphResult`; `get_evidence(decision)->list[Evidence]`; `snapshot()->KnowledgeStateId`; `restore(state_id)->KnowledgeStateId` |
| 21.5 | `KernelBuilder` | `configure(target,config)->dict`; `build(target)->dict`; `get_warnings()->list[str]`; `get_errors()->list[str]` |
| 21.6 | `StaticAnalysisManager` | `run_checkpatch(patch)`, `run_sparse(files)`, `run_smatch(files)`, `run_coccinelle(files,scripts)` → `list[StaticFinding]`; `normalize(output)->list[StaticFinding]` |
| 21.7 | `ReviewEngine` | `review(patch_series, dkp)->list[Decision]`; `explain(decision)->EvidenceGraph`; `generate_report(decisions)->ReviewReport`. **NO domain logic.** |
| 21.8 | `EvidenceEngine` | `gather(decision)->EvidenceGraph`; `verify(evidence)->Evidence`; `format(evidence)->str` |
| 21.9 | `LearningEngine` | `ingest(thread)->list[Pattern]`; `validate(pattern)->dict`; `update_knowledge(pattern)->KnowledgeStateId`; `benchmark()->dict`. **Only home of stochastic elements (Sec. 40).** |
| 21.10 | `SimulationEngine` | `simulate(patch_series,config)->ReviewReport`; `replay(patch_series,knowledge_state)->ReviewReport`; `audit(report)->dict` |
| 16 | `ConfidenceEngine` | `score(decision, evidence_graph)->ConfidenceScore` |
| 9.2 | `DomainKnowledgePackage` | see §5.2 |
| 9.2 | `ReasoningPlugin` | `plugin_id`; `trigger`; `applies(patch,series)->bool`; `evaluate(patch,series)->list[Decision]` |

---

## 4. Engineering Knowledge Graph (EKG) schema

Temporal, versioned property graph (Sec. 8). MVP backend: NetworkX `MultiDiGraph`.

### 4.1 Common node envelope
Every node has: `node_id` (stable), `node_type` (below), `version_range`
(`{valid_from, valid_until}`; `until=null`==HEAD), `provenance`, and a
`properties` dict. Nodes are **never mutated in place** across kernel versions —
a change closes the old node's `valid_until` and opens a new node (temporal
bitemporality, Sec. 8.4).

### 4.2 Node types
| node_type | key properties | origin |
|---|---|---|
| `Subsystem` | name, path_root, maintainers[] | DKP seed |
| `Rule` | rule_id, category, rule_type, description, rationale, doc_ref, enforcement_rate | DKP |
| `Pattern` | pattern_id, description, examples[], confidence, fp_rate | DKP / Learning |
| `Api` | symbol, signature, header, kind(func/macro/struct) | Repo scan / DKP |
| `File` | path, subsystem | Repo |
| `Commit` | hash, subject, author, date | Repo |
| `Patch` | patch_id, series_id, outcome | Lore/Patch |
| `ReviewComment` | comment_id, category, is_maintainer | Lore |
| `Maintainer` | name, email, subsystems[] | MAINTAINERS |
| `Document` | path, title | Repo (Documentation/) |
| `Concept` | name (design concept) | DKP |

### 4.3 Edge types (directed) — `(src) -[TYPE {props}]-> (dst)`
| edge | direction | props |
|---|---|---|
| `GOVERNS` | Rule → Subsystem/Api/File | strength |
| `DEFINED_IN` | Api → File/Header | line |
| `MODIFIES` | Patch/Commit → File/Api | churn |
| `REVIEWS` | ReviewComment → Patch | — |
| `AUTHORED_BY` | Patch/Commit → Maintainer | — |
| `MAINTAINS` | Maintainer → Subsystem | role |
| `DOCUMENTED_BY` | Rule/Api → Document | ref |
| `EXEMPLIFIES` | Patch → Pattern | outcome(accepted/rejected) |
| `SUPERSEDES` | Node → Node | (temporal succession) |
| `DEPENDS_ON` | Api → Api | — |
| `VIOLATES` / `COMPLIES_WITH` | Patch → Rule | — |

Every edge also carries `version_range` + `provenance`.

### 4.4 Temporal validity
A query is always evaluated **as-of** a `KernelVersion`. An element is visible iff
`valid_from <= as_of < (valid_until or +inf)` using `KernelVersion.sort_key()`.

### 4.5 Query patterns (`GraphQuery` shape)
```python
{
  "as_of": "6.9-rc1",              # KernelVersion.raw; required for determinism
  "match": {"node_type": "Rule", "properties": {"category": "..."}},
  "traverse": [{"edge": "GOVERNS", "direction": "out", "to": "Subsystem"}],
  "where": {"rule_type": ["hard", "soft"]},
  "limit": 50
}
```
`GraphResult` is `list[dict]` of matched nodes/paths with their properties +
provenance. Determinism: results MUST be sorted by a stable key (e.g. `node_id`).

### 4.6 Neo4j migration path
Only `KnowledgeManager` (and the `knowledge/` backend it wraps) touch the graph
library. The `GraphQuery` dict maps 1:1 onto Cypher `MATCH…WHERE…RETURN`. Swapping
NetworkX → Neo4j means implementing a new backend behind `KnowledgeManager`;
`query_graph()` signature and `GraphQuery`/`GraphResult` shapes do not change. No
other module imports `networkx`.

---

## 5. DKP contract — the extension boundary (Sec. 9.2)

### 5.1 Manifest schema (`kri/packages/<domain>/manifest.yaml`)
```yaml
package:
  name: <domain>            # MUST equal the kri.dkp entry-point name
  version: <semver>
  description: <text>
schema:
  ekg_schema_version: "1.0" # EKG schema this DKP targets
kernel_version_range:
  valid_from: "6.1"
  valid_until: null          # null == HEAD
requires:
  - runtime: ">=0.1.0"
file_patterns:               # globs; used by owns_file() routing
  - "sound/soc/*"
sub_packages: [core, codec, machine, platform]   # optional decomposition
reasoning_plugins:           # each maps to a ReasoningPlugin
  - plugin_id: <id>
    trigger: "file_touched:<glob>"   #  or "api_used:<symbol_glob>"
    layer: structural|semantic|design
```

### 5.2 Python entry-point convention
```toml
[project.entry-points."kri.dkp"]
<domain> = "kri.packages.<domain>.plugin:<Class>DomainKnowledgePackage"
```
The class MUST satisfy `DomainKnowledgePackage`:
`name`, `version`, `manifest()`, `supports_version(kv)`, `owns_file(path)`,
`build_target()`, `rules(kv=None)`, `patterns()`, `reasoning_plugins()`,
`seed_graph(knowledge_manager)`.

`KnowledgeManager.load_dkp(domain)` resolves the entry point, instantiates the
class, validates the manifest, calls `seed_graph()`, and returns the handle. The
Review Engine receives this handle and calls it only through the protocol.

### 5.3 Isolation guarantee
Domain identifiers (`snd_soc`, `asoc`, `sound/soc`, product names) appear **only**
under `kri/packages/`. Enforced by `tests/test_scaffolding.py::
test_domain_isolation_generic_runtime_has_no_asoc_identifiers`.

---

## 6. Confidence Engine factor model (Sec. 16)

### 6.1 The 8 factors (`ConfidenceFactor`)
| factor | definition (0–1) |
|---|---|
| `historical_agreement` | fraction of comparable historical cases where maintainers agreed with this conclusion |
| `subsystem_evidence` | strength of governing Rule(s): weighted by `RuleType` (hard 1.0 / soft 0.7 / philosophical 0.5) × enforcement_rate |
| `documentation_support` | presence & directness of Documentation/API-header backing |
| `api_certainty` | how unambiguous the API contract is (verified signature/header vs. inferred) |
| `code_similarity` | similarity to known accepted/rejected precedents |
| `review_history` | density/consistency of prior review discussion on this concern |
| `version_consistency` | evidence validity across the target kernel version range |
| `runtime_evidence` | build/static-analysis corroboration (warnings, checkpatch, sparse) |

### 6.2 Scoring
Each factor emits a raw `[0,1]` score from the `EvidenceGraph` (never from free
text). `subsystem_evidence` example:
`min(1.0, Σ_rule (rule_type_weight × (enforcement_rate or 1.0)) / normalizer)`.
Missing evidence for a factor ⇒ factor score `0.0` (conservative), not omitted.

### 6.3 Weights (default; calibratable, persisted in Knowledge State)
```
historical_agreement 0.20, subsystem_evidence 0.20, documentation_support 0.15,
api_certainty 0.15, code_similarity 0.10, review_history 0.10,
version_consistency 0.05, runtime_evidence 0.05    (Σ = 1.00)
```
`score = Σ factor_score[f] × weight[f]`. Weights MUST sum to 1.0 (asserted).
Both `factor_scores` and `factor_weights` are stored in `ConfidenceScore` so the
score is fully reconstructable (explainable + auditable).

### 6.4 Score → level (`ConfidenceLevel.from_score`, deterministic)
`≥0.95 certain · ≥0.80 likely · ≥0.60 possible · ≥0.40 speculative · else unknown`.

### 6.5 Properties (Constitution Sec. 31)
**Reproducible** (same inputs + same Knowledge State ⇒ same score),
**calibrated** (weights tuned against benchmark), **explainable** (per-factor
breakdown + text), **conservative** (unknown beats wrong; missing ⇒ 0).

---

## 7. Reasoning hierarchy + Cognition flow

Six layers (Sec. 7); **MVP = layers 1–3**:
1. **Structural** — checkpatch/style/format, obvious correctness.
2. **Semantic** — API usage correctness, locking, error paths, refcounting.
3. **Design** — subsystem-idiom conformance, abstraction fit.
4. Integration · 5. Maintainability · 6. Ecosystem — post-MVP.

Flow (Sec. 5.2): `ReviewEngine.review(series, dkp)` runs enabled layers, invoking
DKP `reasoning_plugins()` whose `trigger` matches (generic trigger evaluation) →
each plugin returns `Decision`s (language-agnostic `statement`, no evidence yet) →
`EvidenceEngine.gather()`+`verify()` attach a verified `EvidenceGraph` →
`ConfidenceEngine.score()` attaches a `ConfidenceScore` → decisions failing
`is_publishable()` are dropped → `generate_report()` renders the surviving
Decisions into `ReviewComment`s + the Explainability Report.

---

## 8. Data-flow contracts

```
LoreManager.fetch ──raw thread──> PatchManager.parse ──PatchSeries──┐
RepositoryManager.checkout(target_kernel_version) ──TreeState───────┤
                                                                     ▼
KnowledgeManager.load_dkp(domain) ──DKP handle──> ReviewEngine.review(series, dkp)
   │                                                        │
   │  (query_graph, get_evidence)                           ▼  list[Decision]
   ▼                                                 EvidenceEngine.gather/verify
StaticAnalysisManager.run_* ──list[StaticFinding]──> (runtime_evidence factor)
KernelBuilder.build ──warnings/errors───────────────> (runtime_evidence factor)
                                                                     ▼
                                              ConfidenceEngine.score(decision, eg)
                                                                     ▼
                                     SimulationEngine.simulate ──> ReviewReport
                                                                     ▼
                                              report/ renders Explainability Report
LearningEngine.ingest/validate/update_knowledge ──> KnowledgeManager (new state id)
```

Contract invariants:
- `PatchSeries.target_kernel_version` drives **every** EKG query's `as_of`.
- A `Decision` crossing into the report MUST carry both a verified `EvidenceGraph`
  and a `ConfidenceScore`.
- `SimulationEngine.replay(series, knowledge_state)` MUST reproduce a prior
  `simulate` byte-for-byte given the same `KnowledgeStateId`.
- Only `RepositoryManager` and `LoreManager` perform network I/O; both cache.

---

## 9. Per-sprint module ownership map

| Sprint | Team | Owns (directories) | Delivers |
|---|---|---|---|
| **1** | Infrastructure | `repo_manager/`, `patch_manager/`, `lore_manager/`, `static_analysis/` (checkpatch), `web/` | Ingest a lore series + checkout tree + basic static findings; FastAPI UI to submit a series and see raw parse. |
| **2** | Knowledge | `knowledge/` (EKG backend + schema), `knowledge_manager/`, `packages/asoc/` (manifest, rules, patterns, plugins, seed_graph), pattern extraction | Populated EKG; loadable ASoC DKP; `query_graph` as-of queries. |
| **3** | Engine | `review_engine/`, `evidence_engine/`, `confidence_engine/`, `report/`, `benchmark/`, `simulation/`, `learning/` (loop) | End-to-end simulate→report with confidence + evidence; benchmark vs. maintainer ground truth. |
| all | Gatekeeper | (reviews only) | QA/challenge every builder output against §10–11. |

`common/` is **Architect-frozen**; changes require architectural review.

---

## 10. Constitutional compliance — testable assertions

Builders MUST keep these green (see `tests/`):

- **C-9 Domain Isolation:** no `snd_soc|asoc|sound/soc|alsa` token in any `.py`
  outside `kri/packages/`. *(implemented)*
- **C-28 Evidence-First:** `Decision.is_publishable()` ⇒
  `evidence_graph.has_verified_evidence()` is True. No published `ReviewComment`
  without ≥1 verified `Evidence`.
- **C-29/30 Conservative:** decisions with `ConfidenceLevel.UNKNOWN` (score<0.40)
  never appear in the report as findings.
- **C-31 Reproducible confidence:** `score(d, eg)` called twice on equal inputs ⇒
  equal `ConfidenceScore`; weights sum to 1.0.
- **C-37 Provenance:** every `Evidence.provenance` has a resolvable
  `source_url`/`repo_path` + `version_or_commit`.
- **C-38 Replay:** `replay(series, state_id)` == the original `simulate` output.
- **C-40 Stochastic confinement:** no RNG/model nondeterminism outside `learning/`.
- **C-32 Frozen core:** `common/models.py` + `common/interfaces.py` public
  signatures unchanged without an architectural-review note.

---

## 11. Definition-of-Done per module

Every module: (a) implements its Protocol from §3 exactly; (b) unit tests with
fixtures (offline; no live network in CI); (c) ruff + mypy clean; (d) docstrings
citing the relevant Blueprint section; (e) no domain identifiers unless under
`packages/`; (f) deterministic (no wall-clock/unseeded RNG in the reasoning path).

Module specifics:
- **repo_manager:** checkout by tag/commit is exact; `apply_patch` returns a
  structured failure (not an exception) on reject; blame/diff parsed to typed dicts.
- **patch_manager:** round-trips mbox → `PatchSeries` → mbox subject/seq intact;
  `correlate_reviews` maps every maintainer comment to a patch_id.
- **lore_manager:** cached fetch is offline-replayable; `extract_reviews` sets
  `is_maintainer` from MAINTAINERS.
- **knowledge/knowledge_manager:** temporal `as_of` queries correct across a
  `valid_until` boundary; `snapshot`/`restore` round-trip; DKP discovery via entry
  points only.
- **packages/asoc:** manifest validates; `owns_file` matches `sound/soc/*`;
  ≥1 real rule + ≥1 reasoning plugin producing a `Decision` on a known patch.
- **static_analysis:** each tool output normalized to `StaticFinding`; FP filter
  documented.
- **review_engine:** zero domain identifiers; drives layers 1–3; delegates to DKP
  plugins via trigger matching only.
- **evidence_engine:** `verify` sets `verified`/`strength`; unverifiable ⇒
  `verified=False`; `format` emits a resolvable citation.
- **confidence_engine:** 8 factors + weights per §6; reproducibility test.
- **report:** Explainability Report includes decision, evidence graph, confidence
  breakdown, provenance; serializes to JSON.
- **benchmark:** precision/recall vs. `is_maintainer` ground truth; deterministic.
- **simulation:** `simulate`/`replay`/`audit`; replay equality test.
- **learning:** patterns validated (multi-example + significance + FP check) before
  `update_knowledge`; only nondeterministic module.

---

## 12. Open questions / ambiguities (flagged for review)

- **O-1 (ASoC package count) — CORRECTED per Gatekeeper review.** The Engineering
  Intelligence Packages doc defines **25** intelligence packages (Package-01 …
  Package-25), enumerated in the body and Appendices A/C/D. These are *cognitive
  curriculum models, not code modules* ("These are not code modules. These are
  cognitive models"). "Core, Codec, Machine, Platform" are simply Package-01…04 —
  the first four of 25, not the complete set. The ASoC DKP `manifest.yaml`
  `sub_packages: [core, codec, machine, platform]` is a legitimate *driver-type*
  decomposition and stays. Sprint-2 (Knowledge) MUST decide which of the 25
  intelligence packages the MVP DKP actually seeds (the doc marks ASoC Core as
  highest learning priority).
- **O-2 (layers in MVP).** Blueprint lists 6 reasoning layers; MVP scope = 1–3.
  Layers 4–6 have interfaces reserved but no plugins in MVP.
- **O-3 (confidence weights).** §6.3 weights are a reasonable default; final values
  MUST be calibrated by the Engine team against the benchmark and persisted in the
  Knowledge State.
- **O-4 (KG backend).** NetworkX chosen for MVP; Neo4j migration path defined
  (§4.6) but not built. Revisit when graph size or multi-process access demands it.
- **O-5 (build environment).** `KernelBuilder` assumes a toolchain is available;
  MVP may stub `build()` and rely on static analysis for `runtime_evidence`.
