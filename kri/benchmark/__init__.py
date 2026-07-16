"""Benchmark Framework (SPEC §11 / Blueprint Sec. 19).

Measures KRI review quality against real maintainer ground truth.
"""

from .runner import AgreementResult, BenchmarkMetrics, BenchmarkRunner

__all__ = ["BenchmarkRunner", "BenchmarkMetrics", "AgreementResult"]
