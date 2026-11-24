# Plan: complete the pipeline to the frozen metric table

_Locked via claudex-loop — Claude (claude-fable-5-1) + Nils Wagner. Drafted 2026-09-05 on branch
`kw36` at commit `607a89e`, clean tree, after the recon recorded in
[`PIPELINE-COMPLETION-REVIEW-LOG.md`](PIPELINE-COMPLETION-REVIEW-LOG.md)._

**Read this file as the third plan in this folder, not a replacement.** `PROTOTYPE-PLAN.md` remains
the specification of every module; `CORPUS-INTEGRATION-PLAN.md` remains the specification of the
split registry. This plan fixes only what neither of them fixes: the adapted research-question set,
the order and the shape of the remaining build, and the decisions that the ten-round decision
conference and the author's amendment left nowhere in the repository.

**Editorial role clarification (added 2026-11-24).** The author's statement of extent in the final thesis records
that he wrote the basis of the text largely himself, as drafts and bullet points. Some complete
paragraphs originated in his own writing and were then comprehensively reworked by Claude.
He proofread every adopted passage. In his retrospective account, the author also describes
contributing a substantial share of the design proposals, drawing on his own reading of the
literature. The architecture was worked out together with Claude; his contributions included
setting the research scope, making methodological choices and directing subsequent development.
His manual annotation, re-coding and scope decisions are
specified below alongside the agents' drafting, implementation and review tasks. This is a
historical plan with subsequent amendments; the final thesis reports the executed study.

## Goal

By the engineering stop, one production extraction run on a pre-registered evaluation sample exists,
with its run record, and one evaluator turns that run plus the human labels into the frozen metric
table that Chapter 6 is written from — under a research-question set that keeps the thesis on its
registered topic and adds no build day beyond the single run and the single evaluator.

## 0 — Context that exists nowhere else in the repository

Recorded here because a later session cannot recover it from any file. Verified 2026-09-05: the
phrase "Direction D" occurs in no file under `knowledge/`, `tools/`, `paper/` or `scratchpad/`.

- **The decision conference.** A ten-round adversarial review (2026-09-03/04, outside the
  repository) concluded with "Direction D": register **one** research question — the pipeline and
  its agreement with human annotation — demote run-to-run stability to a subordinate diagnostic, and
  permanently close the old RQ2 (matched-language and prompt-variant conditions), RQ3 (translation
  arm with session A6), RQ4 (bias-corrected prevalence, DSL/PPI++). Its arithmetic: the remaining
  build is **12–20 focused days without M7, 17–28 with**, before annotation and before any writing;
  the eight-week clock runs to the end of October; the manuscript is empty.
- **The schedule that arithmetic produced** *(dates superseded by the amendment §5a of 2026-09-15: first coding finalised 09-21, re-code 10-05, primary table 10-07, full draft 10-14; the engineering stop is not pinned)*. Engineering stop **2026-09-25**; primary metric table
  **2026-09-30**; full draft **2026-10-09**. Registration is not yet sent; eight weeks from
  2026-09-08 ends 2026-11-03, and the author's own bound is "end of October in practice".
- **The author's amendment, which governs this plan.** Sub-research questions are **retained in
  adapted form**, not scrapped; the thesis stays close to its topic; adaptation is welcome, wholesale
  deletion is not; everything stays inside the budget above. What may **not** return: translation
  production, prompt-variant machinery, DSL, a second annotated arm.
- **One schedule, dependency-aware (review round 5).** The chain is: Step 1.1–1.2 and the runner
  and evaluator with their fake-client tests → the A1 dry run (certifies the gate, proves both
  modules) → the author's agreement to §1 and the signed specification → freeze and draw → A2 first
  coding (three sittings, ≈ 8–12 h) → executions 1..3 on A2 → the class manifest and the primary
  table. Reviewer's allowance for the remaining build after this session: ≈ 13.5 focused days
  including component tests, against 15 weekdays between 2026-09-07 and 2026-09-25; with the
  90-minute writing rule and A2 first coding that is ≈ 7.5 productive hours per weekday or weekend
  work, and it is stated as such rather than as a buffered commitment. **Dates:** engineering stop
  (code frozen, freeze and draw done) **2026-09-25**; A2 first coding finished **2026-09-24**;
  ~~**primary table 2026-09-30 — the single deadline for the frozen table**~~ *(superseded: see the addenda of §5b; the table was produced on 2026-10-13)*; route-B reliability table
  14 days after first coding ends (2026-10-08 at the latest), which lands in the 2026-10-09 draft;
  full draft 2026-10-09. *(The whole dated schedule of this paragraph is superseded by §5b and its addenda; executed: first coding finalised 2026-09-30 and 10-08, re-code and production run 2026-10-13.)* The specification's hard gate is the engineering stop, not the table date.
  Cutting the third execution saves almost no engineering and is not a schedule lever; the levers
  are annotation hours and writing hours.
- **The daily production rule.** No design review, literature sweep or new planning document begins
  on a day before 90 minutes or 1,500 net thesis characters exist. Honoured today: Chapter 2 §2.1–2.3
  and Chapter 4 §4.2–4.3 were drafted (≈ 15,500 net characters) before this plan was written.

## 1 — The adapted research-question set (AGREED by the author on 2026-09-06; proposed 2026-09-05)

Four sub-questions under the registered Leitfrage. All four are computed from **one production run
on the A2 sample** (with *k* identical repeats of that run) and **one evaluator**. Nothing here needs
a second annotated set, a translation, a prompt variant, a second model, or an estimator with an
identifying assumption. *Revised after review round 1: RQ4 now carries the aggregate-estimation
question the Leitfrage promises, the categorical layer follows the specification's L5 instead of
replacing it, and every estimand names its population, its unit and its weighting.*

**The population every A2 number describes.** The target is the **eligible frame**: the gap-free
candidate paragraphs of `RESERVED_A2` minus article A011 (225 German / 393 English at the time of
writing; recomputed and bound at the freeze, Step 1.5). A2 is a stratified probability sample of that
frame with known π_h, so every count that enters a reported figure — a true positive, a false
positive, a false negative, a prevalence numerator — is multiplied by the paragraph's design weight
1/π_h before the figure is computed. The **unweighted sample figure is printed beside it** and is
read as the balanced-benchmark figure. No figure claims to describe the 1,047-paragraph corpus or any
population beyond the frame. This replaces the DSL clause of L24 (Step 4).

| | Question | Estimand | Data | Marginal cost | Warrant |
|---|---|---|---|---|---|
| **RQ1 Design** | How is a reproducible multilingual extraction pipeline for technology–frame–evaluation relations designed, and why is each part the way it is — including the native-language routing (one English instrument, the paragraph in its original language)? | None — evidenced by artefacts: the frozen codebook, the rendered and pinned instrument, the request builder, the certified gate, the run record, the tests, the provenance chain. | Repository | 0 | `INSTRUMENT-DESIGN.md` D1–D19; P056, P058, P036/P037 |
| **RQ2 Agreement** | How well do the pipeline's labels agree with the declared human reference, per language and per label dimension? | Headline: **weighted micro-F1 over (paragraph × technology × element) tuples** on the outer-join universe (§2 Step 3), pooled and per language. Beside it, one row each: technology-detection micro-F1; per-element F1 with support; the two categorical layers — **evaluation** and **comparison target** — each as two separately defined scores (Step 3): (A) classification conditional on the parent being detected, with accuracy, per-class F1 and macro-F1 over the classes with positive support **among the matched units**, and the matched-unit coverage printed; (B) end-to-end labelled-tuple set scoring with micro-F1 and per-value F1 (L3, L5); per-row exact match as a secondary. Uncertainty: percentile intervals from the article-resampling scheme fixed in Step 3. | A2 gold (the declared reference: the author's pass 2, or the adjudicated A4 labels if a second coder exists) × execution 1 | 0 beyond the run | L1–L5, L21, L24 |
| **RQ3 Execution stability** | How far do identical repeated executions of the pipeline differ from one another? | For each label dimension *d*, each comparison unit *u* and each pair of executions (*r* < *s*): the indicator that *d*'s canonical value at *u* differs between *r* and *s*, with "relation absent" as a value and "execution failed" excluded from the pair and counted separately. Averaged over pairs, then over units → an **absolute change rate** per dimension and language, with article-resampled intervals; the paragraph-level "any change" rate and the per-execution failure rate alongside. Descriptive; no causal reading; the ordering across dimensions is not a finding. | A2 × executions 1..*k*, *k* = 3 | 2 × 120 extra calls; ≈ zero annotation; the runner is loop-shaped | **Research value, stated narrowly (review round 5):** RQ3 measures the reproducibility of the extraction procedure itself — whether repeated use of *this* instrument under the *recorded* configuration changes its outputs — which a validation of a pipeline must establish before agreement figures can be read as properties of the instrument rather than of one execution. It says nothing about robustness to prompt changes, provider updates or deployment over time, and the text bounds it so. Warrant: L6 (P061: a stability coefficient is a distinct job), L7 (P007: *k* repeats at temperature 0, prints no statistic), L8 (P022); *k* = 3 is by fiat and is labelled so (L25) |
| **RQ4 Aggregate estimation error, and the limits of validation** | How far do the aggregate prevalences the pipeline would report differ from those the human reference gives on the same sample, and what does the corpus's size and label sparsity leave unmeasurable? | **The central answer is (a); (b)–(e) qualify it and are presented as diagnostics and, for (d), as a methodological illustration — not as findings of equal rank (review round 5).** (a) **Design-weighted, reference-relative prevalence displacement for the eligible frame**, paragraph-level presence: for a class *c* (a technology; an element; a (frame, evaluation-value) pair) and paragraph *i*, *y_i* = 1 if any gold relation of *i* carries *c*, *ŷ_i* likewise for execution 1, both 0 on a paragraph with no relations; displacement = 100 × Σ *w_i* (*ŷ_i* − *y_i*) / Σ *w_i* over **all** manifest paragraphs of the language, in percentage points, with article-resampled intervals. Occurrence counts (the same element under several technologies) are not prevalences and are not reported as such. Because Σ *w_i* = *N* under fixed stratum allocations, this is an inverse-probability **estimate** of the frame's mean prediction-minus-reference difference — an estimator for the finite eligible frame with **no bias-correction estimator** (DSL, PPI++) applied; the unweighted sample discrepancy is printed beside it. It is a signed, reference-relative quantity: false-positive and false-negative contributions can cancel, so a small displacement establishes neither accurate individual labels nor agreement with an independently known truth, and the resampling interval carries no uncertainty from the human reference itself. Chapter 6 says so in the caption. (b) The per-language **class-support table** with support in rows *and* in articles. (c) The **reportability rule**: a per-class cell is printed when its gold support in that language reaches the author's blind threshold *s*, else it prints `n<s` with the support — suppression, distinct from an undefined score, and separate from macro membership. (d) The **macro-sign illustration**, labelled a constructed example: for *K* classes, *n_L* supported in language *L*, an instrument scoring F1 = *f* on every supported class and *z* on every unsupported one reports macro_L = (*n_L f* + (*K* − *n_L*) *z*) / *K*, so the between-language gap is (*n_de* − *n_en*)(*f* − *z*) / *K* — it changes sign between *z* = 0 and *z* = 1 **whenever the supported-class counts differ**, and is zero under the positive-support convention and zero when the counts match (A1: *n* = 10 vs 6, *f* = 0.6 gives +0.141 and −0.094); computed on A1 and on the actual A2 supports, reporting zero where A2's counts coincide. (e) The **resample-absence rate** of each class, measured from the resampling scheme actually used, not from a closed form. | A2 gold and execution 1; A1 for (d) | 0 calls | L21 (silent → author convention, stated as one); L24; P061 p. 554; P085 for why prevalence error is not read off accuracy |

