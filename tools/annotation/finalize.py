#!/usr/bin/env python3
"""Turn a completed working sheet into a committed label file.

The working sheet in `sheets/` carries the paragraph text and is gitignored,
because article text is licensed. What this writes into `labels/` is paragraph
IDs plus labels plus provenance, and nothing else -- that is what makes the
replication package releasable regardless of how the licence question is
answered, and it is unanswered.

It also appends a line to `labels/sessions.csv`, the run record: who coded what,
when, under which codebook version, in how many sittings. The git commit of that
file is the evidence that the annotation preceded the model run, which no
after-the-fact reconstruction can supply.

    python tools/annotation/finalize.py sheets/A1_development_sheet-2_CODED.csv A1_pass2

EVERYTHING IS CHECKED BEFORE ANYTHING IS WRITTEN. A warning printed after the
file exists is not a guard; the earlier version of this script warned about a
missing `sitting` column only after committing the labels, which is how A0 pass 1
ended up recorded as `unrecorded` forever.

Refusals, each protecting a number that cannot be repaired afterwards:

  mixed codebook versions    an agreement figure across versions is not interpretable
  stale codebook version     labels coded against a superseded instrument, silently
                             stamped with the old one      [--old-codebook]
  model in `annotator`       a model coding run entering the human-label slot; the
                             agreement statistic then measures family correlation
                             and nothing distinguishes it afterwards
                             [--annotator-is-a-model, writes a MODEL-marked file]
  blank annotator            provenance that cannot be reconstructed
  missing / zero `sitting`   drift within a session becomes unmeasurable. `0` is
                             not a sitting number           [--no-sittings]
  missing `coded_at`         the annotation-preceded-the-run claim rests on it
  duplicate row / paragraph  double-counted in every aggregate, silently
  unknown output column      the text strip is name-based, so an unknown column is
                             assumed to be licensed text until declared
  `no_frame_present` set on   the paragraph names no study technology, so whether it
  an off-topic paragraph      FRAMES one is not a question that arises. Conflating the
                             two makes the reported nfp share measure the sampling
                             frame instead of the corpus -- codebook X10

Off-topic paragraphs are COUNTED, not refused. A paragraph in a selected article
that names no study technology is a legitimate outcome of making the paragraph the
unit (codebook X4), and X9 records one from A0 as "coded with no technology,
correctly" -- a measured cost belonging in the limitations chapter. Six of A1's 35
paragraphs are of this kind. They are written to the label file with every
technology 0 and `no_frame_present` blank, so the exclusion rate stays
reconstructable, and they generate no pass-2 row.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codebook_reader  # noqa: E402

HERE = Path(__file__).resolve().parent
LABELS = HERE / "labels"

# --------------------------------------------------------------------------- #
# What may be written. An ALLOWLIST, not a blocklist: finding #18 of plan review
# round 2 was that a renamed article-text column whose values happen to be short
# passes a length test, and many real pilot paragraphs are under 400 characters.
# So an unknown column is refused rather than measured.
# --------------------------------------------------------------------------- #

TEXT_COLUMNS = {"paragraph_text", "headline"}          # stripped, never written

ALLOWED_COLUMNS = {
    # identity
    "row_id", "paragraph_id", "article_id", "technology",
    # context that is metadata, not article prose
    "lang", "outlet", "country", "pub_date",
    # pass-1 technology ticks
    "tech_general", "tech_solar", "tech_wind", "tech_hydrogen", "tech_nuclear",
    "tech_other_renewable", "tech_storage", "wind_siting", "hydrogen_pathway",
    "no_frame_present",
    # pass-2 frame elements
    "F1a", "F1b", "F2a", "F2b", "F2c", "F3a", "F3b", "F4a", "F4b",
    "F5a", "F5b", "F6a", "F6b", "F7a", "F7b", "F8a", "F9",
    "no_frame_for_this_tech",
    # per-frame evaluation
    "eval_costs", "eval_jobs", "eval_climate", "eval_security", "eval_reliability",
    "eval_feasibility", "eval_siting", "eval_regulation", "eval_other",
    # the rest of the record
    "comparison_target", "comparison_note", "tiebreak_used", "speaker",
    "confidence", "note",
    # provenance
    "annotator", "annotator_type", "coded_at", "codebook_version", "sitting",
    "text_sha256",
}

# Free text the annotator writes. Everything else is an enum, a flag or an ID.
FREE_TEXT = {"note", "comparison_note"}

# A sheet must BE one of these shapes. Validating only the columns that happen to
# be present meant a file carrying nothing but row_id and provenance passed clean,
# and omitting an eval_* column silently disabled the orphan check for that frame
# -- round-4 findings #1 and #3.
PROVENANCE_REQUIRED = {"annotator", "annotator_type", "coded_at", "codebook_version"}

PASS1_REQUIRED = PROVENANCE_REQUIRED | {
    "paragraph_id", "tech_general", "tech_solar", "tech_wind", "tech_hydrogen",
    "tech_nuclear", "tech_other_renewable", "tech_storage", "no_frame_present",
    "confidence", "note",
}

# Values that must be present on every row of a pass-2 sheet, because a blank
# one is an uncoded row rather than a substantive answer. `comparison_target`
# has `none` for "no comparison" and `speaker` has `unattributed`, so blank is
# never the right way to say either.
PASS2_REQUIRED = PROVENANCE_REQUIRED | {
    "row_id", "paragraph_id", "technology", "no_frame_for_this_tech",
    "comparison_target", "speaker", "confidence", "note",
}
PASS2_NONBLANK = {"row_id", "paragraph_id", "technology", "comparison_target",
                  "speaker", "confidence"}


def sheet_kind(cols: list[str]) -> str:
    """pass2 if it carries per-technology rows, pass1 if per-paragraph ticks."""
    if "technology" in cols and "row_id" in cols:
        return "pass2"
    if any(c.startswith("tech_") for c in cols):
        return "pass1"
    return "unknown"

MAX_FREE_TEXT_LEN = 600     # a note longer than this is probably pasted prose
MAX_OTHER_LEN = 120         # nothing else is a sentence

MODEL_MARKERS = ("gpt", "claude", "sonnet", "opus", "haiku", "gemini", "llama",
                 "mistral", "qwen", "deepseek", "grok", "o1-", "o3-", "o4-",
                 "-ai", "ai-", "bot", "model", "llm")

# Permitted values are read from the FROZEN CODEBOOK, never copied here --
# plan section A.6. A validator with its own copy of the technology list is a
# fourth source of truth that drifts silently.

# `wind_siting` and `hydrogen_pathway` are SUBTYPE columns, not flags: the
# codebook gives them `onshore | offshore | unspecified` and
# `green | blue | gray | unspecified`. Treating them as binary rejected every
# correct value -- round-4 finding #2, and it would have fired on the first A1
# row that mentions wind.
BINARY_EXACT = {"no_frame_present", "no_frame_for_this_tech", "tiebreak_used"}
BINARY_OK = {"", "0", "1"}
BOOL_ALIASES = {"true": "1", "false": "0", "TRUE": "1", "FALSE": "0"}

DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y")


def is_binary_column(c: str) -> bool:
    if c in BINARY_EXACT:
        return True
    if c.startswith("tech_"):
        return True
    # F1a .. F9, but not "FRAME" or similar
    return bool(re.fullmatch(r"F\d+[a-z]?", c))


def parse_date(v: str):
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(v, f).date()
        except ValueError:
            continue
    return None


def read_sheet(path: Path) -> tuple[list[dict], list[str], str]:
    raw = path.open(encoding="utf-8-sig", newline="").read()
    head = raw.split("\n")[0]
    delim = ";" if head.count(";") > head.count(",") else ","
    reader = csv.DictReader(raw.splitlines(), delimiter=delim)
    cols = list(reader.fieldnames or [])
    rows = [r for r in reader
            if not str(list(r.values())[0] or "").startswith("#")]
    return rows, cols, delim


def session_manifest(session: str) -> tuple[Path, dict | None]:
    """The session's manifest (labels/<base>_manifest.json), if one exists.

    `session` is `<base>_pass1` / `<base>_pass2`; the manifest is per base. A
    manifest marked `reconstructed` belongs to a legacy (schema 1) session and is
    evidence, not a gate: the six committed files predate manifests and are not
    re-validated against them here.
    """
    base = session.rsplit("_pass", 1)[0]
    path = LABELS / f"{base}_manifest.json"
    if not path.exists():
        return path, None
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"{path.name} is not valid JSON: {exc}")


def text_hash(text: str) -> str:
    """The identity hash build_sheets.py writes: sha256 of the paragraph text, 16 hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def frozen_codebook_version() -> str | None:
    """Version of the newest codebook marked `frozen: true`, or None."""
    found = []
    for f in sorted((HERE / "codebook").glob("codebook-v*.yaml")):
        text = f.read_text(encoding="utf-8")
        m = re.search(r'^version:\s*"?([\d.]+)"?', text, re.M)
        if m and re.search(r"^frozen:\s*true\s*$", text, re.M):
            found.append(m.group(1))
    if not found:
        return None
    return max(found, key=lambda v: tuple(int(x) for x in v.split(".")))


