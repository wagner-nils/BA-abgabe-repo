#!/usr/bin/env python3
"""§4e(a) -- the language arm: translated inputs on the German A2 paragraphs (pre-analysis specification §4e(a),
declared 2026-10-19, amended 2026-11-05 before any study paragraph was translated; the author's decision).

Question (amendment item 1): does a fixed German-to-English pre-processing step change agreement with the unchanged
German reference under the unchanged extractor? A comparison of two complete configurations (original German input
versus machine-translated English input), never a language effect: the translation also changes wording, explicitness
and length, the reference was coded on the German text, and the design isolates nothing.

    .venv/bin/python3 tools/pipeline/language_arm.py baseline                 # registered German element F1 (0.621)
    .venv/bin/python3 tools/pipeline/language_arm.py translate [--resume]     # 60 calls, gpt-oss-120b, eu-central-1
    (commit tools/runs/language_arm/translation_journal.jsonl and translation_record.json)
    .venv/bin/python3 tools/pipeline/language_arm.py qa-sheet                 # the author's private check sheet
    .venv/bin/python3 tools/pipeline/language_arm.py validate-check [--seal]  # counts and digest; --seal writes the seal
    (commit tools/runs/language_arm/translation_check_seal.json)
    .venv/bin/python3 tools/pipeline/language_arm.py compare --run-id <id> [--check]

The extraction itself is the runner's declared input override (run_extract.py --translated-input), evaluated by the
registered evaluator (evaluate.py) under the status `exploratory reused A2 — translated inputs`. The frozen request,
the verification of the translation file and the check seal live in sample_contract (one contract for all four).

Privacy: paragraph and translation text live only in the two private files under tools/annotation/sheets/ (gitignored;
every command refuses a path inside the repository that git does not ignore). Nothing here prints text. The public
translation journal and record (tools/runs/language_arm/) carry identifiers, digests, codes, stop reasons, token counts
and timestamps only; reasoning blocks of the translator are counted and discarded, never stored.

Order (amendment items 6 and 8): `translate` reproduces the registered German figure through the arm's scoring path
before its first call and records it in the journal and the record (`baseline` shows it without translating); the
extraction may run before the author's check is finished (finishing the check first is recommended, so the checker has
not seen the arm's predictions -- the seal records which translated-input runs existed when it was written), but no
agreement figure of the arm -- the evaluator's metrics of the translated run or this comparison -- is computed before
the check file's digest is sealed in the tracked `tools/runs/language_arm/translation_check_seal.json` and that seal
is committed unchanged (enforced by sample_contract).

Failure handling (amendment item 4): a call that raises, stops other than end_turn, or returns empty text is repeated
identically up to two times (an exception waits 2 s, then 4 s, before its repeat). A rejection before the translator
is reached -- missing or expired credentials, access denied, a validation error on the frozen request, an unknown model,
or throttling that persists through six backoffs (2 s doubling to 60 s) -- is not an answer of the translator: it is
journalled, charges no attempt, writes no row and aborts the step (exit 2) for `--resume` once access is restored.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_extract as rx                                            # noqa: E402  (manifest, texts, journal, digests)
import sample_contract as sc                                        # noqa: E402

ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
ARM = RUNS / "language_arm"               # public: journal, translation record, check seal (no text)
OUT = RUNS / "tables" / "language_arm"    # the comparison; never paper/
LABELS = ROOT / "tools" / "annotation" / "labels"
GOLD1 = LABELS / "A2_pass1_v1.0.csv"
GOLD2 = LABELS / "A2_pass2_v1.0.csv"
THRESHOLD = 5                             # the sealed threshold of the registered sample
REGISTERED_RUN = "a2_sonnet_20261013"     # the registered run (sc.REGISTERED_MODEL_KEY, k = 3, sealed threshold)
REFERENCE_RUN = REGISTERED_RUN            # the comparison's reference: execution one on the same 60 German paragraphs
DECLARED_GERMAN_ELEMENT_F1 = "0.621"      # amendment item 8: the registered German element micro-F1 to reproduce
SUCCESS = ("technologies_present", "off_topic")

# --------------------------------------------------------------------------- #
# the frozen translation request (amendment item 3) -- defined once, in sample_contract
# --------------------------------------------------------------------------- #
TRANSLATION_PROMPT = sc.TRANSLATION_PROMPT
TRANSLATION_PROMPT_SHA256 = sc.TRANSLATION_PROMPT_SHA256
TRANSLATION_INFERENCE = sc.TRANSLATION_INFERENCE
TRANSLATION_EXTRA = sc.TRANSLATION_EXTRA
TRANSLATION_REPEATS = sc.TRANSLATION_REPEATS
translation_request = sc.translation_request
# failure handling: errors that are not the translator's answer abort without charging an attempt
ABORT_MARKERS = ("NoCredentials", "PartialCredentials", "CredentialRetrieval", "ExpiredToken", "UnrecognizedClient",
                 "InvalidSignature", "AccessDenied", "ValidationException", "ResourceNotFound")
BACKOFF_MARKERS = ("Throttl", "TooManyRequests", "ServiceQuotaExceeded", "ModelNotReady")
THROTTLE_LIMIT = rx.THROTTLE_RETRIES      # six backoffs, as the extraction runner
BACKOFF_BASE_S, BACKOFF_MAX_S = rx.BACKOFF_BASE_S, 60.0
UNCOUNTED = ("throttled", "infrastructure")   # journalled codes that charge no attempt
# PROVISIONAL (2026-11-05): the Bedrock list price of gpt-oss-120b as known to the implementer (USD 0.15 input / 0.60
# output per 1M tokens), not read off the eu-central-1 price page; an estimate for the record, not an invoice
TRANSLATOR_PRICE_PER_M = {"input": 0.15, "output": 0.60}
TRANSLATOR_PRICE_NOTE = ("PROVISIONAL: Bedrock list price of openai.gpt-oss-120b-1:0 as known on 2026-11-05 (0.15 / 0.60 "
                         "USD per 1M input / output tokens), not read off the eu-central-1 price page; estimate, not an invoice")


class InfrastructureAbort(Exception):
    """A rejection before the translator answered (access, credentials, validation, persistent throttling)."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def journal_path() -> Path:
    return Path(sc.TRANSLATION_JOURNAL)


def record_path() -> Path:
    return Path(sc.TRANSLATION_RECORD)


def ensure_private(path: Path) -> None:
    """Text files go only where git ignores them (sample_contract.ensure_ignored, which the runner, the evaluator and
    the comparison apply to the translation file too)."""
    try:
        sc.ensure_ignored(path)
    except sc.ContractError as exc:
        sys.exit(f"REFUSING: {exc}.")


