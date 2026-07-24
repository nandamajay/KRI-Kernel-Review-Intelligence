"""Governance enforcement helpers.

Provides deterministic checks against constitutional rules loaded by
:func:`kri.governance.load_rules`.  All functions are pure (no I/O) so
they can be called at engine-init time without side effects beyond logging.
"""

from __future__ import annotations

import logging
import re

from kri.governance.rules import ConstitutionalRules

logger = logging.getLogger(__name__)

# Symbols forbidden outside kri/learning/ per Sec. 40.
# Each entry: (human-readable label, compiled pattern)
_SEC40_CHECKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("random", re.compile(r"\brandom\b")),
    ("time.", re.compile(r"\btime\.\w")),
    ("datetime.now", re.compile(r"\bdatetime\.now\b")),
    ("uuid.uuid1", re.compile(r"\buuid\.uuid1\b")),
    ("uuid.uuid4", re.compile(r"\buuid\.uuid4\b")),
)


def check_sec40(source: str, rules: ConstitutionalRules) -> list[str]:
    """Return a list of Sec-40 violation labels found in *source*.

    Each returned string is a human-readable label (e.g. ``"random"``,
    ``"time."``). Returns an empty list if the Sec-40 rule is not loaded
    or no matches are found.

    Args:
        source: Python source code to scan (a single file's text).
        rules:  Loaded constitutional rules.  If the Sec-40 rule (section
                "40") is absent, this function is a no-op.
    """
    if rules.by_section("40") is None:
        return []
    violations = []
    for label, pat in _SEC40_CHECKS:
        if pat.search(source):
            violations.append(label)
    return violations


def log_governance_warnings(module_source: str, rules: ConstitutionalRules,
                            module_name: str = "<unknown>") -> None:
    """Log a WARNING for each Sec-40 violation found in *module_source*.

    Args:
        module_source: Python source text of the module being reviewed.
        rules:         Loaded constitutional rules.
        module_name:   Identifier used in log messages (e.g. the file path).
    """
    violations = check_sec40(module_source, rules)
    for v in violations:
        logger.warning(
            "Sec-40 governance violation in %s: forbidden pattern %r",
            module_name,
            v,
        )
