#!/usr/bin/env python3
"""Paraphrase sets for the §4i battery (pre-analysis specification §4i, declared 2026-10-30).

Generators: Kimi K3 (`global.moonshotai.kimi-k3`, the only profile the account can call -- it receives codebook text
only; quoted spans, which include a few verbatim corpus phrases, and backtick spans are masked before sending and
restored after) and Qwen3-235B (EU). One call per text and set: s1 for all 34 definition texts, s2 for the texts of
the important units. Automatic checks: placeholders and element codes preserved, length ratio 0.6-1.6. Blinded
equivalence judging by Codex and a Claude judge happens outside this script (packet / accept).

    .venv/bin/python3 tools/pipeline/battery_paraphrase.py generate [--round 1|2]
    .venv/bin/python3 tools/pipeline/battery_paraphrase.py packet --round 1
    .venv/bin/python3 tools/pipeline/battery_paraphrase.py accept --round 1   # after both judges' verdicts exist
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import battery as bt                                                # noqa: E402
import run_extract as rx                                            # noqa: E402

OUT = bt.OUT
RAW = bt.RAW / "paraphrase"
CANDIDATES = OUT / "paraphrase_candidates.json"      # merged view; generation writes one file per generator and set


def group_file(group: str) -> Path:
    return OUT / f"paraphrase_candidates_{group}.json"


def load_all() -> dict:
    """All candidate items, merged from the per-group files (one writer per file)."""
    cand = {"declared": bt.AUTHORITY, "items": {}}
    for f in sorted(OUT.glob("paraphrase_candidates_*.json")):
        cand["items"].update(json.loads(f.read_text(encoding="utf-8"))["items"])
    return cand


def save_all(cand: dict) -> None:
    groups = {}
    for k, it in cand["items"].items():
        groups.setdefault(k.split("|", 1)[0], {})[k] = it
    for g, items in groups.items():
        group_file(g).write_text(json.dumps({"declared": bt.AUTHORITY, "items": items}, indent=1, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    CANDIDATES.write_text(json.dumps(cand, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
GENERATORS = {"kimi": "global.moonshotai.kimi-k3", "qwen": "qwen.qwen3-235b-a22b-2507-v1:0"}
SETS = {"s1": "all", "s2": "important"}
SYSTEM = (
    "You rewrite one definition from a content-analysis codebook. Write the same rule in different words. "
    "Requirements: (1) The meaning must be exactly the same: do not add, remove, narrow or broaden any condition, case "
    "or exception. (2) Do not resolve any ambiguity: if the original leaves something open, your version must leave it "
    "equally open. (3) Do not add examples or explanations. (4) Keep every placeholder of the form ⟦…⟧ exactly as it "
    "is, and keep every code such as F1a, F5a or F6b exactly. (5) English; one paragraph; roughly the same length. "
    "Answer only with JSON: {\"paraphrase\": \"...\"}.")
# Round 2 (regeneration of texts the judges rejected in round 1) adds the constraints the round-1 judges named; disclosed in §4i
SYSTEM_ROUND2 = SYSTEM.replace(
    "(5) English; one paragraph; roughly the same length.",
    "(5) Keep every capitalised word or phrase (NOT, ONLY, WITHOUT, SUBJECT ...) capitalised. (6) Keep scope-setting verbs and "
    "quantifiers exactly in force: do not turn 'refers to' into 'mentions' or 'concerns', do not add or drop 'any', 'also', "
    "'explicitly', 'such as' or 'namely', and do not turn an illustrative list into an exhaustive one or the reverse. (7) Keep "
    "technical terms (e.g. bioenergy, geothermal, permitting, decarbonisation pathway) as they are. (8) English; one paragraph; "
    "roughly the same length.")
MASK_RE = re.compile(r'"[^"]+"|“[^”]+”|`[^`]+`')
CODE_RE = re.compile(r"\bF\d[a-c]?\b")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask(text: str) -> tuple[str, dict]:
    spans = {}
    def sub(m):
        key = f"⟦{len(spans) + 1}⟧"
        spans[key] = m.group(0)
        return key
    return MASK_RE.sub(sub, text), spans


def unmask(text: str, spans: dict) -> str:
    for k, v in spans.items():
        text = text.replace(k, v)
    return text


def check(original: str, masked_out: str, spans: dict) -> list[str]:
    probs = []
    for k in spans:
        if masked_out.count(k) != 1:
            probs.append(f"placeholder {k} appears {masked_out.count(k)} times")
    if any(p not in spans for p in re.findall(r"⟦\d+⟧", masked_out)):
        probs.append("unknown placeholder")
    if sorted(set(CODE_RE.findall(original))) != sorted(set(CODE_RE.findall(masked_out))):
        probs.append("element codes changed")
    ratio = len(unmask(masked_out, spans)) / max(1, len(original))
    if not 0.6 <= ratio <= 1.6:
        probs.append(f"length ratio {ratio:.2f}")
    if "\n" in masked_out.strip():
        probs.append("more than one paragraph")
    return probs


def call(client, gen: str, masked: str, system: str = SYSTEM) -> tuple[str | None, dict, str]:
    kw = {"modelId": GENERATORS[gen], "system": [{"text": system}],
          "messages": [{"role": "user", "content": [{"text": "Definition:\n" + masked}]}],
          "inferenceConfig": {"maxTokens": 8000} if gen == "kimi" else {"maxTokens": 2000, "temperature": 0.7}}
    for attempt in range(1, 6):
        try:
            r = client.converse(**kw)
            txt = "".join(b.get("text", "") for b in r["output"]["message"]["content"] if "text" in b).strip()
            m = re.search(r"\{.*\}", txt, re.S)
            if not m:
                return None, r.get("usage") or {}, "no JSON object"
            try:
                return json.loads(m.group(0))["paraphrase"].strip(), r.get("usage") or {}, "ok"
            except (ValueError, KeyError, TypeError, AttributeError):
                return None, r.get("usage") or {}, "malformed JSON"
        except Exception as exc:  # noqa: BLE001
            time.sleep(min(2 ** attempt, 30))
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
    return None, {}, last


def cmd_generate(a) -> None:
    rx.load_dotenv()
    import boto3
    from botocore.config import Config
    client = boto3.client("bedrock-runtime", region_name="eu-central-1",
                          config=Config(retries={"total_max_attempts": 1}, read_timeout=600))
    doc, *_ = bt.base()
    units = bt.text_units(doc)
    gfile = group_file(a.only)
    cand = json.loads(gfile.read_text(encoding="utf-8")) if gfile.exists() else {"declared": bt.AUTHORITY, "items": {}}
    todo = []
    for set_id, scope in SETS.items():
        for gen in GENERATORS:
            for path, u in units.items():
                if scope == "important" and u["unit"] not in bt.IMPORTANT:
                    continue
                key = f"{gen}_{set_id}|{path}"
                if f"{gen}_{set_id}" != a.only:
                    continue
                item = cand["items"].get(key)
                if a.round == 1 and item is None:
                    todo.append((key, gen, path))
                elif a.round == 2 and item is not None and item.get("status") == "rejected_round1":
                    todo.append((key, gen, path))
    RAW.mkdir(parents=True, exist_ok=True)
    log = (RAW / f"generate_round{a.round}_{a.only}.jsonl").open("a", encoding="utf-8")
    for key, gen, path in todo:
        orig = units[path]["text"]
        masked, spans = mask(orig)
        out, usage, why = None, {}, ""
        for tries in range(1, 4):                       # a reply that fails the automatic checks is redrawn (max 3)
            out, usage, why = call(client, gen, masked, SYSTEM_ROUND2 if a.round == 2 else SYSTEM)
            probs = check(orig, out, spans) if out else [why]
            log.write(json.dumps({"key": key, "round": a.round, "try": tries, "problems": probs, "usage": usage,
                                  "at": utcnow()}, ensure_ascii=False) + "\n"); log.flush()
            if out and not probs:
                break
        item = cand["items"].setdefault(key, {"generator": gen, "path": path, "unit": units[path]["unit"],
                                             "original": orig, "history": []})
        item["history"].append({"round": a.round, "paraphrase": unmask(out, spans) if out else None,
                                "auto_problems": check(orig, out, spans) if out else [why]})
        item["paraphrase"] = item["history"][-1]["paraphrase"]
        item["status"] = "auto_failed" if item["history"][-1]["auto_problems"] else f"candidate_round{a.round}"
        print(f"  {key}: {item['status']}", flush=True)
        OUT.mkdir(parents=True, exist_ok=True)
        gfile.write_text(json.dumps(cand, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_packet(a) -> None:
    cand = load_all()
    pairs = [(k, it) for k, it in cand["items"].items() if it.get("status") == f"candidate_round{a.round}"]
    rng = random.Random(f"{bt.SEED}-para-{a.round}")
    rng.shuffle(pairs)
    packet, key = [], {}
    for n, (k, it) in enumerate(pairs, 1):
        pid = f"R{a.round}-{n:03d}"
        key[pid] = k
        packet.append({"pair": pid, "original": it["original"], "rewrite": it["paraphrase"]})
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"judge_packet_round{a.round}.json").write_text(json.dumps({"pairs": packet}, indent=1, ensure_ascii=False) + "\n",
                                                          encoding="utf-8")
    (RAW / f"judge_key_round{a.round}.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    print(f"  {len(packet)} pairs -> {RAW / f'judge_packet_round{a.round}.json'}")


def cmd_accept(a) -> None:
    """A pair is accepted only if both judges rate it equivalent and neither reports a resolved ambiguity or a
    changed condition. Rejected pairs become `rejected_round{n}` (round 1 -> regenerated once; round 2 -> final)."""
    cand = load_all()
    key = json.loads((RAW / f"judge_key_round{a.round}.json").read_text(encoding="utf-8"))
    verdicts = {j: json.loads((RAW / f"judge_{j}_round{a.round}.json").read_text(encoding="utf-8")) for j in ("codex", "claude")}
    for pid, k in key.items():
        vs = {}
        for j, v in verdicts.items():
            d = v.get(pid)
            if not isinstance(d, dict) or not all(isinstance(d.get(f), bool) for f in ("equivalent", "resolves_ambiguity", "changes_condition")):
                raise SystemExit(f"REFUSING: judge {j} has no complete verdict for {pid}")
            vs[j] = d
        ok = all(d["equivalent"] and not d["resolves_ambiguity"] and not d["changes_condition"] for d in vs.values())
        it = cand["items"][k]
        it.setdefault("judging", []).append({"round": a.round, "pair": pid, **{j: vs[j] for j in vs}, "accepted": ok})
        it["status"] = "accepted" if ok else f"rejected_round{a.round}"
    save_all(cand)
    from collections import Counter
    print("  status:", dict(Counter(it["status"] for it in cand["items"].values())))
    build_accepted(cand)


def build_accepted(cand: dict) -> None:
    """Pa1 = Kimi s1, Pa2 = Qwen s1 (a twice-rejected text keeps its original); Pu sets P1..P4 = Kimi s1, Qwen s1,
    Kimi s2, Qwen s2 for the important units (a unit with any text not accepted gets no variant in that set)."""
    acc = {"declared": bt.AUTHORITY, "Pa1": {}, "Pa2": {}, "Pu_P1": {}, "Pu_P2": {}, "Pu_P3": {}, "Pu_P4": {}, "kept_original": {}}
    for k, it in cand["items"].items():
        src, path = k.split("|", 1)
        if it["status"] != "accepted":
            continue
        target = {"kimi_s1": ["Pa1", "Pu_P1"], "qwen_s1": ["Pa2", "Pu_P2"], "kimi_s2": ["Pu_P3"], "qwen_s2": ["Pu_P4"]}[src]
        for t in target:
            if t.startswith("Pu_") and it["unit"] not in bt.IMPORTANT:
                continue
            acc[t][path] = it["paraphrase"]
    doc, *_ = bt.base()
    units = bt.text_units(doc)
    for s_ in ("Pa1", "Pa2"):
        acc["kept_original"][s_] = sorted(p_ for p_ in units if p_ not in acc[s_])
    bt.PARAPHRASES.write_text(json.dumps(acc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("  accepted sets:", {k_: len(v_) for k_, v_ in acc.items() if isinstance(v_, dict)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "packet", "accept"):
        p = sub.add_parser(name)
        p.add_argument("--round", type=int, default=1, choices=(1, 2))
        p.add_argument("--only", default="", help="generate: one generator and set, e.g. kimi_s1 (one writer per file)")
    a = ap.parse_args()
    {"generate": cmd_generate, "packet": cmd_packet, "accept": cmd_accept}[a.cmd](a)


if __name__ == "__main__":
    main()
