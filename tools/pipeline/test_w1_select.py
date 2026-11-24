"""Fake-record tests for w1_select.py: the seal, the contract, the classification, the rule.
Run: .venv/bin/python3 tools/pipeline/test_w1_select.py"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import w1_select as w1  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"\n          {detail}"))


def refuses(name, fn, needle):
    """`needle` may be a string or a tuple of acceptable strings (the journal reconciliation now catches
    several manipulations before the check that used to name them)."""
    needles = (needle,) if isinstance(needle, str) else tuple(needle)
    try:
        fn(); check(name, False, "no error")
    except w1.W1Error as exc:
        check(name, any(n in str(exc) for n in needles), str(exc))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


ELEMENTS = ["F1a", "F1b", "F2a", "F2b", "F2c", "F3a", "F3b", "F4a", "F4b", "F5a", "F5b", "F6a", "F6b", "F7a", "F7b", "F8a", "F9"]
REF_POS = 70                        # positive (paragraph, wind, element) tuples in the sealed reference
TEXT = "Der Ausbau der Windkraft an Land stockt, weil Genehmigungen Jahre dauern und Netze fehlen."
TEXT_H = hashlib.sha256(TEXT.encode()).hexdigest()[:16]


def reference_tuples():
    """The fixed sealed reference: paragraphs P00..P22 carry wind with F1a, F1b, F2a (69 tuples), P23 carries
    wind with F1a (70). Every other paragraph is off topic."""
    tuples = []
    for i in range(23):
        for el in ("F1a", "F1b", "F2a"):
            tuples.append((f"P{i:02d}", el))
    tuples.append(("P23", "F1a"))
    assert len(tuples) == REF_POS
    return tuples


def fake_a1(tmp: Path) -> Path:
    """A 35-row session manifest with 32 A1 paragraphs (like the real A1_manifest.json), plus the sealed
    reference files and their finalization record beside them."""
    import test_evaluate as TE
    paras = [{"paragraph_id": f"P{i:02d}", "split": "A1" if i < 32 else "A0b", "article_id": f"A{i % 3}", "lang": "de",
              "text_sha256": TEXT_H} for i in range(35)]
    man = {"session": "A1", "paragraphs": paras}
    p = tmp / "A1_manifest.json"; p.write_text(json.dumps(man))
    ref = reference_tuples(); by_pid = {}
    for pid, el in ref:
        by_pid.setdefault(pid, []).append(el)
    g1 = tmp / "A1_pass1_v1.0.csv"; g2 = tmp / "A1_pass2_v1.0.csv"
    rows1 = [TE.p1row(q["paragraph_id"], ["wind"] if q["paragraph_id"] in by_pid else [], q["article_id"]) for q in paras]
    for r_ in rows1:
        r_["text_sha256"] = TEXT_H                 # the reference rows carry the manifest's text hash
    TE.write_csv(g1, rows1, TE.P1_COLS)
    TE.write_csv(g2, [TE.p2row(pid, "wind", elements=tuple(els), evals={"costs": "negative"}, target="none", subtype="onshore")
                      for pid, els in sorted(by_pid.items())], TE.P2_COLS)
    (tmp / "sessions.csv").write_text("labels_file;labels_sha256\n" + f"{g1.name};{sha(g1)[:16]}\n{g2.name};{sha(g2)[:16]}\n", encoding="utf-8")
    w1.A1_MANIFEST = p; w1.A1_REFERENCE = [g1, g2]
    return p


def realize(counts, ref):
    """Predictions per execution that give exactly (tp, fp, fn) against the sealed reference: tp of the
    reference tuples are predicted, fp foreign tuples (elements absent from the reference for that
    paragraph) are added, and tp + fn must equal the reference size."""
    per_exec = {}
    ref_set = set(ref)
    foreign = [(pid, el) for i in range(24) for el in ELEMENTS if (pid := f"P{i:02d}", el) not in ref_set]
    for r, (tp_, fp_, fn_) in enumerate(counts, 1):
        assert tp_ + fn_ == REF_POS, f"execution {r}: tp + fn must be {REF_POS}, got {tp_ + fn_}"
        assert fp_ <= len(foreign)
        chosen = list(ref[:tp_]) + foreign[:fp_]
        by_pid = {}
        for pid, el in chosen:
            by_pid.setdefault(pid, set()).add(el)
        per_exec[r] = by_pid
    return per_exec


def make(tmp: Path, key: str, seal: dict, counts: list[tuple[int, int, int]], cost=1.0, billing=True, outcomes=None,
         episodes=None, certified=True, unreachable=False, started="2026-09-10T10:00:00+00:00", ended="2026-09-10T11:00:00+00:00",
         n=32, k=3, split="A1", dev=True, contract_tweak=None, msha=None, model_id=None, drop_rows=0, dup_rows=0,
         stale_metrics=False, wrong_reference=False, f1_lie=None, gate_started=None, gate_prov_tweak=None, first_call=None,
         label_gate_sha=None, extra_gate=None, journal_tweak=None, counts_lie=None):
    """A producer-shaped candidate: a runner-format journal whose payloads realize `counts` against the sealed
    reference, terminal.csv and predictions.csv derived from it, a gate artefact, a run record whose accounting is
    the journal's, and a metrics file whose per-execution counts are `counts` (or `counts_lie`)."""
    import run_extract as rx
    make.n = getattr(make, "n", 0) + 1
    run_id = f"w1_{key}_{make.n}"
    contract = dict(seal["contract_common"]); contract["model_id"] = model_id or seal["model_ids"][key]
    if contract_tweak:
        contract.update(contract_tweak)
    fp = hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()
    checks = [{"id": "1", "ok": not unreachable}, {"id": "2", "ok": certified}]
    gate = {"certified": certified and not unreachable, "checks": checks, "models": {key: contract["model_id"]},
            "tool_config_sha256": seal["contract_common"]["tool_config_sha256"],
            "system_prompt_sha256": seal["contract_common"]["prompt_sha256"], "codebook_sha256": seal["contract_common"]["codebook_sha256"],
            "started_at": gate_started or started,
            "provenance": {"preflight_sha256": seal["code"]["preflight.py"], "instrument_sha256": seal["code"]["instrument.py"]},
            "certification_status": "NOT_CERTIFIED_API_UNREACHABLE" if unreachable else ("CERTIFIED" if certified else "NOT_CERTIFIED"),
            "raw_responses": [{"label": "reachability", "outcome": "exception", "error_code": "AccessDeniedException"}] if unreachable else []}
    if gate_prov_tweak:
        gate["provenance"].update(gate_prov_tweak)
    gp = tmp / f"preflight_{run_id}.json"; gp.write_text(json.dumps(gate))
    gates_list = [{"artefact": str(gp), "sha256": sha(gp), "certified": gate["certified"]}]
    if extra_gate is not None:
        gates_list = [extra_gate] + gates_list
    lab_gate = label_gate_sha or sha(gp)
    first_at = first_call or started.replace("T10:00:00", "T10:00:05")
    per_exec = realize(counts, reference_tuples())
    usage_zero = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "usage_complete": True}
    rates = seal["prices_per_million"][key]
    cost_tokens = int(round(cost * n * 1_000_000 / rates["input"]))     # the whole cost on one attempt's input tokens
    usage_ok = usage_zero
    jl = [{"type": "run_started", "run_id": run_id, "started_at": started, "fingerprint": fp, "at": started}]
    jl += [{"type": "gate", "run_id": run_id, **g, "at": started} for g in gates_list]
    for i in range(n):
        pid = f"P{i:02d}"
        for r in range(1, k + 1):
            oc = (outcomes or {}).get((i, r))
            els = per_exec[r].get(pid, set())
            if oc is None:
                oc = "technologies_present" if els else "off_topic"
            ep = (episodes or {}).get((i, r), 0)
            jl.append({"type": "attempt_started", "run_id": run_id, "paragraph_id": pid, "run_index": r, "attempt_no": 1, "gate_sha256": lab_gate, "at": first_at})
            if oc in ("technologies_present", "off_topic"):
                payload = {"outcome": oc, "relations": ([{"technology": "wind", "wind_siting": "onshore", "elements": sorted(els),
                                                          "evaluations": {"costs": "negative"}, "comparison_target": "none"}] if els else [])}
                jl.append({"type": "attempt_finished", "run_id": run_id, "paragraph_id": pid, "run_index": r, "attempt_no": 1,
                           "code": "ok_technologies_present" if els else "ok_off_topic", "usage": usage_ok, "payload": payload, "at": first_at})
                jl.append({"type": "terminal", "run_id": run_id, "paragraph_id": pid, "run_index": r, "outcome": oc, "n_attempts": 1, "codes": ["ok"],
                           "resolved_from_attempt": 1, "episode": ep, "resolved_under_gate": lab_gate, "at": first_at})
            else:
                code_ = "refusal" if oc == "refused" else ("error" if oc == "error" else "schema_invalid")
                jl.append({"type": "attempt_finished", "run_id": run_id, "paragraph_id": pid, "run_index": r, "attempt_no": 1, "code": code_,
                           "usage": None if oc == "error" else usage_ok, "error_class": "ClientError" if oc == "error" else None,
                           "error_code": "ValidationException" if oc == "error" else None, "at": first_at})
                jl.append({"type": "terminal", "run_id": run_id, "paragraph_id": pid, "run_index": r, "outcome": oc, "n_attempts": 1, "codes": [code_],
                           "resolved_from_attempt": None, "episode": ep, "resolved_under_gate": None, "at": first_at})
    for r_ in jl:                                                          # the designated cost-bearing attempt
        if r_.get("type") == "attempt_finished" and r_.get("usage"):
            r_["usage"] = {**r_["usage"], "input_tokens": cost_tokens}
            break
    if not billing:     # an uncertain attempt (a timeout) makes the billing honestly incomplete
        jl.insert(len(gates_list) + 1, {"type": "attempt_started", "run_id": run_id, "paragraph_id": "P00", "run_index": 1, "attempt_no": 0,
                                        "gate_sha256": lab_gate, "at": first_at})
        jl.insert(len(gates_list) + 2, {"type": "attempt_finished", "run_id": run_id, "paragraph_id": "P00", "run_index": 1, "attempt_no": 0,
                                        "code": "error", "usage": None, "error_class": "ReadTimeoutError", "error_code": "ReadTimeoutError", "at": first_at})
    jl.append({"type": "run_finished", "run_id": run_id, "ended_at": ended, "outcome": "completed", "at": ended})
    if journal_tweak:
        journal_tweak(jl)
    cp = tmp / f"{run_id}_calls.jsonl"; cp.write_text("".join(json.dumps(r) + "\n" for r in jl))
    a1 = json.loads(w1.A1_MANIFEST.read_text()); paras = [q for q in a1["paragraphs"] if q["split"] == "A1"]
    term_rows, pred_rows = rx.derive_rows(run_id, jl, paras)
    term_rows = term_rows[drop_rows:]; term_rows = term_rows + term_rows[:dup_rows]
    tp = tmp / f"{run_id}_terminal.csv"
    with tp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rx.TERMINAL_COLUMNS, delimiter=";"); w.writeheader(); w.writerows(term_rows)
    pp = tmp / f"{run_id}_predictions.csv"
    with pp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rx.PRED_COLUMNS, delimiter=";"); w.writeheader(); w.writerows(pred_rows)
    files = {tp.name: {"path": str(tp), "sha256": sha(tp)}, pp.name: {"path": str(pp), "sha256": sha(pp)}, cp.name: {"path": str(cp), "sha256": sha(cp)}}
    tot = rx.accounting(jl)
    est = round((tot.input_tokens * rates["input"] + tot.output_tokens * rates["output"] + tot.cache_read_tokens * rates["cache_read"]
                 + tot.cache_write_tokens * rates["cache_write_1h"]) / 1_000_000, 6)
    # the metrics carry the counts the journal's payloads REALISE against the sealed reference (an
    # outcome override removes that paragraph's predictions), unless a lie is requested
    ref_set = set(reference_tuples()); realised = []
    for r in range(1, k + 1):
        pred = {(f"P{i:02d}", el) for i in range(n) for el in per_exec[r].get(f"P{i:02d}", set())
                if (outcomes or {}).get((i, r), "ok") in ("ok", "technologies_present", "off_topic")}
        realised.append((len(pred & ref_set), len(pred - ref_set), len(ref_set - pred)))
    rec = {"run_id": run_id, "model_key": key, "model_id": contract["model_id"], "dev": dev, "repeats": k,
           "started_at": started, "ended_at": ended, "first_call_at": first_at,
           "manifest": {"sha256": msha or seal["a1_manifest_sha256"], "restrict_split": split, "n_paragraphs": n},
           "execution_contract": contract, "execution_fingerprint": fp,
           "preflight": {"certified": gate["certified"], "checks": checks, "artefact": str(gp), "sha256": sha(gp),
                         "gates": gates_list, "all_certified": all(g["certified"] for g in gates_list),
                         "certification_status": gate["certification_status"]},
           "cost": {"estimated_usd": est, "billing_complete": rx.billing_is_complete(tot), "price_table": seal["price_table"]},
           "totals": {f_: getattr(tot, f_) for f_ in ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                                                     "billing_uncertain_attempts", "usage_incomplete_attempts", "unknown_completion_attempts", "cache_writes_other_ttl")},
           "files": files}
    rp = tmp / f"{run_id}.json"; rp.write_text(json.dumps(rec))
    pe = {}
    for r in range(1, k + 1):
        tp_, fp_, fn_ = (counts_lie or realised)[r - 1]
        f1 = (2 * tp_) / (2 * tp_ + fp_ + fn_)
        pe[str(r)] = {"element_counts_unweighted": {"tp": tp_, "fp": fp_, "fn": fn_},
                      "element_micro_f1_unweighted": f1 if f1_lie is None else f1_lie}
    pe["mean_element_micro_f1_unweighted"] = sum(v["element_micro_f1_unweighted"] for v in pe.values() if isinstance(v, dict)) / k
    met = {"run_id": run_id if not stale_metrics else run_id + "-old", "k": k, "per_execution": {"pooled": pe},
           "inputs": {"run_record_files": files if not stale_metrics else {}, "run_record_sha256": sha(rp),
                      "evaluate_sha256": seal["code"]["evaluate.py"],
                      "reference": dict(seal["reference"]) if not wrong_reference else {"x": "y"}}}
    mp = tmp / f"{run_id}_metrics.json"; mp.write_text(json.dumps(met))
    return w1.load_candidate(key, rp, mp, seal), rp, mp


