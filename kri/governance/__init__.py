"""Constitutional rules for the KRI system.

Each rule captures a hard invariant from the Constitution (the authoritative
spec document). Rules are keyed by section number so cross-references in code
comments (e.g. "Sec. 9") resolve to a machine-readable record.

Usage::

    from kri.governance import load_rules, ConstitutionalRule

    rules = load_rules()            # list[ConstitutionalRule]
    rule_9 = rules.by_section("9")  # look up by section

    from kri.governance import check_sec40
    violations = check_sec40(source_text, rules)
"""

from __future__ import annotations

from .rules import ConstitutionalRule, ConstitutionalRules, load_rules
from .engine import check_sec40, log_governance_warnings

__all__ = [
    "ConstitutionalRule",
    "ConstitutionalRules",
    "load_rules",
    "check_sec40",
    "log_governance_warnings",
]
