"""ConstitutionalRule model and YAML loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_RULES_YAML = Path(__file__).parent / "constitutional_rules.yaml"


class ConstitutionalRule(BaseModel):
    section: str
    title: str
    category: str
    summary: str
    enforced_by: list[str] = Field(default_factory=list)


class ConstitutionalRules:
    """Container for a loaded set of constitutional rules."""

    def __init__(self, rules: list[ConstitutionalRule]) -> None:
        self._rules = rules
        self._by_section: dict[str, ConstitutionalRule] = {r.section: r for r in rules}

    def __iter__(self):
        return iter(self._rules)

    def __len__(self) -> int:
        return len(self._rules)

    def by_section(self, section: str) -> ConstitutionalRule | None:
        return self._by_section.get(section)

    def by_category(self, category: str) -> list[ConstitutionalRule]:
        return [r for r in self._rules if r.category == category]


def load_rules(path: Path | None = None) -> ConstitutionalRules:
    """Load and validate the constitutional rules YAML.

    Args:
        path: Override the default ``constitutional_rules.yaml`` location.
              Useful in tests.
    """
    source = path or _RULES_YAML
    raw: dict[str, Any] = yaml.safe_load(source.read_text(encoding="utf-8"))
    rules = [ConstitutionalRule.model_validate(entry) for entry in raw.get("rules", [])]
    return ConstitutionalRules(rules)
