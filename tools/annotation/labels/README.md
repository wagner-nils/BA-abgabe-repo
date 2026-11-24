# labels/

Completed annotation labels — **committed**, unlike `../sheets/`.

Each session produces two files. The working sheet in `../sheets/` carries the paragraph text and
is gitignored, because article text is licensed. What lands here is paragraph IDs plus labels plus
provenance, and nothing else. The labels are the research data: Chapter 6 is written from them,
and the commit timestamp is the evidence that the annotation preceded the model run.

Naming: `<session>_pass<n>_v<codebook-version>.csv` — e.g. `A0_pass1_v0.3.csv`. Old versions are never
overwritten; the diff between them is evidence for the thesis.

## Session manifests (from 2026-09-05)

`<session>_manifest.json` — IDs and hashes only: which paragraphs a session was coded against, their
identity hashes, splits, the registry head and the generator. Written by `build_sheets.py` when a
sheet is generated; `finalize.py` verifies every schema-2 label file against it by recomputing the
text hash from the sheet, and records the finalized pass-1 keys that pass 2 must reproduce. The
manifests of A0, A0b and A1 are **reconstructed** from the label files and marked so.

## Erratum — free-text notes in the legacy label files

The six files finalized before sheet schema 2 carry the coder's `note` and `comparison_note`
verbatim. A note can quote the paragraph it describes, so these files sit on the wrong side of the
licence boundary the rest of `tools/` observes. They are frozen and are not edited. Before any
publication of the replication package the author decides whether they are released as they stand,
released with the two free-text columns blanked, or withheld. From 2026-09-05 the finalizer writes
free text to the gitignored `private/` folder and commits a marker instead.

## Deviation — first A2 pass-1 attempt discarded (recorded 2026-09-25)

**What happened.** The author began A2 pass 1 in the week before 2026-09-25 (author-reported: "letzte Woche
begonnen", update to the supervisor of 2026-09-25) in a build of the coding interface (the "Frame-Kodierer", a
claude.ai Artifact, not in this repository) that displayed keyword-derived technology suggestions: a "Vorschläge aus
Schlagworten" panel with an "übernimmt alle" shortcut, "+1" badges on the suggested technologies, and highlighting
of the matching terms in the paragraph text. This was identified on 2026-09-25 from a screenshot of the build at
row 1 of 120. Rule 1 of "The coding interface" in [`../README.md`](../README.md) requires the suggestion function to
be **absent** from the build used for A2, because a label taken after seeing a suggestion is a confirmation of the
suggestion — here of the same keyword lexicon the pipeline's prefilter uses, which would inflate technology-detection
agreement against a reference that is no longer independent of it.

**Disposition.** The attempt is not used. No export of it has reached the repository as of 2026-09-25; if one
exists, it goes to `_quarantine/` (gitignored) and is never finalised, exactly as the 2026-08-30 A1 export with model
labels. The interface is rebuilt to a written specification: no suggestion, highlighting, default or pre-selection
function; sheet kind detected from the header; `sitting` as a positive integer; `codebook_version` 1.0 stamped;
export byte-compatible with the blank sheets. Pass 1 restarts from the blank `A2_evaluation_sheet-1.csv` in the
rebuilt build. Nothing about any label of the discarded attempt was inspected or recorded.

**Build record (verified 2026-09-28, before the first recoded row).** Rebuilt build of the Frame-Kodierer
(claude.ai Artifact, source outside the repository), specification of 2026-09-25: no suggestion, highlighting, default
or pre-selection function; sheet kind detected from the header; `sitting` a positive integer; `codebook_version`
1.0 stamped; byte-compatible export. Verified on a one-row test export (`A2_evaluation_sheet-1_coded_2026-09-28.csv`,
kept outside `sheets/`, never finalised): header byte-identical to the blank sheet; 120 rows in the blank sheet's
order; all context columns and `text_sha256` unchanged; UTF-8 BOM, `;`, CRLF; the coded row carries `0`/`1`
technology values, a subtype only for its technology, `no_frame_present` 0/1, `confidence`, `annotator` NW,
`annotator_type` human, `coded_at` as an ISO date, `codebook_version` 1.0, `sitting` 1. `finalize.validate()` on
the coded row alone reports only the expected coverage problem (1 of 120); on the whole export only the 119 uncoded
rows. Screenshot of the build at row 1 shows plain paragraph text and empty controls. **Residual checks on the first
full export:** an off-topic row must carry `no_frame_present` blank (the blank template prefilled `0`; the tool's
off-topic notice says it clears it); the build's own changelog and self-check statement from the rebuild are to be
attached here when the author supplies them.

