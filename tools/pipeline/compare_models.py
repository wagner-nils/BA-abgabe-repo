#!/usr/bin/env python3
"""Robustness model comparison (pre-analysis specification §4f, declared 2026-10-19 on the author's decision).

The registered instrument v1.0 under further models, one execution per paragraph, on the registered A2 sample and on
the A3 census, compared with the Sonnet 4.6 runs on the same paragraphs (the registered run `a2_sonnet_20261013` and
the fresh-sample run `a3_sonnet_20261019`, execution one of each). Descriptive: no ranking, no selection; the
registered model and every registered figure are unchanged.

Every run is loaded through the evaluator's verified input path (`evaluate.load_inputs`: manifest contract, gate
certification and code provenance, journal reconciliation, threshold seal on A2, embargo evidence on A3), and its
metrics file must be the one the evaluator wrote for exactly that run record, reference and threshold. The paired
difference to Sonnet 4.6 is recomputed from the same per-article counts the evaluator uses, under one common article
resample for both runs (B = 2000, seed 20260905, the registered resampling seed): a descriptive interval, like every
interval of the thesis.

Since 2026-10-30 (the author's decision after the §4g runs) the thesis tables and figure also carry the two Mercury 3
configurations of the exploratory diffusion-model comparison (§4g), marked with a dagger, with their paired difference
to Opus 4.6 under the same common resamples in a table note; `--diffusion` writes §4g's verdict files instead.
`--gen5` (§4k, declared 2026-11-01) writes the Claude 5 benchmark beside, never into paper/: Sonnet 5 and Opus 5 under
the 5-series request, the two 4.6 models under the same request as configuration controls, and the declared paired
comparisons (same request, as run, decoding) with their verdicts. Since 2026-11-02 (the author's decision after the §4k
runs, controls ratified) the thesis tables and figure also carry Sonnet 5 and Opus 5, marked with a double dagger; the
controls stay out of the rows and enter a table note with the paired differences to them and to Opus 4.6, and every row
carries its element precision-minus-recall gap with its article-resampling interval (comparison.json).

    .venv/bin/python3 tools/pipeline/compare_models.py [--check] [--diffusion | --gen5]

Outputs: tools/runs/tables/model_comparison/{comparison.json, comparison.csv},
paper/tables/tab_model_comparison.tex, paper/tables/tab_model_comparison_elements.tex,
paper/images/fig_model_comparison.tex.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate as ev                                               # noqa: E402
import figures as fg                                                # noqa: E402
import sample_contract as sc                                        # noqa: E402

ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
LABELS = ROOT / "tools" / "annotation" / "labels"
PAPER = ROOT / "paper"
OUT = RUNS / "tables" / "model_comparison"
B, SEED = 2000, 20260905

SAMPLES = {
    "A2": {"manifest": LABELS / "A2_sample_manifest.json", "gold1": LABELS / "A2_pass1_v1.0.csv",
           "gold2": LABELS / "A2_pass2_v1.0.csv", "threshold": 5, "reference_run": "a2_sonnet_20261013",
           "label": "Evaluation sample (120 paragraphs, design-weighted)"},
    "A3": {"manifest": LABELS / "A3_sample_manifest.json", "gold1": LABELS / "A3_pass1_v1.0.csv",
           "gold2": LABELS / "A3_pass2_v1.0.csv", "threshold": 5, "reference_run": "a3_sonnet_20261019",
           "label": "Fresh census (55 paragraphs, weight one)"},
}
# display order and names; the provider and weights column is descriptive
MODEL_INFO = {
    "sonnet":     ("Claude Sonnet 4.6", "Anthropic, proprietary", 0),
    "opus":       ("Claude Opus 4.6", "Anthropic, proprietary", 1),
    "sonnet45":   ("Claude Sonnet 4.5", "Anthropic, proprietary", 2),
    "haiku45":    ("Claude Haiku 4.5", "Anthropic, proprietary", 3),
    "novapro":    ("Amazon Nova Pro", "Amazon, proprietary", 4),
    "nemotron3s":  ("NVIDIA Nemotron 3 Super", "NVIDIA, open weights", 5),
    "qwen3_235b": ("Qwen3-235B-A22B", "Alibaba, open weights", 6),
    "mercury3_instant": ("Inception Mercury 3 (instant)", "Inception, proprietary, diffusion", 7),
    "mercury3_low": ("Inception Mercury 3 (low)", "Inception, proprietary, diffusion", 8),
    # §4k (2026-11-01): Sonnet 5 and Opus 5 in the thesis tables since 2026-11-02; the controls in the JSON and a table note
    "sonnet5": ("Claude Sonnet 5", "Anthropic, proprietary", 9),
    "opus5": ("Claude Opus 5", "Anthropic, proprietary", 10),
    "sonnet_req5": ("Claude Sonnet 4.6 (5-series request)", "Anthropic, proprietary", 11),
    "opus_req5": ("Claude Opus 4.6 (5-series request)", "Anthropic, proprietary", 12),
    # §4m (2026-11-08): Opus 5.5 in the thesis tables (the author's request to report the findings); its control in the
    # JSON and the table note; the high-effort configuration and the Opus 5 repeat only in compare_gen55.py
    "opus55": ("Claude Opus 5.5", "Anthropic, proprietary", 10.5),     # after Opus 5, before the controls
    "opus5_req55": ("Claude Opus 5 (5.5 request)", "Anthropic, proprietary", 13),
    "opus55_high": ("Claude Opus 5.5 (effort high)", "Anthropic, proprietary", 14),
    "opus5_rep": ("Claude Opus 5 (repeat)", "Anthropic, proprietary", 15),
}
SUCCESS = ("technologies_present", "off_topic")
# §4f roster (declared 2026-10-19, amended the same day before any non-Anthropic run): every member must have exactly one
# evaluated run per sample before the final outputs are written (--preview writes nothing into paper/)
ROSTER = sc.MODEL_COMPARISON_ROSTER_4F + sc.DIFFUSION_ROSTER   # §4f + §4g: the thesis tables carry Mercury 3 since 2026-10-30
OUT_DIFFUSION = RUNS / "tables" / "model_comparison_diffusion"
OUT_GEN5 = RUNS / "tables" / "model_comparison_gen5"
GEN5_SHOWN = ("sonnet5", "opus5")                                   # thesis rows; the controls are not rows
GEN5_CONTROL = {"sonnet5": "sonnet_req5", "opus5": "opus_req5"}    # the 4.6 model of the same tier under the same request
# §4m (2026-11-08): Opus 5.5 is a thesis row; Opus 5 under the 5.5 request is its control (a note, not a row)
GEN55_SHOWN = ("opus55",)
GEN55_CONTROL = {"opus55": "opus5_req55"}
THESIS_GEN55 = GEN55_SHOWN + tuple(GEN55_CONTROL.values())
# §4k: the declared paired comparisons (run, reference), in three families; fixed before any run
GEN5_COMPARISONS = {
    "same_request": (("sonnet5", "sonnet_req5"), ("opus5", "opus_req5")),
    "as_run": (("sonnet5", "sonnet"), ("sonnet5", "opus"), ("opus5", "sonnet"), ("opus5", "opus")),
    "decoding": (("sonnet_req5", "sonnet"), ("opus_req5", "opus")),
}
# Runs that exist but are not used, each with the reason recorded before any of its figures was computed
SUPERSEDED = {
    "mc_nemotron3s_a2_20261019r2": (
        "completed and reportable at 18:59 UTC on 2026-10-19; at 19:00 UTC an erroneous agent resume appended an "
        "uncertified gate (no extraction call) to its journal, which the evaluator's every-gate-certified rule refuses; "
        "superseded by mc_nemotron3s_a2_20261019r3, run under a fresh identifier before any figure of r2 was computed"),
}


class CompareError(RuntimeError):
    pass


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(run_id: str, sample: str) -> dict:
    """One run through the evaluator's verified path, its metrics bound to that path, and its per-article counts."""
    cfg = SAMPLES[sample]
    rp = RUNS / f"{run_id}.json"
    ns = argparse.Namespace(run_record=str(rp), manifest=str(cfg["manifest"]), restrict_split=None,
                            gold_pass1=str(cfg["gold1"]), gold_pass2=str(cfg["gold2"]), dev=False, gold_manifest=None,
                            skip_provenance=False, threshold=cfg["threshold"], reliability=None)
    try:
        inp = ev.load_inputs(ns)
    except ev.EvalError as exc:
        raise CompareError(f"{run_id}: {exc}") from None
    rec = inp["rec"]
    status = ev.analysis_status_of(rec, manifest_kind=(inp.get("manifest") or {}).get("kind"))
    mp = RUNS / "tables" / run_id / "metrics.json"
    if not mp.exists():
        raise CompareError(f"{run_id}: no metrics at {mp}; run evaluate.py first")
    m = json.loads(mp.read_text(encoding="utf-8"))
    prov = inp["provenance"]
    if m.get("B") != B or m.get("seed") != SEED:
        raise CompareError(f"{run_id}: the metrics were resampled with B={m.get('B')}, seed={m.get('seed')}, not the registered {B}, {SEED}")
    if (m.get("dev") or m.get("threshold") != cfg["threshold"] or (m.get("inputs") or {}).get("run_record_sha256") != prov["run_record_sha256"]
            or (m.get("inputs") or {}).get("reference") != prov["reference"] or (m.get("analysis_status") or "registered primary") != status):
        raise CompareError(f"{run_id}: the metrics file was not computed from this run record, reference, threshold and status")
    if sample == "A2" and run_id == cfg["reference_run"] and status != "registered primary":
        raise CompareError("the A2 reference must be the registered run")
    if sample == "A3" and run_id == cfg["reference_run"] and status != sc.A3_STATUS:
        raise CompareError("the A3 reference must be the fresh-sample run")
    if run_id != cfg["reference_run"] and status != sc.MODEL_COMPARISON_STATUS:
        raise CompareError(f"{run_id}: status {status!r} is not the model comparison")
    S = ev.build(inp)
    counts = ev.counts_execution(S, S["pids"], 1)
    return {"run_id": run_id, "sample": sample, "rec": rec, "m": m, "S": S, "counts": counts, "status": status,
            "metrics_sha256": sha(mp), "run_record_sha256": prov["run_record_sha256"]}


