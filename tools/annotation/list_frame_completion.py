#!/usr/bin/env python3
"""Frame completion for the exploratory DSL estimate (pre-analysis specification §4d, 2026-10-19): the frozen
frame's paragraphs that are neither in the registered A2 sample nor in the A3 census, listed once, text-free.

    python3 tools/annotation/list_frame_completion.py --dry-run    # print the listing, write nothing
    python3 tools/annotation/list_frame_completion.py              # write labels/A2_frame_completion_manifest.json

The listing feeds one run of the registered instrument (one execution per paragraph) whose predictions complete the
model's coverage of the frame; it is not a sample, carries no weights, is never scored against a reference, and is
never re-listed. Runs on the system interpreter (stdlib only), like every tool in this folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
FRAME_MANIFEST = LABELS / "A2_frame_manifest.json"
A2_SAMPLE_MANIFEST = LABELS / "A2_sample_manifest.json"
A3_SAMPLE_MANIFEST = LABELS / "A3_sample_manifest.json"
OUT = LABELS / "A2_frame_completion_manifest.json"
KIND = "frame_completion_manifest"


class ListError(RuntimeError):
    pass


def build(frame: dict, a2: dict, a3: dict, registry: sr.SplitRegistry) -> dict:
    freeze = d2.verify_freeze(frame, registry)
    for name, doc, kind in (("A2", a2, "A2_sample_manifest"), ("A3", a3, "A3_sample_manifest")):
        if doc.get("kind") != kind or d2.canonical_digest(doc) != doc.get("digest"):
            raise ListError(f"the {name} manifest is not intact")
        if doc.get("frame_manifest_digest") != frame.get("digest"):
            raise ListError(f"the {name} manifest was not drawn from this frame")
    excluded = {q["digest"] for q in a2["paragraphs"]} | {q["digest"] for q in a3["paragraphs"]}
    paras = [dict(q) for q in frame["paragraphs"] if q["digest"] not in excluded]
    if not paras:
        raise ListError("nothing to complete: every frame paragraph is in A2 or A3")
    paras.sort(key=lambda q: q["paragraph_id"])
    here = Path(__file__).resolve()
    doc = {
        "kind": KIND, "created": date.today().isoformat(),
        "frame_manifest_digest": frame["digest"], "freeze_event_hash": freeze.get("h"),
        "excludes": {"A2": {"kind": "A2_sample_manifest", "digest": a2["digest"]},
                     "A3": {"kind": "A3_sample_manifest", "digest": a3["digest"]}},
        "rule": "every frame paragraph whose digest is in neither the registered A2 sample nor the A3 census; not a "
                "sample, no weights, never scored; the model's execution-one predictions on it complete the frame "
                "coverage for the exploratory DSL estimate (specification §4d)",
        "n": len(paras), "by_language": dict(Counter(q["lang"] for q in paras)),
        "n_articles": len({q["article_id"] for q in paras}), "by_exposure": dict(Counter(q.get("exposure") for q in paras)),
        "paragraphs": paras,
        "generator": {"file": here.name, "sha256": hashlib.sha256(here.read_bytes()).hexdigest()},
        "note": "IDs, digests and frozen metadata only -- no text. Listed once; never re-listed.",
    }
    doc["digest"] = d2.canonical_digest(doc)
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    frame = json.loads(FRAME_MANIFEST.read_text(encoding="utf-8"))
    a2 = json.loads(A2_SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    a3 = json.loads(A3_SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    registry = sr.SplitRegistry.load(bs.SPLIT_FILE)
    try:
        doc = build(frame, a2, a3, registry)
    except (ListError, d2.DrawError) as exc:
        sys.exit(f"REFUSING: {exc}")
    print(f"frame completion: {doc['n']} paragraphs from {doc['n_articles']} articles, {doc['by_language']}, "
          f"exposure {doc['by_exposure']}; digest {doc['digest'][:16]}; frame {doc['frame_manifest_digest'][:16]}, "
          f"excluding A2 {doc['excludes']['A2']['digest'][:16]} and A3 {doc['excludes']['A3']['digest'][:16]}")
    if a.dry_run:
        print("  dry run: nothing written")
        return
    if OUT.exists():
        sys.exit(f"REFUSING: {OUT.name} exists; the listing is written once")
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote labels/{OUT.name}")


if __name__ == "__main__":
    main()
