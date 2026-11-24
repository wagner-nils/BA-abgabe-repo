#!/usr/bin/env python3
"""W3 -- the seal and the selector of the post-registration development phase (pre-analysis
specification §4, block of 2026-10-14; Codex rounds 1-3 of 2026-10-13/14 in the review log).

    .venv/bin/python3 tools/pipeline/w3.py --seal --v0 a1_w3_v10_20261014 --v1 a1_w3_v11S_20261014 --v2 a1_w3_v11E_20261014
    .venv/bin/python3 tools/pipeline/w3.py --select

The seal (tools/runs/w3_seal.json) is written ONCE, before the first candidate run: the digests of the
code the candidates run under (this file included), the pins and artefacts of every candidate, the
development reference, the selector's constants, the three permitted run identifiers with the restart
rule, and the digest of the protocol text. The selector recomputes the seal's digest, refuses any
sealed file that differs from it (its own bytes included), any run identifier outside the sealed set,
any run that started before the seal, and verifies each candidate's full execution contract, every
gate artefact and the journal binding (through the evaluator's own verifier), the exact terminal
identities and the development metrics' provenance before it computes eligibility, S or I.

The rule, verbatim from the protocol: eligible = every journalled gate certified, complete terminal
coverage, every terminal technologies_present or off_topic. S = mean of the three execution-specific
unweighted element micro-F1 values, computed from the integer counts as exact fractions. I = scheduled
executions whose first RETURNED model response (the first attempt that was not a transport failure)
carried a parse-failure code. Incumbent V1 if eligible, else "not achieved"; V2 replaces the incumbent
only if V2 is eligible, S(V2) - S(incumbent) >= 1/20 and I(V2) <= I(incumbent). V0 is SCOREABLE when
its gates certified, its coverage is complete and its development metrics exist (parse failures are
omissions, not disqualifying); if V0 is scoreable and S(selected) < S(V0) the outcome is "no upgrade";
an unscoreable V0 is reported as "baseline not assessed". Every candidate is reported; only the
outcome "selected" authorises any later run of a revised instrument on A2 (the author's decision).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
LABELS = ROOT / "tools" / "annotation" / "labels"
SPEC = ROOT / "tools" / "docs" / "pre-analysis-specification.md"
A1_MANIFEST = LABELS / "A1_manifest.json"
REFERENCE_FILES = ["A1_pass1_v1.0.csv", "A1_pass2_v1.0.csv"]
SEAL = RUNS / "w3_seal.json"
SELECTION = RUNS / "w3_selection.json"

sys.path.insert(0, str(HERE))
import instrument as ins                                         # noqa: E402
import run_extract as rx                                         # noqa: E402
import evaluate as ev                                            # noqa: E402

MARGIN = Fraction(1, 20)
ROSTER = {"v0": "v1.0", "v1": "v1.1S", "v2": "v1.1E"}
PINS_OF = {"v1.0": "pins.json", "v1.1S": "pins_v1.1S.json", "v1.1E": "pins_v1.1E.json"}
RETRY_OF = {"v1.0": "2", "v1.1S": "3", "v1.1E": "3"}
CODE = ["instrument.py", "instrument_v11.py", "run_extract.py", "preflight.py", "evaluate.py", "sample_contract.py", "w3.py"]
PINS = ["pins.json", "pins_v1.1S.json", "pins_v1.1E.json"]
ARTEFACTS = [f"instrument/{n}" for n in (
    "system-prompt_v1.0.txt", "tool-config_v1.0.json", "system-prompt_v1.1S.txt", "tool-config_v1.1S.json",
    "system-prompt_v1.1E.txt", "tool-config_v1.1E.json")]
PARSE_FAIL_CODES = rx.PARSE_FAIL_CODES
TRANSPORT = {rx.THROTTLED, rx.ERROR}
SUCCESS = {rx.T_PRESENT, rx.T_OFF}
K = 3
MAX_TOKENS = 1024
RESTART_POLICY = ("a run that aborted before its first extraction call (a gate refusal: journal without attempt_started, "
                  "last record run_finished with a refused_* outcome) may be restarted ONCE, as exactly <id>_r1; "
                  "a run that made a call is only resumed under its own id")
EVALUATION = {"threshold": 5, "B": ev.DEFAULT_B, "seed": ev.SEED}   # exactly as the dry run of 2026-10-09


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git(*a: str) -> str:
    try:
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def protocol_text() -> str:
    """The W3 block of the specification, from its heading to the next section heading."""
    text = SPEC.read_text(encoding="utf-8")
    m = re.search(r"> \*\*W3 — post-registration instrument development.*?(?=\n## )", text, re.S)
    if not m:
        raise SystemExit("the specification carries no W3 block; nothing to seal")
    return m.group(0)


def digest_of(doc: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in doc.items() if k != "digest"}, sort_keys=True).encode("utf-8")).hexdigest()


def expected_contract(instrument: str, manifest_digest: str, k: int) -> dict:
    """The complete execution contract a candidate run must start under, computed by the runner's own
    fingerprint function with the candidate's bound instrument and pinned artefacts (Codex W3 round 4, #3)."""
    rx.bind_instrument(instrument)
    try:
        art = rx.ins.load_artifacts()
        return rx.fingerprint_payload(art, "sonnet", k, manifest_digest, rx.ins.REGION, "A1", True)
    finally:
        rx.bind_instrument("v1.0")


