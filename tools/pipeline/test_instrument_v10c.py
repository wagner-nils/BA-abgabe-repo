#!/usr/bin/env python3
"""Tests of the §4l (5) context-parity variant v1.0C (pre-analysis specification, declared 2026-11-06).

    .venv/bin/python3 tools/pipeline/test_instrument_v10c.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate as ev                                            # noqa: E402
import instrument as v10                                         # noqa: E402
import instrument_v10c as v10c                                   # noqa: E402
import sample_contract as sc                                     # noqa: E402

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name)


def raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    return False


def main() -> None:
    art = v10.load_artifacts()
    b = v10c.bind()
    para = "Die Windkraft wird teurer, sagt der Verband."
    ctx = {"headline": "Windkraft unter Druck", "outlet": "taz", "lang": "de"}
    digest = b.bind_context({para: ctx})
    reg = v10.build_request(art, para)
    got = b.build_request(art, para)

    # C1: everything but the user text is the registered request, object for object where v1.0 shares objects
    same = copy.deepcopy(got)
    same["messages"][0]["content"][0]["text"] = reg["messages"][0]["content"][0]["text"]
    check("C1 apart from the user text the request equals the registered v1.0 request", same == reg)
    check("C1b the tool configuration is the pinned object itself", got["toolConfig"] is art.tool_config)
    check("C1c system prompt and cache point unchanged", got["system"] == reg["system"])
    check("C1d inference configuration unchanged (temperature 0, 1,024 tokens)",
          got["inferenceConfig"] == {"maxTokens": 1024, "temperature": 0})
    # C2: the declared user text, exactly
    want = f"Headline: Windkraft unter Druck\nOutlet: taz\nLanguage: German\n\nParagraph:\n{para}"
    check("C2 the user text is the three context lines, an empty line and the unchanged paragraph block",
          got["messages"][0]["content"][0]["text"] == want)
    # C3: an unbound text (the gate's probe) gets the declared probe context
    probe = "An invented probe paragraph about solar costs."
    pt = b.build_request(art, probe)["messages"][0]["content"][0]["text"]
    pc = v10c.PROBE_CONTEXT
    check("C3 an unbound text receives the declared PROBE_CONTEXT",
          pt == f"Headline: {pc['headline']}\nOutlet: {pc['outlet']}\nLanguage: English\n\nParagraph:\n{probe}")
    # C4: refusals
    check("C4a an empty field refuses", raises(lambda: b.bind_context({para: {**ctx, "headline": " "}}), v10.InstrumentError))
    check("C4b a multi-line field refuses", raises(lambda: b.bind_context({para: {**ctx, "outlet": "a\nb"}}), v10.InstrumentError))
    check("C4c an unknown language refuses", raises(lambda: b.bind_context({para: {**ctx, "lang": "fr"}}), v10.InstrumentError))
    check("C4d request_for refuses another model", raises(lambda: b.request_for("opus", art, para), v10.InstrumentError))
    check("C4e request_for for the registered model is build_request", b.request_for("sonnet", art, para) == got)
    # C5: the binding digest is stable and order-free
    b2 = v10c.bind()
    d2 = b2.bind_context({"x" * 20: {"headline": "h", "outlet": "o", "lang": "en"}, para: ctx})
    d3 = b2.bind_context({para: ctx, "x" * 20: {"headline": "h", "outlet": "o", "lang": "en"}})
    check("C5 the context digest does not depend on insertion order", d2 == d3 and d2 != digest)
    # C6: identity and constants
    check("C6a identity: version 1.0C, retry policy v2, the registered pins",
          b.INSTRUMENT_VERSION == "1.0C" and b.RETRY_POLICY_VERSION == "2" and b.PINS == v10.PINS)
    check("C6b sample_contract names the variant, status, authority, k and comparators",
          sc.CONTEXT_VARIANT == "v1.0C" and sc.CONTEXT_REPEATS == 1
          and sc.CONTEXT_COMPARATORS == {"A2": "a2_sonnet_20261013", "A3": "a3_sonnet_20261019"}
          and "§4l" in sc.CONTEXT_AUTHORITY)
    # C7: the evaluator binds the module digest to the variant
    base = {"execution_contract": {"instrument_version": "1.0C", "retry_policy_version": "2",
                                   "instrument_v11_sha256": "x" * 64}}
    check("C7a a v1.0C contract without the v1.0C module digest refuses",
          raises(lambda: ev.instrument_identity(base), ev.EvalError))
    ok = copy.deepcopy(base); ok["execution_contract"]["instrument_v10c_sha256"] = "y" * 64
    check("C7b with the digest the identity resolves to v1.0C", ev.instrument_identity(ok)["name"] == "v1.0C")
    bad = copy.deepcopy(ok); bad["execution_contract"]["retry_policy_version"] = "3"
    check("C7c v1.0C under retry policy v3 refuses", raises(lambda: ev.instrument_identity(bad), ev.EvalError))
    other = {"execution_contract": {"instrument_version": "1.0", "instrument_v10c_sha256": "y" * 64}}
    check("C7d a v1.0 contract carrying the v1.0C digest refuses", raises(lambda: ev.instrument_identity(other), ev.EvalError))

    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