**What this retains from the topic and what it closes.** The thesis remains "design and validation of
a multilingual pipeline". Multilinguality is carried by per-language reporting on a corpus that is
German and English by construction, not by a translation condition. Stability, which Direction D
would have demoted to a diagnostic, is a sub-question again because it costs nothing and because
P061 names it as a distinct quantity. The aggregate question survives as a measured displacement on
the validated sample rather than as a corrected population estimate. The following are **closed**:
the matched-language and prompt-scaffolding conditions (old RQ2), the translation arm and A6 (old
RQ3), DSL/PPI++ prevalence correction (old RQ4), the arm-4 development-discordance trigger, and —
*removed from the committed scope after review* — the Sonnet-vs-Opus comparison, which is a second
extraction arm with its own certification, pairing and reporting cost. It may return only by a dated
amendment before the hard gate that names its budget and its activation rule.

**Author decisions this set does not make**, flagged as open in the specification: the blind
minimum-support threshold *s*; the second coder; the registered title and Leitfrage; the hard-gate
date and the cut order.

**Amendment 2026-09-06 — production-model selection (W1; revised after Codex round 1).** The author
enabled Claude Sonnet 5 and Claude Opus 5 on Bedrock and asked for a benchmark. Not the closed
comparison: a one-time **selection** of the single production model among Sonnet 4.6, Opus 4.6,
Sonnet 5 and Opus 5 on the A1 development split (k = 3 each, one runner version, one window), under
the rule fixed in the specification §4 (analysis W1) before any scored run — eligibility by the gate
and a failure-free run, S = mean of the three execution-specific tuple micro-F1 values, competitive set
within δ = 0.02, cheapest member selected, fixed order on exact ties, production stop if nothing is
eligible. **Two things gate it:** runtime access to the 5-series, which every invocation path denied on
2026-09-06 (deadline 2026-09-15, after which an inaccessible candidate is excluded), and the author's
choice between W1-a (pinned request kept; the 5-series, which rejects `temperature`, is then
incompatible by construction) and W1-b (common configuration for all four: no sampling parameter,
thinking explicitly disabled; an instrument change with a B.3 consequence). The selected model is
pinned before the A2 sheet exists; the others are not run on A2. Budget ≈ 4 × 96 calls, under USD 10;
no build day beyond the two model keys, their cache minimums and prices, plus the runner and gate
hardening the review produced (one tool-use block, usage completeness, production boundary).

## 2 — Approach: what gets built, in this order

Every step names the files it touches. New files are created only where `tools/README.md` or
`CORPUS-INTEGRATION-PLAN.md` §"Explicitly out of scope" already lists the artefact as owed.

### Step 0 — One validator, one request builder (no drift between gate and runner)

