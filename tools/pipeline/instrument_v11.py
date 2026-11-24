#!/usr/bin/env python3
"""Instrument v1.1 -- the post-registration development variants of W3 (pre-analysis specification §4,
block of 2026-10-14; lever analysis the project repository).

    .venv/bin/python3 tools/pipeline/instrument_v11.py --variant S --diff
    .venv/bin/python3 tools/pipeline/instrument_v11.py --variant S --update-pins --pinned-on 2026-10-14

What differs from v1.0, and nothing else:
  * the tool is declared `strict: true` (Bedrock structured outputs, constrained decoding);
  * the relation carries a list of `frames`, each `{frame, elements, evaluation}`, so an evaluation
    without a fired frame or a fired frame without an evaluation cannot be produced (the v1.0 schema
    carried `elements` and `evaluations` side by side and left the pairing to a relational rule);
  * the "Output contract" block of the system prompt describes that shape and shows one filled-in
    record for a synthetic paragraph (a format demonstration, no corpus text);
  * optionally (variant E) a required `explanation` field that precedes the record;
  * a deterministic normaliser maps the nested payload to the v1.0 public shape, so the runner's
    derived rows and the evaluator are untouched.
The codebook, every definition, boundary and exclusion rule, the abstention instruction, the model,
the sampling parameters and the request envelope (four Converse keys, cache point, one user text
block) are those of v1.0. v1.0's module, artefacts and pins are not modified; this module imports it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = HERE
sys.path.insert(0, str(HERE))
import instrument as ins                                          # noqa: E402  v1.0, read-only

INSTRUMENT_VERSION = "1.1"
# S = structural only; E = structural + required explanation field (both W3, 2026-10-14);
# T = the W4 candidate v1.2S: per-frame element enums, one required subtype, alphabetical field names (2026-10-14)
VARIANTS = ("S", "E", "T")
VERSION_OF = {"S": "1.1S", "E": "1.1E", "T": "1.2S"}
OUT = PIPELINE / "instrument"
PINS_OF = {v: PIPELINE / f"pins_v{VERSION_OF[v]}.json" for v in VARIANTS}

# re-exported so a consumer can switch modules without touching these
TOOL_NAME, MODELS, REGION, CACHE_TTL, MIN_CACHE_PREFIX = ins.TOOL_NAME, ins.MODELS, ins.REGION, ins.CACHE_TTL, ins.MIN_CACHE_PREFIX
select_tool_use, validate_cache_details, THINKING_CONTRACT = ins.select_tool_use, ins.validate_cache_details, ins.THINKING_CONTRACT
load_codebook, canonical_json, sha256_text, InstrumentError = ins.load_codebook, ins.canonical_json, ins.sha256_text, ins.InstrumentError

# ─── the tool schema ─────────────────────────────────────────────────────────
# Inside Bedrock's documented strict subset (read 2026-10-13): enum, const, anyOf, $ref to $defs,
# required, additionalProperties:false, minItems 0|1. No uniqueItems, no oneOf, no if/then. A
# per-technology anyOf of relation variants was rejected by the provider on 2026-10-13 ("compiled
# grammar is too large"), so the relation stays one object and subtype placement stays a rule.
def subtype_values(subtypes: dict) -> list[str]:
    """`none` plus every subtype value of the codebook, in declaration order, without duplicates."""
    out = ["none"]
    for field in ("wind_siting", "hydrogen_pathway"):
        for v in subtypes[field]:
            if v not in out:
                out.append(v)
    return out


def build_tool_config_typed(doc: dict, subtypes: dict) -> dict:
    """W4's v1.2S schema (2026-10-14): one flat relation with per-frame element enums (anyOf over the
    frames, each with its own element enum, so an element cannot be emitted under another frame), ONE
    required `technology_subtype` on every relation, and field names whose alphabetical order is the
    reading order (`technology`, `technology_frames`, `technology_subtype`, `technology_versus`; inside a
    frame `frame`, `frame_elements`, `frame_evaluation`; optional notes as `z_*`). Inside Bedrock's strict
    subset (probe 2026-10-14: compiles)."""
    techs = doc["technologies"]
    frames = list(doc["frame_derivation"])
    elements_of = {f: [e["id"] for e in doc["frame_elements"] if e["frame"] == f] for f in frames}
    evals = [v["id"] for v in doc["evaluation"]["values"]]
    targets = [v["id"] if isinstance(v, dict) else v for v in doc["comparison_target"]["values"]]
    needs_note = [e["id"] for e in doc["frame_elements"] if e.get("requires_note")]
    frame_objs = [{"type": "object", "additionalProperties": False,
                   "required": ["frame", "frame_elements", "frame_evaluation"],
                   "properties": {
                       "frame": {"type": "string", "const": f},
                       "frame_elements": {"type": "array", "minItems": 1,
                                          "items": {"type": "string", "enum": elements_of[f]},
                                          "description": f"Every element of frame `{f}` that fires for this technology."},
                       "frame_evaluation": {"type": "string", "enum": evals,
                                            "description": "The evaluation of THIS frame for THIS technology."}}}
                  for f in frames]
    relation = {"type": "object", "additionalProperties": False,
                "required": ["technology", "technology_frames", "technology_subtype", "technology_versus"],
                "properties": {
                    "technology": {"type": "string", "enum": [t["id"] for t in techs]},
                    "technology_frames": {"type": "array", "items": {"$ref": "#/$defs/frame"},
                                          "description": "One entry per frame that fires for this technology, each with its "
                                                         "fired elements and its evaluation. An empty list means the paragraph "
                                                         "makes a statement about this technology but frames nothing about it."},
                    "technology_subtype": {"type": "string", "enum": subtype_values(subtypes),
                                           "description": "wind: onshore | offshore | unspecified; hydrogen: green | blue | gray | "
                                                          "unspecified; every other technology: none."},
                    "technology_versus": {"type": "string", "enum": targets,
                                          "description": "The comparison target of this relation; `none` when it frames nothing."},
                    "z_comparison_note": {"type": "string", "description": "Required when technology_versus is `other` or `multiple`."},
                    "z_note": {"type": "string", "description": "Required when any of " + ", ".join(f"`{e}`" for e in needs_note) + " fires."}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["outcome", "relations"],
              "properties": {
                  "outcome": {"type": "string", "enum": ["off_topic", "technologies_present"],
                              "description": "`off_topic` when the paragraph makes no statement about any listed "
                                             "technology; `technologies_present` otherwise."},
                  "relations": {"type": "array", "items": relation}},
              "$defs": {"frame": {"anyOf": frame_objs}}}
    return {"tools": [{"toolSpec": {
                "name": TOOL_NAME,
                "description": "Record the technology-frame-evaluation relations asserted in one news "
                               "paragraph, exactly as the coding instrument defines them.",
                "strict": True,
                "inputSchema": {"json": schema}}}],
            "toolChoice": {"tool": {"name": TOOL_NAME}}}


def build_tool_config(doc: dict, subtypes: dict, explanation: bool = False, typed: bool = False) -> dict:
    if typed:
        return build_tool_config_typed(doc, subtypes)
    techs = doc["technologies"]
    frames = list(doc["frame_derivation"])
    elements = [e["id"] for e in doc["frame_elements"]]
    evals = [v["id"] for v in doc["evaluation"]["values"]]
    targets = [v["id"] if isinstance(v, dict) else v for v in doc["comparison_target"]["values"]]
    needs_note = [e["id"] for e in doc["frame_elements"] if e.get("requires_note")]

    frame = {"type": "object", "additionalProperties": False,
             "required": ["frame", "elements", "evaluation"],
             "properties": {
                 "frame": {"type": "string", "enum": frames},
                 "elements": {"type": "array", "minItems": 1,
                              "items": {"type": "string", "enum": elements},
                              "description": "Every element of THIS frame that fires for this technology; "
                                             "only elements that belong to the named frame."},
                 "evaluation": {"type": "string", "enum": evals,
                                "description": "The evaluation of THIS frame for THIS technology."}}}
    rel_props: dict = {"technology": {"type": "string", "enum": [t["id"] for t in techs]}}
    for t in techs:
        if "subtype_field" in t:
            rel_props[t["subtype_field"]] = {"type": "string", "enum": subtypes[t["subtype_field"]],
                                             "description": f"Only for technology `{t['id']}`, where it is required; "
                                                            f"`unspecified` where the text does not say."}
    rel_props["frames"] = {"type": "array", "items": {"$ref": "#/$defs/frame"},
                           "description": "One entry per frame that fires for this technology, each with its "
                                          "fired elements and its evaluation. An empty list means the paragraph "
                                          "makes a statement about this technology but frames nothing about it."}
    rel_props["comparison_target"] = {"type": "string", "enum": targets}
    rel_props["comparison_note"] = {"type": "string",
                                    "description": "Required when comparison_target is `other` or `multiple`."}
    rel_props["note"] = {"type": "string",
                         "description": "Required when any of " + ", ".join(f"`{e}`" for e in needs_note) + " fires."}
    relation = {"type": "object", "additionalProperties": False,
                "required": ["technology", "frames", "comparison_target"], "properties": rel_props}

    top: dict = {}
    if explanation:
        top["explanation"] = {"type": "string",
                              "description": "One to three sentences, written before the record: which technologies "
                                             "the paragraph makes a statement about, which elements fire for each, "
                                             "and which codebook rule decides any hard case. Quote no more than a "
                                             "few words of the paragraph."}
    top["outcome"] = {"type": "string", "enum": ["off_topic", "technologies_present"],
                      "description": "`off_topic` when the paragraph makes no statement about any listed "
                                     "technology; `technologies_present` otherwise."}
    top["relations"] = {"type": "array", "items": relation}
    schema = {"type": "object", "additionalProperties": False,
              "required": (["explanation"] if explanation else []) + ["outcome", "relations"],
              "properties": top, "$defs": {"frame": frame}}
    return {"tools": [{"toolSpec": {
                "name": TOOL_NAME,
                "description": "Record the technology-frame-evaluation relations asserted in one news "
                               "paragraph, exactly as the coding instrument defines them.",
                "strict": True,
                "inputSchema": {"json": schema}}}],
            "toolChoice": {"tool": {"name": TOOL_NAME}}}


# ─── the prompt ──────────────────────────────────────────────────────────────
# A synthetic paragraph (no corpus text) and its record. It is ONE LABELLED DEMONSTRATION, not only a
# demonstration of form (Codex W3 round 2, finding 3), and its labels were audited against the frozen
# rules before the seal: "create about 300 jobs" -> F2a (jobs created), positive (a supporter would be
# gratified); "spoil the coastal landscape" -> F7b (landscape, visual impact), negative; "cannot be
# relied on when the wind drops" -> F5a (availability when needed), negative; the minister and the
# critics are voiced evaluations coded as asserted (X3); "offshore" -> wind_siting; the solar sentence
# mirrors the codebook's own X1 illustration ("The wind farm went into operation on Tuesday" fires no
# element), so solar is named and unframed. Written after the A2 results were seen; its influence on
# the model's labels is not claimed to be nil.
DEMO_PARAGRAPH = ("The new offshore wind farm will create about 300 jobs in the region, the minister said, "
                  "although critics warn that the turbines will spoil the coastal landscape and that the power "
                  "cannot be relied on when the wind drops. A solar park went into operation nearby on Tuesday.")
DEMO_RECORD = {"outcome": "technologies_present", "relations": [
    {"technology": "wind", "wind_siting": "offshore",
     "frames": [{"frame": "jobs", "elements": ["F2a"], "evaluation": "positive"},
                {"frame": "siting", "elements": ["F7b"], "evaluation": "negative"},
                {"frame": "reliability", "elements": ["F5a"], "evaluation": "negative"}],
     "comparison_target": "none"},
    {"technology": "solar", "frames": [], "comparison_target": "none"}]}

# W4 (v1.2S): the SAME invented paragraph and the same labels as the W3 demonstration, in the new representation
# and in alphabetical key order (json.dumps(sort_keys=True) renders exactly this order).
DEMO_RECORD_T = {"outcome": "technologies_present", "relations": [
    {"technology": "wind",
     "technology_frames": [{"frame": "jobs", "frame_elements": ["F2a"], "frame_evaluation": "positive"},
                           {"frame": "siting", "frame_elements": ["F7b"], "frame_evaluation": "negative"},
                           {"frame": "reliability", "frame_elements": ["F5a"], "frame_evaluation": "negative"}],
     "technology_subtype": "offshore", "technology_versus": "none"},
    {"technology": "solar", "technology_frames": [], "technology_subtype": "none", "technology_versus": "none"}]}

OUTPUT_CONTRACT_V10_HEAD = "## Output contract\n"

# Codex W3 round 2, finding 2: v1.0's text names the v1.0 field (`elements`) when it describes the
# no-frame state; under the nested shape that state is an empty `frames` list. These are the ONLY
# replacements made outside the Output contract block, enumerated so that a test can assert that
# nothing else moved. Each one preserves the codebook's rule and changes the field name only.
# W4 (v1.2S): five enumerated replacements -- the two no-frame sentences name `technology_frames`, the two
# subtype lines of the technology definitions name `technology_subtype`, the comparison-target rule names
# `z_comparison_note`. Nothing else outside the Output contract moves.
FIELD_REFERENCE_REPLACEMENTS_T = (
    ("An empty `elements` list means THE TEXT DOES NOT FRAME this technology. It does NOT mean "
     "\"I could not find an element that fits\". If you believe something is being framed but no "
     "element matches, that is F9 plus a note -- never an empty `elements` list.",
     "An empty `technology_frames` list means THE TEXT DOES NOT FRAME this technology. It does NOT mean "
     "\"I could not find an element that fits\". If you believe something is being framed but no "
     "element matches, that is F9 plus a note -- never an empty `technology_frames` list."),
    ("Where the paragraph makes a statement about a technology but frames nothing about it, give that "
     "technology a relation with an empty `elements` list.",
     "Where the paragraph makes a statement about a technology but frames nothing about it, give that "
     "technology a relation with an empty `technology_frames` list."),
    (": Record `wind_siting` for this technology, one of: onshore | offshore | unspecified.",
     ": Record `technology_subtype` for this technology, one of: onshore | offshore | unspecified."),
    (": Record `hydrogen_pathway` for this technology, one of: green | blue | gray | unspecified.",
     ": Record `technology_subtype` for this technology, one of: green | blue | gray | unspecified."),
    ("List them in `comparison_note`.", "List them in `z_comparison_note`."),
)

FIELD_REFERENCE_REPLACEMENTS = (
    ("An empty `elements` list means THE TEXT DOES NOT FRAME this technology. It does NOT mean "
     "\"I could not find an element that fits\". If you believe something is being framed but no "
     "element matches, that is F9 plus a note -- never an empty `elements` list.",
     "An empty `frames` list means THE TEXT DOES NOT FRAME this technology. It does NOT mean "
     "\"I could not find an element that fits\". If you believe something is being framed but no "
     "element matches, that is F9 plus a note -- never an empty `frames` list."),
    ("Where the paragraph makes a statement about a technology but frames nothing about it, give that "
     "technology a relation with an empty `elements` list.",
     "Where the paragraph makes a statement about a technology but frames nothing about it, give that "
     "technology a relation with an empty `frames` list."),
)


def output_contract_typed() -> str:
    body = ["Return one call to the `record_relations` tool and nothing else.\n",
            "Set `outcome` to `off_topic` when the paragraph makes no statement about any technology listed above, "
            "and leave `relations` empty. Otherwise set it to `technologies_present`.\n",
            "Give **one relation per technology the paragraph makes a statement about** — including a technology it "
            "names but frames nothing about, whose `technology_frames` list is then empty. Do not merge two technologies "
            "into one relation, and do not repeat a technology.\n",
            "Inside a relation, `technology_frames` carries one entry per frame that fires for THAT technology: the "
            "frame's name, every element of that frame that fires (`frame_elements`), and exactly one evaluation of that "
            "frame (`frame_evaluation`). A frame that did not fire has no entry.\n",
            "`technology_subtype` is required on every relation: for `wind` one of onshore | offshore | unspecified, for "
            "`hydrogen` one of green | blue | gray | unspecified, and exactly `none` for every other technology. "
            "`technology_versus` is the comparison target; set it to `none` when the relation frames nothing. "
            "Put a required note in `z_note` and a required comparison note in `z_comparison_note`.\n",
            "Speaker attribution and coder confidence are recorded by the human coder only and are not part of this "
            "response.\n",
            "**Shape of the answer, shown on an invented paragraph:**\n",
            f"> {DEMO_PARAGRAPH}\n",
            "```json\n" + json.dumps(DEMO_RECORD_T, indent=2, ensure_ascii=False, sort_keys=True) + "\n```"]
    return ins.block("Output contract", "\n".join(body))


def output_contract(explanation: bool) -> str:
    demo = dict(DEMO_RECORD)
    if explanation:
        demo = {"explanation": "Wind: jobs created (F2a, positive), landscape and local opposition (F7b, "
                               "negative), power not available when the wind drops (F5a, negative); offshore. "
                               "Solar is named but nothing frames it.", **demo}
    body = ["Return one call to the `record_relations` tool and nothing else.\n"]
    if explanation:
        body.append("Fill `explanation` first: one to three sentences naming the technologies the paragraph "
                    "makes a statement about, the elements that fire for each, and the rule that decides any "
                    "hard case. Then fill the record.\n")
    body.append("Set `outcome` to `off_topic` when the paragraph makes no statement about any technology "
                "listed above, and leave `relations` empty. Otherwise set it to `technologies_present`.\n")
    body.append("Give **one relation per technology the paragraph makes a statement about** — including a "
                "technology it names but frames nothing about, whose `frames` list is then empty. Do not merge "
                "two technologies into one relation, and do not repeat a technology.\n")
    body.append("Inside a relation, `frames` carries one entry per frame that fires for THAT technology: the "
                "frame's name, every element of that frame that fires, and exactly one evaluation of that "
                "frame. List an element only under its own frame. A frame that did not fire has no entry.\n")
    body.append("Record `wind_siting` only on a `wind` relation and `hydrogen_pathway` only on a `hydrogen` "
                "relation, where each is required. Set `comparison_target` to `none` when the relation "
                "frames nothing.\n")
    body.append("Speaker attribution and coder confidence are recorded by the human coder only and are not "
                "part of this response.\n")
    body.append("**Shape of the answer, shown on an invented paragraph:**\n")
    body.append(f"> {DEMO_PARAGRAPH}\n")
    body.append("```json\n" + json.dumps(demo, indent=2, ensure_ascii=False) + "\n```")
    return ins.block("Output contract", "\n".join(body))


def render_prompt(doc: dict, subtypes: dict, cb_sha: str, explanation: bool = False, typed: bool = False) -> str:
    """v1.0's prompt with its 'Output contract' block replaced, the version line adapted and the enumerated
    field-name replacements applied. Every other byte is v1.0's: a test asserts the prefix identity."""
    base = ins.render_prompt(doc, subtypes, cb_sha)
    head, sep, _tail = base.partition(OUTPUT_CONTRACT_V10_HEAD)
    if not sep:
        raise InstrumentError("v1.0 prompt carries no 'Output contract' block; v1.1 cannot be rendered")
    version = VERSION_OF["T" if typed else ("E" if explanation else "S")]
    head = head.replace(
        f"This instrument is codebook v{doc['version']}, sha256 {cb_sha[:16]}, rendered mechanically from that file.",
        f"This instrument is codebook v{doc['version']}, sha256 {cb_sha[:16]}, rendered mechanically from that file "
        f"(instrument v{version}: output {'representation' if typed else 'shape'} revised; definitions unchanged).", 1)
    for old, new in (FIELD_REFERENCE_REPLACEMENTS_T if typed else FIELD_REFERENCE_REPLACEMENTS):
        if head.count(old) != 1:
            raise InstrumentError(f"v1.0 prompt no longer carries the sentence a replacement expects: {old[:60]!r}")
        head = head.replace(old, new, 1)
    return (head + (output_contract_typed() if typed else output_contract(explanation))).rstrip() + "\n"