def read_translation(response: dict) -> tuple[str, str | None, int]:
    """(code, translation or None, reasoning blocks discarded). The answer's text blocks are joined with a newline and
    the outer whitespace of the result is stripped (declared post-processing); reasoning blocks are counted and dropped
    here, so they never reach a file. A stop other than end_turn or an empty text is a failed attempt."""
    stop = response.get("stopReason")
    blocks = (((response.get("output") or {}).get("message") or {}).get("content")) or []
    texts = [b["text"] for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str)]
    n_reasoning = sum(1 for b in blocks if isinstance(b, dict) and "reasoningContent" in b)
    text = "\n".join(texts).strip()
    if stop != "end_turn":
        return f"stop_{stop}", None, n_reasoning
    if not text:
        return "empty_text", None, n_reasoning
    return "ok", text, n_reasoning


def error_kind(exc: BaseException) -> tuple[str, str, str]:
    """(kind, class, code) of a raised call: `infrastructure` (rejected before the translator: abort, no attempt),
    `throttled` (back off, no attempt), or `error` (an attempt, repeated identically after a backoff)."""
    name = type(exc).__name__
    resp = getattr(exc, "response", None)
    err = ((resp or {}).get("Error") or {}).get("Code", name) if isinstance(resp, dict) else name
    s = f"{name} {err}"
    kind = ("infrastructure" if any(m in s for m in ABORT_MARKERS)
            else "throttled" if any(m in s for m in BACKOFF_MARKERS) else "error")
    return kind, name, str(err)


def usage_of(response: dict) -> dict:
    u = response.get("usage") or {}
    ok = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0
    return {"input_tokens": u["inputTokens"] if ok(u.get("inputTokens")) else 0,
            "output_tokens": u["outputTokens"] if ok(u.get("outputTokens")) else 0,
            "usage_complete": ok(u.get("inputTokens")) and ok(u.get("outputTokens"))}


def cost_usd(inp: int, out: int) -> float:
    return round((inp * TRANSLATOR_PRICE_PER_M["input"] + out * TRANSLATOR_PRICE_PER_M["output"]) / 1_000_000, 6)


def german_paragraphs() -> tuple[dict, list[dict]]:
    """The registered A2 sample (the one sample contract, intact digest) and its German paragraphs, in manifest order."""
    mpath = Path(sc.SAMPLE_MANIFEST)
    mdoc = json.loads(mpath.read_text(encoding="utf-8"))
    try:
        sc.verify_sample(mdoc)
    except sc.ContractError as exc:
        sys.exit(f"REFUSING: {exc}")
    rx.manifest_digest(mdoc)                                   # refuses an edited manifest
    de = [q for q in mdoc["paragraphs"] if q.get("lang") == sc.TRANSLATED_LANG]
    if len(de) != int(sc.REGISTERED_TARGET.get(sc.TRANSLATED_LANG, -1)):
        sys.exit(f"REFUSING: the registered sample yields {len(de)} German paragraphs, not "
                 f"{sc.REGISTERED_TARGET.get(sc.TRANSLATED_LANG)}.")
    return mdoc, de


def source_texts(de: list[dict]) -> dict[str, str]:
    """The registered paragraph texts (registry-validated CSV, hash-checked against the manifest by the runner's loader)."""
    return {pid: r["paragraph_text"] for pid, r in rx.load_texts(de).items()}


def _read_rows(path: Path) -> list[dict]:
    cols, rows = sc._read_semicolon_csv(path)
    if cols != sc.TRANSLATION_COLUMNS:
        raise sc.ContractError(f"{path.name} exists with another header; it is not a translation file of this arm")
    return rows


def _append_row(path: Path, row: dict) -> None:
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sc.TRANSLATION_COLUMNS, delimiter=";")
        if new:
            w.writeheader()
        w.writerow(row)
        f.flush(); os.fsync(f.fileno())


def default_translator():
    import boto3
    from botocore.config import Config
    # SDK retries OFF: every resend is a journalled attempt (the runner's convention)
    return boto3.client("bedrock-runtime", region_name=sc.TRANSLATOR_REGION,
                        config=Config(retries={"total_max_attempts": 1, "mode": "standard"}))


def registered_baseline() -> dict:
    """Amendment item 8, the order: the arm's scoring path applied to the registered run's predictions on the German
    paragraphs reproduces the registered German element micro-F1 (0.621). Run by `translate` before its first call and
    recorded with the digests of what produced it."""
    ref = load_run(REGISTERED_RUN, "registered primary")
    b = reproduce_registered_german(ref)
    return {"run_id": ref["run_id"], "recomputed": b["recomputed"], "metrics_value": b["metrics"], "declared": b["declared"],
            "equal": b["equal"], "metrics_sha256": ref["metrics_sha256"], "run_record_sha256": ref["run_record_sha256"],
            "evaluate_sha256": rx.sha256_file(HERE / "evaluate.py"),
            "language_arm_sha256": rx.sha256_file(Path(__file__).resolve()),
            "sample_contract_sha256": rx.sha256_file(HERE / "sample_contract.py"), "at": utcnow()}


def journal_counts(prior: list[dict]) -> tuple[Counter, Counter]:
    """(attempts charged per paragraph, calls started per paragraph) from the journal: a call charges an attempt unless
    it finished as throttled or infrastructure; a call started without a finish (an interruption) is charged."""
    fin = {(r.get("paragraph_id"), r.get("call_no")): r.get("code") for r in prior if r.get("type") == "attempt_finished"}
    charged, calls = Counter(), Counter()
    for r in prior:
        if r.get("type") == "attempt_started":
            calls[r["paragraph_id"]] += 1
            if fin.get((r["paragraph_id"], r.get("call_no"))) not in UNCOUNTED:
                charged[r["paragraph_id"]] += 1
    return charged, calls


def verify_partial(out: Path, de: list[dict], texts: dict) -> dict:
    """The rows already written, fully checked before anything is appended: terminated last line, the declared header,
    no duplicate or foreign paragraph, every row by sample_contract.check_translation_row against the registered text,
    and every row against the journal (no paragraph journalled as finished without its row)."""
    if not out.read_bytes().endswith(b"\n"):
        raise sc.ContractError("the file does not end with a line terminator (a torn write)")
    rows = _read_rows(out)
    want = {q["paragraph_id"]: q for q in de}
    done = {r.get("paragraph_id"): r for r in rows}
    if len(done) != len(rows) or set(done) - set(want):
        raise sc.ContractError("a paragraph appears twice or one lies outside the German A2 sample")
    for pid, r in done.items():
        sc.check_translation_row(r, want[pid], texts[pid])
    return done


