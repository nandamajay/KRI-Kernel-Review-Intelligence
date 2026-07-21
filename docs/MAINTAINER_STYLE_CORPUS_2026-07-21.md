# ASoC Maintainer Style Corpus Study

**Date:** 2026-07-21
**Corpus:** 129 `.mbox.gz` files in `data/lore_cache/` — real ASoC/alsa-devel lore.kernel.org
threads, 2022–2026. Approximately 133 distinct maintainer review comments extracted and
classified across 17 threads from the top-50 largest files plus the 3 canonical fixtures.
**Attribution:** All patterns attributed to "the ASoC/alsa-devel reviewer community" — no
individual is identified or profiled.

---

## 1. Corpus Summary by Category

| Category | Observed count | Notes |
|---|---|---|
| Approval (Reviewed-by / Acked-by / "Applied") | ~45 | Most common single category |
| Style nitpick | ~18 | DT ordering, whitespace, naming, header guards |
| Design objection | ~15 | Wrong abstraction, wrong owner (userspace vs kernel) |
| API misuse | ~14 | Wrong function, missing devm_, firmware lifecycle |
| Clarifying question / justification request | ~18 | "Why X?", "Is this intentional?", "Should this also Y?" |
| Documentation / DT binding | ~14 | Schema validation, missing bindings, YAML style |
| General / correctness | ~9 | Logic errors, build warnings, factual corrections |

---

## 2. Category-by-Category Analysis

### 2.1 Clarifying Questions

**What they look like in the corpus:**

> "I would expect TDM to be configured by set_tdm_slot() from the machine driver, not from
> userspace. I see the driver does actually have a set_tdm_slot() operation..."

> "Why is this a separate API, what is the situation where we would want to report an invalid
> value from a control? I was thinking of just adding this to the existing operations rather
> than adding separate ones that need to be explicitly set."

> "Why add a hog if you defined the regulator already?"

> "Other controls in this driver ignore writes before hw_init is set, should this one?"

> "Is this mclk frequency fixed? Typically, audio MCLK is expected to be 12.288 MHz or
> 24.576 MHz."

> "Or does Herve want to take over maintaining it?"

> "Where is SC8280XP_DAI_DATA() defined?"

**Common phrasing patterns:**
- `"I would expect X to..."` — frames reviewer expectation as reference point, not mandate
- `"Why X?"` — direct, neutral, sometimes a single sentence
- `"Is X fixed/correct/intentional?"` — probes whether the choice is deliberate
- `"Should this [also] do Y?"` — points to a potential omission without asserting it's wrong
- `"I was thinking of just X..."` — offers an alternative framing, invites discussion
- `"...or does [person] want to X?"` — explicitly opens discussion of ownership/intent

**Tone:** Curious, not accusatory. The question is the finding. No assertion that the code
is wrong — the reviewer asks whether the author considered the alternative. Often the question
itself signals the correct answer.

**Sentence structures:**
```
I would expect [X] to be [done by Y / from Z], not [from A].
I see the driver does [actually have / already implement] [relevant thing]...

Why is this [a separate API / different from the existing approach]?
What is the situation where we would want [undesired outcome]?

Is this [value / behavior / configuration] fixed / intentional?
Typically, [industry convention].

[Other thing in driver] [does X / ignores Y], should this one?

Where is [symbol] defined?
```

---

### 2.2 Requests for Justification

**What they look like in the corpus:**

> "This will fail the probe if we fail to read firmware-name from the DT so the firmware name
> is a required property in DT (and ACPI systems will have fun) even though it is not marked as
> such. Either the driver needs to tolerate not having the name configured one way or another
> or the property needs to be mandatory in the bindings."

> "Of course you can leave them, but I think we more or less _guarantee_ their inclusion by
> bitops.h, otherwise bitops.h will require those two in _each_ instance of use which sounds
> not such a clever decision."

> "Please add the node to sm6115.dtsi and override the compat string here."

> "codec_dai_fmt appears to be identical for all MI2S DAIs. Can this be moved into the common
> initialization path instead of being specified per DAI."

