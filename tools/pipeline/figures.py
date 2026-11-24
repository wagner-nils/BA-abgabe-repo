#!/usr/bin/env python3
"""Figures and instrument tables for the thesis, rendered as pgfplots/booktabs.

Runs under the pipeline interpreter (.venv), which has PyYAML for the codebook.
Two subcommands:

  codebook   paper/tables/tab_codebook_elements.tex
             the frozen codebook's nine frames and seventeen elements, one row per
             element; the instrument's shape, no measured quantity (Chapter 4).

  results    <out-dir>/fig_primary.tex        headline scores per dimension and
                                              population with resample intervals
                                              (appendix; registered and fresh sample)
             <out-dir>/fig_elements.tex       two panels per frame element: a, F1 with
                                              precision and recall of the error
                                              decomposition; b, prevalence displacement
                                              with its interval (registered, Chapter 6)
             <out-dir>/fig_headline.tex       panel b of the introduction's Figure 1:
                                              technology F1, evaluation accuracy and
                                              element F1 with element precision and
                                              recall (registered; a tikzpicture that
                                              paper/images/fig_overview.tex inputs)
             <out-dir>/fig_displacement.tex   prevalence displacement per element, pp,
                                              with percentile interval (fresh sample)
             from an evaluator metrics.json. Cells the evaluator suppressed
             (reported = false) or left undefined are omitted, never imputed.
             Per-element precision and recall come from the error decomposition
             beside the metrics file (table10_error_breakdown.csv, written by
             error_breakdown.py), whose F1 column must reproduce the metrics; the
             pooled element precision and recall of Figure 1b come from the model
             comparison (model_comparison/comparison.json), whose registered row must
             name this metrics file by digest. Every figure uses the shared style of
             the thesis preamble (thesisfig, the fig* colours and the mark styles).

The results figures are exercised on development metrics (--dev) into the run's
own table directory; a reportable metrics.json writes into paper/images/. The
reportable path refuses a metrics file whose `dev` flag is true, so development
numbers cannot enter the thesis by this route. Development numbers inform no
design choice (pre-analysis specification, §3).

  tables     paper/tables/tab_*.tex           twelve compact thesis tables (run record,
                                              agreement, per class, categorical, stability,
                                              displacement, reliability, clean domain,
                                              attrition, limits) from a reportable metrics.json
                                              and its run record, plus the W3/W4 development
                                              table from the committed selection records.
             Every cell is a value the evaluator wrote, formatted; nothing is recomputed.
             Since 2026-11-05 tab_primary.tex (Chapter 6) carries the six micro rows with
             dimension-specific score names and no run identifier; tab_primary_full.tex
             (appendix) adds the macro-F1 rows and the whole-row exact match.

  dsl        paper/images/fig_dsl.tex         Figure 6 (added analysis): per reportable element
                                              the adjusted (DSL) prevalence with its design-based
                                              interval, the sample-only estimate (reference
                                              +- 1.96 reference SE) and the model-only share,
                                              from tools/runs/tables/dsl/dsl_prevalence.json.

  language   paper/images/fig_language_arm.tex  Figure 8 (added analysis): paired differences,
             paper/tables/tab_language_arm.tex  translated minus original German input; and the
                                              appendix table of the arm, from
                                              tools/runs/tables/language_arm/language_arm.json and
                                              the translation record it binds by digest.
             Both read committed outputs only (no recomputation). dsl.py (tab_dsl as a longtable) and
             compare_models.py (Figure 7 labels and terms) changed on 2026-11-05 for presentation only;
             their outputs, and language_arm.json through its compare_models digest, were rewritten
             with no change but the recorded script digests.

Usage:
    .venv/bin/python3 tools/pipeline/figures.py codebook
    .venv/bin/python3 tools/pipeline/figures.py tables --metrics tools/runs/tables/<run>/metrics.json
    .venv/bin/python3 tools/pipeline/figures.py results --metrics tools/runs/tables/dev/<run>/metrics.json --dev
    .venv/bin/python3 tools/pipeline/figures.py results --metrics tools/runs/tables/<run>/metrics.json
    .venv/bin/python3 tools/pipeline/figures.py dsl [--check]
    .venv/bin/python3 tools/pipeline/figures.py language [--check]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "paper"
CODEBOOK = ROOT / "tools" / "annotation" / "codebook" / "codebook-v1.0.yaml"

POPULATIONS = ["pooled", "de", "en"]
POP_LABEL = {"pooled": "Pooled", "de": "German", "en": "English"}

# dimension key in metrics -> (label, resample key, point key)
DIMENSIONS = [
    ("technology", "Technology detection", "micro_f1_weighted"),
    ("element", "Frame elements", "micro_f1_weighted"),
    ("evaluation_A", "Evaluation, score A", "accuracy_weighted"),
    ("evaluation_B", "Evaluation, score B", "micro_f1_weighted"),
    ("comparison_A", "Comparison target, score A", "accuracy_weighted"),
    ("comparison_B", "Comparison target, score B", "micro_f1_weighted"),
]

# mark style by population, defined once in the thesis preamble (circle pooled, square German, triangle English)
POP_STYLE = {"pooled": "pooled", "de": "german", "en": "english"}
# vertical offset of the three populations within a row, in row units, so that intervals never overlap
POP_OFFSET = {"de": -0.26, "pooled": 0.0, "en": 0.26}
# short element names for figure rows (plain name first, code in parentheses); the full labels are the codebook's
ELEMENT_SHORT = {
    "F1a": "Cost or price", "F1b": "Subsidies", "F2a": "Employment", "F2b": "Investment, industry",
    "F2c": "Deployment growth", "F3a": "Emissions, climate", "F3b": "Transition pathway",
    "F4a": "Import dependence", "F4b": "Security of supply", "F5a": "Intermittency", "F5b": "Grid integration",
    "F6a": "Technical feasibility", "F6b": "Technical performance", "F7a": "Land requirement",
    "F7b": "Landscape", "F8a": "Regulation", "F9": "Other frame",
}
ALPHA_MARK = 2 / 3      # reading aid for the coder's re-code (Chapter 6), not a registered threshold
# Palette 2026-11-06 (thesis preamble, one meaning per colour across every figure): the registered model, its scores and
# its shortfall against the reference in model blue (figModel; the displacement marks' alias figBelow resolves to it),
# its surplus in orange (figAbove), a changed configuration in vermillion (figChanged: the translated input), estimates
# that combine model and coder in green (figAdjusted), the coder and the sample-only estimate in black (figInk). Every
# colour is doubled by a shape, a fill or a position, so that the figures read in greyscale. Changed here: the headline's
# precision and recall labels (model blue), the DSL figure's three estimators (green, blue, black) and the language arm's
# marks and caption (vermillion); the displacement captions now say orange. Numbers, shapes and fills are unchanged.


def element_name(code: str) -> str:
    return f"{ELEMENT_SHORT.get(code, code)} ({code})"


def tex_escape(s: str) -> str:
    return (s.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%")
             .replace("_", r"\_").replace("#", r"\#").replace("$", r"\$"))


# ------------------------------------------------------------------ codebook

# Where each frame of the codebook comes from (X5, construct validity; 2026-11-06): the five frames the topic description
# names, collapsed from the Policy Frames Codebook and Toetzke et al.'s labels (Chapter 2); three added in v0.2 after
# calibration session A0 showed gaps (the codebook's own record); the residual frame as in the Policy Frames Codebook.
FRAME_SOURCE = {
    "costs": "Topic description", "jobs": "Topic description", "climate": "Topic description",
    "security": "Topic description", "reliability": "Topic description",
    # 2026-11-12, second cut pass (75k): the source cell reads "Calibration", without the "(v0.2)" qualifier
    "feasibility": "Calibration", "siting": "Calibration", "regulation": "Calibration",
    "other": "Residual",
}


def table_codebook() -> str:
    cb = yaml.safe_load(CODEBOOK.read_text(encoding="utf-8"))
    frames: dict[str, list[str]] = cb["frame_derivation"]
    elements = {e["id"]: e for e in cb["frame_elements"]}
    n_el = len(elements)
    lines = [
        f"% Generated by tools/pipeline/figures.py codebook from codebook-v{cb['version']}.yaml.",
        r"% Do not edit by hand.",
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\begin{threeparttable}",
        # 2026-11-09, second slimming pass (G-83): caption syntax tightened
        f"\\caption[Frame elements and frames of codebook v{cb['version']}]{{Frozen codebook (v{cb['version']}): {n_el} frame "
        f"elements and {len(frames)} derived frames. A frame fires when any of its elements fires; "
        r"coder and model mark elements only.}",
        r"\label{tab:codebook_elements}",
        r"\begin{tabular}{@{}ll>{\raggedright\arraybackslash}p{80mm}l@{}}",
        r"\toprule",
        r"\textbf{Frame} & \textbf{Element} & \textbf{Label} & \textbf{Source} \\",
        r"\midrule",
    ]
    first = True
    for frame, ids in frames.items():
        if not first:
            lines.append(r"\addlinespace")
        first = False
        for i, eid in enumerate(ids):
            e = elements[eid]
            label = tex_escape(e["label"])
            marks = []
            if e.get("added_in"):
                marks.append(f"added in v{e['added_in']}")
            if e.get("split_in_v1_0"):
                marks.append("narrowed in v1.0")
            if e.get("requires_note"):
                marks.append("requires a written note")
            suffix = f" ({'; '.join(marks)})" if marks else ""
            lines.append(f"{frame.capitalize() if i == 0 else ''} & {eid} & {label}{suffix} & "
                         f"{FRAME_SOURCE.get(frame, '') if i == 0 else ''} \\\\")
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\begin{tablenotes}\footnotesize",
        # 2026-11-09, slimming debate consensus F-06 (the author's note: even shorter)
        # 2026-11-10 (third review, provenance): one account of where the frames come from; the crosswalk is tab:frame_crosswalk
        # 2026-11-12, M16 cut pass (K2-01, K2-02): the provenance account and the crosswalk pointer stand in Section 2.1
        # 2026-11-12, second cut pass (75k): note wording shortened ("named but unframed", "Codes such as")
        r"\item A named but unframed technology takes "
        r"\emph{no frame for this technology}. Codes such as F1a are identifiers, not F1 scores.",
        r"\end{tablenotes}",
        r"\end{threeparttable}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- results

def fmt(x: float | None, nd: int = 3) -> str:
    return "" if x is None else f"{x:.{nd}f}"


def big(n: int) -> str:
    """A count with a thin thousands separator as the thesis text prints it (2{,}000)."""
    return f"{n:,}".replace(",", "{,}")


def _rows_axis(labels: list[str]) -> list[str]:
    """Axis options for numeric rows 1..n read top to bottom, labelled by `labels` (no dropped categories)."""
    n = len(labels)
    return [f"  ymin=0.4, ymax={n + 0.6}, y dir=reverse, rowaxis,",
            f"  ytick={{{','.join(str(i + 1) for i in range(n))}}},",
            f"  yticklabels={{{','.join('{' + l + '}' for l in labels)}}},"]


def _primary_title(m: dict) -> str:
    """The finding title of the headline figure when the data support it: the pooled scores spread more across the label
    dimensions than German and English differ in any dimension, and the two languages' intervals overlap in every one."""
    pt, rs = m["point"], m["resample"]
    pooled = [pt["pooled"][k][s] for k, _, s in DIMENSIONS if pt.get("pooled", {}).get(k, {}).get(s) is not None]
    lang_gap, overlap = 0.0, True
    for k, _, s in DIMENSIONS:
        de, en = pt.get("de", {}).get(k, {}).get(s), pt.get("en", {}).get(k, {}).get(s)
        if de is None or en is None:
            continue
        lang_gap = max(lang_gap, abs(de - en))
        a, b = rs.get("de", {}).get(f"{k}.{s}", {}), rs.get("en", {}).get(f"{k}.{s}", {})
        if None in (a.get("lo"), a.get("hi"), b.get("lo"), b.get("hi")) or a["hi"] < b["lo"] or b["hi"] < a["lo"]:
            overlap = False
    if pooled and max(pooled) - min(pooled) > lang_gap and overlap:
        return ("Agreement differs more between label dimensions than between languages, whose intervals overlap in every "
                "dimension.")
    return "Agreement with the reference by scoring dimension and language."


def fig_primary(m: dict, fresh: bool = False) -> str:
    """Dot plot: one row per dimension, one mark per population offset within the row, resample interval; open marks on
    the fresh sample (the figure style: evaluation sample filled, fresh sample open, same shape)."""
    labels = [d[1] for d in DIMENSIONS]
    opn = ", open" if fresh else ""
    plots = []
    for pop in POPULATIONS:
        pts = []
        for i, (key, label, score) in enumerate(DIMENSIONS):
            p = m["point"].get(pop, {}).get(key, {}).get(score)
            iv = m["resample"].get(pop, {}).get(f"{key}.{score}", {})
            if p is None:
                continue
            lo, hi = iv.get("lo"), iv.get("hi")
            if lo is None or hi is None:
                lo, hi = p, p
            pts.append(f"({p:.4f},{i + 1 + POP_OFFSET[pop]:.2f}) -= ({p - lo:.4f},0) += ({hi - p:.4f},0)")
        plots.append(f"\\addplot[{POP_STYLE[pop]}{opn}, error bars/.cd, x dir=both, x explicit] coordinates {{{' '.join(pts)}}};")
        plots.append(f"\\addlegendentry{{{POP_LABEL[pop]} ($n$ = {m['populations'][pop]})}}")
    return "\n".join([
        f"% Generated by tools/pipeline/figures.py from {m['run_id']}/metrics.json. Do not edit by hand.",
        r"\begin{figure}[htbp]",
        r"\centering",
        r"\begin{tikzpicture}",
        r"\begin{axis}[thesisfig,",
        r"  width=105mm, height=78mm, scale only axis=false,",
        r"  xmin=0, xmax=1, xtick={0,0.2,0.4,0.6,0.8,1}, xlabel={Score (micro-F1 or accuracy)},",
        *_rows_axis(labels),
        r"  legend columns=3,",
        r"]",
        *plots,
        r"\end{axis}",
        r"\end{tikzpicture}",
        f"\\caption[Headline agreement by dimension and language]{{\\textbf{{{_primary_title(m)}}} The model's first execution "
        f"against the human reference: circles pooled, squares German, triangles English"
        f"{'; open marks: fresh sample' if fresh else ''}; {weight_word(m, False)} point estimates with 2.5--97.5 percentile "
        f"intervals over {big(m['B'])} article resamples. Score A: accuracy on the matched reference units; score B: micro-F1 end "
        f"to end. Run \\texttt{{{tex_escape(m['run_id'])}}}.}}",
        r"\label{fig:primary}",
        r"\end{figure}",
    ]) + "\n"