# ─── the relational contract the schema cannot express ───────────────────────
def validate_response_typed(payload: dict, doc: dict, subtypes: dict) -> list[str]:
    """The relational rules of v1.2S (W4). Element-frame membership is enforced by the schema; what remains:
    outcome/relations consistency, repeated technologies, repeated frames, duplicate elements, the subtype's
    applicability (a wind value for wind, a hydrogen value for hydrogen, `none` otherwise), a comparison target
    without a frame, and the required notes. Rejects, never repairs."""
    problems: list[str] = []
    frame_of = {e["id"]: e["frame"] for e in doc["frame_elements"]}
    needs_note = {e["id"] for e in doc["frame_elements"] if e.get("requires_note")}
    owner = {t["id"]: t["subtype_field"] for t in doc["technologies"] if "subtype_field" in t}
    outcome = payload.get("outcome")
    rels = payload.get("relations") or []
    if outcome == "off_topic" and rels:
        problems.append(f"outcome is off_topic but {len(rels)} relation(s) are present")
    if outcome == "technologies_present" and not rels:
        problems.append("outcome is technologies_present but relations is empty")
    seen: set[str] = set()
    for i, r in enumerate(rels):
        tech = r.get("technology")
        where = f"relation {i} ({tech})"
        if tech in seen:
            problems.append(f"{where}: technology repeated; one relation per technology")
        seen.add(tech)
        frames = [fr.get("frame") for fr in (r.get("technology_frames") or [])]
        if len(frames) != len(set(frames)):
            problems.append(f"{where}: a frame is repeated; one entry per fired frame")
        for fr in (r.get("technology_frames") or []):
            wrong = [e for e in (fr.get("frame_elements") or []) if frame_of.get(e) != fr.get("frame")]
            if wrong:
                problems.append(f"{where}: frame `{fr.get('frame')}` lists {wrong}, which belong to another frame")
            if len(fr.get("frame_elements") or []) != len(set(fr.get("frame_elements") or [])):
                problems.append(f"{where}: frame `{fr.get('frame')}` lists an element twice")
        sub = r.get("technology_subtype")
        allowed = subtypes[owner[tech]] if tech in owner else ["none"]
        if sub not in allowed:
            problems.append(f"{where}: technology_subtype {sub!r} is not a valid subtype for `{tech}` "
                            f"(expected one of {allowed})")
        fired = {e for fr in (r.get("technology_frames") or []) for e in (fr.get("frame_elements") or [])}
        if (fired & needs_note) and not (r.get("z_note") or "").strip():
            problems.append(f"{where}: {sorted(fired & needs_note)} fired, which requires a note")
        if r.get("technology_versus") in ("other", "multiple") and not (r.get("z_comparison_note") or "").strip():
            problems.append(f"{where}: comparison_target is {r.get('technology_versus')!r}, which requires a "
                            f"comparison_note")
        if not frames and r.get("technology_versus") not in (None, "none"):
            problems.append(f"{where}: frames nothing, so comparison_target must be `none`, not "
                            f"{r.get('technology_versus')!r}")
    return problems


