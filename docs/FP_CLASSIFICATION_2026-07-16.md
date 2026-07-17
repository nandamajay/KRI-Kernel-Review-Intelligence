# False Positive Classification — Benchmark Fixture Set

Date: 2026-07-16 (post WP-9.1a, commit `ab468a1`)

## Methodology

Reproduced the benchmark run (`tests/test_benchmark_regression.py`) with per-decision
detail extraction. A false positive is defined as: a publishable Decision (verified
evidence + confidence >= SPECULATIVE) with `agreement == "no_ground_truth"` — i.e. no
matching maintainer comment on that patch in the cached lore thread.

Classification was performed on all 114 FPs (100% sample, no sampling required since
the count is below the 50-threshold). Each FP was classified by:

1. Extracting the actual added diff lines for the patch that triggered the rule.
2. Checking whether the rule's signal strings matched the actual anti-pattern the rule
   describes, or merely incidental occurrences of the signal substring in unrelated
   context (help text, comments, firmware-loading code, etc.).
3. For cases where the signal genuinely matches the anti-pattern: checking whether the
   maintainer simply didn't comment on it (real disagreement) vs. KRI fabricating
   something that isn't there.

## Summary

| Category | Count | % of 114 | Description |
|---|---|---|---|
| **B. SHALLOW_RULE** | 90 | 78.9% | Signal match is incidental — rule fires on substring presence but the actual anti-pattern is absent |
| **E. REAL_DISAGREEMENT** | 24 | 21.1% | Anti-pattern genuinely present; maintainer didn't comment on this specific issue |
| A. LLM_HALLUCINATION | 0 | 0% | N/A — no FPs originate from kri/llm/ path in this benchmark |
| C. MISSING_SERIES_CONTEXT | 0 | 0% | N/A — no plugin emits MISSING_SYMBOL/FILE/BINDING evidence yet |
| D. FABRICATED_CITATION | 0 | 0% | N/A — all evidence comes from real seeded EKG nodes |
| F. UNKNOWN | 0 | 0% | — |

## Breakdown by rule

| Rule | SHALLOW_RULE | REAL_DISAGREEMENT | Total |
|---|---|---|---|
| `asoc-resume-must-clean-up` | 50 | 15 | 65 |
| `asoc-tdm-slot-not-userspace` | 40 | 0 | 40 |
| `asoc-use-component-read-write` | 0 | 9 | 9 |

All 114 FPs originate from `PatternMatchPlugin` in the ASoC DKP. Zero FPs from
`ProcessEtiquettePlugin` (process-rules checks produce decisions that always
match ground truth since they target formatting/trailer issues that maintainers
uniformly flag). Zero FPs from the LLM review path (not exercised by this benchmark).

## Root cause analysis per rule

### `asoc-resume-must-clean-up` (65 FPs, 50 shallow)

**Signals**: `["resume", "kzalloc", "kmalloc"]`

