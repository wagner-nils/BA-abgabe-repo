#!/usr/bin/env python3
"""Reproduction check of the thesis (decision-sheet item D44, 2026-11-06): one command that (1) re-renders every
generated table and figure from the committed run records, labels and manifests and compares it with the file in
paper/ (each generator's --check mode), and (2) traces every number of the main text (Chapters 1-8 and the abstract)
to a machine-written result: a results file under tools/runs/ or a generated table or figure.

    .venv/bin/python3 tools/pipeline/reproduce.py [--skip-generators] [--verbose]

Part 2 collects every number that the results files contain, in the roundings the text uses (integers; one to three
decimals; shares as percentages with zero or one decimal), plus every number printed in a generated float, and reports
each number of the running text that none of them contains. A reported number is not necessarily wrong: counts from
the corpus manifest, dates, model versions or a number derived in the text (a difference of two printed values) are
legitimate; the list is what a reader checks by hand, with the `% SRC:` comment above each paragraph naming the source.
Nothing is written. Exit 1 if a generator check fails.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PAPER = ROOT / "paper"
PY = str(ROOT / ".venv" / "bin" / "python3")
L = "tools/annotation/labels"
GENERATORS = [
    [PY, "tools/pipeline/figures.py", "dsl", "--check"],
    [PY, "tools/pipeline/figures.py", "language", "--check"],
    [PY, "tools/pipeline/dsl.py", "--a2-run", "tools/runs/a2_sonnet_20261013.json", "--completion-run",
     "tools/runs/frame_completion_20261019.json", "--a3-run", "tools/runs/a3_sonnet_20261019.json",
     "--gold-pass1", f"{L}/A2_pass1_v1.0.csv", "--gold-pass2", f"{L}/A2_pass2_v1.0.csv",
     "--a3-gold-pass1", f"{L}/A3_pass1_v1.0.csv", "--a3-gold-pass2", f"{L}/A3_pass2_v1.0.csv", "--threshold", "5", "--check"],
    [PY, "tools/pipeline/error_breakdown.py", "--run-record", "tools/runs/a2_sonnet_20261013.json", "--manifest",
     f"{L}/A2_sample_manifest.json", "--gold-pass1", f"{L}/A2_pass1_v1.0.csv", "--gold-pass2", f"{L}/A2_pass2_v1.0.csv",
     "--threshold", "5", "--check"],
    [PY, "tools/pipeline/compare_models.py", "--check"],
    [PY, "tools/pipeline/compare_form.py", "--check"],
    [PY, "tools/pipeline/compare_context.py", "--check"],
    [PY, "tools/pipeline/compare_a3_candidate.py", "--check"],
    [PY, "tools/pipeline/battery_analyse.py", "--check"],
    [PY, "tools/pipeline/review_analyses.py", "--check"],
    [PY, "tools/pipeline/compare_gen55.py", "--check"],          # §4m (2026-11-08): Opus 5.5 and the precision-recall gap
    [PY, "tools/pipeline/review_checks_4o.py", "--check"],       # §4o (2026-11-10): checks after the third review
    [PY, "tools/pipeline/review_checks_4p.py", "--check"],       # §4p (2026-11-11): reference reading, baselines, failed output
]
# generators without a --check mode: re-render into the working tree and require no change against git
RERENDER = [
    [PY, "tools/pipeline/figures.py", "codebook"],
    [PY, "tools/pipeline/figures.py", "tables", "--metrics", "tools/runs/tables/a2_sonnet_20261013/metrics.json"],
    [PY, "tools/pipeline/figures.py", "results", "--metrics", "tools/runs/tables/a2_sonnet_20261013/metrics.json"],
    [PY, "tools/pipeline/codebook_review.py", "score"],
]
NUM = re.compile(r"(?<![\w.\\])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")


def run_generators(verbose: bool) -> bool:
    ok = True
    for cmd in GENERATORS:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        good = r.returncode == 0 and "STALE" not in (r.stdout + r.stderr)
        ok &= good
        print(f"  {'ok  ' if good else 'FAIL'} {' '.join(Path(c).name if i == 1 else c for i, c in enumerate(cmd[1:4]))}")
        if not good or verbose:
            print("       " + (r.stdout + r.stderr).strip().replace("\n", "\n       ")[:800])
    before = subprocess.run(["git", "status", "--porcelain", "paper/"], cwd=ROOT, capture_output=True, text=True).stdout
    for cmd in RERENDER:
        subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    r = subprocess.run(["git", "status", "--porcelain", "paper/"], cwd=ROOT, capture_output=True, text=True).stdout
    changed = sorted(set(r.splitlines()) - set(before.splitlines()))
    good = not changed
    ok &= good
    print(f"  {'ok  ' if good else 'FAIL'} re-render of figures.py codebook/tables/results and codebook_review score"
          + ("" if good else f": changed {changed}"))
    return ok


def _walk_json(x, out: set):
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        add_value(float(x), out)
    elif isinstance(x, dict):
        for v in x.values():
            _walk_json(v, out)
    elif isinstance(x, list):
        for v in x:
            _walk_json(v, out)
    elif isinstance(x, str):
        for n in NUM.findall(x):
            out.add(n.replace(",", ""))


def add_value(v: float, out: set):
    if v == int(v) and abs(v) < 1e7:
        out.add(str(int(abs(v))))
    for d in (1, 2, 3):
        out.add(f"{abs(v):.{d}f}")
    if abs(v) <= 1.0:
        for d in (0, 1):
            out.add(f"{abs(v) * 100:.{d}f}")


def evidence() -> set:
    out: set = set()
    for p in (ROOT / "tools" / "runs").rglob("*"):
        if p.suffix == ".json" and p.stat().st_size < 30_000_000:
            try:
                _walk_json(json.loads(p.read_text(encoding="utf-8")), out)
            except (ValueError, UnicodeDecodeError):
                pass
        elif p.suffix == ".csv" and "tables" in p.parts:
            for n in NUM.findall(p.read_text(encoding="utf-8", errors="replace")):
                out.add(n.replace(",", ""))
    for p in list((PAPER / "tables").glob("*.tex")) + list((PAPER / "images").glob("*.tex")):
        t = p.read_text(encoding="utf-8")
        if "Generated by" in t[:400]:
            for n in NUM.findall(re.sub(r"(?<!\\)%.*", "", t)):
                out.add(n.replace(",", "").replace("{,}", ""))
    return out


def text_numbers(path: Path, part: str | None = None):
    t = path.read_text(encoding="utf-8")
    if part == "abstract":
        t = t[t.index("\\begin{abstract}"):t.index("\\end{abstract}")]
    t = re.sub(r"(?<!\\)%.*", "", t)
    t = re.sub(r"\\(label|ref|eqref|cite\w*|parencite\w*|textcite\w*|input|includegraphics|modelid|texttt)\*?(\[[^\]]*\])*\{[^}]*\}", " ", t)
    t = t.replace("{,}", ",")
    for i, line in enumerate(t.splitlines(), 1):
        for n in NUM.findall(line):
            yield i, n.replace(",", ""), line.strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--skip-generators", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    ok = True
    if not a.skip_generators:
        print("(1) generated floats against the run records")
        ok = run_generators(a.verbose)
    print("(2) numbers of the main text traced to machine-written results")
    ev = evidence()
    files = [(PAPER / "BA_NW_2026.tex", "abstract")] + [(p, None) for p in sorted((PAPER / "chapters").glob("0*.tex"))]
    untraced = []
    for f, part in files:
        for i, n, line in text_numbers(f, part):
            small = re.fullmatch(r"\d", n) is not None
            year = re.fullmatch(r"(19|20)\d\d", n) is not None
            if small or year or n in ev:
                continue
            untraced.append((f.name + (" (abstract)" if part else ""), n, line[:140]))
    print(f"  {len(untraced)} number(s) without a match in a results file or a generated float"
          + (" (check by hand; the % SRC comment of the paragraph names the source):" if untraced else ""))
    for f, n, line in untraced:
        print(f"    {f:<34} {n:>9}  {line}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
