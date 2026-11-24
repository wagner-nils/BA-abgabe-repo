#!/usr/bin/env python3
"""M5a — render the production instrument from the frozen codebook.

Emits three artifacts and nothing else:

    instrument/system-prompt_v<codebook>.txt    the exact system text M4 sends
    instrument/tool-config_v<codebook>.json     the exact Converse toolConfig M4 sends
    instrument/manifest_v<codebook>.json        hashes, provenance, and what was excluded

    .venv/bin/python3 tools/pipeline/instrument.py            render, compare against pins
    .venv/bin/python3 tools/pipeline/instrument.py --update-pins   accept the render as the baseline
    .venv/bin/python3 tools/pipeline/instrument.py --diff      show what changed against the pins

WHY THIS MODULE EXISTS. The thesis compares human labels with model labels, and the
comparison only means something if both received the same instrument. Before this
module the production prompt did not exist, so the claim could not be checked --
`codebook_reader.py` reaches the enumeration lists and not the prose that carries
the actual definitions. Plan §A.6 requires the prompt to be generated and never
hand-edited, "and the only reliable defence is that hand-editing is impossible".

Every design decision here is written up, with its citation or its argument, in
INSTRUMENT-DESIGN.md. The decision labels below (D1, D8, D13 …) refer to it. Read
that file before changing anything in the projection: several exclusions look
arbitrary and are not.

WHAT THIS MODULE IS NOT. §A.6 requires the codebook to emit three artifacts --
prompt, schema, and the human annotation sheet. This emits the first two. M5 is
therefore incomplete, and the two pins going non-null does not close it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:                                      # pragma: no cover
    sys.exit("PyYAML is required. Run: uv sync   (see ../../pyproject.toml)")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CODEBOOK = ROOT / "tools" / "annotation" / "codebook" / "codebook-v1.0.yaml"
OUT = HERE / "instrument"
PINS = HERE / "pins.json"

sys.path.insert(0, str(ROOT / "tools" / "annotation"))
import codebook_reader                                           # noqa: E402

TOOL_NAME = "record_relations"

# ─── the projection ──────────────────────────────────────────────────────────
# Every source path is classified. An unclassified path is a HARD FAILURE, not a
# silent omission: that is what stops a future codebook field from slipping into
# or out of the instrument unnoticed (D9).
#
#   prompt      → rendered into the system prompt
#   schema      → shapes the tool schema
#   both        → rendered and shapes the schema
#   manifest    → recorded in the manifest, not sent to the model
#   provenance  → describes how the instrument was built; excluded (D8)
#   human_only  → part of the human workflow; the model is not asked for it (D7)
#   redundant   → says nothing the rendered fields do not already say (D8)
TOP_LEVEL = {
    "unit_of_analysis":    "prompt",
    "perspective":         "prompt",
    "technologies":        "container",
    "frame_elements":      "container",
    "frame_derivation":    "both",
    "no_frame_present":    "container",
    "evaluation":          "container",
    "comparison_target":   "container",
    "exclusion_principles": "container",
    "prompt":              "container",
    "speaker":             "human_only",
    "confidence":          "human_only",
    "version":             "manifest",
    "created":             "provenance",
    "status":              "provenance",
    "frozen":              "provenance",
    "frozen_on":           "provenance",
    "frozen_after":        "provenance",
    "supersedes":          "provenance",
    "calibration_evidence": "provenance",
}

FIELDS = {
    "technologies": {
        "id": "both", "label": "prompt", "definition": "prompt",
        "subtype_field": "schema",
        "status": "provenance",        # `storage: PROVISIONAL` is governance history
        "note": "redundant",           # the enum's existence is the instruction (D8)
    },
    "frame_elements": {
        "id": "both", "label": "prompt", "frame": "both",
        "fires_when": "prompt", "does_not_fire_when": "prompt",
        "boundary": "prompt", "boundary_vs_F5a": "prompt",
        "recall_warning": "prompt",    # "no mechanism required" is a real boundary
        "requires_note": "both",
        "note": "redundant",           # F2c restates routing; F7b is rationale (D8)
        "a0_evidence": "provenance",   # bare A0 paragraph ids, no instruction
        "added_in": "provenance", "split_in_v1_0": "provenance",
        "supersedes": "provenance",
    },
    "no_frame_present": {
        "label": "prompt",
        "fires_when": "prompt",
        "hard_rule": "prompt",         # "does NOT mean I could not decide" — load-bearing
        "why": "provenance",           # the A0 story: A002_p03 and A019_p12 (D8)
        "note": "redundant",           # reporting rationale; the outcome enum says it
    },
    "evaluation": {
        "litmus_test": "prompt",
        "values": "both",
        "excluded_value": "prompt",    # `uncertain` is deliberately absent — matters
                                       # MORE for the model, which has no confidence field
        "calibration_rule": "prompt",  # the conservative tie-break (D4)
    },
    "comparison_target": {
        "definition": "prompt",
        "values": "both",
        "rules": "prompt",
        "resolved_open_decision": "provenance",   # v0.1-v0.3 decision history
        "retired_in_v1_0": "provenance",          # the dead `tiebreak_used` column
    },
    "prompt": {
        "abstention_instruction": "prompt",   # rendered verbatim (D5)
        "system": "provenance",               # a note about this renderer
        "language": "provenance",             # ablation bookkeeping (D6)
        "cache_point": "provenance",          # run configuration, not instruction
    },
    "exclusion_principles": {"id": "prompt", "rule": "projected"},
}

# ─── D13: the transformation map, keyed by SOURCE PATH ───────────────────────
# Some fields instruct the coder to fill a field the model is not asked for, and
# some carry calibration provenance inside otherwise operative prose. Handing the
# model the original text plus a disclaimer would be worse than transforming it: it
# gives the model two incompatible instructions and relies on it inferring which
# wins. Every transformation records its reason, its exact replacement and what
# authorises it.
#
# This started as a map over the exclusion rules only. Reading the first rendered
# prompt found three more paths with the same defect -- `perspective` pointed the
# model at a `speaker` field it does not have, `evaluation.excluded_value` pointed
# it at `confidence`, and F5b's boundary wrapped its operative rule in "ADDED IN
# v0.3, from A0b. The annotator wrote ...". Fail-on-unknown cannot catch that:
# the field is classified, and the problem is inside its prose. The map is keyed by
# path for that reason.
TRANSFORMS: dict[str, dict] = {
    "perspective": {
        "reason": "ends with \"is recorded separately in the `speaker` field\" -- a field the "
                  "model is not asked for (D7).",
        "authority": "D7; the plan's fourth-entry decision dropped speaker from the "
                     "quantitative output.",
        "text": "The label captures WHAT THE PARAGRAPH ASSERTS about the technology-frame "
                "relation. It is not what a reader would feel, and it is not whether the "
                "outlet endorses the claim.",
    },
    "evaluation.excluded_value": {
        "reason": "ends with \"Annotator uncertainty goes in `confidence`\" -- a field the "
                  "model is not asked for (D7).",
        "authority": "D7; human confidence records coder uncertainty, which is a different "
                     "quantity from a model self-report and has no stated analysis.",
        "text": "There is deliberately no `uncertain` value. It would be a statement about the "
                "coder rather than about the text, and mixing an epistemic label into a "
                "semantic label set makes the distribution uninterpretable and inflates "
                "apparent disagreement. Choose the semantic label the text supports.",
    },
    "frame_elements.F4b.recall_warning": {
        "reason": "the operative rule -- no stated causal mechanism is required -- is followed "
                  "by two label priors: P034's \"single largest code in energy opinion "
                  "writing\" and \"expect this to be the hardest element for humans too\". "
                  "Telling the model an element is common, or hard, is exactly the prior D8 "
                  "excludes. Found by reading the rendered prompt, not by the tests.",
        "authority": "D8: exclude aggregate prevalence and difficulty claims. The rule and its "
                     "example are kept.",
        "text": "A frame does NOT require a stated causal mechanism. \"Wind strengthens "
                "Germany's energy security\", with no explanation of how, fires F4b.",
    },
    "evaluation.values.neutral.definition": {
        "reason": "ends with \"News prose is overwhelmingly factual in register, so this class "
                  "will be large, and how you define it will drive the numbers\" -- a direct "
                  "statement that one label should be frequent.",
        "authority": "D8: exclude prevalence claims.",
        "text": "A factual mention within the frame that would gratify neither side.",
    },
    "evaluation.values.mixed.definition": {
        "reason": "the operative sentence is followed by SemEval provenance explaining where "
                  "the value was inherited from. Not a prior, but not an instruction either.",
        "authority": "D8: exclude decision provenance; the operative sentence is kept.",
        "text": "Both a positive and a negative evaluation are present on the SAME frame.",
    },
    "prompt.abstention_instruction": {
        "reason": "reads \"return an empty relation list and set no_frame_present\" -- but "
                  "`no_frame_present` is no longer an outcome value after D12 collapsed three "
                  "states into two. Rendered verbatim it contradicts the output contract two "
                  "sections later. My own simplification created this; the verbatim-appearance "
                  "test blessed it.",
        "authority": "D12: the outcome vocabulary is off_topic | technologies_present, and a "
                     "technology named but unframed is a relation with empty `elements`.",
        "text": "Do not invent a relation, an element or an evaluation to fill the schema. "
                "Where the paragraph makes a statement about a technology but frames nothing "
                "about it, give that technology a relation with an empty `elements` list. "
                "Where it makes no statement about any listed technology at all, set `outcome` "
                "to `off_topic` and return no relations.",
    },
    "no_frame_present.hard_rule": {
        "reason": "names the retired `no_frame_present` field three times. Its operative "
                  "content -- an unmatched element is F9 plus a note, never a claim that "
                  "nothing was framed -- must survive in the new vocabulary.",
        "authority": "D12, as above.",
        "text": "An empty `elements` list means THE TEXT DOES NOT FRAME this technology. It "
                "does NOT mean \"I could not find an element that fits\". If you believe "
                "something is being framed but no element matches, that is F9 plus a note -- "
                "never an empty `elements` list.",
    },
    "comparison_target.rules": {
        "reason": "the `multiple` rule illustrates itself with a calibration paragraph id "
                  "(A004_p04) -- which is also one of the three paragraphs coded in both A0b "
                  "and A1. The example text is what defines the boundary; the id is provenance.",
        "authority": "D8: keep a calibration-derived example where it defines the operative "
                     "boundary, drop the identifier.",
        "text": [
            "`none` is a substantive value and NOT missing data.",
            # "and should now be rare" removed: a prevalence prior in my OWN replacement
            # text, which the sentinel tests cannot reach by construction.
            "`other` requires a note.",
            "`multiple` where the text compares against more than one reference point, for "
            "example against \"nuclear and coal\" together. List them in `comparison_note`. "
            "Single values stay single so that the agreement figure remains computable.",
            "A technology named ONLY as a reference point does not get its own relation unless "
            "the text also evaluates it in its own right.",
        ],
    },
    "frame_elements.F6b.boundary_vs_F5a": {
        "reason": "the operative rule -- go by the CAUSE, not the effect -- is wrapped in "
                  "version narrative (\"ADDED IN v0.3, from A0b\") and in who said it (\"The "
                  "annotator wrote ...\").",
        "authority": "D8: include a calibration-derived example where it defines the operative "
                     "boundary; exclude the version narrative and the attribution. The example "
                     "itself is kept.",
        "text": "Go by the CAUSE, not the effect. Where the cause is weather, season or "
                "variability, it is an availability claim and fires F5a: \"weak wind conditions "
                "cut the wind turbines' output by 3.2 percent\" is F5a, because the wind did not "
                "blow. Where the cause is the design, the site or the technology's efficiency, "
                "it is a performance claim and fires F6b: \"PV on noise barriers yields X "
                "kWh/m2\" is F6b. Both fire when the text makes both claims.",
    },
}

XRULES = {
    "X1": ("include", None),
    "X2": ("include", None),
    "X3": ("transform", {
        "reason": "instructs `set speaker`, a field the model is not asked for (D7). "
                  "The first sentence is load-bearing and must survive.",
        "authority": "PROTOTYPE-PLAN.md fourth-entry decision: speaker dropped from the "
                     "quantitative output; the human field survives as a note-taking aid.",
        "text": "Code evaluations voiced by quoted or attributed sources as evaluations "
                "asserted in the paragraph, but do not infer that the outlet endorses them. "
                "Where several voices evaluate the same technology and frame in opposite "
                "directions, record `mixed` on the single relation for that technology; do "
                "not create one relation per speaker.",
    }),
    "X4": ("transform", {
        "reason": "instructs `set confidence: 1`, a field the model is not asked for (D7). "
                  "The paragraph-is-the-unit rule must survive.",
        "authority": "D7; the human confidence field records coder uncertainty, which is not "
                     "the same quantity as a model's self-report and has no stated analysis.",
        "text": "Use only the paragraph in front of you. Do not resolve a technology, an "
                "antecedent or an evaluation from the headline, the neighbouring paragraphs "
                "or the rest of the article.",
    }),
    "X5": ("include", None),
    "X6": ("include", None),
    "X7": ("transform", {
        "reason": "the operative per-technology rule is buried under a literature statistic "
                  "(25-69 accuracy points, P040) and a corpus prevalence figure (41 % "
                  "multi-technology). Prevalence is excluded by D8.",
        "authority": "D8: exclude aggregate prevalence and article-frequency claims.",
        "text": "Do not read a technology's evaluation off the article's overall tone. Where "
                "several technologies appear in one paragraph, judge each one separately on "
                "what the paragraph says about that technology.",
    }),
    "X8": ("exclude", "spreadsheet-entry hygiene (\"the technology columns take 0 or 1\"). "
                      "The JSON schema enforces types; there are no columns to mistype."),
    "X9": ("exclude", "its own first sentence calls it a MEASURED COST, \"not as a rule "
                      "change\". X4 and X11 already carry the operative rules; what remains "
                      "is a calibration paragraph, a 5 % prevalence figure and a limitations "
                      "conclusion. Excluded by D8."),
    "X10": ("exclude", "F2c's fires_when and does_not_fire_when already carry the operative "
                       "rule with its examples. The X10 block is superseded-decision "
                       "narrative plus prevalence."),
    "X11": ("transform", {
        "reason": "the operative rule is followed by an A0 anecdote naming a calibration "
                  "paragraph and the annotator's note.",
        "authority": "D8: include a calibration-derived example only where it defines the "
                     "operative boundary; this one does not, the rule is already explicit.",
        "text": "Tick only the technologies the paragraph itself makes a statement about. "
                "Do not add a technology because you know it is implied by a policy, a "
                "programme or a company named in the text. That is world knowledge, not text.",
    }),
}

# ─── loading ─────────────────────────────────────────────────────────────────
def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def load_codebook() -> tuple[dict, dict, str]:
    """Parse the codebook, and recover the two comment-only subtype lists.

    A documented hybrid, and a v1.0 legacy exception. The subtype values exist
    only as a YAML comment --

        subtype_field: wind_siting        # onshore | offshore | unspecified

    -- so `safe_load` returns `'wind_siting'` and discards the values, while
    `codebook_reader` recovers them by regular expression. Three assertions bind
    the two readers together so they cannot silently describe different files.
    The clean fix is a codebook revision storing them as real YAML; frozen v1.0
    is not edited to achieve it.
    """
    raw = CODEBOOK.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    legacy = codebook_reader.load(CODEBOOK)

    if legacy.version != str(doc.get("version")):
        raise SystemExit(f"reader disagreement: yaml says version {doc.get('version')!r}, "
                         f"codebook_reader says {legacy.version!r}")
    declared = {t["subtype_field"] for t in doc["technologies"] if "subtype_field" in t}
    if declared != set(legacy.subtypes):
        raise SystemExit(f"reader disagreement on subtype fields: yaml {sorted(declared)} vs "
                         f"codebook_reader {sorted(legacy.subtypes)}")
    empty = [k for k, v in legacy.subtypes.items() if not v]
    if empty:
        raise SystemExit(f"subtype value list(s) empty: {empty}. The comment they live in was "
                         f"probably reformatted; see load_codebook().")
    return doc, legacy.subtypes, sha256_text(raw)


def check_projection(doc: dict) -> None:
    """Every path classified, or the build fails. No silent omissions (D9)."""
    unknown = sorted(set(doc) - set(TOP_LEVEL))
    if unknown:
        raise SystemExit(
            f"unclassified top-level codebook field(s): {unknown}\n"
            f"The projection must say whether each one instructs the model or describes the "
            f"instrument. Add it to TOP_LEVEL and record the reason in INSTRUMENT-DESIGN.md.")
    for key, table in FIELDS.items():
        node = doc.get(key)
        items = node if isinstance(node, list) else [node] if isinstance(node, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            bad = sorted(set(item) - set(table))
            if bad:
                raise SystemExit(
                    f"unclassified field(s) {bad} under {key}"
                    f"{' → ' + str(item.get('id')) if item.get('id') else ''}\n"
                    f"Add them to FIELDS[{key!r}] and record the reason.")
    missing = sorted(x["id"] for x in doc["exclusion_principles"] if x["id"] not in XRULES)
    if missing:
        raise SystemExit(f"exclusion principle(s) {missing} have no treatment in XRULES. "
                         f"Every rule is include, transform or exclude — see D13.")
    stale = sorted(set(TRANSFORMS) - _transform_paths(doc))
    if stale:
        raise SystemExit(f"TRANSFORMS names path(s) that no longer exist in the codebook: "
                         f"{stale}. A transformation whose source has moved is silently "
                         f"replacing nothing.")


def _transform_paths(doc: dict) -> set[str]:
    """Every path a transformation may legitimately target."""
    out = {"perspective", "unit_of_analysis"}
    for key in ("evaluation", "comparison_target", "no_frame_present", "prompt"):
        out |= {f"{key}.{k}" for k in doc.get(key, {})}
    for v in doc["evaluation"]["values"]:
        out |= {f"evaluation.values.{v['id']}.{k}" for k in v}
    for el in doc["frame_elements"]:
        out |= {f"frame_elements.{el['id']}.{k}" for k in el}
    for t in doc["technologies"]:
        out |= {f"technologies.{t['id']}.{k}" for k in t}
    return out


def projected(path: str, original: object):
    """What actually reaches the model for one source path.

    Returns a string for a scalar source and a list for a list source, so a
    transformation can replace a whole rule list without the renderer having to
    know which paths are lists.
    """
    t = TRANSFORMS.get(path)
    if t:
        return t["text"]
    if isinstance(original, list):
        return [flow(x) for x in original]
    return flow(original)


# ─── rendering ───────────────────────────────────────────────────────────────
def flow(text: object) -> str:
    """Collapse a folded YAML scalar to one paragraph. Whitespace only."""
    return re.sub(r"\s+", " ", str(text)).strip()


def block(title: str, body: str) -> str:
    return f"## {title}\n\n{body.rstrip()}\n"


def render_prompt(doc: dict, subtypes: dict, cb_sha: str) -> str:
    """The system prompt. Declaration order is preserved everywhere (D10)."""
    p: list[str] = []
    p.append("# Coding instrument\n")
    p.append(
        f"You are applying a frozen research codebook to one news paragraph. This instrument "
        f"is codebook v{doc['version']}, sha256 {cb_sha[:16]}, rendered mechanically from that "
        f"file. Apply it as written: where the codebook draws a boundary, that boundary decides "
        f"the case, not your own judgement of what the paragraph is about.\n")

    p.append(block("Unit of analysis", projected("unit_of_analysis", doc["unit_of_analysis"])))
    p.append(block("Perspective", projected("perspective", doc["perspective"])))

    # technologies — full definitions, not names (D1)
    t = ["Tick every technology the paragraph makes a statement about. A paragraph may "
         "make statements about several.\n"]
    for tech in doc["technologies"]:
        t.append(f"**{tech['id']}** — {tech['label']}\n: {flow(tech['definition'])}")
        if "subtype_field" in tech:
            vals = " | ".join(subtypes[tech["subtype_field"]])
            t.append(f": Record `{tech['subtype_field']}` for this technology, one of: {vals}.")
        t.append("")
    p.append(block("Technologies", "\n".join(t)))

    # frame elements — fires / does not fire / boundaries (D1)
    fe = ["You mark ELEMENTS. The frame is derived from them and is never coded directly.\n"]
    for el in doc["frame_elements"]:
        fe.append(f"**{el['id']}** — {el['label']} · frame `{el['frame']}`")
        fe.append(f": Fires when: {flow(el['fires_when'])}")
        for field, lead in (("does_not_fire_when", "Does NOT fire when"),
                            ("boundary", "Boundary"),
                            ("boundary_vs_F5a", "Boundary against F5a"),
                            ("recall_warning", "Note on recall")):
            if field in el:
                path = f"frame_elements.{el['id']}.{field}"
                fe.append(f": {lead}: {projected(path, el[field])}")
        if el.get("requires_note"):
            fe.append(": A note is REQUIRED when this element fires.")
        fe.append("")
    p.append(block("Frame elements", "\n".join(fe)))

    deriv = "\n".join(f"- `{k}` ← {', '.join(v)}" for k, v in doc["frame_derivation"].items())
    p.append(block("How frames derive from elements",
                   "An element firing means its frame fired.\n\n" + deriv))

    nfp = doc["no_frame_present"]
    p.append(block("When nothing frames the technology",
                   "\n\n".join(projected(f"no_frame_present.{k}", nfp[k]) for k in nfp
                                if FIELDS["no_frame_present"].get(k) == "prompt")))

    # evaluation — litmus test, values, tie-breaks (D1, D4)
    ev = doc["evaluation"]
    e = ["Coded PER FIRED FRAME, not once per paragraph.\n",
         f"**The test:** {flow(ev['litmus_test'])}\n"]
    for v in ev["values"]:
        d_ = projected(f"evaluation.values.{v['id']}.definition", v["definition"])
        e.append(f"- `{v['id']}` — {d_}")
    e.append("\n" + projected("evaluation.excluded_value", ev["excluded_value"]))
    e.append(f"\n{flow(ev['calibration_rule'])}")
    p.append(block("Evaluation", "\n".join(e)))

    ct = doc["comparison_target"]
    c = [flow(ct["definition"]), ""]
    c += [f"- `{v}`" for v in ct["values"]]
    c.append("")
    c += [f"- {r}" for r in projected("comparison_target.rules", ct["rules"])]
    p.append(block("Comparison target", "\n".join(c)))

    # exclusion principles — projected, prominent (D3, D13)
    x = ["These decide the hard cases. Where one of them applies, it overrides an "
         "impression that the paragraph is 'about' a technology.\n"]
    for rule in doc["exclusion_principles"]:
        treatment, extra = XRULES[rule["id"]]
        if treatment == "include":
            x.append(f"- **{rule['id']}** — {flow(rule['rule'])}")
        elif treatment == "transform":
            x.append(f"- **{rule['id']}** — {extra['text']}")
    p.append(block("What does NOT count", "\n".join(x)))

    p.append(block("Abstention",
                   projected("prompt.abstention_instruction",
                             doc["prompt"]["abstention_instruction"])))

    p.append(block(
        "Output contract",
        "Return one call to the `record_relations` tool and nothing else.\n\n"
        "Set `outcome` to `off_topic` when the paragraph makes no statement about any "
        "technology listed above, and leave `relations` empty. Otherwise set it to "
        "`technologies_present`.\n\n"
        "Give **one relation per technology the paragraph makes a statement about** — "
        "including a technology it names but frames nothing about, whose `elements` list "
        "is then empty. Do not merge two technologies into one relation, and do not repeat "
        "a technology.\n\n"
        "Every element you list fires for THAT technology. Its frame then needs exactly one "
        "evaluation in `evaluations`, and a frame that did not fire must not carry one.\n\n"
        "Speaker attribution and coder confidence are recorded by the human coder only and "
        "are not part of this response."))
    return "\n".join(p).rstrip() + "\n"


def build_tool_config(doc: dict, subtypes: dict) -> dict:
    """The exact Converse toolConfig M4 sends. Pinned as one object (D10)."""
    techs = [t["id"] for t in doc["technologies"]]
    elements = [e["id"] for e in doc["frame_elements"]]
    frames = list(doc["frame_derivation"])
    evals = [v["id"] for v in doc["evaluation"]["values"]]
    targets = [v["id"] if isinstance(v, dict) else v for v in doc["comparison_target"]["values"]]
    needs_note = [e["id"] for e in doc["frame_elements"] if e.get("requires_note")]

    relation = {
        "type": "object", "additionalProperties": False,
        "required": ["technology", "elements", "evaluations", "comparison_target"],
        "properties": {
            "technology": {"type": "string", "enum": techs},
            "elements": {"type": "array", "items": {"type": "string", "enum": elements},
                         "uniqueItems": True,
                         "description": "Every element that fires for THIS technology. An "
                                        "empty list means the paragraph makes a statement "
                                        "about this technology but frames nothing about it."},
            "evaluations": {
                "type": "object", "additionalProperties": False,
                "description": "One entry per fired frame; none for a frame that did not fire.",
                "properties": {f: {"type": "string", "enum": evals} for f in frames}},
            "comparison_target": {"type": "string", "enum": targets},
            "comparison_note": {"type": "string",
                                "description": "Required when comparison_target is `other` "
                                               "or `multiple`."},
            "note": {"type": "string",
                     "description": "Required when any of "
                                    + ", ".join(f"`{e}`" for e in needs_note) + " fires."},
        },
    }
    for tech in doc["technologies"]:
        if "subtype_field" in tech:
            relation["properties"][tech["subtype_field"]] = {
                "type": "string", "enum": subtypes[tech["subtype_field"]],
                "description": f"Only for technology `{tech['id']}`."}

    return {
        "tools": [{"toolSpec": {
            "name": TOOL_NAME,
            "description": "Record the technology-frame-evaluation relations asserted in one "
                           "news paragraph, exactly as the coding instrument defines them.",
            "inputSchema": {"json": {
                "type": "object", "additionalProperties": False,
                "required": ["outcome", "relations"],
                "properties": {
                    # Two states, not three. An earlier draft carried
                    # off_topic | no_frame_present | relations_present plus a
                    # separate technologies_present array -- which left a real
                    # ambiguity: in a paragraph where one technology is framed and
                    # another is merely named, does the unframed one get a relation?
                    # That question silently moves the paragraph x technology
                    # denominator, which is precisely the defect A1 exposed on the
                    # human side. Resolved by mirroring the human instrument: every
                    # technology the paragraph makes a statement about gets a
                    # relation, and an EMPTY `elements` list is the per-technology
                    # no-frame state -- the model's equivalent of the sheet's
                    # `no_frame_for_this_tech`. Paragraph-level "nothing framed" is
                    # then derived, not asserted twice.
                    "outcome": {"type": "string",
                                "enum": ["off_topic", "technologies_present"],
                                "description": "`off_topic` when the paragraph makes no "
                                               "statement about any listed technology; "
                                               "`technologies_present` otherwise."},
                    "relations": {"type": "array", "items": relation},
                }}},
        }}],
        # Forced, so a run cannot silently return prose instead of a record.
        "toolChoice": {"tool": {"name": TOOL_NAME}},
    }


def validate_response(payload: dict, doc: dict, subtypes: dict) -> list[str]:
    """The relational rules JSON Schema cannot express. Returns problems; empty is valid.

    The schema pins vocabulary and shape and stops there: it will happily accept
    `off_topic` alongside three relations, the same technology twice, an evaluation
    for a frame that never fired, or `wind_siting` on solar. Listing those
    invariants in a manifest and enforcing none of them would be a contract in name
    only, so they live here -- next to the schema they complete -- and M4 calls this
    on every response before anything is written.
    """
    problems: list[str] = []
    techs = {t["id"] for t in doc["technologies"]}
    frame_of = {e["id"]: e["frame"] for e in doc["frame_elements"]}
    needs_note = {e["id"] for e in doc["frame_elements"] if e.get("requires_note")}
    subtype_owner = {t["subtype_field"]: t["id"] for t in doc["technologies"]
                     if "subtype_field" in t}

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

        fired = list(r.get("elements") or [])
        frames_fired = {frame_of[e] for e in fired if e in frame_of}
        evals = r.get("evaluations") or {}
        for f in sorted(frames_fired - set(evals)):
            problems.append(f"{where}: frame `{f}` fired but carries no evaluation")
        for f in sorted(set(evals) - frames_fired):
            problems.append(f"{where}: evaluation for `{f}`, which did not fire")

        if (set(fired) & needs_note) and not (r.get("note") or "").strip():
            problems.append(f"{where}: {sorted(set(fired) & needs_note)} fired, which requires "
                            f"a note")
        if r.get("comparison_target") in ("other", "multiple") \
                and not (r.get("comparison_note") or "").strip():
            problems.append(f"{where}: comparison_target is "
                            f"{r.get('comparison_target')!r}, which requires a "
                            f"comparison_note")
        for field, owner in subtype_owner.items():
            if field in r and tech != owner:
                problems.append(f"{where}: carries `{field}`, which belongs to `{owner}`")
            # Required, not merely permitted. The prompt says "Record" the subtype and the
            # enum offers `unspecified` for the case the text does not say, so an absent
            # field is a missing value rather than an inapplicable one.
            if tech == owner and field not in r:
                problems.append(f"{where}: is missing `{field}`, which this technology "
                                f"requires (use `unspecified` where the text does not say)")

        # A comparison target attaches to an evaluation, which attaches to a fired frame.
        # Nothing fired, so there is nothing to compare against.
        if not fired and r.get("comparison_target") not in (None, "none"):
            problems.append(f"{where}: frames nothing, so comparison_target must be `none`, "
                            f"not {r.get('comparison_target')!r}")
    return problems




def select_tool_use(blocks, *, allow_reasoning: bool = False) -> tuple[dict | None, str]:
    """The ONE way a response's content is reduced to a payload (Codex W1 rounds 1–2, #7/#8).

    Returns (toolUse block, "ok") only when every content block is a single-key object, no
    block is `reasoningContent`, and EXACTLY one block is a `toolUse` whose value is an
    object. Both consumers (gate and runner) used `next(...)`, which silently took the first
    of several tool calls -- an off-topic call followed by a contradictory relation was
    accepted as off-topic, and reversed order flipped the verdict. A block carrying two keys
    (`{"toolUse": ..., "reasoningContent": ...}`) or a non-object block is malformed and is
    never read around. A `reasoningContent` block means the model ran with thinking on, which
    the pinned request does not permit; its ABSENCE is not evidence of anything (adaptive
    thinking may emit no block on a simple request) -- the run record states the thinking
    contract of the request, not an inference from the response (THINKING_CONTRACT).

    §4m (2026-11-08): `allow_reasoning=True` only for the configurations whose request asks for thinking
    (`THINKING_ON`); there a `reasoningContent` block beside the one toolUse is accepted and never read. Every other
    rule holds unchanged."""
    if not isinstance(blocks, list):
        return None, "malformed: content is not a list"
    kinds = []
    for b in blocks:
        if not isinstance(b, dict) or len(b) != 1:
            return None, ("malformed: a content block is not a single-key object ("
                          + (f"keys {sorted(b)}" if isinstance(b, dict) else type(b).__name__) + ")")
        kinds.append(next(iter(b)))
    if "reasoningContent" in kinds and not allow_reasoning:
        return None, f"reasoning_content: blocks={kinds}"
    uses = [b["toolUse"] for b in blocks if "toolUse" in b]
    if not uses:
        return None, "none"
    if len(uses) > 1:
        return None, f"multiple: {len(uses)} toolUse blocks ({kinds})"
    if not isinstance(uses[0], dict):
        return None, "malformed: toolUse is not an object"
    return uses[0], "ok"


def validate_cache_details(u: dict) -> tuple[bool, list[str], int, str]:
    """ONE reading of a response's `usage.cacheDetails` for the gate and the runner (Codex W1
    round 5, #5: the two disagreed on malformed breakdowns). Returns (ok, ttls, total, why).
    A key that is absent is fine (nothing to reconcile: ok, [], 0). A key that is present must
    be a list whose every entry is an object with a ttl string and a non-negative int
    inputTokens, and whose total equals `cacheWriteInputTokens` -- an empty list or a zero
    total beside a positive write counter is a contradiction, not missing evidence."""
    if "cacheDetails" not in u:
        return True, [], 0, "no cacheDetails"
    details = u.get("cacheDetails")
    if not isinstance(details, list):
        return False, [], 0, f"cacheDetails is {type(details).__name__}, not a list"
    ttls, total = [], 0
    for x in details:
        if not (isinstance(x, dict) and isinstance(x.get("ttl"), str) and isinstance(x.get("inputTokens"), int)
                and not isinstance(x.get("inputTokens"), bool) and x["inputTokens"] >= 0):
            return False, [], 0, f"malformed cacheDetails entry {x!r} (needs ttl and int inputTokens)"
        ttls.append(x["ttl"]); total += x["inputTokens"]
    write = u.get("cacheWriteInputTokens")
    write = write if isinstance(write, int) and not isinstance(write, bool) else 0
    if total != write:
        return False, sorted(set(ttls)), total, f"cacheDetails total {total} != cacheWriteInputTokens {write}"
    return True, sorted(set(ttls)), total, "ok"


# What the REQUEST says about thinking. Recorded into every run record instead of the
# former literal "off", which was an inference from the response and not a fact about the
# request (Codex W1 round 2, #8). Changes only with build_request().
THINKING_CONTRACT = ("not requested: the request carries no thinking field; for the pinned Claude 4.6 "
                     "models the provider default is thinking off. This describes the request, not the "
                     "response: a reasoningContent block in a response is a contract violation "
                     "(unexpected_content), and its absence proves nothing.")
# §4k (2026-11-01): the 5-series request and its controls disable thinking explicitly
THINKING_DISABLED_CONTRACT = ("disabled: the request carries thinking {type: disabled} in additionalModelRequestFields "
                              "(§4k). This describes the request, not the response: a reasoningContent block in a "
                              "response is a contract violation (unexpected_content).")
# §4m (2026-11-08): the 5.5 request and its control ask for adaptive thinking at a declared effort
# a str.format template: literal braces are doubled (the first version left them single, and every §4m run that thinks
# crashed while writing its record after its last call, 2026-11-08; those runs were superseded, never resumed)
THINKING_ADAPTIVE_CONTRACT = ("adaptive: the request carries thinking {{type: adaptive}} and output_config.effort={effort} "
                              "in additionalModelRequestFields (§4m; Opus 5.5 cannot disable thinking). This describes "
                              "the request, not the response: a reasoningContent block beside the single tool call is "
                              "accepted and never read or stored publicly; reasoning tokens count in the output tokens.")


def validate_tool_use(block: dict, art: "Artifacts", doc: dict,
                      subtypes: dict) -> tuple[bool, str]:
    """Validate a returned `toolUse` block: tool name, then the PINNED JSON Schema,
    then the relational contract of `validate_response()`. Returns (ok, why).

    This is the ONE validator. It used to live in `preflight.py`, which meant the
    gate and the runner could validate the same response by two different codes
    and disagree without anyone noticing -- the same drift D14 closes for the
    request builder, one step later in the call. It lives here, beside the schema
    it completes, and both `preflight.py` and the runner import it (D18, D14).

    The success message truncates the payload for a human reader; callers that
    persist verdicts publicly must persist a CODE, not this string, because the
    string can echo model output that quotes licensed text.
    """
    from jsonschema import Draft202012Validator          # declared dependency

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

    problems = validate_response(got, doc, subtypes)
    if problems:
        return False, "relational contract: " + "; ".join(problems[:4])
    return True, f"schema-valid and contract-valid: {json.dumps(got)[:300]}"


def canonical_json(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def build_manifest(doc: dict, cb_sha: str, prompt: str, cfg: str) -> dict:
    def git(*a: str) -> str:
        try:
            return subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True,
                                  text=True, timeout=30).stdout.strip()
        except Exception:
            return ""

    # Both transformation maps are archived, not just the exclusion rules. The first
    # version recorded only the X-rules, which left the four ordinary source-path
    # transformations -- perspective, the excluded evaluation value, the comparison
    # rules, F6b's boundary -- and the three prevalence removals invisible in the
    # evidence trail, even though they change what the model was told.
    transformations = {
        path: {"reason": t["reason"], "authority": t["authority"], "replacement": t["text"]}
        for path, t in TRANSFORMS.items()
    }
    transformations.update({
        f"exclusion_principles.{k}": {"reason": v[1]["reason"],
                                      "authority": v[1]["authority"],
                                      "replacement": v[1]["text"]}
        for k, v in XRULES.items() if v[0] == "transform"})

    return {
        "module": "M5a",
        "renders": ["system prompt", "tool config"],
        "does_not_render": "the human annotation sheet — see INSTRUMENT-DESIGN.md, scope",
        "codebook": {"path": str(CODEBOOK.relative_to(ROOT)),
                     "version": str(doc["version"]), "sha256": cb_sha},
        "artifacts": {"system_prompt_sha256": sha256_text(prompt),
                      "tool_config_sha256": sha256_text(cfg)},
        "provenance": {"code_input_commit": git("rev-parse", "HEAD") or "unknown",
                       "renderer_sha256": sha256_text(Path(__file__).read_text("utf-8"))[:16],
                       "python": sys.version.split()[0],
                       "pyyaml": yaml.__version__},
        "excluded_top_level": sorted(k for k, v in TOP_LEVEL.items()
                                     if v in ("provenance", "human_only")),
        "exclusion_rule_treatment": {k: v[0] for k, v in XRULES.items()},
        "transformations": transformations,
        "relational_invariants": {
            "enforced_by": "validate_response() in this module. M4 MUST call it on every "
                           "response before anything is written; M4 does not exist yet.",
            "not_expressible_in_json_schema": [
                "off_topic implies no relations, and technologies_present implies at least one",
                "one relation per technology, never repeated",
                "exactly one evaluation per fired frame, none for an unfired frame",
                "a note where a note-requiring element fires",
                "a comparison_note where comparison_target is other or multiple",
                "a subtype field only on the technology that owns it",
                "the owning subtype field REQUIRED on wind and hydrogen relations -- the "
                "instrument says to record it and offers `unspecified`, so absence is a "
                "missing value, not an inapplicable one",
                "comparison_target `none` where `elements` is empty -- a comparison target "
                "attaches to an evaluation, which attaches to a fired frame",
            ],
        },
        # Deliberately NOT an implementation-status claim. This artifact is unpinned
        # and written at render time, so any sentence here about what has or has not
        # been verified yet is stale the moment it is true. Status lives in the run
        # records under tools/runs/ and in the usage log; this field states only where
        # these bytes are consumed.
        "consumed_by": "preflight.py loads these artifacts by pin; build_request() submits "
                       "them unchanged. Verification status is recorded per run in "
                       "tools/runs/, not here.",
    }


# ─── the shared request builder ──────────────────────────────────────────────
# This is the piece that actually prevents drift, and it is the reason it lives
# here rather than in preflight: preflight and M4 must construct the request
# through the SAME function. A green preflight proves nothing if the runner then
# assembles its own request -- it would certify a different object than the one
# that runs. So there is exactly one place where "what we send" is defined, and
# both consumers call it.
#
# Loaded from the PINNED FILES, not re-rendered. Preflight's job is to verify the
# artifact on disk that a reported run will use; re-rendering here would test the
# renderer a second time and leave the pinned bytes unverified.

REGION = "eu-central-1"


class InstrumentError(RuntimeError):
    """The pinned instrument cannot be loaded, or does not match its pins.

    A distinct type because preflight has to record that as an OFFLINE assertion
    failure. It used to escape as a bare exception and be reported as "could not
    reach the API" -- a different and much less alarming thing than a pinned
    artifact that is malformed.
    """


@dataclass(frozen=True)
class Artifacts:
    """The pinned instrument, loaded from disk."""
    prompt: str
    prompt_sha256: str
    tool_config: dict
    tool_config_sha256: str
    codebook_version: str
    codebook_sha256: str


def tool_config_problems(cfg: object) -> list[str]:
    """Every structural promise `build_request()` and the response validator rely on.

    Kept separate from the renderer so it can be run against BYTES FROM DISK. The
    renderer's own output is correct by construction; the thing that needs checking
    is the file, which may have been edited, truncated or swapped.
    """
    if not isinstance(cfg, dict):
        return [f"the top level is {type(cfg).__name__}, expected an object"]
    bad: list[str] = []
    tools = cfg.get("tools")
    if not isinstance(tools, list) or len(tools) != 1:
        bad.append(f"`tools` is {type(tools).__name__} of length "
                   f"{len(tools) if isinstance(tools, list) else 'n/a'}, expected exactly one "
                   f"tool -- forced tool choice is meaningless with none, ambiguous with two")
        return bad
    spec = tools[0].get("toolSpec") if isinstance(tools[0], dict) else None
    if not isinstance(spec, dict):
        return ["`tools[0].toolSpec` is missing or not an object"]
    if spec.get("name") != TOOL_NAME:
        bad.append(f"`toolSpec.name` is {spec.get('name')!r}, expected {TOOL_NAME!r}")
    if not isinstance(spec.get("description"), str) or not spec["description"].strip():
        bad.append("`toolSpec.description` is missing or empty")
    schema = (spec.get("inputSchema") or {}).get("json") if isinstance(
        spec.get("inputSchema"), dict) else None
    if not isinstance(schema, dict):
        bad.append("`toolSpec.inputSchema.json` is missing or not an object")
    elif schema.get("type") != "object" or not isinstance(schema.get("properties"), dict) \
            or not schema["properties"]:
        bad.append("the input schema is not an object schema with properties, so it "
                   "constrains nothing")
    # toolChoice must force THIS tool. `{"auto": {}}` or a name that does not match
    # leaves the model free to answer in prose, and the whole structured-output
    # guarantee rests on it not being free to.
    choice = cfg.get("toolChoice")
    if not isinstance(choice, dict) or "tool" not in choice:
        bad.append(f"`toolChoice` is {choice!r}, expected {{'tool': {{'name': ...}}}} -- "
                   f"anything else leaves the model free not to call the tool")
    elif not isinstance(choice["tool"], dict):
        # `toolChoice: {"tool": "record_relations"}` used to reach .get() on a string
        # and raise AttributeError -- a malformed artifact escaping as an unclassified
        # exception, which is the very thing this function exists to prevent.
        bad.append(f"`toolChoice.tool` is {type(choice['tool']).__name__}, expected an object "
                   f"with a `name`")
    elif choice["tool"].get("name") != spec.get("name"):
        bad.append(f"`toolChoice` forces {choice['tool'].get('name')!r} but the only declared "
                   f"tool is {spec.get('name')!r}")
    return bad


def manifest_without_stamp(text: str) -> str:
    """The manifest with the commit stamp removed, for staleness comparison.

    `provenance.code_input_commit` is the git HEAD at render time. It is worth recording
    and worthless to compare: it changes whenever anything in the repository is
    committed, so comparing it turned the manifest check into a refusal that fires on
    every commit. Everything that describes the PROJECTION stays in the comparison --
    the codebook digest, the renderer digest, the field classification, the
    transformations, the artifact digests.
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return text                      # unparseable differs from anything; let it fail
    prov = doc.get("provenance")
    if isinstance(prov, dict):
        prov.pop("code_input_commit", None)
    return canonical_json(doc)


