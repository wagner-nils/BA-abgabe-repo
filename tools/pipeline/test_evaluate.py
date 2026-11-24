#!/usr/bin/env python3
"""Contract tests for the M6-lite evaluator on fixtures. The round-4 acceptance list.

    .venv/bin/python3 tools/pipeline/test_evaluate.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as ev                                           # noqa: E402
ev.sc.REQUIRE_SEAL_COMMIT = False        # fixture seals live in a temp dir; the git-commitment path has its own test (F8r)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name, cond, detail=""):
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"\n          {detail}" if not cond and detail else ""))


P2_COLS = ["row_id", "paragraph_id", "technology", "wind_siting", "hydrogen_pathway", "lang", "outlet",
           *ev.ELEMENTS, "no_frame_for_this_tech", *[f"eval_{f}" for f in ev.FRAMES], "comparison_target",
           "comparison_note", "speaker", "confidence", "note", "annotator", "annotator_type", "coded_at",
           "codebook_version", "sitting"]
P1_COLS = ["paragraph_id", "article_id", "lang", "outlet", "country", "pub_date", "tech_general", "tech_solar",
           "tech_wind", "tech_hydrogen", "tech_nuclear", "tech_other_renewable", "tech_storage", "wind_siting",
           "hydrogen_pathway", "no_frame_present", "confidence", "note", "annotator", "annotator_type",
           "coded_at", "codebook_version", "sitting", "text_sha256"]
TECHS = ["general", "solar", "wind", "hydrogen", "nuclear", "other_renewable", "storage"]


def p2row(pid, tech, elements=(), evals=None, target="none", subtype=""):
    r = {c: "" for c in P2_COLS}
    r.update({"row_id": f"{pid}__{tech}", "paragraph_id": pid, "technology": tech, "comparison_target": target,
              "speaker": "outlet", "confidence": "3", "annotator": "NW", "annotator_type": "human",
              "coded_at": "2026-09-05", "codebook_version": "1.0", "sitting": "1"})
    for e in ev.ELEMENTS:
        r[e] = "1" if e in elements else "0"
    r["no_frame_for_this_tech"] = "0" if elements else "1"
    for f, v in (evals or {}).items():
        r[f"eval_{f}"] = v
    if tech == "wind":
        r["wind_siting"] = subtype or "unspecified"
    return r


def p1row(pid, techs, article):
    r = {c: "" for c in P1_COLS}
    r.update({"paragraph_id": pid, "article_id": article, "annotator": "NW", "annotator_type": "human",
              "coded_at": "2026-09-05", "codebook_version": "1.0", "sitting": "1", "confidence": "3",
              "no_frame_present": "0" if techs else ""})
    for t in TECHS:
        r[f"tech_{t}"] = "1" if t in techs else "0"
    return r


def write_csv(path, rows, cols):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";"); w.writeheader(); w.writerows(rows)


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


class Fixture:
    """A manifest, gold and a run's outputs from compact descriptions."""

    def __init__(self, tmp: Path, name: str, paras: list[dict], gold: dict, preds: dict, k: int,
                 outcomes: dict | None = None, reportable=True):
        self.tmp, self.name = tmp, name
        man = {"kind": "A2_sample_manifest", "paragraphs": paras}
        # a reportable fixture hand-shakes with a fixture frame manifest and registry (Codex W1 #13)
        self.frame = self.registry = None
        if reportable:
            # The frame realises the fixture's design weights: a paragraph with weight w sits in a
            # stratum of N_h = n_h * w frame paragraphs of which the n_h fixture paragraphs are drawn
            # (pi = 1/w). Paragraphs of different weight get different outlets, so strata stay pure;
            # the undrawn frame members are fillers that never appear in the sample.
            from collections import Counter as _C
            for q in paras:
                q["outlet"] = f"{q.get('outlet', 'O')}-w{q['weight']:g}"
            drawn = _C((q["lang"], q["outlet"]) for q in paras)
            fillers = []
            for (L, O), n_h in sorted(drawn.items()):
                w = next(q["weight"] for q in paras if (q["lang"], q["outlet"]) == (L, O))
                N_h = round(n_h * w); assert abs(N_h - n_h * w) < 1e-9, (n_h, w)
                for q in paras:
                    if (q["lang"], q["outlet"]) == (L, O):
                        q.update({"stratum": f"{L}|{O}", "N_h": N_h, "n_h": n_h, "pi": n_h / N_h, "weight": N_h / n_h})
                for i in range(N_h - n_h):
                    pid = f"fill-{L}-{O}-{i}"
                    fillers.append({"paragraph_id": pid, "article_id": f"FILL-{L}-{O}", "digest": f"d-{pid}",
                                    "text_sha256": f"t-{pid}", "lang": L, "outlet": O, "exposure": "clean_article"})
            frame = {"kind": "A2_frame_manifest", "pre_freeze_head": f"pfh-{name}", "n_eligible": len(paras) + len(fillers),
                     "eligible_checksum": f"ec-{name}",
                     "paragraphs": [{k: q[k] for k in ("paragraph_id", "article_id", "digest", "text_sha256", "lang", "outlet", "exposure")}
                                    for q in paras] + fillers}
            fb = json.dumps(frame, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            frame["digest"] = hashlib.sha256(fb).hexdigest()
            self.frame = tmp / f"{name}_frame.json"; self.frame.write_text(json.dumps(frame))
            reg = {"assignment_log": [{"seq": 1, "kind": "assign", "h": f"pfh-{name}"},
                                      {"seq": 2, "kind": "a2_frozen", "audit_ref": frame["digest"], "h": f"frz-{name}",
                                       "eligible_checksum": f"ec-{name}"}]}
            self.registry = tmp / f"{name}_registry.json"; self.registry.write_text(json.dumps(reg))
            man["frame_manifest_digest"] = frame["digest"]; man["freeze_event_hash"] = f"frz-{name}"
            man["n"] = len(paras); man["by_language"] = dict(_C(q["lang"] for q in paras))
            man["design"] = {"cap_used": {L: 24 for L in man["by_language"]}, "cap_registered": 24,
                             "target": dict(man["by_language"])}
        body = json.dumps(man, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        man["digest"] = hashlib.sha256(body).hexdigest()
        self.msha = man["digest"]
        self.manifest = tmp / f"{name}_manifest.json"; self.manifest.write_text(json.dumps(man))
        self.seal = None
        if self.frame is not None:          # a reportable fixture carries its own threshold seal (s = 1), like the real draw
            self.seal = tmp / f"{name}_threshold_seal.json"
            import sample_contract as _sc
            _sc.write_threshold_seal(1, "value chosen blind to every A2 label (fixture)",
                                     seal_path=self.seal, sample_path=self.manifest, frame_path=self.frame)
        g1 = [p1row(p["paragraph_id"], sorted({t for (pid, t) in gold if pid == p["paragraph_id"]}), p["article_id"]) for p in paras]
        g2 = [p2row(pid, t, **spec) for (pid, t), spec in sorted(gold.items())]
        self.g1 = tmp / f"{name}_p1.csv"; self.g2 = tmp / f"{name}_p2.csv"
        write_csv(self.g1, g1, P1_COLS); write_csv(self.g2, g2, P2_COLS)
        runs = tmp / "runs"; runs.mkdir(exist_ok=True)
        # The gate this fixture's run relies on, certifying its model, instrument and code.
        gate = {"certified": reportable, "certification_status": "CERTIFIED" if reportable else "NOT_CERTIFIED",
                "checks": [{"id": "1", "ok": True}], "models": {"sonnet": "eu.anthropic.claude-sonnet-4-6"},
                "tool_config_sha256": "tc", "system_prompt_sha256": "sp", "codebook_sha256": "cb", "started_at": "2026-09-10T09:00:00+00:00",
                "provenance": {"preflight_sha256": "pf", "instrument_sha256": "in"}}
        gp = runs / f"preflight_{name}.json"; gp.write_text(json.dumps(gate)); self.gate = gp
        contract = {"preflight_sha256": "pf", "instrument_sha256": "in"}
        fp = hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()
        # A runner-format calls journal from which terminal.csv and predictions.csv are DERIVED
        # (rx.derive_rows), exactly as the runner writes them; the evaluator reconciles against it.
        started, first_call, ended = "2026-09-10T09:00:00+00:00", "2026-09-10T09:00:05+00:00", "2026-09-10T09:30:00+00:00"
        jl = [{"type": "run_started", "run_id": name, "started_at": started, "fingerprint": fp, "at": started},
              {"type": "gate", "run_id": name, "artefact": str(gp), "sha256": sha(gp), "certified": reportable, "at": started}]
        by_key = {}
        for (r, pid, tech), spec in sorted(preds.items()):
            by_key.setdefault((pid, r), []).append((tech, spec))
        usage0 = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "usage_complete": True}
        for p in paras:
            for r in range(1, k + 1):
                pid = p["paragraph_id"]
                oc = (outcomes or {}).get((r, pid))
                rels = by_key.get((pid, r), [])
                if oc is None:
                    oc = "technologies_present" if rels else "off_topic"
                jl.append({"type": "attempt_started", "run_id": name, "paragraph_id": pid, "run_index": r, "attempt_no": 1,
                           "gate_sha256": sha(gp), "at": first_call})
                if oc in ("technologies_present", "off_topic"):
                    payload = {"outcome": oc, "relations": [
                        {"technology": tech, "wind_siting": spec.get("subtype", "") if tech == "wind" else "",
                         "hydrogen_pathway": spec.get("subtype", "") if tech == "hydrogen" else "",
                         "elements": list(spec.get("elements", ())), "evaluations": dict(spec.get("evals") or {}),
                         "comparison_target": spec.get("target", "none")} for tech, spec in rels]}
                    jl.append({"type": "attempt_finished", "run_id": name, "paragraph_id": pid, "run_index": r, "attempt_no": 1,
                               "code": "ok_technologies_present" if rels else "ok_off_topic", "usage": usage0, "payload": payload, "at": first_call})
                    jl.append({"type": "terminal", "run_id": name, "paragraph_id": pid, "run_index": r, "outcome": oc, "n_attempts": 1,
                               "codes": ["ok"], "resolved_from_attempt": 1, "episode": 0, "resolved_under_gate": sha(gp), "at": first_call})
                else:
                    jl.append({"type": "attempt_finished", "run_id": name, "paragraph_id": pid, "run_index": r, "attempt_no": 1,
                               "code": "error", "usage": None, "error_class": "ClientError", "error_code": "ValidationException", "at": first_call})
                    jl.append({"type": "terminal", "run_id": name, "paragraph_id": pid, "run_index": r, "outcome": oc, "n_attempts": 1,
                               "codes": ["error"], "resolved_from_attempt": None, "episode": 0, "resolved_under_gate": None, "at": first_call})
        jl.append({"type": "run_finished", "run_id": name, "ended_at": ended, "outcome": "completed", "at": ended})
        cp = runs / f"{name}_calls.jsonl"; cp.write_text("".join(json.dumps(r) + "\n" for r in jl)); self.calls = cp
        import run_extract as rx
        term_rows, pred_rows = rx.derive_rows(name, jl, paras)
        pp = runs / f"{name}_predictions.csv"; write_csv(pp, pred_rows, rx.PRED_COLUMNS)
        tp = runs / f"{name}_terminal.csv"; write_csv(tp, term_rows, rx.TERMINAL_COLUMNS)
        tot = rx.accounting(jl)
        rec = {"run_id": name, "reportable": reportable, "dev": not reportable, "repeats": k,
               "started_at": started, "first_call_at": first_call, "ended_at": ended,
               "model_key": "sonnet", "model_id": "eu.anthropic.claude-sonnet-4-6", "tool_config_sha256": "tc",
               "prompt": {"sha256": "sp"}, "codebook": {"sha256": "cb"},
               "execution_contract": contract, "execution_fingerprint": fp,
               "preflight": {"certified": reportable, "artefact": str(gp), "sha256": sha(gp), "all_certified": reportable,
                             "gates": [{"artefact": str(gp), "sha256": sha(gp), "certified": reportable}]},
               "totals": {f_: getattr(tot, f_) for f_ in ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                                                         "billing_uncertain_attempts", "usage_incomplete_attempts", "unknown_completion_attempts", "cache_writes_other_ttl")},
               "cost": {"estimated_usd": 0.0, "billing_complete": rx.billing_is_complete(tot)},
               "manifest": {"sha256": self.msha, "restrict_split": None, "n_paragraphs": len(paras)},
               "files": {f"{name}_calls.jsonl": {"path": str(cp), "sha256": sha(cp)},
                         f"{name}_terminal.csv": {"path": str(tp), "sha256": sha(tp)},
                         f"{name}_predictions.csv": {"path": str(pp), "sha256": sha(pp)}}}
        self.rec = runs / f"{name}.json"; self.rec.write_text(json.dumps(rec))

    def args(self, **kw):
        import argparse
        # fixtures have no sessions.csv, so they run as development evaluations with the provenance
        # binding skipped -- which the evaluator accepts only together with dev=True
        base = dict(run_record=str(self.rec), manifest=str(self.manifest), restrict_split=None, gold_pass1=str(self.g1),
                    gold_pass2=str(self.g2), dev=True, gold_manifest=None, skip_provenance=True, threshold=1)
        base.update(kw)
        if self.frame is not None:          # point the evaluator's handshake at this fixture's frame and registry
            ev.FRAME_MANIFEST, ev.REGISTRY_FILE = self.frame, self.registry
            # the fixture IS the registered draw: same file, its own target, the registered cap
            man_now = json.loads(self.manifest.read_text())
            ev.sc.SAMPLE_MANIFEST = self.manifest; ev.sc.REGISTERED_TARGET = dict(man_now["by_language"]); ev.sc.REGISTERED_CAP = 24
            ev.sc.THRESHOLD_SEAL = self.seal    # the fixture's own seal, written in __init__
        return argparse.Namespace(**base)


