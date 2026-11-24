#!/usr/bin/env python3
"""W4 -- the seal and the selector of the second development iteration (pre-analysis specification §4,
W4 block of 2026-10-14). A thin phase module over `w3.py`: the same seal document, the same candidate
verification (`w3.assess`) and the same evidence states, with W4's roster (V0 = v1.0, V1 = v1.2S), W4's
files, W4's protocol block and W4's single-candidate rule.

    .venv/bin/python3 tools/pipeline/w4.py --seal --v0 a1_w4_v10_20261014 --v1 a1_w4_v12S_20261014
    .venv/bin/python3 tools/pipeline/w4.py --select

Rule: outcome "selected" only if v1.2S is eligible, V0 is scoreable, S(v1.2S) >= S(V0) and I(v1.2S) <= I(V0);
otherwise "retained" (v1.0); if V0 is not scoreable, "comparison_inconclusive" (v1.0 retained). W4 is
A1-only: nothing here authorises a run on the evaluation sample (the runner's guard reads W3's record only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import w3                                                        # noqa: E402

ROOT, RUNS = w3.ROOT, w3.RUNS
SEAL = RUNS / "w4_seal.json"
SELECTION = RUNS / "w4_selection.json"
ROSTER = {"v0": "v1.0", "v1": "v1.2S"}
CODE = ["instrument.py", "instrument_v11.py", "run_extract.py", "preflight.py", "evaluate.py", "sample_contract.py", "w3.py", "w4.py"]
PINS = ["pins.json", "pins_v1.2S.json"]
ARTEFACTS = [f"instrument/{n}" for n in ("system-prompt_v1.0.txt", "tool-config_v1.0.json",
                                          "system-prompt_v1.2S.txt", "tool-config_v1.2S.json")]


def protocol_text() -> str:
    text = w3.SPEC.read_text(encoding="utf-8")
    m = re.search(r"> \*\*W4 — second post-registration development iteration.*?(?=\n## )", text, re.S)
    if not m:
        raise SystemExit("the specification carries no W4 block; nothing to seal")
    return m.group(0)


def bind_phase() -> None:
    """Point w3's module state at W4's roster, files and protocol. w3.py itself is not edited."""
    w3.SEAL, w3.SELECTION, w3.ROSTER = SEAL, SELECTION, ROSTER
    w3.CODE, w3.PINS, w3.ARTEFACTS = CODE, PINS, ARTEFACTS
    w3.PINS_OF = {**w3.PINS_OF, "v1.2S": "pins_v1.2S.json"}
    w3.RETRY_OF = {**w3.RETRY_OF, "v1.2S": "3"}
    w3.protocol_text = protocol_text


def seal_document(run_ids: dict, now: str | None = None, k: int | None = None, evaluation: dict | None = None,
                  manifest_digest: str | None = None) -> dict:
    bind_phase()
    if set(run_ids) != set(ROSTER) or len(set(run_ids.values())) != 2:
        raise SystemExit("the W4 seal needs two distinct run identifiers for v0 and v1")
    return _seal_two(run_ids, now, w3.K if k is None else k, evaluation, manifest_digest)


def _seal_two(run_ids: dict, now, k, evaluation, manifest_digest) -> dict:
    """w3.seal_document insists on three slots; W4 has two, so the document is assembled here from w3's parts."""
    mdig = manifest_digest or w3.rx.manifest_digest(json.loads(w3.A1_MANIFEST.read_text(encoding="utf-8")))
    contracts = {}
    for slot, name in ROSTER.items():
        c = w3.expected_contract(name, mdig, k)
        contracts[slot] = {"contract": c, "fingerprint": hashlib.sha256(json.dumps(c, sort_keys=True).encode("utf-8")).hexdigest()}
    return {
        "kind": "w4_seal", "phase": "W4", "sealed_at": now or datetime.now(timezone.utc).isoformat(),
        "git_commit": w3.git("rev-parse", "HEAD"), "git_dirty": bool(w3.git("status", "--porcelain")),
        "code": {f: w3.sha(HERE / f) for f in CODE},
        "pins": {f: w3.sha(HERE / f) for f in PINS},
        "artefacts": {f: w3.sha(HERE / f) for f in ARTEFACTS},
        "reference": {"manifest": w3.sha(w3.A1_MANIFEST), **{f: w3.sha(w3.LABELS / f) for f in w3.REFERENCE_FILES}},
        "manifest_digest": mdig, "expected_contracts": contracts,
        "evaluation": dict(evaluation or w3.EVALUATION),
        "protocol_sha256": hashlib.sha256(protocol_text().encode("utf-8")).hexdigest(),
        "roster": ROSTER, "run_ids": dict(run_ids), "restart_policy": w3.RESTART_POLICY,
        "selector": {"rule": "selected only if v1.2S eligible, V0 scoreable, S(v1.2S) >= S(V0) and I(v1.2S) <= I(V0); "
                             "else retained; V0 not scoreable -> comparison_inconclusive; A1-only, no A2 authorisation",
                     "score": "mean over executions 1..3 of the element micro-F1 from per_execution.pooled[k].element_counts_unweighted as exact fractions",
                     "invalidity": "scheduled executions whose first returned model response carried a parse-failure code"},
        "development_set": {"manifest": str(w3.A1_MANIFEST.relative_to(ROOT)) if w3.A1_MANIFEST.is_relative_to(ROOT) else str(w3.A1_MANIFEST),
                            "restrict_split": "A1", "k": k},
    }


