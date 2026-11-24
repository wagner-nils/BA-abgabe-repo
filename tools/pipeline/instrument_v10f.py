#!/usr/bin/env python3
"""Instrument v1.0F -- the mandatory-form variant (pre-analysis specification §4j, declared 2026-10-30).

A mechanical transformation of v1.0r3 (the registered request under retry policy v3), so that against v1.0r3 the form
is the difference:
  * tool configuration: the relation's `elements` array becomes `element_checks`, an object with the 17 element ids
    as required booleans in codebook order (`additionalProperties: false`, non-strict as registered), at the same
    position and with `elements` replaced by `element_checks` at the same index of `required`; nothing else changes;
  * system prompt: the registered prompt with exactly the four enumerated replacements below (every sentence that
    refers to the `elements` list), each asserted to occur once; the version line is left unchanged;
  * output cap 2,048 tokens, set from the longest journalled v1.0 answers re-encoded in the form (about 1,000
    tokens); the declaration names an invented five-technology record instead, which gives about 720 tokens -- a
    disclosed deviation (§4j status), without which the longest observed answer (1,121 tokens) would have truncated;
  * a normaliser maps true checks to the registered `elements` list (codebook order) at the same key position, so
    the registered validator, derivation, public payload and evaluator apply unchanged;
  * retry policy v3, the feedback builder of v1.0r3.

    .venv/bin/python3 tools/pipeline/instrument_v10f.py --write --update-pins   # render the artefacts and pin them
    .venv/bin/python3 tools/pipeline/instrument_v10f.py --check                 # artefacts equal the fresh render
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import date
from pathlib import Path

import instrument as v10
import instrument_v10r3 as v10r3

HERE = Path(__file__).resolve().parent
INSTRUMENT_VERSION = "1.0F"
RETRY_POLICY_VERSION = "3"
MAX_TOKENS = 2048
FIELD = "element_checks"
PINS = HERE / "pins_v1.0F.json"
PROMPT_FILE = HERE / "instrument" / "system-prompt_v1.0F.txt"
CONFIG_FILE = HERE / "instrument" / "tool-config_v1.0F.json"
CHECKS_DESCRIPTION = ("Every one of the 17 elements, answered for THIS technology: true if it fires, false if it does "
                      "not. All false means the paragraph makes a statement about this technology but frames nothing "
                      "about it.")
REPLACEMENTS = (
    ("An empty `elements` list means THE TEXT DOES NOT FRAME this technology. It does NOT mean \"I could not find an "
     "element that fits\". If you believe something is being framed but no element matches, that is F9 plus a note -- "
     "never an empty `elements` list.",
     "`element_checks` with every check false means THE TEXT DOES NOT FRAME this technology. It does NOT mean \"I could "
     "not find an element that fits\". If you believe something is being framed but no element matches, that is F9 true "
     "plus a note -- never all checks false."),
    ("give that technology a relation with an empty `elements` list.",
     "give that technology a relation whose `element_checks` are all false."),
    ("including a technology it names but frames nothing about, whose `elements` list is then empty.",
     "including a technology it names but frames nothing about, whose `element_checks` are then all false."),
    ("Every element you list fires for THAT technology. Its frame then needs exactly one evaluation in `evaluations`, "
     "and a frame that did not fire must not carry one.",
     "In `element_checks`, answer every one of the 17 elements for THAT technology: true if it fires, false if it does "
     "not; no element may be left out. Every element marked true fires for THAT technology. Its frame then needs exactly "
     "one evaluation in `evaluations`, and a frame that did not fire must not carry one."),
)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def element_order() -> list[str]:
    doc, _, _ = v10.load_codebook()
    return [e["id"] for e in doc["frame_elements"]]


def render_prompt(base_prompt: str) -> str:
    p = base_prompt
    for old, new in REPLACEMENTS:
        n = p.count(old)
        if n != 1:
            raise v10.InstrumentError(f"v1.0F: a replaced sentence occurs {n} times in the v1.0 prompt: {old[:60]!r}")
        p = p.replace(old, new)
    if "`elements` list" in p:
        raise v10.InstrumentError("v1.0F: the prompt still refers to the `elements` list")
    return p


def build_tool_config(base_cfg: dict) -> dict:
    cfg = copy.deepcopy(base_cfg)
    rel = cfg["tools"][0]["toolSpec"]["inputSchema"]["json"]["properties"]["relations"]["items"]
    els = list(rel["properties"]["elements"]["items"]["enum"])
    props = {}
    for k, v in rel["properties"].items():
        props[FIELD if k == "elements" else k] = (
            {"type": "object", "additionalProperties": False, "required": els,
             "properties": {e: {"type": "boolean"} for e in els}, "description": CHECKS_DESCRIPTION}
            if k == "elements" else v)
    rel["properties"] = props
    rel["required"] = [FIELD if r == "elements" else r for r in rel["required"]]
    return cfg


def render() -> tuple[str, dict]:
    art = v10.load_artifacts()
    return render_prompt(art.prompt), build_tool_config(art.tool_config)


def normalise(payload: dict) -> dict:
    """element_checks -> the registered `elements` list (codebook order), at the same key position; total on any
    object, so the registered validator sees the registered shape (a missing or malformed check object becomes an
    empty list and is caught by the schema before this point)."""
    if not isinstance(payload, dict):
        return payload
    order = element_order()
    out = {}
    for k, v in payload.items():
        if k != "relations" or not isinstance(v, list):
            out[k] = v
            continue
        rels = []
        for r in v:
            if not isinstance(r, dict) or FIELD not in r:
                rels.append(r)
                continue
            q = {}
            for kk, vv in r.items():
                if kk == FIELD:
                    checks = vv if isinstance(vv, dict) else {}
                    q["elements"] = [e for e in order if checks.get(e) is True]
                else:
                    q[kk] = vv
            rels.append(q)
        out[k] = rels
    return out


def validate_response(payload: dict, doc: dict, subtypes: dict) -> list[str]:
    return v10.validate_response(normalise(payload), doc, subtypes)


def validate_tool_use(block: dict, art, doc: dict, subtypes: dict) -> tuple[bool, str]:
    """Tool name, then the PINNED v1.0F schema, then the registered relational contract on the normalised payload."""
    from jsonschema import Draft202012Validator
    name = block.get("name")
    want = art.tool_config["tools"][0]["toolSpec"]["name"]
    if name != want:
        return False, f"toolUse.name is {name!r}, expected {want!r}"
    got = block.get("input")
    if not isinstance(got, dict):
        return False, f"toolUse.input is {type(got).__name__}, expected object"
    schema = art.tool_config["tools"][0]["toolSpec"]["inputSchema"]["json"]
    errs = sorted(Draft202012Validator(schema).iter_errors(got), key=lambda e: list(e.path))
    if errs:
        return False, "schema: " + "; ".join(f"{'/'.join(str(x) for x in e.path) or '<root>'}: {e.message}" for e in errs[:4])
    problems = v10.validate_response(normalise(got), doc, subtypes)
    if problems:
        return False, "relational contract: " + "; ".join(problems[:4])
    return True, f"schema-valid and contract-valid: {json.dumps(got)[:300]}"


def read_pins() -> dict:
    return json.loads(PINS.read_text(encoding="utf-8")) if PINS.exists() else {}


def load_artifacts(pins: dict | None = None) -> v10.Artifacts:
    """Reads the pinned bytes, hashes THOSE bytes, requires the codebook pin to be the registered one."""
    pins = pins if pins is not None else read_pins()
    if not pins:
        raise v10.InstrumentError("no pins for instrument v1.0F; run instrument_v10f.py --write --update-pins")
    texts = {}
    for key in ("system_prompt", "tool_config"):
        spec = pins.get(key) or {}
        path = (v10.ROOT / "tools" / spec.get("path", "")).resolve()
        if not spec.get("sha256") or not path.is_file():
            raise v10.InstrumentError(f"pin `{key}` needs an existing `path` and a `sha256`")
        raw = path.read_bytes()
        if _sha(raw) != spec["sha256"]:
            raise v10.InstrumentError(f"{spec['path']} hashes to {_sha(raw)[:16]} but its pin says {spec['sha256'][:16]}")
        texts[key] = raw.decode("utf-8")
    reg = v10.read_pins()
    if pins.get("codebook") != reg.get("codebook"):
        raise v10.InstrumentError("v1.0F: the codebook pin differs from the registered one")
    return v10.Artifacts(prompt=texts["system_prompt"], prompt_sha256=pins["system_prompt"]["sha256"],
                         tool_config=json.loads(texts["tool_config"]), tool_config_sha256=pins["tool_config"]["sha256"],
                         codebook_version=str(reg["codebook"]["version"]), codebook_sha256=str(reg["codebook"]["sha256"]))


def request_for(model_key: str, art, paragraph: str, *, max_tokens: int = MAX_TOKENS) -> dict:
    if model_key != "sonnet":
        raise v10.InstrumentError("v1.0F is declared for the registered model only (§4j)")
    return v10.build_request(art, paragraph, max_tokens=MAX_TOKENS)


def build_request(art, paragraph: str, *, max_tokens: int = MAX_TOKENS) -> dict:
    return v10.build_request(art, paragraph, max_tokens=MAX_TOKENS)


def build_feedback_request(art, paragraph: str, prior: dict, problems: list[str], *, max_tokens: int = MAX_TOKENS) -> dict:
    return v10r3.build_feedback_request(art, paragraph, prior, problems, max_tokens=MAX_TOKENS)


_REEXPORT = ("MODELS", "REGION", "CACHE_TTL", "MIN_CACHE_PREFIX", "TOOL_NAME", "THINKING_CONTRACT", "ROOT",
             "NO_CACHE_POINT", "select_tool_use", "validate_cache_details", "load_codebook", "canonical_json",
             "sha256_text", "InstrumentError", "Artifacts")


class Bound:
    """The v1.0 surface with the form: `run_extract.py` and `preflight.py` bind `ins = instrument_v10f.bind()`."""

    def __init__(self):
        self.variant = None
        self.INSTRUMENT_VERSION = INSTRUMENT_VERSION
        self.RETRY_POLICY_VERSION = RETRY_POLICY_VERSION
        self.MAX_TOKENS = MAX_TOKENS
        self.PINS = PINS
        self.PROMPT_PATH_REL = "tools/pipeline/instrument/system-prompt_v1.0F.txt"
        for name in _REEXPORT:
            if hasattr(v10, name):
                setattr(self, name, getattr(v10, name))
        self.build_request, self.request_for = build_request, request_for
        self.build_feedback_request = build_feedback_request
        self.validate_tool_use, self.validate_response, self.normalise = validate_tool_use, validate_response, normalise
        self.load_artifacts, self.read_pins = load_artifacts, read_pins
        self.read_pins_checked = lambda: read_pins() or (_ for _ in ()).throw(
            v10.InstrumentError("pins_v1.0F.json is missing; run instrument_v10f.py --write --update-pins"))
        self.module_sha256 = {"instrument.py": _sha((HERE / "instrument.py").read_bytes()),
                              "instrument_v11.py": _sha((HERE / "instrument_v11.py").read_bytes()),
                              "instrument_v10r3.py": _sha((HERE / "instrument_v10r3.py").read_bytes()),
                              "instrument_v10f.py": _sha(Path(__file__).read_bytes())}


def bind() -> Bound:
    return Bound()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--update-pins", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    prompt, cfg = render()
    cfg_text = v10.canonical_json(cfg)
    if a.write:
        PROMPT_FILE.write_text(prompt, encoding="utf-8")
        CONFIG_FILE.write_text(cfg_text, encoding="utf-8")
        print(f"  wrote {PROMPT_FILE.name}, {CONFIG_FILE.name}")
    if a.update_pins:
        reg = v10.read_pins()
        pins = {"_comment": ["Pins of the §4j form variant v1.0F; the codebook pin is the registered one, copied."],
                "codebook": reg["codebook"],
                "system_prompt": {"path": "pipeline/instrument/system-prompt_v1.0F.txt",
                                  "sha256": _sha(PROMPT_FILE.read_bytes()), "codebook_version": reg["codebook"]["version"],
                                  "codebook_sha256": reg["codebook"]["sha256"], "pinned_on": str(date.today())},
                "tool_config": {"path": "pipeline/instrument/tool-config_v1.0F.json",
                                "sha256": _sha(CONFIG_FILE.read_bytes()), "codebook_version": reg["codebook"]["version"],
                                "codebook_sha256": reg["codebook"]["sha256"], "pinned_on": str(date.today())}}
        PINS.write_text(json.dumps(pins, indent=2) + "\n", encoding="utf-8")
        print(f"  wrote {PINS.name}")
    if a.check:
        ok = PROMPT_FILE.read_text(encoding="utf-8") == prompt and CONFIG_FILE.read_text(encoding="utf-8") == cfg_text
        art = load_artifacts()
        print(f"  artefacts equal the fresh render: {ok}; pins verify: {bool(art)}")
        if not ok:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
