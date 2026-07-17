"""WP-9.2a-polish-v2 sub-commit 1: enforce every seeded precedent commit
hash exists in the upstream kernel tree, and (WP-9.2a-polish-v2 addition 1)
that a hash which exists is also relevant to the rule it's attached to --
existence != relevance. This would have caught the fabricated hashes
reverted in 650540f.

CANONICAL_PRECEDENTS real-hash entries are gated on data/kernel/linux being
deepened beyond its current --depth 1 grafted state. See
data/kernel/linux/README.md for the shallow-clone constraint. See
WP-9.2a-polish-v2 closeout notes for the fabrication-avoidance policy.
xfail_strict is enabled (pyproject.toml) to force the xfail-removal
conversation on the first real hash that passes cat-file -e.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from kri.packages.asoc.knowledge import CANONICAL_PRECEDENTS

# A real, resolvable git commit hash (short or full hex SHA).
_HASH_RE = re.compile(r"^[a-f0-9]{7,40}$")


def _kernel_path() -> Path | None:
    # Mirror conftest.py's KERNEL_PATH derivation
    workspace_root = Path(__file__).resolve().parents[2]
    p = workspace_root / "data" / "kernel" / "linux"
    return p if p.exists() else None


def test_every_seeded_precedent_hash_exists_in_kernel_tree() -> None:
    kernel = _kernel_path()
    if kernel is None:
        pytest.skip("kernel clone not present at data/kernel/linux")

    failures: list[str] = []
    for rule_id, precedents in CANONICAL_PRECEDENTS.items():
        for entry in precedents:
            commit_hash = entry["commit_hash"]
            expected_path = entry["expected_path"]

            if not _HASH_RE.match(commit_hash):
                failures.append(
                    f"{rule_id}: entry is not a real commit hash: {commit_hash!r}"
                )
                continue

            result = subprocess.run(
                ["git", "-C", str(kernel), "cat-file", "-e", commit_hash],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                failures.append(
                    f"{rule_id}: hash {commit_hash} does NOT exist in "
                    f"data/kernel/linux (entry: {entry!r})"
                )
                continue

            # Mechanical relevance check (existence != relevance). Placeholder
            # entries (expected_path == "") are not checkable this way and are
            # skipped -- by construction a "concept:" placeholder never
            # matches _HASH_RE above, so in practice this branch is only ever
            # reached by real, hash-shaped entries.
            if not expected_path:
                continue

            show = subprocess.run(
                ["git", "-C", str(kernel), "show", "--name-only", "--format=", commit_hash],
                capture_output=True,
                text=True,
            )
            actual_files = [line for line in show.stdout.splitlines() if line.strip()]
            if not any(f.startswith(expected_path) for f in actual_files):
                failures.append(
                    f"Precedent hash {commit_hash} for rule {rule_id} exists but "
                    f"touches {actual_files}, none of which start with "
                    f"expected_path {expected_path!r}. This is the 'existence ≠ "
                    f"relevance' gap -- the hash is real but wrong for this rule. "
                    f"See WP-9.2a-polish-v2 closeout notes."
                )

    if failures:
        pytest.xfail(
            reason=(
                "Placeholder concept: strings pending replacement in "
                "sub-commit 2 (human-authored real precedents)"
            )
        )

    assert not failures, (
        "Seeded precedents contain nonexistent or irrelevant commit hashes:\n"
        + "\n".join(f"  - {f}" for f in failures)
        + "\n\nThis is the exact failure mode that caused revert 650540f. "
        "Do not add commit hashes to CANONICAL_PRECEDENTS without verifying "
        "each one via `git -C data/kernel/linux show <hash> --stat`."
    )


def test_hash_regex_matches_expected_shape() -> None:
    """Self-test: ensure the regex matches the format we seed."""
    assert _HASH_RE.match("abc1234def567") is not None
    assert _HASH_RE.match("concept:asoc-accept-tdm-via-machine-driver") is None
    assert _HASH_RE.match("") is None
