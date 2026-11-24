#!/usr/bin/env python3
"""The route-B re-code sheet: the 40 flagged A2 paragraphs in a fresh order, without labels.

Specification L22 and §4 (2026-09-15): after a 14-day washout the author re-codes the
reliability subsample blind -- fresh item order, no first-coding label visible, the same
frozen codebook -- and the evaluator reports intra-coder temporal stability. This tool
generates that sheet and its session manifest; it never touches the first coding.

    python3 tools/annotation/recode_sheet.py            # writes sheets/A2R_reliability_sheet-1.csv,
                                                        #        sheets/A2R_reliability_sheet-2.csv,
                                                        #        labels/A2R_manifest.json

Guards: the registered sample manifest recomputes to its digest; exactly the flagged
paragraphs are taken; their text hashes equal the A2 session manifest's (same text as the
first coding); the first coding is finalised (both A2 label files recorded in sessions.csv with
the digests they carry today, every row dated)
-- a re-code sheet that exists before the first coding is a temptation, not a tool; the
sheet is written once. The order seed is fixed here, in the specification, before any A2
label existed. Pass 2 is expanded with expand_sheet2.py, both passes are finalised with
finalize.py under the sessions A2R_pass1 / A2R_pass2; the evaluator binds the session manifest's
`recode` block to route B (bind_reliability_inputs) and requires the 14-day washout.

Second wave (specification §4n, declared 2026-11-10 before this sheet existed):

    python3 tools/annotation/recode_sheet.py --wave 2  # writes sheets/A2R2_reliability_sheet-1.csv,
                                                        #        sheets/A2R2_reliability_sheet-2.csv,
                                                        #        labels/A2R2_manifest.json

the 80 A2 paragraphs outside the route-B subsample, blind, in a fresh order (seed 20261110), sessions
A2R2_pass1 / A2R2_pass2. The 25 of them that carry a D39 item (the coder saw both codings' elements on one row of
each on 2026-11-09) stay in, by the author's decision; their identifiers are recorded in the manifest so that the
declared sensitivity without them needs no other file. Same guards as wave 1, plus: the route-B re-code is finalised.
"""
from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_sheets as bs  # noqa: E402
import split_registry as sr  # noqa: E402

SESSION = "A2R"
NAME = "A2R_reliability"
OF_SESSION = "A2"
SEED_RECODE = 20261005          # pre-registered 2026-09-15 (specification §4, route B block)
WASHOUT_DAYS = 14
FIRST_CODING_FILES = ("A2_pass1_v1.0.csv", "A2_pass2_v1.0.csv")
# second wave, specification §4n (declared 2026-11-10, before the sheet was generated)
SESSION2 = "A2R2"
NAME2 = "A2R2_reliability"
SEED_RECODE2 = 20261110
WAVE1_FILES = ("A2R_pass1_v1.0.csv", "A2R_pass2_v1.0.csv")
D39_SHEET = HERE / "sheets" / "A2_missed_elements_sheet.csv"
WAVE2_DECLARED = {"paragraphs": 80, "by_language": {"de": 40, "en": 40}, "d39_exposed": 25}


class RecodeError(RuntimeError):
    pass


def parse_date(value: str):
    """A coding date in exactly the formats finalize.py accepts (its DATE_FORMATS: ISO, D.M.YYYY, D.M.YY with
    strptime's century pivot); RecodeError otherwise. The evaluator parses with the same three formats."""
    import datetime as dt
    v = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise RecodeError(f"unparseable coding date {value!r}")


def first_coding_check(labels_dir: Path) -> str:
    """The first coding is finalised: both A2 label files exist, are recorded in sessions.csv with the
    digest they carry today, and every row's coded_at parses. Returns the latest coding date (ISO)."""
    import hashlib
    rec = labels_dir / "sessions.csv"
    if not rec.exists():
        raise RecodeError("no sessions.csv beside the labels; the first coding is not finalised")
    recorded = {r["labels_file"]: r["labels_sha256"] for r in csv.DictReader(rec.open(encoding="utf-8-sig"), delimiter=";")}
    dates = []
    for f in FIRST_CODING_FILES:
        path = labels_dir / f
        if f not in recorded or not path.exists():
            raise RecodeError(f"the first coding is not finalised ({f} missing or not recorded in sessions.csv)")
        if hashlib.sha256(path.read_bytes()).hexdigest()[:16] != recorded[f]:
            raise RecodeError(f"{f} differs from the digest sessions.csv recorded at finalisation; not a finalised reference")
        for r in csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"):
            v = (r.get("coded_at") or "").strip()
            if not v:
                raise RecodeError(f"{f}: a row without coded_at; the washout cannot be computed")
            dates.append(parse_date(v))
    if not dates:
        raise RecodeError("the first coding carries no coding dates")
    return max(dates).isoformat()


