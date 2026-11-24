# tools/

The software of the thesis. Start with the `README.md` in the repository root.

```
pipeline/     instrument.py (prompt renderer, request builder, response validator) · preflight.py (gate)
              run_extract.py (runner) · evaluate.py (evaluator) · sample_contract.py (sample and seal contracts)
              reproduce.py and snapshot_check.py (reproduction check) · figures.py, render_instrument_appendix.py (generators)
              later analyses, one script per section of the specification:
                error_breakdown (§4c) · dsl (§4d) · language_arm (§4e) · compare_models (§4f, §4g, §4k)
                codebook_review (§4h) · battery* (§4i) · compare_form (§4j) · compare_a3_candidate (§4b)
                review_analyses, compare_context, missed_elements_sheet (§4l) · compare_gen55 (§4m)
                review_checks_4o (§4o) · review_checks_4p (§4p)
              instrument variants: instrument_v10r3 · instrument_v10f · instrument_v10c · instrument_v11
              instrument/   the pinned prompts, tool configurations and manifests
              test_*.py · INSTRUMENT-DESIGN.md (design record) · pins*.json
annotation/   codebook/ (v0.1 to v1.0, frozen) · labels/ (label files, manifests, seals, session record)
              split_registry.py · draw_a2.py, draw_a3.py · build_sheets.py, expand_sheet2.py, finalize.py, recode_sheet.py
runs/         run records, per-call journals, terminal and prediction files, gate records
              tables/   one folder per evaluated run and per analysis
docs/         the pre-analysis specification, the completion plan and the review log of the prototype plan
```

The pipeline runs in `.venv` (Python 3.13); the annotation scripts use the standard library only and run on the
system `python3` (3.14), which the split registry records.

## Article text is not committed

Anything that can contain article text lives in `annotation/sheets/`, `annotation/labels/private/`, `data/` or
`runs/raw/`, which git ignores. Committed files carry paragraph identifiers, hashes and labels. Three exceptions are
recorded:

1. The pinned prompt `pipeline/instrument/system-prompt_v1.0.txt` quotes one phrase and one figure from paragraph
   `A011_p02`; article A011 is excluded from the evaluation frame for that reason.
2. The codebook files quote short passages of calibration paragraphs as examples.
3. Six legacy label files (`annotation/labels/*_v0.*.csv`, `A1_*_v1.0.csv`) carry the coder's free-text notes, which
   may quote a paragraph. The files are frozen and not edited.

Article metadata (outlet, date, headline, URL) is committed in `corpus/manifest.csv`.
