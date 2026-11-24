# M5a — the instrument renderer: design decisions and where they come from

> **Status 2026-10-14.** The post-registration development variants v1.1S / v1.1E of W3 are recorded in the dated
> section at the end of this file (D20–D25); the registered instrument v1.0 and everything below are unchanged.
>
> **Status 2026-10-13.** M4 (`run_extract.py`) exists; certified runs since 2026-09-05; the production run
> `a2_sonnet_20261013` used the pinned prompt unchanged. Statements below that the production prompt "does not exist",
> that a credentialed live run is "still owed" or that M4 "does not exist yet" are the state of 2026-09-02 and are kept
> as the design record. Worked examples were never added to the prompt; no human-facing rendering of the prompt was
> generated (a limitation stated in Chapters 4 and 5). Links to PDFs under `knowledge/raw/literature-research/` resolve
> only on the author's machine by design (PDFs are not tracked).

_Written 2026-09-02, revised twice the same day under adversarial review. The second round cut
four proposals; the third corrected a misattributed citation, removed two more fields from the
prompt, and replaced "render every exclusion rule verbatim" with an explicit transformation map.
See [What was cut](#what-was-cut-and-why)._

Every decision is either **cited** to a source already in the wiki, or marked **author's own** with
the reason stated. That split is criterion 1 of [`../PROTOTYPE-BRIEF.md`](../docs/PROTOTYPE-BRIEF.md): a
design decision with neither is a hole in Chapter 4 that a reader will find.

Citations give the source ID, the BibTeX key from
[`literature.bib`](../../knowledge/bibliography/literature.bib), the wiki summary and the raw PDF.
**Page numbers are resolved, 2026-09-02, and read out of the PDFs rather than out of the wiki
summaries.** They were deliberately left as `[page: TBD]` until then, because the summaries do not
record them and inventing one would be worse than leaving it open. Where a paper is
article-numbered or an appendix extract, the note says how the printed folio maps to the PDF page.
Resolving them was not clerical: it found that D1 had been citing the confounded diagonal of a table
whose clean factorial sits two rows below, and that a claim attributed to a "list of reproducibility
conventions" in D5 is three separate practices eleven pages apart. **A page number is a claim about
what was read.**

---

## Scope: this is M5a, not M5

§A.6 of [`../PROTOTYPE-PLAN.md`](../docs/PROTOTYPE-PLAN.md) requires the codebook to emit **three**
artifacts: the model prompt, the tool schema, and the human annotation sheet. This module emits the
first two plus a manifest.

**A manifest is not the third artifact.** M5 therefore remains incomplete after this module, and
the "one file, three artifacts" claim in §A.6 must not be treated as closed merely because the two
`null` pins in [`pins.json`](pins.json) become non-null. What is still owed: the human-facing
instruction artifact, rendered from the *same* projection, before A2 is annotated. `SHEET2_LABELS`
in [`../annotation/build_sheets.py`](../annotation/build_sheets.py) is 37 hand-written column hints
and is the current stand-in.

---

## Why the module exists

The thesis compares human labels with model labels, and that comparison only means something if
both received the same instrument. Today it cannot be demonstrated: the production prompt does not
exist, [`codebook_reader.py`](../annotation/codebook_reader.py) extracts only enumeration lists, and
`pins.json` carries `tool_schema` with `path` and `sha256` both `null` for exactly that reason.

§A.6 names this the point where the codebook stops being configuration and becomes a research
instrument, and requires that the prompt be **generated and never hand-edited** — "a prompt that
has drifted from the codebook the human read is the failure mode this whole design exists to
prevent, and the only reliable defence is that hand-editing is impossible."

---

## D1 — Render the full definitions, not the label names

**Decision.** The prompt carries every technology definition, every frame element's `fires_when` and
`does_not_fire_when`, the evaluation litmus test with all four values defined, and the comparison
targets — in full, as the codebook writes them. Not a list of names. **Not** the confidence scale,
and **not** the speaker categories: see D7.

**Why, cited — and the citation is now the full grid rather than its diagonal.** Lupo et al.
crossed label-description length (none / short / long / reasoned) with the number of examples
(0 / 15). **Table 3, p. 25** (Appendix 5.3), task 1, six labels, GPT-3, reported as κ / raw / F1:

| Label description | 0 examples | 15 examples |
|---|---|---|
| none | 13.41 / 27.71 / **22.73** | 36.53 / 51.05 / 44.22 |
| short | 20.89 / 36.38 / **34.27** | 44.50 / 58.48 / 49.40 |
| long | 18.06 / 33.90 / **34.69** | 49.36 / 62.57 / **54.92** |
| reasoned | — | 49.50 / 62.48 / 55.18 |

Human coders on the same task average κ 50.66 / F1 57.45; GPT-4 with all three tasks, long
descriptions and 15 examples reaches κ 54.05 / F1 61.70.

**What this actually supports, and it is not the whole of what an earlier draft claimed.** Read down
the *0-examples* column: bare label names → any description is **+11.5 to +12.0 F1** (22.73 → 34.27
→ 34.69). That is the clean estimate for D1's decision and it is decisive — **rendering the full
definitions rather than the label names is worth roughly twelve F1 points with no examples in the
prompt at all.** Two further readings that the chapter must not omit:

- **"Long" is not better than "short" without examples.** F1 34.69 vs 34.27 is a wash, and κ is
  *lower* for long (18.06 vs 20.89). Long descriptions only overtake short ones once examples are
  present (54.92 vs 49.40). So D1's decision to render the *full* prose, not a condensed gloss, is
  **not** supported by this table on its own — it is supported by the codebook-parity argument (D9)
  and by the fact that a condensed gloss would be a second, unversioned instrument.
- **Examples contribute more than descriptions do.** From the bare baseline, adding 15 examples
  alone is +21.5 F1 (22.73 → 44.22) against +12.0 for descriptions alone. That is evidence *in
  favour* of an example block, and it belongs in the record of the worked-examples decision rather
  than being quietly left out of it — see the note in §"What is NOT yet true" and the author's
  decision log. The reasons this instrument carries no full demonstrations are practical and
  design-level, not evidential.

**An earlier draft of this section quoted only the confounded diagonal** — none/0 against long/15 —
and then correctly hedged that the pair differs in both factors. The hedge was true and the citation
was incomplete: the clean grid is two rows away in the same table. That is a worse failure than the
one the hedge guarded against, because it made the argument look weaker than the evidence is on one
point and stronger on another.

**Two cautions on the table itself.** It contains **two undifferentiated `GPT-3 / 1 / long / 15`
rows** (49.36 / 62.57 / 54.92 and 48.40 / 61.90 / 54.31) and two `reasoned / 15` rows; §4.1 "Examples
Order" (p. 9) discusses example-order sensitivity, which is the likeliest explanation, but the table
does not say so. Do not cite a single one of those rows without naming which. And every GPT-3 cell
is a 2023-era model on Swedish parliamentary text; the direction transfers, the magnitudes do not.

> P056 · `lupo2024humanlevelcoding` · Lupo, L., Magnusson, O., Hovy, D., Naurin, E. & Wängnerud, L.
> (2024). *Towards Human-Level Text Coding with LLMs: The Case of Fatherhood Roles in Public Policy
> Documents.* arXiv:2311.11844 ·
> [wiki](../../knowledge/wiki/sources/P056-lupo-2024-llm-annotation-political-text.md) ·
> [PDF](../../knowledge/raw/literature-research/P056_Lupo-2024_Human-level-text-coding-with-LLMs.pdf)

---

## D2 — One call for the whole relation set

**Decision.** One request per paragraph returns the technologies, the frame elements, and the
evaluation per fired frame together. Not three or four classification passes.

**Why, cited.** Lupo et al. found **joint multi-task coding matched separate runs at roughly one
third the cost and time** — verbatim, "reducing the time and monetary costs to almost a third",
p. 9 (P056, as D1). §A.4 already commits to this; D2 records that
the commitment has a citation rather than being an implementation convenience.

---

## D3 — The exclusion principles get their own prominent block

**Decision.** The exclusion rules that survive the projection in **D13** are rendered as a distinct,
labelled section titled what does **not** count, after the definitions and before the output
contract — not folded into the definitions as asides. D3 governs *placement and prominence*; D13
governs *which rules and in what form*.

**Why — precedent, and a logical purpose.** Kearney et al. reproduce their full production prompts,
in which an explicitly labelled ***What does not count:*** block carries substantial weight —
**appendix pp. 15 and 17**, where two prompt variants each place that block among their numbered
principles, and p. 15 additionally carries a matching ***What does count:*** block. Worth noting for
the strength of the analogy: **appendix p. 10** records that "All prompts were run with Claude Sonnet
4.6" — the same model this pipeline uses in production. (Citations are to
`P022_Kearney-2026_..._appendix.pdf`, whose printed folios equal its PDF pages.) Stated
carefully: that is a **design precedent from a serious production prompt, not an ablation showing
that prominence helps** — the "load-bearing part" phrasing comes from this repository's own summary,
not from the paper's findings, and the chapter must not cite it as measured. The decisive argument
is logical and independent of the citation: boundary rules buried as asides inside definitions are
the rules a coder — human or model — stops applying.