def gate_only(tmp: Path, key: str, seal: dict, *, unreachable=False, validation=False, certified=False, model=None,
              started="2026-09-10T09:00:00+00:00", tool_sha=None, validation_on_probe=False) -> Path:
    raw = []
    if validation:
        raw.append({"model": key, "label": "production-call-1", "outcome": "exception", "error_code": "ValidationException",
                    "error_class": "ClientError", "error": "ValidationException: temperature is not supported"})
    if validation_on_probe:
        raw.append({"model": key, "label": "reachability", "outcome": "exception", "error_code": "ValidationException",
                    "error_class": "ClientError", "error": "ValidationException"})
    if unreachable:
        raw.append({"model": key, "label": "reachability", "outcome": "exception", "error_code": "AccessDeniedException",
                    "error_class": "ClientError", "error": "AccessDeniedException: anthropic.x is not available for this account"})
    gate = {"certified": certified, "certification_status": "CERTIFIED" if certified else ("NOT_CERTIFIED_API_UNREACHABLE" if (unreachable or validation_on_probe) else "NOT_CERTIFIED_ASSERTION_FAILED"),
            "checks": [{"id": "1", "ok": not (unreachable or validation_on_probe)}, {"id": "2", "ok": certified}], "models": {key: model or seal["model_ids"][key]},
            "tool_config_sha256": tool_sha or seal["contract_common"]["tool_config_sha256"],
            "system_prompt_sha256": seal["contract_common"]["prompt_sha256"], "codebook_sha256": seal["contract_common"]["codebook_sha256"],
            "provenance": {"preflight_sha256": seal["code"]["preflight.py"], "instrument_sha256": seal["code"]["instrument.py"]},
            "raw_responses": raw, "started_at": started, "ended_at": "2026-09-10T09:01:00+00:00"}
    gate_only.n = getattr(gate_only, "n", 0) + 1
    p = tmp / f"gate_{key}_{gate_only.n}.json"; p.write_text(json.dumps(gate)); return p


