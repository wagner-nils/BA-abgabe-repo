#!/usr/bin/env python3
"""Draw the A2 evaluation sample from the frozen frame manifest. Pre-registered.

    python3 tools/annotation/draw_a2.py            # write labels/A2_sample_manifest.json
    python3 tools/annotation/draw_a2.py --dry-run  # print the allocation, write nothing

Everything below is fixed by tools/PIPELINE-COMPLETION-PLAN.md Step 1.6 and the
pre-analysis specification before any evaluation paragraph is inspected:

  frame       labels/A2_frame_manifest.json -- reserve ∩ candidate ∩ gap-free ∩ article ≠ A011
  strata      language × outlet
  target      60 German / 60 English
  allocation  proportional to N_h within each language, capped at CAP paragraphs per outlet,
              remainder redistributed proportionally among uncapped strata, integers by the
              largest-remainder method with exact fractions; remainder seats to the largest
              fractional part, ties by larger N_h then outlet name ascending
  within      identities sorted by digest, then random.Random(SEED_DRAW).sample
  weights     π_h = n_h / N_h, weight 1/π_h, per paragraph
  reliability 20 + 20 drawn from the sample with SEED_RELIABILITY
  order       the manifest lists the sample in a seeded shuffle -- that is the sitting order

The draw REFUSES unless the registry head is the a2_frozen event whose audit_ref is the
frame manifest's recomputed digest and whose predecessor hash is the manifest's
pre_freeze_head. Any event after the freeze -- an assignment, a drift acceptance --
makes the frame stale and the draw refuses (plan Step 1.5).

Runs on the system interpreter (stdlib only), like every tool in this folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import split_registry as sr                                          # noqa: E402

LABELS = HERE / "labels"
FRAME_MANIFEST = LABELS / "A2_frame_manifest.json"
SAMPLE_MANIFEST = LABELS / "A2_sample_manifest.json"

TARGET = {"de": 60, "en": 60}
CAP = 24                     # 40 % of a 60-paragraph language target, an integer allowance
RELIABILITY_PER_LANG = 20
SEED_DRAW = 20260905         # fixed here, before the frame is inspected
SEED_RELIABILITY = 20260906
SEED_ORDER = 20260907


def canonical_digest(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


class DrawError(RuntimeError):
    pass


def verify_freeze(frame: dict, registry: sr.SplitRegistry) -> dict:
    """The Step 1.5 handshake. Returns the freeze event or raises."""
    if canonical_digest(frame) != frame.get("digest"):
        raise DrawError("the frame manifest's content does not match its digest; it was edited.")
    log = registry.assignment_log
    if not log:
        raise DrawError("empty assignment log")
    head = log[-1]
    if head.get("kind") != sr.EVENT_FREEZE:
        raise DrawError(f"the registry head is a {head.get('kind')!r} event, not the freeze. "
                        f"Either the reserve is not frozen or something was appended after it; "
                        f"either way the frame is stale.")
    if head.get("audit_ref") != frame["digest"]:
        raise DrawError("the freeze event's audit_ref is not this frame manifest's digest.")
    prev_hash = log[-2].get("h") if len(log) > 1 else None
    if prev_hash != frame.get("pre_freeze_head"):
        raise DrawError("the freeze event's predecessor is not the frame manifest's pre_freeze_head.")
    if head.get("eligible_checksum") != frame.get("eligible_checksum"):
        raise DrawError("the freeze's eligible_checksum differs from the frame manifest's.")
    return head


def smallest_feasible_cap(sizes: dict[str, int], target: int, cap: int) -> int:
    c = cap
    while sum(min(n, c) for n in sizes.values()) < target:
        c += 1
        if c > max(sizes.values(), default=0):
            raise DrawError(f"target {target} exceeds the frame ({sum(sizes.values())})")
    return c


def allocate(sizes: dict[str, int], target: int, cap: int) -> dict[str, int]:
    """Capped proportional allocation with largest-remainder integers. Deterministic."""
    if sum(min(n, cap) for n in sizes.values()) < target:
        raise DrawError("infeasible: raise the cap first")
    alloc: dict[str, int] = {}
    fixed: dict[str, int] = {}
    open_ = dict(sizes)
    remaining = target
    while True:
        total = sum(open_.values())
        if total == 0 or remaining == 0:
            break
        raw = {h: Fraction(remaining * n, total) for h, n in open_.items()}
        over = {h: min(cap, open_[h]) for h, x in raw.items() if x > min(cap, open_[h])}
        if not over:
            floors = {h: int(x) for h, x in raw.items()}
            seats = remaining - sum(floors.values())
            order = sorted(raw, key=lambda h: (-(raw[h] - floors[h]), -open_[h], h))
            for h in order[:seats]:
                floors[h] += 1
            alloc.update(floors)
            break
        for h, n in over.items():
            fixed[h] = n
            remaining -= n
            del open_[h]
    alloc.update(fixed)
    for h in sizes:
        alloc.setdefault(h, 0)
    assert sum(alloc.values()) == target, alloc
    assert all(alloc[h] <= min(cap, sizes[h]) for h in sizes), alloc
    return alloc


def draw(frame: dict, registry: sr.SplitRegistry, *, dry_run: bool, cap: int = CAP,
         cap_amendment: str | None = None) -> dict:
    freeze = verify_freeze(frame, registry)
    paras = frame["paragraphs"]
    by_stratum: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in paras:
        by_stratum[(p["lang"], p["outlet"])].append(p)
    if any(p["article_id"] == "A011" for p in paras):
        raise DrawError("A011 is in the frame; the frame manifest violates its own rule.")

    rng = random.Random(SEED_DRAW)
    sample: list[dict] = []
    allocation_report = []
    caps_used = {}
    for lang, target in sorted(TARGET.items()):
        sizes = {o: len(v) for (l, o), v in by_stratum.items() if l == lang}
        if sum(min(n, cap) for n in sizes.values()) < target:
            # The cap is part of the registered design; it is NOT raised silently. The
            # smallest feasible integer is named so an explicit, dated amendment can set it.
            raise DrawError(f"cap {cap} is infeasible for {lang} (capacity "
                            f"{sum(min(n, cap) for n in sizes.values())} < {target}); the smallest "
                            f"feasible cap is {smallest_feasible_cap(sizes, target, cap)}. Amend the "
                            f"design explicitly with --cap N --amendment '<reason>'.")
        caps_used[lang] = cap
        alloc = allocate(sizes, target, cap)
        for outlet in sorted(sizes):                 # sorted traversal, one seeded RNG
            n_h, N_h = alloc[outlet], sizes[outlet]
            pool = sorted(by_stratum[(lang, outlet)], key=lambda p: p["digest"])
            chosen = rng.sample(pool, n_h) if n_h else []
            pi = Fraction(n_h, N_h) if N_h else Fraction(0)
            for p in chosen:
                q = dict(p)
                q.update({"stratum": f"{lang}|{outlet}", "N_h": N_h, "n_h": n_h,
                          "pi": float(pi), "weight": float(1 / pi) if pi else None,
                          "reliability_subsample": False})
                sample.append(q)
            allocation_report.append({"lang": lang, "outlet": outlet, "N_h": N_h, "n_h": n_h,
                                      "pi": str(pi)})
    # reliability subsample: 20 per language from the sample, own seed
    rel = random.Random(SEED_RELIABILITY)
    for lang in sorted(TARGET):
        idx = [i for i, q in enumerate(sample) if q["lang"] == lang]
        for i in rel.sample(idx, RELIABILITY_PER_LANG):
            sample[i]["reliability_subsample"] = True
    # sitting order: a seeded shuffle of the whole sample
    order = random.Random(SEED_ORDER)
    order.shuffle(sample)
    for i, q in enumerate(sample, 1):
        q["sitting_order"] = i

    here = Path(__file__).resolve()
    doc = {
        "kind": "A2_sample_manifest",
        "created": date.today().isoformat(),
        "frame_manifest_digest": frame["digest"],
        "freeze_event_hash": freeze.get("h"),
        "design": {"strata": "language x outlet", "target": TARGET, "cap_registered": CAP,
                   "cap_used": caps_used, "cap_amendment": cap_amendment, "allocation": "proportional within language, capped, "
                   "largest remainder with exact fractions; ties by larger N_h then outlet name",
                   "within_stratum": "sorted by digest, random.Random(SEED_DRAW).sample",
                   "seeds": {"draw": SEED_DRAW, "reliability": SEED_RELIABILITY,
                             "order": SEED_ORDER},
                   "reliability_per_language": RELIABILITY_PER_LANG},
        "allocation": allocation_report,
        "n": len(sample),
        "by_language": dict(Counter(q["lang"] for q in sample)),
        "by_exposure": dict(Counter(q["exposure"] for q in sample)),
        "n_articles": len({q["article_id"] for q in sample}),
        "paragraphs": sorted(sample, key=lambda q: q["sitting_order"]),
        "generator": {"file": here.name, "sha256": hashlib.sha256(here.read_bytes()).hexdigest()},
        "note": ("IDs, digests, strata, probabilities and weights only -- no text. Drawn once "
                 "against the frozen frame; never re-drawn."),
    }
    doc["digest"] = canonical_digest(doc)
    if not dry_run:
        if SAMPLE_MANIFEST.exists():
            raise DrawError(f"{SAMPLE_MANIFEST.name} exists: the sample is drawn once.")
        SAMPLE_MANIFEST.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cap", type=int, default=CAP,
                    help=f"per-outlet cap; {CAP} is the registered design, anything else needs --amendment")
    ap.add_argument("--amendment", default=None, help="dated reason for a cap other than the registered one")
    a = ap.parse_args()
    if a.cap != CAP and not a.amendment:
        sys.exit("a cap other than the registered one requires --amendment '<dated reason>'")
    if not FRAME_MANIFEST.exists():
        sys.exit("no frame manifest: run build_sheets.py --freeze-a2 first")
    frame = json.loads(FRAME_MANIFEST.read_text(encoding="utf-8"))
    registry = sr.SplitRegistry.load()
    try:
        doc = draw(frame, registry, dry_run=a.dry_run, cap=a.cap, cap_amendment=a.amendment)
    except DrawError as exc:
        sys.exit(f"REFUSING to draw: {exc}")
    print("allocation (lang | outlet : n_h / N_h, pi):")
    for r in doc["allocation"]:
        print(f"  {r['lang']} | {r['outlet']:<28} {r['n_h']:>3} / {r['N_h']:<4} pi={r['pi']}")
    print(f"caps used: {doc['design']['cap_used']}   sample n={doc['n']} "
          f"{doc['by_language']}   articles={doc['n_articles']}   exposure={doc['by_exposure']}")
    if a.dry_run:
        print("dry run: nothing written")
    else:
        print(f"wrote labels/{SAMPLE_MANIFEST.name}  digest {doc['digest'][:16]}   commit it "
              f"before generating the sheet")


if __name__ == "__main__":
    main()
