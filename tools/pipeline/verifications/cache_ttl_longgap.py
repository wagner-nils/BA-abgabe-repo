#!/usr/bin/env python3
"""Does a `ttl: 1h` cachePoint actually outlive the 5-minute default?

A TEST, not an observation script. It asserts, exits non-zero on failure, and
writes a timestamped JSON artifact. The first version printed SURVIVED/EXPIRED
and exited 0 either way, which made its advertised verdict unenforced -- the same
defect class as an assertion that passes without comparing anything.

Three levels of evidence exist and only the third settles the question:

  the price page lists a 1-hour tier    AWS bills for it        not behaviour
  preflight check 13: `30m` rejected    the field is validated  not persistence
  THIS TEST, with a 5-minute control    persistence            yes

The 5-minute arm is the negative control and it is what makes this a test: without
it, a read after the gap could equally mean the default TTL is longer than
documented. Both production models are exercised, because the plan's caching claim
covers both and a service-level property should not be assumed uniform.

Why it matters: section B.6 counts one cache WRITE per TTL window, and a write burns
daily quota where a read does not. On the 5-minute default a long run pays its
prefix into the quota roughly twelve times an hour instead of once.

Too slow for preflight.py (~7 minutes), so it lives here and is re-run when the
region, the model generation or the caching documentation changes.

    python tools/pipeline/verifications/cache_ttl_longgap.py
    python tools/pipeline/verifications/cache_ttl_longgap.py --gap 480

WHAT A PASS LICENSES. A pass at `--gap G` establishes persistence beyond G seconds
and nothing more: a hypothetical 10-minute TTL would pass a 410 s test and its
control identically. To support section B.6's "one cache write per hour", run with
`--gap 3300` or more. The output says which of the two conclusions the run earned.

Exit 0 = 1h survived the gap AND the 5m control expired, on every model.
Exit 1 = at least one assertion failed.
Exit 2 = the API could not be reached.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGION = "eu-central-1"
MODELS = {
    "sonnet": "eu.anthropic.claude-sonnet-4-6",
    "opus": "eu.anthropic.claude-opus-4-6-v1",
}
DEFAULT_GAP = 410          # > 300 s, so the 5-minute default must have expired
MIN_PREFIX_TOKENS = 1024   # both models cache above this; see preflight MIN_CACHE_PREFIX


def prefix(tag: str) -> str:
    """Unique per (model, ttl) so t0 is guaranteed to be a WRITE, not a read."""
    unit = ("Coding rule placeholder. Tick a frame element only where the paragraph "
            "itself supplies the evidence. ")
    return f"[{tag}] " + "".join(f"[{i:03d}] {unit}" for i in range(300))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gap", type=int, default=DEFAULT_GAP,
                    help=f"seconds between write and read (default {DEFAULT_GAP}; "
                         f"must exceed 300 or the control proves nothing)")
    ap.add_argument("--model", choices=[*MODELS, "both"], default="both")
    a = ap.parse_args()

    if a.gap <= 300:
        return sys.exit(f"--gap must exceed 300 s, got {a.gap}. Below the 5-minute default "
                        f"the control arm cannot expire, so the test cannot distinguish a "
                        f"1-hour TTL from the default.")

    try:
        import boto3
    except ImportError:
        print("boto3 is not installed (it is present in .venv).", file=sys.stderr)
        return 2

    client = boto3.client("bedrock-runtime", region_name=REGION)
    models = list(MODELS) if a.model == "both" else [a.model]

    def call(model_key: str, ttl: str, text: str) -> dict:
        r = client.converse(
            modelId=MODELS[model_key],
            messages=[{"role": "user", "content": [{"text": "Reply OK."}]}],
            system=[{"text": text}, {"cachePoint": {"type": "default", "ttl": ttl}}],
            inferenceConfig={"maxTokens": 8, "temperature": 0})
        u = r["usage"]
        return {"write": u.get("cacheWriteInputTokens", 0),
                "read": u.get("cacheReadInputTokens", 0)}

    started = datetime.now(timezone.utc)
    result: dict = {"started_at": started.isoformat(), "region": REGION,
                    "gap_seconds": a.gap, "models": {k: MODELS[k] for k in models},
                    "arms": {}, "failures": []}
    texts: dict[tuple[str, str], str] = {}

    try:
        for key in models:
            for ttl in ("1h", "5m"):
                tag = uuid.uuid4().hex
                texts[(key, ttl)] = prefix(tag)
                t0 = call(key, ttl, texts[(key, ttl)])
                result["arms"][f"{key}/{ttl}"] = {"t0": t0}
                print(f"t=0     {key:<7} ttl={ttl:<3} write={t0['write']:<6} "
                      f"read={t0['read']}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"could not reach the API: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"\nwaiting {a.gap} s (past the 5-minute default)...", flush=True)
    time.sleep(a.gap)
    print(flush=True)

    for key in models:
        for ttl in ("1h", "5m"):
            arm = result["arms"][f"{key}/{ttl}"]
            arm["t_gap"] = call(key, ttl, texts[(key, ttl)])
            print(f"t={a.gap:<5} {key:<7} ttl={ttl:<3} write={arm['t_gap']['write']:<6} "
                  f"read={arm['t_gap']['read']}", flush=True)

    # ---- assertions ------------------------------------------------------ #
    fails = result["failures"]
    for name, arm in result["arms"].items():
        key, ttl = name.split("/")
        t0, tg = arm["t0"], arm["t_gap"]

        if t0["write"] < MIN_PREFIX_TOKENS or t0["read"] != 0:
            fails.append(f"{name}: t0 was not a clean cache WRITE "
                         f"(write={t0['write']}, read={t0['read']}). The unique prefix "
                         f"failed, so nothing this arm reports is attributable.")
            arm["verdict"] = "invalid"
            continue

        if ttl == "1h":
            if tg["read"] == t0["write"] and tg["write"] == 0:
                arm["verdict"] = "survived"
            else:
                arm["verdict"] = "expired"
                fails.append(f"{name}: a 1-hour checkpoint did NOT survive {a.gap} s "
                             f"(write={tg['write']}, read={tg['read']}, expected "
                             f"read={t0['write']}). Section B.6's cache-write budget assumes "
                             f"one write per hour; on the 5-minute default it is ~12x that.")
        else:
            if tg["write"] >= MIN_PREFIX_TOKENS and tg["read"] == 0:
                arm["verdict"] = "expired (control behaved)"
            else:
                arm["verdict"] = "unexpectedly survived"
                fails.append(f"{name}: the 5-minute CONTROL survived {a.gap} s "
                             f"(read={tg['read']}). Without an expiring control, a read on "
                             f"the 1h arm does not distinguish a 1-hour TTL from a default "
                             f"that is simply longer than documented. This test cannot "
                             f"conclude anything while the control misbehaves.")

    result["ended_at"] = datetime.now(timezone.utc).isoformat()
    result["passed"] = not fails

    out = HERE / f"cache_ttl_longgap_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")

    print()
    for name, arm in result["arms"].items():
        print(f"  {name:<14} {arm['verdict']}")
    print(f"\nartifact: {out.relative_to(HERE.parent.parent.parent)}")

    if fails:
        print(f"\n{len(fails)} assertion(s) FAILED:", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1

    # What this run licenses, stated exactly. Round-4 finding #6: a 10-minute TTL
    # would pass a 410 s test and its control, so a 410 s pass does not establish a
    # one-hour lifetime -- only persistence beyond the gap. Section B.6's "one cache
    # write per hour" needs a gap near the claimed boundary.
    near_boundary = a.gap >= 3300
    print(f"\nPASS: on every model, a `ttl: 1h` checkpoint survived {a.gap} s and every "
          f"5-minute control expired.")
    print(f"      The ttl field is honoured, not merely accepted.")
    if near_boundary:
        print(f"      At {a.gap} s this is within 10 % of the claimed 3600 s lifetime, so it "
              f"does\n      support section B.6's one-cache-write-per-hour budget.")
    else:
        print(f"      CLAIM LIMIT: {a.gap} s establishes persistence PAST {a.gap} s and nothing\n"
              f"      more -- a 10-minute TTL would pass this test and its control identically.\n"
              f"      Section B.6's 'one cache write per hour' needs --gap >= 3300. Until then, budget\n"
              f"      cache writes as ceil(run_duration / {a.gap} s), which is conservative.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