def normalise_typed(payload: dict) -> dict:
    """v1.2S payload -> v1.0 public shape. Lossless for a valid payload: elements are the union of the
    frames' elements, evaluations the frame -> evaluation map, the subtype goes to the owning field."""
    rels = []
    for r in payload.get("relations") or []:
        tech = r.get("technology")
        q = {"technology": tech,
             "elements": sorted({e for fr in (r.get("technology_frames") or []) for e in (fr.get("frame_elements") or [])}),
             "evaluations": {fr["frame"]: fr["frame_evaluation"] for fr in (r.get("technology_frames") or []) if "frame" in fr},
             "comparison_target": r.get("technology_versus")}
        sub = r.get("technology_subtype")
        if tech == "wind" and sub not in (None, "none"):
            q["wind_siting"] = sub
        if tech == "hydrogen" and sub not in (None, "none"):
            q["hydrogen_pathway"] = sub
        if "z_note" in r:
            q["note"] = r["z_note"]
        if "z_comparison_note" in r:
            q["comparison_note"] = r["z_comparison_note"]
        rels.append(q)
    return {"outcome": payload.get("outcome"), "relations": rels}


def validate_response(payload: dict, doc: dict, subtypes: dict, typed: bool = False) -> list[str]:
    if typed:
        return validate_response_typed(payload, doc, subtypes)
    problems: list[str] = []
    frame_of = {e["id"]: e["frame"] for e in doc["frame_elements"]}
    needs_note = {e["id"] for e in doc["frame_elements"] if e.get("requires_note")}
    subtype_owner = {t["subtype_field"]: t["id"] for t in doc["technologies"] if "subtype_field" in t}
    outcome = payload.get("outcome")
    rels = payload.get("relations") or []
    if outcome == "off_topic" and rels:
        problems.append(f"outcome is off_topic but {len(rels)} relation(s) are present")
    if outcome == "technologies_present" and not rels:
        problems.append("outcome is technologies_present but relations is empty")
    seen: set[str] = set()
    for i, r in enumerate(rels):
        tech = r.get("technology")
        where = f"relation {i} ({tech})"
        if tech in seen:
            problems.append(f"{where}: technology repeated; one relation per technology")
        seen.add(tech)
        frames = [fr.get("frame") for fr in (r.get("frames") or [])]
        if len(frames) != len(set(frames)):
            problems.append(f"{where}: a frame is repeated; one entry per fired frame")
        for fr in (r.get("frames") or []):
            wrong = [e for e in (fr.get("elements") or []) if frame_of.get(e) != fr.get("frame")]
            if wrong:
                problems.append(f"{where}: frame `{fr.get('frame')}` lists {wrong}, which belong to another frame")
            if len(fr.get("elements") or []) != len(set(fr.get("elements") or [])):
                problems.append(f"{where}: frame `{fr.get('frame')}` lists an element twice")
        for field, owner in subtype_owner.items():
            if field in r and tech != owner:
                problems.append(f"{where}: carries `{field}`, which belongs to `{owner}`")
            if tech == owner and field not in r:
                problems.append(f"{where}: is missing `{field}`, which this technology requires "
                                f"(use `unspecified` where the text does not say)")
        fired = {e for fr in (r.get("frames") or []) for e in (fr.get("elements") or [])}
        if (fired & needs_note) and not (r.get("note") or "").strip():
            problems.append(f"{where}: {sorted(fired & needs_note)} fired, which requires a note")
        if r.get("comparison_target") in ("other", "multiple") and not (r.get("comparison_note") or "").strip():
            problems.append(f"{where}: comparison_target is {r.get('comparison_target')!r}, which requires a "
                            f"comparison_note")
        if not frames and r.get("comparison_target") not in (None, "none"):
            problems.append(f"{where}: frames nothing, so comparison_target must be `none`, not "
                            f"{r.get('comparison_target')!r}")
    return problems


