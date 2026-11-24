#!/usr/bin/env python3
"""Expand a completed pass-1 sheet into the pass-2 skeleton.

Pass 1 asks only which technologies a paragraph makes a statement about. This
script then writes one pass-2 row per (paragraph x ticked technology), carrying
the paragraph text across so you never have to look anything up, and pre-filling
every binary with 0 so you only ever type 1s.

Why the two passes exist: in a multi-technology paragraph -- 33% of the
candidates in this corpus -- "F1a fired" says nothing unless it says which
technology it fired for. Codex flagged this as the design's most serious hole,
and this is the fix.

    python tools/annotation/expand_sheet2.py A0_calibration
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_sheets import (SHEET2_COLUMNS, SHEET2_LABELS, ANNOTATOR,  # noqa: E402
                          CODEBOOK_VERSION, DELIM, ENC)

TECH_COLUMNS = {
    "tech_general": "general", "tech_solar": "solar", "tech_wind": "wind",
    "tech_hydrogen": "hydrogen", "tech_nuclear": "nuclear",
    "tech_other_renewable": "other_renewable", "tech_storage": "storage",
}
ZERO = ["F1a", "F1b", "F2a", "F2b", "F3a", "F3b", "F4a", "F4b", "F5a", "F5b",
        "F2c", "F6a", "F6b", "F7a", "F7b", "F8a", "F9", "no_frame_for_this_tech"]
# `text_sha256` travels with the row so pass 2 can be bound to the text it was coded
# against (sheet schema 2). Blank on a legacy pass-1 sheet that lacks the column.
CONTEXT = ["lang", "outlet", "headline", "paragraph_text", "text_sha256"]


def main() -> None:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(argv) != 1:
        sys.exit(__doc__)
    # Either a session name or a path to the coded sheet. The bare convention
    # `{session}_sheet-1.csv` pointed at the BLANK template, so a session coded
    # into `A1_development_sheet-1_coded_2026-09-01.csv` -- the natural thing to
    # do, and what happened -- would have been expanded from 35 empty rows.
    arg = argv[0]
    as_path = Path(arg)
    if as_path.suffix == ".csv":
        src = as_path if as_path.is_absolute() else Path.cwd() / as_path
        if not src.exists():
            sys.exit(f"no such sheet: {src}")
        stem = src.name
        if "_sheet-1" not in stem:
            sys.exit(f"{src.name} is not a sheet-1 file; pass the pass-1 sheet.")
        session = stem.split("_sheet-1")[0]
    else:
        session = arg
        pool = sorted((HERE / "sheets").glob(f"{session}_sheet-1*.csv"))
        pool = [q for q in pool if ".bak" not in q.name]
        if not pool:
            sys.exit(f"no such sheet: {HERE / 'sheets' / f'{session}_sheet-1.csv'}")

        def coded_rows(q: Path) -> int:
            raw = q.open(encoding="utf-8-sig", newline="").read()
            d = ";" if raw.split("\n")[0].count(";") > raw.split("\n")[0].count(",") else ","
            return sum(1 for r in csv.DictReader(raw.splitlines(), delimiter=d)
                       if not str(r.get("paragraph_id", "")).startswith("#")
                       and any(str(r.get(c, "")).strip() in {"1", "y", "Y", "x", "X"}
                               for c in TECH_COLUMNS))

        filled = [q for q in pool if coded_rows(q)]
        if len(filled) > 1:
            sys.exit(f"ambiguous: {len(filled)} coded sheet-1 files for {session}: "
                     f"{', '.join(q.name for q in filled)}\n"
                     f"Pass the one you mean as a path.")
        src = filled[0] if filled else pool[0]
        if len(pool) > 1:
            print(f"  (chose {src.name} of {len(pool)} candidates)")
    dst = HERE / "sheets" / f"{session}_sheet-2.csv"

    raw = src.open(encoding="utf-8-sig", newline="").read()
    delim = ";" if raw.split("\n")[0].count(";") > raw.split("\n")[0].count(",") else ","  # sniff on read
    rows = [r for r in csv.DictReader(raw.splitlines(), delimiter=delim)
            if not str(r.get("paragraph_id", "")).startswith("#")
            and not str(list(r.values())[0] or "").startswith("#")]
    print(f"  (read with '{delim}' delimiter)")
    # A warning here silently shrank the session: an unfinished pass-1 row produced
    # no pass-2 rows, so A1's denominator quietly became smaller than 35 and nothing
    # downstream could tell. Refuse instead (round-5 finding #1).
    TICK = {"1", "y", "Y", "x", "X"}
    problems: list[str] = []

    # A paragraph naming no study technology is OFF TOPIC, not unfinished. See the
    # long note in finalize.py: articles are keyword-selected, paragraphs are not,
    # and codebook X4/X11 make coding no technology the correct outcome there (X9
    # records one from A0 as exactly that). It contributes zero pass-2 rows because
    # there is nothing to frame -- which is the right denominator, not a shrunken
    # one. `no_frame_present` is therefore required on technology-bearing rows and
    # must be blank on off-topic ones, so the two statements cannot be confused.
    off_topic = [r["paragraph_id"] for r in rows
                 if not any(str(r.get(c, "")).strip() in TICK for c in TECH_COLUMNS)]
    bad_nfp = [r["paragraph_id"] for r in rows
               if r["paragraph_id"] not in off_topic
               and str(r.get("no_frame_present", "")).strip() not in {"0", "1"}]
    nfp_off_topic = [r["paragraph_id"] for r in rows
                     if r["paragraph_id"] in off_topic
                     and str(r.get("no_frame_present", "")).strip() in {"0", "1"}]
    no_conf = [r["paragraph_id"] for r in rows
               if str(r.get("confidence", "")).strip() not in {"1", "2", "3"}]
    no_prov = [r["paragraph_id"] for r in rows
               if not str(r.get("annotator", "")).strip()
               or not str(r.get("coded_at", "")).strip()]

    if nfp_off_topic:
        problems.append(
            f"{len(nfp_off_topic)} paragraphs have no technology ticked yet carry a "
            f"`no_frame_present` value: {', '.join(nfp_off_topic[:8])}"
            f"{' ...' if len(nfp_off_topic) > 8 else ''}\n"
            f"     `no_frame_present` says a NAMED technology is not framed. It cannot also\n"
            f"     mean that no technology is named -- one column, one meaning, or the\n"
            f"     reported nfp share measures the sampling frame (codebook X10). Clear it,\n"
            f"     and the paragraph counts as off topic.")
    if bad_nfp:
        problems.append(f"{len(bad_nfp)} paragraphs name a technology but have "
                        f"`no_frame_present` neither 0 nor 1: "
                        f"{', '.join(bad_nfp[:8])}. It is a substantive value, not missing data.")
    if no_conf:
        problems.append(f"{len(no_conf)} paragraphs have no `confidence` in 1-3: "
                        f"{', '.join(no_conf[:8])}.")
    if no_prov:
        problems.append(f"{len(no_prov)} paragraphs are missing `annotator` or `coded_at`: "
                        f"{', '.join(no_prov[:8])}.")

    if problems and "--partial" not in sys.argv:
        print(f"\nREFUSING to expand {session}: pass 1 is not finished.\n", file=sys.stderr)
        for i, pr in enumerate(problems, 1):
            print(f"  {i}. {pr}\n", file=sys.stderr)
        print("Nothing was written. Finish pass 1, or pass --partial to expand anyway and\n"
              "accept a denominator smaller than the session claims.", file=sys.stderr)
        sys.exit(1)
    if problems:
        print(f"! --partial: expanding despite {len(problems)} completeness problem(s). "
              f"The session's denominator will be smaller than {len(rows)}.")

    out = []
    for r in rows:
        for col, tech in TECH_COLUMNS.items():
            if str(r.get(col, "")).strip() not in {"1", "y", "Y", "x", "X"}:
                continue
            row = {c: "" for c in SHEET2_COLUMNS}
            row.update({
                "row_id": f"{r['paragraph_id']}__{tech}",
                "paragraph_id": r["paragraph_id"],
                "technology": tech,
                **{c: r.get(c, "") for c in CONTEXT},
                "annotator": ANNOTATOR,
                "annotator_type": r.get("annotator_type", "human"),
                "codebook_version": CODEBOOK_VERSION,
                # NOT inherited from pass 1. The sitting number describes when THIS
                # pass was coded, and pass 2 is a different session -- carrying
                # pass 1's number over would date the frame coding to the day the
                # technologies were ticked. Blank, so finalize.py demands an answer.
                "sitting": "",
                # Subtypes are recorded once per paragraph in pass 1 and belong to
                # the technology they qualify, so they travel with that row. Without
                # this they were coded and then dropped: the final labels lost them
                # entirely (round-4 finding #2).
                **({"wind_siting": r.get("wind_siting", "")} if tech == "wind" else {}),
                **({"hydrogen_pathway": r.get("hydrogen_pathway", "")}
                   if tech == "hydrogen" else {}),
            })
            for c in ZERO:
                row[c] = 0
            out.append(row)

    # Regenerating over a pass-2 sheet that already carries work destroys it silently.
    if dst.exists():
        prev = [r for r in csv.DictReader(
            dst.open(encoding="utf-8-sig", newline="").read().splitlines(), delimiter=DELIM)
            if not str(r.get("row_id", "")).startswith("#")]
        coded = [r for r in prev
                 if any(str(r.get(c, "")).strip() not in {"", "0"}
                        for c in r if c.startswith(("F", "eval_")))]
        if coded and "--force" not in sys.argv:
            sys.exit(f"REFUSING: {dst.name} exists and {len(coded)} of its {len(prev)} rows "
                     f"carry coding. Regenerating would destroy that work. Save your coded "
                     f"copy as {session}_sheet-2_CODED.csv first, or pass --force.")

    with dst.open("w", newline="", encoding=ENC) as f:
        w = csv.DictWriter(f, fieldnames=SHEET2_COLUMNS, delimiter=DELIM)
        w.writeheader()
        w.writerow({c: SHEET2_LABELS.get(c, "") for c in SHEET2_COLUMNS})
        w.writerows(out)
    print(f"wrote {dst.relative_to(HERE.parents[1])}  —  {len(out)} rows "
          f"from {len(rows)} paragraphs")
    if out:
        per = len(out) / len({r['paragraph_id'] for r in out})
        print(f"  {per:.1f} technology rows per framed paragraph")
    if off_topic:
        # Printed here as well as in finalize.py, because this is the step where the
        # paragraph count drops and the reason has to be legible at the drop.
        print(f"  {len(off_topic)} of {len(rows)} paragraphs are off topic (name no study "
              f"technology) and produce no row: {', '.join(off_topic)}")


if __name__ == "__main__":
    main()
