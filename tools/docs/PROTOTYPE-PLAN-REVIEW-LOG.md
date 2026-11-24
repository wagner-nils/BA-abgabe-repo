# Plan review log — the extraction pipeline prototype


> **Append-only review transcript.** Historical by nature: it records what was argued and when.
> Nothing here is a current-state authority.

Append-only transcript of the adversarial review of [`PROTOTYPE-PLAN.md`](PROTOTYPE-PLAN.md).
Phases 0–1 of claudex-loop (recon + interrogation) are complete; the plan is locked with the
author. `MAX_ROUNDS = 5`. Reviewer: OpenAI Codex, `codex-cli 0.151.0`, model = CLI default
(`~/.codex/config.toml` carries no `model` pin), read-only sandbox on every round.

**Editorial context on the author's contribution (added 2026-11-24).** This transcript records the assistants' review
work; the author's contribution is documented separately. In his retrospective account, the
author describes reading and assessing relevant literature himself and contributing a substantial
share of the design proposals. He and Claude developed the architecture jointly, with his
methodological priorities and requirements shaping the design. He made the decisions explicitly
attributed to him below and required the manual-annotation track. In the completed thesis,
Section 5.3 records him as the sole human coder. Some paragraphs were initially written entirely
by the author; Claude later reworked those paragraphs in full. The author's statement in the
overview of aids describes his own draft material and his direct proofreading of every adopted
passage.

---

## Phase 0 — recon (Claude, 2026-08-29)

Read: the brief, the exposé, `thesis-scope-decision`, `discourse-frames`,
`technology-categories`, `corpus-construction`, `annotation-and-validation`,
`reporting-and-reproducibility`, `thesis-formal-requirements`, the 2026-08-04 coherence review,
the 2026-07-30 meeting note, the correspondence timeline, the pilot-corpus README, manifest and
`.gitignore`, `GUIDE-LLM.tex`, `llm-usage-log.md`, and the chapter skeletons.

Then verified rather than assumed, against AWS account `794593359112`, `eu-central-1`:

- Callable models and their exact inference-profile IDs (live `Converse` calls).
- Service quotas, including the non-adjustable per-day token caps.
- Which generation parameters the API accepts (`temperature`, `top_p`, thinking, `seed`).
- Run-to-run variance at `temperature: 0`, 5 repeats per model.
- The structured-output route (forced tool use), prompt caching via `cachePoint`,
  `cachePoint` inside `toolConfig`, `CountTokens` support, batch-inference reachability.

And measured rather than assumed, against the pilot corpus: 340 paragraphs, 204 naming a
technology, 84 naming more than one.

Six corrections to existing planning documents resulted; they are §1–6 of the plan and were logged
to `knowledge/raw/methodology/llm-usage-log.md` in the same turn.

## Phase 1 — interrogation (Claude ↔ Nils, 2026-08-29)

Six load-bearing decisions resolved with the author. All six went to the recommended option:

| | Decision | Chosen |
|---|---|---|
| — | Chapter 4's subject | The pipeline |
| — | Production extractor | Sonnet 4.6 |
| B1 | Evaluation-set source | Hybrid, pilot corpus as guaranteed floor |
| B2 | Opus 4.6's role | Capability ablation on identical items + model–model agreement as a descriptive. Second-coder substitution and disagreement adjudication were offered and **declined** |
| B3 | Decoding | `temperature: 0`, thinking off, plus a thinking ablation on the multi-technology subset |
| B4 | RQ scope | RQ1 + RQ2 full, RQ3 short, RQ4 conditional on the corpus |
| B5 | Frame coding | Binary elements, 2 per frame |
| B6 | Evaluation perspective | What the paragraph asserts; speaker recorded separately |

The author additionally required a manual-annotation track with its own folder, protocol and
schedule — delivered as [`annotation/README.md`](annotation/README.md).

Research gate: **web**. One workflow run (4 parallel finders → adversarial verification of every
numeric claim → synthesis) covering Bedrock pricing, batch-inference mechanics, prompt-caching
rules, and academic conventions for a paired two-model annotator comparison.

---

## Round 1 — Codex

Reviewer: `codex-cli 0.151.0`, model = CLI default, `-s read-only`.
Thread `01a04dcb-1ad1-74a3-b6a0-914b4d918fd6`. 22 findings. **VERDICT: REVISE.**

Round 1's verbatim critique was not preserved (it lived in `scratchpad/`, which is gitignored, and
the convention of reproducing it here was only adopted at round 2). Summarised by severity, with
Claude's disposition as final arbiter:

### Accepted and already fixed in this round

| # | Finding | Fix applied |
|---|---|---|
| 2 | Dev/eval separation was not mechanical at article level — paragraph-level splitting lets the same article, event and outlet vocabulary enter both blocks | `build_sheets.py` now splits **by article**, never by paragraph, and records the assignment in `split-assignment.json` |
| 9 | **The codebook could not identify technology-specific relations.** Sheet 1 recorded frame elements once per paragraph while Sheet 2 assigned frames to particular technologies; in a multi-technology paragraph nothing said whether F1a belonged to solar or wind. The worked examples silently supplied a judgement the instrument did not capture | Frame elements moved to **(paragraph × technology)**. Sheet 1 now identifies technologies only; Sheet 2 carries the elements and per-frame evaluation, generated by `expand_sheet2.py` |

Finding 9 is the most valuable thing in the review. It is a genuine hole in the instrument, it was
invisible in the prose, and it would have produced an uninterpretable gold standard.

### Accepted, fix pending

| # | Finding | Disposition |
|---|---|---|
| 1 | Fatal contradiction: the pilot was declared *permanently* developmental, yet the no-Factiva thesis drew its 120-item evaluation set from the same corpus | Correct. Resolved in the sheet builder (9 articles reserved, untouched) but the plan text still asserts both. §A.5 and §B.8 to be rewritten |
| 7 | "Exact-match accuracy" is undefined for variable-size relation sets | Correct and blocking. Requires a canonical relation-set normalisation before A2 |
| 8 | Intra-coder agreement was called an "upper bound" on the human ceiling. Not mathematically justified — test–retest may be higher or lower | Accepted in full. Rename to test–retest consistency, drop every "ceiling"/"upper bound" claim, state that inter-coder reliability and construct validity remain unmeasured |
| 16 | Token arithmetic internally inconsistent: 204 calls × a 4,096-token prefix exceeds the claimed 574,000-token "full pass", and arm 4 counts cached prefixes against quota after the plan itself establishes that cache reads do not burn quota | Correct. Superseded anyway by the corpus recount; billable and quota-burn columns to be separated |
| 4 | The paired comparison has no defined estimand across three repeats — first run, majority vote, or pooled? 120 × 3 are not 360 independent pairs | Correct. Pre-specify majority vote with deterministic tie-breaking; repeats analysed separately as stability data |
| 5 | The bootstrap ignores article clustering and the stratified design | Correct. Paired **cluster** bootstrap at article level, survey-weighted |
| 11, 12, 13 | Several frame elements are topics rather than falsifiable frames (F2b fires on almost any industrial news, F3b on generic transition language, F5b on routine permitting, F6 unconstrained); the security recall rule waives textual evidence rather than only causal explanation; `no_frame_present` collides with the broad elements | Accepted. F4b to require an explicit security/dependence/supply/geopolitical predicate tied to the technology — waive the *mechanism*, not the *evidence*. Element prevalence and co-occurrence to be reported after A0 before the codebook freezes. This is what A0 is for |
| 14, 15 | The language arm conflates model competence with translation quality; the translation arm cannot reuse native gold labels, since translation can alter frame content | Accepted. Confirmatory claims limited to de/en; translated items need their own adjudication |
| 20 | Load-bearing API claims need executable contract tests, not prose. Cache failure in particular corrupts cost and scheduling while inference still succeeds | Accepted. A preflight suite asserting model IDs, forced-tool output, cache write/read counters, TTL, usage accounting and stop reasons, aborting on mismatch |

### Accepted as the author's decision, not the agent's

| # | Finding | Why it is escalated |
|---|---|---|
| 19 | The eight-week build is over-scoped. Codex would cut GDELT, batch inference, the Haiku filter, the thinking ablation, extra languages and speaker analysis | A scope decision with supervisor implications |
| 22 | **The strongest objection.** Comparing two Claude models establishes within-vendor capability, not that the LLM approach beats a cheaper baseline. The repository's own literature (P081) says the defensible response is an encoder baseline | Either add a minimal multilingual baseline or narrow the contribution claim explicitly. Changes what the thesis claims |
| 18 | The annotation budget is optimistic; Codex estimates 30–40 hours against the plan's 17–20 | Partly superseded — the corpus is smaller than the budget assumed — but the per-paragraph estimate does not yet include re-annotation after codebook changes |
| 21 | Provisional decisions (storage as a seventh category, the tie-break) are embedded as though settled | Deliberate and argued in the plan: coding `storage` now and collapsing later is free, splitting later is not. Escalated rather than reversed |

### Rejected, with reasons

| # | Finding | Why |
|---|---|---|
| 3 | "A5 does not repair evaluation leakage; replace it with a pre-registered holdout" | Partly right, but A5 is not claimed to repair leakage. It is P056's own recommendation and answers a different question — whether metrics degrade on material drawn after the full corpus run. The reserved block already is the pre-registered holdout Codex asks for. A5 stays, its purpose stated more narrowly |
| 6 | "Make the comparison exploratory now, or run a blinded sample-size calculation" | Agreed in substance, but the plan already schedules the discordance measurement on the development set at M-2 and forbids choosing the framing after seeing the result. That is the blinded calculation, using dev rather than simulation, which is more informative on a corpus this small |
| 10 | "One file, identical instructions overstates comparability" | Accepted as wording, rejected as a design change. Generating parallel staged instructions for the model would make the pipeline less like the thing being validated. The claim is reworded to *harmonised*, not *identical* |

### Claude's own correction, found while building the sheets

Independently of Codex: **the corpus is 198 paragraphs across 14 articles, not 340 across 35.**
Twenty-one article files carry no body prose — for nineteen of them the outlet's terms bar
reproducing it — and the naive segmentation was counting keyword lists and collector notes as
article paragraphs. Every affected figure in the plan is corrected in §4. This is the same class of
defect Codex found in the instrument, arrived at from the other direction, and it argues for
generating artefacts early: the sheet builder caught what prose review did not.

**Round 1 outcome: REVISE accepted.** Two fixes applied, ten accepted for the next revision, four
escalated to the author, three rejected with reasons.

---

## Revision 1 — 2026-08-30, after calibration

Applied to `PROTOTYPE-PLAN.md` and to the codebook. Findings closed:

| # | Was | Now |
|---|---|---|
| 1 | Plan asserted both "the pilot is permanently developmental" and "the no-Factiva evaluation set comes from the pilot" | §A.5 rewritten. Article-level partition, written once, enforced by `split-assignment.json`. Reserve is 9 articles / 70 candidate paragraphs and it exists. The A0b exception is stated with the narrow ground it stands on |
| 4 | No defined estimand across three repeats | Primary prediction = majority vote over the three runs, ties to the lowest run index. Repeats analysed separately as stability data, never pooled |
| 5 | Bootstrap ignored article clustering | Paired **cluster** bootstrap resampling *articles*. With 14 clusters the precision ceiling is itself reportable |
| 7 | "Exact-match accuracy" undefined for variable-size relation sets | Scoring unit is the `(paragraph × technology)` row matched by `row_id`; missing and spurious rows both count as incorrect; per-field micro/macro reported alongside |
| 8 | Intra-coder called an "upper bound" on the inter-coder ceiling | Rejected as unjustified. Renamed test–retest consistency; every "ceiling"/"upper bound" claim removed; inter-coder reliability and construct validity stated as unmeasured |
| 11, 12, 13 | Elements possibly too broad; `no_frame_present` colliding with them | **Closed empirically, which is what the finding asked for.** Prevalence and co-occurrence computed on 64 rows before freezing. F2b did fire on 38 % and was split into F2b + F2c. `no_frame_present` rule rewritten in v0.2 after two calibration rows misused it |

