#!/usr/bin/env python3
"""Tests for draw_a3.py (A3 fresh-sample listing): the frame rule, the text-free exposure audit (an article
identifier inside a paragraph identifier, a digest prefix), the refusal, and the write guard. Stdlib only;
runs on the system interpreter like every tool in this folder:

    python3 tools/annotation/test_draw_a3.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import draw_a3 as d3                                                 # noqa: E402

PASSED, FAILED = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append((name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def para(pid: str, exposure: str = "clean_article", lang: str = "de") -> dict:
    return {"paragraph_id": pid, "article_id": pid.split("_")[0], "digest": (pid + "x" * 64)[:64].replace("_", "a"),
            "text_sha256": (pid + "y" * 16)[:16].replace("_", "b"), "lang": lang, "outlet": "taz", "exposure": exposure}


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="a3test_"))
    old = (d3.ROOT, d3.AUDIT_SOURCES, d3.A3_SAMPLE_MANIFEST, d3.SHEETS, d3.LABELS)
    try:
        frame = {"paragraphs": [para("A001_p01"), para("A001_p02"), para("A002_p01", "sibling_calibration_article"),
                                para("A003_p01"), para("A003_p02", lang="en")]}
        a2 = {"paragraphs": [{"paragraph_id": "A001_p01", "article_id": "A001", "digest": para("A001_p01")["digest"]}]}
        out = d3.a3_frame(frame, a2)
        check("T1 frame rule: A2 digests, A2 articles and non-clean exposure are excluded; the rest is listed by id",
              [q["paragraph_id"] for q in out] == ["A003_p01", "A003_p02"], str([q["paragraph_id"] for q in out]))

        d3.ROOT = tmp; d3.AUDIT_SOURCES = [("runs", "*.jsonl"), ("labels", "*.csv")]
        (tmp / "runs").mkdir(); (tmp / "labels").mkdir()
        (tmp / "runs" / "x_calls.jsonl").write_text('{"paragraph_id": "A003_p01", "type": "terminal"}\n', encoding="utf-8")
        audit = d3.exposure_audit(out)
        check("T2 an article identifier inside a paragraph identifier is found and refused",
              not audit["clean"] and audit["overlap"].get("runs/x_calls.jsonl") == ["A003"], str(audit["overlap"]))
        (tmp / "runs" / "x_calls.jsonl").write_text('{"paragraph_id": "A999_p01"}\n', encoding="utf-8")
        (tmp / "labels" / "y.csv").write_text("id;digest\nZ;" + out[1]["digest"][:16] + "\n", encoding="utf-8")
        audit = d3.exposure_audit(out)
        check("T3 a 16-character digest prefix of an A3 paragraph is found and refused",
              not audit["clean"] and audit["overlap"].get("labels/y.csv") == [out[1]["digest"][:16]], str(audit["overlap"]))
        (tmp / "labels" / "y.csv").write_text("id;text_sha256\nZ;" + out[0]["text_sha256"] + "\n", encoding="utf-8")
        audit = d3.exposure_audit(out)
        check("T4 a text hash of an A3 paragraph is found and refused", not audit["clean"] and "labels/y.csv" in audit["overlap"])
        (tmp / "labels" / "y.csv").write_text("id;digest\nZ;abc\n", encoding="utf-8")
        audit = d3.exposure_audit(out)
        check("T5 a clean audit lists every file read with its digest and identifier count",
              audit["clean"] and len(audit["files"]) == 2 and all(len(f["sha256"]) == 64 and "article_ids" in f for f in audit["files"]),
              json.dumps(audit["files"]))
        check("T6 the identifier pattern matches standalone and paragraph/row-embedded identifiers only",
              d3.ARTICLE_RE.findall("a038 A038_p01 XA038 A0381 A038 A038_p01_wind") == ["A038", "A038", "A038"])

        d3.A3_SAMPLE_MANIFEST = tmp / "A3_sample_manifest.json"; d3.A3_SAMPLE_MANIFEST.write_text("{}", encoding="utf-8")
        d3.SHEETS = tmp / "sheets"; d3.LABELS = tmp / "labels"
        try:
            d3.write_sheets({"paragraphs": []}, [], None); refused = False
        except d3.DrawError:
            refused = True
        check("T7 an existing target refuses the listing (A3 is listed once, nothing is overwritten)", refused)
    finally:
        d3.ROOT, d3.AUDIT_SOURCES, d3.A3_SAMPLE_MANIFEST, d3.SHEETS, d3.LABELS = old
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for n, d in FAILED:
            print(f"  FAILED: {n}  {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