def element_pr(counts: dict) -> tuple[float, float]:
    tp = fp = fn = 0.0
    for cls, v in counts["C"]["element"].items():
        tp += v[0]; fp += v[1]; fn += v[2]
    return (tp / (tp + fp) if tp + fp else None, tp / (tp + fn) if tp + fn else None)


def _pr(per_art, mult: dict) -> tuple:
    """Weighted element micro precision and recall over the articles of `mult` (multiplicities)."""
    tp = fp = fn = 0.0
    for a, m in mult.items():
        for cls, v in per_art[a]["element"].items():
            tp += m * v[0]; fp += m * v[1]; fn += m * v[2]
    return (tp / (tp + fp) if tp + fp else None, tp / (tp + fn) if tp + fn else None)


def _stat(name: str, per_art, mult: dict):
    if name in ("element", "technology"):
        return ev.micro(per_art, name, mult, True)
    p, r = _pr(per_art, mult)
    return p if name == "element_precision" else r


_COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                10: "ten", 11: "eleven", 12: "twelve"}


def deficit_title(pts: dict) -> str:
    """Figure 7's takeaway, counted from the plotted points (form review of 2026-11-06): the configurations whose
    element precision exceeds recall on both samples in point estimate, out of all plotted configurations."""
    n = sum(1 for k in pts if all(s in pts[k] and pts[k][s][1] > pts[k][s][0] for s in ("A2", "A3")))
    w = lambda x: _COUNT_WORDS.get(x, str(x))
    # 2026-11-09, slimming debate consensus F-13: the qualifiers "in point estimate" and "on both samples" in the title
    return (f"In point estimate, {w(n)} of {w(len(pts))} configurations mark fewer frame elements than the first coding on both "
            f"samples: precision exceeds recall.")


def paired(run: dict, ref: dict, stats=("element", "technology")) -> dict:
    """Difference (run minus reference) of weighted statistics -- element and technology micro-F1, and with
    `stats` also element precision and recall (§4g) -- under common article resamples; both runs cover the same
    paragraphs and articles."""
    if set(run["S"]["pids"]) != set(ref["S"]["pids"]) or run["S"]["art"] != ref["S"]["art"]:
        raise CompareError(f"{run['run_id']} and {ref['run_id']} do not cover the same paragraphs")
    arts = sorted(set(run["S"]["art"].values()))
    base = {a: 1 for a in arts}
    out = {}
    for table in stats:
        p_run = _stat(table, run["counts"]["per_art"], base); p_ref = _stat(table, ref["counts"]["per_art"], base)
        point = None if p_run is None or p_ref is None else p_run - p_ref
        rng = random.Random(SEED)
        draws = []
        for _ in range(B):
            mult = Counter(rng.choice(arts) for _ in arts)
            a_ = _stat(table, run["counts"]["per_art"], mult); b_ = _stat(table, ref["counts"]["per_art"], mult)
            if a_ is not None and b_ is not None:
                draws.append(a_ - b_)
        draws.sort()
        q = (lambda p: draws[int(round(p * (len(draws) - 1)))]) if draws else (lambda p: None)
        out[table] = {"diff": point, "lo": q(0.025), "hi": q(0.975), "B": B, "seed": SEED, "articles": len(arts),
                      "defined_draws": len(draws)}
    return out


def pr_gap(run: dict) -> dict:
    """Element precision minus recall of one run (execution one) with its article-resampling interval (§4k reporting,
    descriptive; the same resampling scheme as `paired`)."""
    arts = sorted(set(run["S"]["art"].values()))
    def g(mult):
        p, r = _pr(run["counts"]["per_art"], mult)
        return None if p is None or r is None else p - r
    rng = random.Random(SEED)
    draws = sorted(v for v in (g(Counter(rng.choice(arts) for _ in arts)) for _ in range(B)) if v is not None)
    q = (lambda p: draws[int(round(p * (len(draws) - 1)))]) if draws else (lambda p: None)
    return {"diff": g({a: 1 for a in arts}), "lo": q(0.025), "hi": q(0.975), "B": B, "seed": SEED, "articles": len(arts),
            "defined_draws": len(draws)}


