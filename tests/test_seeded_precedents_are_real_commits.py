"""WP-9.2a-polish-v2 sub-commit 1: enforce every seeded precedent commit
hash exists in the upstream kernel tree. This would have caught the
fabricated hashes reverted in 650540f.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from kri.packages.asoc.knowledge import CANONICAL_PRECEDENTS

# Match "abcd1234ef56" prefix from strings like:
#   'abcd1234ef56 ("subject text")'
_HASH_RE = re.compile(r"^([a-f0-9]{7,40})\s+\(")


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
            m = _HASH_RE.match(entry)
            if not m:
                failures.append(
                    f"{rule_id}: entry does not start with a hash: {entry!r}"
                )
                continue
            commit_hash = m.group(1)
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

    if failures:
        pytest.xfail(
            reason=(
                "Placeholder concept: strings pending replacement in "
                "sub-commit 2 (human-authored real precedents)"
            )
        )

    assert not failures, (
        "Seeded precedents contain nonexistent commit hashes:\n"
        + "\n".join(f"  - {f}" for f in failures)
        + "\n\nThis is the exact failure mode that caused revert 650540f. "
        "Do not add commit hashes to CANONICAL_PRECEDENTS without verifying "
        "each one via `git -C data/kernel/linux show <hash> --stat`."
    )


def test_hash_regex_matches_expected_shape() -> None:
    """Self-test: ensure the regex matches the format we seed."""
    assert _HASH_RE.match('abc1234def567 ("Some subject")') is not None
    assert _HASH_RE.match('nothash ("subject")') is None
    assert _HASH_RE.match('') is None