def reconcile(done: dict, prior: list[dict], jr) -> int:
    """A row fsynced before its journal finish (an interruption between the two writes): the journal is completed from
    the row -- the row is the evidence of the successful (or exhausted) attempt -- before anything else is written."""
    oks = {r.get("paragraph_id") for r in prior if r.get("type") == "attempt_finished" and r.get("code") == "ok"}
    fails = {r.get("paragraph_id") for r in prior if r.get("type") == "paragraph_failed"}
    n = 0
    for pid, r in done.items():
        if r["outcome"] == sc.T_TRANSLATED and pid not in oks:
            started = [x for x in prior if x.get("type") == "attempt_started" and x.get("paragraph_id") == pid]
            jr.write({"type": "attempt_finished", "paragraph_id": pid, "call_no": started[-1].get("call_no") if started else None,
                      "code": "ok", "translation_sha256": r["translation_sha256"], "reconciled_from_file": True, "at": utcnow()})
            n += 1
        elif r["outcome"] == sc.T_FAILED and pid not in fails:
            jr.write({"type": "paragraph_failed", "paragraph_id": pid, "attempts": int(r["attempts"]),
                      "reconciled_from_file": True, "at": utcnow()})
            n += 1
    return n


# --------------------------------------------------------------------------- #
# 1 -- translate once
# --------------------------------------------------------------------------- #
def translate(resume: bool = False, client_factory=None, out_path: Path | None = None, sleep=time.sleep,
              baseline=None) -> dict:
    """Translate the German A2 paragraphs once into the private file. A complete file is never touched (a missing or
    stale public record is rebuilt from the journal and the file, without a call); an incomplete one is continued only
    with --resume, after every written row has been verified, missing rows only, the attempts already charged counting
    against the allowance of one call plus two identical repeats. Before the first call, the registered German figure
    is reproduced (amendment item 8) and journalled. Write order per call: attempt_started (public journal), the call,
    the private row on success (flushed and fsynced), attempt_finished. Returns the public record."""
    out = Path(out_path or sc.TRANSLATIONS_FILE)
    ensure_private(out)
    _mdoc, de = german_paragraphs()
    ids = [q["paragraph_id"] for q in de]
    texts = source_texts(de)
    jp = journal_path()
    try:
        prior = sc.read_translation_journal(jp)[0] if jp.exists() else []
    except sc.ContractError as exc:
        sys.exit(f"REFUSING: {exc}")
    done = {}
    if out.exists():
        try:
            done = verify_partial(out, de, texts)
        except sc.ContractError as exc:
            sys.exit(f"REFUSING: {out.name} does not verify ({exc}); a translation file is never appended to, edited or "
                     f"rewritten once it fails.")
        if set(done) == set(ids):
            rebuilt = ensure_record(out, de, texts)
            sys.exit(f"REFUSING: {out.name} is complete ({len(done)} rows); a translation is written once and never "
                     f"re-generated, edited or selected (specification §4e(a) item 4)"
                     + ("; the public record was rebuilt from the journal and the file, without a call." if rebuilt else "."))
        if not resume:
            sys.exit(f"REFUSING: {out.name} exists with {len(done)} of {len(ids)} rows; pass --resume to translate the "
                     f"missing paragraphs only.")
    elif prior:
        try:
            sc.verify_translation_journal({}, jp, complete=False)
        except sc.ContractError as exc:
            sys.exit(f"REFUSING: the translation file is missing but {exc}; restore the file.")
        if any(journal_counts(prior)[0].values()) and not resume:
            sys.exit("REFUSING: the translation journal records charged attempts but no translation file exists yet; pass "
                     "--resume to continue (the charged attempts count against the allowance).")
    # amendment item 8: before any translation call, the arm's scoring path reproduces the registered German figure
    import compare_models as cm
    try:
        base = (baseline or registered_baseline)()
    except (cm.CompareError, sc.ContractError) as exc:
        sys.exit(f"REFUSING: the registered German element micro-F1 is not reproduced through the arm's scoring path "
                 f"({exc}); nothing is translated before it is (specification §4e(a) item 8).")
    if not isinstance(base, dict) or base.get("equal") is not True or not base.get("at"):
        sys.exit("REFUSING: the baseline reproduction did not succeed; nothing is translated (specification §4e(a) item 8).")
    if client_factory is None:
        client_factory = default_translator
    jr = rx.Journal(jp)
    jr.write({"type": "run_started", "at": utcnow(), "model_id": sc.TRANSLATOR_MODEL_ID, "region": sc.TRANSLATOR_REGION,
              "prompt_sha256": TRANSLATION_PROMPT_SHA256, "inference_config": TRANSLATION_INFERENCE,
              "additional_model_request_fields": TRANSLATION_EXTRA, "repeats_on_failure": TRANSLATION_REPEATS,
              "paragraphs": len(ids), "already_done": len(done), "resume": bool(resume), "baseline": base,
              "authority": sc.TRANSLATED_AUTHORITY})
    reconcile(done, prior, jr)
    charged, calls = journal_counts(prior)
    aborted = None
    try:
        try:
            client = client_factory()
        except Exception as exc:  # noqa: BLE001  -- class only; no client, no call
            raise InfrastructureAbort({"paragraph_id": None, "error_class": type(exc).__name__,
                                       "error_code": type(exc).__name__, "reason": "no translator client"}) from None
        for q in de:
            pid = q["paragraph_id"]
            if pid in done:
                continue
            source = texts[pid]
            env = translation_request(source)
            dig = rx.request_digest(env)
            assert dig == sc.translation_request_digest(source)
            last = {"stop_reason": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
            n, throttles, errors = charged[pid], 0, 0
            while n < sc.TRANSLATION_MAX_ATTEMPTS:
                calls[pid] += 1
                jr.write({"type": "attempt_started", "paragraph_id": pid, "call_no": calls[pid], "request_sha256": dig,
                          "at": utcnow()})
                t0 = time.monotonic()
                fin = {"type": "attempt_finished", "paragraph_id": pid, "call_no": calls[pid]}
                try:
                    resp = client.converse(**env)
                except Exception as exc:  # noqa: BLE001  -- class and code only; the message is never stored
                    kind, name, err = error_kind(exc)
                    fin.update({"code": kind, "stop_reason": None, "usage": None, "error_class": name, "error_code": err,
                                "charged": kind == "error", "latency_ms": int((time.monotonic() - t0) * 1000)})
                    wait = 0.0
                    if kind == "error":
                        n += 1; errors += 1
                        fin["attempt_no"] = n
                        last = {"stop_reason": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
                        if n < sc.TRANSLATION_MAX_ATTEMPTS:
                            wait = min(BACKOFF_BASE_S * 2 ** (errors - 1), BACKOFF_MAX_S)
                    elif kind == "throttled":
                        throttles += 1
                        if throttles <= THROTTLE_LIMIT:
                            wait = min(BACKOFF_BASE_S * 2 ** (throttles - 1), BACKOFF_MAX_S)
                    fin.update({"backoff_s": wait, "at": utcnow()})
                    jr.write(fin)
                    if kind == "infrastructure" or (kind == "throttled" and throttles > THROTTLE_LIMIT):
                        raise InfrastructureAbort({"paragraph_id": pid, "error_class": name, "error_code": err,
                                                   "reason": "persistent throttling" if kind == "throttled"
                                                   else "rejected before the translator answered"}) from None
                    if wait:
                        sleep(wait)
                    continue
                n += 1
                code, tr, n_reason = read_translation(resp)
                u = usage_of(resp)
                last = {"stop_reason": resp.get("stopReason"), "usage": u}
                fin.update({"code": code, "attempt_no": n, "charged": True, "stop_reason": resp.get("stopReason"),
                            "usage": u, "reasoning_blocks_discarded": n_reason,
                            "latency_ms": int((time.monotonic() - t0) * 1000)})
                if code == "ok":
                    th = sha_text(tr)
                    _append_row(out, {"paragraph_id": pid, "article_id": q["article_id"], "source_sha256": sha_text(source),
                                      "outcome": sc.T_TRANSLATED, "translation": tr, "translation_sha256": th,
                                      "model_id": sc.TRANSLATOR_MODEL_ID, "region": sc.TRANSLATOR_REGION,
                                      "request_sha256": dig, "prompt_sha256": TRANSLATION_PROMPT_SHA256,
                                      "stop_reason": last["stop_reason"], "input_tokens": u["input_tokens"],
                                      "output_tokens": u["output_tokens"], "attempts": n, "translated_at": utcnow()})
                    fin.update({"translation_sha256": th, "at": utcnow()})
                    jr.write(fin)
                    break
                fin["at"] = utcnow()
                jr.write(fin)
            else:
                # item 4: still without a translation after the allowance -- a failed paragraph, reported by count
                _append_row(out, {"paragraph_id": pid, "article_id": q["article_id"], "source_sha256": sha_text(source),
                                  "outcome": sc.T_FAILED, "translation": "", "translation_sha256": "",
                                  "model_id": sc.TRANSLATOR_MODEL_ID, "region": sc.TRANSLATOR_REGION, "request_sha256": dig,
                                  "prompt_sha256": TRANSLATION_PROMPT_SHA256, "stop_reason": last["stop_reason"] or "",
                                  "input_tokens": last["usage"]["input_tokens"], "output_tokens": last["usage"]["output_tokens"],
                                  "attempts": n, "translated_at": utcnow()})
                jr.write({"type": "paragraph_failed", "paragraph_id": pid, "attempts": n, "at": utcnow()})
    except InfrastructureAbort as exc:
        aborted = exc.args[0]
        jr.write({"type": "infrastructure_abort", **aborted, "at": utcnow()})
    finally:
        jr.write({"type": "run_finished", "at": utcnow()})
        jr.close()
    rec = write_translation_record(out, de, texts)
    if aborted is not None:
        print(f"ABORTED: {aborted['reason']} ({aborted['error_class']} / {aborted['error_code']}"
              + (f", at {aborted['paragraph_id']}" if aborted["paragraph_id"] else "") + "); no attempt was charged and no "
              "row written for it. Restore access, then run `language_arm.py translate --resume`.", file=sys.stderr)
        sys.exit(2)
    return rec


def write_translation_record(out: Path, de: list[dict], texts: dict) -> dict:
    """The public record of the translation step, rebuilt from the journal and the file -- a pure function of the two
    (and the frozen constants), so a rebuild yields the same bytes. Intact by its own digest; when the file is complete,
    it names the file's digest and per-paragraph digest after the full verification (rows against the registered text,
    rows against the journal)."""
    recs, torn = rx.Journal.read_verbatim(journal_path())
    fin = [r for r in recs if r.get("type") == "attempt_finished"]
    started = [r for r in recs if r.get("type") == "attempt_started"]
    runs = [r for r in recs if r.get("type") == "run_started"]
    inp = sum(int((r.get("usage") or {}).get("input_tokens", 0) or 0) for r in fin)
    outp = sum(int((r.get("usage") or {}).get("output_tokens", 0) or 0) for r in fin)
    try:
        rows = _read_rows(out) if out.exists() else []
    except sc.ContractError as exc:
        sys.exit(f"REFUSING: {exc}")
    complete = {r["paragraph_id"] for r in rows} == {q["paragraph_id"] for q in de}
    first_call = min((r["at"] for r in started), default=None)
    base = next((r.get("baseline") for r in runs), None)
    rec = {"kind": sc.TRANSLATION_RECORD_KIND, "authority": sc.TRANSLATED_AUTHORITY,
           "translator": {"model_id": sc.TRANSLATOR_MODEL_ID, "region": sc.TRANSLATOR_REGION,
                          "invocation": "on-demand in-region (no cross-region or global profile)", "provider": "Amazon Bedrock"},
           "request": {"system_prompt": TRANSLATION_PROMPT, "system_prompt_sha256": TRANSLATION_PROMPT_SHA256,
                       "user_turn": "the paragraph text alone", "inference_config": TRANSLATION_INFERENCE,
                       "additional_model_request_fields": TRANSLATION_EXTRA,
                       "post_processing": "the answer's text blocks joined with a newline, outer whitespace stripped; "
                                          "reasoning blocks counted and discarded, never stored",
                       "failure_handling": (f"up to {TRANSLATION_REPEATS} identical repeats after an exception (backoff 2 s, "
                                            f"then 4 s), a stop other than end_turn or empty text; a rejection before the "
                                            f"translator (credentials, access, validation, unknown model, throttling "
                                            f"persisting through {THROTTLE_LIMIT} backoffs) charges no attempt and aborts")},
           "file": {"path": sc._rel(out), "complete": complete, "rows": len(rows),
                    "translated": sum(r["outcome"] == sc.T_TRANSLATED for r in rows),
                    "failed": sum(r["outcome"] == sc.T_FAILED for r in rows)},
           "attempts": {"calls_started": len(started), "calls_finished": len(fin),
                        "codes": dict(sorted(Counter(r.get("code") for r in fin).items(), key=lambda kv: str(kv[0]))),
                        "charged": len(started) - sum(r.get("code") in UNCOUNTED for r in fin),
                        "unknown_completion": len(started) - len(fin), "torn_journal_lines": torn,
                        "infrastructure_aborts": sum(r.get("type") == "infrastructure_abort" for r in recs),
                        "backoff_seconds": sum(float(r.get("backoff_s") or 0) for r in fin),
                        "reconciled_from_file": sum(bool(r.get("reconciled_from_file")) for r in recs),
                        "reasoning_blocks_discarded": sum(int(r.get("reasoning_blocks_discarded") or 0) for r in fin)},
           "usage": {"input_tokens": inp, "output_tokens": outp},
           "cost": {"estimated_usd": cost_usd(inp, outp), "rates_per_million_usd": TRANSLATOR_PRICE_PER_M,
                    "note": TRANSLATOR_PRICE_NOTE},
           "baseline_before_first_call": base,
           "first_call_at": first_call,
           "last_call_at": max((r["at"] for r in fin if r.get("at")), default=None),
           "journal": {"path": sc._rel(journal_path()), "sha256": rx.sha256_file(journal_path()) if journal_path().exists() else None}}
    if complete:
        try:
            vrows, binding = sc.load_translation_file(out, de, texts, provenance=False)
            sc.verify_translation_journal(vrows, journal_path(), complete=True)
        except sc.ContractError as exc:
            sys.exit(f"REFUSING: the completed translation file does not verify ({exc}).")
        rec["file"].update({"sha256": binding["file_sha256"], "pairs_sha256": binding["pairs_sha256"]})
    rec["digest"] = sc.canonical_digest(rec)
    record_path().parent.mkdir(parents=True, exist_ok=True)
    record_path().write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return rec


def ensure_record(out: Path, de: list[dict], texts: dict) -> bool:
    """For a complete file: rebuild the public record when it is missing, not intact, or does not name this file
    (an interruption between the last row and the record). Returns whether it was written. No call."""
    try:
        old = json.loads(record_path().read_text(encoding="utf-8"))
        if (isinstance(old, dict) and sc.canonical_digest(old) == old.get("digest") and (old.get("file") or {}).get("complete")
                and (old.get("file") or {}).get("sha256") == rx.sha256_file(out)):
            return False
    except (OSError, ValueError):
        pass
    write_translation_record(out, de, texts)
    return True


# --------------------------------------------------------------------------- #
# 2 -- the author's check sheet, its validation and seal
# --------------------------------------------------------------------------- #
def complete_translations(path: Path | None = None) -> tuple[list[dict], dict, dict, dict]:
    """The complete translation file, verified in full (registered text, journal, committed record)."""
    path = Path(path or sc.TRANSLATIONS_FILE)
    _mdoc, de = german_paragraphs()
    texts = source_texts(de)
    try:
        rows, binding = sc.load_translation_file(path, de, texts)
    except sc.ContractError as exc:
        sys.exit(f"REFUSING: the translation file is not complete and intact ({exc}).")
    return de, rows, binding, texts


def qa_sheet(out_path: Path | None = None, translations_path: Path | None = None) -> int:
    """The private check sheet: paragraph id, source text, translation and the empty rubric. Written once."""
    out = Path(out_path or sc.TRANSLATION_CHECK_FILE)
    ensure_private(out)
    if out.exists():
        sys.exit(f"REFUSING: {out.name} exists; the check sheet is written once (the author may already be filling it).")
    de, rows, _binding, texts = complete_translations(translations_path)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sc.CHECK_COLUMNS, delimiter=";")
        w.writeheader()
        for q in de:
            pid = q["paragraph_id"]; r = rows[pid]
            w.writerow({"paragraph_id": pid, "source_text": texts[pid], "translation": r["translation"],
                        **{c: "" for c in sc.CHECK_CATEGORIES}, "severity": "", "note": ""})
    return len(de)


def validate_check(seal: bool = False, check_path: Path | None = None, translations_path: Path | None = None,
                   seal_path: Path | None = None) -> dict:
    """Validate the filled sheet (counts only, no text); with `seal`, write the tracked seal -- once: an identical seal
    is left as it is, a different one is written only while no seal at that path was ever committed."""
    check_path = Path(check_path or sc.TRANSLATION_CHECK_FILE)
    seal_path = Path(seal_path or sc.TRANSLATION_CHECK_SEAL)
    _de, rows, binding, _texts = complete_translations(translations_path)
    try:
        summary, majors = sc.validate_translation_check(check_path, rows)
    except sc.ContractError as exc:
        sys.exit(f"REFUSING: {exc}")
    out = {"check_sha256": rx.sha256_file(check_path), "summary": summary, "major_paragraphs": len(majors)}
    if seal:
        new = sc.build_check_seal(check_path, rows, binding["file_sha256"], utcnow(), sc.translated_runs(RUNS))
        volatile = ("sealed_at", "digest", "translated_runs_before_seal")
        if seal_path.exists():
            try:
                old = json.loads(seal_path.read_text(encoding="utf-8"))
            except ValueError:
                old = {}
            if {k: v for k, v in old.items() if k not in volatile} == {k: v for k, v in new.items() if k not in volatile}:
                out["seal"] = {"path": sc._rel(seal_path), "digest": old.get("digest"), "written": False}
                return out
        try:
            committed = sc.SEAL_EVER_COMMITTED(seal_path)
        except sc.ContractError as exc:
            sys.exit(f"REFUSING: the seal's history cannot be established ({exc}).")
        if committed:
            sys.exit("REFUSING: a check seal at this path is tracked or was committed before; the seal is written once and "
                     "a committed seal is never replaced.")
        seal_path.parent.mkdir(parents=True, exist_ok=True)
        seal_path.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out["seal"] = {"path": sc._rel(seal_path), "digest": new["digest"], "written": True,
                       "translated_runs_before_seal": len(new["translated_runs_before_seal"])}
    return out


# --------------------------------------------------------------------------- #
# 3 -- scoring on the German paragraphs, and the comparison
# --------------------------------------------------------------------------- #
def load_run(run_id: str, want_status: str) -> dict:
    """One run through the evaluator's verified input path (manifest contract, gates, journal reconciliation, threshold
    seal; for the translated run also the translation binding and the committed check seal), its metrics bound to that
    path (B, seed, threshold, run record, reference, status), and its evaluator structures."""
    import compare_models as cm
    import evaluate as ev
    ns = argparse.Namespace(run_record=str(RUNS / f"{run_id}.json"), manifest=str(sc.SAMPLE_MANIFEST), restrict_split=None,
                            gold_pass1=str(GOLD1), gold_pass2=str(GOLD2), dev=False, gold_manifest=None,
                            skip_provenance=False, threshold=THRESHOLD, reliability=None)
    try:
        inp = ev.load_inputs(ns)
        status = ev.analysis_status_of(inp["rec"], manifest_kind=(inp.get("manifest") or {}).get("kind"))
    except ev.EvalError as exc:
        raise cm.CompareError(f"{run_id}: {exc}") from None
    if status != want_status:
        raise cm.CompareError(f"{run_id}: status {status!r}, expected {want_status!r}")
    mp = RUNS / "tables" / run_id / "metrics.json"
    if not mp.exists():
        raise cm.CompareError(f"{run_id}: no metrics at {mp}; run evaluate.py first")
    m = json.loads(mp.read_text(encoding="utf-8"))
    prov = inp["provenance"]
    if (m.get("B") != cm.B or m.get("seed") != cm.SEED or m.get("dev") or m.get("threshold") != THRESHOLD
            or (m.get("inputs") or {}).get("run_record_sha256") != prov["run_record_sha256"]
            or (m.get("inputs") or {}).get("reference") != prov["reference"]
            or (m.get("analysis_status") or "registered primary") != status):
        raise cm.CompareError(f"{run_id}: the metrics file was not computed from this run record, reference, threshold and status")
    S = ev.build(inp)
    return {"run_id": run_id, "rec": inp["rec"], "m": m, "S": S, "status": status, "provenance": prov,
            "metrics_sha256": rx.sha256_file(mp), "run_record_sha256": prov["run_record_sha256"]}


def view(run: dict, pids: list[str], r: int = 1) -> dict:
    """The run restricted to `pids` at execution `r`: the evaluator's own counts (counts_execution), original design
    weights, the articles of those paragraphs only -- the shape compare_models.paired consumes."""
    import evaluate as ev
    S = {**run["S"], "pids": list(pids), "art": {p: run["S"]["art"][p] for p in pids}}
    return {**run, "S": S, "counts": ev.counts_execution(run["S"], list(pids), r)}


def element_f1(v: dict, table: str = "element") -> float | None:
    import evaluate as ev
    base = {a: 1 for a in sorted(set(v["S"]["art"].values()))}
    return ev.micro(v["counts"]["per_art"], table, base, True)


def german_pids(run: dict) -> list[str]:
    return [p for p in run["S"]["pids"] if run["S"]["lang"][p] == sc.TRANSLATED_LANG]


def reproduce_registered_german(ref: dict) -> dict:
    """Amendment item 8: the comparison's scoring path, applied to the registered run's predictions restricted to the
    German paragraphs, must reproduce the registered German element micro-F1 of the evaluator's metrics exactly."""
    import compare_models as cm
    got = element_f1(view(ref, german_pids(ref)))
    want = ref["m"]["point"][sc.TRANSLATED_LANG]["element"]["micro_f1_weighted"]
    equal = got is not None and want is not None and abs(got - want) <= 1e-12
    out = {"recomputed": got, "metrics": want, "equal": equal, "declared": None}
    if ref["run_id"] == REGISTERED_RUN and DECLARED_GERMAN_ELEMENT_F1:
        out["declared"] = DECLARED_GERMAN_ELEMENT_F1
        equal = equal and f"{got:.3f}" == DECLARED_GERMAN_ELEMENT_F1
        out["equal"] = equal
    if not equal:
        raise cm.CompareError(f"the German scoring path does not reproduce the registered German element micro-F1 "
                              f"({got!r} vs metrics {want!r}, declared {out['declared']})")
    return out


def change_rate(Sa: dict, ra: int, Sb: dict, rb: int, pids: list[str]) -> dict:
    """Between two executions on matched units: paragraphs on which both succeeded; per such paragraph whether the
    technology set differs; per (paragraph, technology) predicted by both whether the canonical relation (elements,
    evaluations, comparison target, subtype) and the element set differ. Unweighted counts and design-weighted rates."""
    paras = tech_changed = units = rel_changed = el_changed = 0
    w_units = w_rel = w_el = 0.0
    for pid in pids:
        if Sa["outcome"].get(ra, {}).get(pid) not in SUCCESS or Sb["outcome"].get(rb, {}).get(pid) not in SUCCESS:
            continue
        paras += 1
        ta = {t: x for (p, t), x in Sa["pred"].get(ra, {}).items() if p == pid}
        tb = {t: x for (p, t), x in Sb["pred"].get(rb, {}).items() if p == pid}
        tech_changed += set(ta) != set(tb)
        w = float(Sa["w"][pid])
        for t in sorted(set(ta) & set(tb)):
            ch, ce = ta[t] != tb[t], ta[t].elements != tb[t].elements
            units += 1; rel_changed += ch; el_changed += ce
            w_units += w; w_rel += w * ch; w_el += w * ce
    div = lambda a, b: None if not b else a / b
    return {"paragraphs_both_successful": paras, "technology_set_changed": tech_changed,
            "technology_set_change_rate": div(tech_changed, paras), "matched_units": units,
            "relation_changed": rel_changed, "elements_changed": el_changed,
            "relation_change_rate": div(rel_changed, units), "element_change_rate": div(el_changed, units),
            "relation_change_rate_weighted": div(w_rel, w_units), "element_change_rate_weighted": div(w_el, w_units)}


def outcome_of(pv: dict) -> str:
    """Amendment item 8: interval entirely above zero -> above; entirely below -> below; otherwise inconclusive."""
    d = pv["element"]
    if d["lo"] is None or d["hi"] is None:
        return "undefined"
    return "above" if d["lo"] > 0 else "below" if d["hi"] < 0 else "inconclusive"


STATS = ("element", "technology", "element_precision", "element_recall")


def analyse(arm: dict, ref: dict, majors: list[str]) -> dict:
    """Every declared quantity of the arm from the two loaded runs. Pure given its inputs (fixture-testable)."""
    import compare_models as cm
    import evaluate as ev
    pids = german_pids(arm)
    if sorted(pids) != sorted(arm["S"]["pids"]) or sorted(pids) != sorted(german_pids(ref)):
        raise cm.CompareError("the translated run and the registered run do not cover the same German paragraphs")
    if {p: arm["S"]["art"][p] for p in pids} != {p: ref["S"]["art"][p] for p in pids} \
            or any(arm["S"]["w"][p] != ref["S"]["w"][p] for p in pids):
        raise cm.CompareError("the two runs disagree on articles or design weights of the German paragraphs")
    baseline = reproduce_registered_german(ref)
    a1, r1 = view(arm, pids), view(ref, pids)
    arm_point = element_f1(a1)
    if arm_point is None or abs(arm_point - arm["m"]["point"][sc.TRANSLATED_LANG]["element"]["micro_f1_weighted"]) > 1e-12:
        raise cm.CompareError("the German scoring path does not reproduce the translated run's own evaluator figure")
    pv = cm.paired(a1, r1, stats=STATS)
    base = {a: 1 for a in sorted(set(a1["S"]["art"].values()))}
    pr_a, pr_r = cm._pr(a1["counts"]["per_art"], base), cm._pr(r1["counts"]["per_art"], base)
    k_arm, k_ref = int(arm["S"]["k"]), int(ref["S"]["k"])
    fail = lambda run, r: sum(1 for p in pids if run["S"]["outcome"].get(r, {}).get(p, "missing") not in SUCCESS)
    execs = {"translated": {str(r): element_f1(view(arm, pids, r)) for r in range(1, k_arm + 1)},
             "original": {str(r): element_f1(view(ref, pids, r)) for r in range(1, k_ref + 1)}}
    rng_ = lambda d: None if any(v is None for v in d.values()) else {"min": min(d.values()), "max": max(d.values()),
                                                                     "range": max(d.values()) - min(d.values())}
    pc_a = ev.per_class(a1["counts"]["per_art"], "element", base, True, ev.ELEMENTS)
    pc_r = ev.per_class(r1["counts"]["per_art"], "element", base, True, ev.ELEMENTS)
    supp = r1["counts"]["supp_rows"]["element"]
    per_element = {e: {"translated": pc_a[e], "original": pc_r[e], "support_rows": int(supp.get(e, 0)),
                       "reported": int(supp.get(e, 0)) >= THRESHOLD} for e in ev.ELEMENTS}
    native = {f"1_vs_{r}": change_rate(ref["S"], 1, ref["S"], r, pids) for r in range(2, k_ref + 1)}
    trans_rep = {f"1_vs_{r}": change_rate(arm["S"], 1, arm["S"], r, pids) for r in range(2, k_arm + 1)}
    keep = [p for p in pids if p not in set(majors)]
    sens = None
    if keep:
        pv_s = cm.paired(view(arm, keep), view(ref, keep), stats=("element",))
        sens = {"paragraphs": len(keep), "articles": len({arm["S"]["art"][p] for p in keep}),
                "excluded_paragraphs": len(pids) - len(keep), "translated": element_f1(view(arm, keep)),
                "original": element_f1(view(ref, keep)), "paired": pv_s, "outcome": outcome_of(pv_s),
                "note": "predeclared sensitivity; it never replaces the primary result"}
    return {
        "paragraphs": len(pids), "articles": len(set(a1["S"]["art"].values())),
        "baseline_reproduction": baseline,
        "primary": {"statistic": "element micro-F1, design-weighted, execution one, German A2 paragraphs, against the "
                                 "unchanged German reference",
                    "translated": arm_point, "original": element_f1(r1), "difference": pv["element"]["diff"],
                    "lo": pv["element"]["lo"], "hi": pv["element"]["hi"], "B": pv["element"]["B"],
                    "seed": pv["element"]["seed"], "articles": pv["element"]["articles"], "outcome": outcome_of(pv)},
        "secondary": {
            "technology_f1": {"translated": element_f1(a1, "technology"), "original": element_f1(r1, "technology"),
                              "paired": pv["technology"]},
            "element_precision": {"translated": pr_a[0], "original": pr_r[0], "paired": pv["element_precision"]},
            "element_recall": {"translated": pr_a[1], "original": pr_r[1], "paired": pv["element_recall"]},
            "execution1_failures": {"translated": fail(arm, 1), "original": fail(ref, 1),
                                    "translated_outcomes": dict(Counter(arm["S"]["outcome"].get(1, {}).get(p, "missing") for p in pids)),
                                    "original_outcomes": dict(Counter(ref["S"]["outcome"].get(1, {}).get(p, "missing") for p in pids))},
            "change_translated_vs_original_execution1": change_rate(arm["S"], 1, ref["S"], 1, pids),
            "native_repeat_original": native,
            "repeat_translated": trans_rep,
            "executions_element_f1": execs,
            "executions_range": {"translated": rng_(execs["translated"]), "original": rng_(execs["original"])},
            "per_element_f1": per_element,
        },
        "sensitivity_without_major": sens,
    }


def compare(run_id: str, check: bool = False) -> dict:
    """The declared comparison; refuses without the committed check seal (through the evaluator's input path and
    again here, where the major flags of the sensitivity are read from the sealed check file)."""
    import compare_models as cm
    import evaluate as ev
    try:
        ref = load_run(REFERENCE_RUN, "registered primary")
        arm = load_run(run_id, sc.TRANSLATED_STATUS)
        ti = (arm["rec"].get("execution_contract") or {}).get("translated_input") or {}
        tpath = Path(ti.get("file") or "")
        tpath = tpath if tpath.is_absolute() else ROOT / tpath
        try:
            de = [arm["S"]["by_pid"][p] for p in arm["S"]["pids"]]
            trows, binding = sc.load_translation_file(tpath, de, source_texts(de))
            if binding != ti:
                raise sc.ContractError("the translation file is not the one the run's execution contract binds")
            seal = sc.verify_check_seal(binding["file_sha256"], trows)
        except sc.ContractError as exc:
            raise cm.CompareError(f"{run_id}: {exc}") from None
        res = analyse(arm, ref, seal["major_paragraphs"])
    except cm.CompareError as exc:
        sys.exit(f"REFUSING: {exc}")
    out = {"declared": sc.TRANSLATED_AUTHORITY, "status": sc.TRANSLATED_STATUS,
           "question": ("does a fixed German-to-English pre-processing step change agreement with the unchanged German "
                        "reference under the unchanged extractor? A comparison of two complete configurations, never a "
                        "language effect; descriptive and exploratory, no causal or equivalence claim"),
           "rule": ("element micro-F1 (design-weighted, execution one) translated minus original on the same German "
                    "paragraphs, paired over common resamples of their articles: interval entirely above zero -> above, "
                    "entirely below -> below, otherwise inconclusive"),
           "B": cm.B, "seed": cm.SEED, "threshold": THRESHOLD,
           "runs": {"translated": {"run_id": arm["run_id"], "run_record_sha256": arm["run_record_sha256"],
                                   "metrics_sha256": arm["metrics_sha256"]},
                    "original": {"run_id": ref["run_id"], "run_record_sha256": ref["run_record_sha256"],
                                 "metrics_sha256": ref["metrics_sha256"], "execution": 1}},
           **res,
           "translation": {k: binding[k] for k in ("file_sha256", "pairs_sha256", "rows", "translated", "failed",
                                                    "translator_model_id", "translator_region", "prompt_sha256",
                                                    "journal_sha256", "record_sha256")},
           "translation_check": {"seal_digest": seal["digest"], "check_sha256": seal["check_sha256"],
                                 "summary": seal["summary"], "sealed_at": seal["sealed_at"],
                                 "commitment": seal["commitment"],
                                 "translated_runs_before_seal": seal.get("translated_runs_before_seal"),
                                 "checker_note": "the checker is the coder of the reference"},
           "inputs": {"language_arm_sha256": rx.sha256_file(Path(__file__).resolve()),
                      "evaluate_sha256": rx.sha256_file(HERE / "evaluate.py"),
                      "compare_models_sha256": rx.sha256_file(HERE / "compare_models.py")}}
    f = lambda v: "" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))
    lines = ["quantity;translated;original;difference;lo;hi;outcome_or_note"]
    p = out["primary"]
    lines.append(";".join(["element_f1_execution1 (primary)", f(p["translated"]), f(p["original"]), f(p["difference"]),
                           f(p["lo"]), f(p["hi"]), p["outcome"]]))
    for name in ("technology_f1", "element_precision", "element_recall"):
        q = out["secondary"][name]
        lines.append(";".join([name, f(q["translated"]), f(q["original"]), f(q["paired"]["diff"]), f(q["paired"]["lo"]),
                               f(q["paired"]["hi"]), "descriptive"]))
    fl = out["secondary"]["execution1_failures"]
    lines.append(";".join(["execution1_failures", f(fl["translated"]), f(fl["original"]), "", "", "", "count"]))
    ch = out["secondary"]["change_translated_vs_original_execution1"]
    nat = out["secondary"]["native_repeat_original"]
    lines.append(";".join(["relation_change_rate_execution1 (translated vs original)", f(ch["relation_change_rate"]), "", "",
                           "", "", f"{ch['relation_changed']}/{ch['matched_units']} matched units"]))
    for key, v in nat.items():
        lines.append(";".join([f"relation_change_rate_native_repeat_{key}", "", f(v["relation_change_rate"]), "", "", "",
                               f"{v['relation_changed']}/{v['matched_units']} matched units"]))
    for key, v in out["secondary"]["repeat_translated"].items():
        lines.append(";".join([f"relation_change_rate_translated_repeat_{key}", f(v["relation_change_rate"]), "", "", "", "",
                               f"{v['relation_changed']}/{v['matched_units']} matched units"]))
    rg_t = out["secondary"]["executions_range"]["translated"] or {}
    rg_o = out["secondary"]["executions_range"]["original"] or {}
    lines.append(";".join(["element_f1_executions_range (max minus min)", f(rg_t.get("range")), f(rg_o.get("range")), "",
                           "", "", "over the executions of each run"]))
    for e, v in out["secondary"]["per_element_f1"].items():
        lines.append(";".join([f"element_f1_{e}", f(v["translated"]) if v["reported"] else "n<s",
                               f(v["original"]) if v["reported"] else "n<s", "", "", "",
                               f"support {v['support_rows']} rows (no test)"]))
    s_ = out["sensitivity_without_major"]
    if s_ is not None:
        sp = s_["paired"]["element"]
        lines.append(";".join(["element_f1_execution1_without_major (sensitivity)", f(s_["translated"]), f(s_["original"]),
                               f(sp["diff"]), f(sp["lo"]), f(sp["hi"]), s_["outcome"]]))
    summ = out["translation_check"]["summary"]
    for sv, n in summ["severity"].items():
        lines.append(";".join([f"check_severity_{sv}", str(n), "", "", "", "", "pairs"]))
    for c, n in summ["categories"].items():
        lines.append(";".join([f"check_category_{c}", str(n), "", "", "", "", "pairs flagged"]))
    files = {OUT / "language_arm.json": json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n",
             OUT / "language_arm.csv": "\n".join(lines) + "\n"}
    stale = []
    for path, text in files.items():
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"  wrote {sc._rel(path)}")
    if check:
        if stale:
            sys.exit("STALE: " + ", ".join(sc._rel(p_) for p_ in stale))
        print(f"  {len(files)} file(s) match")
    return out


