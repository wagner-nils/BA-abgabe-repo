#!/usr/bin/env python3
"""Where and how to sharpen: packets for the qualitative reading of the §4i battery (specification §4i, 2026-10-30).

For each unit the analysis flags, one private packet: the unit's definition as rendered, its accepted paraphrases, and
for every variant the paragraphs on which the unit flips against the baseline on the same route while every valid
baseline execution on that route (at least two) agrees (so the flip is neither repetition noise nor a route difference) -- with the paragraph text and the two relations (baseline and
variant) for the technologies concerned. Readers turn the recurring pattern into a proposed rule; the public summary
carries the rules and counts only, never paragraph text.

    .venv/bin/python3 tools/pipeline/battery_sharpen.py packets --units general,F2c,...   # private packets
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import battery as bt                                                # noqa: E402
import battery_analyse as ba                                        # noqa: E402
import run_extract as rx                                            # noqa: E402

PACKETS = bt.RAW / "sharpen_packets"
MAX_PER_VARIANT = 6


def relevant(unit: str, v: str) -> bool:
    """The variants whose flips speak to a unit's definition: its own one-unit paraphrases, the all-texts paraphrases,
    the planted gaps, neutral labels, and for `general` the two reading sentences."""
    return (v.startswith(f"Pu_{unit}_") or v in ("Pa1", "Pa2", "E1", "E2", "G")
            or (unit in ("general", "general_beside") and v in ("K1", "K2")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("packets")
    p.add_argument("--units", required=True)
    a = ap.parse_args()
    analysis = json.loads((bt.OUT / "analysis.json").read_text(encoding="utf-8"))
    flips = analysis["flips_on_stable_paragraphs"]["by_class"]
    doc, subtypes, cb_sha, art = bt.base()
    units_txt = bt.text_units(doc)
    acc = bt.load_paraphrases()
    vs_all = bt.variants(doc, subtypes, cb_sha, art, acc)
    latest = ba.latest_terminals()
    if ba.terminal_fingerprint(latest) != analysis.get("terminals"):
        sys.exit("REFUSING: analysis.json was not computed from the current terminal set; run battery_analyse.py first")
    payload = {(v, k, pid): r.get("payload") for (s, v, k, pid), r in latest.items() if s == "FC"}
    route = {(v, k, pid): r.get("route", "eu") for (s, v, k, pid), r in latest.items() if s == "FC"}
    r_execs = sorted({k for (v, k, pid) in payload if v == "R"})

    def baseline_relations(v, k, pid):
        """The relations of the first valid baseline execution on the variant execution's route (as the analysis)."""
        for j in r_execs:
            if route.get(("R", j, pid)) == route[(v, k, pid)] and payload.get(("R", j, pid)) is not None:
                return j, payload[("R", j, pid)].get("relations") or []
        return None, []
    man = json.loads(bt.FC_MANIFEST.read_text(encoding="utf-8"))
    texts = rx.load_texts(man["paragraphs"])
    PACKETS.mkdir(parents=True, exist_ok=True)
    for unit in a.units.split(","):
        cls = unit if unit[:1] == "F" or unit == "general_beside" else f"tech:{unit}"
        tech = None if unit[:1] == "F" else ("general" if unit == "general_beside" else unit)
        defs = {path: u["text"] for path, u in units_txt.items() if u["unit"] in (unit, tech)}
        paras = {s_: {p_: acc.get(s_, {}).get(p_) for p_ in defs if acc.get(s_, {}).get(p_)} for s_ in ("Pa1", "Pa2", "Pu_P1", "Pu_P2", "Pu_P3", "Pu_P4")}
        cases = []
        for vk, fl in sorted((flips.get(cls) or {}).items()):
            v, k = vk.split("#")
            if not relevant(unit, v):
                continue
            for direction in ("on", "off"):
                pids = list(fl[direction])
                random.Random(f"{bt.SEED}-{unit}-{vk}-{direction}").shuffle(pids)     # per case list, order-free
                for pid in pids[:MAX_PER_VARIANT]:
                    j, rel_r = baseline_relations(v, int(k), pid)
                    rel_v = (payload.get((v, int(k), pid)) or {}).get("relations") or []
                    cases.append({"variant": v, "execution": int(k), "unit_turns": direction, "paragraph_id": pid,
                                  "route": route[(v, int(k), pid)], "baseline_execution": j,
                                  "text": texts[pid]["paragraph_text"], "baseline_relations": rel_r, "variant_relations": rel_v})
        packet = {"unit": unit, "class": cls, "definitions": defs, "paraphrases": paras,
                  "variant_changes": {v: vs_all[v]["change"] for v in {c["variant"] for c in cases}},
                  "cases": cases, "family_results": {v: (analysis["families"].get(v) or {}).get(cls)
                                                     for v in analysis["families"] if (analysis["families"].get(v) or {}).get(cls)}}
        out = PACKETS / f"{unit}.json"
        out.write_text(json.dumps(packet, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  {unit}: {len(cases)} cases -> {out}")


if __name__ == "__main__":
    main()
