"""WP-9.2a-polish-v3: enforce every seeded precedent commit hash exists in
the upstream kernel tree AND is relevant to the rule it's attached to
(existence != relevance). This guardrail caught the fabricated hashes
reverted in 650540f and must remain active permanently.

All CANONICAL_PRECEDENTS entries are now real, verified upstream commits
(WP-9.2a-polish-v3, 2026-07-20). The placeholder concept: strings and
their associated xfail/trip-wire tests have been removed per the migration
protocol documented in WP-9.2a-polish-v2 closeout notes.
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
    workspace_root = Path(__file__).resolve().parents[2]
    p = workspace_root / "data" / "kernel" / "linux"
    return p if p.exists() else None


def test_every_seeded_precedent_hash_exists_in_kernel_tree() -> None:
    """Every CANONICAL_PRECEDENTS entry must be a real commit that exists in
    the kernel tree AND touches the declared expected_path."""
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
                    f"relevance' gap -- the hash is real but wrong for this rule."
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