def normalise(payload: dict, typed: bool = False) -> dict:
    """The nested v1.1 payload in v1.0's shape (elements list + evaluations map per relation), so
    `run_extract.public_payload` and `derive_rows` consume it unchanged. `explanation` is free text
    and is dropped here (the runner journals it privately, like notes)."""
    if typed:
        return normalise_typed(payload)
    rels = []
    for r in payload.get("relations") or []:
        q = {"technology": r.get("technology"),
             "elements": sorted({e for fr in (r.get("frames") or []) for e in (fr.get("elements") or [])}),
             "evaluations": {fr["frame"]: fr["evaluation"] for fr in (r.get("frames") or []) if "frame" in fr},
             "comparison_target": r.get("comparison_target")}
        for f in ("wind_siting", "hydrogen_pathway", "note", "comparison_note"):
            if f in r:
                q[f] = r[f]
        rels.append(q)
    return {"outcome": payload.get("outcome"), "relations": rels}


def validate_tool_use(block: dict, art: "Artifacts", doc: dict, subtypes: dict, typed: bool = False) -> tuple[bool, str]:
    """Same interface and same order as v1.0: tool name, pinned schema, relational contract."""
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
        return False, "schema: " + "; ".join(
            f"{'/'.join(str(x) for x in e.path) or '<root>'}: {e.message}" for e in errs[:4])
    problems = validate_response(got, doc, subtypes, typed=typed)
    if problems:
        return False, "relational contract: " + "; ".join(problems[:4])
    return True, f"schema-valid and contract-valid: {json.dumps(got)[:300]}"


