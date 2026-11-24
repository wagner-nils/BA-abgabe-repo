#!/usr/bin/env python3
"""One-off API contract probes — the questions that do not need re-asking per run.

Split out of `preflight.py` on plan-review round-4 finding #10: *"the thesis needs an
exact production-request smoke test, not a reusable Bedrock conformance framework."*
That was the right call. What stayed in preflight is what can break a specific run
and be invisible when it does. What moved here is what characterises the API itself:
true today, true next week, and worth a dated artifact rather than a gate.

Re-run this when the region, the model generation, or the SDK version changes —
and read the artifact rather than re-running it before every extraction.

    python tools/pipeline/verifications/api_contract.py
    python tools/pipeline/verifications/api_contract.py --model sonnet

Every probe asserts an ENUMERATED expected error code. A check that accepts any
exception as proof of a constraint reports PASS for a network blip, which is how
the first version of this code passed while proving nothing.

Exit 0 = the API behaves as PROTOTYPE-PLAN.md section A.4 describes.
Exit 1 = at least one claim in section A.4 is now wrong. Update the plan before running.
Exit 2 = the API could not be reached.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGION = "eu-central-1"
MODELS = {
    "sonnet": "eu.anthropic.claude-sonnet-4-6",
    "opus": "eu.anthropic.claude-opus-4-6-v1",
}

EXPECTED_VALIDATION = {"ValidationException"}
EXPECTED_CLIENT_SIDE = {"ParamValidationError"}
EXPECTED_UNAVAILABLE = {"ValidationException", "AccessDeniedException",
                        "ResourceNotFoundException", "UnknownOperationException"}


def err_code(exc: BaseException) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code")
        if code:
            return code
    return type(exc).__name__


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=[*MODELS, "both"], default="both")
    a = ap.parse_args()

    try:
        import boto3
    except ImportError:
        print("boto3 is not installed (it is present in .venv).", file=sys.stderr)
        return 2

    client = boto3.client("bedrock-runtime", region_name=REGION)
    models = list(MODELS) if a.model == "both" else [a.model]
    started = datetime.now(timezone.utc)
    out: dict = {"started_at": started.isoformat(), "region": REGION,
                 "models": {k: MODELS[k] for k in models}, "probes": [], "failures": []}

    def user(t: str):
        return [{"role": "user", "content": [{"text": t}]}]

    long_prefix = "".join(f"[{i:03d}] Coding rule placeholder text. " for i in range(300))

    for key in models:
        mid = MODELS[key]

        # A.4's three request rules. The REJECTIONS are the claim: a rule that has
        # silently become legal would change decoding without any error.
        probes = [
            ("temperature 0 is accepted", "accept",
             dict(inferenceConfig={"maxTokens": 8, "temperature": 0}), None,
             "section A.4 pins temperature 0 for the main run"),
            ("temperature + topP together is rejected", "reject",
             dict(inferenceConfig={"maxTokens": 8, "temperature": 0, "topP": 0.9}),
             EXPECTED_VALIDATION,
             "if both became legal, section A.4's request rules need rewriting"),
            ("temperature 0 + adaptive thinking is rejected", "reject",
             dict(inferenceConfig={"maxTokens": 2048, "temperature": 0},
                  additionalModelRequestFields={
                      "thinking": {"type": "enabled", "budget_tokens": 1024}}),
             EXPECTED_VALIDATION,
             "the determinism fork in section A.4 exists only because these are exclusive"),
            ("a seed parameter is rejected", "reject",
             dict(inferenceConfig={"maxTokens": 8, "temperature": 0, "seed": 42}),
             EXPECTED_CLIENT_SIDE | EXPECTED_VALIDATION,
             "every document saying 'fixed seed, temperature 0' describes something absent"),
            ("a bogus cachePoint ttl is rejected", "reject",
             dict(system=[{"text": long_prefix},
                          {"cachePoint": {"type": "default", "ttl": "30m"}}],
                  inferenceConfig={"maxTokens": 8, "temperature": 0}),
             EXPECTED_VALIDATION,
             "if ttl were ignored rather than validated, ttl:1h would be decoration and "
             "section B.6's cache-write budget would be wrong"),
        ]

        for name, mode, kw, expect, why in probes:
            rec = {"model": key, "probe": name, "mode": mode, "why_it_matters": why}
            try:
                r = client.converse(modelId=mid, messages=user("Reply OK."), **kw)
                if mode == "accept":
                    rec.update(ok=True, detail="accepted",
                               usage=r.get("usage"), stop_reason=r.get("stopReason"))
                else:
                    rec.update(ok=False,
                               detail=f"ACCEPTED, but section A.4 says it is rejected. {why}")
            except Exception as exc:  # noqa: BLE001
                code = err_code(exc)
                if mode == "accept":
                    rec.update(ok=False, error_code=code, error=str(exc)[:400],
                               detail=f"REJECTED with {code}, but section A.4 assumes accepted")
                else:
                    good = code in expect
                    if name.endswith("ttl is rejected"):
                        good = good and "ttl" in str(exc).lower()
                    rec.update(ok=good, error_code=code, error=str(exc)[:400],
                               detail=(f"rejected with {code}" if good else
                                       f"rejected with {code}, expected one of "
                                       f"{sorted(expect)} -- an unexpected error is not "
                                       f"evidence of the constraint"))
            out["probes"].append(rec)
            if not rec["ok"]:
                out["failures"].append(f"{key}: {name} -- {rec['detail']}")
            print(f"  [{'PASS' if rec['ok'] else 'FAIL'}] {key:<7} {name}")
            print(f"         {rec['detail'][:150]}")

        # CountTokens: a capability question, not a gate.
        rec = {"model": key, "probe": "CountTokens callability", "mode": "capability"}
        try:
            client.count_tokens(modelId=mid,
                                input={"converse": {"messages": user("probe")}})
            rec.update(ok=False, detail=(
                "CALLABLE -- section A.4 says it is not. Not a failure of the API, a failure of "
                "the plan: calls can be priced before they are made and section A.8's post-hoc-only "
                "accounting can be simplified. Update the plan, then re-run."))
            out["failures"].append(f"{key}: CountTokens is now callable -- update section A.4/A.8")
        except Exception as exc:  # noqa: BLE001
            code = err_code(exc)
            rec.update(ok=code in EXPECTED_UNAVAILABLE, error_code=code,
                       detail=(f"not callable ({code}) -- accounting stays post-hoc"
                               if code in EXPECTED_UNAVAILABLE else
                               f"failed with {code}, which is not a recognised 'unavailable' "
                               f"error; a transport failure says nothing about CountTokens"))
            if not rec["ok"]:
                out["failures"].append(f"{key}: CountTokens -- {rec['detail']}")
        out["probes"].append(rec)
        print(f"  [{'PASS' if rec['ok'] else 'FAIL'}] {key:<7} CountTokens callability")
        print(f"         {rec['detail'][:150]}")

    out["ended_at"] = datetime.now(timezone.utc).isoformat()
    out["passed"] = not out["failures"]
    art = HERE / f"api_contract_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    art.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"\nartifact: {art.name}")

    if out["failures"]:
        print(f"\n{len(out['failures'])} claim(s) in PROTOTYPE-PLAN.md section A.4 are now wrong:",
              file=sys.stderr)
        for f in out["failures"]:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nPASS: every request rule in section A.4 still holds, with the expected error codes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