**Disclosure.** Chapter 5 states this deviation: the coder had seen keyword suggestions on some paragraphs during
the discarded attempt, and that exposure cannot be undone. The intra-coder stability re-code (A2R) compares the
recoded first coding with a second coding in the same suggestion-free build. The supervisor was informed on
2026-09-25 and endorsed the recoding in her reply of 2026-10-01 ("Die Neucodierung klingt sinnvoll").

## A2 pass 1 finalised (2026-09-30) — and one format correction before finalisation

`A2_pass1_v1.0.csv` (sha256 `46db3ab6e661d80b`), 120 of 120 paragraphs, coder NW, codebook v1.0, five sittings on
2026-09-28, 09-29 and 09-30, recorded in `sessions.csv`; the 34 free-text notes are in the gitignored
`private/A2_pass1_v1.0_notes.csv`. Coded in the rebuilt interface (build record above). One paragraph is off topic
(`A037_p03`, names no study technology); one names a technology without framing it (`A042_p01`,
`no_frame_present` = 1). 119 technology-bearing paragraphs yield 178 (paragraph × technology) rows for pass 2, 41
paragraphs name more than one technology. Sheet 2 was expanded from this file on 2026-09-30.

**Format correction, disclosed.** The export carried `no_frame_present` = 1 on the off-topic row `A037_p03`. On a row
with no technology ticked that value has no meaning (`no_frame_present` says a *named* technology is not framed) and
`finalize.py` refuses it; the coder's own note on the row states the off-topic reading ("no technology named"). The
value was cleared to blank in the repository copy of the sheet before finalisation, by the agent, on 2026-09-30; no
other cell was touched (sheet sha256 `5621ae3c4436e537` → `8c8cb2f5e95539ff`; the tool's original export remains
unchanged in the author's Downloads folder). The interface should not offer `no_frame_present` while no technology
is ticked; the rebuilt build evidently still allowed the sequence tick → set 1 → untick. Recorded as a known gap of
the build, not repaired in the label.

## A2 pass 2 finalised (2026-10-08); blind re-code sheet generated the same day

`A2_pass2_v1.0.csv` (sha256 `59eb4937324e8e3d`), 178 of 178 (paragraph × technology) rows, coder NW, codebook v1.0,
four sittings (6–9) on 2026-09-30, 10-05 and 10-08, recorded in `sessions.csv`; 161 free-text cells in the gitignored
`private/A2_pass2_v1.0_notes.csv`. Checked before finalisation: header and row order identical to the expanded
sheet, context unchanged, every row either fires an element or carries `no_frame_for_this_tech` = 1 (18 rows), an
evaluation on every fired frame and on no unfired one, `comparison_target` and `speaker` on every row, F9 (2 rows)
and `other`/`multiple` (5 rows) with notes. No correction was needed. Frames fired (rows): jobs 82, costs 70,
climate 49, reliability 37, feasibility 33, regulation 28, security 20, siting 19, other 2.

**Re-code sheet** `sheets/A2R_reliability_sheet-1.csv` (40 paragraphs, 20 de / 20 en, fresh order, seed 20261005,
no labels) and `A2R_manifest.json` were generated by `recode_sheet.py` on 2026-10-08 after both passes were
finalised. **Earliest admissible re-code day under the registered rule: 2026-10-22** (last first-coding date 2026-10-08 + 14
days) — superseded by the author's deviation of 2026-10-12 (section below): the sheet was opened and coded on
2026-10-13; the evaluator records the registered, applied and realised intervals.

## Cross-pass screening discrepancy, recorded 2026-10-08 (labels unchanged; interpretation deferred)

Pass 1 screens each paragraph once (`no_frame_present`: a named technology is or is not framed at all); pass 2 codes
each (paragraph × technology) row (`no_frame_for_this_tech`). Joining the two finalised files shows **six paragraphs
whose screening says "framed" (`no_frame_present` = 0) while every one of their pass-2 rows is unframed**
(`no_frame_for_this_tech` = 1): `A037_p05`, `A051_p02`, `A067_p02`, `A072_p36`, `A083_p02`, `A091_p05`. Pass 2 therefore implies seven wholly unframed
technology-bearing paragraphs (these six plus `A042_p01`, which both passes mark unframed) against one in the
screening. Found by the reviewer (Codex, 2026-10-08) after finalisation.

**Disposition.** Both label files are frozen and stay as they are. The pass-2 elements and evaluations govern the
scoring: the evaluator builds the (paragraph × technology) relation from elements, evaluations, comparison target
and subtype, so a row without any fired element enters as its empty state; it reads neither `no_frame_present` nor
`no_frame_for_this_tech` directly, and the pass-1 flag feeds no metric. The discrepancy is reported in Chapter 5 as a
disclosed cross-pass inconsistency of the reference, with the count. The re-code does not resolve it: only two of the
six paragraphs lie in the fixed A2R subsample and no screening-flag agreement statistic is reported. **No case is re-read or interpreted before the A2R
re-code is finalised** (washout rule); the agent does not discuss these paragraphs with the author before then.
*The embargo ended with the A2R finalisation on 2026-10-13; the six cases may now be read for Chapters 5 and 7.*

## Deviation — washout shortened (author's decision 2026-10-12)

The registered washout for the blind re-code is 14 days (L22, fixed 2026-09-06; `A2R_manifest.json` records
`washout_days: 14` and the earliest admissible day 2026-10-22). On 2026-10-12 the author decided to begin the re-code
at once, to recover schedule; it was coded on 2026-10-13, five days after the last first-coding date; the agent had stated the rule and its
consequence twice before the decision. The manifest is not edited (it is the record of the rule as generated); the
evaluator is run with `--washout-days 4 --washout-deviation "author's decision 2026-10-12, schedule; agent advised
against"`, and it writes the registered and the applied value and the note into the metrics and the route label of
the reliability table. Consequence: a five-day interval raises the risk that the first coding is recalled rather than
re-decided, so the intra-coder stability figure is read as potentially inflated by recall and as limited evidence of temporal
stability (not as a numerical bound on the 14-day result, which was not observed); this is said in Chapter 5 and in
the limitations. Everything else of the re-code protocol is unchanged (fresh seeded order,
no first label visible, same frozen codebook, A2R finalised as its own session). No A2 label, note or prediction
informed the decision.

## A2R re-code finalised (2026-10-13) — and two format corrections before finalising pass 2

`A2R_pass1_v1.0.csv` (sha256 `201b439216d7a62e`), 40 of 40 paragraphs, one sitting on 2026-10-13, no correction;
`A2R_pass2_v1.0.csv`, 54 of 54 (paragraph × technology) rows, sittings 1–2 on 2026-10-13. Both recorded in
`sessions.csv`; notes in the gitignored private sidecar. Coded five days after the last first-coding date (2026-10-08) under the
disclosed washout deviation (above); the evaluator was run with an applied minimum of four days.

**Format corrections on the pass-2 export, disclosed.** (1) On the eight rows marked `no_frame_for_this_tech` = 1 the
export left `comparison_target` blank; the rule requires `none` where nothing is compared and `finalize.py` refuses a
blank. The agent set `none` on exactly those eight rows (the interface should require the field on every row; it
evidently accepts a no-frame row without it). (2) On one row (`A063_p03__wind`) the export carried a `wind_siting`
value different from the one the pass-1 label file records; the subtype is coded once in pass 1 and travels into
pass 2 as context, so the pass-1 value was restored (the interface should not offer the subtype for editing in pass
2). No other cell was touched; sheet sha256 `04e7435ba3958c5d` → `a1b1696dc5d398a2`; the tool's original export remains unchanged in the
author's Downloads folder. Both are recorded as known gaps of the interface build.

## Coding note — the `general` category beside individual technologies (recorded 2026-10-14; codebook v1.0 unchanged)

The v1.0 definition of `general` ("Renewables, clean power, green energy, or 'the energy transition' as a bundle,
with no identifiable individual technology carrying the statement") admits two readings. (a) **Per statement:** a
bundle term that carries a statement of its own yields a `general` row even when individual technologies are named
in the same paragraph. (b) **Residual:** `general` fires only when no individual technology is identifiable in the
paragraph. Rule X11 ("tick only the technologies the paragraph itself makes a statement about") supports (a); the
codebook does not decide. The reference applies (a), consistently: in A2 `general` is ticked on 40 of 120
paragraphs, on 22 of them beside at least one individual technology (A071_p04 and A054_p17 general + solar + wind +
storage; A092_p07 general + wind; A008_p02 and A012_p29 general + solar), and omitted where only individual
technologies are named (A078_p03, A042_p01, A070_p10). The A3 coding (in progress) applies the same reading: of the
eight A3 paragraphs naming solar and wind, four also carry `general` (A084_p16, A050_p02, A050_p03, A050_p06) and
four do not (A084_p05, A084_p14, A081_p04, A064_p03). Consequence: under reading (b) `general` would fire less
often and exactly those rows would drop. No label is changed (v1.0 is frozen, A2 is final); the point is recorded
here and in the thesis's limitations (Chapter 7). The rendered prompt carries the same definition, so the model
faces the same ambiguity.

## A3 fresh-sample census listed (2026-10-14) — coding not started

**Naming.** "A3" below is the fresh-sample evaluation set of the pre-analysis specification §4b (declared and revised
on 2026-10-14 after three Codex rounds). The earlier "A3 — reliability subsample" of the annotation README was a
superseded design name; that re-code was executed as A2R on 2026-10-13.

**What was listed.** `A3_sample_manifest.json` (kind `A3_sample_manifest`, digest `65c9bf350261c978…`, generator
`draw_a3.py` sha256 `bc8586d1d58ba61c…`): a **census** of the 55 paragraphs of the frozen A2 frame
(digest `a5eccabd1bf6ef64…`, freeze event `24167d3a65b99f06`) that are not in the registered A2 sample
(digest `1cbfa597b9203b64…`), whose registry exposure is `clean_article` and whose article has no
paragraph in the A2 sample — 27 German / 28 English from 10 articles (A038 3, A040 6, A045 4, A050 4, A062 8, A064 10, A073 6, A081 4, A084 9, A090 1); strata
(reporting only): BILD 9, DER SPIEGEL 4, DIE ZEIT 4, Neue Zürcher Zeitung 9, Süddeutsche Zeitung 1, The Economist 4, The Guardian 8, The New York Times 16. Inclusion probability 1, weight 1; the estimand is the registered quantities on this
census itself (no extrapolation to the frame). Sitting order: seeded shuffle, `SEED_ORDER_A3 = 20261014`.

**Exposure audit (text-free, written into the manifest).** 84 files read — every public run record under
`tools/runs/`, every session manifest and finalised label file here (the frame manifest excepted), the codebook files
and the rendered instrument artefacts — for article identifiers (standalone or inside paragraph and row identifiers)
and for the A3 paragraphs' digests and text hashes: **no documented exposure**. Freshness means absence of documented
study exposure; the audit cannot establish absence of unlogged use (author's attestation).

**Files.** `A3_manifest.json` (session manifest, sha256 `50092ba0b1c2d394…`; carries a `fresh_sample` block naming the sample
manifest and its digest) and `A3_sample_manifest.json` are text-free and committed. The working sheet
`sheets/A3_evaluation_sheet-1.csv` (55 rows, blank, seeded order; header identical to the A2 sheet-1 header) and the
header-only `sheets/A3_evaluation_sheet-2.csv` carry paragraph text and stay untracked (`sheets/` is ignored).

**Coding and embargo.** Pass 1 in the sheet above; `expand_sheet2.py A3_evaluation` after pass 1; `finalize.py` for
`A3_pass1` and `A3_pass2` (sessions `A3_pass1` / `A3_pass2` in `sessions.csv`, label files `A3_pass1_v1.0.csv` /
`A3_pass2_v1.0.csv`). **No model run touches an A3 paragraph before both passes are finalised and committed**: the
runner (`run_extract.py`, A3 embargo) refuses any run of any instrument, development mode included, whose manifest
shares a paragraph (by identifier, digest or text hash) with this listing until both label files exist, are recorded
in `sessions.csv` with matching hashes, cover exactly these paragraphs, agree between the passes, match
`A3_manifest.json`, and are committed without pending changes. No reliability re-code is planned for A3; the A3
reference's reliability is unmeasured.

**A3 pass 1 finalised (2026-10-14).** `A3_pass1_v1.0.csv` (sha256 `211591df0511cd34…`), 55 of 55 paragraphs, one sitting on
2026-10-14, confidence 2 on 45 rows, 3 on 9, 1 on 1; no paragraph off topic; 81 (paragraph × technology) keys recorded in
`A3_manifest.json`; 24 free-text notes stripped to `labels/private/`. Technology ticks: solar 22, general 21 (10 of them
beside an individual technology, the reading of the coding note above), wind 16, hydrogen 12, other renewable 6,
nuclear 4, storage 0. The export carries the labels as they stood when the consistency question above was asked: the
four `general` rows discussed there are ticked, the four counter-examples are not; no label was changed after the
exchange. Pass-2 sheet `sheets/A3_evaluation_sheet-2.csv` (81 rows) generated by `expand_sheet2.py` the same day;
pass 2 is coded next. The runner embargo stays in force until pass 2 is finalised and committed.

**A3 pass 2 finalised (2026-10-19).** `A3_pass2_v1.0.csv` (sha256 `0257e3b0e081a766…`), 81 (paragraph × technology)
rows over the 55 paragraphs, coded by NW in sittings 2–4 on 2026-10-14 and 2026-10-19 under codebook 1.0; 94 free-text
cells stripped to `labels/private/`; technology rows as in pass 1 (solar 22, general 21, wind 16, hydrogen 12, other
renewable 6, nuclear 4). The author's export was placed at `sheets/A3_evaluation_sheet-2_CODED.csv` (untracked; the
untouched export is kept beside it as `…_CODED_as-exported_2026-10-19.csv`). `finalize.py` first refused it because
`comparison_target` was blank on 10 rows, all of them rows with `no_frame_for_this_tech = 1` and no element ticked
(A064_p18 hydrogen; A064_p13 wind; A050_p03 solar, wind; A064_p03 solar, wind, other renewable; A062_p05 general;
A064_p19 wind; A045_p03 wind). On the author's written instruction of 2026-10-19 ("the ten blanks mean none", the same
practice as the 18 no-frame rows of A2 pass 2) the agent set `none` on exactly those ten cells and nothing else, verified
cell by cell against the export, then finalised. *Author's remark after the second pass (2026-10-19, paraphrased from the
author's message):* the codebook leaves some definitions open, so that coding was not always clear-cut and human codings
may differ for that reason; recorded here as the author's statement, beside the `general` coding note above, as context
for the A3 figures. The runner embargo lifted with the commit of this file, the label file and `sessions.csv` (`3f633bb`); the run
`a3_sonnet_20261019` (v1.0, Sonnet 4.6, k = 3) started at 17:22 UTC the same day, its record carries the embargo
evidence (both passes finalised, session manifest bound, tree clean), and the evaluation (threshold 5, declared) and
the DSL frame estimate followed (usage log, fourth entry of 2026-10-19). On 2026-10-26 the author decided to run the one frozen candidate configuration `v1.0r3` on the census (run `a3_sonnet_r3_20261026`, usage log of 2026-10-26); the labels were not touched.


## A2R2 second re-code wave finalised (2026-11-10; specification §4n)

`A2R2_pass1_v1.0.csv` (sha256 `8369b667b08bd12e`), 80 of 80 paragraphs (the registered A2 sample outside the route-B
subsample), one sitting on 2026-11-10, one paragraph off topic (A093_p07); `A2R2_pass2_v1.0.csv` (sha256
`efbfe7b50769096f`), 113 of 113 (paragraph × technology) rows, sittings 2–3 on 2026-11-10. Sheets generated by
`recode_sheet.py --wave 2` (seed 20261110, no first-coding label); coded 33 days after the last first-coding date. The
25 paragraphs re-read in D39 (§4l (6)) are in the population by the author's decision and are listed in
`A2R2_manifest.json`. The session manifest was not committed before the sheet was opened (disclosed in §4n).
**Format correction on the pass-2 export, disclosed.** On the eight rows marked `no_frame_for_this_tech` = 1 the export
left `comparison_target` blank (A080_p10 wind, A041_p02 hydrogen, A082_p02 general, A043_p04 other renewable, A037_p05
general, A072_p36 nuclear, A044_p04 other renewable, A051_p02 nuclear); as for A2R and A3, the agent set `none` on
exactly those eight cells and nothing else, verified cell by cell (export sha256 `a120b87b7509b1d8` →
`9dd62b7e66207295`; the export as returned is kept as `sheets/A2R2_reliability_sheet-2_CODED_as-exported_2026-11-10.csv`).
The subtypes agree with pass 1 on every wind and hydrogen row. Informs only the §4n analyses; never the reference.

**Disclosure (2026-10-14, author's statement) — a consistency question to an AI assistant during the first pass.**
During the first pass of A3, after setting the labels, the author put a consistency question about the `general`
category (the coding note above) to a language-model assistant, a Claude Code session outside the pipeline. That
session read the A3 working sheet, paragraph text and provisional labels included, and the finalised A2 labels,
reported counts, discussed the four A3 paragraphs that carry `general` beside solar and wind against the codebook,
and recommended no label. The author states that the labels had been set before the question was asked. This is
exposure of A3 paragraph text and provisional labels to a language model before finalisation — outside the
pipeline and outside the repository's records, so the listing audit could not cover it; it is not pipeline output,
no A3 prediction by the registered instrument exists, and the runner embargo is unaffected. The text went to the
assistant's provider (Anthropic, Claude Code) outside the pipeline's Bedrock EU route (GUIDE-LLM D.1). **Any change
of an A3 label after this exchange is to be recorded here before finalisation.**


## A2 frame manifest (2026-09-06) — and one gap in the assignment log

`A2_frame_manifest.json` is the frozen sampling frame for the evaluation set: the gap-free candidate
paragraphs of `RESERVED_A2` minus article A011 (the direct exemplar quoted in the prompt), 618
paragraphs in 18 language × outlet strata, with the eligibility rules, the exposure classes, the
generator hash and the pre-freeze registry head inside the document and a canonical digest over it.
The registry head is the terminal `a2_frozen` event (seq 1052) whose `audit_ref` is that digest;
`draw_a2.py` and `build_sheets.py --from-manifest` verify this handshake and refuse otherwise.

**Erratum, disclosed rather than repaired.** The freeze was run as `--freeze-a2
--accept-binding-drift` while three binding-drift records (the `build_sheets.py` code hash recorded
for corpus versions v1–v3 versus the code on disk) were still unaccepted. The flag bypassed the
refusal, but the acceptance event was only ever written on the default code path, which the freeze
branch returned before; so the terminal event was appended and the acceptance never was. Nothing can
be appended after a terminal event without breaking the handshake, so the log will permanently show
those three records as unaccepted. They are on record in two other places: the frame manifest names
the generator that produced it (`0324e0fa…`), and the archive record stores that code's bytes. The
inputs (article files, `manifest.csv`) carried no unaccepted drift at the freeze. The code was fixed
the same day (acceptance recorded before any branch, `--archive` records it too, and the freeze now
refuses while drift is unaccepted; tests L15/L15b, A2-23/23b), and `test_split_registry.py --live`
reports the disclosed records as a note, not a failure.

**The three records, exactly** (drift fingerprint fields `version · component · key · recorded → observed`,
16-hex prefixes as the registry prints them):

| version | component | key | recorded (at version registration) | observed (at the freeze, 2026-09-06) |
|---|---|---|---|---|
| v1 | code | `build_sheets.py` | `cc648f588ee9e0df` | `0324e0fa7b5a6b36` |
| v2 | code | `build_sheets.py` | `cc648f588ee9e0df` | `0324e0fa7b5a6b36` |
| v3 | code | `build_sheets.py` | `cc648f588ee9e0df` | `0324e0fa7b5a6b36` |

The observed hash is the generator hash the frame manifest carries and the hash of `code/build_sheets.py`
in the archive record (`archive-manifest.json`), so the code that froze the frame is preserved and
verifiable (`test_split_registry.py --live`, L15c). Later edits to `build_sheets.py` show up as further
unaccepted code-hash records; they are reported as drift of the generator since the freeze (L15d), which
the digest-bound frame does not depend on. Drift of the inputs stays a failure (L15b).

## A2 sample and session manifests (2026-09-06)

`A2_sample_manifest.json` is the pre-registered draw against the frozen frame: 120 paragraphs (60 German, 60
English) from 56 articles, allocated proportionally within language across the 18 language × outlet strata
under the registered cap of 24 per outlet, drawn with seed 20260905 inside strata sorted by digest, with the
40-paragraph reliability subsample (20 per language, seed 20260906) flagged and the sitting order shuffled
(seed 20260907). Every paragraph carries its stratum sizes, selection probability π = n_h/N_h and design weight
1/π. The manifest names the frame digest and the freeze event; `build_sheets.py --from-manifest` verified that
chain before writing `sheets/A2_evaluation_sheet-1.csv` (gitignored, licensed text) and `A2_manifest.json`,
the session manifest with the per-paragraph text hashes the finalizer checks pass-2 against.

**Coding procedure (route B, decided 2026-09-06: no second coder).** Pass 1 in `A2_evaluation_sheet-1.csv`
in sheet order, three sittings; then `expand_sheet2.py` builds sheet-2 from the ticked technologies; pass 2 in
sheet-2; `finalize.py` for both passes writes `A2_pass1_v1.0.csv` / `A2_pass2_v1.0.csv` and their record into
`sessions.csv` (notes go to the gitignored `private/` sidecar). After a 14-day washout the author re-codes the
40 flagged paragraphs blind, in a fresh random order, without seeing the earlier labels; that re-code informs
only the reliability table (intra-coder temporal stability) and never replaces the reference. The blind
minimum-support threshold *s* must be committed before pass 1 starts.

## A2 threshold seal (written and committed 2026-09-07, commit `7c61b3d`; s = 5)

`A2_threshold_seal.json` holds the author's blind minimum-support threshold *s*: the value, the fixed
reporting rule and support units per table, the digests of the registered sample and frame manifests, the
freeze event, the git head and tree state, a UTC timestamp and the author's one-sentence confirmation that
the value was chosen without inspecting any A2 label. It is written once by

    .venv/bin/python3 tools/pipeline/sample_contract.py --seal-threshold S --author-confirmation "..."

and committed **on a calendar day before the first A2 paragraph is coded**; the evaluator refuses every
evaluation over A2 paragraphs whose threshold is not the sealed one, whose seal is incomplete, uncommitted,
rewritten after its first commit, or committed on or after the first coding day. The seal is a committed
record under the author's control; its digest is also sent to the supervisor before coding as the one
timestamp outside it. The value s = 5 was fixed and sealed by the author on 2026-09-07 (inputs committed in `1870c02`, seal in
`7c61b3d`, digest `4d4fbd87421c0bde…`), three days before the first planned coding sitting.
