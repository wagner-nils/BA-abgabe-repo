#!/usr/bin/env python3
"""Candidate comparison on the A3 census (pre-analysis specification §4b; the author's decision of 2026-10-26).

The registered instrument v1.0 (run `a3_sonnet_20261019`) beside the one frozen candidate v1.0r3 (the registered
request byte for byte under retry policy v3, the feedback retry; run `a3_sonnet_r3_20261026`), both three executions
per paragraph on the 55 census paragraphs, weight one. Reported as §4b prescribes: invalid first responses,
recoveries, terminal failures, the element micro-F1 with its indicative interval, and the paired difference under
common article resamples -- a descriptive comparison of complete configurations, never a confirmed improvement.

Both runs are loaded through the evaluator's verified input path (`evaluate.load_inputs`: census contract, gate
certification and code provenance, journal reconciliation, embargo evidence), their metrics files must be the ones
the evaluator wrote for exactly those records, and the candidate's record must carry the author's decision bound
by its execution contract (`evaluate.analysis_status_of`). The paired difference reuses `compare_models.paired`
(B = 2000, seed 20260905, the registered resampling seed).

    .venv/bin/python3 tools/pipeline/compare_a3_candidate.py [--candidate-run a3_sonnet_r3_20261026] [--check]

Outputs: tools/runs/tables/a3_candidate/{comparison.json, comparison.csv}, paper/tables/tab_a3_candidate.tex.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compare_models as cm                                         # noqa: E402
import evaluate as ev                                               # noqa: E402
import figures as fg                                                # noqa: E402
import sample_contract as sc                                        # noqa: E402

ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
PAPER = ROOT / "paper"
OUT = RUNS / "tables" / "a3_candidate"
INVALID = ("schema_invalid", "contract_invalid")


class CandidateError(RuntimeError):
    pass


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(run_id: str, *, candidate: bool) -> dict:
    """One A3 run through the evaluator's verified path, its metrics bound to that path, and its per-article counts."""
    cfg = cm.SAMPLES["A3"]
    rp = RUNS / f"{run_id}.json"
    if not rp.exists():
        raise CandidateError(f"{run_id}: no run record at {rp}")
    ns = argparse.Namespace(run_record=str(rp), manifest=str(cfg["manifest"]), restrict_split=None,
                            gold_pass1=str(cfg["gold1"]), gold_pass2=str(cfg["gold2"]), dev=False, gold_manifest=None,
                            skip_provenance=False, threshold=cfg["threshold"], reliability=None)
    try:
        inp = ev.load_inputs(ns)
        rec = inp["rec"]
        status = ev.analysis_status_of(rec, manifest_kind=(inp.get("manifest") or {}).get("kind"))
        ident = ev.instrument_identity(rec)
    except ev.EvalError as exc:
        raise CandidateError(f"{run_id}: {exc}") from None
    if status != sc.A3_STATUS:
        raise CandidateError(f"{run_id}: status {status!r} is not the fresh-sample evaluation")
    want = sc.A3_CANDIDATE if candidate else "v1.0"
    if ident["name"] != want:
        raise CandidateError(f"{run_id}: instrument {ident['name']!r}, expected {want!r}")
    if candidate and not ((rec.get("a3_candidate") or {}).get("decision") or "").strip():
        raise CandidateError(f"{run_id}: the candidate run carries no recorded decision")
    if int(rec["repeats"]) != sc.A3_REPEATS:
        raise CandidateError(f"{run_id}: k = {rec['repeats']}, §4b prescribes {sc.A3_REPEATS}")
    mp = RUNS / "tables" / run_id / "metrics.json"
    if not mp.exists():
        raise CandidateError(f"{run_id}: no metrics at {mp}; run evaluate.py first")
    m = json.loads(mp.read_text(encoding="utf-8"))
    prov = inp["provenance"]
    if m.get("B") != cm.B or m.get("seed") != cm.SEED:
        raise CandidateError(f"{run_id}: the metrics were resampled with B={m.get('B')}, seed={m.get('seed')}, not {cm.B}, {cm.SEED}")
    if (m.get("dev") or m.get("threshold") != cfg["threshold"] or (m.get("inputs") or {}).get("run_record_sha256") != prov["run_record_sha256"]
            or (m.get("inputs") or {}).get("reference") != prov["reference"] or m.get("analysis_status") != status):
        raise CandidateError(f"{run_id}: the metrics file was not computed from this run record, reference, threshold and status")
    S = ev.build(inp)
    counts = ev.counts_execution(S, S["pids"], 1)
    term = RUNS / f"{run_id}_terminal.csv"
    if not term.exists():
        raise CandidateError(f"{run_id}: no terminal file")
    return {"run_id": run_id, "rec": rec, "m": m, "S": S, "counts": counts, "status": status, "ident": ident,
            "terminal": list(csv.DictReader(term.open(encoding="utf-8"), delimiter=";")),
            "metrics_sha256": sha(mp), "run_record_sha256": prov["run_record_sha256"]}