def seal_document(run_ids: dict, now: str | None = None, k: int = K, evaluation: dict | None = None,
                  manifest_digest: str | None = None) -> dict:
    if set(run_ids) != set(ROSTER) or len(set(run_ids.values())) != 3:
        raise SystemExit("the seal needs three distinct run identifiers for v0, v1 and v2")
    mdig = manifest_digest or rx.manifest_digest(json.loads(A1_MANIFEST.read_text(encoding="utf-8")))
    contracts = {}
    for slot, name in ROSTER.items():
        c = expected_contract(name, mdig, k)
        contracts[slot] = {"contract": c, "fingerprint": hashlib.sha256(json.dumps(c, sort_keys=True).encode("utf-8")).hexdigest()}
    return {
        "kind": "w3_seal", "sealed_at": now or datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
        "code": {f: sha(HERE / f) for f in CODE},
        "pins": {f: sha(HERE / f) for f in PINS},
        "artefacts": {f: sha(HERE / f) for f in ARTEFACTS},
        "reference": {"manifest": sha(A1_MANIFEST), **{f: sha(LABELS / f) for f in REFERENCE_FILES}},
        "manifest_digest": mdig,
        "expected_contracts": contracts,
        "evaluation": dict(evaluation or EVALUATION),
        "protocol_sha256": hashlib.sha256(protocol_text().encode("utf-8")).hexdigest(),
        "roster": ROSTER, "run_ids": dict(run_ids), "restart_policy": RESTART_POLICY,
        "selector": {"margin": str(MARGIN),
                     "score": "mean over executions 1..3 of the element micro-F1 computed from per_execution.pooled[k].element_counts_unweighted as exact fractions",
                     "invalidity": "scheduled executions whose first returned model response (first non-transport attempt) carried a parse-failure code",
                     "eligibility": "every journalled gate certified; exactly the A1 paragraphs x 3 executions; every terminal technologies_present or off_topic",
                     "baseline": "V0 scoreable = gates certified, complete coverage, development metrics present (parse failures are omissions, not disqualifying)",
                     "rule": "incumbent V1 if eligible else not_achieved; V2 replaces only if V2 eligible and S gain >= 1/20 and I not higher; "
                             "if V0 scoreable and S(selected) < S(V0) then no_upgrade; an unscoreable V0 is reported as baseline not assessed; "
                             "only the outcome selected authorises a later A2 run"},
        "development_set": {"manifest": str(A1_MANIFEST.relative_to(ROOT)) if A1_MANIFEST.is_relative_to(ROOT) else str(A1_MANIFEST),
                            "restrict_split": "A1", "k": k},
    }


