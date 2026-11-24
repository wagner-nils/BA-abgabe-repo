#!/usr/bin/env python3
"""The analyses on existing data after the examiner review (pre-analysis specification §4l (1)-(4), declared 2026-11-06
before any of them was computed; the author's decisions D26, D33, D34, D35).

  (1) D26  modal-class baseline for the comparison target on the matched rows of the registered run;
  (2) D33  the model and the first coding against the blind re-code A2R (40 paragraphs, element tuples);
  (3) D34  the registered element false negatives by cause, and the union of executions one to three;
  (4) D35  Krippendorff's alpha per element on the full row grid of the re-code, beside the aligned-row alpha;
  §4n (1)  (declared 2026-11-10) the second re-code wave A2R2 of the other 80 paragraphs: alpha alone, pooled with the
           route-B re-code and without the 25 D39-exposed paragraphs; the model and the first coding against it;
  §4n (2)  (declared 2026-11-10) 95 % intervals over B article resamples and Krippendorff's q for every alpha of
           table 9 and of (4); the point values must reproduce the registered ones.

Descriptive, test-set reuse, nothing selected; the registered run and every registered figure stay as they are. Every
input is loaded through the evaluator's own verified path (`evaluate.load_inputs`), the re-code files are bound by the
digests the registered table 9 records, and the script refuses to write unless it reproduces the registered
comparison-target accuracy, the registered element false negatives and the registered aligned-row alpha exactly.

    .venv/bin/python3 tools/pipeline/review_analyses.py [--check]

Writes tools/runs/tables/review_analyses/results.json and the appendix tables paper/tables/tab_recode_comparison.tex,
tab_fn_causes.tex and tab_alpha_full_grid.tex; once the second re-code wave A2R2 is finalised (§4n (1)), also
tab_alpha_waves.tex and tab_recode_waves.tex.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compare_models as cm                                         # noqa: E402
import evaluate as ev                                               # noqa: E402

ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
LABELS = ROOT / "tools" / "annotation" / "labels"
PAPER = ROOT / "paper" / "tables"
OUT = RUNS / "tables" / "review_analyses"
REG = "a2_sonnet_20261013"
B, SEED, S_MIN = 2000, 20260905, 5
POPS = ("pooled", "de", "en")
CAUSES = ("failed_execution", "row_missing", "element_missing")
CAUSE_LABEL = {"failed_execution": "execution without a valid record",
               "row_missing": "valid record, technology row missing",
               "element_missing": "row present, element missing"}


class AnalysisError(SystemExit):
    pass


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_registered():
    ns = argparse.Namespace(run_record=str(RUNS / f"{REG}.json"), manifest=str(LABELS / "A2_sample_manifest.json"),
                            restrict_split=None, gold_pass1=str(LABELS / "A2_pass1_v1.0.csv"),
                            gold_pass2=str(LABELS / "A2_pass2_v1.0.csv"), dev=False, gold_manifest=None,
                            skip_provenance=False, threshold=S_MIN, reliability=None)
    try:
        inp = ev.load_inputs(ns)
    except ev.EvalError as exc:
        raise AnalysisError(f"REFUSING: the registered run does not load through the evaluator ({exc})") from None
    status = ev.analysis_status_of(inp["rec"], manifest_kind=(inp.get("manifest") or {}).get("kind"))
    if status != "registered primary":
        raise AnalysisError(f"REFUSING: {REG} has status {status!r}, not 'registered primary'")
    m = json.loads((RUNS / "tables" / REG / "metrics.json").read_text(encoding="utf-8"))
    prov = inp["provenance"]
    if ((m.get("inputs") or {}).get("run_record_sha256") != prov["run_record_sha256"]
            or (m.get("inputs") or {}).get("reference") != prov["reference"] or m.get("B") != B or m.get("seed") != SEED):
        raise AnalysisError("REFUSING: the registered metrics were not written for this run record and reference")
    ev.INV = ev.inventories()
    return inp, ev.build(inp), m


def pops_of(S: dict, pids: list[str]) -> dict:
    return {"pooled": pids, **{L: [p for p in pids if S["lang"][p] == L] for L in ("de", "en")}}


# --------------------------------------------------------------------------- (1) D26
def modal_baseline(S: dict, m: dict) -> dict:
    out = {}
    for pop, ps in pops_of(S, S["pids"]).items():
        cnt = ev.counts_execution(S, ps, 1)
        units = [u for us in cnt["A_units"]["comparison_A"].values() for u in us]   # (w, ok, gold, pred)
        den = sum(u[0] for u in units)
        acc = sum(u[0] * u[1] for u in units) / den if den else None
        want = m["point"][pop]["comparison_A"]["accuracy_weighted"]
        if acc is None or abs(acc - want) > 1e-9 or len(units) != m["point"][pop]["comparison_A"]["units"]:
            raise AnalysisError(f"REFUSING: comparison-target accuracy ({pop}) {acc} does not reproduce the registered {want}")
        wcls = Counter()
        for u in units:
            wcls[u[2]] += u[0]
        modal, wmodal = max(sorted(wcls.items()), key=lambda kv: kv[1])
        out[pop] = {"units": len(units), "model_accuracy": acc, "modal_class": modal,
                    "modal_share": wmodal / den, "modal_units": sum(1 for u in units if u[2] == modal)}
    return out


# --------------------------------------------------------------------------- (3) D34
def fn_causes(S: dict, m: dict) -> dict:
    pred1, out1 = S["pred"].get(1, {}), S["outcome"].get(1, {})
    out = {}
    for pop, ps in pops_of(S, S["pids"]).items():
        pset = set(ps)
        wsum, n = Counter(), Counter()
        for (pid, tech), g in S["gold"].items():
            if pid not in pset:
                continue
            p = pred1.get((pid, tech))
            for e in g.elements:
                if p is not None and e in p.elements:
                    continue
                cause = ("failed_execution" if out1.get(pid) not in ev.SUCCESS
                         else "row_missing" if p is None else "element_missing")
                wsum[cause] += S["w"][pid]; n[cause] += 1
        cnt = ev.counts_execution(S, ps, 1)
        reg_fn_w = sum(v[2] for v in cnt["C"]["element"].values()); reg_fn = sum(v[5] for v in cnt["C"]["element"].values())
        if abs(sum(wsum.values()) - reg_fn_w) > 1e-6 or sum(n.values()) != reg_fn:
            raise AnalysisError(f"REFUSING: the false negatives by cause ({pop}) do not sum to the registered element false "
                                f"negatives ({sum(n.values())} vs {reg_fn})")
        tp_w = sum(v[0] for v in cnt["C"]["element"].values()); fp_w = sum(v[1] for v in cnt["C"]["element"].values())
        # union of executions one to three, per row; a failed execution contributes nothing
        union = defaultdict(set)
        for r in range(1, S["k"] + 1):
            for (pid, tech), rel in S["pred"].get(r, {}).items():
                if pid in pset:
                    union[(pid, tech)] |= set(rel.elements)
        utp = ufp = ufn = 0.0
        for key in sorted({k for k in S["gold"] if k[0] in pset} | set(union)):
            ge = S["gold"][key].elements if key in S["gold"] else frozenset()
            ue = union.get(key, set())
            wt = S["w"][key[0]]
            for e in ev.ELEMENTS:
                utp += wt * (e in ge and e in ue); ufp += wt * (e not in ge and e in ue); ufn += wt * (e in ge and e not in ue)
        out[pop] = {"fn_rows": dict(n), "fn_weighted": {c: wsum[c] for c in CAUSES},
                    "fn_weighted_total": reg_fn_w, "fn_total": reg_fn,
                    "share_weighted": {c: wsum[c] / reg_fn_w if reg_fn_w else None for c in CAUSES},
                    "exec1_recall": tp_w / (tp_w + reg_fn_w), "exec1_precision": tp_w / (tp_w + fp_w),
                    "union_recall": utp / (utp + ufn), "union_precision": utp / (utp + ufp) if utp + ufp else None}
    return out


# --------------------------------------------------------------------------- (2) D33 and (4) D35
def recode_inputs(inp: dict, S: dict, m: dict):
    files = m["reliability"]["inputs"]["files"]
    paths = {k: ROOT / v["path"] for k, v in files.items()}
    for k, v in files.items():
        if sha(paths[k]) != v["sha256"]:
            raise AnalysisError(f"REFUSING: {v['path']} is not the file the registered table 9 binds")
    if (files["pass1_a"]["sha256"], files["pass2_a"]["sha256"]) != (inp["provenance"]["reference"]["A2_pass1_v1.0.csv"],
                                                                      inp["provenance"]["reference"]["A2_pass2_v1.0.csv"]):
        raise AnalysisError("REFUSING: coding A of table 9 is not the registered reference")
    p1b, p2b = ev.read_csv(paths["pass1_b"]), ev.read_csv(paths["pass2_b"])
    p2a = ev.read_csv(paths["pass2_a"])
    pids40 = sorted(p for p in S["pids"] if S["by_pid"][p].get("reliability_subsample"))
    if set(r["paragraph_id"] for r in p1b) != set(pids40) or len(pids40) != 40:
        raise AnalysisError("REFUSING: the re-code does not cover exactly the 40 flagged paragraphs")
    k1 = {(r["paragraph_id"], c[len("tech_"):]) for r in p1b for c in r if c.startswith("tech_") and str(r[c]).strip() == "1"}
    if k1 != {(r["paragraph_id"], r["technology"]) for r in p2b}:
        raise AnalysisError("REFUSING: the re-code's pass-1 ticks and pass-2 rows differ")
    frame_of = inp["frame_of"]
    ra = {(r["paragraph_id"], r["technology"]): ev.rel_from_row(r, frame_of) for r in p2a if r["paragraph_id"] in set(pids40)}
    rb = {(r["paragraph_id"], r["technology"]): ev.rel_from_row(r, frame_of) for r in p2b}
    if ra != {k: v for k, v in S["gold"].items() if k[0] in set(pids40)}:
        raise AnalysisError("REFUSING: coding A on the 40 paragraphs is not the evaluator's reference")
    return pids40, ra, rb


def _interval(fn, arts) -> tuple:
    rng = random.Random(SEED)
    draws = sorted(v for v in (fn(Counter(rng.choice(arts) for _ in arts)) for _ in range(B)) if v is not None)
    if not draws:
        return None, None
    q = lambda p: draws[int(round(p * (len(draws) - 1)))]
    return q(0.025), q(0.975)


def _interval_n(fn, arts) -> dict:
    """_interval with the same draws, plus the number of defined and undefined draws (§4n (3))."""
    rng = random.Random(SEED)
    vals = [fn(Counter(rng.choice(arts) for _ in arts)) for _ in range(B)]
    d = sorted(v for v in vals if v is not None)
    q = lambda p: d[int(round(p * (len(d) - 1)))]
    return {"iv": (q(0.025), q(0.975)) if d else (None, None), "defined": len(d), "undefined": B - len(d)}


def recode_comparison(S: dict, pids40, ra, rb) -> dict:
    pred1 = {k: v for k, v in S["pred"].get(1, {}).items() if k[0] in set(pids40)}
    setups = {"model_vs_second": (rb, pred1), "first_vs_second": (rb, ra), "model_vs_first": (ra, pred1)}
    out = {}
    for pop, ps in pops_of(S, pids40).items():
        arts = sorted({S["art"][p] for p in ps})
        per = {}
        for name, (gold, pred) in setups.items():
            T = {"gold": gold, "pred": {1: pred}, "w": S["w"], "art": S["art"]}
            per[name] = ev.counts_execution(T, ps, 1)["per_art"]
        base = {a: 1 for a in arts}
        res = {"paragraphs": len(ps), "articles": len(arts)}
        for name, pa in per.items():
            p, r = cm._pr(pa, base)
            f = ev.micro(pa, "element", base, True)
            res[name] = {"precision": p, "recall": r, "f1": f,
                         "precision_iv": _interval(lambda mu, pa=pa: cm._pr(pa, mu)[0], arts),
                         "recall_iv": _interval(lambda mu, pa=pa: cm._pr(pa, mu)[1], arts),
                         "f1_iv": _interval(lambda mu, pa=pa: ev.micro(pa, "element", mu, True), arts)}
        def rdiff(mu):
            a_, b_ = cm._pr(per["model_vs_second"], mu)[1], cm._pr(per["model_vs_first"], mu)[1]
            return None if a_ is None or b_ is None else a_ - b_
        res["recall_diff_second_minus_first"] = {"diff": rdiff(base), "iv": _interval(rdiff, arts)}
        out[pop] = res
    return out


def alpha_full_grid(m: dict, pids40, ra, rb) -> dict:
    union, aligned = sorted(set(ra) | set(rb)), sorted(set(ra) & set(rb))
    out = {"rows_union": len(union), "rows_aligned": len(aligned), "rows_only_first": len(set(ra) - set(rb)),
           "rows_only_second": len(set(rb) - set(ra)), "elements": {}}
    full_all, al_all = [], []
    for e in ev.ELEMENTS:
        full = [(int(k in ra and e in ra[k].elements), int(k in rb and e in rb[k].elements)) for k in union]
        al = [(int(e in ra[k].elements), int(e in rb[k].elements)) for k in aligned]
        a_full, raw_full, _ = ev.krippendorff_nominal(full)
        a_al, _, _ = ev.krippendorff_nominal(al)
        want = m["reliability"]["elements"][e]["alpha"]
        if (want is None) != (a_al is None) or (want is not None and abs(want - a_al) > 1e-12):
            raise AnalysisError(f"REFUSING: the aligned-row alpha of {e} ({a_al}) does not reproduce the registered {want}")
        out["elements"][e] = {"alpha_full": a_full, "raw_full": raw_full, "alpha_aligned": a_al,
                              "support_first_full": sum(x for x, _ in full), "support_second_full": sum(y for _, y in full),
                              "support_first_aligned": sum(x for x, _ in al),
                              "reported": sum(x for x, _ in full) >= S_MIN}
        full_all += full; al_all += al
    out["pooled_full"] = ev.krippendorff_nominal(full_all)[0]
    out["pooled_aligned"] = ev.krippendorff_nominal(al_all)[0]
    return out



# --------------------------------------------------------------------------- §4n (2): intervals for the coder's alpha
A_MIN = (0.667, 0.800)


def alpha_units(S: dict, pids, ra, rb) -> dict:
    """Every alpha item of table 9 and of §4l (4) as a list of (article, value A, value B) units, built exactly as
    ev.reliability() and alpha_full_grid() build their pairs."""
    pids = sorted(pids); art = S["art"]
    ta, tb = defaultdict(set), defaultdict(set)
    for (pid, t) in ra:
        ta[pid].add(t)
    for (pid, t) in rb:
        tb[pid].add(t)
    techs = list(ev.inventories()["technology"])
    techs += sorted({t for s_ in list(ta.values()) + list(tb.values()) for t in s_ if t not in techs})
    aligned, union = sorted(set(ra) & set(rb)), sorted(set(ra) | set(rb))
    U = {}
    for t in techs:
        U[("technology", t)] = [(art[p], int(t in ta[p]), int(t in tb[p])) for p in pids]
    for e in ev.ELEMENTS:
        U[("elements", e)] = [(art[k[0]], int(e in ra[k].elements), int(e in rb[k].elements)) for k in aligned]
        U[("full_grid", e)] = [(art[k[0]], int(k in ra and e in ra[k].elements), int(k in rb and e in rb[k].elements))
                               for k in union]
    for fr in ev.FRAMES:
        us = []
        for k in aligned:
            ea, eb = dict(ra[k].evals), dict(rb[k].evals)
            if fr in ea and fr in eb:
                us.append((art[k[0]], ea[fr], eb[fr]))
        U[("evaluation", fr)] = us
    U[("comparison_target", "all aligned rows")] = [(art[k[0]], ra[k].target, rb[k].target) for k in aligned]
    U[("pooled", "aligned")] = [u for e in ev.ELEMENTS for u in U[("elements", e)]]
    U[("pooled", "full_grid")] = [u for e in ev.ELEMENTS for u in U[("full_grid", e)]]
    return U


def _alpha_from(N: int, D: int, vals: Counter):
    """Nominal alpha for two ratings per unit from N units, D disagreeing units and the value counts; None without
    variation. Algebraically ev.krippendorff_nominal (D_e = (n^2 - sum n_c^2) / (n (n - 1)), n = 2N)."""
    if N == 0:
        return None
    n = 2 * N
    De = (n * n - sum(c * c for c in vals.values())) / (n * (n - 1))
    if De == 0:
        return None
    return 1 - (D / N) / De


def alpha_intervals(S: dict, pids, ra, rb, reference: dict | None = None) -> dict:
    """§4n (2): per item the point alpha, the 95 % percentile interval over B resamples of whole articles (seed SEED,
    one common set of resamples for every item) and Krippendorff's q for each alpha_min; resamples without variation
    are counted and left out. `reference` maps (level, item) to the alpha the registered tables print; every point
    alpha must reproduce it, or nothing is written."""
    U = alpha_units(S, pids, ra, rb)
    summ = {}
    for key, units in U.items():
        per = {}
        for a_, x, y in units:
            s = per.setdefault(a_, [0, 0, Counter()])
            s[0] += 1; s[1] += int(x != y); s[2][x] += 1; s[2][y] += 1
        summ[key] = per
    def at(key, mult):
        N = D = 0; vals = Counter()
        for a_, k in mult.items():
            s = summ[key].get(a_)
            if s is None:
                continue
            N += k * s[0]; D += k * s[1]
            for v, c in s[2].items():
                vals[v] += k * c
        return _alpha_from(N, D, vals)
    out = {}
    for key, units in U.items():
        point = ev.krippendorff_nominal([(x, y) for _, x, y in units])[0]
        mine = at(key, Counter({a_: 1 for a_ in summ[key]}))
        if (point is None) != (mine is None) or (point is not None and abs(point - mine) > 1e-12):
            raise AnalysisError(f"REFUSING: the aggregated alpha of {key} ({mine}) differs from krippendorff_nominal ({point})")
        if reference is not None and key in reference:
            want = reference[key]
            if (want is None) != (point is None) or (want is not None and abs(want - point) > 1e-12):
                raise AnalysisError(f"REFUSING: the alpha of {key} ({point}) does not reproduce the registered {want}")
        binary = key[0] in ("technology", "elements", "full_grid", "pooled")
        raw = ev.krippendorff_nominal([(x, y) for _, x, y in units])[1]
        out[key] = {"alpha": point, "raw": raw, "n_units": len(units),
                    # support (the evaluator's convention): positives of each coding for binary items, units otherwise
                    "support_first": sum(1 for _, x, _ in units if x == 1) if binary else len(units),
                    "support_second": sum(1 for _, _, y in units if y == 1) if binary else len(units),
                    "support_articles": len({a_ for a_, x, _ in units if (x == 1 if binary else True)}),
                    "draws": [], "undefined": 0}
    arts = sorted({S["art"][p] for p in pids})
    rng = random.Random(SEED)
    for _ in range(B):
        mult = Counter(rng.choice(arts) for _ in arts)
        for key in U:
            v = at(key, mult)
            if v is None:
                out[key]["undefined"] += 1
            else:
                out[key]["draws"].append(v)
    res = {"articles": len(arts), "paragraphs": len(pids), "B": B, "seed": SEED, "alpha_min": list(A_MIN), "items": {}}
    for key, x in out.items():
        d = sorted(x.pop("draws"))
        qf = lambda p: d[int(round(p * (len(d) - 1)))]
        x["ci"] = (qf(0.025), qf(0.975)) if d else (None, None)
        x["q"] = {f"{am:.3f}": (sum(1 for v in d if v < am) / len(d) if d else None) for am in A_MIN}
        res["items"][f"{key[0]}|{key[1]}"] = x
    return res


def registered_alpha(m: dict, ag: dict) -> dict:
    """The alpha values the registered table 9 and the §4l (4) table print, keyed as alpha_units keys them."""
    r = m["reliability"]; ref = {}
    for lvl in ("technology", "elements", "evaluation"):
        for item, d in r[lvl].items():
            ref[(lvl, item)] = d["alpha"]
    ref[("comparison_target", "all aligned rows")] = r["comparison_target"]["alpha"]
    for e, d in ag["elements"].items():
        ref[("full_grid", e)] = d["alpha_full"]
    ref[("pooled", "aligned")] = ag["pooled_aligned"]; ref[("pooled", "full_grid")] = ag["pooled_full"]
    return ref



# --------------------------------------------------------------------------- §4n (1): the second re-code wave A2R2
WAVE2 = ("A2R2_pass1_v1.0.csv", "A2R2_pass2_v1.0.csv")


def wave2_inputs(inp: dict, S: dict):
    """The finalised A2R2 labels, bound by the digests sessions.csv recorded, over exactly the 80 registered A2
    paragraphs outside the route-B subsample; None before both passes exist."""
    paths = [LABELS / f for f in WAVE2]
    if not all(x.exists() for x in paths):
        return None
    rec = {r["labels_file"]: r["labels_sha256"] for r in ev.read_csv(LABELS / "sessions.csv")}
    for x in paths:
        if rec.get(x.name) != sha(x)[:16]:
            raise AnalysisError(f"REFUSING: {x.name} is not the file sessions.csv recorded at its finalisation")
    man = json.loads((LABELS / "A2R2_manifest.json").read_text(encoding="utf-8"))
    p1b, p2b = ev.read_csv(paths[0]), ev.read_csv(paths[1])
    pids80 = sorted(p for p in S["pids"] if not S["by_pid"][p].get("reliability_subsample"))
    if len(pids80) != 80 or set(r["paragraph_id"] for r in p1b) != set(pids80) \
            or {q["paragraph_id"] for q in man["paragraphs"]} != set(pids80):
        raise AnalysisError("REFUSING: the second wave does not cover exactly the 80 paragraphs outside the route-B subsample")
    k1 = {(r["paragraph_id"], c[len("tech_"):]) for r in p1b for c in r if c.startswith("tech_") and str(r[c]).strip() == "1"}
    if k1 != {(r["paragraph_id"], r["technology"]) for r in p2b}:
        raise AnalysisError("REFUSING: the second wave's pass-1 ticks and pass-2 rows differ")
    frame_of = inp["frame_of"]
    rb = {(r["paragraph_id"], r["technology"]): ev.rel_from_row(r, frame_of) for r in p2b}
    ra = {k: v for k, v in S["gold"].items() if k[0] in set(pids80)}
    exposed = sorted(man["recode"]["d39_exposed"])
    if len(exposed) != 25 or not set(exposed) <= set(pids80):
        raise AnalysisError("REFUSING: the manifest does not list the 25 declared D39-exposed paragraphs")
    # cross-check: the evaluator's own reliability() on the wave reproduces the unit construction used here
    p1a = [r for r in ev.read_csv(LABELS / "A2_pass1_v1.0.csv") if r["paragraph_id"] in set(pids80)]
    p2a = [r for r in ev.read_csv(LABELS / "A2_pass2_v1.0.csv") if r["paragraph_id"] in set(pids80)]
    rel = ev.reliability(p1a, p2a, p1b, p2b, frame_of, designated=set(pids80))
    return {"pids": pids80, "ra": ra, "rb": rb, "exposed": exposed, "evaluator_reliability": rel,
            "files": {x.name: sha(x)[:16] for x in paths}}


def paired_extras(S: dict, ps, ra, rb) -> dict:
    """§4n (3) (a), (b): paired precision-minus-recall gaps against each coding, their change, and the F1 contrast
    model-versus-second minus first-versus-second, on common article resamples, per language block."""
    pred1 = {k: v for k, v in S["pred"].get(1, {}).items() if k[0] in set(ps)}
    setups = {"model_vs_second": (rb, pred1), "first_vs_second": (rb, ra), "model_vs_first": (ra, pred1)}
    out = {}
    for pop, qs in pops_of(S, ps).items():
        arts = sorted({S["art"][p] for p in qs})
        per = {n: ev.counts_execution({"gold": g, "pred": {1: pr}, "w": S["w"], "art": S["art"]}, qs, 1)["per_art"]
               for n, (g, pr) in setups.items()}
        base = {a: 1 for a in arts}
        def gap(name):
            def f(mu):
                p_, r_ = cm._pr(per[name], mu)
                return None if p_ is None or r_ is None else p_ - r_
            return f
        def change(mu):
            a_, b_ = gap("model_vs_second")(mu), gap("model_vs_first")(mu)
            return None if a_ is None or b_ is None else a_ - b_
        def f1c(mu):
            a_, b_ = ev.micro(per["model_vs_second"], "element", mu, True), ev.micro(per["first_vs_second"], "element", mu, True)
            return None if a_ is None or b_ is None else a_ - b_
        out[pop] = {k: {"diff": fn(base), **_interval_n(fn, arts)} for k, fn in
                    (("gap_vs_second", gap("model_vs_second")), ("gap_vs_first", gap("model_vs_first")),
                     ("gap_change", change), ("f1_model_second_minus_first_second", f1c))}
    return out


def element_direction(S: dict, pids40, pids80, ra_all, rb_all) -> dict:
    """§4n (3) (c), (d): per element, the model's design-weighted precision and recall against each reading on all
    120 paragraphs (a cell only where that reading has at least S_MIN tuples), and unweighted tuple counts."""
    ps = sorted(set(pids40) | set(pids80))
    pred1 = {k: v for k, v in S["pred"].get(1, {}).items() if k[0] in set(ps)}
    arts = {S["art"][p]: 1 for p in ps}
    res = {}
    for name, gold in (("first", ra_all), ("second", rb_all)):
        pa = ev.counts_execution({"gold": gold, "pred": {1: pred1}, "w": S["w"], "art": S["art"]}, ps, 1)["per_art"]
        for e in ev.ELEMENTS:
            tp = fp = fn = 0.0
            for a in arts:
                v = pa[a]["element"].get(e)
                if v:
                    tp += v[0]; fp += v[1]; fn += v[2]
            sup = sum(1 for k, r in gold.items() if k[0] in set(ps) and e in r.elements)
            res.setdefault(e, {})[name] = {"precision": tp / (tp + fp) if tp + fp else None,
                                           "recall": tp / (tp + fn) if tp + fn else None,
                                           "tuples": sup, "reported": sup >= S_MIN}
    for e in ev.ELEMENTS:
        cnt = lambda X, qs: sum(1 for k, r in X.items() if k[0] in set(qs) and e in r.elements)
        res[e]["model_tuples"] = sum(1 for k, r in pred1.items() if e in r.elements)
        res[e]["by_wave"] = {"wave1": {"first": cnt(ra_all, pids40), "second": cnt(rb_all, pids40), "model": cnt(pred1, pids40)},
                             "wave2": {"first": cnt(ra_all, pids80), "second": cnt(rb_all, pids80), "model": cnt(pred1, pids80)}}
        for who, X in (("first", ra_all), ("second", rb_all)):
            if res[e]["by_wave"]["wave1"][who] + res[e]["by_wave"]["wave2"][who] != res[e][who]["tuples"]:
                raise AnalysisError(f"REFUSING: the wave counts of {e} ({who}) do not sum to the total")
        if res[e]["by_wave"]["wave1"]["model"] + res[e]["by_wave"]["wave2"]["model"] != res[e]["model_tuples"]:
            raise AnalysisError(f"REFUSING: the model's wave counts of {e} do not sum to the total")
    tot = lambda X, qs: sum(len(r.elements) for k, r in X.items() if k[0] in set(qs))
    totals = {"first": tot(ra_all, ps), "second": tot(rb_all, ps), "model": sum(len(r.elements) for r in pred1.values()),
              "wave1": {"first": tot(ra_all, pids40), "second": tot(rb_all, pids40), "model": tot(pred1, pids40)},
              "wave2": {"first": tot(ra_all, pids80), "second": tot(rb_all, pids80), "model": tot(pred1, pids80)},
              "rows": {"first": sum(1 for k in ra_all if k[0] in set(ps)), "second": sum(1 for k in rb_all if k[0] in set(ps)),
                       "model": len(pred1)}}
    return {"elements": res, "totals": totals}


def wave2_analysis(S: dict, w2: dict, pids40, ra40, rb40) -> dict:
    """§4n (1): alpha with intervals on the wave alone, both waves pooled, and both without the D39-exposed
    paragraphs; the model and the first coding against the second coding on the same populations."""
    rel = w2["evaluator_reliability"]
    ref = {}
    for lvl in ("technology", "elements", "evaluation"):
        for item, d in rel[lvl].items():
            ref[(lvl, item)] = d["alpha"]
    ref[("comparison_target", "all aligned rows")] = rel["comparison_target"]["alpha"]
    ra_all = {**ra40, **w2["ra"]}; rb_all = {**rb40, **w2["rb"]}
    ex = set(w2["exposed"])
    pops = {"wave2": (w2["pids"], None), "both": (sorted(set(pids40) | set(w2["pids"])), None),
            "wave2_without_d39": ([p for p in w2["pids"] if p not in ex], None),
            "both_without_d39": (sorted(set(pids40) | {p for p in w2["pids"] if p not in ex}), None)}
    out = {"files": w2["files"], "d39_exposed": w2["exposed"], "populations": {}}
    for name, (ps, _) in pops.items():
        sp = set(ps)
        a_ = {k: v for k, v in ra_all.items() if k[0] in sp}; b_ = {k: v for k, v in rb_all.items() if k[0] in sp}
        ai = alpha_intervals(S, ps, a_, b_, reference=ref if name == "wave2" else None)
        al, un = set(a_) & set(b_), set(a_) | set(b_)
        out["populations"][name] = {"paragraphs": len(ps), "rows_aligned": len(al), "rows_union": len(un),
                                    "rows_only_first": len(set(a_) - set(b_)), "rows_only_second": len(set(b_) - set(a_)),
                                    "alpha": ai, "comparison": recode_comparison(S, ps, a_, b_),
                                    "paired": paired_extras(S, ps, a_, b_)}
    out["element_direction"] = element_direction(S, pids40, w2["pids"], ra_all, rb_all)
    # the per-element cells, summed, must reproduce the pooled comparison of the same population
    for who, k in (("first", "model_vs_first"), ("second", "model_vs_second")):
        ps = sorted(set(pids40) | set(w2["pids"])); gold = ra_all if who == "first" else rb_all
        pred1 = {kk: v for kk, v in S["pred"].get(1, {}).items() if kk[0] in set(ps)}
        pa = ev.counts_execution({"gold": gold, "pred": {1: pred1}, "w": S["w"], "art": S["art"]}, ps, 1)["per_art"]
        p_, r_ = cm._pr(pa, {S["art"][q]: 1 for q in ps})
        c = out["populations"]["both"]["comparison"]["pooled"][k]
        if abs(p_ - c["precision"]) > 1e-12 or abs(r_ - c["recall"]) > 1e-12:
            raise AnalysisError(f"REFUSING: the element scoring does not reproduce the pooled {k}")
    out["paired_wave1"] = paired_extras(S, sorted(pids40), ra40, rb40)       # §4n (3), clarification of round 2
    return out


# --------------------------------------------------------------------------- tables
def f3(x):
    return "--" if x is None else f"{x:.3f}"


def sg(x):
    return "--" if x is None else (f"$+{x:.3f}$" if x >= 0 else f"$-{abs(x):.3f}$")


def iv(t):
    return "" if not t or t[0] is None else f" [{f3(t[0])}, {f3(t[1])}]"


HEAD = "% Generated by tools/pipeline/review_analyses.py from tools/runs/tables/review_analyses/results.json (specification §4l). Do not edit by hand.\n"


def table_recode(res: dict) -> str:
    rows = []
    lab = {"model_vs_second": "Model against the second coding", "first_vs_second": "First coding against the second coding",
           "model_vs_first": "Model against the first coding (registered reference)"}
    for pop, name in (("pooled", "Both languages"), ("de", "German"), ("en", "English")):
        r = res[pop]
        rows.append(f"\\multicolumn{{4}}{{@{{}}l}}{{\\emph{{{name} ({r['paragraphs']} paragraphs, {r['articles']} articles)}}}} \\\\")
        for k in ("model_vs_second", "first_vs_second", "model_vs_first"):
            x = r[k]
            rows.append(f"{lab[k]} & {f3(x['precision'])}{iv(x['precision_iv'])} & {f3(x['recall'])}{iv(x['recall_iv'])} & "
                        f"{f3(x['f1'])}{iv(x['f1_iv'])} \\\\")
        d = r["recall_diff_second_minus_first"]
        rows.append(f"Model recall, second minus first coding & -- & {sg(d['diff'])}{iv(d['iv'])} & -- \\\\")
        rows.append("\\addlinespace")
    body = "\n".join(rows[:-1])
    return HEAD + "% OUTLINE: The registered re-code supports a descriptive comparison against the second reading after a minimum five-day interval.\n" + r"""\begin{table}[htbp]