AFTER = "2026-09-16T08:00:00+00:00"       # after the access deadline: exclusions are final


def integration(tmp: Path, seal: dict, sp: Path):
    """The actual producer path: run_extract (fake client, dev, k = 3, 32 A1 paragraphs) -> evaluate --dev
    -> w1_select.load_candidate. Codex W1 round 6, #1: the fixtures had hidden a chronology mismatch."""
    import argparse, contextlib, io
    import run_extract as rx, evaluate as ev, test_evaluate as TE
    print("── W1 integration: runner -> evaluator -> selector on producer output")
    tmp2 = tmp / "integ"; tmp2.mkdir()
    rx.RUNS = tmp2 / "runs"; rx.RAW = rx.RUNS / "raw"; rx.RUNS.mkdir(parents=True); rx.RAW.mkdir()
    text = "Der Ausbau der Windkraft an Land stockt, weil Genehmigungen Jahre dauern und Netze fehlen."
    import hashlib as _h
    h = _h.sha256(text.encode()).hexdigest()[:16]     # the corpus binds texts by a 16-hex prefix
    pids = [f"P{i:02d}" for i in range(32)]
    rx.ALL_PARAGRAPHS = tmp2 / "ALL_paragraphs.csv"
    with rx.ALL_PARAGRAPHS.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["paragraph_id", "split", "article_id", "text_status", "lang", "outlet", "country", "pub_date", "n_words",
                    "text_sha256", "has_transcription_gap", "headline", "paragraph_text"])
        for i, pid in enumerate(pids):
            w.writerow([pid, "A1", f"A{i % 3}", "full", "de", "taz", "DE", "2026-01-01", len(text.split()), h, "", "H", text])
    man = json.loads(w1.A1_MANIFEST.read_text())
    for q in man["paragraphs"]:
        q["text_sha256"] = h
    man_p = tmp2 / "A1_manifest.json"; man_p.write_text(json.dumps(man))
    OFF = {"outcome": "off_topic", "relations": []}
    import test_run_extract as TR
    WIND = TR.WIND                                   # a schema- and contract-valid relation payload
    def resp(payload=OFF, u=None):
        return {"stopReason": "tool_use", "usage": u or {"inputTokens": 400, "outputTokens": 60, "cacheReadInputTokens": 2000, "cacheWriteInputTokens": 0},
                "output": {"message": {"content": [{"toolUse": {"name": rx.ins.TOOL_NAME, "input": payload}}]}}}
    # paragraph-major: P00..P03 x 3 executions answer with the wind relation, the rest off topic
    plan = [resp(), resp(u={"inputTokens": 400, "outputTokens": 60, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 2000}),
            resp()] + [resp(WIND) for _ in range(12)] + [resp() for _ in range(84)]
    class Fake:
        def __init__(self, plan): self.plan = list(plan)
        def converse(self, **kw): return self.plan.pop(0)
    fake = Fake(plan)
    a = argparse.Namespace(manifest=str(man_p), restrict_split="A1", model="sonnet", repeats=3, run_id="integ_sonnet",
                           resume=False, dev=True, recover_infrastructure=False)
    # the selector lapsed (2026-09-15) before the A3 listing existed (2026-10-14): its seal carries the contract the
    # runner wrote while W1 was live, `a3_embargo: None`, so the producer runs here without the listing, as it did then;
    # W1-I2c runs it with the listing and records the drift
    real_a3 = rx.A3_SAMPLE_MANIFEST
    rx.A3_SAMPLE_MANIFEST = tmp2 / "no_A3_sample_manifest.json"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rec = rx.run(a, client_factory=lambda: fake, sleep=lambda s: None)
    finally:
        rx.A3_SAMPLE_MANIFEST = real_a3
    check("W1-I1 the runner produced a development record with 96 executions and a certified gate",
          rec["totals"]["calls"] == 96 and rec["preflight"]["certified"] and rec["first_call_at"] is not None)
    # a reference for the 32 paragraphs: wind in P00..P03 (partial element agreement), the rest off topic
    g1 = tmp2 / "A1_pass1_v1.0.csv"; g2 = tmp2 / "A1_pass2_v1.0.csv"
    rows1 = [TE.p1row(pid, ["wind"] if i < 4 else [], f"A{i % 3}") for i, pid in enumerate(pids)]
    for r_ in rows1:
        r_["text_sha256"] = h
    TE.write_csv(g1, rows1, TE.P1_COLS)
    TE.write_csv(g2, [TE.p2row(pid, "wind", elements=("F8a",), evals={"regulation": "negative"}, target="none", subtype="onshore")
                      for pid in pids[:4]], TE.P2_COLS)
    ea = argparse.Namespace(run_record=str(rx.RUNS / "integ_sonnet.json"), manifest=str(man_p), restrict_split="A1",
                            gold_pass1=str(g1), gold_pass2=str(g2), dev=True, gold_manifest=None, skip_provenance=True)
    inp = ev.load_inputs(ea); res = ev.evaluate(inp, threshold=1, B=5, seed=1, dev=True)
    out = tmp2 / "tables"; ev.write_tables(res, out, out / "cm.json")
    met_p = out / "metrics.json"
    # a seal for THIS producer setup: the fake A1 manifest and its reference files
    (tmp2 / "sessions.csv").write_text("labels_file;labels_sha256\n" + f"{g1.name};{sha(g1)[:16]}\n{g2.name};{sha(g2)[:16]}\n", encoding="utf-8")
    w1.A1_MANIFEST = man_p; w1.A1_REFERENCE = [g1, g2]
    # W1-I2: the producer stamps the gate with the real clock and the registered W1 window closed on 2026-09-15, so
    # the integration path is checked under a controlled window (deadline 2099) — the seal records the window it was
    # built under and the selector reads it from the seal. W1-I2b checks the refusal under the registered window.
    real_deadline = w1.ACCESS_DEADLINE
    w1.ACCESS_DEADLINE = "2099-12-31T23:59:59+00:00"
    try:
        seal2 = w1.build_seal("W1-a", "The author confirms W1 for the integration run.", now="2026-09-01T00:00:00+00:00")
        sp2 = tmp2 / "seal.json"; sp2.write_text(json.dumps(seal2)); seal2 = w1.load_seal(sp2)
        cand = w1.load_candidate("sonnet", rx.RUNS / "integ_sonnet.json", met_p, seal2)
        check("W1-I2 the selector consumes the producer's record and metrics under a controlled window: eligible, S computed from the counts",
              cand["status"] == "eligible" and cand["S"] is not None, str(cand.get("status")))
    except w1.W1Error as exc:
        check("W1-I2 the selector consumes the producer's record and metrics under a controlled window: eligible, S computed from the counts", False, str(exc))
    finally:
        w1.ACCESS_DEADLINE = real_deadline
    seal3 = w1.build_seal("W1-a", "The author confirms W1 for the integration run.", now="2026-09-01T00:00:00+00:00")
    sp3 = tmp2 / "seal_registered.json"; sp3.write_text(json.dumps(seal3)); seal3 = w1.load_seal(sp3)
    lapsed = datetime.now(timezone.utc) > datetime.fromisoformat(real_deadline)
    label = "W1-I2b under the registered window (lapsed 2026-09-15) a gate dated today is refused; before the lapse it is eligible"
    try:
        cand3 = w1.load_candidate("sonnet", rx.RUNS / "integ_sonnet.json", met_p, seal3)
        check(label, not lapsed and cand3["status"] == "eligible", "accepted although the window has lapsed" if lapsed else str(cand3.get("status")))
    except w1.W1Error as exc:
        check(label, lapsed and "outside seal .. access deadline" in str(exc), str(exc))
    # W1-I2c: the drift itself. With the A3 listing present the current runner stamps the embargo state into the
    # contract (an A1 run touches no A3 paragraph); the lapsed seal lacks it, so the selector refuses the record at the
    # contract check, which precedes every metrics check (the W1-I2 metrics only fill the argument)
    fake_a3 = Fake(plan)
    with contextlib.redirect_stdout(io.StringIO()):
        rec_a3 = rx.run(argparse.Namespace(**{**vars(a), "run_id": "integ_sonnet_a3"}),
                        client_factory=lambda: fake_a3, sleep=lambda s: None)
    emb = rec_a3["execution_contract"].get("a3_embargo") or {}
    label = "W1-I2c with the A3 listing the runner stamps a non-touching embargo state the lapsed seal lacks; the selector refuses it"
    try:
        w1.load_candidate("sonnet", rx.RUNS / "integ_sonnet_a3.json", met_p, seal3)
        check(label, False, "accepted although the contract carries an embargo state the seal does not")
    except w1.W1Error as exc:
        check(label, emb.get("touches_a3") is False and emb.get("paragraphs_touched") == 0
              and "differs from the sealed one in ['a3_embargo']" in str(exc), f"{exc}; embargo state {emb}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="w1_"))
    w1.A1_MANIFEST = fake_a1(tmp)
    print("── W1 selection: seal, contract, classification, rule")
    seal = w1.build_seal("W1-a", "The author confirms W1 as written on 2026-09-06.", now="2026-09-09T12:00:00+00:00")
    sp = tmp / "seal.json"; sp.write_text(json.dumps(seal))
    check("W1-0 the seal names fork, roster, A1 digest, reference and code hashes and the common contract",
          seal["fork"] == "W1-a" and seal["candidates"] == w1.CANDIDATE_ORDER and set(seal["code"]) == set(w1.CODE_FILES)
          and "model_id" not in seal["contract_common"] and "prompt_sha256" in seal["contract_common"], str(sorted(seal)))
    loaded = w1.load_seal(sp)
    check("W1-0b the seal reloads while the code and reference are unchanged", loaded["sealed_at"] == seal["sealed_at"])
    refuses("W1-0c a seal needs an author confirmation", lambda: w1.build_seal("W1-a", ""), "confirmation")
    good = lambda key, **kw: make(tmp, key, seal, [(60, 20, 10), (61, 20, 9), (62, 20, 8)], **kw)
    a, _, _ = good("sonnet", cost=1.0)
    b, _, _ = make(tmp, "sonnet5", seal, [(70, 15, 0), (70, 15, 0), (70, 15, 0)], cost=0.6)
    c, _, _ = make(tmp, "opus", seal, [(50, 30, 20), (50, 30, 20), (50, 30, 20)], cost=2.0)
    d, _, _ = make(tmp, "opus5", seal, [(69, 15, 1), (69, 15, 1), (69, 15, 1)], cost=1.9)
    res = w1.select([a, b, c, d], seal)
    check("W1-1 S is exact and the highest S wins when the runner-up is within δ but dearer",
          res["decision"]["status"] == "selected" and res["decision"]["model_key"] == "sonnet5"
          and b["_S_exact"] == Fraction(140, 155) and set(res["decision"]["competitive_set"]) == {"sonnet5", "opus5"}, str(res["decision"]))
    e, _, _ = make(tmp, "opus5", seal, [(69, 15, 1)] * 3, cost=0.5)
    res = w1.select([a, b, c, e], seal)
    check("W1-2 inside the competitive set the cheaper candidate is selected even with a lower S", res["decision"]["model_key"] == "opus5")
    b3, _, _ = make(tmp, "sonnet5", seal, [(70, 15, 0)] * 3, cost=0.55)
    f, _, _ = make(tmp, "opus5", seal, [(70, 15, 0)] * 3, cost=0.55)
    res = w1.select([a, b3, c, f], seal)
    check("W1-3 an exact cost tie is broken by the fixed candidate order", res["decision"]["model_key"] == "sonnet5" and "fixed order" in res["decision"]["reason"],
          str((b3["cost_per_paragraph_usd"], f["cost_per_paragraph_usd"], res["decision"])))
    g, _, _ = make(tmp, "opus5", seal, [(69, 15, 1)] * 3, cost=0.5, billing=False)
    res = w1.select([a, b, c, g], seal)
    check("W1-4 an incomplete cost inside a competitive set of more than one stops the selection", res["decision"]["status"] == "selection_stop_incomplete_cost")
    # the strict δ boundary, exactly: S_max = 0.70 and S = 0.68 differ by exactly 1/50 -> NOT competitive
    h1, _, _ = make(tmp, "sonnet", seal, [(70, 30, 0)] * 3, cost=1.0)           # F1 = 140/200 = 0.70
    h2, _, _ = make(tmp, "sonnet5", seal, [(68, 62, 2)] * 3, cost=0.1)          # F1 = 136/200 = 0.68
    h3, _, _ = make(tmp, "opus", seal, [(1, 100, 69)] * 3, cost=0.1)
    h4, _, _ = make(tmp, "opus5", seal, [(1, 100, 69)] * 3, cost=0.1)
    res = w1.select([h1, h2, h3, h4], seal)
    check("W1-5 a difference of exactly δ is not competitive (exact fractions, no float boundary drift)",
          res["decision"]["model_key"] == "sonnet" and res["decision"]["competitive_set"] == ["sonnet"], str(res["decision"]))
    i1, _, _ = good("sonnet", outcomes={(0, 2): "parse_failed"})
    i2, _, _ = good("sonnet5", outcomes={(1, 1): "refused"})
    i3, _, _ = good("opus", outcomes={(2, 3): "error"}, episodes={(2, 3): 2})
    i3p, _, _ = good("opus", outcomes={(2, 3): "error"}, episodes={(2, 3): 1})
    i4, _, _ = good("opus5", unreachable=True)
    i5, _, _ = good("opus5", certified=False)
    check("W1-6 classification from the records: ineligible / not_assessed / recovery_pending / inaccessible / gate_failed",
          (i1["status"], i2["status"], i3["status"], i3p["status"], i4["status"], i5["status"])
          == ("ineligible", "ineligible", "not_assessed", "recovery_pending", "inaccessible", "gate_failed"),
          str([(x["key"], x["status"]) for x in (i1, i2, i3, i3p, i4, i5)]))
    refuses("W1-6b a candidate with recovery episodes left blocks the selection", lambda: w1.select([a, b, i3p, d], seal), "recovery episodes left")
    res = w1.select([i1, i2, i3, i4], seal, now=AFTER)
    check("W1-7 no eligible candidate -> production_stop", res["decision"]["status"] == "production_stop")
    go = w1.load_gate_only("opus5", gate_only(tmp, "opus5", seal, unreachable=True), seal)
    gv = w1.load_gate_only("opus5", gate_only(tmp, "opus5", seal, validation=True), seal)
    gf = w1.load_gate_only("opus5", gate_only(tmp, "opus5", seal), seal)
    gvp = w1.load_gate_only("opus5", gate_only(tmp, "opus5", seal, validation_on_probe=True), seal)
    check("W1-8 gate-only records classify inaccessible / incompatible / gate_failed from raw_responses error codes, a validation rejection on the probe included",
          (go["status"], gv["status"], gf["status"], gvp["status"]) == ("inaccessible", "incompatible", "gate_failed", "incompatible"),
          str((go["status"], gv["status"], gf["status"], gvp["status"])))
    refuses("W1-8c an exclusion before the access deadline is not final: the selection refuses",
            lambda: w1.select([a, b, c, go], seal, now="2026-09-12T08:00:00+00:00"), "not final before")
    res = w1.select([a, b, c, go], seal, now=AFTER)
    check("W1-8b a gate-only candidate takes its roster place and the selection proceeds", res["decision"]["model_key"] == "sonnet5")
    # contract refusals
    refuses("W1-9 k != 3", lambda: good("sonnet", k=1), "k = 1")
    refuses("W1-9b no --restrict-split A1", lambda: good("sonnet", split=None), "restrict_split")
    refuses("W1-9c not a development run", lambda: good("sonnet", dev=False), "development runs")
    refuses("W1-9d not the sealed A1 manifest", lambda: good("sonnet", msha="0" * 64), "sealed A1 manifest")
    refuses("W1-9e wrong paragraph count", lambda: good("sonnet", n=1), "paragraphs, the contract fixes")
    refuses("W1-9f a contract that differs from the sealed one (other prompt hash)", lambda: good("sonnet", contract_tweak={"prompt_sha256": "x"}), ("differs from the sealed one", "journal reconciliation"))
    def _rewritten_contract():
        c, rp_, mp_ = good("sonnet")
        rd_ = json.loads(rp_.read_text()); rd_["execution_contract"]["max_tokens"] = 2048
        rd_["execution_fingerprint"] = hashlib.sha256(json.dumps(rd_["execution_contract"], sort_keys=True).encode()).hexdigest()
        rp_.write_text(json.dumps(rd_)); md_ = json.loads(mp_.read_text()); md_["inputs"]["run_record_sha256"] = sha(rp_); mp_.write_text(json.dumps(md_))
        return w1.load_candidate("sonnet", rp_, mp_, seal)
    refuses("W1-25 a record whose contract and fingerprint were rewritten after the run is refused by the journal's run_started fingerprint",
            _rewritten_contract, ("journal reconciliation", "differs from the sealed one"))
    refuses("W1-9g a model_id that is not the sealed one for the key", lambda: good("sonnet", model_id="eu.other"), "not the sealed")
    refuses("W1-9h a run started before the seal", lambda: good("sonnet", started="2026-09-01T10:00:00+00:00"), "before the seal")
    refuses("W1-9i a terminal file whose digest differs", lambda: (lambda c: (c[1].write_text(c[1].read_text()), Path(str(c[1]).replace(".json", "_terminal.csv")).write_text("tampered"), w1.load_candidate("sonnet", c[1], c[2], seal)))(good("sonnet")), "digest differs")
    refuses("W1-9j terminal rows that do not cover every (paragraph, execution) once", lambda: good("sonnet", drop_rows=1, dup_rows=1), ("exactly once", "journal reconciliation", "exactly one per"))
    refuses("W1-9k stale metrics not computed from this run's files", lambda: good("sonnet", stale_metrics=True), "metrics")
    refuses("W1-9l metrics against another reference", lambda: good("sonnet", wrong_reference=True), "sealed reference")
    refuses("W1-9m an F1 that does not match its own counts", lambda: good("sonnet", f1_lie=0.99), ("does not match its own counts", "not what the journal-verified"))
    refuses("W1-9n metrics whose counts are not what the journal-verified predictions give against the sealed reference",
            lambda: good("sonnet", counts_lie=[(70, 0, 0)] * 3), "not what the journal-verified predictions give")
    refuses("W1-10 a duplicate candidate", lambda: w1.select([a, a, b, c], seal), "supplied twice")
    refuses("W1-10b a missing candidate", lambda: w1.select([a, b, c], seal), "roster is not the sealed one")
    late, _, _ = good("opus5", started="2026-09-12T13:00:00+00:00", ended="2026-09-12T14:00:00+00:00")
    refuses("W1-10c runs spanning more than 48 hours (first start to last end)", lambda: w1.select([a, b, c, late], seal), "48 hours")
    # round 4: fork validation, complete seal, gate-only provenance, unrun, provenance fields, bounds
    refuses("W1-13 the fork label must match the request the builder sends (W1-b with the pinned request is refused)",
            lambda: w1.build_seal("W1-b", "The author confirms W1-b."), "fork W1-b needs")
    partial = dict(seal); partial.pop("access_deadline"); sp3 = tmp / "seal3.json"; sp3.write_text(json.dumps(partial))
    refuses("W1-14 an incomplete seal is refused", lambda: w1.load_seal(sp3), "incomplete")
    nocode = dict(seal); nocode["code"] = {}; sp4 = tmp / "seal4.json"; sp4.write_text(json.dumps(nocode))
    refuses("W1-14b a seal that does not hash all six code files is refused", lambda: w1.load_seal(sp4), "six code files")
    refuses("W1-15 a gate-only artefact naming another model is refused",
            lambda: w1.load_gate_only("opus5", gate_only(tmp, "opus5", seal, unreachable=True, model="eu.other"), seal), "not for the sealed")
    empty = tmp / "empty_gate.json"; empty.write_text("{}")
    refuses("W1-15b an empty artefact is not a gate record", lambda: w1.load_gate_only("opus5", empty, seal), "not a preflight record")
    refuses("W1-15c a gate-only artefact dated before the seal is refused",
            lambda: w1.load_gate_only("opus5", gate_only(tmp, "opus5", seal, unreachable=True, started="2026-09-01T09:00:00+00:00"), seal), "outside seal")
    refuses("W1-19 a scored candidate whose gate predates the seal is refused",
            lambda: good("sonnet", gate_started="2026-09-01T09:00:00+00:00"), "outside seal")
    refuses("W1-19b a gate dated after the first extraction call is refused",
            lambda: good("sonnet", gate_started="2026-09-10T10:30:00+00:00"), "after the first extraction call")
    refuses("W1-20 a gate produced by a non-sealed preflight.py is refused",
            lambda: good("sonnet", gate_prov_tweak={"preflight_sha256": "0" * 64}), "not the sealed one")
    gf_run, _, _ = good("opus5", certified=False)
    refuses("W1-21 exclusion finality is by disposition: a gate-failed RUN record before the deadline refuses the selection",
            lambda: w1.select([a, b, c, gf_run], seal, now="2026-09-12T08:00:00+00:00"), "not final before")
    refuses("W1-15d a gate-only artefact under another tool config is refused",
            lambda: w1.load_gate_only("opus5", gate_only(tmp, "opus5", seal, unreachable=True, tool_sha="0" * 64), seal), "another instrument")
    ur = w1.load_unrun("opus5", gate_only(tmp, "opus5", seal, certified=True), seal)
    check("W1-16 a certified but unrun candidate is not_assessed", ur["status"] == "not_assessed" and "no scored run" in ur["reason"])
    refuses("W1-16b an unrun record needs a certified gate", lambda: w1.load_unrun("opus5", gate_only(tmp, "opus5", seal), seal), "CERTIFIED gate")
    cc2, rp2, mp2 = good("sonnet")
    rd = json.loads(rp2.read_text()); rd["cost"]["estimated_usd"] = 5.0; rp2.write_text(json.dumps(rd))
    refuses("W1-17 a run record edited after the evaluation is refused", lambda: w1.load_candidate("sonnet", rp2, mp2, seal), ("changed after the evaluation", "not the journal's usage"))
    cc3, rp3, mp3 = good("sonnet")
    rd = json.loads(rp3.read_text()); rd["cost"]["estimated_usd"] = -100.0; rp3.write_text(json.dumps(rd))
    md = json.loads(mp3.read_text()); md["inputs"]["run_record_sha256"] = sha(rp3); mp3.write_text(json.dumps(md))
    refuses("W1-17b a negative cost is refused", lambda: w1.load_candidate("sonnet", rp3, mp3, seal), ("finite non-negative", "not the journal's usage"))
    # a shaved recorded cost cannot move the selection: the candidate costs with the JOURNAL's recomputed value
    c26, rp26, mp26 = make(tmp, "opus5", seal, [(70, 15, 0)] * 3, cost=0.55)
    rd_ = json.loads(rp26.read_text()); rd_["cost"]["estimated_usd"] = rd_["cost"]["estimated_usd"] - 0.0000005; rp26.write_text(json.dumps(rd_))
    md_ = json.loads(mp26.read_text()); md_["inputs"]["run_record_sha256"] = sha(rp26); mp26.write_text(json.dumps(md_))
    try:
        c26b = w1.load_candidate("opus5", rp26, mp26, seal)
        check("W1-26 a cost shaved by half a millionth does not move the selection: the candidate carries the journal's recomputed cost",
              abs(c26b["cost_per_paragraph_usd"] - 0.55) < 1e-12, str(c26b["cost_per_paragraph_usd"]))
    except w1.W1Error as exc:
        check("W1-26 a cost shaved by half a millionth does not move the selection: the candidate carries the journal's recomputed cost",
              "six decimals" in str(exc), str(exc))
    rd_ = json.loads(rp26.read_text()); rd_["cost"]["estimated_usd"] = rd_["cost"]["estimated_usd"] - 0.001; rp26.write_text(json.dumps(rd_))
    md_ = json.loads(mp26.read_text()); md_["inputs"]["run_record_sha256"] = sha(rp26); mp26.write_text(json.dumps(md_))
    refuses("W1-26b a recorded cost that differs at six decimals from the journal's is refused", lambda: w1.load_candidate("opus5", rp26, mp26, seal), "six decimals")
    cc4, rp4, mp4 = good("sonnet")
    md = json.loads(mp4.read_text()); md["inputs"]["evaluate_sha256"] = "0" * 64; mp4.write_text(json.dumps(md))
    refuses("W1-17c metrics from a non-sealed evaluate.py are refused", lambda: w1.load_candidate("sonnet", rp4, mp4, seal), "not the sealed one")
    refuses("W1-18 a run record that ends before it starts is refused",
            lambda: good("sonnet", started="2026-09-10T11:00:00+00:00", ended="2026-09-10T10:00:00+00:00"), "ends before it starts")
    cc5, rp5, mp5 = good("sonnet")
    tp5 = Path(json.loads(rp5.read_text())["files"][f"{cc5['run_id']}_terminal.csv"]["path"])
    tp5.write_text(tp5.read_text().replace("P00", "ZZZ")); rd = json.loads(rp5.read_text()); rd["files"][tp5.name]["sha256"] = sha(tp5); rp5.write_text(json.dumps(rd))
    md = json.loads(mp5.read_text()); md["inputs"]["run_record_files"] = rd["files"]; md["inputs"]["run_record_sha256"] = sha(rp5); mp5.write_text(json.dumps(md))
    refuses("W1-18b terminal rows naming a paragraph outside the sealed A1 set are refused", lambda: w1.load_candidate("sonnet", rp5, mp5, seal), ("sealed A1 paragraphs", "journal reconciliation"))
    cc6, rp6, mp6 = good("sonnet")
    gp6 = Path(json.loads(rp6.read_text())["preflight"]["artefact"]); g = json.loads(gp6.read_text()); g["models"] = {"sonnet": "eu.other"}; gp6.write_text(json.dumps(g))
    rd = json.loads(rp6.read_text()); rd["preflight"]["sha256"] = sha(gp6); rd["preflight"]["gates"][0]["sha256"] = sha(gp6); rp6.write_text(json.dumps(rd))
    md = json.loads(mp6.read_text()); md["inputs"]["run_record_sha256"] = sha(rp6); mp6.write_text(json.dumps(md))
    refuses("W1-18c a scored candidate whose gate artefact certifies another model is refused", lambda: w1.load_candidate("sonnet", rp6, mp6, seal), "not for the sealed")
    # round 7: labels bound to the gate they were produced under; every journalled gate verified
    first_bad = gate_only(tmp, "sonnet", seal)                                   # an uncertified first gate
    r22, _, _ = good("sonnet", extra_gate={"artefact": str(first_bad), "sha256": sha(first_bad), "certified": False},
                     label_gate_sha=sha(first_bad), gate_started="2026-09-10T10:00:00+00:00")
    check("W1-22 labels produced under an uncertified first gate stay gate_failed although a later gate certified",
          r22["status"] == "gate_failed" and "later certified gate does not qualify" in r22["reason"], str((r22["status"], r22["reason"])))
    r23, rp23, mp23 = good("sonnet", extra_gate={"artefact": str(tmp / "gone.json"), "sha256": "0" * 64, "certified": True})  if False else (None, None, None)
    def _missing_middle():
        c, rp_, mp_ = good("sonnet")
        rd_ = json.loads(rp_.read_text()); rd_["preflight"]["gates"].insert(0, {"artefact": str(tmp / "gone.json"), "sha256": "0" * 64, "certified": True})
        rp_.write_text(json.dumps(rd_)); md_ = json.loads(mp_.read_text()); md_["inputs"]["run_record_sha256"] = sha(rp_); mp_.write_text(json.dumps(md_))
        return w1.load_candidate("sonnet", rp_, mp_, seal)
    refuses("W1-23 a journalled gate whose artefact is missing refuses the candidate", _missing_middle, "missing or its digest differs")
    def _loose_label():
        c, rp_, mp_ = good("sonnet", label_gate_sha="f" * 64)
        return c
    refuses_not = None
    try:
        c_loose = _loose_label()
        check("W1-23b a label naming a gate that is not one of the run's certified gates is not eligible",
              c_loose["status"] == "gate_failed" and "produced under a gate that did not certify" in c_loose["reason"], str((c_loose["status"], c_loose["reason"])))
    except w1.W1Error as exc:
        check("W1-23b a label naming a gate that is not one of the run's certified gates is not eligible", False, str(exc))
    # round 8: the summary must be what the journal says
    def _summary_drops_failed_gate():
        c, rp_, mp_ = good("sonnet", extra_gate={"artefact": str(first_bad), "sha256": sha(first_bad), "certified": False},
                           label_gate_sha=sha(first_bad), gate_started="2026-09-10T10:00:00+00:00")
        rd_ = json.loads(rp_.read_text()); rd_["preflight"]["gates"] = rd_["preflight"]["gates"][1:]; rd_["preflight"]["all_certified"] = True
        tp_ = Path(rd_["files"][f"{c['run_id']}_terminal.csv"]["path"]); rows_ = list(csv.DictReader(tp_.open(encoding="utf-8"), delimiter=";"))
        for r_ in rows_:
            r_["gate"] = rd_["preflight"]["gates"][0]["sha256"]
        with tp_.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_[0].keys()), delimiter=";"); w.writeheader(); w.writerows(rows_)
        rd_["files"][tp_.name]["sha256"] = sha(tp_); rp_.write_text(json.dumps(rd_))
        md_ = json.loads(mp_.read_text()); md_["inputs"]["run_record_files"] = rd_["files"]; md_["inputs"]["run_record_sha256"] = sha(rp_); mp_.write_text(json.dumps(md_))
        return w1.load_candidate("sonnet", rp_, mp_, seal)
    refuses("W1-24 dropping the failed gate from the summary and relabelling the terminals is refused by the journal", _summary_drops_failed_gate, "journal reconciliation")
    # seal integrity
    tampered = dict(seal); tampered["code"] = dict(seal["code"]); tampered["code"]["w1_select.py"] = "0" * 64
    sp2 = tmp / "seal2.json"; sp2.write_text(json.dumps(tampered))
    refuses("W1-11 a seal whose code hashes are not the code on disk is refused", lambda: w1.load_seal(sp2), "code has changed")
    # CLI
    out = tmp / "sel.json"
    ca, rpa, mpa = good("sonnet", cost=1.0); cb, rpb, mpb = make(tmp, "sonnet5", seal, [(70, 15, 0)] * 3, cost=0.6)
    cc, rpc, mpc = make(tmp, "opus", seal, [(50, 30, 20)] * 3, cost=2.0)
    cd_, rpd, mpd = make(tmp, "opus5", seal, [(60, 20, 10)] * 3, cost=1.9)
    doc = w1.main(["--seal", str(sp), "--candidate", f"sonnet={rpa}:{mpa}", "--candidate", f"sonnet5={rpb}:{mpb}",
                   "--candidate", f"opus={rpc}:{mpc}", "--candidate", f"opus5={rpd}:{mpd}", "--out", str(out)])
    check("W1-12 the CLI writes one decision file naming the seal, all four candidates, the decision and the trace",
          out.exists() and doc["kind"] == "W1_selection" and len(doc["candidates"]) == 4 and doc["decision"]["model_key"] == "sonnet5"
          and doc["seal"]["fork"] == "W1-a" and not any(k.startswith("_") for c in doc["candidates"] for k in c))
    ur2 = w1.load_unrun("opus", gate_only(tmp, "opus", seal, certified=True), seal)
    res = w1.select([ca, cb, ur2, cd_], seal, now=AFTER)
    check("W1-12c an unrun candidate takes its roster place once the deadline has passed", res["decision"]["model_key"] == "sonnet5")
    try:
        w1.main(["--seal", str(sp), "--candidate", f"sonnet={rpa}:{mpa}", "--out", str(out)])
        check("W1-12b the decision file is written once", False, "no error")
    except SystemExit as exc:
        check("W1-12b the decision file is written once", "written once" in str(exc))
    integration(tmp, seal, sp)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("  FAILED: " + "; ".join(FAILED)); sys.exit(1)


if __name__ == "__main__":
    main()
