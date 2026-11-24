#!/usr/bin/env python3
"""A3 -- the fresh-sample evaluation set: a CENSUS of the frozen frame's paragraphs from articles that no
coding or model run has touched. Pre-registered in the pre-analysis specification §4b (2026-10-14).

    python3 tools/annotation/draw_a3.py --dry-run    # print the frame and the order, write nothing
    python3 tools/annotation/draw_a3.py              # write labels/A3_sample_manifest.json,
                                                     #       sheets/A3_evaluation_sheet-1.csv (blank),
                                                     #       sheets/A3_evaluation_sheet-2.csv (header only),
                                                     #       labels/A3_manifest.json (session manifest)

Fixed here, before any A3 paragraph is coded or predicted:

  frame     the frozen A2 frame manifest (618 eligible paragraphs; freeze event verified exactly as
            draw_a2.py verifies it) MINUS the registered A2 sample (by paragraph digest) MINUS every
            paragraph whose article has a paragraph in A0, A0b, A1 (exposure `sibling_calibration_article`)
            or in A2. What remains are paragraphs from articles no human coding and no model run has seen.
  design    a census: every remaining paragraph, pi = 1, weight = 1; strata language x outlet are
            recorded for reporting only; no cap, no allocation, no reliability subsample.
  order     the manifest lists the paragraphs in a seeded shuffle (SEED_ORDER_A3) -- the sitting order.
  binding   the manifest carries the frame digest, the freeze event hash and the A2 sample manifest
            digest it excludes; the session manifest binds the text hashes.
  audit     before listing, a TEXT-FREE exposure audit reads every public run record under tools/runs/,
            every session manifest and finalised label file under labels/ (the frame manifest excepted),
            the codebook files and the rendered instrument artefacts, collects the article identifiers
            they carry (standalone or inside paragraph and row identifiers) and searches them for the A3
            paragraphs' digests and text hashes, and REFUSES on any hit; files, digests and overlap go
            into the manifest. Freshness = absence of documented study exposure, nothing more.
            Tests: test_draw_a3.py (system interpreter).

Runs on the system interpreter (stdlib only), like every tool in this folder. Nothing is overwritten.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_sheets as bs                                            # noqa: E402
import draw_a2 as d2                                                 # noqa: E402  verify_freeze, canonical_digest
import split_registry as sr                                          # noqa: E402

LABELS = HERE / "labels"
SHEETS = HERE / "sheets"
FRAME_MANIFEST = LABELS / "A2_frame_manifest.json"
A2_SAMPLE_MANIFEST = LABELS / "A2_sample_manifest.json"
A3_SAMPLE_MANIFEST = LABELS / "A3_sample_manifest.json"
SESSION = "A3"
NAME = "A3_evaluation"
SEED_ORDER_A3 = 20261014     # fixed in the specification §4b before the frame was listed
ROOT = HERE.parents[1]
ARTICLE_RE = re.compile(r"(?<![A-Za-z0-9])(A\d{3})(?![A-Za-z0-9])")   # also inside A038_p01 and A038_p01_wind
AUDIT_SOURCES = [                 # (directory, glob) -- public, text-free records only; never tools/runs/raw
    ("tools/runs", "*_calls.jsonl"), ("tools/runs", "*_terminal.csv"), ("tools/runs", "*_predictions.csv"),
    ("tools/runs", "*.json"), ("tools/annotation/labels", "*.csv"), ("tools/annotation/labels", "*.json"),
    ("tools/annotation/codebook", "*.yaml"), ("tools/annotation/codebook", "*.yml"), ("tools/annotation/codebook", "*.md"),
    ("tools/pipeline/instrument", "*"),
]
AUDIT_EXCEPT = {FRAME_MANIFEST.name, A3_SAMPLE_MANIFEST.name, f"{SESSION}_manifest.json"}


class DrawError(RuntimeError):
    pass


def a3_frame(frame: dict, a2: dict) -> list[dict]:
    a2_digests = {p["digest"] for p in a2["paragraphs"]}
    a2_articles = {p["article_id"] for p in a2["paragraphs"]}
    out = []
    for p in frame["paragraphs"]:
        if p["digest"] in a2_digests:
            continue
        if p.get("exposure") != "clean_article":
            continue
        if p["article_id"] in a2_articles:
            continue
        out.append(dict(p))
    return sorted(out, key=lambda q: q["paragraph_id"])


def exposure_audit(paras: list[dict]) -> dict:
    """Text-free: every audited file is read for article identifiers (standalone or inside paragraph and row
    identifiers) and for the A3 paragraphs' digests and text hashes (16-character prefixes, which every full
    digest contains); nothing is printed or copied."""
    articles = {q["article_id"] for q in paras}
    hashes = {str(q[k])[:16] for q in paras for k in ("digest", "text_sha256") if q.get(k)}
    files, overlap = [], {}
    for d, pat in AUDIT_SOURCES:
        for f in sorted((ROOT / d).glob(pat)):
            if not f.is_file() or f.name in AUDIT_EXCEPT:
                continue
            data = f.read_bytes()
            text = data.decode("utf-8", errors="replace")
            ids = set(ARTICLE_RE.findall(text))
            rel = str(f.relative_to(ROOT))
            files.append({"file": rel, "sha256": hashlib.sha256(data).hexdigest(), "article_ids": len(ids)})
            hit = sorted(ids & articles) + sorted(h for h in hashes if h in text)
            if hit:
                overlap[rel] = hit
    return {"rule": "refuse the listing if any A3 article identifier (standalone or inside a paragraph or row identifier) "
                    "or any A3 paragraph digest or text hash appears in a public run record, a session manifest, a "
                    "finalised label file, a codebook file or a rendered instrument artefact; the frame manifest is "
                    "excepted because it lists the frame itself. Covers documented study exposure only.",
            "sources": [f"{d}/{pat}" for d, pat in AUDIT_SOURCES], "excepted": sorted(AUDIT_EXCEPT),
            "files": files, "overlap": overlap, "clean": not overlap}


def draw(frame: dict, a2: dict, registry: sr.SplitRegistry) -> dict:
    freeze = d2.verify_freeze(frame, registry)
    if a2.get("kind") != "A2_sample_manifest" or d2.canonical_digest(a2) != a2.get("digest"):
        raise DrawError("the A2 sample manifest is not intact")
    if a2.get("frame_manifest_digest") != frame.get("digest"):
        raise DrawError("the A2 sample was not drawn from this frame")
    paras = a3_frame(frame, a2)
    if not paras:
        raise DrawError("the A3 frame is empty")
    audit = exposure_audit(paras)
    if not audit["clean"]:
        raise DrawError("documented exposure of an A3 article: " + "; ".join(f"{k}: {v}" for k, v in audit["overlap"].items()))
    for q in paras:
        q.update({"stratum": f"{q['lang']}|{q['outlet']}", "pi": 1.0, "weight": 1.0})
    order = random.Random(SEED_ORDER_A3)
    order.shuffle(paras)
    for i, q in enumerate(paras, 1):
        q["sitting_order"] = i
    here = Path(__file__).resolve()
    strata = Counter(q["stratum"] for q in paras)
    doc = {
        "kind": "A3_sample_manifest",
        "created": date.today().isoformat(),
        "frame_manifest_digest": frame["digest"],
        "freeze_event_hash": freeze.get("h"),
        "excludes_sample_manifest": {"kind": "A2_sample_manifest", "digest": a2["digest"]},
        "design": {"census": True, "rule": "every frame paragraph not in the A2 sample, with exposure clean_article "
                                          "and an article that has no paragraph in the A2 sample",
                   "estimand": "the registered quantities on this residual census itself; weight 1 describes the census "
                               "and gives no design-based extrapolation to the frame",
                   "strata": "language x outlet (reporting only)", "pi": 1.0, "weight": 1.0,
                   "reliability_subsample": None, "seeds": {"order": SEED_ORDER_A3}},
        "exposure_audit": audit,
        "strata": [{"stratum": s, "n": n} for s, n in sorted(strata.items())],
        "n": len(paras),
        "by_language": dict(Counter(q["lang"] for q in paras)),
        "n_articles": len({q["article_id"] for q in paras}),
        "by_article": dict(sorted(Counter(q["article_id"] for q in paras).items())),
        "paragraphs": sorted(paras, key=lambda q: q["sitting_order"]),
        "generator": {"file": here.name, "sha256": hashlib.sha256(here.read_bytes()).hexdigest()},
        "note": ("IDs, digests, strata and the sitting order only -- no text. A census of the unseen-article "
                 "frame, listed once; never re-listed."),
    }
    doc["digest"] = d2.canonical_digest(doc)
    return doc


def write_sheets(doc: dict, rows: list[dict], registry: sr.SplitRegistry) -> dict:
    targets = [A3_SAMPLE_MANIFEST, SHEETS / f"{NAME}_sheet-1.csv", SHEETS / f"{NAME}_sheet-2.csv",
               LABELS / f"{SESSION}_manifest.json"]
    existing = [t.name for t in targets if t.exists()]
    if existing:
        raise DrawError(f"{', '.join(existing)} exist(s); A3 is listed once and nothing is overwritten")
    by_key = {(r["article_id"], r["paragraph_id"], r["digest_full"]): r for r in rows}
    sub = []
    for p in doc["paragraphs"]:
        r = by_key.get((p["article_id"], p["paragraph_id"], p["digest"]))
        if r is None:
            raise DrawError(f"{p['paragraph_id']} is not present in the current corpus with its registered identity")
        if r["text_sha256"] != p["text_sha256"]:
            raise DrawError(f"{p['paragraph_id']}: text hash differs from the frame manifest")
        r = dict(r); r["split"] = SESSION
        sub.append(r)
    A3_SAMPLE_MANIFEST.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    SHEETS.mkdir(exist_ok=True)
    with (SHEETS / f"{NAME}_sheet-1.csv").open("x", newline="", encoding=bs.ENC) as f:
        w = csv.DictWriter(f, fieldnames=bs.SHEET1_COLUMNS, delimiter=bs.DELIM)
        w.writeheader()
        for r in sub:
            w.writerow(bs.blank_row(r))
    with (SHEETS / f"{NAME}_sheet-2.csv").open("x", newline="", encoding=bs.ENC) as f:
        w2 = csv.writer(f, delimiter=bs.DELIM)
        w2.writerow(bs.SHEET2_COLUMNS)
        w2.writerow([bs.SHEET2_LABELS.get(c, "") for c in bs.SHEET2_COLUMNS])
    mpath = bs.write_session_manifest(SESSION, sub, registry, kind="sheet-1")
    sdoc = json.loads(mpath.read_text(encoding="utf-8"))
    sdoc["fresh_sample"] = {"sample_manifest": A3_SAMPLE_MANIFEST.name, "sample_manifest_digest": doc["digest"],
                            "seed_order": SEED_ORDER_A3, "census": True, "authority": "pre-analysis specification §4b (2026-10-14)"}
    mpath.write_text(json.dumps(sdoc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"rows": len(sub), "by_language": doc["by_language"], "n_articles": doc["n_articles"],
            "sheet": SHEETS / f"{NAME}_sheet-1.csv", "manifest": mpath}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    frame = json.loads(FRAME_MANIFEST.read_text(encoding="utf-8"))
    a2 = json.loads(A2_SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    registry = sr.SplitRegistry.load(bs.SPLIT_FILE)
    try:
        doc = draw(frame, a2, registry)
    except (DrawError, d2.DrawError) as exc:
        sys.exit(f"REFUSING: {exc}")
    print(f"A3 frame: {doc['n']} paragraphs from {doc['n_articles']} unseen articles, {doc['by_language']}; "
          f"strata {[(s['stratum'], s['n']) for s in doc['strata']]}")
    print(f"  digest {doc['digest'][:16]}  bound to frame {doc['frame_manifest_digest'][:16]}, "
          f"freeze {doc['freeze_event_hash']}, excluding A2 sample {doc['excludes_sample_manifest']['digest'][:16]}")
    print(f"  exposure audit: {len(doc['exposure_audit']['files'])} files read for article identifiers, no documented exposure")
    if a.dry_run:
        print("  dry run: nothing written")
        return
    rows, _ = bs.collect()
    try:
        res = write_sheets(doc, rows, registry)
    except DrawError as exc:
        sys.exit(f"REFUSING: {exc}")
    print(f"wrote labels/{A3_SAMPLE_MANIFEST.name}")
    print(f"wrote {res['sheet'].relative_to(HERE)}   {res['rows']} rows, blank, seeded sitting order")
    print(f"wrote labels/{res['manifest'].name}   session manifest; commit both manifests before opening the sheet")


if __name__ == "__main__":
    main()