# ─── artefacts, pins, requests ───────────────────────────────────────────────
@dataclass
class Artifacts:
    prompt: str
    prompt_sha256: str
    tool_config: dict
    tool_config_sha256: str
    codebook_version: str
    codebook_sha256: str
    variant: str
    instrument_version: str = INSTRUMENT_VERSION


def read_pins(variant: str) -> dict:
    p = PINS_OF[variant]
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_artifacts(variant: str, pins: dict | None = None) -> Artifacts:
    """Mirrors v1.0's loader: reads the pinned bytes, hashes THOSE bytes, requires both artefacts to
    name the same codebook as the codebook pin; every refusal is an InstrumentError."""
    pins = pins if pins is not None else read_pins(variant)
    if not isinstance(pins, dict) or not pins:
        raise InstrumentError(f"no pins for instrument v{VERSION_OF[variant]}; run instrument_v11.py "
                              f"--variant {variant} --update-pins first")
    texts: dict[str, str] = {}
    for key in ("system_prompt", "tool_config"):
        spec = pins.get(key)
        if not isinstance(spec, dict) or not spec.get("sha256") or not spec.get("path"):
            raise InstrumentError(f"pin `{key}` needs both `path` and `sha256`")
        path = (ins.ROOT / "tools" / spec["path"]).resolve()
        if not path.exists():
            raise InstrumentError(f"pin `{key}` names {spec['path']}, which does not exist")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != spec["sha256"]:
            raise InstrumentError(f"{spec['path']} hashes to {digest[:16]} but its pin says {spec['sha256'][:16]}")
        texts[key] = raw.decode("utf-8")
    cb = pins.get("codebook") or {}
    for key in ("system_prompt", "tool_config"):
        if pins[key].get("codebook_version") != cb.get("version") or pins[key].get("codebook_sha256") != cb.get("sha256"):
            raise InstrumentError(f"pin `{key}` names another codebook than the codebook pin")
    cfg = json.loads(texts["tool_config"])
    return Artifacts(prompt=texts["system_prompt"], prompt_sha256=pins["system_prompt"]["sha256"],
                     tool_config=cfg, tool_config_sha256=pins["tool_config"]["sha256"],
                     codebook_version=str(cb.get("version", "?")), codebook_sha256=str(cb.get("sha256", "")),
                     variant=variant)