> "That said, I would avoid adding them here as the compiler would need to mmap() the first
> page of each header, check the guard and unmap, and repeat for each header. This will slow
> down the build for no particular reason."

**Common phrasing patterns:**
- `"Either [option A] or [option B]"` — presents two valid resolutions without prescribing one
- `"Can this be [moved / simplified / refactored]?"` — improvement as question
- `"[X] appears to be [identical / redundant]. Can [improvement]?"` — observation + question
- `"[Consequence of current code], which [effect]."` — explains the implication, lets author judge
- Full explanation of WHY the current approach is problematic — author decides the fix

**Tone:** Analytical. The reviewer explains the reasoning, not just the verdict. "Either...or"
constructions are common because maintainers don't want to prescribe the solution — they want
to identify the tension and let the author resolve it appropriately.

---

### 2.3 API Misuse Reviews

**What they look like in the corpus:**

> "On/off switches should be a Switch control, not an enum."

> "Turning effects on and off are still things being turned on and off."
> *(follow-up after author pushed back)*

> "Should use snd_soc_component_read() here rather than reading the regmap directly — the
> component API handles the power state correctly."

> "Both suspend and remove should clean up anything that's pending."

> "You can retain the error message with 'return dev_err_probe'"

> "Please pad address to 8 hex digits: reg = <0x0 0x0a7c0000 0x0 0x20000>"

> "Leave these here and add pll8k and pll11k."
> *(DT bindings — specifying what should be in the schema)*

> "Nodes with a unit address must be ordered by it - the diff hunk above shows you that this
> one is out-of-order"

**Common phrasing patterns:**
- `"[X] should be a [correct type / variant], not [wrong type]."` — states the rule directly
- `"Should use [correct API] here rather than [wrong API]"` — no "you", impersonal
- `"[Correct API] handles [the problem] correctly."` — explains why
- `"Both [A] and [B] should [clean up / do X]."` — states expectation with scope
- `"You can [achieve same result better] with [alternative]"` — offers improvement
- `"[Imperative]: [correct form]"` — for DT/format issues, direct correction with example