def attempts(run: dict) -> dict:
    """From the terminal records: executions, invalid first responses, recoveries, terminal failures, by language."""
    out = {"executions": 0, "invalid_first": 0, "recovered": 0, "terminal_failures": 0, "first_execution_failures": 0,
           "attempts": 0, "by_language": {}}
    for r in run["terminal"]:
        codes = [c for c in (r.get("codes") or "").replace("|", ";").split(";") if c]
        lang = r.get("lang") or "?"
        bl = out["by_language"].setdefault(lang, {"executions": 0, "invalid_first": 0, "recovered": 0, "terminal_failures": 0})
        out["executions"] += 1; bl["executions"] += 1
        out["attempts"] += int(r.get("n_attempts") or len(codes) or 0)
        first_invalid = bool(codes) and codes[0] in INVALID
        success = r.get("outcome") in cm.SUCCESS
        if first_invalid:
            out["invalid_first"] += 1; bl["invalid_first"] += 1
            if success:
                out["recovered"] += 1; bl["recovered"] += 1
        if not success:
            out["terminal_failures"] += 1; bl["terminal_failures"] += 1
            if str(r.get("run_index")) == "1":
                out["first_execution_failures"] += 1
    return out


def summarise(run: dict) -> dict:
    m, rec = run["m"], run["rec"]
    pt, rs = m["point"], m["resample"]
    def iv(pop, k):
        v = rs.get(pop, {}).get(k, {})
        return [v.get("lo"), v.get("hi")]
    prec, recall = cm.element_pr(run["counts"])
    cost = (rec.get("cost") or {}).get("estimated_usd")
    at = attempts(run)
    return {"run_id": run["run_id"], "instrument": run["ident"]["name"], "retry_policy_version": run["ident"]["retry_policy_version"],
            "decision": (rec.get("a3_candidate") or {}).get("decision"),
            "element_f1": {p: pt[p]["element"]["micro_f1_weighted"] for p in ("pooled", "de", "en")},
            "element_iv": {p: iv(p, "element.micro_f1_weighted") for p in ("pooled", "de", "en")},
            "technology_f1": pt["pooled"]["technology"]["micro_f1_weighted"], "technology_iv": iv("pooled", "technology.micro_f1_weighted"),
            "element_precision": prec, "element_recall": recall,
            "evaluation_A": pt["pooled"]["evaluation_A"]["accuracy_weighted"], "evaluation_A_units": pt["pooled"]["evaluation_A"].get("units"),
            "evaluation_B": pt["pooled"]["evaluation_B"]["micro_f1_weighted"],
            "stability_any": (m.get("stability") or {}).get("pooled", {}).get("any", {}).get("weighted"),
            "attempts": at, "cost_usd": cost, "billing_complete": (rec.get("cost") or {}).get("billing_complete"),
            "metrics_sha256": run["metrics_sha256"], "run_record_sha256": run["run_record_sha256"]}


