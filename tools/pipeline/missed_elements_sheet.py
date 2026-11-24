#!/usr/bin/env python3
"""Coding sheet for the optional qualitative analysis of missed elements (pre-analysis specification §4l (6), declared
2026-11-06 with its sample and categories; run only if the author decides to code it, decision-sheet item D39).

Draws 45 element tuples with `random.Random(20261106).sample` from the false negatives of the registered run's
execution 1 whose technology row exists (cause "row present, element missing" of §4l (3)), the tuples sorted by
paragraph, technology and element before the draw, and writes a PRIVATE sheet (article text; the folder is gitignored)
plus a text-free manifest of the drawn tuples:

    .venv/bin/python3 tools/pipeline/missed_elements_sheet.py
      -> tools/annotation/sheets/A2_missed_elements_sheet.csv   (private: paragraph text, for coding)
      -> tools/runs/tables/review_analyses/missed_elements_sample.json   (tracked: ids and digests only)

HOW TO CODE (the author; about 6-7 hours for 45 items)
  Open the CSV (semicolon-separated, UTF-8) in a spreadsheet. For each row read the headline and the paragraph, the
  technology, the missed element with its codebook definition, and what the coder and the model marked on that row.
  Enter exactly ONE letter in the column `category`, and a short reason in `note` where the choice is not obvious:
    a  clear miss              the paragraph states the element's cue explicitly; the model should have marked it
    b  borderline              the cue supports the element only under a generous reading of the codebook
    c  coder possibly over-marked   on re-reading, the paragraph does not support the element
    d  cue outside the paragraph    the element rests on the headline, the outlet or another paragraph of the article
    e  assigned elsewhere      the model expressed the same content through another element or another technology's row
    f  other                   anything else; the note is then required
  Decide by the codebook text in `element_fires_when` / `element_does_not_fire_when`, not by what the model did. Do not
  change the reference labels: this sheet records judgements about them, it does not correct them. The model's output
  is visible, so the coding is not blind to the prediction; the thesis says so. When done, save the CSV under the same
  name and tell the agent; the counts per category with their shares go into the appendix, worded as one coder's
  judgement of their own reference (category c is at most a lower bound on over-marking, nothing here is an error rate).

TALLY (after the coding; added 2026-11-09, when the coded sheet came back)
    .venv/bin/python3 tools/pipeline/missed_elements_sheet.py --tally
  checks the coded sheet against the manifest (same 45 tuples in the same order, same paragraph text by digest, one
  category a-f per row, a note wherever the category is f) and writes a text-free block "coding" into the manifest:
  counts and shares per category, counts per element and per language (the language split was not declared), the
  elements both sides marked on each row, and the category per item. The notes stay in the private sheet, since they
  may quote article text. The reference labels are not touched.
  Two categories that contradicted their own note were reconciled on the author's instruction (RECONCILED below);
  the tally checks the sheet holds the reconciled values and also records the counts as returned.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate as ev                                               # noqa: E402
import instrument as ins                                            # noqa: E402
import review_analyses as ra                                        # noqa: E402

ROOT = HERE.parents[1]
SHEET = ROOT / "tools" / "annotation" / "sheets" / "A2_missed_elements_sheet.csv"
MANIFEST = ROOT / "tools" / "runs" / "tables" / "review_analyses" / "missed_elements_sample.json"
PARAS = ROOT / "tools" / "annotation" / "sheets" / "ALL_paragraphs.csv"
SEED, N = 20261106, 45
COLUMNS = ["item", "paragraph_id", "technology", "missed_element", "element_label", "element_fires_when",
           "element_does_not_fire_when", "coder_elements_on_row", "model_elements_on_row", "headline", "paragraph_text",
           "category", "note"]


def main() -> None:
    inp, S, m = ra.load_registered()
    pred1, out1 = S["pred"].get(1, {}), S["outcome"].get(1, {})
    pool = []
    for (pid, tech), g in sorted(S["gold"].items()):
        p = pred1.get((pid, tech))
        if p is None or out1.get(pid) not in ev.SUCCESS:
            continue
        for e in sorted(g.elements):
            if e not in p.elements:
                pool.append((pid, tech, e))
    drawn = random.Random(SEED).sample(sorted(pool), N)
    if len(pool) != ra.fn_causes(S, m)["pooled"]["fn_rows"].get("element_missing"):
        raise SystemExit("REFUSING: the pool is not the cause-(iii) set of §4l (3)")
    doc, _, cb_sha = ins.load_codebook()
    el = {x["id"]: x for x in doc["frame_elements"]}
    texts = {}
    with PARAS.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            texts[r["paragraph_id"]] = r
    if SHEET.exists():
        raise SystemExit(f"REFUSING: {SHEET} exists; it may hold the author's coding. Move it away deliberately.")
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    with SHEET.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";")
        w.writeheader()
        for i, (pid, tech, e) in enumerate(drawn, 1):
            t = texts[pid]
            w.writerow({"item": i, "paragraph_id": pid, "technology": tech, "missed_element": e,
                        "element_label": el[e]["label"], "element_fires_when": el[e]["fires_when"].strip(),
                        "element_does_not_fire_when": (el[e].get("does_not_fire_when") or "").strip(),
                        "coder_elements_on_row": " ".join(sorted(S["gold"][(pid, tech)].elements)),
                        "model_elements_on_row": " ".join(sorted(pred1[(pid, tech)].elements)),
                        "headline": t.get("headline", ""), "paragraph_text": t["paragraph_text"],
                        "category": "", "note": ""})
    MANIFEST.write_text(json.dumps({
        "authority": "pre-analysis specification §4l (6), declared 2026-11-06", "seed": SEED, "n": N,
        "pool_size": len(pool), "codebook_sha256": cb_sha, "run_record_sha256": inp["provenance"]["run_record_sha256"],
        "items": [{"item": i, "paragraph_id": pid, "technology": tech, "element": e,
                   "text_sha256": texts[pid]["text_sha256"]} for i, (pid, tech, e) in enumerate(drawn, 1)],
        "categories": {"a": "clear miss", "b": "borderline", "c": "coder possibly over-marked",
                       "d": "cue outside the paragraph", "e": "assigned elsewhere", "f": "other"},
        "sheet_sha256_at_creation": hashlib.sha256(SHEET.read_bytes()).hexdigest()}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SHEET.relative_to(ROOT)} ({N} of {len(pool)} tuples) and {MANIFEST.relative_to(ROOT)}")


CATEGORIES = "abcdef"
# Two categories contradicted the coder's own note in the sheet as returned (2026-11-09). On the author's instruction
# ("Das bekommst du selbst hin") the coding agent (Claude Code, Claude Opus 5.5) reconciled them by the note and the
# codebook text; the sheet as returned is kept beside it (A2_missed_elements_sheet_as-returned_2026-11-09.csv, private).
RECONCILED = {
    16: ("c", "b", "the note argues that the paragraph supports F8a, but only through a regulatory reading the "
                   "paragraph does not state and that partly depends on the interview context: a generous reading"),
    21: ("a", "b", "the note itself concludes 'borderline under the current codebook' (a record without a figure)"),
}


def tally() -> None:
    """Tally the coded sheet into the manifest's block "coding" (counts only, no text); refuses on any mismatch."""
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sheet_sha = hashlib.sha256(SHEET.read_bytes()).hexdigest()
    if sheet_sha == man["sheet_sha256_at_creation"]:
        raise SystemExit("REFUSING: the sheet is still the blank sheet of creation; nothing is coded")
    with SHEET.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        if reader.fieldnames != COLUMNS:
            raise SystemExit(f"REFUSING: the sheet's columns changed: {reader.fieldnames}")
        rows = list(reader)
    if len(rows) != man["n"] or len(rows) != len(man["items"]):
        raise SystemExit(f"REFUSING: {len(rows)} rows against {man['n']} drawn tuples")
    paras = {}
    with PARAS.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            paras[r["paragraph_id"]] = r
    items, counts = [], {c: 0 for c in CATEGORIES}
    by_element, by_language = {}, {}
    for r, it in zip(rows, man["items"]):
        key = (int(r["item"]), r["paragraph_id"], r["technology"], r["missed_element"])
        if key != (it["item"], it["paragraph_id"], it["technology"], it["element"]):
            raise SystemExit(f"REFUSING: row {r['item']} is not the drawn tuple {it}")
        p = paras[r["paragraph_id"]]
        if p["text_sha256"] != it["text_sha256"]:
            raise SystemExit(f"REFUSING: the paragraph text of item {it['item']} changed since the draw")
        cat = r["category"].strip().lower()
        if len(cat) != 1 or cat not in CATEGORIES:
            raise SystemExit(f"REFUSING: item {it['item']} has category {r['category']!r}; exactly one of a-f")
        if cat == "f" and not r["note"].strip():
            raise SystemExit(f"REFUSING: item {it['item']} is category f without the required note")
        counts[cat] += 1
        by_element.setdefault(it["element"], {c: 0 for c in CATEGORIES})[cat] += 1
        by_language.setdefault(p["lang"], {c: 0 for c in CATEGORIES})[cat] += 1
        items.append({"item": it["item"], "category": cat, "language": p["lang"],
                      "coder_elements_on_row": r["coder_elements_on_row"].split(),
                      "model_elements_on_row": r["model_elements_on_row"].split()})
    n = len(items)
    as_returned = dict(counts)
    for i in items:
        if i["item"] in RECONCILED:
            old, new, _ = RECONCILED[i["item"]]
            if i["category"] != new:
                raise SystemExit(f"REFUSING: item {i['item']} should carry the reconciled category {new!r}")
            as_returned[new] -= 1
            as_returned[old] += 1
    man["coding"] = {
        "tallied": __import__("datetime").date.today().isoformat(),
        "sheet_sha256_coded": sheet_sha,
        "reading": ("one coder's judgement of their own reference, made with the model's output in view and after "
                    "every other result was known; category c is at most a lower bound on over-marking, and nothing "
                    "here is an error rate; the reference labels were not changed"),
        "n": n, "paragraphs": len({r["paragraph_id"] for r in rows}),
        "articles": len({paras[r["paragraph_id"]]["article_id"] for r in rows}),
        "counts": counts,
        "shares_percent": {c: round(100 * k / n, 1) for c, k in counts.items()},
        "reconciled": {str(k): {"as_returned": o, "reconciled": nw, "reason": why,
                                "by": "coding agent (Claude Code, Claude Opus 5.5) on the author's instruction, 2026-11-09"}
                       for k, (o, nw, why) in RECONCILED.items()},
        "counts_as_returned": as_returned,
        "ai_assistance_in_coding": "none (the author's statement of 2026-11-09: categories and notes written by the author)",
        "model_marked_another_element_on_row": {
            c: sum(1 for i in items if i["category"] == c and i["model_elements_on_row"]) for c in CATEGORIES},
        "by_element": dict(sorted(by_element.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))),
        "by_language_not_declared": dict(sorted(by_language.items())),
        "items": items,
    }
    MANIFEST.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    print(f"tallied {n} items into {MANIFEST.relative_to(ROOT)}: {counts}")


if __name__ == "__main__":
    tally() if sys.argv[1:] == ["--tally"] else main()