def fnum(v, nd=3):
    return "--" if v is None else f"{v:.{nd}f}"


def main(client_factory=None, sleep=time.sleep, baseline=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("translate", help="translate the 60 German A2 paragraphs once (Bedrock eu-central-1)")
    t.add_argument("--resume", action="store_true", help="continue an incomplete translation file: missing rows only")
    sub.add_parser("qa-sheet", help="write the author's private check sheet (once)")
    v = sub.add_parser("validate-check", help="validate the filled check sheet; print its digest and counts")
    v.add_argument("--seal", action="store_true", help="also write the tracked check seal (commit it before any figure)")
    sub.add_parser("baseline", help="reproduce the registered German element micro-F1 through this scoring path")
    c = sub.add_parser("compare", help="the declared comparison (refuses without the committed check seal)")
    c.add_argument("--run-id", required=True, help="the translated-input run")
    c.add_argument("--check", action="store_true", help="verify that the outputs on disk are current; write nothing")
    a = ap.parse_args()
    if a.cmd == "translate":
        if client_factory is None:
            rx.load_dotenv()            # the Bedrock credentials, as the runner loads them; the region stays the declared one
        rec = translate(resume=a.resume, client_factory=client_factory, sleep=sleep, baseline=baseline)
        fl, u, at = rec["file"], rec["usage"], rec["attempts"]
        print(f"translation: {fl['translated']} translated, {fl['failed']} failed, {fl['rows']} of "
              f"{sc.REGISTERED_TARGET.get(sc.TRANSLATED_LANG)} rows ({'complete' if fl['complete'] else 'INCOMPLETE'}); "
              f"{at['calls_finished']} calls {at['codes']}, {at['charged']} attempts charged, unknown completion "
              f"{at['unknown_completion']}, backoff {at['backoff_seconds']:.0f} s; tokens in {u['input_tokens']} out "
              f"{u['output_tokens']}; est. cost ${rec['cost']['estimated_usd']} (provisional rates)")
        b = rec.get("baseline_before_first_call") or {}
        print(f"  registered German element micro-F1 reproduced before the first call: {b.get('recomputed')!r} "
              f"(declared {b.get('declared')})")
        print(f"  private file {fl['path']}" + (f" (sha256 {fl['sha256']})" if fl.get("sha256") else ""))
        print(f"  record {sc._rel(record_path())}; journal {rec['journal']['path']}: commit both before the extraction run")
    elif a.cmd == "qa-sheet":
        n = qa_sheet()
        print(f"wrote {n} rows to {sc._rel(sc.TRANSLATION_CHECK_FILE)} (private, gitignored): fill the rubric columns "
              f"(1 or x = flagged) and severity (none / minor / major) per pair, then run validate-check --seal")
    elif a.cmd == "validate-check":
        res = validate_check(seal=a.seal)
        s = res["summary"]
        print(f"check file sha256 {res['check_sha256']}: {s['rows']} rows, {s['checked']} pairs checked, "
              f"{s['not_translated']} without translation")
        print("  severity  " + ", ".join(f"{k} {n}" for k, n in s["severity"].items()))
        print("  categories " + ", ".join(f"{k} {n}" for k, n in s["categories"].items()))
        print(f"  noted for the author (counted, not refused): {s['minor_or_major_without_category']} minor/major pair(s) "
              f"without a flagged category, {s['none_with_category']} 'none' pair(s) with a flagged category")
        if "seal" in res:
            print(f"  seal {res['seal']['path']} (digest {res['seal']['digest'][:16]}, "
                  f"{'written' if res['seal']['written'] else 'already sealed, unchanged'}); commit it before any "
                  f"agreement figure of the arm is computed")
            if res["seal"].get("translated_runs_before_seal"):
                print(f"  note: {res['seal']['translated_runs_before_seal']} translated-input run(s) existed when the seal "
                      f"was written (recorded in the seal)")
    elif a.cmd == "baseline":
        import compare_models as cm
        try:
            b = registered_baseline()
        except cm.CompareError as exc:
            sys.exit(f"REFUSING: {exc}")
        print(f"registered German element micro-F1 (execution one, design-weighted) through the language-arm scoring "
              f"path: {b['recomputed']!r} = metrics {b['metrics_value']!r} (declared {b['declared']}): reproduced "
              f"(`translate` repeats and records this before its first call)")
    elif a.cmd == "compare":
        out = compare(a.run_id, check=a.check)
        p = out["primary"]
        print(f"  element F1 translated {fnum(p['translated'])} vs original {fnum(p['original'])}: difference "
              f"{fnum(p['difference'])} [{fnum(p['lo'])}, {fnum(p['hi'])}] -> {p['outcome']}")


if __name__ == "__main__":
    main()
