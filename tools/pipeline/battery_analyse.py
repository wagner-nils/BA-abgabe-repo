#!/usr/bin/env python3
"""Analysis of the §4i label-free instrument-sensitivity battery (pre-analysis specification §4i, 2026-10-30).

Reads the battery journals (terminal records: outcome and the public payload, ids only) and computes, per class
(17 elements, 7 technologies, and `general` beside another technology):

* d(X, Y): the share of paragraphs valid in both executions whose presence of the class differs; J(X, Y) the same
  count divided by the paragraphs where either execution has the class (1 - Jaccard; prevalence-normalised);
* the shift of a variant V against the baseline R: mean d(V_i, R_j) - (d(V_1, V_2) + mean d(R_i, R_j)) / 2 where V has
  two executions, else mean d(V_1, R_j) - mean d(R_i, R_j) (disclosed); the same for J; the firing-rate change;
* 95 % intervals by resampling articles jointly over all executions (B = 2,000, seed 20260905).

Primary readings (declared): PR1 planted gaps -- for each of the eight rule-carrying elements, its shift when its
rules are removed (E1 or E2) minus its shift when the other half's are; the mean over the eight. PR2 the documented
gap -- the shift of `general` under K1 minus the mean shift of `hydrogen` and `nuclear` under K1c. Secondary: per
family and unit; paraphrase directions; the O1 position effect; K2; a swap matrix; correlations with coder alpha and
per-element F1; the A2 unit-level validation (AUC). Outputs: tools/runs/battery/analysis.json and the thesis tables.

Routes (2026-10-30): after the EU daily token quota was exhausted the remaining calls ran on the global profile, and the
baseline repeated itself less exactly across and on that route. Every shift is therefore computed route-stratified --
within the EU and within the global route, each from pairs whose two executions ran on it, against baseline executions
1-5 (4 and 5 are the global-route baseline, phase RG) -- and averaged with weights equal to the compared
variant-baseline pairs; the declared computation over every pair against executions 1-3 is reported beside
(`as_declared`). Flips, swaps and K2 compare a variant with the baseline on its own route.

    .venv/bin/python3 tools/pipeline/battery_analyse.py                  # bootstrap, analysis.json and the tables
    .venv/bin/python3 tools/pipeline/battery_analyse.py --tables-only    # tables from analysis.json
    .venv/bin/python3 tools/pipeline/battery_analyse.py --check          # tables match analysis.json
"""
from __future__ import annotations

import csv
import json
import multiprocessing as mp
import random
import sys
from collections import Counter, defaultdict
from operator import mul
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import battery as bt                                                # noqa: E402

ROOT = HERE.parents[1]
LABELS = ROOT / "tools" / "annotation" / "labels"
TABLES = ROOT / "tools" / "runs" / "tables" / "a2_sonnet_20261013"
import os
B, SEED = int(os.environ.get("BATTERY_B", "2000")), 20260905      # BATTERY_B only for a quick look; reported figures use 2,000
OK = ("technologies_present", "off_topic")
MIN_UNION = 10


def latest_terminals() -> dict:
    """(sample, variant, execution, pid) -> the latest terminal record (an `error` re-run replaces it)."""
    latest = {}
    for r in bt.read_journals():
        if r.get("type") == "terminal":
            k_ = (r["sample"], r["variant"], r["execution"], r["paragraph_id"])
            if k_ not in latest or r["at"] > latest[k_]["at"]:
                latest[k_] = r
    return latest


def terminal_fingerprint(latest: dict) -> dict:
    """The terminal set an analysis read: count and a digest over (sample, variant, execution, pid, at, outcome)."""
    import hashlib
    h = hashlib.sha256()
    for k_ in sorted(latest):
        r = latest[k_]
        h.update(json.dumps([*k_, r["at"], r["outcome"]], ensure_ascii=False).encode("utf-8"))
    return {"n": len(latest), "sha256": h.hexdigest()}


def load_terminals(latest: dict | None = None) -> dict:
    """(sample, variant, execution) -> pid -> (valid, elements, technologies, relations)."""
    runs = defaultdict(dict)
    for r in (latest if latest is not None else latest_terminals()).values():
        if r.get("type") != "terminal":
            continue
        valid = r["outcome"] in OK
        els, techs, rels = set(), set(), set()
        for rel in ((r.get("payload") or {}).get("relations") or []) if valid else []:
            techs.add(rel["technology"])
            for e in rel.get("elements") or []:
                els.add(e); rels.add((rel["technology"], e))
        runs[(r["sample"], r["variant"], r["execution"])][r["paragraph_id"]] = (valid, els, techs, rels)
    return runs


def classes(doc: dict) -> list[str]:
    return [e["id"] for e in doc["frame_elements"]] + [f"tech:{t['id']}" for t in doc["technologies"]] + ["general_beside"]


def present(cell, cls: str) -> bool:
    valid, els, techs, rels = cell
    if cls.startswith("tech:"):
        return cls[5:] in techs
    if cls == "general_beside":
        return "general" in techs and len(techs) > 1
    return cls in els


ARTS: list = []                 # the frame-completion articles in resampling order (set in main)
ART_INDEX: dict = {}


class Mult(dict):
    """An article multiplicity (article -> count) with the dense vector over ARTS that the shift sums use."""

    def __init__(self, counts, arts: list):
        super().__init__(counts)
        # over all frame-completion articles, so that a resample of a subset (one language) still lines up with the counts
        self.vec = [self.get(a, 0) for a in (ARTS or arts)]


def ones(arts: list) -> Mult:
    return Mult({a: 1 for a in arts}, arts)


def pair_counts(X: dict, Y: dict, cls_list: list[str], art: dict, rX: dict | None = None, rY: dict | None = None,
                only: str | None = None) -> dict:
    """class -> five dense lists over ARTS [diff, union, n_valid_both, present_in_X, present_in_Y]; with routes
    (pid -> route) and a route `only` given, only paragraphs whose two executions both ran on that route count."""
    n_ = len(ARTS)
    out = {c: [[0] * n_ for _ in range(5)] for c in cls_list}
    for pid in X.keys() & Y.keys():
        if not (X[pid][0] and Y[pid][0]):
            continue
        if only is not None and not (rX[pid] == rY[pid] == only):
            continue
        a = ART_INDEX[art[pid]]
        for c in cls_list:
            px, py = present(X[pid], c), present(Y[pid], c)
            v = out[c]
            v[0][a] += px != py; v[1][a] += px or py; v[2][a] += 1; v[3][a] += px; v[4][a] += py
    return out


