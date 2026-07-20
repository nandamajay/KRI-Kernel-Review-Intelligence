# Kernel Tree Usage Analysis

**Date:** 2026-07-20
**Purpose:** Document how KRI uses the kernel reference tree to inform baseline decisions.

## Usage Paths

### 1. `git blame(file, line)` — RepoManager
**Code:** `kri/repo_manager/manager.py:237-272`

Runs `git blame` against the checked-out HEAD to find who last modified a given
line. Used for identifying the author/context of existing code that a patch
modifies.

**Version sensitivity:** HIGH. Blame results change with every commit. A newer
HEAD means blame reflects more recent authorship/changes. For patches targeting
the current merge window, blame against a recent tree is more accurate.

### 2. `git apply(series)` — RepoManager
**Code:** `kri/repo_manager/manager.py:153-212`

Applies a patch to the working tree to verify it applies cleanly. This is
version-critical — a patch written against today's tree won't apply cleanly to
a stale baseline.

**Version sensitivity:** HIGH. Patches on lore right now are written against
linux-next or the current RC. A mismatch causes spurious apply failures.

### 3. `git diff(a, b)` — RepoManager
**Code:** `kri/repo_manager/manager.py:275-288`

Computes diffs between arbitrary commits. Requires both commits to exist in the
history.

**Version sensitivity:** NONE — with the full-history clone, all commits are
present regardless of HEAD position.

### 4. MAINTAINERS file parsing
**Code:** `kri/lore_manager/maintainers.py` + `tests/conftest.py:20`

Reads `data/kernel/linux/MAINTAINERS` on disk to identify subsystem
maintainers/reviewers and tag `is_maintainer` on review comments.

**Version sensitivity:** MODERATE. Maintainer lists and mailing list addresses
change over time. The v6.6→v7.1.3 refresh already caught the
`alsa-devel@alsa-project.org` → `linux-sound@vger.kernel.org` change.

### 5. `scripts/checkpatch.pl` execution — StaticAnalysisManager
**Code:** `kri/static_analysis/manager.py:50, 70-71`

Locates and runs `scripts/checkpatch.pl` from the tree. The script version
should match the kernel version being developed against.

**Version sensitivity:** MODERATE. Newer checkpatch has more checks and fewer
false positives on modern patterns.

### 6. Provenance repo_path verification — EvidenceEngine
**Code:** `kri/evidence_engine/engine.py:181-183`

Evidence verification checks if `provenance.repo_path` is "set and non-empty" —
does NOT stat the file on disk. String-presence check only.

**Version sensitivity:** NONE.

### 7. CANONICAL_PRECEDENTS hash verification — test guardrail
**Code:** `tests/test_seeded_precedents_are_real_commits.py:52-56`

Runs `git cat-file -e <hash>` to verify seeded precedent commits exist. Uses
the object store directly, not HEAD.

**Version sensitivity:** NONE (full history present regardless of HEAD).

### 8. ASoC DKP knowledge provenance strings
**Code:** `kri/packages/asoc/knowledge.py:51, 67`

Hardcoded `version_or_commit="v6.6"` in Provenance objects. These are metadata
labels indicating when the knowledge was sourced, not runtime lookups.

**Version sensitivity:** NONE for runtime. Historical provenance label.

### 9. Documentation/header path existence (ASoC citation verification)

Stable kernel documentation and header files that exist in all relevant versions.

**Version sensitivity:** LOW.

## Benchmark Impact

The benchmark runs entirely from cached lore fixtures (`data/lore_cache/`). It
does NOT touch the kernel tree during execution. Changing HEAD cannot affect
pinned benchmark metrics.

## Conclusion

The two most version-sensitive operations (git apply, git blame) strongly favour
the most current mainline HEAD. Since KRI reviews patches targeting the current
merge window, the runtime baseline should track the latest RC tag.
