#!/usr/bin/env python3
"""Exploratory DSL estimate of frame-level prevalences (pre-analysis specification §4d, declared 2026-10-19).

Design-based supervised learning (Egami, Hinck, Stewart & Wei 2023): for every class c of the registered
displacement table (a technology, an element, a frame--evaluation pair), the share of the frozen eligible frame's
paragraphs on which c is present, estimated as

    theta_DSL(c) = (1/N) * sum_i [ yhat_i(c) + (R_i / pi_i) * (y_i(c) - yhat_i(c)) ]

with yhat_i the model's execution-one indicator on paragraph i, y_i the reference indicator, R_i = 1 for the
registered A2 sample and pi_i its registered inclusion probability (n_h / N_h). The model's coverage of the frame
comes from three journalled runs of the registered instrument: the registered run (120 sampled paragraphs), the
frame-completion run (443 paragraphs outside A2 and A3) and the A3 run (55 census paragraphs); no estimate is
computed before all three exist (the remainder of the frame outside A3 is sample-dependent). Every run is verified as the
evaluator verifies it (file digests, certified gates, journal reconciliation); every manifest under its contract.
Uncertainty, two readings side by side: (i) the 2.5--97.5 percentile over resamples of entire frame articles
(B = 2000, seed 20260905), the sampled indicator travelling with its paragraph; descriptive, as every interval of the
thesis; (ii) added 2026-10-19 after the ingest of prediction-powered inference (Angelopoulos et al. 2023, P100): the
design-based standard error of the estimator under the registered stratified draw. The first term of the estimator is
a census constant, so the design variance is that of the Horvitz--Thompson total of the residual e_i = y_i - yhat_i
over the sample: sum over variance groups of (1 - f_g) * n_g/(n_g - 1) * sum_i (w_i e_i - mean_g(w e))^2, divided by
N^2, with f_g = n_g/N_g for a group that is a single stratum (the exact stratified-SRSWOR estimator) and f_g = 0 for a
pooled group. Groups: the strata of a language are walked in allocation order and a group closes once it holds two
draws, so a single-draw stratum is pooled with its neighbours; a pooled group keeps the with-replacement form without
the finite-population factor, which overstates its variance in expectation (with the factor it can understate it, as a
counterexample of the Codex review of 2026-10-19 shows).
A normal 95 % interval and, for comparison, the same standard error of the reference-only Horvitz--Thompson estimate
(the "classical" interval of PPI) are reported: a design-based analogue of the prediction-powered interval derived for
this draw, not the published i.i.d. result. The predictor is fixed, execution noise is not included, the normal
approximation rests on 120 residuals, and nominal coverage is not guaranteed.
A paragraph whose execution ended without a valid record contributes yhat = 0 (the registered treatment of
failures), with the count reported. Classes whose reference support in the sample is below the threshold print
their state. Exploratory, never a registered figure.

    .venv/bin/python3 tools/pipeline/dsl.py --a2-run tools/runs/a2_sonnet_20261013.json \\
        --completion-run tools/runs/frame_completion_20261019.json [--a3-run tools/runs/<a3 run>.json] \\
        --gold-pass1 tools/annotation/labels/A2_pass1_v1.0.csv --gold-pass2 tools/annotation/labels/A2_pass2_v1.0.csv \\
        --threshold 5 [--check]
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
import evaluate as ev                                           # noqa: E402
import figures as fg                                            # noqa: E402
import sample_contract as sc                                    # noqa: E402

ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
PAPER = ROOT / "paper"
B_DEFAULT, SEED = ev.DEFAULT_B, ev.SEED


class DslError(RuntimeError):
    pass


# --------------------------------------------------------------------------- pure estimator
def dsl_point(units: list[dict]) -> dict:
    """units: dicts with yhat (0/1), R (0/1), pi (>0 when R=1), y (0/1 when R=1). Returns the DSL estimate, the naive
    model prevalence and the Horvitz--Thompson reference prevalence, all over len(units) as the population size."""
    N = len(units)
    if N == 0:
        raise DslError("no units")
    dsl = naive = ht = 0.0
    for u in units:
        yhat = float(u["yhat"]); naive += yhat
        term = yhat
        if u["R"]:
            if not u.get("pi") or u["pi"] <= 0 or u["pi"] > 1:
                raise DslError("a sampled unit without a valid inclusion probability")
            term += (float(u["y"]) - yhat) / u["pi"]
            ht += float(u["y"]) / u["pi"]
        dsl += term
    return {"dsl": dsl / N, "naive": naive / N, "reference_ht": ht / N, "N": N}


def dsl_bootstrap(units: list[dict], B: int, seed: int) -> dict:
    """Percentile interval over resamples of entire articles (article key `art` per unit); the bracket term and the
    unit count are pre-summed per article so a resample is a weighted sum over articles."""
    per_art_sum = defaultdict(float); per_art_n = Counter()
    for u in units:
        term = float(u["yhat"]) + ((float(u["y"]) - float(u["yhat"])) / u["pi"] if u["R"] else 0.0)
        per_art_sum[u["art"]] += term; per_art_n[u["art"]] += 1
    arts = sorted(per_art_n)
    rng = random.Random(seed)
    vals = []
    for _ in range(B):
        draw = Counter(rng.choice(arts) for _ in arts)
        num = sum(m * per_art_sum[a] for a, m in draw.items()); den = sum(m * per_art_n[a] for a, m in draw.items())
        vals.append(num / den)
    vals.sort()
    def q(p):
        k = p * (len(vals) - 1); lo = int(k); hi = min(lo + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)
    return {"lo": q(0.025), "hi": q(0.975), "B": B, "seed": seed, "articles": len(arts)}


Z975 = 1.959963984540054


def collapse_strata(strata: list[dict]) -> dict[str, str]:
    """Variance groups for the design-based standard error. `strata`: one dict per stratum with `stratum`
    (lang|outlet), `lang`, `N_h`, `n_h`. Walked per language in allocation order (sorted stratum label): a group
    closes once it holds at least two draws, so a stratum with a single draw is collapsed with the following stratum
    of its language, or with the preceding group when it is the last; a language with fewer than two draws in total
    has no estimable variance and is refused. Returns stratum -> group label."""
    groups: dict[str, str] = {}
    by_lang = defaultdict(list)
    for st in sorted(strata, key=lambda x: x["stratum"]):
        by_lang[st["lang"]].append(st)
    for lang, sts in by_lang.items():
        closed: list[list[dict]] = []; open_: list[dict] = []
        for st in sts:
            if st["n_h"] < 1:
                raise DslError(f"stratum {st['stratum']} has no draw")
            open_.append(st)
            if sum(x["n_h"] for x in open_) >= 2:
                closed.append(open_); open_ = []
        if open_:
            if not closed:
                raise DslError(f"language {lang} has fewer than two draws: no design-based variance")
            closed[-1].extend(open_)
        for grp in closed:
            label = "+".join(x["stratum"] for x in grp)
            for x in grp:
                groups[x["stratum"]] = label
    return groups


def design_variance(units: list[dict], groups: dict[str, str], value_key: str) -> dict:
    """Design-based variance of (1/N) * sum_{sampled} w_i * v_i under stratified simple random sampling without
    replacement, with v_i = units[i][value_key] on the sampled units (the residual y - yhat for the DSL estimate, y
    for the reference-only estimate). Per variance group g: (1 - f_g) * n_g/(n_g - 1) * sum_i (w_i v_i - mean)^2 with
    f_g = n_g/N_g when the group is a single stratum (then this equals the exact N_h^2 (1 - f_h) s_h^2 / n_h) and
    f_g = 0 when it pools several strata (the with-replacement form, which overstates the variance in expectation; with
    the pooled factor it can understate it). Summed over groups and divided by N^2. Sampled units carry `stratum`, `N_h`,
    `n_h` and `pi`; N is the number of units (the census)."""
    N = len(units)
    per_group: dict[str, list[float]] = defaultdict(list); sizes: dict[str, dict] = {}
    for u in units:
        if not u["R"]:
            continue
        for key in ("stratum", "N_h", "n_h"):
            if u.get(key) in (None, ""):
                raise DslError("a sampled unit without its stratum, N_h or n_h: no design-based variance")
        g = groups.get(u["stratum"])
        if g is None:
            raise DslError(f"stratum {u['stratum']} has no variance group")
        per_group[g].append(float(u[value_key]) / float(u["pi"]))
        sizes.setdefault(g, {"N": 0, "n": 0, "strata": set()})
        if u["stratum"] not in sizes[g]["strata"]:
            sizes[g]["strata"].add(u["stratum"]); sizes[g]["N"] += int(u["N_h"]); sizes[g]["n"] += int(u["n_h"])
    var_total = 0.0
    for g, vals in per_group.items():
        n_g, N_g = sizes[g]["n"], sizes[g]["N"]
        if len(vals) != n_g:
            raise DslError(f"variance group {g}: {len(vals)} sampled units, allocation says {n_g}")
        if n_g < 2:
            raise DslError(f"variance group {g} has a single draw")
        mean = sum(vals) / n_g
        fpc = (1.0 - n_g / N_g) if len(sizes[g]["strata"]) == 1 else 1.0     # no finite-population factor for a pooled group
        var_total += fpc * (n_g / (n_g - 1)) * sum((v - mean) ** 2 for v in vals)
    se = var_total ** 0.5 / N
    return {"se": se, "groups": len(per_group), "collapsed": sorted(g for g in per_group if "+" in g)}


# --------------------------------------------------------------------------- inputs
def read_csv(p: Path) -> list[dict]:
    return ev.read_csv(p)


REGISTERED_RUN_ID = "a2_sonnet_20261013"          # the registered run §4d names; no other A2 run supplies the sampled predictions
CONTRACT_KEYS = ("model_id", "region", "prompt_sha256", "tool_config_sha256", "codebook_sha256", "inference_config",
                 "additional_model_request_fields", "max_tokens", "retry_policy", "retry_policy_version",
                 "instrument_version", "instrument_v11_sha256")


def configuration(rec: dict) -> dict:
    """The request configuration a run bound, read from its fingerprint-bound execution contract (model, region,
    prompt, tool configuration, codebook, decoding, additional model settings, token cap, retry policy and
    allowances, instrument). The record's display fields must agree with the contract, or the record was edited."""
    c = rec.get("execution_contract") or {}
    if not c:
        raise DslError(f"{rec.get('run_id')}: the run record carries no execution contract")
    shown = {"model_id": rec.get("model_id"), "tool_config_sha256": rec.get("tool_config_sha256"),
             "prompt_sha256": (rec.get("prompt") or {}).get("sha256"), "codebook_sha256": (rec.get("codebook") or {}).get("sha256"),
             "region": rec.get("region"), "inference_config": rec.get("inference_config")}
    for k, v in shown.items():
        if v is not None and k in c and c[k] != v:
            raise DslError(f"{rec.get('run_id')}: the record's {k} differs from its execution contract; the record was edited")
    out = {k: c.get(k) for k in CONTRACT_KEYS}
    # records written before 2026-10-14 carry no instrument fields and are the registered v1.0 under retry policy v2
    # (evaluate.instrument_identity applies the same reading)
    out["instrument_version"] = str(out["instrument_version"] or "1.0")
    out["retry_policy_version"] = str(out["retry_policy_version"] or "2")
    return out