def without_failed(run: dict, ref: dict) -> tuple[dict, dict]:
    """Both runs restricted to the paragraphs on which neither run's execution one failed (§4k sensitivity, descriptive)."""
    bad = {p for x in (run, ref) for p in x["S"]["pids"] if x["S"]["outcome"][1].get(p, "missing") not in SUCCESS}
    keep = [p for p in run["S"]["pids"] if p not in bad]
    def cut(x):
        S = {**x["S"], "pids": keep, "art": {p: x["S"]["art"][p] for p in keep}}
        return {**x, "S": S, "counts": ev.counts_execution(x["S"], keep, 1)}
    return cut(run), cut(ref)


def summarise(run: dict, ref: dict | None) -> dict:
    m, rec = run["m"], run["rec"]
    pt, rs = m["point"]["pooled"], m["resample"]["pooled"]
    iv = lambda k: [rs.get(k, {}).get("lo"), rs.get(k, {}).get("hi")]
    prec, rec_ = element_pr(run["counts"])
    out1 = Counter(run["S"]["outcome"][1].get(p, "missing") for p in run["S"]["pids"])
    s = m["threshold"]
    disp = [d["weighted_pp"] for c, d in m["displacement"]["pooled"].items()
            if d.get("gold_paragraphs", 0) >= s and d.get("weighted_pp") is not None]
    per_el = {c: (v.get("f1_weighted") if v.get("reported") else None)
              for c, v in pt["element"]["per_class"].items()}
    cost = (rec.get("cost") or {}).get("estimated_usd", (rec.get("cost") or {}).get("observed_usd"))
    execs = int(rec["repeats"]) * len(run["S"]["pids"])
    key = rec.get("model_key")
    return {
        "run_id": run["run_id"], "sample": run["sample"], "model_key": key, "model_id": rec.get("model_id"),
        "model": MODEL_INFO.get(key, (key, "", 99))[0], "provider": MODEL_INFO.get(key, ("", key, 99))[1],
        "status": run["status"], "authority": sc.MODEL_COMPARISON_AUTHORITY_OF.get(key), "repeats": int(rec["repeats"]),
        "paragraphs": len(run["S"]["pids"]),
        "technology_f1": pt["technology"]["micro_f1_weighted"], "technology_iv": iv("technology.micro_f1_weighted"),
        "element_f1": pt["element"]["micro_f1_weighted"], "element_iv": iv("element.micro_f1_weighted"),
        "element_precision": prec, "element_recall": rec_,
        "evaluation_A": pt["evaluation_A"]["accuracy_weighted"], "evaluation_A_units": pt["evaluation_A"].get("units"),
        "evaluation_A_iv": iv("evaluation_A.accuracy_weighted"),
        "evaluation_B": pt["evaluation_B"]["micro_f1_weighted"], "evaluation_B_iv": iv("evaluation_B.micro_f1_weighted"),
        "comparison_A": pt["comparison_A"]["accuracy_weighted"], "comparison_A_iv": iv("comparison_A.accuracy_weighted"),
        "comparison_A_units": pt["comparison_A"].get("units"),
        "comparison_B": pt["comparison_B"]["micro_f1_weighted"], "comparison_B_iv": iv("comparison_B.micro_f1_weighted"),
        "exact": (pt.get("exact_match") or {}).get("weighted"),
        "execution1_failed": sum(n for o, n in out1.items() if o not in SUCCESS), "execution1_outcomes": dict(out1),
        "displacement_down": sum(1 for d in disp if d < 0), "displacement_up": sum(1 for d in disp if d > 0),
        "displacement_reportable": len(disp),
        "per_element_f1": per_el,
        "cost_usd": cost, "billing_complete": (rec.get("cost") or {}).get("billing_complete"), "executions": execs, "usd_per_100_executions": (100 * cost / execs) if cost is not None and execs else None,
        "paired_vs_sonnet46": paired(run, ref) if ref is not None else None,
        "metrics_sha256": run["metrics_sha256"], "run_record_sha256": run["run_record_sha256"],
    }


def discover() -> dict[str, list[tuple[str, str]]]:
    found = {"A2": [], "A3": []}
    a3_path = str(SAMPLES["A3"]["manifest"].relative_to(ROOT)); a2_path = str(SAMPLES["A2"]["manifest"].relative_to(ROOT))
    for p in sorted(RUNS.glob("*.json")):
        if p.name.startswith(("preflight_", "w3_", "w4_")) or p.name.endswith("_class_manifest.json"):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("analysis_status") != sc.MODEL_COMPARISON_STATUS or not rec.get("reportable"):
            continue
        if rec.get("run_id") in SUPERSEDED:
            continue
        mpath = (rec.get("manifest") or {}).get("path")
        if mpath == a2_path:
            found["A2"].append((rec["run_id"], rec.get("model_key")))
        elif mpath == a3_path:
            found["A3"].append((rec["run_id"], rec.get("model_key")))
    return found


def fnum(v, nd=3):
    return "--" if v is None else f"{v:.{nd}f}"


def fsign(v, nd=3):
    return "--" if v is None else f"{v:+.{nd}f}"


def fiv(p, iv):
    return f"{fnum(p)} [{fnum(iv[0])}, {fnum(iv[1])}]" if p is not None else "undefined"


def mark(r: dict) -> str:
    return (r"$^{\dagger}$" if r["model_key"] in sc.DIFFUSION_ROSTER else
            r"$^{\ddagger}$" if r["model_key"] in GEN5_SHOWN else
            r"\textsuperscript{\S}" if r["model_key"] in GEN55_SHOWN else "")


def shown(rows: list) -> list:
    """The rows the thesis shows: every model except the configuration controls of §4k and §4m."""
    return [r for r in rows if r["model_key"] not in set(GEN5_CONTROL.values()) | set(GEN55_CONTROL.values())]


