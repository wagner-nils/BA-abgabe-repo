#!/usr/bin/env python3
"""Contract tests for the M4 runner, driven by a fake client. tools/docs/PIPELINE-COMPLETION-PLAN.md
Step 2 and the round-4 acceptance list.

    .venv/bin/python3 tools/pipeline/test_run_extract.py

Every path the runner writes to is redirected into a temp dir; the pinned instrument and
the frozen codebook are the real ones, the paragraph texts are synthetic.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instrument as ins                                        # noqa: E402
import run_extract as rx                                        # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"\n          {detail}" if not cond and detail else ""))


QUOTE = "Der Ausbau der Windkraft an Land stockt und die Anwohner klagen"
TEXTS = {
    "T001_p01": f"{QUOTE}, während Genehmigungsverfahren im Schnitt fast zwei Jahre dauern und Netzbetreiber warnen.",
    "T001_p02": "Solar power reached a record 22 percent of the electricity mix last month, up a fifth on the year, the agency said.",
    "T002_p01": "The council met on Tuesday evening and adjourned without a vote on the community centre budget.",
}


def h16(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


def usage(inp=400, out=60, read=0, write=0):
    return {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": read,
            "cacheWriteInputTokens": write}


def resp(tool_input=None, *, stop="tool_use", u=None, content=None):
    blocks = content if content is not None else [
        {"toolUse": {"toolUseId": "tu_fixture", "name": ins.TOOL_NAME, "input": tool_input}}]   # a real response carries an id
    return {"stopReason": stop, "usage": u or usage(), "output": {"message": {"content": blocks}}}


OFF = {"outcome": "off_topic", "relations": []}
WIND = {"outcome": "technologies_present", "relations": [
    {"technology": "wind", "wind_siting": "onshore", "elements": ["F8a", "F7b"],
     "evaluations": {"regulation": "negative", "siting": "negative"}, "comparison_target": "none",
     "note": f"quoted: {QUOTE}"}]}
SOLAR_TWO = {"outcome": "technologies_present", "relations": [
    {"technology": "solar", "elements": ["F2c"], "evaluations": {"jobs": "positive"},
     "comparison_target": "none"},
    {"technology": "general", "elements": [], "evaluations": {}, "comparison_target": "none"}]}
BAD_SCHEMA = {"outcome": "off_topic"}                                # relations missing
BAD_CONTRACT = {"outcome": "off_topic", "relations": [
    {"technology": "solar", "elements": [], "evaluations": {}, "comparison_target": "none"}]}


class Throttle(Exception):
    def __init__(self):
        super().__init__("Rate exceeded")
        self.response = {"Error": {"Code": "ThrottlingException"}}


class Fake:
    def __init__(self, plan):
        self.plan, self.seen = list(plan), []

    def converse(self, **kw):
        self.seen.append(kw)
        item = self.plan.pop(0) if self.plan else RuntimeError("fake client exhausted")
        if isinstance(item, BaseException):
            raise item
        return item


def gate_plan(certify=True):
    """preflight for one model: reachability + two byte-identical production calls."""
    if certify:
        return [resp(OFF), resp(OFF, u=usage(write=2000)), resp(OFF, u=usage(read=2000))]
    return [resp(OFF), resp(OFF, u=usage()), resp(OFF, u=usage())]     # neither writes nor reads


def setup(tmp: Path, pids: list[str]) -> Path:
    rx.RUNS = tmp / "runs"; rx.RAW = rx.RUNS / "raw"
    rx.ALL_PARAGRAPHS = tmp / "ALL_paragraphs.csv"
    with rx.ALL_PARAGRAPHS.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["paragraph_id", "split", "article_id", "text_status", "lang", "outlet", "country",
                    "pub_date", "n_words", "text_sha256", "has_transcription_gap", "headline", "paragraph_text"])
        for pid, t in TEXTS.items():
            w.writerow([pid, "A1", pid.split("_")[0], "full", "de" if pid.startswith("T001_p01") else "en",
                        "taz", "DE", "2026-01-01", len(t.split()), h16(t), "", "H", t])
    from collections import Counter as _C
    paras = [{"paragraph_id": p, "article_id": p.split("_")[0], "digest": h16(TEXTS[p]), "text_sha256": h16(TEXTS[p]),
              "lang": "de" if p == "T001_p01" else "en", "outlet": "taz", "exposure": "clean_article", "split": "A1"} for p in pids]
    sizes = _C((q["lang"], q["outlet"]) for q in paras)
    # the frame holds one undrawn filler per stratum besides the drawn paragraphs, so N_h = n_h + 1
    fillers = [{"paragraph_id": f"FILL_{L}_{O}", "article_id": f"FILL_{L}", "digest": f"d-fill-{L}-{O}", "text_sha256": f"t-fill-{L}-{O}",
                "lang": L, "outlet": O, "exposure": "clean_article"} for (L, O) in sorted(sizes)]
    for q in paras:
        n_h = sizes[(q["lang"], q["outlet"])]; N_h = n_h + 1
        q.update({"stratum": f"{q['lang']}|{q['outlet']}", "N_h": N_h, "n_h": n_h, "pi": n_h / N_h, "weight": N_h / n_h})
    # a fixture frame and registry the sample contract hand-shakes with (Codex W1 round 2, #1)
    frame = {"kind": "A2_frame_manifest", "pre_freeze_head": "pfh", "n_eligible": len(paras) + len(fillers), "eligible_checksum": "ec",
             "paragraphs": [{k: q[k] for k in ("paragraph_id", "article_id", "digest", "text_sha256", "lang", "outlet", "exposure")} for q in paras] + fillers}
    frame["digest"] = rx.sc.canonical_digest(frame)
    (tmp / "frame.json").write_text(json.dumps(frame))
    (tmp / "registry.json").write_text(json.dumps({"assignment_log": [{"seq": 1, "kind": "assign", "h": "pfh"},
                                                                       {"seq": 2, "kind": "a2_frozen", "audit_ref": frame["digest"], "h": "frz", "eligible_checksum": "ec"}]}))
    rx.sc.FRAME_MANIFEST = tmp / "frame.json"; rx.sc.REGISTRY_FILE = tmp / "registry.json"
    by_lang = dict(_C(q["lang"] for q in paras))
    man = {"kind": "A2_sample_manifest", "frame_manifest_digest": frame["digest"], "freeze_event_hash": "frz",
           "n": len(paras), "by_language": by_lang,
           "design": {"cap_used": {L: 24 for L in by_lang}, "cap_registered": 24, "target": by_lang},
           "paragraphs": paras}
    man["digest"] = rx.manifest_digest(man)
    mp = tmp / "manifest.json"; mp.write_text(json.dumps(man))
    # the fixture IS the registered draw: the contract binds reportable work to this file and design
    rx.sc.SAMPLE_MANIFEST = mp; rx.sc.REGISTERED_TARGET = by_lang; rx.sc.REGISTERED_CAP = 24
    # no A3 listing in the fixture world unless a test lists one (the repository's real listing would otherwise
    # be validated against the fixture frame and refuse every run)
    rx.A3_SAMPLE_MANIFEST = rx.sc.A3_SAMPLE_MANIFEST = tmp / "A3_sample_manifest.absent.json"
    return mp


def ns(**kw):
    base = dict(manifest="", restrict_split=None, model="sonnet", repeats=1, run_id="t", resume=False, dev=False,
                recover_infrastructure=False)
    base.update(kw); return argparse.Namespace(**base)


ERROR_RETRIES_PLUS_ONE = rx.ERROR_RETRIES + 1


def go(tmp, plan, **kw):
    fake = Fake(plan)
    slept = []
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            rec = rx.run(ns(**kw), client_factory=lambda: fake, sleep=slept.append)
            code = 0
        except SystemExit as e:
            rec, code = None, e.code
    return rec, fake, slept, code


def recs(tmp, run_id):
    return rx.Journal.read(rx.RUNS / f"{run_id}_calls.jsonl")[0]


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="run_extract_"))
    try:
        art = ins.load_artifacts()
        # ---- T1 a normal run --------------------------------------------------------- #
        mp = setup(tmp, ["T001_p01", "T001_p02", "T002_p01"])
        rec, fake, _, code = go(tmp, gate_plan() + [resp(WIND), resp(SOLAR_TWO), resp(OFF)],
                                manifest=str(mp), run_id="r1")
        check("T1 a certified run completes and is reportable", code == 0 and rec["reportable"] is True,
              f"code={code}")
        terms = [r for r in recs(tmp, "r1") if r["type"] == "terminal"]
        check("T1b one terminal record per paragraph x execution",
              sorted(t["outcome"] for t in terms) == ["off_topic", "technologies_present", "technologies_present"])
        sent = fake.seen[3:]
        digests_ok = all(rx.request_digest(kw) == rx.request_digest(
            {"modelId": ins.MODELS["sonnet"], **ins.build_request(art, TEXTS[p], max_tokens=rx.MAX_TOKENS)})
            for kw, p in zip(sent, ["T001_p01", "T001_p02", "T002_p01"]))
        check("T1c every sent envelope IS build_request()'s output for its paragraph", digests_ok)
        preds = list(csv.DictReader((rx.RUNS / "r1_predictions.csv").open(encoding="utf-8"), delimiter=";"))
        check("T1d predictions: 3 relation rows (1 wind, 2 for the solar paragraph), keyed by execution",
              len(preds) == 3 and {p["technology"] for p in preds} == {"wind", "solar", "general"}
              and all(p["run_index"] == "1" for p in preds), str([(p["paragraph_id"], p["technology"]) for p in preds]))
        wind = next(p for p in preds if p["technology"] == "wind")
        check("T1e element flags, evaluations and subtype land in the pass-2 vocabulary",
              wind["F8a"] == "1" and wind["F7b"] == "1" and wind["F1a"] == "0" and wind["eval_regulation"] == "negative"
              and wind["wind_siting"] == "onshore" and wind["no_frame_for_this_tech"] == "0")
        gen = next(p for p in preds if p["technology"] == "general")
        check("T1f an empty element list is the per-technology no-frame state", gen["no_frame_for_this_tech"] == "1")
        check("T1g the run record links the gate artefact, its digest and its check codes only",
              rec["preflight"]["certified"] and rec["preflight"]["sha256"] and
              all(set(c) == {"id", "ok", "skipped"} for c in rec["preflight"]["checks"]))
        check("T1h cost is token-derived and billing is complete", rec["cost"]["billing_complete"]
              and rec["cost"]["estimated_usd"] > 0 and rec["totals"]["calls"] == 3)
        # ---- T2 licence: the planted quotation reaches no public file ------------------ #
        public = [rx.RUNS / f for f in ("r1_calls.jsonl", "r1_terminal.csv", "r1_predictions.csv", "r1.json",
                                          "preflight_r1.json")]
        leaks = [f.name for f in public if f.exists() and QUOTE in f.read_text(encoding="utf-8")]
        check("T2 the planted quotation in `note` appears in no public file", not leaks, str(leaks))
        check("T2b and it IS in the private raw file", QUOTE in (rx.RAW / "r1.jsonl").read_text(encoding="utf-8"))
        # ---- T3 outcome-specific retry policy ---------------------------------------- #
        mp = setup(tmp, ["T001_p02"])
        rec, fake, _, _ = go(tmp, gate_plan() + [resp(OFF, stop="content_filtered", content=[])], manifest=str(mp), run_id="r3a")
        t = [r for r in recs(tmp, "r3a") if r["type"] == "terminal"][0]
        check("T3a a refusal terminates after ONE attempt and produces no prediction",
              t["outcome"] == "refused" and t["n_attempts"] == 1 and len(fake.seen) == 4)
        rec, fake, _, _ = go(tmp, gate_plan() + [resp(OFF, stop="max_tokens", content=[]), resp(SOLAR_TWO)], manifest=str(mp), run_id="r3b")
        t = [r for r in recs(tmp, "r3b") if r["type"] == "terminal"][0]
        check("T3b a truncated response is retried once and the valid retry resolves",
              t["outcome"] == "technologies_present" and t["codes"] == ["truncated", "ok_technologies_present"])
        rec, fake, _, _ = go(tmp, gate_plan() + [resp(BAD_SCHEMA), resp(BAD_CONTRACT)], manifest=str(mp), run_id="r3c")
        t = [r for r in recs(tmp, "r3c") if r["type"] == "terminal"][0]
        fin = [r for r in recs(tmp, "r3c") if r["type"] == "attempt_finished"]
        check("T3c two malformed responses -> parse_failed with schema_invalid then contract_invalid",
              t["outcome"] == "parse_failed" and t["codes"] == ["schema_invalid", "contract_invalid"]
              and fin[1]["contract_rules"] == ["off_topic_with_relations"], str(t["codes"]))
        check("T3d no prediction row and no payload for a failed execution",
              all(r["payload"] is None for r in fin) and
              len(list(csv.DictReader((rx.RUNS / "r3c_predictions.csv").open(), delimiter=";"))) == 0)
        rec, fake, slept, _ = go(tmp, gate_plan() + [Throttle(), Throttle(), resp(OFF)], manifest=str(mp), run_id="r3e")
        t = [r for r in recs(tmp, "r3e") if r["type"] == "terminal"][0]
        check("T3e throttles back off, are counted, and do not consume the parse budget",
              t["outcome"] == "off_topic" and rec["totals"]["throttles"] == 2 and slept == [2.0, 4.0], f"{t['codes']} {slept}")
        # ---- T4 repeats ---------------------------------------------------------------- #
        mp = setup(tmp, ["T001_p01", "T001_p02"])
        rec, fake, _, _ = go(tmp, gate_plan() + [resp(WIND), resp(OFF), resp(OFF), resp(SOLAR_TWO), resp(SOLAR_TWO), resp(OFF)],
                             manifest=str(mp), run_id="r4", repeats=3)
        terms = [r for r in recs(tmp, "r4") if r["type"] == "terminal"]
        preds = list(csv.DictReader((rx.RUNS / "r4_predictions.csv").open(), delimiter=";"))
        check("T4 k=3 gives exactly 3 terminal records per paragraph with variable relation counts",
              len(terms) == 6 and sorted(t["run_index"] for t in terms) == [1, 1, 2, 2, 3, 3] and len(preds) == 1 + 2 + 2,
              f"{len(terms)} terminals, {len(preds)} rows")
        check("T4b execution 1 of the wind paragraph is the WIND response, execution 2 off_topic (scheduled index)",
              next(t for t in terms if t["paragraph_id"] == "T001_p01" and t["run_index"] == 1)["outcome"] == "technologies_present"
              and next(t for t in terms if t["paragraph_id"] == "T001_p01" and t["run_index"] == 2)["outcome"] == "off_topic")
        # ---- T5 recovery at every write boundary ------------------------------------- #
        def seed_journal(run_id, entries, raws, dev=False, restrict_split=None):
            msha = rx.manifest_digest(json.loads(mp.read_text()))
            fp = rx.fingerprint(art, "sonnet", 1, msha, rx.ins.REGION, restrict_split, dev)
            j = rx.Journal(rx.RUNS / f"{run_id}_calls.jsonl")
            j.write({"type": "run_started", "run_id": run_id, "started_at": "2026-09-05T00:00:00+00:00",
                     "fingerprint": fp, "manifest": "m", "manifest_sha256": msha,
                     "model": ins.MODELS["sonnet"], "repeats": 1, "reportable": True})
            for e in entries:
                j.write(e)
            j.close()
            r = rx.Journal(rx.RAW / f"{run_id}.jsonl")
            for x in raws:
                r.write(x)
            r.close()
        mp = setup(tmp, ["T001_p02"])
        started = {"type": "attempt_started", "run_id": "r5a", "paragraph_id": "T001_p02", "run_index": 1,
                   "attempt_no": 1, "request_sha256_built": "x", "request_sha256_sent": "x", "at": "t"}
        # (a) valid response persisted raw, crash before finished -> no further call
        seed_journal("r5a", [started], [{"type": "raw", "run_id": "r5a", "paragraph_id": "T001_p02", "run_index": 1,
                                         "attempt_no": 1, "response": resp(SOLAR_TWO), "at": "t"}])
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r5a", resume=True)
        t = [r for r in recs(tmp, "r5a") if r["type"] == "terminal"]
        check("T5a raw-only valid attempt: rebuilt, promoted, NO extra model call",
              code == 0 and len(fake.seen) == 3 and t and t[0]["outcome"] == "technologies_present"
              and rec["totals"]["recovered_attempts"] == 1, f"code={code} calls={len(fake.seen)}")
        # (b) malformed first attempt persisted raw, crash -> the prescribed retry is issued
        seed_journal("r5b", [dict(started, run_id="r5b")],
                     [{"type": "raw", "run_id": "r5b", "paragraph_id": "T001_p02", "run_index": 1,
                       "attempt_no": 1, "response": resp(BAD_SCHEMA), "at": "t"}])
        rec, fake, _, code = go(tmp, gate_plan() + [resp(SOLAR_TWO)], manifest=str(mp), run_id="r5b", resume=True)
        t = [r for r in recs(tmp, "r5b") if r["type"] == "terminal"]
        check("T5b raw-only MALFORMED attempt: replayed, the second attempt is made, not a terminal",
              code == 0 and len(fake.seen) == 4 and t[0]["codes"] == ["schema_invalid", "ok_technologies_present"],
              f"calls={len(fake.seen)} codes={t[0]['codes'] if t else None}")
        # (c) finished but no terminal -> promoted without a call
        fin = {"type": "attempt_finished", "run_id": "r5c", "paragraph_id": "T001_p02", "run_index": 1, "attempt_no": 1,
               "code": "ok_off_topic", "contract_rules": [], "stop_reason": "tool_use", "usage": usage(),
               "latency_ms": 5, "payload": {"outcome": "off_topic", "relations": []}, "at": "t"}
        seed_journal("r5c", [dict(started, run_id="r5c"), fin], [])
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r5c", resume=True)
        t = [r for r in recs(tmp, "r5c") if r["type"] == "terminal"]
        check("T5c finished-without-terminal: promoted to terminal, no extra call",
              code == 0 and len(fake.seen) == 3 and t[0]["outcome"] == "off_topic")
        # (d) started, no raw -> unknown completion, re-sent and counted
        seed_journal("r5d", [dict(started, run_id="r5d")], [])
        rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="r5d", resume=True)
        check("T5d started-without-raw: unknown completion, re-sent, counted, billing incomplete",
              code == 0 and len(fake.seen) == 4 and rec["totals"]["unknown_completion_attempts"] == 1
              and rec["cost"]["billing_complete"] is False)
        # (e) torn last line is discarded and counted
        p = rx.RUNS / "r5d_calls.jsonl"; p.write_text(p.read_text() + '{"type": "terminal", "trunc')
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r5d", resume=True)
        check("T5e a torn journal line is discarded and counted", code == 0 and rec["totals"]["torn_records"] == 1,
              f"code={str(code)[:160]} torn={(rec or {}).get('totals', {}).get('torn_records')}")
        # (f) a changed contract refuses to resume
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r5c", resume=True, repeats=2)
        check("T5f resume under a changed execution contract (repeats) is refused", code not in (0, None) and rec is None)
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r5c")
        check("T5g an existing run-id without --resume is refused", code not in (0, None))
        # ---- T6 the gate ------------------------------------------------------------- #
        rec, fake, _, code = go(tmp, gate_plan(certify=False) + [resp(OFF)], manifest=str(mp), run_id="r6a")
        check("T6a an uncertified gate refuses a reportable run before any extraction call",
              code not in (0, None) and len(fake.seen) == 3)
        rec, fake, _, code = go(tmp, gate_plan(certify=False) + [resp(OFF)], manifest=str(mp), run_id="r6b", dev=True, restrict_split="A1")
        check("T6b --dev tolerates the uncertified gate and stamps reportable: false",
              code == 0 and rec["reportable"] is False and rec["dev"] is True)
        # ---- T8 round-6 contracts ------------------------------------------------------ #
        # (a) accounting after resume includes prior usage; a completed run resumed adds nothing
        rec1, fake, _, _ = go(tmp, gate_plan() + [resp(OFF, u=usage(inp=300, out=20))], manifest=str(mp), run_id="r8a")
        # changed 2026-10-19 (Codex §4f round 2, #3): a completed run whose record matches its journal is no longer
        # re-gated on resume -- the resume is refused before any call and nothing is appended (a failing re-gate made a
        # finished run unevaluable); the cumulative accounting therefore stays exactly the record's
        j8 = rx.RUNS / "r8a_calls.jsonl"; jb = j8.read_bytes(); rb = (rx.RUNS / "r8a.json").read_bytes()
        rec2, fake2, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r8a", resume=True)
        check("T8a resuming a completed run makes no call, appends nothing and leaves the record (and its cumulative accounting) unchanged",
              code == 0 and rec2 is not None and rec2["totals"]["calls"] == 1 and len(fake2.seen) == 0 and j8.read_bytes() == jb and (rx.RUNS / "r8a.json").read_bytes() == rb
              and rec1["totals"]["calls"] == 1 and rec1["totals"]["input_tokens"] == 300, f"code={str(code)[:120]} calls={len(fake2.seen)}")
        # (b) an abandoned attempt stays counted once across resumes and never re-uses its number
        seed_journal("r8b", [dict(started, run_id="r8b")], [])
        rec, fake, _, _ = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="r8b", resume=True)
        rec2, fake2, _, code2 = go(tmp, gate_plan(), manifest=str(mp), run_id="r8b", resume=True)
        fin = [r for r in recs(tmp, "r8b") if r["type"] == "attempt_finished"]
        on_disk = json.loads((rx.RUNS / "r8b.json").read_text(encoding="utf-8"))
        # since 2026-10-19 the second resume of the now completed run is refused without touching anything (T8a, T14i)
        check("T8b the resend gets attempt_no 2 and the abandonment is counted exactly once, also after a second (refused) resume",
              fin and fin[0]["attempt_no"] == 2 and rec["totals"]["unknown_completion_attempts"] == 1 and code2 == 0
              and not fake2.seen and on_disk["totals"]["unknown_completion_attempts"] == 1 and on_disk["totals"]["calls"] == 1,
              str(on_disk["totals"]))
        # (c) a recorded exception without a finished record is replayed as that outcome
        seed_journal("r8c", [dict(started, run_id="r8c")],
                     [{"type": "exception", "run_id": "r8c", "paragraph_id": "T001_p02", "run_index": 1, "attempt_no": 1,
                       "error_class": "ThrottlingException", "error_code": "ThrottlingException", "error": "x", "at": "t"}])
        rec, fake, _, _ = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="r8c", resume=True)
        t = [r for r in recs(tmp, "r8c") if r["type"] == "terminal"][0]
        check("T8c a recorded throttle without a finished record replays as throttled, then the retry resolves",
              t["codes"] == ["throttled", "ok_off_topic"] and rec["totals"]["throttles"] == 1 and rec["totals"]["recovered_attempts"] == 1, str(t["codes"]))
        # (d) a torn tail is truncated so the next record does not merge into it
        p = rx.RUNS / "r8c_calls.jsonl"; p.write_text(p.read_text() + '{"type": "terminal", "trunc')
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r8c", resume=True)
        rr, torn = rx.Journal.read(p)
        check("T8d the torn tail is cut before appending: every line parses and the tear is counted",
              code == 0 and torn == 0 and rec["totals"]["torn_records"] >= 1 and all("type" in r for r in rr))
        # (e) a payload under end_turn is not accepted
        rec, fake, _, _ = go(tmp, gate_plan() + [resp(OFF, stop="end_turn"), resp(OFF)], manifest=str(mp), run_id="r8e")
        t = [r for r in recs(tmp, "r8e") if r["type"] == "terminal"][0]
        check("T8e a payload under stopReason end_turn is unexpected_stop_reason and retried, not accepted",
              t["codes"] == ["unexpected_stop_reason", "ok_off_topic"], str(t["codes"]))
        # (e2) several toolUse blocks, or a reasoning block, are unexpected_content (Codex W1 #7)
        two = {"stopReason": "tool_use", "usage": usage(),
               "output": {"message": {"content": [{"toolUse": {"name": ins.TOOL_NAME, "input": OFF}},
                                                  {"toolUse": {"name": ins.TOOL_NAME, "input": WIND}}]}}}
        rec, fake, _, _ = go(tmp, gate_plan() + [two, resp(OFF)], manifest=str(mp), run_id="r8e2")
        t = [r for r in recs(tmp, "r8e2") if r["type"] == "terminal"][0]
        check("T8e2 two toolUse blocks are unexpected_content and retried, never the first one accepted",
              t["codes"] == ["unexpected_content", "ok_off_topic"], str(t["codes"]))
        think = {"stopReason": "tool_use", "usage": usage(),
                 "output": {"message": {"content": [{"reasoningContent": {"reasoningText": {"text": ""}}},
                                                    {"toolUse": {"name": ins.TOOL_NAME, "input": OFF}}]}}}
        rec, fake, _, _ = go(tmp, gate_plan() + [think, resp(OFF)], manifest=str(mp), run_id="r8e3")
        t = [r for r in recs(tmp, "r8e3") if r["type"] == "terminal"][0]
        check("T8e3 a reasoningContent block makes the attempt unexpected_content (thinking was not off)",
              t["codes"] == ["unexpected_content", "ok_off_topic"], str(t["codes"]))
        # (e4) a response without usage is accepted as a label but the billing is INCOMPLETE (Codex W1 #9)
        nouse = {"stopReason": "tool_use", "usage": {"outputTokens": 5},
                 "output": {"message": {"content": [{"toolUse": {"name": ins.TOOL_NAME, "input": OFF}}]}}}
        rec, fake, _, _ = go(tmp, gate_plan() + [nouse], manifest=str(mp), run_id="r8e4")
        check("T8e4 a finished attempt without complete usage counts as usage-incomplete and billing is INCOMPLETE",
              rec is not None and rec["totals"]["usage_incomplete_attempts"] == 1 and rec["cost"]["billing_complete"] is False
              and rec["totals"]["verdict_codes"].get("ok_off_topic") == 1, str(rec and rec["cost"]))
        check("T8e5 the cost is labelled an estimate at frozen provisional rates, with the rates persisted",
              rec is not None and "estimated_usd" in rec["cost"] and "observed_usd" not in rec["cost"]
              and rec["cost"]["rates_per_million_usd"] == rx.PRICES_PER_M["sonnet"] and "estimate" in rec["cost"]["basis"])
        # (e6) the production boundary (Codex W1 #13)
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r8e6", dev=True)
        check("T8e6 a development run without --restrict-split is refused", rec is None and code and "restrict-split" in str(code), str(code))
        doc = json.loads(mp.read_text()); doc["kind"] = "A1_session_manifest"; doc.pop("digest"); doc["digest"] = rx.manifest_digest(doc)
        mp3 = tmp / "session.json"; mp3.write_text(json.dumps(doc))
        rx.sc.SAMPLE_MANIFEST = mp3; rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp3), run_id="r8e7"); rx.sc.SAMPLE_MANIFEST = mp
        check("T8e7 a reportable run on a non-A2 manifest is refused", rec is None and code and "A2 sample manifest" in str(code), str(code))
        doc = json.loads(mp.read_text()); [q.pop("weight", None) for q in doc["paragraphs"]]; doc.pop("digest"); doc["digest"] = rx.manifest_digest(doc)
        mp4 = tmp / "unweighted.json"; mp4.write_text(json.dumps(doc))
        rx.sc.SAMPLE_MANIFEST = mp4; rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp4), run_id="r8e8"); rx.sc.SAMPLE_MANIFEST = mp
        check("T8e8 a reportable run on an unweighted manifest is refused", rec is None and code and "design weight" in str(code), str(code))
        doc = json.loads(mp.read_text()); doc["paragraphs"][0]["digest"] = "not-in-frame"; doc.pop("digest"); doc["digest"] = rx.manifest_digest(doc)
        mp5 = tmp / "foreign.json"; mp5.write_text(json.dumps(doc))
        rx.sc.SAMPLE_MANIFEST = mp5; rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp5), run_id="r8e8b"); rx.sc.SAMPLE_MANIFEST = mp
        check("T8e8b a reportable run on a paragraph outside the frozen frame is refused before any call",
              rec is None and code and "not members of the frozen frame" in str(code) and len(fake.seen) == 0, str(code))
        # Codex W1 round 4, #1: swap a drawn paragraph for the undrawn member of the same stratum -- internally
        # consistent, digest recomputed, but not the registered draw -> refused before any call
        doc = json.loads(mp.read_text()); q0 = doc["paragraphs"][0]
        q0.update({"paragraph_id": f"FILL_{q0['lang']}_{q0['outlet']}", "article_id": f"FILL_{q0['lang']}",
                   "digest": f"d-fill-{q0['lang']}-{q0['outlet']}", "text_sha256": f"t-fill-{q0['lang']}-{q0['outlet']}"})
        doc.pop("digest"); doc["digest"] = rx.manifest_digest(doc)
        mp6 = tmp / "otherdraw.json"; mp6.write_text(json.dumps(doc))
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp6), run_id="r8e8c")
        check("T8e8c a same-stratum swap that is internally consistent but not the registered draw is refused before any call",
              rec is None and code and "not the registered sample manifest" in str(code) and len(fake.seen) == 0, str(code))
        rx.sc.SAMPLE_MANIFEST = mp6
        try:
            rx.sc.verify_sample(json.loads(mp6.read_text())); swapped_ok = True
        except rx.sc.ContractError:
            swapped_ok = False
        rx.sc.SAMPLE_MANIFEST = mp
        check("T8e8d the swapped manifest is internally consistent (only the registration binding catches it)", swapped_ok)
        # (e13) recovery episodes for infrastructure terminals (Codex W1 round 2, #2)
        boom = [RuntimeError("boom") for _ in range(ERROR_RETRIES_PLUS_ONE)]
        rec, fake, _, _ = go(tmp, gate_plan() + boom, manifest=str(mp), run_id="r8e13")
        t0 = [r for r in recs(tmp, "r8e13") if r["type"] == "terminal"]
        check("T8e13 three exceptions exhaust the policy into a terminal error",
              rec is not None and len(t0) == 1 and t0[0]["outcome"] == "error" and t0[0]["episode"] == 0, str(t0))
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r8e13", resume=True)
        check("T8e13b a plain --resume makes no call (since 2026-10-19 not even a gate: the run is complete) and keeps the error terminal", code == 0 and len(fake.seen) == 0
              and rec["totals"]["verdict_codes"] == {"error": 3}, str(rec and rec["totals"]["verdict_codes"]))
        rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="r8e13", resume=True, recover_infrastructure=True)
        t1 = [r for r in recs(tmp, "r8e13") if r["type"] == "terminal"]
        rows = list(csv.DictReader((rx.RUNS / "r8e13_terminal.csv").open(encoding="utf-8"), delimiter=";"))
        check("T8e13c --resume --recover-infrastructure reopens it in episode 1, resolves, and the last terminal stands",
              code == 0 and len(t1) == 2 and t1[-1]["outcome"] == "off_topic" and t1[-1]["episode"] == 1
              and len(rows) == 1 and rows[0]["outcome"] == "off_topic" and rows[0]["episode"] == "1"
              and rec["totals"]["calls"] == 4 and rec["totals"]["verdict_codes"] == {"error": 3, "ok_off_topic": 1},
              str((len(t1), rows, rec and rec["totals"])))
        rec, fake, _, _ = go(tmp, gate_plan() + boom, manifest=str(mp), run_id="r8e14")
        rec, fake, _, _ = go(tmp, gate_plan() + boom, manifest=str(mp), run_id="r8e14", resume=True, recover_infrastructure=True)
        rec, fake, _, _ = go(tmp, gate_plan() + boom, manifest=str(mp), run_id="r8e14", resume=True, recover_infrastructure=True)
        rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="r8e14", resume=True, recover_infrastructure=True)
        t2 = [r for r in recs(tmp, "r8e14") if r["type"] == "terminal"]
        check("T8e14 after two failed episodes a third recovery makes no call: the error terminal stands (not assessed)",
              code == 0 and len(fake.seen) == 0 and len(t2) == 3 and all(x["outcome"] == "error" for x in t2)
              and t2[-1]["episode"] == 2 and rec["totals"]["calls"] == 9, str((len(fake.seen), len(t2), rec and rec["totals"]["calls"])))
        rec, fake, _, _ = go(tmp, gate_plan() + [resp(BAD_SCHEMA), resp(BAD_SCHEMA)], manifest=str(mp), run_id="r8e15")
        rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="r8e15", resume=True, recover_infrastructure=True)
        t3 = [r for r in recs(tmp, "r8e15") if r["type"] == "terminal"]
        check("T8e15 a parse_failed terminal is never reopened by a recovery episode",
              code == 0 and len(fake.seen) == 0 and len(t3) == 1 and t3[0]["outcome"] == "parse_failed", str(t3))
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r8e15", recover_infrastructure=True)
        check("T8e16 --recover-infrastructure without --resume is refused", rec is None and "only makes sense with --resume" in str(code), str(code))
        # round 3: uncertain by default, mixed histories recover, artefacts retained, contract persisted
        rec, fake, _, _ = go(tmp, gate_plan() + [RuntimeError("boom"), resp(OFF)], manifest=str(mp), run_id="r8e17")
        check("T8e17 an unknown exception class leaves the billing uncertain (the default is incomplete)",
              rec is not None and rec["totals"]["billing_uncertain_attempts"] == 1 and rec["cost"]["billing_complete"] is False)
        mixed = [resp(OFF, stop="max_tokens")] + [RuntimeError("boom") for _ in range(ERROR_RETRIES_PLUS_ONE)]
        rec, fake, _, _ = go(tmp, gate_plan() + mixed, manifest=str(mp), run_id="r8e18")
        t0 = [r for r in recs(tmp, "r8e18") if r["type"] == "terminal"]
        rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="r8e18", resume=True, recover_infrastructure=True)
        t1 = [r for r in recs(tmp, "r8e18") if r["type"] == "terminal"]
        check("T8e18 a truncation followed by exhausted exceptions is an error terminal that a recovery episode reopens",
              t0[0]["outcome"] == "error" and code == 0 and t1[-1]["outcome"] == "off_topic" and t1[-1]["episode"] == 1
              and len(rec["preflight"]["gates"]) == 2, str((t0[0]["outcome"], [x["outcome"] for x in t1], rec and rec["preflight"]["gates"])))
        check("T8e19 the run record carries the execution contract as a document beside its digest",
              isinstance(rec["execution_contract"], dict) and rec["execution_contract"]["runner_sha256"] == rx.sha256_file(Path(rx.__file__))
              and rec["execution_fingerprint"] == hashlib.sha256(json.dumps(rec["execution_contract"], sort_keys=True).encode()).hexdigest())
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r8e20", restrict_split="A1")
        check("T8e20 a reportable run with --restrict-split is refused", rec is None and "whole registered sample" in str(code), str(code))
        zero_break = {"stopReason": "tool_use", "usage": {"inputTokens": 400, "outputTokens": 60, "cacheReadInputTokens": 0,
                                                           "cacheWriteInputTokens": 6000, "cacheDetails": []},
                      "output": {"message": {"content": [{"toolUse": {"name": ins.TOOL_NAME, "input": OFF}}]}}}
        rec, fake, _, _ = go(tmp, gate_plan() + [zero_break], manifest=str(mp), run_id="r8e21")
        check("T8e21 an empty breakdown beside a 6,000-token write is a contradiction: usage incomplete",
              rec is not None and rec["totals"]["usage_incomplete_attempts"] == 1 and rec["cost"]["billing_complete"] is False)
        # (e22) accounting boundary: an error attempt's usage shape is no evidence; a 503 stays uncertain
        recs22 = [{"type": "attempt_finished", "paragraph_id": "P", "run_index": 1, "attempt_no": 1, "code": "error",
                   "usage": {}, "error_class": "ReadTimeoutError", "error_code": "ReadTimeoutError"},
                  {"type": "attempt_finished", "paragraph_id": "P", "run_index": 1, "attempt_no": 2, "code": "error",
                   "usage": {"input_tokens": 1, "usage_complete": False}, "error_class": "ClientError", "error_code": "ServiceUnavailableException"},
                  {"type": "attempt_finished", "paragraph_id": "P", "run_index": 1, "attempt_no": 3, "code": "error",
                   "usage": None, "error_class": "ClientError", "error_code": "ValidationException"}]
        t22 = rx.accounting(recs22)
        check("T8e22 error attempts with {} or partial usage are uncertain; a 503 is uncertain; a validation error is certainly unbilled",
              t22.billing_uncertain_attempts == 2 and t22.usage_incomplete_attempts == 0, str(t22))
        check("T8e23 the execution contract records where a thinking setting would live (None under the pinned request)",
              "additional_model_request_fields" in rec["execution_contract"])
        g0 = rec["preflight"]["gates"][0]["artefact"]
        gdoc = json.loads((rx.ROOT / g0).read_text()) if not Path(g0).is_absolute() else json.loads(Path(g0).read_text())
        check("T8e24 the run record stamps the first extraction call, and the first gate precedes it",
              rec["first_call_at"] is not None and gdoc["started_at"] <= rec["first_call_at"] and rec["started_at"] <= gdoc["started_at"],
              str((rec["started_at"], gdoc["started_at"], rec["first_call_at"])))
        # round 7: labels are bound to the gate they were produced under; a later gate cannot qualify them
        rec, fake, _, _ = go(tmp, gate_plan(certify=False) + [resp(OFF)], manifest=str(mp), run_id="r8e25", dev=True, restrict_split="A1")
        # since 2026-10-19 the resume of a complete run with an intact record is a no-op; the run record is removed here so
        # the resume re-gates and rewrites it, the situation this test guards (a later gate must not qualify earlier labels)
        (rx.RUNS / "r8e25.json").unlink()
        rec2, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r8e25", resume=True, dev=True, restrict_split="A1")
        rows = list(csv.DictReader((rx.RUNS / "r8e25_terminal.csv").open(encoding="utf-8"), delimiter=";"))
        check("T8e25 an uncertified first gate followed by a certified resume: two gates journalled, all_certified False, the label bound to the first gate",
              code == 0 and len(rec2["preflight"]["gates"]) == 2 and rec2["preflight"]["gates"][0]["certified"] is False
              and rec2["preflight"]["all_certified"] is False and rows[0]["gate"] == rec2["preflight"]["gates"][0]["sha256"]
              and len(fake.seen) == 3, str((rec2 and rec2["preflight"], rows and rows[0].get("gate"))))
        (rx.RUNS / "preflight_r8e26-old.json").write_text("{}")
        rec, fake, _, _ = go(tmp, gate_plan(), manifest=str(mp), run_id="r8e26")
        check("T8e26 gates are collected by exact run identity from the journal, not by a name prefix",
              len(rec["preflight"]["gates"]) == 1 and "r8e26-old" not in rec["preflight"]["gates"][0]["artefact"], str(rec["preflight"]["gates"]))
        # round 8: an aborting gate is journalled; the summary is reconciled with the journal
        class Denied(Exception):
            pass
        rec, fake, _, code = go(tmp, [Denied("AccessDeniedException: not available")], manifest=str(mp), run_id="r8e27", dev=True, restrict_split="A1")
        j27 = recs(tmp, "r8e27")
        check("T8e27 a gate that could not reach the model is journalled before the refusal, and the refusal closes the invocation",
              rec is None and code and sum(1 for r in j27 if r.get("type") == "gate") == 1 and j27[-2]["type"] == "gate" and j27[-2]["certified"] is False
              and j27[-1]["type"] == "run_finished" and j27[-1]["outcome"] == "refused_unreachable", str([r.get("type") for r in j27]))
        rec2, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="r8e27", resume=True, dev=True, restrict_split="A1")
        check("T8e27b the resume keeps the aborted gate on record: two gates, all_certified False",
              code == 0 and len(rec2["preflight"]["gates"]) == 2 and rec2["preflight"]["all_certified"] is False, str(rec2 and rec2["preflight"]["gates"]))
        rows25 = list(csv.DictReader((rx.RUNS / "r8e25_terminal.csv").open(encoding="utf-8"), delimiter=";"))
        rec25 = json.loads((rx.RUNS / "r8e25.json").read_text())
        try:
            rx.verify_journal_binding(rec25, rows25, rx.RUNS / "r8e25_calls.jsonl"); ok25 = True
        except ValueError as exc:
            ok25 = False; print("      ", exc)
        check("T8e28 the honest summary of r8e25 reconciles with its journal", ok25)
        rec25b = json.loads(json.dumps(rec25)); rec25b["preflight"]["gates"] = rec25b["preflight"]["gates"][1:]   # drop the failed gate
        rows25b = [dict(r) for r in rows25]; rows25b[0]["gate"] = rec25b["preflight"]["gates"][0]["sha256"]          # relabel under the certified one
        try:
            rx.verify_journal_binding(rec25b, rows25b, rx.RUNS / "r8e25_calls.jsonl"); ok25b = True
        except ValueError:
            ok25b = False
        check("T8e28b a summary that drops the failed gate and relabels the terminal under the certified one is refused by the journal", not ok25b)
        # round 9: the contract the run started under, and a reader that never touches the evidence
        rec25c = json.loads(json.dumps(rec25)); rec25c["execution_contract"]["max_tokens"] = 2048
        rec25c["execution_fingerprint"] = hashlib.sha256(json.dumps(rec25c["execution_contract"], sort_keys=True).encode()).hexdigest()
        try:
            rx.verify_journal_binding(rec25c, rows25, rx.RUNS / "r8e25_calls.jsonl"); ok25c = True
        except ValueError:
            ok25c = False
        check("T8e29 a record whose contract and fingerprint were rewritten is refused: the journal's run_started fingerprint differs", not ok25c)
        jp = rx.RUNS / "r8e25_calls.jsonl"; before = jp.read_bytes(); jp.write_bytes(before.rstrip(b"\n"))
        try:
            rx.verify_journal_binding(rec25, rows25, jp); ok25d = True
        except ValueError:
            ok25d = False
        check("T8e30 a journal whose last line lacks its newline is refused as torn evidence and is NOT modified by verification",
              not ok25d and jp.read_bytes() == before.rstrip(b"\n"))
        jp.write_bytes(before)
        # round 10: predictions, accounting and timestamps are the journal's
        rec25 = json.loads((rx.RUNS / "r8e25.json").read_text()); rows25 = list(csv.DictReader((rx.RUNS / "r8e25_terminal.csv").open(encoding="utf-8"), delimiter=";"))
        preds25 = list(csv.DictReader((rx.RUNS / "r8e25_predictions.csv").open(encoding="utf-8"), delimiter=";"))
        man25 = json.loads(mp.read_text())["paragraphs"]
        try:
            rx.verify_journal_binding(rec25, rows25, jp, pred_rows=preds25, rates=rx.PRICES_PER_M["sonnet"], paras=man25); ok31 = True
        except ValueError as exc:
            ok31 = False; print("      ", exc)
        check("T8e31 the honest record reconciles predictions, accounting and timestamps with its journal", ok31)
        rec31 = json.loads(json.dumps(rec25)); rec31["cost"]["estimated_usd"] = 0.0; rec31["cost"]["billing_complete"] = True
        try:
            rx.verify_journal_binding(rec31, rows25, jp, pred_rows=preds25, rates=rx.PRICES_PER_M["sonnet"], paras=man25); ok31b = True
        except ValueError:
            ok31b = False
        check("T8e31b an edited cost summary is refused: the accounting is recomputed from the journal", not ok31b)
        for bad_cost, label in ((rec25["cost"]["estimated_usd"] - 0.0000005, "shaved by half a millionth"), (float("nan"), "NaN"), (-0.0, "-0.0 (kept: equals 0 at six decimals)")):
            rec35 = json.loads(json.dumps(rec25)); rec35["cost"]["estimated_usd"] = bad_cost
            try:
                rx.verify_journal_binding(rec35, rows25, jp, pred_rows=preds25, rates=rx.PRICES_PER_M["sonnet"], paras=man25); ok35 = True
            except ValueError:
                ok35 = False
            expect_ok = label.startswith("-0.0") and rec25["cost"]["estimated_usd"] == 0
            check(f"T8e35 a cost {label} is {'accepted' if expect_ok else 'refused'} at six-decimal exactness", ok35 == expect_ok)
        rec32 = json.loads(json.dumps(rec25)); rec32["ended_at"] = "2026-01-01T00:00:00+00:00"
        try:
            rx.verify_journal_binding(rec32, rows25, jp, pred_rows=preds25, rates=rx.PRICES_PER_M["sonnet"], paras=man25); ok32 = True
        except ValueError:
            ok32 = False
        check("T8e32 an edited ended_at is refused: the journal's last run_finished is the end", not ok32)
        # a run with a relation: predictions.csv must be the payload-derived set
        rec33, fake, _, _ = go(tmp, gate_plan() + [resp(WIND)], manifest=str(mp), run_id="r8e33")
        preds33 = list(csv.DictReader((rx.RUNS / "r8e33_predictions.csv").open(encoding="utf-8"), delimiter=";"))
        rows33 = list(csv.DictReader((rx.RUNS / "r8e33_terminal.csv").open(encoding="utf-8"), delimiter=";"))
        try:
            rx.verify_journal_binding(rec33, rows33, rx.RUNS / "r8e33_calls.jsonl", pred_rows=preds33, rates=rx.PRICES_PER_M["sonnet"], paras=man25); ok33 = True
        except ValueError as exc:
            ok33 = False; print("      ", exc)
        edited = [dict(r) for r in preds33]; edited[0]["F1a"] = "1"
        try:
            rx.verify_journal_binding(rec33, rows33, rx.RUNS / "r8e33_calls.jsonl", pred_rows=edited, rates=rx.PRICES_PER_M["sonnet"], paras=man25); ok33b = True
        except ValueError:
            ok33b = False
        check("T8e33 predictions.csv is the journal's payload-derived set; an edited element is refused", ok33 and not ok33b and len(preds33) == 1)
        j33 = recs(tmp, "r8e33")
        check("T8e34 every invocation journals its completion and the record's ended_at is the last one",
              j33[-1]["type"] == "run_finished" and rec33["ended_at"] == j33[-1]["ended_at"])
        # (e9) exception-path accounting: a timeout may have been served, a throttle was not (Codex W1 r2 #3)
        class ReadTimeoutError(Exception):
            pass
        rec, fake, _, _ = go(tmp, gate_plan() + [ReadTimeoutError("read timed out"), resp(OFF)], manifest=str(mp), run_id="r8e9")
        check("T8e9 an error attempt with a transport failure leaves the billing INCOMPLETE (uncertain)",
              rec is not None and rec["totals"]["billing_uncertain_attempts"] == 1 and rec["cost"]["billing_complete"] is False
              and rec["totals"]["verdict_codes"].get("ok_off_topic") == 1, str(rec and rec["totals"]))
        rec, fake, _, _ = go(tmp, gate_plan() + [Throttle(), resp(OFF)], manifest=str(mp), run_id="r8e10")
        check("T8e10 a throttled attempt is certainly unbilled: billing stays complete",
              rec is not None and rec["totals"]["billing_uncertain_attempts"] == 0 and rec["cost"]["billing_complete"] is True, str(rec and rec["totals"]))
        # (e11) a write reported only in cacheDetails, or stored for 5m, is visible in the accounting (#7)
        det = {"stopReason": "tool_use", "usage": {"inputTokens": 400, "outputTokens": 60, "cacheReadInputTokens": 0,
                                                    "cacheDetails": [{"ttl": "1h", "inputTokens": 2000}]},
               "output": {"message": {"content": [{"toolUse": {"name": ins.TOOL_NAME, "input": OFF}}]}}}
        rec, fake, _, _ = go(tmp, gate_plan() + [det], manifest=str(mp), run_id="r8e11")
        check("T8e11 a cacheDetails write without the aggregate counter makes the usage incomplete",
              rec is not None and rec["totals"]["usage_incomplete_attempts"] == 1 and rec["cost"]["billing_complete"] is False)
        five = {"stopReason": "tool_use", "usage": {"inputTokens": 400, "outputTokens": 60, "cacheReadInputTokens": 0,
                                                     "cacheWriteInputTokens": 2000, "cacheDetails": [{"ttl": "5m", "inputTokens": 2000}]},
                "output": {"message": {"content": [{"toolUse": {"name": ins.TOOL_NAME, "input": OFF}}]}}}
        rec, fake, _, _ = go(tmp, gate_plan() + [five], manifest=str(mp), run_id="r8e12")
        check("T8e12 a production write stored for 5m is counted as cache_writes_other_ttl and makes the billing INCOMPLETE",
              rec is not None and rec["totals"]["cache_writes_other_ttl"] == 1 and rec["cache_writes_other_ttl"] == 1
              and rec["cost"]["billing_complete"] is False and "not requested" in rec["thinking"], str(rec and rec["totals"]))
        # (f) a manifest whose content changed under a kept digest is refused
        doc = json.loads(mp.read_text()); doc["paragraphs"][0]["lang"] = "xx"; mp2 = tmp / "tampered.json"; mp2.write_text(json.dumps(doc))
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp2), run_id="r8f")
        check("T8f a manifest edited under a kept digest is refused before any call", code not in (0, None) and len(fake.seen) == 0)
        # (g) a development run cannot be resumed as reportable
        seed_journal("r8g", [], [], dev=True, restrict_split="A1")   # a dev run names its split
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r8g", resume=True)
        check("T8g resuming a --dev run without --dev is refused (development status is part of the contract)", code not in (0, None))
        rec, fake, _, code = go(tmp, gate_plan(certify=False) + [resp(OFF)], manifest=str(mp), run_id="r8g", resume=True, dev=True, restrict_split="A1")
        check("T8h and with --dev it resumes and stays reportable: false", code == 0 and rec["reportable"] is False)

        # ---- T9 inspection findings ----------------------------------------------------- #
        # abandoned attempt 1, then a successful resend: the prediction rows must exist
        seed_journal("r9a", [dict(started, run_id="r9a")], [])
        rec, fake, _, _ = go(tmp, gate_plan() + [resp(SOLAR_TWO)], manifest=str(mp), run_id="r9a", resume=True)
        preds = list(csv.DictReader((rx.RUNS / "r9a_predictions.csv").open(), delimiter=";"))
        t = [r for r in recs(tmp, "r9a") if r["type"] == "terminal"][0]
        check("T9a a success after an abandoned attempt keeps its prediction rows (resolved_from_attempt = 2)",
              t["resolved_from_attempt"] == 2 and len(preds) == 2, f"{t} rows={len(preds)}")
        # a torn multibyte tail is repaired at the byte level; a complete line without newline is kept
        p = rx.RUNS / "r9a_calls.jsonl"; raw = p.read_bytes()
        p.write_bytes(raw + '{"type": "terminal", "x": "ä'.encode("utf-8")[:-1])   # cut inside the ä
        rec, fake, _, code = go(tmp, gate_plan(), manifest=str(mp), run_id="r9a", resume=True)
        check("T9b a tear inside a multibyte character is cut, not raised", code == 0 and rec["totals"]["torn_records"] >= 1)
        p.write_bytes(p.read_bytes() + b'{"type": "note", "complete": true}')            # no newline
        rr, torn = rx.Journal.read(p)
        check("T9c a complete last record without newline is kept and terminated, not cut",
              torn == 0 and rr[-1] == {"type": "note", "complete": True} and p.read_bytes().endswith(b"\n"))
        head_bytes = p.read_bytes(); p.write_bytes(head_bytes + b'{"torn": ')
        rx.repair_tail(p)
        check("T9d tail repair truncates only the damaged suffix and keeps every earlier byte", p.read_bytes() == head_bytes)

        # ---- T7 the state machine is pure -------------------------------------------- #
        check("T7 resolve(): valid wins, refusal terminates, two parse failures -> parse_failed, none -> None",
              rx.resolve(["ok_off_topic"]) == "off_topic" and rx.resolve(["refusal"]) == "refused"
              and rx.resolve(["truncated"]) is None and rx.resolve(["truncated", "schema_invalid"]) == "parse_failed"
              and rx.resolve(["throttled"] * 7) == "error" and rx.resolve([]) is None)
        # ---- T9 W3 (2026-10-14): instrument v1.1S binding and retry policy v3 ------------ #
        mp = setup(tmp, ["T001_p01", "T001_p02", "T002_p01"])    # a fresh three-paragraph fixture
        a2m = tmp / "registered_a2.json"                           # the registered A2 sample: other paragraphs
        a2doc = {"kind": "A2_sample_manifest", "paragraphs": [{"paragraph_id": "A2X_p01"}]}
        a2doc["digest"] = rx.manifest_digest(a2doc)                # intact: kind and a digest its content reproduces
        a2m.write_text(json.dumps(a2doc))
        rx.sc.SAMPLE_MANIFEST = a2m
        GOOD_NESTED = {"outcome": "technologies_present", "relations": [
            {"technology": "wind", "wind_siting": "onshore",
             "frames": [{"frame": "jobs", "elements": ["F2a"], "evaluation": "positive"}], "comparison_target": "none"}]}
        BAD_NESTED = {"outcome": "technologies_present", "relations": [
            {"technology": "solar", "wind_siting": "onshore",          # subtype on the wrong technology: a relational rule
             "frames": [{"frame": "jobs", "elements": ["F2a"], "evaluation": "positive"}], "comparison_target": "none"}]}
        rec, fake, _, code = go(tmp, gate_plan() + [resp(BAD_NESTED), resp(GOOD_NESTED), resp(OFF), resp(OFF)],
                                manifest=str(mp), run_id="w3a", dev=True, restrict_split="A1", instrument="v1.1S")
        t = [r for r in recs(tmp, "w3a") if r.get("type") == "terminal" and r["paragraph_id"] == "T001_p01"]
        check("T9a v1.1S: an invalid first call is followed by ONE feedback retry that resolves the execution",
              code == 0 and t and t[0]["outcome"] == "technologies_present" and t[0]["codes"] == ["contract_invalid", "ok_technologies_present"]
              and len(fake.seen) == 7, f"code={code} calls={len(fake.seen)} codes={t[0]['codes'] if t else None}")
        first, retry = fake.seen[3], fake.seen[4]
        tr = retry["messages"][-1]["content"][0].get("toolResult") or {} if len(retry["messages"]) == 3 else {}
        check("T9b the retry is a three-turn request carrying the prior call and the validator's message; the first request is one turn",
              len(first["messages"]) == 1 and len(retry["messages"]) == 3 and retry["messages"][1]["content"][0]["toolUse"]["input"] == BAD_NESTED
              and tr.get("status") == "error" and "wind_siting" in tr.get("content", [{}])[0].get("text", "")
              and first["toolConfig"]["tools"][0]["toolSpec"].get("strict") is True and retry["toolConfig"] is first["toolConfig"]
              and retry["system"] == first["system"] and retry["inferenceConfig"] == first["inferenceConfig"])
        calls_j = [json.loads(l) for l in (rx.RUNS / "w3a_calls.jsonl").read_text(encoding="utf-8").splitlines()]
        raw_j = [json.loads(l) for l in (rx.RAW / "w3a.jsonl").read_text(encoding="utf-8").splitlines()]
        kinds = [r.get("retry_kind") for r in calls_j if r.get("type") == "attempt_started" and r["paragraph_id"] == "T001_p01"]
        fbk = [r for r in raw_j if r.get("type") == "feedback_request"]
        check("T9c the public journal records retry_kind first/feedback; the private journal keeps the exact feedback and prior call",
              kinds == ["first", "feedback"] and len(fbk) == 1 and fbk[0]["prior_call"]["input"] == BAD_NESTED and fbk[0]["problems"]
              and "wind_siting" in fbk[0]["feedback"] and all("feedback" not in r for r in calls_j))
        preds = list(csv.DictReader((rx.RUNS / "w3a_predictions.csv").open(encoding="utf-8"), delimiter=";"))
        check("T9d the nested payload is normalised into the registered row format (elements and evaluations columns)",
              len(preds) == 1 and preds[0]["technology"] == "wind" and preds[0]["F2a"] == "1" and preds[0]["eval_jobs"] == "positive"
              and preds[0]["wind_siting"] == "onshore" and preds[0]["no_frame_for_this_tech"] == "0")
        c = rec["execution_contract"]
        check("T9e the record and the contract name the instrument, the variant module digest, the prompt path and retry policy v3",
              rec["instrument"]["name"] == "v1.1S" and rec["instrument"]["retry_policy_version"] == "3" and c["retry_policy_version"] == "3"
              and c["instrument_version"] == "1.1S" and c["instrument_v11_sha256"] and c["prompt_path"].endswith("system-prompt_v1.1S.txt")
              and rec["prompt"]["path"].endswith("system-prompt_v1.1S.txt") and rec["instrument"]["pins"].endswith("pins_v1.1S.json")
              and c["retry_policy"]["parse"] == 1)
        gate_doc = json.loads((rx.ROOT / rec["preflight"]["artefact"]).read_text(encoding="utf-8")) if (rx.ROOT / rec["preflight"]["artefact"]).exists() \
            else json.loads(Path(rec["preflight"]["artefact"]).read_text(encoding="utf-8"))
        check("T9f the gate of a v1.1S run certified v1.1S", gate_doc.get("instrument") == "v1.1S" and gate_doc.get("provenance", {}).get("instrument_version") == "v1.1S")
        # v1.0 keeps the identical retry and policy v2, and carries no v1.1 digest
        rec, fake, _, code = go(tmp, gate_plan() + [resp(BAD_CONTRACT), resp(OFF), resp(OFF), resp(OFF)],
                                manifest=str(mp), run_id="w3g", dev=True, restrict_split="A1")
        calls_j = [json.loads(l) for l in (rx.RUNS / "w3g_calls.jsonl").read_text(encoding="utf-8").splitlines()]
        kinds = [r.get("retry_kind") for r in calls_j if r.get("type") == "attempt_started" and r["paragraph_id"] == "T001_p01"]
        check("T9g v1.0 (default): the retry is identical, retry policy v2, no v1.1 digest in the contract",
              code == 0 and len(fake.seen[4]["messages"]) == 1 and kinds == ["first", "identical"]
              and rec["instrument"]["name"] == "v1.0" and rec["instrument"]["retry_policy_version"] == "2"
              and rec["execution_contract"]["instrument_v11_sha256"] is None and rec["execution_contract"]["instrument_version"] == "1.0")
        # a failure without a payload (truncation) keeps the identical retry under v1.1S too
        rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF, stop="max_tokens", content=[]), resp(GOOD_NESTED), resp(OFF), resp(OFF)],
                                manifest=str(mp), run_id="w3h", dev=True, restrict_split="A1", instrument="v1.1S")
        t = [r for r in recs(tmp, "w3h") if r.get("type") == "terminal" and r["paragraph_id"] == "T001_p01"]
        check("T9h v1.1S: a truncated first attempt (no call to feed back) is retried identically, as under v2",
              code == 0 and t[0]["codes"] == ["truncated", "ok_technologies_present"] and len(fake.seen[4]["messages"]) == 1)
        # a second invalid call ends the execution: the budget is one retry, feedback or not
        rec, fake, _, code = go(tmp, gate_plan() + [resp(BAD_NESTED), resp(BAD_NESTED), resp(OFF), resp(OFF)],
                                manifest=str(mp), run_id="w3i", dev=True, restrict_split="A1", instrument="v1.1S")
        t = [r for r in recs(tmp, "w3i") if r.get("type") == "terminal" and r["paragraph_id"] == "T001_p01"]
        check("T9i v1.1S: two invalid calls end in parse_failed after exactly two attempts (unchanged budget)",
              code == 0 and t[0]["outcome"] == "parse_failed" and t[0]["n_attempts"] == 2 and len(fake.seen) == 7)
        # ---- T9T W4 (2026-10-14): instrument v1.2S -- typed representation, subtype rule, normalisation ----- #
        GOOD_T = {"outcome": "technologies_present", "relations": [
            {"technology": "wind", "technology_subtype": "onshore", "technology_versus": "none",
             "technology_frames": [{"frame": "jobs", "frame_elements": ["F2a"], "frame_evaluation": "positive"}]}]}
        BAD_T = {"outcome": "technologies_present", "relations": [
            {"technology": "solar", "technology_subtype": "onshore", "technology_versus": "none",     # a wind value on solar
             "technology_frames": [{"frame": "jobs", "frame_elements": ["F2a"], "frame_evaluation": "positive"}]}]}
        rec, fake, _, code = go(tmp, gate_plan() + [resp(BAD_T), resp(GOOD_T), resp(OFF), resp(OFF)],
                                manifest=str(mp), run_id="w3v12", dev=True, restrict_split="A1", instrument="v1.2S")
        t = [r for r in recs(tmp, "w3v12") if r.get("type") == "terminal" and r["paragraph_id"] == "T001_p01"]
        fin = [r for r in recs(tmp, "w3v12") if r.get("type") == "attempt_finished" and r["paragraph_id"] == "T001_p01"]
        preds = list(csv.DictReader((rx.RUNS / "w3v12_predictions.csv").open(encoding="utf-8"), delimiter=";"))
        check("T9T v1.2S: an incompatible subtype is a contract problem with the public id subtype_incompatible, the feedback retry resolves it, the rows are normalised",
              code == 0 and t and t[0]["codes"] == ["contract_invalid", "ok_technologies_present"] and fin[0]["contract_rules"] == ["subtype_incompatible"]
              and len(fake.seen[4]["messages"]) == 3 and len(preds) == 1 and preds[0]["technology"] == "wind" and preds[0]["wind_siting"] == "onshore"
              and preds[0]["F2a"] == "1" and preds[0]["eval_jobs"] == "positive" and preds[0]["comparison_target"] == "none"
              and rec["instrument"]["name"] == "v1.2S" and rec["execution_contract"]["instrument_version"] == "1.2S",
              f"code={code} codes={t[0]['codes'] if t else None} rules={fin[0].get('contract_rules') if fin else None}")
        # ---- T10 W3, Codex round 3: pending feedback across a resume, a transport failure, an unreplayable call;
        #      privacy sentinels under v1.1E; the selector's assessment on a real fixture run --------------- #
        # (1) crash between the invalid attempt and its retry: the resume must rebuild the feedback retry
        rec, fake, _, code = go(tmp, gate_plan() + [resp(BAD_NESTED), resp(GOOD_NESTED), resp(OFF), resp(OFF)],
                                manifest=str(mp), run_id="w3r", dev=True, restrict_split="A1", instrument="v1.1S")
        cj = rx.RUNS / "w3r_calls.jsonl"; rj = rx.RAW / "w3r.jsonl"
        lines = cj.read_text(encoding="utf-8").splitlines()
        keep = []
        for l in lines:
            keep.append(l)
            r = json.loads(l)
            if r.get("type") == "attempt_finished" and r.get("paragraph_id") == "T001_p01" and r.get("attempt_no") == 1:
                break
        cj.write_text("\n".join(keep) + "\n", encoding="utf-8")
        rlines = [l for l in rj.read_text(encoding="utf-8").splitlines() if json.loads(l).get("attempt_no") in (None, 1) and json.loads(l).get("type") != "feedback_request"]
        rj.write_text("\n".join(rlines) + "\n", encoding="utf-8")
        for f in ("w3r.json", "w3r_terminal.csv", "w3r_predictions.csv"):
            (rx.RUNS / f).unlink(missing_ok=True)
        rec, fake, _, code = go(tmp, gate_plan() + [resp(GOOD_NESTED), resp(OFF), resp(OFF)], manifest=str(mp), run_id="w3r",
                                dev=True, restrict_split="A1", instrument="v1.1S", resume=True)
        t = [r for r in recs(tmp, "w3r") if r.get("type") == "terminal" and r["paragraph_id"] == "T001_p01"]
        kinds = [r.get("retry_kind") for r in recs(tmp, "w3r") if r.get("type") == "attempt_started" and r["paragraph_id"] == "T001_p01"]
        check("T10a after a crash between the invalid attempt and its retry, the resume sends the FEEDBACK request (rebuilt from the private journal)",
              code == 0 and len(fake.seen[3]["messages"]) == 3 and t and t[0]["codes"] == ["contract_invalid", "ok_technologies_present"]
              and kinds == ["first", "feedback"], f"code={code} msgs={len(fake.seen[3]['messages']) if len(fake.seen) > 3 else None} kinds={kinds}")
        # (1b) the same crash with the private response missing: the resume refuses before any call
        rec, fake, _, code = go(tmp, gate_plan() + [resp(BAD_NESTED), resp(GOOD_NESTED), resp(OFF), resp(OFF)],
                                manifest=str(mp), run_id="w3m", dev=True, restrict_split="A1", instrument="v1.1S")
        cj = rx.RUNS / "w3m_calls.jsonl"; rj = rx.RAW / "w3m.jsonl"
        lines = cj.read_text(encoding="utf-8").splitlines(); keep = []
        for l in lines:
            keep.append(l)
            if json.loads(l).get("type") == "attempt_finished" and json.loads(l).get("attempt_no") == 1:
                break
        cj.write_text("\n".join(keep) + "\n", encoding="utf-8"); rj.write_text("", encoding="utf-8")
        for f in ("w3m.json", "w3m_terminal.csv", "w3m_predictions.csv"):
            (rx.RUNS / f).unlink(missing_ok=True)
        rec, fake, _, code = go(tmp, gate_plan() + [resp(GOOD_NESTED)], manifest=str(mp), run_id="w3m",
                                dev=True, restrict_split="A1", instrument="v1.1S", resume=True)
        last = recs(tmp, "w3m")[-1]
        check("T10b a resume whose pending invalid response is missing from the private journal refuses before the gate and before any call",
              code not in (0, None) and len(fake.seen) == 0 and last.get("type") == "run_finished" and last.get("outcome") == "refused_missing_private_response",
              f"code={code} calls={len(fake.seen)} last={last.get('outcome')}")
        # (2) a transport failure between the invalid response and the retry keeps the feedback request pending
        rec, fake, _, code = go(tmp, gate_plan() + [resp(BAD_NESTED), Throttle(), resp(GOOD_NESTED), resp(OFF), resp(OFF)],
                                manifest=str(mp), run_id="w3t", dev=True, restrict_split="A1", instrument="v1.1S")
        t = [r for r in recs(tmp, "w3t") if r.get("type") == "terminal" and r["paragraph_id"] == "T001_p01"]
        kinds = [r.get("retry_kind") for r in recs(tmp, "w3t") if r.get("type") == "attempt_started" and r["paragraph_id"] == "T001_p01"]
        check("T10c invalid -> throttled -> the feedback request is sent again, not the original envelope",
              code == 0 and t[0]["codes"] == ["contract_invalid", "throttled", "ok_technologies_present"] and kinds == ["first", "feedback", "feedback"]
              and len(fake.seen[4]["messages"]) == 3 and len(fake.seen[5]["messages"]) == 3, f"codes={t[0]['codes'] if t else None} kinds={kinds}")
        # (3) a prior call that cannot be replayed verbatim (another tool name) gets the identical retry, journalled
        wrong = resp(OFF, content=[{"toolUse": {"toolUseId": "tu_w", "name": "another_tool", "input": GOOD_NESTED}}])
        rec, fake, _, code = go(tmp, gate_plan() + [wrong, resp(GOOD_NESTED), resp(OFF), resp(OFF)],
                                manifest=str(mp), run_id="w3u", dev=True, restrict_split="A1", instrument="v1.1S")
        t = [r for r in recs(tmp, "w3u") if r.get("type") == "terminal" and r["paragraph_id"] == "T001_p01"]
        kinds = [r.get("retry_kind") for r in recs(tmp, "w3u") if r.get("type") == "attempt_started" and r["paragraph_id"] == "T001_p01"]
        raw_j = [json.loads(l) for l in (rx.RAW / "w3u.jsonl").read_text(encoding="utf-8").splitlines()]
        check("T10d an unreplayable prior call -> identical retry, retry_kind identical_unreplayable, reason journalled privately",
              code == 0 and t[0]["codes"] == ["schema_invalid", "ok_technologies_present"] and kinds == ["first", "identical_unreplayable"]
              and len(fake.seen[4]["messages"]) == 1 and any(r.get("type") == "feedback_skipped" for r in raw_j), f"codes={t[0]['codes'] if t else None} kinds={kinds}")
        # (4) privacy under v1.1E: sentinels planted in explanation, note, comparison_note and in the invalid call never reach a public file
        SENT = "ZQX-SENTINEL-93"
        good_e = {"explanation": f"wind jobs {SENT}-expl", "outcome": "technologies_present", "relations": [
            {"technology": "wind", "wind_siting": "onshore", "note": f"{SENT}-note", "comparison_note": f"{SENT}-cnote",
             "frames": [{"frame": "jobs", "elements": ["F2a"], "evaluation": "positive"}], "comparison_target": "none"}]}
        bad_e = {"explanation": f"{SENT}-badexpl", "outcome": "technologies_present", "relations": [
            {"technology": "solar", "wind_siting": "onshore", "note": f"{SENT}-badnote",
             "frames": [{"frame": "jobs", "elements": ["F2a"], "evaluation": "positive"}], "comparison_target": "none"}]}
        off_e = {"explanation": f"nothing {SENT}-off", "outcome": "off_topic", "relations": []}
        gate_e = [resp(off_e), resp(off_e, u=usage(write=2000)), resp(off_e, u=usage(read=2000))]   # E needs the explanation on the probes too
        rec, fake, _, code = go(tmp, gate_e + [resp(bad_e), resp(good_e), resp(off_e), resp(off_e)],
                                manifest=str(mp), run_id="w3p", dev=True, restrict_split="A1", instrument="v1.1E")
        public = "".join((rx.RUNS / f).read_text(encoding="utf-8") for f in ("w3p_calls.jsonl", "w3p_terminal.csv", "w3p_predictions.csv", "w3p.json"))
        private = (rx.RAW / "w3p.jsonl").read_text(encoding="utf-8")
        check("T10e v1.1E: no sentinel from explanations, notes or the invalid call reaches any public file; the private journal holds them",
              code == 0 and SENT not in public and private.count(SENT) >= 4 and rec["instrument"]["name"] == "v1.1E",
              f"code={code} public_hits={public.count(SENT)} private_hits={private.count(SENT)}")
        # (5) the selector's assessment on the real fixture runs: end to end through the evaluator, then mutations
        import w3, evaluate as ev, test_evaluate as te, argparse as _ap
        old = (w3.RUNS, w3.A1_MANIFEST, w3.LABELS, w3.REFERENCE_FILES, w3.SEAL, w3.SELECTION)
        gold1, gold2 = tmp / "A1_pass1_v1.0.csv", tmp / "A1_pass2_v1.0.csv"
        te.write_csv(gold1, [te.p1row("T001_p01", ["wind"], "T001"), te.p1row("T001_p02", [], "T001"), te.p1row("T002_p01", [], "T002")], te.P1_COLS)
        te.write_csv(gold2, [te.p2row("T001_p01", "wind", ["F2a"], {"jobs": "positive"}, subtype="onshore")], te.P2_COLS)
        w3.RUNS, w3.A1_MANIFEST, w3.LABELS, w3.REFERENCE_FILES = rx.RUNS, mp, tmp, [gold1.name, gold2.name]
        w3.SEAL, w3.SELECTION = rx.RUNS / "w3_seal.json", rx.RUNS / "w3_selection.json"
        try:
            def dev_metrics(run_id):
                a_ = _ap.Namespace(run_record=str(rx.RUNS / f"{run_id}.json"), manifest=str(mp), restrict_split="A1", gold_pass1=str(gold1),
                                   gold_pass2=str(gold2), dev=True, gold_manifest=None, skip_provenance=True, threshold=5)
                inp = ev.load_inputs(a_)
                res = ev.evaluate(inp, threshold=5, B=5, seed=1, dev=True)
                res["inputs"] = inp.get("provenance")
                d_ = rx.RUNS / "tables" / "dev" / run_id; d_.mkdir(parents=True, exist_ok=True)
                (d_ / "metrics.json").write_text(json.dumps(res, default=str))
                return res
            dev_metrics("w3a"); dev_metrics("w3p")
            seal = w3.seal_document({"v0": "none0", "v1": "w3a", "v2": "w3p"}, now="2026-01-01T00:00:00+00:00", k=1,
                                    evaluation={"threshold": 5, "B": 5, "seed": 1})
            c = w3.assess("w3a", "v1", seal)
            check("T10f assess(): a certified, complete fixture run with real evaluator metrics is assessed, eligible and scored (exact fraction)",
                  c["assessed"] and c["eligible"] and c["scoreable"] and c["S"] is not None and c["problems"] == []
                  and c["I"] == 1 and c["retries"] == 1 and c["feedback_retries"] == 1 and c["terminal_outcomes"] == {"technologies_present": 1, "off_topic": 2},
                  f"{c['problems']} S={c['S']} I={c['I']} outcomes={c['terminal_outcomes']}")
            c3 = w3.assess("w3p", "v2", seal)
            check("T10f2 the v1.1E fixture run is assessed in slot v2, eligible, with its invalid first response counted", c3["eligible"] and c3["I"] == 1, str(c3["problems"]))
            try:
                w3.assess("w3a", "v0", seal); wrong_slot = False
            except SystemExit:
                wrong_slot = True
            late = dict(seal, sealed_at="2099-01-01T00:00:00+00:00")
            try:
                w3.assess("w3a", "v1", late); pre_seal = False
            except SystemExit:
                pre_seal = True
            check("T10g assess() refuses a run in the wrong roster slot and a run that started before the seal", wrong_slot and pre_seal)
            # mutations of the evidence
            mpath = rx.RUNS / "tables" / "dev" / "w3a" / "metrics.json"; morig = mpath.read_text(encoding="utf-8")
            m_ = json.loads(morig); m_["inputs"]["reference"][gold1.name] = "0" * 64; mpath.write_text(json.dumps(m_))
            try:
                w3.assess("w3a", "v1", seal); ref_caught = False
            except SystemExit:
                ref_caught = True
            m_ = json.loads(morig); m_["threshold"] = 1; mpath.write_text(json.dumps(m_))
            try:
                w3.assess("w3a", "v1", seal); thr_caught = False
            except SystemExit:
                thr_caught = True
            mpath.write_text(morig, encoding="utf-8")
            check("T10h metrics scored against another reference or under another threshold are refused", ref_caught and thr_caught)
            tp = rx.RUNS / "w3a_terminal.csv"; orig = tp.read_text(encoding="utf-8")
            rows = orig.splitlines(); tp.write_text("\n".join(rows + [rows[-1]]) + "\n", encoding="utf-8")
            c2 = w3.assess("w3a", "v1", seal); tp.write_text(orig, encoding="utf-8")
            check("T10i a public file that differs from the run record makes the candidate not assessed (checked before the journal is read)",
                  not c2["assessed"] and not c2["eligible"] and any("differs from the run record" in p_ for p_ in c2["problems"]), str(c2["problems"]))
            cj = rx.RUNS / "w3a_calls.jsonl"; jorig = cj.read_text(encoding="utf-8")
            cj.write_text(jorig.replace('"code": "contract_invalid"', '"code": "ok_off_topic"', 1), encoding="utf-8")
            c4 = w3.assess("w3a", "v1", seal); cj.write_text(jorig, encoding="utf-8")
            check("T10i2 an edited journal (first verdict changed) is caught by the digest check, not silently rescored", not c4["assessed"] and c4["I"] is None)
            rec_w = json.loads((rx.RUNS / "w3a.json").read_text(encoding="utf-8")); rec_w["execution_contract"]["repeats"] = 2
            (rx.RUNS / "w3a_tampered.json").write_text(json.dumps(rec_w))
            try:
                w3.permitted_run_id(seal, "v1", "w3a_r2"); r2 = False
            except SystemExit:
                r2 = True
            check("T10i3 only the one restart _r1 is permitted, never _r2", r2)
            # (6) the launch guard for a revised instrument on the registered sample
            rx.sc.SAMPLE_MANIFEST = mp                         # the registered sample IS the fixture now
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="w3g1", dev=True, restrict_split="A1", instrument="v1.1S")
            check("T10m a development run of a revised instrument over registered A2 paragraphs is refused before any call",
                  code not in (0, None) and "development mode" in str(code) and len(fake.seen) == 0, f"code={str(code)[:80]} calls={len(fake.seen)}")
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="w3g2", instrument="v1.1S", repeats=3)
            check("T10n a reportable run of a revised instrument without a seal is refused", code not in (0, None) and "seal" in str(code) and len(fake.seen) == 0, str(code)[:100])
            w3.write_seal({"v0": "none0", "v1": "w3a", "v2": "w3p"})
            sealed = json.loads(w3.SEAL.read_text(encoding="utf-8"))
            (rx.RUNS / "w3_selection.json").write_text(json.dumps({"kind": "w3_selection", "seal_digest": sealed["digest"],
                                                                   "decision": {"outcome": "selected", "selected": "v1.1S"}}))
            a3 = tmp / "A3-protocol.md"; a3.write_text("# A3 protocol (fixture)\n")
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="w3g3", instrument="v1.1S", repeats=3)
            check("T10o the guard refuses without the author's recorded decision and the A3 protocol", code not in (0, None) and "decision" in str(code))
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF), resp(OFF), resp(OFF)] * 3, manifest=str(mp), run_id="w3g4", instrument="v1.1S", repeats=3,
                                    w3_author_decision="Nils Wagner, 2026-10-14 (fixture)", a3_protocol=str(a3))
            check("T10p with an intact seal, a bound selection, Sonnet k = 3, the decision and the A3 protocol the run proceeds as 'exploratory reused A2' and binds the digests",
                  code == 0 and rec["analysis_status"] == "exploratory reused A2" and rec["w3_authorisation"]["seal_digest"] == sealed["digest"]
                  and rec["execution_contract"]["w3_authorisation"] == rec["w3_authorisation"] and ev.analysis_status_of(rec) == "exploratory reused A2",
                  f"code={str(code)[:120]}")
            (rx.RUNS / "w3_selection.json").write_text(json.dumps({"kind": "w3_selection", "seal_digest": sealed["digest"],
                                                                   "decision": {"outcome": "no_upgrade", "selected": "v1.0"}}))
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="w3g5", instrument="v1.1S", repeats=3,
                                    w3_author_decision="x", a3_protocol=str(a3))
            check("T10q a selection outcome other than 'selected' never authorises the run", code not in (0, None) and "selected" in str(code))
            (rx.RUNS / "w3_selection.json").write_text(json.dumps({"kind": "w3_selection", "seal_digest": sealed["digest"],
                                                                   "decision": {"outcome": "selected", "selected": "v1.1S"}}))
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="w3g6", instrument="v1.1S", repeats=1,
                                    w3_author_decision="x", a3_protocol=str(a3))
            check("T10r k = 1 is refused for the exploratory A2 run even with a valid 'selected' record, before any call",
                  code not in (0, None) and "k = 3" in str(code) and len(fake.seen) == 0, f"code={str(code)[:100]} calls={len(fake.seen)}")
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="w3g7", instrument="v1.1S", repeats=3, model="opus",
                                    w3_author_decision="x", a3_protocol=str(a3))
            check("T10s another model is refused for the exploratory A2 run", code not in (0, None) and "Sonnet" in str(code) and len(fake.seen) == 0)
            bad_a2 = tmp / "broken_a2.json"; bad_a2.write_text("{not json")
            rx.sc.SAMPLE_MANIFEST = bad_a2
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="w3g8", dev=True, restrict_split="A1", instrument="v1.1S")
            check("T10t a development launch of a revised instrument is refused when A2 membership cannot be established (malformed registered manifest)",
                  code not in (0, None) and "cannot be established" in str(code) and len(fake.seen) == 0, str(code)[:100])
            edited = json.loads(mp.read_text()); edited["paragraphs"] = edited["paragraphs"][1:]   # an identity removed, digest kept
            ed_a2 = tmp / "edited_a2.json"; ed_a2.write_text(json.dumps(edited)); rx.sc.SAMPLE_MANIFEST = ed_a2
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)], manifest=str(mp), run_id="w3g9", dev=True, restrict_split="A1", instrument="v1.1S")
            check("T10u a registered manifest whose content no longer reproduces its digest is not accepted as the membership source",
                  code not in (0, None) and ("cannot be established" in str(code) or "does not match its stored digest" in str(code))
                  and len(fake.seen) == 0, str(code)[:100])
            # (7) W4: the executable selection workflow on real fixture runs -- seal -> load -> assess -> select
            import w4
            rx.sc.SAMPLE_MANIFEST = a2m                          # the fixture paragraphs are development paragraphs again
            dev_metrics("w3g"); dev_metrics("w3v12")             # V0 = the v1.0 fixture run, V1 = the v1.2S fixture run
            w4.SEAL, w4.SELECTION = rx.RUNS / "w4_seal.json", rx.RUNS / "w4_selection.json"
            try:
                w4.write_seal({"v0": "w3g", "v1": "w3g"}); dup = False
            except SystemExit:
                dup = True
            w3.K = 1
            try:
                w4.write_seal({"v0": "w3g", "v1": "w3v12"})
                w4doc = json.loads(w4.SEAL.read_text(encoding="utf-8"))
                w4doc["evaluation"] = {"threshold": 5, "B": 5, "seed": 1}; w4doc["sealed_at"] = "2026-01-01T00:00:00+00:00"
                w4doc["digest"] = w3.digest_of(w4doc); w4.SEAL.write_text(json.dumps(w4doc))   # fixture settings, re-digested
                loaded = w4.load_seal()
                out4 = w4.select()
                check("T10v W4: duplicate identifiers refused; a genuine W4 seal loads; the selection runs end to end on the fixture runs",
                      dup and loaded["kind"] == "w4_seal" and out4["decision"]["outcome"] in ("selected", "retained", "comparison_inconclusive")
                      and out4["candidates"]["v1"]["assessed"] and out4["candidates"]["v0"]["assessed"] and "A1-only" in out4["note"],
                      f"dup={dup} decision={out4['decision'] if 'out4' in dir() else None}")
                w3doc = json.loads(w4.SEAL.read_text(encoding="utf-8")); w3doc["kind"] = "w3_seal"; w3doc["digest"] = w3.digest_of(w3doc)
                w4.SEAL.write_text(json.dumps(w3doc))
                try:
                    w4.load_seal(); wrong_kind = False
                except SystemExit:
                    wrong_kind = True
                check("T10w W4 refuses a seal of another phase", wrong_kind)
            finally:
                w3.K = 3
        finally:
            w3.RUNS, w3.A1_MANIFEST, w3.LABELS, w3.REFERENCE_FILES, w3.SEAL, w3.SELECTION = old
            rx.sc.SAMPLE_MANIFEST = a2m
        # ---- T11 A3 embargo (specification §4b, 2026-10-14; Codex A3 round 2) ------------------ #
        import hashlib as _hl
        mp = setup(tmp, ["T001_p01", "T001_p02", "T002_p01"])
        frame_digest = json.loads((tmp / "frame.json").read_text())["digest"]
        a2m_t11 = tmp / "registered_a2_t11.json"                   # A2 holds T001_p01 and T002_p01: the rule then admits only fillers
        a2doc11 = {"kind": "A2_sample_manifest", "frame_manifest_digest": frame_digest, "freeze_event_hash": "frz",
                   "paragraphs": [{"paragraph_id": "T001_p01", "article_id": "T001", "digest": h16(TEXTS["T001_p01"])},
                                  {"paragraph_id": "T002_p01", "article_id": "T002", "digest": h16(TEXTS["T002_p01"])}]}
        a2doc11["digest"] = rx.manifest_digest(a2doc11); a2m_t11.write_text(json.dumps(a2doc11))
        rx.sc.SAMPLE_MANIFEST = a2m_t11
        old_a3 = (rx.A3_SAMPLE_MANIFEST, rx.LABELS, rx.SESSIONS_CSV, rx.a3_committed)
        labels = tmp / "labels_a3"; labels.mkdir(exist_ok=True)
        rx.LABELS = labels; rx.A3_SAMPLE_MANIFEST = labels / "A3_sample_manifest.json"; rx.SESSIONS_CSV = labels / "sessions.csv"
        PID = "T001_p02"; TH = h16(TEXTS[PID])

        def a3doc(paragraphs, stale=False):
            d = {"kind": "A3_sample_manifest", "frame_manifest_digest": frame_digest, "paragraphs": paragraphs}
            d["digest"] = rx.manifest_digest(d) if not stale else "0" * 64
            rx.A3_SAMPLE_MANIFEST.write_text(json.dumps(d))

        SESS_HEAD = "session;codebook_version;n_rows;annotators;coded_at;sittings;labels_file;labels_sha256;source_sheet;finalized_on\n"

        def evidence(rows, *, swap=False, bad_hash=False, extra_key=False, foreign=False):
            """rows: [(paragraph_id, text_sha256, [technologies])] -- valid finalisation evidence unless a flaw is asked for."""
            p1 = ["paragraph_id;article_id;tech_wind;tech_solar;text_sha256"] + [
                f"{pid};{pid.split('_')[0]};{1 if 'wind' in techs else 0};{1 if 'solar' in techs else 0};{th_}" for pid, th_, techs in rows]
            p2 = ["row_id;paragraph_id;technology"] + [f"{pid}_{tech};{pid};{tech}" for pid, _, techs in rows for tech in techs]
            if extra_key:
                p2.append("X;T001_p01;solar")
            (labels / "A3_pass1_v1.0.csv").write_text("\n".join(p1) + "\n", encoding="utf-8-sig")
            (labels / "A3_pass2_v1.0.csv").write_text("\n".join(p2) + "\n", encoding="utf-8-sig")
            sha = lambda n: _hl.sha256((labels / n).read_bytes()).hexdigest()[:16]
            f1, f2 = ("A3_pass2_v1.0.csv", "A3_pass1_v1.0.csv") if swap else ("A3_pass1_v1.0.csv", "A3_pass2_v1.0.csv")
            h1 = "deadbeefdeadbeef" if bad_hash else sha("A3_pass1_v1.0.csv")
            (labels / "sessions.csv").write_text(SESS_HEAD + f"A3_pass1;1.0;{len(rows)};NW;2026-10-20;1;{f1};{h1};s;2026-10-20\n"
                                                 f"A3_pass2;1.0;{len(p2) - 1};NW;2026-10-21;1;{f2};{sha('A3_pass2_v1.0.csv')};s;2026-10-21\n", encoding="utf-8-sig")
            sm = [{"paragraph_id": pid, "article_id": pid.split("_")[0], "text_sha256": th_} for pid, th_, _ in rows]
            if foreign:
                sm.append({"paragraph_id": "T002_p01", "article_id": "T002", "text_sha256": h16(TEXTS["T002_p01"])})
            (labels / "A3_manifest.json").write_text(json.dumps({"session": "A3", "paragraphs": sm}))

        def clear_evidence():
            for n in ("A3_pass1_v1.0.csv", "A3_pass2_v1.0.csv", "sessions.csv", "A3_manifest.json"):
                (labels / n).unlink(missing_ok=True)

        try:
            rec, fake, _, code = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e0", dev=True, restrict_split="A1")
            check("T11a without an A3 listing the embargo is inactive and the record and contract say so",
                  code == 0 and rec["a3_embargo"] is None and rec["execution_contract"]["a3_embargo"] is None)
            a3doc([{"paragraph_id": PID, "article_id": "T001", "digest": h16(TEXTS[PID]), "text_sha256": TH}])
            _, f1, _, c1 = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e1", dev=True, restrict_split="A1")
            _, f1b, _, c1b = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e1b", dev=True, restrict_split="A1", instrument="v1.1S")
            check("T11b a run touching an A3 paragraph is refused before any call -- v1.0 and a revised instrument, development mode",
                  c1 != 0 and c1b != 0 and not f1.seen and not f1b.seen, f"codes {c1} {c1b} calls {len(f1.seen)} {len(f1b.seen)}")
            rows = [(PID, TH, ["wind"])]
            rx.a3_committed = lambda paths: (True, "tracked and clean (test)")
            flaws = {}
            for name, kw in (("swapped filenames", dict(swap=True)), ("wrong hash", dict(bad_hash=True)),
                             ("extra pass-2 key", dict(extra_key=True)), ("foreign paragraph in the session manifest", dict(foreign=True))):
                evidence(rows, **kw)
                _, fk, _, ck = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e2" + name[:2].replace(" ", ""), dev=True, restrict_split="A1")
                flaws[name] = (ck != 0 and not fk.seen)
            evidence([("T001_p01", h16(TEXTS["T001_p01"]), ["solar"])])        # covers the wrong paragraph
            _, fc, _, cc = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e2cov", dev=True, restrict_split="A1")
            flaws["incomplete coverage"] = (cc != 0 and not fc.seen)
            evidence(rows); rx.a3_committed = lambda paths: (False, "not tracked (test)")
            _, fg, _, cg = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e2git", dev=True, restrict_split="A1")
            flaws["not committed"] = (cg != 0 and not fg.seen)
            check("T11c a development run on an A3 paragraph is refused without a call whatever the evidence state (the evidence checks themselves are exercised by T12c2)",
                  all(flaws.values()), str(flaws))
            rx.a3_committed = lambda paths: (True, "tracked and clean (test)")
            evidence(rows)
            _, f4, _, c4 = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e4", dev=True, restrict_split="A1")
            check("T11d valid finalisation evidence does not open A3 to development runs: still refused without a call (A3 is an evaluation set)",
                  c4 != 0 and not f4.seen, f"code {c4}")
            clear_evidence()
            a3doc([{"paragraph_id": "ZZZ_p01", "article_id": "ZZZ", "digest": "nomatch", "text_sha256": "nomatch"}])
            rec5, _, _, c5 = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e5", dev=True, restrict_split="A1")
            a3doc([{"paragraph_id": "ZZZ_p01", "article_id": "ZZZ", "digest": "nomatch", "text_sha256": "nomatch"}], stale=True)
            _, f5b, _, c5b = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e5b", dev=True, restrict_split="A1")
            check("T11e a listing that shares no paragraph with the run proceeds (touches_a3 false); the same listing under a stale digest refuses",
                  c5 == 0 and (rec5 or {}).get("a3_embargo", {}).get("touches_a3") is False and c5b != 0 and not f5b.seen, f"codes {c5} {c5b}")
            a3doc([{"paragraph_id": "ZZZ_p01", "article_id": "ZZZ", "digest": h16(TEXTS[PID]), "text_sha256": "nomatch"}])
            _, f6, _, c6 = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e6", dev=True, restrict_split="A1")
            a3doc([{"paragraph_id": "ZZZ_p01", "article_id": "ZZZ", "digest": "nomatch", "text_sha256": TH}])
            _, f6b, _, c6b = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e6b", dev=True, restrict_split="A1")
            check("T11f membership by digest only or by text hash only is caught", c6 != 0 and not f6.seen and c6b != 0 and not f6b.seen)
            rx.A3_SAMPLE_MANIFEST.write_text("{broken")
            _, f7, _, c7 = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e7", dev=True, restrict_split="A1")
            d_ = {"kind": "A3_sample_manifest", "frame_manifest_digest": "other", "paragraphs": [{"paragraph_id": "Q_p01", "article_id": "Q", "digest": "x", "text_sha256": "y"}]}
            d_["digest"] = rx.manifest_digest(d_); rx.A3_SAMPLE_MANIFEST.write_text(json.dumps(d_))
            _, f7b, _, c7b = go(tmp, gate_plan() + [resp(OFF)] * 3, manifest=str(mp), run_id="e7b", dev=True, restrict_split="A1")
            check("T11g an unreadable listing, or one not bound to the frozen frame, refuses every run", c7 != 0 and not f7.seen and c7b != 0 and not f7b.seen)
        finally:
            rx.A3_SAMPLE_MANIFEST, rx.LABELS, rx.SESSIONS_CSV, rx.a3_committed = old_a3
            rx.sc.SAMPLE_MANIFEST = a2m
        # ---- T12 A3 fresh-sample evaluation: contract, runner, evaluator, figures (specification §4b) ------ #
        import evaluate as ev, test_evaluate as te, figures as fg, argparse as _ap, hashlib as _hl
        TEXTS["T003_p01"] = "Der Netzausbau für Windstrom im Norden verzögert sich laut Betreiber um weitere Jahre."
        TEXTS["T004_p01"] = "A fifth paragraph about grid connection queues that the calibration articles touched."
        mp = setup(tmp, ["T001_p01", "T001_p02"])              # ALL_paragraphs for every fixture text; A2 = article T001
        TEXTS["T004_p01"] = "A fifth paragraph about grid connection queues that the calibration articles touched."
        def fpara(pid, lang, exposure="clean_article"):
            return {"paragraph_id": pid, "article_id": pid.split("_")[0], "digest": h16(TEXTS[pid]), "text_sha256": h16(TEXTS[pid]),
                    "lang": lang, "outlet": "taz", "country": "DE", "exposure": exposure}
        frame = {"kind": "A2_frame_manifest", "pre_freeze_head": "pfh", "n_eligible": 5, "eligible_checksum": "ec",
                 "paragraphs": [fpara("T001_p01", "de"), fpara("T001_p02", "en"), fpara("T002_p01", "en"), fpara("T003_p01", "de"),
                                fpara("T004_p01", "en", "sibling_calibration_article")]}
        frame["digest"] = rx.sc.canonical_digest(frame)
        a2 = {"kind": "A2_sample_manifest", "frame_manifest_digest": frame["digest"], "freeze_event_hash": "frz", "n": 2,
              "by_language": {"de": 1, "en": 1}, "design": {"target": {"de": 1, "en": 1}, "cap_used": {"de": 24, "en": 24}, "cap_registered": 24},
              "paragraphs": [dict(fpara("T001_p01", "de"), pi=0.5, weight=2.0), dict(fpara("T001_p02", "en"), pi=0.5, weight=2.0)]}
        a2["digest"] = rx.manifest_digest(a2)
        A3P = [dict(fpara("T002_p01", "en"), stratum="en|taz", pi=1.0, weight=1.0, sitting_order=1),
               dict(fpara("T003_p01", "de"), stratum="de|taz", pi=1.0, weight=1.0, sitting_order=2)]
        def listing(**over):
            d = {"kind": "A3_sample_manifest", "created": "2026-10-14", "frame_manifest_digest": frame["digest"], "freeze_event_hash": "frz",
                 "excludes_sample_manifest": {"kind": "A2_sample_manifest", "digest": a2["digest"]},
                 "design": {"census": True, "pi": 1.0, "weight": 1.0, "seeds": {"order": 20261014}},
                 "exposure_audit": {"clean": True, "overlap": {}, "files": [{"file": "x", "sha256": "0" * 64, "article_ids": 0}]},
                 "n": 2, "by_language": {"de": 1, "en": 1}, "n_articles": 2, "by_article": {"T002": 1, "T003": 1},
                 "strata": [{"stratum": "de|taz", "n": 1}, {"stratum": "en|taz", "n": 1}], "paragraphs": [dict(q) for q in A3P]}
            d.update(over); d["digest"] = rx.manifest_digest(d); return d
        d3 = tmp / "a3fix"; d3.mkdir(exist_ok=True)
        (d3 / "frame.json").write_text(json.dumps(frame))
        (d3 / "registry.json").write_text(json.dumps({"assignment_log": [{"seq": 1, "kind": "assign", "h": "pfh"},
                                                                          {"seq": 2, "kind": "a2_frozen", "audit_ref": frame["digest"], "h": "frz", "eligible_checksum": "ec"}]}))
        (d3 / "A2_sample_manifest.json").write_text(json.dumps(a2))
        a3p = d3 / "A3_sample_manifest.json"; a3p.write_text(json.dumps(listing()))
        sessp = d3 / "A3_manifest_dev.json"; sessp.write_text(json.dumps({"session": "A3", "paragraphs": [dict(q, split="A3") for q in A3P]}))
        labels = d3 / "labels"; labels.mkdir(exist_ok=True)
        old_a3 = (rx.sc.FRAME_MANIFEST, rx.sc.REGISTRY_FILE, rx.sc.SAMPLE_MANIFEST, rx.sc.A3_SAMPLE_MANIFEST, rx.A3_SAMPLE_MANIFEST, rx.LABELS,
                  rx.SESSIONS_CSV, rx.a3_committed, ev.FRAME_MANIFEST, ev.REGISTRY_FILE, ev.RUNS, ev.GIT_LAST_CHANGE)
        rx.sc.FRAME_MANIFEST = ev.FRAME_MANIFEST = d3 / "frame.json"; rx.sc.REGISTRY_FILE = ev.REGISTRY_FILE = d3 / "registry.json"
        rx.sc.SAMPLE_MANIFEST = d3 / "A2_sample_manifest.json"; rx.sc.A3_SAMPLE_MANIFEST = rx.A3_SAMPLE_MANIFEST = a3p
        rx.LABELS = labels; rx.SESSIONS_CSV = labels / "sessions.csv"; ev.RUNS = rx.RUNS
        try:
            # (1) the census contract
            try:
                rx.sc.verify_a3_sample(listing(), listing_path=a3p); c_ok = True; c_err = ""
            except rx.sc.ContractError as e_:
                c_ok = False; c_err = str(e_)
            passed_bad = []
            variants = {
                "incomplete census": listing(paragraphs=[dict(A3P[0])], n=1, by_language={"en": 1}, n_articles=1, by_article={"T002": 1}, strata=[{"stratum": "en|taz", "n": 1}]),
                "wrong article counts": listing(n_articles=1),
                "A2 paragraph listed": listing(paragraphs=[dict(q) for q in A3P] + [dict(fpara("T001_p02", "en"), stratum="en|taz", pi=1.0, weight=1.0, sitting_order=3)], n=3, by_language={"de": 1, "en": 2}, n_articles=3, by_article={"T001": 1, "T002": 1, "T003": 1}, strata=[{"stratum": "de|taz", "n": 1}, {"stratum": "en|taz", "n": 2}]),
                "weight not one": listing(paragraphs=[dict(A3P[0], weight=2.0), dict(A3P[1])]),
                "dirty audit": listing(exposure_audit={"clean": False, "overlap": {"f": ["T002"]}, "files": [{"file": "f", "sha256": "0" * 64, "article_ids": 1}]}),
                "other seed": listing(design={"census": True, "pi": 1.0, "weight": 1.0, "seeds": {"order": 1}}),
                "other A2 manifest named": listing(excludes_sample_manifest={"kind": "A2_sample_manifest", "digest": "0" * 64}),
            }
            for name, v in variants.items():
                a3p.write_text(json.dumps(v))
                try:
                    rx.sc.verify_a3_sample(v, listing_path=a3p); passed_bad.append(name)
                except rx.sc.ContractError:
                    pass
            a3p.write_text(json.dumps(listing()))
            a2_other = {k_: v_ for k_, v_ in a2.items() if k_ != "digest"}; a2_other["frame_manifest_digest"] = "0" * 64
            a2_other["digest"] = rx.manifest_digest(a2_other)
            (d3 / "A2_sample_manifest.json").write_text(json.dumps(a2_other))
            try:
                rx.sc.verify_a3_sample(listing(excludes_sample_manifest={"kind": "A2_sample_manifest", "digest": a2_other["digest"]}), listing_path=a3p); passed_bad.append("A2 manifest from another frame")
            except rx.sc.ContractError:
                pass
            (d3 / "A2_sample_manifest.json").write_text(json.dumps(a2))
            check("T12a the census contract accepts the listing and refuses an incomplete census, wrong article counts, an A2 paragraph, a weight other than one, a dirty audit, another seed, another A2 manifest and an A2 manifest from another frame",
                  c_ok and not passed_bad, f"ok={c_ok} {c_err} passed_bad={passed_bad}")
            # (2) finalisation evidence for both A3 paragraphs (full-column reference files)
            SESS_HEAD = "session;codebook_version;n_rows;annotators;coded_at;sittings;labels_file;labels_sha256;source_sheet;finalized_on\n"
            p1a = te.p1row("T002_p01", ["wind"], "T002"); p1a["text_sha256"] = h16(TEXTS["T002_p01"])
            p1b = te.p1row("T003_p01", ["wind"], "T003"); p1b["text_sha256"] = h16(TEXTS["T003_p01"])
            te.write_csv(labels / "A3_pass1_v1.0.csv", [p1a, p1b], te.P1_COLS)
            te.write_csv(labels / "A3_pass2_v1.0.csv", [te.p2row("T002_p01", "wind", ["F8a"], {"regulation": "negative"}, subtype="onshore"),
                                                        te.p2row("T003_p01", "wind", ["F7b"], {"siting": "negative"}, subtype="onshore")], te.P2_COLS)
            sha16 = lambda n: _hl.sha256((labels / n).read_bytes()).hexdigest()[:16]
            (labels / "sessions.csv").write_text(SESS_HEAD + f"A3_pass1;1.0;2;NW;2026-10-20;1;A3_pass1_v1.0.csv;{sha16('A3_pass1_v1.0.csv')};s;2026-10-20\n"
                                                 f"A3_pass2;1.0;2;NW;2026-10-21;1;A3_pass2_v1.0.csv;{sha16('A3_pass2_v1.0.csv')};s;2026-10-21\n", encoding="utf-8-sig")
            (labels / "A3_manifest.json").write_text(json.dumps({"session": "A3", "paragraphs": [
                {"paragraph_id": q["paragraph_id"], "article_id": q["article_id"], "text_sha256": q["text_sha256"]} for q in A3P]}))
            rx.a3_committed = lambda paths: (True, "tracked and clean (test)")
            # (3) the runner
            _, f1, _, c1 = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(sessp), run_id="a3d", dev=True, restrict_split="A3", repeats=3)
            check("T12b after finalisation a development run over A3 paragraphs is still refused without a call", c1 != 0 and not f1.seen, f"code {c1}")
            _, f2, _, c2 = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(a3p), run_id="a3k", dev=False, repeats=1)
            _, f3, _, c3 = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(a3p), run_id="a3i", dev=False, repeats=3, instrument="v1.1S")
            check("T12c an A3 run needs k = 3 and the registered instrument; otherwise refused before any call",
                  c2 != 0 and c3 != 0 and not f2.seen and not f3.seen, f"codes {c2} {c3}")
            # (3b) the finalisation evidence is exercised on the reportable path: every flaw refuses before a call
            def write_evidence(*, swap=False, bad_hash=False, extra_key=False, foreign=False, drop=False):
                rows1 = [p1a] if drop else [p1a, p1b]
                te.write_csv(labels / "A3_pass1_v1.0.csv", rows1, te.P1_COLS)
                rows2 = [te.p2row("T002_p01", "wind", ["F8a"], {"regulation": "negative"}, subtype="onshore"),
                         te.p2row("T003_p01", "wind", ["F7b"], {"siting": "negative"}, subtype="onshore")]
                if extra_key:
                    rows2.append(te.p2row("T001_p01", "solar", ["F2c"], {"jobs": "positive"}))
                te.write_csv(labels / "A3_pass2_v1.0.csv", rows2, te.P2_COLS)
                f1_, f2_ = ("A3_pass2_v1.0.csv", "A3_pass1_v1.0.csv") if swap else ("A3_pass1_v1.0.csv", "A3_pass2_v1.0.csv")
                h1_ = "deadbeefdeadbeef" if bad_hash else sha16("A3_pass1_v1.0.csv")
                (labels / "sessions.csv").write_text(SESS_HEAD + f"A3_pass1;1.0;2;NW;2026-10-20;1;{f1_};{h1_};s;2026-10-20\n"
                                                     f"A3_pass2;1.0;2;NW;2026-10-21;1;{f2_};{sha16('A3_pass2_v1.0.csv')};s;2026-10-21\n", encoding="utf-8-sig")
                sm_ = [{"paragraph_id": q["paragraph_id"], "article_id": q["article_id"], "text_sha256": q["text_sha256"]} for q in A3P]
                if foreign:
                    sm_.append({"paragraph_id": "T001_p01", "article_id": "T001", "text_sha256": h16(TEXTS["T001_p01"])})
                (labels / "A3_manifest.json").write_text(json.dumps({"session": "A3", "paragraphs": sm_}))
            flaws2 = {}
            for name, kw in (("swapped files", dict(swap=True)), ("wrong hash", dict(bad_hash=True)), ("extra pass-2 key", dict(extra_key=True)),
                             ("foreign paragraph", dict(foreign=True)), ("incomplete coverage", dict(drop=True))):
                write_evidence(**kw)
                _, fx, _, cx = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(a3p), run_id="a3f" + name[:3].replace(" ", ""), dev=False, repeats=3)
                flaws2[name] = (cx != 0 and not fx.seen)
            write_evidence(); rx.a3_committed = lambda paths: (False, "not tracked (test)")
            _, fx, _, cx = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(a3p), run_id="a3fgit", dev=False, repeats=3)
            flaws2["not committed"] = (cx != 0 and not fx.seen)
            rx.a3_committed = lambda paths: (True, "tracked and clean (test)"); write_evidence()
            check("T12c2 on the reportable A3 path every flaw of the finalisation evidence (swapped files, wrong hash, extra pass-2 key, foreign paragraph, incomplete coverage, not committed) refuses before a call",
                  all(flaws2.values()), str(flaws2))
            # (3c) a trimmed, re-digested listing cannot release a paragraph the §4b rule protects
            a3p.write_text(json.dumps(listing(paragraphs=[dict(A3P[0])], n=1, by_language={"en": 1}, n_articles=1, by_article={"T002": 1}, strata=[{"stratum": "en|taz", "n": 1}])))
            sess2 = d3 / "A3_manifest_dev2.json"; sess2.write_text(json.dumps({"session": "A3", "paragraphs": [dict(A3P[1], split="A3")]}))
            _, ft, _, ct = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(sess2), run_id="a3trim", dev=True, restrict_split="A3", repeats=3)
            a3p.write_text(json.dumps(listing()))
            check("T12c3 a trimmed, re-digested listing does not release the dropped paragraph: membership is the listing plus what the rule admits from the frame", ct != 0 and not ft.seen, f"code {ct}")
            rec, fk, _, ck = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(a3p), run_id="a3run", dev=False, repeats=3)
            emb = (rec or {}).get("a3_embargo") or {}
            started = [r for r in recs(tmp, "a3run") if r.get("type") == "run_started"]
            check("T12d the A3 census runs reportably under v1.0 with k = 3; status fresh-sample evaluation; the embargo evidence is in the record, the contract and the start event",
                  ck == 0 and rec["reportable"] and rec["analysis_status"] == "fresh-sample evaluation" and emb.get("touches_a3") is True
                  and emb.get("paragraphs_touched") == 2 and set(emb.get("finalised", {})) == set(rx.A3_FINAL.values())
                  and rec["execution_contract"]["a3_embargo"] == emb and started and started[0].get("a3_embargo") == emb,
                  f"code {ck} status={rec.get('analysis_status') if rec else None} emb={emb}")
            # ---- T12i/T12j the frozen candidate v1.0r3 on the census (specification §4b; implemented 2026-10-26) ---- #
            rx.sc.A3_BASELINE_RUN = "a3run"            # seam: the fixture's v1.0 A3 run stands in for the baseline record
            _, fr0, _, cr0 = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(a3p), run_id="a3r3nodec", dev=False, repeats=3, instrument="v1.0r3")
            _, fr1, _, cr1 = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(a3p), run_id="a3r3v10", dev=False, repeats=3, instrument="v1.0", a3_candidate_decision="x")
            _, fr2, _, cr2 = go(tmp, gate_plan() + [resp(WIND)] * 6, manifest=str(sessp), run_id="a3r3dev", dev=True, restrict_split="A3", repeats=3, instrument="v1.0r3", a3_candidate_decision="x")
            rec3, fr3, _, cr3 = go(tmp, gate_plan() + [resp(BAD_CONTRACT), resp(WIND)] + [resp(WIND)] * 5, manifest=str(a3p), run_id="a3r3", dev=False, repeats=3,
                                   instrument="v1.0r3", a3_candidate_decision="NW, 2026-10-26 (fixture): run the frozen candidate on the census")
            c3 = (rec3 or {}).get("execution_contract") or {}
            second = fr3.seen[3 + 1] if len(fr3.seen) > 4 else {}
            fb = (second.get("messages") or [{}])[-1].get("content") or [{}]
            feedback_sent = isinstance(fb, list) and bool(fb) and "toolResult" in fb[0] and fb[0]["toolResult"].get("status") == "error"
            check("T12i the candidate v1.0r3 runs on the census only with the author's recorded decision (refused without it, under v1.0 with it, and in development mode); status fresh-sample evaluation; retry policy v3, the candidate block and the v10r3 digest are in the contract and the record; the retry after an invalid first response carries the call and the validator's messages",
                  cr0 != 0 and not fr0.seen and cr1 != 0 and not fr1.seen and cr2 != 0 and not fr2.seen and cr3 == 0 and rec3["reportable"]
                  and rec3["analysis_status"] == "fresh-sample evaluation" and c3.get("retry_policy_version") == "3" and c3.get("instrument_version") == "1.0r3"
                  and c3.get("instrument_v10r3_sha256") and c3.get("instrument_v11_sha256") and c3.get("a3_candidate") == rec3.get("a3_candidate")
                  and (rec3.get("a3_candidate") or {}).get("configuration") == "v1.0r3" and feedback_sent,
                  f"codes {cr0} {cr1} {cr2} {cr3} fb={feedback_sent}")
            a3r = _ap.Namespace(run_record=str(rx.RUNS / "a3r3.json"), manifest=str(a3p), restrict_split=None,
                                gold_pass1=str(labels / "A3_pass1_v1.0.csv"), gold_pass2=str(labels / "A3_pass2_v1.0.csv"),
                                dev=False, gold_manifest=None, skip_provenance=False, threshold=5, reliability=None)
            ev.GIT_LAST_CHANGE = lambda p_: ("2026-01-01T00:00:00+00:00", te.sha(p_))
            inp3 = ev.load_inputs(a3r)
            st3 = ev.analysis_status_of(inp3["rec"], manifest_kind="A3_sample_manifest")
            tampered = json.loads((rx.RUNS / "a3r3.json").read_text(encoding="utf-8")); tampered["a3_candidate"] = dict(tampered["a3_candidate"], decision="someone else")
            try:
                ev.analysis_status_of(tampered, manifest_kind="A3_sample_manifest"); tamp = False
            except ev.EvalError:
                tamp = True
            stripped = json.loads((rx.RUNS / "a3r3.json").read_text(encoding="utf-8")); stripped.pop("a3_candidate", None)
            try:
                ev.analysis_status_of(stripped, manifest_kind="A3_sample_manifest"); tamp = False
            except ev.EvalError:
                pass
            check("T12j the evaluator scores the v1.0r3 run under the fresh-sample status with the candidate block bound to the contract, and refuses a record whose candidate block was edited",
                  st3 == "fresh-sample evaluation" and inp3["rec"]["instrument"]["name"] == "v1.0r3" and tamp, f"{st3} tamp={tamp}")
            # (4) the evaluator: contract, declared threshold, embargo evidence, status; and its refusals
            a_ = _ap.Namespace(run_record=str(rx.RUNS / "a3run.json"), manifest=str(a3p), restrict_split=None,
                               gold_pass1=str(labels / "A3_pass1_v1.0.csv"), gold_pass2=str(labels / "A3_pass2_v1.0.csv"),
                               dev=False, gold_manifest=None, skip_provenance=False, threshold=5, reliability=None)
            ev.GIT_LAST_CHANGE = lambda p_: ("2026-01-01T00:00:00+00:00", te.sha(p_))
            inp = ev.load_inputs(a_)
            res = ev.evaluate(inp, threshold=5, B=5, seed=1, dev=False)
            res["inputs"] = inp.get("provenance")
            check("T12e the evaluator accepts the A3 run under the declared threshold, binds the embargo evidence and labels the status",
                  res["analysis_status"] == "fresh-sample evaluation" and res["inputs"]["threshold_declaration"]["s"] == 5
                  and res["inputs"]["a3_evidence"]["finalised"] == emb["finalised"] and "clean_pooled" not in res["point"]
                  and res["populations"] == {"pooled": 2, "de": 1, "en": 1}, f"{res.get('analysis_status')} {res['inputs'].get('a3_evidence')} {res['populations']}")
            def refused(**over):
                b = _ap.Namespace(**{**vars(a_), **over})
                try:
                    ev.load_inputs(b); return False
                except ev.EvalError:
                    return True
            r_thr = refused(threshold=4); r_dev = refused(dev=True); r_rel = refused(reliability=["a", "b", "c", "d"])
            ev.GIT_LAST_CHANGE = lambda p_: ("2099-01-01T00:00:00+00:00", te.sha(p_)); r_late = refused()
            ev.GIT_LAST_CHANGE = lambda p_: (None, None); r_nogit = refused()
            ev.GIT_LAST_CHANGE = lambda p_: ("2026-01-01T00:00:00+00:00", "0" * 64); r_dirty = refused()
            ev.GIT_LAST_CHANGE = lambda p_: ("2026-01-01T00:00:00+00:00", te.sha(p_))
            check("T12f the evaluator refuses another threshold, a development evaluation over A3, a reliability table, and reference files committed after the first call, uncommitted or changed since",
                  r_thr and r_dev and r_rel and r_late and r_nogit and r_dirty, f"{r_thr} {r_dev} {r_rel} {r_late} {r_nogit} {r_dirty}")
            # ---- T14g/h the §4f model comparison on the A3 census (2026-10-19) ---- #
            opus_gate = [resp(OFF), resp(OFF, u=usage(write=5000)), resp(OFF, u=usage(read=5000))]
            DEC = "Nils Wagner, 2026-10-19 (fixture): run the model comparison"
            _, fm3, _, cm3 = go(tmp, opus_gate + [resp(WIND)] * 6, manifest=str(a3p), run_id="a3mc3", dev=False, repeats=3,
                                model="opus", model_comparison_decision=DEC)
            recm, fm, _, cm = go(tmp, opus_gate + [resp(WIND)] * 2, manifest=str(a3p), run_id="a3mc", dev=False, repeats=1,
                                 model="opus", model_comparison_decision=DEC)
            check("T14g the A3 census runs under another model only as the §4f comparison (k = 1, the author's decision bound by the contract, the embargo evidence kept); k = 3 is refused before any call",
                  cm3 != 0 and not fm3.seen and cm == 0 and recm["analysis_status"] == "robustness model comparison"
                  and recm["model_comparison"]["decision"] == DEC and recm["execution_contract"]["model_comparison"] == recm["model_comparison"]
                  and recm["model_comparison"]["sample"] == "A3 census" and (recm.get("a3_embargo") or {}).get("finalised"),
                  f"codes {cm3} {cm} status={recm.get('analysis_status') if recm else None}")
            am_ = _ap.Namespace(**{**vars(a_), "run_record": str(rx.RUNS / "a3mc.json")})
            inpm = ev.load_inputs(am_)
            resm = ev.evaluate(inpm, threshold=5, B=5, seed=1, dev=False)
            resm["inputs"] = inpm.get("provenance")
            check("T14h the evaluator scores the A3 comparison run under the declared threshold with the embargo evidence and labels it as the model comparison",
                  resm["analysis_status"] == "robustness model comparison" and resm["model_key"] == "opus"
                  and resm["inputs"]["a3_evidence"]["finalised"] == recm["a3_embargo"]["finalised"], f"{resm.get('analysis_status')} {resm.get('model_key')}")
            # (5) tables and figures carry the provenance; table 7 is the stub; no table 9
            out_dir = rx.RUNS / "tables" / "a3run"; cm = rx.RUNS / "a3run_class_manifest.json"
            ev.write_tables(res, out_dir, cm)
            t1 = (out_dir / "table1_primary.tex").read_text(encoding="utf-8"); t7 = (out_dir / "table7_clean_domain.csv").read_text(encoding="utf-8")
            check("T12g the evaluator's tables carry the fresh-sample stamp and table 7 is the not-applicable stub",
                  "FRESH-SAMPLE EVALUATION" in t1 and "not applicable" in t7 and not (out_dir / "table9_reliability.csv").exists())
            figs = tmp / "a3figs"; fg.results(out_dir / "metrics.json", False, figs, run_record=rx.RUNS / "a3run.json")
            fp = (figs / "fig_primary.tex").read_text(encoding="utf-8")
            tabs = tmp / "a3tabs"; fg.tables(out_dir / "metrics.json", rx.RUNS / "a3run.json", tabs)
            tp = (tabs / "tab_primary.tex").read_text(encoding="utf-8")
            rr = (tabs / "tab_run_record.tex").read_text(encoding="utf-8")
            check("T12h figures and compact tables carry the fresh-sample provenance and relabelled figure and table labels; no reliability, clean-domain or development table; no 'sealed' threshold wording",
                  "Fresh-sample evaluation" in fp and "fig:fresh_primary" in fp and "fresh census; intervals indicative" in tp
                  and "tab:fresh_primary" in tp and "\\label{tab:primary}" not in tp and "FRESH-SAMPLE" in rr and "A3 census run" in rr
                  and "sealed" not in (out_dir / "table2_per_class.tex").read_text(encoding="utf-8") and "declared" in (out_dir / "table5_displacement.tex").read_text(encoding="utf-8")
                  and not (tabs / "tab_reliability.tex").exists() and not (tabs / "tab_clean_domain.tex").exists() and not (tabs / "tab_development.tex").exists())
            try:
                fg.tables(out_dir / "metrics.json", rx.RUNS / "a3run.json", fg.PAPER / "tables"); ov = False
            except SystemExit:
                ov = True
            try:
                fg.results(out_dir / "metrics.json", False, fg.PAPER / "images", run_record=rx.RUNS / "a3run.json"); ov2 = False
            except SystemExit:
                ov2 = True
            edited = tmp / "a3edited"; edited.mkdir(exist_ok=True)
            m_ed = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8")); m_ed["analysis_status"] = "registered primary"
            (edited / "metrics.json").write_text(json.dumps(m_ed, default=str))
            try:
                fg.results(edited / "metrics.json", False, tmp / "a3edfigs", run_record=rx.RUNS / "a3run.json"); ov3 = False
            except SystemExit:
                ov3 = True
            check("T12i fresh-sample tables and figures refuse to overwrite the registered outputs in paper/, and a metrics file whose status was edited is refused against the run record", ov and ov2 and ov3)
            # the headline table (2026-11-05): the main-text version has the micro rows only, dimension-specific score
            # names, one column per population and no run identifier; the appendix version adds the macro rows and the
            # exact match; the fresh-sample census keeps its complete table
            m_reg = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8")); m_reg["analysis_status"] = None
            tp_main, tp_full = fg.tab_primary(m_reg), fg.tab_primary(m_reg, full=True)
            names = ("matched-frame accuracy", "matched-row accuracy", "end-to-end F1", "Technology detection", "Frame elements")
            check("T12m the main-text headline table keeps the micro rows with dimension-specific names and no run identifier; the appendix table and the fresh-sample table carry the macro rows and the exact match",
                  all(n in tp_main for n in names) and "macro-F1" not in tp_main.split("\\midrule")[1].split("\\bottomrule")[0]
                  and "exact match &" not in tp_main and "\\texttt" not in tp_main and "\\label{tab:primary}" in tp_main
                  and "Pooled" in tp_main and "German" in tp_main and "English" in tp_main
                  and "\\label{tab:primary_full}" in tp_full and "end-to-end macro-F1" in tp_full and "exact match &" in tp_full
                  and "end-to-end macro-F1" in tp and "exact match &" in tp and "\\texttt{a3run" not in tp)   # 2026-11-11: the complete table's note names the evaluator's output file in typewriter; the check is that no run identifier is printed
            # ---- T13 frame completion for the DSL estimate (specification §4d, 2026-10-19) ---------------- #
            comp = {"kind": "frame_completion_manifest", "created": "2026-10-19", "frame_manifest_digest": frame["digest"], "freeze_event_hash": "frz",
                    "excludes": {"A2": {"kind": "A2_sample_manifest", "digest": a2["digest"]}, "A3": {"kind": "A3_sample_manifest", "digest": listing()["digest"]}},
                    "n": 1, "by_language": {"en": 1}, "paragraphs": [fpara("T004_p01", "en", "sibling_calibration_article")]}
            comp["digest"] = rx.manifest_digest(comp)
            cp = d3 / "A2_frame_completion_manifest.json"; cp.write_text(json.dumps(comp))
            old_fc = (rx.FRAME_COMPLETION_MANIFEST, rx.sc.FRAME_COMPLETION_MANIFEST)
            rx.FRAME_COMPLETION_MANIFEST = rx.sc.FRAME_COMPLETION_MANIFEST = cp
            try:
                try:
                    rx.sc.verify_frame_completion(comp, listing_path=cp, a3_path=a3p); fc_ok = True; fc_err = ""
                except rx.sc.ContractError as e_:
                    fc_ok = False; fc_err = str(e_)
                fc_bad = []
                for name, over in (("A3 paragraph included", {"paragraphs": [fpara("T004_p01", "en", "sibling_calibration_article"), dict(A3P[0])], "n": 2, "by_language": {"en": 2}}),
                                   ("paragraph missing", {"paragraphs": [], "n": 0, "by_language": {}}),
                                   ("A2 paragraph included", {"paragraphs": [fpara("T004_p01", "en", "sibling_calibration_article"), fpara("T001_p02", "en")], "n": 2, "by_language": {"en": 2}})):
                    v = {k_: v_ for k_, v_ in comp.items() if k_ != "digest"}; v.update(over); v["digest"] = rx.manifest_digest(v)
                    cp.write_text(json.dumps(v))
                    try:
                        rx.sc.verify_frame_completion(v, listing_path=cp, a3_path=a3p); fc_bad.append(name)
                    except rx.sc.ContractError:
                        pass
                cp.write_text(json.dumps(comp))
                check("T13a the frame-completion contract accepts exactly the frame minus A2 and A3 and refuses an A3 paragraph, an A2 paragraph and a missing paragraph",
                      fc_ok and not fc_bad, f"ok={fc_ok} {fc_err} passed_bad={fc_bad}")
                _, g1, _, k1 = go(tmp, gate_plan() + [resp(WIND)] * 3, manifest=str(cp), run_id="fc3", dev=False, repeats=3)
                _, g2, _, k2 = go(tmp, gate_plan() + [resp(WIND)] * 3, manifest=str(cp), run_id="fcv", dev=False, repeats=1, instrument="v1.1S")
                recf, g3, _, k3 = go(tmp, gate_plan() + [resp(WIND)] * 3, manifest=str(cp), run_id="fc1", dev=False, repeats=1)
                check("T13b a frame-completion run needs k = 1 and v1.0; it then runs with the status 'exploratory frame completion' and touches no A3 paragraph",
                      k1 != 0 and not g1.seen and k2 != 0 and not g2.seen and k3 == 0 and recf["analysis_status"] == "exploratory frame completion"
                      and recf["a3_embargo"]["touches_a3"] is False, f"codes {k1} {k2} {k3} status={recf.get('analysis_status') if recf else None}")
                b_ = _ap.Namespace(run_record=str(rx.RUNS / "fc1.json"), manifest=str(cp), restrict_split=None, gold_pass1=str(labels / "A3_pass1_v1.0.csv"),
                                   gold_pass2=str(labels / "A3_pass2_v1.0.csv"), dev=False, gold_manifest=None, skip_provenance=False, threshold=5, reliability=None)
                try:
                    ev.load_inputs(b_); sc_ref = False
                except ev.EvalError:
                    sc_ref = True
                check("T13c the evaluator refuses to score a frame-completion run (no reference exists for it)", sc_ref)
            finally:
                rx.FRAME_COMPLETION_MANIFEST, rx.sc.FRAME_COMPLETION_MANIFEST = old_fc
        finally:
            (rx.sc.FRAME_MANIFEST, rx.sc.REGISTRY_FILE, rx.sc.SAMPLE_MANIFEST, rx.sc.A3_SAMPLE_MANIFEST, rx.A3_SAMPLE_MANIFEST, rx.LABELS,
             rx.SESSIONS_CSV, rx.a3_committed, ev.FRAME_MANIFEST, ev.REGISTRY_FILE, ev.RUNS, ev.GIT_LAST_CHANGE) = old_a3
            rx.sc.REQUIRE_SEAL_COMMIT = True                   # test_evaluate's import relaxed it; restore the production default
            TEXTS.pop("T003_p01", None); TEXTS.pop("T004_p01", None)
        # ---- T14 the §4f robustness model comparison on the registered A2 sample (2026-10-19) ---- #
        rx.bind_instrument("v1.0")
        mp14 = setup(tmp, ["T001_p01", "T002_p01"])
        opus_gate = [resp(OFF), resp(OFF, u=usage(write=5000)), resp(OFF, u=usage(read=5000))]
        DEC = "Nils Wagner, 2026-10-19 (fixture): run the model comparison"
        _, f0, _, c0 = go(tmp, opus_gate + [resp(WIND), resp(WIND)], manifest=str(mp14), run_id="mc_nodec", model="opus", repeats=1)
        _, f3, _, c3 = go(tmp, opus_gate + [resp(WIND)] * 6, manifest=str(mp14), run_id="mc_k3", model="opus", repeats=3, model_comparison_decision=DEC)
        _, fs, _, cs = go(tmp, gate_plan() + [resp(WIND)] * 2, manifest=str(mp14), run_id="mc_sonnet", model="sonnet", repeats=1, model_comparison_decision=DEC)
        _, fv, _, cv = go(tmp, opus_gate + [resp(WIND)] * 2, manifest=str(mp14), run_id="mc_v11", model="opus", repeats=1, model_comparison_decision=DEC, instrument="v1.1S")
        rx.bind_instrument("v1.0")
        # §4k (2026-11-01) put opus5 on a roster; an invented key stands in for a callable model outside every roster
        ins.MODELS["offroster_test"] = "eu.anthropic.claude-opus-4-8"
        try:
            _, fr5, _, cr5 = go(tmp, opus_gate + [resp(WIND)] * 2, manifest=str(mp14), run_id="mc_off_roster", model="offroster_test", repeats=1, model_comparison_decision=DEC)
        finally:
            ins.MODELS.pop("offroster_test", None)
        check("T14f a model outside the declared §4f roster is refused before any call even with the decision", cr5 != 0 and not fr5.seen, f"code {cr5}")
        check("T14a a reportable run under a non-registered model is refused before any call without the author's decision, with k = 3 or a revised instrument; the decision flag is refused on a registered-model run",
              c0 != 0 and not f0.seen and c3 != 0 and not f3.seen and cs != 0 and not fs.seen and cv != 0 and not fv.seen,
              f"codes {c0} {c3} {cs} {cv}")
        recc, fc, _, cc = go(tmp, opus_gate + [resp(WIND), resp(WIND)], manifest=str(mp14), run_id="mc_ok", model="opus", repeats=1, model_comparison_decision=DEC)
        started = [r for r in recs(tmp, "mc_ok") if r.get("type") == "run_started"]
        sent_ok = all(kw.get("modelId") == ins.MODELS["opus"] for kw in fc.seen[3:])
        same_body = all({k: v for k, v in kw.items() if k != "modelId"} == ins.build_request(art, TEXTS[pid])
                        for kw, pid in zip(fc.seen[3:], ["T001_p01", "T002_p01"]))
        check("T14b with the decision, v1.0 and k = 1 it runs as 'robustness model comparison'; the decision is bound by the contract and the start event, and every request is the pinned body under the other model id",
              cc == 0 and recc["reportable"] and recc["analysis_status"] == "robustness model comparison"
              and recc["model_comparison"]["decision"] == DEC and recc["execution_contract"]["model_comparison"] == recc["model_comparison"]
              and started and started[0].get("model_comparison") == recc["model_comparison"] and sent_ok and same_body,
              f"code {cc} status={recc.get('analysis_status') if recc else None} sent_ok={sent_ok} same_body={same_body}")
        recs_ = json.loads((rx.RUNS / "mc_ok.json").read_text(encoding="utf-8"))
        ok_status = ev.analysis_status_of(recs_, manifest_kind="A2_sample_manifest") == "robustness model comparison"
        def st_refused(mut):
            r_ = json.loads(json.dumps(recs_)); mut(r_)
            try:
                ev.analysis_status_of(r_, manifest_kind="A2_sample_manifest"); return False
            except ev.EvalError:
                return True
        f_reg = st_refused(lambda r_: r_.update(analysis_status="registered primary"))
        f_dec = st_refused(lambda r_: r_["model_comparison"].update(decision="edited afterwards"))
        f_k = st_refused(lambda r_: r_.update(repeats=3))
        f_son = st_refused(lambda r_: r_.update(model_key="sonnet"))
        f_hide = st_refused(lambda r_: (r_.pop("model_comparison"), r_.update(analysis_status="registered primary")))
        f_disg = st_refused(lambda r_: (r_.pop("model_comparison"), r_.update(analysis_status="registered primary", model_key="sonnet")))
        f_hide = f_hide and f_disg
        check("T14c the evaluator labels the run as the model comparison and refuses it relabelled as registered primary, with an edited decision, k = 3, the registered model, or the comparison block removed",
              ok_status and f_reg and f_dec and f_k and f_son and f_hide, f"{ok_status} {f_reg} {f_dec} {f_k} {f_son} {f_hide}")
        # ---- T14i a resume of a completed run changes nothing (Codex §4f round 2, #3) ---- #
        jp_ok = rx.RUNS / "mc_ok_calls.jsonl"; before = jp_ok.read_bytes(); rec_before = (rx.RUNS / "mc_ok.json").read_bytes()
        _, fres, _, cres = go(tmp, opus_gate + [resp(WIND), resp(WIND)], manifest=str(mp14), run_id="mc_ok", model="opus", repeats=1,
                              model_comparison_decision=DEC, resume=True)
        check("T14i a resume of a completed run returns its record unchanged before the gate and any call; journal and record are byte-identical afterwards",
              cres == 0 and not fres.seen and jp_ok.read_bytes() == before and (rx.RUNS / "mc_ok.json").read_bytes() == rec_before,
              f"code {cres} calls={len(fres.seen)}")
        # ---- T14d/e §4f no-cache-point models: the request without the cache point, certified by the adapted gate ---- #
        nc_gate = [resp(OFF), resp(OFF, u=usage()), resp(OFF, u=usage())]          # no cache counters at all
        recn, fn, _, cn = go(tmp, nc_gate + [resp(WIND), resp(WIND)], manifest=str(mp14), run_id="mc_nova", model="novapro", repeats=1,
                             model_comparison_decision=DEC)
        prod = fn.seen[1:3] + fn.seen[3:]
        no_cp = all(all("cachePoint" not in b for b in kw.get("system", [])) and kw.get("system") == [{"text": art.prompt}] for kw in prod)
        same_rest = all({k: v for k, v in kw.items() if k not in ("modelId", "system")} ==
                        {k: v for k, v in ins.build_request(art, TEXTS[pid]).items() if k != "system"}
                        for kw, pid in zip(fn.seen[3:], ["T001_p01", "T002_p01"]))
        gate_doc = json.loads(Path(recn["preflight"]["gates"][0]["artefact"]).read_text(encoding="utf-8")) if cn == 0 else {}
        ids = {c_.get("id") for c_ in gate_doc.get("checks", [])} if gate_doc else set()
        check("T14d a §4f no-cache model runs on the pinned request without the cache point (everything else identical), the contract records it, and the gate certifies it with check 5n instead of 5/6",
              cn == 0 and recn["reportable"] and no_cp and same_rest and recn["execution_contract"].get("cache_point") is False
              and gate_doc.get("certified") and "5n" in ids and "5" not in ids and "6" not in ids,
              f"code {cn} no_cp={no_cp} same_rest={same_rest} ids={sorted(i for i in ids if i)[:20]}")
        bad_gate = [resp(OFF), resp(OFF, u=usage(write=2000)), resp(OFF, u=usage(read=2000))]   # cache traffic without a cache point
        _, fb, _, cb = go(tmp, bad_gate + [resp(WIND), resp(WIND)], manifest=str(mp14), run_id="mc_nova_bad", model="novapro", repeats=1,
                          model_comparison_decision=DEC)
        recs_reg = json.loads((rx.RUNS / "mc_ok.json").read_text(encoding="utf-8"))
        check("T14e cache traffic reported without a cache point fails the gate before any extraction call; a cache-point model's contract carries no cache_point key (earlier fingerprints unchanged)",
              cb != 0 and len(fb.seen) == 3 and "cache_point" not in recs_reg["execution_contract"], f"code {cb} calls={len(fb.seen)}")
        # ---- T15 §4g the diffusion model Mercury 3 through the Converse adapter (2026-10-30) ---- #
        import inception_client as icx
        rx.bind_instrument("v1.0")
        req_i = ins.request_for("mercury3_instant", art, "P"); req_l = ins.request_for("mercury3_low", art, "P")
        body_i = icx.to_chat_body({"modelId": ins.MODELS["mercury3_instant"], **req_i})
        spec = art.tool_config["tools"][0]["toolSpec"]
        check("T15a the adapter sends the pinned prompt, schema and user turn, the named tool choice, and exactly the declared decoding (temperature 0.5, 1,024 / 8,192 tokens, reasoning_effort instant / low), no cache point",
              body_i["model"] == "mercury-3" and body_i["messages"] == [{"role": "system", "content": art.prompt}, {"role": "user", "content": "Paragraph:\nP"}]
              and body_i["tools"] == [{"type": "function", "function": {"name": spec["name"], "description": spec.get("description", ""), "parameters": spec["inputSchema"]["json"]}}]
              and body_i["tool_choice"] == {"type": "function", "function": {"name": spec["name"]}}
              and body_i["temperature"] == 0.5 and body_i["max_completion_tokens"] == 1024 and body_i["reasoning_effort"] == "instant"
              and icx.to_chat_body({"modelId": "mercury-3", **req_l})["max_completion_tokens"] == 8192
              and icx.to_chat_body({"modelId": "mercury-3", **req_l})["reasoning_effort"] == "low"
              and req_i["toolConfig"] is art.tool_config and all("cachePoint" not in b for b in req_i["system"]), str(body_i)[:200])
        def chat(payload=None, finish="stop", args=None, u=None):
            tc = [] if payload is None and args is None else [{"id": "call_1", "type": "function", "index": 0,
                  "function": {"name": ins.TOOL_NAME, "arguments": args if args is not None else json.dumps(payload)}}]
            return {"id": "x", "object": "chat.completion", "model": "mercury-3", "warning": None,
                    "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": "" if tc else "OK", "tool_calls": tc or None}}],
                    "usage": u or {"prompt_tokens": 4100, "completion_tokens": 120, "total_tokens": 4220,
                                   "prompt_tokens_details": {"cached_tokens": 3700}, "completion_tokens_details": {"reasoning_tokens": 0}}}
        r1 = icx.to_converse_response(chat(WIND)); r2 = icx.to_converse_response(chat(args="{not json")); r3 = icx.to_converse_response(chat(WIND, finish="length"))
        use, why = ins.select_tool_use(r1["output"]["message"]["content"])
        check("T15b responses: a forced tool call with finish 'stop' is tool_use with the parsed input; unparseable arguments pass through as a string (the validator rejects them); 'length' is max_tokens; cached and reasoning tokens stay outside the Converse cache counters",
              r1["stopReason"] == "tool_use" and use == {"toolUseId": "call_1", "name": ins.TOOL_NAME, "input": WIND}
              and r1["usage"] == {"inputTokens": 4100, "outputTokens": 120, "totalTokens": 4220}
              and r1["additionalModelResponseFields"]["cached_tokens"] == 3700 and r1["additionalModelResponseFields"]["reasoning_tokens"] == 0
              and r2["output"]["message"]["content"][0]["toolUse"]["input"] == "{not json" and r3["stopReason"] == "max_tokens", str(r1)[:200])
        e429, e503 = icx.to_client_error(429, {"error": {"type": "rate_limit_error"}}), icx.to_client_error(503, {})
        check("T15c HTTP 429 is a ThrottlingException and 503 a ServiceUnavailableException, the codes the runner classifies",
              e429.response["Error"]["Code"] == "ThrottlingException" and e503.response["Error"]["Code"] == "ServiceUnavailableException")
        # end to end: gate and run through the adapter with a scripted transport
        plan = [chat(), chat(OFF), chat(OFF), chat(WIND), chat(WIND)]
        sent = []
        def transport(url, body, headers, timeout):
            sent.append((url, body))
            return 200, plan.pop(0)
        mp15 = setup(tmp, ["T001_p01", "T002_p01"])
        DEC15 = "Nils Wagner, 2026-10-30 (fixture): benchmark Mercury 3"
        recm3 = None
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                recm3 = rx.run(ns(manifest=str(mp15), run_id="mc_mercury", model="mercury3_instant", repeats=1, model_comparison_decision=DEC15),
                               client_factory=lambda: icx.InceptionConverseClient(api_key="test-key", transport=transport), sleep=lambda s_: None)
                c15 = 0
            except SystemExit as e_:
                c15 = e_.code
        con = (recm3 or {}).get("execution_contract") or {}
        gdoc15 = json.loads(Path(recm3["preflight"]["gates"][0]["artefact"]).read_text(encoding="utf-8")) if recm3 else {}
        ids15 = {c_.get("id") for c_ in gdoc15.get("checks", [])}
        check("T15d a Mercury run goes through the certified gate (check 5n), the journal and the validator; its contract binds the endpoint, temperature 0.5, reasoning_effort and the §4g authority, and the evaluator labels it a model comparison",
              c15 == 0 and recm3["reportable"] and recm3["analysis_status"] == "robustness model comparison"
              and recm3["model_comparison"]["authority"] == rx.sc.DIFFUSION_AUTHORITY and con.get("region") == ins.EXTERNAL_API["mercury3_instant"]
              and con.get("inference_config") == {"maxTokens": 1024, "temperature": 0.5} and con.get("additional_model_request_fields") == {"reasoning_effort": "instant"}
              and con.get("cache_point") is False and recm3["provider"].startswith("Inception") and gdoc15.get("certified") and "5n" in ids15
              and all(u_ == ins.EXTERNAL_API["mercury3_instant"] for u_, _ in sent)
              and ev.analysis_status_of(recm3, manifest_kind="A2_sample_manifest") == "robustness model comparison",
              f"code {c15} ids={sorted(i for i in ids15 if i)[:12]} con={ {k: con.get(k) for k in ('region', 'inference_config', 'cache_point')} }")
        # a builder that deviates from the declared adaptation fails the gate's check 13 before any extraction call
        real_rf = ins.request_for
        def drifting(model_key, art_, paragraph, *, max_tokens=1024):
            r_ = real_rf(model_key, art_, paragraph, max_tokens=max_tokens)
            if model_key == "mercury3_instant":
                r_["inferenceConfig"] = {"maxTokens": 1024, "temperature": 0.7}
            return r_
        ins.request_for = drifting
        plan[:] = [chat(), chat(OFF), chat(OFF), chat(WIND), chat(WIND)]; sent.clear()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                rx.run(ns(manifest=str(mp15), run_id="mc_mercury_drift", model="mercury3_instant", repeats=1, model_comparison_decision=DEC15),
                       client_factory=lambda: icx.InceptionConverseClient(api_key="test-key", transport=transport), sleep=lambda s_: None)
                cdrift = 0
            except SystemExit as e_:
                cdrift = e_.code
            finally:
                ins.request_for = real_rf
        check("T15e a request that deviates from the declared §4g adaptation (temperature 0.7) fails the gate before any extraction call",
              cdrift not in (0, None) and len(sent) <= 3, f"code={str(cdrift)[:100]} calls={len(sent)}")
        # ---- T15f-i Codex §4g round 1: malformed envelopes, unknown statuses, the adapter digest, the verdict rule ---- #
        u0 = {"prompt_tokens": 4100, "completion_tokens": 50, "total_tokens": 4150}
        bad = [{"choices": [], "usage": u0},
               {"choices": [chat(WIND)["choices"][0], chat(OFF)["choices"][0]], "usage": u0},
               {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": ["x"], "tool_calls": None}}], "usage": u0},
               {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "", "tool_calls": [None]}}], "usage": u0}]
        codes_bad = [rx.classify(icx.to_converse_response(b_), art, *ins.load_codebook()[:2])[0] for b_ in bad]
        kept = all(icx.to_converse_response(b_)["usage"] == {"inputTokens": 4100, "outputTokens": 50, "totalTokens": 4150} for b_ in bad)
        nofin = chat(WIND); nofin["choices"][0]["finish_reason"] = None
        code_nofin = rx.classify(icx.to_converse_response(nofin), art, *ins.load_codebook()[:2])[0]
        check("T15f malformed envelopes (no or several choices, non-string content, a null tool call) are response failures with their usage kept, never accepted or infrastructure errors; a missing finish reason is refused as an unexpected stop",
              all(c_ in rx.PARSE_FAIL_CODES for c_ in codes_bad) and kept and code_nofin == rx.UNEXPECTED_STOP
              and "providerResponse" in icx.to_converse_response(bad[0]), f"{codes_bad} {code_nofin}")
        # Codex §4g round 2 (#1): a body that is not a JSON object and a finish reason that is not a string are malformed too
        fr_dict = chat(WIND); fr_dict["choices"][0]["finish_reason"] = {}
        fr_list = chat(WIND); fr_list["choices"][0]["finish_reason"] = []
        odd = [None, [], "<html>bad gateway</html>", fr_dict, fr_list]
        try:
            conv_odd = [icx.to_converse_response(b_) for b_ in odd]
            codes_odd = [rx.classify(c_, art, *ins.load_codebook()[:2])[0] for c_ in conv_odd]
            raised_odd = None
        except Exception as e_:
            conv_odd, codes_odd, raised_odd = [], [], repr(e_)
        try:
            err_list = icx.to_client_error(502, []).response["Error"]["Code"]
        except Exception as e_:
            err_list = repr(e_)
        check("T15j a body that is not a JSON object (null, list, text) and a finish reason that is not a string are response failures with the original body and the usage kept, never a Python exception; an error status with a list body still maps to its code",
              raised_odd is None and len(codes_odd) == 5 and all(c_ in rx.PARSE_FAIL_CODES for c_ in codes_odd)
              and [c_["providerResponse"] for c_ in conv_odd[:3]] == odd[:3]
              and conv_odd[3]["usage"] == {"inputTokens": 4100, "outputTokens": 120, "totalTokens": 4220}
              and err_list == "ServiceUnavailableException", f"{codes_odd} {raised_odd} {err_list}")
        # Codex §4g round 3 (#1): the original provider response survives every path, a valid tool call included
        two_calls = chat(WIND); two_calls["choices"][0]["message"]["tool_calls"] = two_calls["choices"][0]["message"]["tool_calls"] * 2
        empty_args = chat(args="{}")
        orig_ok = all(icx.to_converse_response(json.loads(json.dumps(b_)))["providerResponse"] == b_
                      for b_ in (chat(WIND), empty_args, two_calls, chat(args="{not json"), bad[3], chat(WIND, finish="length")))
        check("T15k the provider's original response is kept as providerResponse for valid, empty-argument, multiple, unparseable and malformed tool calls",
              orig_ok, str(icx.to_converse_response(empty_args)["providerResponse"])[:80])
        e408 = icx.to_client_error(408, {}); e499 = icx.to_client_error(499, {})
        unbilled = lambda e_: any(mk in e_.response["Error"]["Code"] for mk in rx.KNOWN_UNBILLED_MARKERS)
        check("T15g an undocumented HTTP status keeps an uncertain billing state; the documented 400 rejection stays unbilled",
              not unbilled(e408) and not unbilled(e499) and unbilled(icx.to_client_error(400, {})), f"{e408.response['Error']['Code']}")
        plan[:] = [chat(), chat(OFF), chat(OFF), chat(WIND), chat(WIND)]; sent.clear()
        recad = None
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                recad = rx.run(ns(manifest=str(mp15), run_id="mc_mercury_ad", model="mercury3_low", repeats=1, model_comparison_decision=DEC15),
                               client_factory=lambda: icx.InceptionConverseClient(api_key="test-key", transport=transport), sleep=lambda s_: None)
            except SystemExit:
                recad = None
        gad = json.loads(Path(recad["preflight"]["gates"][0]["artefact"]).read_text(encoding="utf-8")) if recad else {}
        bound = bool(recad) and recad["execution_contract"].get("adapter_sha256") == (gad.get("provenance") or {}).get("adapter_sha256") == te.sha(Path(rx.__file__).resolve().parent / "inception_client.py")
        r_bad = json.loads(json.dumps(recad)) if recad else {}
        if r_bad:
            r_bad["execution_contract"]["adapter_sha256"] = "0" * 64
        try:
            ev.verify_gate(r_bad); refused_ad = False
        except (ev.EvalError, Exception):
            refused_ad = True
        check("T15h an external model's contract binds the adapter digest, the gate's provenance names the same code, and the evaluator refuses a contract naming other adapter code",
              bound and recad["execution_contract"].get("endpoint") == ins.EXTERNAL_API["mercury3_low"] and refused_ad,
              f"bound={bound} refused={refused_ad}")
        import compare_models as cmp
        pv = lambda lo, hi: {"element": {"lo": lo, "hi": hi}}
        check("T15i the verdict rule is §4g's: better / worse only when the interval excludes zero, otherwise no distinguishable difference",
              cmp.verdict(pv(0.01, 0.2)) == "better" and cmp.verdict(pv(-0.3, -0.01)) == "worse" and cmp.verdict(pv(-0.1, 0.1)) == "no distinguishable difference"
              and cmp.verdict(None) == "undefined")
        # ---- T16 §4j: the mandatory-form variant v1.0F and its comparator v1.0r3 (2026-10-30) ---- #
        import instrument_v10f as i10f
        rx.bind_instrument("v1.0")
        order_ = i10f.element_order()
        def form_of(payload):
            out = json.loads(json.dumps(payload))
            for r_ in out.get("relations") or []:
                els_ = r_.pop("elements")
                r_["element_checks"] = {e: (e in els_) for e in order_}
            return out
        mp16 = setup(tmp, ["T001_p01", "T002_p01"])
        DEC16 = "Nils Wagner, 2026-10-30 (fixture): run the form comparison"
        _, g0, _, c0 = go(tmp, gate_plan() + [resp(form_of(WIND))] * 2, manifest=str(mp16), run_id="f_nodec", repeats=1, instrument="v1.0F")
        rx.bind_instrument("v1.0")
        _, g1, _, c1 = go(tmp, gate_plan() + [resp(form_of(WIND))] * 6, manifest=str(mp16), run_id="f_k3", repeats=3, instrument="v1.0F",
                          form_variant_decision=DEC16)
        rx.bind_instrument("v1.0")
        _, g2, _, c2 = go(tmp, gate_plan() + [resp(WIND)] * 2, manifest=str(mp16), run_id="f_v10", repeats=1, form_variant_decision=DEC16)
        rx.bind_instrument("v1.0")
        check("T16a v1.0F is refused before any call without the author's §4j decision and with k = 3 on A2; the decision flag is refused for v1.0",
              c0 != 0 and not g0.seen and c1 != 0 and not g1.seen and c2 != 0 and not g2.seen, f"codes {c0} {c1} {c2}")
        recf, gf, _, cf = go(tmp, gate_plan() + [resp(form_of(WIND)), resp(form_of(SOLAR_TWO))], manifest=str(mp16), run_id="f_ok",
                             repeats=1, instrument="v1.0F", form_variant_decision=DEC16)
        artf = i10f.load_artifacts()
        sent_f = gf.seen[3:]
        okreq = all(kw["inferenceConfig"] == {"maxTokens": 2048, "temperature": 0} and kw["system"][0]["text"] == artf.prompt
                    and kw["toolConfig"] == artf.tool_config for kw in sent_f)
        pays = [json.loads(l)["payload"] for l in (rx.RUNS / "f_ok_calls.jsonl").read_text(encoding="utf-8").splitlines()
                if json.loads(l).get("type") == "attempt_finished" and json.loads(l).get("payload")]
        same_pub = pays == [rx.public_payload(WIND), rx.public_payload(SOLAR_TWO)]
        check("T16b with the decision, v1.0F on A2 (k = 1) runs as 'post-draft instrument variant': the form prompt and schema with the "
              "declared cap 2,048, the decision and the module digest bound by the contract, and the normalised public payload equals the "
              "registered shape",
              cf == 0 and recf["analysis_status"] == rx.sc.FORM_STATUS and recf["form_variant"]["decision"] == DEC16
              and recf["execution_contract"]["form_variant"] == recf["form_variant"]
              and bool(recf["execution_contract"].get("instrument_v10f_sha256")) and okreq and same_pub,
              f"code {cf} status={recf.get('analysis_status') if recf else None} okreq={okreq} same_pub={same_pub}")
        rf_ = json.loads((rx.RUNS / "f_ok.json").read_text(encoding="utf-8"))
        def st_ref16(mut):
            r_ = json.loads(json.dumps(rf_)); mut(r_)
            try:
                ev.analysis_status_of(r_, manifest_kind="A2_sample_manifest"); return False
            except ev.EvalError:
                return True
        ok16 = ev.analysis_status_of(rf_, manifest_kind="A2_sample_manifest") == rx.sc.FORM_STATUS
        check("T16c the evaluator accepts the §4j record and refuses it with another status, without the decision, with an unbound "
              "block, with k = 3 or without the module digest",
              ok16 and st_ref16(lambda r_: r_.update(analysis_status="registered primary"))
              and st_ref16(lambda r_: r_["form_variant"].update(decision=""))
              and st_ref16(lambda r_: r_["execution_contract"]["form_variant"].update(sample="A3"))
              and st_ref16(lambda r_: r_.update(repeats=3))
              and st_ref16(lambda r_: r_["execution_contract"].update(instrument_v10f_sha256=None)), "")
        _, gd, _, cd = go(tmp, gate_plan() + [resp(form_of(WIND))] * 2, manifest=str(mp16), run_id="f_dev_notA1", repeats=1,
                          instrument="v1.0F", form_variant_decision=DEC16, dev=True, restrict_split="A1")
        rx.bind_instrument("v1.0")
        rd_ = json.loads(json.dumps(rf_)); rd_.update(dev=True, analysis_status="development"); rd_.pop("form_variant")
        rd_["execution_contract"].pop("form_variant")
        try:
            ev.analysis_status_of(rd_); dev_ref = False
        except ev.EvalError:
            dev_ref = True
        check("T16e (Codex §4j round 1) a §4j development run on anything but the A1 split of the A1 session manifest is refused "
              "before any call, and a v1.0F development record without its §4j block is refused by the evaluator",
              cd != 0 and not gd.seen and dev_ref, f"code {cd} dev_ref={dev_ref}")
        ids32 = [f"A1X_p{i:02d}" for i in range(rx.sc.FORM_A1_PARAGRAPHS)]
        rows32 = [{"paragraph_id": i, "split": "A1", "text_sha256": f"h{i}"} for i in ids32]
        auth32 = {i: f"h{i}" for i in ids32}
        md32 = {"session": "A1", "paragraphs": rows32}
        md33 = {"session": "A1", "paragraphs": rows32 + [dict(rows32[0])]}
        a1_exact = rx.form_a1_ok("A1", md32, rows32, auth32, set())
        a1_dup = rx.form_a1_ok("A1", md33, rows32 + [dict(rows32[0])], auth32, set())
        a1_hash = rx.form_a1_ok("A1", md32, rows32[:-1] + [{**rows32[-1], "text_sha256": "other"}], auth32, set())
        a1_excl = rx.form_a1_ok("A1", md32, rows32, auth32, {ids32[5]})
        check("T16f (Codex §4j round 3) the A1 selection check accepts exactly the 32 authoritative paragraphs and refuses a "
              "repeated row, a changed text hash and an A2/A3 paragraph",
              a1_exact and not a1_dup and not a1_hash and not a1_excl, f"{a1_exact} {a1_dup} {a1_hash} {a1_excl}")
        bad_missing = form_of(WIND); bad_missing["relations"][0]["element_checks"].pop("F1a")
        allfalse = form_of(SOLAR_TWO)
        doc16, sub16, _ = ins.load_codebook()
        v_missing = i10f.validate_tool_use({"name": ins.TOOL_NAME, "input": bad_missing}, artf, doc16, sub16)
        v_norm = i10f.normalise(allfalse)["relations"][1]["elements"] == []
        check("T16d a form answer that leaves one element out is schema-invalid, and all checks false normalises to the empty "
              "(no-frame) list", (not v_missing[0]) and v_missing[1].startswith("schema:") and v_norm, str(v_missing)[:120])
        # ---- T17 §4k the Claude 5 benchmark: Sonnet 5, Opus 5 and the 4.6 controls under the 5-series request (2026-11-01) ---- #
        rx.bind_instrument("v1.0")
        mp17 = setup(tmp, ["T001_p01", "T002_p01"])
        DEC17 = "Nils Wagner, 2026-11-01 (fixture): benchmark Sonnet 5 and Opus 5"
        pinned17 = ins.build_request(art, "P")
        want17 = {**{k_: v_ for k_, v_ in pinned17.items() if k_ != "inferenceConfig"}, "inferenceConfig": {"maxTokens": 1024},
                  "additionalModelRequestFields": {"thinking": {"type": "disabled"}}}
        reqs17 = all(ins.request_for(k_, art, "P") == want17 for k_ in rx.sc.GEN5_ROSTER)
        reg17 = ins.request_for("sonnet", art, "P") == pinned17 and ins.request_for("opus", art, "P") == pinned17
        alias17 = ins.request_for("sonnet5", art, "P")["additionalModelRequestFields"] is not ins.GEN5_REQUEST["additional"]
        check("T17a the 5-series request is the pinned request without temperature and with thinking disabled (cache point, prompt, schema, "
              "user turn and 1,024 tokens unchanged) for Sonnet 5, Opus 5 and both controls; the controls name the 4.6 model ids; the "
              "registered model and Opus 4.6 keep the pinned request",
              reqs17 and reg17 and alias17 and ins.MODELS["sonnet_req5"] == ins.MODELS["sonnet"] and ins.MODELS["opus_req5"] == ins.MODELS["opus"],
              f"{reqs17} {reg17} {alias17}")
        rec17, f17, _, c17 = go(tmp, gate_plan() + [resp(WIND), resp(WIND)], manifest=str(mp17), run_id="mc_sonnet5", model="sonnet5",
                                repeats=1, model_comparison_decision=DEC17)
        con17 = (rec17 or {}).get("execution_contract") or {}
        g17 = json.loads(Path(rec17["preflight"]["gates"][0]["artefact"]).read_text(encoding="utf-8")) if rec17 else {}
        sent17 = all({k_: v_ for k_, v_ in kw.items() if k_ != "modelId"} == ins.request_for("sonnet5", art, TEXTS[pid])
                     for kw, pid in zip(f17.seen[3:], ["T001_p01", "T002_p01"])) and len(f17.seen) == 5
        gate17 = all("temperature" not in kw["inferenceConfig"]
                     and kw.get("additionalModelRequestFields") == {"thinking": {"type": "disabled"}} for kw in f17.seen[1:3])
        check("T17b a Sonnet 5 run goes through the certified gate and the journal on the 5-series request; its contract binds no "
              "temperature, thinking disabled and the §4k authority; the record states the disabled thinking field; the evaluator "
              "labels it a model comparison",
              c17 == 0 and rec17["reportable"] and rec17["analysis_status"] == "robustness model comparison"
              and rec17["model_comparison"]["authority"] == rx.sc.GEN5_AUTHORITY
              and con17.get("inference_config") == {"maxTokens": 1024} and rec17["inference_config"] == {"maxTokens": 1024}
              and con17.get("additional_model_request_fields") == {"thinking": {"type": "disabled"}}
              and rec17["thinking"] == ins.THINKING_DISABLED_CONTRACT and g17.get("certified") and sent17 and gate17
              and all(kw.get("modelId") == ins.MODELS["sonnet5"] for kw in f17.seen)
              and ev.analysis_status_of(rec17, manifest_kind="A2_sample_manifest") == "robustness model comparison",
              f"code {c17} con={ {k_: con17.get(k_) for k_ in ('inference_config', 'additional_model_request_fields')} } sent={sent17} gate={gate17}")
        real_rf17 = ins.request_for
        def drift17(model_key, art_, paragraph, *, max_tokens=1024):
            r_ = real_rf17(model_key, art_, paragraph, max_tokens=max_tokens)
            if model_key == "opus5":
                r_["inferenceConfig"] = {"maxTokens": 1024, "temperature": 0}
            return r_
        def nothink17(model_key, art_, paragraph, *, max_tokens=1024):
            r_ = real_rf17(model_key, art_, paragraph, max_tokens=max_tokens)
            if model_key == "opus5":
                r_.pop("additionalModelRequestFields", None)
            return r_
        codes17 = []
        for fn_, rid in ((drift17, "mc_opus5_drift"), (nothink17, "mc_opus5_nothink")):
            ins.request_for = fn_
            try:
                _, fd17, _, cd17 = go(tmp, gate_plan() + [resp(WIND)] * 2, manifest=str(mp17), run_id=rid, model="opus5", repeats=1,
                                      model_comparison_decision=DEC17)
            finally:
                ins.request_for = real_rf17
            codes17.append((cd17, len(fd17.seen)))
        check("T17c a 5-series request that deviates from the declared adaptation (temperature 0 sent back, or the thinking field "
              "dropped) fails the gate before any extraction call",
              all(c_ not in (0, None) and n_ <= 3 for c_, n_ in codes17), str(codes17)[:200])
        rec17c, f17c, _, c17c = go(tmp, opus_gate + [resp(WIND), resp(WIND)], manifest=str(mp17), run_id="mc_opus_req5", model="opus_req5",
                                   repeats=1, model_comparison_decision=DEC17)
        def refused17(mut):
            r_ = json.loads(json.dumps(rec17c)); mut(r_)
            try:
                ev.analysis_status_of(r_, manifest_kind="A2_sample_manifest"); return False
            except ev.EvalError:
                return True
        check("T17d a configuration control runs the Opus 4.6 model id under the 5-series request as a §4k model comparison; the "
              "evaluator refuses it relabelled as the registered primary, under the §4f authority, or keyed as Opus 4.6",
              c17c == 0 and rec17c["model_id"] == ins.MODELS["opus"] and rec17c["model_comparison"]["authority"] == rx.sc.GEN5_AUTHORITY
              and all("temperature" not in kw["inferenceConfig"] for kw in f17c.seen[1:])
              and ev.analysis_status_of(rec17c, manifest_kind="A2_sample_manifest") == "robustness model comparison"
              and refused17(lambda r_: r_.update(analysis_status="registered primary"))
              and refused17(lambda r_: r_["model_comparison"].update(authority=rx.sc.MODEL_COMPARISON_AUTHORITY))
              and refused17(lambda r_: r_.update(model_key="opus")),
              f"code {c17c}")
        import compare_models as cmp17
        check("T17e the thesis comparison's roster excludes §4k (written by --gen5 only), §4k is on the runner's and evaluator's roster, "
              "and the declared comparison families are exactly §4k's",
              not set(cmp17.ROSTER) & set(rx.sc.GEN5_ROSTER) and set(rx.sc.GEN5_ROSTER) <= set(rx.sc.MODEL_COMPARISON_ROSTER)
              and cmp17.GEN5_COMPARISONS == {"same_request": (("sonnet5", "sonnet_req5"), ("opus5", "opus_req5")),
                                             "as_run": (("sonnet5", "sonnet"), ("sonnet5", "opus"), ("opus5", "sonnet"), ("opus5", "opus")),
                                             "decoding": (("sonnet_req5", "sonnet"), ("opus_req5", "opus"))},
              str(cmp17.ROSTER))
        # ---- T17f/g agent review of §4k: the gate computes the declared decoding independently of the builder's helper;
        # the evaluator's stamp names the declaring section and the request deviation ---- #
        real_aic = ins.adapted_inference_config
        ins.adapted_inference_config = lambda adapt: {"maxTokens": adapt["max_tokens"], "temperature": 0}
        try:
            _, fh17, _, ch17 = go(tmp, gate_plan() + [resp(WIND)] * 2, manifest=str(mp17), run_id="mc_sonnet5_helper", model="sonnet5",
                                  repeats=1, model_comparison_decision=DEC17)
        finally:
            ins.adapted_inference_config = real_aic
        g13 = next((c_ for c_ in json.loads(Path(rec17["preflight"]["gates"][0]["artefact"]).read_text(encoding="utf-8"))["checks"]
                    if c_.get("id") == "13"), {})
        check("T17f a fault in the instrument's decoding helper (temperature 0 sent back) fails the gate's check 13 before any "
              "extraction call; a certified 5-series gate names the disabled thinking field instead of 'no thinking block'",
              ch17 not in (0, None) and len(fh17.seen) <= 3 and "thinking {type: disabled}" in (g13.get("detail") or "")
              and "no thinking block" not in (g13.get("detail") or ""), f"code={str(ch17)[:80]} calls={len(fh17.seen)}")
        st_k = ev.comparison_stamp({"model_key": "sonnet5", "model_comparison_authority": rx.sc.GEN5_AUTHORITY,
                                    "request_deviation": ev.request_deviation(rec17)})
        st_g = ev.comparison_stamp({"model_key": "mercury3_low", "model_comparison_authority": rx.sc.DIFFUSION_AUTHORITY,
                                    "request_deviation": {"temperature": 0.5, "additional_model_request_fields": {"reasoning_effort": "low"},
                                                          "cache_point": False, "max_tokens": 8192}})
        st_f = ev.comparison_stamp({"model_key": "opus", "model_comparison_authority": rx.sc.MODEL_COMPARISON_AUTHORITY,
                                    "request_deviation": ev.request_deviation(recc)})
        check("T17g the evaluator's stamp names the declaring section and the request deviation (§4k: no temperature, thinking "
              "disabled; §4g: temperature, reasoning, cache point, tokens; §4f Opus 4.6: none)",
              st_k.endswith("(specification §4k)") and "(no temperature, thinking disabled)" in st_k
              and st_g.endswith("(specification §4g)") and "temperature 0.5, reasoning_effort low, no cache point, 8192 tokens" in st_g
              and st_f.endswith("(specification §4f)") and "v1.0 under opus, one execution" in st_f, f"{st_k} | {st_g} | {st_f}")
        # ---- T17m §4m (2026-11-08): Opus 5.5 under the 5.5 request, end to end: free tool choice, adaptive thinking, a
        # reasoningContent block beside the call accepted only there, k = 3, a run record that states the request ---- #
        think = [{"reasoningContent": {"reasoningText": {"text": "", "signature": "sig"}}},
                 {"toolUse": {"toolUseId": "tu_m", "name": ins.TOOL_NAME, "input": WIND}}]
        g55 = [resp(OFF), resp(OFF, u=usage(write=6000), content=[think[0], {"toolUse": {"toolUseId": "g1", "name": ins.TOOL_NAME,
               "input": OFF}}]), resp(OFF, u=usage(read=6000))]
        rec17m, f17m, _, c17m = go(tmp, g55 + [resp(content=think)] * 6, manifest=str(mp17), run_id="mc_opus55_t",
                                   model="opus55", repeats=3, model_comparison_decision=DEC17)
        _, _, _, c17m1 = go(tmp, g55 + [resp(content=think)] * 2, manifest=str(mp17), run_id="mc_opus55_k1", model="opus55",
                            repeats=1, model_comparison_decision=DEC17)
        rec17n, _, _, c17n = go(tmp, gate_plan() + [resp(content=think)] * 4, manifest=str(mp17), run_id="mc_opus5_rep_t",
                                model="opus5_rep", repeats=3, model_comparison_decision=DEC17)
        codes17n = __import__("collections").Counter(r_["code"] for r_ in recs(tmp, "mc_opus5_rep_t") if r_.get("type") == "attempt_finished")
        check("T17m a §4m Opus 5.5 run completes with k = 3, every call sending toolChoice auto, adaptive thinking at effort low and "
              "8,192 tokens, the reasoning block accepted, the record stating the adaptive request and the tool choice; k = 1 refused; "
              "the same reasoning block under the §4k request of the Opus 5 repeat is unexpected content",
              c17m == 0 and rec17m["model_comparison"]["authority"] == rx.sc.GEN55_AUTHORITY and rec17m["repeats"] == 3
              and all(kw["toolConfig"]["toolChoice"] == {"auto": {}} and kw["inferenceConfig"] == {"maxTokens": 8192}
                      and kw["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
                      for kw in f17m.seen[1:])
              and rec17m["thinking"].startswith("adaptive: the request carries thinking {type: adaptive} and output_config.effort=low")
              and rec17m["execution_contract"]["tool_choice"] == {"auto": {}}
              and (rec17m["totals"]["verdict_codes"] or {}).get("ok_technologies_present") == 6
              and ev.analysis_status_of(rec17m, manifest_kind="A2_sample_manifest") == "robustness model comparison"
              and "adaptive thinking, effort low" in ev.comparison_stamp({"model_key": "opus55", "model_comparison_authority":
                  rx.sc.GEN55_AUTHORITY, "request_deviation": ev.request_deviation(rec17m)})
              and c17m1 not in (0, None) and c17n == 0 and codes17n.get("unexpected_content", 0) >= 3
              and "tool_choice" not in rec17n["execution_contract"],
              f"codes {c17m} {c17m1} {c17n} {dict(codes17n)}")
        rx.bind_instrument("v1.0")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for n, d in FAILED:
            print(f"  FAILED: {n}  {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