def same_configuration(a: dict, b: dict) -> list[str]:
    """The contract keys on which two runs' configurations differ (empty = the same registered request)."""
    ca, cb = configuration(a), configuration(b)
    return sorted(k for k in CONTRACT_KEYS if ca[k] != cb[k])


def require_registered(rec: dict) -> None:
    if rec.get("run_id") != REGISTERED_RUN_ID:
        raise DslError(f"the sampled predictions must come from the registered run {REGISTERED_RUN_ID} (§4d), not {rec.get('run_id')!r}")


def load_run(path: Path, expect_status: str | None, expect_kind: str | None) -> dict:
    """A journalled run, verified as the evaluator verifies a reportable one: file digests, the manifest it ran on
    (digest, kind, contract), every gate certified, journal reconciliation, one execution recorded per paragraph.
    Returns the record, the manifest, the paragraphs, and execution-one predictions and outcomes per paragraph."""
    rec = json.loads(path.read_text(encoding="utf-8"))
    status = rec.get("analysis_status") or "registered primary"
    if expect_status and status != expect_status:
        raise DslError(f"{path.name}: analysis status {status!r}, expected {expect_status!r}")
    if rec.get("dev") or not rec.get("reportable"):
        raise DslError(f"{path.name}: not a reportable (certified, non-development) run")
    files = {}
    for name, meta in rec["files"].items():
        p = ROOT / meta["path"] if not Path(meta["path"]).is_absolute() else Path(meta["path"])
        if not p.exists() or ev.sha256_file(p) != meta["sha256"]:
            raise DslError(f"{path.name}: {name} is missing or its digest differs from the run record")
        files[name] = p
    mpath = ROOT / rec["manifest"]["path"] if not Path(rec["manifest"]["path"]).is_absolute() else Path(rec["manifest"]["path"])
    man = json.loads(mpath.read_text(encoding="utf-8"))
    if ev.canonical_digest(man) != rec["manifest"]["sha256"] or (man.get("digest") and man["digest"] != ev.canonical_digest(man)):
        raise DslError(f"{path.name}: the manifest on disk is not the one the run was executed on")
    if expect_kind and man.get("kind") != expect_kind:
        raise DslError(f"{path.name}: manifest kind {man.get('kind')!r}, expected {expect_kind!r}")
    try:
        if man.get("kind") == "A2_sample_manifest":
            sc.verify_sample(man)
        elif man.get("kind") == sc.FRAME_COMPLETION_KIND:
            sc.verify_frame_completion(man)
        elif man.get("kind") == sc.A3_KIND:
            sc.verify_a3_sample(man)
        else:
            raise DslError(f"{path.name}: unknown manifest kind {man.get('kind')!r}")
    except sc.ContractError as exc:
        raise DslError(f"{path.name}: {exc}") from None
    paras = man["paragraphs"]
    if rec["manifest"].get("restrict_split") or len(paras) != rec["manifest"]["n_paragraphs"]:
        raise DslError(f"{path.name}: the run did not cover the whole manifest")
    pset = {p["paragraph_id"] for p in paras}
    term = [r for r in read_csv(next(p for n, p in files.items() if n.endswith("_terminal.csv"))) if r["paragraph_id"] in pset]
    pred = list(read_csv(next(p for n, p in files.items() if n.endswith("_predictions.csv"))))
    try:
        ev.verify_gate(rec, term, pred, paras)
    except ev.EvalError as exc:
        raise DslError(f"{path.name}: {exc}") from None
    k = int(rec["repeats"])
    ids = Counter((r["paragraph_id"], int(r["run_index"])) for r in term)
    want = {(pid, r) for pid in pset for r in range(1, k + 1)}
    if set(ids) != want or any(n != 1 for n in ids.values()):
        raise DslError(f"{path.name}: terminal records are not exactly one per (paragraph, execution 1..{k})")
    out1 = {r["paragraph_id"]: r["outcome"] for r in term if int(r["run_index"]) == 1}
    rows1 = defaultdict(list)
    for r in pred:
        if int(r["run_index"]) == 1 and r["paragraph_id"] in pset:
            rows1[r["paragraph_id"]].append(r)
    calls = next(p for n, p in files.items() if n.endswith("_calls.jsonl"))
    return {"rec": rec, "manifest": man, "paras": paras, "pred": rows1, "outcome": out1, "path": path, "calls": calls}