def tex_tables(res: dict, head: str) -> dict[str, str]:
    rows = []
    for sample in ("A2", "A3"):
        rows.append(f"\\multicolumn{{9}}{{@{{}}l}}{{\\emph{{{SAMPLES[sample]['label']}}}}} \\\\")
        for r in shown(res["rows"][sample]):
            pv = r["paired_vs_sonnet46"]
            d = ("reference" if pv is None else "undefined" if pv["element"]["diff"] is None else
                 f"{pv['element']['diff']:+.3f} [{fsign(pv['element']['lo'])}, {fsign(pv['element']['hi'])}]")
            name = fg.tex_escape(r["model"]) + (" (registered)" if r["status"] == "registered primary" else
                                                 " (registered model)" if r["status"] == sc.A3_STATUS else mark(r))
            rows.append(" & ".join([
                name, fnum(r["technology_f1"]),
                f"{fnum(r['element_f1'])} [{fnum(r['element_iv'][0])}, {fnum(r['element_iv'][1])}]", d,
                f"{fnum(r['element_precision'], 2)} / {fnum(r['element_recall'], 2)}",
                fnum(r["evaluation_A"]), fnum(r["evaluation_B"]),
                f"{r['execution1_failed']} of {r['paragraphs']}",
                f"{r['displacement_down']} / {r['displacement_up']}"]) + r" \\")
        rows.append(r"\addlinespace")
    rows = rows[:-1]
    header = (r"Model & Tech.\ F1 & Element F1 [interval] & $\Delta$ element F1 vs Sonnet 4.6 [interval] & "
              r"Element precision / recall & Eval., score A & Eval., score B & Failed first exec. & "
              r"Displ.\ down / up")
    L = ">{\\raggedright\\arraybackslash}p{%smm}"
    # widths chosen so that every interval fits one line (2026-10-30, with the two Mercury rows the table had outgrown a page;
    # 2026-11-02, with the Claude 5 rows: two plus signs per interval and two-digit upward displacements; widths measured at
    # \scriptsize, column separation 3pt so that the table stays within the 160 mm text width)
    colspec = "@{}" + L % 25 + L % 7 + L % 25 + L % 32 + L % 14.2 + L % 7.5 + L % 7.5 + L % 12 + L % 9.5 + "@{}"
    # 2026-11-12, M22: the scores are against each sample's first coding (sweep, repair 5 of the fourth review); the
    # parenthesis follows the run records (page review, finding 57): three executions for Sonnet 4.6 and Opus 5.5, one otherwise
    caption = ("Model comparison: the registered instrument v1.0 under further models, first execution per paragraph "
               "(the only one, except for Sonnet 4.6 and Opus 5.5, which ran three), against each sample's first coding; the Sonnet 4.6 rows are the first execution of the registered "
               "run and of the fresh-census run. Scores are design-weighted on the evaluation sample and unweighted on the fresh "
               "census; brackets are "
               f"2.5--97.5 percentile intervals over {B} article resamples; the difference to Sonnet 4.6 is computed under common "
               "resamples of both runs. Failed first executions end without a valid record (parse failures after the identical "
               "retry) and contribute omissions only. Displacements count the reportable classes (reference support of at least "
               "five paragraphs) whose prevalence the model marks lower / higher than the reference. Descriptive; no ranking and no "
               "selection follow (specification §4f; the rows marked $\\dagger$ §4g, $\\ddagger$ §4k, \\textsuperscript{\\S} §4m).")
    dag = {s_: {r["model_key"]: r for r in res["rows"][s_]} for s_ in ("A2", "A3")}
    def vs_opus(key, field="paired_vs_opus46", stat="element"):
        cells = []
        for s_ in ("A2", "A3"):
            r_ = dag[s_].get(key) or {}
            e_ = r_.get(field) if field == "element_pr_gap" else ((r_.get(field) or {}).get(stat) or {})
            e_ = e_ or {}
            cells.append("undefined" if e_.get("diff") is None else
                         f"${e_['diff']:+.3f}$ [${fsign(e_['lo'])}$, ${fsign(e_['hi'])}$]")
        return " and ".join(cells)
    def ctl(key):
        a2, a3 = dag["A2"][key], dag["A3"][key]
        return (f"{fnum(a2['element_f1'])} and {fnum(a3['element_f1'])} "
                f"({a2['execution1_failed']} and {a3['execution1_failed']} failed first executions)")
    notes = ["The non-Anthropic models of §4f receive the pinned request without the cache point, which their providers do not "
             "support; prompt, tool schema, forced tool choice and decoding are identical. Estimated cost per 100 executions at "
             "provisional list rates: " + "; ".join(
                 f"{fg.tex_escape(r['model'])} USD {r['usd_per_100_executions']:.2f}" + ("" if r.get("billing_complete") else "*")
                 for r in shown(res["rows"]["A2"]) if r.get("usd_per_100_executions") is not None)
             + "; * the provider reported no usage for attempts that ended in an exception, which may have been billed and are "
               "not in the estimate. Gate calls and a superseded run are excluded (specification §4f).",
             r"$^{\dagger}$ Specification §4g: the diffusion model Mercury 3 through its developer's API outside Bedrock, the "
             r"pinned request without cache point, temperature 0.5 (the API's minimum); instant: no reasoning, 1{,}024 completion "
             r"tokens; low: low reasoning, 8{,}192; cost at the list price at the time (USD 0.10 / 0.35 per million input / output "
             r"tokens). $\Delta$ element F1 vs Opus 4.6 on A2 and A3: instant "
             + vs_opus("mercury3_instant") + "; low " + vs_opus("mercury3_low") + ".",
             r"$^{\ddagger}$ Specification §4k: Claude Sonnet 5 and Claude Opus 5, which reject temperature 0, on Bedrock EU "
             r"inference profiles (accessed 1 November 2026); the pinned request without a temperature, so at the provider's default "
             r"sampling, and with extended thinking disabled; cost at provisional rates (list price plus the EU premium). The two "
             r"4.6 models under this same request (configuration controls) have element F1 on A2 and A3: Sonnet 4.6 " + ctl("sonnet_req5")
             + "; Opus 4.6 " + ctl("opus_req5") + r". $\Delta$ element F1 vs the control of the same tier on A2 and A3: Sonnet 5 "
             + vs_opus("sonnet5", "paired_vs_control") + "; Opus 5 " + vs_opus("opus5", "paired_vs_control")
             + r"; vs Opus 4.6: Sonnet 5 " + vs_opus("sonnet5") + "; Opus 5 " + vs_opus("opus5")
             + r". Element precision minus recall on A2 and A3: Sonnet 5 " + vs_opus("sonnet5", "element_pr_gap")
             + "; Opus 5 " + vs_opus("opus5", "element_pr_gap") + "."]
    if all(f in dag[s_].get("opus55", {}) for f in ("paired_vs_opus5", "paired_vs_control") for s_ in ("A2", "A3")):
        notes.append(
            r"\textsuperscript{\S} Specification §4m: Claude Opus 5.5 (Bedrock EU inference profile, accessed 8 November 2026), "
            r"which rejects disabled thinking and a forced tool choice: the pinned prompt and tool schema, the tool chosen freely "
            r"(the only one offered), adaptive thinking at the lowest effort (low), no temperature, 8{,}192 output tokens; cost at "
            r"provisional rates. Opus 5 under this same request (configuration control) has element F1 on A2 and A3 "
            + ctl("opus5_req55") + r". $\Delta$ element F1 of Opus 5.5 vs this control on A2 and A3: "
            + vs_opus("opus55", "paired_vs_control") + "; vs Opus 5: " + vs_opus("opus55", "paired_vs_opus5")
            + ". Element precision minus recall: " + vs_opus("opus55", "element_pr_gap")
            + r". Further executions, effort high and the Opus 5 repeat: Table~\ref{tab:opus55}.")
    # a paginating table since 2026-11-02: with the Claude 5 rows and the §4k note it no longer fits one page
    tab = fg.longtable_env(caption, "tab:model_comparison", colspec, 9, header, rows, notes,
                           short="Model comparison", tabcolsep="3pt")
    # per-element F1 on A2, one column per model
    models = shown(res["rows"]["A2"])
    SHORT = {"sonnet": "S 4.6", "opus": "O 4.6", "sonnet45": "S 4.5", "haiku45": "H 4.5", "novapro": "Nova",
             "nemotron3s": "Nemo.", "qwen3_235b": "Qwen3", "mercury3_instant": "M3 i", "mercury3_low": "M3 l",
             "sonnet5": "S 5", "opus5": "O 5", "opus55": "O 5.5"}
    hdr = "Element & " + " & ".join(SHORT.get(r["model_key"], fg.tex_escape(r["model"])) for r in models)
    erows = []
    for e in fg.ELEMENT_ORDER:
        cells = []
        for r in models:
            v = r["per_element_f1"].get(e)
            cells.append("--" if v is None else f"{v:.2f}")
        erows.append(f"{e} & " + " & ".join(cells) + r" \\")
    ecap = ("Per-element F1 on the registered evaluation sample by model (design-weighted, first execution; -- where the "
            "reference supports the element with fewer than five rows). S, O, H: Claude Sonnet, Opus, Haiku with version; Nova: "
            "Amazon Nova Pro; Nemo.: NVIDIA Nemotron 3 Super; Qwen3: Qwen3-235B-A22B; M3 i, M3 l: Mercury 3, instant and low "
            "(specification §4g, temperature 0.5); S 5, O 5: Claude Sonnet 5 and Opus 5 (specification §4k, the provider's "
            "default sampling); O 5.5: Claude Opus 5.5 (specification §4m, adaptive thinking at effort low, free tool choice). "
            "Model comparison, specification §4f, §4g, §4k and §4m; descriptive.")
    etab = fg.table_env(ecap, "tab:model_comparison_elements", "@{}l" + "r" * len(models) + "@{}", hdr, erows, [],
                        size=r"\scriptsize", short="Per-element F1 by model")
    # every headline quantity of the primary table with its interval, per model and sample (specification §4f)
    frows = []
    for sample in ("A2", "A3"):
        frows.append(f"\\multicolumn{{8}}{{@{{}}l}}{{\\emph{{{SAMPLES[sample]['label']}}}}} \\\\")
        for r in shown(res["rows"][sample]):
            frows.append(" & ".join([fg.tex_escape(r["model"]) + mark(r), fiv(r["technology_f1"], r["technology_iv"]),
                                     fiv(r["element_f1"], r["element_iv"]), fiv(r["evaluation_A"], r["evaluation_A_iv"]),
                                     fiv(r["evaluation_B"], r["evaluation_B_iv"]), fiv(r["comparison_A"], r["comparison_A_iv"]),
                                     fiv(r["comparison_B"], r["comparison_B_iv"]), fnum(r["exact"])]) + r" \\")
        frows.append(r"\addlinespace")
    frows = frows[:-1]
    fhdr = (r"Model & Technology, micro-F1 & Elements, micro-F1 & Evaluation, score A & Evaluation, score B & "
            r"Comparison target, score A & Comparison target, score B & Exact rows")
    fcap = ("Headline quantities of the primary table by model and sample, first execution, with 2.5--97.5 percentile intervals "
            f"over {B} article resamples (design-weighted on the evaluation sample, weight one on the fresh census). Model "
            "comparison, specification §4f; "
            "Mercury 3 §4g, temperature 0.5; Claude 5 §4k, the provider's default sampling; Opus 5.5 §4m, adaptive thinking at "
            "effort low; descriptive.")
    L2 = ">{\\raggedright\\arraybackslash}p{%smm}"
    ftab = fg.table_env(fcap, "tab:model_comparison_full", "@{}" + L2 % 22 + (L2 % 17) * 6 + L2 % 9 + "@{}", fhdr, frows, [],
                        size=r"\scriptsize", short="Headline quantities by model")
    return {"tab_model_comparison.tex": head + tab, "tab_model_comparison_elements.tex": head + etab,
            "tab_model_comparison_full.tex": head + ftab}