def tex_table(res: dict, head: str) -> str:
    b, c, d = res["baseline"], res["candidate"], res["paired"]
    def f(v, nd=3):
        return "--" if v is None else f"{v:.{nd}f}"
    def ivs(v):
        return "--" if not v or v[0] is None else f"[{v[0]:.3f}, {v[1]:.3f}]"
    def att(s, key):
        return str(s["attempts"][key])
    rows = [
        r"\multicolumn{3}{@{}l}{\emph{Attrition over 165 executions}} \\",
        f"Invalid first responses & {att(b, 'invalid_first')} & {att(c, 'invalid_first')} \\\\",
        f"Recovered by the retry & {att(b, 'recovered')} & {att(c, 'recovered')} \\\\",
        f"Terminal parse failures & {att(b, 'terminal_failures')} ({b['attempts']['by_language'].get('de', {}).get('terminal_failures', 0)} de, {b['attempts']['by_language'].get('en', {}).get('terminal_failures', 0)} en) & "
        f"{att(c, 'terminal_failures')} ({c['attempts']['by_language'].get('de', {}).get('terminal_failures', 0)} de, {c['attempts']['by_language'].get('en', {}).get('terminal_failures', 0)} en) \\\\",
        f"Paragraphs without a valid first execution & {att(b, 'first_execution_failures')} & {att(c, 'first_execution_failures')} \\\\",
        r"\addlinespace",
        r"\multicolumn{3}{@{}l}{\emph{Agreement with the reference, first execution (weight one; intervals indicative)}} \\",
        f"Element micro-F1, pooled & {f(b['element_f1']['pooled'])} {ivs(b['element_iv']['pooled'])} & {f(c['element_f1']['pooled'])} {ivs(c['element_iv']['pooled'])} \\\\",
        f"Element micro-F1, German / English & {f(b['element_f1']['de'])} / {f(b['element_f1']['en'])} & {f(c['element_f1']['de'])} / {f(c['element_f1']['en'])} \\\\",
        f"Element precision / recall & {f(b['element_precision'])} / {f(b['element_recall'])} & {f(c['element_precision'])} / {f(c['element_recall'])} \\\\",
        f"Technology micro-F1 & {f(b['technology_f1'])} {ivs(b['technology_iv'])} & {f(c['technology_f1'])} {ivs(c['technology_iv'])} \\\\",
        f"Evaluation, score A (units) / score B & {f(b['evaluation_A'])} ({b['evaluation_A_units']}) / {f(b['evaluation_B'])} & {f(c['evaluation_A'])} ({c['evaluation_A_units']}) / {f(c['evaluation_B'])} \\\\",
        f"Change rate over executions, any dimension & {f(b['stability_any'])} & {f(c['stability_any'])} \\\\",
        r"\addlinespace",
        r"\multicolumn{3}{@{}l}{\emph{Paired difference, candidate minus registered, common article resamples}} \\",
        f"Element micro-F1 & \\multicolumn{{2}}{{l}}{{{cm.fsign(d['element']['diff'])} {ivs([d['element']['lo'], d['element']['hi']])}}} \\\\",
        f"Technology micro-F1 & \\multicolumn{{2}}{{l}}{{{cm.fsign(d['technology']['diff'])} {ivs([d['technology']['lo'], d['technology']['hi']])}}} \\\\",
        r"\addlinespace",
        f"Estimated cost, USD & {f(b['cost_usd'], 2)} & {f(c['cost_usd'], 2)} \\\\",
    ]
    caption = ("Retry candidate on the fresh census (specification §4b): the registered instrument v1.0 and the frozen candidate "
               "v1.0r3, the registered request byte for byte under retry policy v3, in which the one retry after a schema- or "
               "contract-invalid response returns the model's own call with the validator's messages instead of resending the identical "
               "request. Both runs: 55 paragraphs, three executions, weight one. A descriptive comparison of complete configurations, "
               "decided by the author on 26 October 2026 after the v1.0 figure had been inspected; never a confirmed improvement. "
               "Brackets are the 2.5--97.5 percentile over 2{,}000 resamples of the ten census articles, the paired difference under "
               f"common resamples. Runs \\texttt{{{fg.tex_escape(b['run_id'])}}} and \\texttt{{{fg.tex_escape(c['run_id'])}}}.")
    notes = ["Invalid first responses are executions whose first attempt returned a schema- or contract-invalid call; recovered means "
             "the retry returned a valid record; a terminal parse failure contributes omissions only to the agreement figures. "
             "The reference's reliability on the census is unmeasured; the per-element figures are in the evaluator's tables."]
    header = r"Quantity & v1.0 (identical retry) & v1.0r3 (feedback retry)"
    return head + fg.table_env(caption, "tab:a3_candidate", "@{}>{\\raggedright\\arraybackslash}p{52mm}>{\\raggedright\\arraybackslash}p{44mm}>{\\raggedright\\arraybackslash}p{44mm}@{}",
                               header, rows, notes, size=r"\scriptsize", short="Retry candidate on the fresh census")   # thesis terms (2026-11-05)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate-run", default="a3_sonnet_r3_20261026")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    try:
        base = load(sc.A3_BASELINE_RUN, candidate=False)
        cand = load(a.candidate_run, candidate=True)
        paired = cm.paired(cand, base)
    except (CandidateError, cm.CompareError) as exc:
        sys.exit(f"REFUSING: {exc}")
    res = {"baseline": summarise(base), "candidate": summarise(cand), "paired": paired,
           "authority": sc.A3_CANDIDATE_AUTHORITY, "sample": "A3 census (55 paragraphs, weight one)",
           "script_sha256": sha(Path(__file__))}
    head = (f"% Generated by tools/pipeline/compare_a3_candidate.py (sha256 {res['script_sha256'][:16]}) from runs "
            f"{base['run_id']} and {cand['run_id']} and their evaluator metrics; candidate comparison of specification §4b "
            "(author's decision 2026-10-26); descriptive. Do not edit by hand.\n")
    lines = ["quantity;v1.0;v1.0r3"]
    for key in ("element_f1", "technology_f1", "element_precision", "element_recall", "evaluation_A", "evaluation_B", "stability_any", "cost_usd"):
        bv, cv = res["baseline"][key], res["candidate"][key]
        if isinstance(bv, dict):
            for p in bv:
                lines.append(f"{key}.{p};{bv[p]};{cv[p]}")
        else:
            lines.append(f"{key};{bv};{cv}")
    for key in ("executions", "attempts", "invalid_first", "recovered", "terminal_failures", "first_execution_failures"):
        lines.append(f"attempts.{key};{res['baseline']['attempts'][key]};{res['candidate']['attempts'][key]}")
    for t in ("element", "technology"):
        lines.append(f"paired_diff.{t};{paired[t]['diff']};{paired[t]['lo']};{paired[t]['hi']}")
    files = {OUT / "comparison.json": json.dumps(res, indent=2, ensure_ascii=False) + "\n",
             OUT / "comparison.csv": "\n".join(lines) + "\n",
             PAPER / "tables" / "tab_a3_candidate.tex": tex_table(res, head)}
    if a.check:
        stale = [str(p) for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            sys.exit("STALE: " + ", ".join(stale))
        print(f"  {len(files)} file(s) match the runs and metrics")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for p, text in files.items():
        p.write_text(text, encoding="utf-8")
        print(f"  wrote {p.relative_to(ROOT)}")
    b, c, d = res["baseline"], res["candidate"], paired
    print(f"  v1.0: element F1 {b['element_f1']['pooled']:.3f}, failures {b['attempts']['terminal_failures']}/{b['attempts']['executions']}; "
          f"v1.0r3: {c['element_f1']['pooled']:.3f}, failures {c['attempts']['terminal_failures']}/{c['attempts']['executions']}; "
          f"paired element diff {d['element']['diff']:+.3f} [{d['element']['lo']:+.3f}, {d['element']['hi']:+.3f}]")


if __name__ == "__main__":
    main()
