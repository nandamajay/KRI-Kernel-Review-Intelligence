# Autonomous Execution Model

**Date:** 2026-07-24
**Status:** Active — governs all autonomous implementation decisions
**Scope:** Defines the multi-agent autonomous execution framework, permission
tiers, governance engine, validation gates, ledger, and STOP conditions.

---

## 1. Mission Statement

The autonomous execution framework exists to implement blueprint-aligned work
without waiting for human input at every step. It does not exist to generate
documentation. It does not exist to discuss architecture. Its single deliverable
is working, tested, committed code that closes the gap between blueprint intent
and current implementation.

Documentation phases (Phases 0–3) are preconditions — they exist only to ensure
safe autonomous implementation, not as ends in themselves.

---

## 2. Agents

Five agents operate in each execution loop:

### 2.1 Implementer (Agent 1)

**Role:** Produce the implementation artifact for the selected task.
**Inputs:** Task specification, architecture understanding doc, reconciliation findings, current code state.
**Outputs:** Code diff, test additions, implementation rationale, confidence record.
**Constraints:**
- May only touch files within the current permission tier.
- Must record every assumption made.
- Must not modify `kri/governance/**` outside Tier 3 scope.
- Must pass Sec-40 scan before producing output.

### 2.2 Adversarial Agent (Agent 2)

**Role:** Challenge every claim made by Agent 1. Find the failure mode. Find the
constitutional violation. Find the test gap.
**Inputs:** Agent 1's diff, rationale, and confidence record.
**Outputs:** Findings list (title / location / scenario / impact / fix / test gap), go/no-go recommendation with confidence.
**Constraint:** Must produce at least one finding or an explicit "no findings — here is why" statement.

### 2.3 Test-Gap Auditor (Agent 3)

**Role:** Verify that Agent 1's implementation is covered by tests that would
actually catch a regression.
**Inputs:** Agent 1's diff, existing test suite.
**Outputs:** List of untested behaviors, list of required new tests with assertions
described, go/no-go on test coverage.
**Constraint:** Must cite file:line for every claimed test coverage gap.

### 2.4 Architect (Agent 4)

**Role:** Verify architecture drift — ensure the implementation moves toward
blueprint intent and does not violate any constitutional rule.
**Inputs:** Agent 1's diff, `AUTONOMOUS_SYSTEM_UNDERSTANDING.md`, `BLUEPRINT_RECONCILIATION.md`.
**Outputs:** Architecture drift gate result (pass/fail/rework), drift classification if fail.
**Constraint:** Must answer all five Architecture Drift Gate questions (see §7).

### 2.5 Arbiter (Main loop / Orchestrator)

**Role:** Synthesize all four agent outputs. Make the go/rework/escalate decision.
Execute the commit if go. Update ledger. Select next task.
**Constraint:** Cannot override a Sec-40 violation or a STOP condition, regardless of agent confidence.

---

## 3. Execution Loop

```
SELECT TASK (highest priority, tier-eligible, unblocked)
  │
  ├─ Agent 1: Implementer → diff + rationale + confidence
  │
  ├─ Agent 2: Adversarial → findings + go/no-go
  │
  ├─ Agent 3: Test-Gap Auditor → test coverage + go/no-go
  │
  ├─ Agent 4: Architect → architecture drift gate result
  │
  └─ Agent 5 (Arbiter): Synthesize
        │
        ├─ All green? → Validation (Layers A–E)
        │     ├─ Pass → Commit + Ledger + Next task
        │     └─ Fail → REWORK (retry budget -1)
        │
        ├─ Any REWORK? → Retry (budget MAX=3)
        │     └─ Retry 3 exhausted → ESCALATE (STOP)
        │
        └─ Any STOP condition? → STOP immediately
```

---

## 4. Task Selection Policy

1. Check `.kri/ledger/tasks/` for any `IN_PROGRESS` task — resume it first.
2. Otherwise: select the highest-priority `PENDING` task whose:
   - Tier requirement ≤ current tier
   - Blocked-by list is empty
   - Has no UNRESOLVED conflict in `BLUEPRINT_RECONCILIATION.md` that overlaps its scope
3. Record task start in `.kri/ledger/tasks/<task-id>.jsonl`.

---

## 5. Validation Layers

All five layers must pass before any commit. No partial pass.

| Layer | Name | Tool / Check | STOP on fail? |
|-------|------|-------------|---------------|
| A | Automated tests | `pytest kri/tests/ -x` — full suite must stay green | Yes |
| B | Behavioral validation | Per-task behavioral template (see §6) | Yes |
| C | Byte-identity (off-mode) | `mode="off"` output byte-identical to pre-task baseline | Yes if scope touches reducer path |
| D | Sec-40 scan | No `random`, `time.*`, `datetime.now`, `uuid.uuid1/4` outside `kri/learning/`; `hashlib` only sha256/blake2b | Yes |
| E | Diff hygiene | No staged-by-accident files; no `docs/*.md` staged unless the task explicitly creates them; no `kri_server.new.log` | Yes |