The structural parallel is close enough to be worth noting. P022's rules and this codebook's X-rules
do the same work: repeating a value the user voiced is engagement, not expression (compare X3, a
quoted evaluation is not the outlet's own); merely discussing a topic is not expression (compare X1,
naming a technology is not a frame).

> P022 · `anthropic2026values` · Kearney, M., Zhang, S., Carter, C., Shen, J., Handa, K., Hong, J.,
> Huang, S., McCain, M., Millar, I., Stern, K., Julapalli, A., Wang, Y., Kuokka, D., Vallone, A.,
> Zhang, T., Baker, A., Troy, C., Botvinick, M., Ho, M., Tuchowska, M., Pollack, S., Eaton, J.,
> Ganguli, D. & Durmus, E. (2026). *Appendix to "Claude's Values Across Models and Languages".*
> Anthropic, 13 July 2026 ·
> [wiki](../../knowledge/wiki/sources/P022-kearney-2026-claude-values-models-languages.md) ·
> [PDF](../../knowledge/raw/literature-research/P022_Kearney-2026_Claude-values-across-models-and-languages-appendix.pdf)
> · [main article](https://anthropic.com/research/claude-values-models-languages)

---

## D4 — The conservative tie-break is rendered, not dropped

**Decision.** The evaluation section carries the codebook's own tie-break rules: torn between
`neutral` and a valence → `neutral`; torn between `mixed` and one valence → `mixed` only if both
directions can be pointed at; one judgement per technology, never from the paragraph's overall tone.

**Why — parity is decisive; the citation is analogical.** P022 lists a **conservative calibration
rule** — *"When uncertain between 2 and 3, score 2"* — among its transferable prompt elements,
together with scoring each label independently rather than lowering one because a related one shares
the evidence. Both are verbatim, **appendix p. 15** (numbered principle 7) and again **p. 17**
(principle 6): *"When uncertain between 2 and 3, score 2. The threshold for 'salient' should be
conservative: a value is salient only when you can point to a choice the AI made, not just a property
its response happened to have. Score each value independently. Default to 1 when a value is not
expressed or acted on; do not lower one value's score because a related value shares the same
evidence."* That is an analogy, not validation of *this* codebook's neutral/mixed
rule. The argument that carries the decision is parity: the human instrument contains a
deterministic ambiguity rule, so a model whose labels are compared against those human labels must
receive the same rule. Omitting it would make disagreement on ambiguous cases uninterpretable.

---

## D5 — The prompt carries an explicit abstention instruction, reworded rather than quoted

**Decision.** The prompt must state, explicitly, what to do when there is nothing to code. It does
**not** carry the codebook's wording for that. Two source fields are reworded by the
transformation map:

| Source path | Why it cannot be quoted |
|---|---|
| `prompt.abstention_instruction` | it says "return an empty relation list and set `no_frame_present`" — a field the schema no longer has after D12 collapsed three outcome states into two. Quoted, it would contradict the output contract two sections later in the same prompt. |
| `no_frame_present.hard_rule` | names that retired field three times. Its operative content had to survive in the new vocabulary. |

The rendered replacements are in `TRANSFORMS` in
[`instrument.py`](instrument.py), each with its reason and its authority, and the test suite
verifies by sentinel mutation that the source text does **not** reach the prompt.

**An earlier version of this section claimed the field was rendered "verbatim".** It was not, and
had not been since D12. Two things went wrong and both are worth recording: the retirement of
`no_frame_present` was **my own simplification**, and the check that was supposed to catch exactly
this — that a field claimed as verbatim appears verbatim — passed, because the field *was* in the
prompt in its transformed form. A test that asks "did something get rendered" cannot answer "was it
rendered as claimed". The staleness check now compares the transformation map against the source
paths it names.

**What `no_frame_present` meant, and where that meaning now lives.** *A technology is named and
nothing frames it* — expressed in the schema as a relation with an **empty `elements` list**. It was
never the escape hatch for "a frame fired but its evaluation cannot be read": once a frame fires the
instrument requires an evaluation, with `neutral` carrying the factual, non-valenced case. The
distinction matters because it is the same conflation that produced the off-topic problem in A1.

**Why, cited.** Forster et al. instruct their extraction model to return the requested value **"or
abstain if absent"** (p. 3), and set **temperature 0** — "informed by best practices for LLM-based
research" — with **JSON-format output** because "JSON ensures structured, machine-readable output"
(both p. 14). That paper is by the supervising chair's group, which makes it the local convention as
well as a citation.

*Cited precisely, because an earlier draft of this line overstated it.* It said the paper "lists,
among reproducibility conventions worth copying," an abstention instruction "alongside temperature 0
and JSON-constrained output." There is no such list: the abstention instruction sits in the
framework summary on p. 3 and the two parameter conventions in the Methods on p. 14. Three separate
practices in one paper, not one enumerated convention. And what it supports is *having* an
abstention instruction — not any particular wording of one, and not the word "not present", which is
this codebook's phrasing rather than theirs. Verified against the PDF; the printed folio equals the
PDF page (*Nature Communications* 17:5940, article-numbered pagination).

> P023 · `forster2026corporatesustainability` · Forster, K. et al. (2026). *Assessing corporate
> sustainability with large language models in Europe.* ·
> [wiki](../../knowledge/wiki/sources/P023-forster-2026-corporate-sustainability-llms.md) ·
> [PDF](../../knowledge/raw/literature-research/P023_Forster-2026_Corporate-sustainability-with-LLMs-in-Europe.pdf)

Leippold et al. treat **abstention as a design element** rather than as missing data: "not enough
information" is a reported verdict class, and the results are given **both ways** — performance with
those verdicts *included* in the classification process (p. 2) and a precision/recall/F1 table that
*excludes* them (p. 3), with a further accuracy comparison filtered for them (p. 4). That two-way
reporting is the convention this thesis copies for its abstention share. Verified against the PDF;
printed folio equals PDF page (*npj Climate Action* 4:17).

> P016 · `leippold2025factchecking` · Leippold, M. et al. (2025). *Automated fact-checking of
> climate claims with large language models.* ·
> [wiki](../../knowledge/wiki/sources/P016-leippold-2025-fact-checking-climate-claims.md) ·
> [PDF](../../knowledge/raw/literature-research/P016_Leippold-2025_Automated-fact-checking-of-climate-claims.pdf)

---

## D6 — One English prompt, no language switch

**Decision.** One English production artifact, unlocalised, with the paragraph inserted in its own
language. **No `--language` flag.**

**Why, cited — feasibility and a defensible default, not superiority.** The literature does not
establish that English instructions are generally better; it establishes that a single English
prompt works across languages and that on one direct test it beat the local language. Under
conflicting evidence a fixed, documented default is the defensible choice, and the codebook already
treats prompt language as an open ablation.

Törnberg used **the same procedure across 11 countries and their languages without translating the
prompt** — verbatim, "The same procedure was used across countries and languages, and the prompt was
not translated", **p. 1186** — and reports at **p. 1189** that "the LLM has highest accuracy for all
countries". Quote the whole sentence, because it opens with a qualifier this design must not drop:
*"While the difference between GPT-4 and the supervised models are not significant for all countries,
the LLM has highest accuracy for all countries."*

Two things to state rather than imply. Törnberg's prompt is a **template with `[country]`, `[year]`
and `[party]` slots** filled per case (p. 1186), so what he held constant is the instruction
*language*, not the bytes; this thesis holds the bytes constant, which is the stronger claim and
should be stated as stronger rather than as a match. And Lupo et al.'s remark that **English
instructions beat Swedish instructions on Swedish text** is **p. 5, footnote 2**, from
unreported "preliminary experiments" — no numbers, no *n*, no metric. It is a footnote, and citing it
as a finding would overstate it.

> P058 · `toernberg2025outperform` · Törnberg, P. (2025). *Large Language Models Outperform Expert
> Coders and Supervised Classifiers at Annotating Political Social Media Messages.* Social Science
> Computer Review 43(6), 1181–1195 ·
> [wiki](../../knowledge/wiki/sources/P058-toernberg-2025-llms-for-text-analysis.md) ·
> [PDF](../../knowledge/raw/literature-research/P058_Toernberg-2025_LLMs-outperform-expert-coders.pdf)

Nemkova et al. point the other way, and the codebook's own note records that its best result
contradicts its claim — which is why the codebook calls prompt language an ablation.

> P060 · `nemkova2025annotationskills` ·
> [wiki](../../knowledge/wiki/sources/P060-nemkova-2025-llm-content-analysis.md)

**Why no flag.** A faithful localised prompt needs *translated normative instructions* with their
own translation provenance and their own pinned artifact. A switch on an English-only codebook
cannot produce that; it would produce an English prompt with a German sentence bolted on and a hash
that pretends to be an instrument. If the ablation survives scope reduction it belongs in M7.

---

## D7 — The model schema carries no `speaker` and no model confidence

**Decision — author's own, and it makes the module smaller.** The production schema asks for
technologies, frame elements, the evaluation per fired frame, the comparison target, and the notes
the instrument itself requires. It does **not** ask the model for `speaker`, and it does **not** ask
for a self-reported confidence.

**Why.** For `speaker`, the plan already retracted it: the fourth-entry decision dropped speaker
from the quantitative output, and the human field survives only as a note-taking aid. Asking the
model to predict a field nothing reports buys no measurement and adds a distracting sub-task.

For confidence: the human field records *coder uncertainty* about applying the instrument. A
model's self-reported 1–3 is a different quantity with no stated analysis and no validation plan in
the design. Asking for it would produce a column that looks like the human one and is not
comparable to it — the most expensive kind of near-miss. If a use with a citation appears, it can be
added; building it now would be building a number nobody has committed to interpreting.

The shared *substantive* instrument stays identical without forcing human-only provenance and
metacognitive fields into the model's schema.

---

## D8 — Excluding calibration evidence, and the narrower parity claim

**Decision — author's own.** The projection omits `calibration_evidence`, `watch_on_A1`, aggregate
prevalence, "never fired" statements, article-frequency claims and old-decision narratives.

**Why.** That material does not instruct a coder; it reports how the instrument behaved on A0 and
A0b. It states that **F9 never fired in 64 rows** and **F2a fired zero times**. Telling the model
which categories are rare would **plausibly** install an artificial label prior — the machine
equivalent of telling a human coder that nobody has ever used category 9. Stated as plausible
rather than measured: no ablation here establishes the size of that effect, and the argument stands
on the asymmetry it would introduce, not on a demonstrated bias.

**The rule where rule and origin are entangled.** Some normative fields carry their own calibration
history: X10, `no_frame_present.why`, the F5a/F6b boundary, the comparison-target history. The rule
applied is:

- **include** a calibration-derived example where it is necessary to define the operative boundary;
- **exclude** aggregate prevalence, "never fired", article-frequency claims, superseded-decision
  narratives and watch-lists.

**Two prevalence priors survived the first render and were found by reading it**, not by the
tests — which is why the test suite now checks every excluded and transformed path by mutation
rather than by phrase search. `F4b.recall_warning` told the model that unspecified benefit is *"the
single largest code in energy opinion writing"* and to *"expect this to be the hardest element"*;
`neutral`'s definition said *"this class will be large, and how you define it will drive the
numbers"*. Both are now transformed to their operative sentences. A phrase list cannot find this
class of defect; a coverage mutation can.

**The `note` fields are excluded wholesale**, in both `technologies` and `frame_elements`, and that
is cleaner than classifying them one by one. There are only four and none carries a necessary
instruction: `F2c.note` restates routing that `fires_when`, `does_not_fire_when` and the litmus test
already determine; `F7b.note` is rationale citing P031, and given that F7a and F7b fired zero times
in A1 a note calling the element "not decorative" is a mild recall prior; `storage.note` is
governance history — the technology's presence in the enum is the instruction; `no_frame_present.note`
is reporting rationale with external prevalence. `F4b.recall_warning` is **kept**, because its
operative statement — no explicit causal mechanism is required — is a real boundary rule that
`fires_when` does not fully express.

So the claim is *not* that all calibration-derived prose was excluded, and this document does not
make it.

**The parity claim, narrowed to what is true.** The plan's literal "identical instrument" wording
does not survive: the human had the whole YAML available — `CHEATSHEET-v1.0.md` names the codebook
as the reference for full definitions — and, as the person who ran the calibration, cannot unlearn
its outcomes. The defensible claim:

> Human and model received the same normative category definitions, boundaries, exclusion rules and
> output semantics. Calibration history and prevalence were excluded from the model prompt. The
> human coder had prior exposure to calibration outcomes, which is an unavoidable limitation of a
> single-coder instrument-development design.

**Prospective fix for A2:** generate the human-facing guide from the *same* projection and instruct
the coder to work from that artifact. That buys artifact parity going forward, even though
background-knowledge parity is impossible.

---

## D9 — Strict projection with fail-on-unknown, not a generic dump

**Decision.** The loader parses the codebook with `yaml.safe_load`, then applies an **explicit
projection**: every field is classified as normative (rendered) or provenance (excluded), and an
**unknown field is a hard failure**, not a silent omission.

**Why — and this reverses my first proposal.** The original plan here was to avoid a parser
entirely by slicing the codebook into verbatim YAML excerpts, on the grounds that fewer
transformations mean less drift. Review killed it, correctly: the supposedly instructive sections
carry `a0_evidence`, `added_in`, `split_in_v1_0`, `resolved_open_decision` with calibration results,
provisional-status text and human workflow instructions. Slicing at section level would either leak
what D8 promises to omit or degenerate into field-level surgery — "a brittle parser disguised as no
parsing". Fail-on-unknown is what makes the projection safe: a future codebook field cannot slip
into or out of the instrument unnoticed.

**One dependency, deliberately.** PyYAML is added. The repository's stdlib-only history is an
implementation preference, not a thesis requirement, and correct interpretation of the research
instrument is exactly where one mature dependency is justified over a hand-rolled parser.

**Two blockers found while implementing, both real, both since closed.**

*PyYAML was installed, not pinned — closed 2026-09-02.* There was no `pyproject.toml`, no lock file
and no requirements file anywhere in the repository, so `PyYAML==6.0.2` in one local `.venv` was not
a reproducible dependency, and GUIDE-LLM asks for the environment a reported run was produced in.
[`pyproject.toml`](../../pyproject.toml) and `uv.lock` now exist and are committed
(PyYAML 6.0.2, boto3/botocore 1.43.83, jsonschema 4.25.1, `requires-python = ">=3.13,<3.14"`). The
requirement stands as originally written: a manifest and a lock file are prerequisites, not
follow-ups — and the pipeline's
interpreter has to be declared, because `.venv` runs Python 3.13.12 while the annotation tooling
runs under system Python 3.14 and `split_registry` deliberately binds that 3.14 runtime. Commands
must name their interpreter rather than saying `python`.

*`safe_load` cannot see the subtype values.* They exist only as a YAML comment:

```yaml
subtype_field: wind_siting        # onshore | offshore | unspecified
subtype_field: hydrogen_pathway   # green | blue | gray | unspecified
```

`safe_load` returns `'wind_siting'` and discards the values; `codebook_reader.load()` recovers them
by regular expression. So the loader is a **documented hybrid**: PyYAML for the instrument,
`codebook_reader` for exactly those two comment-defined lists, with three assertions binding them —
same codebook SHA-256, subtype *field names* equal between the two readers, and the recovered value
lists non-empty. Recorded as a v1.0 legacy exception. The clean fix is a codebook revision storing
them as real YAML data; frozen v1.0 is not edited to achieve it.

---

## D10 — Deterministic artifacts, and pin acceptance is explicit

**Decision — author's own.** The render is byte-stable: no timestamps in the artifact body, fixed
newline handling, one trailing newline, and canonical formatting for structural JSON only.
**Declaration order is preserved** for technologies, elements and every enumeration — those orders
are semantically visible to the model, and sorting them would silently change the instrument. The codebook's
SHA-256 is written into the prompt header and the manifest. **Rendering does not update
`pins.json`** — that needs `--update-pins`, and an ordinary render fails loudly when the rendered
bytes disagree with the existing pins.

**Why the pin split matters.** Without it a renderer change would redefine its own approved
baseline and immediately certify itself. `pins.json` already states the principle: *"A pin with a
digest but no loadable artifact is refused, not passed — filling in a hash must never be enough to
turn the suite green."* Silent pin updates are the same hole from the other side.

**What gets pinned:** the exact `tool_config.json` as sent to Bedrock — tool name, description,
input schema and the forced `toolChoice` — not an inner schema fragment that each module then wraps
independently. The obsolete `tool_schema` and `rendered_prompt` pin entries are removed.

**Wired, 2026-09-02.** `preflight.py` now loads exactly these pinned artifacts and assembles every
probe through `build_request()`, so its verdict is about this configuration and no other. An earlier
draft of this section claimed that before it was true; the claim is recorded here rather than
deleted, because it is the one failure mode this whole module is built against — documentation that
describes an intention as a fact.

**What is still owed:** one **credentialed live run**. All five offline assertions pass — the
instrument loads, both artifact pins match their files byte for byte, both were rendered from the
pinned codebook, and the request the builder selects carries them — but nothing is yet known about
caching, forced tool use or `stopReason` against these bytes. Until an artifact in `../runs/` says
otherwise, §A.10's live claims rest on the **stand-in** measurements of 2026-08-31.

**And the pin check reads the file, not just the render.** The first implementation compared the
pin's digest against freshly rendered bytes and ignored the recorded path entirely, so a missing,
moved or substituted artifact still reported `match`. It now resolves the recorded path, requires it
to be the expected one, reads it, and hashes those bytes. An ordinary run also renders **in memory**
and writes nothing: writing before comparing destroyed the very bytes a pin referred to whenever
verification failed.

---

## D11 — Archived artifacts

**Decision.** The prompt and the tool config are written to disk as versioned files rather than
assembled in memory at call time.

**Why, cited — and the citation is corrected.** An earlier draft of this document attributed an
"eight-step checklist whose last step is archive code, prompts and data" to P024, the GUIDE-LLM
checklist. **That was the wrong Feuerriegel paper.** Box 3 with that checklist is in P063, the
chair's methods review; its eighth step is *archive code, prompts and data*.

> P063 · `feuerriegel2025nlptextdata` · Feuerriegel, S. et al. (2025). *Using natural language
> processing to analyse text data in behavioural science.* ·
> [wiki](../../knowledge/wiki/sources/P063-feuerriegel-2025-nlp-text-data-behavioural-science.md)

P024 is still the governing document for *what* must be reported — exact prompts recoverable, code
shared — which is the requirement these artifacts satisfy. It is cited for that, not for Box 3.

> P024 · `feuerriegel2026guidellm` · Feuerriegel, S. et al. (2026). *A reporting checklist for large
> language models in behavioural research.* ·
> [wiki](../../knowledge/wiki/sources/P024-feuerriegel-2026-guide-llm-checklist.md) ·
> [PDF](../../knowledge/raw/literature-research/P024_Feuerriegel-2026_GUIDE-LLM-reporting-checklist.pdf)

GUIDE-LLM items this settles: **B.4** (tool calling / structured output), **C.1** and **C.2** (the
task and system prompts, now recoverable artifacts rather than intentions).

---

## D12 — Two outcomes, and per-technology no-frame

**Decision — author's own, and it comes out of A1.** The schema carries
`outcome: off_topic | technologies_present`, one relation per technology the paragraph makes a
statement about, and an **empty `elements` list as the per-technology no-frame state**.

**Why.** A1 proved that *"no study technology is present"* and *"a technology is named and nothing
frames it"* are different reported quantities: 6 of 35 paragraphs named no study technology, the
annotator first recorded them as `no_frame_present`, and separating the two is what stopped the
reported no-frame share from measuring the sampling frame instead of the corpus. An empty relation
list alone cannot tell them apart, so the outcome has to say which case it is.

**Revised after review, and the revision made it smaller.** The first version had three outcomes —
`off_topic | no_frame_present | relations_present` — plus a separate `technologies_present` array.
That left a real ambiguity the review caught: in a paragraph where one technology is framed and
another is merely named, does the unframed one get a relation? Either answer silently moves the
paragraph × technology denominator, which is the same class of defect as the A1 conflation.

The resolution mirrors the human instrument instead of inventing a parallel one. Sheet 1 ticks the
technologies; sheet 2 has one row per technology with `no_frame_for_this_tech`. So: one relation per
stated technology, empty `elements` means nothing framed it, and paragraph-level "nothing framed at
all" is **derived** rather than asserted a second time. Two states, no side array, and the A1
distinction preserved.

**Enforced, not merely listed.** JSON Schema cannot express these, so `validate_response()` in
`instrument.py` does. `preflight.py` already runs it on **both** production responses; M4 must call
it on every response before anything is written — a requirement on a component that does not exist
yet, not a description of one that does:

```
off_topic              → no relations
technologies_present   → at least one relation
                       → one relation per technology, never repeated
                       → exactly one evaluation per fired frame, none for an unfired frame
                       → a note where a note-requiring element fires
                       → a comparison_note where comparison_target is other or multiple
                       → a subtype field only on the technology that owns it
                       → the OWNING subtype field required on wind and hydrogen
                       → comparison_target `none` where nothing fired
```

The last two were added after review. The subtype is required rather than optional because the
instrument says to *record* it and the enum offers `unspecified` for the case the text does not
say — so an absent field is a missing value, not an inapplicable one. And a comparison target
attaches to an evaluation, which attaches to a fired frame, so a relation that frames nothing
cannot compare against anything.

An earlier draft listed those invariants in the manifest and enforced none of them, which is a
contract in name only.

## D13 — The exclusion rules are projected, not rendered verbatim

**Decision.** Two of the eleven X-rules instruct the coder to fill fields the model is not asked
for. They are **transformed**, and every transformation is recorded in a map that names the source
path, the reason, the exact replacement text and the correction that authorises it.

**Why.** The alternative was to render all eleven verbatim and add a disclaimer saying that
`speaker` and `confidence` are not requested. That is worse: it hands the model two mutually
incompatible instructions and relies on it inferring which has priority. Four further rules carry
calibration prevalence inside otherwise operative prose, and three add nothing the definitions do
not already say.

| Rule | Treatment | Reason |
|---|---|---|
| X1, X2, X5, X6 | include as written | pure boundary rules |
| X3 | **transform** | instructs `set speaker`; the first sentence is load-bearing and must survive |
| X4 | **transform** | instructs `set confidence: 1`; the paragraph-is-the-unit rule must survive |
| X7 | **transform** | keep the per-technology rule, drop the 25–69-point literature figure and the 41 % corpus prevalence |
| X8 | exclude | spreadsheet-entry hygiene ("columns take 0 or 1"); JSON Schema enforces types |
| X9 | exclude | its own first sentence says it is a measured cost, *"not as a rule change"*. X4 and X11 already carry the operative rules; what remains is a calibration paragraph, a 5 % prevalence figure and a limitations conclusion |
| X10 | exclude | F2c's `fires_when` and `does_not_fire_when` already carry the operative rule with its examples; the X10 block is mostly superseded-decision narrative and prevalence |
| X11 | **transform** | keep the operative rule — code only technologies the paragraph states — drop the A0 anecdote |

This is why the tests cannot simply assert that every normative source scalar appears verbatim in
the prompt. They assert instead that every source path is *classified*, and that every transformed
field has an exact recorded mapping.

## D14 — One request builder, consumed by preflight and — required — by M4

**Decision — author's own.** `load_artifacts()` and `build_request()` live in `instrument.py`.
`preflight.py` calls them and no longer assembles a request of its own; **M4 must call them too, and
must not build a request any other way.** M4 does not exist yet, so that half is a constraint on
what may be written, not a fact about what has been. Stated this way deliberately: a design document
that describes an intention in the present tense is how "preflight certifies the production request"
came to be written here two days before it was true.

**Why.** This is the point of the whole rewiring, and it is logic rather than literature: a
preflight that builds its own request certifies a *different object* than the runner sends, so a
green preflight would be a green light for nothing. §A.10 already says preflight becomes a true gate
only when the runner calls it with the runner's own resolved configuration; one shared construction
function is what makes that possible instead of aspirational.

It also removed three duplicated constants. `REGION`, `CACHE_TTL` and `MIN_CACHE_PREFIX` now have
one definition in the module that owns the request. Two copies of a constant that has to agree
between the certifier and the runner is the same defect one level down.

**Loaded, not re-rendered.** `load_artifacts()` reads the pinned files and refuses any artifact
whose bytes do not match its pin. Re-rendering inside preflight would test the renderer a second
time and leave the pinned bytes — the ones a reported run actually uses — unverified.

---

## D15 — The cache check accepts a warm start; the nonce is gone

**Decision — author's own, and it made the request smaller.** The cache probe sends the exact
production prefix, with no run tag and no filler, and accepts either initial state:

```
cold start   call 1 writes N   call 2 reads N
warm start   call 1 reads  N   call 2 reads N
ambiguous    call 1 reports both a write and a read  ->  FAIL
```

**Why the nonce had to go.** Preflight used to prepend a unique run tag so that call 1 was always a
cache *write*, because a rerun inside a live TTL would otherwise see a read and the check would fail
spuriously. That reasoning was sound for a **stand-in** prefix, whose freshness was the thing being
tested. Against a **pinned production** prefix it is not: a warm first read is not noise, it is
evidence that this exact prefix already has a live cache entry. Keeping the nonce would have cost
two things that matter more than the simpler assertion — the certified request would differ from the
production request by construction, and the nonce's own tokens would land inside the very
measurement check 6 exists to take.

**An ambiguous start fails rather than being interpreted.** A call reporting a write and a read at
once is explained by neither state, so the cache accounting cannot be read and nothing computed from
it should be trusted. The run record carries `cache_initial_state` per model so a later reader does
not have to reconstruct it from the check text.

---

## D16 — Each model against its own cache minimum

**Decision — author's own; it corrects a stricter rule than the API imposes.** Check 6 compares the
measured prefix against `MIN_CACHE_PREFIX[model]` — Sonnet 1024, Opus 4096 — and reports the margin.
It no longer requires both models to clear 4096.

**Why.** The old rule took `max()` of the two minima on the reasoning that one checkpoint should
serve both models so arm 4's cost comparison stays comparable. That conflates two claims. The
operative one is that the configured checkpoint clears *each model's applicable* minimum; requiring
Sonnet to report at least Opus's threshold is stricter than the API requires, and tokenization
differs between models, so the same prefix does not produce the same count. Opus remains the binding
case, which is the useful thing to know.

Deliberately **not** built: separate tools-only and system-only token probes. They would measure
altered requests, add calls, and protect no claim the thesis makes. Reporting
`cached_prefix_tokens`, `model_minimum` and `margin` makes the headroom visible without inventing
two requests nobody sends.

---

> **D15–D19 are engineering notes, not thesis-facing instrument decisions.** They record why the
> *certifier* is built the way it is. None of them changes what the model is asked or how a label is
> produced, so none belongs in the design chapter — the plan-review's judgement, and it is right:
> the review invested more in the gate than in the instrument. Condense these five to a paragraph
> when Chapter 4 is written, and keep the argument here for whoever next touches the code.

## D19 — The model IDs live beside the request builder, not in a pinned run config

**Decision — author's own.** `REGION`, `MODELS`, `CACHE_TTL` and `MIN_CACHE_PREFIX` are constants in
[`instrument.py`](instrument.py), imported by the certifier. The selected model travels inside the
request envelope that check 13b digests, so a changed model is a changed request.

**What this replaced.** The IDs used to sit only in `preflight.MODELS`. That is a real gap and it is
worth naming precisely: the runner could import `build_request()`, send the pinned prompt and the
pinned schema to a *different* model, and still say a preflight covered its run. The prompt was
shared; the thing being measured was not.

**Why not a pinned run configuration, which the reviewer proposed.** Because a pin exists to detect
a file drifting away from an approved copy of itself, and there is no file here to drift: the model
IDs are two string constants in the same module as the builder that uses them. Adding
`run_config.json` plus a pin plus a pin check would create a second place for the same fact to live
and a fourth artifact to keep synchronised — the very failure the projection is built to prevent,
imported into the layer above it. A constant beside its only consumer needs no digest; what it needs
is to be *inside the certified request*, which is what the envelope change achieves.

**What this does not solve, and the honest limit.** Nothing stops M4 from writing
`client.converse(modelId="something-else", **build_request(...))`. No pin would stop that either —
only M4 calling `instrument.MODELS` will, and D14 states that as a requirement on code that does not
exist yet. The run record names the resolved model IDs, so the discrepancy would at least be
*visible* in the artifact rather than absent from it.

---

## D17 — An offline mode, so missing credentials cannot look like a failed run

**Engineering hygiene, not an instrument decision.** Recorded here for the code's sake; it does not
belong in the thesis's design chapter and needs no literature.

`--offline` runs only the file checks, makes no API call, and records `certified: false` with
`NOT_CERTIFIED_LIVE_CHECKS_NOT_RUN`. Exit 3 = assertions ran, certification impossible; exit 2 stays
for a live run that reached for the API and could not get there. The distinction is worth the flag
because a missing precondition and a broken contract are different findings, and the environment
this was written in has no credentials.

What may be claimed offline is stated in the report itself: the pins match their files, the codebook
is the frozen one, the tool config is well formed, and the request builder **selects** those exact
artifacts — not that anything was sent. Nothing about model behaviour or caching.

One correction worth keeping: the first version suppressed failed assertions under `--offline`, and
did hide two real pin failures until the exit code was read. A failed assertion now exits non-zero
in every mode.

---

## D18 — Validate against the pinned schema, then the relational contract

**Decision.** `validate_tool_use()` validates the returned `toolUse.input` with `jsonschema` against
the **pinned** schema, then runs `instrument.validate_response()` for the relational rules.

**Why.** It used to walk the schema by hand, with the comment *"jsonschema is not installed and the
brief forbids installing packages without asking"*. Both halves are now obsolete: `jsonschema` is a
declared, locked dependency, and the schema is the pinned production one rather than a stand-in. The
hand-walk also could not reach nested objects, which is exactly where the production schema keeps
the relations — so it would have validated the outer envelope of a response and declared the whole
thing valid.

Two layers because the schema cannot express the rest, and the second layer is the same function M4
is required to run on every response. One contract, one implementation — and, once M4 exists, two
callers.

---

## What the tests must assert

Coverage, not spot checks. A test that only asks "does every line of the included sections appear"
cannot notice that a whole substantive section was excluded by mistake.

1. every source path is classified as `prompt`, `schema`, `both`, `provenance`, `human_only`,
   `redundant` or `transformed` — an unclassified path is a hard failure;
2. every **untransformed** normative scalar appears in its assigned artifact;
3. every **transformed** field has an exact source-path → rendered-text mapping;
4. **mutation:** changing a normative field changes the artifact;
5. **mutation:** changing an excluded field leaves both artifacts byte-identical;
6. enum values *and their order* match the source exactly;
7. re-rendering is byte-identical;
8. loaded artifact bytes equal the pinned bytes, or the command fails;
9. the tool config accepts fixture outputs for all five states — empty, off-topic, no-frame,
   single-technology, multi-technology;
10. contradictory outcome states, missing conditional notes and duplicate technology relations are
    rejected.

Mutation tests replace searching the prompt for "distinctive" excluded strings: a string search
collides with legitimate text, and it cannot notice that a whole substantive section was excluded
by mistake.

---

## What was cut, and why

Recorded because the chapter should say what was considered and rejected, not only what was built.

| Cut | Why |
|---|---|
| A `reasoning` field, first in the schema | P022 uses reasoning-before-score, but it is absent from the frozen *human* output contract, JSON property order is not a generation guarantee, and a generated rationale is a post-hoc justification rather than an independent audit trail. Raw responses and the instrument's own required notes already give inspectable evidence. Cost on every call, no committed analysis. |
| A reverse-order ablation artifact | P022 wraps label lists in `<shuffle>` tags to neutralise order effects, and the plan never mentions order effects — a real gap. But reversing every list at once cannot identify which ordering mattered, "forward vs reverse" is not a principled neutralisation, and it was not pre-committed. The planned prompt-perturbation arm is its proper home. Canonical declaration order, documented as the author's choice, and stop. |
| A `--language` switch | See D6. A flag cannot generate faithful translated normative instructions from an English-only codebook. |
| Verbatim YAML slicing instead of a parser | See D9. |
| Rendering all eleven X-rules verbatim | See D13. Two instruct the model to fill fields it is not asked for; rendering them plus a disclaimer would hand the model contradictory instructions. |
| `note` fields, and X8/X9/X10 | See D8 and D13. None carries an instruction the definitions do not already give. |

---

## Open, and not decided here

- **Worked examples.** D1's cited effect comes from long descriptions **and 15 examples**; v1.0
  contains no worked-example block. §A.6 claims humans and models get the same worked examples,
  which is currently not true of either. Do **not** quietly promote calibration paragraphs into
  few-shot examples: that is an instrument revision and needs approving as one, before A2. Either
  amend §A.6 to say v1.0 carries no worked examples, or add them deliberately. This is the single
  most likely improvement to extraction quality.
- **The human-facing artifact** that makes this M5 rather than M5a.
- **Page numbers**, per the note at the top.

---

## v1.1 — the post-registration development variants of W3 (added 2026-10-14)

_Authority: the W3 block of the pre-analysis specification §4 (declared 2026-10-14) and the lever analysis
`knowledge/wiki/syntheses/pipeline-robustness-levers.md`. Module `instrument_v11.py`, pins `pins_v1.1S.json` /
`pins_v1.1E.json`, artefacts `instrument/*_v1.1S.*` and `*_v1.1E.*`. The decisions D1–D19 above stand; v1.1 imports
v1.0 and changes only what is listed here. Development status: whether a variant is used for any further analysis is
decided by the sealed W3 selector on the development split; the registered v1.0 remains the reported instrument._

### D20 — Strict decoding on the tool

**Decision.** `strict: true` on the tool specification (Bedrock structured outputs, documented for Claude Sonnet 4.6 on
Converse, read 2026-10-13), inside the documented JSON-Schema subset (enum, const, anyOf, `$ref` to `$defs`,
`additionalProperties: false`, `minItems` 0 or 1; no `oneOf`, `uniqueItems`, `if/then`).

**Why, cited — a transfer hypothesis, not a demonstration.** Format restrictions degrade reasoning tasks and were
neutral-to-helpful on single-label classification; where a loss occurs it is not a parsing artefact
(P089 Tam et al. 2024, `tam2024speakfreely`, pp. 1, 4–5). The pipeline's task is a relational multi-label
extraction, so the finding supports *trying* strict decoding without a documented accuracy cost, not an accuracy
claim. The validation of D18 stays: the provider documents refusal and truncation exceptions.

**What was rejected.** A per-technology `anyOf` of relation variants (which would make subtype misplacement impossible
by construction): rejected by the provider on 2026-10-13 ("compiled grammar is too large"), twice. Subtype placement
stays a relational rule.

### D21 — Nested frames, one evaluation per fired frame, a normaliser

**Decision.** A relation carries `frames: [{frame, elements, evaluation}]` instead of `elements` plus a parallel
`evaluations` map; an empty `frames` list is the registered no-frame state (D12 unchanged). A deterministic normaliser
maps the nested record to the v1.0 row shape, so `public_payload`, `derive_rows` and the evaluator consume one shape;
the suite asserts that a valid nested record maps losslessly into the v1.0 schema and relational contract.

**Why, author's own.** One of the four rule families observed in the registered run's 29 contract-invalid responses
(an evaluation without a fired frame, four flags) is a pairing error; its converse (a fired frame without an
evaluation) was not observed and is prevented proactively. Nesting is the encoding chosen for the pairing within
the provider's documented strict subset (which has no `if/then` or `dependentRequired`); the gap was documented in
`validate_response`'s docstring before the production run.

### D22 — Retry policy v3

**Decision.** The one parse retry that follows a *returned* response classified `schema_invalid` or
`contract_invalid` is a three-turn request: the paragraph, the model's own call, the validator's messages as an error
tool result. Every other parse-failure code keeps the identical retry of v2; refusals are never retried; the transport
transitions and the one-retry allowance are v2's; a recovery episode starts over and carries no earlier invalid call.
The feedback text and the prior call are journalled privately (they can quote article text); the public journal
carries rule identifiers and `retry_kind`.

**Why, cited.** Self-correction is effective with reliable external feedback and undemonstrated with prompted
self-feedback (P091 Kamoi et al. 2024, `kamoi2024selfcorrect`, p. 1, §7 p. 10); against a sound verifier, re-prompting
helps and the value of the feedback's content is domain-dependent (P090 Stechly et al. 2024,
`stechly2024selfverification`, Table 1 p. 7). The validator is a sound verifier of the output contract and only of
that. Equal retry allowances are not equal compute (P091 Table 7, p. 11, asks for comparable cost); observed tokens
and calls are reported.