def pooled(pcs: list, cls_list: list[str]) -> list:
    """The pairs of one family summed into one (their counts are additive under any article multiplicity)."""
    if not pcs:
        return []
    return [{c: [[sum(col) for col in zip(*(p[c][i] for p in pcs))] for i in range(5)] for c in cls_list}]


def total(counts: list, mult: Mult, idx: int) -> float:
    return sum(map(mul, mult.vec, counts[idx]))


def d_and_j(counts: dict, mult: dict) -> tuple:
    diff, union, n = total(counts, mult, 0), total(counts, mult, 1), total(counts, mult, 2)
    return (diff / n if n else None, diff / union if union else None, union)


class Shift:
    """The shift of variant V against R for a set of classes, computable under any article multiplicity."""

    def __init__(self, runs: dict, sample: str, vid: str, cls_list: list[str], art: dict, base: str = "R",
                 route: dict | None = None, r_execs: tuple | None = None):
        """route None: every pair as declared (baseline executions `r_execs`), d averaged over the pairs. route given:
        route-stratified -- within each route only paragraphs whose two executions both ran on it count, and the pairs
        of each family (variant-baseline, variant-variant, baseline-baseline) are pooled into one ratio of sums: the
        Jaccard instability is sum(differing) / sum(union) over the pairs, so a pair contributes in proportion to its
        union, and the share-based d is sum(differing) / sum(paragraphs), in proportion to its matched paragraphs (a
        plain mean would give a pair with a handful of matched paragraphs the weight of a full one); the routes'
        shifts are then averaged with weights equal to the compared variant-baseline paragraph pairs."""
        self.cls = cls_list
        self.R = sorted(k for (s, v, k) in runs if s == sample and v == base and (r_execs is None or k in r_execs))
        self.V = sorted(k for (s, v, k) in runs if s == sample and v == vid)
        get = lambda v, k: runs[(sample, v, k)]
        rt = (lambda v, k: {pid: route[(sample, v, k, pid)] for pid in runs[(sample, v, k)]}) if route else (lambda v, k: None)
        self.strata = {}
        for rho in (("eu", "global") if route else (None,)):
            pc = lambda v1, k1, v2, k2: pair_counts(get(v1, k1), get(v2, k2), cls_list, art, rt(v1, k1), rt(v2, k2), rho)
            fams = ([pc(vid, i, base, j) for i in self.V for j in self.R],
                    [pc(vid, self.V[0], vid, self.V[1])] if len(self.V) > 1 else [],
                    [pc(base, i, base, j) for n_, i in enumerate(self.R) for j in self.R[n_ + 1:]])
            self.strata[rho] = tuple(pooled(f, cls_list) for f in fams) if route else fams
        self.n_vr_pairs = len(self.V) * len(self.R)

    def stat(self, c: str, mult: dict) -> dict:
        parts = [(self._stat(c, mult, *pairs), sum(total(p[c], mult, 2) for p in pairs[0])) for pairs in self.strata.values()]
        if len(parts) == 1:
            return parts[0][0]
        out = {}
        for key in ("shift", "shift_j", "rate_change", "rate_base"):
            xs = [(st[key], w) for st, w in parts if st[key] is not None and w]
            out[key] = sum(x * w for x, w in xs) / sum(w for _, w in xs) if xs else None
        # reporting support: the class's union per compared variant-baseline pair, summed over the routes
        out["union_min"] = sum(sum(total(p[c], mult, 1) for p in pairs[0]) for pairs in self.strata.values()) / self.n_vr_pairs
        out["by_route"] = {rho: {"shift_j": st["shift_j"], "shift": st["shift"], "pairs": w}
                           for rho, (st, w) in zip(self.strata, parts)}
        return out

    def _stat(self, c: str, mult: dict, vr_: list, vv_: list, rr_: list) -> dict:
        def mean(xs):
            xs = [x for x in xs if x is not None]
            return sum(xs) / len(xs) if xs else None
        vr = [d_and_j(p[c], mult) for p in vr_]
        rr = [d_and_j(p[c], mult) for p in rr_]
        vv = [d_and_j(p[c], mult) for p in vv_]
        out = {}
        for i, name in ((0, "shift"), (1, "shift_j")):
            a, nr = mean([x[i] for x in vr]), mean([x[i] for x in rr])
            nv = mean([x[i] for x in vv]) if vv else nr
            out[name] = None if a is None or nr is None or nv is None else a - (nv + nr) / 2
        # firing rates: share present among valid paragraphs, V executions against R executions
        rv = mean([total(p[c], mult, 3) / total(p[c], mult, 2) for p in vr_ if total(p[c], mult, 2)])
        rb = mean([total(p[c], mult, 4) / total(p[c], mult, 2) for p in vr_ if total(p[c], mult, 2)])
        out["rate_change"] = None if rv is None or rb is None else rv - rb
        out["rate_base"] = rb
        out["union_min"] = min((x[2] for x in vr), default=0)
        return out


def interval(fn, arts: list, seed_offset: int = 0) -> tuple:
    """fn(mult) -> float|None; returns (point, lo, hi) under article resampling."""
    point = fn(ones(arts))
    rng = random.Random(SEED + seed_offset)
    draws = []
    for _ in range(B):
        mult = Mult(Counter(rng.choice(arts) for _ in arts), arts)
        v = fn(mult)
        if v is not None:
            draws.append(v)
    draws.sort()
    q = (lambda p: draws[int(round(p * (len(draws) - 1)))]) if draws else (lambda p: None)
    return point, q(0.025), q(0.975)


def spearman(x: list, y: list) -> float | None:
    if len(x) < 3:
        return None
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    rx_, ry_ = ranks(x), ranks(y)
    mx, my = sum(rx_) / len(rx_), sum(ry_) / len(ry_)
    num = sum((a - mx) * (b - my) for a, b in zip(rx_, ry_))
    den = (sum((a - mx) ** 2 for a in rx_) * sum((b - my) ** 2 for b in ry_)) ** 0.5
    return num / den if den else None


