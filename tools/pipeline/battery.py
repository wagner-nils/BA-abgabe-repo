#!/usr/bin/env python3
"""Label-free instrument-sensitivity battery (pre-analysis specification §4i, declared 2026-10-30).

Where does the registered instrument's output depend on the order of the codebook, on element labels or on the
wording of a definition -- and does that sensitivity locate the gaps whose position is known? The registered Sonnet
4.6 request is sent byte for byte for the baseline R and, for every variant, with exactly one declared change to the
system prompt text or to the order of the schema enums. Variants are rendered in memory from the frozen codebook with
the registered renderer; no pinned file changes. Classification, resolution, retry policy v2 and the journal are the
runner's own (`run_extract.classify`, `resolve`, `Journal`).

    .venv/bin/python3 tools/pipeline/battery.py check                 # renders, asserts R == registered request
    .venv/bin/python3 tools/pipeline/battery.py run --phase A --worker 0 --workers 4
    .venv/bin/python3 tools/pipeline/battery.py status
    .venv/bin/python3 tools/pipeline/battery.py analyse               # public outputs and thesis tables

Phases (the §4i priority order): A = R, E1/E2, K1/K1c/K2, O1/O2, G on the 443 frame-completion paragraphs;
B = Pa1/Pa2; C = Pu (one unit at a time, four paraphrase sets); D = the A2 subset (R x 2, Pa1, Pa2, O1).
Raw responses stay in tools/runs/raw/battery/ (gitignored); public files carry ids, codes, labels and codebook text only
(the paraphrase files restate definitions, including the one calibration quotation the registered prompt itself carries).

Route: `--route eu` (default) sends the registered model id `eu.anthropic.claude-sonnet-4-6`; `--route global` sends the
same model through the global cross-region inference profile, which has its own daily token quota. The author
authorised the global route for the remaining battery calls on 2026-10-30 after the EU daily quota was exhausted; the
route is journalled with every attempt and terminal (records without it ran on the EU route).
"""
from __future__ import annotations

import argparse
import copy
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
import sample_contract as sc                                        # noqa: E402

ROOT = HERE.parents[1]
LABELS = ROOT / "tools" / "annotation" / "labels"
OUT = ROOT / "tools" / "runs" / "battery"
RAW = ROOT / "tools" / "runs" / "raw" / "battery"
FC_MANIFEST = LABELS / "A2_frame_completion_manifest.json"
A2_MANIFEST = LABELS / "A2_sample_manifest.json"
FC_CALLS = ROOT / "tools" / "runs" / "frame_completion_20261019_calls.jsonl"
PARAPHRASES = OUT / "paraphrases_accepted.json"
AUTHORITY = "pre-analysis specification §4i (2026-10-30)"
MODEL = "sonnet"
SEED = 20261030
BUDGET_USD = 150.0              # §4i and §4j together; the battery stops a phase rather than exceed its share
BUDGET_RESERVE_4J = 12.0        # kept free for §4j and the paraphrase calls
ROUTES = {"eu": ins.MODELS[MODEL], "global": "global.anthropic.claude-sonnet-4-6"}

SECONDARY = (("does_not_fire_when", "Does NOT fire when"), ("boundary", "Boundary"),
             ("boundary_vs_F5a", "Boundary against F5a"), ("recall_warning", "Note on recall"))
IMPORTANT = ("general", "hydrogen", "F2b", "F2c", "F3b", "F5a", "F6b")       # §4i Pu units
K1_GENERAL = ("Tick `general` whenever a bundle term — renewables, clean power, green energy, the energy transition — "
              "carries a statement of its own, even if the same paragraph also names individual technologies.")
K1_CONTROL = {"hydrogen": "Tick `hydrogen` whenever the paragraph makes a statement about hydrogen as an energy "
                          "carrier, even if the same paragraph also names other technologies.",
              "nuclear": "Tick `nuclear` whenever the paragraph makes a statement about nuclear power, even if the "
                         "same paragraph also names other technologies."}
K2_GENERAL = ("Tick `general` only when no individual technology is identifiable in the paragraph; otherwise record "
              "the statement under the named technology.")


class BatteryError(RuntimeError):
    pass


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# the instrument and its text units
# --------------------------------------------------------------------------- #
def base() -> tuple[dict, dict, str, ins.Artifacts]:
    doc, subtypes, cb_sha = ins.load_codebook()
    art = ins.load_artifacts()
    if ins.render_prompt(doc, subtypes, cb_sha) != art.prompt or ins.build_tool_config(doc, subtypes) != art.tool_config:
        raise BatteryError("the in-memory render differs from the pinned artefacts")
    return doc, subtypes, cb_sha, art


