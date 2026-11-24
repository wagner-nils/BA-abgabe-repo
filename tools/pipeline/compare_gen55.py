#!/usr/bin/env python3
"""Opus 5.5 and the precision-recall gap (pre-analysis specification §4m, declared 2026-11-08 before any run of it on
study text; the author's request to examine precision and recall further and to report the findings in the thesis).

Configurations (every one under the registered instrument v1.0 otherwise byte for byte): `opus55` (Opus 5.5 under the
5.5 request: free choice of the one pinned tool, adaptive thinking at effort low, 8,192 tokens, no temperature; k = 3),
`opus55_high` (the same at effort high; k = 1), `opus5_req55` (Opus 5 under exactly the 5.5 request, the configuration
control; k = 1) and `opus5_rep` (Opus 5 under the §4k request again; k = 3), beside the Sonnet 4.6 reference runs, the
§4f Opus 4.6 runs and the §4k Opus 5 runs on the registered A2 sample and the A3 census.

  (1) per configuration, execution one: element micro-F1, precision, recall, the precision-minus-recall gap with its
      article-resampling interval, and the weighted count of marked element tuples against the reference's, pooled and
      per language;
  (2) the declared paired comparisons (same request, as run, request, effort) with element and technology micro-F1 and
      element precision and recall, under the rule of §4k;
  (3) repeats (`opus55`, `opus5_rep`): precision, recall, gap and element micro-F1 per execution; executions two and
      three against execution one; the union and the intersection of the three executions; execution one of
      `opus5_rep` against the §4k run a week earlier;
  (4) misses by cause (failed execution, row missing, element missing in a present row) for execution one of every
      configuration, pooled and per language -- the shared routine of §4l (3), which refuses unless it reproduces each
      run's element false negatives.

Descriptive, test-set reuse, nothing selected; the registered run and every registered figure stay as they are. Every
run is loaded through the evaluator's verified path (`compare_models.load`).

    .venv/bin/python3 tools/pipeline/compare_gen55.py [--check]

Writes tools/runs/tables/model_comparison_gen55/{gen55_comparison.json, gen55_verdicts.csv} and the appendix table
paper/tables/tab_opus55.tex.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compare_models as cm                                         # noqa: E402
import evaluate as ev                                               # noqa: E402
import review_analyses as ra                                        # noqa: E402
import sample_contract as sc                                        # noqa: E402

ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
OUT = RUNS / "tables" / "model_comparison_gen55"
PAPER = ROOT / "paper" / "tables"
B, SEED = cm.B, cm.SEED

# the runs this analysis reads: the two references and the earlier comparison runs it pairs with, then §4m
FIXED = {"A2": {"sonnet": "a2_sonnet_20261013", "opus": "mc_opus46_a2_20261019", "opus5": "mc_opus5_a2_20261101"},
         "A3": {"sonnet": "a3_sonnet_20261019", "opus": "mc_opus46_a3_20261019", "opus5": "mc_opus5_a3_20261101"}}
GEN55_RUN = {"opus55": "mc_opus55_{s}_20261108r2", "opus55_high": "mc_opus55_high_{s}_20261108r2",
             "opus5_req55": "mc_opus5_req55_{s}_20261108r2", "opus5_rep": "mc_opus5_rep_{s}_20261108"}
# runs of §4m that exist only as journals and are not used, each with the reason recorded before any figure was computed
SUPERSEDED = {
    **{f"mc_{k}_{s}_20261108": (
        "started 2026-11-08 under commit bffc44e; the runner's statement of the adaptive-thinking request was a str.format "
        "template with single braces, so every thinking configuration crashed while writing its run record after its last "
        "call (mc_opus5_req55_a3_20261108, 55 paragraphs complete) and the others were stopped before completion once the "
        "defect was found; no run record exists and no figure was computed; fixed in 5ed4cdc and re-run under r2, never "
        "resumed") for k in ("opus55", "opus55_high", "opus5_req55") for s in ("a3",)},
    "mc_opus5_req55_a2_20261108": "as the A3 runs of 2026-11-08 above: stopped after its first calls; re-run under r2",
}
ORDER = ("sonnet", "opus", "opus5", "opus5_rep", "opus5_req55", "opus55", "opus55_high")
LABEL = {"sonnet": "Claude Sonnet 4.6 (reference run)", "opus": "Claude Opus 4.6 (§4f)",
         "opus5": "Claude Opus 5 (§4k, 2026-11-01)", "opus5_rep": "Claude Opus 5, repeated (§4k request)",
         "opus5_req55": "Claude Opus 5 under the 5.5 request (control)", "opus55": "Claude Opus 5.5, effort low",
         "opus55_high": "Claude Opus 5.5, effort high"}
# §4m: the declared paired comparisons (run, reference), fixed before any run
PAIRS = {
    "same_request": (("opus55", "opus5_req55"),),
    "as_run": (("opus55", "opus5"), ("opus55", "sonnet"), ("opus55", "opus")),
    "request": (("opus5_req55", "opus5"),),
    "effort": (("opus55_high", "opus55"),),
}
REPEATED = ("opus55", "opus5_rep")
CAUSE_ORDER = ("failed_execution", "row_missing", "element_missing")      # the column order of panel (c)
assert set(CAUSE_ORDER) == set(ra.CAUSES), ra.CAUSES
STATS = ("element", "technology", "element_precision", "element_recall")


def run_ids(sample: str) -> dict:
    s = sample.lower()
    return {**FIXED[sample], **{k: GEN55_RUN[k].format(s=s) for k in sc.GEN55_ROSTER}}


def load_all(sample: str) -> dict:
    out = {}
    for key, rid in run_ids(sample).items():
        run = cm.load(rid, sample)
        want = sc.REGISTERED_MODEL_KEY if key == "sonnet" else key
        if run["rec"].get("model_key") != want:
            raise cm.CompareError(f"{rid}: model key {run['rec'].get('model_key')!r}, expected {want!r}")
        if key in sc.GEN55_ROSTER and int(run["rec"]["repeats"]) != sc.GEN55_REPEATS[key]:
            raise cm.CompareError(f"{rid}: k = {run['rec']['repeats']}, declared {sc.GEN55_REPEATS[key]}")
        out[key] = run
    return out


def _sums(counts: dict) -> tuple[float, float, float]:
    tp = fp = fn = 0.0
    for v in counts["C"]["element"].values():
        tp += v[0]; fp += v[1]; fn += v[2]
    return tp, fp, fn


def prf(tp, fp, fn) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
    return {"precision": p, "recall": r, "f1": f, "gap": None if p is None or r is None else p - r,
            "marked_w": tp + fp, "reference_w": tp + fn}


def pops(S: dict) -> dict:
    if not set(S["lang"].values()) <= {"de", "en"}:
        raise cm.CompareError(f"unexpected language codes {sorted(set(S['lang'].values()))}")
    return {"pooled": S["pids"], **{L: [p for p in S["pids"] if S["lang"][p] == L] for L in ("de", "en")}}


def with_execution(run: dict, r: int) -> dict:
    """The run with its execution `r` in the place of execution one (for the gap interval of a repeat)."""
    return {**run, "counts": ev.counts_execution(run["S"], run["S"]["pids"], r)}


def config_row(run: dict) -> dict:
    S = run["S"]
    out1 = Counter(S["outcome"][1].get(p, "missing") for p in S["pids"])
    row = {"run_id": run["run_id"], "model_key": run["rec"].get("model_key"), "repeats": int(run["rec"]["repeats"]),
           "model_id": run["rec"].get("model_id"), "inference_config": run["rec"].get("inference_config"),
           "thinking": run["rec"].get("thinking"),
           "attempt_verdict_codes": (run["rec"].get("totals") or {}).get("verdict_codes"),
           "output_tokens": (run["rec"].get("totals") or {}).get("output_tokens"),
           "calls": (run["rec"].get("totals") or {}).get("calls"),
           "cost_usd": (run["rec"].get("cost") or {}).get("estimated_usd"),
           "execution1_failed": sum(n for o, n in out1.items() if o not in cm.SUCCESS),
           "element_f1_iv": [((run["m"]["resample"]["pooled"].get("element.micro_f1_weighted") or {}).get(x)) for x in ("lo", "hi")],
           "technology_f1": run["m"]["point"]["pooled"]["technology"]["micro_f1_weighted"],
           "gap_interval": cm.pr_gap(run), "by_population": {}}
    for pop, ps in pops(S).items():
        row["by_population"][pop] = prf(*_sums(ev.counts_execution(S, ps, 1)))
    if not same_counts(against_gold(S, tuples(S, 1)), row["by_population"]["pooled"]):
        raise cm.CompareError(f"{run['run_id']}: execution one's tuple set does not reproduce the evaluator's counts")
    if abs(row["by_population"]["pooled"]["f1"] - run["m"]["point"]["pooled"]["element"]["micro_f1_weighted"]) > 1e-9:
        raise cm.CompareError(f"{run['run_id']}: element micro-F1 does not reproduce the evaluator's")
    return row


def tuples(S: dict, r: int) -> dict:
    return {key: frozenset(rel.elements) for key, rel in S["pred"].get(r, {}).items()}


def agreement(S: dict, a: dict, b: dict) -> dict:
    """Element micro-F1 of the tuple set `a` against `b` (b as reference), design-weighted."""
    tp = fp = fn = 0.0
    for key in sorted(set(a) | set(b)):          # sorted: a set's order varies with the hash seed, and so would the sums
        wt = S["w"][key[0]]
        ea, eb = a.get(key, frozenset()), b.get(key, frozenset())
        tp += wt * len(ea & eb); fp += wt * len(ea - eb); fn += wt * len(eb - ea)
    return prf(tp, fp, fn)


def against_gold(S: dict, pred: dict, pids=None) -> dict:
    keep = set(S["pids"] if pids is None else pids)
    gold = {k: frozenset(g.elements) for k, g in S["gold"].items() if k[0] in keep}
    return agreement(S, {k: v for k, v in pred.items() if k[0] in keep}, gold)


def same_counts(a: dict, b: dict) -> bool:
    """Precision and recall, not only F1 (review 2026-11-08: F1 is symmetric in false positives and negatives)."""
    return all(abs(a[f] - b[f]) <= 1e-9 for f in ("marked_w", "reference_w", "f1"))


def repeats(run: dict) -> dict:
    S = run["S"]
    k = S["k"]
    per = {}
    ex = {r: tuples(S, r) for r in range(1, k + 1)}
    for r in range(1, k + 1):
        c = ev.counts_execution(S, S["pids"], r)
        per[r] = {**prf(*_sums(c)), "gap_interval": cm.pr_gap(with_execution(run, r)),
                  "failed": sum(1 for p in S["pids"] if S["outcome"][r].get(p, "missing") not in cm.SUCCESS)}
        g = against_gold(S, ex[r])
        if not same_counts(g, per[r]):
            raise cm.CompareError(f"{run['run_id']}: execution {r}'s tuple set does not reproduce the evaluator's counts")
    keys = set().union(*ex.values())
    union = {key: frozenset().union(*(ex[r].get(key, frozenset()) for r in ex)) for key in keys}
    inter = {key: frozenset.intersection(*(ex[r].get(key, frozenset()) for r in ex)) for key in keys}
    u = against_gold(S, union)
    fc = ra.fn_causes(S, run["m"])["pooled"]                # the §4l (3) routine computes the union independently
    if abs(u["recall"] - fc["union_recall"]) > 1e-9 or abs(u["precision"] - fc["union_precision"]) > 1e-9:
        raise cm.CompareError(f"{run['run_id']}: the union of executions does not reproduce the §4l (3) union")
    ok_all = [p for p in S["pids"] if all(S["outcome"][r].get(p, "missing") in cm.SUCCESS for r in range(1, k + 1))]
    return {"k": k, "per_execution": per,
            "vs_execution1": {r: agreement(S, ex[r], ex[1]) for r in range(2, k + 1)},
            "union": u, "intersection": against_gold(S, inter),
            # descriptive sensitivity: only the paragraphs on which every execution returned a valid record
            "all_succeeded": {"paragraphs": len(ok_all), "execution1": against_gold(S, ex[1], ok_all),
                              "union": against_gold(S, union, ok_all), "intersection": against_gold(S, inter, ok_all)}}


def misses(run: dict) -> dict:
    return ra.fn_causes(run["S"], run["m"])


def compute() -> dict:
    res = {"declared": sc.GEN55_AUTHORITY, "B": B, "seed": SEED, "rule": (
        "per sample: better / worse if the 95 % paired article-resampling interval of the element micro-F1 difference "
        "lies entirely above / below zero, otherwise no distinguishable difference; overall better / worse only if both "
        "samples agree, otherwise no consistent difference (§4k's rule, as §4m declares)"),
           "configs": {}, "comparisons": [], "repeats": {}, "misses": {}, "drift_opus5": {}}
    loaded = {}
    for sample in ("A2", "A3"):
        L = loaded[sample] = load_all(sample)
        res["configs"][sample] = {k: config_row(L[k]) for k in ORDER}
        res["repeats"][sample] = {k: repeats(L[k]) for k in REPEATED}
        res["misses"][sample] = {k: misses(L[k]) for k in ORDER}
        res["drift_opus5"][sample] = cm.paired(L["opus5_rep"], L["opus5"], stats=STATS)
    for family, pairs in PAIRS.items():
        for key, ref in pairs:
            per = {}
            for sample in ("A2", "A3"):
                pv = cm.paired(loaded[sample][key], loaded[sample][ref], stats=STATS)
                per[sample] = {"verdict": cm.verdict(pv), "paired": pv}
            v = {per[s]["verdict"] for s in ("A2", "A3")}
            overall = next(iter(v)) if len(v) == 1 and next(iter(v)) in ("better", "worse") else "no consistent difference"
            res["comparisons"].append({"family": family, "model_key": key, "reference": ref, **per, "overall": overall})
    res["superseded"] = SUPERSEDED
    here = Path(__file__).resolve()
    res["inputs"] = {"compare_gen55_sha256": cm.sha(here), "compare_models_sha256": cm.sha(HERE / "compare_models.py"),
                     "evaluate_sha256": cm.sha(HERE / "evaluate.py"),
                     "runs": {s: {k: {"run_id": loaded[s][k]["run_id"], "run_record_sha256": loaded[s][k]["run_record_sha256"],
                                      "metrics_sha256": loaded[s][k]["metrics_sha256"]} for k in ORDER} for s in ("A2", "A3")}}
    return res


# ----------------------------------------------------------------------------------------------------------- output
def f3(x):
    return "--" if x is None else f"{x:.3f}"


def s3(x):
    """Signed, for the console."""
    return "--" if x is None else f"{x:+.3f}"


def m3(x):
    """Signed, for LaTeX: one math group per value (review 2026-11-08: the first version wrapped `$-$` in `$` again)."""
    return "--" if x is None else f"${x:+.3f}$"


def iv(d):
    return "" if d is None or d.get("lo") is None else f" [{s3(d['lo'])}, {s3(d['hi'])}]"


def csv_lines(res: dict) -> str:
    lines = ["family;model_key;reference;sample;verdict;overall;" + ";".join(f"diff_{s};lo_{s};hi_{s}" for s in STATS)]
    for c in res["comparisons"]:
        for s in ("A2", "A3"):
            cells = []
            for st in STATS:
                d = c[s]["paired"][st]
                cells += ["" if d[x] is None else f"{d[x]:.4f}" for x in ("diff", "lo", "hi")]
            lines.append(";".join([c["family"], c["model_key"], c["reference"], s, c[s]["verdict"], c["overall"]] + cells))
    return "\n".join(lines) + "\n"


# the takeaway caption, written after the runs from the figures of this table (2026-11-08); since 2026-11-12 (M22 sweep,
# repair 5 of the fourth review) the caption and the first note name the first coding, and the registered row is labelled
# "(registered)" as in Tables tab:model_comparison and tab:pr_gaps (page review, finding 56)
# Caption as reworded by the author in the thesis on 2026-11-16 (the generator follows the committed table).
CAPTION = (r"\textbf{On the evaluation sample, Opus~5's precision and recall remain close across three further executions. "
           r"Opus~5.5 marks fewer elements than the first coding there, and higher reasoning effort raises its precision and "
           r"lowers its recall in point estimate.} "
           r"First execution of each configuration, then the spread over three executions, then the missed elements by "
           r"cause.")


TEX_LABEL = {"sonnet": "Sonnet 4.6 (registered)", "opus": r"Opus 4.6 (\S4f)", "opus5": r"Opus 5 (\S4k, 1 Nov)",
             "opus5_rep": "Opus 5, repeated", "opus5_req55": "Opus 5, 5.5 request (control)",
             "opus55": "Opus 5.5, effort low", "opus55_high": "Opus 5.5, effort high"}


def tex_label(k: str) -> str:
    """The short row label of the appendix table (all rows are Claude models)."""
    return TEX_LABEL[k]


def failed_words(res: dict) -> str:
    parts = []
    for k in REPEATED:
        for s, name in (("A2", "evaluation sample"), ("A3", "census")):
            f = [res["repeats"][s][k]["per_execution"][r]["failed"] for r in sorted(res["repeats"][s][k]["per_execution"])]
            parts.append(f"{LABEL[k].replace('Claude ', '').replace(' (§4k request)', '')}, {name}: {', '.join(map(str, f))}")
    return "; ".join(parts)


def tex_table(res: dict) -> str:
    """The appendix table: (a) execution one of every configuration, (b) the three executions of Opus 5.5 and of the
    Opus 5 repeat with their union and intersection, (c) the missed reference elements of execution one by cause."""
    head = (f"% Generated by tools/pipeline/compare_gen55.py (sha256 {res['inputs']['compare_gen55_sha256'][:16]}) from "
            + ", ".join(v["run_id"] for s in ("A2", "A3") for v in res["inputs"]["runs"][s].values())
            + "; specification §4m, descriptive, not the registered result. Do not edit by hand.\n")
    C, R, M = res["configs"], res["repeats"], res["misses"]
    L = [r"\begin{table}[htbp]", r"\centering", r"\scriptsize", r"\setlength{\tabcolsep}{4.2pt}", r"\begin{threeparttable}",
         r"\caption[Opus 5.5, its control and repeated executions]{" + CAPTION + "}", r"\label{tab:opus55}",
         # all rows are Claude models; the caption says so through the takeaway, the labels drop the vendor
         r"\begin{tabular}{@{}>{\raggedright\arraybackslash}p{42mm}rrrrrrrr@{}}", r"\toprule",
         r" & \multicolumn{4}{c}{\textbf{Evaluation sample (120)}} & \multicolumn{4}{c}{\textbf{Fresh census (55)}} \\",
         r"\cmidrule(lr){2-5}\cmidrule(l){6-9}",
         r"\textbf{Configuration} & \textbf{F1} & \textbf{P} & \textbf{R} & \textbf{P$-$R} & \textbf{F1} & \textbf{P} & "
         r"\textbf{R} & \textbf{P$-$R} \\", r"\midrule",
         r"\multicolumn{9}{@{}l}{\textit{(a) First execution}} \\"]
    for k in ORDER:
        cells = []
        for s in ("A2", "A3"):
            p = C[s][k]["by_population"]["pooled"]
            cells += [f3(p["f1"]), f3(p["precision"]), f3(p["recall"]), m3(p["gap"])]
        L.append(tex_label(k) + " & " + " & ".join(cells) + r" \\")
    L += [r"\addlinespace", r"\multicolumn{9}{@{}l}{\textit{(b) Three executions, their union and their intersection}} \\"]
    for k in REPEATED:
        rows_b = [(f"execution {r}", R["A2"][k]["per_execution"][r], R["A3"][k]["per_execution"][r])
                  for r in sorted(R["A2"][k]["per_execution"])]
        rows_b += [(name, R["A2"][k][name], R["A3"][k][name]) for name in ("union", "intersection")]
        L.append(r"\multicolumn{9}{@{}l}{" + tex_label(k) + r"} \\")
        for name, a2, a3 in rows_b:
            cells = []
            for u in (a2, a3):
                cells += [f3(u["f1"]), f3(u["precision"]), f3(u["recall"]), m3(u["gap"])]
            L.append(r"\quad " + name + " & " + " & ".join(cells) + r" \\")
    L += [r"\addlinespace",
          r"\multicolumn{9}{@{}l}{\textit{(c) Missed reference elements of the first execution, share by cause}} \\",
          r" & \textbf{Missed} & \textbf{Failed} & \textbf{Row} & \textbf{Element} & \textbf{Missed} & \textbf{Failed} & "
          r"\textbf{Row} & \textbf{Element} \\"]
    for k in ORDER:
        cells = []
        for s in ("A2", "A3"):
            m = M[s][k]["pooled"]
            cells += [str(sum(m["fn_rows"].values()))]                     # a count of tuples; the shares are weighted
            cells += [f"{100 * (m['share_weighted'][c] or 0):.0f}\\%" for c in CAUSE_ORDER]
        L.append(tex_label(k) + " & " + " & ".join(cells) + r" \\")
    d2, d3 = res["drift_opus5"]["A2"]["element"], res["drift_opus5"]["A3"]["element"]
    notes = [
        r"F1, P, R: element micro-F1, precision and recall against each sample's first coding, design-weighted on the evaluation sample "
        r"and unweighted on the fresh census; P$-$R: precision minus recall. A failed execution counts as omissions, as "
        r"registered. Specification \S4m, run on 8 November 2026 on Bedrock EU inference profiles; descriptive, no "
        r"selection. The three configurations with thinking were re-run under fresh identifiers after a runner defect "
        r"had stopped their first runs before any run record was written; no figure was computed from those.",
        r"Requests: Opus 5 (\S4k) and its repeat without temperature and with thinking disabled, the tool forced; Opus 5.5 "
        r"and the control with the tool chosen freely and adaptive thinking at effort low (Opus 5.5 also at high), which "
        r"Opus 5.5 requires; Table~\ref{tab:model_config}.",
        r"(b) Union: an element counts as predicted on a row if any of the three executions predicts it there; "
        r"intersection: only if all three do. A failed execution predicts nothing, so it empties the intersection and adds "
        r"nothing to the union for its paragraph (failed executions per run: " + failed_words(res) + r"). The Opus 5 "
        r"repeat's first execution against the run of 1 November: element F1 " + f"{m3(d2['diff'])} [{m3(d2['lo'])}, "
        f"{m3(d2['hi'])}] on the evaluation sample and {m3(d3['diff'])} [{m3(d3['lo'])}, {m3(d3['hi'])}] on the census.",
        r"(c) Missed: the number of reference element tuples the first execution does not predict; the shares are "
        r"design-weighted on the evaluation sample; failed: the paragraph's execution ended without a valid record; row: the record has no row "
        r"for the reference technology; element: the row exists but lacks the element (Table~\ref{tab:fn_causes}).",
    ]
    L += [r"\bottomrule", r"\end{tabular}", r"\begin{tablenotes}\footnotesize"] + [f"\\item {n}" for n in notes]
    L += [r"\end{tablenotes}", r"\end{threeparttable}", r"\end{table}"]
    return head + "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-tex", action="store_true", help="write only tools/runs/tables/model_comparison_gen55/")
    a = ap.parse_args()
    try:
        res = compute()
    except (cm.CompareError, ra.AnalysisError) as exc:
        sys.exit(f"REFUSING: {exc}")
    files = {OUT / "gen55_comparison.json": json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n",
             OUT / "gen55_verdicts.csv": csv_lines(res)}
    if not a.no_tex:
        if CAPTION == "CAPTION":
            sys.exit("REFUSING: the takeaway caption of tab:opus55 is not set (pass --no-tex for the JSON only)")
        files[PAPER / "tab_opus55.tex"] = tex_table(res)
    stale = []
    for path, text in files.items():
        if a.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"  wrote {path.relative_to(ROOT)}")
    if a.check:
        if stale:
            sys.exit("STALE: " + ", ".join(str(p.relative_to(ROOT)) for p in stale))
        print(f"  {len(files)} file(s) match")
    for s in ("A2", "A3"):
        for k in ORDER:
            r = res["configs"][s][k]; p = r["by_population"]["pooled"]
            print(f"  {s} {LABEL[k]:<48} F1 {f3(p['f1'])}  P {f3(p['precision'])}  R {f3(p['recall'])}  gap {s3(p['gap'])}"
                  f"{iv(r['gap_interval'])}  failed {r['execution1_failed']}")
    for c in res["comparisons"]:
        print(f"  {c['family']:<13} {c['model_key']:<12} vs {c['reference']:<12} "
              + "  ".join(f"{s} {c[s]['verdict']} {s3(c[s]['paired']['element']['diff'])}{iv(c[s]['paired']['element'])}"
                          for s in ("A2", "A3")) + f"  -> {c['overall']}")


if __name__ == "__main__":
    main()
