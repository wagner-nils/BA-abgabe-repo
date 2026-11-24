#!/usr/bin/env python3
"""M4 -- the production extraction runner. PIPELINE-COMPLETION-PLAN.md Step 2.

    .venv/bin/python3 tools/pipeline/run_extract.py --manifest tools/annotation/labels/A2_sample_manifest.json \\
        --model sonnet --repeats 3 --run-id a2_sonnet_001
    .venv/bin/python3 tools/pipeline/run_extract.py --manifest tools/annotation/labels/A1_manifest.json \\
        --restrict-split A1 --model sonnet --repeats 3 --run-id a1_dryrun_001 --dev
    ... --resume            # continue an interrupted run under the same run-id

ONE request builder, ONE validator. Every envelope is `instrument.build_request()` on the
pinned artefacts; every response goes through `instrument.validate_tool_use()`. The gate
(`preflight.py`) runs IN PROCESS, with this runner's resolved model, before the first
extraction call, and the run refuses to proceed unless that gate certifies. A reported run
therefore carries the certificate of the exact configuration it used (D14, plan Step 2).

Records, in write order per attempt (the recovery contract):
    (1) attempt-started      public   tools/runs/<run_id>_calls.jsonl
    (2) raw response         PRIVATE  tools/runs/raw/<run_id>.jsonl   (gitignored: may quote text)
    (3) attempt-finished     public   verdict CODE, usage, stop reason, structured payload minus free text
    (4) terminal             public   one per paragraph x execution, only when the retry-state
                                      machine says so
Every write is flushed and fsynced. On --resume, recovered attempts are REPLAYED through the same
retry-state machine as normal execution: a valid response completes without another call, a
malformed first attempt gets its prescribed retry, a started attempt without a raw record is of
unknown completion and is re-sent and counted.

Public files carry an ALLOWLIST of enum, boolean and identifier fields. The model's `note` and
`comparison_note`, the validator's message strings and the verbatim response stay in the raw file.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
RAW = RUNS / "raw"
ALL_PARAGRAPHS = ROOT / "tools" / "annotation" / "sheets" / "ALL_paragraphs.csv"

sys.path.insert(0, str(HERE))
import instrument as ins                                        # noqa: E402
import instrument_v11 as ins11                                  # noqa: E402  W3 variants (2026-10-14)
import instrument_v10r3 as ins10r3                              # noqa: E402  §4b candidate v1.0r3 (2026-10-26)
import instrument_v10f as ins10f                                # noqa: E402  §4j form variant v1.0F (2026-10-30)
import instrument_v10c as ins10c                                # noqa: E402  §4l context variant v1.0C (2026-11-06)
import sample_contract as sc                                    # noqa: E402

# --------------------------------------------------------------------------- #
# the instrument binding (W3, 2026-10-14): v1.0 is the registered module, untouched; v1.1S / v1.1E are
# instrument_v11.bind(variant). Everything below calls `ins.<name>` and never imports a variant itself.
# --------------------------------------------------------------------------- #
INSTRUMENTS = ("v1.0", "v1.1S", "v1.1E", "v1.2S", "v1.0r3", "v1.0F", "v1.0C")   # v1.2S W4 (2026-10-14); v1.0r3 §4b (2026-10-26); v1.0F §4j; v1.0C §4l
VARIANT_OF = {"v1.1S": "S", "v1.1E": "E", "v1.2S": "T"}
INSTRUMENT_NAME = "v1.0"


def model_request(model_key: str, art, text: str) -> dict:
    """The request body for one paragraph: `instrument.request_for` where the bound instrument has it (v1.0: the pinned
    request, without the cache point for the §4f no-cache models), else the variant's `build_request`."""
    if hasattr(ins, "request_for"):
        return ins.request_for(model_key, art, text, max_tokens=output_cap())
    return ins.build_request(art, text, max_tokens=output_cap())


def thinking_statement(model_key: str, art) -> str:
    """What the run record states about thinking: a statement about the request this model receives, never an
    inference from the responses."""
    extra = model_request(model_key, art, "x").get("additionalModelRequestFields") or {}
    if extra.get("reasoning_effort"):
        return (f"provider reasoning_effort={extra['reasoning_effort']} (§4g); reasoning tokens are counted in the output "
                f"tokens and never returned as content")
    if extra.get("thinking") == {"type": "disabled"}:
        return ins.THINKING_DISABLED_CONTRACT                                      # §4k
    if extra.get("thinking") == {"type": "adaptive"}:                              # §4m
        return ins.THINKING_ADAPTIVE_CONTRACT.format(effort=(extra.get("output_config") or {}).get("effort"))
    return ins.THINKING_CONTRACT


def output_cap() -> int:
    """The output-token cap of the bound instrument: 1,024 as registered; v1.0F declares 2,048 (§4j)."""
    return int(getattr(ins, "MAX_TOKENS", MAX_TOKENS))


def bind_instrument(name: str):
    """Rebind the module-level `ins` to the chosen instrument and return it. The default keeps the
    registered behaviour byte for byte: `instrument` itself, retry policy v2."""
    global ins, INSTRUMENT_NAME
    if name == "v1.0":
        import instrument as v10
        ins = v10
    elif name in VARIANT_OF:
        ins = ins11.bind(VARIANT_OF[name])
    elif name == "v1.0r3":
        ins = ins10r3.bind()                 # the registered request under retry policy v3 (specification §4b)
    elif name == "v1.0F":
        ins = ins10f.bind()                  # the mandatory form under retry policy v3 (specification §4j)
    elif name == "v1.0C":
        ins = ins10c.bind()                  # the registered request with the context lines, retry policy v2 (§4l (5))
    else:
        raise SystemExit(f"unknown instrument {name!r}; choose one of {INSTRUMENTS}")
    INSTRUMENT_NAME = name
    return ins


def retry_policy_version() -> str:
    """v2 for the registered instrument; v3 (feedback retry) for the v1.1 variants -- part of the fingerprint."""
    return str(getattr(ins, "RETRY_POLICY_VERSION", RETRY_POLICY_VERSION))


MISSING_PRIVATE = "missing_private_response"   # the pending invalid call cannot be reconstructed on resume


def replayable(prior: dict | None) -> bool:
    """A prior call can be sent back verbatim only if it is a real tool call to the declared tool with an
    id and an object input; anything else (wrong tool name, no id) gets the identical retry instead, and the
    reason is journalled privately (Codex W3 round 3, #9)."""
    return (isinstance(prior, dict) and bool(prior.get("toolUseId")) and prior.get("name") == ins.TOOL_NAME
            and isinstance(prior.get("input"), dict))


def pending_feedback(st: dict):
    """Retry policy v3: the pending invalid call of this (paragraph, execution), if the last RETURNED model
    response was schema- or contract-invalid. Transport failures (throttled, error) in between do not clear
    it; a returned response of any other code does; a recovery episode resets it (Codex W3 round 3, #1/#2)."""
    if retry_policy_version() != "3":
        return None
    return st.get("last_invalid")

# --------------------------------------------------------------------------- #
# the retry policy -- a constant, part of the execution fingerprint
# --------------------------------------------------------------------------- #
RETRY_POLICY_VERSION = "2"   # v2 (2026-09-06): journalled recovery episodes for infrastructure terminals
PARSE_RETRIES = 1        # one identical retry after a malformed response, then parse_failed
THROTTLE_RETRIES = 6     # exponential backoff, base 2 s
ERROR_RETRIES = 2        # any other exception
# A terminal `error` (throttling or exceptions exhausting the policy above) is an infrastructure
# outcome, not a model-output failure. `--resume --recover-infrastructure` reopens such a terminal
# in a journalled recovery episode that replays the retry policy from scratch, at most this many
# times per (paragraph, execution); every earlier attempt and its cost stay on the journal
# (Codex W1 round 2, #2). A `parse_failed` or `refused` terminal is never reopened.
MAX_RECOVERY_EPISODES = 2
BACKOFF_BASE_S = 2.0
MAX_TOKENS = 1024

# verdict codes -- the ONLY response-derived values that reach a public file
OK_PRESENT, OK_OFF = "ok_technologies_present", "ok_off_topic"
NO_TOOL_USE, TRUNCATED, REFUSAL = "no_tool_use", "truncated", "refusal"
SCHEMA_INVALID, CONTRACT_INVALID = "schema_invalid", "contract_invalid"
UNEXPECTED_STOP = "unexpected_stop_reason"      # a payload under any stop reason but tool_use
UNEXPECTED_CONTENT = "unexpected_content"       # several toolUse blocks, or a reasoningContent block
THROTTLED, ERROR = "throttled", "error"
PARSE_FAIL_CODES = {NO_TOOL_USE, TRUNCATED, SCHEMA_INVALID, CONTRACT_INVALID, UNEXPECTED_STOP,
                    UNEXPECTED_CONTENT}
REFUSAL_STOPS = {"refusal", "content_filtered", "guardrail_intervened"}

# outcomes of a terminal record
T_PRESENT, T_OFF, T_PARSE_FAILED, T_REFUSED, T_ERROR = (
    "technologies_present", "off_topic", "parse_failed", "refused", "error")
# §4e(a) language arm (2026-11-05): a paragraph whose translation failed enters the arm as a failed execution without
# any call -- no record, omissions only; never reopened by a recovery episode (amendment item 4)
T_NO_INPUT = "no_translated_input"

# Price map read 2026-08-14 (tools/research/2026-08-29_bedrock-costs-and-comparison-conventions.md):
# Geo/in-region cross-region inference = Global x 1.10; 1h cache write = 2.0 x input; read = 0.1 x input.
# sonnet5 / opus5 added 2026-09-06: the AWS pricing page did not render machine-readably,
# so their rows are the Anthropic list prices (platform.claude.com/docs pricing page, read
# 2026-09-06: Sonnet 5 = 2 / 10, 1h write 4, read 0.20; Opus 5 = 5 / 25, 1h write 10,
# read 0.50) times the documented 10 % premium for regional (eu.) endpoints -- the same
# convention that produced the two rows above (3.00 x 1.10 = 3.30). PROVISIONAL until
# read off an AWS invoice or the Bedrock price page; recorded in the usage log.
PRICE_TABLE_VERSION = ("aws-bedrock-price-map-2026-08-14 (sonnet, opus) + anthropic-list-2026-09-06 "
                       "x 1.10 eu regional premium (sonnet5, opus5; §4k controls sonnet_req5, opus_req5 at the sonnet, opus rows); §4f 2026-10-19 provisional rows (haiku45, sonnet45, "
                       "novapro, qwen3_235b, nemotron3s); §4m 2026-11-08 provisional rows (opus55, opus55_high = anthropic list "
                       "x 1.10; opus5_req55, opus5_rep at the opus5 row); USD per 1M tokens")
PRICES_PER_M = {
    "sonnet":  {"input": 3.30, "output": 16.50, "cache_write_1h": 6.60, "cache_read": 0.33},
    "opus":    {"input": 5.50, "output": 27.50, "cache_write_1h": 11.00, "cache_read": 0.55},
    "sonnet5": {"input": 2.20, "output": 11.00, "cache_write_1h": 4.40, "cache_read": 0.22},
    "opus5":   {"input": 5.50, "output": 27.50, "cache_write_1h": 11.00, "cache_read": 0.55},
    # §4k (2026-11-01): the configuration controls are the 4.6 models and bill at their rows
    "sonnet_req5": {"input": 3.30, "output": 16.50, "cache_write_1h": 6.60, "cache_read": 0.33},
    "opus_req5":   {"input": 5.50, "output": 27.50, "cache_write_1h": 11.00, "cache_read": 0.55},
    # §4m (2026-11-08), PROVISIONAL: Opus 5.5 = Anthropic list price (claude-api reference cached 2026-10-06: 4 / 20,
    # cache read 0.20; one-hour write = 2 x input = 8) x 1.10 eu regional premium; the Opus 5 configurations at opus5
    "opus55":      {"input": 4.40, "output": 22.00, "cache_write_1h": 8.80, "cache_read": 0.22},
    "opus55_high": {"input": 4.40, "output": 22.00, "cache_write_1h": 8.80, "cache_read": 0.22},
    "opus5_req55": {"input": 5.50, "output": 27.50, "cache_write_1h": 11.00, "cache_read": 0.55},
    "opus5_rep":   {"input": 5.50, "output": 27.50, "cache_write_1h": 11.00, "cache_read": 0.55},
    # §4f (2026-10-19), PROVISIONAL: haiku45 / sonnet45 = Anthropic list price x 1.10 eu regional premium (the convention
    # above; Haiku 4.5 = 1 / 5, Sonnet 4.5 = 3 / 15); nemotron3s = Bedrock price page, Europe (Frankfurt), read
    # 2026-10-19 (0.18 / 0.78); qwen3_235b = Bedrock price page, Asia Pacific (Sydney) standard tier, the only region
    # listed when read (0.2266 / 0.9064), used as a proxy; novapro = US list price 0.80 / 3.20 x 1.10 (the page did not
    # render the Nova rows). The three non-Anthropic models are called without a cache point: no cache rates.
    "haiku45":    {"input": 1.10, "output": 5.50, "cache_write_1h": 2.20, "cache_read": 0.11},
    "sonnet45":   {"input": 3.30, "output": 16.50, "cache_write_1h": 6.60, "cache_read": 0.33},
    "novapro":    {"input": 0.88, "output": 3.52, "cache_write_1h": 0.0, "cache_read": 0.0},
    "qwen3_235b": {"input": 0.2266, "output": 0.9064, "cache_write_1h": 0.0, "cache_read": 0.0},
    "nemotron3s": {"input": 0.18, "output": 0.78, "cache_write_1h": 0.0, "cache_read": 0.0},
    # §4g (2026-10-30), PROVISIONAL: Inception models page, mercury-3 launch-discount list price 0.10 input / 0.35 output
    # (regular 0.20 / 0.70; cached input 0.01, not used: the provider's automatic caching is not counted, so the
    # estimate prices every input token at the uncached rate and overstates the charge)
    "mercury3_instant": {"input": 0.10, "output": 0.35, "cache_write_1h": 0.0, "cache_read": 0.0},
    "mercury3_low":     {"input": 0.10, "output": 0.35, "cache_write_1h": 0.0, "cache_read": 0.0},
}