def build_request(art: Artifacts, paragraph: str, *, max_tokens: int = 1024) -> dict:
    """Byte-for-byte the v1.0 envelope shape (four Converse keys, pinned prompt then one cache point,
    the pinned tool config, one user text block, maxTokens + temperature 0)."""
    return {
        "system": [{"text": art.prompt}, {"cachePoint": {"type": "default", "ttl": CACHE_TTL}}],
        "messages": [{"role": "user", "content": [{"text": f"Paragraph:\n{paragraph}"}]}],
        "toolConfig": art.tool_config,
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
    }


FEEDBACK_HEAD = "Your previous call to `record_relations` was rejected by the coding instrument's validator:"
FEEDBACK_TAIL = ("Return one corrected call to `record_relations` for the same paragraph. Apply the instrument "
                 "as written; change only what the errors require.")


def feedback_text(problems: list[str]) -> str:
    return FEEDBACK_HEAD + "\n- " + "\n- ".join(problems) + "\n" + FEEDBACK_TAIL


def build_feedback_request(art: Artifacts, paragraph: str, prior: dict, problems: list[str],
                           *, max_tokens: int = 1024) -> dict:
    """Retry policy v3: the first request plus the model's own (invalid) call and the validator's
    messages as a toolResult with status error. Stateless: nothing but these three turns."""
    req = build_request(art, paragraph, max_tokens=max_tokens)
    # the prior call goes back VERBATIM (id, name, input); a call that cannot be replayed verbatim is the
    # runner's business (it sends the identical retry instead) -- never rewritten here (Codex W3 round 3, #9)
    if not (isinstance(prior, dict) and prior.get("toolUseId") and prior.get("name") == TOOL_NAME
            and isinstance(prior.get("input"), dict)):
        raise InstrumentError("the prior call is not replayable verbatim (needs toolUseId, the declared tool name "
                              "and an object input)")
    use_id = prior["toolUseId"]
    req["messages"] = req["messages"] + [
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": use_id, "name": prior["name"],
                                                       "input": prior["input"]}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": use_id, "status": "error",
                                                     "content": [{"text": feedback_text(problems)}]}}]}]
    return req