def indicators(rows: list[dict], frame_of: dict) -> set[str]:
    """Paragraph-level class indicators of table 5: any relation carrying the class."""
    out = set()
    for r in rows:
        out.add(f"technology:{r['technology']}")
        rel = ev.rel_from_row(r, frame_of)
        for e in rel.elements:
            out.add(f"element:{e}")
        for f, v in rel.evals:
            out.add(f"eval:{f}={v}")
    return out


# --------------------------------------------------------------------------- main
def estimate(a2: dict, comp: dict, a3: dict | None, gold1: list[dict], gold2: list[dict], frame: dict, frame_of: dict,
             s: int, B: int, seed: int, metrics: dict) -> dict:
    """The frame estimate (N = every frame paragraph). Requires the model's execution-one predictions on EVERY frame
    paragraph: the sample-dependent remainder of the frame outside A3 is not a population the registered inclusion
    probabilities describe (Codex, 2026-10-19), so no estimate is computed before the A3 run exists."""
    frame_pids = {p["paragraph_id"]: p for p in frame["paragraphs"]}
    pi = {p["paragraph_id"]: float(p["pi"]) for p in a2["paras"]}
    sampled = set(pi)
    design = {p["paragraph_id"]: {"stratum": p.get("stratum") or f"{p.get('lang')}|{p.get('outlet')}", "lang": p.get("lang"),
                                  "N_h": p.get("N_h"), "n_h": p.get("n_h")} for p in a2["paras"]}
    strata = {d["stratum"]: {"stratum": d["stratum"], "lang": d["lang"], "N_h": d["N_h"], "n_h": d["n_h"]} for d in design.values()}
    if any(v["N_h"] is None or v["n_h"] is None for v in strata.values()):
        raise DslError("the sample manifest does not carry N_h and n_h per paragraph: no design-based variance")
    groups = collapse_strata(list(strata.values()))
    if a3 is None:
        raise DslError("the frame estimate needs the A3 run's predictions on the 55 census paragraphs; the remainder of "
                       "the frame outside A3 is sample-dependent and is not estimated (specification §4d)")
    for run in (a2, comp, a3):
        if run is not a2:
            diff = same_configuration(a2["rec"], run["rec"])
            if diff:
                raise DslError(f"{run['rec']['run_id']} was not run under the registered request: differs in {diff}")
    covered = {}
    for run in (a2, comp, a3):
        for p in run["paras"]:
            if p["paragraph_id"] in covered:
                raise DslError(f"{p['paragraph_id']} is covered by two runs")
            covered[p["paragraph_id"]] = run
    if set(covered) != set(frame_pids):
        raise DslError(f"coverage mismatch: {len(covered)} covered, {len(frame_pids)} in the frame; missing "
                       f"{sorted(set(frame_pids) - set(covered))[:4]}, extra {sorted(set(covered) - set(frame_pids))[:4]}")
    for pid, run in covered.items():
        q = frame_pids[pid]; p_ = next(x for x in run["paras"] if x["paragraph_id"] == pid)
        if p_.get("digest") != q.get("digest") or p_.get("text_sha256") != q.get("text_sha256"):
            raise DslError(f"{pid}: the run's manifest identity differs from the frozen frame")
    g_rows = defaultdict(list)
    for r in gold2:
        g_rows[r["paragraph_id"]].append(r)
    have1 = {r["paragraph_id"] for r in gold1}
    if sampled - have1:
        raise DslError("the reference pass-1 file does not cover every sampled paragraph")
    gold_ind = {pid: indicators(g_rows.get(pid, []), frame_of) for pid in sampled}
    yhat_ind = {}; failed = Counter()
    for pid, run in covered.items():
        if run["outcome"][pid] not in ("technologies_present", "off_topic"):
            failed[run["rec"]["run_id"]] += 1
        yhat_ind[pid] = indicators(run["pred"].get(pid, []), frame_of)
    classes = sorted(c for c in set().union(*yhat_ind.values(), *gold_ind.values()) if c.startswith(("technology:", "element:", "eval:")))
    gold_support = {c: sum(1 for pid in sampled if c in gold_ind[pid]) for c in classes}
    results = {}
    for c in classes:
        units = [{"yhat": 1 if c in yhat_ind[pid] else 0, "R": 1 if pid in sampled else 0, "pi": pi.get(pid),
                  "y": (1 if c in gold_ind[pid] else 0) if pid in sampled else None, "art": frame_pids[pid]["article_id"],
                  **(design[pid] if pid in sampled else {})}
                 for pid in covered]
        for u in units:
            if u["R"]:
                u["resid"] = float(u["y"]) - float(u["yhat"])
        pt = dsl_point(units); bs = dsl_bootstrap(units, B, seed)
        dv = design_variance(units, groups, "resid"); cv = design_variance(units, groups, "y")
        reg = metrics.get("displacement", {}).get("pooled", {}).get(c, {}).get("weighted_pp")
        results[c] = {"dsl_pp": 100 * pt["dsl"], "lo_pp": 100 * bs["lo"], "hi_pp": 100 * bs["hi"], "naive_pp": 100 * pt["naive"],
                      "reference_ht_pp": 100 * pt["reference_ht"], "gold_support": gold_support[c],
                      "reported": gold_support[c] >= s, "registered_displacement_pp": reg,
                      "se_design_pp": 100 * dv["se"], "ci_design_lo_pp": 100 * (pt["dsl"] - Z975 * dv["se"]),
                      "ci_design_hi_pp": 100 * (pt["dsl"] + Z975 * dv["se"]), "se_reference_design_pp": 100 * cv["se"]}
    return {"population": "frame", "N": len(covered), "articles": len({frame_pids[p]["article_id"] for p in covered}),
            "sampled": len(sampled), "failed_executions": dict(failed), "threshold": s, "B": B, "seed": seed,
            "variance_groups": {"n": len(set(groups.values())), "strata": len(groups), "collapsed": sorted({g for g in groups.values() if "+" in g})},
            "classes": results, "runs": {r["rec"]["run_id"]: {"analysis_status": r["rec"].get("analysis_status"), "n": len(r["paras"])}
                                          for r in (a2, comp, a3)}}