def load_decomposition(metrics_path: Path, m: dict) -> dict[str, tuple[float, float]] | None:
    """Pooled per-element (precision, recall) of the error decomposition beside the metrics file
    (table10_error_breakdown.csv, written by error_breakdown.py), or None if the file is absent. Its F1 column must
    reproduce the metrics' per-class F1 to the printed precision, or the figure is refused."""
    path = metrics_path.parent / "table10_error_breakdown.csv"
    if not path.exists():
        return None
    out = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    head = lines[0].split(";")
    ip, ir, i1 = head.index("precision (weighted)"), head.index("recall (weighted)"), head.index("F1 (weighted)")
    for line in lines[1:]:
        c = line.split(";")
        if c[0] != "pooled" or c[1] != "element":
            continue
        try:
            p, r, f = float(c[ip]), float(c[ir]), float(c[i1])
        except ValueError:          # suppressed cell (n<s) or undefined
            continue
        want = m["point"]["pooled"]["element"]["per_class"][c[2]].get("f1_weighted")
        if want is None or abs(round(want, 3) - f) > 1e-9:
            sys.exit(f"REFUSING: {path.name} F1 for {c[2]} ({f}) does not reproduce the metrics ({want}).")
        out[c[2]] = (p, r)
    return out


def load_pooled_pr(m: dict, digest: str) -> tuple[float, float]:
    """Pooled element micro precision and recall of the registered first execution from the model comparison, whose
    registered row must name this metrics file by digest and whose harmonic mean must reproduce the headline F1."""
    path = RUNS / "tables" / "model_comparison" / "comparison.json"
    res = json.loads(path.read_text(encoding="utf-8"))
    row = next((r for r in res["rows"]["A2"] if r["run_id"] == m["run_id"]), None)
    if row is None or row.get("metrics_sha256") != digest:
        sys.exit(f"REFUSING: {path} has no row bound to this metrics file by digest.")
    p, r = row["element_precision"], row["element_recall"]
    if abs(2 * p * r / (p + r) - m["point"]["pooled"]["element"]["micro_f1_weighted"]) > 1e-9:
        sys.exit("REFUSING: the comparison's element precision and recall do not reproduce the headline F1.")
    return p, r


def _displacement_rows(m: dict, pop: str = "pooled") -> dict[str, tuple[float, float, float]]:
    """Reportable element displacements (weighted pp, lo, hi): the sealed reporting rule of the displacement table, a
    class is shown only when the reference carries it on at least s paragraphs."""
    disp, rs, s = m["displacement"][pop], m["resample"][pop], int(m["threshold"])
    out = {}
    for k, d in disp.items():
        if not k.startswith("element:"):
            continue
        v = d.get("weighted_pp")
        if v is None or int(d.get("gold_paragraphs", 0)) < s:
            continue
        iv = rs.get(f"displacement.{k}", {})
        lo, hi = iv.get("lo"), iv.get("hi")
        out[k.split(":", 1)[1]] = (v, v if lo is None else lo, v if hi is None else hi)
    return out


ZERO_PP = 0.05          # a displacement within +-0.05 pp prints as 0.0 and is drawn as no displacement (grey)


def _signed_marks(rows: list[str], d: dict, ypos) -> list[str]:
    """Displacement marks: model blue below the reference (figBelow, the preamble's alias of figModel: the model's
    shortfall) / orange above it (figAbove: its surplus), grey on it (no displacement), filled where the interval excludes
    zero; the side of the dashed zero line doubles the colour in greyscale."""
    groups: dict[tuple[str, bool], list[str]] = {}
    for i, e in enumerate(rows):
        if e not in d:
            continue
        v, lo, hi = d[e]
        side = "figBelow" if v < -ZERO_PP else "figAbove" if v > ZERO_PP else "figGreyA"
        excl = hi < 0 or lo > 0
        groups.setdefault((side, excl), []).append(f"({v:.3f},{ypos(i)}) -= ({v - lo:.3f},0) += ({hi - v:.3f},0)")
    plots = []
    for (side, excl), pts in sorted(groups.items()):
        fill = side if excl else "white"
        plots.append(f"\\addplot[only marks, mark=*, draw={side}, mark options={{draw={side}, fill={fill}, line width=0.8pt}}, "
                     f"error bars/.cd, x dir=both, x explicit, error bar style={{{side}}}] coordinates {{{' '.join(pts)}}};")
    return plots


def _sign_key(d: dict) -> str:
    """The colour key of a displacement panel; the grey clause only when an element sits on the reference."""
    grey = any(abs(v[0]) <= ZERO_PP for v in d.values())
    return "blue below and orange above the reference" + (", grey on it" if grey else "")


def _disp_axis(d: dict) -> str:
    """Explicit limits and ticks for a displacement axis: multiples of the tick step that include zero and every interval
    (a symmetric default axis when no element is reportable)."""
    if not d:
        return "  xmin=-10, xmax=10, xtick={-10,0,10},"
    lo = min(min(v) for v in d.values())
    hi = max(max(v) for v in d.values())
    step = 10 if hi - lo > 25 else 5
    xlo, xhi = step * math.floor(min(lo, 0) / step), step * math.ceil(max(hi, 0) / step)
    xhi = max(xhi, xlo + 2 * step)
    return f"  xmin={xlo}, xmax={xhi}, xtick={{{','.join(str(t) for t in range(xlo, xhi + 1, step))}}},"


def fig_elements(m: dict, dec: dict | None) -> str:
    """Two panels with shared element rows sorted by registered F1: a, F1 (filled) with precision and recall of the
    error decomposition (counted on paragraph-technology rows); b, prevalence displacement (a share of paragraphs) with
    its interval, dashed zero line."""
    pc = m["point"]["pooled"]["element"]["per_class"]
    f1 = {e: c["f1_weighted"] for e, c in pc.items() if c.get("reported") and c.get("f1_weighted") is not None}
    d = _displacement_rows(m)
    rows = sorted(set(f1) | set(d), key=lambda e: (-(f1.get(e) if e in f1 else -1), ELEMENT_ORDER.index(e)))
    rel = (m.get("reliability") or {}).get("elements", {})
    s = int(m["threshold"])
    # the reading aid of Chapter 6: elements with at least s first-coding positives whose re-code alpha is below 0.667
    low_alpha = {e for e, r in rel.items()
                 if r.get("support_rows", 0) >= s and r.get("alpha") is not None and r["alpha"] < ALPHA_MARK}
    # a full-size dagger in the label font (a superscript one would fall below the 7 pt floor of the figure style)
    labels = [element_name(e) + (r"\,\dag" if e in low_alpha else "") for e in rows]
    y = lambda i: i + 1
    seg, pmarks, rmarks, fmarks = [], [], [], []
    for i, e in enumerate(rows):
        if dec and e in dec:
            p, r = dec[e]
            seg.append(f"\\draw[figGreyB, line width=0.8pt] (axis cs:{min(p, r):.4f},{y(i)}) -- (axis cs:{max(p, r):.4f},{y(i)});")
            pmarks.append(f"({p:.4f},{y(i)})")
            rmarks.append(f"({r:.4f},{y(i)})")
        if e in f1:
            fmarks.append(f"({f1[e]:.4f},{y(i)})")
    pa = [r"\begin{axis}[thesisfig, name=pa,",
          f"  width=50mm, height={6 * len(rows) + 10}mm, scale only axis,",
          r"  xmin=0, xmax=1, xtick={0,0.2,0.4,0.6,0.8,1}, xticklabels={0,0.2,0.4,0.6,0.8,1}, xlabel={Agreement with the reference},",
          *_rows_axis(labels),
          r"  legend columns=3,",
          r"]",
          *seg]
    if dec:
        pa += [f"\\addplot[precisionmark] coordinates {{{' '.join(pmarks)}}};", r"\addlegendentry{Precision}",
               f"\\addplot[recallmark] coordinates {{{' '.join(rmarks)}}};", r"\addlegendentry{Recall}"]
    pa += [f"\\addplot[pooled] coordinates {{{' '.join(fmarks)}}};", r"\addlegendentry{F1 (registered)}",
           r"\end{axis}",
           # panel letters on one baseline: a at the left edge of panel a with its row labels, b at panel b's corner
           r"\node[panellabel] at ([yshift=6mm]pa.north -| current bounding box.west) {a};"]
    # the alpha column right of panel b: the coder's re-code alpha where the first coding has at least s positives,
    # otherwise suppressed (the reporting rule of the reliability table); right-aligned at a fixed distance from panel b
    def alpha_cell(e: str) -> str:
        r = rel.get(e)
        if r is None or r.get("support_rows", 0) < s:
            return r"\textcolor{figGreyA}{suppressed}"
        return "undefined" if r.get("alpha") is None else f"{r['alpha']:.3f}"
    pb_axis = _disp_axis(d)
    xmax = re.search(r"xmax=(-?\d+)", pb_axis).group(1)
    acol = []
    if rel:                 # development metrics carry no re-code: no column
        acol = [f"\\node[figtext, anchor=east, xshift=17mm] at (axis cs:{xmax},{y(i)}) {{{alpha_cell(e)}}};"
                for i, e in enumerate(rows)]
        acol.append(f"\\node[figtext, anchor=south east, xshift=17mm] at (axis cs:{xmax},0.4) {{Re-code $\\alpha$}};")
    pb = [r"\begin{axis}[thesisfig, name=pb, at={(pa.south east)}, anchor=south west, xshift=12mm,",
          f"  width=38mm, height={6 * len(rows) + 10}mm, scale only axis,",
          pb_axis,
          r"  xlabel={Displacement (percentage points)},",
          f"  ymin=0.4, ymax={len(rows) + 0.6}, y dir=reverse, rowaxis, ytick=\\empty,",
          r"]",
          f"\\draw[refline] (axis cs:0,0.4) -- (axis cs:0,{len(rows) + 0.6});",
          *_signed_marks(rows, d, y),
          *acol,
          r"\end{axis}",
          r"\node[panellabel] at ([xshift=-3mm, yshift=6mm]pb.north west) {b};"]
    n = m["populations"]["pooled"]
    missed = sum(1 for e in rows if dec and e in dec and dec[e][0] > dec[e][1])
    down = sum(1 for e in rows if e in d and d[e][0] < -ZERO_PP)
    # 2026-11-10, §4n integration consensus G-28: the direction is stated against the first coding
    title = ("Against the first coding, most frame elements have lower recall than precision and lower paragraph shares."
             if dec and 2 * missed > len(rows) and 2 * down > len(d) else "Agreement and prevalence displacement per frame element.")
    # the aggregation step from tuples (panel a) to paragraph shares (panel b), with the element that shows it most
    # plainly when one exists: precision above recall in a, yet a positive displacement in b
    diverge = [e for e in rows if dec and e in dec and e in d and dec[e][0] > dec[e][1] and d[e][0] > ZERO_PP]
    ex = max(diverge, key=lambda e: d[e][0]) if diverge else None
    # 2026-11-09, slimming debate consensus N-03 with C6-09: the caption keeps both scoring units, the timing qualifier,
    # every key and threshold, and the worked divergence with its displacement interval (moved here from Section 6.5)
    def m1(v):
        return f"${v:+.1f}$"
    # 2026-11-12, second cut pass (75k): "on any technology row" shortened to "on any row"
    agg = (r" Paragraph shares count an element on any row, so the panels can differ."
           + (f" {ELEMENT_SHORT.get(ex, ex)[:1].upper()}{ELEMENT_SHORT.get(ex, ex)[1:]}: precision {dec[ex][0]:.3f}, "
              f"recall {dec[ex][1]:.3f}, yet displacement {m1(d[ex][0])} [{m1(d[ex][1])}, {m1(d[ex][2])}] points."
              if ex else ""))
    grey = ", grey: on" if any(abs(v[0]) <= ZERO_PP for v in d.values()) else ""
    n_rel = (m.get("reliability") or {}).get("n_paragraphs")
    cap = (r"\caption[Agreement and prevalence displacement per frame element]{\textbf{" + title + r"} "
           + f"{weight_word(m)}, {n} paragraphs. Elements with at least {s} reference rows, sorted by F1. "
           + r"\textbf{a},~F1"   # 2026-11-09, second slimming pass (K-17): the legend keys the three marks
           # 2026-11-12, second cut pass (75k): "added after the registered result" shortened to "added later"
           + (r" and, added later, precision and recall over (paragraph, technology, element) "
              r"tuples." if dec else   # 2026-11-12, M16 cut pass (F-25)
              r" over (paragraph, technology, element) tuples.")
           + r" \textbf{b},~Paragraph-share displacement (model minus reference), with 2.5--97.5 percentile intervals. "
           r"Filled: interval excludes zero. Blue: below, orange: above" + grey + r" the reference (dashed)." + agg
           + ((r" Right: unweighted $\alpha$ of the registered re-code"   # 2026-11-10, consensus G-29
               + (f" ({n_rel} paragraphs), suppressed below {s} first-coding positives." if n_rel else
                  f", suppressed below {s} first-coding positives.")
               + r" \dag: $\alpha < 0.667$.")   # 2026-11-12, M16 cut pass (F-04): the pointers stand in Section 6.2
              if rel else "") + "}")
    return "\n".join([
        f"% Generated by tools/pipeline/figures.py from {m['run_id']}/metrics.json. Do not edit by hand.",
        r"% Palette 2026-11-06: panel b marks model blue (figBelow = figModel) below and orange (figAbove) above the reference, grey on it; panel a takes model blue from the preamble styles pooled, precisionmark and recallmark. Shapes, fills and positions unchanged.",
        r"\begin{figure}[htbp]",
        r"\centering",
        r"\begin{tikzpicture}",
        *pa, *pb,
        r"\end{tikzpicture}",
        cap,
        r"\label{fig:elements}",
        r"\end{figure}",
    ]) + "\n"