### Layer B — Behavioral Validation Templates

Each task type has a mandatory behavioral assertion:

**Reducer rule task:** run the 6-series shadow batch; assert finding count is within the expected delta; assert no `blocker` or `warning`-with-confidence-≥0.7 finding is absent from output that was present in the pre-task baseline.

**Schema change task:** run `python -c "from kri.llm.models import InlineComment; InlineComment()"` plus a roundtrip serialization check.

**Engine wiring task:** run a synthetic series through `IntelligentReviewEngine` with `series_reducer_mode="off"`; assert byte-identity to pre-task baseline.

**Governance rule task:** run governance rule validation script; assert all rules parse and all invariants hold.

**New test task:** run only the new tests; assert all pass; assert the test would fail if the behavior being tested were reverted.

---

## 6. Retry Policy

```
MAX_REWORK_ATTEMPTS = 3

On REWORK:
  - Record attempt number + failure mode in ledger
  - Agent 1 must address EVERY finding from Agent 2 and Agent 3 that caused REWORK
  - Agent 1 must not introduce new findings

On third REWORK failure:
  STATUS = ESCALATE
  Record:
    - root_cause: what specifically prevented all three attempts from succeeding
    - evidence: the exact finding(s) that blocked each attempt
    - recommended_next_action: one of [split_task / defer_task / manual_review_required]
  STOP autonomous execution on this task
  Mark task as ESCALATED in ledger
  Continue to next task only if MAX_CONCURRENT_ESCALATIONS (=2) not reached
```

---

## 7. Architecture Drift Gate

Mandatory for Agent 4. Answered before every commit.

| # | Question | Fail condition |
|---|----------|---------------|
| 1 | Does this move implementation closer to blueprint intent? | Answer is "no" → REWORK |
| 2 | Does this violate any constitutional rule? | Any violation → REWORK |
| 3 | Would the blueprint author likely accept this tradeoff? | Clear "no" → REWORK; "uncertain" → record assumption |
| 4 | Does this increase future maintenance burden? | "yes, materially" → REWORK unless blueprint explicitly accepts the tradeoff |
| 5 | Is a simpler implementation available that achieves the same blueprint alignment? | "yes and significantly simpler" → REWORK |

Result outcomes: `PASS` / `REWORK` / `ESCALATE` (if drift cannot be resolved in 3 retries).

---

## 8. Confidence Accounting

Every agent records confidence for every claim it makes.

| Label | Score threshold | Definition |
|-------|-----------------|-----------|
| Certain | ≥ 0.95 | Claim follows by construction from code or spec |
| Likely | ≥ 0.70 | Strong evidence; one or two gap assumptions |
| Guessing | ≥ 0.40 | Inference from partial evidence; must be labeled |
| Unknown | < 0.40 | Do not present as a finding |

**Accounting rule:** For every `[Certain]` claim made in a commit rationale,
the ledger records the claim and the outcome (validated/refuted by tests or
future evidence). If 3 of the last 10 `[Certain]` claims are refuted: STOP.

Ledger file: `.kri/ledger/confidence/accounting.jsonl`

---

## 9. Evidence Requirement

No commit may proceed without evidence that the implementation is correct.

Mandatory evidence per task:
- Automated test results (pass/fail counts)
- Behavioral validation output
- Architecture drift gate answers
- Agent 2 finding list (including "no findings" record)
- Confidence record for the Arbiter's go decision

Evidence is stored in `.kri/ledger/validations/<task-id>.jsonl`.

---

## 10. Permission Tier Model

### Tier 1 (T1) — Analysis, Tests, Documentation

**Allowed:** Any file not in the arch-set and not in `kri/**/*.py` production code.
Includes: `tests/`, `docs/`, `kri/governance/rules/*.yaml` (read-only verification),
`.kri/ledger/**`, analysis scripts under `/tmp/`.

**Forbidden:** Any `kri/**/*.py` production module edit.

### Tier 2 (T2) — Small Production Changes

**Allowed:** `kri/**/*.py` edits of ≤50 LOC total, ≤2 files per task, not in arch-set.

**Forbidden:** Arch-set files, governance rule mutation.

**Arch-set files (T3-only):**
- `kri/llm/reviewer.py`
- `kri/series/reducer.py`
- `kri/series/__init__.py`
- `kri/common/models.py`
- `kri/governance/**`

### Tier 3 (T3) — Arch-Set Files

**Allowed:** Any file including arch-set. LOC limit raised to 150 LOC / 4 files per task.

**Forbidden:** Blueprint mutations, changes to governance rule semantics without
explicit blueprint evidence.

### Tier 4 (T4) — Blueprint Mutations

