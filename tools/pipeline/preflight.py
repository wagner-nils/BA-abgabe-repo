#!/usr/bin/env python3
"""Preflight contract tests -- PROTOTYPE-PLAN.md section A.10.

Every load-bearing claim in the plan's sections A.4 and B.6 was originally a
sentence in a document, verified once by hand on 2026-08-29. A sentence cannot
fail a build, and the failures that matter here do not raise: a sub-minimum cache
checkpoint does not error -- inference succeeds and the prefix simply is not
cached, corrupting the cost claim and the schedule while every call returns 200.

WHAT THIS CERTIFIES -- read before trusting a PASS
--------------------------------------------------
It certifies THE PRODUCTION REQUEST. Every probe is assembled by
`instrument.build_request()` from the artifacts named in `pins.json`, the same
function and the same bytes M4 will use, and the pins are compared against the
files on disk before anything is sent. A green run therefore says: these two
models resolve; the pinned prompt and the pinned tool config are the ones the
runner will submit; forced tool use returns an object that satisfies the pinned
schema AND the codebook's relational contract; the pinned prefix caches and reads
back whole above each model's minimum; and `usage` is complete on every response.

This file used to hold a stand-in schema and a filler prefix, because the
codebook->prompt renderer did not exist. It does now (`instrument.py`), so the
stand-ins are gone. A preflight that assembles its own request certifies a
different object than the one that runs, which is a green light for nothing.

It still does NOT certify that the labels are CORRECT. Substantive agreement with
a human coder is validation (GUIDE-LLM E.1) and is measured on A2, not here. A
CERTIFIED run means the instrument is wired as documented, not that it is right.

SCOPE -- deliberately narrow (plan-review round-4 finding #10)
--------------------------------------------------------------
A smoke test for THE RUN ABOUT TO HAPPEN, not a Bedrock conformance framework. It
keeps only checks that can break a specific run and be invisible when they do.
The probes that characterise the API rather than the run -- illegal parameter
combinations, `seed`, `CountTokens`, the `ttl` enum -- live in
`verifications/api_contract.py`, which is run when the region or model generation
changes and leaves a dated artifact. Re-asking them before every extraction was
maintenance risk without a matching gain.

    python tools/pipeline/preflight.py                # both models
    python tools/pipeline/preflight.py --model sonnet
    python tools/pipeline/preflight.py --offline      # file checks only, no credentials
    python tools/pipeline/preflight.py --json         # also print the report

Exit 0 = every assertion passed, every pin was compared, and the artifact is on disk.
Exit 1 = at least one FAIL.
Exit 2 = a live run was asked for and the API could not be reached at all.
Exit 3 = assertions passed but the run is NOT CERTIFIED -- a pin was unasserted,
         capture was off, no artifact was written, or --offline was given.
"""

from __future__ import annotations

import argparse
import hashlib
import copy
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANNOTATION = HERE.parent / "annotation"
PINS_PATH = HERE / "pins.json"

sys.path.insert(0, str(HERE))
import instrument as ins                                        # noqa: E402
import instrument_v11 as ins11                                  # noqa: E402  W3 variants (2026-10-14)
import instrument_v10r3 as ins10r3                              # noqa: E402  §4b candidate v1.0r3 (2026-10-26)
import instrument_v10f as ins10f                                # noqa: E402  §4j form variant v1.0F (2026-10-30)
import instrument_v10c as ins10c                                # noqa: E402  §4l context variant v1.0C (2026-11-06)

INSTRUMENTS = ("v1.0", "v1.1S", "v1.1E", "v1.2S", "v1.0r3", "v1.0F", "v1.0C")   # v1.2S W4; v1.0r3 §4b; v1.0F §4j; v1.0C §4l
VARIANT_OF = {"v1.1S": "S", "v1.1E": "E", "v1.2S": "T"}
INSTRUMENT_NAME = "v1.0"

# Region, cache ttl and the per-model minimum prefix all come from `instrument`,
# which owns the request. Two copies of a constant that has to agree between the
# certifier and the runner is the same defect this whole rewiring exists to close.
REGION = ins.REGION
CACHE_TTL = ins.CACHE_TTL
MIN_CACHE_PREFIX = ins.MIN_CACHE_PREFIX

# Imported, not restated. The IDs used to live here only, so the runner could share
# `build_request()`, call a different model, and still claim this preflight covered its
# run. They now sit beside the builder in `instrument.py` and travel inside the request
# envelope that check 13b hashes.
MODELS = ins.MODELS

USAGE_FIELDS = ("inputTokens", "outputTokens",
                "cacheReadInputTokens", "cacheWriteInputTokens")

PROBE_PARAGRAPH = (
    "Der Ausbau der Windkraft an Land stockt: Genehmigungsverfahren dauern im "
    "Schnitt fast zwei Jahre, und Anwohner klagen gegen neue Standorte."
)


# --------------------------------------------------------------------------- #
# result plumbing
# --------------------------------------------------------------------------- #

@dataclass
class Check:
    id: str
    name: str
    silent: bool               # would this failure be invisible without the assertion?
    ok: bool | None = None
    detail: str = ""
    skipped: str = ""          # non-empty => not asserted, and the run is not certified
    # Which phase produced it. The run record reports the two phases separately, so
    # "the offline checks passed" cannot be true of a report in which a live check
    # failed -- the earlier version derived both lines from the same failure list.
    phase: str = "offline"


@dataclass
class Report:
    started_at: str
    region: str
    models: dict[str, str]
    tool_config_sha256: str
    checks: list[Check] = field(default_factory=list)
    raw: list[dict] = field(default_factory=list)
    # Recorded per model so the run record answers "did the production prefix cache,
    # from a cold or a warm start, and by what margin" without re-reading the checks.
    cache_state: dict = field(default_factory=dict)
    cache_tokens: dict = field(default_factory=dict)
    ended_at: str = ""
    # Set the moment the first live call is about to be made. Without it the run
    # record could not distinguish "the live phase ran and found nothing wrong" from
    # "the live phase was never entered because an offline check aborted it".
    live_ran: bool = False

    def add(self, c: Check) -> Check:
        self.checks.append(c)
        return c

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    @property
    def unasserted(self) -> list[Check]:
        return [c for c in self.checks if c.skipped]

    def failed_in(self, phase: str) -> list[Check]:
        return [c for c in self.failed if c.phase == phase]

    captured: bool = True        # False under --no-raw
    persisted_to: str = ""       # empty until the artifact is on disk

    @property
    def certified(self) -> bool:
        # Capture must be DURABLE. Round-4 finding #7: the suite held responses in
        # memory, exited on captured=True, and then discarded them -- printing
        # CERTIFIED while producing no run-record artifact at all.
        return (not self.failed and not self.unasserted
                and self.captured and bool(self.persisted_to))


@dataclass
class Pin:
    """A pinned artifact. `sent` is what the probe actually used, so the check can
    assert the loaded object IS the object under test rather than assuming it."""
    name: str
    ok: bool | None          # None when nothing was compared -- a SKIP has no verdict
    detail: str
    skipped: str = ""


