# Kernel Reference Tree Refresh

**Date:** 2026-07-18 (initial clone refresh)
**Updated:** 2026-07-20 (runtime baseline pinned to v7.2-rc3)

## Summary

The kernel reference tree at `data/kernel/linux` has been refreshed from a
shallow grafted clone to a full-history clone, and the **runtime baseline**
(checked-out HEAD) pinned to **v7.2-rc3**.

## History

### Phase 1 (2026-07-18): Shallow → Full-history clone

| Property | BEFORE (shallow) | AFTER (full) |
|---|---|---|
| Tag | v6.6 | v7.2-rc3 (master available) |
| HEAD | `ffc253263a13` (grafted) | see Phase 2 |
| Clone type | `--depth 1`, single-tag | full history, standard refspec |
| Reachable commits | 1 | 1,969,968+ |
| `.git` size | 252 MB | 6.5 GB |
| Total tree size | 1.8 GB | 8.0 GB |
| `git log --follow` | broken | works |
| `git cat-file -e` (deep history) | fails | works for all commits |

### Phase 2 (2026-07-20): Runtime baseline pinned to v7.2-rc3

| Property | Value |
|---|---|
| Previous HEAD | `ffc253263a1375a65fa6c9f62a893e9767fbebfa` (v6.6) |
| New HEAD | `a13c140cc289c0b7b3770bce5b3ad42ab35074aa` |
| Tag | **v7.2-rc3** |
| Checkout mode | detached HEAD at exact tag |
| ASoC-relevant commits (v7.1.3..v7.2-rc3) | 439 |

**Rationale for v7.2-rc3 over v7.1.3:**

- KRI reviews current lore.kernel.org patches targeting the active merge window.
- `git apply` and `git blame` are HEAD-sensitive — patches targeting v7.2
  development won't apply cleanly to v7.1.3 (439 ASoC commits behind).
- `scripts/checkpatch.pl` and `MAINTAINERS` should match what patch submitters
  test against.
- Cached benchmark metrics are tree-independent (operate on lore fixtures in
  memory, not the working tree) and remain fully reproducible.
- v6.6 provenance labels in the ASoC DKP (`version_or_commit="v6.6"`) are
  historical metadata recording when knowledge was sourced — NOT runtime
  baseline indicators. They remain unchanged.

**Why NOT floating master:**

The baseline must be pinned to an exact tag and commit hash for reproducibility.
Floating master changes with every fetch and makes results non-deterministic
across environments.

## Disk cost

| | Size |
|---|---|
| Tree (`data/kernel/linux`) | 8.0 GB |
| Old backup (`data/kernel/linux.old.v6.6`) | 1.8 GB |
| Filesystem free | 191 GB / 2.0 TB |

## Benchmark impact

Precision, recall, and F1 are unchanged across all baseline transitions:

| Metric | v6.6 (shallow) | v6.6 (full) | v7.2-rc3 | Delta |
|---|---|---|---|---|
| Fixtures used | 101 | 99 | 99 | 0 |
| Total decisions | 31 | 31 | 31 | 0 |
| Precision | 0.5909 | 0.5909 | 0.5909 | 0 |
| Recall | 1.0000 | 1.0000 | 1.0000 | 0 |
| F1 | 0.7429 | 0.7429 | 0.7429 | 0 |

Floors pinned in `tests/test_benchmark_regression.py` (precision >= 0.55,
recall >= 0.95, F1 >= 0.70) were not modified.

## Test suite (v7.2-rc3 baseline)

- Full suite: 147 passed, 1 xfailed (unchanged from prior baselines)
- Frozen-core signature: PASS
- CANONICAL_PRECEDENTS guardrail: 2 passed, 1 xfailed (expected — concept
  placeholders pending real-hash seeding)

## ASoC rule citation verification (v7.2-rc3)

All provenance paths referenced by `kri/packages/asoc/knowledge.py` verified
present:

- `Documentation/sound/soc/codec.rst` — OK
- `Documentation/sound/soc/overview.rst` — OK (implicit via codec.rst presence)
- `include/linux/regmap.h` — OK
- `include/sound/soc-dai.h` — OK
- `include/sound/soc.h` — OK (parent of soc-dai.h, always present)
- `MAINTAINERS` — OK
- `scripts/checkpatch.pl` — OK

## Precedent seeding status

**Unblocked.** The full-history tree supports all precedent verification
operations regardless of HEAD position:

- `git cat-file -e <hash>` — works for all commits in full history
- `git show --name-only --format="" <hash>` — mechanical relevance check
- `git log --follow -- <path>` — file rename history for precedent discovery

CANONICAL_PRECEDENTS remains at `concept:` placeholders pending human-authored
real-hash seeding in a follow-up WP.

## MAINTAINERS changes (v7.2-rc3 vs v7.1.3)

The ASoC section maintainer persons (M: lines) are unchanged (Girdwood +
Brown). The mailing list remains `linux-sound@vger.kernel.org` (changed from
`alsa-devel@alsa-project.org` between v6.6 and v7.1.3, stable since).

## References

- WP-KERNEL-REFRESH (this work package)
- Unblocks: WP-9.2a-polish-v3 (real precedent seeding)
- Prior context: revert 650540f (fabricated hashes), guardrail a60ef43
- Analysis: docs/KERNEL_TREE_USAGE_ANALYSIS.md
