# Benchmark History

Tracks `BenchmarkRunner.compare()` precision/recall/F1 against the cached
lore fixtures with real maintainer ground truth
(`tests/test_benchmark_regression.py`). Every future sprint MUST ratchet the
floor upward as real quality improves — never loosen it without a documented,
reviewed reason.

| date | precision | recall | f1 | commit | notes |
|---|---|---|---|---|---|
| 2026-07-16 | 0.1493 | 1.0000 | 0.2597 | 1fbf6f5 | Phase-A floor set: precision>=0.13, recall>=0.95, f1>=0.22 |
| 2026-07-16 | 0.1493 | 1.0000 | 0.2597 | 956b73a | WP-9.1a (sequence fix + SeriesContext + cross-patch resolver): unchanged. Floor NOT ratcheted -- see WP-9.1a close-out report for why the cached-fixture benchmark doesn't exercise the FP class this WP targets. |
| 2026-07-17 | 0.2043 | 1.0000 | 0.3393 | b06881c | WP-9.1c sub-commit 1: TDM signal tightening (conjunctive window). 40 FPs eliminated. 1 spurious TP lost (see BENCHMARK_ORACLE_LIMITATIONS.md). Floor ratcheted: precision>=0.19. |
| 2026-07-17 | 0.5909 | 1.0000 | 0.7429 | pending | WP-9.1c sub-commit 2: resume signal tightening (context-aware). 65 FPs eliminated. 6 TPs lost (4 spurious + 2 spec-limitation, all documented). Floor ratcheted: precision>=0.55, f1>=0.70. |