1. Move `preflight.validate_tool_use()` into `instrument.py` as `validate_tool_use(block, art, doc,
   subtypes) -> tuple[bool, str]` — tool name, then the pinned JSON Schema, then
   `validate_response()`, in that order. `preflight.py` imports it. Behaviour unchanged; the
   existing tests must pass unmodified, and one new test asserts that `preflight` has no validator
   of its own (`not hasattr(preflight, "validate_tool_use")` or that it *is* the instrument's).
2. `build_request()` is unchanged and remains the only request constructor. The runner (Step 2) is
   tested with a fake client to assert that every envelope it sends has the digest of
   `build_request()`'s output for that paragraph — the check preflight already performs on itself.

### Step 1 — The A2 prerequisites and the draw (nothing here is annotation)

In the order `tools/README.md` gives:

1. **Session manifests and `text_sha256` in both passes.** `build_sheets.py` writes, beside every
   sheet it generates, a `labels/<session>_manifest.json` (tracked; IDs and hashes, no text):
   session, sheet kind, `sheet_schema: 2`, generated-at, codebook version and sha, registry head
   hash, generator code sha, and one record per paragraph — `paragraph_id`, `article_id`,
   `text_sha256`, `split`, `lang`, `outlet`. `SHEET2_COLUMNS` gains `text_sha256`, and
   `expand_sheet2.py` **propagates it from the pass-1 row** (its `CONTEXT` list is the actual
   producer of pass-2 sheets and must carry the hash, otherwise the new column is blank).
   `finalize.py` validates by `sheet_schema`: for schema 2 it **recomputes** `sha256(paragraph_text)`
   from the sheet it is reading (the sheet carries the text; the label file will not), requires the
   recomputed hash to equal both the row's hash cell and the session manifest, and — for a pass-2
   sheet — requires the set of `(paragraph_id, technology)` keys to equal the set implied by the
   finalized pass-1 ticks of the same session, or the write is refused; schema 1 (the six committed
   files) keeps validating unchanged, and the reconstruction step below runs the same key
   comparison once on A1 and records any discrepancy. For the six existing sessions, manifests are reconstructed from
   the label files and `ALL_paragraphs.csv` and marked `"reconstructed": true` with the evidence
   named. **Publication boundary for human notes (review round 5):** a coder's `note` and
   `comparison_note` can quote the paragraph, and the legacy finalizer committed them (up to 600
   characters). For schema-2 sessions the finalizer writes the free text to
   `labels/private/<session>_notes.csv` (gitignored) and the committed label file carries only a
   `[private: n chars]` marker in those cells; the note-required rules are checked on the sheet
   before the strip, so the marker proves a note existed. The six frozen legacy files are not
   edited; their exposure is recorded as an erratum in `labels/README.md` for the author to
   decide on before publication.
2. **Corpus collection cutoff and archive.** The cutoff is prospective and is the A2 freeze event:
   no paragraph identity registered after `a2_frozen` joins A2, and the registry already refuses
   that. The archive is `build_sheets.py --archive`: a `tar.gz` of the gitignored article sources
   (transcripts, renders and the author's prints) **plus** `manifest.csv`, `split-assignment.json`,
   the **bytes** of `build_sheets.py`, `split_registry.py`, `expand_sheet2.py` and `finalize.py`,
   the git commit with its dirty flag, and the interpreter fingerprint, into **`tools/data/archive/`**
   (gitignored; `tools/data/` is the licensed-text store of `PROTOTYPE-PLAN.md` §A.11), and a
   tracked **`tools/annotation/archive-manifest.json`** with the tarball's sha256, each file's
   sha256 and the corpus-version checksums it binds. *Corrected after review rounds 1 and 5:*
   nothing is written under `knowledge/raw/`, which the standing rules declare immutable.
   **Stated honestly:** the archive preserves the *current* realisation of the sources, which the
   registry accepted after 45 binding-drift records; the bytes corpus version v2 was originally
   built from are not recoverable and the manifest says so. Copying the archive off the machine is
   the author's act and is listed as such.
3. **Second coder** — author decision. The draw does not wait for it: the sample manifest marks a
   random 40-paragraph reliability subsample (20 de / 20 en, own seed) at draw time, so the decision
   can be taken afterwards without re-drawing. If no second coder is confirmed by the hard-gate
   date, L22 fires: a 14-day-washout blind re-code, named intra-coder temporal stability.
4. **A1b withdrawal.** Under the adapted set the only whitelisted development branch — the arm-4
   discordance trigger — is closed, so no development set beyond A1 is needed. **A1b is fixed as the
   empty set**, recorded in the specification and the log before any A2 draw, and the
   `RESERVED_A2 → A1b` transition is thereby closed. No registry event is required for an empty set;
   the freeze event that follows makes the transition impossible.
5. **The frame manifest, then the A2 freeze.** `build_sheets.py --freeze-a2` first writes
   `labels/A2_frame_manifest.json` (tracked): the eligibility rules in words, every eligible
   identity triple with `lang`, `outlet`, `article_id`, the exposure class, the per-stratum N_h,
   the generator code sha and `pre_freeze_head` — the registry head hash **before** the freeze.
   The manifest's digest is the sha256 of its canonical JSON **with the `digest` field excluded**,
   and is then written into that field. It then calls
   `SplitRegistry.freeze_a2(eligible_checksum_of(current_splits()), audit_ref=<manifest digest>)`.
   The registry's checksum freezes reserve *membership*; the frame manifest freezes the *filtered
   frame and its axes*, which the checksum alone does not cover. Terminal. `draw_a2.py` verifies
   that the registry head **is** an `a2_frozen` event whose `audit_ref` equals the manifest digest
   and whose predecessor hash equals `pre_freeze_head`; any event after the freeze, including a
   drift acceptance, makes it refuse.
6. **The draw, pre-registered against the frame manifest.** `tools/annotation/draw_a2.py` (new;
   owed at README item 6). Frame: as bound in the frame manifest — `RESERVED_A2` ∩ candidate ∩
   gap-free ∩ article ≠ A011 (`A011_p02` is quoted in the prompt). Strata: language × outlet.
   Target **60 de / 60 en**. Allocation: proportional to N_h within each language, capped at
   **24 paragraphs per outlet** (40 %), remainder redistributed proportionally among uncapped
   strata, integers by the **largest-remainder method** with exact fractions, remainder seats going to the
   largest fractional part, ties broken by larger N_h and then by outlet name ascending; strata
   are traversed in sorted (language, outlet) order with one seeded generator. Feasibility is recomputed
   from the manifest and printed before anything is written; on the frame as it stands the review's
   independent recount gives capacity Σ min(N_h, 24) = 105 for English (Al Jazeera 22, Economist
   108, Guardian 68, NYT 184, Washington Post 11) and 200 for German, so the cap is feasible with
   slack. **The cap is part of the registered design and is never raised silently** (post-build
   inspection #16): an infeasible frame makes the draw refuse and name the smallest feasible integer;
   only an explicit `--cap N --amendment '<dated reason>'` changes it, and the amendment is recorded in
   the sample manifest. Within a stratum: identities sorted by digest, then `random.Random(seed).sample`.
   Selection probabilities π_h = n_h / N_h and weights 1/π_h are written per paragraph. Exposure
   flag per paragraph: `direct_exemplar_article` / `sibling_calibration_article` / `clean_article`.
   The reliability subsample: 20 de / 20 en drawn from the sample with a second fixed seed,
   flagged `reliability_subsample`. Output: `labels/A2_sample_manifest.json` (tracked; IDs, digests,
   strata, N_h, n_h, π, weights, flags, both seeds, the frame-manifest sha, the code sha) — then the
   A2 sheet-1 via `build_sheets.py --from-manifest`. The manifest is committed before the sheet is
   opened, and the amended specification is signed before A2 membership is inspected.

### Step 2 — M4, the production runner

`tools/pipeline/run_extract.py` (new; owed as M4 in `PROTOTYPE-PLAN.md` §A.11).

- **Input:** a sample manifest (Step 1.6) or, for the development dry run, a session manifest;
  paragraph text is read from `annotation/sheets/ALL_paragraphs.csv` (gitignored) and each
  paragraph's `text_sha256` is checked against the manifest before it is sent. The runner does
  **not** import `split_registry` (it would inherit the interpreter binding) — it reads the CSV the
  registry already validated.
- **Gate, in process:** before the first extraction call the runner invokes `preflight.run()` with
  its own resolved model and the pinned artefacts, persists the preflight artefact (private: it holds
  raw responses and is already gitignored as `tools/runs/preflight_*.json`), and refuses to extract
  unless that artefact is `certified`. The run record links the artefact's path and sha256 and
  copies its check verdicts as codes (`id`, `ok`, `skipped`) without detail strings. `--dev` allows
  an uncertified gate and stamps the run `reportable: false`.
- **Request:** `instrument.load_artifacts()` once, `instrument.build_request()` per paragraph, the
  model from `instrument.MODELS[--model]`, the envelope digest recorded at build and at send.
- **Executions:** `--repeats k` schedules executions 1..*k* per paragraph; `run_index` is the
  **scheduled** index and never renumbers. Execution 1 is the primary prediction whatever its outcome.
- **Durable records, written in this order:** (i) an *attempt-started* line (run_id, run_index,
  paragraph_id, attempt_no, request digest, timestamp) **before** the call; (ii) an *attempt-finished*
  line with usage, stop reason, latency, the validation verdict and, on success, the validated
  structured payload **without** `note` / `comparison_note`; (iii) once per paragraph × run_index, a
  *terminal* record with outcome ∈ {`technologies_present`, `off_topic`, `parse_failed`, `refused`,
  `error`} and the attempt it resolved from. All three go to `tools/runs/<run_id>_calls.jsonl`
  (committed for reported runs). Free text and the verbatim raw response go to
  `tools/runs/raw/<run_id>.jsonl`, gitignored (rule added in this step), because model notes may
  quote licensed text.
- **Response handling** per `PROTOTYPE-PLAN.md` §A.7: `stopReason` first; then
  `instrument.validate_tool_use()`. Parse or contract failure → one retry with identical parameters
  (attempt_no 2), then `parse_failed`; `refusal` counted, not retried; `ThrottlingException` →
  exponential backoff with a retry cap, counted in the run record, not an attrition row; any other
  exception after the cap → `error`. Nothing is imputed.
- **Write order and recovery at every boundary.** Per attempt: (1) attempt-started (public);
  (2) the verbatim raw response (private file); (3) attempt-finished (public, derived from 2);
  (4) terminal (public). On resume: a terminal record → skip; otherwise every recovered attempt is **replayed through the
  same retry-state machine that normal execution uses** — a finished record without terminal is fed
  to that machine, a raw record without finished is first rebuilt into a finished record and then
  fed to it, and a started record with neither is of unknown completion, **re-sent**, and counted as
  `unknown_completion_attempts`. A terminal is created only where the machine would have created one
  uninterrupted: a valid response, an `off_topic` outcome, a refusal, or an exhausted retry budget;
  a recovered first parse failure therefore triggers the prescribed second attempt rather than a
  terminal. A torn last line in any file is discarded and counted as `torn_records`. Public and
  private files are flushed and fsynced after every record, so "durable" means on disk. The resume fingerprint is the sha256 of the complete resolved execution contract:
  manifest sha, prompt sha, tool-config sha, model id, region, inference config, `max_tokens`, *k*,
  the retry-policy constant, the runner's code sha and `instrument.py`'s sha (the shared request
  builder and validator); a mismatch refuses to resume under that
  `run_id`. Predictions are reconstructed from terminal records, never from memory.
- **Cost accounting:** `cost_observed` is derived from the token usage of finished attempts only;
  `billing_complete` is `false` whenever `unknown_completion_attempts` > 0, and no complete total is
  claimed in that case. Reconciliation against provider billing, if available, is a separate note.
- **Public validation results are codes, not text.** The public files carry a verdict code per
  attempt (`ok`, `no_tool_use`, `truncated`, `refusal`, `schema_invalid`, `contract_invalid`,
  `throttled`, `error`) and, for contract failures, the rule identifier; the validator's message
  strings, which can echo model output, stay in the private raw file.
- **Predictions:** `tools/runs/<run_id>_predictions.csv`, keyed by
  `(run_id, run_index, paragraph_id, technology)`, one row per relation of a `technologies_present`
  terminal record, carrying only enum, boolean and identifier fields — the element flags, the
  per-frame evaluations, `comparison_target`, the subtype — in the pass-2 column vocabulary so the
  evaluator joins on `(paragraph_id, technology)` per execution. A companion
  `tools/runs/<run_id>_terminal.csv` holds one row per paragraph × run_index with its outcome, so
  paragraph coverage is established by construction.
- **Run record** `tools/runs/<run_id>.json`: §A.8 fields (git commit and dirty flag, versions,
  codebook / prompt / tool-config hashes and the prompt's *path* rather than its text — the rendered
  prompt is itself a tracked artefact, and its one quoted corpus phrase (`A011_p02`) is a recorded
  instrument decision (D8, GUIDE-LLM C.1) whose article is excluded from the frame; the licence
  boundary governs article text as corpus data, not that published phrase, and Chapter 5 says so —
  model, region, inference config verbatim, manifest sha,
  totals, retries, orphaned attempts, wall clock, cost against a named price table) plus the linked
  preflight artefact.
- **Tests (fake client):** every sent envelope has the digest of `build_request()`'s output; a
  refusal, a truncated object, a schema-invalid object and a contract-invalid object each produce
  the right terminal outcome and no prediction row; a throttle is retried and counted; *k* repeats
  produce exactly *k* terminal records per paragraph with variable relation counts; a crash injected
  between attempt-started and attempt-finished is resumed by re-sending and counted as orphaned; a
  payload whose `note` quotes a paragraph leaves no trace in any committed output file (predictions,
  terminal, calls, run record) — the public serialisation is an allowlist of fields, and the test
  greps the quotation; a crash injected after a *valid* attempt-finished and before terminal is recovered without a
  further call; a crash injected after a *malformed* first attempt is recovered by issuing the
  prescribed second attempt, not a terminal; a raw record without a finished record is rebuilt and
  replayed (tested separately from unknown completion); a run resumed under a changed inference
  config is refused.

### Step 3 — M6-lite, the evaluator

`tools/pipeline/evaluate.py` (new; owed as M6 in §A.11). Inputs: the sample manifest, the gold
pass-1 and pass-2 label files with their session manifests, the terminal and prediction files of one
run, the author's threshold *s*. Every rule below is fixed here, before any A2 prediction exists.

- **Coverage first.** Every manifest paragraph must have a gold pass-1 row (off-topic or with
  technologies) and exactly one terminal record per execution, or the evaluator refuses. The
  attrition and abstention table (per language, per execution): gold off-topic; predicted off-topic;
  `parse_failed`; `refused`; `error`; technologies present on both sides.
- **Universe:** the outer join of gold and predicted `(paragraph × technology)` rows for execution
  1 over **every** manifest paragraph; a paragraph that is off-topic in gold has an empty gold
  relation set, so any predicted row on it is an invention and every predicted element a false
  positive. A gold row with no predicted row is a **model
  omission**: every gold element on it is a false negative, and its evaluation and comparison-target
  tuples are false negatives in the end-to-end scores below. A predicted row with no gold row is a **model
  invention**: every predicted element is a false positive. A test asserts both on fixtures. A
  paragraph whose execution 1 terminated as `parse_failed`, `refused` or `error` has **no predicted
  rows** and therefore contributes only omissions — the primary figure absorbs failures as errors,
  and the abstention table shows how many.
- **Weights:** every TP, FP, FN and prevalence count is multiplied by the paragraph's design weight
  before a figure is computed; the unweighted figure is printed beside it.
- **Gold completeness:** before scoring, the evaluator requires that the set of
  `(paragraph, technology)` keys in the reference pass-2 file equals the set implied by the
  reference pass-1 ticks, and that every manifest paragraph has a pass-1 row; a mismatch refuses.
- **The categorical layers (L3, L5), as two separate scores with their formulas.**
  *(A) Intersection-conditional classification.* Units: gold-fired (row, frame) pairs whose row and
  frame were also predicted. Over those units: accuracy = correct / units; per-class F1 for each
  evaluation value from the confusion counts among the units; macro-F1 over the values with positive
  gold support **among the units**. Missing rows and frames are excluded here and their count is
  printed with the table; the caption states that the conditioning is post hoc on the intersection
  — a single stateless call cannot receive gold frames as SemEval's second phase did.
  *(B) End-to-end labelled tuples.* Tuples (paragraph, technology, frame, evaluation) as sets: TP if
  the identical tuple is in gold and prediction, FP for a predicted tuple not in gold, FN for a gold
  tuple not predicted; micro-F1 over all tuples, and per-value F1 over the tuples carrying that
  value. A missing row or frame is a false negative here, an invented one a false positive. The same
  two scores apply to `comparison_target` on (paragraph, technology, target) tuples. Neither score is
  called "the" categorical result; both are printed.
- **Class manifest:** `tools/runs/<run_id>_class_manifest.json`, written once from the **final**
  reference labels (Step 1.3 decides what those are): for each dimension **and each table's own
  denominator population** (the outer-join universe for detection; the intersection units for score
  A; the tuple sets for score B), three sets with their supports in rows and in articles — per
  language, pooled, and common-support. Point estimates use these sets; every class in them is
  defined in the full sample by construction. **Inside a resample** a class with zero gold support
  and zero predictions is undefined; the resampled statistic is therefore the **available-class
  macro-F1**, named exactly that, its interval is labelled as an interval of that statistic and not
  of a fixed-class one, and the distribution of the defined-class count over resamples is printed
  beside it. A class with zero gold support and positive predictions has F1 = 0 and stays in. False
  positives always enter the micro counts whatever the class set.
- **Resampling, fixed:** *B* = 2,000 resamples (a choice; L25 is silent). Per-language figures
  resample the sampled **articles of that language** with replacement, *c_L* draws from *c_L*
  articles; pooled figures resample all sampled articles globally. Not within outlet — several
  outlets contribute a single article. An article drawn *m* times contributes its rows with their
  weights multiplied by *m*; identities are never duplicated, so the duplicate-collapsing rule of L1
  never interacts with multiplicity. Intervals are 2.5–97.5 percentile, reported as descriptive
  resampling intervals; no finite-population coverage guarantee is claimed (L24).
- **Reportability:** a per-class cell prints its value when gold support in that language reaches *s*
  rows, else `n<s` with the support; the article count is printed beside every support. Suppression
  is a reporting rule and does not remove a class from the positive-support macro set.
- **Degenerate inputs yield documented `undefined`, never a fabricated number:** an empty
  intersection for score A, a macro set with no defined class, a resample with no unit at a level,
  a reliability item without variation. Each table's caption names the rule.
- **Stability (RQ3), universe and formula.** Successful executions of a paragraph are those with
  outcome `technologies_present` or `off_topic`; a paragraph with fewer than two successful
  executions has no pairs and is counted in `paragraphs_without_pairs`. Paragraph level: the
  technology set (sorted) per execution; the change indicator per pair. Relation level: the unit
  universe of a paragraph is the **union** of technologies over its successful executions; for each
  unit and pair, the element set (sorted), the evaluation map keyed by frame, the comparison target
  and the subtype are compared, with "relation absent" as a value. A paragraph whose union is
  **empty** (every successful execution `off_topic`) has a technology-set change rate of zero and
  **no relation-level score**: it is not applicable there, its weight is excluded from the
  relation-level means, and its count is reported as coverage. Per paragraph with units, the rate is
  the mean over its units and pairs; across paragraphs, the design-weighted mean over the paragraphs
  to which the level applies; then article resampling. Reported with the valid-pair coverage and
  named as conditional on successful executions; failed executions enter the failure rate only.
- **Prevalence displacement (RQ4a):** exactly as defined in §1 — paragraph-level presence
  indicators, both zero on a paragraph without relations, displacement = 100 × Σ *w_i* (*ŷ_i* −
  *y_i*) / Σ *w_i* over **all** manifest paragraphs of the language, with article-resampled
  intervals.
- **Reporting eligibility:** the evaluator takes the run record as input, verifies the sha256 of
  the predictions, terminal and calls files against the digests the run record stores, and refuses
  to write the reporting tables unless `reportable: true`; `--dev` writes DEVELOPMENT-stamped tables
  to `tools/runs/tables/dev/` only.
- **Human reliability (both routes), on aligned units.** Input: both coders' pass-1 **and** pass-2
  label files over the same 40 reliability paragraphs, either the second coder's and the author's
  (route A) or the author's original and 14-day-washout re-code (route B); both coders' pass-1
  coverage of all 40 paragraphs is required. Three levels, each with its own unit and mask, fixed
  here: (i) **technology detection** — per technology, the binary tick per paragraph, rated by both
  coders for every paragraph, so a technology one coder ticks and the other does not is a
  disagreement and never a missing rating; (ii) **elements** — per element, the binary flag on
  (paragraph × technology) rows that **both** coders created, with the number of rows only one coder
  created printed as non-aligned coverage (their disagreement is already counted at level i);
  (iii) **evaluation and comparison target** — per frame, the categorical label on aligned rows where
  **both** coders fired that frame (the applicability mask), with the mask's coverage printed; the
  comparison target on all aligned rows. Per item: nominal Krippendorff's α, raw agreement, support in
  rows and articles; an item with zero variation in both coders' ratings has undefined α and is
  reported as undefined, not as 1 or 0. Route A is named inter-coder reliability, route B intra-coder
  temporal stability (L22). *Final
  reference merge:* under route A the adjudicated A4 labels — pass-1 technology decisions and pass-2
  rows alike — **replace** the author's rows for those 40 paragraphs and the merged file is
  finalised as its own session; under route B the reference stays the author's original A2 labels
  and the re-code informs nothing but the reliability table.
- **Clean-domain sensitivity (the specification's own requirement):** the primary table recomputed on
  `clean_article` rows only, with the remaining *n*, articles, outlets and class coverage printed —
  presented as a sensitivity, not as an isolated contamination effect.
- **Tables:** (1) primary metric table; (2) per-class with support; (3) categorical layers under
  both masks; (4) stability; (5) prevalence displacement; (6) limits — supports, reportability, the
  macro-sign illustration, measured resample-absence rates, cluster counts; (7) clean-domain
  sensitivity; (8) attrition and abstention; (9) human reliability. CSV and `booktabs` LaTeX to `tools/runs/tables/`.
- **The development dry run — before the draw, not after.** The runner and evaluator are exercised
  end to end on A1 with *k* = 3, using the A1 session manifest of Step 1.1 restricted to the 32
  paragraphs whose registry split is A1 (the 13 pass-2 rows from three A0b-split paragraphs are
  excluded from the dry-run gold and this is stated). That run is (a) the first credentialed
  production call against the pinned artefacts, certified by the in-process gate, (b) the
  engineering proof of both modules, and (c) **not** a development analysis: under the firewall its
  numbers may inform no instrument, prompt, model, sampling or metric choice, and this plan says so
  before the run.

### Step 4 — Documentation in the same turn as the change

`tools/README.md` current-state table and the stale "First milestone" line, plus **the licence
paragraph**, which must name its two exceptions instead of claiming none: the one quoted phrase
from `A011_p02` in the tracked prompt (a recorded instrument decision, D8/C.1, its article excluded
from the frame) and the coder's free-text notes in the six legacy label files; the specification
(§2 RQ table, §3 replaced by RQ3's estimand, L3/L5 restated as the two scores A and B with A's
class set conditional on the matched units, L23 struck as inoperative, L22 given its activation deadline, L24's DSL clause replaced by design-weighted counts,
the target population, the A1b decision, the four open author decisions); `thesis-scope-decision.md` §"Four
nested sub-questions"; `llm-usage-log.md` and `GUIDE-LLM.tex` — **every affected answer cell**, not a short list
(review round 5): B.1 (Opus is no longer a comparison model in the committed scope; the stage-1
filter is out of scope), B.3, B.4, C.1 (the prompt-language ablation is closed, not open; the
quoted phrase is the named exception), D.1 (the corpus is author-assembled, no database licence
governs it, and the author's authorisation to send it to the provider stands — the stale
"clarification pending" gate must not survive), E.1, E.2, F.1 (what is committed: run records,
call records without free text, terminal and prediction files, sample and class manifests; what is
not: raw responses, notes, article text), and the cost/volume item (the 60,000-paragraph filter
and 6,000-call experiment estimates are replaced by the actual A1 and A2 call counts once run);
planned functionality stays unticked and the human-annotation declarations stay author-owned.
`wiki/log.md`. **And a manuscript claim audit** on the drafted chapters, with these replacements:
translation sensitivity is a motivation and an untested limitation, not the premise of a matched
experiment (Ch. 2); frames can be *regrouped* from element flags for exploration only, because the
evaluations were coded conditional on the frozen frames, so reported relations keep the frozen
mapping (Ch. 2); the off-topic / unframed distinction describes the **eligible candidate frame**,
not the corpus (Ch. 4, 5); speaker is recorded as note-level context and **not** reported as a
distribution (Ch. 2); raw responses are persisted privately *before* validation and the retry
policy is outcome-specific — refusals are not retried (Ch. 4); the thesis is a resource-constrained
validation case study, not a solution to the aggregate-correction gap (Ch. 3); the identity hash is
over the paragraph text as segmented, and the containment check's normalisation is a different
operation (Ch. 5).

## 3 — Key decisions and trade-offs

1. **Stability is a sub-question, not a diagnostic** — the author's amendment. Cost: two extra
   passes over 120 paragraphs. Risk: a reader treats the change rate as a validity claim; the text
   says it is not.
2. **A1b is the empty set.** Alternative: withdraw ~30 paragraphs for prompt tuning. Rejected: the
   firewall forbids prompt revision after the specification, so a tuning set has no permitted use.
3. **Execution 1 is the primary prediction; no majority vote.** Alternative: majority over *k*.
   Rejected: it evaluates a different pipeline than the one Chapter 4 describes. Execution 1 is the
   scheduled index, so a failed first execution is an error in the primary figure, not a
   replacement.
4. **The target population is the eligible frame; figures are design-weighted, with the unweighted
   sample figure beside them.** Alternative: unweighted only. Rejected: the draw is stratified with
   a cap, so unweighted counts describe a benchmark nobody sampled; weighting is what the recorded
   π_h are for.
5. **Paragraph-level stratified sampling with article resampling at inference**, not article
   sampling. Alternative: sample articles, then paragraphs. Rejected: with ≈ 67 eligible articles a
   two-stage draw would put fewer than two paragraphs per article in the sample and make the
   per-outlet cap meaningless. Resampling is per language for per-language figures and global for
   pooled ones, never within outlet.
6. **The per-outlet cap is 24 (40 %), integer, largest-remainder allocation, recomputed on the
   bound frame.** Alternative: the smallest feasible cap. Rejected: minimal caps have zero slack.
7. **Free text and raw responses are gitignored; the committed serialisation is an allowlist of
   enum, boolean and identifier fields.** Alternative: commit everything as §A.8 reads. Rejected:
   model notes can quote paragraph text and the licence boundary is a directory boundary. The
   rendered prompt is referenced by path and hash, not copied into run records.
8. **The runner reads `ALL_paragraphs.csv`, not the registry.** Alternative: import
   `split_registry`. Rejected: the registry binds the interpreter (Python 3.14 Unicode data); the
   pipeline runs in the `uv` environment (3.13) and would trip the drift refusal by design.
9. **The gate runs in process before every reported run.** Alternative: point at the newest
   certified artefact. Rejected: it could certify another configuration.
10. **The model comparison is out of the committed scope.** Alternative: keep it as a flag.
    Rejected: it is a second extraction arm with its own certification, pairing and reporting cost;
    it returns only by a dated amendment with a budget. *Amendment 2026-09-06:* what returned is not the comparison but the selection W1 of §1, with its budget and its activation rule in the specification §4.
11. **The macro layer is subordinate everywhere.** Headline micro; macro only over the frozen
    positive-support set, never compared across languages over full inventories (L21 plus the
    sign-flip illustration).
12. **Reportability suppression is separate from macro membership and from undefinedness.** Three
    distinct states in the tables: a value, `n<s` (suppressed by the blind rule), and `undefined`
    (zero support and zero predictions).

## 4 — Toolchain

Skill inventory scan (2026-09-05): Claude side `~/.claude/skills/` holds `amazon-bedrock` and
`aws-sdk-python-usage`; the Codex side `~/.agents/skills/` is empty. The build of Step 2 loads
`aws-sdk-python-usage` for the boto3 client configuration (retry mode, timeouts) and
`amazon-bedrock` for Converse error handling and throttling diagnosis. No other installed skill
matches the domain. Reviewer: Codex CLI 0.153.4, config model `gpt-6-astra`, reasoning effort
raised to `xhigh` on the command line, read-only sandbox every round.

## 5 — Assumptions (confirmed ledger, with sources)

1. Scope authority is `knowledge/wiki/syntheses/thesis-scope-decision.md`; `EXPOSE.md` is superseded
   — brief; page revision notes of 2026-09-02.
2. Corpus and split counts as in `tools/README.md` §"Current state" — re-measured 2026-09-05 with
   `build_sheets.py --report` under Python 3.14; all match.
3. Codebook v1.0 frozen, 17 elements, 9 frames, 7 technologies, 15 comparison targets, 11 exclusion
   principles — read from the YAML.
4. Instrument pinned; 156 tests pass; registry live suite 44 pass — run 2026-09-05.
5. A1 pass 2: 53 rows, 29 paragraphs, 3 articles; 13 rows belong to 3 paragraphs whose registry
   split is A0b — measured; harmless for reporting, recorded.
6. The class-set finding and its arithmetic — recomputed from `A1_pass2_v1.0.csv`; exact.
7. No production extraction call has ever been made; the only preflight artefact is uncertified —
   `tools/runs/`.
8. Production models and request shape are those in `instrument.MODELS` and `build_request()`.
9. The pipeline environment is `.venv` (Python 3.13, `uv.lock`); the annotation tooling runs on
   Homebrew Python 3.14 — `pyproject.toml`, registry drift check.
10. `paper/` is writable in this session because the author asked for chapter text; the wiki agent's
    restriction does not bind the thesis author's own request.

## 5a — Amendment 2026-09-15: supervisor decisions, re-planned calendar, optional robustness analyses

*Visualised (2026-09-16) as one zoomable page: `knowledge/blueprint/build_blueprint.py` generates
`thesis-blueprint.tex` → `build/thesis-blueprint.pdf` (calendar swimlanes plus reference panels). The plan is the
authority; the blueprint pictures it and is regenerated when this section changes.*

*Restored 2026-09-24 from commit a017a42 after the file had been made local-only on 2026-09-19 and was found
missing from the working tree on 2026-09-24; the only content lost was this pointer paragraph, re-added here.*

**Source.** Kerstin Forster's reply of 2026-09-10 to the Loom update, and her Factiva e-mail of the same
night ([correspondence](../../knowledge/raw/correspondence/2026-09-08_loom-update_forster-thread.md),
[Factiva thread](../../knowledge/raw/correspondence/2026-08-04_factiva-request_toetzke-thread.md)).

**What is now settled by the supervisor.**

1. The hand-built corpus and the frozen design stand. No redraw of the frame, no extension of the corpus
   from other sources; the frozen validation design is kept. Malte Toetzke's 97-article Factiva sample is
   for an **ingestion feasibility check only**; the scalable Factiva route is her parallel track with Malte,
   and any later addition is a joint decision "ohne größeren Eingriff".
2. Scope is pipeline plus validation. Language drift and a model comparison are welcome only as
   **supplementary robustness analyses if time remains** — outside the registered design, after the primary
   table, never in it.
3. A shorter title and a simpler Leitfrage are proposed (scope decision, 2026-09-10 section); the author
   adopts a wording, then registers.
4. The timeline is accepted; she is reachable in October.

**What has slipped, stated plainly.** A2 first coding has not started as of 2026-09-15 (sheet-1 is empty;
the sittings planned for 09-10/12/14 did not take place). The seal of 2026-09-07 holds for any first coding
day from 2026-09-08 on. Coding is the critical path; every day without a sitting moves the re-code, the
production run and the draft by a day.

**Re-planned calendar (adopted by the author on 2026-09-15; replaces the dates of §1 and the risk table where they differ).**

| Step | Date | Owner |
|---|---|---|
| **Before sitting 1:** message to the supervisor with the full inputs commit `1870c02…`, the seal commit `7c61b3d…` and the full seal digest `4d4fbd87421c0bdee902dd77fb818a29884565543e0f8255a478cab0ef20b790` (sent 2026-09-15; recorded in the Loom thread), the storage decision (storage is **retained as one of seven categories for this frozen evaluation**; the frozen design she endorsed contains it; a later objection can affect the discussion or future work, never the A2 scoring), the corrected Sonnet/Opus remark, and the note that the robustness comparison W2 is pre-registered | before the first row is coded | author |
| A2 pass 1, three sittings of 40 rows; **pass 1 completed (all rows coded) by 09-17**; finalised together with pass 2 | 2026-09-15 (evening), 09-16, 09-17 | author |
| `expand_sheet2.py`; pass 2, three sittings | 09-17 expansion; 09-18, 09-19, 09-20 | author (agent expands) |
| `finalize.py` both passes; label files and `sessions.csv` committed and pushed | **2026-09-21** (Mon) | author, agent prepares |
| Registration (joint Slack message: title, start date); eight-week clock from the entered start date | this week, on adoption of the wording | author |
| Factiva ingestion feasibility check, local only | by 2026-09-19 | agent |
| Re-code sheet for the 40 flagged paragraphs, fresh seeded order (seed 20261005, pre-registered 2026-09-15), first labels absent: `recode_sheet.py`, refuses before the first coding is finalised | tool built 2026-09-15; sheet generated after 09-21, opened on 10-05 | agent builds, author codes |
| Washout: the evaluator requires the re-code's first `coded_at` to lie ≥ 14 days after the **last `coded_at` of either first-coding pass** (`bind_reliability_inputs`); with pass 2 ending 09-20 the earliest admissible day is 10-04, and 10-05 is the buffered target; finalisation on 09-21 does not restart the clock, further coding would | earliest 2026-10-04; target **2026-10-05** | — |
| Re-code, both passes; finalised as its own session | 2026-10-05, finalised 10-06 | author |
| Production run (Sonnet 4.6, k = 3) **after** the re-code is finalised; evaluation; class manifest; ~~eight~~ ten tables and three figures | run 10-06, tables **2026-10-07** *(superseded by §5b addenda; executed 2026-10-13)* | agent |
| Chapter 6 written from the tables; Chapters 1, 7, 8 drafted | 10-08 – 10-13 | agent drafts, author reviews |
| **Full draft to the supervisor** | **2026-10-14** (Wed) | author |
| Feedback returned — **a proposal to put to her in the next message**; her "timeline passt" of 2026-09-10 referred to the looser dates spoken in the video | 2026-10-28 | supervisor |
| Revision; reproducibility package; submission by the formal deadline (registration 09-16 → 2026-11-11) | to 2026-11-06 | author |

Two dates move against the plan of §1 and against the specification's §6 hard-stop text (which names
2026-09-30 and is amended to 10-07 on the author's adoption of this calendar): the primary table from 09-30 to
10-07 and the full draft from 10-09 to 10-14, both consequences of the coding start and of keeping the production run behind the re-code.
The methodological promise is *no access to model output before the re-code*, not *no earlier execution*;
an earlier run with an independently controlled output embargo would also satisfy it. Re-code-first is
preferred because it is the simpler procedure and needs no embargo mechanism whose independence would have
to be argued; it costs five to seven calendar days. The author's own end-of-October bound gives way to the
formal deadline; the supervisor accepted an October feedback window in general terms, and the specific
dates above are a proposal to her, not an agreement.

**Workload, budgeted from what was observed.** A1 expanded 29 on-topic paragraphs into 53 relation rows
(1.8 rows per on-topic paragraph); at the A1 off-topic rate (17 %) the 120 A2 paragraphs give roughly 100
on-topic paragraphs and **180–220 pass-2 rows**, and the 40 reliability paragraphs roughly 60–75 rows for
the re-code. Pass 1 ran at about 35 paragraphs per sitting in A1, pass 2 at about 25–30 rows per sitting.
Budget: pass 1 ≈ 3 sittings of 1.5 h; pass 2 ≈ 6–8 h across the three days planned, more if the rate
holds at 25 rows an hour; re-code ≈ 3–4 h. **Slippage trigger:** if pass 1 is not *completed* by 09-17 or
pass 2 not *finalised* by 09-21, every downstream date moves day for day and the optional analyses are off; the trigger is
checked on those two evenings, not at the end.

**Optional robustness analyses (the supervisor's "wenn am Ende noch Zeit ist").** Both are outside the
primary analysis and neither may touch the primary results; (a) is pre-registered as W2, (b) is closed. Three registered clauses stand in their way and
have to be amended explicitly, not talked around: §1's closure ("translation production … may not return"),
the specification's W1 rule that no non-selected candidate is run on A2, and the specification's adaptation
firewall (§4), which forbids choosing a protocol after inspecting the primary results. The firewall covers
the **A2 human labels as well as the model results** (specification §4), so the only admissible moment for a
design decision is before the first A2 label is inspected, that is, **before sitting 1**. Therefore: **either an
analysis is pre-registered by a dated amendment to the specification's §4 — protocol, budget, activation
condition and reporting place fixed — before the first A2 row is coded, or it stays closed.** The date
2026-10-10 is a *resource check only* (is the whole manuscript, Chapters 1 and 6–8 included, on track for
10-14?), never a design choice. **Decided by the author on 2026-09-15, before the first A2 row was coded: (a) is pre-registered as W2 in the
specification's §4 with the protocol below; (b) stays closed.**

- (a) **Model comparison as robustness, not selection** — pre-registered as W2 on 2026-09-15, run only if the
  resource check passes: Claude Opus 4.6 on the A2 sample under the identical pinned request, k = 1 (≈ 120 calls, a few US
  dollars), scored by the same evaluator against the same reference, reported in a separate "Robustness"
  section with the same intervals. It answers whether the agreement pattern depends on the model; it selects
  nothing, and W1's rule is amended to "no non-selected candidate on A2 *before the primary table exists*".
  No new annotation. **Protocol, registered as W2 in the specification's §4 on 2026-09-15 (the authoritative text is there):** model
  `eu.anthropic.claude-opus-4-6-v1` under the byte-identical pinned request (same prompt, tool schema,
  `max_tokens`, temperature 0, cache point) on the registered A2 sample, one execution per paragraph, run only
  after the primary table exists and only if the resource check of 10-10 passes; planning estimate approximately USD 10 (no spending stop, see below); scored by
  `evaluate.py` under the sealed threshold against the same reference; reported in a separate "Robustness"
  subsection of Chapter 6 with the same tables and intervals, and in Chapter 7 read as a descriptive
  comparison of agreement patterns, never as a selection or a ranking; the W1 rule "no non-selected candidate
  on A2" is amended to "not before the primary table exists"; the run record, journal and gate rules of the
  production run apply unchanged. **Activation, gate and budget, fixed here so that no choice remains
  after inspection:** the resource check of 10-10 is taken by Nils Wagner and recorded in the review log; it
  passes only if, on that day, Chapter 6 is written from the eight primary tables, Chapters 1, 7 and 8 carry
  no bracketed placeholder, and the full draft compiles — otherwise the analysis is closed, and that entry is
  the record. If activated, the pre-extraction gate is attempted on at most two calendar days (10-11 and 10-12); a
  gate that fails before any extraction is retried under a fresh run identifier, and failure on both days ends
  the analysis with an "unavailable — not run" record in the usage log, without model substitution and without a
  development-mode fallback. Once the gate has certified, the run keeps its identifier: an infrastructure
  interruption is resumed under the production recovery contract (same journal, completed predictions kept, at
  most two recovery episodes), and a run still unfinished after that is reported as "incomplete — not evaluated". USD 10 is a planning estimate from the dry run's per-call cost, not an executable ceiling: the runner
  reports `estimated_usd` after the fact and has no budget stop; the run is not interrupted for cost; an
  execution interrupted for infrastructure reasons follows the recovery rule stated above and, if still
  incomplete, is reported as "incomplete — not evaluated" rather than partially.
- (b) **Translation-plus-re-extraction sensitivity** — **closed (author, 2026-09-15)**. Recorded for the record, should a later
  thesis reopen it: machine translation of the 60 German paragraphs into English (translator,
  version and date fixed in the amendment), re-extraction under the identical instrument, and the agreement
  between original-language and translated extraction as the statistic, **read against the native repeat
  variability of RQ3** as a descriptive benchmark, not a floor (a translated extraction can disagree less
  than a second native execution), and only with comparable units and statistics. It does not hold content constant and does not isolate the input
  language: translation can shift meaning, framing and evaluative intensity, and re-extraction adds execution
  variability, so the statistic measures the combined translation-and-extraction intervention and is named
  as such. The usage log's earlier correction on causal language attribution applies.

**W1 has lapsed.** The access deadline 2026-09-15 passed without a selection; the pin (Claude Sonnet 4.6)
stands as the production model. The 5-series entries in `instrument.MODELS` remain as the documented roster
of the lapsed analysis. Recorded in the specification §4 and GUIDE-LLM B.1.

**Factiva ingestion feasibility check, bounded and done locally on 2026-09-15.** Input: the RTF export
Malte delivered, kept outside the repository (SHA-256 `ac8aa13d…` recorded). Tool:
`tools/annotation/factiva_rtf_feasibility.py`, record `tools/annotation/factiva_rtf_feasibility_20260915.json`.
Result: 97 accession records, 94 with a full metadata block and 3 unresolved; among the 94, 28 distinct
headline–date keys, of which 6 are edition groups covering 72 records (largest 14) with body similarity ≥ 0.9
(the export lists one record per regional edition) — a grouping candidate, not a story count; 760 paragraphs
under the pipeline's rule with an approximate RTF conversion, 182 keyword candidates; one verified overlap with
the hand-built corpus (`A037`). No comparison of the candidate share with the hand-built corpus is drawn (different
units, selection, period). **What the check does not establish is authorisation:** the sample was downloaded
by an ETH account holder and handed to an LMU researcher; the ETH Library's guidance (TDM on manually
downloaded documents) speaks to the licensee's own use, Malte's pending query concerns automated retrieval,
and neither covers onward transfer, local processing by the recipient, or processing by a named external
provider. The local measurement is recorded as performed, not as authorised; **no paragraph of the sample is
sent to the model provider**, and any further use waits for a clarification that names transfer, local
processing and the provider (GUIDE-LLM D.1, OPEN). Value: an evidence-based paragraph in the outlook on what a
database export looks like and costs to ingest.

## 5b — Schedule amendment 2026-09-24: the slippage trigger fired

**Fact.** On 2026-09-24 no coded pass-1 export had reached the repository and no A2 label file existed; the first
coding was not finalised by 2026-09-21. The trigger of §5a applies: every internal milestone moves day for day and
**W2 is closed** (recorded in the specification §4). The formal deadline and the supervisor's dates do not move by
themselves; the supervisor is asked to confirm the new handover and feedback dates in the next Friday update.

| Step | Date | Owner |
|---|---|---|
| Pass-1 export saved to `annotation/sheets/`; agent checks, finalises pass 1, expands sheet 2 | 2026-09-24 | author, agent |
| Pass 2, three sittings | 09-24 – 09-27 | author |
| Both passes finalised, committed, pushed | **2026-09-28** | author, agent prepares |
| Washout: 14 days after the latest `coded_at` of either pass (pass 2 ending 09-27 → earliest 10-11; target 10-12) | earliest 2026-10-11 | — |
| Re-code sheet generated (`recode_sheet.py`, after finalisation); re-code A2R both passes; finalised and committed | 10-12, finalised **10-13** | author |
| Production run Sonnet 4.6, k = 3, after the re-code is finalised; evaluation; tables and figures | run 10-13, tables **2026-10-14** | agent |
| Chapter 6 from the tables; Chapters 7, 8, 1 complete drafts; Chapter 5 reconciliation | 10-15 – 10-20 | agent drafts, author reviews |
| **Full draft to the supervisor** | **2026-10-21** | author |
| Feedback asked for | 2026-11-04 | supervisor |
| Revision; reproducibility package; submission by the formal deadline: **2026-11-25** (registration sent 2026-09-30, start date entered 2026-09-30, + 8 weeks) | to 2026-11-25 | author |

Every date beyond 09-28 is conditional on the finalisation date actually reached; the evaluator computes the washout
from the recorded coding dates, not from this table.

**Addendum 2026-09-25.** The first pass-1 attempt was discarded (the coding interface showed keyword suggestions;
`tools/annotation/labels/README.md`, deviation section); the interface is rebuilt and pass 1 restarts from the blank
sheet, so the 09-28 finalisation is not reachable. The author sets the finalisation date when both passes are done;
every later row moves day for day from it (washout = latest recorded `coded_at` + 14 days). The update to the
supervisor of 2026-09-25 named no dates; the confirmation of the draft and feedback dates is still to be asked, together
with the registered start date. Registration was announced to the supervisor for the week of 2026-09-28; the formal
deadline is the entered start date + 8 weeks.

**Addendum 2026-09-30 (registration).** Registration sent on 2026-09-30 as the joint Slack message to Shuk-ki Lam,
Stefan Feuerriegel and Kerstin Forster with the start date **2026-09-30**; formal deadline **2026-11-25** (Wednesday).
This supersedes the assumed 2026-11-11 of §5a (registration 09-16). Between the proposed feedback date 2026-11-04 and
the deadline lie three weeks, which meets the supervisor's onboarding expectation of about three weeks at the end. The
study-office entry is to be checked by the author.

**Addendum 2026-09-30.** Pass 1 finalised: `labels/A2_pass1_v1.0.csv` (sha256 `46db3ab6e661d80b`), 120/120, five
sittings 09-28 to 09-30; sheet 2 expanded (178 rows). Pass 2 is next; its finalisation date sets every later row
(washout = latest recorded `coded_at` of both passes + 14 days).

**Addendum 2026-10-13 (re-code finalised, production run done).** A2R both passes finalised 2026-10-13; production run
`a2_sonnet_20261013` and evaluation the same day; the primary table exists eleven days before the amended
deadline. Next: Chapter 6 from the tables, Chapters 7, 8, 1, Chapter 5 reconciliation; full draft proposed for
2026-10-20, feedback asked by 2026-11-03 (to be confirmed by the supervisor).

**Addendum 2026-10-12 (washout shortened by the author's decision).** The author decided on 2026-10-12 to begin the
blind re-code A2R at once instead of on 2026-10-22; it was coded on 2026-10-13, five days after the last first-coding
date; recorded as a deviation in the specification §4 (route B
block), the labels README, the usage log and GUIDE-LLM E.1. The evaluator is run with `--washout-days 4` (the applied minimum) and the
deviation note; the realised interval was five days (re-code on 2026-10-13). Consequence for the calendar: every row after the re-code may move earlier by up to ten days once
A2R is finalised (production run after finalisation, tables the next day); the full-draft and feedback dates stay
proposals until the supervisor confirms them.

**Addendum 2026-10-08 (pass 2 finalised; calendar set).** `labels/A2_pass2_v1.0.csv` (sha256 `59eb4937324e8e3d`),
178/178, sittings 6–9, last coding date 2026-10-08. Re-code sheet and A2R manifest generated the same day; the
evaluator's earliest admissible re-code day is **2026-10-22**. The rows below replace the dates of §5b for everything
after the washout; the formal deadline 2026-11-25 is fixed. *(Superseded in turn by the addenda of 2026-10-12 and
2026-10-13 above: the re-code and the production run took place on 2026-10-13.)*

| Step | Date | Owner |
|---|---|---|
| Washout: no A2 coding, no model output; writing window (Chapter 5 deviations, Chapter 2/3 polish, literature pass). Agent tooling before production, Codex-reviewed by 10-16: an executable, provenance-preserving implementation of the registered fallback "drop execution three" to the specification in `tools/docs/dev-run-report_2026-10-09.md` (activation by the registered calendar; runner `--finalise-interrupted`; evaluator requires complete coverage of executions 1–2; tests), long-table output for Chapter 6 (presentation only, acceptance criteria in the same report), and the production checklist of that report | 10-09 – 10-21 | author, agent |
| **Chapters 1–5 as an interim draft to the supervisor**, so that she has ample reading time on the bulk of the text before the results exist | **2026-10-16** | author |
| Blind re-code A2R, both passes (40 paragraphs, fresh order), sheet opened that day at the earliest | **2026-10-22** | author |
| A2R finalised and committed | 2026-10-23 | author, agent |
| Gate and production run Sonnet 4.6, k = 3; evaluation under the sealed threshold s = 5, reliability route B; tables and figures | run 10-23, tables **2026-10-24** | agent |
| Chapter 6 from the tables (10-26, after the authorised recovery day 10-25); Chapters 7, 8, 1 complete; Chapter 5 reconciliation | 10-26 – 10-29 | agent drafts, author reviews |
| **Full draft to the supervisor** | **2026-10-30** | author |
| Feedback asked for | 2026-11-13 | supervisor |
| Revision; reproducibility package; GUIDE-LLM final | 11-14 – 11-20 | author, agent |
| Submission | 11-23 / 11-24, deadline **2026-11-25** | author |

The specification's hard-stop deadline for the primary metric table moves to 2026-10-24 by the same trigger. The
dates 10-16, 10-30 and 11-13 are proposals until the supervisor confirms them (Friday update of 2026-10-09).

*Reviewer notes 2026-10-08 (Codex round 1), adopted:* (i) the block 10-22 → 10-24 carries no recovery day; 10-25 is
the authorised recovery day for the re-code, the run and the tables — the 10-24 table deadline stays the target and
the specification's fallback applies only if the table does not exist by the end of 10-25; the Chapter 6 writing
allocation compresses to 10-26 and the chapters 7, 8, 1 keep 10-27/28 (blueprint regenerated 2026-10-09); (ii) the
specification's fallback (drop execution three, then postpone the reliability table) relieves a run problem, not an
A2R problem — if the re-code cannot be finalised by 10-24, the production run still follows the finalised re-code and
every later row moves day for day; (iii) a one-week slip of the full draft with unchanged feedback and revision
durations would end the revision on 11-27, after the deadline — so a slip of the full draft beyond 11-02 shortens the
revision window, not the deadline; (iv) the onboarding e-mail asks for "sufficient time for feedback toward the end
(e.g. three weeks)" — it does not fix a three-week reading turnaround, and the 09-30 addendum's sentence is to be read
that way; (v) the sittings-and-dates chronology of both passes, the discarded first attempt and the 10-22 earliest
re-code day are reconciled into Chapters 5 and 6 before the interim draft of 10-16.

## 5c — Addendum 2026-10-14: post-registration development phase W3 (exploratory; the registered result stands)

**Trigger.** The author's request of 2026-10-13, after the primary table existed: make the pipeline more robust and better, scientific feasibility first, every change disclosed and traceable. **Authority:** the W3 block of the pre-analysis specification §4 (declared 2026-10-14); the lever analysis in `knowledge/wiki/syntheses/pipeline-robustness-levers.md`; twelve sources ingested for it (P088–P099).

**What is built.** Instrument v1.1 in two variants (S: strict schema, nested frames, one labelled demonstration, two enumerated field-name replacements; E: plus a required explanation field), retry policy v3 (one feedback retry, same maximum allowance), a normaliser to the v1.0 row format, variant-specific pins and artefacts, `w3.py` (seal and selector), tests. The v1.0 instrument module, pins and artefacts stay byte-identical; the shared runner, gate and evaluator gained the instrument binding, the v3 retry path and provenance fields (their registered state is commit `8460d05`; the suite asserts the v2 behaviour unchanged for v1.0); the registered run and its outputs are untouched.

**Order and dates.**

| Step | Date | Owner |
|---|---|---|
| Protocol written into the specification; Codex round 2 on the protocol | 2026-10-14 | agent |
| Implementation and tests; Codex round 3 on code and seal | 2026-10-14 | agent |
| Seal (`w3_seal.json`) committed; three development runs on A1 (V0, V1, V2), evaluation, selection record | 2026-10-14/15 | agent |
| Documentation: design record, READMEs, usage log, GUIDE-LLM, Chapters 4/5/6/7 placeholders | same day as the runs | agent |
| Decision on the exploratory A2 run and on the A3 proposal | author, after reading the selection record | author |
| Chapter 6 and 7 writing (the author's note of 2026-10-14: the quality of the pipeline is judged only once Chapter 7 stands and the internal consistency figures are read with it) | from 2026-10-15 | agent drafts, author reviews |

**Costs.** Estimates, no stop: three A1 runs ≈ USD 1.8; probes of 2026-10-13 a few cents; an exploratory A2 run, if chosen, ≈ USD 3.

**Outcome (2026-10-14, midday).** W3 development runs (A1, 32 paragraphs from three articles, k = 3, 2026-10-14 09:39–09:54 UTC; every gate certified; selector `tools/runs/w3_selection.json`, seal digest a333c43b…): V0 v1.0 — 96 calls, 0 invalid first responses, 96 successful terminals, S = 0.6625 (execution F1 0.654 / 0.667 / 0.667), USD 0.51; V1 v1.1S — 118 calls, 20 of 96 first responses invalid (24 invalid responses over all attempts; rule flags over all attempts: element_frame_mismatch 11, subtype_misplaced 10, subtype_missing 9, comparison_note_required 2; among the first returned responses 11, 6, 6 and 1), 21 feedback retries, 4 `parse_failed` terminals, 2 read-timeout transport errors recovered (billing uncertain), S = 0.6457 (0.673 / 0.636 / 0.629), USD 0.61; V2 v1.1E — 105 calls, 9 of 96 first responses invalid (10 over all attempts; flags over all attempts repeated_frame 6, subtype_misplaced 4, comparison_note_required 2, element_frame_mismatch 1; among first returned responses 6, 3, 1 and 1), 9 feedback retries, 1 `parse_failed` terminal, S = 0.6557 (0.623 / 0.648 / 0.696), USD 0.74. **Outcome: not achieved** — V1 is not eligible, v1.0 is retained; the baseline guard was not applied. Read as development figures: the nested representation under strict decoding produced more invalid responses and more lost executions than the registered instrument on this split; of the initially invalid executions the feedback retry recovered 16 of 20 (V1; 21 feedback attempts, one of them resent after a timeout) and 8 of 9 (V2) — feasibility of correction, not superiority over the identical retry, which the bundled comparison cannot isolate. Chapter 6 reports the three candidates in the post-registration subsection; Chapter 7 reads the result as development evidence that the structural change, as designed, did not improve robustness on this split.

**Not done in W3 (deliberately):** a worked-examples block (one labelled demonstration in the output-contract block is the only example), decomposition, majority voting, prompt-language variants, any codebook change, any change to thresholds, metrics, sample, model, temperature or token cap.

### 5c.1 — Second development iteration W4 (declared 2026-10-14, after the W3 result)

**Authority:** the W4 block of the specification (§4a). **Why:** W3's structural variant produced new output-contract
failures on A1 (elements under a neighbouring frame; subtypes misplaced or omitted) and lost four executions. **What:**
one candidate v1.2S (`instrument_v11.py` variant T; pins `pins_v1.2S.json`; artefacts `instrument/*_v1.2S.*`): per-frame
element enums, one required `technology_subtype`, field names whose alphabetical order is the reading order, the same
demonstration paragraph and labels in that order; retry policy v3 unchanged. **Rule:** single candidate — selected only
if eligible, V0 scoreable, S ≥ S(V0) and I ≤ I(V0); else retained; A1-only, no A2 authorisation (the only evaluation
route is A3). **Stopping rule:** W4 is the last development iteration before the draft. **Seal and selector:**
`tools/pipeline/w4.py --seal --v0 a1_w4_v10_20261014 --v1 a1_w4_v12S_20261014` → `tools/runs/w4_seal.json`;
`w4.py --select` → `tools/runs/w4_selection.json`. The W3 record stays as the first iteration's record, reproducible at
commit `b01840d`. **Cost:** two A1 runs ≈ USD 1.2.

**Outcome (2026-10-14, early afternoon).** W4 development runs (A1, k = 3, 2026-10-14 10:17–10:23 UTC; every gate certified; seal `tools/runs/w4_seal.json` digest c7d7e591…, commit c79f2f2, clean tree at sealing; selector `tools/runs/w4_selection.json`): V0 v1.0 — 97 calls, 1 invalid first response (`subtype_missing`, recovered by the identical retry), 96 successful terminals, S = 0.6625 (0.667 / 0.654 / 0.667), 49,295 uncached input, 418,943 cache-read and 12,936 output tokens, USD 0.51; V1 v1.2S — 98 calls, 2 invalid first responses (`repeated_frame`, both recovered by the feedback retry), 96 successful terminals, S = 0.5912 (0.566 / 0.623 / 0.585), 50,056 uncached input, 569,282 cache-read and 13,057 output tokens, USD 0.57; billing complete for both (token-derived estimates at the frozen rates, gate probes excluded). **Outcome: retained** — v1.2S is eligible but not at least as good on both counts (S 0.591 < 0.663; I 2 > 1); v1.0 is retained. Both run records show a dirty tree at start: at V0's start the tracked descriptive file `manifest_v1.1S.json` was modified; at V1's start both `manifest_v1.1S.json` and `manifest_v1.1E.json` plus V0's own freshly written outputs were outstanding (regenerated manifests are descriptive artefacts that no seal covers; committed as da82975); the sealed code, pins and request artefacts were unchanged and verify. Read as development figures: in this run no element–frame or subtype incompatibility occurred (its two invalid first responses were `repeated_frame`, which the schema cannot exclude), and the element micro-F1 was lower on this split; execution-1 per-element values with their reference support, as descriptive examples that neither rank the declines nor explain the change in S: F3b 0.65 → 0.31 (22 rows), F1a 1.00 → 0.89 (5), F2b 0.73 → 0.67 (7), while F4b (1 row) and F9 (4 rows) rose. The two complete configurations differ in representation, constraints and retry at once, so no component is credited. The stopping rule applies — no further development iteration before the draft.

**What the two iterations leave for the plan.** The lever the development split cannot assess is the feedback retry on the registered instrument itself (v1.0's first request byte-identical to the registered run; only the retry after an invalid response changes): v1.0 produced 0 of 96 and 1 of 96 initially invalid executions on A1 against 19 of 360 on A2 (32 invalid responses over all attempts, 13 terminal failures), so its effect can only be measured where failures occur — A1 offers too few failures for a useful comparison rather than being unable to assess retries; decomposition remains the other lever the lever page names for a fresh-sample study — on a fresh sample (A3) or, by the author's decision under a dated amendment, in an exploratory reused-A2 analysis. Neither is executed; the stopping rule of W4 holds. If the author chooses the exploratory reused-A2 analysis, its dated amendment (a new phase, an explicit departure from the "no W5" commitment) must require: exactly one frozen candidate (the registered first-request bytes, definitions, schema and validator unchanged; only a schema- or contract-invalid response receives the single feedback retry; transport, resume, fallback and private-journalling rules of v3 kept); a declared comparison and estimand, preferably a contemporaneous v1.0 control, since identical requests need not reproduce identical responses; fixed accounting (whole-sample coverage, initially invalid executions, terminal failures, conditional recovery, end-to-end agreement, costs, uncertainty; contract validity distinguished from label correctness); frozen run identifiers, stopping rule and reporting before execution; a new pipeline identity with its own seal, tests, gate certification and an exploratory stamp (the evaluator labels a non-development v1.0 run "registered primary"); no confirmatory improvement claim; and the A3 route for any independent evaluation.

### 5c.2 — Fresh-sample evaluation set A3 (declared 2026-10-14; revised after Codex round 1 the same day)

**Authority:** the A3 block of the specification (§4b). **What:** a census of the 55 frame paragraphs from the 10 articles with no documented study exposure (27 de / 28 en), listed by `tools/annotation/draw_a3.py` after a text-free exposure audit over every public run record, session manifest, label file, codebook and instrument artefact (seeded sitting order 20261014); coded blind by the author in two passes under codebook v1.0, finalised as `A3_pass1` / `A3_pass2`; then v1.0 (k = 3) under an evaluator extension that accepts the A3 kind with census weights under the status "fresh-sample evaluation" (threshold 5 declared for A3; tables 1–6, 8 and the figures; table 7 coincides with table 1, table 9 not applicable). One frozen candidate configuration, `v1.0r3` (the registered request + retry policy v3 as implemented for W3), may follow by the author's decision after the baseline figure, as a descriptive comparison disclosed with its date. **Estimand:** the figures on the residual census itself; intervals indicative (model-based article resampling on a census); A2–A3 differences descriptive. **No power claim:** the specification carries a hypothetical sensitivity note with its assumptions (SE(Δ) = SE·√(2(1−ρ))), not an MDE. **Embargo:** the runner refuses any run — any instrument, development mode included — that touches an A3 paragraph before both passes are finalised, recorded and committed.

| Step | Date | Owner |
|---|---|---|
| Protocol §4b; `draw_a3.py` with exposure audit; runner embargo with tests; Codex rounds; text-free manifests committed (sheets stay untracked) | 2026-10-14 | agent |
| A3 pass 1 (55 rows, about two sittings); `expand_sheet2.py`; pass 2 (about 80 rows); `finalize.py` both passes; commit | author's calendar | author (agent expands and finalises) |
| Runner/evaluator extension for the A3 kind (tests; usage log); the evaluator verifies the embargo evidence in the run record and the journal (reference files committed before the run's first call; development mode refused for A3) | done 2026-10-14 (`verify_a3_sample`, runner rules, evaluator evidence, figures; tests T12a–i; reviews in the review log) | agent |
| v1.0 on A3, k = 3; evaluation; tables and figures under the fresh-sample label; Chapter 6 subsection | after finalisation | agent |
| Decision on `v1.0r3` (disclosed with its date) | author, after the v1.0 figure | author |

### 5c.3 — Supervisor feedback of 2026-10-19: error decomposition, DSL, optional robustness analyses

**Authority:** the reply of 2026-10-19 (correspondence thread) and the specification's §4c–§4e. **What:** (1) development iterations short in Chapter 6, details in the appendix (done); (2) the stability table stays in the main text, the shortened washout is a limitation (done); (3) a descriptive error decomposition of the registered run, per element precision/recall/F1 and per derived frame detection precision/recall/F1 with the conditional (score A) and end-to-end (score B) evaluation per frame (`tools/pipeline/error_breakdown.py`; §4c); (4) an exploratory DSL estimate of the frame prevalences from the registered predictions, a frame-completion run on the 443 remaining frame paragraphs and the A3 run (`tools/pipeline/dsl.py`; §4d; a design-based standard error under the stratified draw added 2026-10-19 after the ingest of P100, beside the descriptive interval; computed 2026-10-19 after the A3 run `a3_sonnet_20261019`, which followed the finalisation of A3 pass 2 the same day); (5) two optional robustness analyses declared, not run: the language arm on translated German inputs and the reopening of W2 (§4e), the author's decision after the draft.

| Step | Date | Owner |
|---|---|---|
| Chapter 6/appendix split; washout sentence; abbreviations list; correspondence ingested | 2026-10-19 | agent |
| Error decomposition script with the F1 reproduction check; tables; Chapter 6 paragraph and Chapter 7 reading | 2026-10-19 | agent |
| Frame-completion listing (443), runner/contract support for the kind, certified run of v1.0 (k = 1, ≈ USD 3), DSL estimator with article resampling and tests; domain estimate (563) now, frame estimate (618) after the A3 run | 2026-10-19 → after A3 | agent |
| Language arm / W2: author's decision after the full draft; if run, as declared in §4e | after 2026-10-20 | author |

## 6 — Risks and open questions

| Risk / question | Owner | Consequence if unresolved |
|---|---|---|
| ~~Blind support threshold *s* not committed before A2 labels are inspected~~ **decided 2026-09-07: *s* = 5** | author | Sealed by `sample_contract.py --seal-threshold 5` and committed on a day before sitting 1 (the writer refuses while the manifests, the registry or the module are uncommitted); until that commit exists the risk stands as written: RQ4's rule becomes post hoc; the table cannot print `n<s` honestly |
| ~~Second coder~~ **decided 2026-09-06: none** | author | L22 fires now: 14-day washout re-code of the 40-paragraph subsample by the author, named intra-coder temporal stability; "gold" means the author's pass 2; inter-coder reliability unmeasured, stated |
| ~~Registered title and Leitfrage~~ **adopted 2026-09-15** | author + supervisor | The supervisor's wording of 2026-09-10 adopted verbatim (scope decision); the registration message goes out with the full template; the eight-week clock starts with the entered start date |
| Hard-gate date and cut order | author | This plan assumes 2026-09-25; **the author does not pin the date (2026-09-07)**, it stays a working assumption. Cut order aligned to the specification §6 on 2026-09-07 (the closed Sonnet-vs-Opus comparison was removed from scope after review; the W1 selection is a separate amendment whose closure is proposed to the author): the third execution (*k* = 3 → 2) first, then the reliability table moves from the primary-table date to the full-draft date; nothing else. Neither cut is a schedule lever; annotation and writing hours are |
| Bedrock credential or quota failure during the A2 run | build | The runner resumes from the per-call file; repeats are per paragraph |
| Sonnet 5 / Opus 5 under the pinned Converse request (1-hour cache point, forced tool use); Anthropic documents a separate Messages-API integration for Opus 4.7 and later | build | The gate decides eligibility per candidate; an ineligible candidate is dropped, the instrument is never adapted per model; the newer tokenizer (≈ 30 % more tokens) changes cost and cache accounting, both printed |
| Feasibility of 60 en after excluding A011 under a 24-paragraph cap | build | Printed before the draw; if infeasible the draw refuses and names the smallest feasible cap; only a dated explicit amendment changes the design |
| Model notes quoting paragraph text | build | Raw sidecar gitignored; committed outputs are an allowlist of fields; a test greps a planted quotation |
| Annotation calendar | author | **Re-planned 2026-09-15 (§5a): coding not started; first coding finalised 09-21, re-code 10-05, run 10-06, primary table 10-07, draft 10-14.** Superseded text: Primary table on 2026-09-30 needs the A2 reference labels finalised by 2026-09-24 (execution 1 and the evaluator take a day). Route B's 14-day washout means the reliability table lands 14 days after A2's first coding ends: first coding done by 2026-09-16 → reliability table by 2026-09-30; done later → the reliability table moves to the 2026-10-09 draft while the primary table keeps its date. Route A needs the second coder's 40 paragraphs and A4 before the class manifest is written |
| Registry events after the freeze | build | `draw_a2.py` and the evaluator verify the Step 1.5 handshake — the head is the `a2_frozen` event, its `audit_ref` equals the recomputed frame-manifest digest, its predecessor equals `pre_freeze_head` — and refuse otherwise |

## 7 — Out of scope

M1 ingest adapters beyond the markdown corpus already registered; M3 relevance cascade (the corpus is
already candidate-filtered by the registry's keyword prefilter, and this is stated in Chapter 5 as
attrition); M7 experiment harness; any translation, prompt variant, DSL or PPI++ estimator; a second
annotated arm; encoder fine-tuning; multicalibration; speaker attribution as a measured contribution;
any substantive claim about energy discourse.

---

*Relocated 2026-09-24 from `tools/` to `tools/docs/` without any change of content. Live relative links were repointed to the new location the same day; path citations in prose (`tools/<name>.md`) are historical and map through `README.md` of this folder; `tools/docs/README.md` maps every old path to its new one.*
