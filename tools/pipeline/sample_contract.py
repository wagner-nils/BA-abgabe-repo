"""The A2 sample contract -- ONE validator for a reportable run and a reportable evaluation.

Codex W1 round 2, finding #1: matching freeze references did not establish sample membership.
A relabelled development manifest carrying the real frame digest and freeze-event hash was
accepted as reportable with zero paragraphs belonging to the frozen frame, and NaN weights
passed as positive. This module is what both the runner (before the first extraction call)
and the evaluator (before scoring) call for reportable work; development work never reaches it.

It reads the frame manifest and the registry as JSON. It never imports `split_registry`: the
annotation tooling runs on another interpreter, and the pipeline must not depend on it.

What is verified, in order, each with its own refusal:
  1. the manifest is an `A2_sample_manifest`;
  2. the frame manifest on disk recomputes to its own digest and the sample names that digest;
  3. the registry head is the terminal `a2_frozen` event, its `audit_ref` is the frame digest,
     its hash is the freeze event the sample names, its predecessor is the frame's
     `pre_freeze_head`, and its `eligible_checksum` is the frame's;
  4. every sample paragraph is a member of the frame with the same identity (paragraph_id,
     digest, text_sha256) and metadata (article_id, lang, outlet), and no paragraph is listed twice;
  5. the design: for every stratum (lang, outlet) the recorded N_h is the frame's stratum size,
     n_h is the number of sample paragraphs in that stratum, pi = n_h/N_h, the weight is finite,
     positive and equal to 1/pi, n_h does not exceed the cap used, and the manifest's `n` and
     `by_language` counts are the paragraphs'.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRAME_MANIFEST = ROOT / "tools/annotation/labels/A2_frame_manifest.json"
REGISTRY_FILE = ROOT / "tools/annotation/split-assignment.json"
# The registered draw (Codex W1 round 4, #1): reportable work accepts only the sample manifest the
# draw wrote to this path, digest for digest, and only if its design states the registered target
# and cap. Reproducing the seeded draw from the frozen frame is `draw_a2.py`'s job (annotation
# tooling, other interpreter) and is asserted by its tests; it is not re-implemented here.
SAMPLE_MANIFEST = ROOT / "tools/annotation/labels/A2_sample_manifest.json"
REGISTERED_TARGET = {"de": 60, "en": 60}
REGISTERED_CAP = 24
EVENT_FREEZE = "a2_frozen"
# The blind minimum-support threshold s (specification §6 item 1, L27; Codex Loom round 1, #9): the
# author seals the value before the first A2 paragraph is coded; the reportable evaluator refuses
# any threshold that is not the sealed one. Written once, never rewritten.
THRESHOLD_SEAL = ROOT / "tools/annotation/labels/A2_threshold_seal.json"
SEAL_KIND = "A2_threshold_seal"
# A reportable evaluation accepts the seal only as committed: the file's first commit is the external
# commitment, its content must still equal that commit's, and the commit must not postdate the first
# coded reference row. Tests that build seals outside a repository switch this off explicitly.
REQUIRE_SEAL_COMMIT = True
SEAL_RULE = ("a per-class cell prints its value only if the reference support in that population is >= s units; "
             "otherwise n<s with the support; a statistic whose own denominator is empty prints undefined; "
             "suppression changes no aggregate and no macro membership")
SEAL_APPLIES_TO = ["table2_per_class", "table5_displacement", "table9_reliability"]
SUPPORT_UNITS = {
    "agreement_tables": "unweighted reference rows in the population: (paragraph x technology) units for "
                        "technologies and elements; frame-bearing reference units for evaluation values "
                        "(score B); matched units for score A",
    "displacement_table": "distinct paragraphs of the population on which the reference carries the class",
    "reliability_table": "aligned units on which the first coding is positive (technology ticks, element "
                         "flags); units inside the applicability mask for evaluation and comparison target",
}


class ContractError(RuntimeError):
    pass


def canonical_digest(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _finite_positive(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def verify_sample(man: dict, *, frame_path: Path | None = None, registry_path: Path | None = None) -> dict:
    """Raise ContractError on the first violation; return the frame manifest on success."""
    frame_path = Path(frame_path or FRAME_MANIFEST)
    registry_path = Path(registry_path or REGISTRY_FILE)

    # 1 -- kind, and identity with the registered sample manifest
    if man.get("kind") != "A2_sample_manifest":
        raise ContractError(f"a reportable run needs an A2 sample manifest (kind={man.get('kind')!r}); "
                            f"pass --dev for a development run")
    if not Path(SAMPLE_MANIFEST).exists():
        raise ContractError(f"no registered sample manifest at {SAMPLE_MANIFEST}; the draw has not been made")
    registered = json.loads(Path(SAMPLE_MANIFEST).read_text(encoding="utf-8"))
    if canonical_digest(man) != canonical_digest(registered) or man.get("digest") != registered.get("digest"):
        raise ContractError("the manifest presented is not the registered sample manifest "
                            f"({Path(SAMPLE_MANIFEST).name}): digests differ")
    # 2 -- frame
    if not frame_path.exists():
        raise ContractError(f"no frame manifest at {frame_path}; a reportable run needs the frozen frame")
    frame = json.loads(frame_path.read_text(encoding="utf-8"))
    if canonical_digest(frame) != frame.get("digest"):
        raise ContractError("the frame manifest's content does not match its digest; it was edited")
    if man.get("frame_manifest_digest") != frame["digest"]:
        raise ContractError("the sample manifest was not drawn from the frame manifest on disk (frame digest differs)")
    # 3 -- registry handshake
    if not registry_path.exists():
        raise ContractError(f"no registry at {registry_path}")
    log = json.loads(registry_path.read_text(encoding="utf-8")).get("assignment_log") or []
    head = log[-1] if log else {}
    if head.get("kind") != EVENT_FREEZE:
        raise ContractError(f"the registry head is {head.get('kind')!r}, not the terminal freeze; the frame is stale")
    if head.get("audit_ref") != frame["digest"]:
        raise ContractError("the freeze event's audit_ref is not the frame manifest's digest")
    if head.get("h") != man.get("freeze_event_hash"):
        raise ContractError("the sample manifest names a different freeze event than the registry head")
    prev = log[-2].get("h") if len(log) > 1 else None
    if prev != frame.get("pre_freeze_head"):
        raise ContractError("the freeze event's predecessor is not the frame manifest's pre_freeze_head")
    if head.get("eligible_checksum") != frame.get("eligible_checksum"):
        raise ContractError("the freeze event's eligible_checksum is not the frame manifest's")
    # 4 -- membership and identity
    paras = man.get("paragraphs") or []
    if not paras:
        raise ContractError("the sample manifest lists no paragraphs")
    ids = [q.get("paragraph_id") for q in paras]
    dup = [k for k, c in Counter(ids).items() if c > 1]
    if dup:
        raise ContractError(f"{len(dup)} paragraph(s) listed more than once: {dup[:4]}")
    members = {p["paragraph_id"]: p for p in frame.get("paragraphs") or []}
    bad = []
    for q in paras:
        m = members.get(q.get("paragraph_id"))
        # exposure is analysis-relevant (the clean-domain sensitivity reads it), so it is frozen too
        if m is None or any(q.get(f) != m.get(f) for f in ("digest", "text_sha256", "article_id", "lang", "outlet", "exposure")):
            bad.append(q.get("paragraph_id"))
    if bad:
        raise ContractError(f"{len(bad)} sample paragraph(s) are not members of the frozen frame, or differ "
                            f"from it in identity or frozen metadata (digest, text, article, lang, outlet, exposure): {bad[:4]}")
    # 5 -- design: stratum sizes, allocation, probabilities, weights, counts
    # the registered design must be stated, not inferred (Codex W1 round 3, #5)
    design = man.get("design")
    if not isinstance(design, dict):
        raise ContractError("the sample manifest carries no `design` block (target, caps, allocation)")
    target = design.get("target"); caps = design.get("cap_used"); cap_reg = design.get("cap_registered")
    if not isinstance(target, dict) or not target or not isinstance(caps, dict) or not isinstance(cap_reg, int):
        raise ContractError("the design block must state `target` per language, `cap_used` per language and `cap_registered`")
    if dict(target) != dict(REGISTERED_TARGET):
        raise ContractError(f"the design target {target} is not the registered target {REGISTERED_TARGET}")
    if cap_reg != REGISTERED_CAP:
        raise ContractError(f"cap_registered {cap_reg} is not the registered cap {REGISTERED_CAP}")
    if set(caps) != set(target):
        raise ContractError(f"cap_used must name every language of the target: {sorted(caps)} vs {sorted(target)}")
    for L, cap in caps.items():
        if cap != cap_reg and not design.get("cap_amendment"):
            raise ContractError(f"cap used for {L} is {cap} but the registered cap is {cap_reg} and no dated cap_amendment is recorded")
    if man.get("n") is None or man.get("by_language") is None:
        raise ContractError("the sample manifest must state `n` and `by_language`")
    if dict(target) != dict(man["by_language"]):
        raise ContractError(f"by_language {man['by_language']} is not the registered target {target}")
    sizes = Counter((p["lang"], p["outlet"]) for p in frame.get("paragraphs") or [])
    drawn = Counter((q["lang"], q["outlet"]) for q in paras)
    problems = []
    for q in paras:
        key = (q["lang"], q["outlet"])
        N_h, n_h = sizes[key], drawn[key]
        if q.get("N_h") != N_h:
            problems.append(f"{q['paragraph_id']}: N_h {q.get('N_h')} != frame stratum size {N_h}")
        if q.get("n_h") != n_h:
            problems.append(f"{q['paragraph_id']}: n_h {q.get('n_h')} != drawn in stratum {n_h}")
        if q.get("stratum") not in (None, f"{q['lang']}|{q['outlet']}"):
            problems.append(f"{q['paragraph_id']}: stratum label {q.get('stratum')!r} is not lang|outlet")
        pi_want = Fraction(n_h, N_h) if N_h else None
        pi = q.get("pi")
        if pi_want is None or not _finite_positive(pi) or abs(pi - float(pi_want)) > 1e-12:
            problems.append(f"{q['paragraph_id']}: pi {pi!r} != n_h/N_h {pi_want}")
        w = q.get("weight")
        if pi_want is None or not _finite_positive(w) or abs(w - float(1 / pi_want)) > 1e-9:
            problems.append(f"{q['paragraph_id']}: design weight {w!r} is not a finite positive 1/pi")
        cap = caps.get(q["lang"])
        if isinstance(cap, int) and n_h > cap:
            problems.append(f"{key}: n_h {n_h} exceeds the cap used {cap}")
        if problems and len(problems) >= 6:
            break
    if problems:
        raise ContractError("the registered design does not hold for this sample: " + "; ".join(problems[:6]))
    if man["n"] != len(paras):
        raise ContractError(f"the manifest says n={man['n']} but lists {len(paras)} paragraphs")
    by_lang = dict(Counter(q["lang"] for q in paras))
    if dict(man["by_language"]) != by_lang:
        raise ContractError(f"by_language {man.get('by_language')} does not match the paragraphs {by_lang}")
    return frame


# --------------------------------------------------------------------------- #
# A3 -- the fresh-sample census (pre-analysis specification §4b, 2026-10-14)
# --------------------------------------------------------------------------- #
A3_SAMPLE_MANIFEST = ROOT / "tools/annotation/labels/A3_sample_manifest.json"
A3_KIND = "A3_sample_manifest"
A3_STATUS = "fresh-sample evaluation"
A3_THRESHOLD = 5                  # declared in §4b before any A3 label existed; the A2 seal covers the A2 sample only
A3_SEED_ORDER = 20261014
A3_REPEATS = 3
A3_AUTHORITY = "pre-analysis specification §4b (2026-10-14)"
# the one frozen candidate configuration of §4b: the registered request under retry policy v3 (feedback retry);
# run on the census only, after the v1.0 A3 figure, on the author's recorded decision (2026-10-26)
A3_CANDIDATE = "v1.0r3"
A3_CANDIDATE_AUTHORITY = "pre-analysis specification §4b (2026-10-14): the one frozen candidate configuration v1.0r3"
A3_BASELINE_RUN = "a3_sonnet_20261019"


FRAME_COMPLETION_MANIFEST = ROOT / "tools/annotation/labels/A2_frame_completion_manifest.json"
FRAME_COMPLETION_KIND = "frame_completion_manifest"
FRAME_COMPLETION_STATUS = "exploratory frame completion"
FRAME_COMPLETION_REPEATS = 1
FRAME_COMPLETION_AUTHORITY = "pre-analysis specification §4d (2026-10-19)"

# Robustness model comparison (pre-analysis specification §4f, declared 2026-10-19 on the author's decision; W2 of §4
# reopened as declared there and extended to further models): the registered instrument v1.0 under another model, one
# execution per paragraph, on the registered A2 sample and on the A3 census; descriptive, no selection, never in the
# place of the registered result.
REGISTERED_MODEL_KEY = "sonnet"
MODEL_COMPARISON_STATUS = "robustness model comparison"
MODEL_COMPARISON_REPEATS = 1
MODEL_COMPARISON_AUTHORITY = "pre-analysis specification §4f (2026-10-19)"
# the declared roster (§4f, amended the same day before any non-Anthropic run); no other model runs as the comparison
MODEL_COMPARISON_ROSTER_4F = ("opus", "sonnet45", "haiku45", "novapro", "qwen3_235b", "nemotron3s")
# §4g (declared 2026-10-30 on the exploration branch, before any Mercury run): the diffusion model Mercury 3 in two
# configurations, under the same status and rules, with its own authority
DIFFUSION_AUTHORITY = "pre-analysis specification §4g (2026-10-30)"
# §4j (2026-10-30): the mandatory-form variant v1.0F and its comparator v1.0r3, declared after every other result on the
# author's decision -- A1 development split (k = 1), the registered A2 sample (k = 1), the A3 census for v1.0F (k = 3)
FORM_VARIANT = "v1.0F"
FORM_STATUS = "post-draft instrument variant"
FORM_AUTHORITY = "pre-analysis specification §4j (2026-10-30)"
FORM_INSTRUMENTS = ("v1.0F", "v1.0r3")
FORM_REPEATS = {"A1": 1, "A2": 1, "A3": 3}
FORM_A1_PARAGRAPHS = 32           # the A1 development split of tools/annotation/labels/A1_manifest.json (Codex §4j round 2)
DIFFUSION_ROSTER = ("mercury3_instant", "mercury3_low")
# §4l (5) (declared 2026-11-06, before any call of it): the context-parity variant v1.0C -- the registered request with
# the coding interface's headline, outlet and language before the paragraph -- on the registered A2 sample and the A3
# census, k = 1 each, the registered model, retry policy v2; compared with the existing v1.0 runs, descriptive
CONTEXT_VARIANT = "v1.0C"
CONTEXT_STATUS = "post-review context variant"
CONTEXT_AUTHORITY = "pre-analysis specification §4l (2026-11-06)"
CONTEXT_REPEATS = 1
CONTEXT_COMPARATORS = {"A2": "a2_sonnet_20261013", "A3": "a3_sonnet_20261019"}
# §4k (declared 2026-11-01, before any run of them on study text): Sonnet 5 and Opus 5 under the 5-series request, and
# the two 4.6 models under the same request as configuration controls; same status and rules, own authority
GEN5_AUTHORITY = "pre-analysis specification §4k (2026-11-01)"
GEN5_ROSTER = ("sonnet5", "opus5", "sonnet_req5", "opus_req5")
# §4m (declared 2026-11-08, before any run of it on study text): Opus 5.5 under the 5.5 request at effort low (k = 3) and
# high (k = 1), Opus 5 under the same request as the configuration control (k = 1), and Opus 5 under the §4k request
# again (k = 3, the repeat); same status and rules, own authority
GEN55_AUTHORITY = "pre-analysis specification §4m (2026-11-08)"
GEN55_ROSTER = ("opus55", "opus55_high", "opus5_req55", "opus5_rep")
GEN55_REPEATS = {"opus55": 3, "opus55_high": 1, "opus5_req55": 1, "opus5_rep": 3}
MODEL_COMPARISON_ROSTER = MODEL_COMPARISON_ROSTER_4F + DIFFUSION_ROSTER + GEN5_ROSTER + GEN55_ROSTER
MODEL_COMPARISON_AUTHORITY_OF = {**{m: MODEL_COMPARISON_AUTHORITY for m in MODEL_COMPARISON_ROSTER_4F},
                                 **{m: DIFFUSION_AUTHORITY for m in DIFFUSION_ROSTER},
                                 **{m: GEN5_AUTHORITY for m in GEN5_ROSTER},
                                 **{m: GEN55_AUTHORITY for m in GEN55_ROSTER}}


def model_comparison_repeats(model_key: str) -> int:
    """The declared executions per paragraph of one comparison configuration: one (§4f, §4g, §4k), or the §4m table."""
    return GEN55_REPEATS.get(model_key, MODEL_COMPARISON_REPEATS)


def _frame_and_registry(man: dict, frame_path: Path, registry_path: Path, what: str) -> tuple[dict, dict]:
    """The frozen frame and the registry handshake of the registered draw, shared by the A3 and the
    frame-completion contracts. Returns (frame, registry head)."""
    if not frame_path.exists():
        raise ContractError(f"no frame manifest at {frame_path}; {what} is a subset of the frozen frame")
    frame = json.loads(frame_path.read_text(encoding="utf-8"))
    if canonical_digest(frame) != frame.get("digest"):
        raise ContractError("the frame manifest's content does not match its digest; it was edited")
    if man.get("frame_manifest_digest") != frame["digest"]:
        raise ContractError(f"{what} was not drawn from the frame manifest on disk (frame digest differs)")
    if not registry_path.exists():
        raise ContractError(f"no registry at {registry_path}")
    log = json.loads(registry_path.read_text(encoding="utf-8")).get("assignment_log") or []
    head = log[-1] if log else {}
    if head.get("kind") != EVENT_FREEZE:
        raise ContractError(f"the registry head is {head.get('kind')!r}, not the terminal freeze; the frame is stale")
    if head.get("audit_ref") != frame["digest"]:
        raise ContractError("the freeze event's audit_ref is not the frame manifest's digest")
    if head.get("h") != man.get("freeze_event_hash"):
        raise ContractError(f"{what} names a different freeze event than the registry head")
    prev = log[-2].get("h") if len(log) > 1 else None
    if prev != frame.get("pre_freeze_head"):
        raise ContractError("the freeze event's predecessor is not the frame manifest's pre_freeze_head")
    if head.get("eligible_checksum") != frame.get("eligible_checksum"):
        raise ContractError("the freeze event's eligible_checksum is not the frame manifest's")
    return frame, head


def verify_frame_completion(man: dict, *, frame_path: Path | None = None, registry_path: Path | None = None,
                            listing_path: Path | None = None, sample_path: Path | None = None,
                            a3_path: Path | None = None) -> dict:
    """The contract of the frame-completion listing (§4d): the intact committed listing; the frozen frame and
    registry handshake; exactly the frame paragraphs that are neither in the registered A2 sample nor in the A3
    census (both intact, both named by digest); frozen identity and metadata. Returns the frame manifest."""
    frame_path = Path(frame_path or FRAME_MANIFEST); registry_path = Path(registry_path or REGISTRY_FILE)
    listing_path = Path(listing_path or FRAME_COMPLETION_MANIFEST)
    sample_path = Path(sample_path or SAMPLE_MANIFEST); a3_path = Path(a3_path or A3_SAMPLE_MANIFEST)
    if man.get("kind") != FRAME_COMPLETION_KIND:
        raise ContractError(f"a frame-completion run needs a {FRAME_COMPLETION_KIND} (kind={man.get('kind')!r})")
    if not listing_path.exists():
        raise ContractError(f"no frame-completion listing at {listing_path}")
    listed = json.loads(listing_path.read_text(encoding="utf-8"))
    if (canonical_digest(man) != man.get("digest") or canonical_digest(listed) != listed.get("digest")
            or man.get("digest") != listed.get("digest")):
        raise ContractError(f"the manifest presented is not the intact frame-completion listing ({listing_path.name})")
    frame, _head = _frame_and_registry(man, frame_path, registry_path, "the frame-completion listing")
    excl = {}
    for name, path_, kind in (("A2", sample_path, "A2_sample_manifest"), ("A3", a3_path, A3_KIND)):
        if not path_.exists():
            raise ContractError(f"no {name} manifest at {path_}; the frame-completion listing is defined relative to it")
        doc = json.loads(path_.read_text(encoding="utf-8"))
        if doc.get("kind") != kind or canonical_digest(doc) != doc.get("digest"):
            raise ContractError(f"the {name} manifest is not intact (kind or digest)")
        if doc.get("frame_manifest_digest") != frame["digest"]:
            raise ContractError(f"the {name} manifest is not bound to the frame manifest on disk")
        named = (man.get("excludes") or {}).get(name) or {}
        if named.get("digest") != doc.get("digest"):
            raise ContractError(f"the frame-completion listing does not name the {name} manifest it excludes")
        excl[name] = {q.get("digest") for q in doc.get("paragraphs") or []}
    members = {q["paragraph_id"]: q for q in frame.get("paragraphs") or []}
    admissible = {pid for pid, m in members.items() if m.get("digest") not in excl["A2"] and m.get("digest") not in excl["A3"]}
    paras = man.get("paragraphs") or []
    ids = [q.get("paragraph_id") for q in paras]
    dup = [k for k, c in Counter(ids).items() if c > 1]
    if dup:
        raise ContractError(f"{len(dup)} paragraph(s) listed more than once: {dup[:4]}")
    if set(ids) != admissible:
        raise ContractError("the frame-completion listing is not exactly the frame minus A2 and A3: "
                            f"missing {sorted(admissible - set(ids))[:4]}, extra {sorted(set(ids) - admissible)[:4]}")
    bad = [q.get("paragraph_id") for q in paras if members.get(q.get("paragraph_id")) is None
           or any(q.get(f) != members[q["paragraph_id"]].get(f) for f in ("digest", "text_sha256", "article_id", "lang", "outlet", "exposure"))]
    if bad:
        raise ContractError(f"{len(bad)} paragraph(s) differ from the frozen frame in identity or metadata: {bad[:4]}")
    if man.get("n") != len(paras) or dict(man.get("by_language") or {}) != dict(Counter(q["lang"] for q in paras)):
        raise ContractError("n / by_language do not match the listed paragraphs")
    return frame


def verify_a3_sample(man: dict, *, frame_path: Path | None = None, registry_path: Path | None = None,
                     listing_path: Path | None = None, sample_path: Path | None = None) -> dict:
    """The sample contract of the fresh-sample census (§4b): the manifest presented is the intact A3 listing on
    disk; it was drawn from the frozen frame with the registry handshake of the registered draw; it is a census
    of EXACTLY the frame paragraphs the §4b rule admits (not in the registered A2 sample by digest, exposure
    clean_article, article without an A2 paragraph); every paragraph carries probability and weight one; the
    seeded order and a clean exposure audit are recorded. Raise ContractError on the first violation; return
    the frame manifest on success. Nothing is defaulted."""
    frame_path = Path(frame_path or FRAME_MANIFEST)
    registry_path = Path(registry_path or REGISTRY_FILE)
    listing_path = Path(listing_path or A3_SAMPLE_MANIFEST)
    sample_path = Path(sample_path or SAMPLE_MANIFEST)
    # 1 -- kind, and identity with the committed listing
    if man.get("kind") != A3_KIND:
        raise ContractError(f"a fresh-sample evaluation needs an A3 sample manifest (kind={man.get('kind')!r})")
    if not listing_path.exists():
        raise ContractError(f"no A3 listing at {listing_path}; the census has not been listed")
    listed = json.loads(listing_path.read_text(encoding="utf-8"))
    if (canonical_digest(man) != man.get("digest") or canonical_digest(listed) != listed.get("digest")
            or man.get("digest") != listed.get("digest")):
        raise ContractError(f"the manifest presented is not the intact A3 listing ({listing_path.name}): digests differ")
    # 2 -- frame
    if not frame_path.exists():
        raise ContractError(f"no frame manifest at {frame_path}; the census is a subset of the frozen frame")
    frame = json.loads(frame_path.read_text(encoding="utf-8"))
    if canonical_digest(frame) != frame.get("digest"):
        raise ContractError("the frame manifest's content does not match its digest; it was edited")
    if man.get("frame_manifest_digest") != frame["digest"]:
        raise ContractError("the A3 listing was not drawn from the frame manifest on disk (frame digest differs)")
    # 3 -- registry handshake, as for the registered draw
    if not registry_path.exists():
        raise ContractError(f"no registry at {registry_path}")
    log = json.loads(registry_path.read_text(encoding="utf-8")).get("assignment_log") or []
    head = log[-1] if log else {}
    if head.get("kind") != EVENT_FREEZE:
        raise ContractError(f"the registry head is {head.get('kind')!r}, not the terminal freeze; the frame is stale")
    if head.get("audit_ref") != frame["digest"]:
        raise ContractError("the freeze event's audit_ref is not the frame manifest's digest")
    if head.get("h") != man.get("freeze_event_hash"):
        raise ContractError("the A3 listing names a different freeze event than the registry head")
    prev = log[-2].get("h") if len(log) > 1 else None
    if prev != frame.get("pre_freeze_head"):
        raise ContractError("the freeze event's predecessor is not the frame manifest's pre_freeze_head")
    if head.get("eligible_checksum") != frame.get("eligible_checksum"):
        raise ContractError("the freeze event's eligible_checksum is not the frame manifest's")
    # 4 -- the registered A2 sample the census excludes
    if not sample_path.exists():
        raise ContractError(f"no registered A2 sample manifest at {sample_path}; the census is defined relative to it")
    a2 = json.loads(sample_path.read_text(encoding="utf-8"))
    if a2.get("kind") != "A2_sample_manifest" or canonical_digest(a2) != a2.get("digest"):
        raise ContractError("the registered A2 sample manifest is not intact (kind or digest)")
    if a2.get("frame_manifest_digest") != frame["digest"] or a2.get("freeze_event_hash") != head.get("h"):
        raise ContractError("the registered A2 sample manifest is not bound to the frame manifest and freeze event on disk")
    excl = man.get("excludes_sample_manifest") or {}
    if excl.get("kind") != "A2_sample_manifest" or excl.get("digest") != a2.get("digest"):
        raise ContractError("the A3 listing does not name the registered A2 sample manifest it excludes")
    a2_dig = {q.get("digest") for q in a2.get("paragraphs") or []}
    a2_art = {q.get("article_id") for q in a2.get("paragraphs") or []}
    # 5 -- the census: exactly the admissible frame paragraphs, with the frozen identity and metadata
    members = {q["paragraph_id"]: q for q in frame.get("paragraphs") or []}
    admissible = {pid for pid, m in members.items()
                  if m.get("digest") not in a2_dig and m.get("exposure") == "clean_article" and m.get("article_id") not in a2_art}
    paras = man.get("paragraphs") or []
    ids = [q.get("paragraph_id") for q in paras]
    dup = [k for k, c in Counter(ids).items() if c > 1]
    if dup:
        raise ContractError(f"{len(dup)} paragraph(s) listed more than once: {dup[:4]}")
    if set(ids) != admissible:
        raise ContractError("the A3 listing is not the census of the admissible frame paragraphs: "
                            f"missing {sorted(admissible - set(ids))[:4]}, extra {sorted(set(ids) - admissible)[:4]}")
    bad = []
    for q in paras:
        m = members.get(q.get("paragraph_id"))
        if m is None or any(q.get(f) != m.get(f) for f in ("digest", "text_sha256", "article_id", "lang", "outlet", "exposure")):
            bad.append(q.get("paragraph_id"))
    if bad:
        raise ContractError(f"{len(bad)} A3 paragraph(s) differ from the frozen frame in identity or metadata: {bad[:4]}")
    # 6 -- design: a census with probability and weight one, the declared seed, counts, a clean audit
    design = man.get("design")
    if not isinstance(design, dict) or design.get("census") is not True:
        raise ContractError("the A3 listing does not declare itself a census")
    if (design.get("seeds") or {}).get("order") != A3_SEED_ORDER:
        raise ContractError(f"the sitting-order seed is not the declared {A3_SEED_ORDER}")
    problems = []
    for q in paras:
        if not (_finite_positive(q.get("pi")) and abs(float(q["pi"]) - 1.0) < 1e-12):
            problems.append(f"{q['paragraph_id']}: pi {q.get('pi')!r} != 1")
        if not (_finite_positive(q.get("weight")) and abs(float(q["weight"]) - 1.0) < 1e-12):
            problems.append(f"{q['paragraph_id']}: weight {q.get('weight')!r} != 1")
        if q.get("stratum") not in (None, f"{q['lang']}|{q['outlet']}"):
            problems.append(f"{q['paragraph_id']}: stratum label {q.get('stratum')!r} is not lang|outlet")
        if len(problems) >= 6:
            break
    if problems:
        raise ContractError("the census design does not hold for this listing: " + "; ".join(problems[:6]))
    if man.get("n") != len(paras):
        raise ContractError(f"the listing says n={man.get('n')} but lists {len(paras)} paragraphs")
    by_lang = dict(Counter(q["lang"] for q in paras))
    if dict(man.get("by_language") or {}) != by_lang:
        raise ContractError(f"by_language {man.get('by_language')} does not match the paragraphs {by_lang}")
    by_art = dict(Counter(q["article_id"] for q in paras))
    if man.get("n_articles") != len(by_art) or dict(man.get("by_article") or {}) != by_art:
        raise ContractError("n_articles / by_article do not match the listed paragraphs")
    strata = Counter(f"{q['lang']}|{q['outlet']}" for q in paras)
    listed_strata = {s_.get("stratum"): s_.get("n") for s_ in (man.get("strata") or [])}
    if listed_strata != dict(strata):
        raise ContractError("the strata block does not match the listed paragraphs")
    audit = man.get("exposure_audit")
    if not isinstance(audit, dict) or audit.get("clean") is not True or audit.get("overlap") or not audit.get("files"):
        raise ContractError("the A3 listing carries no clean exposure audit (files read, no overlap)")
    return frame


# --------------------------------------------------------------------------- #
# the threshold seal
# --------------------------------------------------------------------------- #
def write_threshold_seal(s: int, author_confirmation: str, *, seal_path: Path | None = None,
                         sample_path: Path | None = None, frame_path: Path | None = None) -> dict:
    """Seal the author's blind threshold once. Refuses to overwrite, refuses a non-positive value, and
    records what the value was fixed against: the registered sample and frame digests, the freeze event,
    the git head and whether the tree was dirty, the timestamp and the author's confirmation sentence."""
    import datetime as _dt
    import subprocess as _sp
    seal_path = Path(seal_path or THRESHOLD_SEAL)
    sample_path = Path(sample_path or SAMPLE_MANIFEST)
    frame_path = Path(frame_path or FRAME_MANIFEST)
    if seal_path.exists():
        raise ContractError(f"a threshold seal already exists at {seal_path}; it is written once and never rewritten")
    if not isinstance(s, int) or isinstance(s, bool) or s < 1:
        raise ContractError(f"the threshold must be a positive integer, not {s!r}")
    if not author_confirmation or "blind" not in author_confirmation.lower():
        raise ContractError("the author's confirmation sentence must state that the value was chosen blind to every A2 label")
    if not sample_path.exists() or not frame_path.exists():
        raise ContractError("the registered sample manifest and the frozen frame manifest must exist before the threshold is sealed")
    man = json.loads(sample_path.read_text(encoding="utf-8"))
    frame = json.loads(frame_path.read_text(encoding="utf-8"))
    if man.get("kind") != "A2_sample_manifest" or canonical_digest(man) != man.get("digest"):
        raise ContractError("the registered sample manifest is not intact")
    if canonical_digest(frame) != frame.get("digest") or man.get("frame_manifest_digest") != frame["digest"]:
        raise ContractError("the frame manifest is not the one the sample was drawn from")
    def _git(*args):
        try:
            return _sp.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:                                    # git absent: recorded as unknown, never invented
            return ""
    if REQUIRE_SEAL_COMMIT:
        # the seal binds committed inputs: the registered sample and frame manifests, the registry with its
        # freeze event, and this module must be tracked and clean, so that the recorded git head is the tree
        # the seal speaks about (stand-in review 2026-09-07, #1)
        inputs = [sample_path, frame_path, REGISTRY_FILE, Path(__file__)]
        dirty = []
        for f in inputs:
            try:
                rel = str(f.resolve().relative_to(ROOT.resolve()))
            except ValueError:
                raise ContractError(f"{f} lies outside the repository; the seal binds committed inputs only")
            st = _sp.run(["git", "status", "--porcelain", "--", rel], cwd=ROOT, capture_output=True, text=True, timeout=20)
            if st.returncode != 0:
                raise ContractError(f"git status failed for {rel}: {st.stderr.strip()[:120]}")
            if st.stdout.strip():
                dirty.append(rel)
        if dirty:
            raise ContractError("the seal binds committed inputs, but these are untracked or modified: " + ", ".join(dirty)
                                + "; commit the registry, the frame and sample manifests and this module first")
    seal = {
        "kind": SEAL_KIND,
        "s": s,
        "rule": SEAL_RULE,
        "support_units": dict(SUPPORT_UNITS),
        "applies_to": list(SEAL_APPLIES_TO),
        "sample_manifest_digest": man["digest"],
        "frame_manifest_digest": frame["digest"],
        "freeze_event_hash": man.get("freeze_event_hash"),
        "git_head": _git("rev-parse", "HEAD") or "unknown",
        "git_tree_dirty": bool(_git("status", "--porcelain")),
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "author_confirmation": author_confirmation,
        "generator": {"file": Path(__file__).name,
                      "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
    }
    seal["digest"] = canonical_digest(seal)
    seal_path.parent.mkdir(parents=True, exist_ok=True)
    seal_path.write_text(json.dumps(seal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return seal


LABEL_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y")   # exactly finalize.py's DATE_FORMATS


def parse_date(value) -> "datetime.date":
    """A coding date as finalize.py accepts it (`YYYY-MM-DD`, `D.M.YYYY`, `D.M.YY` with strptime's century
    pivot) or an ISO timestamp; anything else is a ContractError. Both consumers of `coded_at` -- this module
    and the annotation tooling -- parse with the same three formats, so a two-digit year can never be read as
    year 26 by one and as 2026 by the other."""
    import datetime as _dt
    v = str(value or "").strip()
    if "T" in v:
        try:
            return _dt.datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        except Exception:
            raise ContractError(f"unparseable date {value!r}") from None
    for fmt in LABEL_DATE_FORMATS:
        try:
            return _dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ContractError(f"unparseable date {value!r}")


def parse_datetime(value) -> "datetime.datetime":
    """An ISO timestamp as an aware datetime (naive values are read as UTC; a bare date is its first instant)."""
    import datetime as _dt
    v = str(value or "").strip()
    try:
        if "T" in v:
            d = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        else:
            d = _dt.datetime.combine(parse_date(v), _dt.time())
        return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
    except ContractError:
        raise
    except Exception:
        raise ContractError(f"unparseable timestamp {value!r}") from None


def _seal_commitment(seal_path: Path) -> dict:
    """The seal's first commit (hash, committer date) and whether the file still equals that commit's content."""
    import subprocess as _sp
    rel = None
    try:
        rel = str(seal_path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        raise ContractError(f"the threshold seal at {seal_path} lies outside the repository; it cannot have been committed")
    def _git(*args):
        r = _sp.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise ContractError(f"git {' '.join(args[:2])} failed: {r.stderr.strip()[:120]}")
        return r.stdout
    first = _git("log", "--diff-filter=A", "--format=%H %cI", "--follow", "--", rel).strip().splitlines()
    if not first:
        raise ContractError(f"the threshold seal {rel} is not committed; commit it before the first A2 paragraph is coded")
    commit, committed_at = first[-1].split(" ", 1)      # the oldest commit that added the file
    committed_bytes = _sp.run(["git", "show", f"{commit}:{rel}"], cwd=ROOT, capture_output=True, timeout=30).stdout
    if committed_bytes != seal_path.read_bytes():
        raise ContractError("the threshold seal was rewritten after its first commit; the sealed value is the committed one")
    return {"commit": commit, "committed_at": committed_at}


def verify_threshold_seal(s: int, man: dict, *, seal_path: Path | None = None, earliest_coding=None) -> dict:
    """A reportable evaluation runs only under the sealed threshold. Checks, in order: the seal exists and has
    the complete schema with the fixed rule and support units; its digest; it names this sample, frame and
    freeze event; it equals the threshold given; it is committed and unchanged since its first commit (unless
    REQUIRE_SEAL_COMMIT is off, for fixtures outside a repository); and, when the first coding date of the
    reference is known, that commit does not postdate it. Returns the seal with its commitment."""
    seal_path = Path(seal_path or THRESHOLD_SEAL)
    if not seal_path.exists():
        raise ContractError(f"no threshold seal at {seal_path}; the blind threshold s must be sealed before a reportable "
                            f"evaluation (specification §6 item 1)")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    required = {"kind": str, "s": int, "rule": str, "support_units": dict, "applies_to": list, "sample_manifest_digest": str,
                "frame_manifest_digest": str, "freeze_event_hash": str, "git_head": str, "git_tree_dirty": bool,
                "created_at": str, "author_confirmation": str, "generator": dict, "digest": str}
    for key, typ in required.items():
        if key not in seal or not isinstance(seal[key], typ) or (typ is int and isinstance(seal[key], bool)):
            raise ContractError(f"the threshold seal is incomplete or malformed: field {key!r} (schema)")
    if seal["kind"] != SEAL_KIND or canonical_digest(seal) != seal["digest"]:
        raise ContractError("the threshold seal is not intact (kind or digest)")
    if seal["s"] < 1:
        raise ContractError(f"the sealed threshold {seal['s']} is not a positive integer (schema)")
    if seal["rule"] != SEAL_RULE or seal["support_units"] != SUPPORT_UNITS or seal["applies_to"] != SEAL_APPLIES_TO:
        raise ContractError("the threshold seal states a rule, support units or scope that are not the registered ones (schema)")
    if "blind" not in seal["author_confirmation"].lower() or set(seal["generator"]) != {"file", "sha256"}:
        raise ContractError("the threshold seal lacks the author's blind-confirmation sentence or its generator record (schema)")
    parse_date(seal["created_at"])
    if seal["sample_manifest_digest"] != man.get("digest") or seal["frame_manifest_digest"] != man.get("frame_manifest_digest") \
            or seal["freeze_event_hash"] != man.get("freeze_event_hash"):
        raise ContractError("the threshold seal was written against another sample, frame or freeze event than the manifest presented")
    if seal["s"] != s:
        raise ContractError(f"--threshold {s} is not the sealed value s={seal['s']}; the reportable threshold is the sealed one")
    commitment = _seal_commitment(seal_path) if REQUIRE_SEAL_COMMIT else {"commit": None, "committed_at": None}
    if commitment["committed_at"]:
        if parse_datetime(seal["created_at"]) > parse_datetime(commitment["committed_at"]):
            raise ContractError("the threshold seal's created_at postdates its first commit; the seal is not the one that was committed")
        if earliest_coding is not None:
            # label files carry coding DATES, not times: the seal must be committed on an earlier calendar day
            # (the commit's own local date), so a same-day commit never counts as blind
            if parse_date(commitment["committed_at"]) >= parse_date(earliest_coding):
                raise ContractError(f"the threshold seal was committed on {commitment['committed_at'][:10]}, not on a day before the "
                                    f"first reference row was coded ({earliest_coding}); the threshold was not sealed blind")
    return {**seal, "commitment": commitment}


# --------------------------------------------------------------------------- #
# §4e(a) -- the language arm: translated inputs on the German A2 paragraphs (declared 2026-10-19, amended
# 2026-11-05 before any study paragraph was translated). The registered instrument v1.0 runs k = 3 on the stored
# English translations of the 60 German paragraphs of the registered sample; manifest, paragraph ids, design weights
# and the German sampling identity stay those of A2. This block is the ONE contract the translator
# (`language_arm.py translate`), the runner (`--translated-input`), the evaluator and the comparison share: the frozen
# translation request, the verification of the translation file against the manifest, the registered text, the public
# journal and record of the translation step, and the author's sealed check.
# --------------------------------------------------------------------------- #
TRANSLATED_STATUS = "exploratory reused A2 — translated inputs"
TRANSLATED_AUTHORITY = "pre-analysis specification §4e(a), amendment of 2026-11-05"
TRANSLATED_LANG = "de"                       # the arm covers the German paragraphs of the registered sample only
TRANSLATED_REPEATS = 3                       # amendment item 7: three executions per paragraph, execution one primary
TRANSLATOR_MODEL_ID = "openai.gpt-oss-120b-1:0"
TRANSLATOR_REGION = "eu-central-1"           # on-demand in-region invocation; no cross-region or global profile
TRANSLATIONS_FILE = ROOT / "tools/annotation/sheets/A2_translations_de_en.csv"      # PRIVATE (gitignored sheets/)
TRANSLATION_CHECK_FILE = ROOT / "tools/annotation/sheets/A2_translation_check.csv"  # PRIVATE (gitignored sheets/)
TRANSLATION_JOURNAL = ROOT / "tools/runs/language_arm/translation_journal.jsonl"    # tracked: no text
TRANSLATION_RECORD = ROOT / "tools/runs/language_arm/translation_record.json"       # tracked: no text
TRANSLATION_CHECK_SEAL = ROOT / "tools/runs/language_arm/translation_check_seal.json"   # tracked: digests and counts only
TRANSLATION_RECORD_KIND = "language_arm_translation_record"
TRANSLATION_CHECK_SEAL_KIND = "translation_check_seal"
T_TRANSLATED, T_FAILED = "translated", "failed"
TRANSLATION_COLUMNS = ["paragraph_id", "article_id", "source_sha256", "outcome", "translation", "translation_sha256",
                       "model_id", "region", "request_sha256", "prompt_sha256", "stop_reason", "input_tokens",
                       "output_tokens", "attempts", "translated_at"]
# amendment item 6: the fixed rubric, one flag column per category, and a severity per pair
CHECK_CATEGORIES = ("omission_or_addition", "technology_referent", "negation", "modality_or_hedging", "quantities_units",
                    "time_reference", "attribution_reported_speech", "ambiguity_resolved", "other")
CHECK_SEVERITIES = ("none", "minor", "major")
CHECK_COLUMNS = ["paragraph_id", "source_text", "translation", *CHECK_CATEGORIES, "severity", "note"]
CHECK_FLAG_YES, CHECK_FLAG_NO = ("1", "x"), ("", "0")     # a category cell: flagged (1 or x) or not (empty or 0)

# The frozen translation request (amendment item 3), verbatim, and its declared digest as a literal: an edit of the
# prompt anywhere in the code base fails at import, and a translation file made under another prompt or other decoding
# is refused by `load_translation_file` (prompt digest per row; the request digest wherever the source text is held).
TRANSLATION_PROMPT = (
    "Translate the user's German news paragraph into English. Translate faithfully and completely: keep every statement, "
    "hedge, negation, number, unit and name, keep who says what (attribution and reported speech, e.g. render German "
    "reported speech with 'said', 'according to' or 'reportedly'), and keep the order of information. Do not add, "
    "explain, summarise, correct or resolve ambiguity. Output only the English translation.")
TRANSLATION_PROMPT_SHA256 = "bc115b205d187ee5094c42a26e5ab9dd5143135e916565fced2bfca624c87404"
if hashlib.sha256(TRANSLATION_PROMPT.encode("utf-8")).hexdigest() != TRANSLATION_PROMPT_SHA256:
    raise RuntimeError("the frozen translation prompt (specification §4e(a) item 3) was edited: its digest is not the declared one")
TRANSLATION_INFERENCE = {"temperature": 0, "maxTokens": 4096}
TRANSLATION_EXTRA = {"reasoning_effort": "medium"}
TRANSLATION_REPEATS = 2                      # item 4: identical repeats after a raise, a stop other than end_turn, empty text
TRANSLATION_MAX_ATTEMPTS = 1 + TRANSLATION_REPEATS


def translation_request(text: str) -> dict:
    """The frozen Converse request: the system prompt, the paragraph text alone as the user turn -- no headline,
    metadata, neighbouring paragraph, codebook or label -- temperature 0, 4,096 tokens, reasoning effort medium."""
    return {"modelId": TRANSLATOR_MODEL_ID, "system": [{"text": TRANSLATION_PROMPT}],
            "messages": [{"role": "user", "content": [{"text": text}]}],
            "inferenceConfig": dict(TRANSLATION_INFERENCE), "additionalModelRequestFields": dict(TRANSLATION_EXTRA)}


def translation_request_digest(text: str) -> str:
    """The digest of the frozen request on `text`, by the runner's request-digest rule (sorted keys, compact, UTF-8)."""
    return hashlib.sha256(json.dumps(translation_request(text), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(p)


def _int_cell(v) -> int | None:
    s = str(v if v is not None else "").strip()
    return int(s) if s.isdigit() else None


def _read_semicolon_csv(path: Path) -> tuple[list[str], list[dict]]:
    """A semicolon-delimited UTF-8 file; an undecodable or malformed file is a refusal, not a traceback."""
    import csv as _csv
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            rd = _csv.DictReader(f, delimiter=";")
            return list(rd.fieldnames or []), list(rd)
    except UnicodeDecodeError:
        raise ContractError(f"{Path(path).name} is not UTF-8 (save it as 'CSV UTF-8' with ';' as the delimiter)") from None
    except _csv.Error as exc:
        raise ContractError(f"{Path(path).name} is not a well-formed ';'-delimited CSV ({type(exc).__name__}; save it "
                            f"as 'CSV UTF-8' with ';' as the delimiter)") from None


def _git(args: list[str], *, text: bool = True):
    import subprocess as _sp
    try:
        return _sp.run(["git", *args], cwd=str(ROOT), capture_output=True, text=text, timeout=30)
    except (OSError, _sp.SubprocessError) as exc:
        raise ContractError(f"git {args[0]} failed ({type(exc).__name__})") from None


def _repo_rel(path: Path, what: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        raise ContractError(f"{what} at {path} lies outside the repository; it cannot have been committed") from None


def ensure_ignored(path: Path) -> None:
    """A file holding paragraph or translation text lies only where git ignores it: a path inside the repository must
    be ignored (git check-ignore); a path outside it (a test's temporary directory) cannot be committed from here."""
    try:
        rel = str(Path(path).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return
    if _git(["check-ignore", "-q", rel]).returncode != 0:
        raise ContractError(f"{rel} is not ignored by git; paragraph and translation text are kept only where they cannot "
                            f"be committed (tools/annotation/sheets/ is the private location)")


def file_commitment(path: Path, what: str, *, first: bool) -> dict:
    """Committed and unchanged, by content: the bytes on disk are compared with the committed blob, so an
    assume-unchanged or skip-worktree flag hides nothing. With `first`, against the oldest commit that added the file (a
    file written once: the check seal, as the threshold seal); otherwise against HEAD (files that grew before they were
    committed: the translation journal and record). Returns that commit (for HEAD: the last commit touching the file)
    and its committer date."""
    rel = _repo_rel(path, what)
    if first:
        r = _git(["log", "--diff-filter=A", "--format=%H %cI", "--follow", "--", rel])
        lines = r.stdout.strip().splitlines() if r.returncode == 0 else []
        if not lines:
            raise ContractError(f"{what} {rel} is not committed")
        commit, committed_at = lines[-1].split(" ", 1)       # the oldest commit that added the file
        rev = commit
    else:
        r = _git(["log", "-1", "--format=%H %cI", "--", rel])
        line = r.stdout.strip() if r.returncode == 0 else ""
        if not line:
            raise ContractError(f"{what} {rel} is not committed")
        commit, committed_at = line.split(" ", 1)
        rev = "HEAD"
    blob = _git(["show", f"{rev}:{rel}"], text=False)
    if blob.returncode != 0:
        raise ContractError(f"{what} {rel} is not in {'its first commit' if first else 'HEAD'}")
    if blob.stdout != Path(path).read_bytes():
        raise ContractError(f"{what} {rel} differs from its {'first commit (rewritten after it was committed)' if first else 'committed version at HEAD (pending changes)'}")
    return {"commit": commit, "committed_at": committed_at}


def ever_committed(path: Path) -> bool:
    """Whether git tracks `path` or any commit of any branch ever touched it (a seal path once used is never reused)."""
    try:
        rel = str(Path(path).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return False
    if _git(["ls-files", "--error-unmatch", rel]).returncode == 0:
        return True
    r = _git(["log", "--all", "-1", "--format=%H", "--", rel])
    return bool(r.stdout.strip()) if r.returncode == 0 else True     # unknown history: treat as committed (refuse)


# The commitment tests (fixtures outside a repository replace them explicitly). Each returns {commit, committed_at}
# or raises ContractError.
def CHECK_SEAL_COMMITMENT(path: Path) -> dict:
    return file_commitment(path, "the translation check seal", first=True)


def TRANSLATION_PROVENANCE_COMMITMENT(path: Path) -> dict:
    return file_commitment(path, "the translation provenance file", first=False)


SEAL_EVER_COMMITTED = ever_committed


def translation_pairs_digest(rows: dict) -> str:
    """The per-paragraph binding: sha256 over the sorted (paragraph id, source sha256, outcome, translation sha256)
    tuples -- what the execution contract binds besides the file's own digest."""
    body = [[pid, r["source_sha256"], r["outcome"], r.get("translation_sha256") or ""] for pid, r in sorted(rows.items())]
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode("utf-8")).hexdigest()


def check_translation_row(r: dict, want: dict, source_text: str | None = None) -> None:
    """One row of the translation file against its manifest paragraph and, where the caller holds it, the registered
    source text: well formed; the manifest's article; the manifest's text hash (the full hash against the text when
    given); the declared translator and region; the frozen prompt; the frozen request on that text (when given); a
    translated row stopped with end_turn after 1 to 3 attempts, its non-empty text reproducing its hash; a failed row
    without text after the whole allowance. Messages name the paragraph and the column, never a cell's content."""
    pid = want["paragraph_id"]
    if None in r or any(v is None for v in r.values()):
        raise ContractError(f"{pid}: the row is malformed (its field count is not the header's)")
    if r["article_id"] != want.get("article_id"):
        raise ContractError(f"{pid}: article_id is not the manifest's")
    src = r["source_sha256"]
    if len(src) != 64 or src[:16] != want.get("text_sha256"):
        raise ContractError(f"{pid}: the recorded source hash is not the manifest's text hash")
    if source_text is not None and _sha_text(source_text) != src:
        raise ContractError(f"{pid}: the translation was not made from the registered paragraph text (source hash differs)")
    if r["model_id"] != TRANSLATOR_MODEL_ID or r["region"] != TRANSLATOR_REGION:
        raise ContractError(f"{pid}: model_id or region is not the declared {TRANSLATOR_MODEL_ID} in {TRANSLATOR_REGION}")
    if r["prompt_sha256"] != TRANSLATION_PROMPT_SHA256:
        raise ContractError(f"{pid}: not made under the frozen translation prompt (prompt digest differs)")
    rq = r["request_sha256"]
    if len(rq) != 64 or any(c not in "0123456789abcdef" for c in rq):
        raise ContractError(f"{pid}: request_sha256 is not a sha256 digest")
    if source_text is not None and rq != translation_request_digest(source_text):
        raise ContractError(f"{pid}: not the frozen translation request (request digest differs: prompt, decoding or text)")
    if _int_cell(r["input_tokens"]) is None or _int_cell(r["output_tokens"]) is None:
        raise ContractError(f"{pid}: a token count is not a non-negative integer")
    try:
        parse_datetime(r["translated_at"])
    except ContractError:
        raise ContractError(f"{pid}: translated_at is not a timestamp") from None
    oc, text, th, n = r["outcome"], r["translation"], r["translation_sha256"], _int_cell(r["attempts"])
    if oc == T_TRANSLATED:
        if r["stop_reason"] != "end_turn":
            raise ContractError(f"{pid}: a translated row did not stop with end_turn")
        if n is None or not 1 <= n <= TRANSLATION_MAX_ATTEMPTS:
            raise ContractError(f"{pid}: attempts is not between 1 and {TRANSLATION_MAX_ATTEMPTS}")
        if not text.strip() or _sha_text(text) != th:
            raise ContractError(f"{pid}: the translation is empty or does not reproduce its recorded hash (edited)")
    elif oc == T_FAILED:
        if text or th:
            raise ContractError(f"{pid}: a failed translation carries text or a hash")
        if n != TRANSLATION_MAX_ATTEMPTS:
            raise ContractError(f"{pid}: a failed row is written only after all {TRANSLATION_MAX_ATTEMPTS} attempts")
    else:
        raise ContractError(f"{pid}: outcome is neither {T_TRANSLATED!r} nor {T_FAILED!r}")


def read_translation_journal(path: Path) -> tuple[list[dict], int]:
    """The public journal of the translation step, read without touching it (a line that does not parse is counted)."""
    path = Path(path)
    if not path.exists():
        raise ContractError(f"no translation journal at {_rel(path)}; translations enter the arm only through "
                            f"`language_arm.py translate`")
    out, torn = [], 0
    for line in path.read_bytes().decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            torn += 1
            continue
        if isinstance(rec, dict):
            out.append(rec)
        else:
            torn += 1
    return out, torn


def verify_translation_journal(rows: dict, journal_path: Path, *, complete: bool) -> dict:
    """The rows against the public journal of the translation step that wrote them: every request journalled for a
    paragraph is the row's request digest; a translated row's hash is that of the ONE successful attempt journalled for
    its paragraph (never re-generated after a success); a failed row has its paragraph_failed event and no successful
    attempt. With `complete`, the journal names no paragraph outside the file. Returns counts."""
    recs, torn = read_translation_journal(journal_path)
    ok_by, req_by, failed_ev = {}, {}, set()
    for x in recs:
        t, pid = x.get("type"), x.get("paragraph_id")
        if t == "attempt_started":
            req_by.setdefault(pid, set()).add(x.get("request_sha256"))
        elif t == "attempt_finished" and x.get("code") == "ok":
            ok_by.setdefault(pid, []).append(x.get("translation_sha256"))
        elif t == "paragraph_failed":
            failed_ev.add(pid)
    twice = sorted(p for p, v in ok_by.items() if len(v) > 1)
    if twice:
        raise ContractError(f"the journal records more than one successful translation for {twice[:4]} (re-generated)")
    lost = sorted((set(ok_by) | failed_ev) - set(rows))
    if lost:
        raise ContractError(f"the journal records a finished translation for {lost[:4]} that the file lacks (a row was lost; "
                            f"translations are never re-generated)")
    if complete:
        foreign = sorted(set(req_by) - set(rows))
        if foreign:
            raise ContractError(f"the journal names paragraphs outside the translation file: {foreign[:4]}")
    for pid, r in rows.items():
        if req_by.get(pid) != {r["request_sha256"]}:
            raise ContractError(f"{pid}: the journalled requests are not the row's request digest")
        if r["outcome"] == T_TRANSLATED:
            if ok_by.get(pid) != [r["translation_sha256"]]:
                raise ContractError(f"{pid}: the translation is not the one the journal records as the successful attempt")
        elif ok_by.get(pid) or pid not in failed_ev:
            raise ContractError(f"{pid}: a failed row without its journalled failure, or beside a journalled success")
    return {"journal_sha256": hashlib.sha256(Path(journal_path).read_bytes()).hexdigest(), "journal_torn_lines": torn}


def verify_translation_record(binding: dict, journal_sha: str, *, record_path: Path | None = None,
                              journal_path: Path | None = None, commitment: bool = True) -> dict:
    """The public record of the translation step: intact (kind, digest), complete, naming this file (digest and
    per-paragraph digest), the frozen request and decoding, the journal as it is now, and the registered German figure
    reproduced before the first call (amendment item 8); record and journal committed unchanged."""
    rp = Path(record_path or TRANSLATION_RECORD)
    jp = Path(journal_path or TRANSLATION_JOURNAL)
    if not rp.exists():
        raise ContractError(f"no translation record at {_rel(rp)}; translations enter the arm only through "
                            f"`language_arm.py translate`")
    try:
        rec = json.loads(rp.read_text(encoding="utf-8"))
    except ValueError:
        raise ContractError("the translation record is not JSON") from None
    if not isinstance(rec, dict) or rec.get("kind") != TRANSLATION_RECORD_KIND or canonical_digest(rec) != rec.get("digest"):
        raise ContractError("the translation record is not intact (kind or digest)")
    f, q = rec.get("file") or {}, rec.get("request") or {}
    if not f.get("complete") or f.get("sha256") != binding["file_sha256"] or f.get("pairs_sha256") != binding["pairs_sha256"]:
        raise ContractError("the translation record does not name this translation file as complete (file or "
                            "per-paragraph digest differs)")
    if (q.get("system_prompt_sha256") != TRANSLATION_PROMPT_SHA256 or q.get("inference_config") != TRANSLATION_INFERENCE
            or q.get("additional_model_request_fields") != TRANSLATION_EXTRA
            or (rec.get("translator") or {}).get("model_id") != TRANSLATOR_MODEL_ID
            or (rec.get("translator") or {}).get("region") != TRANSLATOR_REGION):
        raise ContractError("the translation record states another prompt, decoding or translator than the frozen ones")
    if (rec.get("journal") or {}).get("sha256") != journal_sha:
        raise ContractError("the translation journal changed after the record was written")
    b = rec.get("baseline_before_first_call") or {}
    if b.get("equal") is not True or not b.get("at") or not rec.get("first_call_at") \
            or parse_datetime(b["at"]) > parse_datetime(rec["first_call_at"]):
        raise ContractError("the translation record does not show the registered German figure reproduced before the "
                            "first translation call (specification §4e(a) item 8)")
    out = {"record_sha256": hashlib.sha256(rp.read_bytes()).hexdigest()}
    if commitment:
        out["record_commitment"] = TRANSLATION_PROVENANCE_COMMITMENT(rp)
        out["journal_commitment"] = TRANSLATION_PROVENANCE_COMMITMENT(jp)
    return out


def load_translation_file(path: Path, paras: list[dict], texts: dict | None = None, *,
                          provenance: bool = True) -> tuple[dict, dict]:
    """Read and verify the private translation file against the German A2 paragraphs `paras` (manifest rows) and, where
    the caller holds them (`texts`: paragraph id -> registered text; the runner and the translator do), the registered
    texts; return (rows by paragraph id, the binding block the execution contract carries). The ONE verification the
    translator's record, the runner, the check sheet, the evaluator and the comparison share. Refuses: a path git does
    not ignore; a missing or torn file; another header; a duplicate, missing or foreign paragraph; any row that fails
    `check_translation_row`; and, with `provenance`, a file that is not the one the translation step journalled and
    recorded (`verify_translation_journal`, `verify_translation_record`, both committed unchanged). The binding is a
    pure function of the files, so the runner and the evaluator compute the same block."""
    path = Path(path)
    ensure_ignored(path)
    if not path.exists():
        raise ContractError(f"no translation file at {path}")
    if not path.read_bytes().endswith(b"\n"):
        raise ContractError("the translation file does not end with a line terminator (a torn write)")
    cols, raw = _read_semicolon_csv(path)
    if cols != TRANSLATION_COLUMNS:
        raise ContractError(f"the translation file's header is not the declared one ({len(cols)} columns)")
    want = {q["paragraph_id"]: q for q in paras}
    if len(want) != len(paras):
        raise ContractError("the paragraph selection lists a paragraph twice")
    ids = [r.get("paragraph_id") for r in raw]
    dup = [k for k, c in Counter(ids).items() if c > 1]
    if dup:
        raise ContractError(f"{len(dup)} paragraph(s) appear more than once in the translation file: {dup[:4]}")
    if set(ids) != set(want):
        raise ContractError(f"the translation file does not cover exactly the {len(want)} German A2 paragraphs: missing "
                            f"{sorted(set(want) - set(ids))[:4]}, foreign {len(set(ids) - set(want))}")
    rows = {}
    for r in raw:
        pid = r["paragraph_id"]
        check_translation_row(r, want[pid], None if texts is None else texts[pid])
        rows[pid] = r
    binding = {"file": _rel(path), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "pairs_sha256": translation_pairs_digest(rows), "paragraph_ids": sorted(rows),
               "rows": len(rows), "translated": sum(r["outcome"] == T_TRANSLATED for r in rows.values()),
               "failed": sum(r["outcome"] == T_FAILED for r in rows.values()),
               "translator_model_id": TRANSLATOR_MODEL_ID, "translator_region": TRANSLATOR_REGION,
               "prompt_sha256": TRANSLATION_PROMPT_SHA256,
               "restrict_lang": TRANSLATED_LANG, "status": TRANSLATED_STATUS, "authority": TRANSLATED_AUTHORITY}
    if provenance:
        j = verify_translation_journal(rows, TRANSLATION_JOURNAL, complete=True)
        rec = verify_translation_record(binding, j["journal_sha256"])
        binding.update({"journal_sha256": j["journal_sha256"], "record_sha256": rec["record_sha256"]})
    return rows, binding


def _norm_eol(s) -> str:
    """Line endings normalised (CRLF and a lone CR read as LF): a spreadsheet's re-save never fails a pair."""
    return str(s or "").replace("\r\n", "\n").replace("\r", "\n")


def validate_translation_check(check_path: Path, translations: dict) -> tuple[dict, list[str]]:
    """The author's filled check sheet against the translation file: exactly the paragraphs of the file, each pair's
    source text and translation unchanged (by hash after normalising line endings on BOTH sides), every translated pair
    with a severity from none / minor / major and category flags 1 / x or empty / 0; a failed translation carries no
    severity and no flag. The declared rubric couples nothing more: a severity without a flagged category, or none with
    a flag, is the author's judgement and is counted, not refused. Returns (counts only, the paragraph ids with a major
    flag). Nothing of the text -- and no cell content -- leaves this function."""
    check_path = Path(check_path)
    if not check_path.exists():
        raise ContractError(f"no translation check file at {check_path}")
    cols, raw = _read_semicolon_csv(check_path)
    if cols != CHECK_COLUMNS:
        raise ContractError("the check sheet's header is not the declared rubric (paragraph_id, source_text, translation, "
                            f"{', '.join(CHECK_CATEGORIES)}, severity, note)")
    ids = [r.get("paragraph_id") for r in raw]
    if len(ids) != len(set(ids)) or set(ids) != set(translations):
        raise ContractError(f"the check sheet does not list each of the {len(translations)} translated paragraphs exactly once")
    problems, majors = [], []
    sev = Counter(); cats = Counter(); not_translated = 0; sev_no_flag = 0; none_flagged = 0
    for r in raw:
        pid = r["paragraph_id"]; t = translations[pid]
        if None in r or any(v is None for v in r.values()):
            problems.append(f"{pid}: the row is malformed (its field count is not the header's)"); continue
        if _sha_text(_norm_eol(r["source_text"])) != t["source_sha256"]:
            problems.append(f"{pid}: source text differs from the translated source"); continue
        flags = {}
        for c in CHECK_CATEGORIES:
            v = str(r.get(c) or "").strip().lower()
            if v not in CHECK_FLAG_YES + CHECK_FLAG_NO:
                problems.append(f"{pid}: column {c} holds an invalid value (allowed: 1 / x / 0 / empty)"); break
            flags[c] = v in CHECK_FLAG_YES
        else:
            s = str(r.get("severity") or "").strip().lower()
            if t["outcome"] == T_FAILED:
                if _norm_eol(r["translation"]) or s or any(flags.values()):
                    problems.append(f"{pid}: a failed translation carries text, a severity or a flag")
                not_translated += 1
                continue
            if _sha_text(_norm_eol(r["translation"])) != _sha_text(_norm_eol(t["translation"])):
                problems.append(f"{pid}: translation differs from the translation file"); continue
            if s not in CHECK_SEVERITIES:
                problems.append(f"{pid}: severity is missing or invalid (allowed: {' / '.join(CHECK_SEVERITIES)})"); continue
            sev[s] += 1
            for c, on in flags.items():
                cats[c] += on
            sev_no_flag += s != "none" and not any(flags.values())
            none_flagged += s == "none" and any(flags.values())
            if s == "major":
                majors.append(pid)
        if len(problems) >= 8:
            break
    if problems:
        raise ContractError("the check sheet is incomplete or invalid: " + "; ".join(problems[:8]))
    summary = {"rows": len(raw), "checked": sum(sev.values()), "not_translated": not_translated,
               "severity": {s: sev.get(s, 0) for s in CHECK_SEVERITIES},
               "categories": {c: cats.get(c, 0) for c in CHECK_CATEGORIES},
               "minor_or_major_without_category": sev_no_flag, "none_with_category": none_flagged}
    return summary, sorted(majors)


def translated_runs(runs_dir: Path) -> list[dict]:
    """The run records under `runs_dir` that carry a translated-input binding (run id, first call), oldest first --
    what the check seal records, so the order of extraction and check can be stated from the artefacts."""
    out = []
    for p in sorted(Path(runs_dir).glob("*.json")):
        try:
            txt = p.read_text(encoding="utf-8")
            if '"translated_input"' not in txt:
                continue
            rec = json.loads(txt)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and (rec.get("execution_contract") or {}).get("translated_input"):
            out.append({"run_id": rec.get("run_id"), "first_call_at": rec.get("first_call_at")})
    return sorted(out, key=lambda r: (str(r.get("first_call_at")), str(r.get("run_id"))))


def build_check_seal(check_path: Path, translations: dict, translation_sha: str, sealed_at: str,
                     runs_before: list[dict]) -> dict:
    summary, _majors = validate_translation_check(check_path, translations)
    seal = {"kind": TRANSLATION_CHECK_SEAL_KIND, "authority": TRANSLATED_AUTHORITY,
            "check_file": _rel(check_path), "check_sha256": hashlib.sha256(Path(check_path).read_bytes()).hexdigest(),
            "translation_file_sha256": translation_sha,
            "rubric": {"categories": list(CHECK_CATEGORIES), "severities": list(CHECK_SEVERITIES)},
            "summary": summary, "sealed_at": sealed_at,
            "translated_runs_before_seal": runs_before,
            "rule": "no agreement figure of the language arm is computed before this seal, naming the check file's digest, "
                    "is committed (specification §4e(a), amendment item 6)"}
    seal["digest"] = canonical_digest(seal)
    return seal


def verify_check_seal(translation_sha: str, translations: dict, *, seal_path: Path | None = None) -> dict:
    """The gate before any agreement figure of the language arm: the tracked check seal exists, is intact, equals its
    first commit (by content) and was written no later than that commit, names the translation file the run bound; the
    private check file it names exists, still has the sealed digest, and validates complete against that translation
    file with the sealed counts. Returns the seal with its commitment and the paragraph ids carrying a major flag (for
    the predeclared sensitivity)."""
    seal_path = Path(seal_path or TRANSLATION_CHECK_SEAL)
    if not seal_path.exists():
        raise ContractError(f"no translation check seal at {_rel(seal_path)}; the author's check of all pairs must be "
                            f"finished and its digest committed first")
    try:
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ContractError(f"the translation check seal is not JSON ({exc})") from None
    if not isinstance(seal, dict) or seal.get("kind") != TRANSLATION_CHECK_SEAL_KIND or canonical_digest(seal) != seal.get("digest"):
        raise ContractError("the translation check seal is not intact (kind or digest)")
    if seal.get("translation_file_sha256") != translation_sha:
        raise ContractError("the translation check seal names another translation file than the one the run bound")
    try:
        commitment = CHECK_SEAL_COMMITMENT(seal_path)
    except ContractError as exc:
        raise ContractError(f"the translation check seal is not committed unchanged ({exc})") from None
    if commitment.get("committed_at") and \
            parse_datetime(seal["sealed_at"]).replace(microsecond=0) > parse_datetime(commitment["committed_at"]):
        raise ContractError("the translation check seal's sealed_at postdates its first commit; it is not the committed seal")
    cp = Path(seal.get("check_file") or "")
    cp = cp if cp.is_absolute() else ROOT / cp
    if not cp.exists():
        raise ContractError(f"the check file the seal names ({seal.get('check_file')}) is missing")
    if hashlib.sha256(cp.read_bytes()).hexdigest() != seal.get("check_sha256"):
        raise ContractError("the check file changed after it was sealed (digest differs)")
    summary, majors = validate_translation_check(cp, translations)
    if summary != seal.get("summary"):
        raise ContractError("the check file's counts are not the sealed ones")
    return {**seal, "commitment": commitment, "major_paragraphs": majors}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Seal the author's blind minimum-support threshold s (written once).")
    ap.add_argument("--seal-threshold", type=int, required=True, metavar="S")
    ap.add_argument("--author-confirmation", required=True,
                    help="one sentence by the author stating that the value was chosen blind to every A2 label")
    a = ap.parse_args()
    try:
        sealed = write_threshold_seal(a.seal_threshold, a.author_confirmation)
    except ContractError as exc:
        raise SystemExit(f"REFUSING to seal: {exc}")
    print(f"sealed s={sealed['s']} in {THRESHOLD_SEAL.relative_to(ROOT)} (digest {sealed['digest'][:16]}, "
          f"git head {sealed['git_head'][:12]}, tree dirty={sealed['git_tree_dirty']}); commit this file before coding")
