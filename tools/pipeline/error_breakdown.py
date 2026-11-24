#!/usr/bin/env python3
"""Error decomposition of the registered run (pre-analysis specification §4c, declared 2026-10-19 at the
supervisor's request): per element, precision, recall and F1; per derived frame, detection precision, recall and
F1 at the (paragraph x technology x frame) level, the evaluation accuracy on the frames the model found (score A,
per frame) and the end-to-end evaluation F1 (score B, per frame). Descriptive; the same universe, weights, threshold,
predictions and reference as the registered evaluation, loaded through the evaluator's own verified input path.
Nothing registered is recomputed: the per-element F1 must reproduce the registered per-class F1 to 1e-9 and the
element micro-F1 the registered headline, or the script refuses to write.

    .venv/bin/python3 tools/pipeline/error_breakdown.py --run-record tools/runs/a2_sonnet_20261013.json \\
        --manifest tools/annotation/labels/A2_sample_manifest.json \\
        --gold-pass1 tools/annotation/labels/A2_pass1_v1.0.csv --gold-pass2 tools/annotation/labels/A2_pass2_v1.0.csv \\
        --threshold 5 [--check]

Writes table10_error_breakdown.{csv,tex} beside the registered tables and the compact Chapter 6 tables
paper/tables/tab_element_prf.tex and tab_frame_prf.tex (every cell a value this script computed, bound to the
registered metrics file by digest).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate as ev                                           # noqa: E402
import figures as fg                                            # noqa: E402

ROOT = HERE.parents[1]
PAPER = ROOT / "paper"
POPS = ["pooled", "de", "en"]
POP_LABEL = {"pooled": "Pooled", "de": "German", "en": "English"}


def prf(tp: float, fp: float, fn: float):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else None
    return p, r, f


def decompose(inp: dict) -> dict:
    """Counts over the outer join of reference and execution-one rows (Section 5.4): a predicted row on a
    reference-off-topic paragraph is all false positives, a reference row without a predicted row all false
    negatives; design weight w_i on every count, the unweighted count beside it."""
    paras = inp["paras"]; frame_of = inp["frame_of"]
    w = {p["paragraph_id"]: float(p["weight"]) for p in paras}
    lang = {p["paragraph_id"]: p["lang"] for p in paras}
    gold = {(r["paragraph_id"], r["technology"]): ev.rel_from_row(r, frame_of) for r in inp["gold2"]}
    pred = {(r["paragraph_id"], r["technology"]): ev.rel_from_row(r, frame_of) for r in inp["pred"] if int(r["run_index"]) == 1}
    keys = set(gold) | set(pred)
    frames = ev.FRAMES
    out = {}
    for pop in POPS:
        ks = [k for k in keys if pop == "pooled" or lang[k[0]] == pop]
        el = {e: [0.0] * 6 for e in ev.ELEMENTS}            # tp_w fp_w fn_w tp_u fp_u fn_u
        det = {f: [0.0] * 6 for f in frames}
        evA = {f: [0.0, 0.0, 0, 0] for f in frames}         # ok_w, den_w, ok_u, units
        evB = {f: [0.0] * 6 for f in frames}
        sup_el = {e: 0 for e in ev.ELEMENTS}; sup_fr = {f: 0 for f in frames}; sup_B = {f: 0 for f in frames}
        for k in ks:
            wi = w[k[0]]
            g = gold.get(k); p = pred.get(k)
            ge = g.elements if g else frozenset(); pe = p.elements if p else frozenset()
            gf = {frame_of[e] for e in ge}; pf = {frame_of[e] for e in pe}
            gv = dict(g.evals) if g else {}; pv = dict(p.evals) if p else {}
            for e in ev.ELEMENTS:
                a, b = e in ge, e in pe
                if a: sup_el[e] += 1
                if a and b: el[e][0] += wi; el[e][3] += 1
                elif b: el[e][1] += wi; el[e][4] += 1
                elif a: el[e][2] += wi; el[e][5] += 1
            for f in frames:
                a, b = f in gf, f in pf
                if a: sup_fr[f] += 1
                if a and b:
                    det[f][0] += wi; det[f][3] += 1
                    evA[f][1] += wi; evA[f][3] += 1
                    if gv.get(f) == pv.get(f): evA[f][0] += wi; evA[f][2] += 1
                elif b: det[f][1] += wi; det[f][4] += 1
                elif a: det[f][2] += wi; det[f][5] += 1
                # score B: (key, frame, value) tuples
                tg = (f, gv.get(f)) if a else None; tp_ = (f, pv.get(f)) if b else None
                if a: sup_B[f] += 1
                if tg and tp_ and tg == tp_: evB[f][0] += wi; evB[f][3] += 1
                else:
                    if tp_: evB[f][1] += wi; evB[f][4] += 1
                    if tg: evB[f][2] += wi; evB[f][5] += 1
        micro = [sum(v[i] for v in el.values()) for i in range(6)]
        out[pop] = {"element": el, "detection": det, "evalA": evA, "evalB": evB, "support_element": sup_el,
                    "support_frame": sup_fr, "support_B": sup_B, "element_micro": micro, "n_keys": len(ks)}
    return out


def reproduce(dec: dict, m: dict) -> None:
    """The decomposition must reproduce the registered per-class element F1 and support, the element micro-F1, the
    score-A evaluation accuracy and matched-unit count, and the score-B evaluation micro-F1 exactly."""
    for pop in POPS:
        A = m["point"][pop]["evaluation_A"]; Bm = m["point"][pop]["evaluation_B"]
        ok = sum(v[0] for v in dec[pop]["evalA"].values()); den = sum(v[1] for v in dec[pop]["evalA"].values())
        units = sum(v[3] for v in dec[pop]["evalA"].values())
        acc = ok / den if den else None
        if units != A.get("units") or acc is None or abs(acc - A["accuracy_weighted"]) > 1e-9:
            raise SystemExit(f"REFUSING: evaluation score A ({pop}) {acc} on {units} units does not reproduce the registered "
                             f"{A['accuracy_weighted']} on {A.get('units')}")
        tp = sum(v[0] for v in dec[pop]["evalB"].values()); fp = sum(v[1] for v in dec[pop]["evalB"].values()); fn = sum(v[2] for v in dec[pop]["evalB"].values())
        bf = prf(tp, fp, fn)[2]
        if bf is None or abs(bf - Bm["micro_f1_weighted"]) > 1e-9:
            raise SystemExit(f"REFUSING: evaluation score B micro-F1 ({pop}) {bf} does not reproduce the registered {Bm['micro_f1_weighted']}")
        want = m["point"][pop]["element"]["micro_f1_weighted"]
        tp, fp, fn = dec[pop]["element_micro"][:3]
        got = prf(tp, fp, fn)[2]
        if got is None or abs(got - want) > 1e-9:
            raise SystemExit(f"REFUSING: element micro-F1 ({pop}) {got} does not reproduce the registered {want}")
        for e, pc in m["point"][pop]["element"]["per_class"].items():
            wf = pc.get("f1_weighted")
            tp, fp, fn = dec[pop]["element"][e][:3]
            gf = prf(tp, fp, fn)[2]
            if (wf is None) != (gf is None) or (wf is not None and abs(wf - gf) > 1e-9):
                raise SystemExit(f"REFUSING: element {e} ({pop}) F1 {gf} does not reproduce the registered {wf}")
            if pc.get("support_rows") != dec[pop]["support_element"][e]:
                raise SystemExit(f"REFUSING: element {e} ({pop}) support {dec[pop]['support_element'][e]} != registered {pc.get('support_rows')}")


def fmt(x, nd=3):
    return "undefined" if x is None else f"{x:.{nd}f}"


def rows_for(dec: dict, s: int) -> list[list]:
    rows = [["population", "level", "class", "precision (weighted)", "recall (weighted)", "F1 (weighted)",
             "precision (unweighted)", "recall (unweighted)", "F1 (unweighted)", "support rows",
             "evaluation A accuracy (weighted)", "evaluation A units", "evaluation B F1 (weighted)", "evaluation B support"]]
    for pop in POPS:
        d = dec[pop]
        for e in ev.ELEMENTS:
            c = d["element"][e]; n = d["support_element"][e]
            pw, rw, fw = prf(*c[:3]); pu, ru, fu = prf(*c[3:])
            state = f"n<{s} ({n})" if n < s else None
            rows.append([pop, "element", e] + ([state] * 6 if state else [fmt(pw), fmt(rw), fmt(fw), fmt(pu), fmt(ru), fmt(fu)]) + [n, "", "", "", ""])
        for f in ev.FRAMES:
            c = d["detection"][f]; n = d["support_frame"][f]
            pw, rw, fw = prf(*c[:3]); pu, ru, fu = prf(*c[3:])
            A = d["evalA"][f]; B = d["evalB"][f]
            acc = (A[0] / A[1]) if A[1] else None
            bf = prf(*B[:3])[2]
            state = f"n<{s} ({n})" if n < s else None
            stateA = f"n<{s} ({A[3]})" if A[3] < s else None          # score A is suppressed on its own denominator
            rows.append([pop, "frame", f] + ([state] * 6 if state else [fmt(pw), fmt(rw), fmt(fw), fmt(pu), fmt(ru), fmt(fu)]) +
                        [n, stateA or fmt(acc), A[3], state or fmt(bf), d["support_B"][f]])
    return rows


def tab_element_prf(dec: dict, m: dict, s: int) -> str:
    rows = []
    for e in ev.ELEMENTS:
        cells = []
        for pop in POPS:
            c = dec[pop]["element"][e]; n = dec[pop]["support_element"][e]
            pw, rw, fw = prf(*c[:3])
            cells.append(f"$n<{s}$ ({n})" if n < s else f"{fmt(pw)} / {fmt(rw)} / {fmt(fw)} ({n})")
        rows.append(f"{e} & " + " & ".join(cells) + r" \\")
    header = r"Element & Pooled P / R / F1 ($n$) & German & English"
    # 2026-11-12, M22 sweep (repair 5 of the fourth review): both captions name the first coding
    caption = (f"Per-element precision, recall and F1 of the first execution against the first coding ({fg.run_label(m)}, "
               f"reportable; descriptive decomposition declared 2026-10-19): design-weighted, over the same tuples as the "
               f"registered per-class F1, which the F1 column reproduces; reference support in rows in parentheses, "
               f"cells with fewer than {s} rows print their state.")
    notes = ["Precision is the share of predicted element tuples that the reference carries, recall the share of reference "
             "tuples the model predicted; a reference row without a predicted row counts every element as a false negative, "
             "a predicted row on a reference-off-topic paragraph as a false positive. No resampling interval (per-class cells "
             "carry none in the registered design)."]
    return fg.table_env(caption, "tab:element_prf", "@{}l>{\\raggedright\\arraybackslash}p{40mm}>{\\raggedright\\arraybackslash}p{40mm}>{\\raggedright\\arraybackslash}p{40mm}@{}",
                        header, rows, notes, size=r"\scriptsize", short="Per-element precision, recall and F1")


def tab_frame_prf(dec: dict, m: dict, s: int) -> str:
    rows = []
    for pop in POPS:
        rows.append(f"\\multicolumn{{6}}{{@{{}}l}}{{\\emph{{{POP_LABEL[pop]}}}}} \\\\")
        d = dec[pop]
        for f in ev.FRAMES:
            c = d["detection"][f]; n = d["support_frame"][f]
            pw, rw, fw = prf(*c[:3]); A = d["evalA"][f]; B = d["evalB"][f]
            acc = (A[0] / A[1]) if A[1] else None; bf = prf(*B[:3])[2]
            cellA = f"$n<{s}$ ({A[3]})" if A[3] < s else f"{fmt(acc)} ({A[3]})"
            if n < s:
                rows.append(f"{f} & $n<{s}$ ({n}) & & & {cellA} & \\\\")
            else:
                rows.append(f"{f} & {fmt(pw)} & {fmt(rw)} & {fmt(fw)} ({n}) & {cellA} & {fmt(bf)} ({d['support_B'][f]}) \\\\")
        if pop != POPS[-1]:
            rows.append(r"\addlinespace")
    header = r"Frame & Detection P & R & F1 ($n$) & Evaluation, score A (units) & Evaluation, score B F1 ($n$)"
    caption = (f"Per-frame decomposition of the first execution against the first coding ({fg.run_label(m)}, reportable; "
               f"descriptive decomposition declared 2026-10-19): precision, recall and F1 of frame detection at the "
               f"(paragraph $\\times$ technology $\\times$ frame) level, the evaluation accuracy on the frames the model "
               f"found (score A, with the matched units) and the end-to-end evaluation F1 (score B, with the reference "
               f"support); design-weighted; a frame with fewer than {s} reference units in the population prints its state, and "
               f"the score-A cell its own state when fewer than {s} units were matched.")
    notes = ["A frame is present on a side when at least one of its elements is; detection recall is therefore the share of "
             "reference frames the model found. Score A conditions on those found frames and asks whether the evaluation "
             "value agrees; score B counts a (paragraph, technology, frame, value) tuple as correct only when the frame was "
             "found and the value agrees, so the gap between the two columns is the agreement lost to frames the model "
             "did not find or invented. No resampling interval."]
    return fg.table_env(caption, "tab:frame_prf", "@{}lrrrrr@{}", header, rows, notes, size=r"\scriptsize",
                        short="Per-frame detection and evaluation, conditional and end to end")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-record", required=True); ap.add_argument("--manifest", required=True)
    ap.add_argument("--gold-pass1", required=True); ap.add_argument("--gold-pass2", required=True)
    ap.add_argument("--threshold", type=int, required=True)
    ap.add_argument("--check", action="store_true", help="recompute and compare with the files on disk; exit 1 if any differs")
    a = ap.parse_args()
    ns = argparse.Namespace(run_record=a.run_record, manifest=a.manifest, restrict_split=None, gold_pass1=a.gold_pass1,
                            gold_pass2=a.gold_pass2, dev=False, gold_manifest=None, skip_provenance=False,
                            threshold=a.threshold, reliability=None)
    try:
        inp = ev.load_inputs(ns)
    except ev.EvalError as exc:
        sys.exit(f"REFUSING: {exc}")
    rec = inp["rec"]
    if rec.get("analysis_status") not in (None, "registered primary"):
        sys.exit("REFUSING: the error decomposition is declared for the registered run only (§4c)")
    out_dir = ev.RUNS / "tables" / rec["run_id"]
    mpath = out_dir / "metrics.json"
    if not mpath.exists():
        sys.exit(f"REFUSING: no registered metrics at {mpath}; evaluate first")
    raw = mpath.read_bytes(); m = json.loads(raw.decode("utf-8")); mdig = hashlib.sha256(raw).hexdigest()
    if m.get("threshold") != a.threshold or m.get("dev"):
        sys.exit("REFUSING: the registered metrics carry another threshold or are development metrics")
    dec = decompose(inp)
    reproduce(dec, m)
    s = a.threshold
    rows = rows_for(dec, s)
    here = Path(__file__).resolve()
    head = (f"% Generated by tools/pipeline/error_breakdown.py (sha256 {hashlib.sha256(here.read_bytes()).hexdigest()[:16]}) "
            f"from run {rec['run_id']}: metrics.json sha256 {mdig}; declared in the pre-analysis specification §4c (2026-10-19), "
            f"descriptive; the per-element F1 reproduces the registered per-class F1. Do not edit by hand.\n")
    csv_text = "\n".join(";".join(str(c) for c in r) for r in rows) + "\n"
    files = {
        out_dir / "table10_error_breakdown.csv": csv_text,
        PAPER / "tables" / "tab_element_prf.tex": head + tab_element_prf(dec, m, s),
        PAPER / "tables" / "tab_frame_prf.tex": head + tab_frame_prf(dec, m, s),
    }
    stale = []
    for path, text in files.items():
        if a.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path)
            continue
        path.write_text(text, encoding="utf-8"); print(f"  wrote {path.relative_to(ROOT)}")
    if a.check:
        if stale:
            sys.exit("STALE: " + ", ".join(str(p) for p in stale))
        print(f"  {len(files)} file(s) match (metrics sha256 {mdig[:16]})")
        return
    d = dec["pooled"]
    for f in ev.FRAMES:
        c = d["detection"][f]; pw, rw, fw = prf(*c[:3]); A = d["evalA"][f]; B = d["evalB"][f]
        print(f"  {f:12s} n={d['support_frame'][f]:3d} detection P {fmt(pw)} R {fmt(rw)} F1 {fmt(fw)} | eval A {fmt((A[0]/A[1]) if A[1] else None)} ({A[3]}) | eval B F1 {fmt(prf(*B[:3])[2])}")


if __name__ == "__main__":
    main()
