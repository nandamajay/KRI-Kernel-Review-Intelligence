"""Tests for kri.governance wiring into IntelligentReviewEngine.

Covers:
- Engine init loads constitutional rules (TB88-1)
- All 5 expected sections present with correct titles (TB88-2)
- Engine init survives missing YAML — fallback to empty ConstitutionalRules (TB88-3)
- check_sec40() detects each Sec-40 forbidden pattern (TB88-4)
- check_sec40() is a no-op when Sec-40 rule absent from loaded rules (TB88-5)
- log_governance_warnings() emits logger.warning per violation (TB88-6)
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kri.governance import ConstitutionalRules, check_sec40, load_rules, log_governance_warnings
from kri.governance.rules import ConstitutionalRule
from kri.llm.reviewer import IntelligentReviewEngine


# ---------------------------------------------------------------------------
# Shared engine factory (no-op LLM client)
# ---------------------------------------------------------------------------


def _engine() -> IntelligentReviewEngine:
    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    return IntelligentReviewEngine(client=client)


# ---------------------------------------------------------------------------
# TB88-1: engine._governance_rules is a non-empty ConstitutionalRules
# ---------------------------------------------------------------------------


def test_TB88_engine_init_loads_governance_rules():
    """Engine init must assign a non-empty ConstitutionalRules to
    self._governance_rules when the YAML is on-disk."""
    engine = _engine()
    assert hasattr(engine, "_governance_rules")
    assert isinstance(engine._governance_rules, ConstitutionalRules)
    assert len(engine._governance_rules) > 0, (
        "governance rules must not be empty after engine init"
    )


# ---------------------------------------------------------------------------
# TB88-2: all 5 sections present with correct titles
# ---------------------------------------------------------------------------


def test_TB88_engine_governance_rules_contains_expected_sections():
    """All five constitutional rules must be present and have the correct
    section number and title after engine construction."""
    engine = _engine()
    rules = engine._governance_rules

    assert len(rules) == 5

    expected = {
        "9": "Domain Isolation",
        "28": "Constitutional Evidence Gate",
        "31": "Determinism",
        "37": "Snapshot Identity",
        "40": "Stochastic Confinement",
    }
    for section, title in expected.items():
        rule = rules.by_section(section)
        assert rule is not None, f"section {section!r} missing from governance rules"
        assert rule.title == title, (
            f"section {section!r}: expected title {title!r}, got {rule.title!r}"
        )


# ---------------------------------------------------------------------------
# TB88-3: engine init survives missing YAML — fallback to empty rules
# ---------------------------------------------------------------------------


def test_TB88_engine_init_survives_missing_governance_yaml(monkeypatch, tmp_path):
    """If the YAML path does not exist, engine construction must not raise.
    self._governance_rules must be a valid (empty) ConstitutionalRules."""
    import kri.governance.rules as rules_mod

    missing = tmp_path / "no_such_file.yaml"
    original = rules_mod._RULES_YAML
    rules_mod._RULES_YAML = missing
    try:
        engine = _engine()  # must not raise
        assert isinstance(engine._governance_rules, ConstitutionalRules)
        # Empty rules — but the attribute is the correct type, not None
        assert len(engine._governance_rules) == 0
    finally:
        rules_mod._RULES_YAML = original


# ---------------------------------------------------------------------------
# TB88-4: check_sec40 detects each forbidden pattern
# ---------------------------------------------------------------------------

_SEC40_FIXTURES = [
    ("import random\n", "random"),
    ("x = time.time()\n", "time."),
    ("ts = datetime.now()\n", "datetime.now"),
    ("uid = uuid.uuid1()\n", "uuid.uuid1"),
    ("uid = uuid.uuid4()\n", "uuid.uuid4"),
]


@pytest.mark.parametrize("source,expected_pattern", _SEC40_FIXTURES)
def test_TB88_check_sec40_detects_forbidden_pattern(source: str, expected_pattern: str):
    """check_sec40 must return a non-empty list for each Sec-40 forbidden
    pattern when the Sec-40 rule is loaded."""
    rules = load_rules()
    violations = check_sec40(source, rules)
    assert any(expected_pattern in v for v in violations), (
        f"check_sec40 should detect {expected_pattern!r} in {source!r}; got {violations}"
    )


def test_TB88_check_sec40_clean_source_returns_empty():
    """check_sec40 must return an empty list for source with no violations."""
    rules = load_rules()
    clean = "import hashlib\ndef foo():\n    return hashlib.sha256(b'x').hexdigest()\n"
    assert check_sec40(clean, rules) == []


# ---------------------------------------------------------------------------
# TB88-5: check_sec40 is no-op when Sec-40 rule absent
# ---------------------------------------------------------------------------


def test_TB88_check_sec40_noop_when_sec40_rule_absent():
    """If the ConstitutionalRules has no section '40', check_sec40 must
    return [] regardless of source content."""
    empty_rules = ConstitutionalRules([])
    source = "import random\nx = time.time()\n"
    assert check_sec40(source, empty_rules) == []


# ---------------------------------------------------------------------------
# TB88-6: log_governance_warnings emits logger.warning per violation
# ---------------------------------------------------------------------------


def test_TB88_log_governance_warnings_emits_for_violation(caplog):
    """log_governance_warnings must emit one WARNING per Sec-40 violation."""
    rules = load_rules()
    source = "import random\n"
    with caplog.at_level(logging.WARNING, logger="kri.governance.engine"):
        log_governance_warnings(source, rules, module_name="test_module.py")
    assert any("Sec-40" in r.message for r in caplog.records), (
        "expected a Sec-40 warning in caplog"
    )
    assert any("test_module.py" in r.message for r in caplog.records)


def test_TB88_log_governance_warnings_silent_for_clean_source(caplog):
    """log_governance_warnings must emit no warnings for clean source."""
    rules = load_rules()
    clean = "import hashlib\n"
    with caplog.at_level(logging.WARNING, logger="kri.governance.engine"):
        log_governance_warnings(clean, rules, module_name="clean.py")
    assert not any("Sec-40" in r.message for r in caplog.records)
