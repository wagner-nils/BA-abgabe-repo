#!/usr/bin/env python3
"""Instrument v1.0r3 -- the one frozen candidate configuration of the fresh-sample evaluation (pre-analysis
specification §4b, declared 2026-10-14; implemented 2026-10-26 on the author's dated decision, after the v1.0
A3 figure of 2026-10-19 existed).

Three parts, as frozen in §4b:
  (i)  the registered first request byte for byte: instrument v1.0's pinned artefacts (`pins.json`), its request
       builder, validator, model table, region and cache point -- every one is the v1.0 module's own object,
       re-exported here and never copied or re-rendered;
  (ii) retry policy v3 exactly as implemented for W3 in `instrument_v11.py`: the one retry after a returned
       schema- or contract-invalid response carries the prior call verbatim and the validator's messages (the
       v1.0 schema and the v1.0 contract rules, as the runner produces them) as a toolResult with status error;
       the unreplayable fallback and the resume rule live in the runner and apply unchanged;
  (iii) the analysis rules are enforced by the runner and the evaluator: the A3 census only, the registered
       model, k = 3, the author's recorded decision bound into the execution contract, scored under the status
       "fresh-sample evaluation" and reported beside v1.0 as a descriptive comparison of complete configurations.

`run_extract.py --instrument v1.0r3 --a3-candidate-decision "<who, when, words>"` binds `bind()`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import instrument as v10
import instrument_v11 as v11

HERE = Path(__file__).resolve().parent
INSTRUMENT_VERSION = "1.0r3"
RETRY_POLICY_VERSION = "3"      # one feedback retry after schema_invalid / contract_invalid; identical budget (W3)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_feedback_request(art, paragraph: str, prior: dict, problems: list[str], *, max_tokens: int = 1024) -> dict:
    """Retry policy v3 over the v1.0 request: the pinned first request plus the model's own (invalid) call and the
    validator's messages as a toolResult with status error. Stateless: nothing but these three turns. The prior
    call goes back VERBATIM; a call that cannot be replayed verbatim is the runner's business (identical retry)."""
    req = v10.build_request(art, paragraph, max_tokens=max_tokens)
    if not (isinstance(prior, dict) and prior.get("toolUseId") and prior.get("name") == v10.TOOL_NAME
            and isinstance(prior.get("input"), dict)):
        raise v10.InstrumentError("the prior call is not replayable verbatim (needs toolUseId, the declared tool name "
                                  "and an object input)")
    use_id = prior["toolUseId"]
    req["messages"] = req["messages"] + [
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": use_id, "name": prior["name"],
                                                       "input": prior["input"]}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": use_id, "status": "error",
                                                     "content": [{"text": v11.feedback_text(problems)}]}}]}]
    return req


_REEXPORT = ("MODELS", "REGION", "CACHE_TTL", "MIN_CACHE_PREFIX", "TOOL_NAME", "THINKING_CONTRACT", "ROOT",
             "NO_CACHE_POINT", "select_tool_use", "validate_cache_details", "load_codebook", "canonical_json",
             "sha256_text", "InstrumentError", "Artifacts", "build_request", "request_for", "validate_tool_use",
             "validate_response", "load_artifacts", "read_pins", "read_pins_checked")


class Bound:
    """The v1.0 surface with retry policy v3: `run_extract.py` and `preflight.py` bind `ins = instrument_v10r3.bind()`
    and keep calling `ins.load_artifacts()`, `ins.build_request()`, `ins.validate_tool_use()` and friends; every
    one of them is the registered module's object. Only the feedback builder and the identity are this module's."""

    def __init__(self):
        self.variant = None
        self.INSTRUMENT_VERSION = INSTRUMENT_VERSION
        self.RETRY_POLICY_VERSION = RETRY_POLICY_VERSION
        self.PINS = v10.PINS
        self.PROMPT_PATH_REL = "tools/pipeline/instrument/system-prompt_v1.0.txt"
        for name in _REEXPORT:
            if hasattr(v10, name):
                setattr(self, name, getattr(v10, name))
        self.build_feedback_request = build_feedback_request
        self.module_sha256 = {"instrument.py": _sha(HERE / "instrument.py"),
                              "instrument_v11.py": _sha(HERE / "instrument_v11.py"),
                              "instrument_v10r3.py": _sha(Path(__file__))}


def bind() -> Bound:
    return Bound()


if __name__ == "__main__":
    b = bind()
    print(json.dumps({"instrument_version": b.INSTRUMENT_VERSION, "retry_policy_version": b.RETRY_POLICY_VERSION,
                      "pins": str(b.PINS), "module_sha256": b.module_sha256}, indent=2))
