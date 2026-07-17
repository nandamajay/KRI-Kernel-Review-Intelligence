# Benchmark Oracle Limitations

## Limitation

The `BenchmarkRunner.compare()` oracle uses two heuristics to score a Decision
as "exact agreement" with a maintainer ground-truth comment:

1. **patch_id match** — the Decision and the ReviewComment target the same patch.
2. **Word-overlap match** — the Decision's `statement` and the comment's `message`
   share >= 3 words.

Neither heuristic proves semantic agreement. A Decision can fire on Patch X for
Reason A while the maintainer commented on Patch X for Reason B, and the oracle
scores it as "exact" if their text happens to share vocabulary. This inflates the
true-positive count by counting coincidental co-location as agreement.

## Documented Discards

When a rule tightening correctly eliminates a false firing, the benchmark may
report a "TP loss" if the old false firing happened to land on a patch with an
unrelated maintainer comment. Each such case is documented here with evidence
that the old agreement was coincidental. Silent TP loss (undocumented drop in
exact count) remains a STOP condition.

| date | fixture | rule | old exact | new exact | evidence of spurious agreement |
|---|---|---|---|---|---|
| 2026-07-17 | `20240305-rk3308-audio-codec-v4-2-312acdbe628f_bootlin.com` | `asoc-tdm-slot-not-userspace` | 1 | 0 | Rule fired on patch-1 because `i2s_tdm->mclk_tx` contains substring "tdm". Patch-1 is an I2S clock fix with no SOC_ENUM. Maintainer comment on patch-1 is about DT bindings ("a question about DT bindings"), unrelated to TDM slot kcontrols. Word-overlap heuristic coincidentally matched. |
| 2026-07-17 | `20260518024704.118613-1-YLCHANG2_nuvoton.com` | `asoc-resume-must-clean-up` | 3 | 2 | Rule fired because "resume" appeared in `nau8360_resume()` function definition. Maintainer comment says "There's no update to the build system, this can't have been tested" — about Kconfig/Makefile, not resume cleanup. |
| 2026-07-17 | `20260609024128.585938-1-YLCHANG2_nuvoton.com` | `asoc-resume-must-clean-up` | 3 | 2 | Rule fired on `nau8360_resume()`. Maintainer comment says "improvements and nitpicks which scale for the entire file" — generic code style feedback, not resume cleanup. |
| 2026-07-17 | `20250910121917.458-1-niranjan.hy_ti.com` | `asoc-resume-must-clean-up` | 2 | 1 | Rule fired because "resume" appeared in `tas2783_sdca_dev_resume()`. Maintainer comment says "This config symbol doesn't exist already and isn't defined by the patch" — about Kconfig, not resume cleanup. |
| 2026-07-17 | `MN0PR11MB5985...d84d0c4d1f95` | `asoc-resume-must-clean-up` | 1 | 0 | Rule fired because "kzalloc" appeared in `cht_bsw_rt5672` probe code (disjunctive match on allocation signal alone, no resume handler present). Maintainer comment says "One nit...Reviewed-by" — unrelated. |
| 2026-07-17 | `20260708093506.895481-1-YLCHANG2_nuvoton.com` | `asoc-resume-must-clean-up` | 2 | 1 | **REAL TP lost (spec limitation)**: Maintainer explicitly said "This gets run every resume but there's no cleanup." The resume handler (`nau8360_resume`) calls `nau8360_dsp_setup()` without cleanup, but uses no kzalloc/kmalloc — the anti-pattern is repeated initialization, not memory allocation. The spec's requirement for allocation signals is too narrow for this case. |
| 2026-07-17 | `20260708093506_895481-1-YLCHANG2_nuvoton_com` | `asoc-resume-must-clean-up` | 2 | 1 | **REAL TP lost (spec limitation)**: Same fixture, deduplicated lore thread file. Same root cause as above. |

## Implication for TP Floor

The effective true-positive count after discarding documented spurious agreements
is the **adjusted exact** metric. The PRECISION_FLOOR in
`tests/test_benchmark_regression.py` is set against the measured precision
(exact / total_decisions) without adjustment — conservative, since some "exact"
agreements may still be coincidental but undiscovered.

Current adjusted baseline: exact=13, precision=0.5909 (post WP-9.1c sub-commits 1+2).
Of 7 total discarded TPs: 5 proven spurious (coincidental word overlap), 2 real
(spec limitation — the resume rule's allocation requirement is too narrow for
non-allocation resource-initialization anti-patterns).

## Future Work

- Replace the word-overlap heuristic with semantic-similarity scoring (embedding
  cosine or LLM judge) to reduce spurious matches.
- Add a `target_category` field to ReviewComment extraction so category-level
  matching can be exact rather than heuristic.
- Periodically audit remaining "exact" agreements for false coincidences (same
  methodology as FP_CLASSIFICATION but applied to TPs).
