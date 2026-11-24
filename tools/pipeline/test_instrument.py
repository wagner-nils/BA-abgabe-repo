#!/usr/bin/env python3
"""Contract tests for the instrument renderer. See INSTRUMENT-DESIGN.md.

    .venv/bin/python3 tools/pipeline/test_instrument.py

The assertions are mostly MUTATION tests: change a source field, re-render, and
require the artifact to move — or to stay byte-identical, for an excluded field.
That is stronger than searching the finished prompt for strings, which collides
with legitimate text and cannot notice that a whole substantive section was
dropped by mistake.

Two things these tests deliberately do NOT cover, because they belong to M4's
production parser rather than to the renderer: the cross-field invariants the JSON
schema cannot express (empty relations iff the outcome says so, one evaluation per
fired frame, a note where one is required), and retry behaviour. The renderer's
manifest lists those invariants so M4 has to answer for them.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instrument as ins                                         # noqa: E402

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:                                      # pragma: no cover
    sys.exit("jsonschema is required. Run: uv sync")

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n          {detail}" if not cond and detail else ""))


def render(doc: dict, subtypes: dict, sha: str) -> tuple[str, str]:
    """The codebook sha is REQUIRED, not defaulted: it appears in the prompt header,
    so a mutation test that let it vary would compare two prompts that differ for a
    trivial reason and report a leak that is not there. The first run of M2 and M3
    failed exactly that way."""
    prompt = ins.render_prompt(doc, subtypes, sha)
    cfg = ins.canonical_json(ins.build_tool_config(doc, subtypes))
    return prompt, cfg


def _all_replacements(ins) -> list[str]:
    """Every hand-written replacement text, flattened.

    These are the one class of prompt content the sentinel coverage mutations cannot
    reach: they are authored here, not projected from the codebook, so a prior
    written INTO a replacement is invisible to a source mutation. A reviewer found
    exactly that -- "`other` requires a note, and should now be rare" -- in my own
    transformation text.
    """
    out: list[str] = []
    for t in ins.TRANSFORMS.values():
        out += [t["text"]] if isinstance(t["text"], str) else list(t["text"])
    for v in ins.XRULES.values():
        if v[0] == "transform":
            out.append(v[1]["text"])
    return out



# ═══════════════════════════════════════════════════════════════════════════
#  P — the certifier itself
# ═══════════════════════════════════════════════════════════════════════════
# Everything above tests the instrument. This tests the GATE: the cache-state
# classification, the response contract, the request-digest comparison, the verdict
# construction and the exit codes. None of it was reachable before -- it could only
# be exercised by a credentialed live run, which happens rarely, is not repeatable,
# and cannot be re-checked by a reader. So the parts of the pipeline most likely to
# be silently wrong were exactly the parts with no test.
#
# Every response below is a fake. That is the point: a real API cannot be asked to
# return an ambiguous cache accounting or a refusal on the second call.

def preflight_gate() -> None:
    import contextlib
    import io
    import tempfile
    import preflight as pf

    OK_INPUT = {"outcome": "off_topic", "relations": []}

    def usage(inp=120, out=40, read=0, write=0):
        return {"inputTokens": inp, "outputTokens": out,
                "cacheReadInputTokens": read, "cacheWriteInputTokens": write}

    def resp(u, *, stop="tool_use", content=None, tool_input=None):
        blocks = content if content is not None else [
            {"toolUse": {"name": ins.TOOL_NAME,
                         "input": OK_INPUT if tool_input is None else tool_input}}]
        return {"stopReason": stop, "usage": u, "output": {"message": {"content": blocks}}}

    class Fake:
        def __init__(self, plan):
            self.plan, self.seen = list(plan), []

        def converse(self, **kw):
            self.seen.append(kw)
            item = self.plan.pop(0) if self.plan else RuntimeError("fake client exhausted")
            if isinstance(item, BaseException):
                raise item
            return item

    consistency: list[tuple[str, bool]] = []

    def gate(plan, argv=(), factory=None, before_send=None):
        """Drive the whole CLI against a fake client. Returns (exit code, artifact, fake)."""
        out = Path(tempfile.mkdtemp(prefix="preflight_gate_")) / "r.json"
        fake = Fake(plan)
        old_argv = sys.argv
        sys.argv = ["preflight.py", "--out", str(out), *argv]
        code = None
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                pf.main(client_factory=factory or (lambda: fake), before_send=before_send)
        except SystemExit as e:
            code = e.code
        finally:
            sys.argv = old_argv
        data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        if data:
            consistency.append((data["certification_status"], data["certified"]))
        return code, data, fake

    def chk(data, cid):
        return next((c for c in data.get("checks", []) if c["id"] == cid), None)

    print("\n── P  the certifier: cache states, response contract, verdict, exit codes")

    # A clean cold start on Opus, whose 4,096-token minimum is the binding one.
    code, d, fake = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=6000))],
                         ["--model", "opus"])
    check("P1 a clean cold-start live run certifies and exits 0",
          code == 0 and d["certified"] and d["certification_status"] == "CERTIFIED",
          f"exit={code} status={d.get('certification_status')}")
    check("P1b the cold start is observed as cold, not warm",
          d["cache_initial_state"] == {"opus": "cold"}, str(d.get("cache_initial_state")))
    check("P1c the prefix size is read from call 2's read, not from a maximum",
          d["cache_prefix_tokens"] == {"opus": 6000}, str(d.get("cache_prefix_tokens")))
    check("P1d exactly three calls: reachability, then two production calls",
          len(fake.seen) == 3, f"{len(fake.seen)} calls")
    check("P1e the reachability probe carries no system prompt and no tools",
          "system" not in fake.seen[0] and "toolConfig" not in fake.seen[0],
          str(sorted(fake.seen[0])))
    check("P1f both production calls are byte-identical",
          pf.request_digest(fake.seen[1]) == pf.request_digest(fake.seen[2]))
    check("P1g check 13b confirms build == send", (chk(d, "13b") or {}).get("ok") is True,
          str((chk(d, "13b") or {}).get("detail"))[:120])
    check("P1h both phase summaries report their own phase, in the same vocabulary",
          d["offline_checks"] == "passed" and d["live_checks"] == "passed",
          f"{d.get('offline_checks')}/{d.get('live_checks')}")

    code, d, _ = gate([resp(usage()), resp(usage(read=6000)), resp(usage(read=6000))],
                      ["--model", "opus"])
    check("P2 a warm start is accepted and recorded as warm",
          code == 0 and d["cache_initial_state"] == {"opus": "warm"},
          f"exit={code} {d.get('cache_initial_state')}")

    # Both non-zero on call 1: the accounting contradicts itself.
    code, d, _ = gate([resp(usage()), resp(usage(write=6000, read=6000)),
                       resp(usage(read=6000))], ["--model", "opus"])
    check("P3 write AND read on call 1 is ambiguous, and check 5 fails",
          code == 1 and chk(d, "5")["ok"] is False
          and "cannot be interpreted" in chk(d, "5")["detail"],
          f"exit={code}")
    check("P3b check 6 is SKIPPED, not passed, when the accounting is uninterpretable",
          bool(chk(d, "6")["skipped"]) and chk(d, "6")["ok"] is None,
          f"ok={chk(d, '6')['ok']} skipped={bool(chk(d, '6')['skipped'])}")
    check("P3c the run reports ASSERTION_FAILED, not the mode it was invoked in",
          d["certification_status"] == "NOT_CERTIFIED_ASSERTION_FAILED",
          d.get("certification_status", ""))

    code, d, _ = gate([resp(usage()), resp(usage()), resp(usage())], ["--model", "opus"])
    check("P4 neither write nor read on call 1 is also ambiguous",
          code == 1 and "Both are zero" in chk(d, "5")["detail"],
          str(chk(d, "5"))[:120])

    code, d, _ = gate([resp(usage()), resp(usage(write=6000)),
                       resp(usage(read=6000, write=100))], ["--model", "opus"])
    check("P5 a second write on a byte-identical repeat fails check 5",
          code == 1 and "not stable" in chk(d, "5")["detail"], str(chk(d, "5"))[:120])

    code, d, _ = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=4000))],
                      ["--model", "opus"])
    check("P6 a partial read fails check 5",
          code == 1 and "partial read" in chk(d, "5")["detail"], str(chk(d, "5"))[:120])

    # The same numbers pass on Sonnet and fail on Opus. This is the whole reason the
    # minimum is per model rather than a single constant.
    plan = lambda: [resp(usage()), resp(usage(write=2000)), resp(usage(read=2000))]
    code_o, d_o, _ = gate(plan(), ["--model", "opus"])
    code_s, d_s, _ = gate(plan(), ["--model", "sonnet"])
    check("P7 a 2,000-token prefix fails Opus's minimum but clears Sonnet's",
          code_o == 1 and chk(d_o, "6")["ok"] is False
          and code_s == 0 and chk(d_s, "6")["ok"] is True,
          f"opus exit={code_o} sonnet exit={code_s}")
    check("P7b check 5 still passes when only the prefix size is too small",
          chk(d_o, "5")["ok"] is True, str(chk(d_o, "5"))[:100])

    # Codex round-3 finding #2: the second call's CONTENT used to be ignored.
    code, d, _ = gate([resp(usage()), resp(usage(write=6000)),
                       resp(usage(read=6000), content=[{"text": "I cannot help with that."}])],
                      ["--model", "opus"])
    check("P8 a refusal on the SECOND production call fails check 2",
          code == 1 and chk(d, "2")["ok"] is False and "call 2" in chk(d, "2")["detail"],
          str(chk(d, "2"))[:140])
    check("P8b and the cache accounting still passes, so only check 2 catches it",
          chk(d, "5")["ok"] is True)

    code, d, _ = gate([resp(usage()), resp(usage(write=6000)),
                       resp(usage(read=6000), stop="max_tokens")], ["--model", "opus"])
    check("P9 stopReason max_tokens on the second call fails check 3",
          code == 1 and chk(d, "3")["ok"] is False and "call 2" in chk(d, "3")["detail"],
          str(chk(d, "3"))[:140])

    code, d, _ = gate([resp(usage()), resp(usage(write=6000)),
                       resp(usage(read=6000), tool_input={"outcome": "nonsense"})],
                      ["--model", "opus"])
    check("P10 a schema-invalid tool input on the second call fails check 2",
          code == 1 and chk(d, "2")["ok"] is False, str(chk(d, "2"))[:140])

    # 13b's failure branch, staged as a REAL mutation in the window the check guards.
    # An earlier version of this test monkeypatched request_digest() to return
    # alternating hashes, which proved the mismatch was reported, not that a mutation
    # was detected. `before_send` exists for this one assertion.
    def mutate_messages(env):
        env["messages"] = [{"role": "user", "content": [{"text": "not the paragraph"}]}]

    code, d, fake = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=6000))],
                         ["--model", "opus"], before_send=mutate_messages)
    check("P11 a real mutation of `messages` after the build is caught by check 13b",
          code == 1 and chk(d, "13b")["ok"] is False
          and "mutated between build and send" in chk(d, "13b")["detail"],
          str(chk(d, "13b"))[:160])
    check("P11b the mutated request really did reach the fake client",
          fake.seen[1]["messages"][0]["content"][0]["text"] == "not the paragraph",
          str(fake.seen[1].get("messages"))[:90])
    check("P11c and it is a LIVE check, so the offline summary stays honest",
          chk(d, "13b")["phase"] == "live" and d["offline_checks"] == "passed",
          f"phase={chk(d, '13b')['phase']} offline={d['offline_checks']}")

    # The model is part of the envelope, so switching it is a changed request. Before
    # the envelope restructure the digest was taken over the body only and `modelId`
    # was added afterwards, so this was invisible.
    def swap_model(env):
        env["modelId"] = "eu.anthropic.claude-sonnet-4-6"

    code, d, fake = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=6000))],
                         ["--model", "opus"], before_send=swap_model)
    check("P11d a swapped modelId is caught, because the digest covers the envelope",
          code == 1 and chk(d, "13b")["ok"] is False, str(chk(d, "13b"))[:140])
    check("P11e and the swapped model is what the client was actually called with",
          fake.seen[1]["modelId"] == "eu.anthropic.claude-sonnet-4-6",
          str(fake.seen[1].get("modelId")))

    # Check 13's clauses, exercised by making the builder produce a request that is
    # wrong in a way the old three-part check could not see.
    real_builder = ins.build_request

    def leaky_builder(art, paragraph, max_tokens=1024):
        kw = real_builder(art, paragraph, max_tokens=max_tokens)
        kw["inferenceConfig"]["topP"] = 0.9      # the API rejects this beside temperature
        return kw

    ins.build_request = leaky_builder
    try:
        code, d, _ = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=6000))],
                          ["--model", "opus"])
    finally:
        ins.build_request = real_builder
    check("P11f check 13 fails when the builder adds topP to inferenceConfig",
          code == 1 and chk(d, "13")["ok"] is False
          and "inferenceConfig is exactly" in chk(d, "13")["detail"],
          str(chk(d, "13"))[:160])

    def nudging_builder(art, paragraph, max_tokens=1024):
        kw = real_builder(art, paragraph, max_tokens=max_tokens)
        kw["messages"][0]["content"][0]["text"] += "\n\nPrefer security frames."
        return kw

    ins.build_request = nudging_builder
    try:
        code, d, _ = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=6000))],
                          ["--model", "opus"])
    finally:
        ins.build_request = real_builder
    check("P11h check 13 fails when an unpinned instruction is appended to the user turn",
          code == 1 and chk(d, "13")["ok"] is False
          and "text block is exactly" in chk(d, "13")["detail"],
          str(chk(d, "13"))[:170])

    def chatty_builder(art, paragraph, max_tokens=1024):
        kw = real_builder(art, paragraph, max_tokens=max_tokens)
        kw["system"].append({"text": "Also, be concise."})
        return kw

    ins.build_request = chatty_builder
    try:
        code, d, _ = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=6000))],
                          ["--model", "opus"])
    finally:
        ins.build_request = real_builder
    check("P11g check 13 fails when a second system instruction is appended",
          code == 1 and chk(d, "13")["ok"] is False
          and "system is the pinned prompt" in chk(d, "13")["detail"],
          str(chk(d, "13"))[:160])

    # §4f (Codex review 2026-10-19, #2): an adapter that mutates the tool configuration in place keeps object identity;
    # check 13 must still fail on the changed content
    real_rf = ins.request_for

    def mutating_request_for(model_key, art, paragraph, *, max_tokens=1024):
        kw = real_rf(model_key, art, paragraph, max_tokens=max_tokens)
        kw["toolConfig"]["toolChoice"] = {"auto": {}}
        return kw

    ins.request_for = mutating_request_for
    try:
        code, d, _ = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=6000))], ["--model", "opus"])
    finally:
        ins.request_for = real_rf
        ins.load_artifacts.cache_clear() if hasattr(ins.load_artifacts, "cache_clear") else None
    check("P11i check 13 fails when the tool configuration is mutated in place (forced tool choice lost), though object identity holds",
          code == 1 and chk(d, "13")["ok"] is False and "toolConfig content" in chk(d, "13")["detail"],
          str(chk(d, "13"))[:200])

    # A request digest must see content, not object identity.
    a = ins.build_request(ins.load_artifacts(), "one", max_tokens=512)
    b = ins.build_request(ins.load_artifacts(), "two", max_tokens=512)
    check("P12 the digest distinguishes requests that differ only in the paragraph",
          pf.request_digest(a) != pf.request_digest(b))
    c = dict(reversed(list(a.items())))
    check("P12b and is insensitive to key order, so it cannot fail spuriously",
          pf.request_digest(a) == pf.request_digest(c))
    a2 = json.loads(json.dumps(a))
    a2["inferenceConfig"]["maxTokens"] = 4096
    check("P12c and catches a changed inferenceConfig, which an identity test cannot",
          pf.request_digest(a) != pf.request_digest(a2))

    code, d, fake = gate([], ["--offline"])
    check("P13 --offline makes no call, exits 3, and records live_attempted false",
          code == 3 and not fake.seen and d["live_attempted"] is False
          and d["certification_status"] == "NOT_CERTIFIED_LIVE_CHECKS_NOT_RUN",
          f"exit={code} calls={len(fake.seen)} attempted={d.get('live_attempted')}")

    # Codex round-3 finding #6: live_attempted came from the absence of --offline, so
    # a live invocation that aborted in the offline phase claimed to have tried.
    tmp = Path(tempfile.mkdtemp(prefix="preflight_pins_"))
    old_pins = pf.PINS_PATH
    pf.PINS_PATH = tmp / "pins.json"
    pf.PINS_PATH.write_text("{not json", encoding="utf-8")
    try:
        code, d, fake = gate([resp(usage())], ["--model", "opus"])
    finally:
        pf.PINS_PATH = old_pins
    check("P14 a malformed pin file is an offline failure, not an unreachable API",
          code == 1 and d["certification_status"] == "NOT_CERTIFIED_ASSERTION_FAILED"
          and d["offline_checks"] == "failed" and chk(d, "14")["ok"] is False,
          f"exit={code} status={d.get('certification_status')}")
    check("P14b and it does not claim a live attempt it never made",
          d["live_attempted"] is False and not fake.seen and d["live_checks"] == "not_run",
          f"attempted={d.get('live_attempted')} calls={len(fake.seen)} "
          f"live={d.get('live_checks')}")

    def boom():
        raise RuntimeError("no credentials configured")

    code, d, _ = gate([], ["--model", "opus"], factory=boom)
    check("P15 an unreachable API exits 2 and says so, and still writes the record",
          code == 2 and d["certification_status"] == "NOT_CERTIFIED_API_UNREACHABLE"
          and d["live_checks"] == "unreachable",
          f"exit={code} status={d.get('certification_status')}")

    code, d, _ = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=6000))],
                      ["--model", "opus", "--no-raw"])
    check("P16 a clean run without captured responses is NOT certified",
          code == 3 and d["certified"] is False
          and d["raw_responses"] == "NOT CAPTURED (--no-raw)",
          f"exit={code} certified={d.get('certified')}")

    code, d, _ = gate([resp(usage()), resp(usage(write=6000)), resp(usage(read=6000))],
                      ["--model", "opus", "--no-out"])
    check("P17 a clean run with no artifact is NOT certified, and writes none",
          code == 3 and not d, f"exit={code} artifact={bool(d)}")

    # The count is asserted, not merely reported. Written as "all {len} artifacts" it
    # adjusted itself: if one expected run stopped writing an artifact, the assertion
    # quietly tested a smaller set and still passed.
    EXPECTED_ARTIFACTS = 21          # +1 on 2026-10-19: P11i (§4f, mutated tool configuration)
    check(f"P18 every run that should have written an artifact did "
          f"({EXPECTED_ARTIFACTS} expected)",
          len(consistency) == EXPECTED_ARTIFACTS,
          f"{len(consistency)} written -- if this changed deliberately, update the constant")
    check("P18b certified and certification_status agreed on every artifact written",
          all((st == "CERTIFIED") == ok for st, ok in consistency),
          str([c for c in consistency if (c[0] == "CERTIFIED") != c[1]]))


def validator_identity() -> None:
    """D14/D18 one step later: the gate and the runner validate a response with ONE
    function, and it is the instrument's. A validator defined in preflight could
    drift from the one M4 calls without any test noticing."""
    import preflight as pf

    print("\n── U  one response validator, owned by the instrument")
    check("U1 preflight.validate_tool_use IS instrument.validate_tool_use",
          pf.validate_tool_use is ins.validate_tool_use)
    check("U2 preflight defines no validator of its own",
          "def validate_tool_use" not in Path(pf.__file__).read_text(encoding="utf-8"))

    art = ins.load_artifacts()
    doc, subtypes, _ = ins.load_codebook()
    ok, why = ins.validate_tool_use({"name": ins.TOOL_NAME,
                                     "input": {"outcome": "off_topic", "relations": []}},
                                    art, doc, subtypes)
    check("U3 a valid off_topic response passes name, schema and contract", ok, why)
    ok, why = ins.validate_tool_use({"name": "wrong_tool", "input": {}}, art, doc, subtypes)
    check("U4 a wrong tool name is refused before the schema is consulted",
          not ok and "toolUse.name" in why, why)
    ok, why = ins.validate_tool_use({"name": ins.TOOL_NAME, "input": "not an object"},
                                    art, doc, subtypes)
    check("U5 a non-object input is refused", not ok and "expected object" in why, why)
    ok, why = ins.validate_tool_use({"name": ins.TOOL_NAME,
                                     "input": {"outcome": "off_topic"}},
                                    art, doc, subtypes)
    check("U6 a schema-invalid object is refused at the schema layer",
          not ok and why.startswith("schema:"), why)
    ok, why = ins.validate_tool_use(
        {"name": ins.TOOL_NAME,
         "input": {"outcome": "off_topic",
                   "relations": [{"technology": "solar", "elements": [],
                                  "evaluations": {}, "comparison_target": "none"}]}},
        art, doc, subtypes)
    check("U7 a schema-valid but contract-invalid object is refused at the contract layer",
          not ok and why.startswith("relational contract:"), why)



def content_contract() -> None:
    """Codex W1 round 1, #7 and #8: exactly one toolUse block, no reasoning block; the
    stored cache TTL is read from cacheDetails when a write is observed."""
    import contextlib, io, json, tempfile
    from pathlib import Path
    import preflight as pf

    OK_INPUT = {"outcome": "off_topic", "relations": []}

    def usage(inp=400, out=60, read=0, write=0, details=None):
        u = {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": read,
             "cacheWriteInputTokens": write}
        if details is not None:
            u["cacheDetails"] = details
        return u

    def resp(content=None, u=None):
        blocks = content if content is not None else [{"toolUse": {"name": ins.TOOL_NAME, "input": OK_INPUT}}]
        return {"stopReason": "tool_use", "usage": u or usage(), "output": {"message": {"content": blocks}}}

    class Fake:
        def __init__(self, plan): self.plan = list(plan)
        def converse(self, **kw):
            item = self.plan.pop(0)
            if isinstance(item, BaseException): raise item
            return item

    def gate(plan):
        out = Path(tempfile.mkdtemp(prefix="preflight_cc_")) / "r.json"
        old = sys.argv; sys.argv = ["preflight.py", "--out", str(out), "--model", "opus"]
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                pf.main(client_factory=lambda: Fake(plan))
        except SystemExit:
            pass
        finally:
            sys.argv = old
        return json.loads(out.read_text(encoding="utf-8"))

    def chk(d, cid): return next((c for c in d.get("checks", []) if c["id"] == cid), None)

    print("\n── C  content contract: one toolUse, no reasoning; stored TTL from cacheDetails")
    two = [{"toolUse": {"name": ins.TOOL_NAME, "input": OK_INPUT}},
           {"toolUse": {"name": ins.TOOL_NAME, "input": OK_INPUT}}]
    d = gate([resp(), resp(content=two, u=usage(write=6000)), resp(content=two, u=usage(read=6000))])
    check("C1 two toolUse blocks fail check 2 and the run does not certify",
          (chk(d, "2") or {}).get("ok") is False and not d["certified"], str(chk(d, "2")))
    reasoning = [{"reasoningContent": {"reasoningText": {"text": ""}}},
                 {"toolUse": {"name": ins.TOOL_NAME, "input": OK_INPUT}}]
    d = gate([resp(), resp(content=reasoning, u=usage(write=6000)), resp(content=reasoning, u=usage(read=6000))])
    check("C2 a reasoningContent block beside the call fails check 2 (thinking was not off)",
          (chk(d, "2") or {}).get("ok") is False and "reasoning" in str((chk(d, "2") or {}).get("detail")))
    d = gate([resp(), resp(u=usage(write=6000, details=[{"ttl": "5m", "inputTokens": 6000}])), resp(u=usage(read=6000))])
    check("C3 a write whose cacheDetails report 5m fails check 5 when 1h was requested",
          (chk(d, "5") or {}).get("ok") is False and "5m" in str((chk(d, "5") or {}).get("detail")))
    d = gate([resp(), resp(u=usage(write=6000, details=[{"ttl": "1h", "inputTokens": 6000}])), resp(u=usage(read=6000))])
    check("C4 a write whose cacheDetails report 1h passes and names the stored ttl",
          (chk(d, "5") or {}).get("ok") is True and "1h" in str((chk(d, "5") or {}).get("detail")) and d["certified"])
    probe_no_cache = {"stopReason": "end_turn", "usage": {"inputTokens": 10, "outputTokens": 2},
                      "output": {"message": {"content": [{"text": "ok"}]}}}
    d = gate([probe_no_cache, resp(u=usage(write=6000)), resp(u=usage(read=6000))])
    check("C5 absent cache counters on the reachability probe do not fail check 8",
          d["certified"] and not any(c["id"] == "8" and c.get("ok") is False for c in d["checks"]),
          str([c for c in d["checks"] if c["id"] == "8"]))
    union = [{"toolUse": {"name": ins.TOOL_NAME, "input": OK_INPUT}, "reasoningContent": {"reasoningText": {"text": ""}}}]
    d = gate([resp(), resp(content=union, u=usage(write=6000)), resp(content=union, u=usage(read=6000))])
    check("C7 a block carrying toolUse AND reasoningContent is malformed and fails check 2",
          (chk(d, "2") or {}).get("ok") is False and "malformed" in str((chk(d, "2") or {}).get("detail")))
    d = gate([resp(), resp(u=usage(write=6000, details=[None])), resp(u=usage(read=6000))])
    check("C8 malformed cacheDetails entries fail check 5 instead of passing as 'no TTL'",
          (chk(d, "5") or {}).get("ok") is False and "malformed cacheDetails" in str((chk(d, "5") or {}).get("detail")))
    d = gate([resp(), resp(u=usage(write=6000, details=[{"ttl": "1h", "inputTokens": 0}])), resp(u=usage(read=6000))])
    check("C9 a breakdown that does not add up to the write counter fails check 5",
          (chk(d, "5") or {}).get("ok") is False and "!= cacheWriteInputTokens" in str((chk(d, "5") or {}).get("detail")))
    d = gate([resp(), resp(u=usage(write=6000, details=[])), resp(u=usage(read=6000))])
    check("C11 an empty breakdown beside a 6,000-token write fails check 5 (gate and runner agree)",
          (chk(d, "5") or {}).get("ok") is False and "!= cacheWriteInputTokens" in str((chk(d, "5") or {}).get("detail")))
    d = gate([resp(), resp(u=usage(write=6000, details={"ttl": "1h"})), resp(u=usage(read=6000))])
    check("C12 a non-list breakdown fails check 5", (chk(d, "5") or {}).get("ok") is False and "not a list" in str((chk(d, "5") or {}).get("detail")))
    sel = ins.select_tool_use
    check("C10 non-dict and multi-key blocks are malformed, never read around",
          sel(["text"])[1].startswith("malformed") and sel(union)[1].startswith("malformed")
          and sel([{"toolUse": "not-an-object"}])[1].startswith("malformed"))
    check("C6 select_tool_use: none / one / multiple / reasoning are told apart",
          sel([])[1] == "none" and sel(two)[0] is None and "multiple" in sel(two)[1]
          and sel(reasoning)[0] is None and sel(reasoning)[1].startswith("reasoning")
          and sel([{"text": "x"}, two[0]])[0] == two[0]["toolUse"])


def main() -> None:
    doc, subtypes, cb_sha = ins.load_codebook()
    ins.check_projection(doc)
    prompt, cfg = render(doc, subtypes, cb_sha)
    cfg_obj = json.loads(cfg)
    schema = cfg_obj["tools"][0]["toolSpec"]["inputSchema"]["json"]

    print(f"\ncodebook v{doc['version']} · prompt {len(prompt.splitlines())} lines\n")

    # ── classification ───────────────────────────────────────────────────── #
    check("C1  every top-level field is classified",
          set(doc) <= set(ins.TOP_LEVEL), f"unclassified: {sorted(set(doc) - set(ins.TOP_LEVEL))}")
    unclassified = []
    for key, table in ins.FIELDS.items():
        node = doc.get(key)
        for item in (node if isinstance(node, list) else [node]):
            if isinstance(item, dict):
                unclassified += [f"{key}.{k}" for k in set(item) - set(table)]
    check("C2  every nested field is classified", not unclassified, f"{unclassified}")
    check("C3  every exclusion rule has a treatment",
          all(x["id"] in ins.XRULES for x in doc["exclusion_principles"]))
    check("C4  every transformation targets a path that exists",
          not (set(ins.TRANSFORMS) - ins._transform_paths(doc)),
          f"stale: {sorted(set(ins.TRANSFORMS) - ins._transform_paths(doc))}")
    check("C5  every transformation records reason, authority and text",
          all({"reason", "authority", "text"} <= set(t) for t in ins.TRANSFORMS.values()))

    # ── determinism ──────────────────────────────────────────────────────── #
    p2, c2 = render(doc, subtypes, cb_sha)
    check("R1  re-rendering the prompt is byte-identical", p2 == prompt)
    check("R2  re-rendering the tool config is byte-identical", c2 == cfg)

    # ── enums come from the codebook, in declaration order (D10) ─────────── #
    rel = schema["properties"]["relations"]["items"]
    check("E1  technology enum equals the codebook, in order",
          rel["properties"]["technology"]["enum"] == [t["id"] for t in doc["technologies"]])
    check("E2  element enum equals the codebook, in order",
          rel["properties"]["elements"]["items"]["enum"]
          == [e["id"] for e in doc["frame_elements"]])
    check("E3  evaluation enum equals the codebook, in order",
          set(rel["properties"]["evaluations"]["properties"]) == set(doc["frame_derivation"])
          and all(v["enum"] == [x["id"] for x in doc["evaluation"]["values"]]
                  for v in rel["properties"]["evaluations"]["properties"].values()))
    check("E4  comparison-target enum equals the codebook, in order",
          rel["properties"]["comparison_target"]["enum"] == doc["comparison_target"]["values"])
    check("E5  the two comment-only subtype enums are recovered",
          rel["properties"]["wind_siting"]["enum"] == subtypes["wind_siting"]
          and rel["properties"]["hydrogen_pathway"]["enum"] == subtypes["hydrogen_pathway"])
    check("E6  additionalProperties is false at every object level",
          schema["additionalProperties"] is False and rel["additionalProperties"] is False
          and rel["properties"]["evaluations"]["additionalProperties"] is False)
    check("E7  the tool call is forced",
          cfg_obj["toolChoice"] == {"tool": {"name": ins.TOOL_NAME}})

    # ── the prompt carries the definitions, not the names (D1) ───────────── #
    for tech in doc["technologies"]:
        check(f"P1  technology `{tech['id']}` appears with its definition",
              ins.flow(tech["definition"]) in prompt)
    missing_fires = [e["id"] for e in doc["frame_elements"]
                     if ins.flow(e["fires_when"]) not in prompt]
    check("P2  every element's fires_when appears", not missing_fires, f"{missing_fires}")
    missing_not = [e["id"] for e in doc["frame_elements"]
                   if "does_not_fire_when" in e
                   and ins.flow(e["does_not_fire_when"]) not in prompt]
    check("P3  every element's does_not_fire_when appears", not missing_not, f"{missing_not}")
    check("P4  the litmus test appears", ins.flow(doc["evaluation"]["litmus_test"]) in prompt)
    check("P5  the conservative tie-break appears (D4)",
          ins.flow(doc["evaluation"]["calibration_rule"]) in prompt)
    # No longer verbatim: D12 retired the outcome name it referenced, so it is
    # transformed. What must survive is the operative instruction -- do not invent
    # a relation to fill the schema.
    check("P6  the abstention instruction is present in the live vocabulary (D5)",
          "Do not invent a relation" in prompt
          and ins.flow(doc["prompt"]["abstention_instruction"]) not in prompt)
    kept = [x["rule"] for x in doc["exclusion_principles"]
            if ins.XRULES[x["id"]][0] == "include"]
    check("P7  every included exclusion rule appears verbatim",
          all(ins.flow(r) in prompt for r in kept))
    check("P8  every transformed field's replacement text appears",
          all(all(t in prompt for t in ([tr["text"]] if isinstance(tr["text"], str)
                                        else tr["text"]))
              for tr in ins.TRANSFORMS.values()))

    # ── nothing leaks that D7 and D8 exclude ─────────────────────────────── #
    body = prompt.split("## Output contract")[0]      # the contract mentions them on purpose
    leaks = [t for t in ("`speaker`", "`confidence`", "`sitting`", "tiebreak_used") if t in body]
    check("X1  no reference to a field the model is not asked for (D7)", not leaks, f"{leaks}")

    # PATH-LEVEL COVERAGE, not a phrase list. The first version searched for a
    # handful of strings and therefore missed two real prevalence priors that were
    # sitting in the prompt: F4b's "single largest code" / "hardest element", and
    # neutral's "this class will be large". A reviewer found them by reading the
    # rendered text. Every excluded or transformed path is now checked by mutation:
    # put a sentinel in the source, re-render, and require it not to appear.
    SENTINEL = "ZZSENTINELZZ"

    def leaks_when_mutated(mutate) -> bool:
        m = copy.deepcopy(doc)
        mutate(m)
        return SENTINEL in render(m, subtypes, cb_sha)[0]

    excluded_paths = {
        "calibration_evidence": lambda m: m.__setitem__("calibration_evidence", SENTINEL),
        "speaker": lambda m: m.__setitem__("speaker", SENTINEL),
        "confidence": lambda m: m.__setitem__("confidence", SENTINEL),
        "status": lambda m: m.__setitem__("status", SENTINEL),
        "supersedes": lambda m: m.__setitem__("supersedes", SENTINEL),
        "no_frame_present.why": lambda m: m["no_frame_present"].__setitem__("why", SENTINEL),
        "no_frame_present.note": lambda m: m["no_frame_present"].__setitem__("note", SENTINEL),
        "comparison_target.resolved_open_decision":
            lambda m: m["comparison_target"].__setitem__("resolved_open_decision", SENTINEL),
        "comparison_target.retired_in_v1_0":
            lambda m: m["comparison_target"].__setitem__("retired_in_v1_0", SENTINEL),
        "prompt.language": lambda m: m["prompt"].__setitem__("language", SENTINEL),
        "prompt.cache_point": lambda m: m["prompt"].__setitem__("cache_point", SENTINEL),
        "frame_elements[*].a0_evidence":
            lambda m: [e.__setitem__("a0_evidence", SENTINEL) for e in m["frame_elements"]],
        "frame_elements[*].note":
            lambda m: [e.__setitem__("note", SENTINEL) for e in m["frame_elements"]],
        "technologies[*].note":
            lambda m: [t.__setitem__("note", SENTINEL) for t in m["technologies"]],
    }
    for path, mut in excluded_paths.items():
        check(f"X2  excluded path `{path}` cannot reach the prompt", not leaks_when_mutated(mut))

    transformed_paths = {
        "perspective": lambda m: m.__setitem__("perspective", SENTINEL),
        "evaluation.excluded_value":
            lambda m: m["evaluation"].__setitem__("excluded_value",
                                                  {"id": "uncertain", "reason": SENTINEL}),
        "evaluation.values.neutral.definition":
            lambda m: [v.__setitem__("definition", SENTINEL)
                       for v in m["evaluation"]["values"] if v["id"] == "neutral"],
        "evaluation.values.mixed.definition":
            lambda m: [v.__setitem__("definition", SENTINEL)
                       for v in m["evaluation"]["values"] if v["id"] == "mixed"],
        "frame_elements.F4b.recall_warning":
            lambda m: [e.__setitem__("recall_warning", SENTINEL)
                       for e in m["frame_elements"] if e["id"] == "F4b"],
        "frame_elements.F6b.boundary_vs_F5a":
            lambda m: [e.__setitem__("boundary_vs_F5a", SENTINEL)
                       for e in m["frame_elements"] if e["id"] == "F6b"],
        "comparison_target.rules":
            lambda m: m["comparison_target"].__setitem__("rules", [SENTINEL]),
    }
    for path, mut in transformed_paths.items():
        check(f"X3  transformed path `{path}` is replaced, not passed through",
              not leaks_when_mutated(mut))

    for rid, (treatment, _) in ins.XRULES.items():
        if treatment in ("exclude", "transform"):
            def mut(m, rid=rid):
                for x in m["exclusion_principles"]:
                    if x["id"] == rid:
                        x["rule"] = SENTINEL
            check(f"X4  exclusion rule {rid} ({treatment}) does not pass through",
                  not leaks_when_mutated(mut))

    check("P9  the hard rule survives in the live vocabulary (it is load-bearing)",
          "that is F9 plus a note" in prompt
          and ins.flow(doc["no_frame_present"]["hard_rule"]) not in prompt)
    check("P10 every transformed exclusion rule's replacement appears",
          all(v[1]["text"] in prompt for v in ins.XRULES.values() if v[0] == "transform"))

    # No retired outcome name may survive anywhere in the prompt. D12 collapsed three
    # outcomes into two, and two fields still named the retired one -- the abstention
    # instruction and the no_frame_present hard rule -- so the prompt contradicted its
    # own output contract while the verbatim-appearance tests blessed it.
    retired = [n for n in ("no_frame_present", "relations_present", "technologies_present ")
               if n in prompt and n != "technologies_present "]
    check("P11 no retired outcome name survives in the prompt", not retired, f"{retired}")
    live = {"off_topic", "technologies_present"}
    check("P12 the prompt names exactly the live outcome values",
          all(o in prompt for o in live))
    check("P13 no prevalence prior in the hand-written replacement texts",
          not [t for t in _all_replacements(ins)
               if any(w in t for w in ("should now be rare", "will be large",
                                       "largest code", "hardest element",
                                       "overwhelmingly", "extremely common"))])

    # ── the pinned artifacts on disk are the ones we render ──────────────── #
    pins = ins.read_pins()
    v_ = str(doc["version"])
    for key, path, fresh in (("system_prompt", ins.OUT / f"system-prompt_v{v_}.txt", prompt),
                             ("tool_config", ins.OUT / f"tool-config_v{v_}.json", cfg)):
        state = ins.pin_state(pins, key, path, ins.sha256_text(fresh))
        check(f"K1  `{key}` is pinned, on disk, and matches the render", state == "match",
              f"state={state}")
    check("K2  the obsolete pins are gone",
          "tool_schema" not in pins and "rendered_prompt" not in pins)

    # ── the relational contract is enforced, not merely listed (D12) ─────── #
    ok = {"outcome": "technologies_present", "relations": [
        {"technology": "wind", "wind_siting": "offshore", "elements": ["F5a"],
         "evaluations": {"reliability": "negative"}, "comparison_target": "none"},
        {"technology": "solar", "elements": [], "evaluations": {},
         "comparison_target": "none"}]}
    check("V1  a valid response with a per-technology no-frame relation passes",
          not ins.validate_response(ok, doc, subtypes),
          "; ".join(ins.validate_response(ok, doc, subtypes)))
    bad_cases = {
        "off_topic with relations": ({"outcome": "off_topic", "relations": [
            {"technology": "solar", "elements": [], "evaluations": {},
             "comparison_target": "none"}]}, "off_topic"),
        "technologies_present with none": ({"outcome": "technologies_present",
                                            "relations": []}, "empty"),
        "a repeated technology": ({"outcome": "technologies_present", "relations": [
            {"technology": "solar", "elements": [], "evaluations": {},
             "comparison_target": "none"},
            {"technology": "solar", "elements": [], "evaluations": {},
             "comparison_target": "none"}]}, "repeated"),
        "a fired frame with no evaluation": ({"outcome": "technologies_present", "relations": [
            {"technology": "solar", "elements": ["F1a"], "evaluations": {},
             "comparison_target": "none"}]}, "no evaluation"),
        "an evaluation for an unfired frame": ({"outcome": "technologies_present",
                                                "relations": [
            {"technology": "solar", "elements": ["F1a"],
             "evaluations": {"costs": "negative", "jobs": "positive"},
             "comparison_target": "none"}]}, "did not fire"),
        "F9 without a note": ({"outcome": "technologies_present", "relations": [
            {"technology": "solar", "elements": ["F9"], "evaluations": {"other": "neutral"},
             "comparison_target": "none"}]}, "requires"),
        "comparison_target other with no note": ({"outcome": "technologies_present",
                                                  "relations": [
            {"technology": "solar", "elements": ["F1a"], "evaluations": {"costs": "neutral"},
             "comparison_target": "other"}]}, "comparison_note"),
        "wind_siting on solar": ({"outcome": "technologies_present", "relations": [
            {"technology": "solar", "wind_siting": "onshore", "elements": [],
             "evaluations": {}, "comparison_target": "none"}]}, "belongs to"),
        "wind without wind_siting": ({"outcome": "technologies_present", "relations": [
            {"technology": "wind", "elements": [], "evaluations": {},
             "comparison_target": "none"}]}, "missing"),
        "hydrogen without a pathway": ({"outcome": "technologies_present", "relations": [
            {"technology": "hydrogen", "elements": [], "evaluations": {},
             "comparison_target": "none"}]}, "missing"),
        "an unframed relation that still compares": ({"outcome": "technologies_present",
                                                      "relations": [
            {"technology": "solar", "elements": [], "evaluations": {},
             "comparison_target": "coal"}]}, "must be `none`"),
    }
    for name, (payload, needle) in bad_cases.items():
        probs = ins.validate_response(payload, doc, subtypes)
        check(f"V2  the validator rejects {name}",
              any(needle in p for p in probs), f"got {probs}")

    # ── mutation: normative in, excluded out ─────────────────────────────── #
    m = copy.deepcopy(doc)
    m["technologies"][0]["definition"] = "MUTATED DEFINITION"
    check("M1  changing a normative field changes the prompt",
          render(m, subtypes, cb_sha)[0] != prompt)
    m = copy.deepcopy(doc)
    m["calibration_evidence"]["rows_coded"] = 999999
    check("M2  changing an excluded field leaves the prompt byte-identical",
          render(m, subtypes, cb_sha)[0] == prompt)
    m = copy.deepcopy(doc)
    m["frame_elements"][0]["a0_evidence"] = "MUTATED PROVENANCE"
    check("M3  changing a provenance field leaves the prompt byte-identical",
          render(m, subtypes, cb_sha)[0] == prompt)
    m = copy.deepcopy(doc)
    m["frame_elements"][0]["fires_when"] = "MUTATED FIRES"
    check("M4  changing an element's fires_when changes the prompt",
          render(m, subtypes, cb_sha)[0] != prompt)
    m = copy.deepcopy(doc)
    m["technologies"].append({"id": "fusion", "label": "Fusion", "definition": "x"})
    check("M5  adding a technology changes the schema enum",
          "fusion" in render(m, subtypes, cb_sha)[1])
    m = copy.deepcopy(doc)
    m["unknown_future_field"] = "x"
    try:
        ins.check_projection(m)
        check("M6  an unclassified new field is refused", False, "accepted silently")
    except SystemExit:
        check("M6  an unclassified new field is refused", True)

    # ── the schema accepts the states the instrument defines (D12) ───────── #
    v = Draft202012Validator(schema)
    fixtures = {
        "off_topic": {"outcome": "off_topic", "relations": []},
        "a named but unframed technology": {"outcome": "technologies_present", "relations": [
            {"technology": "solar", "elements": [], "evaluations": {},
             "comparison_target": "none"}]},
        "single technology": {"outcome": "technologies_present", "relations": [
            {"technology": "solar", "elements": ["F1a"],
             "evaluations": {"costs": "negative"}, "comparison_target": "none"}]},
        "multi technology": {"outcome": "technologies_present", "relations": [
            {"technology": "wind", "wind_siting": "offshore", "elements": ["F5a", "F5b"],
             "evaluations": {"reliability": "mixed"}, "comparison_target": "coal"},
            {"technology": "hydrogen", "hydrogen_pathway": "green", "elements": ["F9"],
             "evaluations": {"other": "positive"}, "comparison_target": "none",
             "note": "safety risk, no listed frame fits"}]},
    }
    for name, doc_ in fixtures.items():
        errs = sorted(v.iter_errors(doc_), key=lambda e: e.path)
        check(f"S1  the schema accepts a {name} response", not errs,
              "; ".join(e.message for e in errs[:2]))
    rejects = {
        "an unknown technology": {"outcome": "technologies_present", "relations": [
            {"technology": "fusion", "elements": [], "evaluations": {},
             "comparison_target": "none"}]},
        "an unknown element": {"outcome": "technologies_present", "relations": [
            {"technology": "solar", "elements": ["F99"], "evaluations": {},
             "comparison_target": "none"}]},
        "an unknown frame in evaluations": {"outcome": "technologies_present", "relations": [
            {"technology": "solar", "elements": ["F1a"], "evaluations": {"vibes": "positive"},
             "comparison_target": "none"}]},
        "an invented top-level field": {"outcome": "off_topic", "relations": [],
                                        "confidence": 3},
        "an unknown outcome": {"outcome": "unsure", "relations": []},
    }
    for name, bad in rejects.items():
        check(f"S2  the schema rejects {name}", bool(list(v.iter_errors(bad))))

    # ── the shared request builder (M4 and preflight both call it) ──────── #
    art = ins.load_artifacts()
    check("B1  the loaded prompt is byte-identical to the render", art.prompt == prompt)
    check("B2  the loaded tool config parses to the rendered object",
          art.tool_config == cfg_obj)
    check("B3  the tool-config digest is over the FILE bytes, not a re-serialisation",
          art.tool_config_sha256 == ins.sha256_text(
              (ins.ROOT / "tools" / ins.read_pins()["tool_config"]["path"])
              .read_text(encoding="utf-8")))

    req = ins.build_request(art, "Ein Absatz über Windkraft an Land.")
    check("B4  system[0] is the pinned prompt, unmodified — no nonce, no run tag",
          req["system"][0] == {"text": art.prompt})
    check("B5  the cache point sits immediately after the prompt block",
          list(req["system"][1]) == ["cachePoint"]
          and req["system"][1]["cachePoint"]["ttl"] == ins.CACHE_TTL)
    check("B6  toolConfig IS the loaded object, not a rebuild",
          req["toolConfig"] is art.tool_config)
    check("B7  temperature 0 and no topP (setting both is a ValidationException)",
          req["inferenceConfig"]["temperature"] == 0
          and "topP" not in req["inferenceConfig"])
    check("B8  the paragraph reaches the user turn and nothing else does",
          req["messages"][0]["role"] == "user"
          and "Windkraft an Land" in req["messages"][0]["content"][0]["text"])
    check("B9  two builds of the same paragraph are identical (no per-call variation)",
          ins.build_request(art, "x") == ins.build_request(art, "x"))
    check("B10 each model's own minimum is recorded, not the maximum",
          ins.MIN_CACHE_PREFIX == {"sonnet": 1024, "opus": 4096, "sonnet5": 1024, "opus5": 512, "haiku45": 4096, "sonnet45": 1024,
                                  "sonnet_req5": 1024, "opus_req5": 4096,   # §4k controls: the 4.6 models' own minimums
                                  "opus55": 512, "opus55_high": 512, "opus5_req55": 512, "opus5_rep": 512}   # §4m
          # §4f (2026-10-19): every model has a cache minimum unless its provider rejects the cache point
          and set(ins.MIN_CACHE_PREFIX) | set(ins.NO_CACHE_POINT) == set(ins.MODELS)
          and not set(ins.MIN_CACHE_PREFIX) & set(ins.NO_CACHE_POINT))

    # §4m (2026-11-08): the 5.5 request -- free choice of the one pinned tool, adaptive thinking at the declared effort,
    # 8,192 tokens, no temperature -- and the reasoning allowance that only these configurations carry
    r55 = ins.request_for("opus55", art, "probe")
    r5 = ins.request_for("opus5_rep", art, "probe")
    check("B10m the 5.5 request differs from the pinned one exactly by the declared tool choice, thinking, effort and cap",
          r55["toolConfig"]["toolChoice"] == {"auto": {}} and r55["toolConfig"]["tools"] is art.tool_config["tools"]
          and {k: v for k, v in r55["toolConfig"].items() if k != "toolChoice"}
              == {k: v for k, v in art.tool_config.items() if k != "toolChoice"}
          and art.tool_config["toolChoice"] == {"tool": {"name": ins.TOOL_NAME}}          # the pin is untouched
          and r55["inferenceConfig"] == {"maxTokens": 8192}
          and r55["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
          and ins.request_for("opus55_high", art, "probe")["additionalModelRequestFields"]["output_config"] == {"effort": "high"}
          and ins.request_for("opus5_req55", art, "probe") == {**r55}
          and r5 == ins.request_for("opus5", art, "probe")
          and ins.THINKING_ON == frozenset({"opus55", "opus55_high", "opus5_req55"}))
    _rb = [{"reasoningContent": {"reasoningText": {"text": "", "signature": "x"}}},
           {"toolUse": {"name": ins.TOOL_NAME, "input": {"outcome": "off_topic", "relations": []}}}]
    check("B10n a reasoning block beside the one call is accepted only with allow_reasoning; two calls never are",
          ins.select_tool_use(_rb)[0] is None and ins.select_tool_use(_rb, allow_reasoning=True)[1] == "ok"
          and ins.select_tool_use(_rb + [_rb[1]], allow_reasoning=True)[0] is None)

    # A fake client. This proves what the request builder WOULD SUBMIT -- it proves
    # nothing about what Bedrock accepts, and the wording matters: no live call is
    # made anywhere in this suite.
    captured: list[dict] = []

    class FakeClient:
        def converse(self, **kw):
            captured.append(kw)
            return {"stopReason": "tool_use", "usage": {},
                    "output": {"message": {"content": [{"toolUse": {
                        "name": ins.TOOL_NAME,
                        "input": {"outcome": "off_topic", "relations": []}}}]}}}

    FakeClient().converse(modelId="m", **ins.build_request(art, "probe"))
    sent = captured[0]
    check("B11 the builder submits the pinned prompt verbatim",
          sent["system"][0]["text"] == art.prompt)
    check("B12 the builder submits the pinned tool config verbatim",
          sent["toolConfig"] == json.loads(
              (ins.ROOT / "tools" / ins.read_pins()["tool_config"]["path"])
              .read_text(encoding="utf-8")))
    check("B13 the submitted request carries exactly the four Converse keys",
          set(sent) == {"modelId", "system", "messages", "toolConfig", "inferenceConfig"})

    # ---- the manifest staleness check: content yes, commit stamp no ----------
    # A commit that touches nothing this renderer reads used to make an ordinary run
    # refuse, because the manifest stamps the git HEAD it was rendered at. A refusal
    # that fires on a no-op trains the reader to run --write without looking.
    import collections
    m_text = (ins.OUT / f"manifest_v{ins.load_artifacts().codebook_version}.json").read_text(
        encoding="utf-8")

    def with_field(path_pair, value):
        d = json.loads(m_text, object_pairs_hook=collections.OrderedDict)
        d[path_pair[0]][path_pair[1]] = value
        return ins.canonical_json(d)

    check("N1 the staleness comparison ignores the commit stamp",
          ins.manifest_without_stamp(with_field(("provenance", "code_input_commit"), "d" * 40))
          == ins.manifest_without_stamp(m_text))
    check("N2 but it still sees a changed renderer digest",
          ins.manifest_without_stamp(with_field(("provenance", "renderer_sha256"), "0" * 16))
          != ins.manifest_without_stamp(m_text))
    check("N3 and a changed codebook digest",
          ins.manifest_without_stamp(with_field(("codebook", "sha256"), "0" * 64))
          != ins.manifest_without_stamp(m_text))
    check("N4 an unparseable manifest differs from anything rather than crashing",
          ins.manifest_without_stamp("{not json") != ins.manifest_without_stamp(m_text))
    check("N5 the stamp is still recorded, not silently dropped from the artifact",
          len(json.loads(m_text)["provenance"].get("code_input_commit", "")) >= 7,
          json.loads(m_text)["provenance"].get("code_input_commit", "<absent>"))

    # ---- what load_artifacts refuses -----------------------------------------
    # Every one of these has to raise InstrumentError specifically, not a bare
    # exception: preflight distinguishes "the pinned instrument is broken" (an
    # offline assertion failure, and alarming) from "the API was unreachable".
    def refuses(name, pins, fragment):
        try:
            ins.load_artifacts(pins)
            check(name, False, "it loaded anyway")
        except ins.InstrumentError as e:
            check(name, fragment in str(e), str(e)[:100])
        except Exception as e:                                   # noqa: BLE001
            check(name, False, f"raised {type(e).__name__}, not InstrumentError")

    bad = copy.deepcopy(ins.read_pins())
    bad["system_prompt"]["sha256"] = "0" * 64
    refuses("B14 load_artifacts refuses an artifact that fails its pin",
            bad, "does not match its pin")

    bad = copy.deepcopy(ins.read_pins())
    bad["tool_config"].pop("path")
    refuses("B15 load_artifacts refuses a pin with no path", bad, "needs both")

    # Each pin can be individually valid while the pair was rendered from two
    # different codebooks -- a prompt from v1.0 beside a schema from v1.1. The run
    # record would then name one codebook version for a request built from two.
    bad = copy.deepcopy(ins.read_pins())
    bad["tool_config"]["codebook_version"] = "1.1"
    refuses("B16 load_artifacts refuses artifacts rendered from different codebooks",
            bad, "different instrument versions")

    # The digest has to be over raw bytes. Hashing read_text() applies universal
    # newline conversion, so a CRLF copy decodes to the identical string, hashes
    # identically, and walks straight past the pin whose whole job is to catch a
    # changed file.
    # The fixture directory is OUTSIDE the repository. It used to be created under
    # tools/, which meant the last three assertions could not run at all in a
    # read-only checkout -- a test suite that needs write access to the tree it is
    # testing cannot be re-run by a reviewer. Absolute paths in a pin resolve as
    # themselves, so no test-only root resolver is needed.
    import shutil, tempfile
    tmp = Path(tempfile.mkdtemp(prefix="instrument_fixtures_"))
    try:
        real = ins.ROOT / "tools" / ins.read_pins()["system_prompt"]["path"]
        lf = real.read_bytes()
        crlf = lf.replace(b"\n", b"\r\n")
        (tmp / "prompt.txt").write_bytes(crlf)
        bad = copy.deepcopy(ins.read_pins())
        bad["system_prompt"]["path"] = str(tmp / "prompt.txt")
        bad["system_prompt"]["sha256"] = hashlib.sha256(lf).hexdigest()
        check("B17a the CRLF copy really does decode to the same text",
              (tmp / "prompt.txt").read_text(encoding="utf-8") == lf.decode("utf-8"))
        refuses("B17b load_artifacts catches a CRLF-only byte change",
                bad, "does not match its pin")

        (tmp / "cfg.json").write_text("{not json", encoding="utf-8")
        bad = copy.deepcopy(ins.read_pins())
        bad["tool_config"]["path"] = str(tmp / "cfg.json")
        bad["tool_config"]["sha256"] = hashlib.sha256(
            (tmp / "cfg.json").read_bytes()).hexdigest()
        refuses("B18 load_artifacts refuses a pinned tool config that is not JSON",
                bad, "not valid JSON")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



    validator_identity()
    content_contract()
    preflight_gate()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for n, d in FAILED:
            print(f"  FAILED: {n}  {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