\centering
\footnotesize
\setlength{\tabcolsep}{4pt}
\begin{threeparttable}
\caption[Agreement against the blind re-code]{Later element-agreement comparisons with the registered blind re-code of 40 paragraphs (specification §4l (2)). The second coding is the reference in the first two rows of each block. Design-weighted; 95\% intervals over article resamples ($B = 2{,}000$, seed 20260905). One coder's consistency after a minimum five-day interval, not an intercoder ceiling.}
\label{tab:recode_comparison}
\begin{tabular}{@{}>{\raggedright\arraybackslash}p{52mm}>{\raggedright\arraybackslash}p{30mm}>{\raggedright\arraybackslash}p{30mm}>{\raggedright\arraybackslash}p{30mm}@{}}
\toprule
\textbf{Comparison} & \textbf{Precision} & \textbf{Recall} & \textbf{Micro-F1} \\
\midrule
""" + body + r"""
\bottomrule
\end{tabular}
\end{threeparttable}
\end{table}
"""


def table_fn(res: dict) -> str:
    rows = []
    for c in CAUSES:
        cells = []
        for pop in POPS:
            x = res[pop]
            cells.append(f"{x['fn_rows'].get(c, 0)} ({100 * x['share_weighted'][c]:.0f}\\%)")
        rows.append(f"{CAUSE_LABEL[c].capitalize()} & " + " & ".join(cells) + " \\\\")
    rows.append("All missed elements & " + " & ".join(f"{res[p]['fn_total']} (100\\%)" for p in POPS) + " \\\\")
    rows.append("\\midrule")
    rows.append("Element recall, execution 1 & " + " & ".join(f3(res[p]["exec1_recall"]) for p in POPS) + " \\\\")
    rows.append("Element recall, union of executions 1--3 & " + " & ".join(f3(res[p]["union_recall"]) for p in POPS) + " \\\\")
    rows.append("Element precision, execution 1 & " + " & ".join(f3(res[p]["exec1_precision"]) for p in POPS) + " \\\\")
    rows.append("Element precision, union of executions 1--3 & " + " & ".join(f3(res[p]["union_precision"]) for p in POPS) + " \\\\")
    # 2026-11-12, M22 sweep (repair 5 of the fourth review): the caption names the first coding
    return HEAD + r"""\begin{table}[htbp]
\centering
\footnotesize
\setlength{\tabcolsep}{4pt}
\begin{threeparttable}
\caption[Missed elements by cause]{Missed elements of the registered run by cause (specification §4l (3)). Counts of first-coding elements the model's execution 1 does not mark, with their design-weighted shares. Below, recall and precision when an element counts as marked if any of the three executions marks it.}
\label{tab:fn_causes}
\begin{tabular}{@{}>{\raggedright\arraybackslash}p{62mm}>{\raggedright\arraybackslash}p{27mm}>{\raggedright\arraybackslash}p{27mm}>{\raggedright\arraybackslash}p{27mm}@{}}
\toprule
\textbf{Cause} & \textbf{Both languages} & \textbf{German} & \textbf{English} \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}
\end{threeparttable}
\end{table}
"""


def th(n: int) -> str:
    """An integer with the LaTeX thousands separator {,} from 1,000 on."""
    return f"{n:,}".replace(",", "{,}")


def q4(x):
    return "--" if x is None else f"{x:.4f}"


def alpha_panel_rows(ai: dict, techs: list, evals: list) -> list[str]:
    tag = f"{ai['paragraphs']} & "                # C2 (H-05): the population, by paragraph count, on every row
    """One population's alpha rows (§4l (4), §4n): technology ticks, elements on shared rows and on the full grid,
    pooled cells, evaluations, the comparison target. Units, both codings' supports, articles with a first-coding
    positive (all units for categorical items), raw agreement, alpha with its interval, q at .667 and .800 over the
    defined draws and the undefined draws. An item below S_MIN first-coding support prints its supports only."""
    it = ai["items"]; rows = []
    def line(label, key, mask=True):
        x = it[key]
        sup = f"{th(x['support_first'])}/{th(x['support_second'])}" if key.split("|")[0] in ("technology", "elements", "full_grid", "pooled") \
            else f"{th(x['n_units'])}"
        lead = tag + f"{label} & {th(x['n_units'])} & {sup} & {x['support_articles']} & {f3(x['raw'])}"
        if mask and x["support_first"] < S_MIN:
            rows.append(lead + f" & $n<{S_MIN}$ & -- & -- & {th(x['undefined'])} \\\\")
        elif x["alpha"] is None:
            rows.append(lead + f" & undefined & -- & -- & {th(x['undefined'])} \\\\")
        else:
            rows.append(lead + f" & {f3(x['alpha'])}{iv(x['ci'])} & {q4(x['q']['0.667'])} & {q4(x['q']['0.800'])} & {th(x['undefined'])} \\\\")
    def head(title):
        rows.append(f"\\multicolumn{{10}}{{@{{}}l}}{{\\textit{{{title}}}}} \\\\")
    head("Technology ticks (paragraphs)")
    for k in techs:
        line(ev_tex(k), f"technology|{k}")
    head("Elements, rows both codings created")
    for e in ev.ELEMENTS:
        line(e, f"elements|{e}")
    line("pooled cells", "pooled|aligned", mask=False)
    head("Elements, full grid of rows either coding created")
    for e in ev.ELEMENTS:
        line(e, f"full_grid|{e}")
    line("pooled cells", "pooled|full_grid", mask=False)
    head("Evaluations, rows on which both codings fired the frame")
    for k in evals:
        line(ev_tex(k), f"evaluation|{k}")
    head("Comparison target, rows both codings created")
    line("all rows", "comparison_target|all aligned rows")
    return rows


ALPHA_HEAD = (r"\textbf{Set} & \textbf{Item} & \textbf{Units} & \textbf{1st/2nd} & \textbf{Art.} & \textbf{Raw} & "
              r"\textbf{$\alpha$ [95\,\% interval]} & \textbf{$q_{.667}$} & \textbf{$q_{.800}$} & \textbf{Undef.} \\")
ALPHA_NOTE = (r"Set: the population, by its number of paragraphs. Units are paragraphs for technology ticks, technology rows for individual elements and comparison targets, "
              r"and shared frame-bearing rows for evaluations. Pooled units are technology-row $\times$ element cells. "
              r"1st/2nd gives positive counts for binary items and applicable-unit counts for categorical items. "
              r"Art. counts articles with a first-coding positive for binary items, and articles with an applicable unit otherwise. "
              r"Raw is the share of units on which both codings agree; it and $\alpha$ are unweighted. "
              r"Intervals use defined draws from 2{,}000 whole-article resamples (seed 20260905). "
              r"$q$ is the fraction of defined draws below the stated benchmark. "
              r"Undef. counts draws with no applicable units or no variation across ratings. "
              r"An item below five first-coding support units has its $\alpha$ suppressed. "
              r"Pooled cells summarise heterogeneous elements and certify none of them.")


def alpha_longtable(caption_short: str, caption: str, label: str, panels: list[tuple[str, dict]], techs, evals) -> str:
    body = []
    for title, ai in panels:
        if title:
            body.append(f"\\multicolumn{{10}}{{@{{}}p{{150mm}}@{{}}}}{{\\textbf{{{title}}}}} \\\\")
        body += alpha_panel_rows(ai, techs, evals)
        body.append("\\addlinespace")
    return HEAD.replace("(specification §4l)", "(specification §4l (4), §4n)") + "% OUTLINE: " + caption_short + \
        ", per population: supports, raw agreement, alpha with article-resampling intervals, q and undefined draws.\n" + r"""\begingroup\scriptsize
\setlength{\tabcolsep}{3pt}
\setlength{\LTpost}{3pt}
\begin{longtable}{@{}r>{\raggedright\arraybackslash}p{22mm}rrrr>{\raggedright\arraybackslash}p{31mm}rrr@{}}
\caption[""" + caption_short + "]{" + caption + r"""}\label{""" + label + r"""} \\
\toprule
""" + ALPHA_HEAD + r"""
\midrule
\endfirsthead
\toprule
""" + ALPHA_HEAD + r"""
\midrule
\endhead
\bottomrule
\endfoot
\bottomrule
\endlastfoot
""" + "\n".join(body[:-1]) + r"""
\end{longtable}
% OUTLINE: The note defines units, supports, weighting, the resampling and the suppression rule.
\par\noindent{\footnotesize """ + ALPHA_NOTE + r"""\par}
\par\bigskip
\endgroup
"""


def table_alpha(res: dict, ai: dict, m: dict) -> str:
    """Table 37: the registered 40-paragraph re-code with intervals (§4l (4), §4n (2))."""
    r = m["reliability"]
    cap = (r"The coder's $\alpha$ in the registered blind re-code of 40 paragraphs from " + f"{ai['articles']}" +
           r" articles. Article-resampling intervals were added later (specification §4n (2)). " +
           f"Both codings created {res['rows_aligned']} shared rows; their union contains {res['rows_union']} rows, "
           f"{res['rows_only_first']} exclusive to the first coding and {res['rows_only_second']} to the second. "
           r"Shared-row $\alpha$ values reproduce Table~\ref{tab:reliability}.")
    return alpha_longtable("The coder's alpha with intervals", cap, "tab:alpha_full_grid", [("", ai)],
                           list(r["technology"]), list(r["evaluation"]))


def table_alpha_waves(ai1: dict, w2: dict, m: dict) -> str:
    """Table 38: complete panels for the second wave, both waves and both without the D39-exposed paragraphs (§4n (1))."""
    P = w2["populations"]; r = m["reliability"]
    names = [("wave2", "Wave 2: the other 80 paragraphs, at least 33 days after the first coding, after the results"),
             ("both", "Both waves: all 120 paragraphs (mixes two intervals and exposures)"),
             ("wave2_without_d39", "Wave 2 without the 25 re-read paragraphs (not a random subsample)"),
             ("both_without_d39", "Both waves without the 25 re-read paragraphs (not a random subsample)")]
    # Printed since 2026-11-24: the two full populations; the two without the D39-exposed paragraphs stay in results.json.
    panels = [(f"{title}; {P[k]['paragraphs']} paragraphs, {P[k]['alpha']['articles']} articles, "
               f"{P[k]['rows_aligned']} shared rows", P[k]["alpha"]) for k, title in names[:2]]
    cap = (r"The coder's $\alpha$ over the second re-code wave and both waves, with article-resampling intervals "
           r"(specification §4n (1)). Wave 2 is the supplementary re-code; all analyses reported here are exploratory. The second wave hid the first labels but followed the registered results; 25 of "
           r"its paragraphs had been re-read with the first coding's and the model's elements in view "
           r"(Table~\ref{tab:missed_elements}). The same panels without these 25 paragraphs are electronic "
           r"(Appendix~\ref{app:electronic}). The registered wave is in Table~\ref{tab:alpha_full_grid}.")
    return alpha_longtable("The coder's alpha over both re-code waves", cap, "tab:alpha_waves", panels,
                           list(r["technology"]), list(r["evaluation"]))


def table_recode_waves(w2: dict, m: dict, rc40: dict) -> str:
    """Table 39: the model and the first coding against the second coding (§4n (1)), with the paired gaps of §4n (3);
    on the full 120 paragraphs the F1 interval of the model against the first coding is the registered one."""
    P = w2["populations"]; rows = []
    lab = {"model_vs_second": "Model against the second coding", "first_vs_second": "First coding against the second coding",
           "model_vs_first": "Model against the first coding"}
    plab = {"gap_vs_second": "Model precision minus recall, against the second coding",
            "gap_vs_first": "Model precision minus recall, against the first coding",
            "gap_change": "Change of that gap, second minus first",
            "f1_model_second_minus_first_second": "F1, model minus first coding, against the second"}
    blocks = [("both", "pooled", "Both waves, both languages"), ("both", "de", "Both waves, German"),
              ("both", "en", "Both waves, English"), ("wave1", "pooled", "Wave 1 alone, before the registered results"),
              ("wave2", "pooled", "Wave 2 alone, after the results"),
              ("both_without_d39", "pooled", "Both waves without the 25 re-read paragraphs"),
              ("wave2_without_d39", "pooled", "Wave 2 without the 25 re-read paragraphs")]
    reg = m["resample"]; regpt = m["per_execution"]
    for popk, lang, name in blocks:
        if popk == "wave1":
            r, pr = rc40[lang], w2["paired_wave1"][lang]
        else:
            r, pr = P[popk]["comparison"][lang], P[popk]["paired"][lang]
        rows.append(f"\\multicolumn{{4}}{{@{{}}l}}{{\\emph{{{name} ({r['paragraphs']} paragraphs, {r['articles']} articles)}}}} \\\\")
        for k in ("model_vs_second", "first_vs_second", "model_vs_first"):
            x = r[k]; f1iv = x["f1_iv"]; mark = ""
            if popk == "both" and k == "model_vs_first":
                want = regpt[lang]["1"]["element_micro_f1_weighted"]
                if abs(want - x["f1"]) > 1e-12:
                    raise AnalysisError(f"REFUSING: model-versus-first F1 ({x['f1']}) does not reproduce the registered {want} ({lang})")
                g = reg[lang]["element.micro_f1_weighted"]; f1iv = (g["lo"], g["hi"]); mark = "$^{\\ast}$"
            rows.append(f"{lab[k]} & {f3(x['precision'])}{iv(x['precision_iv'])} & {f3(x['recall'])}{iv(x['recall_iv'])} & "
                        f"{f3(x['f1'])}{iv(f1iv)}{mark} \\\\")
        for k in ("gap_vs_second", "gap_vs_first", "gap_change", "f1_model_second_minus_first_second"):
            d = pr[k]
            rows.append(f"{plab[k]} & \\multicolumn{{3}}{{@{{}}l}}{{{sg(d['diff'])}{iv(d['iv'])}}} \\\\")
        rows.append("\\addlinespace")
    body = "\n".join(rows[:-1])
    und = sum(pr_[k]["undefined"] for pk, _l, _n in blocks
              for pr_ in [(w2["paired_wave1"] if pk == "wave1" else P[pk]["paired"])[_l]]
              for k in ("gap_vs_second", "gap_vs_first", "gap_change", "f1_model_second_minus_first_second"))
    note = (r"Design-weighted; 95\,\% percentile intervals over 2{,}000 resamples of whole articles (seed 20260905), "
            r"common to the rows of a block, so differences are paired. $^{\ast}$: the registered interval of "
            r"Table~\ref{tab:primary}; the other intervals of that row are later calculations. Paired rows were added "
            r"after the results of the wave were inspected (specification §4n (3)). An interval containing zero shows no "
            r"equivalence. " + ("No paired resample was undefined. " if und == 0 else f"{und} paired resamples were undefined and are left out. ")
            + r"The blocks without the re-read paragraphs are not random subsamples.")
    return HEAD.replace("(specification §4l)", "(specification §4n (1), (3))") + \
        "% OUTLINE: Mixed-wave comparisons report reference sensitivity and paired contrasts without replacing registered results.\n" + r"""\begingroup\footnotesize
\setlength{\tabcolsep}{4pt}
\setlength{\LTpost}{3pt}
\begin{longtable}{@{}>{\raggedright\arraybackslash}p{50mm}>{\raggedright\arraybackslash}p{29mm}>{\raggedright\arraybackslash}p{29mm}>{\raggedright\arraybackslash}p{34mm}@{}}
\caption[Agreement against both re-code waves]{Element agreement against the coder's second coding (specification §4n). Wave 1 is the registered re-code of 40 paragraphs, made before the registered results, at least five days after completion of the first coding. Wave 2 is the supplementary re-code of the remaining 80, made after the results, at least 33 days after completion of the first coding. Comparisons using supplementary labels, alone or pooled, are exploratory. Combined results mix these intervals and exposures. The second coding is the reference in the first two rows of each block. One coder's temporal stability, not an intercoder ceiling.}\label{tab:recode_waves} \\
\toprule
\textbf{Comparison} & \textbf{Precision} & \textbf{Recall} & \textbf{Micro-F1} \\
\midrule
\endfirsthead
\toprule
\textbf{Comparison} & \textbf{Precision} & \textbf{Recall} & \textbf{Micro-F1} \\
\midrule
\endhead
\bottomrule
\endfoot
\bottomrule
\endlastfoot
""" + body + r"""
\end{longtable}
% OUTLINE: The note distinguishes registered intervals from later paired calculations and states the exclusion and undefined-draw conventions.
\par\noindent{\footnotesize """ + note + r"""\par}
\par\bigskip
\endgroup
"""


def table_recode_elements(w2: dict) -> str:
    """§4n (3) (c), (d): per element, the model against both readings on all 120 paragraphs, and the tuple counts."""
    D = w2["element_direction"]; rows = []
    def pr(d):
        if not d["reported"] or d["precision"] is None or d["recall"] is None:
            return f"$n<{S_MIN}$ & --"
        return f"{f3(d['precision'])} & {f3(d['recall'])}"
    for e in ev.ELEMENTS:
        x = D["elements"][e]; bw = x["by_wave"]
        rows.append(f"{e} & {x['first']['tuples']}/{x['second']['tuples']}/{x['model_tuples']} & "
                    f"{bw['wave1']['first']}/{bw['wave1']['second']}/{bw['wave1']['model']} & "
                    f"{bw['wave2']['first']}/{bw['wave2']['second']}/{bw['wave2']['model']} & "
                    f"{pr(x['first'])} & {pr(x['second'])} \\\\")
    T = D["totals"]
    rows.append("\\midrule")
    rows.append(f"All elements & {T['first']}/{T['second']}/{T['model']} & {T['wave1']['first']}/{T['wave1']['second']}/{T['wave1']['model']} & "
                f"{T['wave2']['first']}/{T['wave2']['second']}/{T['wave2']['model']} & \\multicolumn{{4}}{{@{{}}l}}{{pooled: Table~\\ref{{tab:recode_waves}}}} \\\\")
    return HEAD.replace("(specification §4l)", "(specification §4n (3))") + \
        "% OUTLINE: Per-element weighted comparisons and unweighted wave counts show the directions concealed by pooled balance.\n" + r"""\begin{table}[htbp]
\centering
\footnotesize
\setlength{\tabcolsep}{4pt}
\begin{threeparttable}
\caption[Direction per element against both readings]{The model's element precision and recall against the coder's first and second coding of all 120 paragraphs, design-weighted, and the unweighted element-tuple counts (specification §4n (3), added after the results of the supplementary re-code were inspected). Analyses using the second coding are exploratory. Element codes as in Table~\ref{tab:codebook_elements}. A cell needs at least five tuples in its reading. Tuples per wave: first/second/model. Technology rows: """ + f"{T['rows']['first']} (first), {T['rows']['second']} (second), {T['rows']['model']} (model)." + r"""}
\label{tab:recode_elements}
\begin{tabular}{@{}lrrrrrrr@{}}
\toprule
 & \multicolumn{3}{c}{\textbf{Tuples}} & \multicolumn{2}{c}{\textbf{Against the first}} & \multicolumn{2}{c}{\textbf{Against the second}} \\
\cmidrule(lr){2-4}\cmidrule(lr){5-6}\cmidrule(lr){7-8}
\textbf{Element} & \textbf{1st/2nd/model} & \textbf{Wave 1} & \textbf{Wave 2} & \textbf{Precision} & \textbf{Recall} & \textbf{Precision} & \textbf{Recall} \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}
\end{threeparttable}
\end{table}
"""


def ev_tex(s: str) -> str:
    return s.replace("_", "\\_")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="recompute and compare with the files on disk; write nothing")
    a = ap.parse_args()
    inp, S, m = load_registered()
    pids40, ra, rb = recode_inputs(inp, S, m)
    results = {"authority": "pre-analysis specification §4l (1)-(4), declared 2026-11-06",
               "registered_run": REG, "run_record_sha256": inp["provenance"]["run_record_sha256"],
               "recode_files": {k: v["sha256"] for k, v in m["reliability"]["inputs"]["files"].items()},
               "B": B, "seed": SEED, "threshold": S_MIN,
               "modal_baseline": modal_baseline(S, m), "fn_causes": fn_causes(S, m),
               "recode_comparison": recode_comparison(S, pids40, ra, rb), "alpha_full_grid": alpha_full_grid(m, pids40, ra, rb)}
    results["alpha_intervals"] = alpha_intervals(S, pids40, ra, rb, reference=registered_alpha(m, results["alpha_full_grid"]))
    results["alpha_intervals"]["authority"] = "pre-analysis specification §4n (2), declared 2026-11-10"
    w2 = wave2_inputs(inp, S)
    if w2 is not None:
        results["recode_wave2"] = wave2_analysis(S, w2, pids40, ra, rb)
        results["recode_wave2"]["authority"] = "pre-analysis specification §4n (1), declared 2026-11-10"
    files = {OUT / "results.json": json.dumps(results, indent=2, ensure_ascii=False, default=str) + "\n",
             PAPER / "tab_recode_comparison.tex": table_recode(results["recode_comparison"]),
             PAPER / "tab_fn_causes.tex": table_fn(results["fn_causes"]),
             PAPER / "tab_alpha_full_grid.tex": table_alpha(results["alpha_full_grid"], results["alpha_intervals"], m)}
    if "recode_wave2" in results:
        files[PAPER / "tab_alpha_waves.tex"] = table_alpha_waves(results["alpha_intervals"], results["recode_wave2"], m)
        files[PAPER / "tab_recode_waves.tex"] = table_recode_waves(results["recode_wave2"], m, results["recode_comparison"])
        files[PAPER / "tab_recode_elements.tex"] = table_recode_elements(results["recode_wave2"])
    if a.check:
        stale = [str(p.relative_to(ROOT)) for p, t in files.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
        if stale:
            raise AnalysisError(f"STALE: {stale}")
        print("review_analyses --check: all outputs current")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for p, t in files.items():
        p.write_text(t, encoding="utf-8")
    mb, fc, rc, ag = (results[k] for k in ("modal_baseline", "fn_causes", "recode_comparison", "alpha_full_grid"))
    print(json.dumps({"modal_baseline": mb["pooled"], "fn_causes": {p: fc[p]["share_weighted"] for p in POPS},
                      "union": {p: (fc[p]["exec1_recall"], fc[p]["union_recall"]) for p in POPS},
                      "recode": {k: {x: rc["pooled"][k][x] for x in ("precision", "recall", "f1")}
                                 for k in ("model_vs_second", "first_vs_second", "model_vs_first")},
                      "recall_diff": rc["pooled"]["recall_diff_second_minus_first"],
                      "alpha_pooled": (ag["pooled_aligned"], ag["pooled_full"]),
                      "alpha_intervals": {k: (round(x["alpha"], 3), [round(c, 3) for c in x["ci"]], x["q"], x["undefined"])
                                          for k, x in results["alpha_intervals"]["items"].items()
                                          if x["alpha"] is not None and x["ci"][0] is not None}}, indent=1, default=str))


if __name__ == "__main__":
    main()