def build_recode_sheet(sample: dict, rows: list[dict], registry, *, out_dir: Path | None = None,
                       labels_dir: Path | None = None, require_first_coding: bool = True) -> dict:
    """Write the three files; return a small summary. Raises RecodeError on any refusal."""
    out_dir = Path(out_dir or bs.OUT); labels_dir = Path(labels_dir or bs.LABELS)
    if sample.get("kind") != "A2_sample_manifest":
        raise RecodeError("not an A2 sample manifest")
    if bs.canonical_digest(sample) != sample.get("digest"):
        raise RecodeError("the sample manifest's content does not match its digest")
    flagged = [p for p in sample["paragraphs"] if p.get("reliability_subsample")]
    want = sample.get("design", {}).get("reliability_per_language")
    by_lang = {}
    for p in flagged:
        by_lang[p["lang"]] = by_lang.get(p["lang"], 0) + 1
    if not flagged or (want and any(n != want for n in by_lang.values())):
        raise RecodeError(f"the flagged subsample is not the registered one: {by_lang} (registered {want} per language)")
    a2_manifest = labels_dir / f"{OF_SESSION}_manifest.json"
    if not a2_manifest.exists():
        raise RecodeError(f"no first-coding session manifest at {a2_manifest.name}")
    known = {q["paragraph_id"]: q["text_sha256"] for q in json.loads(a2_manifest.read_text(encoding="utf-8"))["paragraphs"]}
    last_first = first_coding_check(labels_dir) if require_first_coding else None
    # every destination is checked before anything is written; nothing here ever overwrites
    targets = [out_dir / f"{NAME}_sheet-1.csv", out_dir / f"{NAME}_sheet-2.csv", labels_dir / f"{SESSION}_manifest.json"]
    existing = [t.name for t in targets if t.exists()]
    if existing:
        raise RecodeError(f"{', '.join(existing)} exist(s); the re-code sheet is generated once and nothing is overwritten")
    by_key = {(r["article_id"], r["paragraph_id"], r["digest_full"]): r for r in rows}
    sub = []
    for p in flagged:
        r = by_key.get((p["article_id"], p["paragraph_id"], p["digest"]))
        if r is None:
            raise RecodeError(f"flagged paragraph {p['paragraph_id']} is not present in the current corpus with its registered identity")
        if known.get(p["paragraph_id"]) != r["text_sha256"]:
            raise RecodeError(f"flagged paragraph {p['paragraph_id']}: text hash differs from the first-coding session manifest")
        r = dict(r); r["split"] = SESSION
        sub.append(r)
    # fresh order: alphabetical identifiers shuffled with the pre-registered seed; never the sitting order
    order = sorted(r["paragraph_id"] for r in sub)
    random.Random(SEED_RECODE).shuffle(order)
    sitting = [p["paragraph_id"] for p in sorted(flagged, key=lambda p: p["sitting_order"])]
    if order == sitting:
        raise RecodeError("the fresh order coincides with the sitting order; refuse rather than present the first order again")
    pos = {pid: i for i, pid in enumerate(order)}
    sub.sort(key=lambda r: pos[r["paragraph_id"]])
    out_dir.mkdir(exist_ok=True)
    with (out_dir / f"{NAME}_sheet-1.csv").open("x", newline="", encoding=bs.ENC) as f:      # exclusive: never overwrite
        w = csv.DictWriter(f, fieldnames=bs.SHEET1_COLUMNS, delimiter=bs.DELIM)
        w.writeheader()
        for r in sub:
            w.writerow(bs.blank_row(r))            # blank: no first-coding label enters this file
    with (out_dir / f"{NAME}_sheet-2.csv").open("x", newline="", encoding=bs.ENC) as f:
        w2 = csv.writer(f, delimiter=bs.DELIM)
        w2.writerow(bs.SHEET2_COLUMNS)
        w2.writerow([bs.SHEET2_LABELS.get(c, "") for c in bs.SHEET2_COLUMNS])
    prev_labels = bs.LABELS; bs.LABELS = labels_dir
    try:
        mpath = bs.write_session_manifest(SESSION, sub, registry, kind="sheet-1")
    finally:
        bs.LABELS = prev_labels
    doc = json.loads(mpath.read_text(encoding="utf-8"))
    doc["recode"] = {"of_session": OF_SESSION, "route": "B", "seed_order": SEED_RECODE, "washout_days": WASHOUT_DAYS,
                     "first_labels_visible": False, "sample_manifest_digest": sample["digest"],
                     "last_first_coding_date": last_first}
    mpath.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"rows": len(sub), "by_language": by_lang, "sheet": out_dir / f"{NAME}_sheet-1.csv", "manifest": mpath,
            "last_first_coding_date": doc["recode"]["last_first_coding_date"]}


