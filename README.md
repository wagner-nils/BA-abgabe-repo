# Extracting Technology Frames from Renewable Energy News with Large Language Models

Submission repository of the bachelor's thesis (LMU Munich, submitted 25 November 2026): the thesis, its LaTeX
sources, the extraction pipeline, the human reference labels and every run record. It is a copy of the author's
project repository at the commit in `SOURCE_COMMIT`, without the version history. Appendix B of the thesis
(Table 46) lists the files behind every claim.

| Path | Content |
|---|---|
| `BA_NW_2026.pdf` | the submitted thesis |
| `paper/` | the thesis in LaTeX, with its tables, figures, bibliography and the GUIDE-LLM checklist |
| `tools/pipeline/` | instrument (prompt, schema, validator), gate, runner, evaluator, analyses, generators, tests |
| `tools/annotation/` | codebook, split registry, sample draws, sheet builders, label files and manifests |
| `tools/runs/` | run records, journals, prediction files and the evaluator's tables |
| `tools/docs/` | the pre-analysis specification (the registered rules and every later declaration), the completion plan and the review log of the prototype plan |
| `corpus/` | corpus manifest (metadata only) |

Not included: article and paragraph text, raw model responses, coding sheets and translations (the rights to
redistribute the texts were not clarified; thesis, Section 7.5), and credentials (`.env` is ignored by git).

## Who did what

The author's account of the division of work: he performed the human reference coding and re-coding, personally
checked all 60 translation pairs, and made the decisions attributed to him in the records of this repository. He
describes the methodological and architectural design as joint work with Claude: his own reading of the literature
informed many of the proposals he brought to their discussions, and he played a substantial part in shaping the
resulting architecture. His initial material included bullet points and complete paragraphs he had written himself;
some of these paragraphs were subsequently revised throughout by Claude. As the thesis's overview of tools used
states, he proofread every adopted passage. The AI-assisted drafting, implementation and reviews documented under
`tools/docs/` formed part of that workflow.

## Set-up

Python 3.13 for the pipeline, Python 3.14 as `python3` for the annotation scripts, TeX Live 2025 with `latexmk` and
`biber`. Amazon Bedrock access (`eu-central-1`) is needed for model calls only.

```sh
git init -b main && git add -A && git commit -m "Submission copy"   # several checks ask git about ignored and changed files
uv sync --locked                                                    # creates .venv from uv.lock
[ -f .env ] || cp .env.example .env                                 # credentials, for model calls only
```

## Build the thesis

```sh
(cd paper && latexmk -pdf -interaction=nonstopmode BA_NW_2026.tex)   # writes paper/build/BA_NW_2026.pdf
```

## Reproduce the reported numbers

No model call and no article text are needed.

```sh
# 1. Re-run or re-render every analysis and compare it with the committed tables and figures; cross-check the numbers of the main text
.venv/bin/python3 tools/pipeline/snapshot_check.py

# 2. Tests
for t in tools/pipeline/test_*.py;   do echo "$t: $(.venv/bin/python3 "$t" | tail -1)"; done
for t in tools/annotation/test_*.py; do echo "$t: $(python3 "$t" | tail -1)"; done

# 3. Recompute the registered evaluation (run a2_sonnet_20261013 against the first coding)
L=tools/annotation/labels
.venv/bin/python3 tools/pipeline/snapshot_check.py --run tools/pipeline/evaluate.py \
  --run-record tools/runs/a2_sonnet_20261013.json --manifest $L/A2_sample_manifest.json \
  --gold-pass1 $L/A2_pass1_v1.0.csv --gold-pass2 $L/A2_pass2_v1.0.csv --threshold 5 \
  --reliability $L/A2_pass1_v1.0.csv $L/A2_pass2_v1.0.csv $L/A2R_pass1_v1.0.csv $L/A2R_pass2_v1.0.csv \
  --reliability-route B --washout-days 4 \
  --washout-deviation "author's decision of 2026-10-12 to begin the re-code at once, to recover schedule; coded 2026-10-13, five days after the last first-coding date (2026-10-08); registered 14 days, applied minimum 4; agent advised against; disclosed in the specification, labels README, usage log, GUIDE-LLM and Chapter 5"
```

Expected: step 1 prints 14 lines `ok` and one `skip` (the codebook-review table needs withheld files) and lists two
numbers it cannot match, both from cited literature. Step 2 reports `0 failed` everywhere except one check of
`test_language_arm.py`. Step 3 prints element micro-F1 0.643 and technology 0.935; afterwards `git diff` shows only
provenance fields in `metrics.json`.

`snapshot_check.py` exists because the evaluator verifies the registration order through git (threshold sealed
before coding, reference committed before the run), which a copy without history cannot show. The wrapper reads the
commit order from `tools/runs/git_provenance.json`, a record written from the project repository, and compares the
files here with the recorded digests. The dates themselves can be verified only in the project repository.

## Run the pipeline

A run needs the article files as collected, under `corpus/articles/` (see `corpus/README.md`).

```sh
# 1. Rebuild the paragraph store (writes tools/annotation/sheets/ALL_paragraphs.csv, ignored by git)
python3 tools/annotation/build_sheets.py

# 2. Extraction: the runner certifies its configuration first, then makes one call per paragraph and execution
.venv/bin/python3 tools/pipeline/run_extract.py --manifest tools/annotation/labels/A2_sample_manifest.json \
  --model sonnet --repeats 3 --run-id a2_sonnet_rerun_001 --dev

# 3. Evaluation against the human reference
.venv/bin/python3 tools/pipeline/snapshot_check.py --run tools/pipeline/evaluate.py \
  --run-record tools/runs/a2_sonnet_rerun_001.json --manifest tools/annotation/labels/A2_sample_manifest.json \
  --gold-pass1 tools/annotation/labels/A2_pass1_v1.0.csv --gold-pass2 tools/annotation/labels/A2_pass2_v1.0.csv \
  --threshold 5 --dev
```

- Keep `--dev` for any re-run: without it the runner records a run on the evaluation sample as the registered one.
  The registered run of the thesis is `a2_sonnet_20261013`.
- A re-run does not reproduce the registered outputs exactly; the provider offers no seed (thesis, Section 6.4).
- Step 1 was checked with the article files in place; steps 2 and 3 were not re-executed for this copy.
- The code is bound to this study's corpus, registry and sample. Another corpus needs its own registry, its own
  human-coded sample and adapted paths.