Still open from round 1: **#14/#15** (language and translation arms not identified), **#16**
(billable vs quota-burn token columns), **#20** (preflight contract tests), and the four escalated
to the author — **#18** annotation budget, **#19** scope cuts, **#21** provisional decisions,
**#22** the encoder-baseline objection.

Codex round 2 should run once #14, #15, #16 and #20 are addressed, so that the reviewer sees a
plan that has answered its own round-1 findings rather than re-reporting them.

---

## Revision 2 — 2026-08-31, closing the four remaining round-1 findings

Applied to `PROTOTYPE-PLAN.md`, `annotation/README.md`, `annotation/finalize.py` and the new
`pipeline/preflight.py`. This clears every round-1 finding that was Claude's to close; what remains
open is the four escalated to the author.

| # | Was | Now |
|---|---|---|
| 14 | The language arm conflated model competence with translation quality — a label change under language L could be the model or the translation, and nothing distinguished them | Confirmatory claims **limited to de/en**, the two languages the author reads, with every confirmatory translation read and passed by the author before extraction. Further languages are explicitly **exploratory** and carry the confound as an unmeasured term. Arm 2 remains the baseline with the decision rule fixed in advance: a language effect is claimed only above the prompt-perturbation change rate on the same items |
| 15 | The translation arm was costed at "zero extra — reuses the gold standard". It cannot: translation moves frame content, so the model was to be scored against labels drawn from a text it never saw | New session **A6** — 40 of A2's items translated to English and **re-annotated on the translated text**, ~1.5 h. Yields two gold standards over the same items and three numbers: model-on-native vs native gold, model-on-translated vs translated gold, and **native gold vs translated gold** — the term that was previously invisible and which bounds the other two. Codex's finding #18 (optimistic annotation budget) is therefore **partly correct by construction**: closing #15 honestly adds 1.5 h |
| 16 | Token arithmetic internally inconsistent — cached prefixes counted against quota after §B.6 established that cache reads do not burn quota | Two accountings defined and never summed: **billable** = `input + output + cacheRead + cacheWrite`; **quota** = `input + cacheWrite` only. Both are named fields in the run record and both columns appear in Table 6.5. Arm 4 recomputed: ≈ 226,000 quota tokens against a 1,296,000 cap, so it runs **in one day with ~6× headroom** rather than "across two days with almost no margin". Three downstream claims corrected with it — open decision 6 (the `eu.` premium is now purely the 10 % price premium), the arm-4 risk row (Medium → Very low), and the stale 340/204 corpus figures in the estimates table |
| 20 | Load-bearing API claims were prose verified once by hand. The dangerous failures do not raise: a sub-minimum cache checkpoint succeeds and silently does not cache | **`pipeline/preflight.py` — written, run, passing.** 11 assertions × 2 models = 26, specified in the new plan §A.10 and executed live on 2026-08-31. Aborts any reported run on mismatch; result written into the run record. **Six of the eleven fail silently if unasserted**, which is the argument for the file |

### What the preflight suite established on its first run

Reported here because two of these are Chapter 4 material and the third is a correction to this plan.

1. **One cache checkpoint serves both models — measured per model, which is what #20 asked for.** A
   10,110-token prefix: `cacheWriteInputTokens: 10110` on call 1, `cacheReadInputTokens: 10110` on
   call 2, identical on Sonnet 4.6 and Opus 4.6. The 1,024-vs-4,096 minimum-prefix asymmetry is
   real and is cleared by a single prefix above the larger bound.
2. **The two accountings, measured on one `usage` block** (second call, identical on both models):
   `inputTokens 473 · outputTokens 69 · cacheReadInputTokens 10110 · cacheWriteInputTokens 0` →
   billable 10,652, quota 473, a **22.5× divergence**. §B.6 previously argued this from
   documentation.
3. **It corrected the plan on first contact.** Per-call uncached input is 473 tokens, not the ~250
   this plan estimated two days earlier, which moved arm 4's quota burn from 98,000 to ≈ 226,000.
   No amount of prose review would have caught that, which is the finding restated as evidence.

Two smaller factual corrections it forced in §A.4:

- **`CountTokens` fails with `AccessDeniedException`, not `ValidationException`.** The operation is
  not necessarily unsupported by the model — it is not permitted to this credential (a Bedrock
  bearer token). A full IAM credential may be able to call it. The design does not change (accounting
  stays post-hoc) but the stated reason does.
- **`seed` is rejected client-side by botocore** (`ParamValidationError`), i.e. the parameter is
  absent from the Converse API model rather than refused by the service. Stronger than the plan said.

### Not a round-1 finding — a defect found while reconstructing context on 2026-08-31

**An A1 pass-1 sheet had been coded by a model and exported into the human-label slot.** All 35 rows
carried `annotator: GPT-5.6 Sol`, `codebook_version: 0.3` (the frozen instrument is v1.0) and
`sitting: 0`. Nothing was committed and no reported number was touched — A1 is the development set
— but had it been finalised, milestone M-2's "first agreement figures" would have compared
Sonnet 4.6 against GPT-5.6, and the discordance rate that sizes A2 would have been measured against
a model gold standard.

Four mechanical protections added, because the interface will be used for ~15 more hours of coding:

- The file is quarantined in `annotation/_quarantine/` (gitignored) rather than deleted — a model
  pass over the same 35 paragraphs is a usable third comparison axis, but only after the human
  labels exist and are committed.
- `finalize.py` now **refuses** a sheet whose `annotator` names a model (`--annotator-is-a-model`
  stores it deliberately, with a `MODEL` marker in the filename), **refuses** a sheet stamped with a
  codebook version other than the frozen one (`--old-codebook` to override), and **refuses** a
  column carrying values over 400 characters that is not declared as free text — a shape-based
  backstop for the name-based text strip.
- `annotation/README.md` gains a **"The coding interface"** section: suggestion features must be
  absent from the build used for A2, not merely unused; the interface must stamp the frozen codebook
  version; `sitting` starts at 1 and `0` is not a sitting number.
- The frozen `codebook-v1.0.yaml` carried a stale banner reading "CODEBOOK v0.1 — DRAFT FOR
  CALIBRATION … Do not annotate A1 against v0.1", directly contradicting its own `frozen: true`
  metadata at exactly the step where it would mislead. Rewritten.

Also corrected in `annotation/README.md`, all pre-existing drift of the class the 2026-08-04
coherence review named: the "not yet executable" status banner (both the codebook and all three
scripts now exist), the 340/204 corpus figures, A1 sized at 60 paragraphs rather than 35, the
multi-technology share at 41 % rather than 33 % of candidates, and — the substantive one — the
**"upper bound on the human ceiling"** language for intra-coder agreement, which finding #8 rejected
in the plan on 2026-08-30 but which was still standing in the protocol the annotator actually reads.

**Still open, all four escalated to the author:** #18 annotation budget (now partly confirmed —
A6 adds 1.5 h), #19 scope cuts, #21 provisional decisions, **#22 the encoder-baseline objection**.
Round 2 can run: every finding that was the agent's to close is closed.

---

## Round 2 — Codex

Reviewer: `codex-cli 0.151.0`, model = CLI default, `-c sandbox_mode="read-only"`.
Same thread as round 1 (`01a04dcb-1ad1-74a3-b6a0-914b4d918fd6`), so the reviewer judged the
revision against its own prior findings rather than reading the plan cold. **18 findings.
VERDICT: REVISE.** The critique is reproduced verbatim in the appendix below —
`scratchpad/` is gitignored, so this log is the only durable copy.

Its opening sentence was the correct one: *"#14, #15, and especially #20 are only partly closed. A
passing preflight currently does not certify the production pipeline."*

**Two findings caught me making the exact error the suite was built to prevent**, and they are worth
naming before the table:

- **#2 — assertions 7 and 11 were advertised but not implemented.** Check 7 hashed the same
  in-memory function twice and reported PASS, which proves only that two consecutive serialisations
  of a constant match. Check 11 printed "recorded, not yet compared" and then set `ok = True`. Both
  appeared as assertions in the plan's §A.10 table. **An assertion that passes without comparing
  anything is precisely the silent-failure class the suite exists to catch**, and I shipped two of
  them in the file whose docstring says six of eleven failures are silent.
- **#11 — I called native-gold-vs-translated-gold a "bound".** It is a *sum* of translation effect
  and the coder's own inconsistency, and it constrains neither extractor comparison. This is the
  same overreach as round 1's #8, the "upper bound on the human ceiling", made three weeks later in
  a section written to fix that class of error.

### Accepted and fixed in this round

| # | Finding | Fix |
|---|---|---|
| 2 | Checks 7 and 11 reported PASS without comparing against a pin | `pipeline/pins.json` added, carrying the codebook digest. A check with a pin **compares** it and FAILs on mismatch; a check with no pin reports **SKIP** and the suite exits `3 = NOT CERTIFIED`. Passing is no longer the default for an unasserted check |
| 4 | The cache test could fail spuriously on rerun (a stable prefix makes call 1 a READ inside a live TTL), and accepted any positive write and read without requiring equality | Prefix is now cache-busted per (run, model), call 1 is asserted to be a write, and `call1.write == call2.read >= minimum` is required. Measured: write == read == 10,128 (Sonnet) / 10,127 (Opus) |
| 5 | "Schema-valid" checked required keys and enums only — not the tool name, field types, or additional properties | `validate_tool_use()` checks `toolUse.name`, every field's JSON type (rejecting an int where boolean is required), enum membership, and `additionalProperties: false`. `jsonschema` is not installed and the brief forbids installing without asking, so the schema is walked directly rather than delegated |
| 6 | Checks 9 and 10 turned **any** exception into a PASS — a network blip counted as proof that `seed` is unsupported | Expected error codes are enumerated (`EXPECTED_VALIDATION`, `EXPECTED_UNAVAILABLE`, `EXPECTED_CLIENT_SIDE`). An unrecognised failure now FAILs with "an unexpected error is not evidence of the constraint" |
| 7 | `usage` was validated on the second cache call only, and non-integer values passed because negativity was tested only for values already `int` | One `check_usage()` runs on **every** successful response, requiring four fields, each a real `int` (a `bool` is rejected), each non-negative |
| 9 | The plan claimed "26 assertions, 13 per model" — the three global checks were counted once per model — and claimed the suite records raw responses and `access_date` when it recorded neither | Corrected to 3 global + 12 per model = **25 passing, 2 unasserted**. `--raw` captures verbatim responses; `ended_at` added; the resolved config and schema digest are in the JSON output |
| 3 | No 1-hour TTL was requested or asserted, while the quota arithmetic assumed one-hour windows | Production `ttl: "1h"` now sent, plus **check 13**: a bogus `ttl: "30m"` must be rejected with the error naming `cachePoint.ttl`. Separately verified behaviourally — see below |
| 16 | `finalize.py` warned about a missing `sitting` **after** writing the file, and let blank annotators, missing dates, `sitting: 0`, duplicate IDs and out-of-range enums through | Rewritten: `validate()` runs every check and **nothing is written unless the list is empty**. Blank annotator, missing `coded_at`, `sitting` of `0` or empty, duplicate `row_id` or `(paragraph_id × technology)`, `confidence` outside 1–3 and `eval_*` outside the four values are all refusals, each naming the flag that accepts it deliberately |
| 18 | The licensed-text guard was a 400-character length test, and many real pilot paragraphs are shorter than that; `note` was exempt and unbounded | Replaced by an **allowlist**: 60-odd named columns may be written, an unknown column is refused as presumed article text, free-text columns are capped at 600 and everything else at 120 |
| 17 (part) | Model detection was a substring heuristic — a name outside the list, or a blank annotator, entered the human namespace | Explicit **`annotator_type` (human \| model)** column added to both sheet generators and to the A1 pass-1 sheet; `finalize.py` refuses `model` and refuses a blank annotator. The substring list stays as a backstop for sheets written before the column existed |
| 14 | "Intra-coder reliability as the ceiling" had **reappeared** in §B.8's fallback summary, contradicting the corrected §B.7 | Replaced with test–retest consistency; inter-coder reliability and construct validity stated as unmeasured. Round 1's #8 was closed in one section and left standing in another |
| 15 | `annotation/README.md` still said "the pilot corpus is the development set, permanently" while nine articles are reserved | Rewritten: A1 is 3 articles, the reserve is 9 articles / 70 candidates, and what is permanent is the *mechanism* — article-level assignment written once, the builder refusing to move an article |
| 11 | A6's native-vs-translated disagreement was called a bound; it combines translation effect with the coder's own inconsistency, and the sampling and washout were unspecified | Renamed **combined translation-plus-recoding variation**, reported against A3's test–retest rate on comparable items as the baseline, with the excess attributed to translation as an estimate carrying its own uncertainty. Sampling (recorded random draw from **German-original** items) and the ≥ 7-day washout written into `annotation/README.md` §A6 |
| 12 | Bilingual author approval was an undocumented subjective screen; arm 1 had no defined paired construction | A written **semantic-preservation checklist recorded per item before extraction** (technologies still named, evaluative predicates present and same-signed, comparison target preserved, no hedge added or dropped) — an appendix artifact with a reported pass rate. **Both translation directions**, balanced by source language, reported direction-specific and never pooled. A language effect must clear **both** baselines: prompt perturbation and translation-plus-recoding |
| 13 | The pilot-only fallback could not supply A2 = 120 against a reserve of 70, while the protocol, comparison, costs and limitations all assumed 120 | New **§B.8a**: two designs specified, **P** (pilot only) and **F** (corpus arrives), with A2 ≤ 70 as a *census* of the reserve, A3 and A6 at 25, 13–15 annotation hours, and — the load-bearing part — **no significance claim, pre-committed**, because *n_d* ≈ 10 discordant pairs against the ≈ 50 needed. Nine bootstrap clusters named as the real precision ceiling. The switch trigger is written down so it is not decided under time pressure |
| 1, 8, 10, 12 | The suite tests a stand-in prompt and schema, nothing makes it a gate, and the revised quota figure rested on the stand-in's 473 tokens while calling itself "measured" | All three claims **downgraded in place rather than defended**. §A.10 now states that a green run means "the API behaves as this plan says", not "the pipeline is safe to run"; the suite reports `3 = NOT CERTIFIED` itself; new check 12 fails-as-SKIP until a `rendered_prompt` digest exists; and §A.9's arm-4 quota is a **range (150,000–340,000, 12–26 % of cap)** with the note that three successive versions of that figure were wrong the same way — a point estimate on an unmeasured input |