# configurations the text names, labelled directly (2026-11-05, supervisor re-read): (sample of the labelled mark, node
# placement, text); a placement given as offsets (dx, dy) in axis units sets the label apart with a thin leader line
NAMED = {
    # Opus 4.6 labelled under its census mark since 2026-11-08: Opus 5.5's evaluation-sample mark (§4m) sits where the
    # label beside its evaluation-sample mark used to be
    "opus": ("A3", "below=2pt", "Opus 4.6"),
    "mercury3_low": ("A3", "above=2pt", "Mercury 3 low"),
    "mercury3_instant": ("A3", (0.045, 0.105), "Mercury 3\\\\instant"),
    "novapro": ("A2", (0.0, 0.065), "Nova Pro"),
}
# the two Opus 5 configurations the text discusses, as changed configurations in vermillion (figChanged): Opus 5, with no
# clear precision-over-recall gap (§4k, reproduced in three further executions, §4m), as a pentagon; Opus 5.5 (§4m, the
# author's request of 2026-11-08 to show it in the figure), which shows the gap on the evaluation sample, as a triangle.
# The colour marks a changed configuration, not a result (agent review of §4m: the highlight must not presuppose one)
HIGHLIGHT = ("opus5", "opus55")
HIGHLIGHT_STYLE = {"opus5": "highlight", "opus55": "highlightb"}
HIGHLIGHT_LABEL = {"opus5": ("A2", "right=3pt"), "opus55": ("A2", "above=3pt")}   # (labelled sample, placement): both beside the filled mark
# Palette 2026-11-06 (thesis preamble, one meaning per colour): the registered model, its joining line and the two sample
# legend images in model blue (figModel; the preamble style `registered` is model blue too), Opus 5 in vermillion
# (figHighlight = figChanged: a changed configuration), the other configurations grey (figContext). The diamond with its
# white halo and direct label, the pentagon with its bold label and the open fill of the fresh census double the colours
# in greyscale. Numbers, shapes and fills unchanged; the caption says blue diamonds instead of black.