**Never auto-promotable.** Requires explicit `PROMOTE T4 <task-id>` from user.

---

## 11. Tier Promotion Policy

| Transition | Condition |
|-----------|-----------|
| T1 → T2 | 5 consecutive T1 tasks with all validations green, no ESCALATE, no STOP |
| T2 → T3 | 10 consecutive T2 tasks with all validations green, no ESCALATE, no STOP |
| T3 → T4 | Forbidden autonomously — requires explicit user command |

**Demotion triggers** (reset counter, revert to lower tier):
- Any STOP condition
- Byte-identity regression on any previously-passing baseline
- 3-of-last-10 `[Certain]` confidence claims refuted
- Sec-40 violation
- Wrong commit identity or missing `-s`

Promotion/demotion recorded in `.kri/ledger/promotions.jsonl`.

---

## 12. Governance Engine

The Governance Engine is a deterministic Python module that enforces constitutional
rules before any mutation is applied.

**Location:** `kri/governance/` (to be created in T1)
**Rules:** `kri/governance/rules/*.yaml`
**Invariants checked on every diff:**
1. No `random`, `time.*`, `datetime.now`, `uuid.uuid1/4` outside `kri/learning/` (Sec-40)
2. No `verify=False` outside `kri/llm/client.py` (TLS)
3. No `git add -A` in any script or fixture
4. Blockers + warnings at confidence ≥ 0.7 never suppressed in `SeriesReducer` without safety-floor check
5. `series_reducer_mode="off"` always produces byte-identical output to pre-WP-S1A
6. Commit identity = `Ajay Kumar Nandam <ajay.nandam@oss.qualcomm.com>` with `-s`

Governance rule files may only be modified in T3+ scope. Any diff that touches
`kri/governance/**` outside a T3-scoped task triggers immediate STOP.

---

## 13. Ledger Structure

All persistent state lives under `.kri/ledger/` in the repository root.

```
.kri/ledger/
  session.jsonl                  ← session start/end, current tier, task counts
  tasks/
    <task-id>.jsonl              ← per-task: start, agents, attempts, outcome
  validations/
    <task-id>.jsonl              ← per-task: all five validation layer results
  escalations/
    <task-id>.jsonl              ← per ESCALATE: root cause, evidence, recommendation
  promotions.jsonl               ← tier promotions and demotions with evidence
  confidence/
    accounting.jsonl             ← per [Certain] claim: prediction, outcome
  baselines/
    <task-id>_baseline.json      ← byte-identity baseline snapshot
  governance/
    snapshot.jsonl               ← governance rule state at session start
```

---

## 14. STOP Conditions

Immediately halt all autonomous execution. Record reason in ledger. Do not commit.

| Condition | Trigger |
|-----------|---------|
| Sec-40 breach | Any `random/time.*/datetime.now/uuid` outside `kri/learning/` found in diff |
| TLS violation | `verify=False` outside `kri/llm/client.py` |
| Diff hygiene violation | `docs/*.pdf`, `kri_server.new.log`, `git add -A` in any staged change |
| Byte-identity regression | `mode="off"` produces different output than baseline on any previously-passing fixture |
| Safety-floor bypass | A `blocker` or `warning`-with-confidence-≥0.7 is absent from output that was present in baseline |
| 2 consecutive ESCALATE | Two tasks in a row exhaust their retry budgets |
| Governance rule modified outside T3 | Any diff touching `kri/governance/**` without T3 tier active |
| Wrong commit identity | Name or email differs from `Ajay Kumar Nandam <ajay.nandam@oss.qualcomm.com>` |
| Missing `-s` on commit | Signed-off-by absent |
| Unauthorized push | Push attempted without explicit user authorization |
| Blueprint contradiction | An implementation is required that directly contradicts a constitutional rule with no resolution path |
| Confidence accounting failure | 3 of last 10 `[Certain]` predictions refuted |

---

## 15. Adversarial Report Format

Every commit must include an adversarial report in the commit body:

```
## Adversarial report — <WP-ID>/<task-id>

Finding F1 — <one-line title>
  Location: <file:line or module>
  Scenario: <inputs/state → wrong output/crash>
  Impact: <what breaks>
  Fix: <what was done>
  Test gap: <what test was added or why none was needed>

Finding F2 — ...
```

A report with zero findings must include:
```
No adversarial findings — here is why: <explicit reasoning>
```

---

## 16. Autonomous Loop Continuation Policy

The loop continues without stopping until:
- No executable tasks remain (all tasks COMPLETED or ESCALATED)
- A STOP condition fires
- The escalation policy triggers (2 consecutive ESCALATE)
- An UNRESOLVED blueprint contradiction blocks all remaining tasks

Between tasks: update the ledger, check STOP conditions, check tier promotion
eligibility, then select next task. Do not wait for human input.