def tex_tables(res: dict, head: str) -> dict[str, str]:
    s = res["threshold"]; pop = res["population"]
    def cell(c):
        r = res["classes"][c]
        if not r["reported"]:       # one spanning cell keeps the state on one line; the support stays in its column
            return None
        return (f"{r['dsl_pp']:.1f}", f"[{r['ci_design_lo_pp']:.1f}, {r['ci_design_hi_pp']:.1f}]", f"[{r['lo_pp']:.1f}, {r['hi_pp']:.1f}]",
                f"{r['naive_pp']:.1f}", f"{r['reference_ht_pp']:.1f} ({r['se_reference_design_pp']:.1f})",
                "" if r["registered_displacement_pp"] is None else f"{r['registered_displacement_pp']:+.1f}", str(r["gold_support"]))
    rows = []
    groups = [("Technology", [f"technology:{t}" for t in fg.TECH_ORDER]), ("Frame element", [f"element:{e}" for e in fg.ELEMENT_ORDER]),
              ("Frame and evaluation", [c for c in sorted(res["classes"]) if c.startswith("eval:")])]
    for title, keys in groups:
        rows.append(f"\\multicolumn{{8}}{{@{{}}l}}{{\\emph{{{title}}}}} \\\\")
        for c in keys:
            if c not in res["classes"]:
                continue
            name = c.split(":", 1)[1].replace("=", " = ")
            got = cell(c)
            if got is None:
                rows.append(f"{fg.tex_escape(name)} & \\multicolumn{{6}}{{l}}{{$n<{s}$, not reported}} & {res['classes'][c]['gold_support']} \\\\")
                continue
            d, ci, ai, n, h, g, sup = got
            rows.append(f"{fg.tex_escape(name)} & {d} & {ci} & {ai} & {n} & {h} & {g} & {sup} \\\\")
        rows.append(r"\addlinespace")
    rows = rows[:-1]
    # a paginating table since 2026-11-05 (layout review): as a float it was taller than the page and lost its last rows;
    # the caption is short and the method moves into the notes after the table
    caption = (f"Adjusted (DSL) prevalences of every class in the frozen eligible frame ({res['N']} paragraphs), in \\% of its "
               f"paragraphs, beside the model-only share, the sample-only estimate of the first coding and the registered displacement.")
    notes = [f"Exploratory analysis declared on 2026-10-19 (specification §4d), not a registered figure. The adjusted estimate is "
             f"the prevalence the human coding instrument would record: the design-based supervised-learning estimator of Egami and "
             f"colleagues with the model's first-execution indicator as a fixed predictor, corrected by the registered reference "
             f"under the sample's inclusion probabilities. Model: the model-only prevalence over the frame. Reference: the "
             f"Horvitz--Thompson prevalence from the sample, with the design-based standard error of this reference-only estimate "
             f"in parentheses. Registered displacement: model minus reference on the sample.",
             f"Two intervals, both approximate and without a coverage guarantee; a degenerate interval (zero width, or reaching "
             f"below zero) marks where the approximation fails. Design: the 95\\% normal-approximation interval on the stratified-sampling standard error of the "
             f"design-weighted residual under the registered allocation, a design-based analogue of the prediction-powered interval "
             f"of Angelopoulos and colleagues ({res['variance_groups']['n']} variance groups from {res['variance_groups']['strata']} "
             f"strata, single-draw strata pooled with their neighbours in allocation order, a pooled group without the "
             f"finite-population factor; nominal coverage not guaranteed). Article: the descriptive article-reweighting interval "
             f"(2.5--97.5 percentile over {res['B']} resamples of {res['articles']} articles, the sampled indicator attached; no "
             f"coverage guarantee).",
             f"A class whose reference support in the sample is below {s} paragraphs is not reported; its support is in the last "
             f"column. The predictor is fixed: execution-to-execution noise is in neither interval. Executions without a valid "
             f"record contribute a zero indicator: "
             + (", ".join(f"\\texttt{{{fg.tex_escape(k)}}}: {v}" for k, v in res["failed_executions"].items()) or "none")
             + ". Runs: " + ", ".join(f"\\texttt{{{fg.tex_escape(r)}}}" for r in res["runs"]) + "."]
    header = (r"Class & Adj., \% & Approx.\ 95\% interval, design-based & Interval, article & Model, \% & Reference, \% (SE) & "
              r"Registered displ., pp & Sample support")
    L, R = ">{\\raggedright\\arraybackslash}p{%smm}", ">{\\raggedleft\\arraybackslash}p{%smm}"
    # 2026-11-12, M22 page review (finding 50): the bold words "Model," and "Registered" are wider than 10 and 14 mm and ran
    # into the next header; 11 and 17 mm hold them, and the table stays within the text width (about 151 of 160 mm).
    # The caption names the first coding as the printed override in appendix.tex does (M22 sweep, repair 5).
    colspec = "@{}" + L % 36 + L % 9 + L % 18 + L % 18 + R % 11 + R % 16 + R % 17 + R % 11 + "@{}"   # headers wrap in p-columns
    tab = fg.longtable_env(caption, "tab:dsl", colspec, 8, header, rows, notes,
                           short="Adjusted prevalences of every class", tabcolsep="3pt")
    return {"tab_dsl.tex": head + tab}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a2-run", required=True, type=Path); ap.add_argument("--completion-run", required=True, type=Path)
    ap.add_argument("--a3-run", type=Path, default=None)
    ap.add_argument("--gold-pass1", required=True, type=Path); ap.add_argument("--gold-pass2", required=True, type=Path)
    ap.add_argument("--a3-gold-pass1", type=Path, default=None); ap.add_argument("--a3-gold-pass2", type=Path, default=None)
    ap.add_argument("--threshold", type=int, required=True)
    ap.add_argument("--resamples", type=int, default=B_DEFAULT); ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    try:
        a2 = load_run(a.a2_run, "registered primary", "A2_sample_manifest")
        require_registered(a2["rec"])
        # the registered reference and metrics, loaded and verified exactly as the registered evaluation loads them
        ns = argparse.Namespace(run_record=str(a.a2_run), manifest=str(ROOT / a2["rec"]["manifest"]["path"]), restrict_split=None,
                                gold_pass1=str(a.gold_pass1), gold_pass2=str(a.gold_pass2), dev=False, gold_manifest=None,
                                skip_provenance=False, threshold=a.threshold, reliability=None)
        try:
            inp = ev.load_inputs(ns)
        except ev.EvalError as exc:
            raise DslError(f"registered reference: {exc}") from None
        mpath = RUNS / "tables" / a2["rec"]["run_id"] / "metrics.json"
        if not mpath.exists():
            raise DslError(f"no registered metrics at {mpath}")
        metrics = json.loads(mpath.read_text(encoding="utf-8"))
        prov = inp["provenance"]
        if (metrics.get("dev") or metrics.get("threshold") != a.threshold or metrics.get("inputs", {}).get("run_record_sha256") != prov["run_record_sha256"]
                or metrics.get("inputs", {}).get("reference") != prov["reference"]):
            raise DslError("the registered metrics were not computed from this run record, reference and threshold")
        comp = load_run(a.completion_run, sc.FRAME_COMPLETION_STATUS, sc.FRAME_COMPLETION_KIND)
        a3 = None
        if a.a3_run:
            if not (a.a3_gold_pass1 and a.a3_gold_pass2):
                raise DslError("--a3-run needs --a3-gold-pass1 and --a3-gold-pass2 (the finalised A3 reference the embargo recorded)")
            a3 = load_run(a.a3_run, sc.A3_STATUS, sc.A3_KIND)
            try:
                ev.analysis_status_of(a3["rec"], manifest_kind=sc.A3_KIND)
                ev.verify_a3_evidence(a3["rec"], a3["manifest"], a.a3_gold_pass1, a.a3_gold_pass2, a3["calls"])
            except ev.EvalError as exc:
                raise DslError(f"A3 run: {exc}") from None
        frame = json.loads(Path(sc.FRAME_MANIFEST).read_text(encoding="utf-8"))
        if sc.canonical_digest(frame) != frame.get("digest") or a2["manifest"].get("frame_manifest_digest") != frame["digest"]:
            raise DslError("the frame manifest on disk is not the one the sample was drawn from")
        res = estimate(a2, comp, a3, inp["gold1"], inp["gold2"], frame, inp["frame_of"], a.threshold, a.resamples, a.seed, metrics)
    except DslError as exc:
        sys.exit(f"REFUSING: {exc}")
    here = Path(__file__).resolve()
    res["inputs"] = {"runs": {r["rec"]["run_id"]: ev.sha256_file(r["path"]) for r in (a2, comp, a3)},
                     "reference": prov["reference"], "frame_manifest_digest": frame["digest"],
                     "metrics_sha256": ev.sha256_file(mpath), "dsl_sha256": ev.sha256_file(here)}
    out_dir = RUNS / "tables" / "dsl"
    head = (f"% Generated by tools/pipeline/dsl.py (sha256 {res['inputs']['dsl_sha256'][:16]}) from runs "
            + ", ".join(res["runs"]) + f"; the frozen frame ({res['N']} paragraphs); exploratory DSL estimate "
            "(specification §4d, 2026-10-19), not a registered figure. Do not edit by hand.\n")
    files = {out_dir / "dsl_prevalence.json": json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n"}
    lines = ["# machine-readable intermediate results: every class, including those below the reporting threshold (see `reported`)",
             "class;dsl_pp;se_design_pp;ci_design_lo_pp;ci_design_hi_pp;article_lo_pp;article_hi_pp;naive_model_pp;reference_ht_pp;"
             "se_reference_design_pp;registered_displacement_pp;sample_support;reported"]
    for c, r in sorted(res["classes"].items()):
        lines.append(";".join(str(x) for x in (c, f"{r['dsl_pp']:.3f}", f"{r['se_design_pp']:.3f}", f"{r['ci_design_lo_pp']:.3f}",
                                              f"{r['ci_design_hi_pp']:.3f}", f"{r['lo_pp']:.3f}", f"{r['hi_pp']:.3f}", f"{r['naive_pp']:.3f}",
                                              f"{r['reference_ht_pp']:.3f}", f"{r['se_reference_design_pp']:.3f}",
                                              "" if r["registered_displacement_pp"] is None else f"{r['registered_displacement_pp']:.3f}",
                                              r["gold_support"], r["reported"])))
    files[out_dir / "dsl_prevalence.csv"] = "\n".join(lines) + "\n"
    for name, text in tex_tables(res, head).items():
        files[PAPER / "tables" / name] = text
    stale = []
    if not a.check:
        out_dir.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        if a.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path)
            continue
        path.write_text(text, encoding="utf-8"); print(f"  wrote {path.relative_to(ROOT)}")
    if a.check:
        if stale:
            sys.exit("STALE: " + ", ".join(str(p) for p in stale))
        print(f"  {len(files)} file(s) match")
        return
    print(f"DSL (frame, N = {res['N']}, {res['articles']} articles, failed executions {res['failed_executions'] or 'none'}):")
    for c in [f"element:{e}" for e in fg.ELEMENT_ORDER] + [f"technology:{t}" for t in fg.TECH_ORDER]:
        r = res["classes"].get(c)
        if r and r["reported"]:
            print(f"  {c:24s} DSL {r['dsl_pp']:5.1f} [{r['lo_pp']:5.1f}, {r['hi_pp']:5.1f}]  model {r['naive_pp']:5.1f}  reference {r['reference_ht_pp']:5.1f}  registered displacement {r['registered_displacement_pp']}")


if __name__ == "__main__":
    main()
