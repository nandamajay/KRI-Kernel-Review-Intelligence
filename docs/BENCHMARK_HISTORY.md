# Benchmark History

Tracks `BenchmarkRunner.compare()` precision/recall/F1 against the cached
lore fixtures with real maintainer ground truth
(`tests/test_benchmark_regression.py`). Every future sprint MUST ratchet the
floor upward as real quality improves — never loosen it without a documented,
reviewed reason.

| date | precision | recall | f1 | commit | notes |
|---|---|---|---|---|---|
| 2026-07-16 | 0.1493 | 1.0000 | 0.2597 | 1fbf6f5 | Phase-A floor set: precision>=0.13, recall>=0.95, f1>=0.22 |
