#!/usr/bin/env python3
"""The §4l (5) comparison: the context-parity variant v1.0C against the registered instrument v1.0 (pre-analysis
specification §4l (5), declared 2026-11-06 before any call of it; the author's decision D37).

v1.0C is the registered request with the coding interface's headline, outlet and language before the unchanged
paragraph block; the context lines are the only difference of instrument. A2: v1.0C (k = 1) against execution 1 of the
registered run `a2_sonnet_20261013`; A3: v1.0C (k = 1) against execution 1 of the fresh-sample run `a3_sonnet_20261019`;
the comparators' executions 2 and 3 against their execution 1 as the noise reference. Every run is loaded through the
evaluator's verified path and its metrics must be the ones the evaluator wrote for that record.

Decision rule (§4l (5), fixed before any call): primary quantity element recall; precision, micro-F1 and the
precision-recall gap beside it, and technology micro-F1; per sample higher / lower if the 95 % paired article-resampling
interval (B = 2000, seed 20260905, common resamples) lies entirely above / below zero; overall only if both samples
agree, otherwise no consistent difference.

    .venv/bin/python3 tools/pipeline/compare_context.py [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compare_models as cm                                         # noqa: E402
import evaluate as ev                                               # noqa: E402
import figures as fg                                                # noqa: E402
import sample_contract as sc                                        # noqa: E402

ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
OUT = RUNS / "tables" / "context_variant"
PAIRS = {"A2": ("ctx_v10C_a2_20261106", sc.CONTEXT_COMPARATORS["A2"]), "A3": ("ctx_v10C_a3_20261106", sc.CONTEXT_COMPARATORS["A3"])}
WANT_STATUS = {"ctx_v10C_a2_20261106": sc.CONTEXT_STATUS, "ctx_v10C_a3_20261106": sc.CONTEXT_STATUS,
               sc.CONTEXT_COMPARATORS["A2"]: "registered primary", sc.CONTEXT_COMPARATORS["A3"]: sc.A3_STATUS}
STATS = ("element_recall", "element_precision", "element", "technology")
OK = ("technologies_present", "off_topic")
INVALID = ("no_tool_use", "truncated", "schema_invalid", "contract_invalid", "unexpected_stop_reason", "unexpected_content")


def load(run_id: str, sample: str) -> dict:
    cfg = cm.SAMPLES[sample]
    rp = RUNS / f"{run_id}.json"
    ns = argparse.Namespace(run_record=str(rp), manifest=str(cfg["manifest"]), restrict_split=None,
                            gold_pass1=str(cfg["gold1"]), gold_pass2=str(cfg["gold2"]), dev=False, gold_manifest=None,
                            skip_provenance=False, threshold=cfg["threshold"], reliability=None)
    try:
        inp = ev.load_inputs(ns)
    except ev.EvalError as exc:
        raise cm.CompareError(f"{run_id}: {exc}") from None
    rec = inp["rec"]
    status = ev.analysis_status_of(rec, manifest_kind=(inp.get("manifest") or {}).get("kind"))
    want = WANT_STATUS[run_id]
    if status != want:
        raise cm.CompareError(f"{run_id}: status {status!r}, expected {want!r}")
    mp = RUNS / "tables" / run_id / "metrics.json"
    if not mp.exists():
        raise cm.CompareError(f"{run_id}: no metrics at {mp}; run evaluate.py first")
    m = json.loads(mp.read_text(encoding="utf-8"))
    prov = inp["provenance"]
    if (m.get("B") != cm.B or m.get("seed") != cm.SEED or m.get("dev") or m.get("threshold") != cfg["threshold"]
            or (m.get("inputs") or {}).get("run_record_sha256") != prov["run_record_sha256"]
            or (m.get("inputs") or {}).get("reference") != prov["reference"] or (m.get("analysis_status") or ("development" if m.get("dev") else "registered primary")) != status):
        raise cm.CompareError(f"{run_id}: the metrics file was not computed from this run record, reference and status")
    S = ev.build(inp)
    return {"run_id": run_id, "sample": sample, "rec": rec, "m": m, "S": S, "status": status,
            "counts": ev.counts_execution(S, S["pids"], 1), "metrics_sha256": cm.sha(mp),
            "run_record_sha256": prov["run_record_sha256"]}


def restricted(run: dict, pids: list[str], k: int = 1) -> dict:
    return {**run, "counts": ev.counts_execution(run["S"], pids, k), "S": {**run["S"], "pids": pids}}


def verdict(pv: dict) -> str:
    lo, hi = pv["lo"], pv["hi"]
    if lo is None:
        return "undefined"
    return "higher" if lo > 0 else "lower" if hi < 0 else "no distinguishable difference"


def describe(run: dict) -> dict:
    S, rec = run["S"], run["rec"]
    p, r = cm.element_pr(run["counts"])
    out1 = {pid: S["outcome"][1].get(pid, "missing") for pid in S["pids"]}
    calls = [json.loads(l) for l in (RUNS / f"{run['run_id']}_calls.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    fin = [c for c in calls if c.get("type") == "attempt_finished"]
    firsts = {}
    for c in fin:
        firsts.setdefault((c["paragraph_id"], c["run_index"]), c)
    first1 = [v for (pid, k), v in firsts.items() if k == 1]
    out_tok = [((c.get("usage") or {}).get("output_tokens") or 0) for c in fin]
    return {"run_id": run["run_id"], "instrument": rec.get("instrument"), "repeats": rec.get("repeats"),
            "element_f1": run["m"]["point"]["pooled"]["element"]["micro_f1_weighted"],
            "element_precision": p, "element_recall": r,
            "technology_f1": run["m"]["point"]["pooled"]["technology"]["micro_f1_weighted"],
            "invalid_first_responses": sum(c.get("code") in INVALID for c in first1),
            "terminal_failures": sum(o not in OK for o in out1.values()), "paragraphs": len(S["pids"]),
            "max_output_tokens": max(out_tok) if out_tok else None,
            "truncated": sum(c.get("code") == "truncated" for c in fin),
            "cost_usd": (rec.get("cost") or {}).get("estimated_usd"),
            "metrics_sha256": run["metrics_sha256"], "run_record_sha256": run["run_record_sha256"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    res = {"declared": sc.CONTEXT_AUTHORITY, "B": cm.B, "seed": cm.SEED, "samples": {}, "verdicts": {}}
    for sample, (f_id, r_id) in PAIRS.items():
        f, r = load(f_id, sample), load(r_id, sample)
        pv = cm.paired(f, r, stats=STATS)
        both = [pid for pid in f["S"]["pids"] if f["S"]["outcome"][1].get(pid) in OK and r["S"]["outcome"][1].get(pid) in OK]
        pv_both = cm.paired(restricted(f, both), restricted(r, both), stats=STATS)
        noise = {}
        if r["rec"].get("repeats", 1) >= 3:          # both comparators: executions 2 and 3 against 1
            for k in (2, 3):
                rk = {**r, "counts": ev.counts_execution(r["S"], r["S"]["pids"], k), "run_id": f"{r_id}#exec{k}"}
                noise[f"exec{k}_vs_exec1"] = cm.paired(rk, r, stats=STATS)
        res["samples"][sample] = {"context": describe(f), "comparator": describe(r), "paired": pv,
                                  "pr_gap_context": cm.pr_gap(f), "pr_gap_comparator": cm.pr_gap(r),
                                  "paired_both_valid": pv_both, "both_valid_paragraphs": len(both),
                                  "comparator_noise": noise,
                                  "verdicts": {s_: verdict(pv[s_]) for s_ in STATS}}
    for s_ in STATS:
        v = {res["samples"][x]["verdicts"][s_] for x in PAIRS}
        res["verdicts"][s_] = next(iter(v)) if len(v) == 1 and next(iter(v)) in ("higher", "lower") else "no consistent difference"
    text = json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n"
    tab = tex_table(res)
    files = {OUT / "context_comparison.json": text, ROOT / "paper" / "tables" / "tab_context_variant.tex": tab}
    if a.check:
        stale = [p for p, t in files.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
        if stale:
            sys.exit("STALE: " + ", ".join(str(p.relative_to(ROOT)) for p in stale))
        print(f"  {len(files)} file(s) match")
    else:
        for p, t in files.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(t, encoding="utf-8")
            print(f"  wrote {p.relative_to(ROOT)}")
    for sample in PAIRS:
        x = res["samples"][sample]
        print(f"  {sample}: context R/P/F1 {x['context']['element_recall']:.3f}/{x['context']['element_precision']:.3f}/{x['context']['element_f1']:.3f}"
              f"  v1.0 {x['comparator']['element_recall']:.3f}/{x['comparator']['element_precision']:.3f}/{x['comparator']['element_f1']:.3f}")
        for s_ in STATS:
            d = x["paired"][s_]
            print(f"     {s_:<18} {d['diff']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}] -> {x['verdicts'][s_]}")
    print("  overall:", res["verdicts"])


def tex_table(res: dict) -> str:
    def f3(v):
        return "--" if v is None else f"{v:.3f}"

    def d3(d):
        return f"${d['diff']:+.3f}$ [${d['lo']:+.3f}$, ${d['hi']:+.3f}$]" if d.get("lo") is not None else "undefined"
    rows = []
    for sample, label in (("A2", "Registered evaluation sample A2 (120 paragraphs, design-weighted; execution 1 of each)"),
                          ("A3", "Fresh-sample census A3 (55 paragraphs; execution 1 of each)")):
        x = res["samples"][sample]
        rows.append(f"\\multicolumn{{5}}{{@{{}}l}}{{\\emph{{{label}}}}} \\\\")
        for key, name in (("context", "v1.0C (headline, outlet, language)"), ("comparator", "v1.0 (paragraph only)")):
            y = x[key]
            rows.append(" & ".join([name, f3(y["element_recall"]), f3(y["element_precision"]), f3(y["element_f1"]),
                                    f"{y['invalid_first_responses']} / {y['terminal_failures']}"]) + r" \\")
        rows.append(" & ".join(["Difference [interval]", d3(x["paired"]["element_recall"]), d3(x["paired"]["element_precision"]),
                                d3(x["paired"]["element"]), "--"]) + r" \\")
        rows.append(r"\addlinespace")
    rows = rows[:-1]
    header = r"\textbf{Configuration} & \textbf{Element recall} & \textbf{Element precision} & \textbf{Element micro-F1} & \textbf{Invalid first / failed}"
    cap = ("Context parity (specification §4l (5)): v1.0C gives the model the headline, outlet and language the coder saw "
           "before the paragraph; v1.0 gives the paragraph alone. Same model, prompt, decoding and retry policy. "
           "Differences are context minus paragraph only under common article resamples ($B = 2{,}000$, seed 20260905). "
           "The comparator runs date from October 2026; descriptive, nothing is selected.")
    head = ("% Generated by tools/pipeline/compare_context.py from tools/runs/tables/context_variant/context_comparison.json "
            "(specification §4l (5)). Do not edit by hand.\n")
    L = ">{\\raggedright\\arraybackslash}p{%smm}"
    colspec = "@{}" + L % 40 + L % 28 + L % 28 + L % 28 + L % 22 + "@{}"
    return head + fg.table_env(cap, "tab:context_variant", colspec, header, rows, [], size=r"\footnotesize",
                               short="Context parity")


if __name__ == "__main__":
    main()