### Rejected, with a reason

| # | Finding | Why |
|---|---|---|
| 17 (part) | "Require annotator IDs from an explicit human allowlist or a signed session manifest" | Disproportionate to a single-coder bachelor's thesis, and it would not add security — the coder signing their own manifest proves nothing an `annotator_type` column does not. The structural half of the finding (explicit provenance rather than a name heuristic) is **accepted and implemented**; the cryptographic half is rejected. If a second coder is found, an allowlist of two names becomes worth adding and this decision should be revisited |

### Behavioural verification the review prompted

Finding #3 asked for the 1-hour TTL to be sent and asserted. Doing that exposed that **three
different levels of evidence had each been treated as sufficient at some point**, and only the third
settles the question:

| Evidence | Shows | Enough? |
|---|---|---|
| The Bedrock price page lists a 1-hour tier ($6.00 vs $3.75 per 1M written) | AWS bills for it | No — billing is not behaviour |
| Preflight check 13: `ttl: "30m"` rejected, error names `system.2.member.cachePoint.ttl` | the field is a validated enum | No — validation is not persistence |
| **Long-gap test with a 5-minute negative control** | **persistence** | **Yes** |

`pipeline/verifications/cache_ttl_longgap.py`, run 2026-08-31: two unique prefixes written at t = 0,
read back at t = +410 s. The `ttl: "1h"` entry returned `read 3,923`; the `ttl: "5m"` control had
expired and wrote again. The control is what makes it a test — without it, a read at +410 s could
mean the default TTL is simply longer than documented. This matters because §B.6 counts one cache
**write** per TTL window and a write burns quota where a read does not: on the 5-minute default a
long run pays its prefix into the quota roughly twelve times an hour instead of once.

### Round 2 outcome

REVISE accepted. **17 of 18 findings fixed, one rejected in part with a reason.** Regression
evidence that the `finalize.py` rewrite did not change behaviour on valid data: replaying both
historical pass-2 sheets through the new validator reproduces the committed label files
**byte-for-byte**, with the SHA-256 digests `24e09e3893aa3104` and `3fe08cb6c885d7fa` that
`labels/sessions.csv` already records.

Still open, all four escalated to the author and none of them the agent's to close:
**#18** annotation budget (now confirmed in part — A6 adds 1.5 h, and §B.8a's Design P cuts it to
13–15 h), **#19** scope cuts, **#21** provisional decisions, **#22** the encoder-baseline objection.

---

## Round 3 — Codex

Same thread. **10 findings. VERDICT: REVISE.** Verbatim in the appendix.

Its framing was the most useful thing in three rounds: *"several fixes exist only in the plan text,
while the executable protocol still permits invalid data or future false certification."* Five of the
ten are exactly that shape — a correction written into `PROTOTYPE-PLAN.md` and not into the file that
actually runs, or into `annotation/README.md`, which is what the annotator reads. **A plan is not a
protocol, and I had been treating a plan edit as a fix.**

**All ten accepted.** Two deserve naming first:

- **#4 — the fake assertion, in deferred form.** Round 2's #2 was a check that hashed a constant and
  reported PASS. My fix made it SKIP when unpinned — but the code then read `if pin: ok = True`,
  loading nothing, hashing nothing, comparing nothing. **Typing a hash into `pins.json` would have
  turned it green while the probe still sent the stand-in.** The same defect, one release later,
  inside the file whose docstring is about silent failures. Now every pin carries a `path`, the
  artifact is loaded and hashed, and the check additionally asserts that the digest of what the probe
  *sent* matches. Verified adversarially: a hash with a null path now FAILs with "this is the deferred
  form of the fake-assertion defect".
- **#2 — `finalize.py` did not "validate everything", and they proved it.** The reviewer ran
  `validate()` with an invalid technology, non-binary frame values, an invalid comparison target, an
  invalid speaker, a blank confidence, a blank `annotator_type`, a malformed date and a non-numeric
  sitting, and **got zero problems back.** Only `confidence` (when non-blank) and the `eval_*` enums
  were checked. My commit message had claimed it "validates everything before writing anything",
  which was true about the *ordering* and false about the *coverage*.

### Accepted and fixed

| # | Finding | Fix |
|---|---|---|
| 2 | `validate()` checked two fields out of a dozen; a severely malformed row passed clean | New `annotation/codebook_reader.py` parses the **frozen codebook's** own value lists — technologies, 17 elements, frame derivation, evaluations, comparison targets, speakers, confidences — and `finalize.py` validates against those. §A.6 says the codebook is the single source of prompt, schema and sheet; a validator with its own copy of the technology list was a fourth source that drifts silently. The reader **raises** rather than returning a short list, because a validator that thinks nothing is permitted accepts everything. Added: technology / comparison-target / speaker / eval enums, binary fields restricted to 0-1, required non-blank confidence in 1–3, date parsing, positive-integer sittings, and the two **relational invariants** calibration had only *observed* — a fired element must carry its frame's evaluation (no orphans), and an evaluation may not sit on a frame that did not fire (no ghosts), plus `no_frame_for_this_tech` excluding any fired element. **Reproducing the reviewer's adversarial row now returns 13 problems** |
| 3 | `annotator_type` was neither required nor validated: blank passed, `banana` passed, only the literal `model` refused | Required on every row, restricted to `human`/`model`, and required to be consistent across the file. The substring heuristic stays as a backstop for sheets predating the column |
| 4 | Check 12 would falsely certify the moment someone filled the pin | `resolve_pin()` loads the artifact at the pinned `path`, hashes it, compares, and compares against what the probe sent. A `sha256` with no `path` FAILs. Also fixed a bug this exposed: a SKIP was being returned with `ok=False` and tallied as a failure |
| 7 | The TTL script was an observation script — no assertions, exit 0 regardless, Sonnet only | Rewritten as a test: asserts a clean write at t=0, `read == write` after the gap on the `1h` arm, and **expiry on the `5m` control**; fails if the control survives ("cannot conclude anything while the control misbehaves"); refuses `--gap <= 300`; writes a timestamped JSON artifact; runs **both models**. Re-run: all four arms behaved, exit 0 |
| 8 | The plan said the suite records the API's actual responses; `--json` recorded `"not captured"` | Flag inverted to `--no-raw`, capture on by default, and **capture is part of certification** — a run without it reports NOT CERTIFIED, because the run record must carry the responses the conclusions were computed from |
| 10 | A callable `CountTokens` would still certify while contradicting the plan | Now reported as `SKIP — CALLABLE, UPDATE REQUIRED` rather than PASS. Certifying against documentation that has been overtaken is how a stale claim reaches Chapter 4 |
| 5 | A6's correction was in the plan; the README still said "take 40", "with or after A2", and "bounds" | README §A6 rewritten to match: German originals only, a recorded random draw with the seed in the sample file, ≥ 7-day washout, A3 as the baseline, and **combined translation-plus-recoding variation** instead of "bound". The note that this was the same error as the "upper bound on the human ceiling" claim is kept in the file, because the pull toward claiming a bound survived knowing about it |
| 6 | §B.8a specified Design P; the README still mandated 120 / 40 / 40 throughout, making it impossible to follow | The session table now carries both columns (P / F), a "Two designs" section states the branch and the trigger, A2's draw rules cover the **census** case (all probabilities 1.0, valid for the reserve and generalising nowhere), A5 is marked Design-F-only, and the pre-committed no-significance-claim is stated where the annotator will see it |
| 9 | §B.6's cost table still carried the superseded 226,000 / 17 % point estimate and a hardcoded 4,096-token prefix | Whole table converted to ranges with a header saying no figure may be quoted as measured until M-1 runs a renderer-backed certified preflight. The risk row and the §A.10 narrative also corrected — including an admission that replacing ~250 with "measured 473" was the same mistake with a better number |
| 1 | **The instrument cannot represent its own speaker model.** The README promised "two rows" for a quoted source disagreeing with the outlet; `expand_sheet2.py` emits one row per (paragraph × technology) and `finalize.py` now rejects a second as a duplicate | Resolved by **dropping the claim, not the constraint** — see below |

### Finding #1, resolved by narrowing the claim

Two options existed: make the relation key `(paragraph × technology × speaker)`, or drop
relation-level speaker attribution and rewrite the protocol. **Dropped**, for three reasons that are
worth recording because the first is a genuine loss:

1. The unit is *frozen*. §A.9 defines exact-match over the `(paragraph × technology)` row, and 64
   calibration rows are coded under it. Changing the key changes the estimand mid-study.
2. Speaker was never going to be a reported contribution — the codebook marks it `DESCRIPTIVE ONLY`
   and §B.7 lists validated speaker attribution as out of scope.
3. The duplicate check that made the contradiction visible is itself correct and worth keeping.

So: **one row, `evaluation: mixed`, and the note records which voice said what.** The cost is real and
now sits in §B.7 rather than being discovered while coding: P009 found 37–38 % of ascribed opinions
did not match the speaker's actual position, so speaker-differentiated evaluation is exactly what
this design cannot check. `mixed` plus a note is a record a human can re-read, not a measurement.

### Round 3 outcome

REVISE accepted, **all 10 findings fixed.** Evidence rather than assertion, for the two biggest:

- **The reviewer's own adversarial row now returns 13 problems** where it returned none. Orphan and
  ghost invariants verified separately, and a clean row still returns zero.
- **The 64 historical calibration rows pass the full codebook-driven validation**, and the replayed
  label files are identical to the committed ones modulo the new `annotator_type` column. So the new
  invariants are neither vacuous nor retroactively broken: calibration's reported "every fired frame
  received an evaluation — no orphans, 100 % coverage" now holds as an assertion rather than an
  observation.
