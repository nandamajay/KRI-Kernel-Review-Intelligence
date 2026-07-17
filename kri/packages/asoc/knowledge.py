"""ASoC domain knowledge data (Sprint-2).

**Domain Isolation boundary (Constitution Sec. 9):** this module — like everything
else under ``kri/packages/asoc/`` — is the ONLY place ASoC/snd_soc/sound-soc/ALSA
identifiers may appear. Nothing here is imported by the Generic Runtime by name;
it is reached only through the :class:`DomainKnowledgePackage` protocol.

Every rule, API fact, and pattern below is grounded in a *real* citation:
 * kernel Documentation/sound/soc/*.rst and include/sound/soc*.h in the target tree
   (Linux v6.6), and/or
 * a real maintainer review comment in the cached lore fixtures under
   ``data/lore_cache/`` (Mark Brown / Krzysztof Kozlowski on the NAU83G60 series).

No hallucinated knowledge (Constitution): if we cannot cite it, it is not here.
"""

from __future__ import annotations

from typing import Any

from kri.common.models import (
    AlternativeRecommendation,
    EvidenceSourceType,
    Provenance,
    Rule,
    RuleType,
    VersionRange,
)
from kri.knowledge.version import make_range

# --- domain roots (the single hardcoded ASoC location set) -----------------
ASOC_ROOT = "sound/soc/"
ASOC_CODECS_ROOT = "sound/soc/codecs/"
ASOC_SUBSYSTEM_ID = "subsystem:asoc"
ASOC_DOC_ROOT = "Documentation/sound/soc/"

# ASoC maintainers (from the kernel MAINTAINERS "SOUND - SOC LAYER" stanza; these
# are public, real identities used as ground-truth review authors).
ASOC_MAINTAINERS: list[dict[str, str]] = [
    {"name": "Mark Brown", "email": "broonie@kernel.org"},
    {"name": "Liam Girdwood", "email": "lgirdwood@gmail.com"},
]


def _doc(path: str, section: str = "") -> Provenance:
    """Provenance rooted in a real kernel documentation file."""
    ref = f"{path}#{section}" if section else path
    return Provenance(
        repo_path=ref,
        source_url=f"https://www.kernel.org/doc/html/latest/{_doc_html(path)}",
        version_or_commit="v6.6",
        transformation_history=["kernel.doc", "asoc.dkp"],
        source_confidence=1.0,
    )


def _doc_html(path: str) -> str:
    # Documentation/sound/soc/codec.rst -> sound/soc/codec.html
    rel = path.replace("Documentation/", "").replace(".rst", ".html")
    return rel


def _header(path: str, symbol: str = "") -> Provenance:
    """Provenance rooted in a real include/sound/soc*.h API header."""
    return Provenance(
        repo_path=f"{path}::{symbol}" if symbol else path,
        version_or_commit="v6.6",
        transformation_history=["kernel.header", "asoc.dkp"],
        source_confidence=1.0,
    )