def write_seal(run_ids: dict) -> Path:
    if SEAL.exists():
        raise SystemExit(f"{SEAL.name} exists; the seal is written once. Remove it deliberately to re-seal.")
    doc = seal_document(run_ids)
    doc["digest"] = digest_of(doc)
    SEAL.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return SEAL


def load_seal() -> dict:
    """The seal, with its digest recomputed and every sealed file re-hashed -- this file included."""
    if not SEAL.exists():
        raise SystemExit("no seal; run --seal before the candidate runs")
    seal = json.loads(SEAL.read_text(encoding="utf-8"))
    if seal.get("kind") != "w3_seal" or digest_of(seal) != seal.get("digest"):
        raise SystemExit("the seal's digest does not match its content; the seal was edited")
    for f, d in seal["code"].items():
        if sha(HERE / f) != d:
            raise SystemExit(f"{f} differs from the sealed code; the selector refuses")
    for f, d in seal["pins"].items():
        if sha(HERE / f) != d:
            raise SystemExit(f"{f} differs from the sealed pins")
    for f, d in seal["artefacts"].items():
        if sha(HERE / f) != d:
            raise SystemExit(f"{f} differs from the sealed artefacts")
    if sha(A1_MANIFEST) != seal["reference"]["manifest"]:
        raise SystemExit("the A1 manifest differs from the sealed one")
    for f in REFERENCE_FILES:
        if sha(LABELS / f) != seal["reference"][f]:
            raise SystemExit(f"{f} differs from the sealed development reference")
    if hashlib.sha256(protocol_text().encode("utf-8")).hexdigest() != seal["protocol_sha256"]:
        raise SystemExit("the W3 block of the specification differs from the sealed protocol text")
    return seal


