# Kernel Reference Tree Refresh

**Date:** 2026-07-18

## Summary

The kernel reference tree at `data/kernel/linux` has been refreshed from a
shallow grafted clone to a full-history clone.

## BEFORE

| Property | Value |
|---|---|
| Tag | v6.6 |
| HEAD | `ffc253263a1375a65fa6c9f62a893e9767fbebfa` (grafted) |
| Clone type | `--depth 1`, `--filter=blob:none`, single-tag fetch refspec |
| Reachable commits | 1 |
| `.git` size | 252 MB |
| Total tree size | 1.8 GB |
| `git log --follow` | broken (grafted history) |
| `git cat-file -e` (deep history) | fails for any pre-v6.6 commit |

## AFTER

| Property | Value |
|---|---|
| Tag (checkout) | v7.1.3 |
| HEAD | `199c9959d3a9b53f346c221757fc7ac507fbac50` |
| Clone type | full history, plain config, standard refspec |
| Reachable commits | 1,969,968 |
| Remote branches | 116 |
| Tags | 5,676 |
| `.git` size | 6.5 GB |
| Total tree size | 8.0 GB |
| `git log --follow` | works |
| `git cat-file -e` (deep history) | works for all commits in ancestry |

## Disk cost

| | Size |
|---|---|
| New tree (`data/kernel/linux`) | 8.0 GB |
| Old backup (`data/kernel/linux.old.v6.6`) | 1.8 GB |
| Filesystem free after refresh | 191 GB / 2.0 TB |

The old shallow clone is preserved at `data/kernel/linux.old.v6.6` as a
rollback safety net. It should be retained for at least one week before
deletion.

## Benchmark impact

Precision, recall, and F1 are unchanged after the refresh:

| Metric | BEFORE | AFTER | Delta |
|---|---|---|---|
| Fixtures used | 101 | 99 | -2 (parse edge case, not a regression) |
| Total decisions | 31 | 31 | 0 |
| Precision | 0.5909 | 0.5909 | 0 |
| Recall | 1.0000 | 1.0000 | 0 |
| F1 | 0.7429 | 0.7429 | 0 |

Floors pinned in `tests/test_benchmark_regression.py` (precision >= 0.55,
recall >= 0.95, F1 >= 0.70) were not modified.

## Test suite

- Full suite: 147 passed, 1 xfailed (unchanged)
- Frozen-core signature: PASS
- CANONICAL_PRECEDENTS guardrail: 2 passed, 1 xfailed (expected)

## ASoC rule citation verification

All provenance paths referenced by `kri/packages/asoc/knowledge.py` verified
present in the v7.1.3 tree:

- `Documentation/sound/soc/codec.rst` — OK
- `Documentation/sound/soc/overview.rst` — OK
- `include/linux/regmap.h` — OK
- `include/sound/soc-dai.h` — OK
- `include/sound/soc.h` — OK

## Precedent seeding status

**Unblocked.** The full-history tree supports:

- `git cat-file -e <hash>` — existence verification against any commit in
  Linux history
- `git show --name-only --format="" <hash>` — mechanical relevance check
  (existence != relevance)
- `git log --follow -- <path>` — tracing file rename history for precedent
  discovery

CANONICAL_PRECEDENTS was NOT modified in this refresh. All three entries
remain `concept:` placeholders pending human-authored real-hash seeding in a
follow-up WP.

## MAINTAINERS changes

The MAINTAINERS file grew from 23,942 to 29,495 lines (+23%). The ASoC
section's mailing list changed from `alsa-devel@alsa-project.org` to
`linux-sound@vger.kernel.org`. Maintainer persons (M: lines) are unchanged
(Girdwood + Brown). This did not affect benchmark metrics because maintainer
attribution is email-based, not list-based.

## References

- WP-KERNEL-REFRESH (this work package)
- Unblocks: WP-9.2a-polish-v3 (real precedent seeding)
- Prior context: revert 650540f (fabricated hashes), guardrail a60ef43