def resolve_pin(pins: dict, name: str, root: Path, used_digest: str | None = None) -> Pin:
    """Load the pinned artifact, hash it, compare, and (if given) require that the
    digest the request builder actually used matches too.

    A pin with a sha256 but no loadable path is a FAILURE, not a pass. Round-2
    finding #2 was a check that hashed a constant and reported PASS; round-3
    finding #4 was the same defect deferred -- `if pin: ok = True` would have gone
    green the moment somebody typed a hash into the file. Both are closed here:
    nothing passes without a file on disk to compare against.
    """
    spec = pins.get(name) or {}
    pinned, rel = spec.get("sha256"), spec.get("path")

    if not pinned:
        return Pin(name, None, "",
                   skipped=f"`{name}` has no pinned sha256 in pins.json, so nothing is "
                           f"compared. Set both `path` and `sha256` to make this an "
                           f"assertion; until then the run is not certified.")
    if not rel:
        return Pin(name, False,
                   f"`{name}` pins sha256={pinned[:16]} but gives no `path`. A hash with "
                   f"nothing to load cannot be verified -- this is the deferred form of the "
                   f"fake-assertion defect and it fails rather than passes.")
    path = (root / rel).resolve()
    if not path.exists():
        return Pin(name, False,
                   f"`{name}` pins {rel} but that file does not exist. Nothing was hashed.")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != pinned:
        return Pin(name, False,
                   f"{rel} is sha256={actual[:16]} but pins.json says {pinned[:16]}. "
                   f"The artifact changed.")
    if used_digest is not None and used_digest != actual:
        return Pin(name, False,
                   f"{rel} matches its pin ({actual[:16]}), but the artifact the request "
                   f"builder loaded hashes to {used_digest[:16]}. The pinned file is not the "
                   f"object under test, so a green run would certify something else.")
    # Deliberately not "the object the probe sent": under --offline nothing is sent,
    # and a certification line that overstates what happened is the defect this file
    # exists to prevent.
    tail = " and is the artifact the production request builder loaded" \
        if used_digest is not None else ""
    return Pin(name, True, f"{rel} sha256={actual[:16]} == pin{tail}")