def load_artifacts(pins: dict | None = None) -> Artifacts:
    """Load the pinned prompt and tool config. Raises if either pin is unusable.

    The digest is over the RAW FILE BYTES. It used to hash `read_text()`, which
    applies universal-newline conversion -- so a CRLF byte change would decode to
    the same string, hash the same, and pass the pin that exists to detect exactly
    that. Bytes are hashed, then the same bytes are decoded.
    """
    pins = pins if pins is not None else read_pins_checked()
    if not isinstance(pins, dict):
        raise InstrumentError(f"the pin document is {type(pins).__name__}, expected an object.")
    out: dict[str, tuple[str, str]] = {}
    for key in ("system_prompt", "tool_config"):
        spec = pins.get(key)
        # A pin entry that is not an object used to reach `.get()` and raise
        # AttributeError, which escaped as an unclassified exception -- reported by
        # the certifier as an unreachable API rather than as a broken pin file.
        if spec is None:
            raise InstrumentError(f"pins.json has no `{key}` entry. Run "
                                  f"instrument.py --update-pins first.")
        if not isinstance(spec, dict):
            raise InstrumentError(f"pin `{key}` is {type(spec).__name__}, expected an object "
                                  f"with `path` and `sha256`.")
        if not spec.get("sha256") or not spec.get("path"):
            raise InstrumentError(f"pin `{key}` needs both `path` and `sha256`. Run "
                                  f"instrument.py --update-pins first.")
        path = (ROOT / "tools" / spec["path"]).resolve()
        if not path.exists():
            raise InstrumentError(f"pin `{key}` names {spec['path']}, which does not exist.")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            # Unreadable is not the same as absent, and neither is an API problem.
            raise InstrumentError(f"{spec['path']} could not be read: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        if digest != spec["sha256"]:
            raise InstrumentError(f"{spec['path']} hashes to {digest[:16]} but its pin says "
                                  f"{spec['sha256'][:16]}. Refusing to build a request from "
                                  f"an artifact that does not match its pin.")
        try:
            out[key] = (raw.decode("utf-8"), digest)
        except UnicodeDecodeError as exc:
            raise InstrumentError(f"{spec['path']} is not valid UTF-8: {exc}") from exc

    cb = pins.get("codebook") or {}
    if not isinstance(cb, dict):
        raise InstrumentError(f"pin `codebook` is {type(cb).__name__}, expected an object.")
    # Both artifact pins must name the SAME codebook as the codebook pin. Each pin
    # can be individually valid while the pair comes from different instrument
    # versions -- a prompt rendered from v1.0 beside a schema rendered from v1.1 --
    # and the run record would then report one codebook version for a request built
    # from two.
    for key in ("system_prompt", "tool_config"):
        got_v, got_s = pins[key].get("codebook_version"), pins[key].get("codebook_sha256")
        if got_v != cb.get("version") or got_s != cb.get("sha256"):
            raise InstrumentError(
                f"pin `{key}` was rendered from codebook v{got_v} ({str(got_s)[:16]}), but the "
                f"codebook pin says v{cb.get('version')} ({str(cb.get('sha256'))[:16]}). The "
                f"two artifacts may come from different instrument versions; re-run "
                f"instrument.py --update-pins.")

    prompt, p_sha = out["system_prompt"]
    cfg_text, c_sha = out["tool_config"]
    try:
        cfg = json.loads(cfg_text)
    except json.JSONDecodeError as exc:
        raise InstrumentError(f"the pinned tool config is not valid JSON: {exc}") from exc
    # The shape check used to require only the two top-level keys, so
    # `{"tools": [], "toolChoice": {}}` loaded cleanly and failed later as an API
    # error -- a malformed pinned artifact reported as a Bedrock problem. Every
    # structural promise the request depends on is checked here instead, because a
    # broken artifact must fail OFFLINE, where the diagnosis is unambiguous.
    for problem in tool_config_problems(cfg):
        raise InstrumentError(f"the pinned tool config is malformed: {problem}")
    return Artifacts(prompt=prompt, prompt_sha256=p_sha,
                     tool_config=cfg, tool_config_sha256=c_sha,
                     codebook_version=str(cb.get("version", "?")),
                     codebook_sha256=str(cb.get("sha256", "?")))


def build_request(art: Artifacts, paragraph: str, *, max_tokens: int = 1024) -> dict:
    """The exact Converse keyword arguments for one paragraph.

    The cache point sits immediately after the pinned system-prompt block. Converse
    builds its cache prefix in the order tools -> system -> messages, so the cached
    prefix is the pinned tool configuration plus the pinned system prompt -- which
    is why A.4's "after the codebook prefix" was too loose to be checkable and now
    names both.

    `temperature: 0` with no `topP` (setting both is a ValidationException) and
    extended thinking off, per A.4. Nothing here varies between calls: no nonce, no
    run tag. An earlier design added a cache-busting prefix so that a preflight
    rerun inside a live TTL would always see a cache WRITE, but that made the
    request differ from the production one and put nonce tokens into the very
    measurement the check exists to take. The cache check accepts a warm first read
    instead -- see preflight.
    """
    return {
        "system": [{"text": art.prompt},
                   {"cachePoint": {"type": "default", "ttl": CACHE_TTL}}],
        "messages": [{"role": "user",
                      "content": [{"text": f"Paragraph:\n{paragraph}"}]}],
        "toolConfig": art.tool_config,
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
    }


CACHE_TTL = "1h"          # verified as a validated enum on cachePoint, 2026-08-31

# Each model's own minimum cacheable prefix. Compared per model rather than against
# the maximum: requiring Sonnet to report at least Opus's 4096 would be stricter
# than the API requires, and tokenization differs between models.
# Minimum cacheable prefix per model, from the prompt-caching documentation
# (platform.claude.com, read 2026-09-06; "these minimums apply on every platform"):
# Sonnet 4.6 and Sonnet 5 = 1,024, Opus 4.6 = 4,096, Opus 5 = 512. Non-monotonic in
# recency, so never interpolated. Models from Opus 4.7 on use a newer tokenizer (about
# 30 % more tokens for the same text), so the measured prefix differs by model.
MIN_CACHE_PREFIX = {"sonnet": 1024, "opus": 4096, "sonnet5": 1024, "opus5": 512,
                    # §4f (2026-10-19): Haiku 4.5 = 4,096, Sonnet 4.5 = 1,024 (prompt-caching documentation)
                    "haiku45": 4096, "sonnet45": 1024,
                    # §4k (2026-11-01): the configuration controls are the 4.6 models, with their own minimums
                    "sonnet_req5": 1024, "opus_req5": 4096,
                    # §4m (2026-11-08): Opus 5.5 = 512 (prompt-caching documentation); the Opus 5 configurations as Opus 5
                    "opus55": 512, "opus55_high": 512, "opus5_req55": 512, "opus5_rep": 512}

# The model IDs live HERE, beside the request builder, not in the certifier. They used
# to sit only in `preflight.MODELS`, which meant the runner could share this builder,
# use a different model, and still claim the preflight applied to its run -- the same
# drift the builder itself exists to prevent, one level up. The selected model is also
# part of the request envelope that check 13b hashes, so a changed model now shows up
# as a changed request rather than as an unremarked difference.
MODELS = {
    "sonnet": "eu.anthropic.claude-sonnet-4-6",
    "opus": "eu.anthropic.claude-opus-4-6-v1",
    # Added 2026-09-06 as candidates of the whitelisted production-model selection W1
    # (pre-analysis specification §4). EU cross-region inference profiles, listed by
    # ListInferenceProfiles in eu-central-1 on that day; the same pinned request goes
    # to every candidate -- nothing here is adapted per model.
    "sonnet5": "eu.anthropic.claude-sonnet-5",
    "opus5": "eu.anthropic.claude-opus-5",
    # Added 2026-10-19 for the robustness model comparison (pre-analysis specification §4f): the Claude models the
    # account reaches through EU inference profiles, under the byte-identical request ...
    "haiku45": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    "sonnet45": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
    # ... and three non-Anthropic models served in the EU region, which reject the extended-TTL cache point and
    # therefore receive the pinned request WITHOUT it (`request_for`, NO_CACHE_POINT): prompt, tool schema, forced tool
    # choice, user turn and decoding unchanged
    "novapro": "eu.amazon.nova-pro-v1:0",
    "qwen3_235b": "qwen.qwen3-235b-a22b-2507-v1:0",
    "nemotron3s": "nvidia.nemotron-super-3-120b",
    # Added 2026-10-30 for the exploratory diffusion-model comparison (specification §4g): Inception's Mercury 3,
    # served by Inception's chat-completions API (not Bedrock), in two declared configurations of the same model
    "mercury3_instant": "mercury-3",
    "mercury3_low": "mercury-3",
    # Added 2026-11-01 for the Claude 5 benchmark (specification §4k): the configuration controls, i.e. the two 4.6 models
    # under the 5-series request (`sonnet5` / `opus5` above are the 5-series models themselves)
    "sonnet_req5": "eu.anthropic.claude-sonnet-4-6",
    "opus_req5": "eu.anthropic.claude-opus-4-6-v1",
    # Added 2026-11-08 for Opus 5.5 and the precision-recall gap (specification §4m): Opus 5.5 under the 5.5 request at
    # effort low and high, Opus 5 under the same request as the configuration control, and Opus 5 under the §4k request
    # again (the repeat, k = 3)
    "opus55": "eu.anthropic.claude-opus-5-5",
    "opus55_high": "eu.anthropic.claude-opus-5-5",
    "opus5_req55": "eu.anthropic.claude-opus-5",
    "opus5_rep": "eu.anthropic.claude-opus-5",
}

# §4f: the models whose providers reject the extended-TTL cache point ("only supported for Anthropic models" /
# "did not allow prompt caching", probe of 2026-10-19). The cache point is a billing and latency directive, not model
# input; for these keys it is removed and nothing else changes. §4g adds the Mercury configurations (the Inception
# API has no cache-point directive; its automatic prompt caching is reported separately, never as cache traffic).
NO_CACHE_POINT = frozenset({"novapro", "qwen3_235b", "nemotron3s", "mercury3_instant", "mercury3_low"})

# §4g (2026-10-30): models reached through a provider API other than Bedrock, served by a Converse-shaped adapter
# (`inception_client.py`); the endpoint replaces the Bedrock region in the run record and the gate artefact.
EXTERNAL_API = {"mercury3_instant": "https://api.inceptionlabs.ai/v1/chat/completions",
                "mercury3_low": "https://api.inceptionlabs.ai/v1/chat/completions"}

# §4g: the declared deviations of these models' request from the pinned one, and nothing else. Mercury 3 rejects
# temperature 0 (accepted range 0.5-1; an out-of-range value is reset to the default 1), so the API minimum 0.5 is
# sent; `reasoning_effort` is the provider's thinking switch ("instant": no reasoning tokens in the access probe;
# "low": the model's default); the completion cap counts reasoning tokens, so the "low" configuration gets 8,192.
# The gate's check 13 reconstructs each model's request from the pinned one and exactly this table.
# §4k (2026-11-01): Sonnet 5 and Opus 5 reject any temperature but the default ("`temperature` is deprecated for this
# model", probe of 2026-11-01) and think by default. Their request therefore carries no temperature (None: the key is
# omitted, the provider's default sampling applies) and disables thinking explicitly; the two configuration controls
# send the 4.6 models exactly the same request.
GEN5_REQUEST = {"temperature": None, "max_tokens": 1024, "additional": {"thinking": {"type": "disabled"}}}
REQUEST_ADAPTATION = {
    "mercury3_instant": {"temperature": 0.5, "max_tokens": 1024, "additional": {"reasoning_effort": "instant"}},
    "mercury3_low": {"temperature": 0.5, "max_tokens": 8192, "additional": {"reasoning_effort": "low"}},
    **{k: GEN5_REQUEST for k in ("sonnet5", "opus5", "sonnet_req5", "opus_req5", "opus5_rep")},
    # §4m (2026-11-08): Opus 5.5 rejects `thinking {type: disabled}` and a forced tool choice (probe of 2026-11-08), so
    # its request lets the model choose the one pinned tool (`tool_choice: auto`), asks for adaptive thinking at an
    # explicit effort (low = the least thinking the model allows; high for the effort contrast) and raises the cap to
    # 8,192 because reasoning tokens count against it; the control sends Opus 5 exactly the same request
    "opus55": {"temperature": None, "max_tokens": 8192, "tool_choice": "auto",
               "additional": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}},
    "opus55_high": {"temperature": None, "max_tokens": 8192, "tool_choice": "auto",
                    "additional": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}}},
    "opus5_req55": {"temperature": None, "max_tokens": 8192, "tool_choice": "auto",
                    "additional": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}},
}
# §4m: the configurations whose request asks for thinking, and whose response contract therefore accepts a
# `reasoningContent` block beside the single tool call (never read, never stored in a public file)
THINKING_ON = frozenset(k for k, v in REQUEST_ADAPTATION.items()
                        if (v.get("additional") or {}).get("thinking") == {"type": "adaptive"})