def finalised_check(labels_dir: Path, files: tuple[str, ...], what: str) -> None:
    """Every file exists and carries the digest sessions.csv recorded at its finalisation."""
    import hashlib
    rec = labels_dir / "sessions.csv"
    recorded = {r["labels_file"]: r["labels_sha256"] for r in csv.DictReader(rec.open(encoding="utf-8-sig"), delimiter=";")} \
        if rec.exists() else {}
    for f in files:
        path = labels_dir / f
        if f not in recorded or not path.exists():
            raise RecodeError(f"{what} is not finalised ({f} missing or not recorded in sessions.csv)")
        if hashlib.sha256(path.read_bytes()).hexdigest()[:16] != recorded[f]:
            raise RecodeError(f"{f} differs from the digest sessions.csv recorded at finalisation")


def build_recode_sheet_wave2(sample: dict, rows: list[dict], registry, *, out_dir: Path | None = None,
                             labels_dir: Path | None = None, d39_sheet: Path | None = None,
                             declared: dict | None = None) -> dict:
    """The second re-code wave of specification §4n: every registered A2 paragraph outside the route-B subsample,
    blind, in a fresh order. Writes the three files once; raises RecodeError on any refusal."""
    import hashlib
    out_dir = Path(out_dir or bs.OUT); labels_dir = Path(labels_dir or bs.LABELS)
    d39_sheet = Path(d39_sheet or D39_SHEET); declared = declared or WAVE2_DECLARED
    if sample.get("kind") != "A2_sample_manifest":
        raise RecodeError("not an A2 sample manifest")
    if bs.canonical_digest(sample) != sample.get("digest"):
        raise RecodeError("the sample manifest's content does not match its digest")
    population = [p for p in sample["paragraphs"] if not p.get("reliability_subsample")]
    by_lang: dict[str, int] = {}
    for p in population:
        by_lang[p["lang"]] = by_lang.get(p["lang"], 0) + 1
    if len(population) != declared["paragraphs"] or by_lang != declared["by_language"]:
        raise RecodeError(f"the population is {len(population)} paragraphs {by_lang}, not the declared "
                          f"{declared['paragraphs']} {declared['by_language']}")
    a2_manifest = labels_dir / f"{OF_SESSION}_manifest.json"
    if not a2_manifest.exists():
        raise RecodeError(f"no first-coding session manifest at {a2_manifest.name}")
    known = {q["paragraph_id"]: q["text_sha256"] for q in json.loads(a2_manifest.read_text(encoding="utf-8"))["paragraphs"]}
    last_first = first_coding_check(labels_dir)
    finalised_check(labels_dir, WAVE1_FILES, "the route-B re-code (wave 1)")
    if not d39_sheet.exists():
        raise RecodeError(f"no D39 sheet at {d39_sheet}; the exposed paragraphs cannot be recorded")
    pop_ids = {p["paragraph_id"] for p in population}
    d39 = {r["paragraph_id"] for r in csv.DictReader(d39_sheet.open(encoding="utf-8-sig"), delimiter=";")}
    exposed = sorted(d39 & pop_ids)
    if len(exposed) != declared["d39_exposed"]:
        raise RecodeError(f"{len(exposed)} population paragraphs carry a D39 item, not the declared {declared['d39_exposed']}")
    targets = [out_dir / f"{NAME2}_sheet-1.csv", out_dir / f"{NAME2}_sheet-2.csv", labels_dir / f"{SESSION2}_manifest.json"]
    existing = [t.name for t in targets if t.exists()]
    if existing:
        raise RecodeError(f"{', '.join(existing)} exist(s); the re-code sheet is generated once and nothing is overwritten")
    by_key = {(r["article_id"], r["paragraph_id"], r["digest_full"]): r for r in rows}
    sub = []
    for p in population:
        r = by_key.get((p["article_id"], p["paragraph_id"], p["digest"]))
        if r is None:
            raise RecodeError(f"paragraph {p['paragraph_id']} is not present in the current corpus with its registered identity")
        if known.get(p["paragraph_id"]) != r["text_sha256"]:
            raise RecodeError(f"paragraph {p['paragraph_id']}: text hash differs from the first-coding session manifest")
        r = dict(r); r["split"] = SESSION2
        sub.append(r)
    order = sorted(r["paragraph_id"] for r in sub)
    random.Random(SEED_RECODE2).shuffle(order)
    sitting = [p["paragraph_id"] for p in sorted(population, key=lambda p: p["sitting_order"])]
    if order == sitting:
        raise RecodeError("the fresh order coincides with the sitting order; refuse rather than present the first order again")
    pos = {pid: i for i, pid in enumerate(order)}
    sub.sort(key=lambda r: pos[r["paragraph_id"]])
    out_dir.mkdir(exist_ok=True)
    with (out_dir / f"{NAME2}_sheet-1.csv").open("x", newline="", encoding=bs.ENC) as f:
        w = csv.DictWriter(f, fieldnames=bs.SHEET1_COLUMNS, delimiter=bs.DELIM)
        w.writeheader()
        for r in sub:
            w.writerow(bs.blank_row(r))            # blank: no first-coding label enters this file
    with (out_dir / f"{NAME2}_sheet-2.csv").open("x", newline="", encoding=bs.ENC) as f:
        w2 = csv.writer(f, delimiter=bs.DELIM)
        w2.writerow(bs.SHEET2_COLUMNS)
        w2.writerow([bs.SHEET2_LABELS.get(c, "") for c in bs.SHEET2_COLUMNS])
    prev_labels = bs.LABELS; bs.LABELS = labels_dir
    try:
        mpath = bs.write_session_manifest(SESSION2, sub, registry, kind="sheet-1")
    finally:
        bs.LABELS = prev_labels
    doc = json.loads(mpath.read_text(encoding="utf-8"))
    doc["recode"] = {"of_session": OF_SESSION, "route": "B", "wave": 2, "authority": "specification §4n (2026-11-10)",
                     "seed_order": SEED_RECODE2, "washout_days": WASHOUT_DAYS, "first_labels_visible": False,
                     "sample_manifest_digest": sample["digest"], "last_first_coding_date": last_first,
                     "population": "registered A2 sample less the route-B subsample",
                     "d39_exposed": exposed,
                     "d39_sheet_sha256": hashlib.sha256(d39_sheet.read_bytes()).hexdigest()[:16]}
    mpath.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"rows": len(sub), "by_language": by_lang, "sheet": out_dir / f"{NAME2}_sheet-1.csv", "manifest": mpath,
            "last_first_coding_date": last_first, "d39_exposed": len(exposed)}