def request_digest(envelope: dict) -> str:
    """A digest of the WHOLE request envelope -- `modelId` included -- with key order
    normalised. This establishes CANONICAL CONTENT equivalence, not byte identity: two
    envelopes with the same content in a different key order hash the same, by design,
    so the check cannot fail for a reason that does not reach the API.

    Check 13 used to assert that the object the client received was the same object
    the builder returned. It is -- always: the builder hands back a mutable dict and
    the caller passes that same dict on, so the identity test was true by
    construction and an in-place edit to `messages`, `inferenceConfig` or the cache
    point survived it untouched. Object ancestry is not immutability. This hashes the
    content instead, once at build time and once immediately before the call.
    """
    return hashlib.sha256(
        json.dumps(envelope, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def provenance() -> dict:
    """Which code produced this verdict.

    A run record used to name the artifacts and the models but nothing about the gate
    itself, so a later reader could not tell which version of the checking logic had
    certified the run -- and the checking logic is exactly what these rounds of review
    kept changing.
    """
    def sha(path: Path) -> str:
        # The whole digest. Truncated to 16 characters, a field named `*_sha256` claimed
        # more evidence than it held -- fine for spotting an accidental change, not
        # enough to stand as the record of which code produced a verdict.
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return "unreadable"

    def git(*args: str) -> str:
        try:
            out = subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                                 text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else "unavailable"
        except (OSError, subprocess.SubprocessError):
            return "unavailable"

    try:
        import botocore
        boto_v = botocore.__version__
    except Exception:                                           # noqa: BLE001
        boto_v = "absent"
    dirty = git("status", "--porcelain")
    return {
        "git_commit": git("rev-parse", "HEAD"),
        # Recorded, not refused. A gate that only ran from a clean tree could not be
        # run while being fixed; a verdict produced from uncommitted code must simply
        # say so.
        "git_dirty": bool(dirty) if dirty != "unavailable" else "unavailable",
        "git_dirty_files": len(dirty.splitlines()) if dirty not in ("", "unavailable") else 0,
        "preflight_sha256": sha(HERE / "preflight.py"),
        "instrument_sha256": sha(HERE / "instrument.py"),
        # §4g (Codex round 1, #1): the Converse adapter for models behind another provider's API
        "adapter_sha256": sha(HERE / "inception_client.py"),
        # W3 (2026-10-14): which instrument this gate certified, and the variant module's digest under v1.1
        "instrument_version": INSTRUMENT_NAME,
        "instrument_v11_sha256": sha(HERE / "instrument_v11.py") if INSTRUMENT_NAME != "v1.0" else None,
        # §4b candidate (2026-10-26): the v1.0r3 module's digest under that configuration
        "instrument_v10r3_sha256": sha(HERE / "instrument_v10r3.py") if INSTRUMENT_NAME == "v1.0r3" else None,
        # §4j (2026-10-30): the form module's digest under v1.0F
        "instrument_v10f_sha256": sha(HERE / "instrument_v10f.py") if INSTRUMENT_NAME == "v1.0F" else None,
        # §4l (2026-11-06): the context module's digest under v1.0C
        "instrument_v10c_sha256": sha(HERE / "instrument_v10c.py") if INSTRUMENT_NAME == "v1.0C" else None,
        "uv_lock_sha256": sha(HERE.parent.parent / "uv.lock"),
        "python": sys.version.split()[0],
        "botocore": boto_v,
    }


def err_code(exc: BaseException) -> str:
    """The provider's error code, or the exception class name as a fallback."""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code")
        if code:
            return code
    return type(exc).__name__


def check_usage(u: dict, where: str) -> tuple[bool, str]:
    """Every successful response goes through here -- finding #7.

    `inputTokens` and `outputTokens` are required ints (a bool is not an int here; a str
    is not coerced), non-negative. The two cache counters are OPTIONAL in the AWS
    TokenUsage schema; when present they must be non-negative ints, when absent they are
    read as 0 and named in the detail so a reader can see the field was not returned
    (Codex W1 round 1, #9: requiring them on the uncached reachability probe would
    disqualify a usable model for a legitimately omitted field).
    """
    required = ("inputTokens", "outputTokens")
    optional = tuple(f for f in USAGE_FIELDS if f not in required)
    missing = [f for f in required if f not in u]
    if missing:
        return False, f"{where}: usage missing {missing}"
    present = [f for f in USAGE_FIELDS if f in u]
    bad_type = [f for f in present if not isinstance(u[f], int) or isinstance(u[f], bool)]
    if bad_type:
        return False, f"{where}: usage fields not int: " \
                      f"{ {f: type(u[f]).__name__ for f in bad_type} }"
    negative = [f for f in present if u[f] < 0]
    if negative:
        return False, f"{where}: usage fields negative: {negative}"
    absent = [f for f in optional if f not in u]
    return True, (f"{where}: usage ok" + (f" (absent, read as 0: {absent})" if absent else ""))


# The response validator is the instrument's, imported and not restated. It used to
# be defined here, which let the gate and the runner validate the same response with
# two different codes. `instrument.validate_tool_use()` checks the tool name, the
# PINNED schema, then the relational contract; the alias keeps the call site below
# readable and the test suite asserts the identity.
validate_tool_use = ins.validate_tool_use


def bind_instrument(name: str):
    """W3 (2026-10-14): certify a v1.1 variant instead of the registered instrument. Rebinds every
    module-level alias the checks read (`ins`, the pin file, the region, the cache minimum, the
    validator), so the same checks 14/11/7/12/13/2/3/5/6/13b run against the variant's pinned
    artefacts. v1.0 is the registered module, untouched."""
    global ins, PINS_PATH, REGION, CACHE_TTL, MIN_CACHE_PREFIX, MODELS, validate_tool_use, INSTRUMENT_NAME
    if name == "v1.0":
        import instrument as v10
        ins = v10
        # the pin path is a test seam (the suite points it at a malformed file); only undo a variant binding
        if Path(PINS_PATH).name.startswith("pins_v1."):
            PINS_PATH = HERE / "pins.json"
    elif name in VARIANT_OF:
        ins = ins11.bind(VARIANT_OF[name])
        PINS_PATH = ins.PINS
    elif name == "v1.0r3":
        ins = ins10r3.bind()                 # the registered artefacts (pins.json) under retry policy v3
        PINS_PATH = ins.PINS
    elif name == "v1.0F":
        ins = ins10f.bind()                  # §4j: the form artefacts (pins_v1.0F.json) under retry policy v3
        PINS_PATH = ins.PINS
    elif name == "v1.0C":
        ins = ins10c.bind()                  # §4l (5): the registered artefacts (pins.json), the context user turn
        PINS_PATH = ins.PINS
    else:
        raise SystemExit(f"unknown instrument {name!r}; choose one of {INSTRUMENTS}")
    REGION, CACHE_TTL, MIN_CACHE_PREFIX, MODELS = ins.REGION, ins.CACHE_TTL, ins.MIN_CACHE_PREFIX, ins.MODELS
    validate_tool_use = ins.validate_tool_use
    INSTRUMENT_NAME = name
    return ins


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #

class _NoCacheDone(Exception):
    """Internal: ends the cache section of the live checks for a §4f no-cache-point model."""


def run(models: list[str], report: Report, keep_raw: bool, offline: bool = False,
        client_factory=None, before_send=None) -> None:
    """`client_factory` exists so the test suite can drive the whole gate with a fake
    client. Without a seam, the cache-state classification, the response contract and
    the verdict construction could only ever be exercised by a credentialed live run,
    which means the parts of the gate most likely to be wrong were the parts never
    tested.

    `before_send` is the second test seam and exists for one assertion only: it is
    called with the complete envelope AFTER the build-time digest is taken and BEFORE
    the send-time one, so a test can stage a real mutation in the exact window check 13b
    guards. Without it, that check could only ever be tested by making the digest
    function lie, which tests the reporting and not the detection. It is `None` in every
    non-test path."""
    # A malformed pins.json is an OFFLINE failure of the instrument, and it has to say
    # so. Read unguarded, its JSONDecodeError escaped to main()'s catch-all and the
    # run was reported as "preflight could not reach the API" -- a network story for a
    # file that never parsed, and exit 2 instead of exit 1.
    pins: dict = {}
    try:
        raw_pins = PINS_PATH.read_text(encoding="utf-8") if PINS_PATH.exists() else "{}"
        pins = json.loads(raw_pins)
        if not isinstance(pins, dict):
            raise ValueError(f"the top level is {type(pins).__name__}, expected an object")
    except (OSError, ValueError) as exc:
        report.add(Check("14", "the pinned instrument loads and matches its pins",
                         silent=True, ok=False,
                         detail=f"{PINS_PATH.name} could not be read: {exc}. Nothing "
                                f"downstream can be verified without it."))
        report.add(Check("--", "live checks not run", silent=False, phase="live",
                         skipped="the pin file could not be read, so no request could be "
                                 "assembled and no call was attempted."))
        return

    # ---- offline phase: the pinned instrument ---------------------------- #
    # Loaded once, from the pinned files, and reused for every check and every
    # call. Not re-rendered: preflight's job is to verify the artifact on disk that
    # a reported run will use, and re-rendering would test the renderer twice while
    # leaving the pinned bytes unverified.
    c = report.add(Check("14", "the pinned instrument loads and matches its pins",
                         silent=True))
    art = None
    tool_snapshot = None
    cb_doc: dict = {}
    cb_subtypes: dict = {}
    try:
        art = ins.load_artifacts(pins)
        # §4f (Codex review 2026-10-19, #2): an independent copy of the pinned tool configuration, taken before any
        # request is built, so check 13 compares CONTENT -- an adapter that mutates the shared object in place would
        # otherwise keep object identity and pass
        tool_snapshot = copy.deepcopy(art.tool_config)
        cb_doc, cb_subtypes, _ = ins.load_codebook()
        c.ok = True
        c.detail = (f"prompt {len(art.prompt):,} chars sha={art.prompt_sha256[:16]}; "
                    f"tool config sha={art.tool_config_sha256[:16]}; "
                    f"codebook v{art.codebook_version}")
    except (ins.InstrumentError, SystemExit) as exc:
        # An unloadable or mismatched instrument is an OFFLINE assertion failure, not
        # an unreachable API. It used to escape `run()` as a bare exception and be
        # reported as "preflight could not reach the API" -- a far less alarming
        # thing than a malformed pinned artifact, and the wrong exit code.
        c.ok, c.detail = False, str(exc)

    # ---- 11 : the instrument is frozen AND matches its pin --------------- #
    c = report.add(Check("11", "codebook is frozen and matches its pinned hash", silent=True))
    try:
        frozen = [f for f in sorted((ANNOTATION / "codebook").glob("codebook-v*.yaml"))
                  if re.search(r"^frozen:\s*true\s*$", f.read_text(encoding="utf-8"), re.M)]
        if not frozen:
            c.ok, c.detail = False, "no codebook is marked `frozen: true`"
        else:
            pin = resolve_pin(pins, "codebook", HERE.parent)
            c.ok, c.detail, c.skipped = pin.ok, pin.detail, pin.skipped
            if pin.ok and (HERE.parent / pins["codebook"]["path"]).resolve() != frozen[-1].resolve():
                c.ok = False
                c.detail = (f"pins.json pins {pins['codebook']['path']}, but the newest "
                            f"FROZEN codebook is {frozen[-1].name}. The pin is stale.")
    except Exception as exc:  # noqa: BLE001
        c.ok, c.detail = False, f"{err_code(exc)}: {exc}"

    # ---- 7 : tool config matches its pin AND is what the builder loads --- #
    # Renamed from `tool_schema`: the pin covers the whole config -- tool name,
    # description, input schema and the forced toolChoice -- not an inner fragment
    # that each module then wraps for itself.
    c = report.add(Check("7", "tool config matches its pin and is the config the "
                              "builder loads", silent=True))
    pin = resolve_pin(pins, "tool_config", HERE.parent,
                      used_digest=art.tool_config_sha256 if art else None)
    c.ok, c.detail, c.skipped = pin.ok, pin.detail, pin.skipped

    # ---- 12 : the prompt under test IS the pinned production prompt ------ #
    c = report.add(Check("12", "the prompt under test IS the pinned production prompt",
                         silent=True))
    pin = resolve_pin(pins, "system_prompt", HERE.parent,
                      used_digest=art.prompt_sha256 if art else None)
    c.ok, c.detail, c.skipped = pin.ok, pin.detail, pin.skipped

    # ---- the single assembly point for every production request ---------- #
    # One function builds every production probe, and check 13 asserts on what that
    # function produced. Two independent paths -- one the check inspects, one the
    # client receives -- would let the certified object and the submitted object
    # drift apart silently, which is the whole failure mode being guarded against.
    nocache = set(models) & set(getattr(ins, "NO_CACHE_POINT", ()))     # §4f (2026-10-19)

    def production_kwargs(paragraph: str, max_tokens: int, key: str | None = None) -> dict:
        """The one place a production request body is assembled (per model: `instrument.request_for`, which is
        `build_request` itself except that the §4f no-cache models get it without the cache point)."""
        if key is not None and hasattr(ins, "request_for"):
            return ins.request_for(key, art, paragraph, max_tokens=max_tokens)
        return ins.build_request(art, paragraph, max_tokens=max_tokens)

    # ---- 13 : the request is built by the function M4 will call ---------- #
    c13 = report.add(Check("13", "the request comes from instrument.build_request()",
                           silent=True))
    if art is None:
        c13.skipped = "the pinned artifacts could not be loaded; nothing was built."
    else:
        # The WHOLE contract, not three of its parts. The earlier version asserted the
        # prompt, the tool config and the cache ttl -- so a builder that set
        # `temperature: 1`, added `topP`, wrapped the paragraph differently or appended a
        # second system instruction passed both 13 and 13b, which faithfully certified
        # the same wrong output.
        WANT_TOKENS = int(getattr(ins, "MAX_TOKENS", 1024))    # §4j: v1.0F declares 2,048; every other instrument 1,024

        def expected_user_text(paragraph: str) -> str:
            """The user block the pinned contract permits, written out here from the declared constants and never
            through the builder (§4l (5): v1.0C places three context lines -- for the gate's probe the declared
            PROBE_CONTEXT -- before the unchanged paragraph block)."""
            if INSTRUMENT_NAME == "v1.0C":
                pc = ins10c.PROBE_CONTEXT
                return (f"Headline: {pc['headline']}\nOutlet: {pc['outlet']}\n"
                        f"Language: {ins10c.LANGUAGE_NAME[pc['lang']]}\n\nParagraph:\n{paragraph}")
            return f"Paragraph:\n{paragraph}"

        def clauses_for(key: str | None) -> dict:
            """The whole contract for the request one model receives (§4f: without the cache point for the
            no-cache models, otherwise byte for byte the pinned request)."""
            req = production_kwargs(PROBE_PARAGRAPH, WANT_TOKENS, key=key)
            sysblk = req.get("system") or []
            msgs = req.get("messages") or []
            content = (msgs[0].get("content") or []) if len(msgs) == 1 else []
            inf = req.get("inferenceConfig") or {}
            nc = key in nocache
            want_sys = ([{"text": art.prompt}] if nc else
                        [{"text": art.prompt}, {"cachePoint": {"type": "default", "ttl": CACHE_TTL}}])
            # §4g: the declared adaptation table of the instrument is the ONLY permitted deviation in decoding
            adapt = (getattr(ins, "REQUEST_ADAPTATION", {}) or {}).get(key)
            # computed from the table here, never through the builder's own helper, so that a fault in the code under
            # certification cannot certify itself (agent review of §4k, 2026-11-01)
            want_inf = (({"maxTokens": adapt["max_tokens"]} if adapt["temperature"] is None
                         else {"maxTokens": adapt["max_tokens"], "temperature": adapt["temperature"]}) if adapt
                        else {"maxTokens": WANT_TOKENS, "temperature": 0})
            want_keys = {"system", "messages", "toolConfig", "inferenceConfig"} | (
                {"additionalModelRequestFields"} if adapt and adapt.get("additional") else set())
            return {
                ("exactly the four Converse keys plus the declared additional fields (§4g)" if adapt else
                 "exactly the four Converse keys"):
                    set(req) == want_keys,
                **({"the additional fields are exactly the declared ones (§4g)":
                        req.get("additionalModelRequestFields") == adapt.get("additional")} if adapt else {}),
                ("system is the pinned prompt only, no cachePoint (provider without extended-TTL caching, §4f)" if nc else
                 "system is the pinned prompt then one cachePoint, nothing else"):
                    sysblk == want_sys,
                **({"toolConfig is the pinned object": req.get("toolConfig") is art.tool_config,
                    "toolConfig content equals the pinned configuration as loaded (independent copy)":
                        req.get("toolConfig") == tool_snapshot}
                   if not (adapt and adapt.get("tool_choice")) else
                   # §4m: the pinned tool list itself, and the pinned configuration with exactly the declared choice
                   {"toolConfig carries the pinned tool list object": (req.get("toolConfig") or {}).get("tools")
                        is art.tool_config.get("tools"),
                    "toolConfig content equals the pinned configuration with toolChoice {auto: {}} (§4m), nothing else":
                        adapt.get("tool_choice") == "auto"
                        and req.get("toolConfig") == {**{k_: v_ for k_, v_ in tool_snapshot.items() if k_ != "toolChoice"},
                                                      "toolChoice": {"auto": {}}}}),
                "one user turn carrying one text block": len(msgs) == 1
                    and msgs[0].get("role") == "user" and len(content) == 1
                    and set(content[0]) == {"text"},
                # Exact equality, not containment. `PROBE_PARAGRAPH in text` would pass a
                # builder that appended an unpinned instruction -- "prefer security frames"
                # -- to the user turn, and checks 13 and 13b would both certify it.
                "the text block is exactly the wrapped paragraph, with nothing appended"
                + (" after the three declared context lines (§4l)" if INSTRUMENT_NAME == "v1.0C" else ""):
                    (content[0].get("text") if content else None)
                    == expected_user_text(PROBE_PARAGRAPH),
                (f"inferenceConfig is exactly the declared maxTokens {want_inf['maxTokens']} and "
                 + (f"temperature {want_inf['temperature']} (§4g)" if "temperature" in want_inf else "no temperature (§4k)")
                 if adapt else "inferenceConfig is exactly maxTokens plus temperature 0"):
                    inf == want_inf,
            }

        clauses = {}
        for key in (models or [None]):
            for name, ok in clauses_for(key).items():
                clauses[(f"[{key}] " if len(models) > 1 else "") + name] = ok
        broken = [k for k, v in clauses.items() if not v]
        c13.ok = not broken
        def thinking_words(key):
            # the thinking field each certified request carries (§4k: the fixed text said "no thinking block" for all)
            extra = ((getattr(ins, "REQUEST_ADAPTATION", {}) or {}).get(key) or {}).get("additional") or {}
            if "reasoning_effort" in extra:
                return f"reasoning_effort {extra['reasoning_effort']} (§4g)"
            if extra.get("thinking") == {"type": "disabled"}:
                return "thinking {type: disabled} (§4k)"
            if extra.get("thinking") == {"type": "adaptive"}:
                return f"thinking {{type: adaptive}}, effort {(extra.get('output_config') or {}).get('effort')} (§4m)"
            return "no thinking field"
        thinking = "; ".join(sorted({(f"[{k_}] " if len(models) > 1 else "") + thinking_words(k_) for k_ in (models or [None])}))
        c13.detail = (("FAILED: " + "; ".join(broken) + ". ") if broken else "") + (
            f"{len(clauses) - len(broken)}/{len(clauses)} clauses hold. No topP (the API "
            f"rejects it beside temperature); thinking: {thinking}; no extra system "
            f"instruction. Converse caches tools then system then messages, so the cached "
            f"prefix is the pinned config plus the pinned prompt.")

    if offline:
        report.add(Check("--", "live checks not run", silent=False, phase="live",
                         skipped="--offline: no API call was made. Every claim about model "
                                 "reachability, forced tool use, stopReason, cache behaviour "
                                 "and usage completeness is UNTESTED. The offline checks above "
                                 "establish only which request the production builder selects, "
                                 "not that anything was sent or accepted."))
        return

    # ---- live phase ------------------------------------------------------ #
    # boto3 is imported and the client constructed only here, AFTER every file check
    # has a verdict. A missing dependency or an unconfigured credential used to abort
    # before checks 7/11/12/13/14 ran, so the two failure modes that need no network
    # at all were reported as an unreachable API.
    if client_factory is None:
        try:
            import boto3
        except ImportError:
            sys.exit("boto3 is not installed (it is present in .venv).")

        external = getattr(ins, "EXTERNAL_API", {})
        if models and all(k in external for k in models):
            # §4g: a model behind another provider's API is gated through the same Converse-shaped adapter the runner uses
            import inception_client as icx

            def client_factory():
                return icx.InceptionConverseClient(endpoint=external[models[0]])
        else:
            def client_factory():
                return boto3.client("bedrock-runtime", region_name=REGION)

    client = client_factory()
    report.live_ran = True

    submitted: list[dict] = []
    usage_ok: dict[tuple[str, str], bool] = {}

    def converse(key: str, label: str, envelope: dict,
                 expect_digest: str | None = None) -> dict:
        """Every call, successful or not, lands in report.raw when capture is on.

        Round-4 finding #8: capturing only successes meant the evidence a check was
        computed from was a short string, while the plan claimed the run record held
        the API's actual responses.

        The send-side digest is taken HERE, on the last line before the client sees the
        envelope, and over the whole envelope including `modelId`. It used to be taken
        one frame up, in `production_converse`, while `modelId` was added afterwards --
        so a changed model, or any mutation inside this wrapper, was invisible to the
        check whose whole job is to notice a changed request.
        """
        at_send = request_digest(envelope)
        if expect_digest is not None:
            submitted.append({"model": key, "label": label,
                              "built_sha256": expect_digest, "sent_sha256": at_send})
        try:
            r = client.converse(**envelope)
        except Exception as exc:  # noqa: BLE001
            if keep_raw:
                report.raw.append({
                    "model": key, "label": label, "outcome": "exception",
                    "error_code": err_code(exc), "error_class": type(exc).__name__,
                    "error": str(exc)[:2000],
                    "provider_response": getattr(exc, "response", None),
                })
            raise
        ok, why = check_usage(r.get("usage", {}), label)
        usage_ok[(key, label)] = ok
        if not ok:
            report.add(Check("8", f"[{key}] usage complete on '{label}'", silent=True,
                             ok=False, detail=why, phase="live"))
        if keep_raw:
            report.raw.append({"model": key, "label": label, "outcome": "ok", "response": r})
        return r

    def production_converse(key: str, label: str, paragraph: str, max_tokens: int) -> dict:
        """The ONLY way a production request reaches the client.

        The envelope -- body plus `modelId` -- is digested here, at build time, and
        again inside `converse()` on the last line before the call. Anything that
        changed it in between shows up as two different digests even though it is the
        same object throughout; object ancestry is not immutability.
        """
        envelope = {"modelId": MODELS[key], **production_kwargs(paragraph, max_tokens, key=key)}
        built = request_digest(envelope)
        if before_send is not None:
            before_send(envelope)          # test seam; see run()'s docstring
        return converse(key, label, envelope, expect_digest=built)

    for key in models:
        m = f"[{key}]"

        # ---- 1 : model resolves and answers ------------------------------ #
        # Deliberately NOT a production request: it carries no system prompt and no
        # tools, so it separates "the model is unreachable" from "the production
        # request was rejected", and it cannot warm the production prefix's cache.
        c = report.add(Check("1", f"{m} inference profile resolves and returns a completion",
                             silent=False, phase="live"))
        try:
            r = converse(key, "reachability", {
                "modelId": MODELS[key],
                "messages": [{"role": "user",
                              "content": [{"text": "Reply with the single word OK."}]}],
                # sampling-neutral: the probe asks only "does a model answer"; a temperature
                # would turn a 5-series validation rejection into a false "unreachable"
                "inferenceConfig": {"maxTokens": 16}})
            c.ok, c.detail = True, f"{MODELS[key]}  stopReason={r['stopReason']}"
        except Exception as exc:  # noqa: BLE001
            c.ok, c.detail = False, f"{err_code(exc)}: {exc}"
            report.add(Check("--", f"{m} remaining checks not run", silent=False, phase="live",
                             skipped="model unreachable; nothing downstream is meaningful"))
            continue

        # ---- 2 + 3 + 5 + 6 : ONE production sequence, two identical calls - #
        # These used to be two blocks: checks 2/3 made a production call, then checks
        # 5/6 made two more. That meant preflight warmed the production prefix itself
        # and then measured the cache it had just filled, so the "cold start" branch
        # of check 5 was unreachable and the initial cache state was never observed.
        # Call 1 now serves checks 2/3 AND the initial-state measurement; call 2 is
        # its byte-identical repeat.
        # Both production calls are asserted, not just the first. The suite made two
        # byte-identical calls and read the second one's `usage` while ignoring its
        # CONTENT, so a run whose second response was a refusal or a truncated object
        # could still certify as long as the cache accounting added up.
        _adapt2 = (getattr(ins, "REQUEST_ADAPTATION", {}) or {}).get(key) or {}
        c2 = report.add(Check("2", (f"{m} the pinned tool, chosen freely under the declared adaptive thinking (§4m), is "
                                    f"called exactly once with a schema-valid toolUse on both production calls"
                                    if _adapt2.get("tool_choice") == "auto" else
                                    f"{m} forced tool use returns exactly one schema-valid toolUse "
                                    f"and no reasoning block on both production calls"),
                              silent=False, phase="live"))
        c3 = report.add(Check("3", f"{m} stopReason is tool_use on both production calls, "
                                   f"never max_tokens", silent=True, phase="live"))
        if key in nocache:
            # §4f: no cache point is sent, so there is no prefix to measure; asserted instead: the responses report
            # no cache traffic at all (a non-zero counter would mean the request was not the one certified)
            c5 = report.add(Check("5n", (f"{m} no cache point is sent and the Converse cache counters report no traffic "
                                         f"(the provider's automatic caching is reported separately)"
                                         if key in getattr(ins, "EXTERNAL_API", {}) else
                                         f"{m} no cache point is sent and no cache traffic is reported"),
                                  silent=True, phase="live"))
            c6 = None
        else:
            c5 = report.add(Check("5", f"{m} the production prefix caches, and reads back whole",
                                  silent=True, phase="live"))
            c6 = report.add(Check("6", f"{m} cached prefix clears this model's minimum "
                                       f"({MIN_CACHE_PREFIX[key]} tokens)",
                                  silent=True, phase="live"))
        try:
            if art is None:
                raise RuntimeError("the pinned instrument did not load; see check 14")

            # No nonce. The earlier design prepended a unique run tag so call 1 was
            # always a WRITE, on the reasoning that a rerun inside a live TTL would
            # otherwise see a READ and fail spuriously. That reasoning belonged to a
            # STAND-IN prefix, whose freshness was the thing being tested. Against a
            # PINNED production prefix a warm first read is not spurious -- it is
            # evidence that this exact prefix already has a live cache entry. So the
            # request stays byte-identical to production, the measurement carries no
            # nonce tokens, and both initial states are accepted.
            cap_ = int(getattr(ins, "MAX_TOKENS", 1024))
            r1 = production_converse(key, "production-call-1", PROBE_PARAGRAPH, cap_)
            r2 = production_converse(key, "production-call-2", PROBE_PARAGRAPH, cap_)
            u1, u2 = r1.get("usage", {}), r2.get("usage", {})

            # §4m: a reasoningContent block is accepted only where the declared adaptation asks for thinking -- read
            # from the table here, not from the builder's THINKING_ON, so that the code under certification does not
            # certify itself
            thinks = (((getattr(ins, "REQUEST_ADAPTATION", {}) or {}).get(key) or {}).get("additional") or {}).get(
                "thinking") == {"type": "adaptive"}

            def contract(r: dict, label: str) -> tuple[bool, str]:
                blocks = ((r.get("output") or {}).get("message") or {}).get("content") or []
                use, why_sel = ins.select_tool_use(blocks, allow_reasoning=thinks)   # one toolUse; reasoning only §4m
                if use is None:
                    return False, (f"{label}: not exactly one toolUse block ({why_sel}); content "
                                   f"keys={[list(b) for b in blocks if isinstance(b, dict)]}")
                ok, why = validate_tool_use(use, art, cb_doc, cb_subtypes)
                return ok, f"{label}: {why}"

            verdicts = [contract(r1, "call 1"), contract(r2, "call 2")]
            c2.ok = all(v for v, _ in verdicts)
            c2.detail = "  |  ".join(why for _, why in verdicts)

            stops = [("call 1", r1.get("stopReason")), ("call 2", r2.get("stopReason"))]
            c3.ok = all(sr == "tool_use" for _, sr in stops)
            c3.detail = ", ".join(f"{lbl} stopReason={sr}" for lbl, sr in stops)

            w1, rd1 = u1.get("cacheWriteInputTokens", 0), u1.get("cacheReadInputTokens", 0)
            w2, rd2 = u2.get("cacheWriteInputTokens", 0), u2.get("cacheReadInputTokens", 0)
            if key in nocache:
                report.cache_state[key] = "no cache point (§4f)"
                c5.ok = not any((w1, rd1, w2, rd2))
                auto = [((r_.get("additionalModelResponseFields") or {}).get("cached_tokens")) for r_ in (r1, r2)]
                c5.detail = (f"no cachePoint in the request; Converse counters call 1 write={w1} read={rd1}, call 2 write={w2} read={rd2}"
                             + (f"; provider-side automatic caching (outside the request, not certified here): cached_tokens={auto}"
                                if any(a_ is not None for a_ in auto) else "")
                             + ("" if c5.ok else " -- cache traffic without a cache point: the request is not the certified one"))
                raise _NoCacheDone()

            # Exactly one of write/read must be non-zero on call 1. Both means the
            # accounting is self-contradictory; neither means the checkpoint did
            # nothing at all. Either way the numbers cannot be read as a prefix size,
            # so nothing derived from them may be reported.
            state = ("cold" if w1 and not rd1 else
                     "warm" if rd1 and not w1 else "ambiguous")
            report.cache_state[key] = state

            if state == "ambiguous":
                c5.ok = False
                c5.detail = (
                    f"call 1 reports write={w1} and read={rd1}. "
                    + ("Neither a cold nor a warm start explains both being non-zero"
                       if (w1 and rd1) else
                       "Both are zero, so the checkpoint neither wrote nor read")
                    + ", so the cache accounting cannot be interpreted and nothing "
                      "downstream of it should be trusted.")
            elif not rd2:
                c5.ok = False
                c5.detail = (f"call 2 read nothing (write={w2}). The prefix is NOT caching "
                             f"for {key}: inference still succeeds, so this is invisible "
                             f"except here, while cost per item is wrong by roughly 10x and "
                             f"quota burn by roughly 18x on this model only.")
            elif w2:
                # A repeat of a byte-identical request must not write again. A write on
                # call 2 means the entry was evicted or the prefix is not stable, and a
                # per-item cost projection from it would be wrong on every later call.
                c5.ok = False
                c5.detail = (f"call 2 wrote {w2} tokens as well as reading {rd2}. A repeat "
                             f"of a byte-identical request should read only; a second write "
                             f"means the prefix is not stable across calls, so neither the "
                             f"cost claim nor the prefix size is projectable.")
            else:
                first = w1 if state == "cold" else rd1
                if first != rd2:
                    c5.ok = False
                    c5.detail = (f"{state} start: call 1 "
                                 f"{'wrote' if state == 'cold' else 'read'} {first} but call 2 "
                                 f"read {rd2}. A partial read means part of the prefix is "
                                 f"re-sent at full price on every call.")
                else:
                    c5.ok = True
                    c5.detail = (f"{state} start: {first} tokens "
                                 f"{'written then' if state == 'cold' else 'read and'} read "
                                 f"back whole (requested ttl={CACHE_TTL})")
                    # The TTL is not observable from two immediate calls; the provider's
                    # `cacheDetails` on the WRITE names the ttl it stored (Codex W1 #8). Both
                    # production responses go through the runner's own breakdown validator
                    # (instrument.validate_cache_details, round 5 #5): a breakdown that is
                    # supplied but malformed, empty beside a write, or not adding up fails here
                    # exactly as it makes an attempt incomplete in the runner.
                    for lbl, uu in (("call 1", u1), ("call 2", u2)):
                        ok_d, ttls, det_total, why_d = ins.validate_cache_details(uu)
                        if not ok_d:
                            c5.ok = False
                            c5.detail += f"; {lbl}: {why_d}"
                        elif "cacheDetails" in uu and det_total:
                            c5.detail += f"; {lbl} cacheDetails: ttl={ttls}, tokens={det_total}"
                            if any(x != CACHE_TTL for x in ttls):
                                c5.ok = False
                                c5.detail += (f" -- requested {CACHE_TTL} but the provider stored "
                                              f"{ttls}; the 1-hour cost claim does not hold")
                    if state == "warm":
                        c5.detail += "; stored TTL not observable here (warm start, no write)"
                    elif "cacheDetails" not in u1:
                        c5.detail += "; stored TTL not observable here (no cacheDetails in the write response)"

            minimum = MIN_CACHE_PREFIX[key]
            if not c5.ok:
                # Gated on check 5, not on max(w1,rd1,w2,rd2). Taking the largest of four
                # numbers from an accounting that check 5 just declared uninterpretable
                # produced a confident prefix size out of contradictory evidence -- and,
                # with the minimum cleared, a PASS beside the FAIL that says the figure
                # means nothing.
                c6.skipped = (f"the cache accounting could not be interpreted (see check 5), "
                              f"so no prefix size was measured and this model's minimum of "
                              f"{minimum} tokens was not asserted.")
            else:
                cached = rd2
                margin = cached - minimum
                report.cache_tokens[key] = cached
                c6.ok = cached >= minimum
                c6.detail = (f"cached_prefix_tokens={cached}  model_minimum={minimum}  "
                             f"margin={margin:+d}. CountTokens is not callable on this "
                             f"credential, so a live call is the only way to know. The cached "
                             f"prefix is the pinned tool config plus the pinned system prompt.")
                if not c6.ok:
                    c6.detail += (f" Below the minimum the checkpoint does not cache at all "
                                  f"for {key}, and arm 4's cost comparison stops being "
                                  f"comparable.")

            # The two accountings, so the run record can be checked by hand. Recorded
            # only when BOTH production responses passed check_usage: a PASS derived
            # from a usage dict that `converse` had already failed for the same id was
            # a report that contradicted itself.
            if usage_ok.get((key, "production-call-1")) and \
                    usage_ok.get((key, "production-call-2")):
                billable = sum(u2.get(f, 0) for f in USAGE_FIELDS)
                quota = u2.get("inputTokens", 0) + u2.get("cacheWriteInputTokens", 0)
                report.add(Check("8", f"{m} usage complete; two accountings recorded",
                                 silent=True, ok=True, phase="live",
                                 detail=f"{ {f: u2.get(f) for f in USAGE_FIELDS} }  |  "
                                        f"billable={billable} quota={quota} "
                                        f"({billable / max(quota, 1):.1f}x divergence)"))
        except _NoCacheDone:
            if usage_ok.get((key, "production-call-1")) and usage_ok.get((key, "production-call-2")):
                report.add(Check("8", f"{m} usage complete (no cache counters: no cache point)", silent=True, ok=True,
                                 phase="live", detail=f"{ {f: u2.get(f) for f in USAGE_FIELDS} }"))
        except Exception as exc:  # noqa: BLE001
            for c in (c5, c6):
                if c is not None and c.ok is None and not c.skipped:
                    c.ok, c.detail = False, f"{err_code(exc)}: {exc}"
            for c in (c2, c3):
                if c.ok is None:
                    c.ok, c.detail = False, f"{err_code(exc)}: {exc}"

    # ---- 13b : the request that reached the client is the request that was built #
    # A separate LIVE check, not an appendix to check 13. Its evidence comes from
    # submitted requests, so recording it as an offline result made the phase
    # summary claim "offline checks passed" about a failure discovered live.
    c = report.add(Check("13b", "every submitted envelope is canonically identical to the "
                                "one the builder produced", silent=True, phase="live"))
    if not submitted:
        c.skipped = "no production request was submitted, so there is nothing to compare."
    else:
        bad = [r for r in submitted if r["built_sha256"] != r["sent_sha256"]]
        c.ok = not bad
        if bad:
            c.detail = (f"{len(bad)} of {len(submitted)} production call(s) were mutated "
                        f"between build and send: "
                        + "; ".join(f"{r['label']} built {r['built_sha256'][:12]} but sent "
                                    f"{r['sent_sha256'][:12]}" for r in bad[:3]))
        else:
            digests = sorted({r["sent_sha256"] for r in submitted})
            c.detail = (f"{len(submitted)} production call(s), "
                        f"{len(digests)} distinct request digest(s): "
                        f"{', '.join(d[:12] for d in digests)}. Every one matched the digest "
                        f"taken when it was built.")


def _pinned_digest(name: str) -> str:
    """The digest pins.json records for an artifact, or a marker. Read from the pin
    rather than recomputed: the run record should say what the pin claimed, so a
    later reader can tell a stale pin from a changed artifact."""
    try:
        return (json.loads(PINS_PATH.read_text()).get(name) or {}).get("sha256") or "unpinned"
    except Exception:  # noqa: BLE001
        return "unreadable"


def main(client_factory=None, before_send=None) -> None:
    """`client_factory` is the test seam. It reaches `run()` unchanged, so a test can
    drive the whole gate -- verdict construction, artifact contents and exit code
    included -- without credentials. Those are precisely the parts that a credentialed
    run exercises once and a reader can never re-check."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # "both" predates the third and fourth model; it and "all" mean every key in MODELS.
    ap.add_argument("--model", choices=[*MODELS, "both", "all"], default="all")
    ap.add_argument("--json", action="store_true",
                    help="print the report as JSON as well as writing it")
    ap.add_argument("--out", default=None,
                    help="where to write the run-record artifact (default: "
                         "runs/preflight_<timestamp>.json). Certification requires it: a "
                         "report that exists only in memory is not a run record.")
    ap.add_argument("--no-out", action="store_true",
                    help="do not write an artifact. The run is then NOT CERTIFIED.")
    ap.add_argument("--offline", action="store_true",
                    help="run only the checks that read files, make no API call, and record "
                         "the run as NOT CERTIFIED. Use this where no credentials exist: "
                         "missing credentials must not masquerade as a failed live run.")
    ap.add_argument("--no-raw", action="store_true",
                    help="omit verbatim API responses. A run without them cannot be "
                         "certified: the run record has to carry the responses the "
                         "conclusions came from, not a summary of them.")
    ap.add_argument("--instrument", choices=INSTRUMENTS, default="v1.0",
                    help="W3 (2026-10-14): certify the registered instrument (v1.0, default) or one of "
                         "the post-registration variants v1.1S / v1.1E (instrument_v11.py, own pins)")
    a = ap.parse_args()
    bind_instrument(a.instrument)

    external = getattr(ins, "EXTERNAL_API", {})
    # "both"/"all" mean every Bedrock model; a §4g model behind another provider's API is gated only by name
    models = [k for k in MODELS if k not in external] if a.model in ("both", "all") else [a.model]
    report = Report(started_at=datetime.now(timezone.utc).isoformat(),
                    region=(external[models[0]] if models and all(k in external for k in models) else REGION),
                    models={k: MODELS[k] for k in models},
                    tool_config_sha256=_pinned_digest("tool_config"),
                    captured=not a.no_raw)
    # The artifact is written even when the run cannot reach the API. The file's own
    # rule is that failed calls are evidence too, and exiting before the write made
    # that untrue for the one failure that matters most: a run that never started.
    reach_error = ""
    try:
        run(models, report, keep_raw=not a.no_raw, offline=a.offline,
            client_factory=client_factory, before_send=before_send)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        reach_error = f"{type(exc).__name__}: {exc}"
        report.add(Check("--", "preflight could not reach the API", silent=False,
                         ok=False, detail=reach_error, phase="live"))
        print(f"preflight could not reach the API: {reach_error}", file=sys.stderr)
    report.ended_at = datetime.now(timezone.utc).isoformat()

    def phase_state(phase: str) -> str:
        if report.failed_in(phase):
            return "failed"
        if [c for c in report.unasserted if c.phase == phase]:
            return "unasserted"
        if not [c for c in report.checks if c.phase == phase]:
            return "not_run"
        return "passed"

    def verdict() -> dict:
        """Every field that expresses the outcome, derived in ONE place from the
        state at the moment of the call.

        Round-5 finding #5 got half-fixed: `certified` was recomputed after
        persistence but `certification_status` and the two phase lines were frozen in
        the payload literal beforehand, so the artifact could read
        `certification_status: NOT_CERTIFIED` beside `certified: true`. They are now
        one function, called once, after `persisted_to` is known.
        """
        # Precedence, strictest first. `--offline` used to come first, so a run whose
        # pin assertions had FAILED was recorded as "live checks not run" -- the mode
        # it was invoked in, reported as though it were the outcome.
        status = ("NOT_CERTIFIED_API_UNREACHABLE" if reach_error
                  else "NOT_CERTIFIED_ASSERTION_FAILED" if report.failed
                  else "NOT_CERTIFIED_LIVE_CHECKS_NOT_RUN" if a.offline
                  else "CERTIFIED" if report.certified
                  else "NOT_CERTIFIED")
        assert (status == "CERTIFIED") == report.certified, \
            "certification_status and certified disagree"
        return {
            "certification_status": status,
            "certified": report.certified,
            # Reported per phase. A live failure must not be able to make the offline
            # line read "failed", nor a pin failure make the live line read "run" --
            # and neither line may read "passed" for checks that were merely SKIPPED,
            # which is the form the fake-assertion defect takes in a summary field.
            "offline_checks": phase_state("offline"),
            "live_checks": ("skipped" if a.offline
                            else "unreachable" if reach_error
                            else "not_run" if not report.live_ran
                            else phase_state("live")),
            "n_failed": len(report.failed),
            "n_failed_offline": len(report.failed_in("offline")),
            "n_failed_live": len(report.failed_in("live")),
            "n_unasserted": len(report.unasserted),
        }

    payload = {
        "started_at": report.started_at,
        "ended_at": report.ended_at,
        "region": report.region,
        "models": report.models,
        "tool_config_sha256": report.tool_config_sha256,
        "system_prompt_sha256": _pinned_digest("system_prompt"),
        "codebook_sha256": _pinned_digest("codebook"),
        "instrument": INSTRUMENT_NAME,
        "provenance": provenance(),
        "execution_mode": "offline" if a.offline else "live",
        # Derived from whether the client was actually constructed, not from the
        # absence of --offline. A live invocation that aborted in the offline phase --
        # an unreadable pin file, for instance -- recorded `live_attempted: true` for a
        # run that never opened a connection.
        "live_attempted": report.live_ran,
        "skip_reason": ("--offline was given; no credentials or no live run wanted"
                        if a.offline else reach_error or ""),
        "cache_initial_state": report.cache_state,
        "cache_prefix_tokens": report.cache_tokens,
        "cache_ttl": CACHE_TTL,
        "checks": [vars(c) for c in report.checks],
        "raw_responses": report.raw if not a.no_raw else "NOT CAPTURED (--no-raw)",
    }

    # Persist BEFORE reporting, so a write failure cannot be reported as certified.
    if not a.no_out:
        stamp = report.started_at.replace(":", "").replace("-", "").split(".")[0]
        out_path = Path(a.out) if a.out else (
            HERE.parent / "runs" / f"preflight_{stamp}.json")
        payload["persisted_to"] = str(out_path)   # self-describing artifact
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Persistence is about to succeed or raise, so assume it and correct on
            # failure -- never the other way round: the verdict has to be inside the
            # bytes that get written.
            report.persisted_to = str(out_path)
            payload.update(verdict())
            tmp = out_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n")
            tmp.replace(out_path)            # atomic
        except OSError as exc:
            report.persisted_to = ""
            payload.update(verdict())
            print(f"could not write the run-record artifact to {out_path}: {exc}",
                  file=sys.stderr)
    else:
        payload.update(verdict())

    if a.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"preflight -- {report.region} -- {report.started_at}")
        print(f"models: {', '.join(report.models.values())}   ttl={CACHE_TTL}   "
              f"mode={'offline' if a.offline else 'live'}\n")
        for c in report.checks:
            mark = "SKIP" if c.skipped else ("PASS" if c.ok else "FAIL")
            silent = " (silent if unasserted)" if c.silent and c.ok is False else ""
            print(f"  [{mark}] {c.id:<4} {c.name}{silent}")
            tail = c.skipped or c.detail
            if tail:
                print(f"         {tail}")
        n_pass = len([c for c in report.checks if c.ok and not c.skipped])
        print()
        if report.failed:
            n_silent = len([c for c in report.failed if c.silent])
            print(f"{len(report.failed)} assertion(s) FAILED, {n_silent} of which would have "
                  f"been invisible in a normal run. DO NOT run the pipeline.")
        elif not report.captured:
            print(f"{n_pass} assertion(s) passed, but --no-raw was given.")
            print("NOT CERTIFIED: the run record would carry summaries instead of the "
                  "responses the conclusions were computed from.")
        elif not report.persisted_to:
            print(f"{n_pass} assertion(s) passed, but no artifact was written.")
            print("NOT CERTIFIED: a report that exists only in memory is not a run record.")
        elif a.offline:
            print(f"{n_pass} offline assertion(s) passed: the pinned instrument loads, "
                  f"matches its pins, and is what the production builder selects.")
            print("NOT CERTIFIED: no API call was made, so nothing is known about model "
                  "behaviour, forced tool use or caching.")
        elif report.unasserted:
            print(f"{n_pass} assertion(s) passed, but {len(report.unasserted)} could not be "
                  f"asserted (see SKIP above).")
            print("NOT CERTIFIED: something the gate is supposed to assert was not "
                  "compared. Fine for development; not a gate for a reported run.")
        else:
            print(f"all {n_pass} assertions passed, every pin was compared, and the "
                  f"artifact is on disk. CERTIFIED.")
        if report.persisted_to:
            print(f"\nartifact: {report.persisted_to}")

    # 0 certified · 1 an assertion failed · 2 a live run was asked for and the API
    # could not be reached · 3 assertions ran but certification was impossible,
    # which is what --offline always is.
    # A failed assertion is a failure in every mode -- the first version suppressed
    # them under --offline, which would have hidden the two pin failures that mode
    # exists to catch.
    if report.failed:
        sys.exit(2 if reach_error else 1)
    sys.exit(0 if report.certified else 3)


if __name__ == "__main__":
    main()
