"""Track-B.10 Live Review Session — generate real calibration triples via LLM.

Usage (from repo root: kri/):
    python3 scripts/run_live_calibration_b10.py

Output files (written to data/ledger/ sibling of kri/):
    ../data/ledger/calibration_triples_b10.jsonl  — one triple per line
    ../data/ledger/calibration_result_b10.json    — full run summary
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import sys
import traceback
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING)  # suppress noise
logger = logging.getLogger("b9_live")
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------
# Paths
# ---------------------------------------------------------------
DATA_DIR = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/data")
LORE_CACHE_DIR = DATA_DIR / "lore_cache"
LEDGER_DIR = DATA_DIR / "ledger"
# Production ReviewHistoryStore lives in lore_cache (matches web/app.py _default_cache_dir)
REVIEW_HISTORY_PATH = LORE_CACHE_DIR / "review_history.jsonl"

# ---------------------------------------------------------------
# Imports
# ---------------------------------------------------------------
from kri.lore_manager.mbox import parse_mbox_gz, parse_mbox_bytes  # noqa: E402
from kri.patch_manager.manager import PatchManagerImpl  # noqa: E402
from kri.learning.store import ReviewHistoryStore  # noqa: E402
from kri.learning.calibration import CFMCalibrator  # noqa: E402
from kri.confidence_engine.engine import ConfidenceEngineImpl  # noqa: E402
from kri.llm.client import LLMClient, LLMConfig  # noqa: E402
from kri.llm.reviewer import IntelligentReviewEngine  # noqa: E402

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _cmt_id(file_path: str, line_number: int, message: str) -> str:
    raw = f"{file_path}:{line_number}:{message[:80]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def pick_mbox_files(n: int = 7) -> list[Path]:
    """Pick n smallest .mbox.gz files so LLM calls are fast."""
    all_files = sorted(
        LORE_CACHE_DIR.glob("*.mbox.gz"),
        key=lambda p: p.stat().st_size,
    )
    # Prefer single-patch series (smaller files more likely to be 1-patch)
    return all_files[:n]


def parse_series_from_mbox_gz(path: Path):
    """Parse a .mbox.gz file into a PatchSeries (or None on error)."""
    pm = PatchManagerImpl()
    try:
        data = path.read_bytes()
        series = pm.parse(gzip.decompress(data))
        if not series.patches:
            return None
        return series
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", path.name, exc)
        return None


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main() -> dict:
    result: dict = {
        "triples_generated": 0,
        "mbox_files_reviewed": [],
        "sample_triples": [],
        "calibration_run": False,
        "pearson": None,
        "error": None,
    }

    # Set up store and calibrator
    store = ReviewHistoryStore(path=REVIEW_HISTORY_PATH)
    cfm_engine = ConfidenceEngineImpl()
    calibrator = CFMCalibrator(confidence_engine=cfm_engine, store=store)

    # Build the IntelligentReviewEngine with real LLM
    config = LLMConfig(
        timeout=90,
        max_retries=2,
    )
    client = LLMClient(config=config)
    engine = IntelligentReviewEngine(
        client=client,
        review_history_store=store,
        cfm_calibrator=calibrator,
        confidence_engine=cfm_engine,
        series_awareness=True,
    )

    # Pick enough files to reach _PEARSON_MIN_SAMPLES (50) triples; use all available
    mbox_files = pick_mbox_files(n=200)
    all_triples: list[tuple[str, float, str]] = []
    reviewed_files: list[str] = []
    llm_errors = 0

    for mbox_path in mbox_files:
        if llm_errors >= 3:
            result["error"] = "LLM_CALLS_FAILED: 3 consecutive errors"
            break

        series = parse_series_from_mbox_gz(mbox_path)
        if series is None:
            logger.info("Skipping %s — no patches", mbox_path.name)
            continue

        # Limit to single-patch series for speed
        if len(series.patches) > 3:
            logger.info("Skipping %s — %d patches (too large)", mbox_path.name, len(series.patches))
            continue

        logger.info("Reviewing %s (%d patch(es))...", mbox_path.name, len(series.patches))
        try:
            report = engine.review(series)
        except Exception as exc:
            llm_errors += 1
            logger.warning("LLM review failed for %s: %s", mbox_path.name, exc)
            traceback.print_exc()
            continue

        llm_errors = 0  # reset on success
        reviewed_files.append(mbox_path.name)

        for pr in report.patches:
            for c in pr.inline_comments:
                cmt_id = _cmt_id(c.file_path, c.line_number, c.message)
                cat = c.category or "general"
                triple = (cmt_id, float(c.confidence), cat)
                all_triples.append(triple)
                logger.info(
                    "  triple: id=%s conf=%.3f cat=%s file=%s line=%d",
                    cmt_id, c.confidence, cat, c.file_path, c.line_number
                )

        if len(all_triples) >= 60:
            break  # enough to clear _PEARSON_MIN_SAMPLES=50 gate

    result["mbox_files_reviewed"] = reviewed_files
    result["triples_generated"] = len(all_triples)
    result["sample_triples"] = [
        {"id": t[0], "conf": t[1], "cat": t[2]}
        for t in all_triples[:20]
    ]

    # Persist triples so downstream calibration can consume them without re-running LLM
    triples_out = LEDGER_DIR / "calibration_triples_b10.jsonl"
    triples_out.parent.mkdir(parents=True, exist_ok=True)
    with triples_out.open("w") as fh:
        import json as _json
        for t in all_triples:
            fh.write(_json.dumps({"comment_id": t[0], "llm_confidence": t[1], "claim_category": t[2]}) + "\n")
    result["triples_file"] = str(triples_out)
    logger.info("Wrote %d triples to %s", len(all_triples), triples_out)

    if not all_triples:
        if result["error"] is None:
            result["error"] = "NO_TRIPLES: LLM returned no inline comments from any series"
        return result

    # Run calibration
    try:
        calib_report = calibrator.calibrate(all_triples)
        result["calibration_run"] = True
        result["pearson"] = calib_report.cfm_vs_llm_correlation
        result["samples_calibrated"] = calib_report.samples_calibrated
        result["recommendation"] = calib_report.recommendation
        result["gate_criteria_status"] = calib_report.gate_criteria_status
        result["review_history_distribution"] = calib_report.review_history_distribution
        result["factor_contributions"] = calib_report.factor_contributions
        result["pearson_t_stat"] = calib_report.pearson_t_stat
        result["correlation_significant"] = calib_report.correlation_significant
        # LLM confidence distribution from triples
        confs = [t[1] for t in all_triples]
        result["llm_confidence_distribution"] = {
            "min": min(confs),
            "max": max(confs),
            "mean": round(sum(confs) / len(confs), 4),
            "std": round((sum((c - sum(confs)/len(confs))**2 for c in confs) / len(confs)) ** 0.5, 4),
            "n": len(confs),
        }
        logger.info(
            "Calibration: n=%d pearson=%s recommendation=%s",
            calib_report.samples_calibrated,
            calib_report.cfm_vs_llm_correlation,
            calib_report.recommendation,
        )
        logger.info("Gate criteria: %s", calib_report.gate_criteria_status)
    except Exception as exc:
        result["error"] = f"CALIBRATION_ERROR: {exc}"
        traceback.print_exc()

    return result


if __name__ == "__main__":
    import json
    out = main()

    # Write full result JSON alongside the triples
    out_path = (
        Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/data/ledger")
        / "calibration_result_b10.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Result written to: {out_path}")
    print(json.dumps(out, indent=2))