def text_units(doc: dict) -> dict[str, dict]:
    """The 34 definition texts as they reach the model: path -> {unit, kind, text, line} where `line` is the exact
    rendered line that carries the text (so a replacement can be asserted to occur once)."""
    out = {}
    for t in doc["technologies"]:
        txt = ins.flow(t["definition"])
        out[f"technologies.{t['id']}.definition"] = {
            "unit": t["id"], "kind": "definition", "text": txt, "line": f"**{t['id']}** — {t['label']}\n: {txt}"}
    for el in doc["frame_elements"]:
        txt = ins.flow(el["fires_when"])
        out[f"frame_elements.{el['id']}.fires_when"] = {
            "unit": el["id"], "kind": "fires_when", "text": txt, "line": f": Fires when: {txt}"}
        for field, lead in SECONDARY:
            if field in el:
                path = f"frame_elements.{el['id']}.{field}"
                txt = ins.projected(path, el[field])
                out[path] = {"unit": el["id"], "kind": field, "text": txt, "line": f": {lead}: {txt}"}
    return out


def artifacts_for(prompt: str, cfg: dict, cb_sha: str, version: str, pinned: ins.Artifacts | None = None) -> ins.Artifacts:
    """In-memory artefacts of one variant; an unchanged prompt or tool configuration keeps the pinned digest."""
    same_p = pinned is not None and prompt == pinned.prompt
    same_c = pinned is not None and cfg == pinned.tool_config
    return ins.Artifacts(prompt=prompt, prompt_sha256=pinned.prompt_sha256 if same_p else sha(prompt), tool_config=cfg,
                         tool_config_sha256=pinned.tool_config_sha256 if same_c else
                         sha(json.dumps(cfg, sort_keys=True, separators=(",", ":"))),
                         codebook_version=version, codebook_sha256=cb_sha)


def replace_once(prompt: str, old: str, new: str, what: str) -> str:
    n = prompt.count(old)
    if n != 1:
        raise BatteryError(f"{what}: expected exactly one occurrence, found {n}")
    return prompt.replace(old, new)