CONTRACT_RULES = [   # rule identifiers derived from validate_response() messages (v1.0 and v1.1)
    ("belong to another frame", "element_frame_mismatch"),     # v1.1 only (Codex W3 round 3, #10)
    ("a frame is repeated", "repeated_frame"),                 # v1.1 only
    ("lists an element twice", "repeated_element"),            # v1.1 only
    ("is not a valid subtype for", "subtype_incompatible"),    # v1.2S only (W4)
    ("off_topic but", "off_topic_with_relations"),
    ("technologies_present but", "present_without_relations"),
    ("technology repeated", "repeated_technology"),
    ("fired but carries no evaluation", "frame_without_evaluation"),
    ("which did not fire", "evaluation_without_frame"),
    ("which requires a note", "note_required"),
    ("requires a comparison_note", "comparison_note_required"),
    ("which belongs to", "subtype_misplaced"),
    ("which this technology requires", "subtype_missing"),
    ("frames nothing, so comparison_target", "comparison_without_frame"),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(p: Path) -> str:
    """Repository-relative where possible; the tests redirect paths outside the repo."""
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def request_digest(envelope: dict) -> str:
    return hashlib.sha256(json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def contract_rule_ids(problems: list[str]) -> list[str]:
    ids = []
    for msg in problems:
        for needle, rid in CONTRACT_RULES:
            if needle in msg:
                ids.append(rid); break
        else:
            ids.append("other")
    return sorted(set(ids))


# --------------------------------------------------------------------------- #
# the retry-state machine: a pure function of the attempt codes so far
# --------------------------------------------------------------------------- #
def resolve(codes: list[str]) -> str | None:
    """Terminal outcome for this (paragraph, execution) given the attempt verdict codes in
    order, or None if another attempt is due. Used identically by normal execution and by
    recovery, which is what makes a crash unable to change an outcome."""
    for c in codes:
        if c == OK_PRESENT:
            return T_PRESENT
        if c == OK_OFF:
            return T_OFF
        if c == REFUSAL:
            return T_REFUSED
    if sum(c in PARSE_FAIL_CODES for c in codes) > PARSE_RETRIES:
        return T_PARSE_FAILED
    if sum(c == THROTTLED for c in codes) > THROTTLE_RETRIES:
        return T_ERROR
    if sum(c == ERROR for c in codes) > ERROR_RETRIES:
        return T_ERROR
    return None


# --------------------------------------------------------------------------- #
# classify one response into a verdict code + the public payload
# --------------------------------------------------------------------------- #
# §4m (2026-11-08): bound once per run from the model key -- True only for the configurations whose request asks for
# thinking (instrument.THINKING_ON), where a reasoningContent block beside the one tool call is accepted
ALLOW_REASONING = False


def classify(response: dict, art: ins.Artifacts, doc: dict, subtypes: dict
             ) -> tuple[str, dict | None, list[str], list[str], dict | None]:
    """(code, validated payload or None, contract rule ids, validator messages, the toolUse block).
    stopReason first, so a truncated object cannot be read as an abstention (A.7). Under a v1.1
    instrument a valid nested payload is normalised to the v1.0 row shape here, once, so every consumer
    downstream (public_payload, derive_rows, the evaluator) sees one shape. The validator messages and
    the block are what retry policy v3 feeds back; they never reach a public file."""
    stop = response.get("stopReason")
    if stop in REFUSAL_STOPS:
        return REFUSAL, None, [], [], None
    if stop == "max_tokens":
        return TRUNCATED, None, [], [], None
    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    use, why_sel = ins.select_tool_use(blocks, allow_reasoning=ALLOW_REASONING)   # one toolUse; reasoning only §4m
    if use is None:
        return (NO_TOOL_USE if why_sel == "none" else UNEXPECTED_CONTENT), None, [], [], None
    if stop != "tool_use":
        # The gate's check 3 requires stopReason == tool_use on every production call; a
        # payload that arrived under end_turn or stop_sequence is not accepted here either.
        return UNEXPECTED_STOP, None, [], [], use
    ok, why = ins.validate_tool_use(use, art, doc, subtypes)
    if ok:
        payload = use["input"]
        if hasattr(ins, "normalise"):
            payload = ins.normalise(payload)
        return (OK_OFF if payload.get("outcome") == "off_topic" else OK_PRESENT), payload, [], [], use
    if why.startswith("schema:") or why.startswith("toolUse."):
        return SCHEMA_INVALID, None, [], [why], use
    problems = ins.validate_response(use.get("input") or {}, doc, subtypes) \
        if isinstance(use.get("input"), dict) else []
    return CONTRACT_INVALID, None, contract_rule_ids(problems), problems, use


def public_payload(payload: dict) -> dict:
    """The allowlist: outcome and, per relation, technology, subtype, elements, evaluations,
    comparison_target. No note, no comparison_note, nothing else."""
    rels = []
    for r in payload.get("relations") or []:
        q = {"technology": r.get("technology"),
             "elements": sorted(r.get("elements") or []),
             "evaluations": dict(sorted((r.get("evaluations") or {}).items())),
             "comparison_target": r.get("comparison_target")}
        for f in ("wind_siting", "hydrogen_pathway"):
            if f in r:
                q[f] = r[f]
        rels.append(q)
    return {"outcome": payload.get("outcome"), "relations": rels}


# --------------------------------------------------------------------------- #
# durable files
# --------------------------------------------------------------------------- #
def repair_tail(path: Path) -> int:
    """Byte-level repair of a journal's last line, BEFORE anything decodes it. A last line
    without a newline is either a complete record that lost its terminator (a crash between
    write and flush) -- then the newline is appended and nothing is lost -- or a torn record,
    which is cut back to the previous newline. Returns the number of torn lines (0 or 1).
    Working on bytes matters: a tear inside a multibyte character would otherwise raise on
    decode and block every later resume."""
    if not path.exists():
        return 0
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return 0
    cut = data.rfind(b"\n") + 1
    tail = data[cut:]
    try:
        json.loads(tail.decode("utf-8"))
        with path.open("ab") as f:                 # append the terminator IN PLACE
            f.write(b"\n"); f.flush(); os.fsync(f.fileno())
        return 0
    except (UnicodeDecodeError, json.JSONDecodeError):
        with path.open("r+b") as f:                # truncate only the damaged suffix
            f.truncate(cut); f.flush(); os.fsync(f.fileno())
        return 1


class Journal:
    """Append-only JSONL with flush + fsync per record; the tail is repaired at open."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.torn_lines = repair_tail(path)
        self.f = path.open("a", encoding="utf-8")

    def write(self, rec: dict) -> None:
        self.f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self) -> None:
        self.f.close()

    @staticmethod
    def read_verbatim(path: Path) -> tuple[list[dict], int]:
        """Records for VERIFICATION: the file is read and never touched (Codex W1 round 9, #2 --
        `read()` repairs the tail in place, which invalidates the digest a consumer just verified).
        A last line without a newline counts as torn evidence whether or not it parses; a line that
        does not parse counts as torn. Nothing is written."""
        out, torn = [], 0
        if not Path(path).exists():
            return out, torn
        data = Path(path).read_bytes()
        if data and not data.endswith(b"\n"):
            torn += 1
            data = data[: data.rfind(b"\n") + 1]
        for line in data.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                torn += 1
        return out, torn

    @staticmethod
    def read(path: Path) -> tuple[list[dict], int]:
        """Records plus the count of torn lines. The tail is repaired at the byte level
        first, so a complete last record without newline is kept and a torn one is cut."""
        out, torn = [], 0
        if not path.exists():
            return out, torn
        torn += repair_tail(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                torn += 1
        return out, torn


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def load_manifest(path: Path, restrict_split: str | None) -> tuple[dict, list[dict]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    paras = doc["paragraphs"]
    if restrict_split:
        paras = [p for p in paras if p.get("split") == restrict_split]
    if not paras:
        sys.exit("the manifest yields no paragraphs (check --restrict-split)")
    return doc, paras


def load_texts(paras: list[dict]) -> dict[str, dict]:
    """Paragraph text from the registry-validated CSV, hash-checked against the manifest.
    The runner reads the CSV and never imports split_registry (interpreter binding)."""
    if not ALL_PARAGRAPHS.exists():
        sys.exit(f"{ALL_PARAGRAPHS} is missing: run build_sheets.py on the system interpreter first.")
    by_pid = {}
    with ALL_PARAGRAPHS.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            by_pid[r["paragraph_id"]] = r
    out = {}
    for p in paras:
        r = by_pid.get(p["paragraph_id"])
        if r is None:
            sys.exit(f"paragraph {p['paragraph_id']} is not in ALL_paragraphs.csv")
        h = hashlib.sha256(r["paragraph_text"].encode("utf-8")).hexdigest()[:16]
        if h != r["text_sha256"] or h != p["text_sha256"]:
            sys.exit(f"text hash mismatch for {p['paragraph_id']}: csv={r['text_sha256']} "
                     f"recomputed={h} manifest={p['text_sha256']}")
        out[p["paragraph_id"]] = r
    return out


def manifest_digest(doc: dict) -> str:
    """The manifest's canonical digest, RECOMPUTED over its content (the `digest` field
    excluded) and compared with the stored one where there is one -- a manifest whose
    content changed under a kept digest is refused, not trusted."""
    body = {k: v for k, v in doc.items() if k != "digest"}
    computed = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode("utf-8")).hexdigest()
    if doc.get("digest") and doc["digest"] != computed:
        sys.exit("REFUSING: the manifest's content does not match its stored digest; it was edited.")
    return computed


def load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in ("\"", "'") and v.find(v[0], 1) > 0:      # quoted value: up to the matching quote, a trailing
            v = v[1:v.find(v[0], 1)]                           # `# comment` after it is ignored (dotenv semantics)
        elif " #" in v:                                        # unquoted value with an inline comment
            v = v.split(" #", 1)[0].rstrip()
        os.environ.setdefault(k.strip(), v)


# --------------------------------------------------------------------------- #
# the gate, in process
# --------------------------------------------------------------------------- #
def run_gate(model_key: str, run_id: str, client_factory=None) -> tuple[Path, dict]:
    """Invoke preflight's own CLI entry with this runner's model; return (artefact, verdict)."""
    import preflight as pf
    # the first invocation writes preflight_<run_id>.json; every later one (a resume, a recovery
    # episode) writes its own stamped artefact -- an attempt is never overwritten, and each one
    # counts toward W1's one-gate-per-candidate-per-day schedule
    out = RUNS / f"preflight_{run_id}.json"
    if out.exists():
        out = RUNS / f"preflight_{run_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.json"
    argv, sys.argv = sys.argv, ["preflight.py", "--model", model_key, "--out", str(out),
                                "--instrument", INSTRUMENT_NAME]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                pf.main(client_factory=client_factory)
            except SystemExit:
                pass
    finally:
        sys.argv = argv
    if not out.exists():
        sys.exit("the gate wrote no artefact; nothing is certified")
    data = json.loads(out.read_text(encoding="utf-8"))
    return out, data


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #
# A3 embargo (pre-analysis specification §4b, 2026-10-14): once the fresh-sample census is listed in
# labels/A3_sample_manifest.json, no run of ANY instrument, development mode included, may touch an A3 paragraph
# before both coding passes are finalised, recorded in sessions.csv and committed without pending changes.
LABELS = ROOT / "tools" / "annotation" / "labels"
A3_SAMPLE_MANIFEST = LABELS / "A3_sample_manifest.json"
FRAME_COMPLETION_MANIFEST = LABELS / "A2_frame_completion_manifest.json"   # §4d (2026-10-19)
A3_FINAL_FILES = ("A3_pass1_v1.0.csv", "A3_pass2_v1.0.csv")
SESSIONS_CSV = LABELS / "sessions.csv"


def a3_committed(paths: list[Path]) -> tuple[bool, str]:
    """True when every path is tracked by git and carries no pending change in the index or the worktree."""
    try:
        rels = [str(p_.relative_to(ROOT)) for p_ in paths]
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", *rels], cwd=str(ROOT),
                                 capture_output=True, text=True, timeout=10)
        if tracked.returncode != 0:
            return False, "not tracked by git: " + (tracked.stderr.strip() or " ".join(rels))
        st = subprocess.run(["git", "status", "--porcelain", "--", *rels], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=10)
        if st.returncode != 0:
            return False, "git status failed"
        if st.stdout.strip():
            return False, "pending changes: " + st.stdout.strip().replace("\n", "; ")
        return True, "tracked and clean"
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return False, f"git unavailable ({exc})"


A3_SESSION = "A3"
A3_FINAL = {"A3_pass1": "A3_pass1_v1.0.csv", "A3_pass2": "A3_pass2_v1.0.csv"}


def _csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def a3_listing() -> dict | None:
    """The intact A3 listing, or None when no listing exists. An unreadable, edited (digest not reproduced) or
    structurally incomplete listing refuses every run: the embargo cannot be evaluated without it."""
    if not A3_SAMPLE_MANIFEST.exists():
        return None
    try:
        doc = json.loads(A3_SAMPLE_MANIFEST.read_text(encoding="utf-8"))
        if doc.get("kind") != "A3_sample_manifest":
            raise ValueError("kind is not A3_sample_manifest")
        if not doc.get("digest") or manifest_digest(doc) != doc["digest"]:
            raise ValueError("the stored digest is not reproduced by the content")
        paras = doc.get("paragraphs")
        if not isinstance(paras, list) or not paras:
            raise ValueError("no paragraphs")
        for q in paras:
            if not all(isinstance(q.get(k), str) and q.get(k) for k in ("paragraph_id", "article_id", "digest", "text_sha256")):
                raise ValueError("a paragraph lacks paragraph_id, article_id, digest or text_sha256")
        frame = json.loads(Path(sc.FRAME_MANIFEST).read_text(encoding="utf-8"))
        if not doc.get("frame_manifest_digest") or doc["frame_manifest_digest"] != frame.get("digest"):
            raise ValueError("not bound to the frozen frame manifest")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"REFUSING: {A3_SAMPLE_MANIFEST} is not an intact A3 sample manifest ({exc}); nothing runs while the "
                 "A3 embargo cannot be evaluated.")
    return doc


def a3_finalised(doc: dict) -> dict:
    """Evidence that the A3 reference is finalised and committed, or a refusal: both label files exist; sessions.csv
    records each pass with its file and a hash prefix the file reproduces; the pass-1 file covers exactly the A3
    paragraphs with their text hashes; the pass-2 (paragraph, technology) keys equal the keys the pass-1 ticks
    imply; the session manifest lists exactly the A3 paragraphs; every one of these files is tracked and clean."""
    ids = {q["paragraph_id"] for q in doc["paragraphs"]}
    th = {q["paragraph_id"]: q["text_sha256"] for q in doc["paragraphs"]}
    files = {sess: LABELS / fn for sess, fn in A3_FINAL.items()}
    missing = [f.name for f in files.values() if not f.exists()]
    if missing:
        sys.exit(f"REFUSING: A3 embargo -- the finalised label file(s) {', '.join(missing)} do not exist; no model run "
                 "touches an A3 paragraph before both passes are finalised and committed (specification §4b).")
    digests = {sess: hashlib.sha256(f.read_bytes()).hexdigest() for sess, f in files.items()}
    recorded = {}
    if SESSIONS_CSV.exists():
        for row in _csv_rows(SESSIONS_CSV):
            if row.get("session") in files:
                recorded[row["session"]] = row
    for sess, f in files.items():
        row = recorded.get(sess)
        if not row or str(row.get("labels_file", "")).strip() != f.name:
            sys.exit(f"REFUSING: A3 embargo -- sessions.csv does not record {sess} with {f.name} (finalize.py writes that record).")
        pref = str(row.get("labels_sha256", "")).strip()
        if len(pref) < 16 or not digests[sess].startswith(pref):
            sys.exit(f"REFUSING: A3 embargo -- the recorded hash of {f.name} does not match the file.")
    p1 = _csv_rows(files["A3_pass1"]); p2 = _csv_rows(files["A3_pass2"])
    if {r.get("paragraph_id") for r in p1} != ids:
        sys.exit("REFUSING: A3 embargo -- the pass-1 file does not cover exactly the A3 paragraphs.")
    if any(str(r.get("text_sha256", "")).strip() != th[r["paragraph_id"]] for r in p1):
        sys.exit("REFUSING: A3 embargo -- a pass-1 row carries a text hash that differs from the A3 listing.")
    keys1 = {(r["paragraph_id"], c[len("tech_"):]) for r in p1 for c in r if c and c.startswith("tech_") and str(r[c]).strip() == "1"}
    keys2 = {(r.get("paragraph_id"), r.get("technology")) for r in p2}
    if keys1 != keys2:
        sys.exit("REFUSING: A3 embargo -- the pass-2 (paragraph, technology) keys do not equal the keys the pass-1 ticks imply.")
    smp = LABELS / f"{A3_SESSION}_manifest.json"
    try:
        sm = json.loads(smp.read_text(encoding="utf-8"))
        if sm.get("session") != A3_SESSION or {q["paragraph_id"] for q in sm["paragraphs"]} != ids:
            raise ValueError("session or membership")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"REFUSING: A3 embargo -- {smp.name} does not list exactly the A3 paragraphs ({exc}).")
    ok, why = a3_committed(list(files.values()) + [SESSIONS_CSV, smp])
    if not ok:
        sys.exit(f"REFUSING: A3 embargo -- the A3 reference files are not committed without pending changes ({why}).")
    return {"finalised": {f.name: digests[sess] for sess, f in files.items()},
            "session_manifest_sha256": sha256_file(smp), "committed": why}


def a3_admissible() -> list[dict]:
    """The frame paragraphs the §4b rule admits, recomputed from the frozen frame and the registered A2 sample on
    disk (both intact); empty when either is missing. Membership in A3 is the union of the listing and this set,
    so an edited or trimmed listing cannot release a paragraph the rule protects (workflow review, 2026-10-14)."""
    try:
        frame = json.loads(Path(sc.FRAME_MANIFEST).read_text(encoding="utf-8"))
        a2 = json.loads(Path(sc.SAMPLE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if sc.canonical_digest(frame) != frame.get("digest") or sc.canonical_digest(a2) != a2.get("digest"):
        return []
    a2_dig = {q.get("digest") for q in a2.get("paragraphs") or []}
    a2_art = {q.get("article_id") for q in a2.get("paragraphs") or []}
    return [q for q in frame.get("paragraphs") or []
            if q.get("digest") not in a2_dig and q.get("exposure") == "clean_article" and q.get("article_id") not in a2_art]


def a3_embargo(paras: list[dict], dev: bool = False) -> dict | None:
    """Refuse (before the gate, before any call) a run that touches an A3 paragraph -- by identifier, digest or
    text hash -- while the A3 reference is not finalised and committed, and ANY development run that touches one
    (A3 is an evaluation set, never a development set); otherwise return the embargo state, which enters the
    execution contract and the run record. None = no A3 listing exists."""
    doc = a3_listing()
    if doc is None:
        return None
    protected = list(doc["paragraphs"]) + a3_admissible()          # the listing, and what the rule admits
    ids = {q.get("paragraph_id") for q in protected}
    dig = {q.get("digest") for q in protected} - {None}
    th = {q.get("text_sha256") for q in protected} - {None}
    touched = sorted({str(p_.get("paragraph_id")) for p_ in paras
                      if p_.get("paragraph_id") in ids or p_.get("digest") in dig or p_.get("text_sha256") in th})
    state = {"manifest": str(A3_SAMPLE_MANIFEST.relative_to(ROOT)) if A3_SAMPLE_MANIFEST.is_relative_to(ROOT) else str(A3_SAMPLE_MANIFEST),
             "a3_manifest_digest": doc["digest"], "touches_a3": bool(touched), "paragraphs_touched": len(touched)}
    if not touched:
        return state
    if dev:
        sys.exit(f"REFUSING: A3 is an evaluation set; a development run never touches an A3 paragraph "
                 f"({len(touched)} in this manifest; specification §4b).")
    state.update(a3_finalised(doc))
    return state


def git_status_snapshot() -> dict:
    """The working tree's status as `git status --porcelain -z` reports it, parsed unstripped and NUL-delimited
    (a leading space in the XY prefix is significant); every changed path is kept. `dirty: None` means git was
    unavailable, which is reported as unknown, never as clean."""
    try:
        r = subprocess.run(["git", "status", "--porcelain", "-z"], cwd=str(ROOT), capture_output=True,
                           text=True, timeout=10)
        if r.returncode != 0:
            return {"dirty": None, "changed_paths": None, "note": "git unavailable"}
    except Exception:  # noqa: BLE001
        return {"dirty": None, "changed_paths": None, "note": "git unavailable"}
    fields = r.stdout.split("\0")
    paths, i = [], 0
    while i < len(fields):
        entry = fields[i]
        if entry:
            xy, path = entry[:2], entry[3:]
            paths.append(path)
            if xy[0] in "RC" or xy[1] in "RC":   # an index OR worktree rename/copy carries the original path next
                i += 1
        i += 1
    return {"dirty": bool(paths), "changed_paths": sorted(paths)}


def git(*a: str) -> str:
    try:
        r = subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else "unavailable"
    except Exception:  # noqa: BLE001
        return "unavailable"


def fingerprint_payload(art: ins.Artifacts, model_key: str, k: int, manifest_sha: str, region: str,
                        restrict_split: str | None = None, dev: bool = False, w3_auth: dict | None = None,
                        a3: dict | None = None, mc: dict | None = None, a3_candidate: dict | None = None,
                        form: dict | None = None, translated: dict | None = None,
                        context: dict | None = None) -> dict:
    """The complete resolved execution contract as a document; `fingerprint()` is its digest.
    Persisted in the run record as `execution_contract` beside `execution_fingerprint`, so a
    consumer (the W1 selector) reads the contract instead of guessing it from a hash (Codex W1
    round 3, #1). Includes the resolved paragraph selection and the development status, so a
    resume cannot widen the sample or turn a development run into a reportable one."""
    contract = {
        "manifest_sha256": manifest_sha, "restrict_split": restrict_split, "dev": dev,
        "a3_embargo": a3,                       # specification §4b: the A3 embargo state binds the run (resume included)
        "prompt_sha256": art.prompt_sha256,
        "tool_config_sha256": art.tool_config_sha256, "codebook_sha256": art.codebook_sha256,
        "model_id": ins.MODELS[model_key], "region": region,
        # the request this model actually receives (§4g: the declared adaptation; for every earlier model these fields
        # keep exactly the pinned values -- a recomputed fingerprint still changes whenever a fingerprinted source file
        # changes; stored records are never rewritten)
        "inference_config": model_request(model_key, art, "x")["inferenceConfig"],
        # where a thinking setting would live under W1-b; None under the pinned request (W1-a)
        "additional_model_request_fields": model_request(model_key, art, "x").get("additionalModelRequestFields"),
        # §4m (2026-11-08): the tool choice, only where the declared adaptation changes it (absent otherwise, so that
        # every earlier contract keeps its keys)
        **({"tool_choice": model_request(model_key, art, "x")["toolConfig"].get("toolChoice")}
           if model_request(model_key, art, "x")["toolConfig"].get("toolChoice") != art.tool_config.get("toolChoice") else {}),
        "max_tokens": model_request(model_key, art, "x")["inferenceConfig"]["maxTokens"], "repeats": k, "retry_policy_version": retry_policy_version(),
        "retry_policy": {"parse": PARSE_RETRIES, "throttle": THROTTLE_RETRIES, "error": ERROR_RETRIES},
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "instrument_sha256": sha256_file(HERE / "instrument.py"),
        # W3 (2026-10-14): which instrument the run binds, and the variant module's digest under v1.1
        "instrument_version": str(getattr(ins, "INSTRUMENT_VERSION", "1.0")),
        "instrument_v11_sha256": (sha256_file(HERE / "instrument_v11.py")
                                  if str(getattr(ins, "INSTRUMENT_VERSION", "1.0")) != "1.0" else None),
        # §4b candidate (2026-10-26): the v1.0r3 module's digest under that configuration, None otherwise
        "instrument_v10r3_sha256": (sha256_file(HERE / "instrument_v10r3.py")
                                    if str(getattr(ins, "INSTRUMENT_VERSION", "1.0")) == "1.0r3" else None),
        "prompt_path": getattr(ins, "PROMPT_PATH_REL", "tools/pipeline/instrument/system-prompt_v1.0.txt"),
        "sample_contract_sha256": sha256_file(HERE / "sample_contract.py"),
        "preflight_sha256": sha256_file(HERE / "preflight.py"),     # the gate's own code, checked per gate artefact
        "max_recovery_episodes": MAX_RECOVERY_EPISODES,
        # W3 (Codex round 4, #4): the digests of the seal, the selection record, the author's decision and the A3
        # protocol under which a revised instrument runs on the registered sample; None for every other run
        "w3_authorisation": w3_auth,
    }
    if mc:
        # §4f (2026-10-19): the author's recorded decision for a robustness model comparison binds the run (resume
        # included); absent for every other run, so their contracts and fingerprints are unchanged
        contract["model_comparison"] = mc
    if a3_candidate:
        # §4b (2026-10-26): the author's recorded decision to run the frozen candidate v1.0r3 on the census binds the
        # run (resume included); absent for every other run
        contract["a3_candidate"] = a3_candidate
    if form:
        # §4j (2026-10-30): the author's recorded decision for the form comparison binds the run; absent elsewhere
        contract["form_variant"] = form
    if translated:
        # §4e(a) (2026-11-05): the translated-input override -- the translation file's digest, the per-paragraph source
        # and translation hashes, the translator and the German restriction bind the run (resume included); absent for
        # every other run, so their contracts and fingerprints are unchanged
        contract["translated_input"] = translated
    if context:
        # §4l (5) (2026-11-06): the author's decision, the comparator and the digest of the bound per-paragraph context
        # bind the run; the context module's own digest beside it; absent for every other run
        contract["context_variant"] = context
        contract["instrument_v10c_sha256"] = sha256_file(HERE / "instrument_v10c.py")
    if str(getattr(ins, "INSTRUMENT_VERSION", "1.0")) == "1.0F":
        # §4j: the form module's own digest (the v1.0r3 module it builds on is covered by the v1.1 digest field above
        # only for v1.1; here both are named explicitly)
        contract["instrument_v10f_sha256"] = sha256_file(HERE / "instrument_v10f.py")
        contract["instrument_v10r3_module_sha256"] = sha256_file(HERE / "instrument_v10r3.py")
    if model_key in getattr(ins, "NO_CACHE_POINT", ()):
        # §4f: the request of this run carries no cache point (provider without extended-TTL caching)
        contract["cache_point"] = False
    if model_key in getattr(ins, "EXTERNAL_API", {}):
        # §4g (Codex round 1, #1): the adapter translates the certified Converse envelope into the provider's request and
        # its answer back, so its code is part of what the run executed; present only for these models
        contract["adapter_sha256"] = sha256_file(HERE / "inception_client.py")
        contract["endpoint"] = ins.EXTERNAL_API[model_key]
    return contract


def fingerprint(art: ins.Artifacts, model_key: str, k: int, manifest_sha: str, region: str,
                restrict_split: str | None = None, dev: bool = False, w3_auth: dict | None = None, a3: dict | None = None,
                mc: dict | None = None, a3_cand: dict | None = None) -> str:
    """Digest of `fingerprint_payload()`; a resume under a different one is refused."""
    contract = fingerprint_payload(art, model_key, k, manifest_sha, region, restrict_split, dev, w3_auth, a3, mc, a3_cand)
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
@dataclass
class Totals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    throttles: int = 0
    errors: int = 0
    unknown_completion_attempts: int = 0
    usage_incomplete_attempts: int = 0     # finished attempts whose usage lacked a required field
    billing_uncertain_attempts: int = 0    # exception attempts whose request may have been served (timeouts)
    cache_writes_other_ttl: int = 0        # attempts whose write was stored for a TTL other than the requested
    torn_records: int = 0
    recovered_attempts: int = 0
    codes: dict = field(default_factory=dict)


def usage_of(r: dict) -> dict:
    """Token usage of one response, with the completeness the accounting needs (Codex W1
    rounds 1–2, #9/#3/#7). `inputTokens` and `outputTokens` are required non-negative ints;
    the two cache counters are optional in the provider schema and read as 0 when absent --
    but positive evidence elsewhere in the response is not ignored: a `cacheDetails`
    breakdown that reports written tokens while the aggregate counter is absent or differs
    makes the usage INCOMPLETE, and each detail entry must carry a ttl string and an int
    inputTokens. The stored TTLs of the write are kept per attempt, so a production write
    stored for 5 minutes is visible, not costed silently at the 1-hour rate."""
    u = r.get("usage") or {}

    def ok_int(v) -> bool:
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0

    def val(f: str) -> int:
        return u[f] if ok_int(u.get(f)) else 0
    complete = (all(ok_int(u.get(f)) for f in ("inputTokens", "outputTokens"))
                and all(f not in u or ok_int(u[f]) for f in ("cacheReadInputTokens", "cacheWriteInputTokens")))
    ok_d, ttls, _det_total, _why = ins.validate_cache_details(u)   # ONE reading, shared with the gate
    if not ok_d:
        complete = False
    return {"input_tokens": val("inputTokens"), "output_tokens": val("outputTokens"),
            "cache_read_tokens": val("cacheReadInputTokens"), "cache_write_tokens": val("cacheWriteInputTokens"),
            "usage_complete": complete,
            "cache_fields_absent": [f for f in ("cacheReadInputTokens", "cacheWriteInputTokens") if f not in u],
            "cache_write_ttl": sorted(set(ttls))}


def cost_of(t: Totals, model_key: str) -> float:
    p = PRICES_PER_M[model_key]
    return round((t.input_tokens * p["input"] + t.output_tokens * p["output"]
                  + t.cache_read_tokens * p["cache_read"]
                  + t.cache_write_tokens * p["cache_write_1h"]) / 1_000_000, 6)



def form_a1_ok(restrict_split, mdoc: dict, paras: list[dict], auth_a1: dict, excluded: set) -> bool:
    """§4j development selection: exactly the A1 split of the A1 session manifest, equal to the authoritative A1 manifest
    by id and text hash (Codex §4j round 2), with no A2 or A3 paragraph -- and no duplicate row (Codex §4j round 3: a
    dictionary comparison alone lets the 32 paragraphs plus a repeated one pass)."""
    a1_ids = {q["paragraph_id"] for q in (mdoc.get("paragraphs") or []) if q.get("split") == "A1"}
    run_ids = {q["paragraph_id"] for q in paras}
    run_hash = {q["paragraph_id"]: q.get("text_sha256") for q in paras}
    return (restrict_split == "A1" and mdoc.get("session") == "A1" and bool(a1_ids) and run_ids == a1_ids
            and not run_ids & excluded and len(auth_a1) == sc.FORM_A1_PARAGRAPHS and run_hash == auth_a1
            and len(paras) == len(run_ids) == sc.FORM_A1_PARAGRAPHS)


def run(a: argparse.Namespace, client_factory=None, sleep=time.sleep) -> dict:
    bind_instrument(getattr(a, "instrument", "v1.0") or "v1.0")   # W3: v1.0 unless a variant is named
    global ALLOW_REASONING
    ALLOW_REASONING = a.model in getattr(ins, "THINKING_ON", frozenset())   # §4m: only where the request asks for it
    load_dotenv()                       # credentials only; the REGION is the instrument's
    # The region is owned by the instrument (D14): the gate certifies a request against
    # ins.REGION, so the runner must call the same region whatever AWS_REGION says.
    region = ins.REGION
    if a.model in getattr(ins, "EXTERNAL_API", {}):
        # §4g: not a Bedrock model; the record names the provider's endpoint where a Bedrock run names its region
        region = ins.EXTERNAL_API[a.model]
    RUNS.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    mpath = Path(a.manifest)
    mdoc, paras = load_manifest(mpath, a.restrict_split)
    # The production boundary is enforced here, not by the --dev flag alone (Codex W1
    # round 1, #13): a development run must name its split, because a session manifest
    # lists more than the development split (A1_manifest.json carries 35 rows, 32 of them
    # A1); a reportable run must run on an A2 sample manifest whose every paragraph
    # carries a positive design weight -- a development manifest cannot be promoted by
    # omitting a flag.
    if a.dev and not a.restrict_split:
        sys.exit("REFUSING: a development run needs --restrict-split <split>; a session manifest "
                 "lists more than the development split.")
    if not a.dev and a.restrict_split:
        sys.exit("REFUSING: a reportable run processes the whole registered sample; --restrict-split is a "
                 "development option (a filtered sample keeps the full sample's weights).")
    fresh = mdoc.get("kind") == sc.A3_KIND     # the fresh-sample census (specification §4b, 2026-10-14)
    completion = mdoc.get("kind") == sc.FRAME_COMPLETION_KIND   # the DSL frame completion (§4d, 2026-10-19)
    ti_path = getattr(a, "translated_input", None)
    if ti_path:
        # §4e(a) language arm (amendment of 2026-11-05): the flag-level guard of the declared input override, before
        # every other analysis's guard, so a combination that would mislabel the run is refused for this arm's reason
        if a.dev or a.restrict_split:
            sys.exit("REFUSING: the translated-input arm is a reportable run on the registered A2 sample (specification "
                     "§4e(a)); --dev and --restrict-split do not combine with --translated-input.")
        try:
            reg_digest = json.loads(Path(sc.SAMPLE_MANIFEST).read_text(encoding="utf-8")).get("digest")
        except (OSError, ValueError, AttributeError):
            reg_digest = None
        if mdoc.get("kind") != "A2_sample_manifest" or fresh or completion or not reg_digest \
                or mdoc.get("digest") != reg_digest:
            sys.exit("REFUSING: --translated-input is declared on the registered A2 sample manifest only (specification "
                     "§4e(a)); the manifest presented is not it.")
        if INSTRUMENT_NAME != "v1.0" or retry_policy_version() != "2":
            sys.exit("REFUSING: the translated-input arm runs the registered instrument v1.0 under retry policy v2 only "
                     "(specification §4e(a) item 7).")
        if a.model != sc.REGISTERED_MODEL_KEY:
            sys.exit(f"REFUSING: the translated-input arm runs the registered model ({sc.REGISTERED_MODEL_KEY}) only; a "
                     f"model comparison does not combine with translated inputs (specification §4e(a)).")
        if any((getattr(a, f, None) or "").strip() for f in ("w3_author_decision", "a3_protocol", "a3_candidate_decision",
                                                               "form_variant_decision", "model_comparison_decision",
                                                               "context_variant_decision")):
            sys.exit("REFUSING: a decision flag of another declared analysis does not combine with --translated-input.")
        if int(a.repeats) != sc.TRANSLATED_REPEATS:
            sys.exit(f"REFUSING: the translated-input arm is declared with k = {sc.TRANSLATED_REPEATS} executions per "
                     f"paragraph (specification §4e(a) item 7); got k = {a.repeats}.")
    if not a.dev:
        # the ONE sample contract: for the registered sample `sample_contract.verify_sample` (kind, frame digest,
        # registry handshake, membership, registered strata, probabilities, finite positive weights); for the A3
        # census `verify_a3_sample` (the intact listing, the same handshake, the census rule, weight one, the
        # audit) -- the same validators the evaluator runs
        try:
            if fresh:
                sc.verify_a3_sample(mdoc, listing_path=A3_SAMPLE_MANIFEST)
            elif completion:
                sc.verify_frame_completion(mdoc, listing_path=FRAME_COMPLETION_MANIFEST, a3_path=A3_SAMPLE_MANIFEST)
            else:
                sc.verify_sample(mdoc)
        except sc.ContractError as exc:
            sys.exit(f"REFUSING: {exc}")
    a3 = a3_embargo(paras, dev=a.dev)           # A3 embargo (specification §4b): refuses before the gate and any call
    # Robustness model comparison (specification §4f, 2026-10-19): a reportable run under any model other than the
    # registered one is the declared comparison -- the registered instrument v1.0, one execution per paragraph, on the
    # registered A2 sample or the A3 census only, and only with the author's recorded decision, which enters the
    # execution contract; it is labelled as such everywhere and informs no registered figure
    # (a revised instrument under another model is not a comparison: the W3 launch guard below refuses it with its own reason)
    comparison = (not a.dev) and a.model != sc.REGISTERED_MODEL_KEY and INSTRUMENT_NAME == "v1.0"
    mc_decision = (getattr(a, "model_comparison_decision", None) or "").strip()
    mc = None
    if mc_decision and not comparison:
        sys.exit("REFUSING: --model-comparison-decision belongs to a reportable run under a non-registered model "
                 "(specification §4f); this run is not one.")
    if comparison:
        if completion:
            sys.exit("REFUSING: the frame-completion run is declared for the registered model only (specification §4d).")
        if a.model not in sc.MODEL_COMPARISON_ROSTER:
            sys.exit(f"REFUSING: {a.model!r} is not in the declared comparison roster {list(sc.MODEL_COMPARISON_ROSTER)} "
                     f"(specification §4f); another model needs its own dated declaration first.")
        if int(a.repeats) != sc.model_comparison_repeats(a.model):
            sys.exit(f"REFUSING: the model comparison declares k = {sc.model_comparison_repeats(a.model)} for "
                     f"{a.model!r} ({sc.MODEL_COMPARISON_AUTHORITY_OF[a.model]}); got k = {a.repeats}.")
        if not mc_decision:
            sys.exit(f"REFUSING: a reportable run under the non-registered model {a.model!r} is the declared model "
                     f"comparison (specification §4f) and needs the author's recorded decision "
                     f"(--model-comparison-decision \"<who decided, when, in which words>\").")
        if fresh and not (a3 and a3.get("touches_a3") and a3.get("finalised")):
            sys.exit("REFUSING: an A3 run carries no embargo evidence (finalised reference); nothing runs.")
        mc = {"decision": mc_decision, "decision_sha256": hashlib.sha256(mc_decision.encode("utf-8")).hexdigest(),
              "authority": sc.MODEL_COMPARISON_AUTHORITY_OF[a.model], "registered_model": sc.REGISTERED_MODEL_KEY,
              "sample": "A3 census" if fresh else "registered A2 sample"}
    # §4j (2026-10-30): the mandatory-form variant v1.0F and its comparator v1.0r3 -- the A1 development split (k = 1),
    # the registered A2 sample (k = 1) and, for v1.0F only, the A3 census (k = 3); the registered model; the author's
    # recorded decision bound into the contract; descriptive, replaces nothing
    form_decision = (getattr(a, "form_variant_decision", None) or "").strip()
    form = None
    if INSTRUMENT_NAME == sc.FORM_VARIANT and not form_decision:
        sys.exit("REFUSING: v1.0F runs only under §4j with the author's recorded decision "
                 "(--form-variant-decision \"<who decided, when, in which words>\").")
    if form_decision:
        if INSTRUMENT_NAME not in sc.FORM_INSTRUMENTS:
            sys.exit(f"REFUSING: --form-variant-decision belongs to {list(sc.FORM_INSTRUMENTS)} under §4j.")
        if comparison or completion or a.model != sc.REGISTERED_MODEL_KEY:
            sys.exit("REFUSING: the §4j form comparison runs the registered model on A1, A2 or A3 only.")
        f_sample = "A1" if a.dev else ("A3" if fresh else "A2")
        if a.dev:
            # Codex §4j round 1 (#1): membership, not the label -- the declared selection is exactly the A1 split of the
            # A1 session manifest, and no paragraph of the registered A2 sample or the A3 census may be among it
            try:
                reg_ids = {q["paragraph_id"] for q in json.loads(Path(sc.SAMPLE_MANIFEST).read_text(encoding="utf-8"))["paragraphs"]}
                a3_ids = {q["paragraph_id"] for q in json.loads(Path(A3_SAMPLE_MANIFEST).read_text(encoding="utf-8"))["paragraphs"]}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                sys.exit(f"REFUSING: the A2 and A3 memberships cannot be established ({exc}); the §4j development run needs them.")
            # Codex §4j round 2 (#1): against the AUTHORITATIVE A1 manifest -- the same 32 paragraphs, by id and text hash
            try:
                auth = json.loads((LABELS / "A1_manifest.json").read_text(encoding="utf-8"))
                auth_a1 = {q["paragraph_id"]: q.get("text_sha256") for q in auth["paragraphs"] if q.get("split") == "A1"}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                sys.exit(f"REFUSING: the authoritative A1 manifest cannot be read ({exc}).")
            if not form_a1_ok(a.restrict_split, mdoc, paras, auth_a1, reg_ids | a3_ids):
                sys.exit("REFUSING: the §4j development run is exactly the A1 split of the A1 session manifest "
                         "(--restrict-split A1), with no A2 or A3 paragraph.")
        if not a.dev and not fresh and mdoc.get("kind") != "A2_sample_manifest":
            sys.exit("REFUSING: a reportable §4j run uses the registered A2 sample manifest or the A3 census.")
        if f_sample == "A3" and INSTRUMENT_NAME != sc.FORM_VARIANT:
            sys.exit(f"REFUSING: on the A3 census §4j runs v1.0F only; its comparator is the existing run {sc.A3_CANDIDATE}.")
        if int(a.repeats) != sc.FORM_REPEATS[f_sample]:
            sys.exit(f"REFUSING: §4j fixes k = {sc.FORM_REPEATS[f_sample]} on {f_sample}; got k = {a.repeats}.")
        if fresh and not (a3 and a3.get("touches_a3") and a3.get("finalised")):
            sys.exit("REFUSING: an A3 run carries no embargo evidence (finalised reference); nothing runs.")
        if retry_policy_version() != "3":
            sys.exit("REFUSING: §4j compares v1.0F and v1.0r3 under retry policy v3; the bound instrument says otherwise.")
        form = {"configuration": INSTRUMENT_NAME, "decision": form_decision,
                "decision_sha256": hashlib.sha256(form_decision.encode("utf-8")).hexdigest(),
                "authority": sc.FORM_AUTHORITY, "sample": f_sample, "retry_policy_version": "3",
                "max_tokens": output_cap()}
    # §4l (5) (2026-11-06): the context-parity variant v1.0C -- the registered request with the coding interface's
    # headline, outlet and language before the paragraph; the registered model, k = 1, retry policy v2, on the registered
    # A2 sample or the A3 census; the author's recorded decision bound into the contract; descriptive, replaces nothing
    ctx_decision = (getattr(a, "context_variant_decision", None) or "").strip()
    context = None
    if INSTRUMENT_NAME == sc.CONTEXT_VARIANT and not ctx_decision:
        sys.exit("REFUSING: v1.0C runs only under §4l (5) with the author's recorded decision "
                 "(--context-variant-decision \"<who decided, when, in which words>\").")
    if ctx_decision:
        if INSTRUMENT_NAME != sc.CONTEXT_VARIANT:
            sys.exit(f"REFUSING: --context-variant-decision belongs to {sc.CONTEXT_VARIANT} under §4l (5).")
        if comparison or completion or form or ti_path or a.model != sc.REGISTERED_MODEL_KEY:
            sys.exit("REFUSING: the §4l context variant runs the registered model on the registered A2 sample or the A3 "
                     "census only, combined with no other declared analysis.")
        if a.dev:
            sys.exit("REFUSING: the §4l context variant is declared as a reportable run on A2 or A3; no development run.")
        if not fresh and mdoc.get("kind") != "A2_sample_manifest":
            sys.exit("REFUSING: a §4l context run uses the registered A2 sample manifest or the A3 census.")
        if fresh and not (a3 and a3.get("touches_a3") and a3.get("finalised")):
            sys.exit("REFUSING: an A3 run carries no embargo evidence (finalised reference); nothing runs.")
        if int(a.repeats) != sc.CONTEXT_REPEATS:
            sys.exit(f"REFUSING: §4l fixes k = {sc.CONTEXT_REPEATS}; got k = {a.repeats}.")
        if retry_policy_version() != "2":
            sys.exit("REFUSING: v1.0C runs under the registered retry policy v2; the bound instrument says otherwise.")
        context = {"configuration": INSTRUMENT_NAME, "decision": ctx_decision,
                   "decision_sha256": hashlib.sha256(ctx_decision.encode("utf-8")).hexdigest(),
                   "authority": sc.CONTEXT_AUTHORITY, "sample": "A3" if fresh else "A2", "retry_policy_version": "2",
                   "comparator": sc.CONTEXT_COMPARATORS["A3" if fresh else "A2"],
                   "fields": list(ins10c.CONTEXT_FIELDS)}
    a3_cand = None
    cand_decision = (getattr(a, "a3_candidate_decision", None) or "").strip()
    if INSTRUMENT_NAME == sc.A3_CANDIDATE and not form and not (fresh and not comparison and not a.dev):
        sys.exit("REFUSING: the candidate configuration v1.0r3 is declared for the reportable A3 census run only "
                 "(specification §4b); it runs nowhere else, development mode included.")
    if cand_decision and INSTRUMENT_NAME != sc.A3_CANDIDATE:
        sys.exit("REFUSING: --a3-candidate-decision belongs to the candidate configuration v1.0r3 on the A3 census "
                 "(specification §4b); this run is not one.")
    if fresh and not comparison and not form and not context:      # §4l (5): v1.0C is guarded by its own block
        # §4b: the registered instrument, or the one frozen candidate v1.0r3 on the author's recorded decision taken
        # after the v1.0 A3 figure; the registered model, k = 3, and the embargo released by finalised, recorded and
        # committed reference files
        if INSTRUMENT_NAME not in ("v1.0", sc.A3_CANDIDATE):
            sys.exit("REFUSING: the A3 census is declared for the registered instrument v1.0 and the one frozen "
                     "candidate v1.0r3 only (specification §4b).")
        if a.model != "sonnet" or int(a.repeats) != sc.A3_REPEATS:
            sys.exit(f"REFUSING: the A3 census runs under the registered model (sonnet) with k = {sc.A3_REPEATS} "
                     f"(specification §4b); got {a.model}, k = {a.repeats}.")
        if not (a3 and a3.get("touches_a3") and a3.get("finalised")):
            sys.exit("REFUSING: an A3 run carries no embargo evidence (finalised reference); nothing runs.")
        if INSTRUMENT_NAME == sc.A3_CANDIDATE:
            if not cand_decision:
                sys.exit("REFUSING: the candidate v1.0r3 runs on the census only with the author's recorded decision, "
                         "taken after the v1.0 A3 figure (--a3-candidate-decision \"<who decided, when, in which words>\"; "
                         "specification §4b).")
            if not (RUNS / f"{sc.A3_BASELINE_RUN}.json").exists():
                sys.exit(f"REFUSING: the candidate v1.0r3 is run after the v1.0 A3 figure; the baseline run record "
                         f"tools/runs/{sc.A3_BASELINE_RUN}.json is missing (specification §4b).")
            if retry_policy_version() != "3":
                sys.exit("REFUSING: v1.0r3 is the registered request under retry policy v3; the bound instrument says otherwise.")
            a3_cand = {"configuration": sc.A3_CANDIDATE, "decision": cand_decision,
                       "decision_sha256": hashlib.sha256(cand_decision.encode("utf-8")).hexdigest(),
                       "authority": sc.A3_CANDIDATE_AUTHORITY, "baseline_run": sc.A3_BASELINE_RUN,
                       "retry_policy_version": "3"}
    if completion:
        # §4d: the registered instrument, the registered model, one execution; the contract excludes every A2 and
        # A3 paragraph by digest, and the embargo check above has refused any A3 contact anyway
        if INSTRUMENT_NAME != "v1.0":
            sys.exit("REFUSING: the frame-completion run is declared for the registered instrument v1.0 only (specification §4d).")
        if a.model != "sonnet" or int(a.repeats) != sc.FRAME_COMPLETION_REPEATS:
            sys.exit(f"REFUSING: the frame-completion run is declared with the registered model (sonnet) and k = "
                     f"{sc.FRAME_COMPLETION_REPEATS} (specification §4d); got {a.model}, k = {a.repeats}.")
        if a3 and a3.get("touches_a3"):
            sys.exit("REFUSING: the frame-completion listing touches an A3 paragraph.")
    # W3 launch guard (Codex W3 rounds 3-4): a revised instrument may touch registered A2 paragraphs only under the
    # conditions the specification's W3 block fixes -- an intact seal, a selection record bound to that seal with the
    # outcome "selected" naming this variant, Sonnet with k = 3, the author's recorded decision and a written A3
    # protocol; their digests enter the execution contract, so a resume is bound to them. A development run of a
    # revised instrument over registered A2 paragraphs is refused outright (it would inspect A2 under an unsealed
    # configuration). The analysis status of every run is recorded and travels into the metrics and the captions.
    analysis_status = ("development" if a.dev else sc.CONTEXT_STATUS if context else sc.FORM_STATUS if form
                       else sc.MODEL_COMPARISON_STATUS if comparison
                       else sc.A3_STATUS if fresh else sc.FRAME_COMPLETION_STATUS if completion else "registered primary")
    w3_auth = None
    if INSTRUMENT_NAME not in ("v1.0", sc.A3_CANDIDATE) and not form and not context:
        # membership in the registered A2 sample must be ESTABLISHED from an intact, structurally valid manifest; if
        # it cannot be, a development launch of a revised instrument is refused rather than let through (Codex round 5, #2)
        try:
            reg = json.loads(Path(sc.SAMPLE_MANIFEST).read_text(encoding="utf-8"))
            registered = reg.get("paragraphs") if isinstance(reg, dict) else None
            if not isinstance(registered, list) or not registered or not all(isinstance(q, dict) and q.get("paragraph_id") for q in registered):
                raise ValueError("no non-empty paragraphs list with paragraph_id values")
            # intact: the registered kind and a stored digest that its content reproduces (Codex W3 round 6, #1)
            if reg.get("kind") != "A2_sample_manifest" or not reg.get("digest") or reg["digest"] != manifest_digest(reg):
                raise ValueError("not an intact A2 sample manifest (kind or digest)")
            registered_ids = {q["paragraph_id"] for q in registered}
        except (OSError, ValueError, TypeError) as exc:
            sys.exit(f"REFUSING: membership in the registered A2 sample cannot be established from {sc.SAMPLE_MANIFEST} "
                     f"({exc}); a revised instrument is not launched without it.")
        touches_a2 = bool({p["paragraph_id"] for p in paras} & registered_ids)
        if a.dev and touches_a2:
            sys.exit("REFUSING: a revised instrument may not run over registered A2 paragraphs in development mode "
                     "(specification §4, W3 block).")
        if not a.dev:
            import w3 as w3mod                                  # lazy: w3 imports this module
            try:
                seal = w3mod.load_seal()
            except SystemExit as exc:
                sys.exit(f"REFUSING: the W3 seal does not verify ({exc}).")
            sel_path = RUNS / "w3_selection.json"
            if not sel_path.exists():
                sys.exit("REFUSING: a revised instrument on the registered sample needs tools/runs/w3_selection.json "
                         "with the outcome 'selected' (specification §4, W3 block).")
            sel = json.loads(sel_path.read_text(encoding="utf-8"))
            d = sel.get("decision") or {}
            if sel.get("seal_digest") != seal.get("digest"):
                sys.exit("REFUSING: the W3 selection record is not bound to the intact seal.")
            if d.get("outcome") != "selected" or d.get("selected") != INSTRUMENT_NAME:
                sys.exit(f"REFUSING: the W3 selection outcome is {d.get('outcome')!r} for {d.get('selected')!r}; only the "
                         f"outcome 'selected' naming {INSTRUMENT_NAME} authorises this run.")
            if a.model != "sonnet" or int(a.repeats) != 3:
                sys.exit("REFUSING: the exploratory A2 run is fixed to Sonnet 4.6 with k = 3 (specification §4, W3 block).")
            decision = (getattr(a, "w3_author_decision", None) or "").strip()
            a3_protocol_path = getattr(a, "a3_protocol", None)
            if not decision:
                sys.exit("REFUSING: the author's decision to run the exploratory A2 analysis must be recorded "
                         "(--w3-author-decision \"<who decided, when, in which words>\").")
            if not a3_protocol_path or not Path(a3_protocol_path).exists():
                sys.exit("REFUSING: the A3 confirmatory protocol must be written before the exploratory A2 run "
                         "(--a3-protocol <existing file>).")
            analysis_status = "exploratory reused A2"
            w3_auth = {"seal_digest": seal["digest"], "selection": rel(sel_path), "selection_sha256": sha256_file(sel_path),
                       "selected": d.get("selected"), "author_decision": decision,
                       "author_decision_sha256": hashlib.sha256(decision.encode("utf-8")).hexdigest(),
                       "a3_protocol": str(a3_protocol_path), "a3_protocol_sha256": sha256_file(Path(a3_protocol_path))}
    # §4e(a) language arm (amendment of 2026-11-05): the declared input override. The registered instrument v1.0 under
    # the registered model, k = 3, on the stored translations of the German paragraphs of the registered A2 sample;
    # the manifest, paragraph ids, design weights and German sampling identity stay those of A2, and only the paragraph
    # text of the registered request is replaced. Every combination that would mislabel the run is refused before the
    # gate and before any call.
    translated, translations = None, None
    if ti_path:
        # (the flag-level guard ran above, before every other analysis's guard; this re-asserts the bound state)
        if INSTRUMENT_NAME != "v1.0" or retry_policy_version() != "2" or w3_auth or form or a3_cand or comparison or mc:
            sys.exit("REFUSING: the translated-input arm runs the registered instrument and model only (specification §4e(a)).")
        de = [p_ for p_ in paras if p_.get("lang") == sc.TRANSLATED_LANG]
        if len(de) != int(sc.REGISTERED_TARGET.get(sc.TRANSLATED_LANG, -1)):
            sys.exit(f"REFUSING: the registered sample yields {len(de)} German paragraphs, not the registered "
                     f"{sc.REGISTERED_TARGET.get(sc.TRANSLATED_LANG)}.")
        # the ONE verification (sample_contract.load_translation_file): a path git ignores; every row against the
        # manifest and the registered text (full source hash, the frozen request's digest, the frozen prompt, end_turn,
        # attempts); the file against the public journal and the committed record of the translation step
        de_texts = {pid: r["paragraph_text"] for pid, r in load_texts(de).items()}
        try:
            translations, translated = sc.load_translation_file(Path(ti_path), de, de_texts)
        except sc.ContractError as exc:
            sys.exit(f"REFUSING: the translated input does not verify ({exc}).")
        paras = de                              # the explicit restriction: the German A2 paragraphs, nothing else
        analysis_status = sc.TRANSLATED_STATUS
    texts = load_texts(paras)
    if context:
        # §4l (5): the per-paragraph context the coding sheets carry (frozen registry metadata), bound before the
        # contract so that its digest binds the run; identical texts must carry identical context
        by_text = {}
        for pid_, r_ in texts.items():
            c_ = {"headline": r_.get("headline") or "", "outlet": r_.get("outlet") or "", "lang": r_.get("lang") or ""}
            if r_["paragraph_text"] in by_text and by_text[r_["paragraph_text"]] != c_:
                sys.exit(f"REFUSING: two paragraphs share the text of {pid_} but not its context; v1.0C cannot bind it.")
            by_text[r_["paragraph_text"]] = c_
        try:
            context["context_digest"] = ins.bind_context(by_text)
        except Exception as exc:                                   # noqa: BLE001 -- a malformed field refuses the run
            sys.exit(f"REFUSING: the context of the sample does not bind ({exc}).")
        context["paragraphs"] = len(texts)
    art = ins.load_artifacts()
    doc, subtypes, _ = ins.load_codebook()
    msha = manifest_digest(mdoc)
    contract = fingerprint_payload(art, a.model, a.repeats, msha, region, a.restrict_split, a.dev, w3_auth, a3, mc, a3_cand,
                                   form, translated, context)
    fp = hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()

    run_record_path = RUNS / f"{a.run_id}.json"
    calls_path = RUNS / f"{a.run_id}_calls.jsonl"
    raw_path = RAW / f"{a.run_id}.jsonl"
    if getattr(a, "recover_infrastructure", False) and not a.resume:
        sys.exit("REFUSING: --recover-infrastructure only makes sense with --resume.")
    if calls_path.exists() and not a.resume:
        sys.exit(f"{calls_path.name} exists; pass --resume to continue this run, or choose a "
                 f"new --run-id. A run is never silently overwritten.")

    # ---- recovery: rebuild state from the journals, replaying the state machine ---- #
    totals = Totals()
    started_at = utcnow()
    # the tree's status BEFORE this invocation creates or touches any file; the ORIGINAL run's snapshot is
    # persisted in the run_started event and restored on resume, this invocation's is kept beside it
    git_at_invocation = git_status_snapshot()
    git_at_start = git_at_invocation
    prior_calls, torn1 = Journal.read(calls_path)
    prior_raw, torn2 = Journal.read(raw_path)
    totals.torn_records = torn1 + torn2
    if prior_calls:
        head = next((r for r in prior_calls if r.get("type") == "run_started"), None)
        if head is None or head.get("fingerprint") != fp:
            sys.exit("REFUSING to resume: the execution contract (manifest and selection, "
                     "development status, instrument, model, inference config, repeats, retry "
                     "policy, code) differs from the one this run started under.")
        started_at = head["started_at"]
        git_at_start = head.get("git_at_start", {"dirty": None, "changed_paths": None,
                                                 "note": "run started before the snapshot existed"})
        # A completed run is never touched by a resume (Codex §4f round 2, #3; the 2026-10-19 incident in which a resume
        # of a finished run appended an uncertified gate and made it unevaluable). Decided from a READ of the journals,
        # before they are opened for appending (opening repairs a torn tail) and before the gate or any call: if the
        # journals carry no torn line, the run record exists and names exactly this journal, every (paragraph,
        # execution) has its terminal and no infrastructure terminal may be reopened, the existing record is returned
        # unchanged. A run without a record, with a stale record or with a torn line is resumed as before.
        if torn1 == 0 and torn2 == 0 and run_record_path.exists():
            try:
                r_ = json.loads(run_record_path.read_text(encoding="utf-8"))
                meta_ = next((v for k_, v in (r_.get("files") or {}).items() if k_.endswith("_calls.jsonl")), None)
                intact_record = bool(meta_) and sha256_file(calls_path) == meta_.get("sha256")
            except (OSError, ValueError, AttributeError):
                intact_record = False
            if intact_record:
                terms_ = {(r["paragraph_id"], int(r["run_index"])): r for r in prior_calls if r.get("type") == "terminal"}
                episodes_ = Counter((r["paragraph_id"], int(r["run_index"])) for r in prior_calls if r.get("type") == "recovery_started")
                pending_work = []
                for p_ in paras:
                    for k_ in range(1, int(a.repeats) + 1):
                        key_ = (p_["paragraph_id"], k_); term_ = terms_.get(key_)
                        reopen_ = (getattr(a, "recover_infrastructure", False) and term_ is not None
                                   and term_.get("outcome") == T_ERROR and episodes_[key_] < MAX_RECOVERY_EPISODES)
                        if term_ is None or reopen_:
                            pending_work.append(key_)
                if not pending_work:
                    print(f"run {a.run_id} is complete and its record matches its journal: nothing to resume; the record is "
                          f"returned unchanged (no gate, no call, nothing appended).", file=sys.stderr)
                    return r_
    raw_by_attempt = {(r["paragraph_id"], r["run_index"], r["attempt_no"]): r
                      for r in prior_raw if r.get("type") == "raw"}
    exc_by_attempt = {(r["paragraph_id"], r["run_index"], r["attempt_no"]): r
                      for r in prior_raw if r.get("type") == "exception"}
    state: dict[tuple[str, int], dict] = {}   # (pid, run_index) -> {codes, payloads, terminal}
    gates_seen: list[dict] = []               # every gate this run ran, in order, from the journal
    for rec in prior_calls:
        t = rec.get("type")
        if t == "gate":
            gates_seen.append(rec)
            continue
        if t not in ("attempt_started", "attempt_finished", "attempt_abandoned", "terminal", "recovery_started"):
            continue
        key = (rec["paragraph_id"], rec["run_index"])
        st = state.setdefault(key, {"codes": [], "attempts": [], "payloads": {}, "terminal": None,
                                    "started_open": {}, "usage": [], "max_attempt": 0, "episode": 0,
                                    "last_invalid": None})
        if t == "recovery_started":
            # the episode replays the retry policy from scratch: the codes resolve() sees start
            # over, the attempt numbering and every journalled attempt do not
            st["terminal"] = None; st["episode"] = int(rec["episode"])
            st["codes"], st["attempts"] = [], []; st["last_invalid"] = None
            continue
        st["max_attempt"] = max(st["max_attempt"], int(rec.get("attempt_no", 0) or 0))
        if t == "attempt_started":
            st["started_open"][rec["attempt_no"]] = rec
            st.setdefault("gate_of", {})[rec["attempt_no"]] = rec.get("gate_sha256")
        elif t == "attempt_finished":
            st["started_open"].pop(rec["attempt_no"], None)
            st["codes"].append(rec["code"]); st["attempts"].append(rec["attempt_no"])
            if rec.get("payload") is not None:
                st["payloads"][rec["attempt_no"]] = rec["payload"]
            st["usage"].append(rec.get("usage") or {})
            # the pending invalid call survives a crash between attempts: rebuilt from the private response
            # of that attempt (Codex W3 round 3, #1); a transport failure keeps it; any other returned
            # response clears it
            if rec["code"] in (SCHEMA_INVALID, CONTRACT_INVALID):
                rr = raw_by_attempt.get((key[0], key[1], rec["attempt_no"]))
                if rr is not None:
                    _c, _p, _r, problems, use = classify(rr["response"], art, doc, subtypes)
                    st["last_invalid"] = (use, problems)
                else:
                    st["last_invalid"] = (MISSING_PRIVATE, None)
            elif rec["code"] not in (THROTTLED, ERROR):
                st["last_invalid"] = None
        elif t == "attempt_abandoned":
            st["started_open"].pop(rec["attempt_no"], None)   # already counted, durably
        elif t == "terminal":
            st["terminal"] = rec
    calls = Journal(calls_path)
    raw = Journal(raw_path)
    if not prior_calls:
        calls.write({"type": "run_started", "run_id": a.run_id, "started_at": started_at,
                     "fingerprint": fp, "manifest": rel(mpath),
                     "manifest_sha256": msha, "model": ins.MODELS[a.model], "repeats": a.repeats,
                     "reportable": not a.dev, "git_at_start": git_at_start,
                     "a3_embargo": a3,
                     "instrument": INSTRUMENT_NAME, "retry_policy_version": retry_policy_version(),
                     "analysis_status": analysis_status, **({"model_comparison": mc} if mc else {}),
                     **({"a3_candidate": a3_cand} if a3_cand else {}),
                     **({"form_variant": form} if form else {}),
                     **({"context_variant": context} if context else {}),
                     **({"translated_input": translated} if translated else {})})
    # started-but-unfinished attempts: rebuild from raw where possible (response OR recorded
    # exception), else unknown completion -- abandoned durably, so a later resume does not
    # count it again, and never re-used as an attempt number
    for key, st in state.items():
        for att_no, srec in sorted(st["started_open"].items()):
            rr = raw_by_attempt.get((key[0], key[1], att_no))
            er = exc_by_attempt.get((key[0], key[1], att_no))
            if rr is None and er is not None:
                is_throttle = "Throttl" in str(er.get("error_class", "")) or "Throttl" in str(er.get("error_code", ""))
                code = THROTTLED if is_throttle else ERROR
                calls.write({"type": "attempt_finished", "run_id": a.run_id, "paragraph_id": key[0],
                             "run_index": key[1], "attempt_no": att_no, "code": code, "contract_rules": [],
                             "stop_reason": None, "usage": None, "latency_ms": None, "payload": None,
                             "error_class": er.get("error_class"), "error_code": er.get("error_code"),
                             "recovered_from_raw": True, "at": utcnow()})
                st["codes"].append(code); st["attempts"].append(att_no)
                totals.recovered_attempts += 1
                continue
            if rr is not None:
                code, payload, rules, problems, use = classify(rr["response"], art, doc, subtypes)
                calls.write({"type": "attempt_finished", "run_id": a.run_id, "paragraph_id": key[0],
                             "run_index": key[1], "attempt_no": att_no, "code": code,
                             "contract_rules": rules, "stop_reason": rr["response"].get("stopReason"),
                             "usage": usage_of(rr["response"]), "latency_ms": None,
                             "payload": public_payload(payload) if payload else None,
                             "recovered_from_raw": True, "at": utcnow()})
                st["codes"].append(code); st["attempts"].append(att_no)
                if payload:
                    st["payloads"][att_no] = public_payload(payload)
                st["usage"].append(usage_of(rr["response"]))
                st["last_invalid"] = (use, problems) if code in (SCHEMA_INVALID, CONTRACT_INVALID) else None
                totals.recovered_attempts += 1
            else:
                calls.write({"type": "attempt_abandoned", "run_id": a.run_id, "paragraph_id": key[0],
                             "run_index": key[1], "attempt_no": att_no, "reason": "unknown_completion",
                             "at": utcnow()})
        st["started_open"] = {}

    # ---- retry policy v3: a pending feedback retry whose private response is missing cannot be rebuilt;
    # refuse BEFORE the gate and before any call (Codex W3 round 5, #3) ------------------------------ #
    if retry_policy_version() == "3":
        for (pid_, k_), st_ in sorted(state.items()):
            pend = st_.get("last_invalid")
            if pend is not None and pend[0] == MISSING_PRIVATE and st_.get("terminal") is None and resolve(st_["codes"]) is None:
                calls.write({"type": "run_finished", "run_id": a.run_id, "ended_at": utcnow(),
                             "outcome": "refused_missing_private_response"})
                calls.close(); raw.close()
                sys.exit(f"REFUSING to resume: the private response of the pending invalid attempt of {pid_} "
                         f"execution {k_} is missing, so the feedback retry of policy v3 cannot be reconstructed.")

    # ---- the client factory: SDK retries OFF, so every resend is a journaled attempt --- #
    if client_factory is None:
        if a.model in getattr(ins, "EXTERNAL_API", {}):
            # §4g: a Converse-shaped client for the Inception API; same journal, retries, validator and gate
            import inception_client as icx
            def client_factory():
                return icx.InceptionConverseClient(endpoint=ins.EXTERNAL_API[a.model])
        else:
            def client_factory():
                import boto3
                from botocore.config import Config
                return boto3.client("bedrock-runtime", region_name=region,
                                    config=Config(retries={"total_max_attempts": 1, "mode": "standard"}))

    # ---- the gate ---------------------------------------------------------------- #
    gate_path, gate = run_gate(a.model, a.run_id, client_factory=client_factory)
    certified = bool(gate.get("certified"))
    # Every gate this run ever ran is journalled, in order, before the first attempt it guards --
    # and before either refusal below, so an aborting gate is on record for the next resume
    # (Codex W1 round 8, #2). A later certified gate cannot qualify labels produced under an
    # earlier, uncertified one: each attempt records the gate it ran under, each terminal the gate
    # its resolving attempt ran under, and reportability requires EVERY gate to have certified.
    gate_sha = sha256_file(gate_path)
    gate_rec = {"type": "gate", "run_id": a.run_id, "artefact": rel(gate_path), "sha256": gate_sha,
                "certified": certified, "certification_status": gate.get("certification_status"),
                "gate_started_at": gate.get("started_at"), "at": utcnow()}
    calls.write(gate_rec)
    gates_seen.append(gate_rec)
    unreachable = any(c.get("id") == "1" and c.get("ok") is False for c in gate.get("checks", [])) \
        or gate.get("live_checks") in ("unreachable", "not_run")
    if unreachable:
        # Even a development run learns nothing from a model it cannot reach; 288 identical
        # errors are not a dry run (found on the first live attempt, 2026-09-05).
        calls.write({"type": "run_finished", "run_id": a.run_id, "ended_at": utcnow(), "outcome": "refused_unreachable"})
        calls.close(); raw.close()
        sys.exit(f"REFUSING to extract: the gate could not reach the model "
                 f"({gate.get('certification_status')}). See {gate_path}.")
    if not certified and not a.dev:
        calls.write({"type": "run_finished", "run_id": a.run_id, "ended_at": utcnow(), "outcome": "refused_uncertified"})
        calls.close(); raw.close()
        sys.exit(f"REFUSING to extract: the gate did not certify ({gate.get('certification_status')}). "
                 f"See {gate_path}. Pass --dev only for a development run, which is not reportable.")

    # ---- the client ----------------------------------------------------------------- #
    client = client_factory()

    # ---- execution loop: paragraph-major, then execution index ---------------------- #
    for p in paras:
        pid = p["paragraph_id"]
        if translated is not None:
            # §4e(a): the stored translation replaces the paragraph text; the request is otherwise the registered one.
            # A paragraph without a translation is sent nowhere (None).
            tr_ = translations[pid]
            text = tr_["translation"] if tr_["outcome"] == sc.T_TRANSLATED else None
        else:
            text = texts[pid]["paragraph_text"]
        envelope = {"modelId": ins.MODELS[a.model], **model_request(a.model, art, text)} if text is not None else None
        built = request_digest(envelope) if envelope is not None else None
        for k in range(1, a.repeats + 1):
            st = state.setdefault((pid, k), {"codes": [], "attempts": [], "payloads": {}, "terminal": None,
                                             "started_open": {}, "usage": [], "max_attempt": 0, "episode": 0,
                                             "last_invalid": None})
            if st["terminal"] is not None:
                # the terminal OUTCOME decides: `error` is reopened whatever the mixed history of
                # the episode (a truncation followed by three exceptions is an error terminal whose
                # parse allowance was never exhausted); parse_failed and refused never are
                reopen = (getattr(a, "recover_infrastructure", False) and st["terminal"].get("outcome") == T_ERROR
                          and st.get("episode", 0) < MAX_RECOVERY_EPISODES)
                if not reopen:
                    continue
                st["episode"] = st.get("episode", 0) + 1
                calls.write({"type": "recovery_started", "run_id": a.run_id, "paragraph_id": pid, "run_index": k,
                             "episode": st["episode"], "after_codes": st["codes"], "at": utcnow()})
                st["terminal"] = None; st["codes"], st["attempts"] = [], []; st["last_invalid"] = None
            if envelope is None:
                # §4e(a) amendment item 4: a failed translation is a failed execution with no call and no record
                term = {"type": "terminal", "run_id": a.run_id, "paragraph_id": pid, "run_index": k, "outcome": T_NO_INPUT,
                        "n_attempts": 0, "codes": [], "resolved_from_attempt": None, "episode": st.get("episode", 0),
                        "resolved_under_gate": None, "at": utcnow()}
                calls.write(term)
                st["terminal"] = term
                continue
            attempt_no = st["max_attempt"] + 1          # never re-used, even after an abandonment
            # replay first: a recovered state may already be terminal
            while True:
                outcome = resolve(st["codes"])
                if outcome is not None:
                    break
                # retry policy v3 (W3): the retry after a returned invalid call carries that call and the
                # validator's messages, also across transport failures and across a resume; v2 and every
                # other code resend the identical envelope
                pending = pending_feedback(st)
                if pending is not None and pending[0] == MISSING_PRIVATE:
                    raise RuntimeError("unreachable: a missing private response is refused before the gate")
                if pending is not None and replayable(pending[0]):
                    prior, problems = pending
                    send = {"modelId": ins.MODELS[a.model],
                            **ins.build_feedback_request(art, text, prior, problems, max_tokens=output_cap())}
                    retry_kind = "feedback"
                    raw.write({"type": "feedback_request", "run_id": a.run_id, "paragraph_id": pid, "run_index": k,
                               "attempt_no": attempt_no, "prior_call": prior, "problems": problems,
                               "feedback": send["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"],
                               "at": utcnow()})
                elif pending is not None:
                    send = envelope
                    retry_kind = "identical_unreplayable"
                    raw.write({"type": "feedback_skipped", "run_id": a.run_id, "paragraph_id": pid, "run_index": k,
                               "attempt_no": attempt_no, "prior_call": pending[0], "problems": pending[1],
                               "reason": "the prior call is not replayable verbatim (tool name or id)", "at": utcnow()})
                else:
                    send = envelope
                    retry_kind = "identical" if st["codes"] else "first"
                sent = request_digest(send)
                calls.write({"type": "attempt_started", "run_id": a.run_id, "paragraph_id": pid,
                             "run_index": k, "attempt_no": attempt_no, "request_sha256_built": built,
                             "request_sha256_sent": sent, "retry_kind": retry_kind,
                             "gate_sha256": gate_sha, "at": utcnow()})
                st.setdefault("gate_of", {})[attempt_no] = gate_sha
                t0 = time.monotonic()
                try:
                    response = client.converse(**send)
                    latency = int((time.monotonic() - t0) * 1000)
                    raw.write({"type": "raw", "run_id": a.run_id, "paragraph_id": pid, "run_index": k,
                               "attempt_no": attempt_no, "response": response, "at": utcnow()})
                    code, payload, rules, problems, use = classify(response, art, doc, subtypes)
                    st["last_invalid"] = (use, problems) if code in (SCHEMA_INVALID, CONTRACT_INVALID) else None
                    u = usage_of(response)
                    totals.calls += 1
                    for f_ in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                        setattr(totals, f_, getattr(totals, f_) + u[f_])
                    if payload is not None and payload.get("relations"):
                        raw.write({"type": "free_text", "run_id": a.run_id, "paragraph_id": pid,
                                   "run_index": k, "attempt_no": attempt_no,
                                   "notes": [{"technology": r.get("technology"), "note": r.get("note"),
                                              "comparison_note": r.get("comparison_note")}
                                             for r in payload["relations"]],
                                   # v1.1E only: the explanation is free text and stays private
                                   "explanation": ((use or {}).get("input") or {}).get("explanation")})
                    fin = {"type": "attempt_finished", "run_id": a.run_id, "paragraph_id": pid,
                           "run_index": k, "attempt_no": attempt_no, "code": code,
                           "contract_rules": rules, "stop_reason": response.get("stopReason"),
                           "usage": u, "latency_ms": latency,
                           "payload": public_payload(payload) if payload else None, "at": utcnow()}
                except Exception as exc:  # noqa: BLE001
                    latency = int((time.monotonic() - t0) * 1000)
                    name = type(exc).__name__
                    err_code = (getattr(exc, "response", {}) or {}).get("Error", {}).get("Code", name)
                    is_throttle = "Throttl" in name or "Throttl" in str(err_code)
                    code = THROTTLED if is_throttle else ERROR
                    totals.calls += 1
                    totals.throttles += is_throttle
                    totals.errors += (not is_throttle)
                    raw.write({"type": "exception", "run_id": a.run_id, "paragraph_id": pid,
                               "run_index": k, "attempt_no": attempt_no, "error_class": name,
                               "error_code": str(err_code), "error": str(exc)[:2000], "at": utcnow()})
                    fin = {"type": "attempt_finished", "run_id": a.run_id, "paragraph_id": pid,
                           "run_index": k, "attempt_no": attempt_no, "code": code,
                           "contract_rules": [], "stop_reason": None, "usage": None,
                           "latency_ms": latency, "payload": None, "error_class": name,
                           "error_code": str(err_code), "at": utcnow()}
                    if is_throttle:
                        n = sum(c == THROTTLED for c in st["codes"])
                        sleep(min(BACKOFF_BASE_S * (2 ** n), 60.0))
                calls.write(fin)
                st["codes"].append(code); st["attempts"].append(attempt_no)
                if fin.get("payload") is not None:
                    st["payloads"][attempt_no] = fin["payload"]
                st["max_attempt"] = attempt_no
                attempt_no += 1
            # terminal: the payload of the attempt that resolved it, if any
            resolving = next((n for n, c in zip(st["attempts"], st["codes"]) if c in (OK_PRESENT, OK_OFF)), None)
            term = {"type": "terminal", "run_id": a.run_id, "paragraph_id": pid, "run_index": k,
                    "outcome": outcome, "n_attempts": len(st["codes"]), "codes": st["codes"],
                    "resolved_from_attempt": resolving, "episode": st.get("episode", 0),
                    "resolved_under_gate": (st.get("gate_of") or {}).get(resolving) if resolving else None,
                    "at": utcnow()}
            calls.write(term)
            st["terminal"] = term

    torn_at_open = calls.torn_lines + raw.torn_lines
    # every invocation ends with a journalled completion; the run record's ended_at IS the last one
    ended_at = utcnow()
    calls.write({"type": "run_finished", "run_id": a.run_id, "ended_at": ended_at, "outcome": "completed"})
    calls.close(); raw.close()

    # ---- derived public files AND the accounting, rebuilt from the journal ----------- #
    recs, torn_now = Journal.read(calls_path)
    write_derived(a.run_id, recs, paras)
    totals = accounting(recs)
    # the first extraction call of the run, from the journal: the gate that guards a run must
    # precede it (the run's started_at is stamped BEFORE the gate, so it cannot serve that check)
    first_call_at = min((r["at"] for r in recs if r.get("type") == "attempt_started" and r.get("at")), default=None)
    # A torn tail is seen by the pre-open read AND cut by the open; count it once.
    totals.torn_records = torn1 + torn2 + torn_now
    del torn_at_open

    gates_journal = [r for r in recs if r.get("type") == "gate"]
    all_certified = bool(gates_journal) and all(bool(g.get("certified")) for g in gates_journal)
    billing_complete = billing_is_complete(totals)
    record = {
        "run_id": a.run_id, "started_at": started_at, "ended_at": ended_at, "first_call_at": first_call_at,
        "reportable": (not a.dev) and all_certified,
        "dev": a.dev, "arm": "main",
        # W3 (2026-10-14): "registered primary" | "development" | "exploratory reused A2" -- the evaluator
        # derives its stamp and captions from this and from the verified contract, never from a flag alone
        "analysis_status": analysis_status, "w3_authorisation": w3_auth,
        "a3_embargo": a3,                        # specification §4b: None = no A3 listing; else the embargo state
        **({"model_comparison": mc} if mc else {}),   # specification §4f: the author's decision, bound by the contract
        **({"a3_candidate": a3_cand} if a3_cand else {}),   # specification §4b: the candidate decision, bound by the contract
        **({"form_variant": form} if form else {}),          # specification §4j: the author's decision, bound by the contract
        **({"context_variant": context} if context else {}),   # specification §4l (5): the decision and the bound context
        **({"translated_input": translated} if translated else {}),   # specification §4e(a): the override, bound by the contract
        "manifest": {"path": rel(mpath), "sha256": msha,
                     "restrict_split": a.restrict_split, "n_paragraphs": len(paras),
                     **({"restrict_lang": sc.TRANSLATED_LANG} if translated else {})},
        "repeats": a.repeats, "execution_fingerprint": fp, "execution_contract": contract,
        "git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
        "git_at_start": git_at_start,             # the original run's tree before its first file (from run_started)
        "git_at_invocation": git_at_invocation,   # this invocation's tree before it touched anything (differs on resume)
        "python": platform.python_version(),
        "botocore": _botocore_version(),
        "codebook": {"version": art.codebook_version, "sha256": art.codebook_sha256},
        "prompt": {"path": getattr(ins, "PROMPT_PATH_REL", "tools/pipeline/instrument/system-prompt_v1.0.txt"),
                   "sha256": art.prompt_sha256,
                   "note": "referenced by path and hash; the rendered prompt is a tracked artefact"},
        "tool_config_sha256": art.tool_config_sha256,
        # W3 (2026-10-14): the bound instrument. "v1.0" is the registered instrument with retry policy v2.
        "instrument": {"name": INSTRUMENT_NAME, "version": str(getattr(ins, "INSTRUMENT_VERSION", "1.0")),
                       "variant": getattr(ins, "variant", None), "retry_policy_version": retry_policy_version(),
                       "pins": rel(Path(getattr(ins, "PINS", HERE / "pins.json"))),
                       "module_sha256": getattr(ins, "module_sha256", {"instrument.py": sha256_file(HERE / "instrument.py")})},
        "model_id": ins.MODELS[a.model], "model_key": a.model, "region": region,
        "provider": ("Inception Labs API (OpenAI-compatible chat completions, Converse adapter inception_client.py)"
                     if a.model in getattr(ins, "EXTERNAL_API", {}) else "Amazon Bedrock"),
        "inference_config": model_request(a.model, art, "x")["inferenceConfig"],
        "thinking": thinking_statement(a.model, art),
        "cache_ttl_requested": (None if a.model in getattr(ins, "NO_CACHE_POINT", ()) else ins.CACHE_TTL),
        "cache_writes_other_ttl": totals.cache_writes_other_ttl,
        "preflight": {"artefact": rel(gate_path), "sha256": gate_sha,
                      # every gate this run ran, from the journal (exact run identity, not a glob)
                      "gates": [{"artefact": g["artefact"], "sha256": g["sha256"], "certified": bool(g.get("certified")),
                                 "certification_status": g.get("certification_status"), "gate_started_at": g.get("gate_started_at")}
                                for g in gates_journal],
                      "all_certified": all_certified,
                      "certified": certified, "certification_status": gate.get("certification_status"),
                      "checks": [{"id": c.get("id"), "ok": c.get("ok"), "skipped": bool(c.get("skipped"))}
                                 for c in gate.get("checks", [])]},
        "totals": {**{k_: getattr(totals, k_) for k_ in ("calls", "input_tokens", "output_tokens",
                                                        "cache_read_tokens", "cache_write_tokens",
                                                        "throttles", "errors",
                                                        "unknown_completion_attempts",
                                                        "usage_incomplete_attempts",
                                                        "billing_uncertain_attempts",
                                                        "cache_writes_other_ttl", "torn_records",
                                                        "recovered_attempts")},
                   "verdict_codes": totals.codes,
                   "billable_tokens": totals.input_tokens + totals.output_tokens
                   + totals.cache_read_tokens + totals.cache_write_tokens,
                   "quota_tokens": totals.input_tokens + totals.cache_write_tokens},
        "cost": {"estimated_usd": cost_of(totals, a.model),
                 "basis": ("estimated USD from the observed token usage of every finished attempt at "
                           "the frozen provisional list rates below; not an invoice, not an observed charge"),
                 "rates_per_million_usd": PRICES_PER_M[a.model], "price_table": PRICE_TABLE_VERSION,
                 "billing_complete": billing_complete,
                 "note": (("the estimate covers finished attempts with complete usage only; "
                           f"{totals.unknown_completion_attempts} unknown-completion, "
                           f"{totals.usage_incomplete_attempts} usage-incomplete and "
                           f"{totals.billing_uncertain_attempts} billing-uncertain attempt(s) may have "
                           f"been billed and are not in it; {totals.cache_writes_other_ttl} write(s) were "
                           "stored for a TTL other than the one costed") if not billing_complete else
                          "every attempt finished with complete usage; the token-derived estimate is complete")},
        "files": {name: {"path": rel((RUNS / name)), "sha256": sha256_file(RUNS / name)}
                  for name in (f"{a.run_id}_calls.jsonl", f"{a.run_id}_terminal.csv",
                               f"{a.run_id}_predictions.csv")},
        "raw_private": rel(raw_path),
    }
    run_record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


# Positively known to be rejected before serving (never billed). Everything else -- a timeout,
# a broken connection, a parser error on a response that DID arrive, an unknown exception --
# may have been served and leaves the billing uncertain (Codex W1 round 3, #6: the default is
# uncertain, not complete).
# (ServiceUnavailableException is NOT listed: a 503 is an availability failure whose billing the
# consulted documentation does not settle, so it stays uncertain -- Codex W1 round 4, #7.)
KNOWN_UNBILLED_MARKERS = ("Throttl", "ValidationException", "AccessDeniedException",
                         "ResourceNotFoundException", "ServiceQuotaExceededException",
                         "ModelNotReadyException")


def is_uncertain_error(rec: dict) -> bool:
    """Whether a failed attempt may nevertheless have been served (and billed). Only an error
    class or code that names a pre-serving rejection is certainly unbilled."""
    cls = f"{rec.get('error_class') or ''} {rec.get('error_code') or ''}"
    return not any(m in cls for m in KNOWN_UNBILLED_MARKERS)


def billing_is_complete(t: "Totals") -> bool:
    """ONE definition, for the runner and every verifier: no unknown-completion, usage-incomplete
    or billing-uncertain attempt, and no write stored for a TTL other than the one costed."""
    return (t.unknown_completion_attempts == 0 and t.usage_incomplete_attempts == 0
            and t.billing_uncertain_attempts == 0 and t.cache_writes_other_ttl == 0)


def verify_journal_binding(rec: dict, term_rows: list[dict], calls_path: Path, pred_rows: list[dict] | None = None,
                           rates: dict | None = None, paras: list[dict] | None = None) -> dict:
    """Reconcile the run record's gate summary and the standing terminals with the hash-verified
    calls journal (Codex W1 round 8, #1): the journal's `gate` records must be exactly
    `preflight.gates` (order, artefact, digest, certification); every standing terminal in
    terminal.csv must be the journal's last terminal for its (paragraph, execution) -- same outcome,
    resolving attempt, episode and gate -- and that resolving attempt's `attempt_started` record must
    name the same gate. Called by the evaluator (reportable) and by the W1 selector; raises ValueError.
    The journal must be intact (no torn line)."""
    recs, torn = Journal.read_verbatim(Path(calls_path))       # never repairs: the digest was just verified
    if torn:
        raise ValueError(f"the calls journal has {torn} torn or unterminated line(s); a torn journal is not evidence")
    # the execution contract the run STARTED under is the one the record claims (Codex W1 round 9,
    # #1): exactly one run_started, whose fingerprint is the record's and is the digest of the
    # record's own contract document
    starts = [r for r in recs if r.get("type") == "run_started"]
    if len(starts) != 1:
        raise ValueError(f"the calls journal has {len(starts)} run_started records, expected exactly one")
    contract = rec.get("execution_contract")
    if not isinstance(contract, dict):
        raise ValueError("the run record carries no execution_contract document")
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()
    if not (starts[0].get("fingerprint") == rec.get("execution_fingerprint") == digest):
        raise ValueError("the run's journalled fingerprint, the record's execution_fingerprint and the digest of the "
                         "record's execution_contract are not one and the same; the contract was edited")
    # timestamps are the journal's, not the record's (Codex W1 round 10, #3): started_at is the
    # run_started's, first_call_at the first attempt's, ended_at the LAST run_finished's, and no
    # journalled event lies after it
    finished_recs = [r for r in recs if r.get("type") == "run_finished"]
    if not finished_recs:
        raise ValueError("the calls journal records no completed invocation (no run_finished)")
    if rec.get("started_at") != starts[0].get("started_at"):
        raise ValueError("the record's started_at is not the journal's run_started")
    first_at = min((r["at"] for r in recs if r.get("type") == "attempt_started" and r.get("at")), default=None)
    if (rec.get("first_call_at") or None) != first_at:
        raise ValueError("the record's first_call_at is not the journal's first attempt")
    if rec.get("ended_at") != finished_recs[-1].get("ended_at"):
        raise ValueError("the record's ended_at is not the journal's last run_finished")
    late = [r for r in recs if r.get("at") and str(r["at"]) > str(rec.get("ended_at"))]
    if late:
        raise ValueError(f"{len(late)} journalled event(s) lie after the record's ended_at")
    j_gates = [{"artefact": r.get("artefact"), "sha256": r.get("sha256"), "certified": bool(r.get("certified"))}
               for r in recs if r.get("type") == "gate"]
    s_gates = [{"artefact": g.get("artefact"), "sha256": g.get("sha256"), "certified": bool(g.get("certified"))}
               for g in ((rec.get("preflight") or {}).get("gates") or [])]
    if j_gates != s_gates:
        raise ValueError("the run record's gate summary is not the journal's gate sequence")
    started = {(r["paragraph_id"], int(r["run_index"]), int(r["attempt_no"])): r
               for r in recs if r.get("type") == "attempt_started"}
    last_term: dict[tuple[str, int], dict] = {}
    for r in recs:
        if r.get("type") == "terminal":
            last_term[(r["paragraph_id"], int(r["run_index"]))] = r
    if len(term_rows) != len(last_term):
        raise ValueError(f"terminal.csv has {len(term_rows)} rows, the journal {len(last_term)} standing terminals")
    for row in term_rows:
        key = (row["paragraph_id"], int(row["run_index"]))
        jt = last_term.get(key)
        if jt is None:
            raise ValueError(f"terminal.csv names {key}, which the journal never resolved")
        rfa = row.get("resolved_from_attempt") or ""
        if (jt.get("outcome") != row.get("outcome") or str(jt.get("resolved_from_attempt") or "") != str(rfa)
                or str(jt.get("episode", 0)) != str(row.get("episode", 0) or 0)
                or str(jt.get("resolved_under_gate") or "") != str(row.get("gate") or "")):
            raise ValueError(f"terminal.csv row {key} differs from the journal's standing terminal")
        if rfa:
            att = started.get((key[0], key[1], int(rfa)))
            if att is None or str(att.get("gate_sha256") or "") != str(row.get("gate") or ""):
                raise ValueError(f"the resolving attempt of {key} was not made under the gate terminal.csv names")
    # the prediction rows must be exactly the journal's payload-derived rows (round 10, #1)
    if pred_rows is not None:
        _, derived = derive_rows(rec.get("run_id"), recs, paras or [])
        norm = lambda rows: sorted(tuple(str(r.get(c, "")) for c in PRED_COLUMNS if c != "run_id") for r in rows)
        if norm(pred_rows) != norm(derived):
            raise ValueError("predictions.csv is not the journal's payload-derived prediction set")
    # the accounting is the journal's (round 10, #2): totals, billing completeness and the estimate
    # at the given rates must be what the record claims
    out = {}
    if rates is not None:
        tot = accounting(recs)
        cost = rec.get("cost") or {}
        if bool(cost.get("billing_complete")) != billing_is_complete(tot):
            raise ValueError("the record's billing_complete is not what the journal's accounting gives")
        est = round((tot.input_tokens * rates["input"] + tot.output_tokens * rates["output"]
                     + tot.cache_read_tokens * rates["cache_read"] + tot.cache_write_tokens * rates["cache_write_1h"]) / 1_000_000, 6)
        # exact at the fixed six-decimal precision, finite and non-negative -- no tolerance a shaved
        # value could hide in (Codex W1 round 11); consumers select on the RECOMPUTED value below
        rec_cost = cost.get("estimated_usd")
        if (not isinstance(rec_cost, (int, float)) or isinstance(rec_cost, bool) or rec_cost != rec_cost
                or abs(rec_cost) == float("inf") or rec_cost < 0 or round(float(rec_cost), 6) != est):
            raise ValueError(f"the record's estimated_usd {rec_cost!r} is not the journal's usage at the given rates ({est}) "
                             f"at six decimals, or is not a finite non-negative number")
        out = {"estimated_usd": est, "billing_complete": billing_is_complete(tot), "totals": tot}
        rt = rec.get("totals") or {}
        for f_ in ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                   "billing_uncertain_attempts", "usage_incomplete_attempts", "unknown_completion_attempts", "cache_writes_other_ttl"):
            if int(rt.get(f_, -1)) != int(getattr(tot, f_)):
                raise ValueError(f"the record's totals.{f_} is not the journal's")
    return out


def accounting(recs: list[dict]) -> Totals:
    """Cumulative totals from the durable journal -- across resumes, never from memory."""
    t = Totals()
    started = {(r["paragraph_id"], r["run_index"], r["attempt_no"]) for r in recs if r.get("type") == "attempt_started"}
    finished = {(r["paragraph_id"], r["run_index"], r["attempt_no"]) for r in recs if r.get("type") == "attempt_finished"}
    for r in recs:
        if r.get("type") != "attempt_finished":
            continue
        t.calls += 1
        t.codes[r["code"]] = t.codes.get(r["code"], 0) + 1
        t.throttles += r["code"] == THROTTLED
        t.errors += r["code"] == ERROR
        t.recovered_attempts += bool(r.get("recovered_from_raw"))
        u = r.get("usage") or {}
        for f_ in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            setattr(t, f_, getattr(t, f_) + int(u.get(f_, 0) or 0))
        if r["code"] not in (THROTTLED, ERROR):
            t.usage_incomplete_attempts += u.get("usage_complete") is not True
        t.cache_writes_other_ttl += any(x != ins.CACHE_TTL for x in (u.get("cache_write_ttl") or []))
        # An exception attempt has no usage. A throttle was rejected before serving and is
        # certainly unbilled; a timeout or a broken connection may have been served and
        # billed, so it leaves the billing uncertain (Codex W1 round 2, #3).
        # whatever shape the journal gives an exception attempt's usage (None, {}, partial), it is
        # no evidence of billing; only the error class can be
        if r["code"] == ERROR and is_uncertain_error(r):
            t.billing_uncertain_attempts += 1
    t.unknown_completion_attempts = sum(1 for r in recs if r.get("type") == "attempt_abandoned") \
        + len(started - finished - {(r["paragraph_id"], r["run_index"], r["attempt_no"])
                                    for r in recs if r.get("type") == "attempt_abandoned"})
    return t


def _botocore_version() -> str:
    try:
        import botocore
        return botocore.__version__
    except Exception:  # noqa: BLE001
        return "absent"


PRED_COLUMNS = ["run_id", "run_index", "paragraph_id", "technology", "wind_siting", "hydrogen_pathway",
                "F1a", "F1b", "F2a", "F2b", "F2c", "F3a", "F3b", "F4a", "F4b", "F5a", "F5b",
                "F6a", "F6b", "F7a", "F7b", "F8a", "F9", "no_frame_for_this_tech",
                "eval_costs", "eval_jobs", "eval_climate", "eval_security", "eval_reliability",
                "eval_feasibility", "eval_siting", "eval_regulation", "eval_other", "comparison_target"]
ELEMENTS = [c for c in PRED_COLUMNS if c[0] == "F" and c[1].isdigit()]


TERMINAL_COLUMNS = ["run_id", "run_index", "paragraph_id", "lang", "outcome", "n_attempts",
                    "codes", "resolved_from_attempt", "episode", "gate"]


def derive_rows(run_id: str, recs: list[dict], paras: list[dict]) -> tuple[list[dict], list[dict]]:
    """ONE derivation of the public rows from the journal, used by the runner to write
    terminal.csv / predictions.csv and by every verifier to check that the files ARE the
    journal's rows (Codex W1 round 10, #1). A recovery episode appends a new terminal for the
    same (paragraph, execution); the last one stands. Allowlisted fields only."""
    lang = {p["paragraph_id"]: p.get("lang", "") for p in paras}
    finished = {(r["paragraph_id"], r["run_index"], r["attempt_no"]): r
                for r in recs if r.get("type") == "attempt_finished"}
    last: dict = {}
    for r in recs:
        if r.get("type") == "terminal":
            last[(r["paragraph_id"], r["run_index"])] = r
    terms = sorted(last.values(), key=lambda r: (r["paragraph_id"], r["run_index"]))
    term_rows = [{"run_id": run_id, "run_index": t["run_index"], "paragraph_id": t["paragraph_id"],
                  "lang": lang.get(t["paragraph_id"], ""), "outcome": t["outcome"], "n_attempts": t["n_attempts"],
                  "codes": "|".join(t["codes"]), "resolved_from_attempt": t.get("resolved_from_attempt") or "",
                  "episode": t.get("episode", 0), "gate": t.get("resolved_under_gate") or ""} for t in terms]
    pred_rows = []
    for t in terms:
        if t["outcome"] != T_PRESENT or not t.get("resolved_from_attempt"):
            continue
        fin = finished.get((t["paragraph_id"], t["run_index"], t["resolved_from_attempt"]))
        payload = (fin or {}).get("payload") or {}
        for rel in payload.get("relations") or []:
            row = {c: "" for c in PRED_COLUMNS}
            row.update({"run_id": run_id, "run_index": t["run_index"],
                        "paragraph_id": t["paragraph_id"], "technology": rel.get("technology"),
                        "wind_siting": rel.get("wind_siting", ""),
                        "hydrogen_pathway": rel.get("hydrogen_pathway", ""),
                        "comparison_target": rel.get("comparison_target", "")})
            fired = set(rel.get("elements") or [])
            for el in ELEMENTS:
                row[el] = 1 if el in fired else 0
            row["no_frame_for_this_tech"] = 0 if fired else 1
            for frame, value in (rel.get("evaluations") or {}).items():
                if f"eval_{frame}" in row:
                    row[f"eval_{frame}"] = value
            pred_rows.append(row)
    return term_rows, pred_rows


def write_derived(run_id: str, recs: list[dict], paras: list[dict]) -> None:
    """terminal.csv and predictions.csv from the journal, through derive_rows()."""
    term_rows, pred_rows = derive_rows(run_id, recs, paras)
    with (RUNS / f"{run_id}_terminal.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TERMINAL_COLUMNS, delimiter=";")
        w.writeheader(); w.writerows(term_rows)
    with (RUNS / f"{run_id}_predictions.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PRED_COLUMNS, delimiter=";")
        w.writeheader(); w.writerows(pred_rows)


def main(client_factory=None, sleep=time.sleep) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="sample manifest (A2) or session manifest (dev)")
    ap.add_argument("--restrict-split", default=None,
                    help="keep only manifest paragraphs with this split (dev runs on A1)")
    ap.add_argument("--model", choices=list(ins.MODELS), default="sonnet")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--recover-infrastructure", action="store_true",
                    help="with --resume: reopen terminal `error` outcomes (throttling/exceptions that "
                         "exhausted the retry policy) in a journalled recovery episode, at most "
                         f"{MAX_RECOVERY_EPISODES} per (paragraph, execution); never a parse or refusal terminal")
    ap.add_argument("--dev", action="store_true",
                    help="development run: an uncertified gate is tolerated and the run is "
                         "stamped reportable: false")
    ap.add_argument("--instrument", choices=INSTRUMENTS, default="v1.0",
                    help="W3 (2026-10-14): v1.0 = the registered instrument (default, retry policy v2); "
                         "v1.1S / v1.1E = the post-registration variants of instrument_v11.py (retry policy v3); "
                         "v1.0r3 = the registered request under retry policy v3, the §4b candidate for the A3 census only")
    ap.add_argument("--w3-author-decision", default=None,
                    help="exploratory A2 run of a revised instrument only: the author's recorded decision (who, when, words)")
    ap.add_argument("--a3-protocol", default=None,
                    help="exploratory A2 run of a revised instrument only: path of the written A3 confirmatory protocol")
    ap.add_argument("--a3-candidate-decision", default=None,
                    help="§4b candidate v1.0r3 on the A3 census only (the registered request under retry policy v3, k = 3, "
                         "after the v1.0 A3 figure): the author's recorded decision (who, when, words)")
    ap.add_argument("--form-variant-decision", default=None,
                    help="§4j only: v1.0F (A1 dev k = 1, A2 k = 1, A3 k = 3) or its comparator v1.0r3 (A1 dev, A2): the "
                         "author's recorded decision (who, when, words)")
    ap.add_argument("--context-variant-decision", default=None,
                    help="§4l (5) only: v1.0C, the registered request with the headline, outlet and language before the "
                         "paragraph, k = 1 on the registered A2 sample or the A3 census: the author's recorded decision")
    ap.add_argument("--translated-input", default=None, metavar="FILE",
                    help="§4e(a) language arm only: the private translation file written by language_arm.py translate; runs "
                         "the registered v1.0 under the registered model, k = 3, on the stored English translations of the "
                         "German A2 paragraphs, binding the file's digest and every paragraph's source and translation "
                         "hashes into the execution contract (status 'exploratory reused A2 — translated inputs')")
    ap.add_argument("--model-comparison-decision", default=None,
                    help="§4f robustness model comparison only (a reportable run under a non-registered model, v1.0, "
                         "k = 1, on the A2 sample or the A3 census): the author's recorded decision (who, when, words)")
    a = ap.parse_args()
    rec = run(a, client_factory=client_factory, sleep=sleep)
    t = rec["totals"]
    print(f"run {rec['run_id']}: {rec['manifest']['n_paragraphs']} paragraphs x {rec['repeats']} "
          f"executions, {t['calls']} calls, codes {t['verdict_codes']}, "
          f"est. cost ${rec['cost']['estimated_usd']} ({'complete' if rec['cost']['billing_complete'] else 'INCOMPLETE'}), "
          f"reportable={rec['reportable']}")
    print(f"  run record  tools/runs/{rec['run_id']}.json")


if __name__ == "__main__":
    main()