# §4m: the one tool-choice deviation the adaptation table may declare; the tool specification itself is unchanged
TOOL_CHOICE_AUTO = {"auto": {}}


def adapted_inference_config(adapt: dict) -> dict:
    """The inferenceConfig of a declared adaptation: maxTokens, and the temperature unless it is None (§4k: omitted)."""
    return ({"maxTokens": adapt["max_tokens"]} if adapt["temperature"] is None
            else {"maxTokens": adapt["max_tokens"], "temperature": adapt["temperature"]})


def request_for(model_key: str, art: "Artifacts", paragraph: str, *, max_tokens: int = 1024) -> dict:
    """`build_request()` for one model: unchanged for every model that supports the cache point; for the keys in
    NO_CACHE_POINT the same request without the cache-point block (the system prompt block, the tool configuration
    object, the user turn and the inference configuration are the very objects `build_request` returns)."""
    req = build_request(art, paragraph, max_tokens=max_tokens)
    if model_key in NO_CACHE_POINT:
        req["system"] = [b for b in req["system"] if "cachePoint" not in b]
    adapt = REQUEST_ADAPTATION.get(model_key)
    if adapt:                                     # §4g: exactly the declared deviations, nothing else
        req["inferenceConfig"] = adapted_inference_config(adapt)
        if adapt.get("additional"):
            req["additionalModelRequestFields"] = json.loads(json.dumps(adapt["additional"]))   # a deep copy
        if adapt.get("tool_choice") == "auto":    # §4m: the pinned tool list itself, only the choice is free
            req["toolConfig"] = {**{k: v for k, v in req["toolConfig"].items() if k != "toolChoice"},
                                 "toolChoice": dict(TOOL_CHOICE_AUTO)}
        elif adapt.get("tool_choice") is not None:
            raise ValueError(f"undeclared tool_choice {adapt.get('tool_choice')!r} for {model_key}")
    return req


