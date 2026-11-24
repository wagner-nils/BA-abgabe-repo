#!/usr/bin/env python3
"""Checks on existing data after the third review (pre-analysis specification §4o, declared 2026-11-10 before any of
them was computed).

  (1) language contrast: German minus English element and technology micro-F1, articles resampled within language;
  (2) terminal parse failures by validator code and language, registered run and fresh-census run;
  (3) agreement on the paragraphs with a valid first execution, registered model and every comparison configuration;
  (4) element agreement of the registered execution 1 without the `general` technology rows;
  (5) article intervals for the per-element F1 of the registered execution 1;
  (6) the design-based adjustment of §4d with the second coding of all 120 paragraphs as reference (sensitivity);
  (7) the class sets behind the macro averages (read from the class manifest).

Descriptive, test-set reuse, nothing selected; no model call. Every input is loaded through the verified paths of
evaluate.py, compare_models.py, review_analyses.py and dsl.py, and the script refuses to write unless it reproduces
the registered element micro-F1 and the registered adjusted prevalences exactly.

    .venv/bin/python3 tools/pipeline/review_checks_4o.py [--check]

Writes tools/runs/tables/review_checks_4o/results.json and paper/tables/tab_checks_4o.tex.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compare_models as cm                                         # noqa: E402
import dsl                                                          # noqa: E402
import evaluate as ev                                               # noqa: E402
import figures as fg                                                # noqa: E402
import review_analyses as ra                                        # noqa: E402
import sample_contract as sc                                        # noqa: E402

ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
LABELS = ROOT / "tools" / "annotation" / "labels"
OUT = RUNS / "tables" / "review_checks_4o"
TEX = ROOT / "paper" / "tables" / "tab_checks_4o.tex"
B, SEED = ra.B, ra.SEED
A3_RUN = "a3_sonnet_20261019"
COMPLETION = "frame_completion_20261019"


class CheckError(RuntimeError):
    pass


def f1(tp, fp, fn):
    return 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else None


def pct(draws):
    d = sorted(v for v in draws if v is not None)
    if not d:
        return (None, None)
    q = lambda p: d[int(round(p * (len(d) - 1)))]
    return (q(0.025), q(0.975))


def prf(per_art, mult):
    p, r = cm._pr(per_art, mult)
    f = ev.micro(per_art, "element", mult, True)
    return p, r, f


def prf_block(per_art, arts, intervals=True):
    base = {a: 1 for a in arts}
    p, r, f = prf(per_art, base)
    out = {"precision": p, "recall": r, "f1": f, "gap": None if p is None or r is None else p - r, "articles": len(arts)}
    if intervals:
        rng = random.Random(SEED)
        draws = [prf(per_art, Counter(rng.choice(arts) for _ in arts)) for _ in range(B)]
        out["precision_iv"] = pct([d[0] for d in draws]); out["recall_iv"] = pct([d[1] for d in draws])
        out["f1_iv"] = pct([d[2] for d in draws])
        out["gap_iv"] = pct([None if d[0] is None or d[1] is None else d[0] - d[1] for d in draws])
    return out


# --------------------------------------------------------------------------- (1)
def language_contrast(S: dict) -> dict:
    per, arts = {}, {}
    for L in ("de", "en"):
        ps = [p for p in S["pids"] if S["lang"][p] == L]
        per[L] = ev.counts_execution(S, ps, 1)["per_art"]
        arts[L] = sorted({S["art"][p] for p in ps})
    out = {}
    for table in ("element", "technology"):
        point = {L: ev.micro(per[L], table, {a: 1 for a in arts[L]}, True) for L in ("de", "en")}
        rng = random.Random(SEED)
        draws = []
        for _ in range(B):
            mu = {L: Counter(rng.choice(arts[L]) for _ in arts[L]) for L in ("de", "en")}
            a, b = ev.micro(per["de"], table, mu["de"], True), ev.micro(per["en"], table, mu["en"], True)
            draws.append(None if a is None or b is None else a - b)
        out[table] = {"de": point["de"], "en": point["en"], "diff_de_minus_en": point["de"] - point["en"], "iv": pct(draws),
                      "articles": {L: len(arts[L]) for L in ("de", "en")}}
    return out


# --------------------------------------------------------------------------- (2)
def failures(run_id: str) -> dict:
    rows = list(csv.DictReader(open(RUNS / f"{run_id}_terminal.csv", encoding="utf-8"), delimiter=";"))
    out = {"executions": len(rows)}
    for scope, sel in (("all_executions", rows), ("execution_1", [r for r in rows if r["run_index"] == "1"])):
        fail = [r for r in sel if r["outcome"] not in ev.SUCCESS]
        by = Counter((r["lang"], r["outcome"], (r["codes"].split("|")[-1] if r["codes"] else "")) for r in fail)
        seq = Counter((r["lang"], r["codes"]) for r in fail)
        out[scope] = {"n": len(sel), "failed": len(fail), "by_lang": dict(Counter(r["lang"] for r in fail)),
                      "by_lang_outcome_terminal_code": [{"lang": k[0], "outcome": k[1], "terminal_code": k[2], "n": v}
                                                        for k, v in sorted(by.items())],
                      "by_lang_code_sequence": [{"lang": k[0], "codes": k[1], "n": v} for k, v in sorted(seq.items())],
                      "paragraphs_by_lang": dict(Counter(r["lang"] for r in sel))}
    return out


# --------------------------------------------------------------------------- (3)
def valid_only(run: dict, weighted_label: str, intervals: bool) -> dict:
    S = run["S"]
    out1 = S["outcome"].get(1, {})
    valid = [p for p in S["pids"] if out1.get(p) in ev.SUCCESS]
    full = ev.counts_execution(S, S["pids"], 1)["per_art"]
    cond = ev.counts_execution(S, valid, 1)["per_art"]
    arts_all = sorted({S["art"][p] for p in S["pids"]}); arts_valid = sorted({S["art"][p] for p in valid})
    return {"run_id": run["run_id"], "weighting": weighted_label, "paragraphs": len(S["pids"]), "valid_first_execution": len(valid),
            "end_to_end": prf_block(full, arts_all, intervals), "valid_only": prf_block(cond, arts_valid, intervals)}


# --------------------------------------------------------------------------- (4)
def without_general(S: dict) -> dict:
    T = {"gold": {k: v for k, v in S["gold"].items() if k[1] != "general"},
         "pred": {1: {k: v for k, v in S["pred"].get(1, {}).items() if k[1] != "general"}}, "w": S["w"], "art": S["art"]}
    out = {}
    for pop, ps in ra.pops_of(S, S["pids"]).items():
        arts = sorted({S["art"][p] for p in ps})
        out[pop] = {"without_general": prf_block(ev.counts_execution(T, ps, 1)["per_art"], arts),
                    "registered": prf_block(ev.counts_execution(S, ps, 1)["per_art"], arts),
                    "general_rows_reference": sum(1 for k in S["gold"] if k[1] == "general" and k[0] in set(ps)),
                    "general_rows_model": sum(1 for k in S["pred"].get(1, {}) if k[1] == "general" and k[0] in set(ps))}
    return out


# --------------------------------------------------------------------------- (5)
def element_intervals(S: dict, m: dict) -> dict:
    per = ev.counts_execution(S, S["pids"], 1)["per_art"]
    arts = sorted({S["art"][p] for p in S["pids"]})

    def stat(cls, mult):
        tp = fp = fn = 0.0
        for a, mu in mult.items():
            v = per[a]["element"].get(cls)
            if v:
                tp += mu * v[0]; fp += mu * v[1]; fn += mu * v[2]
        return f1(tp, fp, fn)

    rng = random.Random(SEED)
    mults = [Counter(rng.choice(arts) for _ in arts) for _ in range(B)]
    base = {a: 1 for a in arts}
    out = {}
    for cls in ev.ELEMENTS:
        support = sum(per[a]["element"][cls][3] + per[a]["element"][cls][5] for a in arts if cls in per[a]["element"])
        draws = [stat(cls, mu) for mu in mults]
        out[cls] = {"f1": stat(cls, base), "iv": pct(draws), "reference_tuples": int(support),
                    "undefined_draws": sum(1 for d in draws if d is None)}
    return out


# --------------------------------------------------------------------------- (6)
def adjustment_second_coding(inp_reg: dict, S: dict, m: dict) -> dict:
    a2 = dsl.load_run(RUNS / f"{ra.REG}.json", "registered primary", "A2_sample_manifest")
    dsl.require_registered(a2["rec"])
    comp = dsl.load_run(RUNS / f"{COMPLETION}.json", sc.FRAME_COMPLETION_STATUS, sc.FRAME_COMPLETION_KIND)
    a3 = dsl.load_run(RUNS / f"{A3_RUN}.json", sc.A3_STATUS, sc.A3_KIND)
    frame = json.loads(Path(sc.FRAME_MANIFEST).read_text(encoding="utf-8"))
    if sc.canonical_digest(frame) != frame.get("digest") or a2["manifest"].get("frame_manifest_digest") != frame["digest"]:
        raise CheckError("the frame manifest on disk is not the one the sample was drawn from")
    first = dsl.estimate(a2, comp, a3, inp_reg["gold1"], inp_reg["gold2"], frame, inp_reg["frame_of"], ra.S_MIN, B, SEED, m)
    stored = json.loads((RUNS / "tables" / "dsl" / "dsl_prevalence.json").read_text(encoding="utf-8"))["classes"]
    for c, v in first["classes"].items():
        if abs(v["dsl_pp"] - stored[c]["dsl_pp"]) > 1e-9 or abs(v["se_design_pp"] - stored[c]["se_design_pp"]) > 1e-9:
            raise CheckError(f"REFUSING: the adjusted prevalence of {c} does not reproduce the stored estimate")
    # the second coding of all 120 paragraphs: route-B re-code (40) and second wave (80), bound as in review_analyses
    files = m["reliability"]["inputs"]["files"]
    p1 = ev.read_csv(ROOT / files["pass1_b"]["path"]) + ev.read_csv(LABELS / ra.WAVE2[0])
    p2 = ev.read_csv(ROOT / files["pass2_b"]["path"]) + ev.read_csv(LABELS / ra.WAVE2[1])
    for k in ("pass1_b", "pass2_b"):
        if ra.sha(ROOT / files[k]["path"]) != files[k]["sha256"]:
            raise CheckError("REFUSING: the route-B re-code file is not the one the registered table 9 binds")
    if ra.wave2_inputs(inp_reg, S) is None:
        raise CheckError("REFUSING: the second re-code wave is not finalised")
    if {r["paragraph_id"] for r in p1} != set(S["pids"]):
        raise CheckError("REFUSING: the second coding does not cover exactly the 120 sampled paragraphs")
    second = dsl.estimate(a2, comp, a3, p1, p2, frame, inp_reg["frame_of"], ra.S_MIN, B, SEED, m)
    out = {}
    for c, v in first["classes"].items():
        if not c.startswith("element:") or not v["reported"]:
            continue
        w = second["classes"].get(c)
        if w is None:
            continue
        out[c.split(":")[1]] = {
            "model_only_pp": v["naive_pp"],
            "first": {"adjusted_pp": v["dsl_pp"], "ci": [v["ci_design_lo_pp"], v["ci_design_hi_pp"]], "sample_only_pp": v["reference_ht_pp"],
                      "support": v["gold_support"]},
            "second": {"adjusted_pp": w["dsl_pp"], "ci": [w["ci_design_lo_pp"], w["ci_design_hi_pp"]], "sample_only_pp": w["reference_ht_pp"],
                       "support": w["gold_support"]},
            "second_minus_first_pp": w["dsl_pp"] - v["dsl_pp"]}
    n_valid = first["N"] - sum(first["failed_executions"].values())
    out["_frame"] = {"N": first["N"], "failed_executions": first["failed_executions"], "valid_records": n_valid,
                     "F2b_model_only_pp_all": first["classes"]["element:F2b"]["naive_pp"],
                     "F2b_model_only_pp_valid_records": first["classes"]["element:F2b"]["naive_pp"] * first["N"] / n_valid}
    return out


# --------------------------------------------------------------------------- (7)
def class_sets() -> dict:
    cmf = json.loads((RUNS / f"{ra.REG}_class_manifest.json").read_text(encoding="utf-8"))
    out = {}
    for table, v in cmf.items():
        if not isinstance(v, dict) or "de" not in v:
            continue
        out[table] = {"pooled": len(v.get("pooled", [])), "de": len(v["de"]), "en": len(v["en"]),
                      "common_support": len(v.get("common_support", [])),
                      "only_de": sorted(set(v["de"]) - set(v["en"])), "only_en": sorted(set(v["en"]) - set(v["de"]))}
    return out


# --------------------------------------------------------------------------- output
def f3(x): return "---" if x is None else f"{x:.3f}"
def sg(x): return "---" if x is None else f"${'+' if x >= 0 else '-'}{abs(x):.3f}$"
def iv(t): return "" if not t or t[0] is None else f" [{t[0]:.3f}, {t[1]:.3f}]"
def siv(t): return "" if not t or t[0] is None else f" [${'+' if t[0] >= 0 else '-'}{abs(t[0]):.3f}$, ${'+' if t[1] >= 0 else '-'}{abs(t[1]):.3f}$]"
def p1(x): return f"{x:.1f}"


# A block heading spans the four columns and wraps within their width (50 + 24 + 36 + 40 mm plus the six inner column
# separations), so that it no longer widens the last column into the margin (2026-11-12, M22 page review, finding 71)
BLOCK = r"\multicolumn{4}{@{}>{\raggedright\arraybackslash}p{\dimexpr150mm+6\tabcolsep\relax}@{}}"


def table(res: dict) -> str:
    L = ["% Generated by tools/pipeline/review_checks_4o.py from tools/runs/tables/review_checks_4o/results.json (specification §4o). Do not edit by hand.",
         r"\begingroup\scriptsize", r"\setlength{\tabcolsep}{4pt}",
         r"\begin{longtable}{@{}>{\raggedright\arraybackslash}p{50mm}>{\raggedright\arraybackslash}p{24mm}>{\raggedright\arraybackslash}p{36mm}>{\raggedright\arraybackslash}p{40mm}@{}}",
         r"\caption[Checks on existing data declared on 10 November 2026]{Checks on existing data declared on 10 November 2026 (specification \S4o), all descriptive and on inspected samples. "
         r"The second coding combines the registered 40-paragraph and the supplementary 80-paragraph re-code; analyses using it are exploratory. "
         r"Evaluation sample: design-weighted; fresh census: unweighted. Brackets: 2.5--97.5 percentile intervals over 2{,}000 article resamples; "
         r"for the adjusted shares, approximate design-based 95\% intervals. A valid first execution ended with technologies present or off topic.}\label{tab:checks_4o} \\",
         r"\toprule", r"\endfirsthead", r"\toprule", r"\endhead", r"\bottomrule", r"\endfoot"]
    lc = res["language_contrast"]
    L += [BLOCK + r"{\emph{(1) Language contrast, registered execution 1 (28 German and 28 English articles, resampled within language)}} \\",
          r"\textbf{Score} & \textbf{German} & \textbf{English} & \textbf{German minus English} \\", r"\midrule"]
    for k, name in (("element", "Frame elements, micro-F1"), ("technology", "Technology detection, micro-F1")):
        L.append(f"{name} & {f3(lc[k]['de'])} & {f3(lc[k]['en'])} & {sg(lc[k]['diff_de_minus_en'])}{siv(lc[k]['iv'])} \\\\")
    L += [r"\addlinespace", BLOCK + r"{\emph{(2) Terminal failures (no valid record after the one retry), by language}} \\",
          r"\textbf{Run and scope} & \textbf{German} & \textbf{English} & \textbf{Validator codes of the failed attempts} \\", r"\midrule"]
    for rid, name in ((ra.REG, "Registered run"), (A3_RUN, "Fresh-census run")):
        for scope, sname in (("execution_1", "execution 1"), ("all_executions", "all executions")):
            d = res["failures"][rid][scope]
            codes = Counter()
            for x in d["by_lang_code_sequence"]:
                for c_ in x["codes"].split("|"):
                    codes[c_.replace("_", r"\_")] += x["n"]
            pl = d["paragraphs_by_lang"]
            L.append(f"{name}, {sname} & {d['by_lang'].get('de', 0)} of {pl.get('de', 0)} & {d['by_lang'].get('en', 0)} of {pl.get('en', 0)} & "
                     + "; ".join(f"\\texttt{{{c_}}} {n}" for c_, n in sorted(codes.items())) + r" \\")
    L += [r"\addlinespace", BLOCK + r"{\emph{(3) Element agreement end to end and on paragraphs with a valid first execution: precision, recall (gap)}} \\",
          r"\textbf{Configuration, sample} & \textbf{Valid} & \textbf{End to end} & \textbf{Valid only} \\", r"\midrule"]
    for row in res["valid_only"]:
        e, v = row["end_to_end"], row["valid_only"]
        L.append(f"{row['name']}, {row['sample_label']} & {row['valid_first_execution']} of {row['paragraphs']} & "
                 f"{f3(e['precision'])}, {f3(e['recall'])} ({sg(e['gap'])}) & {f3(v['precision'])}, {f3(v['recall'])} ({sg(v['gap'])}) \\\\")
    wg = res["without_general"]["pooled"]
    # panels (4)-(6): the reference counts and the per-element F1 are first-coding figures (2026-11-12, M22 sweep, repair 5)
    L += [r"\addlinespace", BLOCK + r"{\emph{(4) Registered execution 1 without the general category's rows (pooled)}} \\",
          r"\textbf{Rows scored} & \textbf{Precision} & \textbf{Recall} & \textbf{F1} \\", r"\midrule"]
    for key, name in (("registered", "All rows (registered)"), ("without_general", f"Without general ({wg['general_rows_reference']} first-coding, {wg['general_rows_model']} model rows dropped)")):
        d = wg[key]
        L.append(f"{name} & {f3(d['precision'])}{iv(d['precision_iv'])} & {f3(d['recall'])}{iv(d['recall_iv'])} & {f3(d['f1'])}{iv(d['f1_iv'])} \\\\")
    L += [r"\addlinespace", BLOCK + r"{\emph{(5) Per-element F1 with article intervals and (6) adjusted share under each coding as reference, in \% of the eligible frame}} \\",
          r"\textbf{Element (first-coding tuples)} & \textbf{F1, first coding} & \textbf{Adjusted, first coding} & \textbf{Adjusted, second coding} \\", r"\midrule"]
    labels = {e["id"]: e["label"] for e in res["element_labels"]}
    for cls in ev.ELEMENTS:
        a = res["adjustment_second_coding"].get(cls)
        if a is None:
            continue
        ei = res["element_intervals"][cls]
        L.append(f"{labels[cls]} ({ei['reference_tuples']}) & {f3(ei['f1'])}{iv(ei['iv'])} & {p1(a['first']['adjusted_pp'])} [{p1(a['first']['ci'][0])}, {p1(a['first']['ci'][1])}] & "
                 f"{p1(a['second']['adjusted_pp'])} [{p1(a['second']['ci'][0])}, {p1(a['second']['ci'][1])}] \\\\")
    L += [r"\end{longtable}", r"\endgroup", ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    inp, S, m = ra.load_registered()
    reg_f1 = ev.micro(ev.counts_execution(S, S["pids"], 1)["per_art"], "element", {x: 1 for x in {S["art"][p] for p in S["pids"]}}, True)
    if abs(reg_f1 - m["point"]["pooled"]["element"]["micro_f1_weighted"]) > 1e-9:
        raise CheckError("REFUSING: the registered element micro-F1 is not reproduced")
    comp = json.loads((RUNS / "tables" / "model_comparison" / "comparison.json").read_text(encoding="utf-8"))
    rows = []
    for sample, label, wl in (("A2", "evaluation", "design-weighted"), ("A3", "census", "unweighted")):
        for r in comp["rows"][sample]:
            if "request" in r["model"]:
                continue                                           # request controls are not main configurations
            run = cm.load(r["run_id"], sample)
            d = valid_only(run, wl, intervals=(r["run_id"] in (ra.REG, A3_RUN)))
            d.update({"name": r["model"].replace("Inception ", ""), "sample": sample, "sample_label": label})
            rows.append(d)
    cb = json.loads((RUNS / f"{ra.REG}.json").read_text(encoding="utf-8"))
    res = {"specification": "§4o, declared 2026-11-10 before computation", "B": B, "seed": SEED,
           "language_contrast": language_contrast(S),
           "failures": {ra.REG: failures(ra.REG), A3_RUN: failures(A3_RUN)},
           "valid_only": rows,
           "without_general": without_general(S),
           "element_intervals": element_intervals(S, m),
           "adjustment_second_coding": adjustment_second_coding(inp, S, m),
           "class_sets": class_sets(),
           "element_labels": [{"id": e, "label": fg.ELEMENT_SHORT[e]} for e in ev.ELEMENTS]}
    js = json.dumps(res, indent=1, ensure_ascii=False, default=str) + "\n"
    tex = table(res)
    if a.check:
        ok = (OUT / "results.json").exists() and (OUT / "results.json").read_text(encoding="utf-8") == js and TEX.read_text(encoding="utf-8") == tex
        print("  same    " if ok else "  DIFFERS ", TEX.relative_to(ROOT)); sys.exit(0 if ok else 1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(js, encoding="utf-8"); TEX.write_text(tex, encoding="utf-8")
    print(f"wrote {OUT / 'results.json'} and {TEX}")


if __name__ == "__main__":
    try:
        main()
    except (CheckError, ra.AnalysisError, cm.CompareError, dsl.DslError) as exc:
        sys.exit(str(exc))
