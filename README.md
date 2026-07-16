# KRI — Kernel Review Intelligence

Evidence-backed simulation of Linux kernel maintainer patch review. KRI ingests a
patch series (from `lore.kernel.org` or direct upload), reasons about it the way a
subsystem maintainer would, and emits **structured, evidence-cited review comments**
with calibrated confidence — never a hallucinated finding.

The MVP is scoped to the **ALSA ASoC** subsystem (`sound/soc/`), but the runtime
itself is **domain-agnostic**. All ASoC knowledge is confined to a pluggable
Domain Knowledge Package (DKP).

> Authoritative design contract: **[`SPEC.md`](./SPEC.md)**. Read it before building.

## Architectural non-negotiables (Constitution, Part VIII)

1. **Domain Isolation (Sec. 9).** The Generic Runtime (everything outside
   `kri/packages/`) contains **zero** ASoC/audio/`snd_soc`/`sound/soc` identifiers.
   Domain content flows in only as *data* through the `DomainKnowledgePackage`
   protocol. Enforced by a test (`test_domain_isolation_*`).
2. **Evidence-First (Sec. 28).** Every published review comment carries ≥1 *verified*
   `Evidence` node. `Decision.is_publishable()` is the gate.
3. **Conservative confidence (Sec. 29/30).** "Unknown is better than wrong."
   Confidence < 0.40 (`UNKNOWN`) is never surfaced as a finding.
4. **Determinism & Replayability (Sec. 31/38/40).** Same inputs + same Knowledge
   State ⇒ identical output. Stochastic elements are confined to the Learning Engine.
5. **Frozen core (Sec. 32).** The types in `kri/common/models.py` and the interfaces
   in `kri/common/interfaces.py` are contracts. Changing a signature requires
   architectural review.

## Layout

```
kri/
├── pyproject.toml            # project + deps + kri.dkp entry-point group
├── SPEC.md                   # authoritative spec (READ FIRST)
├── README.md
├── kri/
│   ├── common/               # FROZEN CORE: models.py + interfaces.py (domain-agnostic)
│   ├── repo_manager/         # 21.1 clone/checkout/apply/blame/diff        [Sprint 1]
│   ├── patch_manager/        # 21.2 parse series, correlate reviews        [Sprint 1]
│   ├── lore_manager/         # 21.3 fetch/parse lore threads               [Sprint 1]
│   ├── knowledge/            # EKG backend (NetworkX) + schema             [Sprint 2]
│   ├── knowledge_manager/    # 21.4 owns EKG + loads DKPs                  [Sprint 2]
│   ├── packages/asoc/        # THE ONLY place ASoC identifiers may appear  [Sprint 2]
│   ├── static_analysis/      # 21.6 checkpatch/sparse/smatch/coccinelle    [Sprint 1/3]
│   ├── review_engine/        # 21.7 cognition orchestrator (NO domain logic) [Sprint 3]
│   ├── evidence_engine/      # 21.8 gather/verify/format evidence          [Sprint 3]
│   ├── confidence_engine/    # 8-factor confidence model                   [Sprint 3]
│   ├── learning/             # 21.9 learning feedback loop (stochastic)    [Sprint 3+]
│   ├── simulation/           # 21.10 pipeline / replay / audit             [Sprint 3]
│   ├── report/               # Review Explainability Report                [Sprint 3]
│   ├── benchmark/            # benchmark suite vs. ground truth            [Sprint 3]
│   └── web/                  # FastAPI UI/API                              [Sprint 1]
└── tests/
```

The per-module sprint ownership map and definition-of-done checklists are in
`SPEC.md`.

## Quick start

```bash
cd kri
python3 -m venv .venv && . .venv/bin/activate      # (or .venv/bin/activate.csh on tcsh)
pip install -e ".[dev]"
pytest -q
```

## Adding a domain (DKP)

1. Create `kri/packages/<domain>/` with a `plugin.py` implementing
   `kri.common.interfaces.DomainKnowledgePackage` and a `manifest.yaml`.
2. Register it under `[project.entry-points."kri.dkp"]` in `pyproject.toml`.
3. The Knowledge Manager discovers and loads it; the Generic Runtime never imports
   it by name.