# ─── pins ────────────────────────────────────────────────────────────────────
def read_pins() -> dict:
    """Raw read. Callers that must not raise an unclassified exception use
    `read_pins_checked()` instead."""
    return json.loads(PINS.read_text(encoding="utf-8"))


def read_pins_checked() -> dict:
    """`read_pins()` with every failure mode converted to InstrumentError.

    `load_artifacts()` called the raw version, so an unreadable or malformed pins.json
    escaped as OSError or JSONDecodeError from the one function whose entire contract
    is "raises InstrumentError if the instrument cannot be loaded". Preflight happened
    not to hit it -- it parses the document itself and passes it in -- but the runner
    will use this path.
    """
    try:
        doc = json.loads(PINS.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InstrumentError(f"{PINS.name} could not be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InstrumentError(f"{PINS.name} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise InstrumentError(f"{PINS.name} holds a {type(doc).__name__}, expected an object.")
    return doc


def pin_state(pins: dict, key: str, expected_path: Path, rendered_digest: str) -> str:
    """Compare the PINNED FILE ON DISK, not just the freshly rendered bytes.

    The first version hashed only what had just been rendered and ignored the pin's
    recorded path entirely, so a missing, moved or substituted artifact still
    reported `match` -- a pin that verified nothing about the artifact it named.
    """
    entry = pins.get(key) or {}
    if not entry.get("sha256"):
        return "unpinned"
    recorded = entry.get("path")
    if not recorded:
        return "no-path"
    if (ROOT / "tools" / recorded).resolve() != expected_path.resolve():
        return "wrong-path"
    on_disk = ROOT / "tools" / recorded
    if not on_disk.exists():
        return "missing"
    if hashlib.sha256(on_disk.read_bytes()).hexdigest() != entry["sha256"]:
        return "file-differs"
    return "match" if entry["sha256"] == rendered_digest else "differs"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="render the three artifacts to disk WITHOUT touching pins.json. "
                         "Use this after a renderer change that leaves the prompt and the "
                         "tool config unchanged: the manifest records the renderer's own "
                         "digest, so it goes stale on every code edit, and forcing "
                         "--update-pins to refresh it would silently re-accept a changed "
                         "prompt at the same time.")
    ap.add_argument("--update-pins", action="store_true",
                    help="write the artifacts AND accept them as the pinned baseline. This is "
                         "the human approval step -- it redefines what a reported run is "
                         "allowed to send.")
    ap.add_argument("--pinned-on", default=None, metavar="YYYY-MM-DD",
                    help="record this acceptance date in pins.json instead of today's")
    ap.add_argument("--diff", action="store_true",
                    help="show how the render differs from the artifacts on disk")
    args = ap.parse_args()

    doc, subtypes, cb_sha = load_codebook()
    check_projection(doc)

    # Rendered in memory. Writing before comparing destroyed the very bytes a pin
    # referred to whenever verification failed -- the artifact was gone exactly when
    # it was needed to see what had changed.
    prompt = render_prompt(doc, subtypes, cb_sha)
    cfg = canonical_json(build_tool_config(doc, subtypes))
    manifest = canonical_json(build_manifest(doc, cb_sha, prompt, cfg))

    v = str(doc["version"])
    p_path, c_path = OUT / f"system-prompt_v{v}.txt", OUT / f"tool-config_v{v}.json"
    m_path = OUT / f"manifest_v{v}.json"
    p_sha, c_sha = sha256_text(prompt), sha256_text(cfg)

    print(f"codebook v{v}  sha256 {cb_sha[:16]}")
    print(f"  system prompt   {len(prompt.splitlines()):>4} lines   sha256 {p_sha[:16]}")
    print(f"  tool config                  sha256 {c_sha[:16]}")
    kept = sum(1 for x in XRULES.values() if x[0] != "exclude")
    print(f"  exclusion rules {kept} of {len(XRULES)} rendered "
          f"({sum(1 for x in XRULES.values() if x[0] == 'transform')} transformed, "
          f"{sum(1 for x in XRULES.values() if x[0] == 'exclude')} excluded)")
    print(f"  transformations  {len(TRANSFORMS)} source paths + "
          f"{sum(1 for x in XRULES.values() if x[0] == 'transform')} exclusion rules")

    pins = read_pins()
    states = {"system_prompt": pin_state(pins, "system_prompt", p_path, p_sha),
              "tool_config": pin_state(pins, "tool_config", c_path, c_sha)}
    print("\n  pins: " + "   ".join(f"{k}={x}" for k, x in states.items()))

    if args.diff:
        import difflib
        for path, fresh in ((p_path, prompt), (c_path, cfg), (m_path, manifest)):
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            d = list(difflib.unified_diff(old.splitlines(), fresh.splitlines(),
                                          f"{path.name} (on disk)", f"{path.name} (rendered)",
                                          lineterm="", n=1))
            print(f"\n── {path.name}: " + (f"{len(d)} diff line(s)" if d else "identical"))
            for line in d[:60]:
                print("   " + line)
        return

    if args.write and not args.update_pins:
        # Refuse BEFORE writing. The first version wrote all three artifacts and only
        # then reported the mismatch, which destroyed the accepted bytes exactly when
        # they were needed to see what had changed -- the same defect the in-memory
        # render was introduced to fix, reintroduced one level down. --write may only
        # ever refresh artifacts that already agree with their pins.
        bad = {k: x for k, x in states.items() if x not in ("match", "unpinned")}
        if bad:
            sys.exit(f"\nREFUSING to write: the render no longer matches the pins: {bad}\n"
                     f"Nothing was written, so the accepted bytes are still on disk. Run "
                     f"--diff to see what changed. Accepting it is --update-pins, which is a "
                     f"decision, not a refresh.")

    if args.write or args.update_pins:
        OUT.mkdir(exist_ok=True)
        for path, text in ((p_path, prompt), (c_path, cfg), (m_path, manifest)):
            path.write_text(text, encoding="utf-8")
        if not args.update_pins:
            print(f"  wrote 3 artifacts to {OUT.relative_to(ROOT)}/; pins.json untouched")
            return
        for key, path, digest in (("system_prompt", p_path, p_sha),
                                  ("tool_config", c_path, c_sha)):
            # Relative to tools/, matching the codebook pin. Two roots in one pin
            # file meant preflight resolved these against the wrong directory and
            # reported "that file does not exist" for artifacts check 14 had just
            # loaded successfully.
            pins[key] = {"path": str(path.relative_to(ROOT / "tools")), "sha256": digest,
                         "codebook_version": v, "codebook_sha256": cb_sha,
                         # The actual acceptance date. It was hard-coded, so every
                         # future acceptance would have recorded a false one.
                         "pinned_on": args.pinned_on or date.today().isoformat()}
        pins.pop("tool_schema", None)      # the pin is the whole config, not a fragment
        pins.pop("rendered_prompt", None)  # superseded by system_prompt
        PINS.write_text(canonical_json(pins), encoding="utf-8")
        print(f"  wrote 3 artifacts to {OUT.relative_to(ROOT)}/ and updated {PINS.name}")
        return

    bad = {k: x for k, x in states.items() if x not in ("match", "unpinned")}
    if bad:
        sys.exit(f"\nREFUSING to certify: {bad}\n"
                 f"Nothing was written. Run --diff to see what changed, then accept it "
                 f"deliberately with --update-pins.")
    # The manifest is unpinned -- it is a research record, not a request input -- but an
    # ordinary run used to certify while a stale or hand-edited manifest sat on disk.
    # That is not hypothetical: it happened once, and --diff was the only thing that
    # would have shown it. So the comparison happens on every ordinary run.
    # Compared MODULO the commit stamp. `provenance.code_input_commit` records the HEAD
    # the artifacts were rendered at, which changes on every commit -- including one that
    # touches nothing this renderer reads. Comparing it made an ordinary run refuse
    # immediately after any commit: a refusal that fires on a no-op, which trains the
    # reader to run --write without looking. The stamp is provenance, not a claim about
    # content; `renderer_sha256` is the claim about content and is still compared.
    on_disk = m_path.read_text(encoding="utf-8") if m_path.exists() else ""
    if not on_disk or manifest_without_stamp(on_disk) != manifest_without_stamp(manifest):
        sys.exit(f"\nREFUSING to certify: {m_path.name} on disk is not what this renderer "
                 f"produces ({'absent' if not on_disk else 'differs'}).\n"
                 f"The manifest records which renderer and which codebook produced the pinned "
                 f"bytes, so it goes stale on a renderer or projection change -- but NOT on a "
                 f"bare commit, whose stamp is excluded from this comparison. Run --diff to see "
                 f"what changed, then --write to refresh it. --write cannot accept a changed "
                 f"prompt, so refreshing the manifest is safe.")
    if "unpinned" in states.values():
        print("\n  NOT CERTIFIED: at least one artifact is unpinned, and nothing was written. "
              "Accept with --update-pins.")
    else:
        print("\n  certified: both artifacts match their pins, and the files on disk match too.")


if __name__ == "__main__":
    main()