- **A hash with no artifact path FAILs**, verified by putting `deadbeef...` in `pins.json` with a
  null path and confirming the check refuses rather than greens.

Still open, all four escalated to the author: **#18** annotation budget, **#19** scope cuts,
**#21** provisional decisions, **#22** the encoder-baseline objection.

---

## Round 4 — Codex

Same thread. **10 findings. VERDICT: REVISE.** Verbatim in the appendix. I asked two questions
directly this round; both were answered, and one of the answers is the most useful thing in four
rounds.

### The two questions I asked

**"Is dropping relation-level speaker attribution defensible, or is the loss too large?"**
Defensible — *"paragraph-level discursive content can legitimately include both journalist and
quoted-source assertions, so `(paragraph × technology)` remains a coherent estimand"* — **but only
if narrowed one step further.** Keeping a single "dominant speaker" per row and reporting its
distribution is arbitrary when different frames in the row came from different voices: that field
has no stable interpretation. **Accepted.** `speaker` is now note-level only: it stays in the sheet
because it helps the annotator write a useful note, and **no distribution, cross-tabulation or
agreement figure is computed from it.** The codebook's own wording ("reported as a distribution")
overstates it; v1.0 is frozen, so §B.7 item 9 is the correction of record.

**"Is anything now over-engineered for a bachelor's thesis with one coder and eight weeks? I would
rather cut than gold-plate."** Yes — *"the finalizer's core integrity checks are worthwhile, but the
regex parser and 658-line general API contract suite create more maintenance risk than they remove.
The thesis needs an exact production-request smoke test, not a reusable Bedrock conformance
framework."* **Accepted, and acted on rather than noted.** The suite split by what each check is
*for*:

| Stays in `preflight.py` — gates a run | Moved to `verifications/api_contract.py` |
|---|---|
| model access · forced-tool parsing · `stopReason` · cache write/read equality · prefix size · usage completeness · the three pins | `temperature`+`topP` · `temperature: 0`+thinking · `seed` · `CountTokens` · the `ttl` enum |

Nine gate assertions instead of thirteen, and the file is shorter than the version that claimed
more. The moved probes ran once as `api_contract.py` — 12 probes, both models, all passing, dated
artifact — and are re-run when the region, the model generation or the SDK version changes.

### Accepted and fixed

| # | Finding | Fix |
|---|---|---|
| 2 | **A live bug.** `wind_siting` and `hydrogen_pathway` were classified as binary, so a correct `wind_siting=onshore` would be **rejected** — and A1's third row mentions wind, so this would have fired on the first session. Separately, `expand_sheet2.py` dropped both subtypes, so final labels lost them | The codebook reader now parses subtype values (`onshore\|offshore\|unspecified`, `green\|blue\|gray\|unspecified`) and `finalize.py` validates them as enums. `expand_sheet2.py` carries each subtype onto the row for its own technology, and pass 2 gained the two columns to receive them. Verified: `onshore` passes, `somewhere nice` is refused |
| 1 | `validate()` returned zero problems for a file containing only `row_id`, `paragraph_id` and provenance. Required fields were checked **only if present**, and omitting an `eval_*` column silently disabled the orphan check for that frame | Required column schemas for pass 1 and pass 2, plus duplicate-header detection and a refusal for a sheet matching no known shape. The pass-2 schema is derived from the codebook, so a missing element or evaluation column is named |
| 3 | Blank `technology`, `comparison_target`, `speaker` and a blank/mixed `codebook_version` all passed — blanks were discarded before the mixed-version check ran | Required non-blank on every pass-2 row, with the codebook's own "nothing here" values pointed at: `comparison_target: none`, `speaker: unattributed`. A blank `codebook_version` on any row is now a refusal |
| 4 | Seven silent misparse modes in the regex reader | Duplicate ids **raise** instead of being deduplicated; `- id:` matching is depth-anchored so a future nested list is not absorbed; duplicate frame names raise; the derivation must **exactly partition** the element set (an element in two frames, or a derivation naming a non-existent element, both raise); inline lists strip comments and reject values the parser cannot trust; version selection is **numeric**, so v1.10 sorts after v1.9. All four failure modes verified by mutating a copy of the codebook |
| 5 | The README's schema table still said "10 binary items" when v1.0 has **17**, and its comparison-target list omitted the technology values v1.0 added — the exact instruction divergence the single-source design exists to prevent, in the annotator-facing file | Table corrected and **marked non-authoritative**, pointing at `codebook_reader.py` for the real lists, with a note that it has drifted before |
| 6 | The TTL test proved "longer than 410 s", not a one-hour lifetime — a 10-minute TTL would pass it and its control identically — yet the script concluded the 1h setting supports one write per hour | Claim narrowed in the script, its README and the plan. The script now says which of the two conclusions the run it just did **earned**, and §B.6 budgets `ceil(run_duration / gap)` until a boundary run exists. A `--gap 3300` run was launched to settle it |
| 7 | Capture was certified **in memory**: the default command captured responses, exited on `captured=True`, then discarded them. It could print CERTIFIED while producing no artifact | The artifact is written atomically to `runs/preflight_<timestamp>.json` **before** the verdict is printed, it names itself, and a run with no artifact is NOT CERTIFIED however many assertions passed |
| 8 | Only successful responses were captured; the `ValidationException` evidence that checks 4/10/13 rested on was reduced to a short string | Failed calls are captured with the provider's full error response. Largely resolved *by* the split: those checks now live in `api_contract.py`, whose artifact records error code, class and provider response per probe |
| 9 | See "the two questions" above | `speaker` removed from all quantitative output |
| 10 | See "the two questions" above | Suite split; nine gate assertions |

### A bug the fixes exposed, and what it says about the design

Adding required-column schemas immediately refused **both historical calibration sheets**: `pass-2
sheet is missing frame element column(s) ['F2c']`. Correct refusal, wrong rule — those sheets were
coded under **v0.3, which had 16 elements**; F2c arrived in v1.0. The schema was validating against
the *frozen* codebook rather than the version the rows declare.

That matters beyond the immediate fix. §A.6's whole design is that old label versions are never
overwritten (`labels/A1_v1.csv`, `A1_v2.csv`) because the diff between codebook versions is evidence
for Chapter 4. **A validator that can only read the current codebook makes every historical session
unreprocessable**, which quietly removes the ability to produce that evidence. So
`codebook_reader.load_version()` now resolves the codebook the rows declare, and the reader's minimum
counts were lowered from v1.0's sizes to floors below the smallest real version — they exist to catch
a *parse failure*, not to assert how large the instrument is. All four versions now parse, and the
count differences are themselves the version history: v0.1 has 11 elements and 6 frames, v0.2 and
v0.3 have 16 and 9, v1.0 has 17 and 9 with 15 comparison targets instead of 7.

### Round 4 outcome

REVISE accepted, **all 10 findings fixed.** Evidence:

- **The subtype bug is closed and was real**: `wind_siting=onshore` passes, `somewhere nice` is
  refused. It would have fired on A1 row 4.
- **The structurally empty file is refused**, the reviewer's blank-value row is refused with the
  codebook's own substitute values named, and **a fully valid pass-2 row still returns zero
  problems.**
- **All four codebook misparse modes raise**, verified by mutating a copy: a duplicated technology
  id, an element in two frames, a derivation entry naming a typo, and an unparseable subtype comment.
- **The 64 historical rows still pass**, now validated against **their own** codebook version, and
  replay identically modulo the three new columns.
- **The A1 pass-1 sheet the author is about to code was run through the full validator with
  simulated labels: zero problems.** That is the check that matters most this week.
- **12 API-contract probes pass** with a dated artifact; **nine gate assertions** remain in
  preflight, which reports NOT CERTIFIED and writes its artifact to disk.

Still open, all four escalated to the author: **#18** annotation budget, **#19** scope cuts,
**#21** provisional decisions, **#22** the encoder-baseline objection.

---

## Round 5 — Codex — MAX_ROUNDS

Same thread. **10 findings. VERDICT: REVISE.** Verbatim in the appendix.

**MAX_ROUNDS = 5 is reached without APPROVED, and this log does not record convergence it did not
get.** What follows is what was fixed, what remains, and why each remaining item is where it is.

I asked two questions. The first — *what is left that would produce a wrong number in the thesis, or
corrupt data the author is about to create in A1 this week* — produced the most operationally
valuable answer of the five rounds, including a bug that would have wasted a full session.

### Fixed this round (8 of 10)

| # | Finding | Fix |
|---|---|---|
| 8 | **The sheet instructed the annotator to enter invalid values.** The pass-2 label row said `pos\|neg\|neutral\|mixed`; the codebook and validator accept only `positive\|negative\|neutral\|mixed`. Coding A1 against the sheet's own header would have produced a file `finalize.py` refuses — **after hours of work.** `eval_jobs` also still said "only if F2a or F2b fired" after v1.0 added F2c | The nine `eval_*` hints are **generated** from the codebook's `evaluation` values and `frame_derivation`. They were hand-written copies of codebook content and had drifted twice |
| 1 | `expand_sheet2.py` warned about unfinished pass-1 rows and carried on, omitting them — so A1's denominator quietly became smaller than 35 with nothing downstream able to tell | Refuses unless every row has a technology, `no_frame_present` in {0,1}, confidence in 1–3 and provenance. `--partial` proceeds and states the resulting denominator. Also refuses to regenerate over a pass-2 sheet that already carries coding. **The old warning also encoded a real confusion**: it treated `no_frame_present=1` as an alternative to ticking a technology. Every candidate paragraph names one — that is how it became a candidate; `no_frame_present` says the paragraph does not *frame* it |
| 3 | **The A1 protocol never finalized pass 1.** Only pass 2 was finalized, so the technology ticks and paragraph-level no-frame decisions stayed in the gitignored sheet and never entered the committed record — and those *are* gold labels: they are what technology agreement and abstention accounting are computed against. The generic command also showed a one-argument CLI that does not exist | README finalizes and commits **both** passes, with the two-argument signature, and warns to budget a working day rather than three hours for 80–110 pass-2 rows |
| 2 | A pass-2 skeleton has every element 0 and `no_frame_for_this_tech` 0. Once provenance, confidence, speaker and comparison were filled it validated clean — the invariant checked only the contradictory direction | Exactly one of {an element fires, `no_frame_for_this_tech`=1} must hold on every pass-2 row. Pass 1 requires a technology and a 0/1 `no_frame_present` |
| 7 | `F9` could fire with a blank note; `comparison_target` `other`/`multiple` needed no note; `row_id` need not equal `paragraph_id__technology` although evaluation joins on it | All three enforced. The `row_id` one matters most: a non-canonical id silently drops the row from the gold↔model match rather than erroring |
| 5 | A certified run could exit 0 while its own artifact recorded `certified: false` — certification was recomputed in memory *after* the write | The final verdict is set before the bytes are written, and reset if the write raises |
| 4 | **The McNemar power calculation used the wrong unit, twice over** | See below — the most methodologically serious finding of five rounds |
| 9 | The summary and risk tables still stated claims earlier rounds had rejected: "2 per frame", benchmarking against a "human ceiling", the intra-coder fallback "declared as an upper bound", and a stale 25-passing/2-unasserted preflight account describing the pre-cut suite | All corrected. This is the fourth round in which a fix landed in one section and was left standing in another; the pattern is worth naming in Chapter 4 as a property of documents this size |

### Finding #4, in full, because it is the one that would have reached print

Two errors pointing in **opposite** directions, which is why neither was visible:

1. Exact match is defined over `(paragraph × technology)` **rows**, but the power calculation was
   written with **paragraph** counts (70 or 120). A1 alone turns 35 paragraphs into 80–110 rows.
2. McNemar assumes independent paired observations. Rows from one paragraph share a sentence, a
   speaker and a framing, so a row-level p-value is invalid **however** *n* is corrected.

Fix: the two units are now named and kept apart. The **row** is the *scoring* unit — per-field and
per-row micro/macro scores. The **paragraph** is the *testing* unit, via a **paragraph-level
correctness indicator**: a paragraph is correct for a model when its entire relation set matches gold
— every row present, none spurious, every field equal. That is a genuinely paired observation.
Clustering goes to the article-level cluster bootstrap, which can represent it; the McNemar p-value
cannot.

