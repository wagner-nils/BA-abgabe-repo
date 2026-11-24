"""W1 -- the mechanical selection of the production model (pre-analysis specification §4, W1).

Two commands, no judgement inside either:

  --write-seal --fork W1-a|W1-b --author-confirmation TEXT
      Writes tools/runs/w1_seal.json ONCE: the fork, the author's confirmation, the timestamp,
      the candidate roster, the A1 manifest digest and the reference-file digests, the hashes of the
      code files and of the two governing documents, and the common execution contract (the
      runner's fingerprint payload without the model) every candidate run must show. Nothing that
      the selection later depends on is left to be recomputed from memory.

  --seal SEAL --candidate KEY=RUN_RECORD:METRICS | KEY=gate:PREFLIGHT ... [--out]
      Reads, per candidate, either the development run record plus the metrics.json of its --dev
      evaluation, or -- for a candidate that never ran because its gate failed -- the gate
      artefact alone. Verifies every input against the seal and against the files it names,
      classifies each candidate, applies the rule, and writes ONE decision file with the trace.

Codex W1 rounds 2-3: "the revised W1 contract remains largely prose", "the selector cannot consume
real runner fingerprints", "scores and eligibility are not bound to verified artifacts", "the sealed
contract is still not enforced". This module is the code those findings asked for.

Contract, refused and never repaired:
  * the seal exists and its code hashes are the hashes on disk NOW (the selector runs on the code
    it was sealed with); the roster is exactly the sealed candidates, no key twice, none missing;
  * every run record: dev, k = 3, `--restrict-split A1`, the sealed A1 manifest digest and paragraph
    count, an `execution_contract` equal to the sealed common contract plus its own model_id,
    started after the seal, started and ended inside one 48-hour window with the other runs;
  * every file the record names has the recorded digest; the terminal rows cover exactly every
    (paragraph, execution) once; the metrics belong to the run, were computed from these very
    files (their `inputs` block names the same digests) and from the sealed reference files;
  * S is recomputed exactly (fractions) from the per-execution integer counts.

Classification, from the records alone:
  inaccessible  -- the gate's reachability check failed (no model answered)
  incompatible  -- the pinned request was rejected with a validation error on a production call
  gate_failed   -- reachable and accepted, but the output or caching checks failed
  ineligible    -- a terminal `parse_failed` or `refused` on any execution (model-output failure);
                   this ends the assessment even if other executions still have recovery episodes
                   left (pre-registered in the specification: a disqualified candidate is not
                   recovered further)
  not_assessed  -- a terminal `error` after the recovery episodes were exhausted (infrastructure)
  recovery_pending -- a terminal `error` with episodes left: the candidate is NOT finished and the
                      selection refuses to run
  eligible      -- every execution ended in `technologies_present` or `off_topic`

Rule: S = mean over executions 1..3 of the exact execution-specific tuple micro-F1 (pooled,
unweighted). C = eligible candidates with S_max - S < 1/50. Selected: the member of C with the
lowest estimated cost per paragraph; an exact tie falls to CANDIDATE_ORDER. No eligible candidate
-> production_stop. |C| > 1 and any member's billing INCOMPLETE -> selection_stop_incomplete_cost.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

CANDIDATE_ORDER = ["sonnet", "sonnet5", "opus", "opus5"]
DELTA = Fraction(1, 50)
K = 3
SPLIT = "A1"
N_PARAGRAPHS = 32
WINDOW_HOURS = 48
MAX_RECOVERY_EPISODES = 2
OK_OUTCOMES = {"technologies_present", "off_topic"}
MODEL_OUTPUT_FAILURES = {"parse_failed", "refused"}
MODEL_FIELDS = {"model_id"}
A1_MANIFEST = ROOT / "tools/annotation/labels/A1_manifest.json"
A1_REFERENCE = [ROOT / "tools/annotation/labels/A1_pass1_v1.0.csv", ROOT / "tools/annotation/labels/A1_pass2_v1.0.csv"]
CODE_FILES = ["instrument.py", "preflight.py", "run_extract.py", "evaluate.py", "sample_contract.py", "w1_select.py"]
DOCUMENTS = ["tools/docs/pre-analysis-specification.md", "tools/docs/PIPELINE-COMPLETION-PLAN.md"]
DEFAULT_SEAL = ROOT / "tools/runs/w1_seal.json"
ACCESS_DEADLINE = "2026-09-15T23:59:59+00:00"      # specification §4 W1, "Gate schedule"
SEAL_KEYS = {"kind", "sealed_at", "fork", "author_confirmation", "candidates", "k", "split", "n_paragraphs", "delta",
             "window_hours", "max_recovery_episodes", "access_deadline", "a1_manifest_sha256", "a1_paragraph_ids",
             "reference", "code", "documents", "contract_common", "model_ids", "prices_per_million", "price_table"}
REQUIRED_FILE_SUFFIXES = ("_terminal.csv", "_predictions.csv", "_calls.jsonl")


class W1Error(RuntimeError):
    pass


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def manifest_digest(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _iso(s: str) -> datetime:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def common_contract(contract: dict) -> dict:
    return {k: v for k, v in contract.items() if k not in MODEL_FIELDS}


# ----------------------------------------------------------------------------- seal
def build_seal(fork: str, confirmation: str, *, now: str | None = None) -> dict:
    if fork not in ("W1-a", "W1-b"):
        raise W1Error("fork must be W1-a or W1-b")
    if not confirmation or len(confirmation.strip()) < 10:
        raise W1Error("the author's confirmation text is required (at least ten characters)")
    import instrument as ins            # noqa: E402  (the pipeline's own interpreter)
    import run_extract as rx            # noqa: E402
    man = json.loads(A1_MANIFEST.read_text(encoding="utf-8"))
    a1_sha = manifest_digest(man)
    n_a1 = sum(1 for p in man.get("paragraphs", []) if p.get("split") == SPLIT)
    if n_a1 != N_PARAGRAPHS:
        raise W1Error(f"the A1 manifest lists {n_a1} {SPLIT} paragraphs, the contract fixes {N_PARAGRAPHS}")
    art = ins.load_artifacts()
    contract = rx.fingerprint_payload(art, CANDIDATE_ORDER[0], K, a1_sha, ins.REGION, SPLIT, True)
    # the fork label must describe the request the builder actually sends (Codex W1 round 4, #2)
    inf = contract.get("inference_config") or {}
    thinking = ((contract.get("additional_model_request_fields") or {}).get("thinking"))
    if fork == "W1-a" and not (inf.get("temperature") == 0 and thinking is None):
        raise W1Error(f"fork W1-a needs the pinned request (temperature 0, no thinking field); the builder sends "
                      f"inferenceConfig={inf}, thinking={thinking}")
    if fork == "W1-b" and not ("temperature" not in inf and thinking == {"type": "disabled"}):
        raise W1Error(f"fork W1-b needs the common configuration (no sampling parameter, thinking explicitly disabled); "
                      f"the builder sends inferenceConfig={inf}, thinking={thinking}")
    return {
        "kind": "W1_seal", "sealed_at": now or datetime.now(timezone.utc).isoformat(),
        "fork": fork, "author_confirmation": confirmation.strip(),
        "candidates": list(CANDIDATE_ORDER), "k": K, "split": SPLIT, "n_paragraphs": N_PARAGRAPHS,
        "delta": str(DELTA), "window_hours": WINDOW_HOURS, "max_recovery_episodes": MAX_RECOVERY_EPISODES,
        "access_deadline": ACCESS_DEADLINE,
        "a1_manifest_sha256": a1_sha,
        "a1_paragraph_ids": sorted(p["paragraph_id"] for p in man.get("paragraphs", []) if p.get("split") == SPLIT),
        "reference": {p.name: sha256_file(p) for p in A1_REFERENCE},
        "code": {f: sha256_file(HERE / f) for f in CODE_FILES},
        "documents": {d: sha256_file(ROOT / d) for d in DOCUMENTS},
        "contract_common": common_contract(contract),
        "model_ids": {k: ins.MODELS[k] for k in CANDIDATE_ORDER},
        # the rates the selection costs with, frozen here: a later price change never re-selects
        "prices_per_million": {k: dict(rx.PRICES_PER_M[k]) for k in CANDIDATE_ORDER},
        "price_table": rx.PRICE_TABLE_VERSION,
    }


def load_seal(path: Path) -> dict:
    if not Path(path).exists():
        raise W1Error(f"no seal at {path}; W1 runs before sealing are not selectable")
    seal = json.loads(Path(path).read_text(encoding="utf-8"))
    if seal.get("kind") != "W1_seal":
        raise W1Error("not a W1 seal")
    missing = sorted(SEAL_KEYS - set(seal))
    if missing:
        raise W1Error(f"the seal is incomplete; missing {missing}")
    if set(seal.get("code") or {}) != set(CODE_FILES):
        raise W1Error(f"the seal must hash exactly the six code files {CODE_FILES}; it hashes {sorted(seal.get('code') or {})}")
    if list(seal.get("candidates") or []) != CANDIDATE_ORDER or set(seal.get("model_ids") or {}) != set(CANDIDATE_ORDER):
        raise W1Error("the seal's roster or model_ids are not the four sealed candidates")
    if len(seal.get("a1_paragraph_ids") or []) != N_PARAGRAPHS:
        raise W1Error(f"the seal names {len(seal.get('a1_paragraph_ids') or [])} A1 paragraphs, not {N_PARAGRAPHS}")
    changed = [f for f, h in (seal.get("code") or {}).items() if not (HERE / f).exists() or sha256_file(HERE / f) != h]
    if changed:
        raise W1Error(f"the code has changed since the seal: {changed}; the selection runs on sealed code only")
    for p in A1_REFERENCE:
        if seal["reference"].get(p.name) != sha256_file(p):
            raise W1Error(f"the reference file {p.name} differs from the sealed one")
    if A1_MANIFEST.exists() and manifest_digest(json.loads(A1_MANIFEST.read_text(encoding="utf-8"))) != seal["a1_manifest_sha256"]:
        raise W1Error("the A1 manifest differs from the sealed one")
    return seal


# ----------------------------------------------------------------------------- candidates
def _terminal_rows(rec: dict) -> tuple[list[dict], Path]:
    for name, meta in (rec.get("files") or {}).items():
        if name.endswith("_terminal.csv"):
            p = Path(meta["path"]) if Path(meta["path"]).is_absolute() else ROOT / meta["path"]
            with p.open(encoding="utf-8") as f:
                return list(csv.DictReader(f, delimiter=";")), p
    raise W1Error("the run record names no terminal.csv")


def _verify_files(key: str, rec: dict) -> None:
    for name, meta in (rec.get("files") or {}).items():
        p = Path(meta["path"]) if Path(meta["path"]).is_absolute() else ROOT / meta["path"]
        if not p.exists() or sha256_file(p) != meta.get("sha256"):
            raise W1Error(f"{key}: {name} is missing or its digest differs from the run record")


INACCESSIBLE_MARKERS = ("AccessDeniedException", "ResourceNotFoundException", "not available for this account",
                        "UnrecognizedClientException", "ExpiredTokenException")


def _gate_class(gate: dict) -> tuple[str, str]:
    """Exclusion class from the gate artefact's own evidence (Codex W1 rounds 4-5): the recorded
    exceptions in `raw_responses` decide, by error code -- an access or availability error is
    *inaccessible*, a validation rejection of the request is *incompatible* (on the reachability
    probe or a production call alike), anything else that did not certify is *gate-failed*."""
    checks = {c.get("id"): c for c in gate.get("checks", [])}
    status = str(gate.get("certification_status", ""))
    raw = gate.get("raw_responses") if isinstance(gate.get("raw_responses"), list) else []
    exc = [r for r in raw if isinstance(r, dict) and r.get("outcome") == "exception"]
    texts = [f"{r.get('error_code')} {r.get('error_class')} {r.get('error')}" for r in exc]
    if any("ValidationException" in x for x in texts):
        where = [r.get("label") for r, x in zip(exc, texts) if "ValidationException" in x]
        return "incompatible", f"the request was rejected with a validation error on {where}"
    if any(any(m in x for m in INACCESSIBLE_MARKERS) for x in texts) or \
            ((checks.get("1") or {}).get("ok") is False) or "UNREACHABLE" in status:
        return "inaccessible", "the reachability call failed with an access or availability error: no model answered"
    failed = sorted(cid for cid, c in checks.items() if c.get("ok") is False)
    return "gate_failed", f"gate not certified ({status}); failed checks {failed}"


def _gate_record(key: str, gate_artifact: Path, seal: dict) -> dict:
    """A gate artefact that stands for a candidate: it must be a real gate record for the sealed model
    under the sealed tool config, dated between the seal and the access deadline (Codex W1 round 4, #3).
    What no code can prove -- that a later certified gate was not withheld -- is covered by the reporting
    commitment: every gate attempt is listed, and the run directory retains every artefact."""
    if key not in seal["candidates"]:
        raise W1Error(f"{key}: not a sealed candidate")
    p = Path(gate_artifact)
    if not p.exists():
        raise W1Error(f"{key}: gate artefact {p} is missing")
    gate = json.loads(p.read_text(encoding="utf-8"))
    for k_ in ("checks", "certification_status", "models", "started_at", "tool_config_sha256"):
        if k_ not in gate:
            raise W1Error(f"{key}: the gate artefact lacks {k_!r}; it is not a preflight record")
    if gate.get("models") != {key: seal["model_ids"][key]}:
        raise W1Error(f"{key}: the gate artefact is for {gate.get('models')}, not for the sealed {seal['model_ids'][key]!r}")
    cc = seal["contract_common"]
    for gk, ck in (("tool_config_sha256", "tool_config_sha256"), ("system_prompt_sha256", "prompt_sha256"),
                   ("codebook_sha256", "codebook_sha256")):
        if gate.get(gk) != cc.get(ck):
            raise W1Error(f"{key}: the gate artefact was made under another instrument than the sealed one ({gk})")
    # the gate's own code provenance must be the sealed code: prompt, tool config and codebook hashes
    # cannot tell W1-a from W1-b, the request builder (instrument.py) and the gate (preflight.py) can
    prov = gate.get("provenance") or {}
    for pk, cf in (("preflight_sha256", "preflight.py"), ("instrument_sha256", "instrument.py")):
        if prov.get(pk) != seal["code"].get(cf):
            raise W1Error(f"{key}: the gate artefact was produced by a {cf} that is not the sealed one")
    when = _iso(gate["started_at"])
    if when < _iso(seal["sealed_at"]) or when > _iso(seal["access_deadline"]):
        raise W1Error(f"{key}: the gate artefact is dated {gate['started_at']}, outside seal .. access deadline")
    gate["_sha256"] = sha256_file(p); gate["_path"] = str(p)
    return gate


def load_gate_only(key: str, gate_artifact: Path, seal: dict) -> dict:
    """A candidate that never extracted because its gate failed: the gate artefact is the record."""
    gate = _gate_record(key, gate_artifact, seal)
    if gate.get("certified"):
        raise W1Error(f"{key}: the gate certified, so a run record (or an `unrun:` record) is required, not a gate-only record")
    status, reason = _gate_class(gate)
    return {"key": key, "model_id": seal["model_ids"][key], "run_id": None, "status": status, "reason": reason,
            "S": None, "S_exact": None, "per_execution_element_micro_f1": None, "cost_per_paragraph_usd": None,
            "billing_complete": None, "started_at": gate.get("started_at"), "ended_at": gate.get("ended_at"),
            "gate_artifact": gate["_path"], "gate_artifact_sha256": gate["_sha256"], "gate_status": gate.get("certification_status")}


def load_unrun(key: str, gate_artifact: Path, seal: dict) -> dict:
    """A candidate whose gate certified but that was not run inside the window: *not assessed*."""
    gate = _gate_record(key, gate_artifact, seal)
    if not gate.get("certified"):
        raise W1Error(f"{key}: an `unrun:` record needs a CERTIFIED gate; this one is {gate.get('certification_status')}")
    return {"key": key, "model_id": seal["model_ids"][key], "run_id": None, "status": "not_assessed",
            "reason": "gate certified but no scored run inside the 48-hour window", "S": None, "S_exact": None,
            "per_execution_element_micro_f1": None, "cost_per_paragraph_usd": None, "billing_complete": None,
            "started_at": gate.get("started_at"), "ended_at": gate.get("ended_at"),
            "gate_artifact": gate["_path"], "gate_artifact_sha256": gate["_sha256"], "gate_status": gate.get("certification_status")}


def load_candidate(key: str, run_record: Path, metrics: Path, seal: dict) -> dict:
    rec = json.loads(Path(run_record).read_text(encoding="utf-8"))
    met = json.loads(Path(metrics).read_text(encoding="utf-8"))
    if key not in seal["candidates"]:
        raise W1Error(f"{key}: not a sealed candidate {seal['candidates']}")
    if rec.get("model_key") != key:
        raise W1Error(f"{key}: the run record is for model_key {rec.get('model_key')!r}")
    if not rec.get("dev"):
        raise W1Error(f"{key}: W1 runs are development runs; this record is not marked dev")
    if int(rec.get("repeats", 0)) != K:
        raise W1Error(f"{key}: k = {rec.get('repeats')}, the contract fixes k = {K}")
    m = rec.get("manifest") or {}
    if m.get("restrict_split") != SPLIT:
        raise W1Error(f"{key}: restrict_split is {m.get('restrict_split')!r}, not {SPLIT!r}")
    if m.get("sha256") != seal["a1_manifest_sha256"]:
        raise W1Error(f"{key}: the run's manifest is not the sealed A1 manifest")
    if int(m.get("n_paragraphs", 0)) != N_PARAGRAPHS:
        raise W1Error(f"{key}: {m.get('n_paragraphs')} paragraphs, the contract fixes {N_PARAGRAPHS}")
    contract = rec.get("execution_contract")
    if not isinstance(contract, dict):
        raise W1Error(f"{key}: the run record carries no execution_contract document")
    if common_contract(contract) != seal["contract_common"]:
        diff = sorted(k_ for k_ in set(common_contract(contract)) | set(seal["contract_common"])
                      if common_contract(contract).get(k_) != seal["contract_common"].get(k_))
        raise W1Error(f"{key}: the run's execution contract differs from the sealed one in {diff}")
    if contract.get("model_id") != seal["model_ids"].get(key):
        raise W1Error(f"{key}: the run's model_id {contract.get('model_id')!r} is not the sealed {seal['model_ids'].get(key)!r}")
    if not rec.get("started_at") or not rec.get("ended_at"):
        raise W1Error(f"{key}: the run record lacks started_at/ended_at")
    if _iso(rec["started_at"]) < _iso(seal["sealed_at"]):
        raise W1Error(f"{key}: the run started before the seal")
    if _iso(rec["ended_at"]) < _iso(rec["started_at"]):
        raise W1Error(f"{key}: the run record ends before it starts")
    files = rec.get("files") or {}
    for suf in REQUIRED_FILE_SUFFIXES:
        if not any(n.endswith(suf) for n in files):
            raise W1Error(f"{key}: the run record names no *{suf}")
    _verify_files(key, rec)
    # every gate the run journalled (Codex W1 round 7): ONE validator for each -- existence, digest,
    # sealed model, sealed instrument and code, dated within [seal, deadline] and not after the run
    # ended; the first gate must precede the first extraction call. Labels are bound to the gate
    # their resolving attempt ran under, so a later certified gate never qualifies earlier labels.
    gate = rec.get("preflight") or {}
    gates = gate.get("gates")
    if not isinstance(gates, list) or not gates:
        raise W1Error(f"{key}: the run record lists no journalled gates")
    if not rec.get("first_call_at"):
        raise W1Error(f"{key}: the run record carries no first_call_at")
    gdocs = []
    for i, g in enumerate(gates):
        gp = Path(g.get("artefact") or "")
        gp = gp if gp.is_absolute() else ROOT / gp
        if not g.get("artefact") or not gp.exists() or sha256_file(gp) != g.get("sha256"):
            raise W1Error(f"{key}: journalled gate {i + 1} is missing or its digest differs from the run record")
        d = _gate_record(key, gp, seal)
        if bool(d.get("certified")) != bool(g.get("certified")):
            raise W1Error(f"{key}: the run record's certification claim for gate {i + 1} differs from the artefact")
        if _iso(d["started_at"]) > _iso(rec["ended_at"]):
            raise W1Error(f"{key}: journalled gate {i + 1} is dated after the run ended")
        gdocs.append((g, d))
    if _iso(gdocs[0][1]["started_at"]) > _iso(rec["first_call_at"]):
        raise W1Error(f"{key}: the first gate is dated after the first extraction call it should have guarded")
    if bool(gate.get("all_certified")) != all(bool(g.get("certified")) for g, _ in gdocs):
        raise W1Error(f"{key}: the run record's all_certified flag does not match its gates")
    certified_shas = {g["sha256"] for g, _ in gdocs if g.get("certified")}
    # the summary and the standing terminals must be what the hash-verified journal says (Codex W1
    # round 8, #1): gate sequence, each terminal's outcome / resolving attempt / gate, and the gate
    # that resolving attempt actually ran under
    rows_all, _tp = _terminal_rows(rec)
    calls_name = next((n for n in files if n.endswith("_calls.jsonl")), None)
    cp = Path(files[calls_name]["path"]) if Path(files[calls_name]["path"]).is_absolute() else ROOT / files[calls_name]["path"]
    pred_name = next((n for n in files if n.endswith("_predictions.csv")), None)
    pp = Path(files[pred_name]["path"]) if Path(files[pred_name]["path"]).is_absolute() else ROOT / files[pred_name]["path"]
    with pp.open(encoding="utf-8") as f:
        pred_rows = list(csv.DictReader(f, delimiter=";"))
    a1_doc = json.loads(A1_MANIFEST.read_text(encoding="utf-8"))
    a1_paras = [q for q in a1_doc.get("paragraphs", []) if q.get("split") == SPLIT]
    try:
        import run_extract as rx
        journal = rx.verify_journal_binding(rec, rows_all, cp, pred_rows=pred_rows, rates=seal["prices_per_million"][key], paras=a1_paras)
    except ValueError as exc:
        raise W1Error(f"{key}: journal reconciliation: {exc}") from None
    # S is RECOMPUTED here from the journal-verified predictions against the sealed reference, with
    # the evaluator's own machinery; the metrics file must agree, it is not the source (Codex W1
    # round 10, #1)
    recomputed = recompute_counts(key, Path(run_record), seal)
    if met.get("run_id") != rec.get("run_id"):
        raise W1Error(f"{key}: metrics belong to run {met.get('run_id')!r}, the record is {rec.get('run_id')!r}")
    if int(met.get("k", 0)) != K:
        raise W1Error(f"{key}: metrics computed with k = {met.get('k')}")
    inputs = met.get("inputs") or {}
    if inputs.get("run_record_files") != rec.get("files"):
        raise W1Error(f"{key}: the metrics were not computed from this run's files (digests differ or are missing)")
    if inputs.get("run_record_sha256") != sha256_file(Path(run_record)):
        raise W1Error(f"{key}: the run record changed after the evaluation (its digest is not the one the metrics name)")
    if inputs.get("evaluate_sha256") != seal["code"].get("evaluate.py"):
        raise W1Error(f"{key}: the metrics were computed by an evaluate.py that is not the sealed one")
    if inputs.get("reference") != seal["reference"]:
        raise W1Error(f"{key}: the metrics were not computed against the sealed reference files")
    # terminal rows: exactly one per (paragraph, execution)
    rows, _ = _terminal_rows(rec)
    pids = sorted({r["paragraph_id"] for r in rows})
    if pids != sorted(seal["a1_paragraph_ids"]):
        raise W1Error(f"{key}: terminal rows do not name exactly the sealed A1 paragraphs")
    seen = [(r["paragraph_id"], int(r["run_index"])) for r in rows]
    want = {(p, r) for p in pids for r in range(1, K + 1)}
    if len(seen) != len(set(seen)) or set(seen) != want:
        raise W1Error(f"{key}: terminal rows do not cover every (paragraph, execution) exactly once")
    outcomes = [r["outcome"] for r in rows]
    episodes = [int(r.get("episode") or 0) for r in rows]
    loose = [r for r in rows if r["outcome"] in OK_OUTCOMES and r.get("gate") not in certified_shas]
    uncertified = [d for g, d in gdocs if not g.get("certified")]
    if uncertified or loose:
        # the exclusion class comes from the first gate that did not certify; a label produced
        # under it, or under no journalled gate at all, is not a certified label
        status, reason = _gate_class(uncertified[0] if uncertified else gdocs[0][1])
        if loose and status == "gate_failed":
            reason = f"{len(loose)} label(s) were produced under a gate that did not certify (a later certified gate does not qualify them); " + reason
    elif any(o in MODEL_OUTPUT_FAILURES for o in outcomes):
        bad = sorted({o for o in outcomes if o in MODEL_OUTPUT_FAILURES})
        status, reason = "ineligible", f"model-output failure terminal(s) {bad} on {sum(o in MODEL_OUTPUT_FAILURES for o in outcomes)} execution(s)"
    elif any(o == "error" for o in outcomes):
        pending = [e for o, e in zip(outcomes, episodes) if o == "error" and e < MAX_RECOVERY_EPISODES]
        if pending:
            status, reason = "recovery_pending", f"{len(pending)} error terminal(s) still have recovery episodes left; the candidate is not finished"
        else:
            status, reason = "not_assessed", f"{sum(o == 'error' for o in outcomes)} execution(s) ended in an infrastructure terminal after {MAX_RECOVERY_EPISODES} recovery episodes"
    elif all(o in OK_OUTCOMES for o in outcomes):
        status, reason = "eligible", "gate certified; all executions ended in an ok terminal"
    else:
        raise W1Error(f"{key}: unknown terminal outcome(s) {sorted(set(outcomes) - OK_OUTCOMES)}")
    # S, exactly, from the integer counts
    pe = (met.get("per_execution") or {}).get("pooled") or {}
    exact = {}
    for r in range(1, K + 1):
        e = pe.get(str(r))
        c = (e or {}).get("element_counts_unweighted")
        if not c or any(not isinstance(c.get(x), int) or isinstance(c.get(x), bool) or c.get(x) < 0 for x in ("tp", "fp", "fn")):
            raise W1Error(f"{key}: no non-negative integer element counts for execution {r} in the metrics")
        if {x: c[x] for x in ("tp", "fp", "fn")} != recomputed[r]:
            raise W1Error(f"{key}: the metrics' element counts for execution {r} ({c}) are not what the journal-verified "
                          f"predictions give against the sealed reference ({recomputed[r]})")
        den = 2 * c["tp"] + c["fp"] + c["fn"]
        if den == 0:
            raise W1Error(f"{key}: execution {r} has an empty tuple universe; S is undefined")
        exact[r] = Fraction(2 * c["tp"], den)
        f1 = e.get("element_micro_f1_unweighted")
        if f1 is None or abs(float(exact[r]) - float(f1)) > 1e-9:
            raise W1Error(f"{key}: the metrics' F1 for execution {r} ({f1}) does not match its own counts ({float(exact[r])})")
    S_exact = sum(exact.values(), Fraction(0)) / K
    cost = rec.get("cost") or {}
    n_par = int(m["n_paragraphs"])
    # the selection costs with the JOURNAL's recomputed estimate at the sealed rates, never the record's
    est = journal["estimated_usd"]
    return {
        "key": key, "model_id": contract.get("model_id"), "run_id": rec.get("run_id"),
        "status": status, "reason": reason,
        "S": float(S_exact), "S_exact": str(S_exact),
        "per_execution_element_micro_f1": {str(r): float(v) for r, v in exact.items()},
        "cost_per_paragraph_usd": float(est) / n_par,
        "billing_complete": bool(journal["billing_complete"]), "price_table": seal.get("price_table"),
        "verdict_codes": (rec.get("totals") or {}).get("verdict_codes"), "calls": (rec.get("totals") or {}).get("calls"),
        "started_at": rec.get("started_at"), "ended_at": rec.get("ended_at"),
        "gate_status": gate.get("certification_status"),
        "gates": [{"artefact": g.get("artefact"), "sha256": g.get("sha256"), "certified": bool(g.get("certified"))} for g, _ in gdocs],
        "_S_exact": S_exact,
    }


def recompute_counts(key: str, run_record: Path, seal: dict) -> dict[int, dict]:
    """Per-execution element TP/FP/FN, pooled and unweighted, computed by the evaluator's own code from
    the run's (journal-verified) prediction and terminal files against the sealed reference and the
    sealed A1 manifest. The reference provenance (sessions.csv, session manifest text hashes) is
    verified as in a reportable evaluation; the development flag only admits the A1 split filter."""
    import argparse
    import evaluate as ev
    ns = argparse.Namespace(run_record=str(run_record), manifest=str(A1_MANIFEST), restrict_split=SPLIT,
                            gold_pass1=str(A1_REFERENCE[0]), gold_pass2=str(A1_REFERENCE[1]), dev=True,
                            gold_manifest=None, skip_provenance=False)
    try:
        inp = ev.load_inputs(ns)
    except ev.EvalError as exc:
        raise W1Error(f"{key}: the evaluator refuses the run against the sealed reference: {exc}") from None
    S = ev.build(inp)
    out = {}
    for r in range(1, K + 1):
        C = ev.counts_execution(S, S["pids"], r)["C"]["element"]
        out[r] = {"tp": int(sum(v[3] for v in C.values())), "fp": int(sum(v[4] for v in C.values())),
                  "fn": int(sum(v[5] for v in C.values()))}
    return out


# ----------------------------------------------------------------------------- rule
def select(cands: list[dict], seal: dict, now: str | None = None) -> dict:
    keys = [c["key"] for c in cands]
    # an exclusion (gate-only or unrun record) is final only once the access deadline has passed;
    # before that, a candidate could still certify and run (Codex W1 round 5, #3)
    # by DISPOSITION, whatever record type carried the evidence (Codex W1 round 6, #3): an
    # inaccessible, incompatible, gate-failed or unrun candidate could still certify and run before
    # the deadline, so no selection that carries such a disposition happens before it
    excluded = [c["key"] for c in cands if c["status"] in ("inaccessible", "incompatible", "gate_failed")
                or (c["status"] == "not_assessed" and not c.get("run_id"))]
    if excluded:
        at = _iso(now) if now else datetime.now(timezone.utc)
        if at < _iso(seal["access_deadline"]):
            raise W1Error(f"{excluded} are excluded (inaccessible / incompatible / gate-failed / unrun), but the access "
                          f"deadline {seal['access_deadline']} has not passed; exclusions are not final before it")
    if len(keys) != len(set(keys)):
        raise W1Error(f"a candidate is supplied twice: {sorted(k for k in set(keys) if keys.count(k) > 1)}")
    if set(keys) != set(seal["candidates"]):
        raise W1Error(f"the roster is not the sealed one: supplied {sorted(keys)}, sealed {sorted(seal['candidates'])}")
    pending = [c["key"] for c in cands if c["status"] == "recovery_pending"]
    if pending:
        raise W1Error(f"{pending} still have recovery episodes left; finish them (or exhaust them) before selecting")
    ran = [c for c in cands if c.get("run_id")]
    if ran:
        starts = [_iso(c["started_at"]) for c in ran]; ends = [_iso(c["ended_at"]) for c in ran]
        if (max(ends) - min(starts)).total_seconds() > WINDOW_HOURS * 3600:
            raise W1Error(f"the scored runs span more than {WINDOW_HOURS} hours from first start to last end")
    trace = [f"{c['key']}: {c['status']} -- {c['reason']}; S={c.get('S_exact')}; cost/paragraph={c['cost_per_paragraph_usd']}; billing_complete={c['billing_complete']}"
             for c in sorted(cands, key=lambda c: CANDIDATE_ORDER.index(c["key"]))]
    decision = {"rule": {"statistic": "mean over executions 1..3 of the exact execution-specific tuple micro-F1 (pooled, unweighted)",
                         "delta": str(DELTA), "tie_break": "lowest estimated cost per paragraph, then CANDIDATE_ORDER",
                         "candidate_order": CANDIDATE_ORDER, "k": K, "split": SPLIT, "window_hours": WINDOW_HOURS,
                         "fork": seal.get("fork"), "sealed_at": seal.get("sealed_at")}}
    eligible = [c for c in cands if c["status"] == "eligible"]
    if not eligible:
        decision.update({"status": "production_stop", "model_key": None,
                         "reason": "no eligible candidate; no A2 run until the author amends W1 by a dated entry"})
        trace.append("no eligible candidate -> production_stop")
        return {"decision": decision, "trace": trace}
    S_max = max(c["_S_exact"] for c in eligible)
    comp = [c for c in eligible if S_max - c["_S_exact"] < DELTA]
    trace.append(f"S_max={S_max}; competitive set (S_max - S < {DELTA}): {[c['key'] for c in comp]}")
    if len(comp) > 1 and any(not c["billing_complete"] for c in comp):
        incomplete = [c["key"] for c in comp if not c["billing_complete"]]
        decision.update({"status": "selection_stop_incomplete_cost", "model_key": None, "S_max": str(S_max),
                         "competitive_set": [c["key"] for c in comp],
                         "reason": f"the competitive set has {len(comp)} members and the cost of {incomplete} is incomplete; partial totals are not compared"})
        trace.append("incomplete cost inside a competitive set of more than one -> selection_stop_incomplete_cost")
        return {"decision": decision, "trace": trace}
    if len(comp) == 1:
        chosen, how = comp[0], "the only member of the competitive set"
    else:
        if any(c["cost_per_paragraph_usd"] is None for c in comp):
            raise W1Error("a competitive candidate has no cost")
        min_cost = min(c["cost_per_paragraph_usd"] for c in comp)
        cheapest = [c for c in comp if c["cost_per_paragraph_usd"] == min_cost]
        chosen = sorted(cheapest, key=lambda c: CANDIDATE_ORDER.index(c["key"]))[0]
        how = (f"lowest cost per paragraph ({min_cost}) among the competitive set"
               + (f"; exact tie among {[c['key'] for c in cheapest]} broken by the fixed order" if len(cheapest) > 1 else ""))
    decision.update({"status": "selected", "model_key": chosen["key"], "model_id": chosen["model_id"],
                     "S_max": str(S_max), "competitive_set": [c["key"] for c in comp], "reason": how})
    trace.append(f"selected {chosen['key']} ({chosen['model_id']}): {how}")
    return {"decision": decision, "trace": trace}


# ----------------------------------------------------------------------------- cli
def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write-seal", action="store_true")
    ap.add_argument("--fork", choices=["W1-a", "W1-b"])
    ap.add_argument("--author-confirmation", default=None)
    ap.add_argument("--seal", default=str(DEFAULT_SEAL))
    ap.add_argument("--candidate", action="append", default=[], metavar="KEY=RUN_RECORD:METRICS | KEY=gate:PREFLIGHT | KEY=unrun:PREFLIGHT")
    ap.add_argument("--out", default=str(ROOT / "tools/runs/w1_selection.json"))
    a = ap.parse_args(argv)
    if a.write_seal:
        out = Path(a.seal)
        if out.exists():
            raise SystemExit(f"{out} exists; a seal is written once.")
        seal = build_seal(a.fork or "", a.author_confirmation or "")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(seal, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"sealed W1 ({seal['fork']}) at {seal['sealed_at']} -> {out}")
        return seal
    seal = load_seal(Path(a.seal))
    cands = []
    for spec in a.candidate:
        key, _, rest = spec.partition("=")
        if rest.startswith("gate:"):
            cands.append(load_gate_only(key, Path(rest[len("gate:"):]), seal))
            continue
        if rest.startswith("unrun:"):
            cands.append(load_unrun(key, Path(rest[len("unrun:"):]), seal))
            continue
        rr, _, mm = rest.partition(":")
        if not (key and rr and mm):
            raise SystemExit(f"--candidate expects KEY=RUN_RECORD:METRICS or KEY=gate:PREFLIGHT, got {spec!r}")
        cands.append(load_candidate(key, Path(rr), Path(mm), seal))
    out = Path(a.out)
    if out.exists():
        raise SystemExit(f"{out} exists; a W1 decision is written once. Choose another --out to re-derive it.")
    result = select(cands, seal)
    public = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands]
    doc = {"kind": "W1_selection", "seal": {k: seal[k] for k in ("sealed_at", "fork", "candidates", "a1_manifest_sha256")},
           "candidates": public, **result}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n".join(result["trace"]))
    print(f"decision: {result['decision']['status']} {result['decision'].get('model_key') or ''}  -> {out}")
    return doc


if __name__ == "__main__":
    try:
        main()
    except W1Error as exc:
        sys.exit(f"REFUSING: {exc}")
