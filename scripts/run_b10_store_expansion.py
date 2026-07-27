"""Track-B.10: Re-ingest all lore mbox.gz files to expand the ReviewHistoryStore.

The updated LoreIngestionEngine has expanded _CLAIM_SIGNALS that cover more
categories.  This script re-ingests every .mbox.gz in the production lore_cache
and appends only new (non-duplicate) entries to the production store.

Usage (from repo root: kri/):
    python3 scripts/run_b10_store_expansion.py

Output files (relative to /local/mnt/workspace/KRI_Kernel_Review_Intelligence/):
    data/ledger/store_expansion_b10.json  -- run summary + per-category breakdown

Deduplication key:
    ReviewHistoryEntry.make_entry_id(series_id, message_id, reviewer_text)
    — identical to how the ingestion engine constructs entry_id, so the store's
    internal dedup by entry_id is the canonical gate.

Safety invariants:
  - Load existing entries BEFORE adding anything (dedup set built first).
  - accepted_patch evidence_type is NOT relabelled as verified review_discussion.
  - All provenance fields are preserved verbatim.
  - Per-file errors are caught; the run continues on malformed mbox.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("b10_store_expansion")
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("/local/mnt/workspace/KRI_Kernel_Review_Intelligence/data")
LORE_CACHE_DIR = DATA_DIR / "lore_cache"
LEDGER_DIR = DATA_DIR / "ledger"
# Production ReviewHistoryStore (matches web/app.py _default_cache_dir convention)
REVIEW_HISTORY_PATH = LORE_CACHE_DIR / "review_history.jsonl"
RESULTS_PATH = LEDGER_DIR / "store_expansion_b10.json"

# ---------------------------------------------------------------------------
# Imports (after sys.path is set)
# ---------------------------------------------------------------------------
from kri.learning.ingestion import LoreIngestionEngine  # noqa: E402
from kri.learning.store import ReviewHistoryStore  # noqa: E402
from kri.lore_manager.manager import LoreManagerImpl, LoreConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_series_id(mbox_path: Path) -> str:
    """Derive a stable series_id from the mbox filename stem.

    The stem is already a deterministic slug produced by LoreManagerImpl._cache_key
    (safe characters + SHA-1 digest suffix), so it is a safe series_id.
    """
    return mbox_path.stem  # e.g. "20231201-descriptors-sound-...abcd12345678"


def _build_dedup_set(store: ReviewHistoryStore) -> set[str]:
    """Return the set of all existing entry_ids (canonical dedup gate)."""
    return {e.entry_id for e in store.all()}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> dict:
    """Run the B10 store expansion.  Returns a summary dict."""

    result: dict = {
        "entries_before": 0,
        "new_entries_added": 0,
        "entries_after": 0,
        "files_processed": 0,
        "files_errored": 0,
        "per_category_new": {},
        "categories_with_verified_rd_ack_nack": [],
        "error": None,
    }

    # ------------------------------------------------------------------
    # 1. Open the production store and snapshot the pre-expansion state.
    # ------------------------------------------------------------------
    LORE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)

    store = ReviewHistoryStore(path=REVIEW_HISTORY_PATH)
    entries_before = store.count()
    result["entries_before"] = entries_before
    logger.info("Store loaded: %d existing entries from %s", entries_before, REVIEW_HISTORY_PATH)

    # Build dedup set BEFORE adding anything (safety invariant).
    existing_ids: set[str] = _build_dedup_set(store)
    logger.info("Dedup set built: %d entry_ids", len(existing_ids))

    # ------------------------------------------------------------------
    # 2. Initialise LoreManagerImpl (offline — only load_cached() used).
    # ------------------------------------------------------------------
    lore_config = LoreConfig(
        cache_dir=LORE_CACHE_DIR,
        offline=True,  # no network I/O — purely local re-ingestion
    )
    lore_manager = LoreManagerImpl(config=lore_config)
    engine = LoreIngestionEngine(lore_manager=lore_manager)

    # ------------------------------------------------------------------
    # 3. Re-ingest all .mbox.gz files.
    # ------------------------------------------------------------------
    mbox_files = sorted(LORE_CACHE_DIR.glob("*.mbox.gz"))
    logger.info("Found %d .mbox.gz files in %s", len(mbox_files), LORE_CACHE_DIR)

    per_category_new: dict[str, int] = defaultdict(int)
    new_entries_added = 0

    for mbox_path in mbox_files:
        series_id = _derive_series_id(mbox_path)
        try:
            entries = engine.ingest(
                mbox_path=mbox_path,
                series_id=series_id,
                lore_url="",  # derived from message_id inside the engine
            )
        except Exception as exc:
            logger.warning(
                "B10: error ingesting %s: %s", mbox_path.name, exc
            )
            logger.debug(traceback.format_exc())
            result["files_errored"] = result["files_errored"] + 1
            continue

        result["files_processed"] = result["files_processed"] + 1

        for entry in entries:
            # Deduplication: skip if already present (by canonical entry_id).
            if entry.entry_id in existing_ids:
                continue

            # Safety invariant: do NOT reclassify accepted_patch as
            # verified review_discussion — preserve evidence_type verbatim.

            added = store.add(entry)
            if added:
                existing_ids.add(entry.entry_id)
                per_category_new[entry.extracted_claim] += 1
                new_entries_added += 1

    entries_after = store.count()

    # ------------------------------------------------------------------
    # 4. Identify categories with at least one verified RD/ACK/NACK entry.
    # ------------------------------------------------------------------
    # "verified" here means evidence_type in (maintainer_ack, maintainer_nack,
    # review_discussion) — not the EKG verified flag.
    verified_types = {"maintainer_ack", "maintainer_nack", "review_discussion"}
    cats_with_signal: set[str] = set()
    for entry in store.all():
        if entry.evidence_type in verified_types:
            cats_with_signal.add(entry.extracted_claim)

    # ------------------------------------------------------------------
    # 5. Populate result dict.
    # ------------------------------------------------------------------
    result["entries_before"] = entries_before
    result["new_entries_added"] = new_entries_added
    result["entries_after"] = entries_after
    result["per_category_new"] = dict(sorted(per_category_new.items()))
    result["categories_with_verified_rd_ack_nack"] = sorted(cats_with_signal)

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        summary = main()
    except Exception as exc:
        logger.error("B10 store expansion fatal error: %s", exc)
        traceback.print_exc()
        summary = {"error": str(exc)}

    # Write results
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(summary, indent=2))
    print(f"Results written to: {RESULTS_PATH}")
    print()
    print("=== B10 Store Expansion Summary ===")
    print(f"  Entries before : {summary.get('entries_before', '?')}")
    print(f"  New entries    : {summary.get('new_entries_added', '?')}")
    print(f"  Entries after  : {summary.get('entries_after', '?')}")
    print(f"  Files processed: {summary.get('files_processed', '?')}")
    print(f"  Files errored  : {summary.get('files_errored', '?')}")
    print()
    per_cat = summary.get("per_category_new", {})
    if per_cat:
        print("  Per-category new entries:")
        for cat, count in sorted(per_cat.items(), key=lambda kv: -kv[1]):
            print(f"    {cat:40s} {count:4d}")
    else:
        print("  No new entries added (store already up to date).")
    print()
    cats = summary.get("categories_with_verified_rd_ack_nack", [])
    print(f"  Categories with >= 1 verified RD/ACK/NACK entry ({len(cats)}):")
    for cat in cats:
        print(f"    - {cat}")
    if summary.get("error"):
        print(f"\n  ERROR: {summary['error']}")