def ev_pred_cols():
    import run_extract as rx
    return rx.PRED_COLUMNS


def para(pid, article, lang, weight=1.0, exposure="clean_article", outlet="O"):
    return {"paragraph_id": pid, "article_id": article, "lang": lang, "weight": weight, "exposure": exposure,
            "outlet": outlet, "digest": f"d-{pid}", "text_sha256": f"t-{pid}"}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="evaluate_"))
    try:
        # ---- F1: universe, omission, invention, off-topic gold ------------------------------ #
        paras = [para("P1", "A1", "de"), para("P2", "A1", "de"), para("P3", "A2", "en")]
        gold = {("P1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"}),
                ("P1", "solar"): dict(elements=("F3a",), evals={"climate": "positive"})}   # P2, P3 off-topic in gold
        preds = {(1, "P1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"}),   # correct
                 (1, "P1", "nuclear"): dict(elements=("F5a",), evals={"reliability": "negative"}),   # invention
                 (1, "P3", "solar"): dict(elements=("F2c",), evals={"jobs": "positive"})}   # invention on gold off-topic
        fx = Fixture(tmp, "f1", paras, gold, preds, k=1)
        inp = ev.load_inputs(fx.args()); res = ev.evaluate(inp, threshold=1, B=50, seed=1, dev=False)
        pooled = res["point"]["pooled"]
        # element tuples: gold {P1 wind F1a, P1 solar F3a}; pred {P1 wind F1a, P1 nuclear F5a, P3 solar F2c} -> TP1 FP2 FN1 -> F1 = 2/(2+2+1)=0.4
        check("F1 element micro-F1 counts the omission as FN and both inventions (incl. on a gold off-topic paragraph) as FP",
              abs(pooled["element"]["micro_f1_unweighted"] - 0.4) < 1e-9, str(pooled["element"]["micro_f1_unweighted"]))
        # technology: gold {wind, solar}; pred {wind, nuclear, P3 solar} -> TP1 FP2 FN1 -> 0.4
        check("F1b technology detection likewise", abs(pooled["technology"]["micro_f1_unweighted"] - 0.4) < 1e-9)
        # score A: matched units = P1 wind costs (correct) -> accuracy 1 on 1 unit; excluded: solar row not predicted (1)
        A = pooled["evaluation_A"]
        check("F1c score A: accuracy 1 on its single retained unit, the omitted row's frame counted as excluded",
              A["accuracy_unweighted"] == 1.0 and A["units"] == 1 and A["excluded"].get("evaluation_A:row_not_predicted") == 1, str(A))
        # score B: tuples gold {(P1,wind,costs,neg),(P1,solar,climate,pos)}; pred {(P1,wind,costs,neg),(P1,nuclear,rel,neg),(P3,solar,jobs,pos)} -> TP1 FP2 FN1 -> 0.4
        check("F1d score B: end-to-end tuples give micro-F1 0.4 while A reports 1.0 -- both printed",
              abs(pooled["evaluation_B"]["micro_f1_unweighted"] - 0.4) < 1e-9)
        # displacement: technology:wind gold 1/3 pred 1/3 -> 0; technology:solar gold 1/3 (P1) pred 1/3 (P3) -> 0; nuclear 0 -> 1/3 = +33.3pp
        d = res["displacement"]["pooled"]
        check("F1e displacement counts paragraph presence once over ALL manifest paragraphs",
              abs(d["technology:nuclear"]["unweighted_pp"] - 100 / 3) < 1e-6 and abs(d["technology:solar"]["unweighted_pp"]) < 1e-9, str(d["technology:solar"]))
        check("F1f the class manifest holds only positive-support classes with row and article support",
              set(res["class_manifest"]["element"]["pooled"]) == {"F1a", "F3a"} and res["class_manifest"]["element"]["pooled"]["F1a"]["articles"] == 1)
        check("F1g attrition: gold off-topic 2, execution 1 outcomes as recorded",
              res["attrition"]["pooled"]["gold_off_topic"] == 2 and res["attrition"]["pooled"]["execution_1"]["technologies_present"] == 2)

        # ---- F2: weights: article drawn twice doubles contribution; suppression changes no aggregate ------- #
        paras = [para("Q1", "B1", "de", weight=1.0), para("Q2", "B2", "de", weight=3.0)]
        gold = {("Q1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"}),
                ("Q2", "wind"): dict(elements=("F1a", "F2a"), evals={"costs": "negative", "jobs": "positive"})}
        preds = {(1, "Q1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"}),
                 (1, "Q2", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})}   # misses F2a on the heavy paragraph
        fx = Fixture(tmp, "f2", paras, gold, preds, k=1)
        inp = ev.load_inputs(fx.args())
        r_lo = ev.evaluate(inp, threshold=1, B=20, seed=1, dev=False)
        r_hi = ev.evaluate(inp, threshold=99, B=20, seed=1, dev=False)
        el = r_lo["point"]["pooled"]["element"]
        # unweighted: TP2 FN1 -> 0.8 ; weighted: TP (1+3)=4, FN 3 -> 8/11
        check("F2 design weights multiply the counts: unweighted 0.800, weighted 0.727", abs(el["micro_f1_unweighted"] - 0.8) < 1e-9 and abs(el["micro_f1_weighted"] - 8 / 11) < 1e-9)
        S = ev.build(inp); cnt = ev.counts_execution1(S, S["pids"])
        check("F2b drawing article B2 twice doubles its contribution (F1 = 8/14 with multiplicity 2)",
              abs(ev.micro(cnt["per_art"], "element", {"B1": 1, "B2": 2}, True) - (2 * 7) / (2 * 7 + 6)) < 1e-9)
        check("F2c the blind threshold suppresses per-class cells but changes no aggregate",
              r_hi["point"]["pooled"]["element"]["micro_f1_weighted"] == el["micro_f1_weighted"]
              and r_hi["point"]["pooled"]["element"]["macro_f1_weighted"] == el["macro_f1_weighted"]
              and not any(v["reported"] for v in r_hi["point"]["pooled"]["element"]["per_class"].values())
              and all(v["reported"] for v in el["per_class"].values() if v["in_macro"]))
        check("F2d a class with zero support and zero predictions is undefined, not 0",
              ev.f1(0, 0, 0) is None and ev.macro_available({"a": None, "b": None}) == (None, 0))

        # ---- F3: stability -------------------------------------------------------------- #
        paras = [para("S1", "C1", "en"), para("S2", "C1", "en"), para("S3", "C2", "en")]
        gold = {("S1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})}
        preds = {(1, "S1", "wind"): dict(elements=("F1a", "F2a"), evals={"costs": "negative", "jobs": "positive"}),
                 (2, "S1", "wind"): dict(elements=("F2a", "F1a"), evals={"jobs": "positive", "costs": "negative"}),  # same, other order
                 (3, "S1", "solar"): dict(elements=("F3a",), evals={"climate": "positive"})}                         # wind absent, solar appears
        outcomes = {(1, "S2"): "off_topic", (2, "S2"): "off_topic", (3, "S2"): "off_topic",
                    (1, "S3"): "parse_failed", (2, "S3"): "off_topic", (3, "S3"): "error"}
        fx = Fixture(tmp, "f3", paras, gold, preds, k=3, outcomes=outcomes)
        inp = ev.load_inputs(fx.args()); res = ev.evaluate(inp, threshold=1, B=10, seed=1, dev=False)
        s1 = ev.stability_per_paragraph(ev.build(inp), "S1")
        # tech sets: {wind},{wind},{solar}: pairs (1,2) same, (1,3) diff, (2,3) diff -> 2/3
        check("F3 ordering of elements alone causes no instability; technology-set change 2/3",
              abs(s1["tech"] - 2 / 3) < 1e-9 and s1["n_pairs"] == 3, str(s1))
        # relation level: union {wind, solar}; per unit x pair: wind: (1,2) same, (1,3) present vs absent -> change, (2,3) change; solar: (1,2) absent-absent same, (1,3) change, (2,3) change -> any = 4/6
        check("F3b absent relations are values; 'any' is a paragraph-level pair indicator = 2/3, elements 4/6",
              abs(s1["any"] - 2 / 3) < 1e-9 and abs(s1["elements"] - 4 / 6) < 1e-9, f"{s1['any']} {s1['elements']}")
        s2 = ev.stability_per_paragraph(ev.build(inp), "S2")
        check("F3c all-off-topic successes: zero technology change, any = 0, no relation-level score, empty union flagged",
              s2["tech"] == 0.0 and s2["any"] == 0.0 and s2["elements"] is None and s2["empty_union"] and s2["n_pairs"] == 3)
        s3 = ev.stability_per_paragraph(ev.build(inp), "S3")
        check("F3d fewer than two successes: no pairs, failures counted", s3["n_pairs"] == 0 and s3["n_fail"] == 2 and s3["tech"] is None)
        st = res["stability"]["pooled"]
        check("F3e the population block excludes non-applicable paragraphs from relation-level means and reports coverage",
              st["elements"]["paragraphs"] == 1 and st["any"]["paragraphs"] == 2 and st["tech"]["paragraphs"] == 2 and st["paragraphs_without_pairs"] == 1 and st["empty_union"] == 1
              and abs(st["failure_rate"] - 2 / 9) < 1e-9, str(st))

        # ---- F4: reliability on aligned units ------------------------------------------ #
        frame_of = {e["id"]: e["frame"] for e in ev.ins.load_codebook()[0]["frame_elements"]}
        p1a = [p1row("R1", ["wind"], "D1"), p1row("R2", ["wind", "solar"], "D1"), p1row("R3", [], "D2")]
        p1b = [p1row("R1", ["wind"], "D1"), p1row("R2", ["wind"], "D1"), p1row("R3", ["solar"], "D2")]
        p2a = [p2row("R1", "wind", ("F1a",), {"costs": "negative"}), p2row("R2", "wind", ("F1a",), {"costs": "positive"}), p2row("R2", "solar", ("F3a",), {"climate": "positive"})]
        p2b = [p2row("R1", "wind", ("F1a",), {"costs": "negative"}), p2row("R2", "wind", ("F1a", "F2a"), {"costs": "negative", "jobs": "positive"}), p2row("R3", "solar", ("F3a",), {"climate": "positive"})]
        rel = ev.reliability(p1a, p2a, p1b, p2b, frame_of)
        check("F4 a technology one coder ticks and the other does not is a disagreement at level (i), never dropped",
              rel["technology"]["solar"]["n"] == 3 and abs(rel["technology"]["solar"]["raw"] - 1 / 3) < 1e-9 and rel["rows_only_a"] == 1 and rel["rows_only_b"] == 1)
        check("F4b elements are compared on rows both coders created (2 aligned rows)", rel["aligned_rows"] == 2 and rel["elements"]["F2a"]["n"] == 2)
        check("F4c zero variation -> alpha undefined; constant-but-opposite coders -> a defined disagreement",
              ev.krippendorff_nominal([(1, 1), (1, 1), (1, 1)])[0] is None and ev.krippendorff_nominal([(1, 1), (1, 1), (1, 1)])[1] == 1.0
              and rel["technology"]["wind"]["alpha"] == 1.0
              and ev.krippendorff_nominal([("a", "b"), ("a", "b")])[0] is not None and ev.krippendorff_nominal([("a", "b"), ("a", "b")])[0] < 0)
        check("F4d the evaluation mask uses rows where both coders fired the frame (costs: 2, jobs: 0)",
              rel["evaluation"]["costs"]["n"] == 2 and rel["evaluation"]["jobs"]["n"] == 0 and rel["evaluation"]["jobs"]["alpha"] is None)

        # ---- F7: inspection findings ------------------------------------------------------- #
        check("F7a per-class output lists the full inventory (17 elements) with support 0 outside the macro set",
              len(res["point"]["pooled"]["element"]["per_class"]) == 17
              and not res["point"]["pooled"]["element"]["per_class"]["F9"]["in_macro"]
              and res["point"]["pooled"]["element"]["per_class"]["F9"]["f1_weighted"] is None)
        check("F7b technology resample absence is measured, not defaulted to zero",
              "technology:wind" in res["resample_absence"]["pooled"])
        fx2 = Fixture(tmp, "f7", paras, gold, preds, k=3, outcomes=outcomes)
        # a manifest that is not the run's is refused; a wrong terminal identity set is refused
        other = json.loads(fx2.manifest.read_text()); other["paragraphs"][0]["weight"] = 100000.0
        del other["digest"]; body = json.dumps(other, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        other["digest"] = hashlib.sha256(body).hexdigest(); (tmp / "other.json").write_text(json.dumps(other))
        try:
            ev.load_inputs(fx2.args(manifest=str(tmp / "other.json"))); check("F7c a manifest that is not the run's is refused", False, "no error")
        except ev.EvalError as exc:
            check("F7c a manifest that is not the run's is refused", "not the manifest" in str(exc))
        tp = tmp / "runs" / "f7_terminal.csv"; lines = tp.read_text(encoding="utf-8-sig").splitlines()
        lines[2] = lines[1]           # duplicate execution 1, drop one execution
        tp.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        recj = json.loads(fx2.rec.read_text()); recj["files"]["f7_terminal.csv"]["sha256"] = sha(tp); fx2.rec.write_text(json.dumps(recj))
        try:
            ev.load_inputs(fx2.args()); check("F7d terminal records must be exactly one per (paragraph, execution)", False, "no error")
        except ev.EvalError as exc:
            check("F7d terminal records must be exactly one per (paragraph, execution)", "exactly one" in str(exc))
        try:
            ev.reliability(p1a[:2], p2a, p1b, p2b, frame_of, designated={"R1", "R2", "R3"})
            check("F7e reliability refuses a coder who did not cover the designated subsample", False, "no error")
        except ev.EvalError as exc:
            check("F7e reliability refuses a coder who did not cover the designated subsample", "designated" in str(exc))

        # ---- F5: eligibility -------------------------------------------------------------- #
        fx = Fixture(tmp, "f5", [para("U1", "E1", "de")], {("U1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})},
                     {(1, "U1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})}, k=1, reportable=False)
        try:
            ev.load_inputs(fx.args(dev=False)); check("F5 an unreportable run is refused without --dev", False, "no error")
        except ev.EvalError as exc:
            check("F5 an unreportable run is refused without --dev", "not reportable" in str(exc))
        fx_ok = Fixture(tmp, "f5ok", [para("U2", "E2", "de")], {("U2", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})},
                        {(1, "U2", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})}, k=1)
        try:
            ev.load_inputs(fx_ok.args(dev=False)); check("F5d --skip-provenance without --dev is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5d --skip-provenance without --dev is refused", "only accepted with --dev" in str(exc))
        try:
            ev.load_inputs(fx_ok.args(dev=False, skip_provenance=False)); check("F5e a reference without sessions.csv is refused (fails closed)", False, "no error")
        except ev.EvalError as exc:
            check("F5e a reference without sessions.csv is refused (fails closed)", "sessions.csv" in str(exc))
        # the frame handshake of a reportable evaluation (Codex W1 round 1, #13)
        fx_h = Fixture(tmp, "f5h", [para("U3", "E3", "de")], {("U3", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})},
                       {(1, "U3", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})}, k=1)
        frame_txt = fx_h.frame.read_text(); reg_txt = fx_h.registry.read_text()
        fr = json.loads(frame_txt); fr["n_eligible"] = 999; fx_h.frame.write_text(json.dumps(fr))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5g an edited frame manifest is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5g an edited frame manifest is refused", "does not match its digest" in str(exc), str(exc))
        fx_h.frame.write_text(frame_txt)
        rg = json.loads(reg_txt); rg["assignment_log"].append({"seq": 3, "kind": "assign", "h": "later"}); fx_h.registry.write_text(json.dumps(rg))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5h a registry whose head is not the freeze is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5h a registry whose head is not the freeze is refused", "not the terminal freeze" in str(exc), str(exc))
        rg = json.loads(reg_txt); rg["assignment_log"][-1]["h"] = "other-freeze"; fx_h.registry.write_text(json.dumps(rg))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5i a sample naming another freeze event is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5i a sample naming another freeze event is refused", "different freeze event" in str(exc), str(exc))
        fx_h.registry.write_text(reg_txt)
        # membership and design (Codex W1 round 2, #1)
        man_txt = fx_h.manifest.read_text(); rec_txt = fx_h.rec.read_text()
        def _reman(mutate):
            m = json.loads(man_txt); mutate(m); m.pop("digest"); m["digest"] = ev.canonical_digest(m)
            fx_h.manifest.write_text(json.dumps(m)); r = json.loads(rec_txt); r["manifest"]["sha256"] = m["digest"]; fx_h.rec.write_text(json.dumps(r))
        def _expect(name, needle):
            try:
                ev.load_inputs(fx_h.args(dev=False)); check(name, False, "no error")
            except ev.EvalError as exc:
                check(name, needle in str(exc), str(exc))
        _reman(lambda m: m["paragraphs"].__setitem__(0, {**m["paragraphs"][0], "digest": "other"}))
        _expect("F5l a paragraph whose identity is not in the frozen frame is refused", "not members of the frozen frame")
        _reman(lambda m: m["paragraphs"].__setitem__(0, {**m["paragraphs"][0], "weight": float("nan")}))
        _expect("F5m a NaN design weight is refused", "finite positive")
        _reman(lambda m: m["paragraphs"].__setitem__(0, {**m["paragraphs"][0], "N_h": 999}))
        _expect("F5n a stratum size that is not the frame's is refused", "frame stratum size")
        _reman(lambda m: m["paragraphs"].__setitem__(0, {**m["paragraphs"][0], "exposure": "other"}))
        _expect("F5o an altered exposure is refused (frozen metadata)", "frozen metadata")
        _reman(lambda m: m.pop("design"))
        _expect("F5p a manifest without its design block is refused", "design")
        _reman(lambda m: m["design"].__setitem__("target", {"xx": 1}))
        _expect("F5q a by_language that is not the registered target is refused", "registered target")
        fx_h.manifest.write_text(man_txt); fx_h.rec.write_text(rec_txt)
        other = tmp / "other_draw.json"; other.write_text(man_txt)
        _reman(lambda m: m.__setitem__("created", "another-day"))   # digest-changing, internally consistent
        a_s = fx_h.args(dev=False); ev.sc.SAMPLE_MANIFEST = other    # args() binds the fixture; rebind to the other draw
        try:
            ev.load_inputs(a_s); check("F5s a manifest that is not the registered sample file (another draw) is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5s a manifest that is not the registered sample file (another draw) is refused", "not the registered sample manifest" in str(exc), str(exc))
        ev.sc.SAMPLE_MANIFEST = fx_h.manifest
        fx_h.manifest.write_text(man_txt); fx_h.rec.write_text(rec_txt)
        try:
            ev.load_inputs(fx_h.args(dev=False, restrict_split="A1")); check("F5r a reportable evaluation with --restrict-split is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5r a reportable evaluation with --restrict-split is refused", "whole registered sample" in str(exc), str(exc))
        fx_h.manifest.write_text(man_txt); fx_h.rec.write_text(rec_txt)
        # the gate the record relies on (Codex W1 round 5, #2)
        gtxt = fx_h.gate.read_text()
        gd = json.loads(gtxt); gd["certified"] = False; fx_h.gate.write_text(json.dumps(gd))
        rd_ = json.loads(rec_txt); rd_["preflight"]["sha256"] = sha(fx_h.gate); rd_["preflight"]["gates"][0]["sha256"] = sha(fx_h.gate); fx_h.rec.write_text(json.dumps(rd_))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5t a reportable record whose gate artefact did not certify is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5t a reportable record whose gate artefact did not certify is refused", "did not certify" in str(exc), str(exc))
        fx_h.gate.write_text(gtxt); fx_h.rec.write_text(rec_txt)
        rd_ = json.loads(rec_txt); rd_["preflight"]["gates"][0]["artefact"] = str(tmp / "missing_gate.json"); fx_h.rec.write_text(json.dumps(rd_))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5u a reportable record naming a missing gate artefact is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5u a reportable record naming a missing gate artefact is refused", "missing or its digest differs" in str(exc), str(exc))
        fx_h.rec.write_text(rec_txt)
        gd = json.loads(gtxt); gd["models"] = {"sonnet": "eu.other"}; fx_h.gate.write_text(json.dumps(gd))
        rd_ = json.loads(rec_txt); rd_["preflight"]["sha256"] = sha(fx_h.gate); rd_["preflight"]["gates"][0]["sha256"] = sha(fx_h.gate); fx_h.rec.write_text(json.dumps(rd_))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5v a gate that certified another model is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5v a gate that certified another model is refused", "not this run's model" in str(exc), str(exc))
        fx_h.gate.write_text(gtxt); fx_h.rec.write_text(rec_txt)
        gd = json.loads(gtxt); gd["provenance"]["preflight_sha256"] = "other"; fx_h.gate.write_text(json.dumps(gd))
        rd_ = json.loads(rec_txt); rd_["preflight"]["sha256"] = sha(fx_h.gate); rd_["preflight"]["gates"][0]["sha256"] = sha(fx_h.gate); fx_h.rec.write_text(json.dumps(rd_))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5y a gate produced by other code than the run's contract names is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5y a gate produced by other code than the run's contract names is refused", "other code than the run's execution contract" in str(exc), str(exc))
        fx_h.gate.write_text(gtxt); fx_h.rec.write_text(rec_txt)
        # Codex round 8 #1: drop a gate from the summary and relabel -- the journal says otherwise
        jtxt = fx_h.calls.read_text()
        bad_gate = tmp / "bad_gate.json"; bad_gate.write_text(json.dumps({**json.loads(gtxt), "certified": False}))
        jl_ = [json.loads(l) for l in jtxt.splitlines()]
        jl_.insert(1, {"type": "gate", "run_id": "f5h", "artefact": str(bad_gate), "sha256": sha(bad_gate), "certified": False, "at": jl_[0]["at"]})
        fx_h.calls.write_text("".join(json.dumps(r) + "\n" for r in jl_))
        rd_ = json.loads(rec_txt); rd_["files"]["f5h_calls.jsonl"]["sha256"] = sha(fx_h.calls); fx_h.rec.write_text(json.dumps(rd_))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5z a summary that omits a journalled (failed) gate is refused by the journal reconciliation", False, "no error")
        except ev.EvalError as exc:
            check("F5z a summary that omits a journalled (failed) gate is refused by the journal reconciliation", "journal reconciliation" in str(exc), str(exc))
        fx_h.calls.write_text(jtxt); fx_h.rec.write_text(rec_txt)
        rd_ = json.loads(rec_txt); rd_.pop("tool_config_sha256"); rd_.pop("prompt"); fx_h.rec.write_text(json.dumps(rd_))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5w a reportable record without its instrument digests is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5w a reportable record without its instrument digests is refused", "no instrument digest" in str(exc), str(exc))
        fx_h.rec.write_text(rec_txt)
        # a label resolved under a gate that is not one of the run's journalled gates (Codex W1 round 7)
        tp_h = Path(json.loads(rec_txt)["files"]["f5h_terminal.csv"]["path"]); trows = list(csv.DictReader(tp_h.open(encoding="utf-8-sig"), delimiter=";"))
        trows[0]["gate"] = "0" * 64; write_csv(tp_h, trows, list(trows[0].keys()))
        rd_ = json.loads(rec_txt); rd_["files"]["f5h_terminal.csv"]["sha256"] = sha(tp_h); fx_h.rec.write_text(json.dumps(rd_))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5x a label resolved under a gate that is not a journalled certified gate is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5x a label resolved under a gate that is not a journalled certified gate is refused", "not one of the run's journalled" in str(exc), str(exc))
        trows[0]["gate"] = sha(fx_h.gate); write_csv(tp_h, trows, list(trows[0].keys()))
        rd_ = json.loads(rec_txt); rd_["files"]["f5h_terminal.csv"]["sha256"] = sha(tp_h); fx_h.rec.write_text(json.dumps(rd_)); rec_txt = fx_h.rec.read_text()
        rd_ = json.loads(rec_txt); rd_.pop("tool_config_sha256"); rd_.pop("prompt"); fx_h.rec.write_text(json.dumps(rd_))
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5w a reportable record without its instrument digests is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5w a reportable record without its instrument digests is refused", "no instrument digest" in str(exc), str(exc))
        fx_h.rec.write_text(rec_txt)
        try:
            ev.load_inputs(fx_h.args(dev=False)); check("F5j with the handshake intact the evaluation proceeds to the provenance gate", False, "no error")
        except ev.EvalError as exc:
            check("F5j with the handshake intact the evaluation proceeds to the provenance gate", "only accepted with --dev" in str(exc), str(exc))
        # per-execution figures carry integer counts that reproduce the F1 exactly, and the metrics bind their inputs
        fx8 = Fixture(tmp, "f8", paras, gold, preds, k=3, outcomes=outcomes)
        inp_pe = ev.load_inputs(fx8.args()); res_pe = ev.evaluate(inp_pe, threshold=1, B=5, seed=1, dev=True)
        pe = res_pe["per_execution"]["pooled"]
        ok_pe = True
        for r in ("1", "2", "3"):
            c = pe[r]["element_counts_unweighted"]; f1 = pe[r]["element_micro_f1_unweighted"]
            exact = (2 * c["tp"]) / (2 * c["tp"] + c["fp"] + c["fn"]) if (2 * c["tp"] + c["fp"] + c["fn"]) else None
            ok_pe = ok_pe and ((f1 is None and exact is None) or (f1 is not None and exact is not None and abs(f1 - exact) < 1e-12))
        check("F8 per-execution element counts reproduce the execution-specific micro-F1 exactly", ok_pe and pe["mean_element_micro_f1_unweighted"] is not None, str(pe))
        check("F8b the metrics name the run record files and the reference digests they were computed from",
              res_pe["inputs"]["run_record_files"] == json.loads(fx8.rec.read_text())["files"] and set(res_pe["inputs"]["reference"]) == {fx8.g1.name, fx8.g2.name})
        # the real frozen frame and registry hand-shake with each other (no sample yet, so only the frame side)
        ev.FRAME_MANIFEST = ev.ROOT / "tools/annotation/labels/A2_frame_manifest.json"; ev.REGISTRY_FILE = ev.ROOT / "tools/annotation/split-assignment.json"
        if ev.FRAME_MANIFEST.exists() and ev.REGISTRY_FILE.exists():
            frd = json.loads(ev.FRAME_MANIFEST.read_text()); head = json.loads(ev.REGISTRY_FILE.read_text())["assignment_log"][-1]
            check("F5k the real frame manifest recomputes and the real registry head is its freeze",
                  ev.canonical_digest(frd) == frd["digest"] and head.get("kind") == "a2_frozen" and head.get("audit_ref") == frd["digest"])
        # a refused write leaves existing artefacts untouched
        inp_ok = ev.load_inputs(fx_ok.args()); res_ok = ev.evaluate(inp_ok, threshold=1, B=5, seed=1, dev=True)
        out = tmp / "out"; cm = out / "cm.json"
        ev.write_tables(res_ok, out, cm)
        before = (out / "metrics.json").read_bytes()
        res_other = dict(res_ok); res_other["class_manifest"] = {"element": {"pooled": {"F9": {"rows": 1, "articles": 1}}}}
        try:
            ev.write_tables(res_other, out, cm); check("F5f a refused evaluation writes nothing", False, "no error")
        except ev.EvalError:
            check("F5f a refused evaluation writes nothing", (out / "metrics.json").read_bytes() == before)
        inp = ev.load_inputs(fx.args(dev=True)); check("F5b --dev admits it", inp["rec"]["run_id"] == "f5")
        (tmp / "runs" / "f5_predictions.csv").write_text("tampered")
        try:
            ev.load_inputs(fx.args(dev=True)); check("F5c a tampered predictions file is refused", False, "no error")
        except ev.EvalError as exc:
            check("F5c a tampered predictions file is refused", "digest" in str(exc))
        # gold completeness: pass-2 row without a pass-1 tick
        fx = Fixture(tmp, "f6", [para("V1", "G1", "de")], {("V1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})},
                     {(1, "V1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})}, k=1)
        rows = list(csv.DictReader(fx.g1.open(encoding="utf-8-sig"), delimiter=";")); rows[0]["tech_wind"] = "0"; rows[0]["tech_solar"] = "1"
        write_csv(fx.g1, rows, P1_COLS)
        try:
            ev.load_inputs(fx.args()); check("F6 pass-1 keys that differ from pass-2 rows are refused", False, "no error")
        except ev.EvalError as exc:
            check("F6 pass-1 keys that differ from pass-2 rows are refused", "differ" in str(exc))

        # ---- F8: L21/L26/L27 reconciliation and the threshold seal (Codex Loom round 1, #3/#4/#8/#9) ----- #
        check("F8a a macro over fewer than two defined classes is undefined (L21)",
              ev.macro_available({"a": 0.5}) == (None, 1) and ev.macro_available({"a": 0.5, "b": None}) == (None, 1)
              and ev.macro_available({"a": 0.5, "b": 1.0}) == (0.75, 2))
        fx8 = Fixture(tmp, "f8r", [para("W1", "H1", "de"), para("W2", "H2", "en")],
                      {("W1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"}),
                       ("W2", "solar"): dict(elements=("F1a", "F3a"), evals={"costs": "positive", "climate": "positive"})},
                      {(1, "W1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"}),
                       (1, "W2", "solar"): dict(elements=("F1a",), evals={"costs": "positive"})}, k=1)
        inp8 = ev.load_inputs(fx8.args()); res8 = ev.evaluate(inp8, threshold=2, B=10, seed=1, dev=True)
        de8 = res8["point"]["de"]["element"]
        check("F8b the common-support macro is computed per language over the classes supported in every language (L21)",
              "macro_common_support_weighted" in de8 and de8["common_support_classes"] == 1 and de8["macro_common_support_weighted"] is None
              and "macro_common_support_weighted" not in res8["point"]["pooled"]["element"])
        res8["reliability"] = ev.reliability(p1a, p2a, p1b, p2b, frame_of); res8["reliability"]["route"] = "intra-coder temporal stability (14-day washout)"
        out8 = tmp / "tables8"; ev.write_tables(res8, out8, tmp / "cm8.json")
        t2 = (out8 / "table2_per_class.csv").read_text(encoding="utf-8")
        check("F8c a class with zero support and zero predictions prints undefined, one below s prints n<s (L27)",
              any("F9" in ln and "undefined" in ln for ln in t2.splitlines() if ln.startswith("pooled"))
              and any("F3a" in ln and "n<2 (1)" in ln for ln in t2.splitlines() if ln.startswith("pooled")))
        t1 = (out8 / "table1_primary.csv").read_text(encoding="utf-8")
        check("F8d table 1 prints the common-support sensitivity rows for the language populations only",
              "common-support classes" in t1 and not any("common-support" in ln for ln in t1.splitlines() if ln.startswith("pooled")))
        t9 = (out8 / "table9_reliability.csv").read_text(encoding="utf-8")
        rel8 = res8["reliability"]
        check("F8e reliability support is the first coding's positives, the second coding's beside it",
              rel8["technology"]["solar"]["support_rows"] == 1 and rel8["technology"]["solar"]["support_rows_b"] == 1
              and rel8["elements"]["F2a"]["support_rows"] == 0 and rel8["elements"]["F2a"]["support_rows_b"] == 1)
        check("F8f the reliability table applies the sealed threshold and the three states (n<s / undefined / value)",
              any(ln.startswith("technology tick") and "solar" in ln and "n<2 (1)" in ln for ln in t9.splitlines())
              and any(ln.startswith("evaluation") and "jobs" in ln and "undefined" in ln for ln in t9.splitlines())   # no units: undefined, not n<s
              and any(ln.startswith("evaluation") and "costs" in ln and "n<2" not in ln and "undefined" not in ln for ln in t9.splitlines())
              and any(ln.startswith("technology tick") and "wind" in ln and "1.000" in ln for ln in t9.splitlines()))
        # the seal binds a reportable evaluation
        fx9 = Fixture(tmp, "f9r", [para("U9", "E9", "de")], {("U9", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})},
                      {(1, "U9", "wind"): dict(elements=("F1a",), evals={"costs": "negative"})}, k=1)
        a9 = fx9.args(dev=False)                      # writes the fixture seal with s=1
        seal_txt = fx9.seal.read_text()
        fx9.seal.unlink()
        try:
            ev.load_inputs(a9); check("F8g a reportable evaluation without a threshold seal is refused", False, "no error")
        except ev.EvalError as exc:
            check("F8g a reportable evaluation without a threshold seal is refused", "no threshold seal" in str(exc), str(exc))
        fx9.seal.write_text(seal_txt)
        try:
            ev.load_inputs(fx9.args(dev=False, threshold=7)); check("F8h a threshold that is not the sealed value is refused", False, "no error")
        except ev.EvalError as exc:
            check("F8h a threshold that is not the sealed value is refused", "not the sealed value" in str(exc), str(exc))
        sj = json.loads(seal_txt); sj["s"] = 7; fx9.seal.write_text(json.dumps(sj))       # edited without re-digesting
        try:
            ev.load_inputs(fx9.args(dev=False, threshold=7)); check("F8i an edited seal is refused as not intact", False, "no error")
        except ev.EvalError as exc:
            check("F8i an edited seal is refused as not intact", "not intact" in str(exc), str(exc))
        sj = json.loads(seal_txt); sj["sample_manifest_digest"] = "other"; sj.pop("digest"); sj["digest"] = ev.canonical_digest(sj); fx9.seal.write_text(json.dumps(sj))
        try:
            ev.load_inputs(fx9.args(dev=False)); check("F8j a seal written against another sample is refused", False, "no error")
        except ev.EvalError as exc:
            check("F8j a seal written against another sample is refused", "another sample" in str(exc), str(exc))
        fx9.seal.write_text(seal_txt)
        try:
            ev.sc.write_threshold_seal(3, "chosen blind", seal_path=fx9.seal, sample_path=fx9.manifest, frame_path=fx9.frame)
            check("F8k the seal is written once and never rewritten", False, "no error")
        except ev.sc.ContractError as exc:
            check("F8k the seal is written once and never rewritten", "already exists" in str(exc))
        try:
            ev.sc.write_threshold_seal(3, "five looks good", seal_path=tmp / "s_new.json", sample_path=fx9.manifest, frame_path=fx9.frame)
            check("F8l a confirmation that does not state blindness is refused", False, "no error")
        except ev.sc.ContractError as exc:
            check("F8l a confirmation that does not state blindness is refused", "blind" in str(exc))
        try:
            ev.sc.write_threshold_seal(0, "chosen blind", seal_path=tmp / "s_zero.json", sample_path=fx9.manifest, frame_path=fx9.frame)
            check("F8m a non-positive threshold is refused", False, "no error")
        except ev.sc.ContractError as exc:
            check("F8m a non-positive threshold is refused", "positive integer" in str(exc))
        check("F8n a dev evaluation over non-A2 identities needs no seal",
              ev.load_inputs(fx8.args()) is not None)
        # ---- round 2 ----------------------------------------------------------------------------- #
        check("F8o inside a resample the available-class macro averages what remains defined (L26), the point estimate needs two (L21)",
              ev.macro_available({"a": 0.5}, min_classes=1) == (0.5, 1) and ev.macro_available({"a": 0.5}) == (None, 1))
        fxc = Fixture(tmp, "f8c", [para("C1", "J1", "de"), para("C2", "J2", "de", exposure="sibling_calibration_article"),
                                   para("C3", "J3", "en"), para("C4", "J4", "en", exposure="sibling_calibration_article")],
                      {("C1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"}), ("C2", "wind"): dict(elements=("F3a",), evals={"climate": "positive"}),
                       ("C3", "solar"): dict(elements=("F1a", "F3a"), evals={"costs": "positive", "climate": "positive"}), ("C4", "solar"): dict(elements=("F3a",), evals={"climate": "negative"})},
                      {(1, "C1", "wind"): dict(elements=("F1a",), evals={"costs": "negative"}), (1, "C2", "wind"): dict(elements=("F3a",), evals={"climate": "positive"}),
                       (1, "C3", "solar"): dict(elements=("F1a",), evals={"costs": "positive"}), (1, "C4", "solar"): dict(elements=("F3a",), evals={"climate": "negative"})}, k=1)
        resc = ev.evaluate(ev.load_inputs(fxc.args()), threshold=1, B=5, seed=1, dev=True)
        cm = resc["class_manifest"]["element"]
        check("F8p the clean subsets get their own common-support set and pooled populations none",
              cm["common_support"] == ["F1a", "F3a"] and cm["common_support_clean"] == ["F1a"]
              and resc["point"]["clean_de"]["element"]["common_support_classes"] == 1
              and "macro_common_support_weighted" not in resc["point"]["clean_pooled"]["element"]
              and "macro_common_support_weighted" not in resc["point"]["pooled"]["element"])
        # seal schema and semantics
        sj = json.loads(seal_txt); sj["s"] = -1; sj.pop("digest"); sj["digest"] = ev.canonical_digest(sj); fx9.seal.write_text(json.dumps(sj))
        try:
            ev.load_inputs(fx9.args(dev=False, threshold=-1)); check("F8q a re-digested seal with an invalid value is refused (schema)", False, "no error")
        except ev.EvalError as exc:
            check("F8q a re-digested seal with an invalid value is refused (schema)", "schema" in str(exc), str(exc))
        sj = json.loads(seal_txt); sj["rule"] = "whatever"; sj.pop("digest"); sj["digest"] = ev.canonical_digest(sj); fx9.seal.write_text(json.dumps(sj))
        try:
            ev.load_inputs(fx9.args(dev=False)); check("F8q2 a seal stating another rule is refused (schema)", False, "no error")
        except ev.EvalError as exc:
            check("F8q2 a seal stating another rule is refused (schema)", "not the registered ones" in str(exc), str(exc))
        sj = json.loads(seal_txt); sj.pop("author_confirmation"); sj.pop("digest"); sj["digest"] = ev.canonical_digest(sj); fx9.seal.write_text(json.dumps(sj))
        try:
            ev.load_inputs(fx9.args(dev=False)); check("F8q3 an incomplete seal is refused (schema)", False, "no error")
        except ev.EvalError as exc:
            check("F8q3 an incomplete seal is refused (schema)", "incomplete" in str(exc), str(exc))
        fx9.seal.write_text(seal_txt)
        # the git commitment: a temporary repository stands in for the project root
        import subprocess as _sp
        repo = tmp / "repo"; (repo / "labels").mkdir(parents=True)
        _sp.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        _sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, check=True)
        seal_in_repo = repo / "labels" / "seal.json"; seal_in_repo.write_text(seal_txt)
        root_before, req_before = ev.sc.ROOT, ev.sc.REQUIRE_SEAL_COMMIT
        try:
            ev.sc.ROOT, ev.sc.REQUIRE_SEAL_COMMIT = repo, True
            man9 = json.loads(fx9.manifest.read_text())
            try:
                ev.sc.verify_threshold_seal(1, man9, seal_path=seal_in_repo); check("F8r an uncommitted seal is refused", False, "no error")
            except ev.sc.ContractError as exc:
                check("F8r an uncommitted seal is refused", "not committed" in str(exc), str(exc))
            _sp.run(["git", "add", "labels/seal.json"], cwd=repo, check=True)
            _sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seal"], cwd=repo, check=True)
            info = ev.sc.verify_threshold_seal(1, man9, seal_path=seal_in_repo)
            check("F8r2 a committed, unchanged seal passes and its commit is returned", bool(info["commitment"]["commit"]) and info["commitment"]["committed_at"])
            try:
                ev.sc.verify_threshold_seal(1, man9, seal_path=seal_in_repo, earliest_coding="2020-01-01")
                check("F8r3 a seal committed after the first coded reference row is refused", False, "no error")
            except ev.sc.ContractError as exc:
                check("F8r3 a seal committed after the first coded reference row is refused", "not sealed blind" in str(exc), str(exc))
            check("F8r4 a seal committed before the first coded row passes the chronology check",
                  ev.sc.verify_threshold_seal(1, man9, seal_path=seal_in_repo, earliest_coding="2099-01-01")["s"] == 1)
            import datetime as _dt
            today = _dt.date.today().isoformat()
            try:
                ev.sc.verify_threshold_seal(1, man9, seal_path=seal_in_repo, earliest_coding=today)
                check("F8r6 a seal committed on the day of the first coding is not blind (strictly earlier day required)", False, "no error")
            except ev.sc.ContractError as exc:
                check("F8r6 a seal committed on the day of the first coding is not blind (strictly earlier day required)", "not on a day before" in str(exc), str(exc))
            sj = json.loads(seal_txt); sj["created_at"] = "2099-01-01T00:00:00+00:00"; sj.pop("digest"); sj["digest"] = ev.canonical_digest(sj)
            late = repo / "labels" / "late.json"; late.write_text(json.dumps(sj))
            _sp.run(["git", "add", "labels/late.json"], cwd=repo, check=True)
            _sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "late"], cwd=repo, check=True)
            try:
                ev.sc.verify_threshold_seal(1, man9, seal_path=late); check("F8r7 a seal whose created_at postdates its commit is refused", False, "no error")
            except ev.sc.ContractError as exc:
                check("F8r7 a seal whose created_at postdates its commit is refused", "postdates its first commit" in str(exc), str(exc))
            sj = json.loads(seal_txt); sj["created_at"] = "2026-01-01T00:00:00+00:00"; sj.pop("digest"); sj["digest"] = ev.canonical_digest(sj)
            seal_in_repo.write_text(json.dumps(sj))
            try:
                ev.sc.verify_threshold_seal(1, man9, seal_path=seal_in_repo); check("F8r5 a seal rewritten after its first commit is refused", False, "no error")
            except ev.sc.ContractError as exc:
                check("F8r5 a seal rewritten after its first commit is refused", "rewritten" in str(exc), str(exc))
            # the writer refuses to seal against uncommitted inputs
            reg_before = ev.sc.REGISTRY_FILE
            try:
                (repo / "labels" / "sample.json").write_text(fx9.manifest.read_text()); (repo / "labels" / "frame.json").write_text(fx9.frame.read_text())
                (repo / "labels" / "registry.json").write_text(fx9.registry.read_text())
                (repo / "labels" / "sample_contract.py").write_text("# stand-in for the module inside the temporary repository\n")
                ev.sc.REGISTRY_FILE = repo / "labels" / "registry.json"
                file_before = ev.sc.__file__; ev.sc.__file__ = str(repo / "labels" / "sample_contract.py")
                try:
                    ev.sc.write_threshold_seal(1, "chosen blind", seal_path=repo / "labels" / "s2.json",
                                               sample_path=repo / "labels" / "sample.json", frame_path=repo / "labels" / "frame.json")
                    check("F8x sealing against uncommitted inputs is refused", False, "no error")
                except ev.sc.ContractError as exc:
                    check("F8x sealing against uncommitted inputs is refused", "untracked or modified" in str(exc) and "sample.json" in str(exc), str(exc))
            finally:
                ev.sc.REGISTRY_FILE = reg_before; ev.sc.__file__ = file_before
        finally:
            ev.sc.ROOT, ev.sc.REQUIRE_SEAL_COMMIT = root_before, req_before
        # development mode over registered A2 identities is bound to the seal as well
        try:
            ev.load_inputs(fx9.args(dev=True, threshold=7)); check("F8s a development evaluation over registered A2 paragraphs needs the sealed threshold", False, "no error")
        except ev.EvalError as exc:
            check("F8s a development evaluation over registered A2 paragraphs needs the sealed threshold", "registered A2 paragraphs" in str(exc) and "sealed value" in str(exc), str(exc))
        # reliability inputs (route B): the reference itself, a separate re-code, the washout
        import argparse as _ap
        inp9 = ev.load_inputs(fx9.args())
        r1 = ev.read_csv(fx9.g1); r2 = ev.read_csv(fx9.g2)
        def _dated(rows, d):
            return [{**r, "coded_at": d} for r in rows]
        rb1 = tmp / "A2R_pass1_v1.0.csv"; rb2 = tmp / "A2R_pass2_v1.0.csv"      # coding B named like a finalised re-code session
        write_csv(rb1, _dated(r1, "2026-10-20"), list(r1[0].keys())); write_csv(rb2, _dated(r2, "2026-10-20"), list(r2[0].keys()))
        (tmp / "A2R_manifest.json").write_text(json.dumps({"session": "A2R", "recode": {"of_session": "A2", "route": "B", "seed_order": 20261005, "washout_days": 14}}))
        ra1 = tmp / "A2_pass1_v1.0.csv"; ra2 = tmp / "A2_pass2_v1.0.csv"          # coding A named like the reference session
        write_csv(ra1, _dated(r1, "2026-09-16"), list(r1[0].keys())); write_csv(ra2, _dated(r2, "2026-09-16"), list(r2[0].keys()))
        ns = _ap.Namespace(dev=True, reliability_route="B", gold_pass1=str(ra1), gold_pass2=str(ra2))
        try:
            ev.bind_reliability_inputs(ns, inp9, [str(fx9.g1), str(fx9.g2), str(rb1), str(rb2)], [r1, r2, ev.read_csv(rb1), ev.read_csv(rb2)])
            check("F8t route B refuses a coding A that is not the reference files", False, "no error")
        except ev.EvalError as exc:
            check("F8t route B refuses a coding A that is not the reference files", "reference label files" in str(exc), str(exc))
        try:
            ev.bind_reliability_inputs(ns, inp9, [str(ra1), str(ra2), str(ra1), str(ra2)], [ev.read_csv(ra1), ev.read_csv(ra2), ev.read_csv(ra1), ev.read_csv(ra2)])
            check("F8t2 route B refuses the reference supplied twice as its own re-code", False, "no error")
        except ev.EvalError as exc:
            check("F8t2 route B refuses the reference supplied twice as its own re-code (no registered re-code session)",
                  "separate session" in str(exc) or "no `recode` block" in str(exc), str(exc))
        early1 = tmp / "early" / "A2R_pass1_v1.0.csv"; early2 = tmp / "early" / "A2R_pass2_v1.0.csv"; early1.parent.mkdir()
        write_csv(early1, _dated(r1, "2026-09-20"), list(r1[0].keys())); write_csv(early2, _dated(r2, "2026-09-20"), list(r2[0].keys()))
        (tmp / "early" / "A2R_manifest.json").write_text((tmp / "A2R_manifest.json").read_text())
        try:
            ev.bind_reliability_inputs(ns, inp9, [str(ra1), str(ra2), str(early1), str(early2)], [ev.read_csv(ra1), ev.read_csv(ra2), ev.read_csv(early1), ev.read_csv(early2)])
            check("F8t3 route B refuses a re-code inside the 14-day washout", False, "no error")
        except ev.EvalError as exc:
            check("F8t3 route B refuses a re-code inside the 14-day washout", "washout" in str(exc), str(exc))
        prov = ev.bind_reliability_inputs(ns, inp9, [str(ra1), str(ra2), str(rb1), str(rb2)], [ev.read_csv(ra1), ev.read_csv(ra2), ev.read_csv(rb1), ev.read_csv(rb2)])
        check("F8t4 a valid route-B re-code returns its provenance (digests, dates, washout days, coding-B session)",
              prov["washout_days"] == 34 and set(prov["files"]) == {"pass1_a", "pass2_a", "pass1_b", "pass2_b"} and prov["coding_b"]["first"] == "2026-10-20"
              and prov["coding_b_session"]["recode"]["route"] == "B")
        nsA = _ap.Namespace(dev=True, reliability_route="A", gold_pass1=str(ra1), gold_pass2=str(ra2))
        try:
            ev.bind_reliability_inputs(nsA, inp9, [str(ra1), str(ra2), str(rb1), str(rb2)], [ev.read_csv(ra1), ev.read_csv(ra2), ev.read_csv(rb1), ev.read_csv(rb2)])
            check("F8t5 a registered re-code session cannot be evaluated under route A (washout bypass)", False, "no error")
        except ev.EvalError as exc:
            check("F8t5 a registered re-code session cannot be evaluated under route A (washout bypass)", "cannot be evaluated under route A" in str(exc), str(exc))
        # a shortened washout is accepted only as a disclosed deviation: explicit value AND a written note, both recorded
        try:
            ev.bind_reliability_inputs(ns, inp9, [str(ra1), str(ra2), str(early1), str(early2)], [ev.read_csv(ra1), ev.read_csv(ra2), ev.read_csv(early1), ev.read_csv(early2)], washout_days=4)
            check("F8t3b a shortened washout without a deviation note is refused", False, "no error")
        except ev.EvalError as exc:
            check("F8t3b a shortened washout without a deviation note is refused", "washout-deviation" in str(exc), str(exc))
        prov6 = ev.bind_reliability_inputs(ns, inp9, [str(ra1), str(ra2), str(early1), str(early2)], [ev.read_csv(ra1), ev.read_csv(ra2), ev.read_csv(early1), ev.read_csv(early2)], washout_days=4, deviation="author's decision, test")
        check("F8t3c a shortened washout with a note is accepted and records registered, applied and the note",
              prov6["washout_days"] == 4 and prov6["washout_days_registered"] == 14 and prov6["washout_days_applied"] == 4 and prov6["washout_deviation"] == "author's decision, test", str(prov6.get("washout_days")))
        plain1 = tmp / "plain" / "X_pass1_v1.0.csv"; plain2 = tmp / "plain" / "X_pass2_v1.0.csv"; plain1.parent.mkdir()
        write_csv(plain1, _dated(r1, "2026-10-20"), list(r1[0].keys())); write_csv(plain2, _dated(r2, "2026-10-20"), list(r2[0].keys()))
        try:
            ev.bind_reliability_inputs(ns, inp9, [str(ra1), str(ra2), str(plain1), str(plain2)], [ev.read_csv(ra1), ev.read_csv(ra2), ev.read_csv(plain1), ev.read_csv(plain2)])
            check("F8t6 route B refuses a coding B that is not a registered re-code session", False, "no error")
        except ev.EvalError as exc:
            check("F8t6 route B refuses a coding B that is not a registered re-code session", "no `recode` block" in str(exc), str(exc))
        # provenance through the finalisation records (non-dev): sessions by digest, manifest from the labels dir
        lab = tmp / "labels"; lab.mkdir()
        def _w(name, rows_, d):
            pth = lab / name; write_csv(pth, _dated(rows_, d), list(rows_[0].keys())); return pth
        A1p = _w("A2_pass1_v1.0.csv", r1, "2026-09-16"); A2p = _w("A2_pass2_v1.0.csv", r2, "20.09.26")        # two-digit year, as finalize accepts
        B1p = _w("A2R_pass1_v1.0.csv", r1, "2026-10-05"); B2p = _w("A2R_pass2_v1.0.csv", r2, "2026-10-05")
        X2p = _w("X_pass2_v1.0.csv", r2, "2026-10-06")          # distinct content, so its digest identifies another session
        early_b = _w("A2Rearly_pass1_v1.0.csv", r1, "30.09.26")
        import hashlib as _h
        def _d(p): return _h.sha256(p.read_bytes()).hexdigest()[:16]
        (lab / "sessions.csv").write_text("session;codebook_version;n_rows;annotators;coded_at;sittings;labels_file;labels_sha256;source_sheet;finalized_on\n"
            + "".join(f"{s_};1.0;1;NW;d;1;{p_.name};{_d(p_)};s;2026-10-06\n" for s_, p_ in (("A2_pass1", A1p), ("A2_pass2", A2p), ("A2R_pass1", B1p), ("A2R_pass2", B2p), ("X_pass2", X2p), ("A2Rearly_pass1", early_b))), encoding="utf-8")
        (lab / "A2_manifest.json").write_text(json.dumps({"session": "A2"}))
        (lab / "A2R_manifest.json").write_text(json.dumps({"session": "A2R", "recode": {"of_session": "A2", "route": "B", "seed_order": 20261005, "washout_days": 14}}))
        (lab / "A2Rearly_manifest.json").write_text(json.dumps({"session": "A2Rearly", "recode": {"of_session": "A2", "route": "B"}}))
        nsB = _ap.Namespace(dev=False, reliability_route="B", gold_pass1=str(A1p), gold_pass2=str(A2p))
        nsA2 = _ap.Namespace(dev=False, reliability_route="A", gold_pass1=str(A1p), gold_pass2=str(A2p))
        provB = ev.bind_reliability_inputs(nsB, inp9, [str(A1p), str(A2p), str(B1p), str(B2p)], [ev.read_csv(A1p), ev.read_csv(A2p), ev.read_csv(B1p), ev.read_csv(B2p)])
        check("F8t7 outside development the sessions come from sessions.csv by digest, and a two-digit year is read as finalize reads it",
              provB["coding_b_session"]["base"] == "A2R" and provB["coding_a"]["last"] == "2026-09-20" and provB["washout_days"] == 15)
        stray = tmp / "stray"; stray.mkdir(); (stray / "B1.csv").write_bytes(B1p.read_bytes())
        try:
            ev.bind_reliability_inputs(nsA2, inp9, [str(A1p), str(A2p), str(stray / "B1.csv"), str(B2p)], [ev.read_csv(A1p), ev.read_csv(A2p), ev.read_csv(B1p), ev.read_csv(B2p)])
            check("F8t8 a re-code file copied elsewhere without its manifest is still identified by digest and refused under route A", False, "no error")
        except ev.EvalError as exc:
            check("F8t8 a re-code file copied elsewhere without its manifest is still identified by digest and refused under route A", "cannot be evaluated under route A" in str(exc), str(exc))
        try:
            ev.bind_reliability_inputs(nsB, inp9, [str(A1p), str(A2p), str(B1p), str(X2p)], [ev.read_csv(A1p), ev.read_csv(A2p), ev.read_csv(B1p), ev.read_csv(X2p)])
            check("F8t9 two coding-B files from different finalised sessions are refused", False, "no error")
        except ev.EvalError as exc:
            check("F8t9 two coding-B files from different finalised sessions are refused", "different sessions" in str(exc), str(exc))
        E2p = _w("A2Rearly_pass2_v1.0.csv", r2, "30.09.26")
        (lab / "sessions.csv").write_text((lab / "sessions.csv").read_text() + f"A2Rearly_pass2;1.0;1;NW;d;1;{E2p.name};{_d(E2p)};s;2026-10-06\n", encoding="utf-8")
        try:
            ev.bind_reliability_inputs(nsB, inp9, [str(A1p), str(A2p), str(early_b), str(E2p)], [ev.read_csv(A1p), ev.read_csv(A2p), ev.read_csv(early_b), ev.read_csv(E2p)])
            check("F8t10 a re-code dated with a two-digit year ten days after pass 2 is refused (no year-26 misreading)", False, "no error")
        except ev.EvalError as exc:
            check("F8t10 a re-code dated with a two-digit year ten days after pass 2 is refused (no year-26 misreading)", "washout" in str(exc), str(exc))
        check("F8u the reliability technology level lists the whole frozen inventory",
              set(ev.inventories()["technology"]) <= set(rel["technology"]))
        check("F8v the evaluation provenance carries the seal when one was verified",
              inp9["provenance"].get("threshold_seal", {}).get("s") == 1)
        # the coding window is read over the whole registered history, never over the restricted rows
        hist = tmp / "hist_pass1.csv"
        write_csv(hist, [{**r1[0], "paragraph_id": "A2X_p01", "coded_at": "2026-09-12"}, {**r1[0], "paragraph_id": "A2X_p02", "coded_at": "2026-09-10"},
                         {**r1[0], "paragraph_id": "OTHER_p01", "coded_at": "2026-01-01"}], list(r1[0].keys()))
        check("F8w the earliest coding date is the earliest over every registered A2 row, not the evaluated subset",
              ev.earliest_registered_coding(hist, {"A2X_p01", "A2X_p02"}) == "2026-09-10")
        write_csv(hist, [{**r1[0], "paragraph_id": "A2X_p01", "coded_at": "2026-09-12"}, {**r1[0], "paragraph_id": "A2X_p02", "coded_at": ""}], list(r1[0].keys()))
        try:
            ev.earliest_registered_coding(hist, {"A2X_p01", "A2X_p02"}); check("F8w2 a registered A2 row without a coding date refuses the chronology check", False, "no error")
        except ev.EvalError as exc:
            check("F8w2 a registered A2 row without a coding date refuses the chronology check", "no coded_at" in str(exc), str(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    alpha_bootstrap_tests()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for n, d in FAILED:
            print(f"  FAILED: {n}  {d}")
        sys.exit(1)


def alpha_bootstrap_tests() -> None:
    """§4n (2) (consensus G-01): the aggregated alpha of review_analyses equals krippendorff_nominal on explicitly
    replicated units -- repeated articles, unequal cluster sizes, absent items, undefined alpha."""
    import random as _r
    from collections import Counter as _C
    import review_analyses as ra
    rng = _r.Random(7)
    arts = {f"A{i}": [(rng.randint(0, 1), rng.randint(0, 1)) for _ in range(rng.randint(1, 6))] for i in range(9)}
    arts["A9"] = []                                                     # an article without units for this item
    mult = _C({"A0": 3, "A1": 1, "A4": 2, "A9": 2, "A7": 1})          # repeated and absent articles
    expl = [u for a, k in mult.items() for _ in range(k) for u in arts[a]]
    N = len(expl); D = sum(int(x != y) for x, y in expl); vals = _C()
    for x, y in expl:
        vals[x] += 1; vals[y] += 1
    want = ev.krippendorff_nominal(expl)[0]
    got = ra._alpha_from(N, D, vals)
    check("A-B1 aggregated alpha equals krippendorff_nominal on replicated units (repeated, unequal, absent articles)",
          want is not None and got is not None and abs(want - got) < 1e-12, f"{want} vs {got}")
    check("A-B2 alpha without variation is undefined in both computations",
          ra._alpha_from(3, 0, _C({1: 6})) is None and ev.krippendorff_nominal([(1, 1)] * 3)[0] is None)
    check("A-B3 no units give no alpha", ra._alpha_from(0, 0, _C()) is None)
    # alpha_intervals on a toy population: point value reproduced, undefined draws counted, draws reproducible
    class R_:
        def __init__(s, el): s.elements = frozenset(el); s.evals = (); s.target = "none"
    S = {"art": {f"P{i}": f"A{i % 4}" for i in range(12)}}
    pids = sorted(S["art"])
    rA = {(p_, "wind"): R_({"F1a"} if i % 3 == 0 else set()) for i, p_ in enumerate(pids)}
    rB = {(p_, "wind"): R_({"F1a"} if i % 3 == 0 or i == 4 else set()) for i, p_ in enumerate(pids)}
    a1 = ra.alpha_intervals(S, pids, rA, rB); a2 = ra.alpha_intervals(S, pids, rA, rB)
    x = a1["items"]["elements|F1a"]
    pairs = [(int("F1a" in rA[k].elements), int("F1a" in rB[k].elements)) for k in sorted(rA)]
    check("A-B4 alpha_intervals reproduces the point alpha and is deterministic under the fixed seed",
          abs(x["alpha"] - ev.krippendorff_nominal(pairs)[0]) < 1e-12 and a1["items"] == a2["items"])
    check("A-B5 undefined count is non-negative and q lies in [0, 1]",
          x["undefined"] >= 0 and all(v is None or 0 <= v <= 1 for v in x["q"].values()))
    y = a1["items"]["elements|F2a"]                                     # never marked: undefined throughout
    check("A-B6 an item without variation is undefined, with every draw counted as undefined",
          y["alpha"] is None and y["undefined"] == ra.B and y["ci"] == (None, None))
    # coherence round C1 (H-12): an independent explicit-resampling reference -- the same article draws, every unit
    # replicated explicitly, alpha from krippendorff_nominal; unequal clusters and an item undefined in some draws
    S2 = {"art": {"P0": "A0", "P1": "A0", "P2": "A0", "P3": "A1", "P4": "A2", "P5": "A2", "P6": "A3", "P7": "A4"}}
    p2 = sorted(S2["art"])
    mark_a = {"P0", "P3"}; mark_b = {"P0", "P4"}                         # F1a varies only in A0, A1, A2
    rA2 = {(q, "wind"): R_({"F1a"} if q in mark_a else set()) for q in p2}
    rB2 = {(q, "wind"): R_({"F1a"} if q in mark_b else set()) for q in p2}
    got = ra.alpha_intervals(S2, p2, rA2, rB2)["items"]["elements|F1a"]
    arts2 = sorted(set(S2["art"].values()))
    by_art = {a: [(int(q in mark_a), int(q in mark_b)) for q in p2 if S2["art"][q] == a] for a in arts2}
    rng2 = _r.Random(ra.SEED); draws = []; und = 0
    for _ in range(ra.B):
        mu = _C(rng2.choice(arts2) for _ in arts2)
        al = ev.krippendorff_nominal([u for a, k in mu.items() for _ in range(k) for u in by_art[a]])[0]
        if al is None:
            und += 1
        else:
            draws.append(al)
    draws.sort(); qf = lambda pp: draws[int(round(pp * (len(draws) - 1)))]
    ref_q = {f"{am:.3f}": sum(1 for v in draws if v < am) / len(draws) for am in ra.A_MIN}
    check("A-B7 intervals, q and undefined counts equal an explicit-resampling reference (unequal clusters, partly undefined)",
          und > 0 and und == got["undefined"] and len(draws) + und == ra.B
          and abs(got["ci"][0] - qf(0.025)) < 1e-12 and abs(got["ci"][1] - qf(0.975)) < 1e-12
          and all(abs(got["q"][k] - v) < 1e-12 for k, v in ref_q.items()), f"{got['undefined']} vs {und}; {got['ci']}")
    # _interval_n: a contrast undefined when a required component is missing from the draw
    fn = lambda mu: (1.0 if mu.get("A1") else None)
    n_ = ra._interval_n(fn, arts2)
    rng3 = _r.Random(ra.SEED); defined = sum(1 for _ in range(ra.B) if _C(rng3.choice(arts2) for _ in arts2).get("A1"))
    check("A-B8 _interval_n counts defined and undefined draws exactly as an independent count",
          n_["defined"] == defined and n_["undefined"] == ra.B - defined and n_["iv"] == (1.0, 1.0))
    # element_direction cells against direct weighted counts on a toy population (one cell mutated in a copy)
    class E_:
        def __init__(s, el): s.elements = frozenset(el); s.evals = (); s.target = "none"
    S3 = {"art": {f"Q{i}": f"B{i % 3}" for i in range(10)}, "w": {f"Q{i}": 1.0 + i % 2 for i in range(10)}}
    q3 = sorted(S3["art"]); w40, w80 = q3[:4], q3[4:]
    first = {(q, "solar"): E_({"F2b"} if i % 2 == 0 else {"F2b", "F2c"}) for i, q in enumerate(q3)}
    second = {(q, "solar"): E_({"F2b"} if i % 3 else set()) for i, q in enumerate(q3)}
    model = {(q, "solar"): E_({"F2b"} if i % 4 else {"F2c"}) for i, q in enumerate(q3)}
    S3["pred"] = {1: model}
    D = ra.element_direction(S3, w40, w80, first, second)["elements"]
    def direct(gold, e):
        tp = sum(S3["w"][k[0]] for k in gold if e in gold[k].elements and e in model[k].elements)
        fp = sum(S3["w"][k[0]] for k in model if e in model[k].elements and e not in gold[k].elements)
        fn_ = sum(S3["w"][k[0]] for k in gold if e in gold[k].elements and e not in model[k].elements)
        return (tp / (tp + fp) if tp + fp else None, tp / (tp + fn_) if tp + fn_ else None)
    ok = all(abs(D["F2b"][who]["precision"] - direct(g, "F2b")[0]) < 1e-12 and abs(D["F2b"][who]["recall"] - direct(g, "F2b")[1]) < 1e-12
             for who, g in (("first", first), ("second", second)))
    second_m = dict(second); second_m[(q3[1], "solar")] = E_({"F2b"}) if not second[(q3[1], "solar")].elements else E_(set())
    Dm = ra.element_direction(S3, w40, w80, first, second_m)["elements"]
    check("A-B9 element_direction reproduces direct weighted per-element counts and reacts to a one-cell mutation",
          ok and (Dm["F2b"]["second"]["recall"] != D["F2b"]["second"]["recall"]
                  or Dm["F2b"]["second"]["precision"] != D["F2b"]["second"]["precision"]))


if __name__ == "__main__":
    main()
