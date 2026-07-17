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
    SeriesContext,
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
    """Return added diff lines (``+`` but not ``+++`` file headers), preserving
    original case. Deterministic."""
    out: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return out


def _check_conjunctive_window(
    adds: list[str],
    require: list[str],
    any_of: list[str],
    window: int = 3,
) -> bool:
    """Return True if ALL ``require`` signals AND at least one ``any_of`` signal
    co-occur within a sliding window of ``window`` consecutive added lines.

    Case-insensitive matching. This is the tightened signal check for rules
    where disjunctive substring matching produces too many false positives."""
    if not adds:
        return False
    require_lower = [s.lower() for s in require]
    any_of_lower = [s.lower() for s in any_of]
    for i in range(len(adds)):
        end = min(i + window, len(adds))
        window_text = "\n".join(adds[i:end]).lower()
        if all(r in window_text for r in require_lower) and any(
            a in window_text for a in any_of_lower
        ):
            return True
    return False


def _check_resume_in_context(
    adds: list[str],
    alloc_signals: list[str],
    window: int = 10,
) -> bool:
    """Return True if 'resume' appears in a function-definition context AND
    an allocation signal co-occurs within ``window`` lines.

    Skips matches where 'resume' only appears in Kconfig help text, comments,
    or string literals without a function signature nearby."""
    alloc_lower = [s.lower() for s in alloc_signals]
    for i, line in enumerate(adds):
        lower = line.lower()
        if "resume" not in lower:
            continue
        # Skip Kconfig help text (indented description lines) and comments
        stripped = line.lstrip()
        if stripped.startswith("*") or stripped.startswith("//"):
            continue
        # Must look like function-definition context: contains parentheses or
        # braces, or matches *_resume / *resume* function signature patterns
        is_func_context = (
            "(" in line
            or "{" in line
            or "_resume" in lower
            or "resume(" in lower
        )
        if not is_func_context:
            continue
        # Check for allocation within window
        start = max(0, i - window // 2)
        end = min(len(adds), i + window)
        window_text = "\n".join(adds[start:end]).lower()
        if any(a in window_text for a in alloc_lower):
            return True
    return False


class PatternMatchPlugin:
    """Generic-by-construction ASoC plugin: matches one seeded review Pattern.

    One instance per pattern in the seeded library, so the Review Engine gets a
    focused plugin per concern. All ASoC specificity lives in the Pattern data
    (signals/rule/provenance), keeping this class thin and auditable.
    """

    def __init__(self, pattern: dict[str, Any]) -> None:
        self._p = pattern
        self._signals = [s.lower() for s in pattern.get("signals", [])]
        self._signal_mode = pattern.get("signal_mode", "disjunctive")
        self._signal_require = [s.lower() for s in pattern.get("signal_require", [])]
        self._signal_any_of = [s.lower() for s in pattern.get("signal_any_of", [])]
        self._signal_window = pattern.get("signal_window", 3)
        self._skip_kconfig = pattern.get("skip_kconfig", False)

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
        rejected-pattern, exhibit matching signals in its added lines."""
        if not any(_owns(f) for f in patch.files_changed):
            return False
        if self._skip_kconfig and all(
            "kconfig" in f.lower() or "makefile" in f.lower()
            for f in patch.files_changed
            if _owns(f)
        ):
            return False
        adds = _added_lines(patch.diff)
        if self._signal_mode == "conjunctive_window":
            return _check_conjunctive_window(
                adds, self._signal_require, self._signal_any_of, self._signal_window
            )
        if self._signal_mode == "resume_context":
            return _check_resume_in_context(
                adds, self._signal_any_of, self._signal_window
            )
        if not self._signals:
            return True
        adds_text = "\n".join(adds).lower()
        return any(sig in adds_text for sig in self._signals)

    def evaluate(
        self,
        patch: CorePatch,
        series: PatchSeries,
        *,
        series_context: SeriesContext | None = None,
    ) -> list[Decision]:
        """Emit a candidate Decision if the pattern's signals are present.

        The Decision carries the pattern_id AND its supporting rule_id so the
        Evidence Engine can gather the seeded Evidence (real provenance). Evidence
        and confidence are intentionally left unset (the Engine fills them).

        ``series_context`` (WP-9.1a) is accepted for Protocol conformance but
        not yet consumed here -- cross-patch suppression/upgrade for ASoC
        findings happens in the Evidence Engine's cross-patch resolver, not
        in this plugin."""
        if not self.applies(patch, series):
            return []
        adds = _added_lines(patch.diff)
        # For structured signal modes, applies() already validated; skip re-check.
        if self._signal_mode in ("conjunctive_window", "resume_context"):
            matched = self._signals or self._signal_require + self._signal_any_of
        else:
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
    """Return the most relevant ASoC-owned file for Decision.location.

    Preference order: .c > .h > Makefile > Kconfig. Within each tier,
    alphabetical. This prevents Kconfig (which sorts first alphabetically)
    from being reported as the location when the actual code change is in
    a .c driver file."""
    owned = [f for f in patch.files_changed if _owns(f)]
    if not owned:
        return None

    def _tier(path: str) -> int:
        lower = path.lower()
        if lower.endswith(".c"):
            return 0
        if lower.endswith(".h"):
            return 1
        if "makefile" in lower:
            return 2
        if "kconfig" in lower:
            return 3
        return 1  # unknown extensions treated as .h tier

    owned.sort(key=lambda f: (_tier(f), f))
    return owned[0]


def build_reasoning_plugins() -> list[PatternMatchPlugin]:
    """Instantiate one :class:`PatternMatchPlugin` per seeded review pattern.

    Deterministic ordering by ``plugin_id`` so the Review Engine sees a stable
    plugin list."""
    plugins = [PatternMatchPlugin(p) for p in build_patterns()]
    plugins.sort(key=lambda pl: pl.plugin_id)
    return plugins


__all__ = ["PatternMatchPlugin", "build_reasoning_plugins"]
