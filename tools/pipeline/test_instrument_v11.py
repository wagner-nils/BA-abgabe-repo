#!/usr/bin/env python3
"""Contract tests for instrument v1.1 (W3, 2026-10-14), the gate under a variant, and the W3 selector.

    .venv/bin/python3 tools/pipeline/test_instrument_v11.py

What is asserted: the v1.1 prompt is v1.0's prompt with only the Output-contract block replaced; the
tool schema stays inside Bedrock's documented strict subset; the nested payload maps losslessly into
the REGISTERED v1.0 contract (schema and relational rules); the relational rules reject what the
schema cannot express; the first request has the gate's check-13 shape; the feedback request is a
three-turn request carrying the prior call; the pins and artefacts load and v1.0's stay untouched; the
gate certifies a variant with a fake client and records which instrument it certified; the selector's
rule behaves as the protocol says.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instrument as ins                                         # noqa: E402
import instrument_v11 as v11                                     # noqa: E402
import preflight as pf                                           # noqa: E402
import run_extract as rx                                         # noqa: E402
import w3                                                        # noqa: E402
from fractions import Fraction                                   # noqa: E402
from jsonschema import Draft202012Validator                      # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"\n          {detail}" if not cond and detail else ""))


def errors(schema, payload):
    return [f"{'/'.join(str(x) for x in e.path) or '<root>'}: {e.message[:60]}"
            for e in sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))]


def walk_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from walk_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_objects(v)


def usage(inp=400, out=60, read=0, write=0):
    return {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": read, "cacheWriteInputTokens": write}


def resp(tool_input, *, u=None):
    return {"stopReason": "tool_use",
            "output": {"message": {"content": [{"toolUse": {"toolUseId": "t1", "name": ins.TOOL_NAME, "input": tool_input}}]}},
            "usage": u or usage()}


class Fake:
    def __init__(self, plan):
        self.plan, self.seen = list(plan), []

    def converse(self, **kw):
        self.seen.append(kw)
        item = self.plan.pop(0) if self.plan else RuntimeError("fake client exhausted")
        if isinstance(item, BaseException):
            raise item
        return item


def gate(plan, argv):
    out = Path(tempfile.mkdtemp(prefix="w3_gate_")) / "r.json"
    fake = Fake(plan)
    old = sys.argv
    sys.argv = ["preflight.py", "--out", str(out), *argv]
    code = None
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            pf.main(client_factory=lambda: fake)
    except SystemExit as e:
        code = e.code
    finally:
        sys.argv = old
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}), fake


GOOD = {"outcome": "technologies_present", "relations": [
    {"technology": "wind", "wind_siting": "offshore",
     "frames": [{"frame": "jobs", "elements": ["F2a", "F2c"], "evaluation": "positive"},
                {"frame": "reliability", "elements": ["F5a"], "evaluation": "negative"}],
     "comparison_target": "none"},
    {"technology": "solar", "frames": [], "comparison_target": "none"}]}
OFF = {"outcome": "off_topic", "relations": []}


def main() -> None:
    doc, st, cb_sha = ins.load_codebook()
    base = ins.render_prompt(doc, st, cb_sha)
    v10_schema = ins.build_tool_config(doc, st)["tools"][0]["toolSpec"]["inputSchema"]["json"]

    GOOD_T = {"outcome": "technologies_present", "relations": [
        {"technology": "wind", "technology_subtype": "offshore", "technology_versus": "none",
         "technology_frames": [{"frame": "jobs", "frame_elements": ["F2a", "F2c"], "frame_evaluation": "positive"},
                               {"frame": "reliability", "frame_elements": ["F5a"], "frame_evaluation": "negative"}]},
        {"technology": "solar", "technology_subtype": "none", "technology_versus": "none", "technology_frames": []}]}

    def render(variant):
        return v11.render_prompt(doc, st, cb_sha, variant == "E", variant == "T")

    def config(variant):
        return v11.build_tool_config(doc, st, variant == "E", variant == "T")

    print("\n-- V  the v1.1 / v1.2 renderer")
    for variant in v11.VARIANTS:
        expl, typed = variant == "E", variant == "T"
        p = render(variant)
        head10 = base.partition(v11.OUTPUT_CONTRACT_V10_HEAD)[0].splitlines()
        head11 = p.partition(v11.OUTPUT_CONTRACT_V10_HEAD)[0].splitlines()
        diff = [(a, b) for a, b in zip(head10, head11) if a != b]
        replaced = [(a, b) for a, b in diff if "rendered mechanically" not in a]
        n_expected = 5 if typed else 2
        new_field = "technology_frames" if typed else "frames"
        ok_repl = all((f"`{new_field}` list" in b and "`elements` list" not in b)
                      or (typed and "`technology_subtype`" in b and ("`wind_siting`" in a or "`hydrogen_pathway`" in a))
                      or (typed and "List them in `z_comparison_note`." in b and "List them in `comparison_note`." in a)
                      for a, b in replaced)
        check(f"V1{variant} everything before the Output contract is v1.0's, except the version line and the {n_expected} enumerated field-name replacements",
              len(head10) == len(head11) and len(diff) == n_expected + 1 and any(f"instrument v{v11.VERSION_OF[variant]}" in b for a, b in diff) and ok_repl,
              f"{len(diff)} differing line(s)")
        tail = p.partition(v11.OUTPUT_CONTRACT_V10_HEAD)[2]
        check(f"V2{variant} the Output contract describes the frames field, the demonstration and (E only) the explanation",
              f"`{new_field}`" in tail and v11.DEMO_PARAGRAPH in tail and ('"explanation"' in tail) == expl and ("Fill `explanation` first" in tail) == expl)
        cfg = config(variant)
        spec = cfg["tools"][0]["toolSpec"]
        schema = spec["inputSchema"]["json"]
        Draft202012Validator.check_schema(schema)
        txt = json.dumps(schema)
        check(f"V3{variant} strict flag, forced tool choice, strict-subset schema, additionalProperties false on every object",
              spec.get("strict") is True and cfg["toolChoice"] == {"tool": {"name": ins.TOOL_NAME}}
              and not any(f'"{k}"' in txt for k in ("uniqueItems", "oneOf", "if", "then", "maxItems", "minLength", "maxLength", "pattern"))
              and all(o.get("additionalProperties") is False for o in walk_objects(schema)))
        if typed:
            demo, good, off = v11.DEMO_RECORD_T, GOOD_T, OFF
            fkey, ekey, vkey = "technology_frames", "frame_elements", "frame_evaluation"
        else:
            demo = ({"explanation": "x", **v11.DEMO_RECORD} if expl else v11.DEMO_RECORD)
            good = ({"explanation": "x", **GOOD} if expl else GOOD)
            off = ({"explanation": "x", **OFF} if expl else OFF)
            fkey, ekey, vkey = "frames", "elements", "evaluation"
        vr = lambda d: v11.validate_response(d, doc, st, typed=typed)
        check(f"V4{variant} the demonstration, a nested record, an off-topic record and an empty-frames relation validate",
              not errors(schema, demo) and not errors(schema, good) and not errors(schema, off)
              and not vr(demo) and not vr(good) and not vr(off), f"{errors(schema, demo)} {errors(schema, good)} {vr(demo)} {vr(good)}")
        missing = copy.deepcopy(good); del missing["relations"][0][fkey][0][vkey]
        wrong_enum = copy.deepcopy(good); wrong_enum["relations"][0][fkey][0][ekey] = ["F99"]
        no_expl = copy.deepcopy(GOOD)
        check(f"V5{variant} a frame without evaluation and an unknown element are schema errors; explanation required iff E",
              bool(errors(schema, missing)) and bool(errors(schema, wrong_enum)) and (typed or bool(errors(schema, no_expl)) == expl))
        norm = v11.normalise(good, typed=typed)
        check(f"V6{variant} the normaliser maps a valid record into the v1.0 schema and contract, losslessly",
              not errors(v10_schema, norm) and not ins.validate_response(norm, doc, st)
              and norm["relations"][0]["elements"] == ["F2a", "F2c", "F5a"]
              and norm["relations"][0]["evaluations"] == {"jobs": "positive", "reliability": "negative"}
              and norm["relations"][0]["wind_siting"] == "offshore" and norm["relations"][0]["comparison_target"] == "none"
              and norm["relations"][1]["elements"] == [] and norm["relations"][1]["evaluations"] == {} and "wind_siting" not in norm["relations"][1]
              and "explanation" not in norm and norm["outcome"] == "technologies_present", json.dumps(norm)[:200])
        if typed:
            wrong_frame = copy.deepcopy(good); wrong_frame["relations"][0][fkey][0][ekey] = ["F3a"]   # a climate element under jobs
            props = schema["properties"]["relations"]["items"]["properties"]
            fprops = schema["$defs"]["frame"]["anyOf"][0]["properties"]
            check("V7T an element under another frame is a SCHEMA error (per-frame enums); the field names sort into the reading order",
                  bool(errors(schema, wrong_frame)) and sorted(props) == ["technology", "technology_frames", "technology_subtype", "technology_versus", "z_comparison_note", "z_note"]
                  and sorted(fprops) == ["frame", "frame_elements", "frame_evaluation"]
                  and json.dumps(v11.DEMO_RECORD_T, sort_keys=True) == json.dumps(v11.DEMO_RECORD_T, sort_keys=True))
            sub_cases = {"onshore on solar": ("solar", "onshore"), "none on wind": ("wind", "none"), "green on wind": ("wind", "green"), "offshore on hydrogen": ("hydrogen", "offshore")}
            bad_ok = True
            for name, (tech, val) in sub_cases.items():
                d = copy.deepcopy(GOOD_T); d["relations"][0]["technology"] = tech; d["relations"][0]["technology_subtype"] = val
                if tech == "solar":
                    d["relations"].pop(1)
                pr = vr(d); ids = rx.contract_rule_ids(pr)
                bad_ok = bad_ok and bool(pr) and "subtype_incompatible" in ids
            d = copy.deepcopy(GOOD_T); d["relations"][0]["technology_subtype"] = "unspecified"
            d2 = copy.deepcopy(GOOD_T); d2["relations"][0]["technology"] = "hydrogen"; d2["relations"][0]["technology_subtype"] = "green"
            check("V8T subtype applicability: a non-applicable or foreign value is rejected with the public id subtype_incompatible; unspecified on wind and green on hydrogen pass",
                  bad_ok and not vr(d) and not vr(d2) and v11.normalise(d2, typed=True)["relations"][0]["hydrogen_pathway"] == "green")

    print("\n-- R  the relational rules the schema cannot express (rejected, never repaired)")
    cases = {
        "repeated technology": lambda d: d["relations"].append(copy.deepcopy(d["relations"][0])),
        "duplicate frame": lambda d: d["relations"][0]["frames"].append(copy.deepcopy(d["relations"][0]["frames"][0])),
        "element under another frame": lambda d: d["relations"][0]["frames"][0]["elements"].append("F3a"),
        "element listed twice": lambda d: d["relations"][0]["frames"][0]["elements"].append("F2a"),
        "subtype on the wrong technology": lambda d: d["relations"][1].__setitem__("wind_siting", "onshore"),
        "subtype missing": lambda d: d["relations"][0].pop("wind_siting"),
        "comparison target without a frame": lambda d: d["relations"][1].__setitem__("comparison_target", "coal"),
        "F9 without a note": lambda d: d["relations"][0]["frames"].append({"frame": "other", "elements": ["F9"], "evaluation": "neutral"}),
        "comparison_target other without a note": lambda d: d["relations"][0].__setitem__("comparison_target", "other"),
        "off_topic with relations": lambda d: d.__setitem__("outcome", "off_topic"),
        "technologies_present without relations": lambda d: d.__setitem__("relations", []),
    }
    expected_ids = {
        "repeated technology": "repeated_technology", "duplicate frame": "repeated_frame",
        "element under another frame": "element_frame_mismatch", "element listed twice": "repeated_element",
        "subtype on the wrong technology": "subtype_misplaced", "subtype missing": "subtype_missing",
        "comparison target without a frame": "comparison_without_frame", "F9 without a note": "note_required",
        "comparison_target other without a note": "comparison_note_required",
        "off_topic with relations": "off_topic_with_relations", "technologies_present without relations": "present_without_relations",
    }
    for name, mutate in cases.items():
        d = copy.deepcopy(GOOD); mutate(d)
        problems = v11.validate_response(d, doc, st)
        ids = rx.contract_rule_ids(problems)
        check(f"R  {name} is a relational problem with the public rule id {expected_ids[name]}",
              bool(problems) and expected_ids[name] in ids and "other" not in ids, f"problems={problems} ids={ids}")

    print("\n-- Q  requests: first request shape, feedback request shape")
    for variant in v11.VARIANTS:
        b = v11.bind(variant)
        art = b.load_artifacts()
        req = b.build_request(art, "PROBE", max_tokens=1024)
        clauses = (set(req) == {"system", "messages", "toolConfig", "inferenceConfig"}
                   and req["system"] == [{"text": art.prompt}, {"cachePoint": {"type": "default", "ttl": ins.CACHE_TTL}}]
                   and req["toolConfig"] is art.tool_config
                   and req["messages"] == [{"role": "user", "content": [{"text": "Paragraph:\nPROBE"}]}]
                   and req["inferenceConfig"] == {"maxTokens": 1024, "temperature": 0})
        check(f"Q1{variant} the first request has exactly the gate's check-13 shape", clauses)
        prior = {"toolUseId": "tu_1", "name": ins.TOOL_NAME, "input": GOOD_T if variant == "T" else GOOD}
        fb = b.build_feedback_request(art, "PROBE", prior, ["relation 0 (wind): technology repeated"], max_tokens=1024)
        roles = [m["role"] for m in fb["messages"]]
        tr = fb["messages"][-1]["content"][0].get("toolResult") or {}
        check(f"Q2{variant} the feedback request is user/assistant/user, carries the prior call, an error toolResult and the message",
              roles == ["user", "assistant", "user"]
              and fb["messages"][1]["content"][0]["toolUse"] == {"toolUseId": "tu_1", "name": ins.TOOL_NAME, "input": prior["input"]}
              and tr.get("toolUseId") == "tu_1" and tr.get("status") == "error"
              and tr["content"][0]["text"].startswith(v11.FEEDBACK_HEAD) and "technology repeated" in tr["content"][0]["text"]
              and fb["system"] == req["system"] and fb["toolConfig"] is art.tool_config and fb["inferenceConfig"] == req["inferenceConfig"])
        try:
            b.build_feedback_request(art, "PROBE", {"toolUseId": "tu_2", "name": "wrong_tool", "input": GOOD}, ["x"]); refused = False
        except ins.InstrumentError:
            refused = True
        check(f"Q2b{variant} a prior call to another tool name is not rewritten but refused (the runner retries identically)", refused)
        check(f"Q3{variant} the bound surface carries the runner's and the gate's names",
              all(hasattr(b, n) for n in ("load_artifacts", "build_request", "build_feedback_request", "validate_tool_use", "select_tool_use",
                                           "validate_cache_details", "MODELS", "REGION", "CACHE_TTL", "MIN_CACHE_PREFIX", "THINKING_CONTRACT",
                                           "normalise", "load_codebook", "PINS", "PROMPT_PATH_REL", "module_sha256"))
              and b.RETRY_POLICY_VERSION == "3" and b.INSTRUMENT_VERSION == v11.VERSION_OF[variant])

    print("\n-- P  pins and artefacts")
    for variant in v11.VARIANTS:
        b = v11.bind(variant)
        pins = b.read_pins()
        art = b.load_artifacts()
        prompt_path = ins.ROOT / b.PROMPT_PATH_REL
        check(f"P1{variant} the pinned artefacts load, hash to their pins, and name codebook v1.0 with the registered digest",
              art.prompt_sha256 == pins["system_prompt"]["sha256"] == hashlib.sha256(prompt_path.read_bytes()).hexdigest()
              and pins["codebook"]["sha256"] == ins.read_pins()["codebook"]["sha256"]
              and art.codebook_version == "1.0" and art.tool_config["tools"][0]["toolSpec"].get("strict") is True)
        fresh = render(variant)
        fresh_cfg = ins.canonical_json(config(variant))
        check(f"P2{variant} the artefacts on disk are what the renderer produces today",
              ins.sha256_text(fresh) == art.prompt_sha256 and ins.sha256_text(fresh_cfg) == art.tool_config_sha256)
        bad = copy.deepcopy(pins); bad["system_prompt"]["sha256"] = "0" * 64
        try:
            b.load_artifacts(bad); refused = False
        except ins.InstrumentError:
            refused = True
        check(f"P3{variant} a tampered pin refuses to load", refused)
    s_art, e_art, t_art = v11.bind("S").load_artifacts(), v11.bind("E").load_artifacts(), v11.bind("T").load_artifacts()
    check("P4 the three variants differ in prompt and schema",
          len({s_art.prompt_sha256, e_art.prompt_sha256, t_art.prompt_sha256}) == 3 and len({s_art.tool_config_sha256, e_art.tool_config_sha256, t_art.tool_config_sha256}) == 3)
    v10 = ins.load_artifacts()
    check("P5 the registered v1.0 artefacts are untouched (prompt 138cade3…, tool config a1e82607…)",
          v10.prompt_sha256.startswith("138cade3add5dcc4") and v10.tool_config_sha256.startswith("a1e82607f50e8c04")
          and ins.read_pins()["system_prompt"]["pinned_on"] == "2026-09-02")

    print("\n-- G  the gate under a variant (fake client)")
    plan = [resp(OFF), resp(OFF, u=usage(write=2000)), resp(OFF, u=usage(read=2000))]
    code, d, fake = gate(plan, ["--model", "sonnet", "--instrument", "v1.1S"])
    s_pins = v11.read_pins("S")
    check("G1 the gate certifies v1.1S against its own pins and says which instrument it certified",
          code in (0, None) and d.get("certified") is True and d.get("instrument") == "v1.1S"
          and d.get("tool_config_sha256") == s_pins["tool_config"]["sha256"] and d.get("system_prompt_sha256") == s_pins["system_prompt"]["sha256"]
          and d.get("provenance", {}).get("instrument_version") == "v1.1S"
          and d.get("provenance", {}).get("instrument_v11_sha256") == hashlib.sha256((Path(__file__).resolve().parent / "instrument_v11.py").read_bytes()).hexdigest(),
          f"code={code} status={d.get('certification_status')} failed={[c['id'] for c in d.get('checks', []) if c.get('ok') is False]}")
    sent = [k for k in fake.seen if "toolConfig" in k]
    check("G2 the production probes carried the strict v1.1S tool config and the v1.1S prompt",
          len(sent) == 2 and all(k["toolConfig"]["tools"][0]["toolSpec"].get("strict") is True for k in sent)
          and all(ins.sha256_text(k["system"][0]["text"]) == s_pins["system_prompt"]["sha256"] for k in sent))
    code, d, fake = gate([resp(OFF), resp(OFF, u=usage(write=2000)), resp(OFF, u=usage(read=2000))], ["--model", "sonnet", "--instrument", "v1.2S"])
    t_pins = v11.read_pins("T")
    check("G1T the gate certifies v1.2S against its own pins", d.get("certified") is True and d.get("instrument") == "v1.2S"
          and d.get("tool_config_sha256") == t_pins["tool_config"]["sha256"], f"status={d.get('certification_status')}")
    code, d, fake = gate([resp(OFF), resp(OFF, u=usage(write=2000)), resp(OFF, u=usage(read=2000))], ["--model", "sonnet"])
    check("G3 without --instrument the gate certifies v1.0 again (rebinding back works) and records no v1.1 digest",
          d.get("instrument") == "v1.0" and d.get("tool_config_sha256") == ins.read_pins()["tool_config"]["sha256"]
          and d.get("provenance", {}).get("instrument_v11_sha256") is None)
    bad_plan = [resp(OFF), resp(GOOD, u=usage(write=2000)), resp(GOOD, u=usage(read=2000))]   # nested payload under v1.0 is schema-invalid
    code, d, fake = gate(bad_plan, ["--model", "sonnet"])
    check("G4 a nested v1.1 payload does not pass the v1.0 gate (the validators are not mixed up)",
          d.get("certified") is False and any(c["id"] == "2" and c.get("ok") is False for c in d.get("checks", [])))
    pf.bind_instrument("v1.0")

    print("\n-- W  the selector's rule (exact fractions)")
    def cand(eligible, S, I, problems=(), scoreable=None, inst="x"):
        return {"eligible": eligible, "scoreable": eligible if scoreable is None else scoreable,
                "S": (str(Fraction(S)) if S is not None else None), "I": I, "problems": list(problems), "instrument": inst}
    c0 = cand(True, Fraction(13, 20), 1)
    check("W1 V1 ineligible -> not achieved, v1.0 retained",
          w3.decide(c0, cand(False, None, 0, ["3 terminal(s) not successful"]), cand(True, Fraction(9, 10), 0))["outcome"] == "not_achieved")
    d1 = w3.decide(c0, cand(True, Fraction(66, 100), 0, inst="v1.1S"), cand(True, Fraction(72, 100), 0, inst="v1.1E"))
    check("W2 V2 replaces V1 when the gain reaches the margin and invalidity is not higher",
          d1["outcome"] == "selected" and d1["selected"] == "v1.1E" and "V2 replaces V1" in d1["reason"])
    d1b = w3.decide(c0, cand(True, Fraction(65, 100), 0, inst="v1.1S"), cand(True, Fraction(70, 100), 0, inst="v1.1E"))
    check("W2b a gain of exactly 1/20 meets the inclusive margin (no floating-point shortfall)", d1b["selected"] == "v1.1E")
    d2 = w3.decide(c0, cand(True, Fraction(66, 100), 0, inst="v1.1S"), cand(True, Fraction(70, 100), 0, inst="v1.1E"))
    check("W3 a gain below the margin keeps V1", d2["outcome"] == "selected" and d2["selected"] == "v1.1S")
    d3 = w3.decide(c0, cand(True, Fraction(66, 100), 0, inst="v1.1S"), cand(True, Fraction(80, 100), 1, inst="v1.1E"))
    check("W4 a higher first-response invalidity blocks V2 whatever the gain", d3["selected"] == "v1.1S")
    d3b = w3.decide(c0, cand(True, Fraction(66, 100), 0, inst="v1.1S"), cand(False, Fraction(90, 100), 0, ["1 terminal(s) not successful"], inst="v1.1E"))
    check("W4b an ineligible V2 never replaces V1, whatever its score", d3b["selected"] == "v1.1S")
    d4 = w3.decide(cand(True, Fraction(70, 100), 0), cand(True, Fraction(60, 100), 0, inst="v1.1S"), cand(False, None, 0, ["x"]))
    check("W5 a selected candidate below a scoreable contemporaneous v1.0 -> no upgrade", d4["outcome"] == "no_upgrade" and d4["selected"] == "v1.0")
    d5 = w3.decide(cand(False, Fraction(70, 100), 2, ["2 terminal(s) not successful"], scoreable=True), cand(True, Fraction(60, 100), 0, inst="v1.1S"), cand(False, None, 0, ["x"]))
    check("W5b a V0 with parse failures is still scoreable and still guards", d5["outcome"] == "no_upgrade")
    d6 = w3.decide(cand(False, None, 0, ["aborted before extraction"], scoreable=False), cand(True, Fraction(60, 100), 0, inst="v1.1S"), cand(False, None, 0, ["x"]))
    check("W5c an unscoreable V0 -> selected with the guard recorded as not applied", d6["outcome"] == "selected" and d6["baseline_guard"].startswith("not applied"))
    ids = {"v0": "a1_w3_v10_x", "v1": "a1_w3_v11S_x", "v2": "a1_w3_v11E_x"}
    doc_ = w3.seal_document(ids, now="2026-01-01T00:00:00+00:00")
    check("W6 the seal names every code file (itself included), pin, artefact, the reference, the run ids and the protocol digest",
          set(doc_["code"]) == set(w3.CODE) and "w3.py" in doc_["code"] and set(doc_["pins"]) == set(w3.PINS) and len(doc_["artefacts"]) == 6
          and set(doc_["reference"]) == {"manifest", *w3.REFERENCE_FILES} and len(doc_["protocol_sha256"]) == 64
          and doc_["run_ids"] == ids and doc_["selector"]["margin"] == "1/20" and doc_["manifest_digest"])
    try:
        w3.permitted_run_id(doc_, "v1", "a1_w3_v11S_x_r1"); ok = False
    except SystemExit:
        ok = True
    try:
        w3.permitted_run_id(doc_, "v1", "a1_w3_v11S_x_r2"); ok2 = False
    except SystemExit:
        ok2 = True
    check("W7 a restart id is refused unless the sealed id aborted before its first call, and _r2 is never permitted",
          ok and ok2 and w3.permitted_run_id(doc_, "v1", "a1_w3_v11S_x") is None)
    check("W8 the seal carries each candidate's complete expected contract and fingerprint, and the evaluation settings",
          set(doc_["expected_contracts"]) == {"v0", "v1", "v2"}
          and doc_["expected_contracts"]["v1"]["contract"]["instrument_version"] == "1.1S"
          and doc_["expected_contracts"]["v0"]["contract"]["retry_policy_version"] == "2"
          and doc_["expected_contracts"]["v2"]["contract"]["retry_policy_version"] == "3"
          and doc_["expected_contracts"]["v1"]["contract"]["repeats"] == 3 and doc_["expected_contracts"]["v1"]["contract"]["dev"] is True
          and doc_["evaluation"] == {"threshold": 5, "B": w3.ev.DEFAULT_B, "seed": w3.ev.SEED}
          and len(doc_["expected_contracts"]["v1"]["fingerprint"]) == 64)

    print("\n-- X  the W4 rule (single candidate)")
    import w4
    def cand4(eligible, S, I, problems=(), scoreable=None, inst="x"):
        return {"eligible": eligible, "scoreable": eligible if scoreable is None else scoreable,
                "S": (str(Fraction(S)) if S is not None else None), "I": I, "problems": list(problems), "instrument": inst}
    check("X1 selected only if eligible, V0 scoreable, S >= S(V0) and I <= I(V0)",
          w4.decide(cand4(True, Fraction(66, 100), 0), cand4(True, Fraction(66, 100), 0))["outcome"] == "selected"
          and "tie" in w4.decide(cand4(True, Fraction(66, 100), 0), cand4(True, Fraction(66, 100), 0))["reason"]
          and w4.decide(cand4(True, Fraction(66, 100), 0), cand4(True, Fraction(70, 100), 1))["outcome"] == "retained"
          and w4.decide(cand4(True, Fraction(66, 100), 2), cand4(True, Fraction(60, 100), 0))["outcome"] == "retained")
    check("X2 an ineligible candidate -> retained; an unscoreable V0 -> comparison inconclusive",
          w4.decide(cand4(True, Fraction(66, 100), 0), cand4(False, Fraction(90, 100), 0, ["1 terminal(s) not successful"]))["outcome"] == "retained"
          and w4.decide(cand4(False, None, 0, ["aborted"], scoreable=False), cand4(True, Fraction(90, 100), 0))["outcome"] == "comparison_inconclusive")
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for n, d in FAILED:
            print(f"  FAILED: {n}  {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