def val(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def validate(rows: list[dict], cols: list[str],
             flags: set[str], manifest: dict | None = None
             ) -> tuple[list[str], list[str], list[str], str, str]:
    """Return (problems, warnings, off_topic, codebook_version, model_marker).

    Writes nothing. `problems` refuse the write; `warnings` do not, and are
    printed either way. `off_topic` lists the paragraphs that name no study
    technology, so the caller can report the rate rather than lose it.
    """
    problems: list[str] = []
    warnings: list[str] = []
    off_topic: list[str] = []

    # ---- the sheet is one of the two known shapes, with no missing columns - #
    dup_cols = sorted({c for c in cols if cols.count(c) > 1})
    if dup_cols:
        problems.append(f"duplicate column header(s) {dup_cols}. Only one of each would "
                        f"survive into the label file, chosen arbitrarily.")

    # Resolve the codebook the ROWS declare, before any value or column check:
    # a v0.3 sheet must be validated against v0.3's 16 elements, not v1.0's 17.
    declared = sorted({val(r, "codebook_version") for r in rows} - {""})
    cb = None
    cb_error = ""
    try:
        cb = (codebook_reader.load_version(declared[0]) if len(declared) == 1
              else codebook_reader.load())
    except Exception as exc:  # noqa: BLE001
        try:
            cb = codebook_reader.load()
            cb_error = (f"rows declare codebook v{declared[0] if declared else '?'}, for which no "
                        f"codebook file was found ({exc}); validating against the frozen version "
                        f"instead, which may refuse correct values")
        except Exception as exc2:  # noqa: BLE001
            cb_error = (f"could not read any codebook, so no label value can be validated: "
                        f"{type(exc2).__name__}: {exc2}")
    if cb_error:
        problems.append(cb_error)
    if cb is None:
        return problems, warnings, off_topic, (declared[0] if declared else "unknown"), ""

    kind = sheet_kind(cols)
    if kind == "unknown":
        problems.append(
            "cannot tell whether this is a pass-1 or a pass-2 sheet: it has neither "
            "`tech_*` columns nor `row_id` + `technology`. A sheet that matches no known "
            "shape is not validated against anything, so it is refused rather than "
            "written.")
    else:
        required = PASS1_REQUIRED if kind == "pass1" else PASS2_REQUIRED
        missing = sorted(required - set(cols))
        if missing:
            problems.append(
                f"this looks like a {kind} sheet and is missing required column(s) "
                f"{missing}. Validating only the columns that happen to be present is how "
                f"a structurally empty file passes clean, and a missing `eval_*` column "
                f"silently disables the orphan check for that frame.")
        if kind == "pass2":
            gone = sorted({f"eval_{f}" for f in cb.frame_derivation} - set(cols))
            if gone:
                problems.append(
                    f"pass-2 sheet is missing evaluation column(s) {gone} for codebook "
                    f"v{cb.version}. Each frame needs one, or its fired elements cannot be "
                    f"checked for an evaluation at all.")
            gone_el = sorted(set(cb.frame_elements) - set(cols))
            if gone_el:
                problems.append(f"pass-2 sheet is missing frame element column(s) {gone_el} "
                                f"for codebook v{cb.version}.")

    # ---- the output columns are known ------------------------------------ #
    keep = [c for c in cols if c not in TEXT_COLUMNS]
    unknown = [c for c in keep if c not in ALLOWED_COLUMNS]
    if unknown:
        problems.append(
            f"unknown output column(s) {unknown}. The text strip is name-based, so a column "
            f"nobody has declared is assumed to carry licensed article text. Add it to "
            f"ALLOWED_COLUMNS (metadata/label) or TEXT_COLUMNS (article text).")

    # ---- shape: no column carries prose ---------------------------------- #
    for c in keep:
        if c not in ALLOWED_COLUMNS:
            continue
        cap = MAX_FREE_TEXT_LEN if c in FREE_TEXT else MAX_OTHER_LEN
        longest = max((len(val(r, c)) for r in rows), default=0)
        if longest > cap:
            problems.append(
                f"column {c!r} has a value of {longest} characters (cap {cap}). "
                f"{'Notes this long are usually pasted paragraph text.' if c in FREE_TEXT else 'This column should hold an enum, a flag or an ID.'}")

    # ---- codebook version ------------------------------------------------ #
    versions = set(declared)
    if len(versions) > 1:
        problems.append(f"rows carry mixed codebook versions {sorted(versions)}. "
                        f"An agreement figure computed across versions is not interpretable.")
    version = sorted(versions)[0] if versions else "unknown"
    if not versions:
        problems.append("`codebook_version` is empty on every row. Which instrument was "
                        "this coded against?")
    frozen = frozen_codebook_version()
    if frozen and version != frozen and "--old-codebook" not in flags:
        problems.append(
            f"rows are stamped codebook v{version}, but the frozen instrument is v{frozen}. "
            f"Either the sheet was coded against a superseded codebook, or the coding "
            f"interface is stamping a stale version -- check which. A wrong version stamp is "
            f"worse than a missing one, because every downstream figure trusts it. "
            f"If the labels really were coded under v{version}, pass --old-codebook.")

    # ---- annotator provenance -------------------------------------------- #
    marker = ""
    blank = sum(1 for r in rows if not val(r, "annotator"))
    if blank:
        problems.append(f"`annotator` is blank on {blank} row(s). A blank annotator is not "
                        f"provenance -- it is a row nobody can attribute.")
    coders = {val(r, "annotator") for r in rows} - {""}
    # An explicit annotator_type is the structural answer; the substring list is a
    # backstop for sheets written before that column existed. Requiring the column
    # is what makes it more than decoration -- a blank annotator_type on a
    # model-written sheet was the hole the first version of this guard left open.
    declared = {val(r, "annotator_type").lower() for r in rows}
    if "annotator_type" not in cols:
        problems.append(
            "the sheet has no `annotator_type` column. Provenance cannot rest on whether "
            "the annotator's name happens to contain a model substring: run "
            "build_sheets.py / expand_sheet2.py to regenerate the sheet with the column, "
            "or add it with the value `human` on every row if you coded it yourself.")
    else:
        blank_type = sum(1 for r in rows if not val(r, "annotator_type"))
        if blank_type:
            problems.append(f"`annotator_type` is blank on {blank_type} row(s). It must be "
                            f"`human` or `model` -- blank is how a model run enters the "
                            f"human namespace unnoticed.")
        bad_type = sorted(declared - {"human", "model", ""})
        if bad_type:
            problems.append(f"`annotator_type` has value(s) other than human/model: "
                            f"{bad_type}")
        if len({d for d in declared if d}) > 1:
            problems.append(f"`annotator_type` is not consistent across the file: "
                            f"{sorted(d for d in declared if d)}. One session, one "
                            f"provenance.")
    suspect = sorted(c for c in coders if any(m in c.lower() for m in MODEL_MARKERS))
    if "model" in declared or suspect:
        if "--annotator-is-a-model" not in flags:
            problems.append(
                f"`annotator` names a model, or `annotator_type` says model: "
                f"{suspect or sorted(declared)}. Human labels and model labels cannot share "
                f"the labels/ namespace: an agreement statistic computed against model labels "
                f"measures family correlation, not agreement, and the defect is undetectable "
                f"once the file is committed. If this really is a model run you want to keep, "
                f"pass --annotator-is-a-model and it is written with a MODEL marker in the "
                f"filename.")
        else:
            marker = "_MODEL"

    # ---- dates and sittings ---------------------------------------------- #
    undated = sum(1 for r in rows if not val(r, "coded_at"))
    if undated:
        problems.append(f"`coded_at` is empty on {undated} row(s). The claim that annotation "
                        f"preceded the model run rests on this column plus the commit date.")
    if "sitting" in cols:
        sittings = [val(r, "sitting") for r in rows]
        zero = sum(1 for s in sittings if s == "0")
        empty = sum(1 for s in sittings if s == "")
        if (zero or empty) and "--no-sittings" not in flags:
            problems.append(
                f"`sitting` is {'zero' if zero else 'empty'} on "
                f"{zero or empty} row(s). Sittings are numbered from 1; `0` is a placeholder, "
                f"not a sitting. Without it, drift within the session is unmeasurable -- A0 "
                f"pass 1 is recorded as `unrecorded` for exactly this reason. "
                f"Pass --no-sittings to accept that permanently.")
    elif "--no-sittings" not in flags:
        problems.append("the sheet has no `sitting` column at all. Pass --no-sittings to "
                        "accept that drift within this session cannot be measured.")

    # ---- uniqueness ------------------------------------------------------- #
    for key in ("row_id", "paragraph_id"):
        if key not in cols:
            continue
        ids = [val(r, key) for r in rows]
        # paragraph_id legitimately repeats in a pass-2 sheet (one row per technology)
        if key == "paragraph_id" and "technology" in cols:
            ids = [f"{val(r, 'paragraph_id')}__{val(r, 'technology')}" for r in rows]
            key = "paragraph_id x technology"
        dupes = [i for i, n in Counter(ids).items() if n > 1 and i]
        if dupes:
            problems.append(
                f"duplicate {key}: {dupes[:5]}"
                f"{f' (+{len(dupes) - 5} more)' if len(dupes) > 5 else ''}. "
                f"A duplicate is double-counted in every aggregate, silently. Note that "
                f"(paragraph x technology) is the frozen scoring unit (plan section A.9): if two "
                f"speakers evaluate the same technology in opposite directions, that is one "
                f"row with `mixed` and a note, NOT two rows -- see annotation/README.md, "
                f"'What the label captures'.")
        if any(not i for i in ids):
            problems.append(f"{key} is empty on at least one row.")

    # ---- every categorical and binary field, against THAT version's lists - #
    def bad_values(col: str, permitted: set[str], allow_blank: bool = True) -> list[str]:
        if col not in cols:
            return []
        seen = {val(r, col) for r in rows}
        if allow_blank:
            seen -= {""}
        return sorted(v for v in seen if v.lower() not in permitted)

    for col, permitted, label in (
        ("technology", set(cb.technologies), "codebook technologies"),
        ("comparison_target", set(cb.comparison_targets), "codebook comparison targets"),
        ("speaker", set(cb.speakers), "codebook speakers"),
    ):
        bad = bad_values(col, permitted)
        if bad:
            problems.append(f"{col!r} has value(s) not in the {label} "
                            f"(v{cb.version}): {bad}")

    # Subtype columns are enums from the codebook, not flags.
    for col, permitted in cb.subtypes.items():
        bad = bad_values(col, set(permitted))
        if bad:
            problems.append(f"{col!r} has value(s) outside {permitted}: {bad}")

    # Blank is not an answer where the codebook supplies one for "nothing here":
    # `comparison_target: none`, `speaker: unattributed`.
    if kind == "pass2":
        for col in sorted(PASS2_NONBLANK):
            if col not in cols:
                continue
            blank = sum(1 for r in rows if not val(r, col))
            if blank:
                hint = {"comparison_target": " Use `none` where nothing is compared.",
                        "speaker": " Use `unattributed` where no source is identifiable."}
                problems.append(f"{col!r} is blank on {blank} row(s). It is required on every "
                                f"pass-2 row.{hint.get(col, '')}")
    missing_version = sum(1 for r in rows if not val(r, "codebook_version"))
    if missing_version:
        problems.append(f"`codebook_version` is blank on {missing_version} row(s). A mix of "
                        f"blank and stamped rows previously passed, because blanks were "
                        f"discarded before the mixed-version check ran.")

    for c in (c for c in cols if c.startswith("eval_")):
        bad = bad_values(c, set(cb.evaluations))
        if bad:
            problems.append(f"{c!r} has value(s) outside {cb.evaluations}: {bad}")

    # confidence is required, not merely constrained: a blank confidence hides
    # instrument uncertainty rather than removing it.
    if "confidence" in cols:
        permitted = set(cb.confidences)
        bad = sorted({val(r, "confidence") for r in rows} - permitted - {""})
        blank = sum(1 for r in rows if not val(r, "confidence"))
        if bad:
            problems.append(f"`confidence` has value(s) outside {sorted(permitted)}: {bad}")
        if blank:
            problems.append(f"`confidence` is blank on {blank} row(s). It is a required "
                            f"field: a row with no confidence cannot be separated from a "
                            f"row the annotator was sure about.")

    for c in (c for c in cols if is_binary_column(c)):
        seen = {BOOL_ALIASES.get(val(r, c), val(r, c)) for r in rows}
        bad = sorted(seen - BINARY_OK)
        if bad:
            problems.append(f"{c!r} is a binary field with value(s) other than 0/1: {bad}")

    # ---- dates and sitting numbers parse --------------------------------- #
    if "coded_at" in cols:
        unparsed = sorted({v for v in (val(r, "coded_at") for r in rows)
                           if v and parse_date(v) is None})
        if unparsed:
            problems.append(f"`coded_at` value(s) do not parse as a date "
                            f"{DATE_FORMATS}: {unparsed}")
    if "sitting" in cols:
        nonnumeric = sorted({v for v in (val(r, "sitting") for r in rows)
                             if v and not (v.isdigit() and int(v) >= 1)})
        if nonnumeric:
            problems.append(f"`sitting` value(s) are not positive integers: {nonnumeric}")

    # ---- relational invariants ------------------------------------------- #
    # These are the ones calibration reported as holding ("every fired frame
    # received an evaluation -- no orphans, 100 % coverage"). Observed once is
    # not asserted, so they are asserted here.
    eval_col = cb.eval_column_for
    present_elements = [e for e in cb.frame_elements if e in cols]
    if present_elements and any(c.startswith("eval_") for c in cols):
        orphans, ghosts = [], []
        for r in rows:
            rid = val(r, "row_id") or val(r, "paragraph_id")
            fired = {e for e in present_elements
                     if BOOL_ALIASES.get(val(r, e), val(r, e)) == "1"}
            needed = {eval_col[e] for e in fired if eval_col.get(e) in cols}
            for col in sorted(needed):
                if not val(r, col):
                    orphans.append(f"{rid}:{col}")
            for col in (c for c in cols if c.startswith("eval_")):
                if val(r, col) and col not in needed:
                    ghosts.append(f"{rid}:{col}")
        if orphans:
            problems.append(
                f"{len(orphans)} fired frame(s) with no evaluation: {orphans[:6]}"
                f"{' ...' if len(orphans) > 6 else ''}. An element that fires without an "
                f"evaluation is an orphan -- calibration reported 100 % coverage, so this "
                f"is a regression, not a known gap.")
        if ghosts:
            problems.append(
                f"{len(ghosts)} evaluation(s) on a frame that did not fire: {ghosts[:6]}"
                f"{' ...' if len(ghosts) > 6 else ''}. The evaluation attaches to a fired "
                f"frame; filling it otherwise makes the per-frame agreement figure "
                f"uninterpretable.")

    # A pass-2 skeleton has every element 0 and no_frame_for_this_tech 0. Once
    # provenance, confidence, speaker and comparison are filled it passed clean --
    # the invariant below only checked the contradictory direction (round-5 #2).
    # Exactly one of {an element fires, no_frame_for_this_tech=1} must hold.
    if kind == "pass2" and present_elements and "no_frame_for_this_tech" in cols:
        uncoded = [(val(r, "row_id") or val(r, "paragraph_id"))
                   for r in rows
                   if not any(BOOL_ALIASES.get(val(r, e), val(r, e)) == "1"
                              for e in present_elements)
                   and BOOL_ALIASES.get(val(r, "no_frame_for_this_tech"),
                                        val(r, "no_frame_for_this_tech")) != "1"]
        if uncoded:
            problems.append(
                f"{len(uncoded)} substantively uncoded row(s): no frame element fires and "
                f"`no_frame_for_this_tech` is not 1: {uncoded[:6]}"
                f"{' ...' if len(uncoded) > 6 else ''}. That is the generated skeleton, not "
                f"an answer. Exactly one of the two must hold on every row -- either "
                f"something frames this technology, or you state that nothing does.")

    # Pass 1: exactly ONE of two questions is answerable per paragraph.
    #
    # This check used to refuse any paragraph with no technology ticked, on the
    # premise that "every candidate paragraph names at least one technology".
    # That premise is false. Articles are selected by keyword, paragraphs are
    # not: A1 carries six paragraphs that name none -- Guterres on fossil fuels,
    # a company portrait about stair railings. Codebook X4 makes the paragraph
    # the unit and X11 permits ticking only what the paragraph itself states, so
    # coding no technology there APPLIES the instrument rather than skipping it,
    # and X9 already records one such paragraph from A0 as "coded with no
    # technology, correctly" -- a measured cost, not an error.
    #
    # So `no_frame_present` is required on a paragraph that names a technology
    # and must be BLANK on one that does not: the annotator either states
    # whether the technology is framed, or the question does not arise. Letting
    # one column carry both meanings is the defect X10 names -- the reported
    # "no frame present" share would measure the sampling frame, not the corpus.
    if kind == "pass1":
        tech_cols = [c for c in cols if c.startswith("tech_")]
        blank_nfp, nfp_off_topic, silent = [], [], []
        for r in rows:
            pid = val(r, "paragraph_id")
            nfp = BOOL_ALIASES.get(val(r, "no_frame_present"),
                                   val(r, "no_frame_present"))
            if any(BOOL_ALIASES.get(val(r, c), val(r, c)) == "1" for c in tech_cols):
                if nfp not in {"0", "1"}:
                    blank_nfp.append(pid)
            else:
                off_topic.append(pid)
                if nfp in {"0", "1"}:
                    nfp_off_topic.append(pid)
                if not val(r, "note"):
                    silent.append(pid)
        if blank_nfp:
            problems.append(
                f"{len(blank_nfp)} paragraph(s) name a technology but have "
                f"`no_frame_present` neither 0 nor 1: {blank_nfp[:6]}"
                f"{' ...' if len(blank_nfp) > 6 else ''}. It is a substantive value.")
        if nfp_off_topic:
            problems.append(
                f"{len(nfp_off_topic)} paragraph(s) have no technology ticked yet carry a "
                f"`no_frame_present` value: {nfp_off_topic[:6]}"
                f"{' ...' if len(nfp_off_topic) > 6 else ''}. `no_frame_present` says a "
                f"NAMED technology is not framed; it cannot also mean that none is named. "
                f"Clear it, and the paragraph counts as off topic.")
        if silent:
            warnings.append(
                f"{len(silent)} off-topic paragraph(s) carry no note: {silent[:6]}"
                f"{' ...' if len(silent) > 6 else ''}. The exclusion rate goes into the "
                f"limitations chapter, and a reason per paragraph is what makes it "
                f"auditable later. Not refused -- the label file records which they are.")

    # ---- #7 : the codebook's conditional note rules, and canonical row_id -- #
    if "note" in cols:
        if "F9" in cols:
            f9_no_note = [(val(r, "row_id") or val(r, "paragraph_id")) for r in rows
                          if BOOL_ALIASES.get(val(r, "F9"), val(r, "F9")) == "1"
                          and not val(r, "note")]
            if f9_no_note:
                problems.append(f"F9 (`other frame`) fires with no note on {len(f9_no_note)} "
                                f"row(s): {f9_no_note[:6]}. A residual category without a "
                                f"description is unusable -- F9 fired zero times in "
                                f"calibration, so any hit is a finding that needs words.")
        if "comparison_target" in cols:
            need_note = [(val(r, "row_id") or val(r, "paragraph_id")) for r in rows
                         if val(r, "comparison_target") in {"other", "multiple"}
                         and not (val(r, "comparison_note") or val(r, "note"))]
            if need_note:
                problems.append(f"`comparison_target` is `other`/`multiple` with no note on "
                                f"{len(need_note)} row(s): {need_note[:6]}. `other` must say "
                                f"what, `multiple` must list them -- otherwise the comparison "
                                f"field's own agreement figure is uninterpretable.")
    if kind == "pass2" and {"row_id", "paragraph_id", "technology"} <= set(cols):
        wrong = [val(r, "row_id") for r in rows
                 if val(r, "row_id") != f"{val(r, 'paragraph_id')}__{val(r, 'technology')}"]
        if wrong:
            problems.append(f"{len(wrong)} row_id(s) are not `paragraph_id__technology`: "
                            f"{wrong[:6]}. Evaluation joins gold to model output on row_id, "
                            f"so a non-canonical id silently drops the row from the match.")

    # no_frame_for_this_tech = 1 must mean no element fired
    if "no_frame_for_this_tech" in cols and present_elements:
        contradictions = [
            (val(r, "row_id") or val(r, "paragraph_id"))
            for r in rows
            if BOOL_ALIASES.get(val(r, "no_frame_for_this_tech"),
                                val(r, "no_frame_for_this_tech")) == "1"
            and any(BOOL_ALIASES.get(val(r, e), val(r, e)) == "1" for e in present_elements)
        ]
        if contradictions:
            problems.append(f"{len(contradictions)} row(s) say `no_frame_for_this_tech` "
                            f"while an element fires: {contradictions[:6]}. These are "
                            f"mutually exclusive by definition.")

    # ---- sheet schema 2: the row is bound to the text it was coded against -- #
    # A current-schema session has a manifest that is not a reconstruction. Then
    # every row must carry `text_sha256`, the hash is RECOMPUTED from the sheet's
    # own paragraph text (the cell alone would pass an edited text with a stale
    # hash), it must be in the manifest, and a pass-2 sheet's (paragraph x
    # technology) keys must equal the keys the finalized pass 1 implied -- a
    # missing relation is otherwise an "invention" of the model at evaluation
    # time (plan-review round 2, finding 6).
    if manifest is not None and not manifest.get("reconstructed"):
        if "text_sha256" not in cols or "paragraph_text" not in cols:
            problems.append("sheet schema 2 requires `text_sha256` and `paragraph_text` on the "
                            "sheet (regenerate with build_sheets.py / expand_sheet2.py).")
        else:
            known = {p_["paragraph_id"]: p_["text_sha256"] for p_ in manifest["paragraphs"]}
            bad_cell, bad_manifest, unknown = [], [], []
            for r in rows:
                pid = val(r, "paragraph_id")
                h = text_hash(r.get("paragraph_text") or "")
                if h != val(r, "text_sha256"):
                    bad_cell.append(pid)
                if pid not in known:
                    unknown.append(pid)
                elif known[pid] != h:
                    bad_manifest.append(pid)
            if bad_cell:
                problems.append(f"{len(bad_cell)} row(s) whose paragraph text no longer hashes "
                                f"to their `text_sha256` cell: {sorted(set(bad_cell))[:6]}. The "
                                f"text was edited after the sheet was generated.")
            if unknown:
                problems.append(f"{len(unknown)} paragraph id(s) not in the session manifest "
                                f"{manifest['session']}: {sorted(set(unknown))[:6]}.")
            if bad_manifest:
                problems.append(f"{len(bad_manifest)} row(s) whose text hash differs from the "
                                f"manifest's: {sorted(set(bad_manifest))[:6]}. The sheet was not "
                                f"generated from this manifest, or the text changed.")
            if kind == "pass1":
                covered = {val(r, "paragraph_id") for r in rows}
                gap = sorted(set(known) - covered)
                if gap:
                    problems.append(f"pass 1 covers {len(covered)} of the manifest's "
                                    f"{len(known)} paragraphs; missing: {gap[:6]}. Every "
                                    f"paragraph needs a pass-1 row (off topic counts).")
            if kind == "pass2":
                want = manifest.get("pass1_keys")
                if want is None:
                    problems.append("the session manifest carries no `pass1_keys`: finalize pass 1 "
                                    "first, so pass 2 can be checked against it.")
                else:
                    have = {f"{val(r, 'paragraph_id')}__{val(r, 'technology')}" for r in rows}
                    only1, only2 = sorted(set(want) - have), sorted(have - set(want))
                    if only1 or only2:
                        problems.append(
                            f"pass-2 rows do not match the finalized pass-1 keys: "
                            f"{len(only1)} missing {only1[:6]}, {len(only2)} extra {only2[:6]}. "
                            f"A relation pass 1 ticked and pass 2 lacks would score as a model "
                            f"invention at evaluation time.")
    elif manifest is None and "--legacy-schema" not in flags:
        problems.append("no session manifest exists for this session (labels/<session>_manifest.json). "
                        "Current sheets are generated with one; pass --legacy-schema only for a "
                        "session that predates manifests.")

    return problems, warnings, off_topic, version, marker


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    known = {"--old-codebook", "--annotator-is-a-model", "--no-sittings", "--legacy-schema"}
    if flags - known:
        sys.exit(f"unknown flag(s): {' '.join(sorted(flags - known))}\n\n{__doc__}")
    if len(args) != 2:
        sys.exit(__doc__)
    src, session = Path(args[0]), args[1]
    if not src.exists():
        sys.exit(f"no such sheet: {src}")

    rows, cols, _ = read_sheet(src)
    if not rows:
        sys.exit("sheet has no data rows")

    mpath, manifest = session_manifest(session)
    problems, warnings, off_topic, version, marker = validate(rows, cols, flags, manifest)

    out = LABELS / f"{session}{marker}_v{version}.csv"
    if out.exists():
        problems.insert(0, f"{out.name} already exists. Label files are never overwritten -- "
                           f"the diff between versions is evidence.")

    # Warnings print whether or not the write proceeds -- they never gate it, so
    # showing them before the refusal keeps them from being read as causes of it.
    for w_ in warnings:
        print(f"WARNING: {w_}\n", file=sys.stderr)

    if problems:
        print(f"REFUSING to write labels/{out.name} -- {len(problems)} problem(s):\n",
              file=sys.stderr)
        for i, pr in enumerate(problems, 1):
            print(f"  {i}. {pr}\n", file=sys.stderr)
        print("Nothing was written. Fix the sheet, or pass the flag each message names.",
              file=sys.stderr)
        sys.exit(1)

    keep = [c for c in cols if c not in TEXT_COLUMNS]
    LABELS.mkdir(exist_ok=True)
    # Publication boundary for the coder's free text (schema 2 only; the six frozen
    # legacy files are not edited). A note can quote the paragraph, so the committed
    # file carries a marker and the text goes to a gitignored sidecar. The
    # note-required rules were already checked above, on the sheet.
    private_rows = []
    schema2 = manifest is not None and not manifest.get("reconstructed")
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keep, delimiter=";")
        w.writeheader()
        for r in rows:
            pub = {c: r.get(c, "") for c in keep}
            if schema2:
                for c in FREE_TEXT:
                    if c in pub and pub[c]:
                        private_rows.append({"row_id": val(r, "row_id") or val(r, "paragraph_id"),
                                             "paragraph_id": val(r, "paragraph_id"),
                                             "field": c, "text": pub[c]})
                        pub[c] = f"[private: {len(pub[c])} chars]"
            w.writerow(pub)
    if schema2:
        priv_dir = LABELS / "private"
        priv_dir.mkdir(exist_ok=True)
        priv = priv_dir / f"{session}{marker}_v{version}_notes.csv"
        with priv.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["row_id", "paragraph_id", "field", "text"], delimiter=";")
            w.writeheader()
            w.writerows(private_rows)
        print(f"wrote labels/private/{priv.name}  ({len(private_rows)} free-text cells, gitignored)")

    digest = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    sittings = sorted({val(r, "sitting") for r in rows} - {""})
    coders = sorted({val(r, "annotator") for r in rows} - {""})
    dates = sorted({val(r, "coded_at") for r in rows} - {""})

    rec = LABELS / "sessions.csv"
    new = not rec.exists()
    with rec.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["session", "codebook_version", "n_rows", "annotators",
                        "coded_at", "sittings", "labels_file", "labels_sha256",
                        "source_sheet", "finalized_on"])
        w.writerow([session, version, len(rows), "|".join(coders), "|".join(dates),
                    "|".join(sittings) or "unrecorded", out.name, digest,
                    src.name, date.today().isoformat()])

    # A finalized pass 1 fixes the (paragraph x technology) keys pass 2 must reproduce.
    # Recorded in the manifest, which is a label artefact: IDs only, no text.
    if manifest is not None and not manifest.get("reconstructed") \
            and sheet_kind(cols) == "pass1":
        keys = sorted(f"{val(r, 'paragraph_id')}__{c[len('tech_'):]}"
                      for r in rows for c in cols
                      if c.startswith("tech_") and BOOL_ALIASES.get(val(r, c), val(r, c)) == "1")
        manifest["pass1_keys"] = keys
        manifest["pass1_off_topic"] = sorted(off_topic)
        manifest["pass1_labels_file"] = out.name
        mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
        print(f"recorded {len(keys)} pass-1 keys in labels/{mpath.name}")

    print(f"wrote labels/{out.name}")
    print(f"  {len(rows)} rows, codebook v{version}, "
          f"{len(keep)} columns ({len(cols) - len(keep)} text columns stripped)")
    print(f"  annotator(s) {coders}  sitting(s) {sittings or ['unrecorded']}")
    print(f"  sha256 {digest}")
    tech = Counter(val(r, "technology") for r in rows)
    if any(tech):
        print(f"  technologies: {dict(tech)}")
    if off_topic:
        print(f"  off topic: {len(off_topic)}/{len(rows)} paragraph(s) name no study "
              f"technology ({100 * len(off_topic) / len(rows):.0f} %) -- "
              f"{', '.join(off_topic)}")
    print("appended to labels/sessions.csv")
    print("\nNow commit labels/ -- that commit timestamp is the evidence that "
          "the annotation preceded the model run.")


if __name__ == "__main__":
    main()