And the power is **worse** once counted correctly, not better: a whole-set indicator is stricter than
a per-row one, so discordance on it is rarer. At 15 % discordance, 120 paragraphs give *n_d* ≈ 18 and
70 give ≈ 10, against the ≈ 50 needed for 80 % power. **Design F is therefore not obviously safe
either** — which is a reason to have the descriptive framing pre-specified, not a reason to hope.

### Not fixed, and why

| # | Finding | Status |
|---|---|---|
| 6 | `validate_tool_use()` checks top-level fields, types, enums and additional properties; a nested array of relation objects could carry missing fields or illegal enums and still pass. The fix is to invoke the production Pydantic parser | **Deferred to M-1 by nature — the production parser does not exist.** Recorded as an M-1 requirement rather than closed: pinning `TOOL_SCHEMA` is explicitly *insufficient*; preflight must call the same parse path M4 uses. Check 12 already reports NOT CERTIFIED until then, so the gap is visible in the tool's own output rather than only here |

### The feasibility answer, and the author's decision

Asked directly whether the plan is implementable by one person in the remaining time:

> A1 is feasible this week, but budget a working day, not three dependable hours. **The complete plan
> is still not credible for one person in eight weeks while writing roughly 70,000 characters.**

With a cut list: GDELT, the batch-inference experiment, Haiku/cascade filtering, the thinking
ablation, additional languages, RQ4, speaker coding beyond `unattributed`; defer the PDF adapter
until an export exists; and under further pressure, cut A6 and the perturbation arm and report de/en
descriptively.

**The author's decision, 2026-08-31: cut nothing. Build through.** That closes escalated finding
**#19** — it was always a scope decision with supervisor implications, and it has now been made
rather than deferred. The reviewer's warning stands on the record here, unedited, and the cut list is
preserved above so that if week 4 forces a cut it is chosen from a list written before the pressure
rather than under it.

### Resolution at MAX_ROUNDS