def _lore(message_id: str, who: str) -> Provenance:
    """Provenance rooted in a real cached lore review comment."""
    return Provenance(
        source_url=f"https://lore.kernel.org/all/{message_id}/",
        version_or_commit=message_id,
        transformation_history=["lore.fetch", "asoc.dkp"],
        source_confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Rules — Hard / Soft / Philosophical (RuleType), each with a real doc_ref.
# ---------------------------------------------------------------------------

_RULES_RAW: list[dict[str, Any]] = [
    {
        "rule_id": "asoc-codec-no-machine-code",
        "category": "componentization",
        "rule_type": RuleType.HARD,
        "description": (
            "A codec class driver must be generic and hardware-independent: it must "
            "contain no platform- or machine-specific code. Platform/machine code "
            "belongs in the platform and machine drivers respectively."
        ),
        "rationale": (
            "Codec independence is the founding goal of ASoC — it enables codec "
            "driver reuse across SoCs/machines and removes the pre-ASoC code "
            "duplication (e.g. four wm8731 copies)."
        ),
        "doc_ref": "Documentation/sound/soc/codec.rst",
        "enforcement_rate": 0.98,
        "provenance": _doc("Documentation/sound/soc/codec.rst", "ASoC Codec Class Driver"),
    },
    {
        "rule_id": "asoc-use-devm-register-component",
        "category": "api_lifecycle",
        "rule_type": RuleType.SOFT,
        "description": (
            "Register the ASoC component with devm_snd_soc_register_component() so "
            "the component is torn down automatically on driver detach; avoid manual "
            "snd_soc_unregister_component() in the common probe path."
        ),
        "rationale": (
            "The devm_ managed registration is the modern idiom and prevents "
            "unregister/leak bugs on the error and remove paths."
        ),
        "doc_ref": "include/sound/soc-component.h",
        "enforcement_rate": 0.9,
        "provenance": _header(
            "include/sound/soc-component.h", "devm_snd_soc_register_component"
        ),
    },
    {
        "rule_id": "asoc-dapm-for-audio-routing",
        "category": "power_management",
        "rule_type": RuleType.SOFT,
        "description": (
            "Express audio paths and their power dependencies with DAPM widgets and "
            "routes rather than ad-hoc control code, so the core can power up only "
            "the active parts of the audio path."
        ),
        "rationale": (
            "Dynamic Audio Power Management is a core ASoC feature; modelling routes "
            "as DAPM widgets lets the framework minimise power on portable devices "
            "and is what maintainers expect for runtime-variable routing."
        ),
        "doc_ref": "Documentation/sound/soc/dapm.rst",
        "enforcement_rate": 0.85,
        # Grounded additionally in Mark Brown's v5 review recommending DAPM routing.
        "provenance": _lore(
            "akpOy2HJsDCu7wVx@sirena.co.uk", "Mark Brown"
        ),
    },
    {
        "rule_id": "asoc-tdm-slot-not-userspace",
        "category": "design_idiom",
        "rule_type": RuleType.SOFT,
        "description": (
            "TDM slot configuration should be driven by the machine driver via "
            "set_tdm_slot() (snd_soc_dai_set_tdm_slot), or by DAPM routing / fixed "
            "device (DT) properties — not exposed as userspace kcontrols (SOC_ENUM) "
            "for functional slot mapping."
        ),
        "rationale": (
            "Slot/functional mapping is a system-integration property, not a runtime "
            "user preference; encoding it as userspace enums breaks the machine-"
            "driver contract. Maintainer explicitly nacked the kcontrol approach and "
            "the submitter moved to DT properties in the respin."
        ),
        "doc_ref": "include/sound/soc-dai.h",
        "enforcement_rate": 0.8,
        "provenance": _lore(
            "66ce56eb-95b9-4915-8658-a1e4d1eacd7f@sirena.org.uk", "Mark Brown"
        ),
    },
    {
        "rule_id": "asoc-resume-must-clean-up",
        "category": "error_paths",
        "rule_type": RuleType.SOFT,
        "description": (
            "Resource acquisition performed on every resume must have matching "
            "cleanup so back-to-back suspend/resume cycles cannot leak or "
            "double-acquire resources."
        ),
        "rationale": (
            "Maintainer review flagged a resume handler that ran every resume with "
            "no cleanup path — a latent leak under repeated suspend/resume."
        ),
        "doc_ref": "Documentation/sound/soc/codec.rst",
        "enforcement_rate": 0.75,
        "provenance": _lore(
            "a5139285-2017-4c94-92f2-f87fbef6fa6a@sirena.org.uk", "Mark Brown"
        ),
    },
    {
        "rule_id": "asoc-prefer-idiomatic-over-novel",
        "category": "philosophy",
        "rule_type": RuleType.PHILOSOPHICAL,
        "description": (
            "Prefer existing ASoC framework idioms (DAPM, standard DAI ops, regmap "
            "controls) over inventing subsystem-specific mechanisms; novelty must be "
            "justified against the framework's existing facilities."
        ),
        "rationale": (
            "ASoC's whole design rationale is reuse and uniformity across codecs and "
            "machines; bespoke mechanisms fragment the subsystem and raise the "
            "review bar. Reflects the consistent maintainer steer toward standard "
            "facilities in the reviewed series."
        ),
        "doc_ref": "Documentation/sound/soc/overview.rst",
        "enforcement_rate": 0.6,
        "provenance": _doc("Documentation/sound/soc/overview.rst", "ASoC Design"),
    },
    {
        "rule_id": "asoc-kcontrol-put-return-convention",
        "category": "api_convention",
        "rule_type": RuleType.SOFT,
        "description": (
            "A kcontrol put() callback must return 1 if the value was changed, "
            "0 if it was unchanged, or a negative error code on fatal error."
        ),
        "rationale": (
            "ALSA's control core uses the put() return value to decide whether "
            "to notify userspace of a change; returning the wrong value causes "
            "either missed change notifications or spurious ones."
        ),
        "doc_ref": "Documentation/sound/kernel-api/writing-an-alsa-driver.rst",
        "enforcement_rate": 0.85,
        "provenance": _doc(
            "Documentation/sound/kernel-api/writing-an-alsa-driver.rst",
            "put callback",
        ),
    },
    {
        "rule_id": "asoc-use-component-read-write",
        "category": "api_usage",
        "rule_type": RuleType.SOFT,
        "description": (
            "Component code should access hardware registers through "
            "snd_soc_component_read()/snd_soc_component_write() rather than "
            "calling regmap or codec-level accessors directly."
        ),
        "rationale": (
            "The component read/write wrappers keep register access routed "
            "through the component's configured regmap/cache and are what the "
            "ASoC component API is built around."
        ),
        "doc_ref": "include/sound/soc-component.h",
        "enforcement_rate": 0.8,
        "provenance": _header(
            "include/sound/soc-component.h", "snd_soc_component_read"
        ),
    },
    {
        "rule_id": "asoc-prefer-regcache-maple",
        "category": "api_convention",
        "rule_type": RuleType.SOFT,
        "description": (
            "New codec drivers should default their regmap cache_type to "
            "REGCACHE_MAPLE rather than REGCACHE_RBTREE or REGCACHE_COMPRESSED."
        ),
        "rationale": (
            "REGCACHE_MAPLE is the newer, better-performing general-purpose "
            "regmap cache implementation; REGCACHE_RBTREE/REGCACHE_COMPRESSED "
            "are the older choices new drivers should no longer default to."
        ),
        "doc_ref": "include/linux/regmap.h",
        "enforcement_rate": 0.6,
        "provenance": _header("include/linux/regmap.h", "REGCACHE_MAPLE"),
    },
    {
        "rule_id": "asoc-pm-runtime-ordering",
        "category": "power_management",
        "rule_type": RuleType.SOFT,
        "description": (
            "Component probe must call pm_runtime_enable() before the component "
            "is registered, and remove()/error paths must call "
            "pm_runtime_disable() to match, so runtime PM is never left enabled "
            "on a device with no active driver."
        ),
        "rationale": (
            "pm_runtime_enable()/pm_runtime_disable() must bracket the "
            "lifetime a driver is bound for the runtime PM core's usage-count "
            "and callback invariants to hold; probe/remove is the standard "
            "place to pair them."
        ),
        "doc_ref": "Documentation/power/runtime_pm.rst",
        "enforcement_rate": 0.7,
        "provenance": _doc(
            "Documentation/power/runtime_pm.rst", "Runtime PM and system sleep"
        ),
    },
    {
        "rule_id": "asoc-dt-binding-schema",
        "category": "dt_binding",
        "rule_type": RuleType.SOFT,
        "description": (
            "New DT-bound ASoC drivers should ship a DT schema (json-schema/"
            "YAML) binding under Documentation/devicetree/bindings/sound/ "
            "rather than a free-text binding document."
        ),
        "rationale": (
            "DT bindings are required to be written in DT schema (json-schema "
            "vocabulary, YAML format) and pass schema validation; free-text "
            "binding docs are the legacy format being phased out."
        ),
        "doc_ref": "Documentation/devicetree/bindings/submitting-patches.rst",
        "enforcement_rate": 0.75,
        "provenance": _doc(
            "Documentation/devicetree/bindings/submitting-patches.rst",
            "DT binding files are written in DT schema format",
        ),
    },
]


def build_rules() -> list[tuple[Rule, Provenance]]:
    """Return (Rule, Provenance) pairs. Provenance is carried alongside because the
    frozen :class:`Rule` model has no provenance field (it exposes
    ``documentation_ref``); the graph node stores the full Provenance."""
    out: list[tuple[Rule, Provenance]] = []
    default_range = make_range("6.1")
    for raw in _RULES_RAW:
        rule = Rule(
            rule_id=raw["rule_id"],
            category=raw["category"],
            rule_type=raw["rule_type"],
            description=raw["description"],
            rationale=raw["rationale"],
            documentation_ref=raw["doc_ref"],
            historical_enforcement_rate=raw["enforcement_rate"],
            version_range=default_range,
        )
        out.append((rule, raw["provenance"]))
    return out


# ---------------------------------------------------------------------------
# API knowledge — snd_soc_* lifecycle as Api nodes + temporal edges.
# ---------------------------------------------------------------------------


def build_apis() -> list[dict[str, Any]]:
    """ASoC API facts. Each entry becomes an ``Api`` node; ``replaced_by`` becomes
    a temporal ``SUPERSEDES`` edge and closes the deprecated API's validity.

    Grounded in real headers in include/sound/ (v6.6)."""
    return [
        {
            "symbol": "devm_snd_soc_register_component",
            "kind": "func",
            "header": "include/sound/soc.h",
            "introduced_in": "6.1",
            "deprecated_in": None,
            "replaced_by": None,
            "provenance": _header(
                "include/sound/soc.h", "devm_snd_soc_register_component"
            ),
        },
        {
            "symbol": "snd_soc_register_component",
            "kind": "func",
            "header": "include/sound/soc.h",
            "introduced_in": "6.1",
            "deprecated_in": None,
            "replaced_by": None,
            "provenance": _header(
                "include/sound/soc.h", "snd_soc_register_component"
            ),
        },
        {
            "symbol": "snd_soc_dai_set_tdm_slot",
            "kind": "func",
            "header": "include/sound/soc-dai.h",
            "introduced_in": "6.1",
            "deprecated_in": None,
            "replaced_by": None,
            "provenance": _header("include/sound/soc-dai.h", "snd_soc_dai_set_tdm_slot"),
        },
        {
            # Real deprecation documented in include/sound/soc.h:349.
            "symbol": "SND_SOC_BYTES_EXT",
            "kind": "macro",
            "header": "include/sound/soc.h",
            "introduced_in": "6.1",
            "deprecated_in": "6.1",
            "replaced_by": "SND_SOC_BYTES_TLV",
            "provenance": _header("include/sound/soc.h", "SND_SOC_BYTES_EXT"),
        },
        {
            "symbol": "SND_SOC_BYTES_TLV",
            "kind": "macro",
            "header": "include/sound/soc.h",
            "introduced_in": "6.1",
            "deprecated_in": None,
            "replaced_by": None,
            "provenance": _header("include/sound/soc.h", "SND_SOC_BYTES_TLV"),
        },
    ]


# ---------------------------------------------------------------------------
# File patterns — what a sound/soc/ path means (routing / concept tagging).
# ---------------------------------------------------------------------------

FILE_PATTERN_MEANINGS: list[dict[str, str]] = [
    {
        "glob": "sound/soc/codecs/*",
        "driver_type": "codec",
        "concept": "Codec class driver (hardware-independent codec control).",
        "doc_ref": "Documentation/sound/soc/codec.rst",
    },
    {
        "glob": "sound/soc/generic/*",
        "driver_type": "machine",
        "concept": "Generic machine (card) driver wiring codecs to CPU DAIs.",
        "doc_ref": "Documentation/sound/soc/machine.rst",
    },
    {
        "glob": "sound/soc/soc-core.c",
        "driver_type": "core",
        "concept": "ASoC core: component/card/DAI registration and matching.",
        "doc_ref": "Documentation/sound/soc/overview.rst",
    },
    {
        "glob": "sound/soc/soc-dapm.c",
        "driver_type": "core",
        "concept": "DAPM core: dynamic audio power management of the audio path.",
        "doc_ref": "Documentation/sound/soc/dapm.rst",
    },
    {
        # Platform/SoC-vendor CPU-DAI + DMA glue lives in per-vendor dirs.
        "glob": "sound/soc/amd/*",
        "driver_type": "platform",
        "concept": "Platform driver: SoC-vendor CPU DAI / DMA integration.",
        "doc_ref": "Documentation/sound/soc/platform.rst",
    },
]


def classify_path(path: str) -> dict[str, str] | None:
    """Return the driver-type meaning of an ASoC path, or ``None`` if not ours.

    Deterministic: patterns are checked most-specific first."""
    import fnmatch

    if path.startswith("sound/soc/codecs/"):
        return FILE_PATTERN_MEANINGS[0]
    ordered = sorted(FILE_PATTERN_MEANINGS, key=lambda m: (-len(m["glob"]), m["glob"]))
    for meaning in ordered:
        if fnmatch.fnmatch(path, meaning["glob"]):
            return meaning
    if path.startswith("sound/soc/"):
        # Unclassified but ours: default to platform-family.
        return {
            "glob": "sound/soc/*",
            "driver_type": "platform",
            "concept": "ASoC subsystem file (platform-family).",
            "doc_ref": "Documentation/sound/soc/overview.rst",
        }
    return None


# ---------------------------------------------------------------------------
# Review patterns — accepted vs rejected, grounded in the cached threads.
# ---------------------------------------------------------------------------

# Evidence-count thresholds (SPEC / learning): 5 => possible, 20 => likely,
# 50 => certain. The seeded patterns below are curated (documented) domain
# knowledge; the Learning Engine (learning/) extends the library from history and
# reports LOW support until thresholds are met.
SUPPORT_THRESHOLDS = {"possible": 5, "likely": 20, "certain": 50}


def build_patterns() -> list[dict[str, Any]]:
    """The seeded ASoC review-pattern library.

    Each pattern is a generic ``Pattern`` dict the Review Engine can match:
    ``pattern_id``, ``description``, ``outcome`` (accepted|rejected), ``signals``
    (substrings the Review Engine looks for in added diff lines), ``rule_id`` it
    supports, ``examples`` (real patch/message ids), and ``provenance``."""
    return [
        {
            "pattern_id": "asoc-reject-userspace-tdm-slot-enum",
            "description": (
                "Rejected: exposing TDM functional slot mapping as userspace "
                "SOC_ENUM kcontrols instead of machine-driver set_tdm_slot / DAPM / "
                "DT properties."
            ),
            "outcome": "rejected",
            "signals": ["SOC_ENUM", "tdm", "slot", "kcontrol"],
            "signal_mode": "conjunctive_window",
            "signal_require": ["SOC_ENUM"],
            "signal_any_of": ["tdm", "slot"],
            "signal_window": 3,
            "rule_id": "asoc-tdm-slot-not-userspace",
            "layer": "design",
            "examples": ["20260630021510.821919-3-YLCHANG2@nuvoton.com"],
            "provenance": _lore(
                "66ce56eb-95b9-4915-8658-a1e4d1eacd7f@sirena.org.uk", "Mark Brown"
            ),
        },
        {
            "pattern_id": "asoc-reject-resume-without-cleanup",
            "description": (
                "Rejected: a resume handler that acquires/initialises resources on "
                "every resume with no matching cleanup path."
            ),
            "outcome": "rejected",
            "signals": ["resume", "kzalloc", "kmalloc"],
            "signal_mode": "resume_context",
            "signal_any_of": ["kzalloc", "kmalloc"],
            "signal_window": 10,
            "skip_kconfig": True,
            "rule_id": "asoc-resume-must-clean-up",
            "layer": "semantic",
            "examples": ["20260708093506.895481-3-YLCHANG2@nuvoton.com"],
            "provenance": _lore(
                "a5139285-2017-4c94-92f2-f87fbef6fa6a@sirena.org.uk", "Mark Brown"
            ),
        },
        {
            "pattern_id": "asoc-accept-devm-register-component",
            "description": (
                "Accepted idiom: register the component with "
                "devm_snd_soc_register_component() for automatic teardown."
            ),
            "outcome": "accepted",
            "signals": ["devm_snd_soc_register_component"],
            "rule_id": "asoc-use-devm-register-component",
            "layer": "semantic",
            "examples": ["20260630021510.821919-3-YLCHANG2@nuvoton.com"],
            "provenance": _header(
                "include/sound/soc-component.h", "devm_snd_soc_register_component"
            ),
        },
        {
            "pattern_id": "asoc-accept-dapm-routing",
            "description": (
                "Accepted idiom: model runtime-variable audio routing with DAPM "
                "widgets and routes."
            ),
            "outcome": "accepted",
            "signals": ["SND_SOC_DAPM", "snd_soc_dapm"],
            "rule_id": "asoc-dapm-for-audio-routing",
            "layer": "design",
            "examples": ["20260630021510.821919-3-YLCHANG2@nuvoton.com"],
            "provenance": _lore("akpOy2HJsDCu7wVx@sirena.co.uk", "Mark Brown"),
        },
        {
            "pattern_id": "asoc-reject-regcache-rbtree-default",
            "description": (
                "Rejected: a new codec driver defaulting its regmap cache_type "
                "to REGCACHE_RBTREE (or REGCACHE_COMPRESSED) instead of the "
                "newer REGCACHE_MAPLE."
            ),
            "outcome": "rejected",
            "signals": ["REGCACHE_RBTREE", "REGCACHE_COMPRESSED"],
            "rule_id": "asoc-prefer-regcache-maple",
            "layer": "structural",
            "examples": [],
            "provenance": _header("include/linux/regmap.h", "REGCACHE_MAPLE"),
        },
        {
            "pattern_id": "asoc-reject-direct-regmap-in-component",
            "description": (
                "Rejected: component code calling regmap_read()/regmap_write() "
                "or a codec-level accessor directly instead of "
                "snd_soc_component_read()/snd_soc_component_write()."
            ),
            "outcome": "rejected",
            "signals": ["regmap_read", "regmap_write"],
            "rule_id": "asoc-use-component-read-write",
            "layer": "structural",
            "examples": [],
            "provenance": _header(
                "include/sound/soc-component.h", "snd_soc_component_read"
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Canonical alternative recommendations — per rule_id corrected-code snippets.
# ---------------------------------------------------------------------------

CANONICAL_RECOMMENDATIONS: dict[str, AlternativeRecommendation] = {
    "asoc-tdm-slot-not-userspace": AlternativeRecommendation(
        snippet=(
            "/* In the machine driver: */\n"
            "ret = snd_soc_dai_set_tdm_slot(dai, tx_mask, rx_mask,\n"
            "\t\t\t\t    slots, slot_width);"
        ),
        language="c",
        rationale=(
            "TDM slot configuration is a system-integration property; expose it "
            "via set_tdm_slot() in the machine driver or DT properties, not as "
            "userspace kcontrols."
        ),
    ),
    "asoc-resume-must-clean-up": AlternativeRecommendation(
        snippet=(
            "static int codec_resume(struct device *dev)\n"
            "{\n"
            "\tstruct priv *p = dev_get_drvdata(dev);\n"
            "\n"
            "\t/* Use devres-managed allocation, or free in suspend */\n"
            "\tp->buf = devm_kzalloc(dev, BUF_SIZE, GFP_KERNEL);\n"
            "\tif (!p->buf)\n"
            "\t\treturn -ENOMEM;\n"
            "\treturn 0;\n"
            "}"
        ),
        language="c",
        rationale=(
            "Resources acquired on resume must have matching cleanup on suspend "
            "or use devm_ allocation so back-to-back cycles cannot leak."
        ),
    ),
    "asoc-use-devm-register-component": AlternativeRecommendation(
        snippet=(
            "ret = devm_snd_soc_register_component(&pdev->dev,\n"
            "\t\t\t\t\t   &codec_drv, dais, n_dais);"
        ),
        language="c",
        rationale=(
            "devm_ registration auto-unregisters on driver detach, preventing "
            "leak/unregister bugs on error and remove paths."
        ),
    ),
    "asoc-prefer-regcache-maple": AlternativeRecommendation(
        snippet=(
            "static const struct regmap_config codec_regmap = {\n"
            "\t.cache_type = REGCACHE_MAPLE,\n"
            "\t/* ... */\n"
            "};"
        ),
        language="c",
        rationale=(
            "REGCACHE_MAPLE is the modern, better-performing general-purpose "
            "regmap cache; new drivers should prefer it over RBTREE/COMPRESSED."
        ),
    ),
    "asoc-use-component-read-write": AlternativeRecommendation(
        snippet=(
            "val = snd_soc_component_read(component, REG_ADDR);\n"
            "snd_soc_component_write(component, REG_ADDR, new_val);"
        ),
        language="c",
        rationale=(
            "Component read/write route access through the component's "
            "configured regmap/cache; calling regmap directly bypasses this."
        ),
    ),
    "asoc-kcontrol-put-return-convention": AlternativeRecommendation(
        snippet=(
            "static int codec_put_volsw(struct snd_kcontrol *kctl,\n"
            "\t\t\t   struct snd_ctl_elem_value *uctl)\n"
            "{\n"
            "\t/* Return 1 if value changed, 0 if unchanged */\n"
            "\tif (val != old_val) {\n"
            "\t\twrite_reg(codec, reg, val);\n"
            "\t\treturn 1;\n"
            "\t}\n"
            "\treturn 0;\n"
            "}"
        ),
        language="c",
        rationale=(
            "ALSA control core relies on put() returning 1/0 to decide whether "
            "to emit a change notification to userspace."
        ),
    ),
    "asoc-pm-runtime-ordering": AlternativeRecommendation(
        snippet=(
            "static int codec_probe(struct platform_device *pdev)\n"
            "{\n"
            "\tpm_runtime_enable(&pdev->dev);\n"
            "\tret = devm_snd_soc_register_component(...);\n"
            "\tif (ret) {\n"
            "\t\tpm_runtime_disable(&pdev->dev);\n"
            "\t\treturn ret;\n"
            "\t}\n"
            "\treturn 0;\n"
            "}"
        ),
        language="c",
        rationale=(
            "pm_runtime_enable() before registration, pm_runtime_disable() on "
            "error/remove, so runtime PM is never left enabled without a driver."
        ),
    ),
}


def get_canonical_recommendation(rule_id: str) -> AlternativeRecommendation | None:
    """Return the canonical corrected-code recommendation for a rule, or None."""
    return CANONICAL_RECOMMENDATIONS.get(rule_id)


# ---------------------------------------------------------------------------
# Canonical precedents — commit references demonstrating the correct pattern.
# Each entry MUST be a real upstream commit hash verified via
# `git -C data/kernel/linux cat-file -e <hash>`.
# Placeholder concept: strings are pending replacement with real hashes
# (WP-9.2a-polish-v2 sub-commit 2, human-authored).
# ---------------------------------------------------------------------------

CANONICAL_PRECEDENTS: dict[str, list[str]] = {
    "asoc-tdm-slot-not-userspace": [
        "concept:asoc-accept-tdm-via-machine-driver",
    ],
    "asoc-resume-must-clean-up": [
        "concept:asoc-accept-resume-with-cleanup",
    ],
    "asoc-use-component-read-write": [
        "concept:asoc-accept-component-read-write",
    ],
}


__all__ = [
    "ASOC_ROOT",
    "ASOC_CODECS_ROOT",
    "ASOC_SUBSYSTEM_ID",
    "ASOC_DOC_ROOT",
    "ASOC_MAINTAINERS",
    "SUPPORT_THRESHOLDS",
    "FILE_PATTERN_MEANINGS",
    "CANONICAL_RECOMMENDATIONS",
    "CANONICAL_PRECEDENTS",
    "build_rules",
    "build_apis",
    "build_patterns",
    "classify_path",
    "get_canonical_recommendation",
    "make_range",
    "VersionRange",
    "EvidenceSourceType",
]