# ---- the selector -------------------------------------------------------------------------- #
def read_journal(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def aborted_before_extraction(run_id: str) -> str | None:
    """The refused_* outcome of a journal that never made an extraction call, else None."""
    jp = RUNS / f"{run_id}_calls.jsonl"
    if not jp.exists():
        return None
    recs = read_journal(jp)
    if any(r.get("type") == "attempt_started" for r in recs):
        return None
    last = recs[-1] if recs else {}
    if last.get("type") == "run_finished" and str(last.get("outcome", "")).startswith("refused"):
        return str(last["outcome"])
    return None


def permitted_run_id(seal: dict, slot: str, run_id: str) -> None:
    """The sealed id, or a restart under the sealed restart rule; anything else is refused."""
    sealed = seal["run_ids"][slot]
    if run_id == sealed:
        return
    if run_id != f"{sealed}_r1":
        raise SystemExit(f"{run_id} is not the sealed run id for {slot} ({sealed}) nor its one permitted restart {sealed}_r1")
    if aborted_before_extraction(sealed) is None:
        raise SystemExit(f"{run_id} is a restart, but {sealed} did not abort before its first extraction call")


def f1_fraction(counts: dict) -> Fraction | None:
    """Exact micro-F1 from integer counts; undefined (None) when nothing was predicted and nothing was there."""
    tp, fp, fn = int(counts["tp"]), int(counts["fp"]), int(counts["fn"])
    den = 2 * tp + fp + fn
    return Fraction(2 * tp, den) if den else None


def _resolve(meta_path: str) -> Path:
    q = Path(meta_path)
    return q if q.is_absolute() else ROOT / q


def assess(run_id: str, slot: str, seal: dict) -> dict:
    """One candidate: contradictory evidence raises (SystemExit); an incomplete or failed candidate is
    reported as not eligible / not assessed with its reasons, so that every protocol outcome can be recorded."""
    want = ROSTER[slot]
    permitted_run_id(seal, slot, run_id)
    out = {"run_id": run_id, "slot": slot, "instrument": want, "assessed": False, "eligible": False, "scoreable": False,
           "problems": [], "S": None, "S_float": None, "I": None, "terminal_outcomes": {}, "retries": None,
           "feedback_retries": None, "cost_usd": None, "started_at": None}
    rec_path = RUNS / f"{run_id}.json"
    if not rec_path.exists():
        ab = aborted_before_extraction(run_id)
        out["problems"].append(f"aborted before extraction ({ab})" if ab else "no run record and no journalled abort")
        return out
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    out["started_at"] = rec.get("started_at"); out["cost_usd"] = (rec.get("cost") or {}).get("estimated_usd")
    contract = rec.get("execution_contract") or {}
    ident = ev.instrument_identity(rec)
    if ident["name"] != want:
        raise SystemExit(f"{run_id} bound instrument {ident['name']!r}, the roster names {want!r}")
    # the COMPLETE execution contract and its fingerprint, as sealed (Codex W3 round 4, #3)
    exp = seal["expected_contracts"][slot]
    if contract != exp["contract"]:
        diff = sorted(k_ for k_ in set(contract) | set(exp["contract"]) if contract.get(k_) != exp["contract"].get(k_))
        raise SystemExit(f"{run_id}: execution_contract differs from the sealed one in {diff}")
    if rec.get("execution_fingerprint") != exp["fingerprint"]:
        raise SystemExit(f"{run_id}: execution_fingerprint is not the sealed one")
    if not rec.get("dev") or rec.get("analysis_status", "development") != "development":
        raise SystemExit(f"{run_id} is not a development run")
    if str(rec.get("started_at")) <= str(seal["sealed_at"]):
        raise SystemExit(f"{run_id} started at {rec.get('started_at')}, before the seal {seal['sealed_at']}")
    k = int(exp["contract"]["repeats"])
    # the public files must be the ones the record names, byte for byte, BEFORE the journal is read
    # (the journal verifier assumes this check; Codex W3 round 4, #2)
    files = rec.get("files") or {}
    paths = {}
    for name, meta in files.items():
        q = _resolve(meta["path"])
        if not q.exists() or sha(q) != meta["sha256"]:
            out["problems"].append(f"public file {name} is missing or differs from the run record")
        paths[name] = q
    calls_path = next((q for n, q in paths.items() if n.endswith("_calls.jsonl")), None)
    term_path = next((q for n, q in paths.items() if n.endswith("_terminal.csv")), None)
    pred_path = next((q for n, q in paths.items() if n.endswith("_predictions.csv")), None)
    if out["problems"] or not (calls_path and term_path and pred_path):
        if not (calls_path and term_path and pred_path):
            out["problems"].append("the run record does not name all three public files")
        return out                                           # evidence incomplete: not assessed
    term_rows = list(csv.DictReader(term_path.open(encoding="utf-8"), delimiter=";"))
    pred_rows = list(csv.DictReader(pred_path.open(encoding="utf-8"), delimiter=";"))
    _, paras = rx.load_manifest(A1_MANIFEST, "A1")
    try:
        ev.verify_gate(rec, term_rows, pred_rows, paras)
    except (ev.EvalError, ValueError, OSError) as exc:
        out["problems"].append(f"gate/journal verification failed: {exc}")
        return out                                           # evidence does not verify: not assessed, nothing scored
    try:
        recs = read_journal(calls_path)                      # the hash-verified journal
    except (ValueError, OSError) as exc:
        out["problems"].append(f"the calls journal cannot be read: {exc}")
        return out
    want_ids = {(p["paragraph_id"], k_) for p in paras for k_ in range(1, k + 1)}
    got_ids = [(t["paragraph_id"], int(t["run_index"])) for t in term_rows]
    coverage_ok = len(got_ids) == len(set(got_ids)) == len(want_ids) and set(got_ids) == want_ids
    if not coverage_ok:
        out["problems"].append(f"terminal coverage is not exactly the {len(want_ids)} scheduled executions "
                               f"({len(got_ids)} rows, {len(set(got_ids))} distinct, {len(set(got_ids) - want_ids)} foreign)")
    outcomes: dict = {}
    for t in term_rows:
        outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
    out["terminal_outcomes"] = outcomes
    infra = outcomes.get(rx.T_ERROR, 0)
    if infra:
        out["problems"].append(f"{infra} unresolved infrastructure terminal(s): the candidate is not assessed")
        return out
    if not coverage_ok:
        return out                                           # incomplete coverage: not assessed
    bad = sum(n for o, n in outcomes.items() if o not in SUCCESS)
    if bad:
        out["problems"].append(f"{bad} terminal(s) not successful: {outcomes}")
    # the development metrics and their provenance (run record, evaluator, reference, settings)
    metrics_path = RUNS / "tables" / "dev" / run_id / "metrics.json"
    S = None
    if not metrics_path.exists():
        out["problems"].append("no development metrics (evaluate.py --dev not run): the candidate is not assessed")
        return out
    else:
        m = json.loads(metrics_path.read_text(encoding="utf-8"))
        inp = m.get("inputs") or {}
        if m.get("run_id") != run_id or not m.get("dev"):
            raise SystemExit(f"metrics under {metrics_path} belong to another run or are not development metrics")
        if inp.get("run_record_sha256") != sha(rec_path):
            raise SystemExit(f"the development metrics of {run_id} were computed from another run record")
        if inp.get("evaluate_sha256") != seal["code"]["evaluate.py"]:
            raise SystemExit(f"the development metrics of {run_id} were computed by another evaluator than the sealed one")
        ref = inp.get("reference") or {}
        for f in REFERENCE_FILES:
            if ref.get(f) != seal["reference"][f]:
                raise SystemExit(f"the development metrics of {run_id} were scored against another {f} than the sealed one")
        ev_want = seal["evaluation"]
        got_ev = {"threshold": m.get("threshold"), "B": m.get("B"), "seed": m.get("seed")}
        if got_ev != ev_want or int(m.get("k", 0)) != k:
            raise SystemExit(f"the development metrics of {run_id} were computed with {got_ev}, k={m.get('k')}; sealed {ev_want}, k={k}")
        pe = (m.get("per_execution") or {}).get("pooled") or {}
        try:
            fs = [f1_fraction(pe[str(k_)]["element_counts_unweighted"]) for k_ in range(1, k + 1)]
            S = sum(fs, Fraction(0)) / k if all(f is not None for f in fs) else None
            if S is None:
                out["problems"].append("undefined execution score (nothing predicted and nothing in the reference)")
        except (KeyError, TypeError, ValueError):
            out["problems"].append("per-execution element counts incomplete: the candidate is not assessed")
            return out
    out["S"], out["S_float"] = (str(S) if S is not None else None), (float(S) if S is not None else None)
    # first returned model response per scheduled execution; retries and their kinds (from the verified journal)
    first: dict = {}
    for r in sorted((r for r in recs if r.get("type") == "attempt_finished"),
                    key=lambda r: (r["paragraph_id"], int(r["run_index"]), int(r.get("attempt_no", 0)))):
        key = (r["paragraph_id"], int(r["run_index"]))
        if key not in first and r.get("code") not in TRANSPORT:
            first[key] = r["code"]
    out["I"] = sum(1 for key in want_ids if first.get(key) in PARSE_FAIL_CODES)
    out["first_returned_missing"] = sum(1 for key in want_ids if key not in first)
    out["retries"] = sum(1 for r in recs if r.get("type") == "attempt_started" and r.get("retry_kind") not in (None, "first"))
    out["feedback_retries"] = sum(1 for r in recs if r.get("type") == "attempt_started" and r.get("retry_kind") == "feedback")
    out["assessed"] = True                                   # record, files, gates, journal, coverage and metrics all verified
    out["scoreable"] = S is not None
    out["eligible"] = bad == 0 and S is not None and out["first_returned_missing"] == 0
    return out


def decide(c0: dict, c1: dict, c2: dict, margin: Fraction = MARGIN) -> dict:
    """The rule, as a pure function of the three assessments (S values are Fraction strings or None)."""
    def S(c):
        return Fraction(c["S"]) if c.get("S") is not None else None
    if not c1["eligible"]:
        return {"outcome": "not_achieved", "selected": "v1.0", "baseline_guard": "not applied",
                "reason": "V1 (v1.1S) is not eligible: " + "; ".join(c1["problems"])}
    incumbent = c1
    steps = ["incumbent = V1 (v1.1S)"]
    if c2["eligible"] and S(c2) is not None and S(incumbent) is not None:
        gain = S(c2) - S(incumbent)
        if gain >= margin and c2["I"] <= incumbent["I"]:
            incumbent = c2
            steps.append(f"V2 replaces V1: V2 eligible, gain {float(gain):+.4f} ({gain}) >= {margin} and I {c2['I']} <= {c1['I']}")
        else:
            steps.append(f"V2 does not replace V1: gain {float(gain):+.4f} ({gain}), I {c2['I']} vs {c1['I']}")
    else:
        steps.append("V2 not eligible or unscored: " + "; ".join(c2["problems"]))
    v0_scoreable = bool(c0.get("scoreable")) and S(c0) is not None
    if v0_scoreable and S(incumbent) is not None and S(incumbent) < S(c0):
        return {"outcome": "no_upgrade", "selected": "v1.0", "baseline_guard": "applied",
                "reason": f"selected {incumbent['instrument']} scores {float(S(incumbent)):.4f} below the contemporaneous v1.0 {float(S(c0)):.4f}",
                "steps": steps}
    guard = "applied" if v0_scoreable else "not applied: baseline not assessed (" + "; ".join(c0.get("problems") or ["no score"]) + ")"
    return {"outcome": "selected", "selected": incumbent["instrument"], "baseline_guard": guard,
            "reason": "; ".join(steps), "steps": steps}


def select(v0: str | None = None, v1: str | None = None, v2: str | None = None) -> dict:
    seal = load_seal()
    ids = {"v0": v0 or seal["run_ids"]["v0"], "v1": v1 or seal["run_ids"]["v1"], "v2": v2 or seal["run_ids"]["v2"]}
    c = {slot: assess(ids[slot], slot, seal) for slot in ("v0", "v1", "v2")}
    d = decide(c["v0"], c["v1"], c["v2"], Fraction(seal["selector"]["margin"]))
    out = {"kind": "w3_selection", "selected_at": datetime.now(timezone.utc).isoformat(), "seal_digest": seal["digest"],
           "candidates": c, "decision": d}
    if SELECTION.exists():
        raise SystemExit(f"{SELECTION.name} exists; the selection is recorded once.")
    SELECTION.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seal", action="store_true")
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--v0"); ap.add_argument("--v1"); ap.add_argument("--v2")
    a = ap.parse_args()
    if a.seal:
        if not (a.v0 and a.v1 and a.v2):
            sys.exit("--seal needs --v0 --v1 --v2 (the three permitted run identifiers)")
        p = write_seal({"v0": a.v0, "v1": a.v1, "v2": a.v2})
        print(f"sealed: {p.relative_to(ROOT)}")
        return
    if a.select:
        out = select(a.v0, a.v1, a.v2)
        for key, c in out["candidates"].items():
            print(f"{key} {c['instrument']:6s} {c['run_id']}: assessed={c['assessed']} eligible={c['eligible']} scoreable={c['scoreable']} "
                  f"S={c['S_float']} I={c['I']} retries={c['retries']} (feedback {c['feedback_retries']}) outcomes={c['terminal_outcomes']} "
                  f"{'; '.join(c['problems'])}")
        print(f"decision: {out['decision']['outcome']} -> {out['decision']['selected']} ({out['decision']['reason']}; "
              f"baseline guard {out['decision']['baseline_guard']})")
        print(f"  record  {SELECTION.relative_to(ROOT)}")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
