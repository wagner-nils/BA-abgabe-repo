#!/usr/bin/env python3
"""Instrument v1.0C -- the context-parity variant of the examiner-review analyses (pre-analysis specification §4l (5),
declared 2026-11-06 before any call of it; the author's decision D37).

The registered instrument v1.0 byte for byte -- pinned system prompt and tool configuration, tool forced by name,
temperature 0, 1,024 output tokens, the one-hour cache point, retry policy v2 -- except the user turn, which carries
three context lines before the unchanged paragraph block:

    Headline: <headline>
    Outlet: <outlet>
    Language: <German|English>

    Paragraph:
    <paragraph text>

These are the three items the coding interface showed beside each paragraph. No instruction is added; the system
prompt's unit definition applies unchanged. Every other object (artefacts, validator, model table, region, cache
point) is the v1.0 module's own object, re-exported and never copied.

The runner binds the per-paragraph context before its first request (`bind_context`); a text the binding does not
know -- the gate's invented probe paragraphs -- receives the declared PROBE_CONTEXT, so the gate certifies the same
request shape the run sends.

`run_extract.py --instrument v1.0C --context-variant-decision "<who, when, words>"` binds `bind()`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import instrument as v10

HERE = Path(__file__).resolve().parent
INSTRUMENT_VERSION = "1.0C"
RETRY_POLICY_VERSION = "2"      # the registered policy: identical resend; the context lines are the only difference
LANGUAGE_NAME = {"de": "German", "en": "English"}
PROBE_CONTEXT = {"headline": "Invented headline for the gate probe", "outlet": "Invented outlet", "lang": "en"}
CONTEXT_FIELDS = ("headline", "outlet", "lang")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def text_key(paragraph: str) -> str:
    return hashlib.sha256(paragraph.encode("utf-8")).hexdigest()


def context_lines(ctx: dict) -> str:
    """The three context lines, exactly as declared; refuses an empty or multi-line field or an unknown language."""
    for f in CONTEXT_FIELDS:
        v = ctx.get(f)
        if not isinstance(v, str) or not v.strip():
            raise v10.InstrumentError(f"context field {f!r} is empty")
        if "\n" in v or "\r" in v:
            raise v10.InstrumentError(f"context field {f!r} spans more than one line")
    if ctx["lang"] not in LANGUAGE_NAME:
        raise v10.InstrumentError(f"unknown language code {ctx['lang']!r}")
    return (f"Headline: {ctx['headline'].strip()}\nOutlet: {ctx['outlet'].strip()}\n"
            f"Language: {LANGUAGE_NAME[ctx['lang']]}")


class Bound:
    """The v1.0 surface with the context user turn: `run_extract.py` and `preflight.py` bind
    `ins = instrument_v10c.bind()` and keep calling `ins.load_artifacts()`, `ins.build_request()`,
    `ins.validate_tool_use()` and friends. Only the request builder, the context binding and the identity are this
    module's; everything else is the registered module's object."""

    _REEXPORT = ("MODELS", "REGION", "CACHE_TTL", "MIN_CACHE_PREFIX", "TOOL_NAME", "THINKING_CONTRACT", "ROOT",
                 "NO_CACHE_POINT", "select_tool_use", "validate_cache_details", "load_codebook", "canonical_json",
                 "sha256_text", "InstrumentError", "Artifacts", "validate_tool_use", "validate_response",
                 "load_artifacts", "read_pins", "read_pins_checked")

    def __init__(self):
        self.variant = None
        self.INSTRUMENT_VERSION = INSTRUMENT_VERSION
        self.RETRY_POLICY_VERSION = RETRY_POLICY_VERSION
        self.PINS = v10.PINS
        self.PROMPT_PATH_REL = "tools/pipeline/instrument/system-prompt_v1.0.txt"
        for name in self._REEXPORT:
            if hasattr(v10, name):
                setattr(self, name, getattr(v10, name))
        self._context: dict[str, dict] = {}
        self.module_sha256 = {"instrument.py": _sha(HERE / "instrument.py"),
                              "instrument_v10c.py": _sha(Path(__file__))}

    # ---- the context binding ------------------------------------------------------------- #
    def bind_context(self, by_text: dict[str, dict]) -> str:
        """Bind {paragraph text: {headline, outlet, lang}} before the first request; returns the binding's digest
        (sorted by text hash, the three fields only), which the execution contract records."""
        bound = {}
        for text, ctx in by_text.items():
            context_lines(ctx)                                   # refuses a malformed field before any call
            bound[text_key(text)] = {f: ctx[f].strip() for f in CONTEXT_FIELDS}
        self._context = bound
        return context_digest(bound)

    def context_for(self, paragraph: str) -> dict:
        return self._context.get(text_key(paragraph), PROBE_CONTEXT)

    # ---- the request --------------------------------------------------------------------- #
    def build_request(self, art, paragraph: str, *, max_tokens: int = 1024) -> dict:
        """v1.0's request for this paragraph, with the context lines placed before its unchanged user block."""
        req = v10.build_request(art, paragraph, max_tokens=max_tokens)
        block = req["messages"][0]["content"][0]["text"]
        if block != f"Paragraph:\n{paragraph}":
            raise v10.InstrumentError("the registered user block changed shape; v1.0C is defined over 'Paragraph:\\n<text>'")
        req["messages"][0]["content"][0]["text"] = f"{context_lines(self.context_for(paragraph))}\n\n{block}"
        return req

    def request_for(self, model_key: str, art, paragraph: str, *, max_tokens: int = 1024) -> dict:
        if model_key != "sonnet":
            raise v10.InstrumentError("v1.0C runs the registered model only (specification §4l (5))")
        return self.build_request(art, paragraph, max_tokens=max_tokens)


def context_digest(bound: dict) -> str:
    return hashlib.sha256(json.dumps(sorted(bound.items()), ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def bind() -> Bound:
    return Bound()


if __name__ == "__main__":
    b = bind()
    print(json.dumps({"instrument_version": b.INSTRUMENT_VERSION, "retry_policy_version": b.RETRY_POLICY_VERSION,
                      "pins": str(b.PINS), "module_sha256": b.module_sha256}, indent=2))