def build_manifest(doc: dict, cb_sha: str, prompt: str, cfg: str, variant: str) -> dict:
    base = ins.build_manifest(doc, cb_sha, prompt, cfg)

    def refresh(node):
        # inherited v1.0 wording written before the runner existed (Codex W3 round 4, #9)
        if isinstance(node, dict):
            return {k_: refresh(v_) for k_, v_ in node.items()}
        if isinstance(node, list):
            return [refresh(v_) for v_ in node]
        if isinstance(node, str):
            return node.replace("; M4 does not exist yet.", "; M4 is run_extract.py, which calls it on every response.")
        return node
    base = refresh(base)
    base["module"] = f"instrument_v11 (variant {variant}) over instrument v1.0"
    base["instrument_version"] = VERSION_OF[variant]
    base["differs_from_v1_0"] = [
        "toolSpec.strict = true (Bedrock structured outputs; constrained decoding)",
        "relation.frames = [{frame, elements, evaluation}] replaces relation.elements + relation.evaluations",
        "Output contract block rewritten for that shape, with one format demonstration on an invented paragraph",
        "explanation field (required, first) -- variant E only",
        "v1.2S (variant T, W4): per-frame element enums, one required technology_subtype, alphabetical field names "
        "(technology, technology_frames, technology_subtype, technology_versus; frame, frame_elements, frame_evaluation; z_* notes), "
        "demonstration with the same paragraph and labels in that order; five enumerated field-name replacements",
        "retry policy v3 in the runner: the ONE retry after schema_invalid/contract_invalid carries the model's call "
        "and the validator's messages; identical budget (PARSE_RETRIES = 1); every other code keeps the v2 behaviour",
        "normaliser maps the nested payload to the v1.0 public shape; evaluator untouched",
    ]
    base["provenance"]["renderer_v11_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return base


RETRY_POLICY_VERSION = "3"   # one feedback retry after schema_invalid/contract_invalid; identical budget


class Bound:
    """The v1.0-compatible surface of one variant, so `run_extract.py` and `preflight.py` can bind
    `ins = instrument_v11.bind("S")` and keep calling `ins.load_artifacts()`, `ins.build_request()`,
    `ins.validate_tool_use()` and friends unchanged. Everything the runner records about the
    instrument (version, variant, pins path, prompt path, module digests) is exposed here too."""

    def __init__(self, variant: str):
        if variant not in VARIANTS:
            raise InstrumentError(f"unknown instrument v1.1 variant {variant!r}; choose one of {VARIANTS}")
        self.variant = variant
        self.explanation = variant == "E"
        self.typed = variant == "T"
        self.INSTRUMENT_VERSION = VERSION_OF[variant]
        self.RETRY_POLICY_VERSION = RETRY_POLICY_VERSION
        self.PINS = PINS_OF[variant]
        self.PROMPT_PATH_REL = f"tools/pipeline/instrument/system-prompt_v{VERSION_OF[variant]}.txt"
        self.MODELS, self.REGION, self.CACHE_TTL, self.MIN_CACHE_PREFIX = MODELS, REGION, CACHE_TTL, MIN_CACHE_PREFIX
        self.TOOL_NAME = TOOL_NAME
        self.THINKING_CONTRACT = THINKING_CONTRACT
        self.select_tool_use, self.validate_cache_details = select_tool_use, validate_cache_details
        self.load_codebook, self.canonical_json, self.sha256_text = load_codebook, canonical_json, sha256_text
        self.InstrumentError, self.Artifacts = InstrumentError, Artifacts
        self.build_request, self.build_feedback_request = build_request, build_feedback_request
        typed = self.typed
        self.validate_tool_use = lambda block, art, doc, subtypes: validate_tool_use(block, art, doc, subtypes, typed=typed)
        self.validate_response = lambda payload, doc, subtypes: validate_response(payload, doc, subtypes, typed=typed)
        self.normalise = lambda payload: normalise(payload, typed=typed)
        self.ROOT = ins.ROOT
        self.module_sha256 = {"instrument.py": hashlib.sha256((HERE / "instrument.py").read_bytes()).hexdigest(),
                              "instrument_v11.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

    def load_artifacts(self, pins: dict | None = None) -> Artifacts:
        return load_artifacts(self.variant, pins)

    def read_pins(self) -> dict:
        return read_pins(self.variant)

    def read_pins_checked(self) -> dict:
        pins = read_pins(self.variant)
        if not pins:
            raise InstrumentError(f"{self.PINS.name} is missing or empty; run instrument_v11.py --variant "
                                  f"{self.variant} --update-pins")
        return pins


def bind(variant: str) -> Bound:
    return Bound(variant)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", choices=VARIANTS, required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--update-pins", action="store_true")
    ap.add_argument("--pinned-on", default=None)
    ap.add_argument("--diff", action="store_true")
    a = ap.parse_args()
    doc, subtypes, cb_sha = load_codebook()
    ins.check_projection(doc)
    explanation, typed = a.variant == "E", a.variant == "T"
    prompt = render_prompt(doc, subtypes, cb_sha, explanation, typed)
    cfg = canonical_json(build_tool_config(doc, subtypes, explanation, typed))
    manifest = canonical_json(build_manifest(doc, cb_sha, prompt, cfg, a.variant))
    v = VERSION_OF[a.variant]
    p_path, c_path, m_path = (OUT / f"system-prompt_v{v}.txt", OUT / f"tool-config_v{v}.json", OUT / f"manifest_v{v}.json")
    p_sha, c_sha = sha256_text(prompt), sha256_text(cfg)
    print(f"instrument v{v} over codebook v{doc['version']} sha256 {cb_sha[:16]}")
    print(f"  system prompt {len(prompt.splitlines()):>4} lines  sha256 {p_sha[:16]}")
    print(f"  tool config                sha256 {c_sha[:16]}")
    pins = read_pins(a.variant)
    states = {"system_prompt": ins.pin_state(pins, "system_prompt", p_path, p_sha),
              "tool_config": ins.pin_state(pins, "tool_config", c_path, c_sha)}
    print("  pins: " + "   ".join(f"{k}={x}" for k, x in states.items()))
    if a.diff:
        import difflib
        for path, fresh in ((p_path, prompt), (c_path, cfg), (m_path, manifest)):
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            d = list(difflib.unified_diff(old.splitlines(), fresh.splitlines(), f"{path.name} (on disk)",
                                          f"{path.name} (rendered)", lineterm="", n=1))
            print(f"\n-- {path.name}: " + (f"{len(d)} diff line(s)" if d else "identical"))
            for line in d[:80]:
                print("   " + line)
        return
    if a.write and not a.update_pins:
        bad = {k: x for k, x in states.items() if x not in ("match", "unpinned")}
        if bad:
            sys.exit(f"REFUSING to write: the render no longer matches the pins: {bad}")
    if a.write or a.update_pins:
        OUT.mkdir(exist_ok=True)
        for path, text in ((p_path, prompt), (c_path, cfg), (m_path, manifest)):
            path.write_text(text, encoding="utf-8")
        if not a.update_pins:
            print(f"  wrote 3 artefacts; {PINS_OF[a.variant].name} untouched")
            return
        cbpin = ins.read_pins().get("codebook") or {}
        new = {"_comment": [f"Pins of instrument v{v} (W3, post-registration development). Same rules as pins.json: "
                            "every pin has a path and a sha256 of the bytes on disk; --update-pins is the explicit "
                            "acceptance; the v1.0 pins in pins.json are not touched."],
               "codebook": dict(cbpin)}
        for key, path, digest in (("system_prompt", p_path, p_sha), ("tool_config", c_path, c_sha)):
            new[key] = {"path": str(path.relative_to(ins.ROOT / "tools")), "sha256": digest,
                        "codebook_version": str(doc["version"]), "codebook_sha256": cb_sha,
                        "pinned_on": a.pinned_on or date.today().isoformat()}
        PINS_OF[a.variant].write_text(canonical_json(new), encoding="utf-8")
        print(f"  wrote 3 artefacts and {PINS_OF[a.variant].name}")
        return
    bad = {k: x for k, x in states.items() if x not in ("match", "unpinned")}
    if bad:
        sys.exit(f"REFUSING to certify: {bad}")
    print("  certified" if "unpinned" not in states.values() else "  NOT CERTIFIED: unpinned")


if __name__ == "__main__":
    main()
