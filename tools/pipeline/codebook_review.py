#!/usr/bin/env python3
"""Model-assisted codebook review, a retrospective benchmark (pre-analysis specification §4h, declared 2026-10-30).

Question: given the codebook version (and, in arm B, the calibration paragraphs) the coder had at the time, would a
language model have flagged the gaps that the human calibration and coding found later -- and does that need the
diffusion model, or does Opus 4.6 do it? Exploratory and descriptive: nothing here touches the frozen codebook v1.0,
the registered instrument or any reported agreement figure.

Sessions, each replaying one human session: S1 = codebook v0.1 + session A0 (20 paragraphs), S2 = v0.2 + A0b (12),
S3 = v1.0 + A1 (35, the development session). Arms: A = the codebook text only (label-free), B = the codebook and the
session's paragraphs. Configurations: opus and sonnet (Bedrock EU, temperature 0), mercury3_low (Inception API,
temperature 0.5, low reasoning), mercury3_instant (temperature 0.5, no reasoning); token caps 8,192, for mercury3_low
32,768 (amended before the main runs). Three runs per cell. A run counts as done only under the current request
bytes, so a request changed by an amendment is run again and the earlier records stay in the journal as superseded. Ground truth: tools/runs/codebook_review/ground_truth.json, committed before the
first call (its sha256 is in §4h and in every run record).

    .venv/bin/python3 tools/pipeline/codebook_review.py run [--dry-run] [--only S1A_opus_1,...]
    .venv/bin/python3 tools/pipeline/codebook_review.py packet     # blinded judging packets (private)
    .venv/bin/python3 tools/pipeline/codebook_review.py score      # metrics from the two judges' verdicts

Raw responses and issue texts, which may quote paragraphs, stay in tools/runs/raw/codebook_review/ (gitignored); the
public files in tools/runs/codebook_review/ carry ids, counts, codebook ids, matches and costs only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import instrument as ins                                            # noqa: E402
import run_extract as rx                                            # noqa: E402

ROOT = HERE.parents[1]
CODEBOOKS = ROOT / "tools" / "annotation" / "codebook"
LABELS = ROOT / "tools" / "annotation" / "labels"
OUT = ROOT / "tools" / "runs" / "codebook_review"
RAW = ROOT / "tools" / "runs" / "raw" / "codebook_review"
GROUND_TRUTH = OUT / "ground_truth.json"
JOURNAL = RAW / "journal.jsonl"          # the smoke calls; the main runs write one journal per configuration


def journal_of(config: str) -> Path:
    return RAW / f"journal_{config}.jsonl"   # one writer per file, so configurations can run in parallel


def journal_lines() -> list[dict]:
    out = []
    for p in sorted(RAW.glob("journal*.jsonl")):
        out += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(out, key=lambda r: r.get("at") or "")
AUTHORITY = "pre-analysis specification §4h (2026-10-30)"
SEED = 20261030

SESSIONS = {"S1": ("v0.1", "A0"), "S2": ("v0.2", "A0b"), "S3": ("v1.0", "A1")}
ARMS = ("A", "B")
REPS = (1, 2, 3)
CONFIGS = {
    # caps amended 2026-10-30 before the main runs (§4h amendment): the smoke call showed mercury3_low spending
    # 7,162 and 7,560 reasoning tokens and truncating at the declared 8,192; every cap is raised so that none binds
    "opus":             {"model_id": ins.MODELS["opus"], "provider": "bedrock", "temperature": 0, "max_tokens": 8192,
                         "additional": None, "price": rx.PRICES_PER_M["opus"]},
    "sonnet":           {"model_id": ins.MODELS["sonnet"], "provider": "bedrock", "temperature": 0, "max_tokens": 8192,
                         "additional": None, "price": rx.PRICES_PER_M["sonnet"]},
    "mercury3_low":     {"model_id": "mercury-3", "provider": "inception", "temperature": 0.5, "max_tokens": 32768,
                         "additional": {"reasoning_effort": "low"}, "price": rx.PRICES_PER_M["mercury3_low"]},
    "mercury3_instant": {"model_id": "mercury-3", "provider": "inception", "temperature": 0.5, "max_tokens": 8192,
                         "additional": {"reasoning_effort": "instant"}, "price": rx.PRICES_PER_M["mercury3_instant"]},
}
DIFFUSION = ("mercury3_low", "mercury3_instant")
ISSUE_TYPES = ["missing_category", "ambiguous_definition", "overlapping_categories", "unclear_rule", "scope_or_unit", "other"]
TOOL_NAME = "report_codebook_issues"
TOOL = {"toolSpec": {
    "name": TOOL_NAME,
    "description": "Report the problems found in the codebook, most important first.",
    "inputSchema": {"json": {
        "type": "object",
        "properties": {"issues": {"type": "array", "maxItems": 15, "items": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ISSUE_TYPES},
                "codebook_ids": {"type": "array", "items": {"type": "string"},
                                 "description": "ids from the codebook the issue concerns; [] for a missing category"},
                "description": {"type": "string", "description": "the problem, in one sentence"},
                "edge_case": {"type": "string", "description": "a short invented example sentence the codebook does not decide"},
                "proposed_rule": {"type": "string"},
                "paragraph_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["type", "codebook_ids", "description", "edge_case", "proposed_rule"]}}},
        "required": ["issues"]}}}}
SYSTEM = (
    "You review a draft codebook for a quantitative content analysis of renewable-energy news (German and English "
    "newspaper paragraphs). Human coders and a language model will both code paragraphs with exactly this codebook, so "
    "every gap in it will produce disagreement. Find the problems a coder would run into: categories that are missing "
    "for statements the corpus is likely to contain, definitions that are ambiguous or leave a case undecided, elements "
    "whose boundaries overlap, rules that are unclear or contradict each other, and unclear scope (what counts as a "
    "technology, a frame, a comparison). Report the most important issues first, at most 15. For each issue give its "
    "type, the codebook ids it concerns (the ids used in the codebook; [] for a missing category), a one-sentence "
    "description, a short invented example sentence the codebook does not decide, and a proposed rule. Answer only by "
    "calling " + TOOL_NAME + ".")
ARM_B_NOTE = (
    "Below the codebook are {n} paragraphs from the corpus, the first material coders will code with it. Use them to "
    "find where the codebook does not fit or does not decide, and cite paragraph ids where relevant. Do not code the "
    "paragraphs. Invent your example sentences; do not quote the paragraphs.")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run_id(session: str, arm: str, config: str, rep: int) -> str:
    return f"{session}{arm}_{config}_{rep}"


def session_inputs(session: str) -> tuple[str, list[dict]]:
    version, material = SESSIONS[session]
    yaml_text = (CODEBOOKS / f"codebook-{version}.yaml").read_text(encoding="utf-8")
    doc = json.loads((LABELS / f"{material}_manifest.json").read_text(encoding="utf-8"))
    paras = doc["paragraphs"]
    texts = rx.load_texts(paras)                    # hash-checked against the manifest
    return yaml_text, [{"paragraph_id": p["paragraph_id"], "lang": p.get("lang", ""),
                        "text": texts[p["paragraph_id"]]["paragraph_text"]} for p in paras]


def build_request(session: str, arm: str, config: str) -> dict:
    version, _ = SESSIONS[session]
    yaml_text, paras = session_inputs(session)
    user = f"CODEBOOK (version {version}, YAML):\n\n{yaml_text}"
    if arm == "B":
        block = "\n\n".join(f"[{p['paragraph_id']}] ({p['lang']}) {p['text']}" for p in paras)
        user = ARM_B_NOTE.format(n=len(paras)) + "\n\n" + user + f"\n\nPARAGRAPHS ({len(paras)}):\n\n{block}"
    c = CONFIGS[config]
    kw = {"modelId": c["model_id"], "system": [{"text": SYSTEM}],
          "messages": [{"role": "user", "content": [{"text": user}]}],
          "toolConfig": {"tools": [TOOL], "toolChoice": {"tool": {"name": TOOL_NAME}}},
          "inferenceConfig": {"maxTokens": c["max_tokens"], "temperature": c["temperature"]}}
    if c["additional"]:
        kw["additionalModelRequestFields"] = dict(c["additional"])
    return kw


def client_for(config: str):
    if CONFIGS[config]["provider"] == "inception":
        import inception_client as icx
        return icx.InceptionConverseClient(endpoint=ins.EXTERNAL_API[config])
    import boto3
    from botocore.config import Config
    return boto3.client("bedrock-runtime", region_name="eu-central-1",
                        config=Config(retries={"total_max_attempts": 1, "mode": "standard"}, read_timeout=300))


def extract_issues(resp: dict) -> tuple[list | None, str]:
    """The issues of the one forced tool call, or (None, why). Nothing is repaired. Codex §4h round 1 (#2): the whole
    declared contract is enforced -- the tool name, at most 15 issues, string codebook ids, string paragraph ids."""
    blocks = (((resp.get("output") or {}).get("message") or {}).get("content")) or []
    uses = [b["toolUse"] for b in blocks if isinstance(b, dict) and "toolUse" in b]
    if len(uses) != 1:
        return None, f"{len(uses)} tool calls"
    if uses[0].get("name") != TOOL_NAME:
        return None, f"tool {uses[0].get('name')!r} is not {TOOL_NAME}"
    inp = uses[0].get("input")
    if not isinstance(inp, dict) or not isinstance(inp.get("issues"), list):
        return None, "tool input is not an object with an issues list"
    if len(inp["issues"]) > 15:
        return None, f"{len(inp['issues'])} issues, more than 15"
    out = []
    strs = lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v)
    for it in inp["issues"]:
        if not isinstance(it, dict) or it.get("type") not in ISSUE_TYPES or not strs(it.get("codebook_ids")) \
                or not all(isinstance(it.get(k), str) for k in ("description", "edge_case", "proposed_rule")) \
                or ("paragraph_ids" in it and not strs(it["paragraph_ids"])):
            return None, "an issue violates the schema"
        out.append(it)
    if resp.get("stopReason") not in ("tool_use", "end_turn"):
        return None, f"stop reason {resp.get('stopReason')!r}"
    return out, "ok"


def cost_of(usage: dict, config: str) -> float:
    p = CONFIGS[config]["price"]
    return (usage.get("inputTokens", 0) * p["input"] + usage.get("outputTokens", 0) * p["output"]) / 1e6


def journal_done() -> dict[str, dict]:
    done = {}
    for r in journal_lines():                # in time order: the latest final record of a run wins
        if r.get("final"):
            done[r["run_id"]] = r
    return done


def cmd_run(a) -> None:
    rx.load_dotenv()
    gt_sha = sha(GROUND_TRUTH.read_bytes())
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "issues").mkdir(exist_ok=True)
    done = journal_done()
    todo = [(s, ar, c, r) for s in SESSIONS for ar in ARMS for c in CONFIGS for r in REPS]
    if a.only:
        keep = set(a.only.split(","))
        todo = [t for t in todo if run_id(*t) in keep]
    clients = {}
    for s, ar, c, r in todo:
        rid = run_id(s, ar, c, r)
        kw = build_request(s, ar, c)
        req_sha = sha(json.dumps(kw, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        if done.get(rid, {}).get("request_sha256") == req_sha:
            continue
        if a.dry_run:
            print(f"  {rid}: request {req_sha[:12]} ({len(kw['messages'][0]['content'][0]['text'])} chars)")
            continue
        if c not in clients:
            clients[c] = client_for(c)
        issues, why, resp, usage, attempts = None, "", {}, {}, 0
        # Codex §4h round 1 (#3) / round 2 (#1): two separate budgets -- up to three resends after transport failures
        # (a fourth failure ends the run), and one identical retry of an invalid answer; every attempt names its request
        invalid_retry_used, transport_failures = False, 0
        while True:
            attempts += 1
            t0 = time.time()
            try:
                resp = clients[c].converse(**kw)
            except Exception as e:                    # transport, throttling, 5xx: back off and resend
                transport_failures += 1
                rec = {"run_id": rid, "attempt": attempts, "error": f"{type(e).__name__}: {str(e)[:300]}",
                       "request_sha256": req_sha, "at": datetime.now(timezone.utc).isoformat(), "final": False}
                with journal_of(c).open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if transport_failures > 3:
                    rec = {"run_id": rid, "final": True, "valid": False, "why": "no response after 4 transport failures",
                           "at": datetime.now(timezone.utc).isoformat(), "session": s, "arm": ar, "config": c, "rep": r,
                           "usage": {}, "ground_truth_sha256": gt_sha, "request_sha256": req_sha}
                    with journal_of(c).open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    print(f"  {rid}: FAILED (no response)")
                    break
                time.sleep(10 * transport_failures)
                continue
            latency = int(1000 * (time.time() - t0))
            usage = {k: v for k, v in (resp.get("usage") or {}).items() if isinstance(v, (int, float))}
            issues, why = extract_issues(resp)
            rec = {"run_id": rid, "attempt": attempts, "session": s, "arm": ar, "config": c, "rep": r,
                   "model_id": CONFIGS[c]["model_id"], "request_sha256": req_sha, "ground_truth_sha256": gt_sha,
                   "at": datetime.now(timezone.utc).isoformat(), "latency_ms": latency, "usage": usage,
                   "stop_reason": resp.get("stopReason"), "valid": issues is not None, "why": why,
                   "response": {k: v for k, v in resp.items() if k != "ResponseMetadata"}, "final": False}
            if issues is None and not invalid_retry_used:   # one identical retry on an invalid answer, then record
                invalid_retry_used = True
                with journal_of(c).open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                continue
            rec["final"] = True
            with journal_of(c).open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            if issues is not None:
                (RAW / "issues" / f"{rid}.json").write_text(json.dumps(issues, indent=2, ensure_ascii=False) + "\n",
                                                            encoding="utf-8")
            print(f"  {rid}: {'valid' if issues is not None else 'INVALID (' + why + ')'}  "
                  f"{len(issues or [])} issues  in {usage.get('inputTokens')} out {usage.get('outputTokens')}  "
                  f"{latency / 1000:.1f}s")
            break
    if not a.dry_run:
        write_runs_table()


def write_runs_table() -> None:
    done = journal_done()
    attempts, superseded = {}, {}
    for r in journal_lines():
        if r.get("request_sha256") and r["request_sha256"] != done.get(r["run_id"], {}).get("request_sha256"):
            superseded[r["run_id"]] = superseded.get(r["run_id"], 0) + 1      # under an amended-away request
            continue
        attempts[r["run_id"]] = attempts.get(r["run_id"], 0) + 1
    rows = []
    for rid, r in sorted(done.items()):
        p = RAW / "issues" / f"{rid}.json"
        issues = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        ids = sorted({i for it in issues for i in it.get("codebook_ids", []) if isinstance(i, str)})
        types = {t: sum(1 for it in issues if it.get("type") == t) for t in ISSUE_TYPES}
        u = r.get("usage") or {}
        rows.append({"run_id": rid, "session": r.get("session"), "arm": r.get("arm"), "config": r.get("config"),
                     "rep": r.get("rep"), "valid": r.get("valid"), "attempts": attempts.get(rid),
                     "superseded_attempts": superseded.get(rid, 0),
                     "n_issues": len(issues), **{f"n_{t}": n for t, n in types.items()}, "codebook_ids": " ".join(ids),
                     "input_tokens": u.get("inputTokens"), "output_tokens": u.get("outputTokens"),
                     "cost_usd": round(cost_of(u, r["config"]), 6) if r.get("config") else None,
                     "latency_ms": r.get("latency_ms")})
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "runs.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {(OUT / 'runs.csv').relative_to(ROOT)} ({len(rows)} runs)")


def cmd_packet(a) -> None:
    """Blinded packets, one per session x arm: the ground-truth items with their criteria and every run's issues
    under a random label (seeded); the key stays private until both judges have answered."""
    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    done = journal_done()
    rng = random.Random(SEED)
    key, packets = {}, {}
    for s in SESSIONS:
        for ar in ARMS:
            rids = [run_id(s, ar, c, r) for c in CONFIGS for r in REPS]
            rng.shuffle(rids)
            pk = f"{s}{ar}"
            runs = []
            for n, rid in enumerate(rids, 1):
                label = f"{pk}-R{n:02d}"
                key[label] = rid
                p = RAW / "issues" / f"{rid}.json"
                issues = json.loads(p.read_text(encoding="utf-8")) if (p.exists() and done.get(rid, {}).get("valid")) else []
                runs.append({"run": label, "issues": [{"n": i + 1, "type": it["type"], "codebook_ids": it["codebook_ids"],
                                                        "description": it["description"], "edge_case": it["edge_case"],
                                                        "proposed_rule": it["proposed_rule"]} for i, it in enumerate(issues)]})
            packets[pk] = {"session": s, "codebook": SESSIONS[s][0], "arm": ar,
                           "items": {g: gt["items"][g]["criterion"] for g in gt["sessions"][s]["items"]}, "runs": runs}
    (RAW / "packets.json").write_text(json.dumps({"matching": gt["matching"], "packets": packets}, indent=1,
                                                 ensure_ascii=False) + "\n", encoding="utf-8")
    (RAW / "packet_key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    print(f"  wrote {len(packets)} packets ({sum(len(p['runs']) for p in packets.values())} runs) to "
          f"{(RAW / 'packets.json').relative_to(ROOT)}; key kept apart")


def kappa(a_: list[bool], b_: list[bool]) -> float | None:
    n = len(a_)
    if not n:
        return None
    po = sum(x == y for x, y in zip(a_, b_)) / n
    pa, pb = sum(a_) / n, sum(b_) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def check_verdicts(gt: dict, key: dict, judges: dict) -> None:
    """Codex §4h round 1 (#4): refuse to score unless every judge decided every run-item pair with a real boolean and
    cited only issues that exist (at least one when it says found)."""
    errs = []
    for label, rid in key.items():
        pk = label.split("-")[0]
        p = RAW / "issues" / f"{rid}.json"
        n = len(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else 0
        for g in gt["sessions"][pk[:2]]["items"]:
            for j, v in judges.items():
                d = ((v.get(pk) or {}).get(label) or {}).get(g)
                if not isinstance(d, dict) or not isinstance(d.get("found"), bool) or not isinstance(d.get("issues"), list):
                    errs.append(f"{j} {label} {g}: missing or malformed decision")
                elif not all(isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= n for i in d["issues"]):
                    errs.append(f"{j} {label} {g}: issue reference outside 1..{n}")
                elif d["found"] and not d["issues"]:
                    errs.append(f"{j} {label} {g}: found without a matching issue")
                elif not d["found"] and d["issues"]:          # Codex §4h round 2 (#2)
                    errs.append(f"{j} {label} {g}: not found but citing issues")
    for j, v in judges.items():
        extra = {(pk, lab) for pk, runs in v.items() for lab in runs} - {(l_.split("-")[0], l_) for l_ in key}
        if extra:
            errs.append(f"{j}: decisions for unknown runs {sorted(extra)[:3]}")
    if errs:
        sys.exit("REFUSING to score: " + "; ".join(errs[:10]) + (f" (+{len(errs) - 10} more)" if len(errs) > 10 else ""))


def cmd_score(a) -> None:
    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    key = json.loads((RAW / "packet_key.json").read_text(encoding="utf-8"))
    judges = {j: json.loads((RAW / f"judge_{j}.json").read_text(encoding="utf-8")) for j in ("codex", "claude")}
    done = journal_done()
    check_verdicts(gt, key, judges)
    matches, pairs = [], {"codex": [], "claude": []}
    found = {}                                          # (run_id, item) -> (codex, claude)
    for label, rid in key.items():
        pk = label.split("-")[0]
        s = pk[:2]
        for g in gt["sessions"][s]["items"]:
            v = {j: judges[j][pk][label][g]["found"] for j in judges}         # validated above
            iss = {j: sorted(((judges[j].get(pk) or {}).get(label) or {}).get(g, {}).get("issues") or []) for j in judges}
            found[(rid, g)] = (v["codex"], v["claude"])
            pairs["codex"].append(v["codex"]); pairs["claude"].append(v["claude"])
            matches.append({"run_id": rid, "item": g, "codex": v["codex"], "claude": v["claude"],
                            "strict": v["codex"] and v["claude"], "liberal": v["codex"] or v["claude"],
                            "issues_codex": " ".join(map(str, iss["codex"])), "issues_claude": " ".join(map(str, iss["claude"]))})
    summary = {"declared": AUTHORITY, "ground_truth_sha256": sha(GROUND_TRUTH.read_bytes()),
               "judge_agreement": {"n": len(pairs["codex"]),
                                   "raw": sum(x == y for x, y in zip(pairs["codex"], pairs["claude"])) / len(pairs["codex"]),
                                   "kappa": kappa(pairs["codex"], pairs["claude"])},
               "cells": [], "diffusion_rule": None}
    for s in SESSIONS:
        items = gt["sessions"][s]["items"]
        for ar in ARMS:
            for c in CONFIGS:
                rids = [run_id(s, ar, c, r) for r in REPS]
                per_run = [sum(found[(rid, g)][0] and found[(rid, g)][1] for g in items) for rid in rids]
                per_run_lib = [sum(found[(rid, g)][0] or found[(rid, g)][1] for g in items) for rid in rids]
                union = [g for g in items if any(found[(rid, g)][0] and found[(rid, g)][1] for rid in rids)]
                in2 = [g for g in items if sum(found[(rid, g)][0] and found[(rid, g)][1] for rid in rids) >= 2]
                n_issues = []
                for rid in rids:
                    p = RAW / "issues" / f"{rid}.json"
                    n_issues.append(len(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else 0)
                summary["cells"].append({
                    "session": s, "arm": ar, "config": c, "items": len(items), "valid_runs": sum(bool(done.get(r_, {}).get("valid")) for r_ in rids),
                    "found_strict_per_run": per_run, "found_liberal_per_run": per_run_lib,
                    "mean_recall_strict": sum(per_run) / (len(items) * len(rids)),
                    "union_strict": union, "in_2_of_3_strict": in2, "issues_per_run": n_issues})
    # §4h rule: a diffusion-unique finding = an item found (strict) by a Mercury configuration in >= 2 of 3 runs and by
    # no Claude configuration in any run of the same session and arm
    unique = []
    for s in SESSIONS:
        for ar in ARMS:
            for g in gt["sessions"][s]["items"]:
                merc = any(sum(found[(run_id(s, ar, c, r), g)][0] and found[(run_id(s, ar, c, r), g)][1] for r in REPS) >= 2
                           for c in DIFFUSION)
                claude = any(found[(run_id(s, ar, c, r), g)][0] and found[(run_id(s, ar, c, r), g)][1]
                             for c in CONFIGS if c not in DIFFUSION for r in REPS)
                if merc and not claude:
                    unique.append({"session": s, "arm": ar, "item": g})
    # also reported: items a Mercury configuration found (strict) in any single run that no Claude run of the same
    # session and arm found -- descriptive, beside the declared rule
    one_off = []
    for s in SESSIONS:
        for ar in ARMS:
            for g in gt["sessions"][s]["items"]:
                merc_any = any(found[(run_id(s, ar, c, r), g)][0] and found[(run_id(s, ar, c, r), g)][1]
                               for c in DIFFUSION for r in REPS)
                claude_any = any(found[(run_id(s, ar, c, r), g)][0] and found[(run_id(s, ar, c, r), g)][1]
                                 for c in CONFIGS if c not in DIFFUSION for r in REPS)
                if merc_any and not claude_any:
                    one_off.append({"session": s, "arm": ar, "item": g})
    summary["diffusion_rule"] = {"unique_findings": unique, "mercury_only_in_any_run": one_off,
                                 "verdict": "diffusion-unique finding(s) under the §4h rule" if unique
                                 else "no diffusion-unique documented gap under the §4h rule"}
    # S3 soft spots: share of runs whose issues name F2c / F6b / F8a, beside every other element
    soft = {}
    for c in CONFIGS:
        for ar in ARMS:
            rids = [run_id("S3", ar, c, r) for r in REPS]
            named = {}
            for rid in rids:
                p = RAW / "issues" / f"{rid}.json"
                ids = {i for it in (json.loads(p.read_text(encoding="utf-8")) if p.exists() else []) for i in it.get("codebook_ids", [])}
                for i in ids:
                    named[i] = named.get(i, 0) + 1
            soft[f"{c}_{ar}"] = {i: named.get(i, 0) for i in sorted(named)}
    summary["s3_elements_named_per_3_runs"] = soft
    # overall per configuration: gap slots found (strict) over all runs, items found by session, and the share of
    # reported issues that both judges matched to some documented gap (a lower bound on useful flags: an unmatched
    # issue may be a real gap nobody documented)
    inv = {v_: k_ for k_, v_ in key.items()}
    overall = {}
    for c in CONFIGS:
        slots = hits = n_iss = n_matched = 0
        per_item = {}
        for s in SESSIONS:
            for ar in ARMS:
                pk = f"{s}{ar}"
                for r in REPS:
                    rid = run_id(s, ar, c, r)
                    label = inv[rid]
                    p_ = RAW / "issues" / f"{rid}.json"
                    n_iss += len(json.loads(p_.read_text(encoding="utf-8"))) if p_.exists() else 0
                    matched = set()
                    for g in gt["sessions"][s]["items"]:
                        slots += 1
                        a_, b_ = found[(rid, g)]
                        if a_ and b_:
                            hits += 1
                            per_item[g] = per_item.get(g, 0) + 1
                            # matched issues come only from affirmative decisions of both judges
                            matched |= (set(judges["codex"][pk][label][g]["issues"]) & set(judges["claude"][pk][label][g]["issues"]))
                    n_matched += len(matched)
        overall[c] = {"gap_slots": slots, "found_strict": hits, "share_found": hits / slots,
                      "runs_finding_item": dict(sorted(per_item.items())), "issues": n_iss,
                      "issues_matched_by_both": n_matched, "share_issues_matched": n_matched / n_iss if n_iss else None}
    summary["overall"] = overall
    cost = {}
    for rid, r in done.items():
        if r.get("config"):
            cost[r["config"]] = cost.get(r["config"], 0) + cost_of(r.get("usage") or {}, r["config"])
    summary["cost_usd_by_config"] = {k: round(v, 4) for k, v in cost.items()}
    with (OUT / "matches.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(matches[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(sorted(matches, key=lambda m: (m["run_id"], m["item"])))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tab = tex_table(summary)
    (ROOT / "paper" / "tables" / "tab_codebook_review.tex").write_text(tab, encoding="utf-8")
    print("  wrote paper/tables/tab_codebook_review.tex")
    print(f"  judges: raw agreement {summary['judge_agreement']['raw']:.3f}, kappa {summary['judge_agreement']['kappa']}")
    for cell in summary["cells"]:
        print(f"  {cell['session']}{cell['arm']} {cell['config']:<17} strict {cell['found_strict_per_run']} of "
              f"{cell['items']}  union {cell['union_strict']}  issues {cell['issues_per_run']}")
    for c, o in summary["overall"].items():
        print(f"  {c:<17} gap slots found {o['found_strict']}/{o['gap_slots']} ({o['share_found']:.2f}); issues matched "
              f"{o['issues_matched_by_both']}/{o['issues']} ({o['share_issues_matched']:.2f}); by item {o['runs_finding_item']}")
    print("  " + summary["diffusion_rule"]["verdict"])


NAMES = {"opus": "Opus 4.6", "sonnet": "Sonnet 4.6", "mercury3_low": "Mercury 3 (low)",
         "mercury3_instant": "Mercury 3 (instant)"}


def tex_table(summary: dict) -> str:
    """The thesis table: documented gaps found per run (strict: both judges), the union over three runs in brackets."""
    import figures as fg
    cells = {(c_["session"], c_["arm"], c_["config"]): c_ for c_ in summary["cells"]}
    rows = []
    for s_, (v_, m_) in SESSIONS.items():
        for ar in ARMS:
            n = cells[(s_, ar, "opus")]["items"]
            label = f"{s_}: v{v_[1:]}, {'codebook only' if ar == 'A' else 'codebook + ' + m_ + ' paragraphs'} ({n})"
            vals = []
            for c in CONFIGS:
                cl = cells[(s_, ar, c)]
                vals.append(" · ".join(str(x) for x in cl["found_strict_per_run"]) + f" ({len(cl['union_strict'])})")
            rows.append(fg.tex_escape(label) + " & " + " & ".join(vals) + r" \\")
    rows.append(r"\addlinespace")
    issues = []
    for c in CONFIGS:
        allr = [x for c_ in summary["cells"] if c_["config"] == c for x in c_["issues_per_run"]]
        issues.append(f"{sum(allr) / len(allr):.1f}" if allr else "--")
    rows.append("All sessions: gap slots found (of 84) & " + " & ".join(
        f"{summary['overall'][c]['found_strict']} ({100 * summary['overall'][c]['share_found']:.0f}\\%)" for c in CONFIGS) + r" \\")
    rows.append("Issues reported per run (mean) & " + " & ".join(issues) + r" \\")
    rows.append("Issues matched to a documented gap & " + " & ".join(
        f"{100 * summary['overall'][c]['share_issues_matched']:.0f}\\%" for c in CONFIGS) + r" \\")
    rows.append("Cost of all 18 runs, USD & " + " & ".join(f"{summary['cost_usd_by_config'].get(c, 0):.2f}" for c in CONFIGS) + r" \\")
    header = "Session and input (documented gaps) & " + " & ".join(NAMES[c] for c in CONFIGS)
    ja = summary["judge_agreement"]
    caption = ("Retrospective codebook review (specification §4h): documented gaps found per run, three runs per cell, "
               "and in brackets the gaps found in any of the three. A gap counts as found only if both blinded judges "
               "(Codex and a Claude judge) match a reported issue to it; the judges agreed on "
               f"{100 * ja['raw']:.0f}\\% of the {ja['n']} run-gap pairs"
               + (f" ($\\kappa = {ja['kappa']:.2f}$)" if ja.get("kappa") is not None else "") + ". Each session replays one "
               "human session: the codebook version the coder had and, in the second row, the same paragraphs; the "
               "documented gaps are those a human found later (calibration A0 and A0b, the freeze, and the general "
               "category's two readings). Opus and Sonnet at temperature 0 on Bedrock, Mercury 3 at temperature 0.5 "
               "through its developer's API. Descriptive.")
    return ("% Generated by tools/pipeline/codebook_review.py score from tools/runs/codebook_review/summary.json "
            "(specification §4h). Do not edit by hand.\n"
            + fg.table_env(caption, "tab:codebook_review", "@{}p{58mm}" + "r" * len(CONFIGS) + "@{}", header, rows, [],
                           size=r"\footnotesize", short="Retrospective codebook review"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--dry-run", action="store_true"); r.add_argument("--only")
    sub.add_parser("packet"); sub.add_parser("score")
    a = ap.parse_args()
    {"run": cmd_run, "packet": cmd_packet, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