### D23 — One labelled demonstration and two field-name replacements

**Decision.** The "Output contract" block is rewritten for the nested shape and shows one filled-in record for an
invented paragraph; the two v1.0 sentences that named the `elements` field for the no-frame state say `frames`
(`FIELD_REFERENCE_REPLACEMENTS`, asserted by the suite); apart from the version banner line in the first paragraph, no other byte of the v1.0 prompt moves.

**Why, author's own, and what it is not.** The demonstration is a worked example (F2a positive, F7b negative, F5a
negative, `wind_siting` offshore; the unframed technology mirrors the codebook's own X1 illustration), audited
against the frozen rules; it supplies supervision, not only form, and it was written after the evaluation results were
seen — the W3 block discloses this rather than claiming independence. A full worked-examples block (named "the single
most likely improvement" above, under *Open*) was **not** added: the codebook's own text carries quoted illustrations
for five of seventeen elements, so a block would be new content after inspection; prompt refinement from error review
yields small gains on average (P092 Pangakis & Wolken 2025, `pangakis2025humansintheloop`, Figure 3 p. 6); and
paraphrase-level prompt choices move results without improving validity (P093 Baumann et al. 2025,
`baumann2025llmhacking`, p. 4).

### D24 — Variant E: a required explanation before the labels (reopens a rejected choice)

**Decision.** Variant E adds a required, first `explanation` string (one to three sentences). This reopens the
rejection of a rationale field recorded under *What was cut* (asymmetry with the human output, cost, rationales not
being audit evidence); the reopening is disclosed, the field is treated as a generation intervention, and the
explanation never reaches a public file.

**Why, cited — mixed.** A rationale raised mean κ from 0.59 to 0.68 on paragraph-level codebook coding with GPT-4
(P094 Dunivin 2024, `dunivin2024scalablecoding`, p. 2); the explanation factor is model- and task-dependent and lowered
GPT-3.5's news-frame accuracy (P095 Atreja et al. 2025, `atreja2025whatsinaprompt`, p. 7). Provider documentation:
forced tool choice disables extended thinking; a property asking for thinking or step-by-step reasoning may trigger a
refusal, a short explanation is the documented alternative; required properties are generated first (which decides
that the text precedes the labels; P089 p. 4 shows that key order changed what a model wrote). Hence a candidate,
chosen over variant S only by the sealed selector's rule.

### D25 — Not changed, and why

The codebook and every definition, boundary, exclusion rule and evaluation rule; the model; temperature 0;
`max_tokens` 1,024; k = 3; the cache point; the envelope shape of the first request (D14's check-13 clauses hold for
v1.1). Decomposition (recognise, then classify) is the most promising accuracy lever in the literature
(P094 p. 2; P097 Hohenwalde et al. 2025, `hohenwalde2025chatgptactors`, p. 1; P096 Ma et al. 2025, `ma2025multilabel`,
p. 5 as a candidate mechanism) but changes the architecture and the cost model (D2), so it belongs to a later study on
a fresh sample. Majority voting over executions evaluates a different pipeline (specification §4) and is excluded.
The instruction language stays English (D6) as a fixed choice under mixed evidence (P098 Enomoto et al. 2025,
`enomoto2025fairtranslationese`, pp. 1, 4; P097 did not ablate it).

### D26 — v1.2S, the W4 candidate (added 2026-10-14, after the W3 development result)

**Decision.** One further variant (`instrument_v11.py` variant T, displayed as v1.2S): per-frame element enums (an
element can only be emitted under its own frame — the `element_frame_mismatch` rule becomes impossible by construction),
one required `technology_subtype` on every relation with `none` for non-applicable technologies and the registered
`unspecified` for applicable-but-unstated ones (incompatible values rejected, public id `subtype_incompatible`), and
field names whose alphabetical order is the reading order (`technology`, `technology_frames`, `technology_subtype`,
`technology_versus`; `frame`, `frame_elements`, `frame_evaluation`; `z_*` notes); the demonstration keeps D23's
paragraph and labels in that order; five enumerated field-name replacements outside the Output contract.

**Why, author's own, from development evidence.** On A1 v1.1S listed an element under a neighbouring frame in eleven
invalid responses and misplaced or omitted the subtype in nineteen; the keys reached the client in alphabetical order
while the demonstration showed another order. Whether the model generated them in that order is a hypothesis (the W4
block says so); the representation change removes two of the three failure families by construction regardless.
Authority and rule: the W4 block of the specification (§4a), A1-only.