def write_seal(run_ids: dict) -> Path:
    bind_phase()
    if SEAL.exists():
        raise SystemExit(f"{SEAL.name} exists; the seal is written once.")
    doc = seal_document(run_ids)                              # the validated path (two distinct identifiers)
    doc["digest"] = w3.digest_of(doc)
    SEAL.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return SEAL


def load_seal() -> dict:
    """W4's own loader: the same integrity checks as W3's (digest recomputed, every sealed file re-hashed,
    reference and protocol digests) over W4's file lists, requiring a W4 seal; W3's loader is not touched."""
    bind_phase()
    if not SEAL.exists():
        raise SystemExit("no W4 seal; run w4.py --seal before the candidate runs")
    seal = json.loads(SEAL.read_text(encoding="utf-8"))
    if seal.get("kind") != "w4_seal" or seal.get("phase") != "W4" or w3.digest_of(seal) != seal.get("digest"):
        raise SystemExit("the seal is not an intact W4 seal (kind, phase or digest)")
    for f, d in seal["code"].items():
        if w3.sha(HERE / f) != d:
            raise SystemExit(f"{f} differs from the sealed code; the selector refuses")
    for f, d in seal["pins"].items():
        if w3.sha(HERE / f) != d:
            raise SystemExit(f"{f} differs from the sealed pins")
    for f, d in seal["artefacts"].items():
        if w3.sha(HERE / f) != d:
            raise SystemExit(f"{f} differs from the sealed artefacts")
    if w3.sha(w3.A1_MANIFEST) != seal["reference"]["manifest"]:
        raise SystemExit("the A1 manifest differs from the sealed one")
    for f in w3.REFERENCE_FILES:
        if w3.sha(w3.LABELS / f) != seal["reference"][f]:
            raise SystemExit(f"{f} differs from the sealed development reference")
    if hashlib.sha256(protocol_text().encode("utf-8")).hexdigest() != seal["protocol_sha256"]:
        raise SystemExit("the W4 block of the specification differs from the sealed protocol text")
    if set(seal.get("run_ids") or {}) != set(ROSTER) or len(set(seal["run_ids"].values())) != 2:
        raise SystemExit("the W4 seal does not carry two distinct run identifiers")
    return seal


def decide(c0: dict, c1: dict) -> dict:
    """The W4 rule as a pure function of the two assessments (S values are Fraction strings or None)."""
    def S(c):
        return Fraction(c["S"]) if c.get("S") is not None else None
    if not (c0.get("scoreable") and S(c0) is not None):
        return {"outcome": "comparison_inconclusive", "selected": "v1.0",
                "reason": "the contemporaneous v1.0 comparator is not scoreable: " + "; ".join(c0.get("problems") or ["no score"])}
    if not c1["eligible"] or S(c1) is None:
        return {"outcome": "retained", "selected": "v1.0", "reason": "v1.2S is not eligible: " + "; ".join(c1["problems"])}
    if S(c1) >= S(c0) and int(c1["I"]) <= int(c0["I"]):
        tie = (S(c1) == S(c0)) or (int(c1["I"]) == int(c0["I"]))
        return {"outcome": "selected", "selected": "v1.2S",
                "reason": f"eligible; S {float(S(c1)):.4f} >= {float(S(c0)):.4f} and I {c1['I']} <= {c0['I']}"
                          + ("; a tie is reported as matching the comparator's observed development performance" if tie else "")}
    return {"outcome": "retained", "selected": "v1.0",
            "reason": f"S {float(S(c1)):.4f} vs {float(S(c0)):.4f}, I {c1['I']} vs {c0['I']}: not at least as good on both"}


def select(v0: str | None = None, v1: str | None = None) -> dict:
    seal = load_seal()
    ids = {"v0": v0 or seal["run_ids"]["v0"], "v1": v1 or seal["run_ids"]["v1"]}
    c = {slot: w3.assess(ids[slot], slot, seal) for slot in ("v0", "v1")}
    d = decide(c["v0"], c["v1"])
    out = {"kind": "w4_selection", "phase": "W4", "selected_at": datetime.now(timezone.utc).isoformat(),
           "seal_digest": seal["digest"], "candidates": c, "decision": d,
           "note": "A1-only: this record authorises no run on the evaluation sample; the only evaluation route is the A3 protocol"}
    if SELECTION.exists():
        raise SystemExit(f"{SELECTION.name} exists; the selection is recorded once.")
    SELECTION.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seal", action="store_true")
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--v0"); ap.add_argument("--v1")
    a = ap.parse_args()
    if a.seal:
        if not (a.v0 and a.v1):
            sys.exit("--seal needs --v0 --v1 (the two permitted run identifiers)")
        p = write_seal({"v0": a.v0, "v1": a.v1})
        print(f"sealed: {p.relative_to(ROOT)}")
        return
    if a.select:
        out = select(a.v0, a.v1)
        for key, c in out["candidates"].items():
            print(f"{key} {c['instrument']:6s} {c['run_id']}: assessed={c['assessed']} eligible={c['eligible']} scoreable={c['scoreable']} "
                  f"S={c['S_float']} I={c['I']} retries={c['retries']} (feedback {c['feedback_retries']}) outcomes={c['terminal_outcomes']} "
                  f"{'; '.join(c['problems'])}")
        print(f"decision: {out['decision']['outcome']} -> {out['decision']['selected']} ({out['decision']['reason']})")
        print(f"  record  {SELECTION.relative_to(ROOT)}")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