def fig_headline(m: dict, pr: tuple[float, float]) -> str:
    """Panel b of Figure 1 (a tikzpicture, no float): technology F1, evaluation accuracy on matched units and element
    F1 with their intervals, and below the element row, on a line of its own so that it cannot be read as a wider
    interval, the element precision and recall of the first execution, labelled directly in model blue (the open square
    and the open triangle, with their labels, double the colour in greyscale)."""
    # each row names its metric (form review X17, 2026-11-06); "shared frames", the abstract's name for matched-frame
    # accuracy (2026-11-12, M22 page review, finding 2: "found frames" could read as the model's frames)
    rows = [("technology", "micro_f1_weighted", "Technologies, micro-F1"),
            ("evaluation_A", "accuracy_weighted", "Evaluation in shared frames, accuracy"),
            ("element", "micro_f1_weighted", "Frame elements, micro-F1")]
    pts, labs = [], []
    for i, (key, score, _) in enumerate(rows):
        p = m["point"]["pooled"][key][score]
        iv = m["resample"]["pooled"][f"{key}.{score}"]
        pts.append(f"({p:.4f},{i + 1}) -= ({p - iv['lo']:.4f},0) += ({iv['hi'] - p:.4f},0)")
        labs.append(f"\\node[figtext, right=3pt] at (axis cs:{iv['hi']:.4f},{i + 1}) {{{p:.3f}}};")
    p, r = pr
    ye = len(rows) + 0.42            # the precision-recall line sits under the element row
    return "\n".join([
        f"% Generated by tools/pipeline/figures.py from {m['run_id']}/metrics.json. Do not edit by hand.",
        r"% Panel b of Figure 1 (paper/images/fig_overview.tex inputs this tikzpicture); element precision and recall from",
        r"% tools/runs/tables/model_comparison/comparison.json, the registered row bound to this metrics file by digest.",
        r"% Palette 2026-11-06: the precision and recall labels in model blue (figModel), as their open marks; the score marks take model blue from the preamble style pooled. Shapes and positions unchanged.",
        r"\begin{tikzpicture}",
        r"\begin{axis}[thesisfig,",
        r"  width=96mm, height=30mm, scale only axis,",
        r"  xmin=0, xmax=1, xtick={0,0.5,1}, xticklabels={0,0.5,1}, minor xtick={0.1,0.2,0.3,0.4,0.6,0.7,0.8,0.9}, xlabel={Score},",
        f"  ymin=0.4, ymax={ye + 0.38:.2f}, y dir=reverse, rowaxis,",
        f"  ytick={{{','.join(str(i + 1) for i in range(len(rows)))}}},",
        f"  yticklabels={{{','.join('{' + x[2] + '}' for x in rows)}}},",
        r"]",
        f"\\draw[figGreyB, line width=0.8pt] (axis cs:{min(p, r):.4f},{ye}) -- (axis cs:{max(p, r):.4f},{ye});",
        f"\\addplot[precisionmark] coordinates {{({p:.4f},{ye})}};",
        f"\\addplot[recallmark] coordinates {{({r:.4f},{ye})}};",
        f"\\addplot[pooled, error bars/.cd, x dir=both, x explicit] coordinates {{{' '.join(pts)}}};",
        *labs,
        f"\\node[figtext, {'left' if r < p else 'right'}=3pt, text=figModel] at (axis cs:{r:.4f},{ye}) {{recall {r:.2f}}};",
        f"\\node[figtext, {'right' if r < p else 'left'}=3pt, text=figModel] at (axis cs:{p:.4f},{ye}) {{precision {p:.2f}}};",
        r"\end{axis}",
        # the panel letter is the picture's left edge, so that it aligns with panel a's letter (fig_overview.tex)
        r"\node[panellabel] at ([yshift=2mm]current axis.north -| current axis.outer west) {b};",
        r"\end{tikzpicture}",
    ]) + "\n"


def fig_displacement(m: dict, pop: str = "pooled") -> str:
    """Horizontal dot plot: weighted displacement in percentage points with the resample interval, rows sorted by value."""
    d = _displacement_rows(m, pop)
    rows = sorted(d, key=lambda e: (d[e][0], ELEMENT_ORDER.index(e)))
    y = lambda i: i + 1
    title = (f"Of the {len(d)} elements, {sum(1 for v in d.values() if v[0] < -ZERO_PP)} are marked on fewer paragraphs by the "
             f"model than by the reference." if d else "No element reaches the reporting threshold.")
    return "\n".join([
        f"% Generated by tools/pipeline/figures.py from {m['run_id']}/metrics.json. Do not edit by hand.",
        r"% Palette 2026-11-06: marks model blue (figBelow = figModel) below and orange (figAbove) above the reference, grey on it; the caption names the colours. Shapes, fills and positions unchanged.",
        r"\begin{figure}[htbp]",
        r"\centering",
        r"\begin{tikzpicture}",
        r"\begin{axis}[thesisfig,",
        f"  width=80mm, height={6 * len(rows) + 10}mm, scale only axis,",
        _disp_axis(d),
        r"  xlabel={Displacement (percentage points)},",
        *_rows_axis([element_name(e) for e in rows]),
        r"]",
        f"\\draw[refline] (axis cs:0,0.4) -- (axis cs:0,{len(rows) + 0.6});",
        *_signed_marks(rows, d, y),
        r"\end{axis}",
        r"\end{tikzpicture}",
        f"\\caption[Prevalence displacement per frame element]{{\\textbf{{{title}}} {weight_word(m)} prevalence displacement per "
        f"element in the {POP_LABEL[pop].lower()} population, the model's share of paragraphs minus the reference's, in percentage "
        f"points of {frame_word(m)}, with 2.5--97.5 percentile intervals over {big(m['B'])} article resamples. Marks are filled "
        f"where the interval excludes zero; {_sign_key(d)}; dashed line: no displacement. Elements the "
        f"reference carries on fewer than {m['threshold']} paragraphs are not shown. No bias correction is applied, and "
        f"opposing errors can cancel. Run \\texttt{{{tex_escape(m['run_id'])}}}.}}",
        f"\\label{{fig:displacement_{pop}}}",
        r"\end{figure}",
    ]) + "\n"


def results(metrics_path: Path, dev: bool, out_dir: Path | None, check: bool = False, run_record: Path | None = None) -> None:
    raw = metrics_path.read_bytes()
    m = json.loads(raw.decode("utf-8"))
    digest = hashlib.sha256(raw).hexdigest()
    if bool(m.get("dev")) != dev:
        sys.exit(f"REFUSING: metrics.json has dev={m.get('dev')!r} but the command was run "
                 f"{'with' if dev else 'without'} --dev; development figures never enter paper/.")
    rec = None
    if not dev:
        rec = load_record(m, run_record)             # the record's verified status must agree with the metrics file
        if not is_fresh(m) and status_label(m) != "reportable":
            if out_dir is None or out_dir.resolve().is_relative_to((PAPER / "images").resolve()):
                sys.exit(f"REFUSING: figures for the status {m.get('analysis_status')!r} never enter paper/images/; give an --out-dir elsewhere.")
    fresh = (not dev) and is_fresh(m)
    inst, tag = fresh_tag(rec) if fresh else ("v1.0", "")
    if out_dir is None:
        out_dir = metrics_path.parent / "figures" if dev else (PAPER / "images" / f"fresh_sample{tag}" if fresh else PAPER / "images")
    if dev and out_dir.resolve().is_relative_to(PAPER.resolve()):
        sys.exit("REFUSING: development figures never enter paper/ (an explicit --out-dir inside paper/ is rejected).")
    if fresh and out_dir.resolve() == (PAPER / "images").resolve():
        sys.exit("REFUSING: fresh-sample figures never overwrite the registered figures in paper/images/ (use the fresh_sample subdirectory).")
    if fresh and tag and out_dir.resolve() == (PAPER / "images" / "fresh_sample").resolve():
        sys.exit(f"REFUSING: figures of the candidate {inst} never overwrite the v1.0 census figures in paper/images/fresh_sample/ (use fresh_sample{tag}).")
    if not check:
        out_dir.mkdir(parents=True, exist_ok=True)
    # registered: the appendix headline figure, the Chapter 6 element figure and panel b of Figure 1; fresh sample: the
    # appendix headline and displacement figures (the per-language bar figure was dropped on 2026-11-05: nearly empty on
    # the census, its values are in the per-class table); development: everything the metrics support, beside them
    files = {out_dir / "fig_primary.tex": fig_primary(m, fresh)}
    if not fresh:
        files[out_dir / "fig_elements.tex"] = fig_elements(m, load_decomposition(metrics_path, m))
    if not fresh and not dev:
        files[out_dir / "fig_headline.tex"] = fig_headline(m, load_pooled_pr(m, digest))
    if fresh or dev:
        files[out_dir / "fig_displacement.tex"] = fig_displacement(m, "pooled")
    cap = re.compile(r"\\caption(\[[^\]]*\])?\{")
    def stamp(text: str, short_prefix: str, long_prefix: str) -> str:
        """Prefix the first caption: its short form (List of Figures) and its long form."""
        def rep(mo):
            short = mo.group(1)
            return ("\\caption" + (f"[{short_prefix}{short[1].lower()}{short[2:-1]}]" if short else "") + "{" + long_prefix)
        return cap.sub(rep, text, count=1)
    stale = []
    for path, text in files.items():
        # the header binds the figure to the exact metrics file it was generated from
        text = text.replace(f"from {m['run_id']}/metrics.json. Do not edit by hand.",
                            f"from {m['run_id']}/metrics.json (sha256 {digest}). Do not edit by hand.", 1)
        if dev:   # visible stamp on every development figure, in the file and in the rendered caption
            text = stamp(text, "DEVELOPMENT RUN: ", "\\textbf{DEVELOPMENT RUN --- not reportable.} ")
            text = "% DEVELOPMENT RUN --- not reportable; never include in the thesis.\n" + text
        elif fresh:   # the A3 census: provenance in the file and in the rendered caption (specification §4b)
            text = stamp(text, "Fresh sample: ", "\\textbf{Fresh-sample evaluation (A3 census, " + inst + "); not the registered result; intervals indicative.} ")
            text = text.replace("\\label{fig:", "\\label{fig:fresh" + tag + "_", 1)
            text = "% FRESH-SAMPLE EVALUATION (A3 census, " + inst + "); not the registered result; intervals indicative.\n" + text
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path)
            continue
        path.write_text(text, encoding="utf-8")
        shown = path.resolve()
        print(f"  wrote {shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else shown}")
    if check:
        if stale:
            sys.exit("STALE: " + ", ".join(str(p) for p in stale) + " differ from what this metrics file generates")
        print(f"  {len(files)} figure(s) match {metrics_path} (sha256 {digest[:16]})")
        return



# -------------------------------------------------------------------- tables
# Compact thesis tables rendered from the same metrics.json the figures use. Every cell is a number
# the evaluator wrote (formatted, never recomputed); suppressed cells print their state. The
# evaluator's own longtables (tools/runs/tables/<run>/) remain the complete record.

RUNS = ROOT / "tools" / "runs"
INVENTORY = {"technology": 7, "element": 17, "evaluation_B": 4, "comparison_B": 15}
ELEMENT_ORDER = ["F1a", "F1b", "F2a", "F2b", "F2c", "F3a", "F3b", "F4a", "F4b", "F5a", "F5b",
                 "F6a", "F6b", "F7a", "F7b", "F8a", "F9"]
TECH_ORDER = ["general", "solar", "wind", "hydrogen", "other_renewable", "storage", "nuclear"]
STAB_DIMS = [("tech", "Technology set"), ("elements", "Element sets"), ("evaluations", "Evaluation maps"),
             ("comparison", "Comparison target"), ("subtype", "Subtype"), ("any", "Any dimension")]


def _split_cells(row: str) -> list[str]:
    """Split a tabular row at its top-level `&` (not inside braces)."""
    cells, depth, cur = [], 0, ""
    i = 0
    while i < len(row):
        ch = row[i]
        if ch == "\\" and i + 1 < len(row):
            cur += row[i:i + 2]; i += 2; continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "&" and depth == 0:
            cells.append(cur); cur = ""
        else:
            cur += ch
        i += 1
    cells.append(cur)
    return cells


def bold_header(header: str) -> str:
    """Column heads in bold (form review of 2026-11-06, X18): every top-level cell of the header row is wrapped in
    \\textbf, inside a \\multicolumn's text argument where there is one; empty and already bold cells stay."""
    out = []
    for c in _split_cells(header):
        t = c.strip()
        if not t or t.startswith("\\textbf") or t.startswith("\\bfseries"):
            out.append(c); continue
        if t.startswith("\\multicolumn"):
            j = t.rfind("{")
            if j > 0 and t.endswith("}") and "\\textbf" not in t[j:]:
                out.append(" " + t[:j] + "{\\textbf{" + t[j + 1:-1] + "}} ")
            else:
                out.append(c)
            continue
        out.append(" \\textbf{" + t + "} ")
    return "&".join(out).strip()


def table_env(caption: str, label: str, colspec: str, header: str, rows: list[str], notes: list[str],
              size: str = r"\footnotesize", short: str = "") -> str:
    cap = ("\\caption[" + short + "]{" if short else "\\caption{") + caption + "}"
    lines = [r"\begin{table}[htbp]", r"\centering", size, r"\setlength{\tabcolsep}{4pt}", r"\begin{threeparttable}",
             cap, f"\\label{{{label}}}", f"\\begin{{tabular}}{{{colspec}}}", r"\toprule",
             bold_header(header) + r" \\", r"\midrule"]
    lines += rows
    lines += [r"\bottomrule", r"\end{tabular}"]
    if notes:
        lines += [r"\begin{tablenotes}\footnotesize"] + [f"\\item {n}" for n in notes] + [r"\end{tablenotes}"]
    lines += [r"\end{threeparttable}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def longtable_env(caption: str, label: str, colspec: str, ncols: int, header: str, rows: list[str],
                  notes: list[str], short: str = "", tabcolsep: str = "4pt") -> str:
    """A paginating table (longtable) for the tables too tall for one page. The notes follow the table as footnotesize
    paragraphs after \\end{longtable} (2026-11-05, layout review): as body rows they could be left alone on a page
    under a repeated header. `ncols` is kept for the callers' signature."""
    cap = ("\\caption[" + short + "]{" if short else "\\caption{") + caption + "}\\label{" + label + "} \\\\"
    lines = [r"\begingroup\scriptsize", r"\setlength{\tabcolsep}{" + tabcolsep + "}",
             r"\setlength{\LTpost}{" + ("3pt" if notes else r"\bigskipamount") + "}",
             "\\begin{longtable}{" + colspec + "}",
             cap, r"\toprule", bold_header(header) + r" \\", r"\midrule", r"\endfirsthead",
             r"\toprule", bold_header(header) + r" \\", r"\midrule", r"\endhead", r"\bottomrule", r"\endfoot",
             r"\bottomrule", r"\endlastfoot"]
    lines += rows
    lines += [r"\end{longtable}"]
    lines += ["\\par\\noindent{\\footnotesize " + n + "\\par}" for n in notes]
    lines += ([r"\par\bigskip"] if notes else []) + [r"\endgroup"]
    return "\n".join(lines) + "\n"