def tex_figure(res: dict, head: str) -> str:
    """Element precision against element recall of the first execution per model and sample (2026-11-05): the
    precision = recall diagonal, faint equal-F1 curves, the registered model as a model-blue diamond, Opus 5 highlighted as a
    vermillion pentagon, every other configuration grey; evaluation sample (A2) filled, fresh sample (A3) open, a model's
    two samples joined by a line drawn under every mark. A joining line that would pass through another configuration's
    mark is bent away from it, so that no line seems to end in a foreign mark. Styles from the thesis preamble (thesisfig,
    registered, highlight, context, open, refline, guideline)."""
    pts = {}
    for sample in ("A2", "A3"):
        for r in shown(res["rows"][sample]):
            if r["element_precision"] is not None and r["element_recall"] is not None:
                pts.setdefault(r["model_key"], {})[sample] = (r["element_recall"], r["element_precision"])
    reg = sc.REGISTERED_MODEL_KEY
    style = lambda k: "registered" if k == reg else HIGHLIGHT_STYLE.get(k, "context")
    colour = lambda k: "figModel" if k == reg else "figHighlight" if k in HIGHLIGHT else "figContext"
    order = sorted(pts, key=lambda k: (k == reg or k in HIGHLIGHT, MODEL_INFO.get(k, ("", "", 99))[2]))  # highlighted on top
    MM = 100.0                       # millimetres per axis unit (80 mm for 0.8 on both axes)
    CLEAR = 1.8                      # a foreign mark closer than this (mm) to a joining line bends the line away from it
    allpts = [(k, s_, xy) for k in pts for s_, xy in pts[k].items()]
    joins, marks = [], []
    for k in order:
        if len(pts[k]) == 2:
            (x1, y1), (x2, y2) = pts[k]["A2"], pts[k]["A3"]
            dx, dy = (x2 - x1) * MM, (y2 - y1) * MM
            ln2 = dx * dx + dy * dy
            bend = ""
            for k2, _, (px, py) in allpts:
                if k2 == k or ln2 == 0:
                    continue
                vx, vy = (px - x1) * MM, (py - y1) * MM
                tpar = (vx * dx + vy * dy) / ln2
                cross = dx * vy - dy * vx
                if 0.05 < tpar < 0.95 and abs(cross) / math.sqrt(ln2) < CLEAR:
                    bend = " to[bend right=35]" if cross > 0 else " to[bend left=35]"
                    break
            joins.append(f"\\draw[{colour(k)}, line width={'0.6' if style(k) != 'context' else '0.4'}pt, shorten <=2pt, "
                         f"shorten >=2pt] (axis cs:{x1:.4f},{y1:.4f}){bend or ' --'} (axis cs:{x2:.4f},{y2:.4f});")
    for k in order:
        for sample, opn in (("A2", ""), ("A3", ", open")):
            if sample in pts[k]:
                x, y = pts[k][sample]
                if k == reg:   # a thin white halo keeps the registered diamond apart from a neighbouring mark
                    marks.append(f"\\addplot[only marks, mark=diamond*, mark size=3.6pt, draw=white, fill=white, forget plot] "
                                 f"coordinates {{({x:.4f},{y:.4f})}};")
                marks.append(f"\\addplot[{style(k)}{opn}, forget plot] coordinates {{({x:.4f},{y:.4f})}};")
    # direct labels: the registered model's label under its fresh-sample diamond, where no line or mark lies; Opus 5's
    # label beside its evaluation-sample mark, in bold so that it stays distinct in greyscale print
    lab = []
    if reg in pts:
        x, y = pts[reg].get("A3", pts[reg].get("A2"))
        lab.append(f"\\node[figtext, below=3pt, align=center, inner sep=1pt, fill=white] at (axis cs:{x:.4f},{y:.4f}) "
                   f"{{{MODEL_INFO[reg][0].replace('Claude ', '')}\\\\(registered)}};")
    for hk in HIGHLIGHT:
        smp, place = HIGHLIGHT_LABEL.get(hk, ("A2", "right=3pt"))
        if hk in pts and smp in pts[hk]:
            x, y = pts[hk][smp]
            lab.append(f"\\node[figtext, {place}, inner sep=1pt, fill=white, text={colour(hk)}, font=\\figfontsmall\\bfseries] at (axis cs:{x:.4f},{y:.4f}) "
                       f"{{{MODEL_INFO[hk][0].replace('Claude ', '')}}};")
    for k, (sample, place, text) in NAMED.items():
        if k not in pts or sample not in pts[k]:
            continue
        x, y = pts[k][sample]
        if isinstance(place, tuple):       # a label set apart, joined to its mark by a thin leader
            lx, ly = x + place[0], y + place[1]
            lab.append(f"\\draw[figGreyA, line width=0.3pt] (axis cs:{x:.4f},{ly - 0.012:.4f}) -- (axis cs:{x:.4f},{y + 0.012:.4f});")
            lab.append(f"\\node[figtext, text=figGreyA, anchor=south, align=center, inner sep=1pt, fill=white] at (axis cs:{lx:.4f},{ly - 0.012:.4f}) "
                       f"{{{text}}};")
        else:
            lab.append(f"\\node[figtext, text=figGreyA, {place}, inner sep=1pt, fill=white] at (axis cs:{x:.4f},{y:.4f}) {{{text}}};")
    iso = []
    for f in (0.4, 0.6, 0.8):
        x0 = f / (2 - f) + 0.0005          # where the curve P = fR/(2R - f) reaches precision 1
        # grey B and dotted (2026-11-05, layout review): grey C vanished in greyscale print; dotted keeps the curves apart
        # from the dashed precision = recall line
        iso.append(f"\\addplot[figGreyB, densely dotted, line width=0.6pt, domain={x0:.4f}:1, samples=60, forget plot] {{{f}*x/(2*x-{f})}};")
        iso.append(f"\\node[figtext, text=figGreyA, anchor=south west, inner sep=1pt] at (axis cs:{x0:.3f},1) {{F1 = {f}}};")
    n_ctx = len([k for k in pts if style(k) == "context"])
    return head + "\n".join([
        r"% Palette 2026-11-06: the registered model, its joining line and the sample legend images model blue (figModel); Opus 5 (pentagon) and, since 2026-11-08, Opus 5.5 (triangle) vermillion (figHighlight = figChanged), the other configurations grey (figContext); the caption says blue diamonds.",
        r"\begin{figure}[htbp]", r"\centering", r"\begin{tikzpicture}", r"\begin{axis}[thesisfig,",
        r"  width=80mm, height=80mm, scale only axis,",
        r"  xmin=0.2, xmax=1, ymin=0.2, ymax=1, xtick={0.2,0.4,0.6,0.8,1}, ytick={0.2,0.4,0.6,0.8,1},",
        r"  xticklabels={0.2,0.4,0.6,0.8,1}, yticklabels={0.2,0.4,0.6,0.8,1},",
        r"  xlabel={Element recall}, ylabel={Element precision},",
        r"  legend columns=3,", r"]",
        r"\draw[refline] (axis cs:0.2,0.2) -- (axis cs:1,1);",
        r"\node[figtext, text=figGreyA, rotate=45, anchor=south] at (axis cs:0.30,0.30) {precision = recall};",
        *iso, *joins, *marks, *lab,
        r"\addlegendimage{registered}\addlegendentry{Sonnet 4.6, registered}",
        *[f"\\addlegendimage{{{HIGHLIGHT_STYLE[k]}}}\\addlegendentry{{{MODEL_INFO[k][0].replace('Claude ', '')}}}"
          for k in HIGHLIGHT if k in pts],
        f"\\addlegendimage{{context}}\\addlegendentry{{Other configurations ({n_ctx})}}",
        r"\addlegendimage{context}\addlegendentry{Evaluation sample}",
        r"\addlegendimage{context, open}\addlegendentry{Fresh census}",
        r"\end{axis}", r"\end{tikzpicture}",
        # 2026-11-09, slimming debate consensus F-13: the legend names every mark and fill, so the caption keeps the
        # takeaway, the weighting, the guides and the pointer to the request differences
        # 2026-11-09, second slimming pass (G-56): caption syntax tightened; Table tab:model_comparison is in Appendix A.11
        # 2026-11-12, second cut pass (75k): the paragraph counts of the two samples dropped from the caption
        r"\caption[Element precision against recall by model]{\textbf{" + deficit_title(pts) + r"} First executions; "
        r"evaluation sample design-weighted, fresh census unweighted. Lines join each "
        r"configuration's samples. Several configurations differed from "   # 2026-11-12, M16 cut pass (K4-01): the lines are labelled in the graphic
        # 2026-11-12, M22 page review (finding 19): the request differences are listed in Table tab:model_config
        r"the registered decoding settings (Table~\ref{tab:model_config}).}",
        r"\label{fig:model_comparison}", r"\end{figure}"]) + "\n"


def verdict(pv: dict | None) -> str:
    """§4g decision rule on one sample: the 95 % paired interval of the element micro-F1 difference."""
    if not pv or pv["element"]["lo"] is None:
        return "undefined"
    lo, hi = pv["element"]["lo"], pv["element"]["hi"]
    return "better" if lo > 0 else "worse" if hi < 0 else "no distinguishable difference"


