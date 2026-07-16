"""ASoC reasoning plugins (Sprint-2) — the domain reasoning hooks the Review Engine
(Sprint-3) invokes.

**Contract for the Review Engine (SPEC §3 / §7):**
Each plugin satisfies :class:`kri.common.interfaces.ReasoningPlugin`:

  * ``plugin_id: str``       — stable id (also used as the Decision.category tag).
  * ``trigger: str``         — a generic trigger expression the Engine matches:
        ``"file_touched:<glob>"``  or  ``"api_used:<symbol_glob>"``.
    The Engine evaluates triggers generically; the plugin does not re-check them
    (but ``applies()`` is a cheap idempotent guard).
  * ``applies(patch, series) -> bool`` — cheap filter; the Engine calls this
    before ``evaluate``.
  * ``evaluate(patch, series) -> list[Decision]`` — returns language-agnostic
    :class:`Decision`s with NO evidence_graph and NO confidence yet. The Engine
    then routes each Decision through EvidenceEngine.gather/verify and
    ConfidenceEngine.score; unpublishable ones are dropped. Plugins therefore
    MUST set ``rule_id``/``pattern_id`` so the Evidence Engine can pull the
    supporting Evidence nodes the DKP seeded (via KnowledgeManager.get_evidence).

Determinism: ``evaluate`` is a pure function of (patch, series); it scans added
diff lines for pattern signals in a fixed order and emits Decisions sorted by
``decision_id``. No wall-clock, no RNG (Constitution Sec. 31/40).

These plugins detect *candidate* concerns and cite the rule/pattern they rest on;
they never fabricate a maintainer decision — the grounding lives in the seeded
Rule/Pattern/Evidence nodes with real provenance.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from kri.common.models import (
    Decision,
    PatchSeries,
    ReasoningLayer,
    Severity,
)
from kri.common.models import (
    Patch as CorePatch,
)

from .knowledge import ASOC_ROOT, build_patterns

_LAYER_MAP = {
    "structural": ReasoningLayer.STRUCTURAL,
    "semantic": ReasoningLayer.SEMANTIC,
    "design": ReasoningLayer.DESIGN,
}


def _added_lines(diff: str) -> list[str]:
    """Return added diff lines (``+`` but not ``+++`` file headers), lower-cased
    once for signal matching. Deterministic."""
    out: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return out


class PatternMatchPlugin:
    """Generic-by-construction ASoC plugin: matches one seeded review Pattern.

    One instance per pattern in the seeded library, so the Review Engine gets a
    focused plugin per concern. All ASoC specificity lives in the Pattern data
    (signals/rule/provenance), keeping this class thin and auditable.
    """

    def __init__(self, pattern: dict[str, Any]) -> None:
        self._p = pattern
        self._signals = [s.lower() for s in pattern.get("signals", [])]

    @property
    def plugin_id(self) -> str:
        return f"asoc:{self._p['pattern_id']}"

    @property
    def trigger(self) -> str:
        # ASoC plugins are gated on touching the subsystem root.
        return f"file_touched:{ASOC_ROOT}*"

    @property
    def layer(self) -> ReasoningLayer:
        return _LAYER_MAP.get(self._p.get("layer", "design"), ReasoningLayer.DESIGN)

    def applies(self, patch: CorePatch, series: PatchSeries) -> bool:
        """Cheap guard: the patch must touch an ASoC file (trigger glob) AND, for a
        rejected-pattern, exhibit at least one signal in its added lines."""
        if not any(_owns(f) for f in patch.files_changed):
            return False
        if not self._signals:
            return True
        adds = "\n".join(_added_lines(patch.diff)).lower()
        return any(sig in adds for sig in self._signals)

    def evaluate(self, patch: CorePatch, series: PatchSeries) -> list[Decision]:
        """Emit a candidate Decision if the pattern's signals are present.

        The Decision carries the pattern_id AND its supporting rule_id so the
        Evidence Engine can gather the seeded Evidence (real provenance). Evidence
        and confidence are intentionally left unset (the Engine fills them)."""
        if not self.applies(patch, series):
            return []
        adds = _added_lines(patch.diff)
        matched = sorted({s for s in self._signals if any(s in ln.lower() for ln in adds)})
        if self._signals and not matched:
            return []

        outcome = self._p.get("outcome", "rejected")
        # Only *rejected* patterns become concerns to surface; accepted patterns
        # are corroborating precedents (used by the Evidence/Confidence engines),
        # not findings, so we do not raise them as Decisions here.
        if outcome != "rejected":
            return []

        severity = Severity.WARNING
        decision = Decision(
            decision_id=f"asoc:{self._p['pattern_id']}:{patch.patch_id}",
            series_id=series.series_id,
            patch_id=patch.patch_id,
            layer=self.layer,
            category=self._p["pattern_id"],
            severity=severity,
            location=_first_owned_file(patch),
            statement=self._p["description"],
            rule_id=self._p.get("rule_id"),
            pattern_id=self._p["pattern_id"],
            evidence_graph=None,
            confidence=None,
        )
        return [decision]


def _owns(path: str) -> bool:
    return path.startswith(ASOC_ROOT) or fnmatch.fnmatch(path, "include/sound/soc*.h")


def _first_owned_file(patch: CorePatch) -> str | None:
    for f in sorted(patch.files_changed):
        if _owns(f):
            return f
    return None


def build_reasoning_plugins() -> list[PatternMatchPlugin]:
    """Instantiate one :class:`PatternMatchPlugin` per seeded review pattern.

    Deterministic ordering by ``plugin_id`` so the Review Engine sees a stable
    plugin list."""
    plugins = [PatternMatchPlugin(p) for p in build_patterns()]
    plugins.sort(key=lambda pl: pl.plugin_id)
    return plugins


__all__ = ["PatternMatchPlugin", "build_reasoning_plugins"]