**Tone:** Direct. API misuse comments are often a single sentence stating the correct approach.
No hedging — the maintainer knows the correct API and states it. But still impersonal ("should
use" not "you must use").

**Key behaviour:** Follow-up to pushback is often *shorter* than the original comment, not longer.
"Turning effects on and off are still things being turned on and off." — terse reaffirmation
after the author tried to argue.

---

### 2.4 Design Objections

**What they look like in the corpus:**

> "I would expect TDM to be configured by set_tdm_slot() from the machine driver, not from
> userspace. I see the driver does actually have a set_tdm_slot() operation..."

> "This would usually be doing using DAPM routing if it's expected to be runtime variable,
> define AIF widgets for the bus slots then route to them."

> "Device properties, this is better if it's supposed to be fixed for the system."
> *(choosing between approaches)*

> "Of course you can leave them, but I think we more or less _guarantee_ their inclusion by
> bitops.h..."

> "There's a bunch of issues reported by Sashiko: [URL] some of which looked valid."
> *(pointing to external validation rather than listing issues directly)*

> "Well, why do you think the binding would accept the GPIO if it couldn't be controlled?
> Of course it can power it on/off, check regulator core code"
> *(direct rebuttal after factual error)*

**Common phrasing patterns:**
- `"I would expect [correct design pattern] to be used, not [submitted approach]."` — framing
- `"This would usually be [correct approach] if it's expected to be [runtime / fixed]..."` — conditional
- `"[Alternative] is better if [condition]."` — comparative with condition
- `"Of course you can [leave it], but [consequence / preferred approach]..."` — acknowledges
  author's choice while guiding
- `"There's a bunch of issues [reported / found], some of which looked valid."` — diplomatic
  aggregate reference, doesn't enumerate each one

**Tone:** The reviewer expresses what the community would expect, not what the rule mandates.
"I would expect" and "This would usually be" appear constantly — they are authority signals
without commands.

**Key pattern:** Design objections often point to the *correct abstraction level*, not the
specific code: "configure from machine driver, not userspace"; "DAPM routing if runtime variable,
device properties if fixed." The reviewer names the framework-level answer, leaves the
implementation to the author.

---

### 2.5 Documentation / DT Binding Comments

**What they look like in the corpus:**

> "It does not look like you tested the DTS against bindings. Please run `make dtbs_check W=1`
> (see Documentation/devicetree/bindings/writing-schema.rst or [URL] for instructions)."

> "Drop |"
> *(single line — unnecessary pipe in YAML description that has no formatting)*

> "'status' should be the last property (let's also keep an \n before it) - file-wide"

> "Please pad address to 8 hex digits: reg = <0x0 0x0a7c0000 0x0 0x20000>"

> "Nodes with a unit address must be ordered by it - the diff hunk above shows you that this
> one is out-of-order"

> "Please add the node to sm6115.dtsi and override the compat string here."

> "Leave these here and add pll8k and pll11k."

> "I got now LKP report about build warning on clang (which I did not build with)."
> *(forwarding automated tool output)*

**Common phrasing patterns:**
- `"Drop [item]"` — single word or phrase for unnecessary elements
- `"[Rule]: [correct form]"` — brief rule statement followed by example
- `"Please [run tool / add node / pad address]"` — polite imperative with specific action
- `"It does not look like you [ran tool]. Please [run tool] (see [link] for instructions)."` —
  process guidance with documentation pointer
- `"[Property] should be [position / format] - [scope note]"` — rule + where it applies
- `"I got [automated report], some of which looked valid."` — forwarding external signals

**Tone:** DT comments are often the most mechanical of all categories. Short, direct, often
with the correct form shown inline. No apology, no padding. "Drop |" is a complete comment.

---

### 2.6 Style Nitpicks

**What they look like in the corpus:**

> "Drop |"

> "pm_ptr() (here and in the Subject), but it's a minor one."

> "There is no formatting to preserve and I did not ask to introduce it here. I commented in
> completely different place."

> "Weird location for this declaration. Why in the middle of nowhere?"

> "One nit below but it's not a strong [reason] for v2."
> *(scoping a nitpick as non-blocking)*

> "Not CC'ing reviewers is frowned upon on the list. Nonetheless, thank you for addressing
> the comments."

> "'status' should be the last property (let's also keep an \n before it) - file-wide"

> "I would suggest to rename the flag from X to Y"

**Common phrasing patterns:**
- `"Drop [item]"` — minimal, for obvious unnecessary elements
- `"[Issue], but it's a minor one."` — names the issue, explicitly scopes it as non-blocking
- `"One nit below but it's not a strong [reason] for v2."` — signals review outcome before detail
- `"[Behaviour] is frowned upon on the list."` — community norm stated as community norm, not
  personal preference
- `"Weird [thing]. Why [question]?"` — combines observation with question
- `"I would suggest to rename [X] to [Y]"` — hedged suggestion ("I would suggest"), not mandate

**Tone:** Style nitpicks are explicitly scoped. Maintainers tell you whether a nit is blocking
("With these two fixed: Reviewed-by: ...") or non-blocking ("but it's a minor one"). They do
not leave the author guessing.

**Key behaviour:** Nitpicks that are conditional approval blockers are always followed by the
`Reviewed-by` they unlock: "With these two fixed: Reviewed-by: ..." — the author knows exactly
what to do to earn the tag.

---

### 2.7 Approval Comments

**What they look like in the corpus:**

> "Applied to for-next branch now. Thanks."

> "Applied to\n\n   https://git.kernel.org/pub/scm/linux/kernel/git/broonie/sound.git for-next\n\nThanks!"

> "Nice and clean, thank you for the updates!\n\nAcked-by: Peter Ujfalusi <peter.ujfalusi@gmail.com>\n\nPS: sorry for the delay."

> "Acked-by: Mark Brown <broonie@kernel.org>"

> "Reviewed-by: Krzysztof Kozlowski <krzysztof.kozlowski@oss.qualcomm.com>\n\nBest regards,\nKrzysztof"

> "Thanks Hans!\n\nReviewed-by: Pierre-Louis Bossart <pierre-louis.bossart@linux.intel.com>"

> "Ack."

> "Applied, thanks!"

> "For patches 1-5,7\n\nReviewed-by: Andy Shevchenko <andy.shevchenko@gmail.com>"

> "Nit: LT9611UXC\n\nNevertheless,\n\nReviewed-by: ..."
> *(minor nit before approval — non-blocking)*

**Common phrasing patterns:**
- Bare `Reviewed-by: / Acked-by:` with no text = strong signal on its own
- `"Applied to [tree]. Thanks."` — terse apply notification
- `"Nice and clean, thank you for the updates!"` — positive but brief
- `"Thanks [Name]!"` + tag — named gratitude, brief
- `"Ack."` — single word when the patch is correct and nothing needs saying
- `"PS: sorry for the delay."` — review latency acknowledged casually
- `"For patches 1-5,7"` — scoped batch review with explicit patch numbers

**Tone:** Approvals are the shortest comment type. The tag does the work. Text before the
tag is brief or absent. "Nice and clean" is the longest praise seen in the corpus — two words.

---

## 3. Cross-Cutting Style Patterns

### 3.1 Sentence structures that appear across categories

| Pattern | Example from corpus | Category |
|---|---|---|
| `"I would expect X to be Y, not Z."` | "I would expect TDM to be configured by set_tdm_slot() from the machine driver, not from userspace." | design/clarification |
| `"This would usually be X if Y..."` | "This would usually be done using DAPM routing if it's expected to be runtime variable..." | design |
| `"Should use X here rather than Y"` | "Should use snd_soc_component_read() here rather than reading the regmap directly" | api_misuse |
| `"Why X?"` | "Why add a hog if you defined the regulator already?" | clarification |
| `"Is X [fixed/intentional]?"` | "Is this mclk frequency fixed?" | clarification |
| `"Either X or Y"` | "Either the driver needs to tolerate not having the name... or the property needs to be mandatory..." | justification |
| `"Can X be Y?"` | "Can this be moved into the common initialization path?" | design/style |
| `"Drop X"` | "Drop |" | style/DT |
| `"Please [imperative]"` | "Please run `make dtbs_check W=1`" | documentation |
| `"[Thing] is frowned upon on the list."` | "Not CC'ing reviewers is frowned upon on the list." | process |
| `"With these fixed: Reviewed-by: ..."` | "With these two fixed: Reviewed-by: ..." | conditional approval |
| `"Nit: [issue]\nNevertheless, Reviewed-by: ..."` | "Nit: LT9611UXC\n\nNevertheless, Reviewed-by: ..." | non-blocking nit |
| `"That's not really correct - X"` | "That's not really correct - the mutex is not destroyed..." | factual correction |
| `"Applied to [tree]. Thanks."` | "Applied to for-next branch now. Thanks." | apply notice |

### 3.2 Length norms

- **Approvals / apply notices:** 1–3 lines. Often just the tag.
- **Style nitpick (non-blocking):** 1–2 sentences. "Drop |" is acceptable.
- **API misuse (clear rule):** 1–2 sentences. State the correct API, optionally explain why.
- **Clarifying question:** 2–4 sentences. State what you expected, what you see, ask the question.
- **Design objection:** 3–8 sentences. Explain the tension; offer an alternative approach.
- **Factual correction:** 1–3 sentences. Direct. No hedging.
- **DT / documentation:** 1–5 sentences. Often includes an example of the correct form.

### 3.3 Things the corpus does NOT do

- Never says "you need to", "you must", "you have to" — always passive or question form.
- Never says "this is wrong" — says "I would expect", "should use", "this doesn't look like".
- Never invents a problem — every comment is grounded in something visible in the diff or
  a concrete known rule ("On/off switches should be a Switch control, not an enum.").
- Never writes multi-paragraph explanations for simple nitpicks.
- Never apologizes for the concern itself — only apologizes for latency ("PS: sorry for the delay").
- Never tags a comment as BLOCKER or WARNING — severity is implicit in phrasing and ordering.
- Rarely uses exclamation points except in approvals ("Applied, thanks!").
- Never addresses the author by name in the review comment body.

### 3.4 Structural invariants

1. **Quote, then comment.** The reviewer quotes the exact offending lines, then writes their
   concern below the quote with no separator. This is the mbox thread convention.

2. **Ask before asserting.** When uncertain, a question is preferred over a statement.
   "Should this also check hw_init?" is better than "This should check hw_init."

3. **Conditional approval is explicit.** If a `Reviewed-by` is conditional on a fix, the
   condition precedes the tag. "With these two fixed: Reviewed-by:..." never leaves the
   author guessing.

4. **Scope is stated for nitpicks.** "One nit below but it's not a strong [reason] for v2."
   Authors need to know whether to re-spin.

5. **Short follow-ups after pushback.** When an author disputes a comment, the maintainer's
   reply is shorter, not longer. "Turning effects on and off are still things being turned on
   and off." — the maintainer stands firm, does not re-litigate.

6. **Multiple issues in one email.** A single review reply often contains 3–6 separate points.
   They are separated by blank lines or new `> quote` blocks, not numbered lists.

7. **External signals are forwarded, not re-explained.** "There's a bunch of issues reported by
   Sashiko: [URL] some of which looked valid." — the maintainer points to the tool output and
   lets the author investigate.

---

## 4. Style Guide: ASoC Reviewer Community Voice

### Rule 1: Ask, don't assert
Unless the rule is unambiguous (API misuse, DT format), phrase findings as questions.
> ✓ "I would expect TDM to be configured by set_tdm_slot() from the machine driver — I see the driver does actually have a set_tdm_slot() operation?"
> ✗ "TDM must be configured by set_tdm_slot(), not userspace."

### Rule 2: Reference community practice, not personal preference
> ✓ "This would usually be done using DAPM routing if it's expected to be runtime variable."
> ✗ "You should use DAPM routing here."

### Rule 3: Use "Either...or" for open questions
When the finding has multiple valid resolutions, present both rather than picking one.
> ✓ "Either the driver needs to tolerate not having the property, or it needs to be marked mandatory in the bindings."
> ✗ "Mark this property as required in the binding."

### Rule 4: State API corrections directly, without hedging
When the correct API is unambiguous, say it plainly in one sentence.
> ✓ "On/off switches should be a Switch control, not an enum."
> ✓ "Should use snd_soc_component_read() here rather than reading the regmap directly."

### Rule 5: Name the correct abstraction level, not the implementation
> ✓ "This would usually be done using DAPM routing..." (names the framework)
> ✗ "You should create snd_soc_dapm_widget structs for each slot and register them with..."

### Rule 6: Keep approvals and apply notices short
> ✓ "Applied to for-next. Thanks."
> ✓ "Acked-by: [name]"
> ✗ "I have carefully reviewed all five patches in this series and I believe they are ready for inclusion..."

### Rule 7: Scope nitpicks explicitly
> ✓ "One nit below but it's not a strong reason for v2. [Reviewed-by:]"
> ✓ "With these two fixed: [Reviewed-by:]"
> ✓ "Nit: LT9611UXC\n\nNevertheless, [Reviewed-by:]"
> ✗ (leaving the author unsure whether a nit is blocking)

### Rule 8: Forward external signals, don't re-explain them
> ✓ "There's a bunch of issues reported by [tool]: [URL] some of which looked valid."
> ✗ (listing every automated finding inline and explaining each one)

---

## 5. Phrase Pattern Library

### Clarifying questions
```
I would expect [X] to be configured by [correct owner], not from [wrong owner].
I see the driver does actually have [relevant hook]...

Why is this a separate [API / function / flag]? What is the situation where we would want [undesired outcome]?

Is this [value / frequency / behavior] fixed?
Typically, [community convention or example].

[Other similar thing in codebase] [does X], should this one?

Should [this path / this control / this callback] also [do Y]?
```

### API misuse
```
[On/off switches / DAPM volumes / register reads] should be [a Switch / component_read / the component API], not [enum / regmap direct access].

Should use [correct_function()] here rather than [wrong_function()] — [correct function] handles [the concern] correctly.

Both [path A] and [path B] should [clean up / do X].

You can [achieve same result] with '[correct API]'.
```

### Design objections
```
I would expect [X] to be [done in the machine driver / configured via DT / handled by DAPM], not [submitted location].

This would usually be done using [correct pattern] if it's expected to be [runtime variable / fixed for the system].

[Device properties / set_tdm_slot / devm_ variants] [is / are] better if [condition].

Of course you can [leave the current approach], but [consequence of doing so].

Either [option A] or [option B].

Can this be moved into [more appropriate location] instead of [current location]?
```

### Style nitpicks
```
Drop [item].

[Incorrect form]. Nit: [correct form].

[Property] should be the [first / last] property [scope modifier, e.g. "file-wide"].

Please pad address to [N] hex digits: [correct form].

Nodes with a unit address must be ordered by it — the diff hunk above shows this one is out-of-order.

I would suggest renaming [X] to [Y].

One nit below but it's not a strong reason for v[N].
```

### DT / documentation
```
It does not look like you tested the DTS against bindings. Please run `make dtbs_check W=1` (see [link] for instructions).

Please add the node to [base dtsi] and override the compatible string here.

New compatible strings need a binding document — either a new schema or an update to an existing one covering [family].
```

### Conditional approvals
```
With [these / this] fixed:

Reviewed-by: [name]

---

Nit: [minor issue]

Nevertheless,

Reviewed-by: [name]
```

### Factual corrections
```
That's not really correct — [brief explanation of actual behavior].

With this addressed the patch looks good.

Well, why do you think [the binding / the API / the framework] would [do X] if it couldn't [Y]? [Correct explanation]. Check [source].
```

### Apply / final approval
```
Applied to for-next branch now. Thanks.

Applied to

   https://git.kernel.org/pub/scm/linux/kernel/git/broonie/sound.git for-next

Thanks!

Applied, thanks!

Nice and clean, thank you for the updates!

Acked-by: [name]
```

---

## 6. Comment Templates (Ready for Prompt Injection)

These are composite templates derived from the most common structures in the corpus. They
can be injected as few-shot examples into the KRI prompt system.

### Template A — Clarifying Question (API / design)
```
> [quoted line of concern]

I would expect [X] to be [done by / configured from / handled via] [correct owner], not [submitted
approach]. I see [the driver / this subsystem / the existing code] does [actually / already] have
[relevant hook / API / pattern] — is there a reason not to use it here, or is the intention to
[alternative purpose]?
```

### Template B — API misuse (clear rule)
```
> [quoted line]

[Wrong construct] should be [a / use the] [correct construct], not [wrong construct].
[Optionally: The [correct] API handles [concern] correctly.]
```

### Template C — Design objection with alternative
```
> [quoted line]

This would usually be done using [correct approach] if [condition — e.g. "it's expected to be
runtime variable"]. [If different condition], [alternative approach] would be better. [Optional:
Either [option A] or [option B].]
```

### Template D — Style nitpick (non-blocking)
```
> [quoted line]

Nit: [brief correction — e.g. "Drop |" / "should be last property" / "pad address to 8 hex digits"].
[Optional: scope: "file-wide" / "in the Subject too".]

[If not blocking: "One nit but it's not a strong reason for v[N]." or continue to Reviewed-by.]
```

### Template E — Conditional approval
```
> [quoted line]

[Brief concern]. With [this / these two] fixed:

Reviewed-by: [Reviewer identity]
```

### Template F — DT binding process
```
> [quoted DTS node or schema line]

[Observation about what's missing or wrong]. [If applicable: Please run `make dtbs_check W=1`
to validate.] [If missing binding: New compatible strings need a binding document — either a new
schema or an update to an existing one covering [family]. Could you clarify whether this is coming
in a follow-up?]
```

---

## 7. Prompt Injection Strategy

### Current state (gap)

`SYSTEM_KERNEL_REVIEWER` establishes a generic persona. `upstream_comment` field instruction adds
one synthetic example. Zero real lore comment excerpts are in any prompt.

### Recommended injection: 3-tier approach

#### Tier 1 — System prompt enrichment (always-on)

Append to `SYSTEM_KERNEL_REVIEWER` in `kri/llm/prompts.py`:

```
When writing maintainer-style comments, follow these community conventions:

PHRASING:
- "I would expect [X] to be [Y], not [Z]." for design concerns
- "Should use [correct API] here rather than [wrong API]." for API misuse
- "Either [option A] or [option B]." when multiple resolutions are valid
- "Drop [item]." for unnecessary elements
- Short follow-ups after pushback — do not re-litigate

SCOPE SIGNALS:
- "One nit but not a strong reason for v[N]." — non-blocking
- "With this fixed: Reviewed-by: ..." — conditional blocking
- "Nit: [X]\nNevertheless, Reviewed-by: ..." — noted but approved

LENGTH:
- API misuse: 1–2 sentences maximum
- Design objection: 3–8 sentences including the alternative
- Style nit: 1 sentence; "Drop [X]" is a complete comment
- Approvals: tag only, or tag + 1 sentence of thanks

WHAT NOT TO DO:
- Do not say "you need to", "you must", "you have to"
- Do not say "this is wrong" — say "I would expect" or "should use"
- Do not enumerate every issue — forward external tool output by URL when available
- Do not explain the implementation — name the correct abstraction (set_tdm_slot, DAPM routing,
  devm_kzalloc) and let the author implement it
```

#### Tier 2 — Category-matched few-shot examples (per finding type)

When generating `upstream_comment`, select 1–2 examples from this corpus matching the
finding's `category` field and inject them as "Here is an example of how this type of concern
is typically phrased in lore review threads:" before the instruction.

Example mapping:
- `category: "api_misuse"` → Template B ("On/off switches should be a Switch control, not an enum.")
- `category: "design"` → Template A or C ("I would expect TDM to be configured by set_tdm_slot()...")
- `category: "dt_binding"` → Template F (dtbs_check instruction)
- `category: "style"` or `"convention"` → Template D ("Drop |" / "pad address to 8 hex digits")

#### Tier 3 — `format_lore_reply()` fix (highest priority)

The `upstream_comment` field already contains the best maintainer-voiced text KRI generates.
`format_lore_reply()` in `kri/llm/formatter.py:64` currently uses `comment.message` instead.

Change `formatter.py:64` from:
```python
reply_lines.append(f"  {tag} {comment.message}")
```
to:
```python
reply_lines.append(f"  {comment.upstream_comment or comment.message}")
```

This is the single change with the highest immediate return — it requires zero new prompting and
recovers work already being done.

---

## 8. Key Conclusions

1. **The ASoC reviewer community communicates via questions, not verdicts.** "I would expect",
   "Should use", "Why X?" are the dominant sentence openers across all substantive categories.
   Direct assertions ("This is wrong") are rare and reserved for clear factual errors.

2. **Length is inversely correlated with confidence.** Longer comments signal uncertainty or
   design discussion. Shorter comments ("Drop |", "On/off switches should be a Switch") signal
   unambiguous rules. KRI's current `upstream_comment` output is too uniform in length — all
   comments are 3–5 sentences regardless of concern type.

3. **Conditional approvals are a critical communication pattern.** "With these two fixed:
   Reviewed-by: ..." is the most actionable comment structure in the corpus. KRI does not
   currently generate anything like this.

4. **Approvals are the most common comment type by count, and the shortest by length.** Any
   corpus-trained prompt system must represent approvals proportionally — otherwise the model
   will write elaborate text for every finding, which is not maintainer behavior.

5. **DT binding comments follow a stricter format** than code comments. They include the
   correct form inline ("pad address to 8 hex digits: reg = <0x0 0x0a7c0000 ...>") and often
   reference validation tools (`make dtbs_check W=1`).

6. **Real names / "Best regards" signatures appear consistently** in shorter approvals and
   international reviewers. This is a cultural marker worth preserving in templates even
   without attribution.

---

## References

- Corpus: `data/lore_cache/` (129 `.mbox.gz` files, 2022–2026)
- Canonical fixtures: `data/lore_cache/FIXTURES.json` (3 ground-truth threads)
- Upstream gap analysis: `docs/MAINTAINER_STYLE_AUDIT_2026-07-21.md`
- Implementation targets: `kri/llm/prompts.py`, `kri/llm/formatter.py`
- Key fix: `formatter.py:64` — use `upstream_comment` instead of `message` in lore reply