def main() -> None:
    sample = json.loads(bs.SAMPLE_MANIFEST.read_text(encoding="utf-8")) if hasattr(bs, "SAMPLE_MANIFEST") else \
        json.loads((HERE / "labels" / "A2_sample_manifest.json").read_text(encoding="utf-8"))
    rows, _ = bs.collect()
    registry = sr.SplitRegistry.load(bs.SPLIT_FILE)
    if "--wave" in sys.argv:
        if sys.argv[sys.argv.index("--wave") + 1:][:1] != ["2"]:
            sys.exit("--wave takes the value 2 (the second wave of specification §4n)")
        try:
            res = build_recode_sheet_wave2(sample, rows, registry)
        except RecodeError as exc:
            sys.exit(f"REFUSING: {exc}")
        print(f"wrote {res['sheet'].relative_to(HERE)}   {res['rows']} rows ({res['by_language']}), fresh order, no labels")
        print(f"wrote labels/{res['manifest'].name}   session manifest ({res['d39_exposed']} D39-exposed paragraphs recorded); "
              f"commit it before opening the sheet")
        print(f"last first-coding date {res['last_first_coding_date']}; code both passes blind, sessions A2R2_pass1 / A2R2_pass2")
        return
    try:
        res = build_recode_sheet(sample, rows, registry)
    except RecodeError as exc:
        sys.exit(f"REFUSING: {exc}")
    import datetime as dt
    last = res["last_first_coding_date"]
    earliest = (dt.date.fromisoformat(last) + dt.timedelta(days=WASHOUT_DAYS)).isoformat() if last else "unknown"
    print(f"wrote {res['sheet'].relative_to(HERE)}   {res['rows']} rows ({res['by_language']}), fresh order, no labels")
    print(f"wrote labels/{res['manifest'].name}   session manifest; commit it before opening the sheet")
    print(f"earliest admissible re-code day: {earliest} (last first-coding date {last} + {WASHOUT_DAYS} days); "
          f"do not open the sheet before that day")


if __name__ == "__main__":
    main()
