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

## Implication for TP Floor

The effective true-positive count after discarding documented spurious agreements
is the **adjusted exact** metric. The PRECISION_FLOOR in
`tests/test_benchmark_regression.py` is set against the measured precision
(exact / total_decisions) without adjustment — conservative, since some "exact"
agreements may still be coincidental but undiscovered.

Current adjusted baseline: exact=19, precision=0.2043 (post WP-9.1c sub-commit 1).

## Future Work

- Replace the word-overlap heuristic with semantic-similarity scoring (embedding
  cosine or LLM judge) to reduce spurious matches.
- Add a `target_category` field to ReviewComment extraction so category-level
  matching can be exact rather than heuristic.
- Periodically audit remaining "exact" agreements for false coincidences (same
  methodology as FP_CLASSIFICATION but applied to TPs).
