#!/usr/bin/env python3
"""Contract tests for the §4e(a) language arm (pre-analysis specification §4e(a), amendment of 2026-11-05): the
translation step, the author's check sheet and its seal, the runner's translated-input override, the evaluator's
translated status, and the comparison -- on a synthetic fixture world with a fake translator and a fake Bedrock client.
No call leaves the process.

    .venv/bin/python3 tools/pipeline/test_language_arm.py

The last check runs `language_arm.py baseline` in a subprocess on the real repository: the comparison's scoring path
must reproduce the registered German element micro-F1 (0.621) from the registered predictions.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import instrument as ins                                        # noqa: E402
import run_extract as rx                                        # noqa: E402
import sample_contract as sc                                    # noqa: E402
import evaluate as ev                                           # noqa: E402
import language_arm as la                                       # noqa: E402
import test_run_extract as tr                                   # noqa: E402  (fake Bedrock client, gate plan, payloads)
import test_evaluate as te                                      # noqa: E402  (reference row builders; switches the seal commit off)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"\n          {detail}" if not cond and detail else ""))


def h(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


SOURCE = {
    "D1_p01": "Der Ausbau der Windkraft an Land stockt, die Genehmigungen dauern fast zwei Jahre, klagen die Anwohner.",
    "D1_p02": "Die Solarbranche hat im vergangenen Jahr rund 20.000 neue Arbeitsplaetze geschaffen, teilte der Verband mit.",
    "D2_p01": "Der Stadtrat vertagte am Dienstagabend die Abstimmung ueber den Haushalt des Gemeindezentrums.",
    "E1_p01": "Solar power created 20,000 jobs last year, the association said.",
}
ENGLISH = {
    "D1_p01": "TRANSLATED-ONE The expansion of onshore wind is stalling; permits take almost two years, residents complain.",
    "D1_p02": "TRANSLATED-TWO The solar industry created around 20,000 new jobs last year, the association said.",
    "D2_p01": "TRANSLATED-THREE The city council postponed the vote on the community centre budget on Tuesday evening.",
}
SECRET = "SECRET-REASONING-NEVER-STORED"
LANG = {"D1_p01": "de", "D1_p02": "de", "D2_p01": "de", "E1_p01": "en"}


def world(tmp: Path) -> Path:
    """Redirect every path of runner, contract, evaluator and arm into `tmp`; a registered A2 sample of three German
    paragraphs in two articles and one English paragraph; reference labels with sessions.csv, session manifest and the
    sealed threshold s = 5."""
    tmp.mkdir(parents=True, exist_ok=True)
    rx.RUNS = tmp / "runs"; rx.RAW = rx.RUNS / "raw"
    la.RUNS = rx.RUNS; la.ARM = rx.RUNS / "language_arm"; la.OUT = rx.RUNS / "tables" / "language_arm"
    rx.ALL_PARAGRAPHS = tmp / "ALL_paragraphs.csv"
    with rx.ALL_PARAGRAPHS.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["paragraph_id", "split", "article_id", "text_status", "lang", "outlet", "country", "pub_date", "n_words",
                    "text_sha256", "has_transcription_gap", "headline", "paragraph_text"])
        for pid, t in SOURCE.items():
            w.writerow([pid, "A2", pid.split("_")[0], "full", LANG[pid], "taz" if LANG[pid] == "de" else "guardian",
                        "DE", "2026-01-01", len(t.split()), h(t)[:16], "", "H", t])
    paras = [{"paragraph_id": p, "article_id": p.split("_")[0], "digest": "d-" + h(SOURCE[p])[:16], "text_sha256": h(SOURCE[p])[:16],
              "lang": LANG[p], "outlet": "taz" if LANG[p] == "de" else "guardian", "exposure": "clean_article", "split": "A2"}
             for p in SOURCE]
    sizes = Counter((q["lang"], q["outlet"]) for q in paras)
    fillers = [{"paragraph_id": f"FILL_{L}_{O}", "article_id": f"FILL_{L}", "digest": f"d-fill-{L}-{O}", "text_sha256": f"t-fill-{L}-{O}",
                "lang": L, "outlet": O, "exposure": "clean_article"} for (L, O) in sorted(sizes)]
    for q in paras:
        n_h = sizes[(q["lang"], q["outlet"])]; N_h = n_h + 1
        q.update({"stratum": f"{q['lang']}|{q['outlet']}", "N_h": N_h, "n_h": n_h, "pi": n_h / N_h, "weight": N_h / n_h})
    frame = {"kind": "A2_frame_manifest", "pre_freeze_head": "pfh", "n_eligible": len(paras) + len(fillers), "eligible_checksum": "ec",
             "paragraphs": [{k: q[k] for k in ("paragraph_id", "article_id", "digest", "text_sha256", "lang", "outlet", "exposure")}
                            for q in paras] + fillers}
    frame["digest"] = sc.canonical_digest(frame)
    (tmp / "frame.json").write_text(json.dumps(frame))
    (tmp / "registry.json").write_text(json.dumps({"assignment_log": [
        {"seq": 1, "kind": "assign", "h": "pfh"},
        {"seq": 2, "kind": "a2_frozen", "audit_ref": frame["digest"], "h": "frz", "eligible_checksum": "ec"}]}))
    sc.FRAME_MANIFEST = ev.FRAME_MANIFEST = tmp / "frame.json"
    sc.REGISTRY_FILE = ev.REGISTRY_FILE = tmp / "registry.json"
    by_lang = dict(Counter(q["lang"] for q in paras))
    man = {"kind": "A2_sample_manifest", "frame_manifest_digest": frame["digest"], "freeze_event_hash": "frz", "n": len(paras),
           "by_language": by_lang, "design": {"cap_used": {L: 24 for L in by_lang}, "cap_registered": 24, "target": by_lang},
           "paragraphs": paras}
    man["digest"] = sc.canonical_digest(man)
    mp = tmp / "A2_sample_manifest.json"; mp.write_text(json.dumps(man))
    sc.SAMPLE_MANIFEST = mp; sc.REGISTERED_TARGET = by_lang; sc.REGISTERED_CAP = 24
    rx.A3_SAMPLE_MANIFEST = sc.A3_SAMPLE_MANIFEST = tmp / "A3_sample_manifest.absent.json"
    sheets = tmp / "sheets"; sheets.mkdir(exist_ok=True)
    sc.TRANSLATIONS_FILE = sheets / "A2_translations_de_en.csv"
    sc.TRANSLATION_CHECK_FILE = sheets / "A2_translation_check.csv"
    sc.TRANSLATION_CHECK_SEAL = la.ARM / "translation_check_seal.json"
    sc.TRANSLATION_JOURNAL = la.ARM / "translation_journal.jsonl"
    sc.TRANSLATION_RECORD = la.ARM / "translation_record.json"
    # outside a repository: the commitment tests are replaced explicitly (the real ones are tested on a git repo, L7)
    sc.CHECK_SEAL_COMMITMENT = COMMITTED
    sc.TRANSLATION_PROVENANCE_COMMITMENT = COMMITTED
    sc.SEAL_EVER_COMMITTED = lambda p: False
    la.REGISTERED_RUN, la.REFERENCE_RUN, la.DECLARED_GERMAN_ELEMENT_F1 = "a2_sonnet_20261013", "a2_sonnet_20261013", "0.621"
    # the reference: D1_p01 wind (F8a, F7b), D1_p02 solar (F2c), D2_p01 off topic, E1_p01 solar (F2c)
    labels = tmp / "labels"; labels.mkdir(exist_ok=True)
    gold = {("D1_p01", "wind"): dict(elements=("F8a", "F7b"), evals={"regulation": "negative", "siting": "negative"}, subtype="onshore"),
            ("D1_p02", "solar"): dict(elements=("F2c",), evals={"jobs": "positive"}),
            ("E1_p01", "solar"): dict(elements=("F2c",), evals={"jobs": "positive"})}
    g1 = []
    for q in paras:
        r = te.p1row(q["paragraph_id"], sorted({t for (p, t) in gold if p == q["paragraph_id"]}), q["article_id"])
        r["text_sha256"] = q["text_sha256"]; g1.append(r)
    g2 = [te.p2row(p, t, **spec) for (p, t), spec in sorted(gold.items())]
    la.GOLD1 = labels / "A2_pass1_v1.0.csv"; la.GOLD2 = labels / "A2_pass2_v1.0.csv"
    te.write_csv(la.GOLD1, g1, te.P1_COLS); te.write_csv(la.GOLD2, g2, te.P2_COLS)
    with (labels / "sessions.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";"); w.writerow(["session", "labels_file", "labels_sha256"])
        w.writerow(["A2_pass1", la.GOLD1.name, h_file(la.GOLD1)[:16]]); w.writerow(["A2_pass2", la.GOLD2.name, h_file(la.GOLD2)[:16]])
    (labels / "A2_manifest.json").write_text(json.dumps({"session": "A2", "paragraphs": [
        {"paragraph_id": q["paragraph_id"], "text_sha256": q["text_sha256"]} for q in paras]}))
    sc.THRESHOLD_SEAL = tmp / "A2_threshold_seal.json"
    sc.REQUIRE_SEAL_COMMIT = False
    if not sc.THRESHOLD_SEAL.exists():                         # a world re-entered keeps its seal (written once)
        sc.write_threshold_seal(5, "value chosen blind to every A2 label (fixture)", seal_path=sc.THRESHOLD_SEAL,
                                sample_path=mp, frame_path=tmp / "frame.json")
    la.THRESHOLD = 5
    return mp


def h_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def COMMITTED(p):
    return {"commit": "fixture", "committed_at": "2999-01-01T00:00:00+00:00"}


def NOT_COMMITTED(p):
    raise sc.ContractError(f"{Path(p).name} differs from its committed version at HEAD (pending changes)")


def fake_base():
    """A baseline reproduction for fixture worlds without a registered run (the real one is L3-pre and L6)."""
    return {"run_id": "fixture", "recomputed": 1.0, "metrics_value": 1.0, "declared": None, "equal": True, "at": la.utcnow()}


class NoCredentialsError(Exception):
    pass


class ClientErr(Exception):
    def __init__(self, code: str):
        super().__init__("message never stored")
        self.response = {"Error": {"Code": code}}


def tresp(text: str | None, stop: str = "end_turn", inp: int = 100, out: int = 50) -> dict:
    blocks = [{"reasoningContent": {"reasoningText": {"text": f"{SECRET} thinking about the paragraph"}}}]
    if text is not None:
        blocks.append({"text": text})
    return {"stopReason": stop, "usage": {"inputTokens": inp, "outputTokens": out}, "output": {"message": {"content": blocks}}}


class Boom(Exception):
    def __init__(self):
        super().__init__("service unavailable")
        self.response = {"Error": {"Code": "ServiceUnavailableException"}}


class Crash(BaseException):
    """An interruption between two calls (a killed process)."""


def silent(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            return fn(*a, **kw), 0, out.getvalue() + err.getvalue()
        except SystemExit as e:
            return None, e.code, out.getvalue() + err.getvalue()


def run_main(argv: list[str], **kw):
    old = sys.argv
    sys.argv = ["language_arm.py", *argv]
    try:
        return silent(la.main, **kw)
    finally:
        sys.argv = old


def go(plan, **kw):
    fake = tr.Fake(plan)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            rec = rx.run(tr.ns(**kw), client_factory=lambda: fake, sleep=lambda s: None)
            code = 0
        except SystemExit as e:
            rec, code = None, e.code
    return rec, fake, code


def write_translations(path: Path, rows: dict) -> None:
    """A self-consistent translation file in the declared format, NOT written by the translation step (for the
    refusal tests: the journal and the record do not name it)."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=sc.TRANSLATION_COLUMNS, delimiter=";"); w.writeheader()
        for pid, text in rows.items():
            ok = text is not None
            w.writerow({"paragraph_id": pid, "article_id": pid.split("_")[0], "source_sha256": h(SOURCE[pid]),
                        "outcome": sc.T_TRANSLATED if ok else sc.T_FAILED, "translation": text or "",
                        "translation_sha256": h(text) if ok else "", "model_id": sc.TRANSLATOR_MODEL_ID,
                        "region": sc.TRANSLATOR_REGION, "request_sha256": sc.translation_request_digest(SOURCE[pid]),
                        "prompt_sha256": la.TRANSLATION_PROMPT_SHA256,
                        "stop_reason": "end_turn" if ok else "max_tokens", "input_tokens": 1, "output_tokens": 1,
                        "attempts": 1 if ok else 3, "translated_at": "2026-11-05T10:00:00+00:00"})