def diffusion_outputs(res: dict) -> dict:
    """§4g: the Mercury rows with paired differences to Opus 4.6 and Sonnet 4.6 (element F1, technology F1, element
    precision and recall) and the declared verdict per sample and overall; written beside, never into paper/."""
    out = {"declared": sc.DIFFUSION_AUTHORITY, "B": B, "seed": SEED, "rule": (
        "per sample: better / worse if the 95 % paired article-resampling interval of the element micro-F1 difference lies "
        "entirely above / below zero, otherwise no distinguishable difference; overall better / worse only if both samples "
        "agree, otherwise no consistent difference"), "rows": {}, "verdicts": {}}
    stats = ("element", "technology", "element_precision", "element_recall")
    for sample in ("A2", "A3"):
        rows = res["rows"][sample]
        by = {r["model_key"]: r for r in rows}
        # the row fields as written on 2026-10-30 (the paper-mode additions `authority` and `paired_vs_opus46` stay out)
        out["rows"][sample] = [{k: v for k, v in r.items() if k not in ("per_element_f1", "authority", "paired_vs_opus46")}
                               for r in rows]
    # recompute the paired statistics with precision and recall against both references
    for sample in ("A2", "A3"):
        cache = {}
        def run_of(key):
            if key not in cache:
                rid = next(r["run_id"] for r in res["rows"][sample] if r["model_key"] == key)
                cache[key] = load(rid, sample)
            return cache[key]
        for key in sc.DIFFUSION_ROSTER:
            for refk, refname in (("opus", "Opus 4.6"), ("sonnet", "Sonnet 4.6")):
                pv = paired(run_of(key), run_of(refk), stats=stats)
                out["verdicts"].setdefault(key, {}).setdefault(refname, {})[sample] = {"verdict": verdict(pv), "paired": pv}
    for key, refs in out["verdicts"].items():
        for refname, per in refs.items():
            v = {per[s_]["verdict"] for s_ in ("A2", "A3")}
            per["overall"] = (next(iter(v)) if len(v) == 1 and next(iter(v)) in ("better", "worse")
                              else "no consistent difference")          # the §4g rule, verbatim
    lines = ["model_key;reference;sample;verdict;diff_element;lo;hi;diff_technology;lo;hi;diff_precision;lo;hi;diff_recall;lo;hi"]
    for key, refs in out["verdicts"].items():
        for refname, per in refs.items():
            for s_ in ("A2", "A3"):
                pv = per[s_]["paired"]
                cells = []
                for st in stats:
                    d = pv[st]
                    cells += [("" if d["diff"] is None else f"{d['diff']:.4f}"), ("" if d["lo"] is None else f"{d['lo']:.4f}"),
                              ("" if d["hi"] is None else f"{d['hi']:.4f}")]
                lines.append(";".join([key, refname, s_, per[s_]["verdict"]] + cells))
    return {OUT_DIFFUSION / "diffusion_comparison.json": json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n",
            OUT_DIFFUSION / "diffusion_verdicts.csv": "\n".join(lines) + "\n"}


