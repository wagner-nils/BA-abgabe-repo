#!/usr/bin/env python3
"""Descriptive tables and figures for Chapter 5 of the thesis.

Runs under the Homebrew interpreter (the same one as build_sheets.py) because it
reuses build_sheets.collect() for the candidate flag, which the tracked
ALL_paragraphs.csv does not carry. Everything it writes is derived from files
that are already tracked or registered:

  paper/tables/tab_corpus.tex           corpus per outlet merged with the A2
                                        allocation (main text since 2026-11-05):
                                        country, articles, paragraphs, candidates,
                                        N_h, n_h, design weight, grouped by language
  paper/tables/tab_a2_allocation.tex    the 18 A2 strata: N_h, n_h, pi_h, weight,
                                        exposure split of the drawn paragraphs
  paper/tables/tab_sessions.tex         the annotation sessions from sessions.csv
  paper/images/corpus_timeline.tex      pgfplots: paragraphs per publication
                                        month, stacked by language
  paper/images/element_support.tex      pgfplots: element fire counts in the
                                        calibration (A0 + A0b) and development
                                        (A1) second-pass label files, one row
                                        per element

Both figures use the shared figure style of the thesis preamble (thesisfig, the
fig* colours and mark styles) and define no colours of their own.

No article text is read into any output; only counts, identifiers and metadata.
Numbers from the development session are shown as a worked example of the
support analysis and inform no design choice (pre-analysis specification, §3).

Usage:
    python3 descriptives.py            # write everything
    python3 descriptives.py --check    # exit 1 if any output would change
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build_sheets as bs  # noqa: E402

PAPER = ROOT / "paper"
TABLES = PAPER / "tables"
IMAGES = PAPER / "images"
LABELS = HERE / "labels"
FRAME_MANIFEST = LABELS / "A2_frame_manifest.json"
SAMPLE_MANIFEST = LABELS / "A2_sample_manifest.json"
SESSIONS = LABELS / "sessions.csv"

COUNTRY_NAME = {"US": "United States", "DE": "Germany", "GB": "United Kingdom",
                "QA": "Qatar", "CH": "Switzerland", "AT": "Austria"}
LANG_NAME = {"de": "German", "en": "English"}

# Element order and frame membership are fixed by the frozen codebook; the
# reader gives the derivation, the order below is the sheet order.
ELEMENTS = ["F1a", "F1b", "F2a", "F2b", "F2c", "F3a", "F3b", "F4a", "F4b",
            "F5a", "F5b", "F6a", "F6b", "F7a", "F7b", "F8a", "F9"]
# Short element names for figure rows; the same names as tools/pipeline/figures.py ELEMENT_SHORT (that module runs
# under the other interpreter and is not imported here).
ELEMENT_SHORT = {
    "F1a": "Cost or price", "F1b": "Subsidies", "F2a": "Employment", "F2b": "Investment, industry",
    "F2c": "Deployment growth", "F3a": "Emissions, climate", "F3b": "Transition pathway",
    "F4a": "Import dependence", "F4b": "Security of supply", "F5a": "Intermittency", "F5b": "Grid integration",
    "F6a": "Technical feasibility", "F6b": "Technical performance", "F7a": "Land requirement",
    "F7b": "Landscape", "F8a": "Regulation", "F9": "Other frame",
}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def tex_escape(s: str) -> str:
    return (s.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%")
             .replace("_", r"\_").replace("#", r"\#").replace("$", r"\$"))


def num(n: int) -> str:
    return f"{n:,}".replace(",", "{,}")


# --------------------------------------------------------------------------- corpus

def corpus_rows() -> tuple[list[dict], set[str]]:
    """Every paragraph with its candidate flag, and the frozen eligible frame."""
    rows, _skipped = bs.collect()
    frame = json.loads(FRAME_MANIFEST.read_text(encoding="utf-8"))
    eligible = {p["paragraph_id"] for p in frame["paragraphs"]}
    return rows, eligible


def table_corpus(rows: list[dict], eligible: set[str]) -> str:
    """Corpus per outlet, merged with the evaluation-sample allocation (main-text table, since 2026-11-05).

    The frame count per outlet is computed from the frozen frame manifest and asserted to equal the stratum size N_h
    of the registered sample manifest, and the allocated n_h is asserted to equal the number of drawn paragraphs, so
    that the two sources cannot disagree silently. The appendix table tab_a2_allocation keeps the per-stratum columns
    this table drops (pi_h, sampled articles, exposure, re-code flags).
    """
    sample = json.loads(SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    alloc = {(a["lang"], a["outlet"]): a for a in sample["allocation"]}
    drawn = Counter((p["lang"], p["outlet"]) for p in sample["paragraphs"])
    per_outlet: dict[str, dict] = defaultdict(lambda: {
        "articles": set(), "paragraphs": 0, "candidates": 0,
        "eligible": 0, "country": None, "lang": None})
    for r in rows:
        o = per_outlet[r["outlet"]]
        o["articles"].add(r["article_id"])
        o["paragraphs"] += 1
        o["candidates"] += int(r["_candidate"])
        o["eligible"] += int(r["paragraph_id"] in eligible)
        o["country"] = r["country"]
        o["lang"] = r["lang"]
    for (lang, outlet), a in alloc.items():
        o = per_outlet[outlet]
        assert o["lang"] == lang and o["eligible"] == a["N_h"], (lang, outlet)
        assert drawn[(lang, outlet)] == a["n_h"], (lang, outlet)
    assert all(o["eligible"] == 0 for k, o in per_outlet.items() if (o["lang"], k) not in alloc)
    # an outlet without a stratum must owe that to the exemplar exclusion alone, which the note then states
    frame = json.loads(FRAME_MANIFEST.read_text(encoding="utf-8"))
    exemplar = set(frame["exposure_classes"]["direct_exemplar_article"])
    no_stratum = sorted(k for k, o in per_outlet.items() if (o["lang"], k) not in alloc)
    assert all(per_outlet[k]["articles"] == exemplar for k in no_stratum), no_stratum
    no_stratum_note = "".join(
        rf" {tex_escape(k)} forms no stratum: its one article is the one quoted in the prompt." for k in no_stratum)
    # sort by language, then descending paragraph count
    order = sorted(per_outlet.items(),
                   key=lambda kv: (kv[1]["lang"], -kv[1]["paragraphs"], kv[0]))
    cap = sample["design"]["cap_registered"]
    lines = [
        r"% Generated by tools/annotation/descriptives.py from the corpus manifest, the split",
        r"% registry, the frozen A2 frame manifest and the registered A2 sample manifest. Do not edit by hand.",
        r"% Appendix A.2 since 2026-11-09 (slimming debate consensus F-03): the main text carries the summary tab:corpus.",
        r"\begin{table}[htbp]",
        r"\centering",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{3pt}",
        r"\begin{threeparttable}",
        r"\caption[Corpus, eligible frame and evaluation-sample allocation by outlet]{Corpus, eligible frame and "
        r"evaluation-sample allocation by outlet; each outlet with eligible paragraphs is one stratum within its language, "
        r"with stratum size $N_h$ in the eligible frame, draw $n_h$ and design weight $w_h = N_h/n_h$.}",
        r"\label{tab:corpus_outlets}",
        r"\begin{tabular}{@{}llrrr@{\hspace{12pt}}rrr@{}}",
        r"\toprule",
        r" & & \multicolumn{3}{c}{\textbf{Corpus}} & \multicolumn{3}{c}{\textbf{Stratum}} \\",
        r"\cmidrule(lr){3-5}\cmidrule(l){6-8}",
        r"\textbf{Outlet} & \textbf{Country} & \textbf{Articles} & \textbf{Paragraphs} & \textbf{Candidates} & $\boldsymbol{N_h}$ & $\boldsymbol{n_h}$ & $\boldsymbol{w_h}$ \\",
        r"\midrule",
    ]
    tot = Counter()
    tot_articles: set[str] = set()
    summary: dict[str, dict] = {}
    by_lang: dict[str, list] = defaultdict(list)
    for outlet, o in order:
        by_lang[o["lang"]].append((outlet, o))
    for i, lang in enumerate(sorted(by_lang)):
        if i:
            lines.append(r"\addlinespace")
        lines.append(rf"\multicolumn{{8}}{{@{{}}l}}{{\textit{{{LANG_NAME[lang]}-language outlets}}}} \\")
        sub = Counter()
        sub_articles: set[str] = set()
        for outlet, o in by_lang[lang]:
            a = alloc.get((lang, outlet))
            if a is None:
                frame_cells = "0 & -- & --"
            else:
                pi = Fraction(a["pi"])
                frame_cells = f"{a['N_h']} & {a['n_h']} & {float(1 / pi):.2f}"
                sub["n"] += a["n_h"]
            lines.append(
                f"{tex_escape(outlet)} & {COUNTRY_NAME.get(o['country'], o['country'])} & "
                f"{len(o['articles'])} & {o['paragraphs']} & {o['candidates']} & {frame_cells} \\\\")
            sub_articles |= o["articles"]
            for k in ("paragraphs", "candidates", "eligible"):
                sub[k] += o[k]
        lines.append(
            rf"\textit{{All {LANG_NAME[lang]}}} & & {len(sub_articles)} & {num(sub['paragraphs'])} & "
            f"{sub['candidates']} & {sub['eligible']} & {sub['n']} & \\\\")
        assert sub["n"] == sample["by_language"][lang]
        weights = [float(1 / Fraction(alloc[(lang, k)]["pi"])) for k, _ in by_lang[lang] if (lang, k) in alloc]
        summary[lang] = {"outlets": len(by_lang[lang]), "articles": len(sub_articles), "paragraphs": sub["paragraphs"],
                         "candidates": sub["candidates"], "eligible": sub["eligible"], "n": sub["n"],
                         "wmin": min(weights), "wmax": max(weights)}
        tot_articles |= sub_articles
        tot += sub
    assert tot["eligible"] == len(eligible) and tot["n"] == sample["n"]
    lines += [
        r"\midrule",
        f"Total & {len({o['country'] for o in per_outlet.values()})} countries & {len(tot_articles)} & {num(tot['paragraphs'])} & "
        f"{tot['candidates']} & {tot['eligible']} & {tot['n']} & \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\begin{tablenotes}\footnotesize",
        r"\item Articles: articles with body prose (articles without it are not counted). Candidates: paragraphs "
        r"the keyword prefilter marks as naming at least one studied technology. $N_h$: candidates in the reserve "
        r"that carry no transcription gap and do not come from the article quoted in the prompt."
        + no_stratum_note + r" Allocation within language by the largest-remainder method, cap "
        rf"{cap} per stratum (binding only where $n_h = {cap}$).",
        r"\end{tablenotes}",
        r"\end{threeparttable}",
        r"\end{table}",
    ]
    # 2026-11-09, slimming debate consensus F-03 (the author's answer "Both"): a small overview by language in the main
    # text under the old label tab:corpus, the outlet table above in Appendix A.2
    langs = sorted(summary)
    srows = [f"{LANG_NAME[L]} & {summary[L]['outlets']} & {summary[L]['articles']} & {num(summary[L]['paragraphs'])} & "
             f"{summary[L]['candidates']} & {summary[L]['eligible']} & {summary[L]['n']} & "
             f"{summary[L]['wmin']:.2f}--{summary[L]['wmax']:.2f} \\\\" for L in langs]
    no_stratum_short = "".join(rf" {tex_escape(k)} forms no stratum." for k in no_stratum)
    small = [
        r"% Generated by tools/annotation/descriptives.py from the corpus manifest, the split",
        r"% registry, the frozen A2 frame manifest and the registered A2 sample manifest. Do not edit by hand.",
        r"% Summary by language since 2026-11-09 (slimming debate consensus F-03); outlet rows: tab:corpus_outlets.",
        r"\begin{table}[htbp]",
        r"\centering",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{3pt}",
        r"\begin{threeparttable}",
        # 2026-11-12, second cut pass (75k): the outlet pointer in parentheses, without the appendix reference
        r"\caption[Corpus and evaluation allocation by language]{Corpus, eligible frame and evaluation allocation by "
        r"language (by outlet: Table~\ref{tab:corpus_outlets}).}",   # 2026-11-12, M16 cut pass (K3-07)
        r"\label{tab:corpus}",
        r"\begin{tabular}{@{}lrrrrrrr@{}}",
        r"\toprule",
        r"\textbf{Language} & \textbf{Outlets} & \textbf{Articles} & \textbf{Paragraphs} & \textbf{Candidates} & "
        r"\textbf{Eligible} & \textbf{Drawn} & \textbf{Weight range} \\",
        r"\midrule",
        *srows,
        f"Total & {sum(v['outlets'] for v in summary.values())} & {len(tot_articles)} & {num(tot['paragraphs'])} & "
        f"{tot['candidates']} & {tot['eligible']} & {tot['n']} & \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\begin{tablenotes}\footnotesize",
        # 2026-11-12, second cut pass (75k): the exclusions point to Figure fig:sample_flow; the weights pointer dropped
        r"\item Only articles with body prose, and their outlets, are counted. Candidates pass the "
        r"keyword prefilter; eligible candidates lie in the reserve and survive the exclusions in "
        r"Figure~\ref{fig:sample_flow}."   # 2026-11-09, second slimming pass (K-38): strata and cap stand in Section 5.2 beside the table
        + no_stratum_short,
        r"\end{tablenotes}",
        r"\end{threeparttable}",
        r"\end{table}",
    ]
    return "\n".join(small) + "\n", "\n".join(lines) + "\n"


def figure_timeline(rows: list[dict]) -> str:
    """Stacked monthly bars by language in the thesis figure style (preamble: thesisfig, figGerman, figEnglish); the
    caption's year share is computed here from the plotted paragraphs.
    Palette 2026-11-06: the preamble now makes figGerman purple and figEnglish yellow (the language series of the appendix
    descriptive figures), so the caption names those colours; the stacking order (German lower, English upper), stated in
    the caption and the legend, doubles the colour in greyscale. Bars and numbers unchanged."""
    by_month: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        month = r["pub_date"][:7]
        by_month[month][r["lang"]] += 1
    months = sorted(by_month)
    # fill the gaps so the axis is a real time axis
    first, last = months[0], months[-1]
    y, m = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    full = []
    while (y, m) <= (ly, lm):
        full.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    de = " ".join(f"({mo}-01,{by_month[mo]['de']})" for mo in full)
    en = " ".join(f"({mo}-01,{by_month[mo]['en']})" for mo in full)
    n_de = sum(c["de"] for c in by_month.values())
    n_en = sum(c["en"] for c in by_month.values())
    last_year = last[:4]
    par_last = sum(1 for r in rows if r["pub_date"][:4] == last_year)
    share = round(100 * par_last / len(rows))
    # a takeaway that matches the shape of the bars: the share of the last year and the months it falls in
    lead = ("More than half" if 2 * par_last > len(rows) else "Nearly half" if share >= 40 else f"{share}\\%")
    first_m = min(r["pub_date"][5:7] for r in rows if r["pub_date"][:4] == last_year)
    span = (f"{MONTHS[int(first_m) - 1]} to {MONTHS[int(last[5:7]) - 1]} {last_year}" if first_m != last[5:7]
            else f"{MONTHS[int(first_m) - 1]} {last_year}")
    years = list(range(int(first[:4]), int(last_year) + 1))       # the axis starts in January of the first year
    xticks = ",".join(f"{{{yy}-01-01}}" for yy in years)
    return "\n".join([
        r"% Generated by tools/annotation/descriptives.py. Do not edit by hand.",
        r"% Palette 2026-11-06: German purple (figGerman) and English yellow (figEnglish) from the preamble; the caption names the colours, the stacking order doubles them. Bars and numbers unchanged.",
        r"\begin{figure}[htbp]",
        r"\centering",
        r"\begin{tikzpicture}",
        r"\begin{axis}[thesisfig,",
        r"  width=140mm, height=45mm, scale only axis,",
        r"  ybar stacked, bar width=2.2mm,",
        r"  date coordinates in=x, xticklabel={\year},",
        f"  xtick={{{xticks}}},",
        f"  xmin={{{first[:4]}-01-01}}, xmax={{{last}-28}}, enlarge x limits=0.02,",
        r"  ymin=0, ylabel={Paragraphs per month},",
        r"  legend columns=2, legend style={yshift=6mm},",      # this axis has no x label: the legend moves up by its height
        r"]",
        f"\\addplot[fill=figGerman, draw=white, line width=0.2pt] coordinates {{{de}}};",
        f"\\addlegendentry{{German ({n_de} paragraphs)}}",
        f"\\addplot[fill=figEnglish, draw=figGreyA, line width=0.2pt] coordinates {{{en}}};",
        f"\\addlegendentry{{English ({n_en} paragraphs)}}",
        r"\end{axis}",
        r"\end{tikzpicture}",
        f"\\caption[Corpus paragraphs by publication month and language]{{\\textbf{{{lead} of the corpus paragraphs date from "
        f"{span}.}} Paragraphs in the corpus per publication month, stacked by language: German (lower, "
        f"purple) and English (upper, yellow); year ticks mark January. {last_year} holds {par_last} of the "
        f"{num(len(rows))} paragraphs ({share}\\%), the rest spread from {first[:4]} to "
        f"{int(last_year) - 1}; the earlier articles were retrieved for specific failure modes the pipeline had to handle.}}",
        r"\label{fig:corpus_timeline}",
        r"\end{figure}",
    ]) + "\n"


# ------------------------------------------------------------------- A2 allocation

def table_allocation() -> str:
    sample = json.loads(SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    alloc = sample["allocation"]
    drawn: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for p in sample["paragraphs"]:
        drawn[(p["lang"], p["outlet"])][p["exposure"]] += 1
        drawn[(p["lang"], p["outlet"])]["reliability"] += int(p["reliability_subsample"])
        drawn[(p["lang"], p["outlet"])]["articles_" + p["article_id"]] += 1
    lines = [
        r"% Generated by tools/annotation/descriptives.py from the registered A2 sample",
        r"% manifest. Do not edit by hand.",
        r"\begin{table}[htbp]",
        r"\centering",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{3pt}",
        r"\begin{threeparttable}",
        r"\caption{Allocation of the evaluation sample across the 18 strata of the frozen frame. "
        r"$N_h$ is the stratum size in the eligible frame, $n_h$ the draw, $\pi_h = n_h/N_h$ "
        r"the selection probability and $1/\pi_h$ the design weight carried by every paragraph "
        r"of the stratum.}",
        r"\label{tab:a2_allocation}",
        r"\begin{tabular}{@{}llrrrrrrr@{}}",
        r"\toprule",
        r"\textbf{Lang.} & \textbf{Outlet} & $\boldsymbol{N_h}$ & $\boldsymbol{n_h}$ & $\boldsymbol{\pi_h}$ & $\boldsymbol{1/\pi_h}$ & \textbf{Articles} & \textbf{Exposed} & \textbf{Reliab.} \\",
        r"\midrule",
    ]
    current = None
    tot = Counter()
    for a in sorted(alloc, key=lambda a: (a["lang"], -a["N_h"], a["outlet"])):
        if current is not None and a["lang"] != current:
            lines.append(r"\addlinespace")
        current = a["lang"]
        pi = Fraction(a["pi"])
        d = drawn[(a["lang"], a["outlet"])]
        n_articles = sum(1 for k in d if k.startswith("articles_"))
        lines.append(
            f"{a['lang']} & {tex_escape(a['outlet'])} & {a['N_h']} & {a['n_h']} & "
            f"{float(pi):.3f} & {float(1/pi):.2f} & {n_articles} & "
            f"{d['sibling_calibration_article']} & {d['reliability']} \\\\")
        tot["N"] += a["N_h"]
        tot["n"] += a["n_h"]
        tot["exposed"] += d["sibling_calibration_article"]
        tot["rel"] += d["reliability"]
    lines += [
        r"\midrule",
        f"Total & 18 strata & {tot['N']} & {tot['n']} & & & {sample['n_articles']} & "
        f"{tot['exposed']} & {tot['rel']} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\begin{tablenotes}\footnotesize",
        r"\item Allocation proportional within language by the largest-remainder method, "
        f"cap {sample['design']['cap_registered']} paragraphs per stratum; the cap binds where "
        r"$n_h$ equals it. Exposed: paragraphs from articles whose other paragraphs had been "
        r"used in calibration or development. Reliab.: paragraphs flagged at the draw for "
        r"the intra-coder re-code after the washout. Seeds and digests are in the sample manifest.",
        r"\end{tablenotes}",
        r"\end{threeparttable}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ sessions

def _date_range(d: str) -> str:
    """'2026-09-28|2026-09-29|2026-09-30' -> '2026-09-28 to 09-30'; a single date unchanged."""
    parts = [x for x in str(d).split("|") if x]
    return parts[0] if len(parts) <= 1 else f"{parts[0]} to {parts[-1][5:]}"


def table_sessions() -> str:
    with SESSIONS.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    # the A3 census sessions (fresh-sample evaluation, after the registered result) are not part of the
    # Chapter 5 session table; their record is in the fresh-sample appendix
    rows = [r for r in rows if not r["session"].startswith("A3_")]
    order = {"A0_pass1": 0, "A0_pass2": 1, "A0b_pass1": 2, "A0b_pass2": 3,
             "A1_pass1": 4, "A1_pass2": 5}
    rows.sort(key=lambda r: order.get(r["session"], 99))
    role = {"A0": "Calibration I", "A0b": "Calibration II", "A1": "Development", "A2": "Evaluation", "A2R": "Re-code",
            "A2R2": "Second re-code"}   # 2026-11-10, §4n (1); coherence round C1 (H-11)
    unit = {"pass1": "paragraphs", "pass2": "paragraph--technology rows"}
    lines = [
        r"% Generated by tools/annotation/descriptives.py from labels/sessions.csv.",
        r"% Do not edit by hand.",
        r"\begin{table}[htbp]", r"\setlength{\tabcolsep}{3pt}\footnotesize",
        r"\centering",
        r"\small",
        r"\begin{threeparttable}",
        r"\caption[Annotation sessions]{Annotation sessions. The calibration and development sessions preceded the evaluation sample and none of their labels enters the reported evaluation. The evaluation session (A2) is the reference and A2R its registered blind re-code. A2R2, the second re-code wave of the other 80 paragraphs, followed the registered results. The fresh census's sessions are in Appendix~\ref{app:fresh}.}",
        r"\label{tab:sessions}",
        r"\begin{tabular}{@{}llllrl@{}}",
        r"\toprule",
        r"\textbf{Session} & \textbf{Pass} & \textbf{Codebook} & \textbf{Coded} & \textbf{Rows} & \textbf{Unit} \\",
        r"\midrule",
    ]
    for r in rows:
        split, p = r["session"].split("_")
        coded = r["coded_at"]
        if "." in coded:  # 29.8.2026 -> 2026-08-29
            d, m, y = coded.split(".")
            coded = f"{y}-{int(m):02d}-{int(d):02d}"
        lines.append(
            f"{role[split]} ({split}) & {p[-1]} & v{r['codebook_version']} & {_date_range(coded)} & "
            f"{r['n_rows']} & {unit[p]} \\\\")
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\begin{tablenotes}\footnotesize",
        r"\item The first pass records technologies present; the second expands to one row "
        r"per paragraph and technology. Both calibration sessions were recoded in the second "
        r"pass under codebook v0.3 before the freeze as v1.0.",
        r"\end{tablenotes}",
        r"\end{threeparttable}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------- element support

def read_pass2(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def element_counts(rows: list[dict]) -> Counter:
    c: Counter = Counter()
    for r in rows:
        for e in ELEMENTS:
            if r.get(e, "") in ("1", "true", "True", "x", "X"):
                c[e] += 1
    return c


def figure_element_support() -> str:
    """Horizontal dot plot in the thesis figure style: one row per element (plain name, code in parentheses), the
    calibration count as an open and the development count as a filled circle, offset within the row, and the
    reportability threshold s = 5 as a dashed line. Black and grey only (both sessions are the coder's; palette
    2026-11-06: the filled circle is figInk explicitly, not the model-blue preamble style pooled)."""
    calib = read_pass2(LABELS / "A0_pass2_v0.3.csv") + read_pass2(LABELS / "A0b_pass2_v0.3.csv")
    dev = read_pass2(LABELS / "A1_pass2_v1.0.csv")
    c_cal, c_dev = element_counts(calib), element_counts(dev)
    n_cal, n_dev = len(calib), len(dev)
    nf_cal = sum(1 for r in calib if r.get("no_frame_for_this_tech") == "1")
    nf_dev = sum(1 for r in dev if r.get("no_frame_for_this_tech") == "1")
    cal = " ".join(f"({c_cal[e]},{i + 1 - 0.18:.2f})" for i, e in enumerate(ELEMENTS))
    devc = " ".join(f"({c_dev[e]},{i + 1 + 0.18:.2f})" for i, e in enumerate(ELEMENTS))
    xmax = 5 * (max(max(c_cal.values()), max(c_dev.values())) // 5 + 1)
    labels = ",".join(f"{{{ELEMENT_SHORT[e]} ({e})}}" for e in ELEMENTS)
    return "\n".join([
        r"% Generated by tools/annotation/descriptives.py from the finalised label files",
        r"% A0_pass2_v0.3.csv, A0b_pass2_v0.3.csv and A1_pass2_v1.0.csv. Do not edit by hand.",
        r"% Palette 2026-11-06: stays black and grey (the coder's counts, no model): the development marks are filled figInk circles drawn explicitly instead of through the preamble style pooled, which is now model blue; the calibration marks stay open grey. Shapes, sizes and numbers unchanged.",
        r"\begin{figure}[htbp]",
        r"\centering",
        r"\begin{tikzpicture}",
        r"\begin{axis}[thesisfig,",
        f"  width=80mm, height={6 * len(ELEMENTS) + 10}mm, scale only axis,",
        f"  xmin=0, xmax={xmax}, xtick={{{','.join(str(t) for t in range(0, xmax + 1, 5))}}}, xlabel={{Rows on which the element fired}},",
        f"  ymin=0.4, ymax={len(ELEMENTS) + 0.6}, y dir=reverse, rowaxis,",
        f"  ytick={{{','.join(str(i + 1) for i in range(len(ELEMENTS)))}}}, yticklabels={{{labels}}},",
        r"  legend columns=2,",
        r"]",
        f"\\draw[refline] (axis cs:5,0.4) -- (axis cs:5,{len(ELEMENTS) + 0.6});",
        r"\node[figtext, text=figGreyA, anchor=south west, inner sep=1pt] at (axis cs:5,0.4) {$s = 5$};",
        # sample is the only factor here: the earlier sessions as open grey-1 circles, the development session filled black.
        # Palette 2026-11-06: the filled circle is drawn in figInk explicitly (the coder's counts), no longer through the
        # preamble style `pooled`, which now means the registered model's score (model blue); same shape and size.
        f"\\addplot[only marks, mark=*, draw=figGreyA, mark options={{draw=figGreyA, fill=white, line width=0.8pt}}] coordinates {{{cal}}};",
        f"\\addlegendentry{{Calibration, v0.3 ({n_cal} rows)}}",
        f"\\addplot[only marks, mark=*, draw=figInk, mark options={{draw=figInk, fill=figInk}}] coordinates {{{devc}}};",
        f"\\addlegendentry{{Development, v1.0 ({n_dev} rows)}}",
        r"\end{axis}",
        r"\end{tikzpicture}",
        r"\caption[Element support in the sessions before the evaluation sample]{\textbf{Before the evaluation sample, several "
        r"elements fired on only a few rows.} Rows per frame element on which the element fired: the two calibration sessions "
        r"recoded under codebook v0.3 (open grey circles) and the development session under the frozen v1.0 (filled circles); "
        r"dashed line: the reportability threshold $s = 5$. Deployment growth (F2c) did not exist before the freeze; the v0.3 "
        r"investment element (F2b) therefore also carried the cases that v1.0 splits off into F2c. "
        f"Rows with no frame for the named technology: {nf_cal} in calibration, {nf_dev} in "
        r"development. The sparsity seen here motivated the reportability rule of "
        r"Section~\ref{subsec:metrics}; its threshold is fixed before the evaluation sample is coded.}",
        r"\label{fig:element_support}",
        r"\end{figure}",
    ]) + "\n"


# ---------------------------------------------------------------------- main

def outputs() -> dict[Path, str]:
    rows, eligible = corpus_rows()
    return {
        **dict(zip((TABLES / "tab_corpus.tex", TABLES / "tab_corpus_outlets.tex"), table_corpus(rows, eligible))),
        TABLES / "tab_a2_allocation.tex": table_allocation(),
        TABLES / "tab_sessions.tex": table_sessions(),
        IMAGES / "corpus_timeline.tex": figure_timeline(rows),
        IMAGES / "element_support.tex": figure_element_support(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if any output would change")
    args = ap.parse_args()
    out = outputs()
    changed = []
    for path, text in out.items():
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != text:
            changed.append(path)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
    for path in out:
        rel = path.relative_to(ROOT)
        print(f"  {'CHANGED' if path in changed else 'same   '} {rel}")
    if args.check and changed:
        sys.exit(1)


if __name__ == "__main__":
    main()