def mutate_rows(src: Path, dst: Path, pid: str, **changes) -> None:
    """A copy of a translation file with one row's cells changed (csv round trip, so the copy stays well formed)."""
    with src.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    for r in rows:
        if r["paragraph_id"] == pid:
            for k, v in changes.items():                  # in order: a later change may read an earlier one
                r[k] = v(r) if callable(v) else v
    with dst.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sc.TRANSLATION_COLUMNS, delimiter=";"); w.writeheader(); w.writerows(rows)


def fill_check(path: Path, fill: dict) -> None:
    """Fill the author's sheet: fill[pid] = (severity, {category: value}) or a callable mutating the row."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    for r in rows:
        spec = fill.get(r["paragraph_id"])
        if callable(spec):
            spec(r)
        elif spec is not None:
            r["severity"] = spec[0]
            r.update(spec[1])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sc.CHECK_COLUMNS, delimiter=";"); w.writeheader(); w.writerows(rows)


WIND_F8A = {"outcome": "technologies_present", "relations": [
    {"technology": "wind", "wind_siting": "onshore", "elements": ["F8a"], "evaluations": {"regulation": "negative"},
     "comparison_target": "none", "note": "permits"}]}
SOLAR_FP = {"outcome": "technologies_present", "relations": [
    {"technology": "solar", "elements": ["F2c"], "evaluations": {"jobs": "positive"}, "comparison_target": "none"}]}
SOLAR_ONE = SOLAR_FP


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def journal(path: Path | None = None) -> list[dict]:
    return [json.loads(l) for l in Path(path or la.journal_path()).read_text(encoding="utf-8").splitlines() if l.strip()]


def eval_args(mp: Path, run_id: str, **kw):
    import argparse
    base = dict(run_record=str(rx.RUNS / f"{run_id}.json"), manifest=str(mp), restrict_split=None,
                gold_pass1=str(la.GOLD1), gold_pass2=str(la.GOLD2), dev=False, gold_manifest=None,
                skip_provenance=False, threshold=5, reliability=None)
    base.update(kw); return argparse.Namespace(**base)


def evaluate_to_tables(mp: Path, run_id: str) -> dict:
    res = ev.evaluate(ev.load_inputs(eval_args(mp, run_id)), threshold=5, B=2000, seed=ev.SEED, dev=False)
    ev.write_tables(res, rx.RUNS / "tables" / run_id, rx.RUNS / f"{run_id}_class_manifest.json")
    return res


def contract_refused(name: str, fn, needle: str, forbidden: str | None = None) -> None:
    try:
        fn(); check(name, False, "no error")
    except sc.ContractError as exc:
        check(name, needle in str(exc) and (forbidden is None or forbidden not in str(exc)), str(exc))


def git_ok(*args, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=30)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="language_arm_"))
    TEXTS = [*SOURCE.values(), *ENGLISH.values()]
    leaks = lambda blob: any(t[:30] in blob for t in TEXTS)
    try:
        art = ins.load_artifacts()
        # ================= L1 the translation step ================================================== #
        w1 = tmp / "w1"; world(w1)
        fake = tr.Fake([tresp(ENGLISH["D1_p01"]),
                        Boom(), tresp(ENGLISH["D1_p02"]),
                        tresp(None, stop="max_tokens"), tresp("", stop="end_turn"), tresp("partial", stop="max_tokens")])
        slept: list[float] = []
        _r, code, out = run_main(["translate"], client_factory=lambda: fake, sleep=slept.append, baseline=fake_base)
        rows = read_csv(sc.TRANSLATIONS_FILE)
        by = {r["paragraph_id"]: r for r in rows}
        check("L1a translate writes one row per German A2 paragraph, the English paragraph untouched",
              code in (0, None) and sorted(by) == ["D1_p01", "D1_p02", "D2_p01"], f"code={code} {sorted(by)} {out[-300:]}")
        check("L1b outcomes and attempts: success, success after an exception, failed after two identical repeats",
              [(by[p]["outcome"], by[p]["attempts"]) for p in ("D1_p01", "D1_p02", "D2_p01")]
              == [("translated", "1"), ("translated", "2"), ("failed", "3")], str([(r["outcome"], r["attempts"]) for r in rows]))
        check("L1c the translation, its hash and the source hash are recorded; a failed row carries no text",
              by["D1_p01"]["translation"] == ENGLISH["D1_p01"] and by["D1_p01"]["translation_sha256"] == h(ENGLISH["D1_p01"])
              and by["D1_p02"]["source_sha256"] == h(SOURCE["D1_p02"]) and by["D2_p01"]["translation"] == ""
              and by["D2_p01"]["translation_sha256"] == "")
        want = [la.translation_request(SOURCE[p]) for p in ("D1_p01", "D1_p02", "D1_p02", "D2_p01", "D2_p01", "D2_p01")]
        check("L1d every call is the frozen request: system prompt, the paragraph alone, temperature 0, 4096, reasoning medium",
              fake.seen == want and all(k["modelId"] == "openai.gpt-oss-120b-1:0" and k["inferenceConfig"] == {"temperature": 0, "maxTokens": 4096}
                                        and k["additionalModelRequestFields"] == {"reasoning_effort": "medium"}
                                        and k["system"][0]["text"].startswith("Translate the user's German news paragraph")
                                        and k["system"][0]["text"].endswith("Output only the English translation.")
                                        and k["messages"] == [{"role": "user", "content": [{"text": k["messages"][0]["content"][0]["text"]}]}]
                                        for k in fake.seen), f"{len(fake.seen)} calls")
        check("L1e the English paragraph is never sent", not any(SOURCE["E1_p01"] in json.dumps(k) for k in fake.seen))
        public = [la.journal_path(), la.record_path()]
        blob = "".join(p.read_text(encoding="utf-8") for p in public) + out
        private_blob = sc.TRANSLATIONS_FILE.read_text(encoding="utf-8")
        check("L1f no paragraph or translation text reaches the journal, the record or the console",
              not leaks(blob) and "partial" not in blob, blob[:200])
        check("L1g reasoning blocks are discarded everywhere (counted, never stored)",
              SECRET not in blob and SECRET not in private_blob
              and json.loads(la.record_path().read_text())["attempts"]["reasoning_blocks_discarded"] == 5)
        rec = json.loads(la.record_path().read_text())
        check("L1h the record: intact digest, complete file digest, usage over every finished call, a provisional cost estimate",
              rec["file"]["complete"] and rec["file"]["sha256"] == h_file(sc.TRANSLATIONS_FILE) and rec["digest"] == sc.canonical_digest(rec)
              and rec["usage"] == {"input_tokens": 500, "output_tokens": 250} and rec["attempts"]["calls_started"] == 6
              and rec["attempts"]["charged"] == 6 and rec["attempts"]["codes"] == {"ok": 2, "error": 1, "stop_max_tokens": 2, "empty_text": 1}
              and rec["cost"]["estimated_usd"] == round((500 * 0.15 + 250 * 0.60) / 1e6, 6) and "PROVISIONAL" in rec["cost"]["note"]
              and "joined with a newline" in rec["request"]["post_processing"], json.dumps(rec["attempts"]))
        check("L1i the console prints counts, token usage and the cost estimate", "2 translated, 1 failed" in out
              and "tokens in 500 out 250" in out and "est. cost $" in out, out[-300:])
        check("L1i2 an exception is repeated after a backoff (2 s), a stop or an empty text at once; no wait after the last attempt",
              slept == [2.0], str(slept))
        before = sc.TRANSLATIONS_FILE.read_bytes()
        fake2 = tr.Fake([tresp("x")])
        _r, code, out = silent(la.translate, resume=True, client_factory=lambda: fake2, baseline=fake_base)
        check("L1j a complete translation file is never overwritten or re-generated (even with --resume)",
              code not in (0, None) and "complete" in str(code) and sc.TRANSLATIONS_FILE.read_bytes() == before and not fake2.seen,
              str(code))
        jr = journal()
        check("L1k the journal holds every call with request digest, code, stop reason and usage, no text",
              sum(r["type"] == "attempt_started" for r in jr) == 6 and sum(r["type"] == "attempt_finished" for r in jr) == 6
              and all(r.get("request_sha256") == sc.translation_request_digest(SOURCE[r["paragraph_id"]])
                      for r in jr if r["type"] == "attempt_started")
              and {r.get("error_code") for r in jr if r.get("code") == "error"} == {"ServiceUnavailableException"})
        rs = [r for r in jr if r["type"] == "run_started"]
        first_call = min(r["at"] for r in jr if r["type"] == "attempt_started")
        check("L1k2 the baseline reproduction precedes the first call and is journalled and recorded (item 8)",
              rs[0]["baseline"]["equal"] is True and rs[0]["baseline"]["at"] <= first_call
              and rec["baseline_before_first_call"] == rs[0]["baseline"], json.dumps(rs[0].get("baseline")))
        # an interruption between the last row and the record: the record is rebuilt from journal and file, no call
        rec_bytes = la.record_path().read_bytes(); la.record_path().unlink()
        fake2 = tr.Fake([tresp("x")])
        _r, code, _o = silent(la.translate, client_factory=lambda: fake2, baseline=fake_base)
        check("L1k3 a complete file without its record: the record is rebuilt (same bytes), still no call, still refused",
              code not in (0, None) and "rebuilt" in str(code) and la.record_path().read_bytes() == rec_bytes and not fake2.seen,
              str(code))
        # the baseline gate: no call before the registered German figure is reproduced
        w1b = tmp / "w1b"; world(w1b)
        import compare_models as cm

        def failing_base():
            raise cm.CompareError("the German scoring path does not reproduce the registered German element micro-F1")
        fake = tr.Fake([tresp("x")] * 3)
        _r, code, _o = silent(la.translate, client_factory=lambda: fake, baseline=failing_base)
        check("L1k4 translate refuses before any call when the registered German figure is not reproduced",
              code not in (0, None) and "item 8" in str(code) and not fake.seen and not sc.TRANSLATIONS_FILE.exists()
              and not la.journal_path().exists(), str(code))
        # resume after an interruption: only the missing rows, the interrupted attempt counts against the allowance
        w1r = tmp / "w1r"; world(w1r)
        fake = tr.Fake([tresp(ENGLISH["D1_p01"]), Crash()])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                la.translate(client_factory=lambda: fake, baseline=fake_base)
            crashed = False
        except Crash:
            crashed = True
        n_rows = len(read_csv(sc.TRANSLATIONS_FILE))
        check("L1l an interruption leaves the rows written so far", crashed and n_rows == 1, f"{crashed} {n_rows}")
        _r, code, _o = silent(la.translate, client_factory=lambda: tr.Fake([]), baseline=fake_base)
        check("L1m an incomplete file is continued only with --resume", code not in (0, None) and "--resume" in str(code), str(code))
        keep = sc.TRANSLATIONS_FILE.read_bytes()
        with sc.TRANSLATIONS_FILE.open("ab") as f:
            f.write(b"D1_p02;D1;abc")                              # a torn row: no terminator
        fake = tr.Fake([tresp(ENGLISH["D1_p02"]), tresp(ENGLISH["D2_p01"])])
        _r, code, _o = silent(la.translate, resume=True, client_factory=lambda: fake, baseline=fake_base)
        check("L1m2 a torn last row is refused on resume: nothing appended, no call", code not in (0, None)
              and "torn" in str(code) and not fake.seen, str(code))
        sc.TRANSLATIONS_FILE.write_bytes(keep + b"D1_p02;D1;abc\r\n")       # a terminated but malformed row
        _r, code, _o = silent(la.translate, resume=True, client_factory=lambda: fake, baseline=fake_base)
        check("L1m3 a malformed row is refused on resume (field count), its content never echoed",
              code not in (0, None) and "malformed" in str(code) and "abc" not in str(code) and not fake.seen, str(code))
        sc.TRANSLATIONS_FILE.write_bytes(keep)
        _r, code, _o = silent(la.translate, resume=True, client_factory=lambda: fake, baseline=fake_base)
        rows = {r["paragraph_id"]: r for r in read_csv(sc.TRANSLATIONS_FILE)}
        rec = json.loads(la.record_path().read_text())
        check("L1n --resume translates the missing paragraphs only; the interrupted attempt is charged (unknown completion)",
              code in (0, None) and len(fake.seen) == 2 and rows["D1_p02"]["attempts"] == "2" and rows["D1_p01"]["attempts"] == "1"
              and rec["file"]["complete"] and rec["attempts"]["unknown_completion"] == 1, f"{code} {len(fake.seen)} {rec['attempts']}")
        sc.TRANSLATIONS_FILE.unlink()
        _r, code, _o = silent(la.translate, client_factory=lambda: tr.Fake([]), baseline=fake_base)
        check("L1o a journal with finished translations but no translation file is refused (no re-generation)",
              code not in (0, None) and "never re-generated" in str(code), str(code))
        _r, code, _o = silent(la.ensure_private, la.ROOT / "tools" / "pipeline" / "not_ignored.csv")
        _r2, code2, _o2 = silent(la.ensure_private, la.ROOT / "tools" / "annotation" / "sheets" / "A2_translations_de_en.csv")
        check("L1p text files are refused where git does not ignore them; tools/annotation/sheets/ is ignored",
              code not in (0, None) and code2 in (0, None), f"{code} {code2}")
        # infrastructure: a rejection before the translator answered charges nothing, writes no row, aborts
        w1a = tmp / "w1a"; world(w1a)
        fake = tr.Fake([NoCredentialsError()])
        slept = []
        _r, code, out = silent(la.translate, client_factory=lambda: fake, sleep=slept.append, baseline=fake_base)
        jr = journal()
        check("L1q missing credentials abort the step: exit 2, no row, journalled, no attempt charged, no repeat",
              code == 2 and len(fake.seen) == 1 and not sc.TRANSLATIONS_FILE.exists() and not slept
              and [r["error_code"] for r in jr if r["type"] == "infrastructure_abort"] == ["NoCredentialsError"]
              and la.journal_counts(jr)[0] == Counter() and "message never stored" not in la.journal_path().read_text(), f"{code} {out[-200:]}")
        fake = tr.Fake([ClientErr("ValidationException")])
        _r, code, _o = silent(la.translate, client_factory=lambda: fake, sleep=slept.append, baseline=fake_base)
        check("L1r a validation error on the frozen request aborts the same way (not an attempt)",
              code == 2 and len(fake.seen) == 1 and not sc.TRANSLATIONS_FILE.exists(), str(code))
        fake = tr.Fake([ClientErr("AccessDeniedException")])
        _r, code, _o = silent(la.translate, client_factory=lambda: fake, baseline=fake_base)
        check("L1s access denied aborts the same way", code == 2 and len(fake.seen) == 1, str(code))
        fake = tr.Fake([tresp(ENGLISH[p]) for p in ("D1_p01", "D1_p02", "D2_p01")])
        _r, code, _o = silent(la.translate, client_factory=lambda: fake, baseline=fake_base)
        rows = read_csv(sc.TRANSLATIONS_FILE)
        check("L1t after aborts the step runs normally (no charged attempt, so no --resume needed): attempts 1 each",
              code in (0, None) and [r["attempts"] for r in rows] == ["1", "1", "1"]
              and json.loads(la.record_path().read_text())["attempts"]["infrastructure_aborts"] == 3, f"{code} {rows and rows[0]['attempts']}")
        w1t = tmp / "w1t"; world(w1t)
        fake = tr.Fake([ClientErr("ThrottlingException")] * 3 + [tresp(ENGLISH[p]) for p in ("D1_p01", "D1_p02", "D2_p01")])
        slept = []
        _r, code, _o = silent(la.translate, client_factory=lambda: fake, sleep=slept.append, baseline=fake_base)
        rows = read_csv(sc.TRANSLATIONS_FILE)
        check("L1u throttling backs off (2, 4, 8 s) with the identical request and charges no attempt",
              code in (0, None) and slept == [2.0, 4.0, 8.0] and rows[0]["attempts"] == "1"
              and all(k == fake.seen[0] for k in fake.seen[:4]), f"{code} {slept}")
        w1t2 = tmp / "w1t2"; world(w1t2)
        fake = tr.Fake([ClientErr("ThrottlingException")] * 7)
        slept = []
        _r, code, _o = silent(la.translate, client_factory=lambda: fake, sleep=slept.append, baseline=fake_base)
        check("L1v throttling that persists through six backoffs (to 60 s) aborts: exit 2, no row, nothing charged",
              code == 2 and slept == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0] and len(fake.seen) == 7
              and not sc.TRANSLATIONS_FILE.exists(), f"{code} {slept}")
        w1e = tmp / "w1e"; world(w1e)
        fake = tr.Fake([Boom(), Boom(), Boom()] + [tresp(ENGLISH[p]) for p in ("D1_p02", "D2_p01")])
        slept = []
        _r, code, _o = silent(la.translate, client_factory=lambda: fake, sleep=slept.append, baseline=fake_base)
        rows = {r["paragraph_id"]: r for r in read_csv(sc.TRANSLATIONS_FILE)}
        check("L1w three raising calls exhaust the allowance with backoffs 2 s and 4 s: a failed row",
              code in (0, None) and slept == [2.0, 4.0] and rows["D1_p01"]["outcome"] == "failed" and rows["D1_p01"]["attempts"] == "3",
              f"{code} {slept}")

        # ================= L2 the check sheet, its validation and seal (world w1: one failed translation) ===== #
        world(w1)                                                  # the w1 files are still on disk
        _r, code, out = run_main(["qa-sheet"])
        with sc.TRANSLATION_CHECK_FILE.open(encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f, delimiter=";"); hdr = rd.fieldnames; qrows = list(rd)
        check("L2a qa-sheet writes the private sheet: paragraph id, source, translation and the empty declared rubric",
              code in (0, None) and hdr == ["paragraph_id", "source_text", "translation", "omission_or_addition", "technology_referent",
                                            "negation", "modality_or_hedging", "quantities_units", "time_reference",
                                            "attribution_reported_speech", "ambiguity_resolved", "other", "severity", "note"]
              and [r["paragraph_id"] for r in qrows] == ["D1_p01", "D1_p02", "D2_p01"]
              and all(r[c] == "" for r in qrows for c in sc.CHECK_COLUMNS[3:])
              and qrows[0]["source_text"] == SOURCE["D1_p01"] and qrows[0]["translation"] == ENGLISH["D1_p01"]
              and not leaks(out), f"{code} {hdr}")
        _r, code, _o = run_main(["qa-sheet"])
        check("L2b the sheet is written once", code not in (0, None) and "written once" in str(code), str(code))
        _r, code, _o = run_main(["validate-check"])
        check("L2c an unfilled sheet is refused (severity missing)", code not in (0, None) and "severity" in str(code), str(code))
        pristine = sc.TRANSLATION_CHECK_FILE.read_bytes()

        def with_fill(fill):
            sc.TRANSLATION_CHECK_FILE.write_bytes(pristine); fill_check(sc.TRANSLATION_CHECK_FILE, fill)
            return run_main(["validate-check"])

        def expect_refusal(name, fill, needle, forbidden=None):
            _r_, c_, _o_ = with_fill(fill)
            check(name, c_ not in (0, None) and needle in str(c_) and (forbidden is None or forbidden not in str(c_)), str(c_))
        good = {"D1_p01": ("major", {"negation": "1", "note": "dropped a negation"}), "D1_p02": ("none", {})}
        expect_refusal("L2d a category value outside 1 / x / 0 / empty is refused, the cell never echoed",
                       {**good, "D1_p02": ("none", {"negation": "quoted paragraph text"})}, "invalid value", "quoted paragraph")
        _r, code, out = with_fill({**good, "D1_p02": ("none", {"other": "x"})})
        check("L2e severity none with a flagged category is the author's judgement: counted and shown, not refused",
              code in (0, None) and "1 'none' pair(s) with a flagged category" in out, f"{code} {out[-200:]}")
        _r, code, out = with_fill({**good, "D1_p02": ("minor", {})})
        check("L2f severity minor without a flagged category is counted and shown, not refused",
              code in (0, None) and "1 minor/major pair(s) without a flagged category" in out, f"{code} {out[-200:]}")
        expect_refusal("L2g a severity outside none / minor / major is refused, the cell never echoed",
                       {**good, "D1_p02": ("smallish", {})}, "severity", "smallish")
        expect_refusal("L2h a translation edited in the sheet is refused",
                       {**good, "D1_p02": lambda r: r.update({"severity": "none", "translation": r["translation"] + " (edited)"})},
                       "differs from the translation file")
        expect_refusal("L2i a failed translation that carries a severity is refused", {**good, "D2_p01": ("none", {})}, "failed translation")
        sc.TRANSLATION_CHECK_FILE.write_bytes(pristine); fill_check(sc.TRANSLATION_CHECK_FILE, good)
        txt = sc.TRANSLATION_CHECK_FILE.read_text(encoding="utf-8-sig").splitlines()
        sc.TRANSLATION_CHECK_FILE.write_text("\n".join(txt[:-1]) + "\n", encoding="utf-8-sig")
        _r, code, _o = run_main(["validate-check"])
        check("L2j a sheet that does not list every paragraph is refused", code not in (0, None) and "exactly once" in str(code), str(code))
        sc.TRANSLATION_CHECK_FILE.write_bytes(pristine); fill_check(sc.TRANSLATION_CHECK_FILE, good)
        keep = sc.TRANSLATION_CHECK_FILE.read_bytes()
        sc.TRANSLATION_CHECK_FILE.write_bytes(keep.decode("utf-8-sig").replace("dropped a negation", "Überprüft").encode("cp1252"))
        _r, code, _o = run_main(["validate-check"])
        check("L2j2 a sheet saved in another encoding is a clean refusal with a hint, not a traceback",
              code not in (0, None) and "not UTF-8" in str(code) and "CSV UTF-8" in str(code), str(code))
        sc.TRANSLATION_CHECK_FILE.write_bytes(keep)
        _r, code, out = run_main(["validate-check"])
        check("L2k a complete sheet validates: its sha256 and the counts per severity and category, no text",
              code in (0, None) and h_file(sc.TRANSLATION_CHECK_FILE) in out and "major 1" in out and "none 1" in out and "minor 0" in out
              and "negation 1" in out and "1 without translation" in out and "dropped a negation" not in out
              and not leaks(out) and not sc.TRANSLATION_CHECK_SEAL.exists(), out)
        _r, code, out = run_main(["validate-check", "--seal"])
        seal = json.loads(sc.TRANSLATION_CHECK_SEAL.read_text())
        check("L2l --seal writes the tracked seal: the check file's digest, the translation file's digest, counts only",
              code in (0, None) and seal["check_sha256"] == h_file(sc.TRANSLATION_CHECK_FILE)
              and seal["translation_file_sha256"] == h_file(sc.TRANSLATIONS_FILE) and seal["digest"] == sc.canonical_digest(seal)
              and seal["summary"]["severity"] == {"none": 1, "minor": 0, "major": 1}
              and seal["translated_runs_before_seal"] == []
              and "D1_p01" not in sc.TRANSLATION_CHECK_SEAL.read_text() and "dropped" not in sc.TRANSLATION_CHECK_SEAL.read_text(), out)
        _de = [q for q in json.loads(sc.SAMPLE_MANIFEST.read_text())["paragraphs"] if q["lang"] == "de"]
        trows, binding = sc.load_translation_file(sc.TRANSLATIONS_FILE, _de)
        ok = sc.verify_check_seal(binding["file_sha256"], trows)
        check("L2m the committed seal verifies, carries its commitment and yields the major paragraphs for the sensitivity",
              ok["major_paragraphs"] == ["D1_p01"] and ok["digest"] == seal["digest"] and ok["commitment"]["commit"] == "fixture")

        def seal_refused(name, needle, sha=None):
            contract_refused(name, lambda: sc.verify_check_seal(sha or binding["file_sha256"], trows), needle)
        sc.CHECK_SEAL_COMMITMENT = NOT_COMMITTED
        seal_refused("L2n an uncommitted (or changed since its first commit) seal is refused", "not committed unchanged")
        sc.CHECK_SEAL_COMMITMENT = lambda p: {"commit": "c0", "committed_at": "2000-01-01T00:00:00+00:00"}
        seal_refused("L2n2 a seal whose sealed_at postdates its first commit is refused", "postdates")
        sc.CHECK_SEAL_COMMITMENT = COMMITTED
        seal_refused("L2o a seal naming another translation file is refused", "another translation file", sha="0" * 64)
        keep = sc.TRANSLATION_CHECK_FILE.read_bytes()
        fill_check(sc.TRANSLATION_CHECK_FILE, {"D1_p02": ("minor", {"other": "1"})})
        seal_refused("L2p a check file changed after sealing is refused", "changed after it was sealed")
        _r, code, _o = run_main(["validate-check", "--seal"])
        check("L2p2 a different seal is written while none was ever committed (the author corrects before committing)",
              code in (0, None) and json.loads(sc.TRANSLATION_CHECK_SEAL.read_text())["digest"] != seal["digest"], str(code))
        sc.TRANSLATION_CHECK_FILE.write_bytes(keep)
        sc.SEAL_EVER_COMMITTED = lambda p: True
        _r, code, _o = run_main(["validate-check", "--seal"])
        check("L2p3 a seal path that is tracked or was ever committed is never rewritten with a different seal",
              code not in (0, None) and "written once" in str(code), str(code))
        sc.SEAL_EVER_COMMITTED = lambda p: False
        _r, code, _o = run_main(["validate-check", "--seal"])
        sc.SEAL_EVER_COMMITTED = lambda p: True
        _r, code2, out2 = run_main(["validate-check", "--seal"])
        check("L2p4 re-sealing an unchanged check leaves the seal as it is, also once committed",
              code in (0, None) and code2 in (0, None) and "already sealed, unchanged" in out2, f"{code} {code2}")
        sc.SEAL_EVER_COMMITTED = lambda p: False
        stxt = sc.TRANSLATION_CHECK_SEAL.read_text(); sc.TRANSLATION_CHECK_SEAL.unlink()
        seal_refused("L2q no seal, no figure", "no translation check seal")
        sc.TRANSLATION_CHECK_SEAL.write_text(stxt)
        seal = json.loads(stxt)

        # ================= L3 the runner's translated-input override (world w1: D2_p01 failed) ============== #
        rec, fake, code = go(tr.gate_plan() + [tr.resp(WIND_F8A)] * 3 + [tr.resp(tr.SOLAR_TWO)] * 3,
                             manifest=str(sc.SAMPLE_MANIFEST), repeats=3, run_id="arm_fail", translated_input=str(sc.TRANSLATIONS_FILE))
        terms = [r for r in rx.Journal.read(rx.RUNS / "arm_fail_calls.jsonl")[0] if r["type"] == "terminal"]
        check("L3a a failed translation is a failed execution with no call: no_translated_input, three terminals",
              code == 0 and len(fake.seen) == 3 + 6 and sorted(t["outcome"] for t in terms if t["paragraph_id"] == "D2_p01")
              == ["no_translated_input"] * 3 and all(t["n_attempts"] == 0 for t in terms if t["paragraph_id"] == "D2_p01"),
              f"code={code} calls={len(fake.seen)}")
        _r, code, out = run_main(["validate-check", "--seal"])
        check("L3a2 the seal records which translated-input runs existed when it was written (here: unchanged check, kept)",
              code in (0, None) and "already sealed, unchanged" in out, f"{code} {out[-200:]}")
        check("L3a3 sample_contract.translated_runs lists the existing translated-input run with its first call",
              [r["run_id"] for r in sc.translated_runs(rx.RUNS)] == ["arm_fail"] and sc.translated_runs(rx.RUNS)[0]["first_call_at"])

        # ================= L3-L5 world w2: every paragraph translated ===================================== #
        w2 = tmp / "w2"; mp = world(w2)
        # the registered-like run on the original texts (k = 3), evaluated first: the baseline the translation needs
        reg, fake_r, code_r = go(tr.gate_plan() + [tr.resp(tr.WIND)] * 3 + [tr.resp(tr.SOLAR_TWO)] * 3 + [tr.resp(tr.OFF)] * 3
                                 + [tr.resp(SOLAR_ONE)] * 3, manifest=str(mp), repeats=3, run_id="reg")
        res_r = evaluate_to_tables(mp, "reg")
        la.REGISTERED_RUN = la.REFERENCE_RUN = "reg"; la.DECLARED_GERMAN_ELEMENT_F1 = "1.000"
        fake_t = tr.Fake([tresp(ENGLISH[p]) for p in ("D1_p01", "D1_p02", "D2_p01")])
        _r, code_t, out_t = silent(la.translate, client_factory=lambda: fake_t)      # the REAL baseline path on the fixture
        trec = json.loads(la.record_path().read_text())
        check("L3-pre translate runs the arm's scoring path on the registered run before its first call and records it",
              code_t in (0, None) and trec["baseline_before_first_call"]["equal"] is True
              and trec["baseline_before_first_call"]["run_id"] == "reg" and trec["baseline_before_first_call"]["recomputed"] == 1.0
              and trec["baseline_before_first_call"]["metrics_sha256"] == h_file(rx.RUNS / "tables" / "reg" / "metrics.json")
              and trec["baseline_before_first_call"]["at"] <= trec["first_call_at"], f"{code_t} {out_t[-300:]}")
        tfile = str(sc.TRANSLATIONS_FILE)
        arm, fake_a, code_a = go(tr.gate_plan() + [tr.resp(WIND_F8A)] * 3 + [tr.resp(tr.SOLAR_TWO)] * 3 + [tr.resp(SOLAR_FP)] * 3,
                                 manifest=str(mp), repeats=3, run_id="arm", translated_input=tfile)
        de = [q for q in json.loads(mp.read_text())["paragraphs"] if q["lang"] == "de"]
        _rows, bind = sc.load_translation_file(sc.TRANSLATIONS_FILE, de)
        check("L3b the translated arm completes, reportable, under the declared status", code_r == 0 and code_a == 0
              and arm["reportable"] and arm["analysis_status"] == "exploratory reused A2 — translated inputs"
              and reg["analysis_status"] == "registered primary", f"{code_r} {code_a}")
        c = arm["execution_contract"]
        check("L3c the execution contract binds the file digest, the per-paragraph hashes, the translator, journal and record",
              c["translated_input"] == bind == arm["translated_input"] and bind["file_sha256"] == h_file(sc.TRANSLATIONS_FILE)
              and bind["pairs_sha256"] == sc.translation_pairs_digest(_rows) and bind["translator_model_id"] == "openai.gpt-oss-120b-1:0"
              and bind["journal_sha256"] == h_file(la.journal_path()) and bind["record_sha256"] == h_file(la.record_path())
              and bind["prompt_sha256"] == sc.TRANSLATION_PROMPT_SHA256
              and arm["execution_fingerprint"] == hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest())
        check("L3d manifest, design and German identity stay A2's: the registered manifest digest, 3 German paragraphs, k = 3",
              arm["manifest"]["sha256"] == reg["manifest"]["sha256"] and arm["manifest"]["n_paragraphs"] == 3
              and arm["manifest"]["restrict_lang"] == "de" and arm["repeats"] == 3 and "restrict_lang" not in reg["manifest"]
              and "translated_input" not in reg["execution_contract"])
        sent = fake_a.seen[3:]
        order = ["D1_p01"] * 3 + ["D1_p02"] * 3 + ["D2_p01"] * 3
        same_req = all(rx.request_digest(kw) == rx.request_digest({"modelId": ins.MODELS["sonnet"], **rx.model_request("sonnet", art, ENGLISH[p])})
                       for kw, p in zip(sent, order))
        only_text = all(json.dumps(kw, sort_keys=True).replace(json.dumps(ENGLISH[p])[1:-1], json.dumps(SOURCE[p])[1:-1])
                        == json.dumps(r_kw, sort_keys=True) for kw, p, r_kw in zip(sent, order, fake_r.seen[3:]))
        check("L3e the request is the registered request byte for byte except the paragraph text", same_req and only_text and len(sent) == 9)
        check("L3f neither the source text nor the English paragraph is sent by the arm",
              not any(SOURCE[p] in json.dumps(kw, ensure_ascii=False) for kw in sent for p in SOURCE))
        public = "".join((rx.RUNS / f).read_text(encoding="utf-8") for f in ("arm.json", "arm_calls.jsonl", "arm_terminal.csv", "arm_predictions.csv"))
        check("L3g no translation text reaches a public file of the run", not any(t[:30] in public for t in ENGLISH.values()))

        def refused(name, needle=None, **kw):
            base = dict(manifest=str(mp), repeats=3, run_id=f"x{len(PASSED) + len(FAILED)}", translated_input=tfile)
            base.update(kw)
            _rec, fk, cd = go(tr.gate_plan(), **base)
            check(name, cd not in (0, None) and not fk.seen and (needle is None or needle in str(cd)), str(cd))
        refused("L3h --translated-input with --dev is refused before the gate", "do not combine", dev=True, restrict_split="A2")
        refused("L3i --translated-input with k != 3 is refused", "k = 3", repeats=1)
        refused("L3j --translated-input under another model is refused by the arm's guard", "registered model", model="opus")
        refused("L3k --translated-input under another model with a comparison decision is refused", "registered model",
                model="opus", repeats=1, model_comparison_decision="author, today")
        refused("L3l --translated-input with a revised instrument is refused by the arm's guard", "registered instrument v1.0",
                instrument="v1.1S")
        refused("L3m --translated-input with another analysis's decision flag is refused", "does not combine",
                w3_author_decision="author, today")
        other = json.loads(mp.read_text()); other["paragraphs"] = other["paragraphs"][:-1]; other["n"] = 3
        other["digest"] = sc.canonical_digest(other); omp = w2 / "other_manifest.json"; omp.write_text(json.dumps(other))
        refused("L3n a manifest that is not the registered sample is refused by the arm's guard", "registered A2 sample manifest only",
                manifest=str(omp))
        for kind, label in ((sc.A3_KIND, "L3n2 the A3 census manifest"), (sc.FRAME_COMPLETION_KIND, "L3n3 the frame-completion manifest")):
            k_doc = json.loads(mp.read_text()); k_doc["kind"] = kind; k_doc["digest"] = sc.canonical_digest(k_doc)
            kp = w2 / f"{kind}.json"; kp.write_text(json.dumps(k_doc))
            refused(f"{label} is refused by the arm's guard", "registered A2 sample manifest only", manifest=str(kp))
        refused("L3n4 a translation file where git does not ignore it is refused before the gate", "not ignored by git",
                translated_input=str(la.ROOT / "tools" / "pipeline" / "not_ignored_translations.csv"))
        bad = w2 / "tampered.csv"; bad.write_text(sc.TRANSLATIONS_FILE.read_text().replace("TRANSLATED-TWO", "TRANSLATED-2"))
        refused("L3o a tampered translation (hash not reproduced) is refused before the gate", "edited", translated_input=str(bad))
        write_translations(bad, {k: v for k, v in ENGLISH.items() if k != "D2_p01"})
        refused("L3p a translation file without every German paragraph is refused", "exactly", translated_input=str(bad))
        txt = sc.TRANSLATIONS_FILE.read_text(); src = h(SOURCE["D1_p01"])
        bad.write_text(txt.replace(src, src[:16] + "f" * 48))
        refused("L3q a translation not made from the registered paragraph text is refused", "source hash differs",
                translated_input=str(bad))
        bad.write_text(txt.replace("openai.gpt-oss-120b-1:0", "openai.gpt-oss-20b-1:0"))
        refused("L3r a translation by another system is refused, the cell never echoed", "not the declared", translated_input=str(bad))
        check("L3r2 (the refusal names the declared translator only)", "gpt-oss-20b" not in str(go(tr.gate_plan(), manifest=str(mp), repeats=3,
              run_id="x_r2", translated_input=str(bad))[2]))
        mutate_rows(sc.TRANSLATIONS_FILE, bad, "D1_p02", translation=lambda r: r["translation"] + " Indeed.",
                    translation_sha256=lambda r: h(r["translation"]))
        refused("L3s a self-consistent re-generated or hand-edited translation (hash recomputed) is refused: not the journalled one",
                "journal", translated_input=str(bad))
        write_translations(bad, ENGLISH)
        refused("L3t a self-consistent file the translation step never wrote is refused (the record names another file)",
                "does not name this translation file",
                translated_input=str(bad))
        sc.TRANSLATION_PROVENANCE_COMMITMENT = NOT_COMMITTED
        refused("L3u the arm does not run before the translation record and journal are committed unchanged", "pending changes")
        sc.TRANSLATION_PROVENANCE_COMMITMENT = COMMITTED
        _rec, fk, cd = go(tr.gate_plan(), manifest=str(mp), repeats=3, run_id="arm", resume=True, translated_input=str(bad))
        check("L3v a resume under another translation file is refused, no call", cd not in (0, None) and not fk.seen, str(cd))
        # the shared verification, row by row (copies of the real file; texts held as by the runner)
        texts = {p: SOURCE[p] for p in ("D1_p01", "D1_p02", "D2_p01")}

        def row_refused(name, needle, use_texts=True, **changes):
            mutate_rows(sc.TRANSLATIONS_FILE, bad, "D1_p01", **changes)
            contract_refused(name, lambda: sc.load_translation_file(bad, de, texts if use_texts else None), needle)
        row_refused("L3w a translation made under another prompt is refused", "frozen translation prompt", prompt_sha256="0" * 64)
        other_dec = hashlib.sha256(json.dumps({**sc.translation_request(SOURCE["D1_p01"]), "inferenceConfig": {"temperature": 0.7, "maxTokens": 4096}},
                                              sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        row_refused("L3x a translation made under other decoding is refused (request digest recomputed from the registered text)",
                    "request digest differs", request_sha256=other_dec)
        row_refused("L3x2 ... and without the text, by the journal (the journalled requests are not the row's)",
                    "journalled requests", use_texts=False, request_sha256=other_dec)
        row_refused("L3y a translated row that did not stop with end_turn is refused", "end_turn", stop_reason="max_tokens")
        row_refused("L3z a translated row with more than three attempts is refused", "attempts", attempts="4")
        row_refused("L3z2 a row whose article is not the manifest's is refused", "article_id", article_id="D9")
        check("L3z3 the declared prompt digest is the verbatim prompt's, and the contract's request digest is the runner's rule",
              sc.TRANSLATION_PROMPT_SHA256 == h(sc.TRANSLATION_PROMPT)
              and sc.translation_request_digest(SOURCE["D1_p01"]) == rx.request_digest(la.translation_request(SOURCE["D1_p01"])))
        rbytes, jbytes = la.record_path().read_bytes(), la.journal_path().read_bytes()
        la.record_path().unlink()
        contract_refused("L3z4 no translation record, no binding", lambda: sc.load_translation_file(sc.TRANSLATIONS_FILE, de, texts),
                         "no translation record")
        r_ = json.loads(rbytes); r_["baseline_before_first_call"] = None; r_["digest"] = sc.canonical_digest(r_)
        la.record_path().write_text(json.dumps(r_))
        contract_refused("L3z5 a record without the baseline reproduction before the first call is refused (item 8)",
                         lambda: sc.load_translation_file(sc.TRANSLATIONS_FILE, de, texts), "item 8")
        la.record_path().write_bytes(rbytes)
        with la.journal_path().open("ab") as f:
            f.write(b'{"type": "run_started", "at": "2026-11-06T00:00:00+00:00"}\n')
        contract_refused("L3z6 a journal changed after the record is refused", lambda: sc.load_translation_file(sc.TRANSLATIONS_FILE, de, texts),
                         "journal changed after the record")
        la.journal_path().write_bytes(jbytes)

        # ================= L4 the evaluator =========================================================== #
        try:
            ev.load_inputs(eval_args(mp, "arm")); check("L4a no agreement figure of the arm before the check seal exists", False, "no error")
        except ev.EvalError as exc:
            check("L4a no agreement figure of the arm before the check seal exists",
                  "no translation check seal" in str(exc) and "no agreement figure" in str(exc), str(exc))
        _r, code, _o = run_main(["qa-sheet"])
        fill_check(sc.TRANSLATION_CHECK_FILE, {"D1_p01": ("major", {"negation": "x"}), "D1_p02": ("none", {}),
                                               "D2_p01": ("minor", {"time_reference": "1"})})
        try:
            ev.load_inputs(eval_args(mp, "arm")); check("L4b a filled but unsealed check is not enough", False, "no error")
        except ev.EvalError as exc:
            check("L4b a filled but unsealed check is not enough", "no translation check seal" in str(exc), str(exc))
        _r, code, _o = run_main(["validate-check", "--seal"])
        check("L4b2 the seal written after the extraction records that run", [r["run_id"] for r in
              json.loads(sc.TRANSLATION_CHECK_SEAL.read_text())["translated_runs_before_seal"]] == ["arm"])
        sc.CHECK_SEAL_COMMITMENT = NOT_COMMITTED
        try:
            ev.load_inputs(eval_args(mp, "arm")); check("L4c a sealed but uncommitted check is refused", False, "no error")
        except ev.EvalError as exc:
            check("L4c a sealed but uncommitted check is refused", "not committed" in str(exc), str(exc))
        sc.CHECK_SEAL_COMMITMENT = COMMITTED
        try:
            ev.load_inputs(eval_args(mp, "arm", dev=True, skip_provenance=True)); check("L4d a translated run is never evaluated in development mode", False, "no error")
        except ev.EvalError as exc:
            check("L4d a translated run is never evaluated in development mode", "development" in str(exc), str(exc))
        inp_a = ev.load_inputs(eval_args(mp, "arm"))
        res_a = ev.evaluate(inp_a, threshold=5, B=2000, seed=ev.SEED, dev=False)
        de_el = res_a["point"]["de"]["element"]["micro_f1_weighted"]
        check("L4e the translated run is scored on the German paragraphs only, with the original weights, under its status",
              res_a["analysis_status"] == sc.TRANSLATED_STATUS and res_a["populations"] == {"pooled": 3, "de": 3}
              and {p["paragraph_id"]: p["weight"] for p in inp_a["paras"]} == {q["paragraph_id"]: q["weight"] for q in de},
              str(res_a["populations"]))
        # hand count: TP F8a (D1_p01), F2c (D1_p02); FN F7b (D1_p01); FP F2c (D2_p01, off topic in the reference)
        check("L4f German element micro-F1 of the arm is the hand-computed 2*2/(2*2+1+1) = 2/3", abs(de_el - 2 / 3) < 1e-12, repr(de_el))
        tc = res_a["inputs"]["translation_check"]
        check("L4g the metrics bind the translated input and the sealed check with its commitment (counts only)",
              res_a["inputs"]["translated_input"] == bind and tc["summary"]["severity"] == {"none": 1, "minor": 1, "major": 1}
              and tc["check_sha256"] == h_file(sc.TRANSLATION_CHECK_FILE) and tc["commitment"] == COMMITTED(None)
              and [r["run_id"] for r in tc["translated_runs_before_seal"]] == ["arm"])
        ev.write_tables(res_a, rx.RUNS / "tables" / "arm", rx.RUNS / "arm_class_manifest.json")
        cap = "".join(p.read_text(encoding="utf-8") for p in sorted((rx.RUNS / "tables" / "arm").glob("table1_primary*")))
        check("L4h the arm's tables carry its own stamp, never 'reportable'", "translated inputs" in cap and "not the registered result" in cap,
              cap[:300])
        check("L4i regression: a run without the override is scored as before (registered primary, both languages, no binding)",
              res_r["analysis_status"] == "registered primary" and res_r["populations"] == {"pooled": 4, "de": 3, "en": 1}
              and "translated_input" not in res_r["inputs"] and "translation_check" not in res_r["inputs"]
              and abs(res_r["point"]["de"]["element"]["micro_f1_weighted"] - 1.0) < 1e-12
              and abs(res_r["point"]["pooled"]["element"]["micro_f1_weighted"] - 1.0) < 1e-12, str(res_r["populations"]))
        # status binding: the status exists only with the bound override, and the override only with the status
        rec_a = json.loads((rx.RUNS / "arm.json").read_text())

        def status_refused(name, mutate, needle):
            r_ = json.loads(json.dumps(rec_a)); mutate(r_)
            try:
                ev.analysis_status_of(r_, manifest_kind="A2_sample_manifest"); check(name, False, "no error")
            except ev.EvalError as exc:
                check(name, needle in str(exc), str(exc))
        status_refused("L4j a run with the override recorded as registered primary is refused",
                       lambda r: r.update({"analysis_status": "registered primary"}), "must be recorded as")
        status_refused("L4k the status without the override block is refused",
                       lambda r: (r.pop("translated_input"), r["execution_contract"].pop("translated_input")), "missing")
        status_refused("L4l a record block that is not the contract's is refused",
                       lambda r: r["translated_input"].update({"file_sha256": "0" * 64}), "not the one its execution contract binds")
        try:
            ev.analysis_status_of(rec_a, manifest_kind=sc.A3_KIND); check("L4n the translated status on the A3 census is refused", False, "no error")
        except ev.EvalError as exc:
            check("L4n the translated status on the A3 census is refused", "A2 sample only" in str(exc), str(exc))
        status_refused("L4o a translated run with k = 1 is refused", lambda r: r.update({"repeats": 1}), "k = 3")
        status_refused("L4p a development record carrying the override is refused",
                       lambda r: r.update({"dev": True, "analysis_status": "development"}), "never a development run")
        # a translation file that changed after the run (self-consistent, other digest) is refused
        keep_t = sc.TRANSLATIONS_FILE.read_bytes()
        write_translations(sc.TRANSLATIONS_FILE, {**ENGLISH, "D1_p02": ENGLISH["D1_p02"] + " Indeed."})
        try:
            ev.load_inputs(eval_args(mp, "arm")); check("L4q a translation file changed after the run is refused", False, "no error")
        except ev.EvalError as exc:
            check("L4q a translation file changed after the run is refused", "translated input" in str(exc) or
                  "not the one the run's execution contract binds" in str(exc), str(exc))
        sc.TRANSLATIONS_FILE.write_bytes(keep_t)
        sc.TRANSLATION_PROVENANCE_COMMITMENT = NOT_COMMITTED
        try:
            ev.load_inputs(eval_args(mp, "arm")); check("L4r the evaluator re-checks the committed translation provenance", False, "no error")
        except ev.EvalError as exc:
            check("L4r the evaluator re-checks the committed translation provenance", "pending changes" in str(exc), str(exc))
        sc.TRANSLATION_PROVENANCE_COMMITMENT = COMMITTED

        # ================= L5 the comparison ========================================================== #
        _r, code, out = silent(la.compare, "arm")
        res = json.loads((la.OUT / "language_arm.json").read_text()) if (la.OUT / "language_arm.json").exists() else {}
        p_ = res.get("primary") or {}
        check("L5a the comparison writes language_arm.json and .csv beside, never into paper/",
              code in (0, None) and (la.OUT / "language_arm.csv").exists() and "paper" not in str(la.OUT), f"{code} {out[-300:]}")
        check("L5b the comparison's scoring path reproduces the reference run's own German figure (baseline gate)",
              res.get("baseline_reproduction", {}).get("equal") is True and abs(res["baseline_reproduction"]["recomputed"] - 1.0) < 1e-12)
        check("L5c primary: translated 2/3 minus original 1.0, paired over the German articles, interval below zero -> below",
              abs(p_.get("translated", 0) - 2 / 3) < 1e-12 and abs(p_.get("original", 0) - 1.0) < 1e-12
              and abs(p_.get("difference", 0) + 1 / 3) < 1e-12 and p_.get("articles") == 2 and p_.get("B") == 2000
              and p_.get("seed") == 20260905 and p_.get("hi", 1) < 0 and p_.get("outcome") == "below", json.dumps(p_))
        sec = res.get("secondary") or {}
        chg = sec.get("change_translated_vs_original_execution1") or {}
        check("L5d secondary: change rate on matched units (1 of 3 relations, 1 of 3 technology sets) against a native repeat rate of 0",
              chg.get("matched_units") == 3 and chg.get("relation_changed") == 1 and chg.get("technology_set_changed") == 1
              and all(v["relation_changed"] == 0 for v in sec["native_repeat_original"].values()), json.dumps(chg))
        check("L5e secondary: technology F1, precision and recall with paired differences, failures, execution ranges, per-element F1",
              abs(sec["technology_f1"]["translated"] - 4 / 6) < 1e-12 and abs(sec["technology_f1"]["original"] - 0.8) < 1e-12
              and abs(sec["element_precision"]["translated"] - 2 / 3) < 1e-12 and abs(sec["element_recall"]["translated"] - 2 / 3) < 1e-12
              and sec["execution1_failures"] == {"translated": 0, "original": 0, "translated_outcomes": {"technologies_present": 3},
                                                 "original_outcomes": {"technologies_present": 2, "off_topic": 1}}
              and sec["executions_range"]["translated"]["range"] == 0 and set(sec["per_element_f1"]) == set(ev.ELEMENTS)
              and sec["per_element_f1"]["F2c"]["reported"] is False, json.dumps(sec.get("execution1_failures")))
        sens = res.get("sensitivity_without_major") or {}
        check("L5f predeclared sensitivity without the major-flagged paragraph: 2/3 vs 1.0 on two paragraphs",
              sens.get("paragraphs") == 2 and sens.get("excluded_paragraphs") == 1 and abs(sens.get("translated", 0) - 2 / 3) < 1e-12
              and abs(sens.get("original", 0) - 1.0) < 1e-12, json.dumps(sens)[:300])
        tcx = res.get("translation_check", {})
        check("L5g the check counts, the seal's commitment and the translation provenance are reported; the outputs carry no text",
              tcx.get("summary", {}).get("severity") == {"none": 1, "minor": 1, "major": 1} and tcx.get("commitment") == COMMITTED(None)
              and res.get("translation", {}).get("record_sha256") == h_file(la.record_path())
              and not leaks((la.OUT / "language_arm.json").read_text() + (la.OUT / "language_arm.csv").read_text()))
        _r, code, out = silent(la.compare, "arm", check=True)
        check("L5h --check confirms the outputs are current", code in (0, None) and "2 file(s) match" in out, out)
        (la.OUT / "language_arm.csv").write_text("edited\n")
        _r, code, _o = silent(la.compare, "arm", check=True)
        check("L5i --check reports an edited output as stale", code not in (0, None) and "STALE" in str(code), str(code))
        sc.CHECK_SEAL_COMMITMENT = NOT_COMMITTED
        _r, code, _o = silent(la.compare, "arm")
        check("L5j the comparison refuses while the check seal is not committed", code not in (0, None) and "not committed" in str(code), str(code))
        sc.CHECK_SEAL_COMMITMENT = COMMITTED
        stxt = sc.TRANSLATION_CHECK_SEAL.read_text(); sc.TRANSLATION_CHECK_SEAL.unlink()
        _r, code, _o = silent(la.compare, "arm")
        check("L5k the comparison refuses without the check seal", code not in (0, None) and "no translation check seal" in str(code), str(code))
        sc.TRANSLATION_CHECK_SEAL.write_text(stxt)
        keep_c = sc.TRANSLATION_CHECK_FILE.read_bytes()
        fill_check(sc.TRANSLATION_CHECK_FILE, {"D1_p02": ("minor", {"other": "1"})})
        _r, code, _o = silent(la.compare, "arm")
        check("L5l the comparison refuses a check file edited after sealing", code not in (0, None) and "changed after it was sealed" in str(code), str(code))
        sc.TRANSLATION_CHECK_FILE.write_bytes(keep_c)
        _r, code, _o = silent(la.compare, "reg")
        check("L5m the comparison refuses a run that is not the translated arm as the arm", code not in (0, None) and "expected" in str(code), str(code))

        # ================= L5n world w1 again: a failed translation through evaluator and comparison ========= #
        world(w1)
        _reg1, _f, code_1 = go(tr.gate_plan() + [tr.resp(tr.WIND)] * 3 + [tr.resp(tr.SOLAR_TWO)] * 3 + [tr.resp(tr.OFF)] * 3
                               + [tr.resp(SOLAR_ONE)] * 3, manifest=str(sc.SAMPLE_MANIFEST), repeats=3, run_id="reg_w1")
        evaluate_to_tables(sc.SAMPLE_MANIFEST, "reg_w1")
        _r, code, _o = run_main(["validate-check", "--seal"])
        res_f = evaluate_to_tables(sc.SAMPLE_MANIFEST, "arm_fail")
        # hand count: D1_p01 TP F8a, FN F7b; D1_p02 TP F2c; D2_p01 no input (off topic in the reference): F1 = 4 / (4 + 1)
        check("L5n a failed translation is scored as omissions only: German element F1 2*2/(2*2+1) = 0.8, all three paragraphs",
              code_1 == 0 and res_f["populations"] == {"pooled": 3, "de": 3}
              and abs(res_f["point"]["de"]["element"]["micro_f1_weighted"] - 0.8) < 1e-12, repr(res_f["point"]["de"]["element"]))
        la.REFERENCE_RUN = "reg_w1"
        _r, code, out = silent(la.compare, "arm_fail")
        res = json.loads((la.OUT / "language_arm.json").read_text())
        fl = res["secondary"]["execution1_failures"]
        check("L5o the comparison counts the failed translation among first-execution failures and outcomes; the check counts it apart",
              code in (0, None) and fl["translated"] == 1 and fl["translated_outcomes"] == {"technologies_present": 2, "no_translated_input": 1}
              and res["translation"]["failed"] == 1 and res["translation_check"]["summary"]["not_translated"] == 1
              and res["translation_check"]["summary"]["checked"] == 2, json.dumps(fl))
        check("L5p the sensitivity drops the major-flagged paragraph only (the failed one stays, as omissions)",
              res["sensitivity_without_major"]["paragraphs"] == 2 and res["sensitivity_without_major"]["excluded_paragraphs"] == 1)

        # ================= L2x world w4: a translation with CRLF, ';', quotes and a leading '=' ================ #
        w4 = tmp / "w4"; world(w4)
        tricky = '=SUM(1) He said: "yes"; then\r\nsecond line\nthird; "end"'
        fake = tr.Fake([tresp(tricky), tresp(ENGLISH["D1_p02"]), tresp(ENGLISH["D2_p01"])])
        _r, code, _o = silent(la.translate, client_factory=lambda: fake, baseline=fake_base)
        rows = {r["paragraph_id"]: r for r in read_csv(sc.TRANSLATIONS_FILE)}
        check("L2x1 special characters and CRLF round-trip through the translation file byte for byte",
              code in (0, None) and rows["D1_p01"]["translation"] == tricky and rows["D1_p01"]["translation_sha256"] == h(tricky))
        _r, code, _o = run_main(["qa-sheet"])
        fill_check(sc.TRANSLATION_CHECK_FILE, {p: ("none", {}) for p in ("D1_p01", "D1_p02", "D2_p01")})
        _r, code, out = run_main(["validate-check", "--seal"])
        check("L2x2 an untouched sheet with such a translation validates and seals", code in (0, None) and "written" in out, str(code))
        fill_check(sc.TRANSLATION_CHECK_FILE, {"D1_p01": lambda r: r.update({"translation": r["translation"].replace("\r\n", "\n")})})
        _r, code, _o = run_main(["validate-check"])
        check("L2x3 a spreadsheet re-save that turns CRLF into LF is not a changed translation (both sides normalised)",
              code in (0, None), str(code))

        # ================= L7 the commitment tests on a real git repository =============================== #
        g = tmp / "git"; g.mkdir()
        old_root = sc.ROOT
        try:
            git_ok("init", "-q", cwd=g)
            sc.ROOT = g
            sp = g / "seal.json"; sp.write_text("v1\n")
            git_ok("add", "seal.json", cwd=g); git_ok("commit", "-q", "-m", "seal", cwd=g)
            c1 = sc.file_commitment(sp, "the seal", first=True)
            check("L7a a committed seal: its first commit and committer date", len(c1["commit"]) == 40 and c1["committed_at"][:4].isdigit())
            sp.write_text("v2\n")
            contract_refused("L7b a seal edited after its commit is refused", lambda: sc.file_commitment(sp, "the seal", first=True), "differs")
            git_ok("update-index", "--assume-unchanged", "seal.json", cwd=g)
            contract_refused("L7c an edit hidden by assume-unchanged is still refused (bytes against the committed blob)",
                             lambda: sc.file_commitment(sp, "the seal", first=True), "differs")
            git_ok("update-index", "--no-assume-unchanged", "seal.json", cwd=g)
            git_ok("commit", "-q", "-am", "reseal", cwd=g)
            contract_refused("L7d a seal replaced and recommitted is refused: the sealed value is the first commit's",
                             lambda: sc.file_commitment(sp, "the seal", first=True), "first commit")
            check("L7e a file that grew before its commit (journal, record) is checked against HEAD",
                  sc.file_commitment(sp, "the record", first=False)["commit"] != c1["commit"])
            check("L7f a tracked seal path counts as committed; an untracked new path does not",
                  sc.ever_committed(sp) and not sc.ever_committed(g / "new.json"))
            git_ok("rm", "-q", "seal.json", cwd=g); git_ok("commit", "-q", "-m", "rm", cwd=g)
            check("L7g a seal path removed from the tree still counts as committed (git rm and re-seal is refused)",
                  sc.ever_committed(sp))
            contract_refused("L7h an uncommitted file is refused", lambda: sc.file_commitment(g / "new.json", "the record", first=False),
                             "not committed")
        finally:
            sc.ROOT = old_root

        # ================= L8 Figure 8 and the appendix table (figures.py language) ====================== #
        import figures as fg
        def pv(d, lo, hi):
            return {"diff": d, "lo": lo, "hi": hi, "B": 2000, "seed": 7, "articles": 3, "defined_draws": 2000}
        def chg(paras, tch, units, el, rel, w):
            return {"paragraphs_both_successful": paras, "technology_set_changed": tch, "matched_units": units,
                    "elements_changed": el, "relation_changed": rel, "element_change_rate": el / units,
                    "element_change_rate_weighted": w, "relation_change_rate": rel / units}
        rec8 = {"translator": {"model_id": "tm", "provider": "Prov", "region": "rg", "invocation": "on-demand"},
                "request": {"system_prompt_sha256": "ab" * 32, "inference_config": {"temperature": 0, "maxTokens": 4096},
                            "additional_model_request_fields": {"reasoning_effort": "medium"}, "post_processing": "joined"},
                "attempts": {"calls_finished": 4, "codes": {"ok": 3, "infrastructure": 1}}, "usage": {"input_tokens": 10, "output_tokens": 20}}
        rec8_bytes = (json.dumps(rec8) + "\n").encode()
        la8 = {"status": "exploratory reused A2 — translated inputs", "paragraphs": 3, "articles": 3,
               "primary": {"translated": 0.5, "original": 0.6, "difference": 0.5 - 0.6, "lo": -0.2, "hi": -0.05, "B": 2000,
                           "seed": 7, "articles": 3, "outcome": "below"},
               "secondary": {"technology_f1": {"translated": 0.9, "original": 0.8, "paired": pv(0.9 - 0.8, -0.05, 0.2)},
                             "element_precision": {"translated": 0.7, "original": 0.7, "paired": pv(0.0, -0.1, 0.1)},
                             "element_recall": {"translated": 0.4, "original": 0.5, "paired": pv(0.4 - 0.5, -0.3, 0.02)},
                             "execution1_failures": {"translated": 1, "original": 0,
                                                     "translated_outcomes": {"technologies_present": 2, "parse_failed": 1},
                                                     "original_outcomes": {"technologies_present": 3}},
                             "change_translated_vs_original_execution1": chg(2, 1, 4, 3, 3, 0.70),
                             "native_repeat_original": {"1_vs_2": chg(2, 0, 5, 1, 2, 0.15), "1_vs_3": chg(2, 0, 6, 0, 1, 0.0)},
                             "repeat_translated": {"1_vs_2": chg(3, 0, 4, 1, 1, 0.2)},
                             "executions_element_f1": {"translated": {"1": 0.5, "2": 0.52}, "original": {"1": 0.6, "2": 0.6, "3": 0.61}},
                             "executions_range": {"translated": {"range": 0.02}, "original": {"range": 0.01}}},
               "sensitivity_without_major": None,
               "translation": {"rows": 3, "translated": 3, "failed": 0, "translator_model_id": "tm", "prompt_sha256": "ab" * 32,
                               "record_sha256": hashlib.sha256(rec8_bytes).hexdigest()},
               "translation_check": {"summary": {"rows": 3, "checked": 3, "not_translated": 0,
                                                 "severity": {"none": 2, "minor": 1, "major": 0},
                                                 "categories": {"negation": 1, "other": 0}},
                                     "sealed_at": "2026-11-05T13:25:00+00:00", "commitment": {"commit": "abcdef1234"},
                                     "translated_runs_before_seal": [], "checker_note": "the checker is the coder of the reference"}}
        f8 = fg.fig_language_arm(la8)
        # 2026-11-11 (fourth review, M22): updated to the palette of 2026-11-06 (one colour, vermillion, for the changed
        # configuration; the sign is read from the position against the zero line) and to the section number 6.8
        check("L8a Figure 8: one row per paired statistic, every mark in the colour of a changed configuration, filled where the "
              "interval excludes zero and open otherwise, the difference written beside each interval, the declared reading in "
              "the caption; the change counts are stated once in the text (Section 6.8) and the appendix table, not repeated "
              "in the caption",
              "Element F1 (primary)" in f8 and "Element recall" in f8 and "draw=figChanged, mark options={draw=figChanged, fill=figChanged" in f8
              and "draw=figChanged, mark options={draw=figChanged, fill=white" in f8 and "figBelow" not in f8 and "figAbove" not in f8
              and "$-0.100$" in f8 and "$+0.100$" in f8 and "under the declared rule" in f8
              and "3 of 4 technology rows" not in f8 and "1 of 5 and 0 of 6 rows" not in f8
              and "\\label{fig:language_arm}" in f8, f8[-700:])
        t8 = fg.tab_language_arm(la8, rec8)
        check("L8b the appendix table carries the configuration, the sealed check summary, the paired rows with the declared "
              "reading, change counts with denominators, executions (a missing one printed as --) and failures",
              "tm on Prov, rg" in t8 and "reasoning effort medium" in t8 and "1 infrastructure rejection" in t8 and "minor 1" in t8
              and "negation 1" in t8 and "the coder of the reference" in t8 and "& below \\\\" in t8 and "3 / 4" in t8
              and "0.750 / 0.700" in t8 and "Original, exec.\\ 1 vs 3" in t8 and "& -- &" in t8
              and "1 of 3 without a valid record (parse failed 1); valid: technologies present 2" in t8
              and "\\label{tab:language_arm}" in t8, t8[:400])
        bad8 = json.loads(json.dumps(la8)); bad8["secondary"]["element_recall"]["paired"]["diff"] = 0.05
        try:
            fg.lang_pairs(bad8); r1 = False
        except SystemExit:
            r1 = True
        src8 = tmp / "la8.json"; src8.write_text(json.dumps(la8)); rp8 = tmp / "rec8.json"; rp8.write_bytes(rec8_bytes)
        fg.language_outputs(src8, rp8, tmp / "l8out", tmp / "l8out")
        ok8 = (tmp / "l8out" / "fig_language_arm.tex").exists() and (tmp / "l8out" / "tab_language_arm.tex").exists()
        rp8.write_bytes(rec8_bytes.replace(b"Prov", b"Prow"))
        try:
            fg.language_outputs(src8, rp8, tmp / "l8out", tmp / "l8out", check=True); r2 = False
        except SystemExit as exc:
            r2 = "not the one" in str(exc)
        check("L8c a paired difference that is not translated minus original, and a translation record other than the bound "
              "one, are refused; the bound pair writes both files", r1 and r2 and ok8)

        # ================= L6 the real registered run (subprocess, nothing written) ====================== #
        r = subprocess.run([sys.executable, str(HERE / "language_arm.py"), "baseline"], capture_output=True, text=True, timeout=600)
        check("L6 the comparison's scoring path reproduces the registered German element micro-F1 0.621 from the registered predictions",
              r.returncode == 0 and "0.6213019678687827 = metrics 0.6213019678687827" in r.stdout and "reproduced" in r.stdout,
              (r.stdout + r.stderr)[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