**No APPROVED verdict was obtained.** Five rounds, **68 findings, 64 fixed.** The unresolved set is
one deferred implementation requirement (#6, blocked on code that does not exist) and three standing
escalations to the author (**#18** annotation budget, **#21** provisional decisions, **#22** the
encoder-baseline objection). #19 is now decided.

The reviewer's closing judgement is worth keeping verbatim, because it is advice about where effort
should go next rather than a finding:

> The engineering already completed is sufficient infrastructure. Further verification-framework work
> now has lower thesis value than completing the extractor, gold data, evaluation, and writing.


---

## Appendix — round 2 critique, verbatim

Reproduced unedited apart from the reviewer's absolute file paths, which have been reduced to
`file:line` form. This is the argument the dispositions above respond to.

```
The revision improves the design, but #14, #15, and especially #20 are only partly closed. A passing preflight currently does not certify the production pipeline.

1. **#20 remains materially open: the preflight tests a synthetic prompt, synthetic 10,110-token prefix, and toy three-field schema—not the rendered production codebook, production tool schema, run configuration, or extraction parser** (`preflight.py:105`, `preflight.py:149`). A passing probe can therefore precede a production run whose real schema fails, truncates, or does not cache.  
   **Fix:** Make preflight consume the exact frozen run configuration and rendered production prompt/schema, then exercise the real response parser on them.

2. **Assertions 7 and 11 are advertised but not implemented.** Check 7 hashes the same in-memory function twice, which proves only that two immediately repeated serializations match; it never compares against a pinned run-config hash. Check 11 explicitly says it records the codebook hash without comparing it, then sets `ok = True` (`preflight.py:181`, `preflight.py:199`). This directly contradicts the plan’s assertion table.  
   **Fix:** Require a run-config path, compare its pinned codebook/prompt/schema hashes to the actual artifacts, and fail if a pin is absent.

3. **The suite does not test the claimed one-hour cache TTL.** Its cache point contains only `{"type": "default"}` (`preflight.py:324`); no `1h` TTL is requested or asserted. The quota estimate nevertheless assumes at most two one-hour windows.  
   **Fix:** Send the production `ttl: 1h` configuration and assert the resulting behavior or returned metadata; otherwise budget the default TTL.

4. **The cache check can fail spuriously on reruns and is weaker than claimed.** Because the synthetic prefix is stable, call 1 may be a cache read when preflight is rerun inside the existing TTL; the code nevertheless requires a write. Conversely, it accepts any positive write and read without requiring equal token counts, so two different cached prefixes could pass.  
   **Fix:** Add a unique cache-busting suffix for the write/read test and require `call1.write == call2.read >= minimum`, while separately testing production-prefix reuse.

5. **The “schema-valid” assertion does not validate the schema.** It checks required keys and enum membership only; it ignores field types, extra properties, the tool name, malformed booleans, and nested structures (`preflight.py:250`). The toy schema itself does not forbid additional properties.  
   **Fix:** Validate the returned `toolUse.name` and input with the same full JSON Schema validator and Pydantic model used by extraction.

6. **Checks 9 and 10 turn arbitrary errors into passes.** CountTokens passes whether the call succeeds or raises any exception; seed rejection also treats every exception—including transient transport or credential failures—as proof that `seed` is unsupported (`preflight.py:302`, `preflight.py:363`). Check 9 also contradicts the plan table, which says it asserts a `ValidationException`, while the observed result was `AccessDeniedException`.  
   **Fix:** Accept only explicitly enumerated expected exception classes/codes; represent CountTokens success as a documented capability change, not as the same passing assertion.

7. **Usage-field validation is not “on every response.”** Only the second cache call is checked; the reachability, tool-use, request-rule, first-cache, and CountTokens responses are not. Non-integer values also pass because negativity is tested only when a value already is an `int`.  
   **Fix:** Route every successful Converse response through one validator requiring four integer, non-negative fields and internally consistent totals.

8. **Nothing currently makes preflight a gate.** There is no extraction runner in `tools/pipeline/` invoking it, no run-record writer, and `--json` merely prints a report. A user can run preflight successfully, then execute a different model, region, schema, prompt, or inference configuration.  
   **Fix:** Have the eventual runner call preflight in-process against its resolved configuration and embed the signed/hash-linked result; refuse standalone “trust me, it passed” linkage.

9. **The plan overstates what the suite records.** It says actual API responses and `access_date` enter the run record, but JSON output contains summarized details rather than raw responses, and the script has only `started_at`. It also claims “26 assertions, 13 per model”; the code has two global checks plus eleven per model—24 checks total when both models are reachable.  
   **Fix:** Correct the count and prose, or store raw/redacted probe responses plus start/end timestamps and the exact resolved configuration.

10. **#16’s accounting distinction is fixed, but the revised quota estimate is still based on the wrong request.** The measured 473 uncached tokens came from the toy schema and synthetic prefix, not a real paragraph under the production schema. The estimate also assumes a roughly 5,000-token codebook while preflight actually cached 10,110 tokens and assumes at most two cache windows without a run-duration basis (`plan:594`). The likely headroom is still ample, but “measured” and “6×” are unsupported.  
    **Fix:** Measure dynamic input and cache size using the actual rendered production request, then present quota headroom as a range over realistic run durations and cache-write counts.

11. **#15 is improved, but A6 does not isolate translation change.** Native-gold versus translated-gold disagreement combines translation effects with the sole coder’s test–retest inconsistency and memory; it does not mathematically “bound” either extractor comparison. The protocol also does not explicitly say that the 40 items are German originals, how they are sampled, or how long the washout is.  
    **Fix:** Draw a recorded random sample of German A2 items, require the stated ≥7-day washout, compare translation disagreement with the A3 test–retest baseline, and call it combined translation-plus-recoding variation—not a bound.

12. **#14 is only partly identified.** Bilingual author approval is an undocumented subjective screen, not evidence that labels were preserved. Arm 1 still lacks a defined paired construction—German originals translated to English, English originals translated to German, or both—and comparing its change rate with prompt perturbations does not separate direction, source-text, and translation effects.  
    **Fix:** Use both translation directions with source items balanced by direction, record a semantic-preservation checklist before extraction, and report direction-specific paired effects; reserve “language effect” for changes exceeding both translation/re-coding and prompt baselines.

13. **The pilot-only fallback still cannot supply the planned A2.** The implemented reserve contains only 70 candidate paragraphs (`plan:390`), while the protocol, model comparison, costs, and limitations consistently assume A2 = 120. If Factiva never arrives, the advertised fallback thesis cannot execute that design.  
    **Fix:** Specify a separate pilot-only design with `n ≤ 70`, revised A3/A6 sizes, costs, power statement, and cluster count; activate the 120-item design only if enough external data exists.

14. **A previously fixed reliability error has reappeared.** The no-Factiva summary again promises “intra-coder reliability as the ceiling” (`plan:965`), directly contradicting the corrected limitations section.  
    **Fix:** Replace it with “test–retest consistency; inter-coder reliability and construct validity unmeasured.”

15. **The annotation protocol still contradicts the implemented split.** It says “the pilot corpus is the development set, permanently” (`README:83`), although nine pilot articles are reserved for A2.  
    **Fix:** State that three pilot articles form A1 and nine form the untouched pilot evaluation reserve.

16. **`finalize.py` does not enforce several rules the README says are mandatory.** Empty annotators, missing dates, missing sitting values, sitting `0`, duplicate row IDs, invalid labels, incomplete rows, and unexpected session sizes all pass. Missing sittings merely generate a warning after the file has already been written (`finalize.py:190`).  
    **Fix:** Validate every required field, enum, relational invariant, unique ID, permitted split, expected session size, and positive sitting number before writing anything.

17. **The model refusal is heuristic rather than provenance-safe.** Any model name absent from `MODEL_MARKERS`, an abbreviation such as “AI,” or a blank annotator enters the human namespace; meanwhile a human whose name contains a marker could be rejected.  
    **Fix:** Require annotator IDs from an explicit human allowlist or signed session manifest, with model/human provenance represented as a separate mandatory field.

18. **The licensed-text refusal provides false assurance.** A renamed article-text column containing values of 400 characters or less passes, and unlimited prose can be placed in `note` or `comparison_note`. Many real pilot paragraphs are shorter than that threshold.  
    **Fix:** Export from a strict allowlist of known non-text columns, scan all free-text fields under the licence policy, and reject every unknown column regardless of value length.

VERDICT: REVISE
```


---

## Appendix — round 3 critique, verbatim

Same treatment: unedited apart from absolute paths.

```
The fail-closed preflight status is a real improvement, as are cache busting, equal write/read checks, typed error handling, and the output-column allowlist. But several fixes exist only in the plan text, while the executable protocol still permits invalid data or future false certification.

1. **The annotation instrument still cannot represent its own speaker model.** The protocol says that an attributed source and a sceptical outlet produce “two rows” for the same technology (`README:300`), but `expand_sheet2.py` generates exactly one `paragraph × technology` row, and `finalize.py` rejects a second as a duplicate (`finalize.py:214`). Opposing evaluations by different speakers are therefore forced into one speaker and one polarity or collapsed to `mixed`.  
   **Fix:** Make the relation key `paragraph × technology × speaker/assertion`, or explicitly drop relation-level speaker attribution and rewrite the protocol and claims accordingly.

2. **`finalize.py` does not “validate everything”; a severely malformed row passes with zero problems.** I exercised `validate()` with an invalid technology, non-binary frame values, invalid comparison target, invalid speaker, blank confidence, blank `annotator_type`, malformed date, and nonnumeric sitting: it returned no errors. The implementation validates only confidence values when nonblank and `eval_*` enums, leaving most codebook fields unchecked (`finalize.py:231`).  
   **Fix:** Validate every categorical and binary field against the frozen codebook, require confidence 1–3, parse dates and positive integer sittings, and enforce frame/evaluation/no-frame relational invariants.

3. **The explicit annotator provenance fix is cosmetic because `annotator_type` is neither required nor validated.** Blank or arbitrary values such as `banana` pass; only the literal value `model` triggers refusal (`finalize.py:170`). Thus a model run labelled with an innocuous annotator name and blank `annotator_type` still enters the human namespace. This does not require the rejected cryptographic manifest—just enforcement of the chosen structural field.  
   **Fix:** Require `annotator_type` on every row, restrict it to `human | model`, require one consistent value per file, and route `model` files to the marked namespace.

4. **Preflight check 12 will falsely certify once someone merely fills the pin.** If `rendered_prompt.sha256` is non-null, the code sets `ok = True` without loading a prompt, hashing it, comparing the digest, or sending it to Bedrock (`preflight.py:342`). `pins.json` does not even define a prompt path. This recreates Round 2’s fake-assertion defect in deferred form: today it exits 3 correctly; at M-1, adding the promised hash alone turns it green while it still sends the stand-in.  
   **Fix:** Give each pin an artifact path, hash the loaded artifact, construct the actual request from it, and certify only when the exact loaded prompt and schema are the objects sent by the probe.

5. **The A6 fix was applied to the plan but not to the annotation protocol.** The plan correctly calls the result combined translation-plus-recoding variation, specifies German-only random sampling and a ≥7-day washout (`plan:589`); the README still says “take 40,” permits “with or after A2,” and claims native-versus-translated gold “bounds” the other comparisons (`README:229`). This is exactly a cosmetic rather than substantive fix because the annotator follows the README.  
   **Fix:** Replace README §A6 with the plan’s random German-original draw, recorded seed, ≥7-day washout, A3 baseline comparison, and non-bound terminology.

6. **The pilot-only design likewise exists only in the plan, not the executable protocol.** §B.8a specifies A2 ≤70, A3=25, and A6=25, but the README still mandates 120/40/40 throughout. Following the annotation instructions under Design P is impossible.  
   **Fix:** Make the README explicitly branch on Design P/F, with generated session manifests carrying the selected design and expected sizes.

7. **The TTL script is an observation script, not a verification test.** It has no assertions and always exits successfully regardless of whether 1h survives or the 5m control expires; it merely prints `SURVIVED`/`EXPIRED` (`cache_ttl_longgap.py:49`). It also tests Sonnet only, while the cache claim supports both Sonnet and Opus. The recorded 2026-08-31 result is useful evidence, but the code does not enforce its advertised verdict.  
   **Fix:** Assert initial writes, exact later read/write behavior and matching counts, exit nonzero on failure, save a timestamped JSON artifact, and run both production models or explicitly limit the conclusion to Sonnet.

8. **The preflight still overclaims response capture.** The plan says it records the API’s actual responses into the run record (`plan:719`), but responses are retained only when `--raw` is supplied; ordinary `--json` records `"not captured"` (`preflight.py:562`).  
   **Fix:** Make raw/redacted response capture mandatory for certification, or narrow the plan’s claim to summarized check results.

9. **The cost table still contradicts the revised quota range.** §A.9 now responsibly gives 150,000–340,000 quota tokens, but §B.6 still reports the superseded 226,000 point estimate and 17% share (`plan:966`). The same table hardcodes a 4,096-token prefix even though production prompt size remains unmeasured.  
   **Fix:** Propagate the range into every downstream table and mark production cost unavailable until the renderer-backed certified preflight measures it.

10. **Successful CountTokens availability would still produce a certified run while contradicting the plan.** Check 9 deliberately passes both “callable” and recognized “unavailable” outcomes (`preflight.py:522`), although the plan presents post-hoc-only accounting as an asserted API constraint. This is acceptable capability detection, but not an assertion that behavior is “as documented.”  
    **Fix:** Treat a capability change as `UNASSERTED/UPDATE REQUIRED` until the plan and run configuration are updated, rather than allowing certification under stale documentation.

VERDICT: REVISE
```


---

## Appendix — round 4 critique, verbatim

```
The speaker scope cut is defensible, but the implementation still has material validation holes. The preflight pin logic is now genuinely fail-closed; the main remaining defects are in annotation integrity and claims around persistence/capture.

1. **`finalize.py` still accepts a structurally empty label file.** I passed it rows containing only `row_id`, `paragraph_id`, and valid provenance; `validate()` returned zero problems. Required columns such as `technology`, all 17 elements, evaluation columns, `comparison_target`, `speaker`, and `confidence` are checked only if present (`finalize.py:296`). Omitting an entire evaluation column also disables the orphan check for that frame.  
   **Fix:** Define required schemas for pass 1 and pass 2 and reject missing or duplicate columns before validating values.

2. **Subtype fields are currently broken.** `wind_siting` and `hydrogen_pathway` are classified as binary fields (`finalize.py:103`); a valid `wind_siting=onshore` row is rejected as “other than 0/1.” Meanwhile `expand_sheet2.py` does not carry either subtype into the finalized relation sheet, so final labels lose them entirely.  
   **Fix:** Parse their categorical values from the codebook, validate them as enums, and propagate them into pass 2—or explicitly remove the subtype claims and columns.

3. **Blank substantive values still pass.** `technology`, `comparison_target`, and `speaker` use `allow_blank=True`; binary elements accept blank; a mixture of blank and valid `codebook_version` values passes because blanks are discarded before comparison (`finalize.py:182`, `finalize.py:296`). This permits incomplete rows to enter reported data.  
   **Fix:** Require canonical nonblank values for every field applicable to the sheet type and require a codebook version on every row.

4. **The regex codebook reader has silent misparse modes.**

   - `_ids()` silently deduplicates duplicate IDs instead of rejecting them.
   - Any future nested list using `- id:`—aliases, excluded values, examples, or subtypes—is absorbed into the enclosing enum.
   - Duplicate frame names in `frame_derivation` overwrite each other.
   - An element assigned to two frames silently maps to the last one.
   - Derivation entries referencing nonexistent elements are not rejected.
   - `_inline_list()` splits on every comma without understanding YAML quoting or comments.
   - The “newest” frozen file is selected lexicographically, so `v1.10` can sort before `v1.9`.

   The minimum-count guards do not catch extra or wrongly classified values (`codebook_reader.py:58`, `codebook_reader.py:93`).  
   **Fix:** At minimum reject duplicate IDs/frames, require the flattened derivation to equal the frame-element set exactly and one-to-one, and select versions numerically. Prefer asking permission for PyYAML or replacing this with a small versioned JSON validation manifest tied to the pinned codebook hash.

5. **The README’s schema summary has drifted from the frozen codebook.** It still says “10 binary items” although v1.0 has 17, and its comparison-target list omits technology targets and `multiple` (`README:332`). This is exactly the human/model instruction divergence the single-source design is supposed to prevent.  
   **Fix:** Generate that table from the codebook or delete the duplicated summary and link directly to the generated guide.

6. **The TTL test proves “longer than five minutes,” not a one-hour lifetime.** A 10-minute cache would pass the 410-second test and its negative control. The script then concludes that the one-hour setting is honored and supports one write per hour (`cache_ttl_longgap.py:138`). That conclusion is stronger than the experiment.  
   **Fix:** Describe the result as persistence beyond 410 seconds, or test near the claimed one-hour boundary before using it to estimate hourly cache writes.

7. **Preflight capture is certified in memory, not durably.** Running the documented default command captures raw responses in `report.raw`, exits based on `captured=True`, then discards them because neither JSON nor a file is written. It can therefore print `CERTIFIED` while producing no run-record artifact (`preflight.py:193`, `preflight.py:612`).  
   **Fix:** Certification should require an output path and successful atomic persistence, or occur only when the runner embeds the report directly.

8. **Expected API errors are not among the “verbatim responses” captured.** The wrapper appends only successful Converse results; ValidationException/ParamValidationError evidence used by checks 4, 10, and 13 is reduced to a short detail string. Thus the plan still overstates the evidence stored for every conclusion.  
   **Fix:** Capture sanitized exception type, full provider error response, request label, and request configuration alongside successful responses.

9. **The speaker resolution is defensible only if narrowed one step further.** Paragraph-level discursive content can legitimately include both journalist and quoted-source assertions, so `(paragraph × technology)` remains a coherent estimand. The loss is acceptable because speaker attribution is explicitly outside the contribution. However, retaining a single “dominant or attributed” speaker and reporting its distribution is arbitrary when different frames in the row come from different voices. That field no longer has a stable interpretation.  
   **Fix:** Keep the paragraph-level evaluation and `mixed` rule, but remove speaker from quantitative output entirely; retain voice distinctions only in optional notes and state that outlet endorsement is not measured.

10. **Yes, parts are now over-engineered for the thesis.** The finalizer’s core integrity checks are worthwhile, but the regex parser and 658-line general API contract suite create more maintenance risk than they remove. The thesis needs an exact production-request smoke test, not a reusable Bedrock conformance framework.  
    **Cut recommendation:** once the renderer exists, retain only model access, exact pinned prompt/schema, forced-tool parsing, production cache write/read, usage capture, and run-record persistence. Move illegal-parameter probes, CountTokens, seed behavior, and TTL experiments to one-off verification artifacts. Replace the regex reader with either approved PyYAML or a pinned version-specific validation manifest. This reduces code while strengthening the checks that affect reported results.

The current preflight pin resolution itself is now sound: a missing pin remains unasserted, a digest without a path fails, and a loaded artifact must match both its pin and the digest claimed as sent. That part no longer warrants revision.

VERDICT: REVISE
```


---

## Appendix — round 5 critique, verbatim

```
The plan is close, but three defects can still corrupt A1 this week, and the paired significance test remains invalid as specified.

## Ranked findings

1. **The pass-2 expander knowingly writes an incomplete dataset.** It merely warns about unfinished pass-1 rows, then omits them from pass 2 ([expand_sheet2.py]%20SoSe%202026/g)%20BA/llm-energy-discourse-analysis/tools/annotation/expand_sheet2.py:52)). It also treats `no_frame_present=1` without any technology as complete, despite the codebook defining no-frame paragraphs as mentioning a technology. This silently shrinks A1’s denominator.

   **Fix:** Exit nonzero unless all 35 rows have at least one technology, explicit `no_frame_present ∈ {0,1}`, valid provenance/confidence, and refuse to overwrite an existing pass-2 file without an explicit flag.

2. **`finalize.py` still accepts substantively uncoded rows.** A pass-2 skeleton has every frame `0` and `no_frame_for_this_tech=0`; once provenance, confidence, speaker and comparison are filled, it passes. The current invariant checks only the contradictory `no_frame=1 + fired frame` direction ([finalize.py]%20SoSe%202026/g)%20BA/llm-energy-discourse-analysis/tools/annotation/finalize.py:491)). Pass 1 likewise allows blank technology/no-frame flags.

   **Fix:** Require exactly one of `{one or more frame elements fire, no_frame_for_this_tech=1}` in pass 2; in pass 1 require at least one technology and a nonblank binary `no_frame_present`.

3. **A1’s protocol never finalizes pass 1.** The documented sequence finalizes only pass 2 ([README.md]%20SoSe%202026/g)%20BA/llm-energy-discourse-analysis/tools/annotation/README.md:154)). That leaves the technology labels and paragraph-level no-frame decisions outside the committed gold record—the very labels needed for technology agreement and abstention accounting. The generic command later in the README also has the wrong CLI signature.

   **Fix:** Validate/finalize and commit both `A1_pass1` and `A1_pass2` before any model output is examined, and publish the exact two-argument commands.

4. **The McNemar power calculation uses the wrong unit and ignores clustering.** Exact match is defined over `(paragraph × technology)` rows ([plan]%20SoSe%202026/g)%20BA/llm-energy-discourse-analysis/tools/PROTOTYPE-PLAN.md:510), but power is computed with 70/120 paragraphs. A1 alone converts 35 paragraphs into an estimated 80–110 rows. Moreover, multiple rows from the same paragraph/article are not independent, so ordinary McNemar p-values are invalid even after correcting \(n\).

   **Fix:** Either define one whole-relation-set correctness indicator per paragraph and use an article-cluster-aware test, or drop McNemar and report only article-clustered paired intervals; do not power a row-level test using paragraph counts.

5. **A “certified” preflight artifact will record `certified: false`.** The payload is constructed and written before `report.persisted_to` is set; only afterward is `payload["certified"]` recomputed in memory, with no second write ([preflight.py]%20SoSe%202026/g)%20BA/llm-energy-discourse-analysis/tools/pipeline/preflight.py:554)). Exit 0 and the durable artifact can therefore disagree.

   **Fix:** Put the final certification state into the temporary payload before its atomic rename, so the bytes on disk contain the same verdict as the exit status.

6. **The preflight’s “schema-valid” check will not validate the eventual production schema.** `validate_tool_use()` checks only top-level required fields, primitive types, enums and additional properties ([preflight.py]%20SoSe%202026/g)%20BA/llm-energy-discourse-analysis/tools/pipeline/preflight.py:294)). A nested array of relation objects could contain missing fields, illegal enums or extra properties and still pass.

   **Fix:** Make preflight invoke the exact production Pydantic/parser path used by M4; merely replacing and pinning `TOOL_SCHEMA` is insufficient.

7. **Two instrument rules remain unenforced.** `F9` can fire with a blank note, and `comparison_target=other` or `multiple` can pass without the required explanatory/list note. `row_id` also need not equal `paragraph_id__technology`, despite evaluation joining on it.

   **Fix:** Enforce the codebook’s conditional note requirements and the canonical row-ID construction before writing labels.

8. **The sheet actively instructs the annotator to enter invalid evaluation values.** Headers say `pos|neg|neutral|mixed`, while the codebook and validator accept `positive|negative|neutral|mixed` ([build_sheets.py]%20SoSe%202026/g)%20BA/llm-energy-discourse-analysis/tools/annotation/build_sheets.py:163)). This should fail loudly rather than corrupt data, but it can interrupt A1 after hours of coding.

   **Fix:** Regenerate the A1 sheets with the codebook’s full evaluation values before coding begins.

9. **Several round-3 corrections remain cosmetic because the plan’s summary and risk sections still state the rejected claims.** It still says “2 per frame,” “benchmark against the human ceiling,” and intra-coder reliability is an “upper bound” ([plan]%20SoSe%202026/g)%20BA/llm-energy-discourse-analysis/tools/PROTOTYPE-PLAN.md:1182), ([plan]%20SoSe%202026/g)%20BA/llm-energy-discourse-analysis/tools/PROTOTYPE-PLAN.md:1224)). The preflight narrative also retains the old 25-passing/2-unasserted account alongside the nine-check design.

   **Fix:** Reconcile the summary, open-decision and risk tables with the operative sections before any plan prose is reused in the thesis.

## Feasibility

A1 is feasible this week, but budget a working day, not three dependable hours: 80–110 pass-2 rows can easily consume four to six hours once difficult notes and corrections are included.

The complete plan is still not credible for one person in eight weeks while writing roughly 70,000 characters. The defensible core is achievable if scope is frozen now:

- Keep markdown ingestion, paragraph storage, a deterministic relevance filter with attrition reasons, Sonnet extraction with complete run records, A1/A2/A3, evaluation, and the descriptive Sonnet–Opus comparison.
- Cut GDELT, the batch-inference experiment, Haiku/cascade filtering, the thinking ablation, additional languages, RQ4, and speaker coding beyond automatic `unattributed`.
- Defer the PDF adapter until an export actually arrives; a small fixture test can demonstrate the adapter boundary later.
- If schedule pressure remains, cut translation/A6 and prompt-perturbation arms and report German/English performance descriptively—not as an identified causal language-invariance effect.

The engineering already completed is sufficient infrastructure. Further verification-framework work now has lower thesis value than completing the extractor, gold data, evaluation, and writing.

VERDICT: REVISE
```


---

# Instrument review — `instrument.py` and `preflight.py`, 2026-09-02

> A **separate** argument from the plan review above, on the same Codex thread
> (`01a05ecb-1184-7460-a20b-2849896d97cc`), read-only sandbox on every round. Round numbering
> restarts here. Subject: the codebook→prompt/schema renderer and the gate that certifies the
> request a reported run will send. `MAX_ROUNDS = 5`, reached.

## What the five rounds actually changed

Rounds 1–2 built and hardened the renderer (recorded in
[`pipeline/INSTRUMENT-DESIGN.md`](pipeline/INSTRUMENT-DESIGN.md) as D1–D18). Rounds 3–5 attacked the
certifier, and every finding in them turned out to be one shape of the same defect: **a check that
certifies the thing next to the thing that matters.**

| Round | The check | What it actually certified | What it missed |
|---|---|---|---|
| 3 | check 13 asserted the submitted request "is the object the builder returned" | object *ancestry* — and the builder returns a mutable dict the caller passes straight on, so it was true by construction | any in-place edit to `messages` or `inferenceConfig` |
| 3 | two byte-identical production calls | call 2's `usage` | call 2's **content** — a refusal or a truncated object on the second call could not fail the gate |
| 3 | `load_artifacts()` hashed the file | the *decoded text* | a CRLF-only byte change, which decodes identically |
| 3 | the tool config "parses and has `tools` and `toolChoice`" | two dictionary keys | `{"tools": [], "toolChoice": {}}`, which loaded cleanly and failed later as an API error |
| 4 | the request digest | the request *body* — `modelId` was added one frame later | a swapped model |
| 4 | check 13 asserted prompt, config, cache TTL | three parts of the request | `temperature`, `topP`, the message shape, `maxTokens` |
| 4 | `--write` refreshed artifacts, then refused on a pin mismatch | nothing — but it destroyed the accepted bytes first | the ordering, which the in-memory render had already fixed one level up |
| 5 | the user turn "contains the paragraph" | containment | an appended unpinned instruction — "prefer security frames" — inside the same text block |

Two further findings were about **evidence rather than logic**, and both were fair: a run record that
names its artifacts and models but not the code that produced the verdict cannot be re-read later,
and `provenance` digests truncated to 16 characters under a field named `*_sha256` claimed more than
they held. Both closed.

**And one finding was neither, and is the most valuable thing in these three rounds.** Asked for the
single likeliest route from a correct instrument to a wrong published number, Codex named
**omission**: the response validator can reject a malformed relation the model *emitted*, but it
cannot see a technology the model *failed* to emit. Scoring only emitted rows would leave every
omission in the denominator instead of counting it as an error, inflating both agreement and
prevalence — worst in exactly the multi-technology paragraphs the design cares about. Written into
[`PROTOTYPE-PLAN.md`](PROTOTYPE-PLAN.md) §A.1 as a pre-registration constraint on M6, because M6 is
where it can no longer be fixed.

## What I rejected, and why

**A pinned run configuration for the region and the model IDs** (round 4, finding 9). The gap was
real — the IDs lived only in the certifier, so the runner could share the request builder, call
another model, and still claim the gate covered its run. But a pin exists to catch a *file* drifting
from an approved copy of itself, and there is no file here: two string constants. A `run_config.json`
plus a pin plus a pin check would create a second home for the same fact and a fourth artifact to
keep synchronised. The constants moved beside the builder instead, and the selected model now
travels inside the envelope the gate digests. Argued as D19, with its limit stated: nothing but M4
calling `instrument.MODELS` prevents M4 using another model.

## Where the reviewer landed on over-engineering

Its closing judgement, and I am recording it rather than arguing with it: **the review invested more
in the certifier than in the instrument.** Specifically named as disproportionate for a bachelor's
prototype — check 13b's mutation framework and the `before_send` seam, the full cross-product of
cache-failure and verdict-artifact tests, and D15–D19 as thesis-facing design decisions. Its own
advice was *not* to delete working, tested code immediately before a live run, but to condense
D15–D19 to a paragraph in Chapter 4 and stop investing in this module. D15–D19 now carry a banner
saying exactly that. The 12 gate assertions and 151 tests stay.

## Outcome

`VERDICT: REVISE` on round 5, with both of its findings closed after the fact. Its stated
precondition for a first credentialed run: the exact-user-text assertion (done) and **a clean tree**
— the offline record honestly reports nine dirty files, and a digest identifies uncommitted code
without being able to reconstruct it.

---

## Appendix — instrument review, round 3 critique, verbatim

```
The merged cache sequence is now logically sound, but the implementation is not yet approvable. I found two certification holes, a stale evidence artifact, and several reporting claims that contradict the repository.

## Material findings

1. Check 13 can miss a substituted production request. `submitted` contains the same mutable dictionary placed in `assembled`, so the identity test is true by construction; an in-place change to `messages`, `inferenceConfig`, or the cache point survives because the post-live audit rechecks only prompt text and `toolConfig`.

Fix: Snapshot or hash the complete request immediately after `build_request()` and compare that snapshot with the complete kwargs immediately before `client.converse()`.

2. The second byte-identical production response is never checked for `toolUse`, schema validity, relational validity, or `stopReason`. A run can therefore observe call 1 succeeding and call 2 returning malformed output or a refusal, yet still certify if its cache accounting is valid.

Fix: Apply checks 2 and 3 to both production responses and refuse certification if either exact production call violates the response contract.

3. `load_artifacts()` does not consistently turn load and shape failures into `InstrumentError`. A non-object pin entry produces `AttributeError`—I reproduced this with `system_prompt: "not-an-object"`—and filesystem read errors also escape the decoding handler.

Fix: Validate the complete pin-entry and tool-config structure up front and wrap `OSError`, type errors, decoding errors, and nested-shape errors in `InstrumentError`.

4. Tool-config “shape” validation is much weaker than claimed. `{"tools": [], "toolChoice": {}}` passes `load_artifacts()` because only the two top-level keys are required; it fails later as a live/API problem rather than offline check 14.

Fix: Require one correctly shaped `toolSpec`, matching forced `toolChoice`, an object input schema, and the expected tool name during artifact loading.

5. The rendered manifest on disk is stale and does not equal the current render. It still says preflight uses a stand-in and is “NOT yet wired”; the current in-memory render says the opposite. I measured distinct hashes: disk `aa089b4e…`, current render `a6e0c8a…`.

Fix: Regenerate the manifest and either pin it or remove mutable implementation-status claims from an unpinned research artifact.

6. A live invocation aborted before the API phase records `"live_attempted": true` merely because `--offline` was absent. This occurs, for example, when malformed pins make `run()` return before client construction.

Fix: Derive `live_attempted` from an explicit “API call about to execute” flag, not from the command-line mode.

7. Phase summaries call offline checks `"passed"` when they were skipped and therefore unasserted; check 13’s post-live audit is also still classified as an offline check even when its failure is discovered from submitted live requests.

Fix: Give phase summaries `passed/failed/unasserted/not_run` states and split check 13 into offline assembly and live submission assertions.

8. The new tests do not exercise preflight’s cache classification, post-live check 13, verdict construction, or exit behavior. B11–B13 call `build_request()` directly through a separate fake client, so they cannot catch a substitution introduced inside `preflight.production_converse()`.

Fix: Add preflight-level fake-client tests covering cold, warm, ambiguous, malformed second response, mutated kwargs, early offline abort, certification, and all exit codes.

9. The reported 111 assertions exist, but the suite writes temporary fixtures inside `tools/`; under the required read-only review it stops at B17 after 108 passes, so the final three assertions cannot be independently executed without granting repository writes.

Fix: Use `TemporaryDirectory()` outside the repository and inject absolute fixture paths through a test-only root resolver.

## Direct answers on the rewrite

- Cold starts are observable now. The reachability call carries neither tools nor system prompt, so it does not warm the production prefix; production call 1 can genuinely report a write.
- Warm starts are also handled coherently: read `N`, followed by read `N`.
- Check 6 is correctly gated on successful check 5 and uses `r2`, not an opportunistic maximum.
- I found no ordinary CLI path where a recorded `FAIL` exits 0 or where `CERTIFIED` is produced without reaching the live phase. The inaccurate `live_attempted` field is nevertheless an evidence defect.
- The major remaining weakness is that check 13 certifies object ancestry, not byte-for-byte immutability of the submitted request.

## D1–D18

| Decision | Assessment |
|---|---|
| D1 | Sound. P056 supports rejecting bare labels, and the confounding with examples is disclosed correctly. |
| D2 | Sound for joint extraction. P056 supports the cost argument, but it does not justify eliminating the separate M3 relevance cascade. |
| D3 | Sound logical choice; P022 is merely precedent, as the document now correctly admits. |
| D4 | Sound. Parity is the load-bearing argument; P022 is analogical decoration rather than evidence for this tie-break. |
| D5 | Substantively defensible, but the document is false: it says “verbatim” and names retired `no_frame_present`, while the renderer transforms both relevant fields. |
| D6 | Defensible default, not evidence of superiority. The cited studies support feasibility amid conflicting results; the planned ablation remains necessary if retained. |
| D7 | Sound own argument. Neither unused speaker nor incomparable self-confidence earns a schema field. |
| D8 | Sound own argument and appropriately narrow parity claim. |
| D9 | Sound architecture, but its “no pyproject/lock; PyYAML installed, not pinned” blocker is now stale. |
| D10 | Sound and load-bearing for reproducibility. |
| D11 | Sound; P063 supports archival and P024 supports exact reporting. |
| D12 | Sound denominator logic, but “M4 calls it” is premature because M4 does not exist. |
| D13 | Sound; the transformation machinery is justified by actual contradictory source prose. |
| D14 | The shared builder is necessary, but “both preflight and M4 call it” should be prospective. |
| D15 | Sound and simpler than the nonce design. |
| D16 | Sound; per-model thresholds are the correct assertion. |
| D17 | Useful engineering hygiene, but it is over-documented as an academic instrument decision. No literature is needed; keep the code and shorten the thesis-facing treatment. |
| D18 | Necessary two-layer validation, but again the claim that M4 already uses it is premature. |

Fix: Rewrite D5, resolve D9’s obsolete blocker text, and change every M4 present-tense claim in D12/D14/D18 to a requirement until that caller exists.

## GUIDE-LLM claims

- B.3 is partly defensible: the builder pins the parameters, while prompt language remains explicitly open. The usage log’s statement that the estimated 5,900-token prefix is above Opus’s minimum is still too strong before the credentialed measurement.
- B.4 may tick base model and tool calling, but “the model’s only legal reply is an object satisfying that schema” is false. Forced tool choice selects the tool; it does not guarantee schema-valid arguments—that is why validation exists.
- B.5 “No” is defensible from the stateless request construction.
- C.2 is defensible: the pinned instrument is the only authored system block and the paragraph is the user message.
- C.1’s claim that relevance filtering and extraction are the same call contradicts the still-active M3 design: M3 is explicitly a cheap relevance cascade before expensive extraction, with its own attrition ledger. `off_topic` in M4 can catch residual false positives; it does not silently abolish M3.
- E.2 contains two mutually exclusive parse-failure policies in adjacent bullets: retry once versus “structurally impossible.” The latter is also technically false—forced tool selection does not make malformed, truncated, or schema-invalid tool input impossible. M4 is unbuilt, so E.2 should remain planned rather than implemented/decided.

Fix: Restore M3/M4 separation in C.1, describe forced tool choice without promising valid arguments, label the cache size as an unverified estimate, and replace the duplicate E.2 bullets with one prospective failure policy.

## Most likely wrong thesis number

The likeliest failure is an inflated agreement or prevalence estimate caused by omitted technology relations. The validator can reject duplicates and malformed emitted relations, but it cannot determine that the model failed to emit a technology mentioned in the paragraph. If M6 scores only emitted rows, omissions disappear from the denominator rather than becoming errors—especially dangerous in multi-technology paragraphs.

Fix: Pre-register an outer-join scoring universe and test that every gold paragraph × technology relation absent from model output is counted as a model omission, never as attrition.

VERDICT: REVISE
```

## Appendix — instrument review, round 4 critique, verbatim

```
The cache and response-contract fixes are real, and the current rendered tool config passes the new shape validator. Material holes remain in request certification and `--write`.

## Findings

1. Check 13b still hashes before the actual client boundary and omits `modelId`. `production_converse()` computes both digests, then passes the request through `converse()`, which separately adds `modelId`; a changed model or mutation inside that wrapper is invisible.

Fix: Compute the second digest inside `converse()` immediately before `client.converse()`, over the complete envelope including `modelId`.

2. Check 13 verifies only prompt, tool config and cache TTL. If `build_request()` changes `temperature` to 1, adds `topP`, alters the user-message wrapper, or adds an extra system instruction, checks 13 and 13b both pass because they faithfully certify the same wrong builder output.

Fix: Make check 13 assert the complete production contract—exact keys, system blocks, user-message shape, inference settings, cache point and requested `maxTokens`.

3. P11 does not stage a request mutation; it monkeypatches `request_digest()` to lie with alternating hashes. P12 proves content sensitivity separately, but no test proves that a real mutation at the send boundary is observed.

Fix: Add a test-only before-send mutator, change `messages` or `inferenceConfig` after the build snapshot, and require check 13b to reject what the fake client actually receives.

4. P18 is self-adjusting: it says “all `{len(consistency)}` artifacts” and checks only those that happened to exist. If one expected run stops writing an artifact, the assertion silently tests a smaller set and still passes.

Fix: Require `len(consistency) == 16` as well as status/certified equivalence.

5. `tool_config_problems()` still crashes on malformed nested shapes. I reproduced `AttributeError` with `toolChoice: {"tool": "wrong-shape"}` because the string receives `.get()`; the `codebook` pin entry is likewise never checked to be a dictionary.

Fix: Type-check `codebook`, `path`, `sha256`, and `toolChoice.tool` before access, with every failure converted to `InstrumentError`.

6. Direct `load_artifacts()` still does not wrap failure to parse `pins.json`: `read_pins()` executes before its validation and can raise bare `JSONDecodeError` or `OSError`. Preflight avoids this only because it parses and passes the document itself; future M4 will call the direct path.

Fix: Wrap `read_pins()` inside `load_artifacts()` and translate its read/JSON failures to `InstrumentError`.

7. `--write` reintroduces the destructive ordering that the renderer previously removed. It computes mismatch states, overwrites all three on-disk artifacts, and only then refuses because the prompt or config differed—so it does not self-certify, but it destroys the locally accepted bytes before reporting the mismatch.

Fix: Under `--write`, refuse before writing unless prompt and tool config already match their pins, then write only the manifest or byte-identical artifacts.

8. The manifest is covered by `--diff`, but not by an ordinary certification run. A stale or hand-edited manifest can therefore remain while `instrument.py` prints “certified”; this is the same evidence gap that already occurred once.

Fix: Compare the on-disk manifest with the current deterministic render on every ordinary run and refuse certification when they differ.

9. The gate still does not bind the model selection to the shared production configuration. Model IDs live only in `preflight.MODELS`, outside `build_request()` and outside `pins.json`; M4 can use another model while sharing the prompt/config builder and claiming the preflight applies.

Fix: Put region and exact model IDs in a pinned run configuration consumed by both preflight and M4, and include the selected model in check 13b’s request digest.

10. A preflight artifact does not identify the code that produced its verdict: it records artifact hashes and model IDs but no git commit, dirty state, `preflight.py`/`instrument.py` hashes, lockfile hash or runtime/package versions. A later reader cannot reconstruct which gate logic certified the run.

Fix: Record the commit, dirty-state deviation, hashes of the gate/builder/lockfile, Python version and boto3/botocore versions in every run artifact.

## `--write` / `--update-pins`

`--update-pins` remains an explicit acceptance operation and does not silently certify itself. `--write`, however, moves the problem from silent acceptance to destructive overwrite: it preserves the old digest but replaces the evidence bytes before refusing. Its safety condition must be checked before any write.

Also, `pinned_on` is hard-coded to `2026-09-02`; every future acceptance would record a false date.

Fix: Set `pinned_on` from the actual acceptance date or require it explicitly as an argument.

## Documentation overclaims

- `PROTOTYPE-PLAN.md` first says twelve gate assertions, then later says “Nine gate assertions, not thirteen.”
- The usage log still says eleven assertions and nine silent failures.
- The preflight table describes check 5 as requiring a write on call 1, contradicting the accepted warm-start path.
- Check 6 is still described as exceeding 4,096 tokens rather than each model’s own threshold.
- GUIDE-LLM E.2 says forced tool selection makes free-text answers “impossible,” while P8 deliberately tests and rejects exactly such a response.
- GUIDE-LLM still describes an explicit `no frame present` value and abstention option; the production contract instead represents this as an empty per-technology `elements` list.
- §A.7 still names retired states such as `no_frame_present` and `technology: none`.
- “Byte-identical” is inaccurate for check 13b: canonical JSON hashing establishes content equivalence while deliberately ignoring key order, not byte identity.

Fix: Make the plan, usage log and GUIDE-LLM use the current twelve conceptual checks, warm/cold semantics, per-model minima, live outcome vocabulary and “canonical content-identical” wording.

## Instrument-design exposure

The remaining examiner-facing weaknesses are acknowledged but unresolved:

- D1’s only strong performance contrast combines long definitions with 15 examples, while this instrument has none.
- The human-facing artifact required for actual prospective parity is still absent.
- Prompt language remains open despite the English production artifact already being pinned.
- Several cited claims still lack page numbers.

These are not hidden defects, but they must be decided before A2 annotation or extraction; otherwise “open” becomes a post-result design choice.

Fix: Freeze the worked-example decision, prompt language and human-facing guide before A2, and complete the citation page references before Chapter 4 relies on them.

I could verify the ordinary render, manifest diff and offline preflight: all current artifacts match and offline exits 3 honestly. The test suite reached its temporary-fixture stage, but this managed environment provides no writable temporary directory; that does not refute its read-only-checkout design, but I could not independently reproduce the claimed final count here.

VERDICT: REVISE
```

## Appendix — instrument review, round 5 critique, verbatim

```
I would run this against real credentials after one small contract fix and a clean commit. The envelope digest, mutation tests, write ordering, manifest check, model inclusion, response validation and verdict logic are now substantive rather than vacuous.

1. Check 13’s user-text clause is still weaker than its claim: it requires `PROBE_PARAGRAPH in text`, so a builder could append an unpinned instruction such as “prefer security frames” and still pass checks 13 and 13b.

Fix: Require exact equality with `f"Paragraph:\\n{PROBE_PARAGRAPH}"` and add a mutation test that appends an instruction inside that text block.

2. Provenance fields named `*_sha256` contain only 16 hex characters, not full SHA-256 digests. This is adequate for accidental identification but overstates the evidence stored.

Fix: Store all 64 hex characters, especially before producing the first credentialed artifact.

Before the first live run, ordered:

1. Close the exact user-text assertion above.
2. Commit the implementation and run from a clean tree, using the default two-model run with raw capture and a durable output artifact. The current offline record correctly reports nine dirty files; a hash identifies uncommitted code but cannot reconstruct it later.

D19 is acceptable for this preflight: the selected model is now in the certified envelope and recorded provenance. Its stated limit is real and correctly documented—M4 must import `instrument.MODELS`, with an integration test refusing any independent model mapping.

This is over-engineered for a bachelor’s prototype around the certifier rather than the instrument. In retrospect, I would have skipped:

- Check 13b’s mutation framework, `before_send`, and P11a–P11e; a complete check 13 plus a single shared builder was proportionate.
- The full cross-product of cache failure and verdict-artifact tests; representative cold, warm, malformed-response and persistence cases were enough.
- D15–D19 as thesis-facing “instrument decisions”; keep brief engineering notes in code or a review log instead.

I would not delete tested code immediately before the live run—it now works, and pruning creates fresh risk. I would delete or sharply condense D15–D19 from the eventual Chapter 4 account and stop investing in this module after the exact-text fix.

I verified that `instrument.py` certifies and offline preflight exits 3 with honest provenance and phase status. This sandbox still provides no writable temporary directory, so I could not independently execute the temporary-file portion of the 150-test suite; the relevant tests were inspected directly.

VERDICT: REVISE
```

---

*Relocated 2026-09-24 from `tools/` to `tools/docs/` without any change of content. This log is append-only, so its relative links and path citations were not edited; they were written from `tools/` and must be read relative to `tools/` (old-to-new table in `README.md` of this folder); `tools/docs/README.md` maps every old path to its new one.*