def interval(m: dict, pop: str, key: str, nd: int = 3) -> str:
    r = (m.get("resample", {}).get(pop) or {}).get(key)
    if not r or r.get("lo") is None:
        return ""
    return f"[{r['lo']:.{nd}f}, {r['hi']:.{nd}f}]"


def score_cell(m: dict, pop: str, dim: str, point_key: str, res_key: str) -> str:
    v = m["point"][pop][dim].get(point_key)
    if v is None:
        return "undefined"
    return f"{v:.3f} {interval(m, pop, res_key)}".strip()


def macro_cell(m: dict, pop: str, dim: str) -> str:
    d = m["point"][pop][dim]
    v = d.get("macro_f1_weighted")
    if v is None:
        return "undefined"
    return f"{v:.3f} {interval(m, pop, f'{dim}.available_class_macro_f1_weighted')} ({d.get('macro_classes')})".strip()


def per_class_cell(pc: dict, s: int) -> str:
    if pc.get("f1_weighted") is None:
        return "undefined"
    if not pc.get("reported"):
        return f"$n<{s}$ ({pc['support_rows']})"
    return f"{pc['f1_weighted']:.3f} ({pc['support_rows']})"


def run_label(m: dict) -> str:
    return r"Run \texttt{" + tex_escape(m["run_id"]) + "}"


FRESH = "fresh-sample evaluation"      # the A3 census (specification §4b); never the registered result


EXPLORATORY = "exploratory reused A2"


def status_label(m: dict) -> str:
    st = m.get("analysis_status")
    if st in (None, "registered primary"):
        return "reportable"
    if st == FRESH:
        return "fresh census; intervals indicative"      # the thesis term (2026-11-05); the run's status is unchanged
    if st == "robustness model comparison":
        return f"robustness model comparison under {m.get('model_key') or 'another model'}, one execution"
    return str(st)


def is_fresh(m: dict) -> bool:
    return m.get("analysis_status") == FRESH


def fresh_tag(rec: dict | None) -> tuple[str, str]:
    """The instrument of a fresh-sample run, from its verified contract, and the suffix that keeps a candidate's
    outputs apart from the v1.0 census outputs: ("v1.0", "") for the registered instrument, ("v1.0r3", "_v10r3")
    for the §4b candidate. Directories, labels and provenance lines all carry the suffix (Codex r3 round 1, #1/#2)."""
    import evaluate as ev                                   # lazy: the evaluator's identity rule is the one rule
    name = ev.instrument_identity(rec)["name"] if rec else "v1.0"
    return name, ("" if name == "v1.0" else "_" + name.replace(".", "").replace("v", "v", 1))


def on_census(m: dict) -> bool:
    """The A3 census by provenance (the fresh-sample run, or a §4f comparison run on A3), not by status alone."""
    return is_fresh(m) or (m.get("inputs") or {}).get("sample_kind") == "A3_sample_manifest"


def frame_word(m: dict) -> str:
    return "the fresh census" if on_census(m) else "the eligible frame"


def weight_word(m: dict, cap: bool = True) -> str:
    """Caption wording for the weighting: design weights on the registered sample, weight one on the A3 census."""
    w = "Unweighted (census, weight one)" if on_census(m) else "Design-weighted"
    return w if cap else w[0].lower() + w[1:]


def ref_coding(m: dict, cap: bool = False) -> str:
    """The human labels a score is computed against: the census has a single coding ("the reference"); the registered
    sample has two, and every score is against the first (2026-11-12, M22 sweep, repair 5 of the fourth review)."""
    w = "the reference" if on_census(m) else "the first coding"
    return w[0].upper() + w[1:] if cap else w


def load_record(m: dict, run_record: Path | None) -> dict:
    """The run record the metrics belong to; its verified status must agree with the metrics' display field, so an
    edited metrics.json cannot steer a thesis output (workflow review, 2026-10-14)."""
    rpath = run_record or RUNS / f"{m['run_id']}.json"
    rec = json.loads(rpath.read_text(encoding="utf-8"))
    if rec.get("run_id") != m["run_id"] or not rec.get("reportable"):
        sys.exit("REFUSING: the run record does not belong to this reportable run.")
    rs, ms = rec.get("analysis_status") or "registered primary", m.get("analysis_status") or "registered primary"
    if rs != ms:
        sys.exit(f"REFUSING: the run record's analysis status ({rs!r}) differs from the metrics file's ({ms!r}); the metrics were edited.")
    # Codex review 2026-10-19 (#1): the metrics must name THIS record by digest, and the status is re-derived from the
    # record and its contract by the evaluator's own rule, so editing both display fields cannot steer an output
    want_sha = (m.get("inputs") or {}).get("run_record_sha256")
    if not want_sha or hashlib.sha256(rpath.read_bytes()).hexdigest() != want_sha:
        sys.exit("REFUSING: the metrics file does not name this run record by digest (missing or different).")
    import evaluate as ev                                   # lazy: the evaluator's status rule is the one rule
    try:
        mkind = json.loads((ROOT / rec["manifest"]["path"]).read_text(encoding="utf-8")).get("kind")
        derived = ev.analysis_status_of(rec, manifest_kind=mkind)
    except (OSError, ValueError, KeyError, ev.EvalError) as exc:
        sys.exit(f"REFUSING: the run record's status cannot be derived from its contract ({exc}).")
    if derived != ms:
        sys.exit(f"REFUSING: the run record's contract makes this run {derived!r}, not {ms!r}.")
    return rec


def num(v, nd: int = 3) -> str:
    """A number the evaluator wrote, or its documented undefined state; never a crash on a sparse population."""
    return "undefined" if v is None else f"{v:.{nd}f}"


# the headline rows (micro scores only), with dimension-specific score names: (dimension, score name, metrics key,
# point key); the dimension name is printed on the first row of its group only
PRIMARY_MICRO = [
    ("Technology detection", "micro-F1", "technology", "micro_f1_weighted"),
    ("Frame elements", "micro-F1", "element", "micro_f1_weighted"),
    ("Evaluation", "matched-frame accuracy", "evaluation_A", "accuracy_weighted"),
    ("Evaluation", "end-to-end F1", "evaluation_B", "micro_f1_weighted"),
    ("Comparison target", "matched-row accuracy", "comparison_A", "accuracy_weighted"),
    ("Comparison target", "end-to-end F1", "comparison_B", "micro_f1_weighted"),
]


def _units(m: dict, dim: str) -> str:
    """The matched units behind a conditional accuracy, pooled and per language, as the evaluator counted them."""
    u = {p: m["point"][p][dim]["units"] for p in POPULATIONS}
    return f"{u['pooled']} pooled, {u['de']} German, {u['en']} English"


def _units_cols(m: dict, dim: str) -> str:
    """2026-11-12, second cut pass (75k): the same matched units in the table's column order (pooled/German/English)."""
    return "/".join(str(m["point"][p][dim]["units"]) for p in POPULATIONS)


def tab_primary(m: dict, full: bool = False) -> str:
    """The headline agreement table. Main text (full = False, label tab:primary): the six micro rows with
    dimension-specific score names, one column per population, no run identifier. Appendix (full = True, label
    tab:primary_full): the same rows plus the macro-F1 rows and the whole-row exact match. The fresh-sample census
    renders the complete table under tab:primary (relabelled by tables()), since it is an appendix table itself."""
    fresh = is_fresh(m)
    complete = full or fresh
    rows, prev = [], None
    for dim_label, score, dim, pk in PRIMARY_MICRO:
        cells = " & ".join(score_cell(m, p, dim, pk, f"{dim}.{pk}") for p in POPULATIONS)
        rows.append(f"{dim_label if dim_label != prev else ''} & {score} & {cells} \\\\")
        prev = dim_label
    if complete:
        rows.append(r"\addlinespace")
        prev = None
        for dim_label, score, dim in [("Frame elements", "macro-F1", "element"),
                                      ("Evaluation", "matched-frame macro-F1", "evaluation_A"),
                                      ("Evaluation", "end-to-end macro-F1", "evaluation_B"),
                                      ("Comparison target", "matched-row macro-F1", "comparison_A"),
                                      ("Comparison target", "end-to-end macro-F1", "comparison_B")]:
            cells = " & ".join(macro_cell(m, p, dim) for p in POPULATIONS)
            rows.append(f"{dim_label if dim_label != prev else ''} & {score} & {cells} \\\\")
            prev = dim_label
        rows.append(r"\addlinespace")
        cells = " & ".join(f"{num(m['point'][p]['exact_match']['weighted'])} ({m['point'][p]['exact_match']['rows']} rows)" for p in POPULATIONS)
        rows.append(f"Whole relation row & exact match & {cells} \\\\")
    n = m["populations"]; a = m["articles"]
    header = (r"Dimension & Score & Pooled ($n$\,=\," + f"{n['pooled']}" + r") & German ($n$\,=\," + f"{n['de']}"
              + r") & English ($n$\,=\," + f"{n['en']}" + ")")
    status = "" if status_label(m) == "reportable" else f" ({status_label(m)})"
    what = "first execution" + (" on the fresh census" if fresh else "")
    lead = (r"Complete headline agreement: the micro scores of Table~\ref{tab:primary} with the macro-F1 and the whole-row "
            r"exact match, by dimension and language. " if full else
            (f"The model's first-execution agreement with the first coding, by dimension and language{status}. " if not fresh else
             f"Agreement of the model's {what} with the coder, by dimension and language{status}. "))
    # 2026-11-12, M16 cut pass (G-46): the main-text caption names the first coding (repair 5 of the fourth review)
    per_lang = (f"{a['pooled']} pooled; {a['de']} per language" if a["de"] == a["en"] else
                f"{a['pooled']} pooled, {a['de']} German, {a['en']} English")
    caption = (f"{lead}{weight_word(m)} point estimates; brackets: 2.5--97.5 percentile intervals from {big(m['B'])} article "
               f"resamples ({per_lang}); $n$: paragraphs.")
    notes = ([f"Matched-frame accuracy: the share of (paragraph, technology, frame) units fired by both {ref_coding(m)} and the "
              f"model on which the evaluation agrees ({_units(m, 'evaluation_A')} units). Matched-row accuracy: the share of "
              f"(paragraph, technology) rows named by both on which the comparison target agrees ({_units(m, 'comparison_A')} "
              f"rows). End-to-end F1 is the micro-F1 over all (paragraph, technology, frame, evaluation) or (paragraph, "
              f"technology, comparison target) tuples, so a frame or row that one side missed counts as an error. "
              f"Micro-F1 pools the tuples of all classes."] if full else
             # 2026-11-09, slimming debate consensus F-07: one note for the main-text table (the end-to-end definition
             # stays in Section 5.4); the fresh-census table keeps both notes
             # 2026-11-12, second cut pass (75k): main-text note compressed to "Scores: Section ..." and the matched units
             # as pooled/German/English per column; the fresh-census note below is unchanged
             ([f"Scores: Section~\\ref{{subsec:metrics}}. Matched units per column: {_units_cols(m, 'evaluation_A')} frames, "
               f"{_units_cols(m, 'comparison_A')} rows."] if not fresh else
              [f"Scores as defined in Section~\\ref{{subsec:metrics}}. Matched units: {_units(m, 'evaluation_A')} frames; "
               f"{_units(m, 'comparison_A')} rows. End-to-end F1 counts a frame or row that one side missed as an error."]))
    if complete:
        notes.append("Macro-F1 is the point estimate over the classes with positive reference support (count after the "
                     "interval); its interval is the available-class macro over resamples. Per-language macros use each "
                     "language's own class set and are not direct language contrasts. The exact-match rate carries no "
                     "interval. Unweighted figures and the per-population defined-class distributions are in the "
                     "evaluator's output file \\texttt{table1\\_primary.csv} (electronic appendix).")
    # (2026-11-09) the main-text table no longer carries a separate macro-F1 pointer: its single note names Table 16
    label = "tab:primary_full" if full else "tab:primary"
    short = ("Agreement with the first coding by dimension and language, complete" if full
             else "Agreement with the first coding by dimension and language" if not fresh
             else "Agreement with the coder by dimension and language")
    L = ">{\\raggedright\\arraybackslash}p{%smm}"
    colspec = "@{}" + L % 24 + L % 28 + (L % 31) * 3 + "@{}"
    return table_env(caption, label, colspec, header, rows, notes,
                     size=r"\footnotesize" if not complete else r"\scriptsize", short=short)


def tab_per_class(m: dict) -> str:
    s = m["threshold"]
    rows = [r"\multicolumn{4}{@{}l}{\emph{Technology}} \\"]
    for c in TECH_ORDER:
        cells = " & ".join(per_class_cell(m["point"][p]["technology"]["per_class"][c], s) for p in POPULATIONS)
        rows.append(f"{tex_escape(c)} & {cells} \\\\")
    rows += [r"\addlinespace", r"\multicolumn{4}{@{}l}{\emph{Frame element}} \\"]
    for c in ELEMENT_ORDER:
        cells = " & ".join(per_class_cell(m["point"][p]["element"]["per_class"][c], s) for p in POPULATIONS)
        rows.append(f"{c} & {cells} \\\\")
    header = r"Class & Pooled & German & English"
    caption = (f"Per-class agreement with the reference, first execution ({run_label(m)}, {status_label(m)}): {weight_word(m, False)} "
               f"F1 with the reference support in rows in parentheses. A cell prints $n<{s}$ with its support when the "
               f"reference supports the class with fewer than {s} rows in that population, and \\emph{{undefined}} when "
               f"support and predictions are both zero.")
    notes = ["Support unit: the unweighted (paragraph $\\times$ technology) reference row. Per-class cells carry no "
             "resampling interval; the rate at which a class is absent from a resample and the article support are "
             "in the evaluator's output file \\texttt{table2\\_per\\_class.csv} (electronic appendix). Suppression is a reporting rule and does not remove a "
             "class from the micro or macro aggregates."]
    return table_env(caption, "tab:per_class", "@{}lrrr@{}", header, rows, notes, short="Per-class agreement with the reference")