def auc(scores: list, labels: list) -> float | None:
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def human_side() -> dict:
    """Coder alpha (elements, and technology ticks keyed by the technology id) and pooled per-element F1 on A2."""
    alpha, f1 = {}, {}
    with (TABLES / "table9_reliability.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            lvl = r.get("level", "")
            if (lvl.startswith("element") or lvl == "technology tick") and r.get("alpha") not in (None, "", "undefined", "NA"):
                try:
                    alpha[r["item"]] = float(r["alpha"])
                except ValueError:
                    pass
    with (TABLES / "table2_per_class.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r.get("population") == "pooled" and r.get("dimension") == "element" and r.get("F1 weighted") not in (None, "", "--"):
                try:
                    f1[r["class"]] = float(r["F1 weighted"])
                except ValueError:
                    pass
    return {"alpha": alpha, "f1": f1}


def reference_presence(path: Path) -> dict:
    out = defaultdict(set)
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            for k, v in r.items():
                if k and k[:1] == "F" and k[1:2].isdigit() and str(v).strip().lower() in ("1", "x", "true", "yes"):
                    out[r["paragraph_id"]].add(k)
    return out


WORKERS = max(1, (os.cpu_count() or 2) - 2)
_CTX = None


def _family(task: tuple) -> tuple:
    """One variant's shifts for every class (or its own unit for a one-unit variant), with intervals.

    Declared measure (§4i): the instability of a class between two executions is 1 - Jaccard over paragraphs
    (`shift_j`, which decides `moves`); the share-based d (`shift`) is reported beside it. The firing-rate change gets
    its own interval: the paraphrase reading is defined on it. The reporting gate (a union of at least 10 paragraphs)
    is the declared one in both modes -- every pair against baseline executions 1-3 -- so both modes report the same
    classes."""
    n, v = task
    runs, art, arts, cls_all, route, r_execs = _CTX
    sh = Shift(runs, "FC", v, cls_all, art, route=route, r_execs=r_execs)
    gate = sh if route is None else Shift(runs, "FC", v, cls_all, art, r_execs=(1, 2, 3))
    fam = {}
    own = None
    if v.startswith("Pu_"):
        unit = v.split("_")[1]
        own = [unit if unit[:1] == "F" else f"tech:{unit}"]
    for i, c in enumerate(own or cls_all):
        g = gate.stat(c, ones(arts))["union_min"]
        if g < MIN_UNION:
            fam[c] = {"n_union_min": g, "reported": False}
            continue
        base_stat = sh.stat(c, ones(arts))
        pt, lo, hi = interval(lambda m, c=c: sh.stat(c, m)["shift"], arts, n * 100 + i)
        ptj, loj, hij = interval(lambda m, c=c: sh.stat(c, m)["shift_j"], arts, n * 100 + i + 50)
        prc, lor, hir = interval(lambda m, c=c: sh.stat(c, m)["rate_change"], arts, n * 100 + i + 75)
        fam[c] = {"shift_j": ptj, "lo_j": loj, "hi_j": hij, "shift": pt, "lo": lo, "hi": hi,
                  "moves": loj is not None and loj > 0,
                  "rate_change": prc, "lo_rate": lor, "hi_rate": hir, "rate_base": base_stat["rate_base"],
                  "rate_direction": 1 if (lor is not None and lor > 0) else -1 if (hir is not None and hir < 0) else 0,
                  "n_union_min": g, "reported": True}
        if "by_route" in base_stat:
            fam[c]["by_route"] = base_stat["by_route"]
    return v, fam


def readings(runs: dict, doc: dict, subtypes, cb_sha, art_pinned, art: dict, arts: list, variants: list, breaking: list,
             cls_all: list, route: dict | None = None, r_execs: tuple | None = None) -> dict:
    """Shifts per family, PR1, PR2, paraphrase readings, the O1 position effect and the correlations with the human side,
    under one pairing mode (see `Shift`)."""
    res = {"families": {}, "primary": {}, "paraphrase_reading": {}, "position_effect": {}, "correlations": {}}
    shifts = {}
    todo = [(n, v) for n, v in enumerate(variants) if v not in breaking]
    global _CTX
    _CTX = (runs, art, arts, cls_all, route, r_execs)
    with mp.get_context("fork").Pool(min(len(todo), WORKERS)) as pool:
        for v, fam in pool.imap(_family, todo):
            res["families"][v] = fam
            print(f"  {v}: {sum(1 for x in fam.values() if x.get('reported'))} classes", flush=True)
    for v in ("E1", "E2", "K1", "K1c"):           # the primary readings need these four in this process
        if v in variants and v not in breaking:
            shifts[v] = Shift(runs, "FC", v, cls_all, art, route=route, r_execs=r_execs)
    # PR1 (planted gaps) and PR2 (the documented gap), on the declared measure (1 - Jaccard) and, beside, on d
    h1, h2 = bt.planted_halves(doc)
    for key, sfx in (("shift_j", ""), ("shift", "_d")):
        if "E1" in shifts and "E2" in shifts:
            planted_in = {e: "E1" for e in h1} | {e: "E2" for e in h2}
            other = lambda e: "E2" if planted_in[e] == "E1" else "E1"
            carriers = h1 + h2
            def contrast(e, m, key=key):
                a_, b_ = shifts[planted_in[e]].stat(e, m)[key], shifts[other(e)].stat(e, m)[key]
                return None if a_ is None or b_ is None else a_ - b_
            def pr1(m):
                vals = [contrast(e, m) for e in carriers]
                return None if any(x is None for x in vals) else sum(vals) / len(vals)
            pt, lo, hi = interval(pr1, arts, 9001 + (sfx == "_d") * 500)
            per = {}
            for e in carriers:
                per[e] = dict(zip(("contrast", "lo", "hi"),
                                  interval(lambda m, e=e: contrast(e, m), arts, 9100 + carriers.index(e) + (sfx == "_d") * 500)))
                per[e]["union_min"] = min(shifts[planted_in[e]].stat(e, ones(arts))["union_min"],
                                          shifts[other(e)].stat(e, ones(arts))["union_min"])
            res["primary"]["PR1" + sfx] = {"mean_contrast": pt, "lo": lo, "hi": hi, "per_element": per,
                                           "halves": {"E1": h1, "E2": h2}, "passes": lo is not None and lo > 0}
        if "K1" in shifts and "K1c" in shifts:
            def pr2(m, key=key):
                g_ = shifts["K1"].stat("tech:general", m)[key]
                hs_ = [shifts["K1c"].stat(t, m)[key] for t in ("tech:hydrogen", "tech:nuclear")]
                return None if g_ is None or any(x is None for x in hs_) else g_ - sum(hs_) / 2
            pt, lo, hi = interval(pr2, arts, 9200 + (sfx == "_d") * 500)
            res["primary"]["PR2" + sfx] = {"contrast": pt, "lo": lo, "hi": hi, "passes": lo is not None and lo > 0,
                                           "general_K1": shifts["K1"].stat("tech:general", ones(arts)),
                                           "controls_K1c": {t: shifts["K1c"].stat(t, ones(arts))
                                                            for t in ("tech:hydrogen", "tech:nuclear")}}
    pr = res["primary"]
    res["primary"]["gap_detector_statement"] = bool(pr.get("PR1", {}).get("passes") and pr.get("PR2", {}).get("passes"))
    # paraphrase reading (declared): a unit whose paraphrase sets -- the four one-unit sets for the important units, Pa1
    # and Pa2 for the others -- all move its firing rate in the same direction is underdetermined by its wording,
    # opposite directions brittle, neither stable. A set moves the rate when the 95 % interval of the firing-rate change
    # excludes zero; sets that do not exist (a text rejected twice), a contract-breaking variant or a union below 10
    # leave the set unavailable, and the reading is on the available sets (their number is recorded).
    acc = bt.load_paraphrases()
    paths_of = defaultdict(list)
    for path_, u_ in bt.text_units(doc).items():
        paths_of[u_["unit"]].append(path_)
    for c in cls_all:
        if c == "general_beside":
            continue
        unit = c[5:] if c.startswith("tech:") else c
        keys = ([f"Pu_{unit}_{x}" for x in ("P1", "P2", "P3", "P4")] if unit in bt.IMPORTANT else ["Pa1", "Pa2"])
        sets = []
        for k_ in keys:
            x = (res["families"].get(k_) or {}).get(c)
            if k_ not in res["families"]:
                sets.append({"set": k_, "available": False, "why": "no variant (text rejected twice) or contract-breaking"})
            elif not (x and x.get("reported")):
                sets.append({"set": k_, "available": False, "why": "union below 10"})
            else:
                sets.append({"set": k_, "available": True, "direction": x["rate_direction"], "rate_change": x["rate_change"],
                             "lo_rate": x["lo_rate"], "hi_rate": x["hi_rate"], "moves_j": x["moves"],
                             "shift_j": x["shift_j"], "lo_j": x["lo_j"], "hi_j": x["hi_j"]})
        av = [x for x in sets if x["available"]]
        dirs = [x["direction"] for x in av]
        reading = ("not available" if not av else
                   "underdetermined by its wording" if all(d == 1 for d in dirs) or all(d == -1 for d in dirs) else
                   "brittle" if 1 in dirs and -1 in dirs else "stable")
        # whether the all-texts sets actually reworded this unit's own texts (Pa2 kept the original of 10 of 33 texts):
        # a move of a unit whose texts were kept comes from the other texts alone
        own = {k_: sum(1 for path_ in paths_of[unit] if path_ in acc.get(k_, {})) for k_ in ("Pa1", "Pa2")}
        res["paraphrase_reading"][unit] = {"sets": sets, "available": len(av), "declared": len(keys),
                                           "important": unit in bt.IMPORTANT, "reading": reading,
                                           "own_texts": len(paths_of[unit]), "own_texts_paraphrased": own}
    # O1 position effect: change in list position against change in firing rate
    order_r = [e["id"] for e in doc["frame_elements"]]
    vs = bt.variants(doc, subtypes, cb_sha, art_pinned)
    order_o = [e["id"] for e in vs["O1"]["doc"]["frame_elements"]]
    pos = {e: order_o.index(e) - order_r.index(e) for e in order_r}
    o1 = res["families"].get("O1") or {}
    pairs_ = [(pos[e], o1[e]["rate_change"]) for e in order_r if (o1.get(e) or {}).get("reported")]
    res["position_effect"] = {"pairs": {e: [pos[e], (o1.get(e) or {}).get("rate_change")] for e in order_r},
                              "spearman": spearman([p for p, _ in pairs_], [r for _, r in pairs_]),
                              "prediction": "negative (moved later -> fires less)"}
    # correlations with the human side (descriptive)
    hs = human_side()
    for fam_key in ("Pa1", "Pa2", "O1", "G"):
        fam = res["families"].get(fam_key) or {}
        for name, ref in (("alpha", hs["alpha"]), ("f1", hs["f1"])):
            xs = [(fam[e]["shift_j"], ref[e]) for e in ref if (fam.get(e) or {}).get("reported") and fam[e]["shift_j"] is not None]
            res["correlations"][f"spearman_{fam_key}_{name}"] = {"rho": spearman([x for x, _ in xs], [y for _, y in xs]), "n": len(xs)}
    return res


# --------------------------------------------------------------------------------------------------- thesis tables
FAMILY_ORDER = ("O1", "O2", "G", "K1", "K1c", "K2", "E1", "E2", "Pa1", "Pa2")
CHANGE = {"O1": "frame blocks reversed", "O2": "technology list reversed", "G": "element labels neutral",
          "K1": "general: per-statement sentence", "K1c": "hydrogen, nuclear: parallel sentence (controls)",
          "K2": "general: residual sentence", "E1": "rules removed: F2c, F4b, F5a, F8a",
          "E2": "rules removed: F1a, F2b, F6a, F6b", "Pa1": "all definitions paraphrased (Kimi K3)",
          "Pa2": "all definitions paraphrased (Qwen3)"}


def _d(v: dict | None, key="shift_j", lo="lo_j", hi="hi_j") -> str:
    if not v or v.get(key) is None:
        return "--"
    if v.get(lo) is None:
        return f"${v[key]:+.3f}$"
    return f"${v[key]:+.3f}$ [${v[lo]:+.3f}$, ${v[hi]:+.3f}$]"


def _cls(c: str) -> str:
    return r"\texttt{" + (c[5:] if c.startswith("tech:") else c).replace("_", r"\_") + "}"


def _arrow(x: dict) -> str:
    if not x.get("available"):
        return "n/a"
    return {1: r"$\uparrow$", -1: r"$\downarrow$", 0: "0"}[x["direction"]]


def tex_tables(res: dict) -> dict:
    """The three appendix tables, from analysis.json alone (no recomputation)."""
    import figures as fg
    head = ("% Generated by tools/pipeline/battery_analyse.py from tools/runs/battery/analysis.json (specification §4i). "
            "Do not edit by hand.\n")
    L = ">{\\raggedright\\arraybackslash}p{%smm}"
    boot = "$B = " + f"{res['B']:,}".replace(",", "{,}") + f"$, seed {res['seed']}"
    execs = Counter(k.split("/")[1] for k in res["runs"] if k.startswith("FC/"))
    dec = res.get("as_declared", {})
    out = {}
    # 1 families
    rows = []
    for v in FAMILY_ORDER:
        fam = res["families"].get(v)
        if fam is None:
            rows.append(" & ".join([v, CHANGE[v], str(execs.get(v, 0)), f"{res['invalid_rate'].get(v, 0):.3f}",
                                    "contract-breaking" if v in res["contract_breaking"] else "--", "--"]) + r" \\")
            continue
        rep_ = {c: x for c, x in fam.items() if x.get("reported")}
        mov = [c for c, x in rep_.items() if x["moves"]]
        mov_d = sum(1 for x in (dec.get("families", {}).get(v) or {}).values() if x.get("reported") and x["moves"])
        top = max(rep_.items(), key=lambda kv: kv[1]["shift_j"] if kv[1]["shift_j"] is not None else -9, default=None)
        rows.append(" & ".join([v, CHANGE[v], str(execs.get(v, 0)), f"{res['invalid_rate'].get(v, 0):.3f}",
                                f"{len(mov)} / {len(rep_)} ({mov_d})",
                                (_cls(top[0]) + " " + _d(top[1])) if top else "--"]) + r" \\")
    pu = {v: f for v, f in res["families"].items() if v.startswith("Pu_")}
    pu_d = {v: f for v, f in dec.get("families", {}).items() if v.startswith("Pu_")}
    pu_mov = sum(1 for f in pu.values() for x in f.values() if x.get("reported") and x["moves"])
    pu_rep = sum(1 for f in pu.values() for x in f.values() if x.get("reported"))
    pu_mov_d = sum(1 for f in pu_d.values() for x in f.values() if x.get("reported") and x["moves"])
    pu_break = sum(1 for v in res["contract_breaking"] if v.startswith("Pu_"))
    rows.append(" & ".join(["Pu", f"one unit paraphrased: {len(pu)} of 28 declared variants read"
                            + (f", {pu_break} contract-breaking" if pu_break else "")
                            + " (the others' text was rejected twice)", "1 each", "--",
                            f"{pu_mov} / {pu_rep} ({pu_mov_d})", "Table~\\ref{tab:battery_units}"]) + r" \\")
    header = "Variant & Change & Exec. & Invalid & Moving / reported & Largest shift [interval]"
    cap = ("Label-free perturbation battery (specification §4i), 443 frame-completion paragraphs. For each variant: the "
           "classes (17 elements, 7 technologies, \\texttt{general} beside another technology) whose shift against the "
           "baseline has a 95\\% interval entirely above zero, out of those reported (a union of at least ten "
           "paragraphs in every pair against baseline executions 1--3). The shift is the instability between variant and "
           "baseline beyond the two's own repetition instability; instability is 1 minus the Jaccard index of the "
           "paragraphs on which the class is present. Computed within each inference route and averaged "
           "(route-stratified); in parentheses the number moving under the declared computation over every pair. "
           "Largest shift: the reported class with the largest shift. Baseline invalid rate "
           f"{res['invalid_rate'].get('R', 0):.3f}" + " (executions 1--3); a variant more than five points above it is "
           "contract-breaking and not read. Article resampling, " + boot + ".")
    colspec = "@{}" + L % 11 + L % 40 + L % 10 + L % 10 + L % 18 + L % 50 + "@{}"
    out["tab_battery_families.tex"] = head + fg.table_env(cap, "tab:battery_families", colspec, header, rows, [],
                                                          size=r"\scriptsize", short="Perturbation battery by variant")
    # 2 planted and documented gaps (declared measure; route-stratified and as declared)
    pr, prd = res["primary"], dec.get("primary", {})
    rows = [r"\multicolumn{3}{@{}l}{\emph{PR1, planted gaps: shift with the element's own rules removed minus with the other half's removed}} \\"]
    for e, v in (pr.get("PR1", {}).get("per_element") or {}).items():
        gate_ = min((res["families"].get(x) or {}).get(e, {}).get("n_union_min", 0) for x in ("E1", "E2"))
        mark = r"\textsuperscript{\dag}" if gate_ < MIN_UNION else ""
        rows.append(" & ".join([_cls(e) + mark, _d(v, "contrast", "lo", "hi"),
                                _d((prd.get("PR1", {}).get("per_element") or {}).get(e), "contrast", "lo", "hi")]) + r" \\")
    rows.append(" & ".join([r"Mean over the eight", _d(pr.get("PR1"), "mean_contrast", "lo", "hi"),
                            _d(prd.get("PR1"), "mean_contrast", "lo", "hi")]) + r" \\")
    rows.append(r"\addlinespace")
    rows.append(r"\multicolumn{3}{@{}l}{\emph{PR2, documented gap: \texttt{general} under K1 minus the mean of hydrogen and nuclear under K1c}} \\")
    p2, p2d = pr.get("PR2", {}), prd.get("PR2", {})
    rows.append(" & ".join(["\\texttt{general}, K1", _d({"x": (p2.get("general_K1") or {}).get("shift_j")}, "x"),
                            _d({"x": (p2d.get("general_K1") or {}).get("shift_j")}, "x")]) + r" \\")
    for t in ("tech:hydrogen", "tech:nuclear"):
        rows.append(" & ".join([_cls(t) + ", K1c", _d({"x": ((p2.get("controls_K1c") or {}).get(t) or {}).get("shift_j")}, "x"),
                                _d({"x": ((p2d.get("controls_K1c") or {}).get(t) or {}).get("shift_j")}, "x")]) + r" \\")
    rows.append(" & ".join([r"Contrast", _d(p2, "contrast", "lo", "hi"), _d(p2d, "contrast", "lo", "hi")]) + r" \\")
    header = "Quantity & Route-stratified [interval] & As declared [interval]"
    def verdict(p_):
        return "both intervals lie above zero" if p_.get("gap_detector_statement") else "not shown at this $n$"
    cap = ("Primary readings of the battery: the rules were fixed before any call (specification §4i), the route "
           "stratification on 30 October 2026 after the switch of inference route and before any variant result was read, "
           "though after a smoke test of the analysis code on partial data had printed interim contrasts. "
           "Shifts on the declared instability (1 minus Jaccard). The statement ``the battery finds gaps whose position is "
           "known'' requires both intervals above zero: route-stratified " + verdict(pr) + ", as declared " + verdict(prd)
           + ". Values without an interval are the components of the contrast; \\dag\\ marks an element with a union below "
           "ten paragraphs in some pair against baseline executions 1--3. Article resampling, " + boot + ".")
    colspec = "@{}" + L % 62 + L % 44 + L % 44 + "@{}"
    out["tab_battery_primary.tex"] = head + fg.table_env(cap, "tab:battery_primary", colspec, header, rows, [],
                                                         size=r"\scriptsize", short="Battery: planted and documented gaps")
    # 3 paraphrase reading for every unit
    hs = res["validation"].get("human_side") or {}
    rd = dec.get("paraphrase_reading", {})
    rows = []
    order = [f"tech:{t}" for t in ("general", "solar", "wind", "hydrogen", "nuclear", "other_renewable", "storage")]
    order += sorted({k for k in res["paraphrase_reading"] if k[:1] == "F"})
    for c in order:
        unit = c[5:] if c.startswith("tech:") else c
        x = res["paraphrase_reading"].get(unit)
        if not x:
            continue
        cells = [_arrow(s_) for s_ in x["sets"]] + [""] * (4 - len(x["sets"]))
        a_ = hs.get("alpha", {}).get(unit)
        kept = (not x["important"]) and any(v_ == 0 for v_ in x["own_texts_paraphrased"].values())
        rows.append(" & ".join([_cls(c) + (r"\textsuperscript{*}" if x["important"] else "") + (r"\textsuperscript{\S}" if kept else ""),
                                f"{x['available']} / {x['declared']}"]
                               + cells + [x["reading"], (rd.get(unit) or {}).get("reading", "--"),
                                          "--" if a_ is None else f"{a_:.2f}"]) + r" \\")
    header = r"Unit & Sets & \multicolumn{4}{@{}l}{Firing rate per set} & Reading & As declared & Coder $\alpha$"
    cap = ("Paraphrase reading per unit (specification §4i): for the seven units marked * the four one-unit paraphrase "
           "sets (Kimi 1, Qwen 1, Kimi 2, Qwen 2), for every other unit the two all-texts sets Pa1 and Pa2. Per set the "
           "direction of the unit's firing-rate change against the baseline when its 95\\% interval excludes zero "
           "($\\uparrow$, $\\downarrow$), 0 when it includes zero, n/a when the set is unavailable (its paraphrase was "
           "rejected twice by the blinded judges, the variant was contract-breaking, or the union is below ten). "
           "Underdetermined by its wording: every available set moves the rate in one direction; brittle: in opposite "
           "directions; stable: neither; read on the available sets (Sets: available / declared). Route-stratified, with "
           "the reading under the declared computation beside. Coder $\\alpha$: the coder's first-versus-re-code "
           "agreement (Table~\\ref{tab:reliability}); -- where fewer than five positives made it unreportable. "
           "\\S: Pa1 or Pa2 kept this unit's original text (its paraphrase was rejected twice), so that set moves it only "
           "through the other texts. Article resampling, " + boot + ".")
    colspec = ("@{}" + L % 26 + L % 10 + L % 8 + L % 8 + L % 8 + L % 8 + L % 28 + L % 28 + L % 10 + "@{}")
    out["tab_battery_units.tex"] = head + fg.table_env(cap, "tab:battery_units", colspec, header, rows, [],
                                                       size=r"\scriptsize", short="Battery: paraphrase reading per unit")
    return out


def write_tables(res: dict, check: bool = False) -> None:
    files = {ROOT / "paper" / "tables" / n: t for n, t in tex_tables(res).items()}
    if check:
        stale = [p_ for p_, t in files.items() if not p_.exists() or p_.read_text(encoding="utf-8") != t]
        if stale:
            sys.exit("STALE: " + ", ".join(str(p_.relative_to(ROOT)) for p_ in stale))
        print(f"  {len(files)} table(s) match")
        return
    for p_, t in files.items():
        p_.write_text(t, encoding="utf-8")
        print(f"  wrote {p_.relative_to(ROOT)}")


def main() -> None:
    if "--tables-only" in sys.argv or "--check" in sys.argv:
        write_tables(json.loads((bt.OUT / "analysis.json").read_text(encoding="utf-8")), check="--check" in sys.argv)
        return
    doc, subtypes, cb_sha, art_pinned = bt.base()
    latest = latest_terminals()
    runs = load_terminals(latest)
    route = {k_: r.get("route", "eu") for k_, r in latest.items()}      # records before 15:11 UTC carry no route: EU
    fingerprint = terminal_fingerprint(latest)
    cls_all = classes(doc)
    res = {"declared": bt.AUTHORITY, "B": B, "seed": SEED, "min_union": MIN_UNION, "terminals": fingerprint,
           "measure": "shift_j: 1 - Jaccard over paragraphs (declared); shift: share of valid paragraphs (beside)",
           "runs": {}, "routes": {}, "families": {},
           "primary": {}, "paraphrase_reading": {}, "position_effect": {}, "k2": {}, "swaps": {}, "validation": {}}
    res["runs"] = {f"{s}/{v}/{k}": {"paragraphs": len(m), "valid": sum(x[0] for x in m.values())}
                   for (s, v, k), m in sorted(runs.items())}
    # route check (author's authorisation of the global route, 2026-10-30): terminals per route and variant, and the
    # baseline repetition disagreement (element or technology set differs) by the route pair of the two executions
    res["routes"]["terminals"] = {f"{s}/{v}/{k}": dict(Counter(route[(s, v, k, pid)] for pid in m))
                                  for (s, v, k), m in sorted(runs.items())}
    man = json.loads(bt.FC_MANIFEST.read_text(encoding="utf-8"))
    art = {p["paragraph_id"]: p["article_id"] for p in man["paragraphs"]}
    global ARTS, ART_INDEX
    ARTS = sorted(set(art.values())); ART_INDEX = {a: i for i, a in enumerate(ARTS)}
    rk = sorted(k for k in runs if k[0] == "FC" and k[1] == "R")
    by_pair = defaultdict(lambda: defaultdict(lambda: [0, 0]))          # route pair -> article -> [differ, pairs]
    for n_, ki in enumerate(rk):
        for kj in rk[n_ + 1:]:
            for pid in runs[ki].keys() & runs[kj].keys():
                x, y = runs[ki][pid], runs[kj][pid]
                if x[0] and y[0]:
                    g = "-".join(sorted((route[(*ki, pid)], route[(*kj, pid)])))
                    c_ = by_pair[g][art[pid]]
                    c_[0] += (x[1], x[2]) != (y[1], y[2]); c_[1] += 1
    def share(g, mult):
        d = sum(m * by_pair[g][a][0] for a, m in mult.items() if a in by_pair[g])
        n = sum(m * by_pair[g][a][1] for a, m in mult.items() if a in by_pair[g])
        return d / n if n else None
    arts_fc = sorted(set(art.values()))
    one = {a: 1 for a in arts_fc}
    res["routes"]["baseline_repetition_disagreement"] = {
        g: {"pairs": sum(v[1] for v in by_pair[g].values()), "share": share(g, one)} for g in sorted(by_pair)}
    res["routes"]["invalid_rate_R"] = {
        rho: (lambda xs: 1 - sum(xs) / len(xs) if xs else None)(
            [runs[k][pid][0] for k in rk for pid in runs[k] if route[(*k, pid)] == rho]) for rho in ("eu", "global")}
    if "eu-eu" in by_pair and "eu-global" in by_pair:
        def diff_(m):
            a_, b_ = share("eu-global", m), share("eu-eu", m)
            return None if a_ is None or b_ is None else a_ - b_
        pt, lo, hi = interval(diff_, arts_fc, 9400)
        res["routes"]["eu_global_minus_eu_eu"] = {"diff": pt, "lo": lo, "hi": hi}
    if "eu-eu" in by_pair and "global-global" in by_pair:
        def diff_g(m):
            a_, b_ = share("global-global", m), share("eu-eu", m)
            return None if a_ is None or b_ is None else a_ - b_
        pt, lo, hi = interval(diff_g, arts_fc, 9401)
        res["routes"]["global_global_minus_eu_eu"] = {"diff": pt, "lo": lo, "hi": hi}
    lang = {p["paragraph_id"]: p.get("lang") for p in man["paragraphs"]}
    arts = sorted(set(art.values()))
    variants = sorted({v for (s, v, k) in runs if s == "FC" and v != "R"})
    # invalid rates per variant (contract-breaking rule: > 5 points above the baseline)
    def inv_rate(v, ks=None):
        keys = [(s, vv, k) for (s, vv, k) in runs if s == "FC" and vv == v and (ks is None or k in ks)]
        return 1 - sum(sum(x[0] for x in runs[k_].values()) for k_ in keys) / sum(len(runs[k_]) for k_ in keys)
    inv = {v: inv_rate(v) for v in variants}
    inv["R"] = inv_rate("R", (1, 2, 3))                 # the declared baseline; executions 4-5 by route below
    inv["R_exec4_5_global"] = inv_rate("R", (4, 5))
    res["invalid_rate"] = inv
    breaking = [v for v in variants if inv[v] - inv["R"] > 0.05]
    res["contract_breaking"] = breaking
    # every shift twice: route-stratified against baseline executions 1-5 -- the reading, fixed on 2026-10-30 after
    # the switch to the global route and before any variant result was read -- and over every pair against baseline
    # executions 1-3, as declared (reported beside)
    for mode, kw in (("route_stratified", {"route": route}), ("as_declared", {"r_execs": (1, 2, 3)})):
        print(f"  -- {mode}", flush=True)
        out_m = readings(runs, doc, subtypes, cb_sha, art_pinned, art, arts, variants, breaking, cls_all, **kw)
        if mode == "route_stratified":
            res["validation"].update(out_m.pop("correlations"))
            res.update(out_m)
        else:
            res["as_declared"] = out_m
    hs_ = human_side()
    n_el_alpha = sum(1 for k_ in hs_["alpha"] if k_[:1] == "F")
    if len(hs_["f1"]) != 15 or n_el_alpha != 8:
        raise SystemExit(f"human side incomplete: {len(hs_['f1'])} element F1 (15 expected), {n_el_alpha} element alphas (8 expected)")
    res["validation"]["human_side"] = hs_
    # per language beside pooled (declared): the route-stratified readings on each language's paragraphs, resampling
    # that language's articles
    res["by_language"] = {}
    for L_ in sorted({x for x in lang.values() if x}):
        print(f"  -- language {L_}", flush=True)
        runs_L = {k_: ({pid: c_ for pid, c_ in m_.items() if lang.get(pid) == L_} if k_[0] == "FC" else m_)
                  for k_, m_ in runs.items()}
        arts_L = sorted({art[p_] for p_ in art if lang.get(p_) == L_})
        o_ = readings(runs_L, doc, subtypes, cb_sha, art_pinned, art, arts_L, variants, breaking, cls_all, route=route)
        res["by_language"][L_] = {
            "paragraphs": sum(1 for x in lang.values() if x == L_), "articles": len(arts_L),
            "moving": {v_: sorted(c_ for c_, x in f_.items() if x.get("reported") and x["moves"]) for v_, f_ in o_["families"].items()},
            "reported": {v_: sum(1 for x in f_.values() if x.get("reported")) for v_, f_ in o_["families"].items()},
            "primary": {k_: {kk: o_["primary"][k_].get(kk) for kk in ("mean_contrast", "contrast", "lo", "hi", "passes") if kk in o_["primary"][k_]}
                        for k_ in ("PR1", "PR2") if k_ in o_["primary"]},
            "paraphrase_reading": {u_: x["reading"] for u_, x in o_["paraphrase_reading"].items()}}
    # the baseline cells of a paragraph on a route: valid R executions on that route, lowest execution first
    rk_all = sorted(k for k in runs if k[0] == "FC" and k[1] == "R")
    def matched_base(pid, rho):
        return [runs[k][pid] for k in rk_all if pid in runs[k] and runs[k][pid][0] and route[(*k, pid)] == rho]
    # K2: general rows beside another technology (in the route-matched baseline) that disappear under the residual reading
    if ("FC", "K2", 1) in runs:
        beside = gone = 0
        for pid, cell in runs[("FC", "K2", 1)].items():
            b = matched_base(pid, route[("FC", "K2", 1, pid)]) if cell[0] else []
            if b and present(b[0], "general_beside"):
                beside += 1; gone += "general" not in cell[2]
        res["k2"] = {"general_beside_in_baseline": beside, "general_removed_under_K2": gone,
                     "share": gone / beside if beside else None}
    # swap matrix: within a relation, element e off and e' on, variant execution 1 against the route-matched baseline
    swaps, swap_rel = Counter(), defaultdict(set)
    for (s, v, k), m in runs.items():
        if s != "FC" or v == "R" or k != 1:
            continue
        for pid, cell in m.items():
            b = matched_base(pid, route[(s, v, k, pid)]) if cell[0] else []
            if not b:
                continue
            for tech in cell[2] & b[0][2]:
                a = {e for t, e in b[0][3] if t == tech}; c_ = {e for t, e in cell[3] if t == tech}
                for e_off in a - c_:
                    for e_on in c_ - a:
                        swaps[f"{e_off}->{e_on}"] += 1
                        swap_rel[f"{e_off}->{e_on}"].add((pid, tech))
    # occurrences count every variant execution in which the swap happens; distinct relations count (paragraph,
    # technology) pairs, so a relation that swaps under many variants counts once there
    both = lambda k_: swap_rel.get(k_, set()) | swap_rel.get("->".join(reversed(k_.split("->"))), set())
    res["swaps"] = {"unit": "occurrences across variant executions (execution 1 of each variant against the route-matched baseline)",
                    "top": swaps.most_common(25),
                    "top_distinct_relations": {k_: len(swap_rel[k_]) for k_, _ in swaps.most_common(25)},
                    "named_boundaries": {k_: swaps.get(k_, 0) + swaps.get("->".join(reversed(k_.split("->"))), 0)
                                         for k_ in ("F5a->F6b", "F5a->F6a", "F1a->F6b", "F5b->F8a", "F2b->F2c")},
                    "named_boundaries_distinct_relations": {k_: len(both(k_))
                                                            for k_ in ("F5a->F6b", "F5a->F6a", "F1a->F6b", "F5b->F8a", "F2b->F2c")}}
    # A2 unit-level validation: instability score per (paragraph, element) on the A2 subset
    if ("A2", "R", 1) in runs and ("A2", "R", 2) in runs:
        a2man = json.loads(bt.A2_MANIFEST.read_text(encoding="utf-8"))
        art2 = {p["paragraph_id"]: p["article_id"] for p in a2man["paragraphs"]}
        R1, R2 = runs[("A2", "R", 1)], runs[("A2", "R", 2)]
        okeys = [k for k in (("A2", "Pa1", 1), ("A2", "Pa2", 1), ("A2", "O1", 1)) if k in runs]
        others = [runs[k] for k in okeys]
        # only comparisons on the route of R1 (all EU on A2): a cross-route difference is not instability
        same = [{pid for pid in runs[k] if route[(*k, pid)] == route[("A2", "R", 1, pid)]} for k in okeys]
        res["validation"]["a2_cross_route_comparisons_excluded"] = sum(len(runs[k]) - len(sm) for k, sm in zip(okeys, same))
        ref = reference_presence(LABELS / "A2_pass2_v1.0.csv")
        rec = reference_presence(LABELS / "A2R_pass2_v1.0.csv")
        rec_ids = set()
        with (LABELS / "A2R_pass2_v1.0.csv").open(encoding="utf-8-sig") as f:
            rec_ids = {r["paragraph_id"] for r in csv.DictReader(f, delimiter=";")}
        units = []
        for pid in R1:
            if not R1[pid][0]:
                continue
            for e in [x["id"] for x in doc["frame_elements"]]:
                score = sum(1 for o, sm in zip(others, same) if pid in o and pid in sm and o[pid][0]
                            and (e in o[pid][1]) != (e in R1[pid][1]))
                units.append({"pid": pid, "art": art2.get(pid), "el": e, "score": score,
                              "rep": (pid in R2 and R2[pid][0] and (e in R2[pid][1]) != (e in R1[pid][1])),
                              "model_ref": (e in R1[pid][1]) != (e in ref.get(pid, set())),
                              "human": ((e in ref.get(pid, set())) != (e in rec.get(pid, set()))) if pid in rec_ids else None})
        def auc_for(key, mult=None):
            sel = [u for u in units if u[key] is not None]
            if mult is not None:
                sel = [u for u in sel for _ in range(mult.get(u["art"], 0))]
            return auc([u["score"] for u in sel], [u[key] for u in sel])
        arts2 = sorted({u["art"] for u in units})
        for key in ("human", "model_ref"):
            pt, lo, hi = interval(lambda m, key=key: auc_for(key, m), arts2, 9300 + (key == "human"))
            res["validation"][f"auc_{key}"] = {"auc": pt, "lo": lo, "hi": hi,
                                               "positives": sum(1 for u in units if u[key]),
                                               "units": sum(1 for u in units if u[key] is not None)}
        res["validation"]["a2_repetition_flip_share"] = sum(u["rep"] for u in units) / len(units) if units else None
    # flips for the qualitative reading (where and how to sharpen): per unit and variant execution, the paragraphs whose
    # presence of the unit differs from the baseline on the same route while every valid baseline execution on that
    # route -- at least two -- agrees on the paragraph's elements and technologies (so the flip is neither repetition
    # noise nor a route difference; on the global route this asks up to five executions to agree, a stricter filter)
    flips = {}
    stable_n = Counter()
    def stable_base(pid, rho):
        b = matched_base(pid, rho)
        return b[0] if len(b) >= 2 and len({(tuple(sorted(x[1])), tuple(sorted(x[2]))) for x in b}) == 1 else None
    for rho in ("eu", "global"):
        stable_n[rho] = sum(1 for pid in runs[("FC", "R", 1)] if stable_base(pid, rho) is not None)
    for (s_, v, k), m in sorted(runs.items()):
        if s_ != "FC" or v == "R":
            continue
        for pid, cell in m.items():
            b = stable_base(pid, route[(s_, v, k, pid)]) if cell[0] else None
            if b is None:
                continue
            for c in cls_all:
                a_, b_ = present(b, c), present(cell, c)
                if a_ != b_:
                    f_ = flips.setdefault(c, {}).setdefault(f"{v}#{k}", {"on": [], "off": []})
                    f_["on" if b_ else "off"].append(pid)
    for c in flips:
        for f_ in flips[c].values():
            f_["on"].sort(); f_["off"].sort()
    res["flips_on_stable_paragraphs"] = {"stable_paragraphs_by_route": dict(stable_n), "by_class": flips}
    out = bt.OUT / "analysis.json"
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"  wrote {out.relative_to(ROOT)}")
    write_tables(res)
    print("  PR1:", {k: res['primary'].get('PR1', {}).get(k) for k in ('mean_contrast', 'lo', 'hi', 'passes')})
    print("  PR2:", {k: res['primary'].get('PR2', {}).get(k) for k in ('contrast', 'lo', 'hi', 'passes')})
    print("  paraphrase readings:", {u: v["reading"] for u, v in res["paraphrase_reading"].items()})
    print("  route check:", res["routes"]["baseline_repetition_disagreement"])
    print("  invalid rates:", {k: round(v, 3) for k, v in inv.items()}, "breaking:", breaking)


if __name__ == "__main__":
    main()