The rule fires when ANY of these three substrings appear in added lines. The word
"resume" appears in: Kconfig help text (`...in suspend/resume...`), comment blocks,
function names like `rz_ssi_resume()` (which don't allocate), and DT descriptions.
`kzalloc`/`kmalloc` commonly appear in probe paths alongside "resume" in the same
large patch but have nothing to do with resume-handler resource cleanup.

**Shallow pattern**: `_added_lines()` concatenation + `any(sig in adds)` matches
"resume" in ANY context. The rule describes a specific anti-pattern (allocating in a
resume handler without a matching free) but the signal check cannot distinguish
"resume mentioned in Kconfig help text" from "actual pm_runtime_resume callback body
that calls kzalloc."

### `asoc-tdm-slot-not-userspace` (40 FPs, all shallow)

**Signals**: `["SOC_ENUM", "tdm", "slot", "kcontrol"]`

This rule fires with a disjunction: ANY of the four signals suffices. The word "tdm"
appears in Kconfig descriptions (`I2S/TDM input`), header includes, and Intel
SoundWire board helper code that configures TDM at the machine level (which is
CORRECT practice per the rule, not a violation). "slot" appears in DMA slot
references, I2S slot configuration (not SOC_ENUM), and audio-routing tables.
"kcontrol" appears in codec drivers that define volume/mute controls (not TDM slot
mapping controls).

**Root cause**: the signal set is a disjunction of overly common words. None of
the 40 FPs involve the actual anti-pattern (a `SOC_ENUM_SINGLE`/`SOC_ENUM_DOUBLE`
definition that exposes functional TDM-slot-to-channel mapping to userspace).

### `asoc-use-component-read-write` (9 FPs, 0 shallow, all real disagreement)

**Signals**: `["regmap_read", "regmap_write"]`

These signals are more specific. All 9 FPs fire on patches that do call
`regmap_read()`/`regmap_write()` directly from component/codec code. The
maintainer in these threads simply didn't flag it — possibly because the patch
was accepted despite this style concern, or the maintainer focuses on other
aspects.

## Top 3 examples per category

### SHALLOW_RULE

1. `herve.codina_bootlin.com...aa46c70fc598` : patch seq=30
   - Rule: `asoc-resume-must-clean-up` | Location: `sound/soc/codecs/Kconfig`
   - The patch adds a new codec to Kconfig (textual description only); "resume" appears nowhere in its added lines. Fires because a different patch in the same fixture's series mentions "resume" and the decision targets the Kconfig patch.

2. `maso.huang_mediatek.com...f048185b02ac` : patch seq=4
   - Rule: `asoc-tdm-slot-not-userspace` | Location: `sound/soc/mediatek/mt7986/mt7986-dai-etdm.c`
   - The patch configures TDM at the DAI level using `set_tdm_slot()` — which is CORRECT practice per the rule's own recommendation. "tdm" is present but the code does the right thing.

3. `pierre-louis.bossart_linux.intel.com...` : patch seq=20
   - Rule: `asoc-resume-must-clean-up` | Location: `sound/soc/intel/boards/Kconfig`
   - Pure Kconfig text patch adding Intel board entries. No resume handler code at all.

### REAL_DISAGREEMENT

1. `wangweidong.a_awinic.com...b13f48594973` : patch seq=5
   - Rule: `asoc-use-component-read-write` | Location: `sound/soc/codecs/Kconfig`
   - The patch adds a new codec driver (AW88399) that calls `regmap_write()` directly in component code. Real concern; maintainer focused on apply-failure issue instead.

2. `topic-sm8650-upstream-wcd939x-codec-v2...` : patch seq=4
   - Rule: `asoc-resume-must-clean-up` | Location: `sound/soc/codecs/Kconfig`
   - WCD939x codec has a resume path that re-initializes state via regmap. Maintainer approved without flagging cleanup.

3. `niranjan.hy_ti.com...0a20147fc764` : patch seq=3
   - Rule: `asoc-resume-must-clean-up` | Location: `sound/soc/ti/davinci-i2s.c`
   - TI DAI driver adds runtime-PM resume. Real resume handler present with potential cleanup concern. Maintainer reviewed other aspects.

## Key observations

1. **All 114 FPs are ASoC PatternMatchPlugin decisions.** The LLM review path and
   ProcessEtiquettePlugin produce zero FPs in this benchmark.

2. **The precision problem is entirely rule-signal specificity.** The three rules use
   common English words (`resume`, `slot`, `tdm`) and low-level API names
   (`kzalloc`, `regmap_write`) as disjunctive signals, matching ANY occurrence in
   added lines with no structural/AST-level validation.

3. **Two rules account for 92% of FPs.** `asoc-resume-must-clean-up` (57%) and
   `asoc-tdm-slot-not-userspace` (35%) together produce 105/114 FPs.

4. **The "tdm" rule is 100% shallow** — zero of its 40 firings represent a real
   instance of the anti-pattern it describes.

5. **Location field is misleading.** Many decisions report `Kconfig` as the location
   because `_first_owned_file(patch)` picks the first ASoC-owned file alphabetically;
   for patches that add new drivers (Kconfig + Makefile + .c + .h), Kconfig sorts
   first. The rule logic actually scans the full diff, not just the file at `location`.

## Recommendation

**Largest category:** SHALLOW_RULE (90/114, 78.9%)

**Which WP would close it:** A signal-specificity tightening pass on the ASoC DKP's
`PatternMatchPlugin` rule signals. Specifically:

- `asoc-tdm-slot-not-userspace`: change signals from `["SOC_ENUM", "tdm", "slot", "kcontrol"]` to a conjunctive check requiring `SOC_ENUM` AND (`tdm` OR `slot`) to co-occur on the SAME added line (or within a small window). This eliminates all 40 FPs since none of them have `SOC_ENUM` definitions.
- `asoc-resume-must-clean-up`: require that `resume` appears in a function definition or assignment context (not Kconfig help text), AND (`kzalloc`|`kmalloc`) appears in the same function body. This likely eliminates 50/65 FPs. Alternatively, require `resume` AND `kzalloc`/`kmalloc` co-occurrence (conjunction rather than disjunction), which is more conservative but still eliminates pure-`resume`-mention FPs.

**Expected precision impact:** eliminating 90 shallow FPs from 114 total FPs (keeping
20 exact agreements unchanged) would raise precision from 0.1493 to
`20 / (20 + 24) = 0.4545` — a 3x improvement. This is the single highest-leverage
change available before touching the LLM path or adding new evidence source types.

**Confidence level:** HIGH. The classification is mechanical (checking diff content
against known signal sets), the root cause is unambiguous (disjunctive substring
matching on common words), and the fix is bounded (2 rules in one file:
`kri/packages/asoc/knowledge.py`'s signal lists + `PatternMatchPlugin.applies()`
logic in `kri/packages/asoc/plugins.py`).