def tab_categorical(m: dict) -> str:
    rows = []
    for p in POPULATIONS:
        for dim, label in [("evaluation", "Evaluation"), ("comparison", "Comparison target")]:
            A = m["point"][p][f"{dim}_A"]; B = m["point"][p][f"{dim}_B"]
            exc = A["excluded"]
            r_exc = exc.get(f"{dim}_A:row_not_predicted", 0); f_exc = exc.get(f"{dim}_A:frame_not_predicted")
            excl = f"{r_exc} / {f_exc}" if f_exc is not None else f"{r_exc} / --"
            rows.append(f"{POP_LABEL[p]} & {label} & {score_cell(m, p, f'{dim}_A', 'accuracy_weighted', f'{dim}_A.accuracy_weighted')} & "
                        f"{A['units']} & {excl} & {score_cell(m, p, f'{dim}_B', 'micro_f1_weighted', f'{dim}_B.micro_f1_weighted')} & "
                        f"{num(A['macro_f1_weighted'])} ({A['macro_classes']}) & {num(B['macro_f1_weighted'])} ({B['macro_classes']}) \\\\")
        if p != POPULATIONS[-1]:
            rows.append(r"\addlinespace")
    header = r"Population & Dimension & Score A accuracy & Units & Excluded units: missing row / frame & Score B micro-F1 & Macro A & Macro B"
    caption = (f"The two categorical dimensions under both scoring conventions ({run_label(m)}, {status_label(m)}). Score A: "
               "accuracy on the matched reference units; excluded are the reference units on rows the model did not "
               "predict and the reference frames it did not predict within matched rows; score B: micro-F1 over end-to-end tuples. Macro-F1 over the supported classes "
               "(count in parentheses), point estimates.")
    ex_p = m["point"]["pooled"]["evaluation_A"]["excluded"]; ex_c = m["point"]["pooled"]["comparison_A"]["excluded"]
    notes = ["The conditioning of score A is post hoc: a single stateless call cannot be handed the reference frames. "
             f"For the evaluation, a missing row excludes its frame--evaluation units (pooled: "
             f"{ex_p.get('evaluation_A:row_not_predicted', 0)} units on {ex_c.get('comparison_A:row_not_predicted', 0)} "
             "rows); the comparison target has no frame level, so only rows can be excluded."]
    return table_env(caption, "tab:categorical", "@{}ll>{\\raggedright\\arraybackslash}p{26mm}r>{\\raggedright\\arraybackslash}p{14mm}>{\\raggedright\\arraybackslash}p{26mm}rr@{}", header, rows, notes, size=r"\scriptsize", short="The categorical dimensions under both scoring conventions")


def tab_stability(m: dict) -> str:
    rows = []
    for key, label in STAB_DIMS:
        cells = []
        for p in POPULATIONS:
            st = m["stability"][p][key]
            cells.append(f"{num(st['weighted'])} {interval(m, p, f'stability.{key}')}".strip())
        rows.append(f"{label} & " + " & ".join(cells) + r" \\")
    rows.append(r"\addlinespace")
    rows.append("Paragraphs entering (paragraph / relation level) & " + " & ".join(
        f"{m['stability'][p]['tech']['paragraphs']} / {m['stability'][p]['elements']['paragraphs']}" for p in POPULATIONS) + r" \\")
    rows.append("Paragraphs without a valid pair & " + " & ".join(str(m["stability"][p]["paragraphs_without_pairs"]) for p in POPULATIONS) + r" \\")
    rows.append("Empty technology union & " + " & ".join(str(m["stability"][p]["empty_union"]) for p in POPULATIONS) + r" \\")
    rows.append("Failure rate over executions & " + " & ".join(num(m['stability'][p]['failure_rate']) for p in POPULATIONS) + r" \\")
    rows.append(r"\addlinespace")
    rows.append("Element micro-F1, executions 1 / 2 / 3 & " + " & ".join(
        " / ".join(num(m['per_execution'][p][k]['element_micro_f1_weighted']) for k in ("1", "2", "3")) for p in POPULATIONS) + r" \\")
    header = r"Quantity & Pooled & German & English"
    caption = (f"Run-to-run change rate between the three byte-identical executions, by relation level and population "
               f"({run_label(m)}, {status_label(m)}). {weight_word(m)} rates with the 2.5--97.5 percentile interval over "
               f"{m['B']} article resamples; conditional on successful executions.")
    notes = ["A change rate is the share of (unit, execution pair) comparisons on which the value differs, averaged "
             "within the paragraph, then over paragraphs with design weights. The last row is the design-weighted "
             f"element micro-F1 of each execution against {ref_coding(m)}; only execution 1 is the reported figure."]
    return table_env(caption, "tab:stability", "@{}>{\\raggedright\\arraybackslash}p{50mm}p{32mm}p{32mm}p{32mm}@{}", header, rows, notes, short="Run-to-run change rates between executions")


def tab_displacement(m: dict) -> str:
    s = m["threshold"]
    def cell(p, key, with_counts=False):
        d = m["displacement"][p].get(key)
        if d is None:
            return "-- & --" if with_counts else "--"
        counts = f" & {d['gold_paragraphs']} / {d['pred_paragraphs']}" if with_counts else ""   # every row keeps its five cells
        if d["gold_paragraphs"] < s:
            return f"$n<{s}$ ({d['gold_paragraphs']})" + counts
        iv = interval(m, p, f"displacement.{key}", 1)
        pp = d["weighted_pp"]
        if pp is None:
            return "undefined" + counts
        out = f"{pp:+.1f} {iv}" if abs(pp) >= 0.05 else f"0.0 {iv}"
        return out + counts
    rows = [r"\multicolumn{5}{@{}l}{\emph{Technology}} \\"]
    for c in TECH_ORDER:
        k = f"technology:{c}"
        rows.append(f"{tex_escape(c)} & {cell('pooled', k, True)} & {cell('de', k)} & {cell('en', k)} \\\\")
    rows += [r"\addlinespace", r"\multicolumn{5}{@{}l}{\emph{Frame element}} \\"]
    for c in ELEMENT_ORDER:
        k = f"element:{c}"
        rows.append(f"{c} & {cell('pooled', k, True)} & {cell('de', k)} & {cell('en', k)} \\\\")
    rows += [r"\addlinespace", r"\multicolumn{5}{@{}l}{\emph{Frame and evaluation (pooled support $\geq$ " + str(s) + r")}} \\"]
    for k in sorted(m["displacement"]["pooled"]):
        if not k.startswith("eval:"):
            continue
        if m["displacement"]["pooled"][k]["gold_paragraphs"] < s:
            continue
        frame, val = k[5:].split("=")
        rows.append(f"{tex_escape(frame)} = {val} & {cell('pooled', k, True)} & {cell('de', k)} & {cell('en', k)} \\\\")
    header = r"Class & Pooled, pp & Ref. / pred. & German, pp & English, pp"
    caption = (f"{weight_word(m)}, reference-relative prevalence displacement in percentage points of {frame_word(m)}: "
               f"the share of paragraphs on which the model marks the class minus the share on which {ref_coding(m)} does "
               f"({run_label(m)}, {status_label(m)}). Brackets are the 2.5--97.5 percentile interval over {m['B']} article "
               f"resamples; the third column gives the reference and predicted paragraph counts in the pooled sample.")
    notes = [f"A class prints $n<{s}$ with its count where the reference carries it on fewer than {s} paragraphs of the "
             "population. Frame--evaluation pairs are listed when their pooled support reaches the threshold; the complete "
             "list is in the evaluator's output file \\texttt{table5\\_displacement.csv}. No bias-correction estimator is applied; opposing errors can cancel, and "
             "the interval carries no uncertainty from the reference."]
    return table_env(caption, "tab:displacement", "@{}lp{30mm}rp{27mm}p{27mm}@{}", header, rows, notes, size=r"\scriptsize", short="Prevalence displacement per class")


def tab_reliability(m: dict) -> str:
    s = m["threshold"]; R = m["reliability"]
    def a(v):
        return "undefined" if v is None else f"{v:.3f}"
    def row(level, item, d, cov=""):
        if d["support_rows"] < s:
            al = raw = f"$n<{s}$ ({d['support_rows']})"
        else:
            al, raw = a(d.get("alpha")), a(d.get("raw"))
        sup = f"{d['support_rows']}" + (f" / {d['support_rows_b']}" if "support_rows_b" in d else "")
        return f"{level} & {tex_escape(item)} & {al} & {raw} & {d['n']} & {sup} & {d['support_articles']} & {cov} \\\\"
    rows = []
    for t in TECH_ORDER:
        rows.append(row("Technology", t, R["technology"][t]))
    rows.append(r"\addlinespace")
    for e in ELEMENT_ORDER:
        rows.append(row("Element", e, R["elements"][e], f"one coding only: {R['rows_only_a']} + {R['rows_only_b']}" if e == "F1a" else ""))
    rows.append(r"\addlinespace")
    for f, d in R["evaluation"].items():
        rows.append(row("Evaluation", f, d, tex_escape(d.get("mask_coverage", "").split(" rows")[0])))
    rows.append(r"\addlinespace")
    rows.append(row("Comparison target", "all aligned rows", R["comparison_target"]))
    header = r"Level & Item & $\alpha$ & Raw & $n$ & Support 1st / 2nd & Articles & Mask"
    inp = R["inputs"]
    # 2026-11-10, §4n integration, coherence round C1 (H-07): presentation only -- the realised interval is a minimum,
    # sentences within 40 words; rows, values, masks and order unchanged
    caption = (f"Intra-coder temporal stability on the registered {R['n_paragraphs']}-paragraph re-code ({run_label(m)}, "
               f"{status_label(m)}). Nominal Krippendorff's $\\alpha$ and raw agreement compare the first coding with the blind "
               f"re-code. The table reports aligned units, both codings' supports and article support. The realised minimum "
               f"interval was {inp['washout_days']} days, rather than the registered {inp['washout_days_registered']}; this "
               f"deviation is disclosed.")
    notes = [f"An item whose first-coding support is below {s} prints its state; $\\alpha$ is undefined when all ratings "
             "across both codings are identical. For technology ticks, the unit is the paragraph. For elements, it is the "
             f"aligned (paragraph $\\times$ technology) row ({R['aligned_rows']} rows). For evaluations, it is the aligned row "
             "on which both codings fired the frame; the mask column gives these rows of the rows on which either fired. "
             "For the comparison target, it is every aligned row."]
    out = longtable_env(caption, "tab:reliability", "@{}>{\\raggedright\\arraybackslash}p{19mm}lrrr>{\\raggedright\\arraybackslash}p{15mm}r>{\\raggedright\\arraybackslash}p{24mm}@{}", 8, header, rows, notes, short="Temporal stability of the reference")
    # C2 (H-07): OUTLINE comments for the generated table and its note; nothing else changes
    out = out.replace("\\par\\noindent{\\footnotesize", "% OUTLINE: The note defines support suppression, undefined alpha and the "
                      "applicable units for each label dimension.\n\\par\\noindent{\\footnotesize", 1)
    return ("% OUTLINE: The registered re-code reports one coder's temporal stability on aligned units after the realised "
            "minimum washout.\n" + out)


def tab_clean_domain(m: dict) -> str:
    spec = [
        ("Technology detection", "micro-F1", "technology", "micro_f1_weighted", "technology.micro_f1_weighted"),
        ("Frame elements (tuples)", "micro-F1", "element", "micro_f1_weighted", "element.micro_f1_weighted"),
        ("Evaluation, score A", "accuracy", "evaluation_A", "accuracy_weighted", "evaluation_A.accuracy_weighted"),
        ("Evaluation, score B", "micro-F1", "evaluation_B", "micro_f1_weighted", "evaluation_B.micro_f1_weighted"),
        ("Comparison target, score A", "accuracy", "comparison_A", "accuracy_weighted", "comparison_A.accuracy_weighted"),
        ("Comparison target, score B", "micro-F1", "comparison_B", "micro_f1_weighted", "comparison_B.micro_f1_weighted"),
    ]
    pops = ["pooled", "clean_pooled", "clean_de", "clean_en"]
    rows = []
    for label, score, dim, pk, rk in spec:
        rows.append(f"{label} & {score} & " + " & ".join(score_cell(m, p, dim, pk, rk) for p in pops) + r" \\")
    rows.append(r"\addlinespace")
    rows.append("Frame elements & macro-F1 & " + " & ".join(macro_cell(m, p, "element") for p in pops) + r" \\")
    rows.append("Whole relation row & exact match & " + " & ".join(num(m['point'][p]['exact_match']['weighted']) for p in pops) + r" \\")
    rows.append(r"\addlinespace")
    rows.append("Paragraphs / articles & & " + " & ".join(f"{m['populations'][p]} / {m['articles'][p]}" for p in pops) + r" \\")
    cd = m["clean_domain_sensitivity"]
    rows.append("Outlets & & " + " & ".join(["19"] + [str(len(cd[p]["outlets"])) for p in pops[1:]]) + r" \\")
    header = r"Dimension & Score & Full sample, pooled & Clean, pooled & Clean, German & Clean, English"
    caption = (f"Clean-domain sensitivity ({run_label(m)}, {status_label(m)}): the headline figures recomputed on the paragraphs "
               "from articles no part of which had been used in calibration or development, beside the full pooled sample. "
               f"{weight_word(m)}, with the 2.5--97.5 percentile interval over {m['B']} article resamples.")
    notes = ["Presented as a sensitivity, not as a contamination effect: the fifteen excluded paragraphs come from "
             "articles whose other paragraphs had been coded before the sample was drawn; none of them had been coded or "
             "predicted. The outlet count of the full sample is that of the corpus with usable text."]
    return table_env(caption, "tab:clean_domain", "@{}>{\\raggedright\\arraybackslash}p{27mm}>{\\raggedright\\arraybackslash}p{12mm}p{26mm}p{26mm}p{26mm}p{26mm}@{}", header, rows, notes, size=r"\scriptsize", short="Clean-domain sensitivity")


def tab_attrition(m: dict) -> str:
    rows = []
    for p in POPULATIONS:
        A = m["attrition"][p]
        for k in ("1", "2", "3"):
            o = A[f"execution_{k}"]
            rows.append(f"{POP_LABEL[p] if k == '1' else ''} & {k} & {o.get('technologies_present', 0)} & {o.get('off_topic', 0)} & "
                        f"{o.get('parse_failed', 0)} & {o.get('refused', 0)} & {o.get('error', 0)} & {A[f'execution_{k}_both_sides_technologies_present']} \\\\")
        if p != POPULATIONS[-1]:
            rows.append(r"\addlinespace")
    header = r"Population & Exec. & Technologies present & Off topic & Parse failed & Refused & Error & Both sides present"
    caption = (f"Terminal outcome of every paragraph per execution ({run_label(m)}, {status_label(m)}). The first five outcome "
               "columns partition the population; the last column is the registered joint count of paragraphs on which "
               f"both {ref_coding(m)} and the model name at least one technology, a subset of the first.")
    n_off = m['attrition']['pooled']['gold_off_topic']
    langs = [POP_LABEL[p] for p in ("de", "en") if (m['attrition'].get(p) or {}).get('gold_off_topic')]
    off_note = (f"{ref_coding(m, cap=True)} marks {n_off} paragraph{'s' if n_off != 1 else ''} as naming no studied technology ({', '.join(langs)}). "
                if n_off else f"{ref_coding(m, cap=True)} marks no paragraph as naming no studied technology. ")
    notes = [off_note + "A parse failure is a response that remained schema- or contract-invalid after the one identical "
             "retry; it enters the agreement figures as omissions."]
    return table_env(caption, "tab:attrition", "@{}lr>{\\raggedright\\arraybackslash}p{20mm}>{\\raggedright\\arraybackslash}p{12mm}>{\\raggedright\\arraybackslash}p{12mm}>{\\raggedright\\arraybackslash}p{12mm}>{\\raggedright\\arraybackslash}p{10mm}>{\\raggedright\\arraybackslash}p{20mm}@{}", header, rows, notes, short="Terminal outcomes per execution")