def planted_halves(doc: dict) -> tuple[list[str], list[str]]:
    carriers = sorted(el["id"] for el in doc["frame_elements"] if any(f in el for f, _ in SECONDARY))
    half = sorted(random.Random(SEED).sample(carriers, len(carriers) // 2))
    return half, sorted(set(carriers) - set(half))


def load_paraphrases() -> dict:
    if not PARAPHRASES.exists():
        return {}
    return json.loads(PARAPHRASES.read_text(encoding="utf-8"))


def variants(doc: dict, subtypes: dict, cb_sha: str, art: ins.Artifacts, para: dict | None = None) -> dict[str, dict]:
    """variant id -> {art, doc, change, edits}. Every variant differs from R only by the declared change."""
    units = text_units(doc)
    v = {"R": {"art": art, "doc": doc, "change": "none (the registered request)", "edits": []}}

    def from_doc(d: dict, change: str) -> dict:
        return {"art": artifacts_for(ins.render_prompt(d, subtypes, cb_sha), ins.build_tool_config(d, subtypes), cb_sha,
                                     doc["version"], art), "doc": d, "change": change, "edits": []}

    # O1: frame blocks reversed, elements kept within their block and in their order
    d = copy.deepcopy(doc)
    frames = []
    for el in d["frame_elements"]:
        if el["frame"] not in frames:
            frames.append(el["frame"])
    d["frame_elements"] = [el for f in reversed(frames) for el in d["frame_elements"] if el["frame"] == f]
    v["O1"] = from_doc(d, "frame blocks in reverse order (prompt and schema enum)")
    # O2: technology list reversed
    d = copy.deepcopy(doc)
    d["technologies"] = list(reversed(d["technologies"]))
    v["O2"] = from_doc(d, "technology list in reverse order (prompt and schema enum)")
    # G: element labels neutralised
    d = copy.deepcopy(doc)
    for i, el in enumerate(d["frame_elements"], 1):
        el["label"] = f"Element {i}"
    v["G"] = from_doc(d, "element labels replaced by neutral tokens")

    def text_variant(edits: list[tuple[str, str, str]], change: str) -> dict:
        p = art.prompt
        for path, old, new in edits:
            p = replace_once(p, old, new, path)
        return {"art": artifacts_for(p, art.tool_config, cb_sha, doc["version"], art), "doc": doc, "change": change,
                "edits": [{"path": path, "old_sha256": sha(old), "new_sha256": sha(new)} for path, old, new in edits]}

    def append_to_definition(tech: str, sentence: str) -> tuple[str, str, str]:
        u = units[f"technologies.{tech}.definition"]
        return (f"technologies.{tech}.definition", u["line"], u["line"] + " " + sentence)

    v["K1"] = text_variant([append_to_definition("general", K1_GENERAL)], "general: explicit per-statement reading")
    v["K1c"] = text_variant([append_to_definition(t, s) for t, s in K1_CONTROL.items()],
                            "hydrogen and nuclear: the parallel per-statement sentence (negative controls)")
    v["K2"] = text_variant([append_to_definition("general", K2_GENERAL)], "general: explicit residual reading")
    h1, h2 = planted_halves(doc)
    for vid, half in (("E1", h1), ("E2", h2)):
        edits = [(path, "\n" + u["line"], "") for path, u in units.items()
                 if u["unit"] in half and u["kind"] in dict(SECONDARY)]
        v[vid] = text_variant(edits, f"planted gaps: secondary rules removed from {', '.join(half)}")
    if para:
        for set_id in ("Pa1", "Pa2"):
            src = para.get(set_id) or {}
            edits = [(path, units[path]["line"], units[path]["line"].replace(units[path]["text"], src[path]))
                     for path in units if path in src]
            if edits:
                v[set_id] = text_variant(edits, f"all definition texts paraphrased ({set_id})")
        for unit in IMPORTANT:
            for set_id in ("P1", "P2", "P3", "P4"):
                src = para.get(f"Pu_{set_id}") or {}
                paths = [p_ for p_, u in units.items() if u["unit"] == unit]
                if paths and all(p_ in src for p_ in paths):
                    edits = [(p_, units[p_]["line"], units[p_]["line"].replace(units[p_]["text"], src[p_])) for p_ in paths]
                    v[f"Pu_{unit}_{set_id}"] = text_variant(edits, f"{unit} paraphrased alone ({set_id})")
    return v


PHASES = {
    "A": [("R", 3), ("E1", 2), ("E2", 2), ("K1", 1), ("K1c", 1), ("K2", 1), ("O1", 2), ("O2", 2), ("G", 2)],
    "B": [("Pa1", 2), ("Pa2", 2)],
    "C": [(f"Pu_{u}_{s}", 1) for u in IMPORTANT for s in ("P1", "P2", "P3", "P4")],      # variants without accepted texts are skipped
    "D": [("R", 2), ("Pa1", 1), ("Pa2", 1), ("O1", 1)],
    # the A2 subset in two parts, so that the part without paraphrases need not wait for them (same tasks as D)
    "D0": [("R", 2), ("O1", 1)],
    "DP": [("Pa1", 1), ("Pa2", 1)],
    # route check (2026-10-30, after the switch to the global route and before any variant result was read): two more
    # baseline executions, numbered 4 and 5, on the global route, so that every paragraph has baseline pairs on both
    # routes and every shift can be computed on route-matched pairs
    "RG": [("R", (4, 5))],
}
# every remaining task in the declared priority order (A, B, C, then the A2 subset), shuffled within each phase only;
# introduced after 13 parallel workers had triggered sustained throttling (2026-10-30)
ORDERED = ("A", "B", "C", "D0", "DP")


def paragraphs(phase: str) -> tuple[str, list[dict], dict]:
    on_a2 = phase.startswith("D")
    man_path = A2_MANIFEST if on_a2 else FC_MANIFEST
    man = json.loads(man_path.read_text(encoding="utf-8"))
    if not on_a2:
        sc.verify_frame_completion(man)
    paras = man["paragraphs"]
    texts = rx.load_texts(paras)
    sample = "A2" if on_a2 else "FC"
    return sample, paras, {p["paragraph_id"]: texts[p["paragraph_id"]]["paragraph_text"] for p in paras}


def tasks_for(phase: str, vs: dict) -> list[tuple[str, int, str]]:
    if phase == "ALL":
        out = []
        for ph in ORDERED:
            out += [(ph, *t) for t in tasks_for(ph, vs)]
        return out
    sample, paras, _ = paragraphs(phase)
    out = []
    for vid, k in PHASES[phase]:
        if vid not in vs:
            if phase == "C":                     # §4i: a one-unit variant with a text not accepted twice is dropped
                continue
            raise BatteryError(f"variant {vid} is not available (paraphrases missing?)")
        for e in (range(1, k + 1) if isinstance(k, int) else k):
            out += [(vid, e, p["paragraph_id"]) for p in paras]
    random.Random(f"{SEED}-{phase}").shuffle(out)          # seeded interleaving across variants and paragraphs
    return out


def journal_paths() -> list[Path]:
    return sorted(RAW.glob("journal_*.jsonl"))


def read_journals() -> list[dict]:
    recs = []
    for p in journal_paths():
        rx.repair_tail(p)
        recs += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return recs


def spent_usd(recs: list[dict]) -> float:
    pr = rx.PRICES_PER_M[MODEL]
    tot = 0.0
    for r in recs:
        u = r.get("usage") or {}
        tot += (u.get("input_tokens", 0) * pr["input"] + u.get("output_tokens", 0) * pr["output"]
                + u.get("cache_read_tokens", 0) * pr["cache_read"] + u.get("cache_write_tokens", 0) * pr["cache_write_1h"]) / 1e6
    return tot


def cmd_check(a) -> None:
    doc, subtypes, cb_sha, art = base()
    vs = variants(doc, subtypes, cb_sha, art, load_paraphrases())
    _, paras, texts = paragraphs("A")
    built = {}
    for line in FC_CALLS.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("type") == "attempt_started" and r.get("run_index") == 1:
            built.setdefault(r["paragraph_id"], r["request_sha256_built"])
    ok = sum(rx.request_digest({"modelId": ins.MODELS[MODEL], **ins.request_for(MODEL, art, texts[p["paragraph_id"]],
                                                                                  max_tokens=rx.MAX_TOKENS)})
             == built.get(p["paragraph_id"]) for p in paras)
    print(f"  R requests identical to frame_completion_20261019: {ok}/{len(paras)}")
    if ok != len(paras):
        raise BatteryError("the baseline request differs from the registered one")
    h1, h2 = planted_halves(doc)
    print(f"  planted halves: E1 {h1}  E2 {h2}")
    for vid, v_ in vs.items():
        print(f"  {vid:<16} prompt {v_['art'].prompt_sha256[:12]} cfg {v_['art'].tool_config_sha256[:12]} "
              f"edits {len(v_['edits'])}  {v_['change']}")


def cmd_run(a) -> None:
    rx.load_dotenv()
    doc, subtypes, cb_sha, art = base()
    vs = variants(doc, subtypes, cb_sha, art, load_paraphrases())
    phases = ORDERED if a.phase == "ALL" else (a.phase,)
    loaded = {ph: paragraphs(ph) for ph in phases}
    raw_tasks = tasks_for(a.phase, vs) if a.phase == "ALL" else [(a.phase, *t) for t in tasks_for(a.phase, vs)]
    tasks = raw_tasks[a.worker::a.workers]
    if a.limit:
        tasks = tasks[:a.limit]
    RAW.mkdir(parents=True, exist_ok=True)
    # an `error` terminal (throttling or exceptions beyond the retry budget) is an infrastructure outcome and is re-run,
    # as the runner's --recover-infrastructure does; every other terminal is final
    done = {(r["sample"], r["variant"], r["execution"], r["paragraph_id"]) for r in read_journals()
            if r.get("type") == "terminal" and r.get("outcome") != rx.T_ERROR}
    jr = rx.Journal(RAW / f"journal_{a.phase}_w{a.worker}.jsonl")
    import boto3
    from botocore.config import Config
    client = boto3.client("bedrock-runtime", region_name=ins.REGION,
                          config=Config(retries={"total_max_attempts": 1, "mode": "standard"}, read_timeout=120))
    # the cap counts what earlier processes already spent, so a restart cannot exceed the budget
    cap = (BUDGET_USD - BUDGET_RESERVE_4J - spent_usd(read_journals())) / a.workers
    model_id = ROUTES[a.route]
    mine = 0.0
    n_new = 0
    if a.phase == "RG" and a.route != "global":
        raise BatteryError("phase RG is the global-route baseline; run it with --route global")
    for ph, vid, e, pid in tasks:
        sample, _, texts = loaded[ph]
        if (sample, vid, e, pid) in done:
            continue
        if mine > cap:
            jr.write({"type": "budget_stop", "phase": a.phase, "worker": a.worker, "spent_usd": mine, "at": utcnow()})
            print(f"  worker {a.worker}: budget share reached (USD {mine:.2f}); stopping")
            break
        v_ = vs[vid]
        req = {"modelId": model_id, **ins.request_for(MODEL, v_["art"], texts[pid], max_tokens=rx.MAX_TOKENS)}
        dig = rx.request_digest(req)
        codes, payloads, attempt = [], {}, 0
        while True:
            outcome = rx.resolve(codes)
            if outcome is not None:
                break
            attempt += 1
            t0 = time.monotonic()
            base_rec = {"sample": sample, "variant": vid, "execution": e, "paragraph_id": pid, "attempt": attempt,
                        "request_sha256": dig, "prompt_sha256": v_["art"].prompt_sha256,
                        "tool_config_sha256": v_["art"].tool_config_sha256, "route": a.route}
            try:
                resp = client.converse(**req)
                lat = int((time.monotonic() - t0) * 1000)
                code, payload, rules, problems, _use = rx.classify(resp, v_["art"], v_["doc"], subtypes)
                u = rx.usage_of(resp)
                mine += spent_usd([{"usage": u}])
                rec = {**base_rec, "type": "attempt", "code": code, "contract_rules": rules,
                       "stop_reason": resp.get("stopReason"), "usage": u, "latency_ms": lat,
                       "payload": rx.public_payload(payload) if payload else None,
                       "response": {k: resp[k] for k in ("output", "stopReason", "usage") if k in resp}, "at": utcnow()}
                if payload is not None:
                    payloads[attempt] = rec["payload"]
            except Exception as exc:  # noqa: BLE001
                lat = int((time.monotonic() - t0) * 1000)
                name = type(exc).__name__
                err = (getattr(exc, "response", {}) or {}).get("Error", {}).get("Code", name)
                throttle = "Throttl" in name or "Throttl" in str(err)
                if throttle and "per day" in str(exc):
                    # the account's daily token quota (2026-10-30): not model behaviour and not a transport failure of this
                    # request -- waited out without spending the retry budget; journalled as a quota wait
                    jr.write({**base_rec, "type": "quota_wait", "error": f"{name}: {str(exc)[:200]}", "at": utcnow()})
                    attempt -= 1
                    time.sleep(600)
                    continue
                code = rx.THROTTLED if throttle else rx.ERROR
                rec = {**base_rec, "type": "attempt", "code": code, "error": f"{name}: {str(exc)[:300]}",
                       "latency_ms": lat, "at": utcnow()}
                if throttle:
                    time.sleep(min(rx.BACKOFF_BASE_S * (2 ** sum(c == rx.THROTTLED for c in codes)), 60.0))
                else:
                    time.sleep(3)
            jr.write(rec)
            codes.append(code)
        resolving = next((i + 1 for i, c in enumerate(codes) if c in (rx.OK_PRESENT, rx.OK_OFF)), None)
        jr.write({"type": "terminal", "sample": sample, "variant": vid, "execution": e, "paragraph_id": pid,
                  "outcome": outcome, "codes": codes, "payload": payloads.get(resolving) if resolving else None,
                  "request_sha256": dig, "route": a.route, "at": utcnow()})
        n_new += 1
        if n_new % 200 == 0:
            print(f"  worker {a.worker} phase {a.phase}: {n_new} done, USD {mine:.2f}", flush=True)
    print(f"  worker {a.worker} phase {a.phase}: finished, {n_new} new terminals, USD {mine:.2f}", flush=True)


def cmd_status(a) -> None:
    recs = read_journals()
    term = [r for r in recs if r.get("type") == "terminal"]
    from collections import Counter
    c = Counter((r["sample"], r["variant"], r["execution"]) for r in term)
    oc = Counter((r["sample"], r["variant"], r["outcome"]) for r in term)
    for k in sorted(c):
        print(f"  {k[0]} {k[1]:<16} exec {k[2]}: {c[k]}")
    print("  outcomes:", dict(sorted(oc.items())))
    print("  terminals by route:", dict(Counter(r.get("route", "eu") for r in term)))
    print(f"  spent USD {spent_usd(recs):.2f}; throttles {sum(r.get('code') == rx.THROTTLED for r in recs)}; "
          f"errors {sum(r.get('code') == rx.ERROR for r in recs)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    r = sub.add_parser("run")
    r.add_argument("--phase", required=True, choices=list(PHASES) + ["ALL"])
    r.add_argument("--worker", type=int, default=0)
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--route", choices=sorted(ROUTES), default="eu")
    r.add_argument("--limit", type=int, default=0, help="smoke test: only the first N tasks of this worker")
    sub.add_parser("status")
    a = ap.parse_args()
    {"check": cmd_check, "run": cmd_run, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