def gen5_outputs(res: dict) -> dict:
    """§4k: the rows of Sonnet 4.6 and Opus 4.6 (as registered / as run in §4f), Sonnet 5, Opus 5 and the two controls,
    each run's attempt verdict codes (truncations included), and the declared paired comparisons with element and
    technology micro-F1 and element precision and recall, the per-sample verdict and the overall verdict; written beside,
    never into paper/."""
    out = {"declared": sc.GEN5_AUTHORITY, "B": B, "seed": SEED, "rule": (
        "per sample: better / worse if the 95 % paired article-resampling interval of the element micro-F1 difference lies "
        "entirely above / below zero, otherwise no distinguishable difference; overall better / worse only if both samples "
        "agree, otherwise no consistent difference"),
        "families": {"same_request": "the generation with decoding held equal (primary reading)",
                     "as_run": "against the 4.6 runs at temperature 0; generation and decoding confounded",
                     "decoding": "the 5-series request against the registered request, on the 4.6 models"},
        "rows": {}, "comparisons": []}
    stats = ("element", "technology", "element_precision", "element_recall")
    keep = ("sonnet", "opus") + tuple(sc.GEN5_ROSTER)
    for sample in ("A2", "A3"):
        rows = []
        for r in res["rows"][sample]:
            if r["model_key"] not in keep:
                continue
            row = {k: v for k, v in r.items() if k != "paired_vs_opus46"}
            rec = json.loads((RUNS / f"{r['run_id']}.json").read_text(encoding="utf-8"))
            row["inference_config"] = rec.get("inference_config")
            row["thinking"] = rec.get("thinking")
            row["attempt_verdict_codes"] = (rec.get("totals") or {}).get("verdict_codes")
            row["precision_exceeds_recall"] = (None if r["element_precision"] is None or r["element_recall"] is None
                                               else r["element_precision"] > r["element_recall"])
            rows.append(row)
        out["rows"][sample] = rows
    loaded = {s_: {} for s_ in ("A2", "A3")}
    def run_of(sample, key):
        if key not in loaded[sample]:
            rid = next(r["run_id"] for r in res["rows"][sample] if r["model_key"] == key)
            loaded[sample][key] = load(rid, sample)
        return loaded[sample][key]
    lines = ["family;model_key;reference;sample;verdict;overall;diff_element;lo;hi;diff_technology;lo;hi;"
             "diff_precision;lo;hi;diff_recall;lo;hi"]
    for family, pairs in GEN5_COMPARISONS.items():
        for key, refk in pairs:
            per = {}
            for sample in ("A2", "A3"):
                pv = paired(run_of(sample, key), run_of(sample, refk), stats=stats)
                per[sample] = {"verdict": verdict(pv), "paired": pv}
            v = {per[s_]["verdict"] for s_ in ("A2", "A3")}
            overall = (next(iter(v)) if len(v) == 1 and next(iter(v)) in ("better", "worse")
                       else "no consistent difference")               # §4g's rule, as §4k declares
            out["comparisons"].append({"family": family, "model_key": key, "reference": refk, **per, "overall": overall})
            for s_ in ("A2", "A3"):
                cells = []
                for st in stats:
                    d = per[s_]["paired"][st]
                    cells += [("" if d[x] is None else f"{d[x]:.4f}") for x in ("diff", "lo", "hi")]
                lines.append(";".join([family, key, refk, s_, per[s_]["verdict"], overall] + cells))
    return {OUT_GEN5 / "gen5_comparison.json": json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n",
            OUT_GEN5 / "gen5_verdicts.csv": "\n".join(lines) + "\n"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--preview", action="store_true", help="allow an incomplete roster; writes only tools/runs/tables/model_comparison/")
    ap.add_argument("--diffusion", action="store_true",
                    help="§4g: add Mercury 3, paired differences to Opus 4.6 and Sonnet 4.6 incl. element precision and recall, "
                         "and the declared verdict; writes only tools/runs/tables/model_comparison_diffusion/ (never paper/)")
    ap.add_argument("--gen5", action="store_true",
                    help="§4k: Sonnet 5, Opus 5 and the two configuration controls beside Sonnet 4.6 and Opus 4.6, with the "
                         "declared comparisons and verdicts; writes only tools/runs/tables/model_comparison_gen5/ (never paper/)")
    a = ap.parse_args()
    if a.gen5 and a.diffusion:
        sys.exit("REFUSING: --gen5 and --diffusion write different comparisons; run them one at a time")
    roster = (("opus",) + tuple(sc.GEN5_ROSTER) if a.gen5 else tuple(ROSTER) if a.diffusion
              else tuple(ROSTER) + tuple(sc.GEN5_ROSTER) + THESIS_GEN55)  # thesis mode: §4f + §4g + §4k + §4m Opus 5.5
                                                                          # (controls for the notes)
    thesis = not (a.gen5 or a.diffusion)
    found_all = discover()
    found = {s_: [rid for rid, mk in v if mk in roster] for s_, v in found_all.items()}
    left_out = sorted({mk for v in found_all.values() for rid, mk in v if mk not in roster})
    if left_out:
        print(f"  not in this comparison's roster (another mode): {left_out}")
    res = {"declared": f"{sc.MODEL_COMPARISON_AUTHORITY}; {sc.DIFFUSION_AUTHORITY} for {', '.join(sc.DIFFUSION_ROSTER)}"
                       + (f"; {sc.GEN5_AUTHORITY} for {', '.join(sc.GEN5_ROSTER)}; {sc.GEN55_AUTHORITY} for {', '.join(THESIS_GEN55)}"
              if thesis else ""),
           "B": B, "seed": SEED, "rows": {}, "inputs": {}}
    try:
        for sample in ("A2", "A3"):
            ref = load(SAMPLES[sample]["reference_run"], sample)
            rows = [summarise(ref, None)]
            loaded = {}
            for run_id in found[sample]:
                run = load(run_id, sample)
                loaded[run["rec"].get("model_key")] = run
                rows.append(summarise(run, ref))
            for r in rows:                       # §4g: the difference to Opus 4.6 under the same common resamples
                if r["model_key"] in sc.DIFFUSION_ROSTER and "opus" in loaded:
                    r["paired_vs_opus46"] = paired(loaded[r["model_key"]], loaded["opus"])
            if thesis:                           # §4k reporting (2026-11-02): note quantities, all descriptive
                byk = {**loaded, sc.REGISTERED_MODEL_KEY: ref}
                for r in rows:
                    r["element_pr_gap"] = pr_gap(byk[r["model_key"]])
                    k = r["model_key"]
                    if k in GEN55_SHOWN and "opus5" in loaded and GEN55_CONTROL[k] in loaded:   # §4m, as §4k below
                        r["paired_vs_opus5"] = paired(loaded[k], loaded["opus5"])
                        r["paired_vs_control"] = paired(loaded[k], loaded[GEN55_CONTROL[k]])
                    if k in GEN5_SHOWN and "opus" in loaded and GEN5_CONTROL[k] in loaded:   # --preview may lack them
                        r["paired_vs_opus46"] = paired(loaded[k], loaded["opus"])
                        r["paired_vs_control"] = paired(loaded[k], loaded[GEN5_CONTROL[k]])
                        cut_run, cut_ref = without_failed(loaded[k], ref)
                        r["paired_vs_sonnet46_without_failed"] = {**paired(cut_run, cut_ref),
                                                                  "paragraphs": len(cut_run["S"]["pids"])}
            rows.sort(key=lambda r: MODEL_INFO.get(r["model_key"], ("", "", 99))[2])
            keys = [r["model_key"] for r in rows]
            if len(set(keys)) != len(keys):
                raise CompareError(f"{sample}: more than one comparison run per model {keys}; remove or name the superseded one")
            res["rows"][sample] = rows
    except CompareError as exc:
        sys.exit(f"REFUSING: {exc}")
    for s_ in ("A2", "A3"):
        got = {r["model_key"] for r in res["rows"][s_]} - {sc.REGISTERED_MODEL_KEY}
        if got - set(roster):
            sys.exit(f"REFUSING: {s_} has comparison runs outside the declared roster: {sorted(got - set(roster))}")
        if set(roster) - got and not a.preview:
            sys.exit(f"REFUSING: {s_} lacks the declared roster member(s) {sorted(set(roster) - got)}; pass --preview for a partial look "
                     f"(nothing is written into paper/)")
    if [r["model_key"] for r in res["rows"]["A2"]] != [r["model_key"] for r in res["rows"]["A3"]] and not a.preview:
        sys.exit(f"REFUSING: the A2 and A3 rosters differ: {[r['model_key'] for r in res['rows']['A2']]} vs "
                 f"{[r['model_key'] for r in res['rows']['A3']]}")
    here = Path(__file__).resolve()
    res["inputs"] = {"compare_models_sha256": sha(here), "evaluate_sha256": sha(HERE / "evaluate.py")}
    head = (f"% Generated by tools/pipeline/compare_models.py (sha256 {res['inputs']['compare_models_sha256'][:16]}) from "
            + ", ".join(r["run_id"] for s in ("A2", "A3") for r in res["rows"][s])
            + "; robustness model comparison (specification §4f; Mercury 3 §4g; Claude 5 §4k; Opus 5.5 §4m), not the registered result. "
              "Do not edit by hand.\n")
    lines = ["sample;model_key;model_id;run_id;status;technology_f1;element_f1;element_lo;element_hi;element_precision;"
             "element_recall;evaluation_A;evaluation_B;comparison_A;comparison_B;exact;execution1_failed;paragraphs;"
             "displacement_down;displacement_up;displacement_reportable;diff_element;diff_element_lo;diff_element_hi;"
             "diff_technology;diff_technology_lo;diff_technology_hi;cost_usd;usd_per_100_executions"]
    for s in ("A2", "A3"):
        for r in res["rows"][s]:
            pv = r["paired_vs_sonnet46"] or {}
            e_, t_ = pv.get("element") or {}, pv.get("technology") or {}
            lines.append(";".join(str("" if v is None else (f"{v:.4f}" if isinstance(v, float) else v)) for v in (
                s, r["model_key"], r["model_id"], r["run_id"], r["status"], r["technology_f1"], r["element_f1"],
                r["element_iv"][0], r["element_iv"][1], r["element_precision"], r["element_recall"], r["evaluation_A"],
                r["evaluation_B"], r["comparison_A"], r["comparison_B"], r["exact"], r["execution1_failed"], r["paragraphs"],
                r["displacement_down"], r["displacement_up"], r["displacement_reportable"], e_.get("diff"), e_.get("lo"),
                e_.get("hi"), t_.get("diff"), t_.get("lo"), t_.get("hi"), r["cost_usd"], r["usd_per_100_executions"])))
    files = {OUT / "comparison.json": json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n",
             OUT / "comparison.csv": "\n".join(lines) + "\n"}
    res["superseded"] = SUPERSEDED
    files[OUT / "comparison.json"] = json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n"
    if a.diffusion:
        files = diffusion_outputs(res)
    elif a.gen5:
        files = gen5_outputs(res)
    elif not a.preview:
        for name, text in tex_tables(res, head).items():
            files[PAPER / "tables" / name] = text
        files[PAPER / "images" / "fig_model_comparison.tex"] = tex_figure(res, head)
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
        for r in res["rows"][s]:
            pv = r["paired_vs_sonnet46"]
            print(f"  {s} {r['model']:<22} tech {fnum(r['technology_f1'])}  elem {fnum(r['element_f1'])} "
                  f"[{fnum(r['element_iv'][0])}, {fnum(r['element_iv'][1])}]  P/R {fnum(r['element_precision'], 2)}/"
                  f"{fnum(r['element_recall'], 2)}  failed {r['execution1_failed']}/{r['paragraphs']}"
                  + ("" if pv is None else f"  diff {fsign(pv['element']['diff'])} [{fsign(pv['element']['lo'])}, {fsign(pv['element']['hi'])}]"))


if __name__ == "__main__":
    main()