def tab_limits(m: dict) -> str:
    rows = []
    for p in POPULATIONS:
        for dim, label in [("technology", "Technology"), ("element", "Frame element"),
                           ("evaluation_B", "Evaluation"), ("comparison_B", "Comparison target")]:
            supported = len(m["class_manifest"][dim][p])
            common = len(m["class_manifest"][dim]["common_support"]) if p == "pooled" else "--"
            dist = m["resample"][p].get("defined_class_counts", {}).get(dim, {})
            dist_s = "; ".join(f"{k}: {v}" for k, v in sorted(dist.items(), key=lambda kv: -int(kv[0])))
            rows.append(f"{POP_LABEL[p] if dim == 'technology' else ''} & {label} & {supported} / {INVENTORY[dim]} & {common} & "
                        f"{m['articles'][p]} & {dist_s} \\\\")
        if p != POPULATIONS[-1]:
            rows.append(r"\addlinespace")
    header = r"Population & Dimension & Supported / inventory & Common support & Articles & Defined classes over resamples (count: resamples)"
    ill = m["illustration"]["element"]
    caption = (f"What the sample can and cannot measure ({run_label(m)}, {status_label(m)}): classes with positive reference "
               "support against the inventory, the classes supported in both languages, the article count behind the "
               f"resampling, and how many classes remained defined across the {m['B']} resamples.")
    notes = [f"Constructed illustration (evaluator): an instrument scoring {ill['f']} on every supported class, with "
             f"{ill['n_de']} and {ill['n_en']} of {ill['K']} elements supported per language, shows a cross-language macro gap of "
             f"{ill['gap_zero_convention']:.3f} under the zero convention, {ill['gap_one_convention']:.3f} under the one convention and "
             f"{ill['gap_positive_support_convention']:.3f} under the positive-support convention; the sign flips between conventions "
             "only when the supported-class counts differ. The common-support macros are point-estimate sensitivities "
             "(evaluator's output file \\texttt{table1\\_primary.csv})."]
    return table_env(caption, "tab:limits", "@{}>{\\raggedright\\arraybackslash}p{15mm}>{\\raggedright\\arraybackslash}p{20mm}>{\\raggedright\\arraybackslash}p{17mm}>{\\raggedright\\arraybackslash}p{14mm}r>{\\raggedright\\arraybackslash}p{46mm}@{}", header, rows, notes, size=r"\scriptsize", short="What the sample can and cannot measure")


def git_clause(rec: dict) -> str:
    g = rec.get("git_at_start") or {}
    if g.get("dirty") is False:
        return "tree clean at start"
    if g.get("dirty") is True:
        ch = g.get("changed_paths") or []
        return "tree dirty at start: " + ", ".join(tex_escape(c) for c in ch[:3]) + (" and more" if len(ch) > 3 else "")
    return "tree status unknown"


def tab_run_record(m: dict, rec: dict) -> str:
    from datetime import datetime
    t0 = datetime.fromisoformat(rec["started_at"]); t1 = datetime.fromisoformat(rec["ended_at"])
    mins = (t1 - t0).total_seconds() / 60
    tot = rec["totals"]; cost = rec["cost"]; vc = tot["verdict_codes"]
    rows = [
        f"Model & {tex_escape(rec['model_id'])} ({tex_escape(rec['provider'])}, region {tex_escape(rec['region'])}) \\\\",
        f"Decoding & temperature {rec['inference_config']['temperature']}, at most {rec['inference_config']['maxTokens']} output tokens, no thinking field, forced tool use \\\\",
        f"Cache & prompt-cache point after the constant prefix, {tex_escape(rec['cache_ttl_requested'])} TTL \\\\",
        f"Codebook / prompt & v{rec['codebook']['version']} (sha256 {rec['codebook']['sha256'][:12]}\\dots) / {rec['prompt']['sha256'][:12]}\\dots \\\\",
        f"Gate & {rec['preflight']['certification_status'].lower()}, {len(rec['preflight']['checks'])} live checks, all passed \\\\",
        f"Sample & {rec['manifest']['n_paragraphs']} paragraphs, manifest sha256 {rec['manifest']['sha256'][:12]}\\dots \\\\",
        f"Executions & {rec['repeats']} per paragraph ({rec['repeats'] * rec['manifest']['n_paragraphs']}), {tot['calls']} attempts in all \\\\",
        f"Attempt verdicts & technologies present {vc.get('ok_technologies_present', 0)}, off topic {vc.get('ok_off_topic', 0)}, contract-invalid {vc.get('contract_invalid', 0)}, schema-invalid {vc.get('schema_invalid', 0)} \\\\",
        f"Throttles / transport errors & {tot['throttles']} / {tot['errors']} \\\\",
        f"Tokens & {tot['input_tokens']:,} uncached input, {tot['cache_read_tokens']:,} cache read, {tot['output_tokens']:,} output \\\\",
        f"Estimated cost & USD {cost['estimated_usd']:.2f} at the frozen list rates (billing complete; not an invoice) \\\\",
        f"Wall-clock time & {mins:.1f} minutes ({t0.strftime('%Y-%m-%d %H:%M')} to {t1.strftime('%H:%M')} UTC) \\\\",
        f"Environment & Python {rec['python']}, botocore {rec['botocore']}, commit {rec['git_commit'][:7]} ({git_clause(rec)}) \\\\",
        (f"Evaluation & threshold $s={m['threshold']}$ (declared for A3, {tex_escape(m['inputs']['threshold_declaration']['authority'])}), {m['B']} resamples, seed {m['seed']}; analysis status: {tex_escape(status_label(m))} \\\\"
         if on_census(m) else
         f"Evaluation & threshold $s={m['threshold']}$ (sealed {m['inputs']['threshold_seal']['created_at'][:10]}, commit {m['inputs']['threshold_seal']['commit'][:7]}), {m['B']} resamples, seed {m['seed']} \\\\"),
    ]
    header = r"Item & Value"
    caption = (f"Run record of the A3 census run ({run_label(m)}, {status_label(m)})." if is_fresh(m)
               else f"Run record of the production run ({run_label(m)}, {status_label(m)}).")
    notes = ["Token counts are summed over every finished attempt, including the retried ones. The attempt verdicts "
             "count responses, not paragraphs; a paragraph whose retry succeeded contributes one invalid and one valid attempt."]
    return table_env(caption, "tab:run_record", "@{}>{\\raggedright\\arraybackslash}p{30mm}>{\\raggedright\\arraybackslash}p{118mm}@{}", header, rows, notes, short="Run record of the production run")


def tab_development() -> str:
    """W3/W4 development comparison from the committed selection records; development figures, labelled."""
    w3 = json.loads((RUNS / "w3_selection.json").read_text(encoding="utf-8"))
    w4 = json.loads((RUNS / "w4_selection.json").read_text(encoding="utf-8"))
    rows = []
    for phase, sel in (("W3", w3), ("W4", w4)):
        for slot in sorted(sel["candidates"]):
            c = sel["candidates"][slot]
            lost = sum(v for k, v in c["terminal_outcomes"].items() if k not in ("technologies_present", "off_topic"))
            rows.append(f"{phase} & {tex_escape(c['instrument'])} & {c['I']} & {lost} & {c['feedback_retries']} & {c['retries']} & "
                        f"{c['S_float']:.3f} & {'yes' if c['eligible'] else 'no'} & USD {c['cost_usd']:.2f} \\\\")
        rows.append(f"\\multicolumn{{9}}{{@{{}}l}}{{\\quad outcome {phase}: \\emph{{{tex_escape(sel['decision']['outcome'].replace('_', ' '))}}}, "
                    f"{tex_escape(sel['decision']['selected'])} retained}} \\\\")
        if phase == "W3":
            rows.append(r"\addlinespace")
    header = r"Phase & Instrument & Invalid first responses & Lost executions & Feedback retries & All retries & $S$ & Eligible & Cost"
    caption = ("Post-registration development iterations on the development split (32 paragraphs from three articles, "
               "three executions each, 96 scheduled executions per candidate). \\textbf{Development figures}, scored "
               "against the development reference; not comparable with the registered result and not used for any reported figure.")
    notes = ["$S$ is the sealed score: the mean over the three executions of the unweighted element micro-F1. "
             "An invalid first response is a scheduled execution whose first returned response failed the schema or the "
             "relational rules; a lost execution ended without a valid record after the one retry. Records: "
             r"\texttt{tools/runs/w3\_selection.json}, \texttt{w4\_selection.json}."]
    return table_env(caption, "tab:development", "@{}ll>{\\raggedright\\arraybackslash}p{15mm}>{\\raggedright\\arraybackslash}p{14mm}>{\\raggedright\\arraybackslash}p{13mm}>{\\raggedright\\arraybackslash}p{11mm}rl>{\\raggedright\\arraybackslash}p{14mm}@{}", header, rows, notes, size=r"\scriptsize", short="Post-registration development iterations (development figures)")


def tables(metrics_path: Path, run_record: Path | None, out_dir: Path | None, check: bool = False) -> None:
    raw = metrics_path.read_bytes()
    m = json.loads(raw.decode("utf-8"))
    digest = hashlib.sha256(raw).hexdigest()
    if m.get("dev"):
        sys.exit("REFUSING: development metrics never render thesis tables (metrics.json has dev=true).")
    rec = load_record(m, run_record)
    fresh = is_fresh(m)
    inst, tag = fresh_tag(rec) if fresh else ("v1.0", "")
    if not fresh and status_label(m) != "reportable":
        if out_dir is None or out_dir.resolve().is_relative_to((PAPER / "tables").resolve()):
            sys.exit(f"REFUSING: tables for the status {m.get('analysis_status')!r} never enter paper/tables/; give an --out-dir elsewhere.")
    out_dir = out_dir or (PAPER / "tables" / f"fresh_sample{tag}" if fresh else PAPER / "tables")
    if fresh and out_dir.resolve() == (PAPER / "tables").resolve():
        sys.exit("REFUSING: fresh-sample tables never overwrite the registered tables in paper/tables/ (use the fresh_sample subdirectory).")
    if fresh and tag and out_dir.resolve() == (PAPER / "tables" / "fresh_sample").resolve():
        sys.exit(f"REFUSING: tables of the candidate {inst} never overwrite the v1.0 census tables in paper/tables/fresh_sample/ (use fresh_sample{tag}).")
    head = (f"% Generated by tools/pipeline/figures.py tables from {m['run_id']}/metrics.json (sha256 {digest}). Do not edit by hand.\n"
            + (f"% FRESH-SAMPLE EVALUATION (A3 census, {inst}); not the registered result; intervals indicative (specification §4b).\n" if fresh else ""))
    files = {
        out_dir / "tab_run_record.tex": tab_run_record(m, rec),
        out_dir / "tab_primary.tex": tab_primary(m),
        out_dir / "tab_per_class.tex": tab_per_class(m),
        out_dir / "tab_categorical.tex": tab_categorical(m),
        out_dir / "tab_stability.tex": tab_stability(m),
        out_dir / "tab_displacement.tex": tab_displacement(m),
        out_dir / "tab_attrition.tex": tab_attrition(m),
        out_dir / "tab_limits.tex": tab_limits(m),
    }
    if not fresh:                                    # table 9 and table 7 are not applicable to the A3 census
        files[out_dir / "tab_primary_full.tex"] = tab_primary(m, full=True)    # the appendix's complete headline table
        files[out_dir / "tab_reliability.tex"] = tab_reliability(m)
        files[out_dir / "tab_clean_domain.tex"] = tab_clean_domain(m)
    if fresh:                                        # labels distinct from the registered ones and, for a candidate, from v1.0's census labels
        files = {p: t.replace("\\label{tab:", "\\label{tab:fresh" + tag + "_", 1) for p, t in files.items()}
    files = {p: head + t for p, t in files.items()}
    if not fresh:
        dev_head = ("% Generated by tools/pipeline/figures.py tables from tools/runs/w3_selection.json and w4_selection.json. "
                    "DEVELOPMENT FIGURES (labelled in the caption). Do not edit by hand.\n")
        files[out_dir / "tab_development.tex"] = dev_head + tab_development()
    stale = []
    if not check:
        out_dir.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path)
            continue
        path.write_text(text, encoding="utf-8")
        print(f"  wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    if check:
        if stale:
            sys.exit("STALE: " + ", ".join(str(p) for p in stale))
        print(f"  {len(files)} table(s) match {metrics_path} (sha256 {digest[:16]})")


# ----------------------------------------------------- added analyses: adjustment and translation
# Figures and the appendix table of two analyses added after the registered result, rendered from their committed
# outputs (never recomputed): the DSL frame estimate (dsl.py, tools/runs/tables/dsl/dsl_prevalence.json) and the
# language arm (language_arm.py compare, tools/runs/tables/language_arm/language_arm.json). Each output names its
# source by digest; `--check` regenerates in memory and compares.

DSL_JSON = RUNS / "tables" / "dsl" / "dsl_prevalence.json"
LANG_JSON = RUNS / "tables" / "language_arm" / "language_arm.json"
TRANSLATION_RECORD = RUNS / "language_arm" / "translation_record.json"
Z975 = 1.959963984540054      # the normal 97.5 % quantile, the one dsl.py uses for its design-based interval
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
DSL_EXAMPLE = "F2b"           # the worked example of the text (investment), shaded in the figure
DSL_ROW_MM = 5.0              # row height of Figure 6 (was 7 mm, then 5.6; compacted again 2026-11-05, marks still clear)


def _short_lower(e: str) -> str:
    """The element's short name for running text: first part, lower case (F2b -> investment)."""
    return ELEMENT_SHORT.get(e, e).split(",")[0].lower()


def dsl_rows(res: dict) -> dict[str, dict]:
    """The reportable elements of the DSL frame estimate (in percentage points of the frame's paragraphs): the
    adjusted estimate with its design-based interval, the sample-only estimate (the reference's Horvitz--Thompson
    estimate +- z times its design-based SE, the same human sample) and the model-only share. Refuses an output whose
    design interval is not the estimate +- z times its SE (the bootstrap interval lo_pp/hi_pp is never drawn)."""
    out = {}
    for c, r in res["classes"].items():
        if not c.startswith("element:") or not r.get("reported"):
            continue
        for side, sign in (("lo", -1), ("hi", 1)):
            want = r["dsl_pp"] + sign * Z975 * r["se_design_pp"]
            if abs(r[f"ci_design_{side}_pp"] - want) > 1e-6:
                sys.exit(f"REFUSING: {c}: the design interval is not the estimate +- z * SE ({r[f'ci_design_{side}_pp']} vs {want}).")
        ref, se_r = r["reference_ht_pp"], r["se_reference_design_pp"]
        out[c.split(":", 1)[1]] = {"adj": r["dsl_pp"], "lo": r["ci_design_lo_pp"], "hi": r["ci_design_hi_pp"],
                                   "model": r["naive_pp"], "ref": ref, "rlo": ref - Z975 * se_r, "rhi": ref + Z975 * se_r,
                                   "wider": r["se_design_pp"] > se_r, "support": r["gold_support"]}
    return out


def fig_dsl(res: dict, example: str = DSL_EXAMPLE) -> str:
    """Figure 6: per reportable element, the adjusted prevalence with its design-based interval (filled green circle,
    figAdjusted: an estimate that combines model and coder), the sample-only estimate with reference +- z * reference SE
    (open black circle, figInk: the coder alone) and the model-only share (model blue cross, figModel), rows sorted by
    the adjusted estimate; shape, fill and the offset within the row double the colours in greyscale; the worked example
    shaded and labelled directly; a double dagger where the adjusted interval is wider than the sample-only one."""
    d = dsl_rows(res)
    rows = sorted(d, key=lambda e: (-d[e]["adj"], ELEMENT_ORDER.index(e)))
    n_rows = len(rows)
    y = lambda i: i + 1
    off = 0.27                                    # model above, adjusted on, sample-only below the row line
    lo_all = min(min(v["lo"], v["rlo"], v["model"]) for v in d.values())
    hi_all = max(max(v["hi"], v["rhi"], v["model"]) for v in d.values())
    xmin = min(0, math.floor(lo_all) - 1)
    xmax = 10 * math.ceil(hi_all / 10)
    ticks = ",".join(str(t) for t in range(0, xmax + 1, 10))
    labels = [element_name(e) + (r"\,\ddag" if d[e]["wider"] else "") for e in rows]
    adj = " ".join(f"({d[e]['adj']:.3f},{y(i):.2f}) -= ({d[e]['adj'] - d[e]['lo']:.3f},0) += ({d[e]['hi'] - d[e]['adj']:.3f},0)"
                   for i, e in enumerate(rows))
    smp = " ".join(f"({d[e]['ref']:.3f},{y(i) + off:.2f}) -= ({d[e]['ref'] - d[e]['rlo']:.3f},0) += ({d[e]['rhi'] - d[e]['ref']:.3f},0)"
                   for i, e in enumerate(rows))
    mdl = " ".join(f"({d[e]['model']:.3f},{y(i) - off:.2f})" for i, e in enumerate(rows))
    band, note = [], []
    if example in rows:
        i = rows.index(example); v = d[example]
        band = [f"\\fill[figGreyC] (axis cs:{xmin},{y(i) - 0.5}) rectangle (axis cs:{xmax},{y(i) + 0.5});"]
        note = [f"\\node[figtext, anchor=north west, align=left, xshift=3mm, inner sep=0pt] at (axis cs:{xmax},{y(i) - 0.45}) "
                f"{{\\textbf{{{ELEMENT_SHORT.get(example, example).split(',')[0]}}} (worked example)\\\\"
                f"adjusted {v['adj']:.1f}\\% [{v['lo']:.1f}, {v['hi']:.1f}]\\\\"
                f"sample only {v['ref']:.1f}\\% [{v['rlo']:.1f}, {v['rhi']:.1f}]\\\\"
                f"model only {v['model']:.1f}\\%}};"]
    up = sum(1 for v in d.values() if v["adj"] > v["model"] + ZERO_PP)
    narrower = sum(1 for v in d.values() if not v["wider"])
    top = max(d, key=lambda e: d[e]["adj"] - d[e]["model"]) if d else None
    if d and 2 * up > n_rows and 2 * narrower > n_rows:
        # 2026-11-09, slimming debate consensus F-11
        title = (f"The adjustment raises {up} of {n_rows} element prevalences above model-only shares, {_short_lower(top)} "
                 f"most, and narrows {narrower} intervals relative to the sample alone.")
    else:
        title = "Element prevalences in the eligible frame: adjusted, sample-only and model-only estimates."
    fails = sum(res["failed_executions"].values())
    wider = [e for e in rows if d[e]["wider"]]
    # Compact layout (review of 2026-11-05); caption tightened 2026-11-09 (consensus F-11): the reference-convention
    # sentence carries the re-reading's qualification for investment (Section 7.3 states it in full). Second slimming
    # pass 2026-11-09 (K-18): the legend keys marks and bars, so the caption names the three series by their legend
    # labels and keeps only what the legend cannot say (estimator, interval type, failed executions, SE).
    # 2026-11-12, second cut pass (75k): article count, the repeated "design-based" and "estimates", and the reporting
    # threshold sentence dropped; failed executions as "(n paragraphs)"; the double dagger names why an interval widens
    cap = (r"\caption[Adjusting element prevalences with the coded sample]{\textbf{" + title + r"} Shares in \% of "
           f"{big(res['N'])} eligible-frame paragraphs, under the first coding's convention. "
           r"Adjusted: "   # 2026-11-12, M16 cut pass (F-11, S3-11)
           r"design-based supervised learning (Section~\ref{subsec:added_methods}), 95\% "
           r"intervals (normal approximation, no coverage guarantee). Sample only: $\pm$\,1.96 design-based "
           r"standard errors (SE). Model only: failed "
           f"executions ({fails} paragraphs) unmarked."
           + (r" \ddag: adjusted interval wider than sample-only, as their model errors vary more than the coder's labels."
              if wider else "")
           + r" Fixed model predictions exclude run-to-run variation from both intervals.}")
    return "\n".join([
        r"% Palette 2026-11-06: adjusted estimate green (figAdjusted, model and coder combined), model-only share model blue (figModel), sample-only estimate black (figInk); shapes, fills and row offsets unchanged, the legend keys the series (caption shortened 2026-11-09, K-18).",
        r"\begin{figure}[htbp]",
        r"\centering",
        r"\begin{tikzpicture}",
        r"\begin{axis}[thesisfig,",
        f"  width=72mm, height={DSL_ROW_MM * n_rows + 3:g}mm, scale only axis,",
        f"  xmin={xmin}, xmax={xmax}, xtick={{{ticks}}}, xlabel={{Prevalence in the eligible frame (\\% of paragraphs)}},",
        *_rows_axis(labels),
        r"  legend columns=1, legend style={at={(1,0)}, anchor=south west, xshift=3mm, yshift=13mm},",
        r"]",
        *band,
        f"\\draw[guideline] (axis cs:0,0.4) -- (axis cs:0,{n_rows + 0.6});",
        f"\\addplot[only marks, mark=x, mark size=2.2pt, draw=figModel, mark options={{draw=figModel, line width=0.8pt}}] "
        f"coordinates {{{mdl}}};",
        r"\addlegendentry{Model only}",
        f"\\addplot[only marks, mark=*, draw=figAdjusted, fill=figAdjusted, mark options={{draw=figAdjusted, fill=figAdjusted}}, "
        f"error bars/.cd, x dir=both, x explicit, error bar style={{figAdjusted}}] coordinates {{{adj}}};",
        r"\addlegendentry{Adjusted, 95\% interval}",
        f"\\addplot[only marks, mark=*, draw=figInk, mark options={{draw=figInk, fill=white, line width=0.8pt}}, "
        f"error bars/.cd, x dir=both, x explicit, error bar style={{figInk}}, error mark options={{rotate=90, mark size=1.6pt, "
        f"line width=0.8pt, draw=figInk}}] coordinates {{{smp}}};",
        r"\addlegendentry{Sample only, $\pm$\,1.96 SE}",
        *note,
        r"\end{axis}",
        r"\end{tikzpicture}",
        cap,
        r"\label{fig:dsl}",
        r"\end{figure}",
    ]) + "\n"


# the language arm: the four paired statistics of language_arm.py in figure order (label, where to read them)
LANG_STATS = [("Element F1 (primary)", "element"), ("Technology F1", "technology_f1"),
              ("Element precision", "element_precision"), ("Element recall", "element_recall")]


def lang_pairs(la: dict) -> list[tuple[str, float, float, float, float, float]]:
    """(label, translated, original, difference, lo, hi) per statistic; refuses an output whose paired difference is
    not translated minus original, or whose four statistics do not share one resampling (B, seed, articles)."""
    out, draws = [], set()
    for label, key in LANG_STATS:
        if key == "element":
            p = la["primary"]
            t, o, dv, lo, hi = p["translated"], p["original"], p["difference"], p["lo"], p["hi"]
            draws.add((p["B"], p["seed"], p["articles"]))
        else:
            q = la["secondary"][key]
            t, o, dv, lo, hi = q["translated"], q["original"], q["paired"]["diff"], q["paired"]["lo"], q["paired"]["hi"]
            draws.add((q["paired"]["B"], q["paired"]["seed"], q["paired"]["articles"]))
        if None in (t, o, dv, lo, hi) or abs(dv - (t - o)) > 1e-9 or not lo <= dv <= hi:
            sys.exit(f"REFUSING: the language arm's {key} difference is not translated minus original inside its interval.")
        out.append((label, t, o, dv, lo, hi))
    if len(draws) != 1:
        sys.exit("REFUSING: the language arm's paired statistics do not share one resampling.")
    return out


def _signed(v: float, nd: int = 3) -> str:
    """A signed number in math mode (a true minus sign in the figure and table fonts)."""
    return f"${v:+.{nd}f}$"


def fig_language_arm(la: dict) -> str:
    """Figure 8 (half-width plot, caption beside it): the paired difference, translated minus original German input, with its paired
    article-resampling interval for element F1 (the declared statistic), technology F1, element precision and
    element recall; every mark vermillion (figChanged: the translated input is a changed configuration), the sign read
    from the position against the dashed zero line; filled where the interval excludes zero, open otherwise; the
    difference written beside each interval."""
    pairs = lang_pairs(la)
    n = len(pairs)
    y = lambda i: i + 1
    lo_all, hi_all = min(p[4] for p in pairs), max(p[5] for p in pairs)
    xmin = 0.05 * math.floor(min(lo_all, 0) / 0.05 - 1e-9)
    xmax = 0.05 * math.ceil((max(hi_all, 0) + 0.06) / 0.05 - 1e-9)     # room for the written difference
    ticks = [t / 10 for t in range(math.ceil(xmin * 10 - 1e-9), math.floor(xmax * 10 + 1e-9) + 1)]
    groups: dict[bool, list[str]] = {}            # filled where the interval excludes zero, open otherwise
    labs = []
    for i, (label, t, o, dv, lo, hi) in enumerate(pairs):
        groups.setdefault(hi < 0 or lo > 0, []).append(
            f"({dv:.4f},{y(i)}) -= ({dv - lo:.4f},0) += ({hi - dv:.4f},0)")
        labs.append(f"\\node[figtext, right=2pt] at (axis cs:{hi:.4f},{y(i)}) {{{_signed(dv)}}};")
    plots = []
    for excl, pts in sorted(groups.items()):
        plots.append(f"\\addplot[only marks, mark=*, draw=figChanged, mark options={{draw=figChanged, fill={'figChanged' if excl else 'white'}, "
                     f"line width=0.8pt}}, error bars/.cd, x dir=both, x explicit, error bar style={{figChanged}}] "
                     f"coordinates {{{' '.join(pts)}}};")
    p = la["primary"]
    allzero = all(lo <= 0 <= hi for *_, lo, hi in pairs)
    # The title states what was observed and must not read as equivalence (review of 2026-11-05); the caption names the
    # declared reading, for agreement when it is inconclusive.
    title = (f"Translated against original German input: all {NUMBER_WORDS.get(n, n)} intervals include zero."   # S5-05
             if allzero else "Paired differences between translated and original German input.")
    # The caption names the declared reading once, briefly; the two F1 values and the explanation of an inconclusive
    # reading are in the text of Section 6.9 directly below (layout verification of 2026-11-05: explained once). The
    # mark colour and the fill are keyed here because the style requires captions to state the encoding.
    # 2026-11-09, second slimming pass (G-57): caption wording tightened, no fact removed
    reading = (f"Element F1: {p['outcome']} under the declared "
               + ("agreement rule" if p["outcome"] == "inconclusive" else "rule"))
    # 2026-11-12, M16 cut pass (K4-02): when every interval includes zero, every mark is open and vermillion, so the key
    # distinguishes nothing; the colour's meaning is keyed in Figure 1 ("Colours throughout"). Otherwise the key stays.
    key = "" if allzero else "; vermillion: translated against original input; open marks: interval includes zero"
    # 2026-11-12, second cut pass (75k): article count and resample count dropped; bars named as 95% intervals
    cap = (r"\caption[Translated against original German input]{\textbf{" + title + r"} Paired first-execution "
           f"differences: {la['paragraphs']} German evaluation paragraphs, design-weighted "
           r"against their first coding. Bars: 95\% intervals over "   # FIX-01 (repair 5)
           f"paired article resamples{key}. "
           f"{reading}.}}")   # the change counts and the F1 values are stated once, in the text of Section 6.9
    # Half-width plot (2026-11-05); since 2026-11-12 the caption stands beneath it, as the chair's guide requires (6.3).
    return "\n".join([
        r"% Palette 2026-11-06: every mark vermillion (figChanged, a changed configuration: the translated input); the sign is read from the position against the dashed zero line, the fill still marks an interval that excludes zero; the caption's sign-colour key is replaced by the colour's meaning.",
        r"\begin{figure}[!htbp]",
        r"\centering",
        r"\begin{tikzpicture}",
        r"\begin{axis}[thesisfig,",
        f"  width=40mm, height={6 * n + 2}mm, scale only axis,",
        f"  xmin={xmin:.2f}, xmax={xmax:.2f}, xtick={{{','.join(f'{t:g}' for t in ticks)}}},",
        f"  xticklabels={{{','.join(('$' + f'{t:g}' + '$') if t else '0' for t in ticks)}}},",
        r"  xlabel={Difference, translated $-$ original},",
        *_rows_axis([x[0] for x in pairs]),
        r"]",
        f"\\draw[refline] (axis cs:0,0.4) -- (axis cs:0,{n + 0.6});",
        *plots,
        *labs,
        r"\end{axis}",
        r"\end{tikzpicture}",
        cap,   # 2026-11-12, second cut pass (75k): caption beneath the figure, as the chair's guide requires (6.3)
        r"\label{fig:language_arm}",
        r"\end{figure}",
    ]) + "\n"




def tab_language_arm(la: dict, rec: dict) -> str:
    """The appendix table of the language arm: configuration of the translator, the author's check, the paired
    comparison, change counts across inputs and between repeated executions (denominators, both weightings),
    element F1 per execution, failures and the predeclared sensitivity. Every cell is a value of language_arm.json or
    of the translation record it binds by digest."""
    pairs = lang_pairs(la)
    s = la["secondary"]; tr = la["translation"]; ck = la["translation_check"]; sm = ck["summary"]
    tl, rq = rec["translator"], rec["request"]
    ic, extra = rq["inference_config"], rq.get("additional_model_request_fields") or {}
    at, us, codes = rec["attempts"], rec["usage"], rec["attempts"].get("codes", {})
    W = 6

    def span(txt: str) -> str:
        return "\\multicolumn{" + str(W - 1) + "}{>{\\raggedright\\arraybackslash}p{104mm}@{}}{" + txt + "}"

    def head(txt: str) -> str:
        return "\\multicolumn{" + str(W) + "}{@{}l}{\\emph{" + txt + "}} \\\\"

    def sub(*cells: str) -> str:
        return " & ".join(["\\quad"] + ["\\textit{" + c + "}" for c in cells]) + r" \\"

    def item(label: str, txt: str) -> str:
        return f"{label} & {span(txt)} \\\\"

    pct = lambda v: "--" if v is None else f"{v:.3f}"
    # -- configuration (translation record, bound by digest)
    decoding = (f"temperature {ic['temperature']}, at most {big(ic['maxTokens'])} output tokens"
                + "".join(f", {tex_escape(k).replace(chr(92) + '_', ' ')} {tex_escape(str(v))}" for k, v in extra.items())
                + "; one stateless call per paragraph with the paragraph alone as the user turn; system prompt sha256 "
                + f"\\texttt{{{tr['prompt_sha256'][:12]}}}\\dots")
    others = ", ".join(f"{v} {tex_escape(k)} rejection{'s' if v != 1 else ''} before the translator (not charged)"
                       for k, v in codes.items() if k != "ok")
    completion = (f"{tr['translated']} of {tr['rows']} paragraphs translated, {tr['failed']} failed; {at['calls_finished']} "
                  f"calls, {codes.get('ok', 0)} answered" + (f", {others}" if others else "")
                  + f"; {big(us['input_tokens'])} input and {big(us['output_tokens'])} output tokens")
    rows = [head("Configuration"),
            item("Translator", f"{tex_escape(tl['model_id'])} on {tex_escape(tl['provider'])}, {tex_escape(tl['region'])} "
                               f"({tex_escape(tl['invocation'])})"),
            item("Decoding", decoding),
            item("Post-processing", tex_escape(rq["post_processing"])),
            item("Completion", completion),
            # 2026-11-12, M22 sweep (repair 5 of the fourth review): the row and the caption name the first coding
            item("Extraction", "the registered instrument unchanged, on the translated text; three executions per "
                               "paragraph, the first execution scored against the unchanged first coding"),
            r"\addlinespace"]
    # -- the author's check (sealed summary)
    sev = ", ".join(f"{k} {v}" for k, v in sm["severity"].items())
    flagged = {k: v for k, v in sm["categories"].items() if v}
    cats = (f"none of the {NUMBER_WORDS.get(len(sm['categories']), len(sm['categories']))} error categories flagged" if not flagged else
            "; ".join(f"{tex_escape(k).replace(chr(92) + '_', ' ')} {v}" for k, v in flagged.items()))
    before = ck.get("translated_runs_before_seal") or []
    seal = (f"sealed {ck['sealed_at'][:10]} {ck['sealed_at'][11:16]} UTC, committed in \\texttt{{{ck['commitment']['commit'][:7]}}}"
            + (f"; the translated-input extraction had started before the seal (first call {before[0]['first_call_at'][11:16]} UTC)"
               if before else ""))
    rows += [head("The author's check of the translations"),
             item("Pairs checked", f"{sm['checked']} of {sm['rows']}, each pair individually; {sm['not_translated']} without translation"),
             item("Severity", sev),
             item("Categories", cats),
             item("Checker", tex_escape(ck["checker_note"].replace("the checker is ", ""))),
             item("Seal", seal),
             r"\addlinespace"]
    # -- the paired comparison
    rows += [head("Paired comparison (design-weighted, first execution)"),
             sub("Translated", "Original", "Difference", "Interval", "Reading")]
    for i, (label, t, o, dv, lo, hi) in enumerate(pairs):
        reading = la["primary"]["outcome"] if i == 0 else "descriptive"
        rows.append(f"{label.replace('F1', 'micro-F1')} & {t:.3f} & {o:.3f} & {_signed(dv)} & [{_signed(lo)}, {_signed(hi)}] & {reading} \\\\")
    sens = la.get("sensitivity_without_major")
    if sens is not None:
        sp = sens["paired"]["element"]
        rows.append(f"Element micro-F1 without major-flagged pairs ({sens['excluded_paragraphs']} paragraphs excluded) & {sens['translated']:.3f} & "
                    f"{sens['original']:.3f} & {_signed(sp['diff'])} & [{_signed(sp['lo'])}, {_signed(sp['hi'])}] & {sens['outcome']} \\\\")
    rows.append(r"\addlinespace")
    # -- change counts: across inputs and between repeated executions of each input
    rows += [head("Changes between executions (paragraphs where both executions succeeded)"),
             sub("Paragraphs", "Technology set changed", "Element set changed, rows", "Element rate, unweighted / weighted",
                 "Relation changed, rows")]

    def change_row(label: str, c: dict) -> str:
        return (f"{label} & {c['paragraphs_both_successful']} & {c['technology_set_changed']} / {c['paragraphs_both_successful']} & "
                f"{c['elements_changed']} / {c['matched_units']} & {pct(c['element_change_rate'])} / "
                f"{pct(c['element_change_rate_weighted'])} & {c['relation_changed']} / {c['matched_units']} \\\\")
    rows.append(change_row("Translated vs original, exec.\\ 1", s["change_translated_vs_original_execution1"]))
    for key, label in (("native_repeat_original", "Original"), ("repeat_translated", "Translated")):
        for k in sorted(s[key], key=lambda k: int(k.split("_vs_")[1])):
            a, b = k.split("_vs_")
            rows.append(change_row(f"{label}, exec.\\ {a} vs {b}", s[key][k]))
    rows.append(r"\addlinespace")
    # -- element F1 per execution and failures of execution one
    ex = s["executions_element_f1"]; rg = s["executions_range"]
    k_max = max(len(ex["translated"]), len(ex["original"]))
    rows += [head("Element micro-F1 per execution"),
             sub(*[f"Execution {r}" for r in range(1, k_max + 1)], *([""] * (W - 2 - k_max)), "Range")]
    for key, label in (("translated", "Translated"), ("original", "Original")):
        vals = [pct(ex[key].get(str(r))) for r in range(1, k_max + 1)]
        rows.append(f"{label} & " + " & ".join(vals + [""] * (W - 2 - k_max)) + f" & {pct((rg[key] or {}).get('range'))} \\\\")
    rows.append(r"\addlinespace")
    fl = s["execution1_failures"]
    def outc(n_failed: int, d: dict) -> str:
        name = lambda k: tex_escape(k).replace(chr(92) + "_", " ")
        bad = ", ".join(f"{name(k)} {v}" for k, v in sorted(d.items()) if k not in ("technologies_present", "off_topic"))
        ok = ", ".join(f"{name(k)} {d[k]}" for k in ("technologies_present", "off_topic") if k in d)
        return f"{n_failed} of {la['paragraphs']} without a valid record" + (f" ({bad})" if bad else "") + f"; valid: {ok}"
    rows += [head("Outcomes of the first execution"),
             item("Translated", outc(fl["translated"], fl["translated_outcomes"])),
             item("Original", outc(fl["original"], fl["original_outcomes"]))]
    p = la["primary"]
    caption = (f"The translated-input comparison in detail, on the {la['paragraphs']} German paragraphs of the evaluation "
               f"sample ({la['articles']} articles) against their unchanged first coding.")
    notes = [f"Intervals: 2.5--97.5 percentile over {big(p['B'])} common resamples of the {p['articles']} articles (seed "
             f"{p['seed']}). Declared rule for the element micro-F1: an interval entirely above zero reads as above, entirely "
             f"below as below, otherwise inconclusive; inconclusive is no evidence of equivalence. The other rows are "
             f"descriptive. A change counts the (paragraph, technology) rows predicted in both executions whose element set "
             f"(or whole relation) differs; the weighted rate weights each row by its paragraph's design weight. Translation "
             f"changes wording, explicitness and length at once, so the comparison is between two complete configurations, "
             f"not a language effect. Added after the registered result."]
    L = ">{\\raggedright\\arraybackslash}p{%smm}"
    colspec = "@{}" + L % 40 + (L % 17) * 3 + L % 25 + L % 18 + "@{}"
    return table_env(caption, "tab:language_arm", colspec, r"Quantity & \multicolumn{5}{l@{}}{Value}", rows, notes,
                     size=r"\scriptsize", short="The translated-input comparison in detail")


def _emit(files: dict[Path, str], check: bool, what: str) -> None:
    """Write the generated files, or (check) compare them with the files on disk and exit 1 on any difference."""
    if check:
        stale = [p for p, t in files.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
        if stale:
            sys.exit("STALE: " + ", ".join(str(p) for p in stale) + f" differ from what {what} generates")
        print(f"  {len(files)} file(s) match {what}")
        return
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        shown = path.resolve()
        print(f"  wrote {shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else shown}")


def dsl_figure(src: Path, out_dir: Path | None, check: bool = False) -> None:
    raw = src.read_bytes()
    res = json.loads(raw.decode("utf-8"))
    if res.get("population") != "frame":
        sys.exit("REFUSING: the DSL output is not the frame estimate.")
    head = (f"% Generated by tools/pipeline/figures.py dsl from {src.name} (sha256 {hashlib.sha256(raw).hexdigest()}). "
            f"Do not edit by hand.\n% Analysis added after the registered result (specification §4d); exploratory.\n")
    out_dir = out_dir or PAPER / "images"
    _emit({out_dir / "fig_dsl.tex": head + fig_dsl(res)}, check, src.name)


def language_outputs(src: Path, record: Path, img_dir: Path | None, tab_dir: Path | None, check: bool = False) -> None:
    raw = src.read_bytes()
    la = json.loads(raw.decode("utf-8"))
    rraw = record.read_bytes()
    if hashlib.sha256(rraw).hexdigest() != la["translation"]["record_sha256"]:
        sys.exit("REFUSING: the translation record is not the one the comparison binds by digest.")
    rec = json.loads(rraw.decode("utf-8"))
    if rec["translator"]["model_id"] != la["translation"]["translator_model_id"] or \
            rec["request"]["system_prompt_sha256"] != la["translation"]["prompt_sha256"]:
        sys.exit("REFUSING: the translation record and the comparison disagree on the translator or the prompt.")
    head = (f"% Generated by tools/pipeline/figures.py language from {src.name} (sha256 {hashlib.sha256(raw).hexdigest()}) "
            f"and {record.name} (sha256 {hashlib.sha256(rraw).hexdigest()}). Do not edit by hand.\n"
            f"% Analysis added after the registered result (specification §4e(a)); {la['status']}.\n")
    files = {(img_dir or PAPER / "images") / "fig_language_arm.tex": head + fig_language_arm(la),
             (tab_dir or PAPER / "tables") / "tab_language_arm.tex": head + tab_language_arm(la, rec)}
    _emit(files, check, src.name)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("codebook", help="write paper/tables/tab_codebook_elements.tex")
    r = sub.add_parser("results", help="write the results figures from an evaluator metrics.json")
    r.add_argument("--metrics", required=True, type=Path)
    r.add_argument("--dev", action="store_true", help="development metrics: write beside the metrics file, never into paper/")
    r.add_argument("--out-dir", type=Path, default=None)
    r.add_argument("--check", action="store_true", help="regenerate in memory and compare with the files on disk; exit 1 if any differs")
    r.add_argument("--run-record", type=Path, default=None, help="defaults to tools/runs/<run_id>.json (reportable only)")
    t = sub.add_parser("tables", help="write the compact thesis tables (paper/tables/tab_*.tex) from a reportable metrics.json")
    t.add_argument("--metrics", required=True, type=Path)
    t.add_argument("--run-record", type=Path, default=None, help="defaults to tools/runs/<run_id>.json")
    t.add_argument("--out-dir", type=Path, default=None)
    t.add_argument("--check", action="store_true")
    d = sub.add_parser("dsl", help="write paper/images/fig_dsl.tex from the DSL frame estimate")
    d.add_argument("--source", type=Path, default=DSL_JSON)
    d.add_argument("--out-dir", type=Path, default=None)
    d.add_argument("--check", action="store_true")
    g = sub.add_parser("language", help="write fig_language_arm.tex and tab_language_arm.tex from the language-arm comparison")
    g.add_argument("--source", type=Path, default=LANG_JSON)
    g.add_argument("--record", type=Path, default=TRANSLATION_RECORD)
    g.add_argument("--out-dir", type=Path, default=None, help="one directory for both files (default: paper/images, paper/tables)")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.cmd == "tables":
        tables(args.metrics, args.run_record, args.out_dir, check=args.check)
        return
    if args.cmd == "dsl":
        dsl_figure(args.source, args.out_dir, check=args.check)
        return
    if args.cmd == "language":
        language_outputs(args.source, args.record, args.out_dir, args.out_dir, check=args.check)
        return
    if args.cmd == "codebook":
        out = PAPER / "tables" / "tab_codebook_elements.tex"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(table_codebook(), encoding="utf-8")
        print(f"  wrote {out.relative_to(ROOT)}")
    else:
        results(args.metrics, args.dev, args.out_dir, check=args.check, run_record=args.run_record)


if __name__ == "__main__":
    main()
